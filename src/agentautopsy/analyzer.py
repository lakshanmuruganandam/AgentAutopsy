"""Fix analyzer for AgentAutopsy."""

import json
import os
import re
import difflib
from statistics import mean
from typing import Any

import anthropic

from agentautopsy.alerts import send_slack_alert
from agentautopsy.db import get_db


def _parse_analysis(text: str) -> tuple[str, str]:
    root_cause = ""
    fix = ""
    for line in text.splitlines():
        if line.startswith("ROOT CAUSE:"):
            root_cause = line[len("ROOT CAUSE:") :].strip()
        elif line.startswith("FIX:"):
            fix = line[len("FIX:") :].strip()
    if not root_cause:
        match = re.search(r"ROOT CAUSE:\s*(.+)", text, re.IGNORECASE)
        if match:
            root_cause = match.group(1).strip()
    if not fix:
        match = re.search(r"FIX:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if match:
            fix = match.group(1).strip()
    return root_cause, fix


def _ensure_divergence_column(db: Any) -> None:
    if not db["runs"].exists():
        return
    existing = {column.name for column in db["runs"].columns}
    if "divergence" not in existing:
        db["runs"].add_column("divergence", str)


def _parse_event_payload(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if raw else {}
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _load_run_events(db: Any, run_id: str) -> list[dict[str, Any]]:
    if not db["events"].exists():
        return []
    events: list[dict[str, Any]] = []
    for row in db["events"].rows_where(
        where="run_id = ?",
        where_args=[run_id],
        order_by="timestamp",
    ):
        events.append(
            {
                "type": row["type"],
                "payload": _parse_event_payload(row.get("payload")),
                "token_input": row.get("token_input"),
                "token_output": row.get("token_output"),
                "cassette_size": len(row["cassette"])
                if row.get("cassette") is not None
                else 0,
            }
        )
    return events


def _run_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    tool_sequence: list[str] = []
    total_tokens = 0
    response_lengths: list[int] = []
    error_signatures: list[str] = []

    for event in events:
        ev_type = event["type"]
        payload = event["payload"]
        if ev_type == "tool_call":
            tool_sequence.append(str(payload.get("tool", "unknown_tool")))
        if ev_type == "error":
            error_signatures.append(
                f"{payload.get('error_type', 'Error')}: {payload.get('message', '')}"
            )
        token_input = event.get("token_input")
        token_output = event.get("token_output")
        if token_input is not None:
            total_tokens += int(token_input)
        if token_output is not None:
            total_tokens += int(token_output)
        if ev_type == "llm_response":
            if token_output is not None:
                response_lengths.append(int(token_output))
            elif event.get("cassette_size"):
                response_lengths.append(int(event["cassette_size"]))

    avg_response_length = (
        int(round(mean(response_lengths))) if response_lengths else 0
    )
    return {
        "tool_sequence": tuple(tool_sequence),
        "tool_sequence_label": " → ".join(tool_sequence) if tool_sequence else "(none)",
        "total_tokens": total_tokens,
        "avg_response_length": avg_response_length,
        "error_signatures": error_signatures,
        "has_error": bool(error_signatures),
    }


def _most_common_tool_sequence(profiles: list[dict[str, Any]]) -> tuple[str, ...] | None:
    if not profiles:
        return None
    counts: dict[tuple[str, ...], int] = {}
    for profile in profiles:
        sequence = profile["tool_sequence"]
        counts[sequence] = counts.get(sequence, 0) + 1
    return max(counts, key=counts.get)


def _compute_divergences(db: Any, run_id: str) -> list[dict[str, str]]:
    if not db["runs"].exists():
        return []

    current_events = _load_run_events(db, run_id)
    current = _run_profile(current_events)

    successful_profiles: list[dict[str, Any]] = []
    historical_errors: set[str] = set()

    for row in db["runs"].rows_where(order_by="start_time"):
        other_id = row["id"]
        if other_id == run_id:
            continue
        events = _load_run_events(db, other_id)
        profile = _run_profile(events)
        historical_errors.update(profile["error_signatures"])
        if not profile["has_error"]:
            successful_profiles.append(profile)

    divergences: list[dict[str, str]] = []
    if not successful_profiles:
        return divergences

    baseline_sequence = _most_common_tool_sequence(successful_profiles)
    if (
        baseline_sequence is not None
        and current["tool_sequence"] != baseline_sequence
    ):
        baseline_label = " → ".join(baseline_sequence) if baseline_sequence else "(none)"
        divergences.append(
            {
                "what_changed": "Tool call sequence differs from usual successful runs",
                "previous": baseline_label,
                "current": current["tool_sequence_label"],
            }
        )

    baseline_tokens = int(round(mean(p["total_tokens"] for p in successful_profiles)))
    if baseline_tokens > 0 and current["total_tokens"] >= baseline_tokens * 2:
        divergences.append(
            {
                "what_changed": "Unusual token spike (2x normal usage)",
                "previous": str(baseline_tokens),
                "current": str(current["total_tokens"]),
            }
        )

    baseline_response_lengths = [
        p["avg_response_length"]
        for p in successful_profiles
        if p["avg_response_length"] > 0
    ]
    if baseline_response_lengths and current["avg_response_length"] > 0:
        baseline_response = int(round(mean(baseline_response_lengths)))
        if baseline_response > 0:
            ratio = current["avg_response_length"] / baseline_response
            if ratio >= 1.5 or ratio <= 0.5:
                divergences.append(
                    {
                        "what_changed": "Model response length changed significantly",
                        "previous": f"{baseline_response} tokens/bytes (avg)",
                        "current": f"{current['avg_response_length']} tokens/bytes (avg)",
                    }
                )

    if current["error_signatures"]:
        current_error = current["error_signatures"][0]
        if historical_errors and current_error not in historical_errors:
            previous_error = next(iter(sorted(historical_errors)))
            divergences.append(
                {
                    "what_changed": "Different error pattern than before",
                    "previous": previous_error,
                    "current": current_error,
                }
            )

    return divergences


def _message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
                else:
                    parts.append(json.dumps(part, default=str))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, indent=2, default=str)
    return str(content)


def _extract_prompt_lines(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event["type"] != "llm_call":
            continue
        payload = event["payload"]
        system = payload.get("system")
        if system:
            lines.append(f"[system] {_message_content(system)}")
        for message in payload.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "unknown"))
            content = _message_content(message.get("content"))
            if content:
                lines.extend(f"[{role}] {part}" for part in content.splitlines() or [content])
    return lines


