"""Schema drift detection for AgentAutopsy tool and function definitions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from agentautopsy.db import create_tables, get_db, insert_event, insert_run

_drift_context: dict[str, Any] = {}


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


def _coerce_dict(value: Any) -> dict[str, Any]:
    data = _safe_json(value)
    return data if isinstance(data, dict) else {}


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _similar_name(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


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


def ensure_schema_tables(db: Any) -> None:
    db["tool_schema_baselines"].create(
        {
            "id": str,
            "source": str,
            "tool_name": str,
            "schema_json": str,
            "updated_at": str,
        },
        pk="id",
        if_not_exists=True,
    )


def diff_schemas(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return schema drift details between two JSON schemas."""
    prev_props = _schema_properties(previous)
    curr_props = _schema_properties(current)
    prev_required = set(previous.get("required") or [])
    curr_required = set(current.get("required") or [])

    added_fields = sorted(set(curr_props) - set(prev_props))
    removed_fields = sorted(set(prev_props) - set(curr_props))
    type_changes: list[dict[str, str]] = []
    renamed_fields: list[dict[str, str]] = []

    for field in sorted(set(prev_props) & set(curr_props)):
        prev_type = str((prev_props[field] or {}).get("type") or "")
        curr_type = str((curr_props[field] or {}).get("type") or "")
        if prev_type and curr_type and prev_type != curr_type:
            type_changes.append(
                {"field": field, "from_type": prev_type, "to_type": curr_type}
            )

    for removed in removed_fields:
        for added in added_fields:
            if _similar_name(removed, added) >= 0.72:
                renamed_fields.append({"from": removed, "to": added})

    required_added = sorted(curr_required - prev_required)
    required_removed = sorted(prev_required - curr_required)
    changed = bool(
        added_fields
        or removed_fields
        or type_changes
        or renamed_fields
        or required_added
        or required_removed
    )
    return {
        "added_fields": added_fields,
        "removed_fields": removed_fields,
        "renamed_fields": renamed_fields,
        "type_changes": type_changes,
        "required_added": required_added,
        "required_removed": required_removed,
        "has_drift": changed,
    }


def infer_schema_from_serialized(serialized: dict[str, Any]) -> dict[str, Any]:
    """Best-effort JSON schema extraction from LangChain-style tool metadata."""
    if not isinstance(serialized, dict):
        return {}
    kwargs = serialized.get("kwargs")
    if not isinstance(kwargs, dict):
        kwargs = {}
    for key in ("args_schema", "schema", "input_schema", "inputSchema"):
        candidate = kwargs.get(key)
        if isinstance(candidate, dict):
            return _coerce_dict(candidate)
        if candidate is not None and hasattr(candidate, "model_json_schema"):
            try:
                return _coerce_dict(candidate.model_json_schema())
            except Exception:
                pass
        if candidate is not None and hasattr(candidate, "schema"):
            try:
                return _coerce_dict(candidate.schema())
            except Exception:
                pass
    args = serialized.get("args")
    if isinstance(args, dict):
        properties = {name: {"type": "string"} for name in args}
        return {
            "type": "object",
            "properties": properties,
            "required": list(args.keys()),
        }
    return {}


def schema_from_tool_input(tool_input: Any) -> dict[str, Any]:
    """Infer a lightweight schema from observed tool input."""
    payload = _safe_json(tool_input)
    if not isinstance(payload, dict) or not payload:
        return {}
    properties = {
        key: {"type": _python_type_name(value)} for key, value in payload.items()
    }
    return {"type": "object", "properties": properties, "required": sorted(payload)}


