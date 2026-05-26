"""LangChain callback integration for AgentAutopsy."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event


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
        insert_event(
            self.db,
            self.run_id,
            "tool_call",
            {
                "tool": serialized.get("name", "unknown_tool"),
                "input": _tool_input(serialized, input_str, **kwargs),
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


if __name__ == "__main__":
    # Example: wire AgentAutopsy into a LangChain chain
    #
    # import agentautopsy
    # from langchain_openai import ChatOpenAI
    # from langchain_core.prompts import ChatPromptTemplate
    #
    # agentautopsy.watch()
    # handler = agentautopsy.get_callback_handler()
    #
    # prompt = ChatPromptTemplate.from_messages([("user", "{question}")])
    # chain = prompt | ChatOpenAI()
    # chain.invoke(
    #     {"question": "hello"},
    #     config={"callbacks": [handler]},
    # )
    print("Usage: agentautopsy.watch() then pass get_callback_handler() in config={'callbacks': [...]}")
