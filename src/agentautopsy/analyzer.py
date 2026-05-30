"""Fix analyzer for AgentAutopsy."""

import os
import re

import anthropic

from agentautopsy.alerts import send_slack_alert


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
