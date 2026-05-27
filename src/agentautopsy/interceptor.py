"""OpenAI and Anthropic LLM interceptors for AgentAutopsy."""

import time
from typing import Any, Callable

import openai

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event


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

    original_send = httpx.Client.send

    def patched_send(self, request, **kwargs):
        insert_event(
            db,
            run_id,
            "http_request",
            {"method": request.method, "url": str(request.url)},
        )
        try:
            response = original_send(self, request, **kwargs)
        except Exception as e:
            insert_event(
                db,
                run_id,
                "error",
                {"error_type": type(e).__name__, "message": str(e)},
            )
            raise
        insert_event(
            db,
            run_id,
            "http_response",
            {"status_code": response.status_code},
            cassette=response.content,
        )
        return response

    httpx.Client.send = patched_send


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
