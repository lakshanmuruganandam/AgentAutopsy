"""CrewAI callback integration for AgentAutopsy."""

from __future__ import annotations

import json
from typing import Any, Callable

from agentautopsy.db import insert_event


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


class AgentAutopsyCrewAIHandler:
    """Log CrewAI agent activity into the AgentAutopsy SQLite trace."""

    def __init__(self, run_id: str, db: Any) -> None:
        self.run_id = run_id
        self.db = db
        self._active_agent: str | None = None

    def on_task_start(self, agent: str, task: Any) -> None:
        self._active_agent = agent
        insert_event(
            self.db,
            self.run_id,
            "crewai_task_start",
            {"agent": agent, "task": _safe_payload(task), "framework": "crewai"},
        )

    def on_task_end(self, agent: str, task: Any, output: Any = None) -> None:
        insert_event(
            self.db,
            self.run_id,
            "crewai_task_end",
            {
                "agent": agent,
                "task": _safe_payload(task),
                "output": _safe_payload(output),
                "framework": "crewai",
            },
        )

    def on_tool_start(self, agent: str, tool: str, tool_input: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "tool_call",
            {
                "agent": agent,
                "tool": tool,
                "input": _safe_payload(tool_input),
                "framework": "crewai",
            },
        )

    def on_tool_end(self, agent: str, tool: str, output: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "tool_result",
            {
                "agent": agent,
                "tool": tool,
                "output": _safe_payload(output),
                "framework": "crewai",
            },
        )

    def on_agent_handoff(self, from_agent: str, to_agent: str, context: Any = None) -> None:
        self._active_agent = to_agent
        insert_event(
            self.db,
            self.run_id,
            "crewai_handoff",
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "context": _safe_payload(context),
                "framework": "crewai",
            },
        )

    def on_crew_output(self, output: Any) -> None:
        insert_event(
            self.db,
            self.run_id,
            "crewai_output",
            {"output": _safe_payload(output), "framework": "crewai"},
        )

    def on_error(self, error: BaseException, agent: str | None = None) -> None:
        insert_event(
            self.db,
            self.run_id,
            "error",
            {
                "error_type": type(error).__name__,
                "message": str(error),
                "agent": agent or self._active_agent,
                "framework": "crewai",
            },
        )

    def __call__(self, output: Any) -> None:
        """Support Crew(..., callbacks=[handler]) and task callback=handler."""
        if isinstance(output, BaseException):
            self.on_error(output)
            return
        self.on_crew_output(output)

    def step_callback(self, step: Any) -> None:
        """Dispatch CrewAI step objects or dict payloads."""
        if hasattr(step, "tool") and getattr(step, "tool", None):
            agent = self._active_agent or "agent"
            tool = str(step.tool)
            self.on_tool_start(agent, tool, getattr(step, "tool_input", None))
            if hasattr(step, "result"):
                self.on_tool_end(agent, tool, step.result)
            return

        if isinstance(step, dict):
            payload = step
        elif hasattr(step, "model_dump"):
            payload = step.model_dump()
        elif hasattr(step, "__dict__"):
            payload = dict(step.__dict__)
        else:
            payload = {"raw": str(step)}

        event_type = str(payload.get("type") or payload.get("event") or "").lower()
        agent = str(payload.get("agent") or payload.get("agent_name") or self._active_agent or "agent")

        if "error" in event_type or payload.get("error"):
            error = payload.get("error")
            if isinstance(error, BaseException):
                self.on_error(error, agent)
            else:
                self.on_error(RuntimeError(str(error)), agent)
            return

        if "handoff" in event_type:
            self.on_agent_handoff(
                str(payload.get("from_agent") or agent),
                str(payload.get("to_agent") or "unknown"),
                payload.get("context"),
            )
            return

        if "tool" in event_type:
            tool = str(payload.get("tool") or "unknown_tool")
            if "end" in event_type or payload.get("output") is not None:
                self.on_tool_end(agent, tool, payload.get("output"))
            else:
                self.on_tool_start(agent, tool, payload.get("input"))
            return

        if "task" in event_type and "end" in event_type:
            self.on_task_end(agent, payload.get("task"), payload.get("output"))
            return

        if "task" in event_type or "start" in event_type:
            self.on_task_start(agent, payload.get("task") or payload)
            return

        if "output" in event_type or payload.get("crew_output") is not None:
            self.on_crew_output(payload.get("output") or payload.get("crew_output"))
            return

    def as_step_callback(self) -> Callable[[Any], None]:
        """Return a callable suitable for Crew(..., step_callback=...)."""
        return self.step_callback


if __name__ == "__main__":
    print(
        "Usage: agentautopsy.watch() then pass get_crewai_handler() "
        "to Crew(..., step_callback=handler.step_callback)"
    )
