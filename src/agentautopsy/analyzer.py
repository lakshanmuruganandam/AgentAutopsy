"""Fix analyzer for AgentAutopsy."""

import anthropic


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
    return response.content[0].text


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
