"""LangChain callback integration for AgentAutopsy."""

from __future__ import annotations

import os
from typing import Any

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event

try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
except ImportError:  # pragma: no cover - optional dependency
    BaseCallbackHandler = object  # type: ignore[misc, assignment]
    LLMResult = Any  # type: ignore[misc, assignment]

try:
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - optional dependency
    ChatAnthropic = Any  # type: ignore[misc, assignment]
    ChatOpenAI = Any  # type: ignore[misc, assignment]


def get_openai_client(timeout: int = 60, max_retries: int = 3) -> ChatOpenAI:
    """Initialize OpenAI client with timeout and retry configuration."""
    return ChatOpenAI(
        model="gpt-4",
        timeout=timeout,
        max_retries=max_retries,
        api_key=os.getenv("OPENAI_API_KEY"),
    )


def get_anthropic_client(timeout: int = 60, max_retries: int = 3) -> ChatAnthropic:
    """Initialize Anthropic client with timeout and retry configuration."""
    return ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        timeout=timeout,
        max_retries=max_retries,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )


def _model_from_serialized(serialized: dict[str, Any]) -> str:
    kwargs = serialized.get("kwargs")
    if isinstance(kwargs, dict):
        for key in ("model", "model_name", "model_id"):
            value = kwargs.get(key)
            if value:
                return str(value)
    name = serialized.get("name")
    if name:
        return str(name)
    model_id = serialized.get("id")
    if isinstance(model_id, list) and model_id:
        return str(model_id[-1])
    return "unknown"


def _tool_input(serialized: dict[str, Any], input_str: str, **kwargs: Any) -> Any:
    inputs = kwargs.get("inputs")
    if inputs is not None:
        return inputs
    return input_str


def _tool_output(output: Any) -> Any:
    if hasattr(output, "model_dump"):
        try:
            return output.model_dump()
        except Exception:
            pass
    if isinstance(output, (dict, list, str, int, float, bool)) or output is None:
        return output
    return str(output)


class AgentAutopsyCallbackHandler(BaseCallbackHandler):
    """Log LangChain runs into the AgentAutopsy SQLite trace."""

    def __init__(self, run_id: str, db: Any) -> None:
        self.run_id = run_id
        self.db = db

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        insert_event(
            self.db,
            self.run_id,
            "llm_call",
            {"model": _model_from_serialized(serialized), "prompts": prompts},
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "llm_response",
            {},
            cassette=save_cassette(response),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        tool_input = _tool_input(serialized, input_str, **kwargs)
        from agentautopsy.schema_drift import record_tool_from_serialized

        record_tool_from_serialized(
            serialized,
            source="langchain",
            tool_input=tool_input,
        )
        insert_event(
            self.db,
            self.run_id,
            "tool_call",
            {
                "tool": serialized.get("name", "unknown_tool"),
                "input": tool_input,
            },
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "tool_result",
            {"output": _tool_output(output)},
        )

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "error",
            {"error_type": type(error).__name__, "message": str(error)},
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        from agentautopsy.interceptor import insert_http_error

        metadata = kwargs.get("metadata")
        url = ""
        if isinstance(metadata, dict):
            url = str(metadata.get("url") or metadata.get("api_url") or "")
        insert_http_error(
            self.db,
            self.run_id,
            method="POST",
            url=url or "https://api.openai.com/v1/chat/completions",
            exc=error,
        )


if __name__ == "__main__":
    print(
        "Usage: agentautopsy.watch() then pass get_callback_handler() "
        "in config={'callbacks': [...]}"
    )
