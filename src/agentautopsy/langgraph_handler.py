"""LangGraph callback integration for AgentAutopsy."""

from __future__ import annotations

import json
from typing import Any

from agentautopsy.cassette import save_cassette
from agentautopsy.db import insert_event

try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.outputs import LLMResult

    _LANGCHAIN_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    BaseCallbackHandler = object  # type: ignore[misc, assignment]
    LLMResult = Any  # type: ignore[misc, assignment]
    _LANGCHAIN_AVAILABLE = False


def _safe_payload(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def _node_from_kwargs(**kwargs: Any) -> str | None:
    metadata = kwargs.get("metadata")
    if isinstance(metadata, dict):
        for key in ("langgraph_node", "node", "langgraph_step"):
            value = metadata.get(key)
            if value:
                return str(value)
    tags = kwargs.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            text = str(tag)
            if text.startswith("graph:node:"):
                return text.split("graph:node:", 1)[-1]
            if text.startswith("node:"):
                return text.split("node:", 1)[-1]
    return None


class AgentAutopsyLangGraphHandler(BaseCallbackHandler):
    """Log LangGraph node, edge, and state activity into AgentAutopsy."""

    def __init__(self, run_id: str, db: Any) -> None:
        self.run_id = run_id
        self.db = db
        self._last_node: str | None = None
        self._latest_memory_snapshot: Any = None

    def on_graph_node_start(self, node: str, input_data: Any = None) -> None:
        if self._last_node and self._last_node != node:
            self.on_graph_edge(self._last_node, node)
        self._last_node = node
        insert_event(
            self.db,
            self.run_id,
            "langgraph_node_start",
            {"node": node, "input": _safe_payload(input_data), "framework": "langgraph"},
        )

    def on_graph_node_end(self, node: str, output: Any = None) -> None:
        insert_event(
            self.db,
            self.run_id,
            "langgraph_node_end",
            {"node": node, "output": _safe_payload(output), "framework": "langgraph"},
        )

    def on_graph_edge(self, from_node: str, to_node: str) -> None:
        insert_event(
            self.db,
            self.run_id,
            "langgraph_edge",
            {"from_node": from_node, "to_node": to_node, "framework": "langgraph"},
        )

    def on_graph_state_change(self, state: Any) -> None:
        safe_state = _safe_payload(state)
        self._latest_memory_snapshot = safe_state
        insert_event(
            self.db,
            self.run_id,
            "langgraph_state_change",
            {"state": safe_state, "framework": "langgraph"},
        )

    def on_graph_error(self, error: BaseException, node: str | None = None) -> None:
        cassette_data = None
        if self._latest_memory_snapshot is not None:
            cassette_data = json.dumps(self._latest_memory_snapshot, default=str).encode("utf-8")
        
        insert_event(
            self.db,
            self.run_id,
            "error",
            {
                "error_type": type(error).__name__,
                "message": str(error),
                "node": node or self._last_node,
                "framework": "langgraph",
            },
            cassette=cassette_data,
        )

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        node = _node_from_kwargs(**kwargs) or serialized.get("name")
        if node:
            self.on_graph_node_start(str(node), inputs)

    def on_chain_end(self, outputs: dict[str, Any], **kwargs: Any) -> None:
        node = _node_from_kwargs(**kwargs) or self._last_node
        if outputs:
            self.on_graph_state_change(outputs)
        if node:
            self.on_graph_node_end(str(node), outputs)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        node = _node_from_kwargs(**kwargs) or self._last_node
        self.on_graph_error(error, node)

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
                "input": input_str,
                "framework": "langgraph",
            },
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "tool_result",
            {"output": _safe_payload(output), "framework": "langgraph"},
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        model = "unknown"
        model_kwargs = serialized.get("kwargs")
        if isinstance(model_kwargs, dict):
            model = str(model_kwargs.get("model") or model_kwargs.get("model_name") or model)
        insert_event(
            self.db,
            self.run_id,
            "llm_call",
            {"model": model, "prompts": prompts, "framework": "langgraph"},
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "llm_response",
            {"framework": "langgraph"},
            cassette=save_cassette(response),
        )


if __name__ == "__main__":
    print(
        "Usage: agentautopsy.watch() then pass get_langgraph_handler() "
        'in graph.invoke(..., config={"callbacks": [handler]})'
    )
