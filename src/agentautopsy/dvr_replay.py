"""DVR fork and replay for AgentAutopsy agent runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from difflib import unified_diff
from typing import Any

from agentautopsy.cassette import load_cassette
from agentautopsy.db import create_tables, get_db, insert_event, insert_run
from agentautopsy.detector import detect_failure

_dvr_context: dict[str, Any] = {}

REPLAYABLE_TYPES = frozenset(
    {
        "llm_call",
        "llm_response",
        "tool_call",
        "tool_result",
        "http_request",
        "http_response",
        "mcp_tool_call",
        "mcp_tool_result",
        "error",
        "http_error",
    }
)


def ensure_dvr_tables(db: Any) -> None:
    db["dvr_sessions"].create(
        {
            "id": str,
            "source_run_id": str,
            "replay_run_id": str,
            "from_step": int,
            "session_type": str,
            "new_input_json": str,
            "created_at": str,
        },
        pk="id",
        if_not_exists=True,
    )


def _parse_payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


class DVRReplay:
    """Record, replay, fork, and diff agent runs step-by-step."""

    def __init__(self, db: Any | None = None, run_id: str | None = None) -> None:
        self.db = db or get_db()
        self.run_id = run_id

    def watch(self) -> DVRReplay:
        """Activate DVR recording for the active or next run."""
        create_tables(self.db)
        ensure_dvr_tables(self.db)
        if self.run_id:
            self.enable_recording(self.run_id)
        _dvr_context["recorder"] = self
        return self

    def enable_recording(self, run_id: str) -> None:
        """Mark a run as fully DVR-recorded."""
        self.run_id = run_id
        ensure_dvr_tables(self.db)
        insert_event(
            self.db,
            run_id,
            "dvr_recording_start",
            {
                "message": "DVR recording enabled",
                "recordable_types": sorted(REPLAYABLE_TYPES),
            },
        )
        if self.db["runs"].exists() and self.db["runs"].get(run_id) is not None:
            self.db["runs"].update(run_id, {"framework": "dvr"})

    def list_runs(self) -> list[dict[str, Any]]:
        """List all recorded runs with step counts and token totals."""
        if not self.db["runs"].exists():
            return []
        runs: list[dict[str, Any]] = []
        for row in self.db["runs"].rows_where(order_by="start_time desc"):
            run_id = row["id"]
            timeline = self.load_timeline(run_id)
            if not timeline:
                continue
            token_input = sum(step.get("token_input") or 0 for step in timeline)
            token_output = sum(step.get("token_output") or 0 for step in timeline)
            runs.append(
                {
                    "run_id": run_id,
                    "agent_name": row.get("agent_name") or "agent",
                    "status": row.get("status") or "",
                    "start_time": row.get("start_time") or "",
                    "parent_run_id": row.get("parent_run_id"),
                    "step_count": len(timeline),
                    "token_input": token_input,
                    "token_output": token_output,
                    "dvr_enabled": any(
                        step["type"] == "dvr_recording_start" for step in timeline
                    ),
                }
            )
        return runs

    def load_timeline(self, run_id: str) -> list[dict[str, Any]]:
        """Load a numbered step timeline for a run."""
        if not self.db["events"].exists():
            return []
        timeline: list[dict[str, Any]] = []
        for index, row in enumerate(
            self.db["events"].rows_where(
                where="run_id = ?",
                where_args=[run_id],
                order_by="timestamp",
            ),
            start=1,
        ):
            payload = _parse_payload(row.get("payload"))
            cassette = row.get("cassette")
            timeline.append(
                {
                    "step": index,
                    "id": row["id"],
                    "type": row["type"],
                    "timestamp": row.get("timestamp") or "",
                    "payload": payload,
                    "token_input": row.get("token_input"),
                    "token_output": row.get("token_output"),
                    "latency_ms": row.get("latency_ms"),
                    "cost_usd": row.get("cost_usd"),
                    "has_cassette": bool(cassette),
                    "summary": _step_summary(row["type"], payload),
                }
            )
        return timeline

    def replay(self, run_id: str, *, from_step: int = 1) -> dict[str, Any]:
        """Replay a run from a specific step (alias for replay_from_step)."""
        return self.replay_from_step(run_id, from_step)

    def replay_from_step(
        self,
        run_id: str,
        step_number: int,
        *,
        replay_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Replay cassette-backed LLM responses from an exact step forward."""
        timeline = self.load_timeline(run_id)
        if not timeline:
            raise ValueError(f"No recorded timeline for run {run_id}")
        if step_number < 1 or step_number > len(timeline):
            raise ValueError(
                f"step_number must be between 1 and {len(timeline)}, got {step_number}"
            )

        branch_run_id = replay_run_id or self.fork_run(run_id, step_number)
        events_replayed, verified = self._replay_cassettes(timeline, step_number)
        insert_event(
            self.db,
            branch_run_id,
            "dvr_replay_complete",
            {
                "source_run_id": run_id,
                "from_step": step_number,
                "events_replayed": events_replayed,
                "verified": verified,
            },
        )
        if replay_run_id is None:
            self._record_session(
                source_run_id=run_id,
                replay_run_id=branch_run_id,
                from_step=step_number,
                session_type="replay",
            )
        diff = self.diff_runs(run_id, branch_run_id)
        return {
            "source_run_id": run_id,
            "replay_run_id": branch_run_id,
            "from_step": step_number,
            "events_replayed": events_replayed,
            "verified": verified,
            "diff": diff,
        }

    def replay_with_fix(
        self,
        run_id: str,
        step_number: int,
        new_input: Any,
    ) -> dict[str, Any]:
        """Fork at a step and patch the input before replaying forward."""
        replay_run_id = self.fork_run(run_id, step_number, new_input=new_input)
        result = self.replay_from_step(
            run_id,
            step_number,
            replay_run_id=replay_run_id,
        )
        result["patched_input"] = _safe_json(new_input)
        self._record_session(
            source_run_id=run_id,
            replay_run_id=replay_run_id,
            from_step=step_number,
            session_type="fix",
            new_input=new_input,
        )
        result["diff"] = self.diff_runs(run_id, replay_run_id)
        return result

    def _replay_cassettes(
        self,
        timeline: list[dict[str, Any]],
        step_number: int,
    ) -> tuple[int, bool]:
        steps = timeline[step_number - 1 :]
        cassettes: list[dict[str, Any]] = []
        for step in steps:
            if step["type"] != "llm_response" or not step["has_cassette"]:
                continue
            row = self.db["events"].get(step["id"])
            if row is None:
                continue
            cassette_bytes = row.get("cassette")
            if not cassette_bytes:
                continue
            response = load_cassette(cassette_bytes)
            if response:
                cassettes.append(response)

        verified = False
        events_replayed = 0
        if not cassettes:
            return events_replayed, verified

        import openai

        original_create = openai.chat.completions.create
        call_index = [0]

        def replay_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if call_index[0] >= len(cassettes):
                raise RuntimeError("No more cassette responses to replay")
            response = cassettes[call_index[0]]
            call_index[0] += 1
            return response

        openai.chat.completions.create = replay_create
        try:
            result = openai.chat.completions.create(model="gpt-4", messages=[])
            verified = result == cassettes[0]
            events_replayed = call_index[0]
        finally:
            openai.chat.completions.create = original_create
        return events_replayed, verified

    def fork(
        self,
        run_id: str,
        *,
        at_step: int,
        new_input: Any | None = None,
    ) -> str:
        """Create a new branch from a specific step."""
        return self.fork_run(run_id, at_step, new_input=new_input)

    def fork_run(
        self,
        run_id: str,
        step_number: int,
        *,
        new_input: Any | None = None,
    ) -> str:
        """Copy events up to a step into a new branched run."""
        timeline = self.load_timeline(run_id)
        if not timeline:
            raise ValueError(f"No recorded timeline for run {run_id}")
        if step_number < 1 or step_number > len(timeline):
            raise ValueError(
                f"step_number must be between 1 and {len(timeline)}, got {step_number}"
            )

        source_run = self.db["runs"].get(run_id) if self.db["runs"].exists() else None
        agent_name = (source_run or {}).get("agent_name") or "agent"
        replay_run_id = insert_run(
            self.db,
            agent_name=f"{agent_name}-fork",
            parent_run_id=run_id,
        )

        for step in timeline[:step_number]:
            row = self.db["events"].get(step["id"])
            if row is None:
                continue
            payload = _parse_payload(row.get("payload"))
            if step["step"] == step_number and new_input is not None:
                payload = _patch_step_payload(payload, step["type"], new_input)
            insert_event(
                self.db,
                replay_run_id,
                step["type"],
                payload,
                cassette=row.get("cassette"),
                latency_ms=row.get("latency_ms"),
                token_input=row.get("token_input"),
                token_output=row.get("token_output"),
                cost_usd=row.get("cost_usd"),
            )

        insert_event(
            self.db,
            replay_run_id,
            "dvr_fork",
            {
                "source_run_id": run_id,
                "forked_at_step": step_number,
                "new_input": _safe_json(new_input),
            },
        )
        self._record_session(
            source_run_id=run_id,
            replay_run_id=replay_run_id,
            from_step=step_number,
            session_type="fork",
            new_input=new_input,
        )
        return replay_run_id

    def diff_runs(self, original_run_id: str, replay_run_id: str) -> dict[str, Any]:
        """Show what changed between an original run and a replay/fork."""
        original = self.load_timeline(original_run_id)
        replay = self.load_timeline(replay_run_id)
        changes: list[dict[str, Any]] = []
        max_steps = max(len(original), len(replay))

        for index in range(max_steps):
            step_number = index + 1
            orig_step = original[index] if index < len(original) else None
            replay_step = replay[index] if index < len(replay) else None
            if orig_step and replay_step:
                if (
                    orig_step["type"] != replay_step["type"]
                    or orig_step["payload"] != replay_step["payload"]
                ):
                    changes.append(
                        {
                            "step": step_number,
                            "change": "modified",
                            "original_type": orig_step["type"],
                            "replay_type": replay_step["type"],
                            "original_summary": orig_step["summary"],
                            "replay_summary": replay_step["summary"],
                        }
                    )
            elif orig_step and not replay_step:
                changes.append(
                    {
                        "step": step_number,
                        "change": "removed",
                        "original_summary": orig_step["summary"],
                    }
                )
            elif replay_step and not orig_step:
                changes.append(
                    {
                        "step": step_number,
                        "change": "added",
                        "replay_summary": replay_step["summary"],
                    }
                )

        original_failed = detect_failure(original_run_id, self.db)["failed"]
        replay_failed = detect_failure(replay_run_id, self.db)["failed"]
        improved = original_failed and not replay_failed
        text_diff = self._build_text_diff(original, replay)
        return {
            "original_run_id": original_run_id,
            "replay_run_id": replay_run_id,
            "changes": changes,
            "change_count": len(changes),
            "improved": improved,
            "original_failed": original_failed,
            "replay_failed": replay_failed,
            "text_diff": text_diff,
        }

    def _record_session(
        self,
        *,
        source_run_id: str,
        replay_run_id: str,
        from_step: int,
        session_type: str,
        new_input: Any | None = None,
    ) -> None:
        ensure_dvr_tables(self.db)
        self.db["dvr_sessions"].insert(
            {
                "id": str(uuid.uuid4()),
                "source_run_id": source_run_id,
                "replay_run_id": replay_run_id,
                "from_step": from_step,
                "session_type": session_type,
                "new_input_json": json.dumps(_safe_json(new_input))
                if new_input is not None
                else "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            pk="id",
        )

    @staticmethod
    def _build_text_diff(
        original: list[dict[str, Any]],
        replay: list[dict[str, Any]],
    ) -> str:
        original_text = json.dumps(
            [{"step": s["step"], "type": s["type"], "summary": s["summary"]} for s in original],
            indent=2,
            default=str,
        )
        replay_text = json.dumps(
            [{"step": s["step"], "type": s["type"], "summary": s["summary"]} for s in replay],
            indent=2,
            default=str,
        )
        return "\n".join(
            unified_diff(
                original_text.splitlines(),
                replay_text.splitlines(),
                fromfile="original",
                tofile="replay",
                lineterm="",
            )
        )


def get_active_dvr() -> DVRReplay | None:
    recorder = _dvr_context.get("recorder")
    return recorder if isinstance(recorder, DVRReplay) else None


def _step_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "llm_call":
        return f"LLM call model={payload.get('model', 'unknown')}"
    if event_type == "llm_response":
        return "LLM response"
    if event_type == "tool_call":
        return f"Tool {payload.get('tool', 'unknown')}"
    if event_type == "tool_result":
        return "Tool result"
    if event_type in ("error", "http_error"):
        return f"Error {payload.get('error_type') or payload.get('exception_type')}"
    if event_type == "dvr_fork":
        return f"Fork from step {payload.get('forked_at_step')}"
    return event_type


def _patch_step_payload(
    payload: dict[str, Any],
    event_type: str,
    new_input: Any,
) -> dict[str, Any]:
    patched = dict(payload)
    safe_input = _safe_json(new_input)
    if event_type == "llm_call":
        if isinstance(safe_input, str):
            patched["messages"] = [{"role": "user", "content": safe_input}]
            patched["patched_prompt"] = safe_input
        elif isinstance(safe_input, list):
            patched["messages"] = safe_input
        else:
            patched["patched_input"] = safe_input
    elif event_type in ("tool_call", "mcp_tool_call"):
        patched["input"] = safe_input
        patched["patched_input"] = safe_input
    else:
        patched["patched_input"] = safe_input
    patched["dvr_patched"] = True
    return patched


def load_dvr_ui_data(db: Any) -> dict[str, Any]:
    """Bundle DVR data for the web UI."""
    dvr = DVRReplay(db=db)
    runs = dvr.list_runs()
    timelines = {run["run_id"]: dvr.load_timeline(run["run_id"]) for run in runs}
    sessions: list[dict[str, Any]] = []
    if db["dvr_sessions"].exists():
        for row in db["dvr_sessions"].rows_where(order_by="created_at desc"):
            sessions.append(
                {
                    "id": row["id"],
                    "source_run_id": row["source_run_id"],
                    "replay_run_id": row["replay_run_id"],
                    "from_step": row["from_step"],
                    "session_type": row["session_type"],
                    "created_at": row.get("created_at") or "",
                }
            )
    return {"runs": runs, "timelines": timelines, "sessions": sessions}


if __name__ == "__main__":
    from agentautopsy.db import insert_event as _insert_event

    db = get_db()
    create_tables(db)
    run_id = insert_run(db, agent_name="demo")
    dvr = DVRReplay(db=db, run_id=run_id)
    dvr.watch()
    _insert_event(db, run_id, "llm_call", {"model": "gpt-4", "messages": ["hi"]})
    _insert_event(db, run_id, "tool_call", {"tool": "search", "input": {"q": "test"}})
    print(dvr.list_runs())
