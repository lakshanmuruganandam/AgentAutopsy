"""MCP post-mortem tracing for AgentAutopsy."""

from __future__ import annotations

import atexit
import json
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from agentautopsy.db import create_tables, get_db, insert_event, insert_run, mark_run_failed
from agentautopsy.schema_drift import (
    SchemaDriftDetector,
    _coerce_dict,
    ensure_schema_tables,
    get_active_detector,
)

_mcp_context: dict[str, Any] = {}


def _safe_json(value: Any) -> Any:
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


def _tool_list(tools_result: Any) -> list[Any]:
    if tools_result is None:
        return []
    if hasattr(tools_result, "tools"):
        return list(tools_result.tools or [])
    if isinstance(tools_result, dict):
        return list(tools_result.get("tools") or [])
    return []


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "unknown_tool")
    return str(getattr(tool, "name", None) or "unknown_tool")


def _tool_input_schema(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    else:
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    return _coerce_dict(schema) if isinstance(schema, dict) else {}


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _python_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _types_compatible(expected_type: str, value: Any) -> bool:
    actual = _python_type_name(value)
    mapping = {
        "string": {"string"},
        "integer": {"integer"},
        "number": {"integer", "number"},
        "boolean": {"boolean"},
        "array": {"array"},
        "object": {"object"},
        "null": {"null"},
    }
    return actual in mapping.get(expected_type, {actual})


def _similar_name(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def compare_input_to_schema(
    expected_schema: dict[str, Any],
    received_input: dict[str, Any],
) -> dict[str, Any]:
    """Compare received tool input against the MCP tool input schema."""
    properties = _schema_properties(expected_schema)
    required = list(expected_schema.get("required") or [])
    received = received_input if isinstance(received_input, dict) else {}

    missing_fields: list[str] = []
    renamed_fields: list[dict[str, str]] = []
    type_changes: list[dict[str, str]] = []
    unexpected_fields: list[str] = []

    for field in required:
        if field not in received:
            missing_fields.append(field)

    for field, definition in properties.items():
        if field not in received:
            continue
        expected_type = str((definition or {}).get("type") or "")
        if expected_type and not _types_compatible(expected_type, received[field]):
            type_changes.append(
                {
                    "field": field,
                    "expected_type": expected_type,
                    "received_type": _python_type_name(received[field]),
                }
            )

    expected_names = set(properties.keys()) | set(required)
    received_names = set(received.keys())
    for field in sorted(received_names - expected_names):
        unexpected_fields.append(field)
        best_match = ""
        best_score = 0.0
        for candidate in expected_names:
            if candidate in received:
                continue
            score = _similar_name(field, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
        if best_match and best_score >= 0.72:
            renamed_fields.append({"from": best_match, "to": field})

    mismatches = bool(missing_fields or renamed_fields or type_changes or unexpected_fields)
    root_cause = "none"
    if renamed_fields:
        root_cause = "renamed field"
    elif missing_fields:
        root_cause = "missing field"
    elif type_changes:
        root_cause = "type mismatch"
    elif unexpected_fields:
        root_cause = "schema drift"

    return {
        "missing_fields": missing_fields,
        "renamed_fields": renamed_fields,
        "type_changes": type_changes,
        "unexpected_fields": unexpected_fields,
        "has_mismatch": mismatches,
        "root_cause": root_cause,
    }


class MCPAutopsy:
    """Trace MCP tool calls, schema mismatches, and downstream contamination."""

    def __init__(self, run_id: str, db: Any, server_name: str = "mcp") -> None:
        self.run_id = run_id
        self.db = db
        self.server_name = server_name
        self._expected_schemas: dict[str, dict[str, Any]] = {}
        self._contaminated_tools: set[str] = set()
        self._downstream_contaminated = 0
        self._affected_agents: set[str] = set()
        self._active_agent = server_name

    def _drift_detector(self) -> SchemaDriftDetector:
        detector = get_active_detector()
        if detector is not None and detector.run_id == self.run_id:
            return detector
        local = SchemaDriftDetector(
            run_id=self.run_id,
            db=self.db,
            agent_name=self._active_agent,
        )
        local.watch()
        return local

    def register_tool_schema(self, tool_name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """Store tool schema and return drift report if schema changed."""
        schema = _coerce_dict(schema) if isinstance(schema, dict) else {}
        self._expected_schemas[tool_name] = schema
        if not schema:
            return None
        result = self._drift_detector().record_schema(
            tool_name,
            schema,
            source=f"mcp:{self.server_name}",
            agent_name=self._active_agent,
        )
        if result is None:
            return None
        insert_event(
            self.db,
            self.run_id,
            "mcp_schema_drift",
            {
                "server": self.server_name,
                "tool": tool_name,
                "drift": result.get("drift"),
                "report": result.get("report"),
                "affected_agents": result.get("affected_agents"),
            },
        )
        return {
            "tool": tool_name,
            "drift": result.get("drift"),
            "report": result.get("report"),
        }

    def on_tools_listed(self, tools_result: Any) -> None:
        for tool in _tool_list(tools_result):
            name = _tool_name(tool)
            schema = _tool_input_schema(tool)
            self.register_tool_schema(name, schema)

    def get_expected_schema(self, tool_name: str) -> dict[str, Any]:
        if tool_name in self._expected_schemas:
            return self._expected_schemas[tool_name]
        baseline = self._drift_detector().load_baseline(f"mcp:{self.server_name}", tool_name)
        return baseline or {}

    def on_tool_call(
        self,
        tool_name: str,
        arguments: Any,
        expected_schema: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        if agent_name:
            self._active_agent = agent_name
        args = _safe_json(arguments)
        if not isinstance(args, dict):
            args = {"value": args}
        schema = expected_schema if expected_schema is not None else self.get_expected_schema(tool_name)
        comparison = compare_input_to_schema(schema, args)
        contaminated_by = None
        if self._contaminated_tools:
            contaminated_by = sorted(self._contaminated_tools)
            self._downstream_contaminated += 1
            insert_event(
                self.db,
                self.run_id,
                "mcp_contamination",
                {
                    "server": self.server_name,
                    "tool": tool_name,
                    "agent": self._active_agent,
                    "contaminated_by": contaminated_by,
                    "downstream_count": self._downstream_contaminated,
                },
            )

        payload = {
            "server": self.server_name,
            "tool": tool_name,
            "agent": self._active_agent,
            "input": args,
            "expected_schema": schema,
            "comparison": comparison,
            "contaminated_by": contaminated_by,
        }
        insert_event(self.db, self.run_id, "mcp_tool_call", payload)

        if comparison["has_mismatch"]:
            mismatch_report = self.generate_mcp_failure_report(
                tool_name,
                schema,
                args,
                comparison,
                contaminated_downstream=self._downstream_contaminated,
            )
            insert_event(
                self.db,
                self.run_id,
                "mcp_schema_mismatch",
                {
                    "server": self.server_name,
                    "tool": tool_name,
                    "comparison": comparison,
                    "report": mismatch_report,
                },
            )
            self._contaminated_tools.add(tool_name)
            mark_run_failed(self.db, self.run_id)
            insert_event(
                self.db,
                self.run_id,
                "mcp_failure",
                {
                    "server": self.server_name,
                    "tool": tool_name,
                    "root_cause": comparison["root_cause"],
                    "comparison": comparison,
                    "contaminated_downstream": self._downstream_contaminated,
                    "report": mismatch_report,
                },
            )
            print(mismatch_report)
        return comparison

    def on_tool_result(
        self,
        tool_name: str,
        arguments: Any,
        result: Any,
        model_response: Any = None,
    ) -> None:
        insert_event(
            self.db,
            self.run_id,
            "mcp_tool_result",
            {
                "server": self.server_name,
                "tool": tool_name,
                "agent": self._active_agent,
                "input": _safe_json(arguments),
                "output": _safe_json(result),
                "model_response": _safe_json(model_response),
            },
        )

    def on_tool_failure(
        self,
        tool_name: str,
        arguments: Any,
        expected_schema: dict[str, Any] | None,
        error: BaseException,
    ) -> None:
        schema = expected_schema or self.get_expected_schema(tool_name)
        args = _safe_json(arguments)
        if not isinstance(args, dict):
            args = {"value": args}
        comparison = compare_input_to_schema(schema, args)
        report = self.generate_mcp_failure_report(
            tool_name,
            schema,
            args,
            comparison,
            error=str(error),
            contaminated_downstream=self._downstream_contaminated,
        )
        insert_event(
            self.db,
            self.run_id,
            "error",
            {
                "error_type": type(error).__name__,
                "message": str(error),
                "tool": tool_name,
                "framework": "mcp",
            },
        )
        insert_event(
            self.db,
            self.run_id,
            "mcp_failure",
            {
                "server": self.server_name,
                "tool": tool_name,
                "root_cause": comparison.get("root_cause") or "tool execution failed",
                "comparison": comparison,
                "contaminated_downstream": self._downstream_contaminated,
                "report": report,
            },
        )
        mark_run_failed(self.db, self.run_id)
        print(report)

    @staticmethod
    def generate_mcp_failure_report(
        tool_name: str,
        expected_schema: dict[str, Any],
        received_input: dict[str, Any],
        comparison: dict[str, Any],
        *,
        error: str | None = None,
        contaminated_downstream: int = 0,
    ) -> str:
        lines = [
            "═══════════════════════════════════════",
            " MCP FAILURE REPORT",
            "═══════════════════════════════════════",
            f"Tool called: {tool_name}",
            "",
            "Schema expected:",
            json.dumps(expected_schema, indent=2, default=str),
            "",
            "Input received:",
            json.dumps(received_input, indent=2, default=str),
            "",
            "Missing fields:",
            ", ".join(comparison.get("missing_fields") or []) or "(none)",
            "",
            "Renamed fields:",
        ]
        renamed = comparison.get("renamed_fields") or []
        if renamed:
            for item in renamed:
                lines.append(f"  - {item['from']} -> {item['to']}")
        else:
            lines.append("  (none)")
        lines.extend(
            [
                "",
                "Type changes:",
            ]
        )
        type_changes = comparison.get("type_changes") or []
        if type_changes:
            for item in type_changes:
                lines.append(
                    f"  - {item['field']}: expected {item['expected_type']}, "
                    f"received {item['received_type']}"
                )
        else:
            lines.append("  (none)")
        lines.extend(
            [
                "",
                f"Root cause: {comparison.get('root_cause', 'unknown')}",
                f"Downstream calls contaminated: {contaminated_downstream}",
            ]
        )
        if error:
            lines.extend(["", f"Execution error: {error}"])
        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)

    @staticmethod
    def generate_schema_drift_report(
        tool_name: str,
        previous_schema: dict[str, Any],
        current_schema: dict[str, Any],
        drift: dict[str, Any],
        *,
        affected_agents: list[str] | None = None,
    ) -> str:
        lines = [
            "═══════════════════════════════════════",
            " SCHEMA DRIFT DETECTED",
            "═══════════════════════════════════════",
            f"Tool: {tool_name}",
            "",
            "Previous schema:",
            json.dumps(previous_schema, indent=2, default=str),
            "",
            "Current schema:",
            json.dumps(current_schema, indent=2, default=str),
            "",
            "Changes:",
        ]
        if drift.get("added_fields"):
            lines.append(f"  Added fields: {', '.join(drift['added_fields'])}")
        if drift.get("removed_fields"):
            lines.append(f"  Removed fields: {', '.join(drift['removed_fields'])}")
        if drift.get("renamed_fields"):
            for item in drift["renamed_fields"]:
                lines.append(f"  Renamed: {item['from']} -> {item['to']}")
        if drift.get("type_changes"):
            for item in drift["type_changes"]:
                lines.append(
                    f"  Type change {item['field']}: {item['from_type']} -> {item['to_type']}"
                )
        if drift.get("required_added"):
            lines.append(f"  New required fields: {', '.join(drift['required_added'])}")
        if drift.get("required_removed"):
            lines.append(f"  Removed required fields: {', '.join(drift['required_removed'])}")
        if affected_agents:
            lines.extend(["", "Affected agents:", ", ".join(affected_agents)])
        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)

    def watch_mcp_server(self, server_name: str | None = None) -> MCPAutopsy:
        """Install MCP interceptors for the active run."""
        if server_name:
            self.server_name = server_name
        _mcp_context["autopsy"] = self
        ensure_schema_tables(self.db)
        patched = _install_mcp_patches()
        if patched:
            print(
                f"[AgentAutopsy MCP] watching '{self.server_name}' — run {self.run_id}"
            )
        else:
            print(
                f"[AgentAutopsy MCP] manual hooks ready for '{self.server_name}' "
                f"— run {self.run_id} (install `mcp` for automatic interception)"
            )
        return self

    @classmethod
    def start(
        cls,
        server_name: str | None = None,
        *,
        agent_name: str | None = None,
        parent_run_id: str | None = None,
        register_exit: bool = True,
    ) -> MCPAutopsy:
        """One-line MCP watch setup (zero-config)."""
        db = get_db()
        create_tables(db)
        run_id = insert_run(
            db,
            agent_name=agent_name or server_name or "mcp",
            parent_run_id=parent_run_id,
        )
        autopsy = cls(run_id, db, server_name=server_name or "mcp")
        SchemaDriftDetector(
            run_id=run_id,
            db=db,
            agent_name=agent_name or server_name or "mcp",
        ).watch()
        autopsy.watch_mcp_server(server_name)
        if register_exit:
            atexit.register(_print_mcp_exit_summary, run_id, db)
        return autopsy


def _install_mcp_patches() -> bool:
    try:
        from mcp.client.session import ClientSession
    except ImportError:
        return False

    if getattr(ClientSession, "_agentautopsy_mcp_patched", False):
        return True

    original_call_tool = ClientSession.call_tool
    original_list_tools = ClientSession.list_tools

    async def patched_call_tool(self, name, arguments=None, *args, **kwargs):
        autopsy: MCPAutopsy | None = _mcp_context.get("autopsy")
        arguments = arguments or {}
        if autopsy is not None:
            autopsy.on_tool_call(str(name), arguments)
        try:
            result = await original_call_tool(self, name, arguments, *args, **kwargs)
        except Exception as exc:
            if autopsy is not None:
                autopsy.on_tool_failure(str(name), arguments, None, exc)
            raise
        if autopsy is not None:
            model_response = None
            content = getattr(result, "content", None)
            if content is not None:
                model_response = _safe_json(content)
            autopsy.on_tool_result(str(name), arguments, result, model_response)
        return result

    async def patched_list_tools(self, *args, **kwargs):
        result = await original_list_tools(self, *args, **kwargs)
        autopsy: MCPAutopsy | None = _mcp_context.get("autopsy")
        if autopsy is not None:
            autopsy.on_tools_listed(result)
        return result

    ClientSession.call_tool = patched_call_tool  # type: ignore[method-assign]
    ClientSession.list_tools = patched_list_tools  # type: ignore[method-assign]
    ClientSession._agentautopsy_mcp_patched = True
    return True


def _print_mcp_exit_summary(run_id: str, db: Any) -> None:
    if not db["events"].exists():
        return
    failures = list(
        db["events"].rows_where(
            where='run_id = ? AND type = ?',
            where_args=[run_id, "mcp_failure"],
            order_by="timestamp",
        )
    )
    drifts = list(
        db["events"].rows_where(
            where='run_id = ? AND type = ?',
            where_args=[run_id, "mcp_schema_drift"],
            order_by="timestamp",
        )
    )
    if failures:
        contaminated = sum(
            1
            for row in db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[run_id, "mcp_contamination"],
            )
        )
        print(f"\n[AgentAutopsy MCP] {len(failures)} MCP failure(s) recorded — run {run_id}")
        try:
            payload = json.loads(failures[-1]["payload"])
            report = payload.get("report")
            if report and contaminated:
                report = report.replace(
                    f"Downstream calls contaminated: {payload.get('contaminated_downstream', 0)}",
                    f"Downstream calls contaminated: {contaminated}",
                )
            if report:
                print(report)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    elif drifts:
        print(f"\n[AgentAutopsy MCP] schema drift detected — run {run_id}")
    else:
        print(f"\n[AgentAutopsy MCP] run completed cleanly — {run_id}")


if __name__ == "__main__":
    autopsy = MCPAutopsy.start("demo-server")
    autopsy.register_tool_schema(
        "search",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    autopsy.on_tool_call("search", {"qery": "market trends"})
