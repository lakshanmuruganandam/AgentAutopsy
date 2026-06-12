"""Automatic eval generation for AgentAutopsy.

Every time AgentAutopsy catches a failure it can turn that failure into a
runnable pytest regression test. The generated test captures the exact input
that triggered the failure, the step it failed at, the tool or LLM call that
broke, and an assertion that catches the failure if it ever happens again.

Once AgentAutopsy has written your test suite, you can never remove it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentautopsy.db import create_tables, get_db
from agentautopsy.detector import detect_failure, take_snapshot

_eval_context: dict[str, Any] = {}

# Event types that represent a concrete call we can capture as the breaking step.
CALL_TYPES: tuple[str, ...] = (
    "mcp_tool_call",
    "tool_call",
    "llm_call",
    "http_request",
)

FAILURE_TYPES: tuple[str, ...] = ("error", "http_error")

GENERATED_DIR_PARTS: tuple[str, ...] = ("tests", "generated")


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


def _slug(text: str, *, fallback: str = "failure") -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", str(text or "")).strip("_").lower()
    return cleaned or fallback


def _call_input(payload: dict[str, Any]) -> Any:
    """Extract the most meaningful 'input' from a call payload."""
    for key in ("input", "messages", "args", "arguments", "query", "prompt", "url", "params"):
        if key in payload and payload[key] not in (None, "", [], {}):
            return _safe_json(payload[key])
    return _safe_json(payload)


def _call_label(event_type: str, payload: dict[str, Any]) -> str:
    if event_type in ("tool_call", "mcp_tool_call"):
        name = payload.get("tool") or payload.get("name") or payload.get("tool_name")
        return f"{event_type} -> {name}" if name else event_type
    if event_type == "llm_call":
        model = payload.get("model")
        return f"llm_call -> {model}" if model else "llm_call"
    if event_type == "http_request":
        method = payload.get("method") or "GET"
        url = payload.get("url") or ""
        return f"http_request -> {method} {url}".strip()
    return event_type


class EvalGenerator:
    """Generate pytest regression tests from recorded agent failures."""

    def __init__(
        self,
        db: Any | None = None,
        run_id: str | None = None,
        *,
        output_dir: str | Path | None = None,
        agent_name: str = "agent",
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.agent_name = agent_name
        self.output_dir = Path(output_dir) if output_dir is not None else None

    def watch(self) -> EvalGenerator:
        """Register this generator so watch() auto-generates evals on failure."""
        if self.db is None:
            self.db = get_db()
        create_tables(self.db)
        _eval_context["generator"] = self
        return self

    def _resolve_db(self) -> Any:
        if self.db is None:
            self.db = get_db()
        return self.db

    def _resolve_output_dir(self) -> Path:
        if self.output_dir is not None:
            return self.output_dir
        return Path.cwd().joinpath(*GENERATED_DIR_PARTS)

    def _failed_run_ids(self, db: Any) -> list[str]:
        if not db["runs"].exists():
            return []
        seen: list[str] = []
        marked = {
            str(row["id"])
            for row in db["runs"].rows_where(where="status = ?", where_args=["failed"])
        }
        seen.extend(sorted(marked))
        if db["events"].exists():
            for failure_type in FAILURE_TYPES:
                for row in db["events"].rows_where(
                    where='"type" = ?',
                    where_args=[failure_type],
                ):
                    rid = str(row.get("run_id") or "")
                    if rid and rid not in marked and rid not in seen:
                        seen.append(rid)
        return seen

    def _failure_context(
        self,
        timeline: list[dict[str, Any]],
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        failure_event_id = failure.get("failure_event_id")
        failure_index = len(timeline) - 1
        for index, event in enumerate(timeline):
            if event.get("id") == failure_event_id:
                failure_index = index
                break

        # The breaking call is the last concrete call before the failure event.
        failing_call: dict[str, Any] | None = None
        for event in reversed(timeline[:failure_index]):
            if event.get("type") in CALL_TYPES:
                failing_call = event
                break

        # The triggering input is the first concrete call in the run.
        initial_call: dict[str, Any] | None = None
        for event in timeline:
            if event.get("type") in CALL_TYPES:
                initial_call = event
                break

        initial_payload = (initial_call or {}).get("payload") or {}
        failing_payload = (failing_call or {}).get("payload") or {}

        return {
            "failed_at_step": failure_index + 1,
            "failure_type": failure.get("failure_event_type"),
            "captured_input": _call_input(initial_payload),
            "failing_call_type": (failing_call or {}).get("type"),
            "failing_call_label": (
                _call_label(failing_call["type"], failing_payload)
                if failing_call
                else "unknown"
            ),
            "failing_call_payload": _safe_json(failing_payload),
        }

    def generate_from_run(self, run_id: str) -> str | None:
        """Generate a pytest test for a run if it contains a failure.

        Returns the path to the generated test file, or None if the run did
        not fail.
        """
        db = self._resolve_db()
        create_tables(db)
        failure = detect_failure(run_id, db)
        if not failure.get("failed"):
            return None

        timeline = take_snapshot(run_id, db)
        context = self._failure_context(timeline, failure)
        source = self._render_test_source(run_id, failure, context)

        output_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_package_marker(output_dir)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = output_dir / f"test_auto_{timestamp}.py"
        path.write_text(source, encoding="utf-8")
        return str(path)

    def generate_all(self) -> list[str]:
        """Generate tests for every recorded failure in the database."""
        db = self._resolve_db()
        create_tables(db)
        paths: list[str] = []
        for run_id in self._failed_run_ids(db):
            path = self.generate_from_run(run_id)
            if path:
                paths.append(path)
        return paths

    @staticmethod
    def _ensure_package_marker(output_dir: Path) -> None:
        init_file = output_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text(
                '"""Auto-generated AgentAutopsy regression tests."""\n',
                encoding="utf-8",
            )

    def _render_test_source(
        self,
        run_id: str,
        failure: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        generated_at = datetime.now(timezone.utc).isoformat()
        error_type = failure.get("error_type") or "AgentFailure"
        error_message = failure.get("message") or "agent failed silently"
        failed_at_step = context["failed_at_step"]
        failing_label = context["failing_call_label"]

        root_cause = (
            f"{error_type} at step {failed_at_step} "
            f"during {failing_label}: {error_message}"
        )

        func_name = f"test_auto_{_slug(error_type)}_{run_id.replace('-', '')[:8]}"

        captured_input_json = json.dumps(context["captured_input"], indent=4)
        failing_call_json = json.dumps(context["failing_call_payload"], indent=4)

        docstring = (
            f'"""Auto-generated regression test for AgentAutopsy.\n\n'
            f"Generated at : {generated_at}\n"
            f"Source run   : {run_id}\n"
            f"Catches      : {error_type} — {error_message}\n"
            f"Failed step  : #{failed_at_step} ({failing_label})\n"
            f"Root cause   : {root_cause}\n\n"
            f"This test re-runs your agent against the exact input that caused the\n"
            f"original failure and asserts the same failure does not happen again.\n\n"
            f"Wire it to your agent by setting the AGENTAUTOPSY_EVAL_TARGET environment\n"
            f'variable to "your_module:your_entrypoint". The entrypoint receives\n'
            f"CAPTURED_INPUT and should run the agent. If it is not set, the test is\n"
            f"skipped so your suite stays green until you connect it.\n"
            f'"""'
        )

        return f'''{docstring}

from __future__ import annotations

import importlib
import json
import os

import pytest

RUN_ID = {json.dumps(run_id)}
GENERATED_AT = {json.dumps(generated_at)}
FAILED_AT_STEP = {failed_at_step}
FAILING_CALL = {json.dumps(failing_label)}
RECORDED_ERROR_TYPE = {json.dumps(error_type)}
RECORDED_ERROR_MESSAGE = {json.dumps(error_message)}

# The exact input that triggered the original failure.
CAPTURED_INPUT = json.loads(
    """{captured_input_json}"""
)

# The tool / LLM call that broke during the original run.
FAILING_CALL_PAYLOAD = json.loads(
    """{failing_call_json}"""
)


def _load_target():
    """Resolve the agent entrypoint from AGENTAUTOPSY_EVAL_TARGET (module:function)."""
    spec = os.environ.get("AGENTAUTOPSY_EVAL_TARGET")
    if not spec or ":" not in spec:
        return None
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr, None)


def {func_name}() -> None:
    """Regression guard: {error_type} must not reproduce on CAPTURED_INPUT."""
    target = _load_target()
    if target is None:
        pytest.skip(
            "Set AGENTAUTOPSY_EVAL_TARGET='module:function' to run this regression "
            "test against your agent."
        )

    try:
        result = target(CAPTURED_INPUT)
    except Exception as exc:  # noqa: BLE001 - we assert on the recorded failure
        assert type(exc).__name__ != RECORDED_ERROR_TYPE, (
            f"Regression: {{RECORDED_ERROR_TYPE}} happened again at step "
            f"{{FAILED_AT_STEP}} ({{FAILING_CALL}}): {{exc}}"
        )
        raise

    # A returned error marker counts as the same silent failure recurring.
    if isinstance(result, dict):
        assert not result.get("error"), (
            f"Regression: agent returned an error for the recorded input "
            f"(step {{FAILED_AT_STEP}}, {{FAILING_CALL}}): {{result.get('error')}}"
        )
'''


def get_active_generator() -> EvalGenerator | None:
    generator = _eval_context.get("generator")
    return generator if isinstance(generator, EvalGenerator) else None


def generate_eval_for_run(run_id: str, db: Any | None = None) -> str | None:
    """Generate an eval for a single run using the active or a new generator."""
    generator = get_active_generator()
    if generator is None:
        generator = EvalGenerator(db=db)
    elif db is not None:
        generator.db = db
    try:
        return generator.generate_from_run(run_id)
    except Exception:
        return None


if __name__ == "__main__":
    from agentautopsy.db import insert_event, insert_run

    db = get_db()
    create_tables(db)
    run_id = insert_run(db, agent_name="demo")
    insert_event(db, run_id, "llm_call", {"model": "gpt-4", "messages": ["hi"]})
    insert_event(db, run_id, "tool_call", {"tool": "search", "input": {"q": "agents"}})
    insert_event(
        db,
        run_id,
        "error",
        {"error_type": "TimeoutError", "message": "request timed out after 30s"},
    )
    path = EvalGenerator(db=db).generate_from_run(run_id)
    print(f"Generated eval: {path}")
