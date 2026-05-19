"""OpenAI and Anthropic LLM interceptors for AgentAutopsy."""

from typing import Any, Callable

import openai

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event


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
        insert_event(
            db,
            run_id,
            "llm_response",
            {},
            cassette=save_cassette(response),
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
            insert_event(
                db,
                run_id,
                "llm_call",
                {
                    "provider": "anthropic",
                    "model": kwargs.get("model"),
                    "messages": kwargs.get("messages"),
                },
            )
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
            insert_event(
                db,
                run_id,
                "llm_response",
                {},
                cassette=save_cassette(response),
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