def _find_previous_run_id(db: Any, run_id: str) -> str | None:
    if not db["runs"].exists():
        return None
    previous_id: str | None = None
    for row in db["runs"].rows_where(order_by="start_time"):
        if row["id"] == run_id:
            return previous_id
        previous_id = row["id"]
    return None


def _compute_line_diff(
    previous_lines: list[str], current_lines: list[str]
) -> list[dict[str, str]]:
    matcher = difflib.SequenceMatcher(None, previous_lines, current_lines)
    diff_lines: list[dict[str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in previous_lines[i1:i2]:
                diff_lines.append({"type": "unchanged", "text": line})
        elif tag == "delete":
            for line in previous_lines[i1:i2]:
                diff_lines.append({"type": "removed", "text": line})
        elif tag == "insert":
            for line in current_lines[j1:j2]:
                diff_lines.append({"type": "added", "text": line})
        elif tag == "replace":
            old_lines = previous_lines[i1:i2]
            new_lines = current_lines[j1:j2]
            max_len = max(len(old_lines), len(new_lines))
            for index in range(max_len):
                old_line = old_lines[index] if index < len(old_lines) else None
                new_line = new_lines[index] if index < len(new_lines) else None
                if old_line is not None and new_line is not None:
                    if old_line == new_line:
                        diff_lines.append({"type": "unchanged", "text": old_line})
                    else:
                        diff_lines.append(
                            {
                                "type": "changed",
                                "text": new_line,
                                "previous": old_line,
                            }
                        )
                elif old_line is not None:
                    diff_lines.append({"type": "removed", "text": old_line})
                elif new_line is not None:
                    diff_lines.append({"type": "added", "text": new_line})

    return diff_lines


def diff_prompts(run_id: str) -> dict[str, Any]:
    """Compare prompt lines in the current run against the previous run."""
    db = get_db()
    if not db["runs"].exists():
        return {"has_previous": False, "lines": []}

    previous_run_id = _find_previous_run_id(db, run_id)
    if not previous_run_id:
        return {"has_previous": False, "lines": []}

    previous_lines = _extract_prompt_lines(_load_run_events(db, previous_run_id))
    current_lines = _extract_prompt_lines(_load_run_events(db, run_id))
    return {
        "has_previous": True,
        "previous_run_id": previous_run_id,
        "lines": _compute_line_diff(previous_lines, current_lines),
    }


def detect_divergence(run_id: str) -> list[dict[str, str]]:
    """Compare a run against prior successful runs and persist divergence results."""
    db = get_db()
    _ensure_divergence_column(db)
    divergences = _compute_divergences(db, run_id)
    if db["runs"].get(run_id) is not None:
        db["runs"].update(run_id, {"divergence": json.dumps(divergences)})
    return divergences


def analyze(pruned_snapshot, failure):
    lines = [
        f"Error: {failure['error_type']}: {failure['message']}",
        "Trace:"
    ]
    for e in pruned_snapshot:
        lines.append(f"- [{e['type']}] {e['payload']}")
    user_message = "\n".join(lines)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=(
            "You are AgentAutopsy, an expert AI agent debugger. "
            "Given a trace of an AI agent's decisions leading up to a failure, output:\n"
            "FAILURE NODE: <exact step that caused failure>\n"
            "ROOT CAUSE: <one sentence>\n"
            "FIX: <concrete patch or instruction>"
        ),
        messages=[{"role": "user", "content": user_message}]
    )
    analysis = response.content[0].text

    webhook_url = os.environ.get("AGENTAUTOPSY_SLACK_WEBHOOK")
    if webhook_url:
        root_cause, fix = _parse_analysis(analysis)
        error = f"{failure['error_type']}: {failure['message']}"
        run_id = failure.get("run_id", "unknown")
        try:
            send_slack_alert(webhook_url, run_id, error, root_cause, fix)
        except Exception as exc:
            print(f"[AgentAutopsy] Slack alert failed: {exc}")

    return analysis


if __name__ == "__main__":
    fake_snapshot = [
        {"id": "1", "type": "llm_call", "payload": {"model": "gpt-4", "messages": [{"role": "user", "content": "fetch data from api"}]}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:01"},
        {"id": "2", "type": "error", "payload": {"error_type": "TimeoutError", "message": "request timed out after 30s"}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:02"},
    ]
    fake_failure = {
        "failed": True,
        "error_type": "TimeoutError",
        "message": "request timed out after 30s",
        "run_id": "test-123",
        "failure_event_id": "2"
    }
    result = analyze(fake_snapshot, fake_failure)
    print(result)
