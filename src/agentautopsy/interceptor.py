"""OpenAI and Anthropic LLM interceptors for AgentAutopsy."""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable
from urllib.parse import urlparse

import openai

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event, mark_run_failed


def _http_display_path(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(str(url))
    return parsed.path or str(url)


def infer_http_root_cause(payload: dict[str, Any]) -> str:
    error_type = str(
        payload.get("exception_type") or payload.get("error_type") or "Error"
    )
    message = str(payload.get("message") or "")
    url = str(payload.get("url") or "").lower()
    connection_errors = {
        "APIConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "NetworkError",
        "RemoteProtocolError",
    }
    if error_type in connection_errors or "connection" in message.lower():
        if "openai" in url or "chat/completions" in url:
            return "OpenAI connection failed"
        return "HTTP connection failed"
    if message:
        return f"{error_type} — {message}"
    return error_type


def insert_http_error(
    db: Any,
    run_id: str,
    *,
    method: str | None,
    url: str | None,
    exc: BaseException,
) -> None:
    payload = {
        "exception_type": type(exc).__name__,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "url": str(url or ""),
        "method": method or "",
    }
    request = getattr(exc, "request", None)
    if request is not None:
        payload["method"] = payload["method"] or getattr(request, "method", "")
        payload["url"] = payload["url"] or str(getattr(request, "url", ""))

    insert_event(db, run_id, "http_error", payload)
    mark_run_failed(db, run_id)


def _estimate_cost_usd(model: str | None, token_input: int, token_output: int) -> float:
    model_key = (model or "").lower()
    if "gpt-4o-mini" in model_key:
        input_rate, output_rate = 0.00015 / 1000, 0.0006 / 1000
    elif "gpt-4" in model_key:
        input_rate, output_rate = 0.03 / 1000, 0.06 / 1000
    elif "claude-haiku" in model_key or "haiku" in model_key:
        input_rate, output_rate = 0.00025 / 1000, 0.00125 / 1000
    elif "claude-sonnet" in model_key or "sonnet" in model_key:
        input_rate, output_rate = 0.003 / 1000, 0.015 / 1000
    else:
        return 0.0
    return (token_input * input_rate) + (token_output * output_rate)


def _openai_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _anthropic_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def _insert_llm_response(
    db: Any,
    run_id: str,
    model: str | None,
    response: Any,
    latency_ms: int,
    token_input: int,
    token_output: int,
) -> None:
    cost_usd = _estimate_cost_usd(model, token_input, token_output)
    insert_event(
        db,
        run_id,
        "llm_response",
        {},
        cassette=save_cassette(response),
        latency_ms=latency_ms,
        token_input=token_input,
        token_output=token_output,
        cost_usd=cost_usd,
    )


def _record_http_request(db: Any, run_id: str, method: str, url: str) -> None:
    insert_event(
        db,
        run_id,
        "http_request",
        {"method": method, "url": url, "path": _http_display_path(url)},
    )


def _handle_http_response(
    db: Any,
    run_id: str,
    method: str,
    url: str,
    response: Any,
) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    insert_event(
        db,
        run_id,
        "http_response",
        {"status_code": status_code, "url": url, "method": method},
        cassette=getattr(response, "content", None),
    )
    if status_code >= 400:
        exc = RuntimeError(f"HTTP {status_code} for {method} {_http_display_path(url)}")
        insert_http_error(
            db,
            run_id,
            method=method,
            url=url,
            exc=exc,
        )


def start_interceptor(run_id: str, db: Any) -> None:
    completions = openai.chat.completions
    original_create: Callable[..., Any] = completions.create

    def create_wrapper(*args: Any, **kwargs: Any) -> Any:
        model_name = kwargs.get("model")
        messages_list = kwargs.get("messages")
        insert_event(
            db,
            run_id,
            "llm_call",
            {"model": model_name, "messages": messages_list},
        )
        started = time.time()
        try:
            response = original_create(*args, **kwargs)
        except Exception as e:
            insert_event(
                db,
                run_id,
                "error",
                {"error_type": type(e).__name__, "message": str(e)},
            )
            insert_http_error(
                db,
                run_id,
                method="POST",
                url="https://api.openai.com/v1/chat/completions",
                exc=e,
            )
            raise
        latency_ms = int((time.time() - started) * 1000)
        token_input, token_output = _openai_usage(response)
        _insert_llm_response(
            db, run_id, model_name, response, latency_ms, token_input, token_output
        )
        return response

    completions.create = create_wrapper


def start_anthropic_interceptor(run_id: str, db: Any) -> None:
    import anthropic

    client_class = anthropic.Anthropic
    original_init = client_class.__init__

    def patched_init(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        original_create = self.messages.create

        def create_wrapper(*args: Any, **kwargs: Any) -> Any:
            model_name = kwargs.get("model")
            insert_event(
                db,
                run_id,
                "llm_call",
                {
                    "provider": "anthropic",
                    "model": model_name,
                    "messages": kwargs.get("messages"),
                },
            )
            started = time.time()
            try:
                response = original_create(*args, **kwargs)
            except Exception as e:
                insert_event(
                    db,
                    run_id,
                    "error",
                    {"error_type": type(e).__name__, "message": str(e)},
                )
                insert_http_error(
                    db,
                    run_id,
                    method="POST",
                    url="https://api.anthropic.com/v1/messages",
                    exc=e,
                )
                raise
            latency_ms = int((time.time() - started) * 1000)
            token_input, token_output = _anthropic_usage(response)
            _insert_llm_response(
                db, run_id, model_name, response, latency_ms, token_input, token_output
            )
            return response

        self.messages.create = create_wrapper

    client_class.__init__ = patched_init


def start_http_interceptor(run_id: str, db: Any) -> None:
    import httpx

    _http_context: dict[str, Any] = getattr(start_http_interceptor, "_context", {})
    _http_context["run_id"] = run_id
    _http_context["db"] = db
    start_http_interceptor._context = _http_context

    if not getattr(httpx.Client, "_agentautopsy_http_patched", False):
        original_send = httpx.Client.send

        def patched_send(self, request, **kwargs):
            import time
            active_run_id = _http_context["run_id"]
            active_db = _http_context["db"]
            
            # Retrieve causality thread ID and inject into outgoing requests
            res = active_db.execute("SELECT causality_thread_id FROM runs WHERE id=?", [active_run_id]).fetchone()
            if res and res[0]:
                request.headers["X-AgentAutopsy-Causality-ID"] = str(res[0])
            request.headers["X-AgentAutopsy-Parent-Run"] = str(active_run_id)

            method = request.method
            url = str(request.url)
            _record_http_request(active_db, active_run_id, method, url)
            
            retries = 3
            backoff = 1.5
            for attempt in range(retries + 1):
                try:
                    response = original_send(self, request, **kwargs)
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                        time.sleep(backoff ** attempt)
                        continue
                    _handle_http_response(active_db, active_run_id, method, url, response)
                    return response
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt < retries:
                        time.sleep(backoff ** attempt)
                        continue
                    insert_http_error(active_db, active_run_id, method=method, url=url, exc=exc)
                    raise
                except Exception as exc:
                    insert_http_error(active_db, active_run_id, method=method, url=url, exc=exc)
                    raise

        httpx.Client.send = patched_send
        httpx.Client._agentautopsy_http_patched = True

    if not getattr(httpx.AsyncClient, "_agentautopsy_http_patched", False):
        original_async_send = httpx.AsyncClient.send

        async def patched_async_send(self, request, **kwargs):
            import asyncio
            active_run_id = _http_context["run_id"]
            active_db = _http_context["db"]
            
            # Retrieve causality thread ID and inject into outgoing requests
            res = active_db.execute("SELECT causality_thread_id FROM runs WHERE id=?", [active_run_id]).fetchone()
            if res and res[0]:
                request.headers["X-AgentAutopsy-Causality-ID"] = str(res[0])
            request.headers["X-AgentAutopsy-Parent-Run"] = str(active_run_id)

            method = request.method
            url = str(request.url)
            _record_http_request(active_db, active_run_id, method, url)
            
            retries = 3
            backoff = 1.5
            for attempt in range(retries + 1):
                try:
                    response = await original_async_send(self, request, **kwargs)
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                        await asyncio.sleep(backoff ** attempt)
                        continue
                    _handle_http_response(active_db, active_run_id, method, url, response)
                    return response
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt < retries:
                        await asyncio.sleep(backoff ** attempt)
                        continue
                    insert_http_error(active_db, active_run_id, method=method, url=url, exc=exc)
                    raise
                except Exception as exc:
                    insert_http_error(active_db, active_run_id, method=method, url=url, exc=exc)
                    raise

        httpx.AsyncClient.send = patched_async_send
        httpx.AsyncClient._agentautopsy_http_patched = True


if __name__ == "__main__":
    from agentautopsy.db import create_tables, get_db, insert_run

    db = get_db()
    create_tables(db)
    run_id = insert_run(db)
    start_interceptor(run_id, db)
    start_anthropic_interceptor(run_id, db)
    start_http_interceptor(run_id, db)
    print("OpenAI patched")
    print("Anthropic patched")
    print("HTTP patched")
    print("Both interceptors active")