def extract_openai_tool_schemas(tools: Any) -> list[tuple[str, dict[str, Any]]]:
    """Extract (tool_name, schema) pairs from OpenAI chat completion tools."""
    if not isinstance(tools, list):
        return []
    schemas: list[tuple[str, dict[str, Any]]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        parameters = function.get("parameters")
        if name and isinstance(parameters, dict):
            schemas.append((str(name), _coerce_dict(parameters)))
    return schemas


def record_tools_from_llm_kwargs(kwargs: dict[str, Any], *, source: str = "openai") -> None:
    detector = get_active_detector()
    if detector is None:
        return
    for tool_name, schema in extract_openai_tool_schemas(kwargs.get("tools")):
        detector.record_schema(tool_name, schema, source=source)


def record_tool_from_serialized(
    serialized: dict[str, Any],
    *,
    source: str,
    tool_input: Any = None,
    agent_name: str | None = None,
) -> None:
    detector = get_active_detector()
    if detector is None:
        return
    tool_name = str(serialized.get("name") or "unknown_tool")
    schema = infer_schema_from_serialized(serialized)
    if not schema and tool_input is not None:
        schema = schema_from_tool_input(tool_input)
    if schema:
        detector.record_schema(tool_name, schema, source=source, agent_name=agent_name)


class SchemaDriftDetector:
    """Detect and log tool schema drift across agent runs."""

    def __init__(
        self,
        run_id: str | None = None,
        db: Any | None = None,
        *,
        agent_name: str = "agent",
    ) -> None:
        self.run_id = run_id
        self.db = db
        self.agent_name = agent_name
        self._affected_agents: dict[str, set[str]] = {}

    def watch(self) -> SchemaDriftDetector:
        """Activate schema drift detection for the current or new run."""
        if self.db is None:
            self.db = get_db()
        create_tables(self.db)
        ensure_schema_tables(self.db)
        if self.run_id is None:
            self.run_id = insert_run(self.db, agent_name=self.agent_name)
        _drift_context["detector"] = self
        return self

    def load_baseline(self, source: str, tool_name: str) -> dict[str, Any] | None:
        return self._load_baseline(source, tool_name)

    def _load_baseline(self, source: str, tool_name: str) -> dict[str, Any] | None:
        if not self.db or not self.db["tool_schema_baselines"].exists():
            return None
        rows = list(
            self.db["tool_schema_baselines"].rows_where(
                where="source = ? AND tool_name = ?",
                where_args=[source, tool_name],
                order_by="updated_at desc",
            )
        )
        if not rows:
            return None
        try:
            schema = json.loads(rows[0]["schema_json"])
            return schema if isinstance(schema, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def _store_baseline(self, source: str, tool_name: str, schema: dict[str, Any]) -> None:
        ensure_schema_tables(self.db)
        self.db["tool_schema_baselines"].insert(
            {
                "id": str(uuid.uuid4()),
                "source": source,
                "tool_name": tool_name,
                "schema_json": json.dumps(schema),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            pk="id",
        )

    def _track_agent(self, source: str, tool_name: str, agent_name: str | None) -> None:
        key = f"{source}:{tool_name}"
        self._affected_agents.setdefault(key, set())
        self._affected_agents[key].add(agent_name or self.agent_name)

    def record_schema(
        self,
        tool_name: str,
        schema: dict[str, Any],
        *,
        source: str = "default",
        agent_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Store baseline on first sight; compare and log drift on changes."""
        if self.db is None or self.run_id is None:
            return None

        schema = _coerce_dict(schema) if isinstance(schema, dict) else {}
        if not schema:
            return None

        self._track_agent(source, tool_name, agent_name)
        previous = self._load_baseline(source, tool_name)
        if previous is None:
            self._store_baseline(source, tool_name, schema)
            return None

        drift = diff_schemas(previous, schema)
        if not drift["has_drift"]:
            return None

        key = f"{source}:{tool_name}"
        affected = sorted(self._affected_agents.get(key, {agent_name or self.agent_name}))
        recommendation = (
            f"Update tool definitions for '{tool_name}' ({source}) to match the "
            f"current schema. Review renamed, added, and removed fields before the "
            f"next deployment."
        )
        report = self.generate_drift_report(
            tool_name,
            previous,
            schema,
            drift,
            source=source,
            affected_agents=affected,
            recommendation=recommendation,
        )
        payload = {
            "tool": tool_name,
            "source": source,
            "drift": drift,
            "previous_schema": previous,
            "current_schema": schema,
            "affected_agents": affected,
            "recommendation": recommendation,
            "report": report,
        }
        insert_event(self.db, self.run_id, "schema_drift", payload)
        print(report)
        self._store_baseline(source, tool_name, schema)
        return payload

    @staticmethod
    def generate_drift_report(
        tool_name: str,
        previous_schema: dict[str, Any],
        current_schema: dict[str, Any],
        drift: dict[str, Any],
        *,
        source: str = "default",
        affected_agents: list[str] | None = None,
        recommendation: str | None = None,
    ) -> str:
        lines = [
            "═══════════════════════════════════════",
            " SCHEMA DRIFT DETECTED",
            "═══════════════════════════════════════",
            f"Tool: {tool_name}",
            f"Source: {source}",
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
        if recommendation:
            lines.extend(["", "Recommendation:", recommendation])
        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)


def get_active_detector() -> SchemaDriftDetector | None:
    detector = _drift_context.get("detector")
    return detector if isinstance(detector, SchemaDriftDetector) else None


def load_schema_drift_events(db: Any) -> list[dict[str, Any]]:
    """Load schema drift events for the UI."""
    if not db["events"].exists():
        return []
    events: list[dict[str, Any]] = []
    for event_type in ("schema_drift", "mcp_schema_drift"):
        for row in db["events"].rows_where(
            where="type = ?",
            where_args=[event_type],
            order_by="timestamp desc",
        ):
            payload: dict[str, Any]
            try:
                payload = json.loads(row.get("payload") or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            events.append(
                {
                    "id": row["id"],
                    "run_id": row.get("run_id"),
                    "timestamp": row.get("timestamp", ""),
                    "type": event_type,
                    "payload": payload,
                }
            )
    events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return events
