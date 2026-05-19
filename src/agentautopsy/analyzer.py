# fix analyzer — built day 10c:\Users\abhishek\.cursor\projects\empty-window\agentautopsy\src\agentautopsy\analyzer.py
"""Fix analyzer for AgentAutopsy."""

import openai


def analyze(pruned_snapshot, failure):
    lines = [
        f"Error: {failure['error_type']}: {failure['message']}",
        "Trace:"
    ]
    for e in pruned_snapshot:
        lines.append(f"- [{e['type']}] {e['payload']}")
    user_message = "\n".join(lines)

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are AgentAutopsy, an expert AI agent debugger. "
                    "You will be given a trace of an AI agent's decisions leading up to a failure. "
                    "Your job is to:\n"
                    "1. Identify the exact step where the agent went wrong\n"
                    "2. Explain why it failed in one sentence\n"
                    "3. Output a concrete fix as a code patch or instruction\n\n"
                    "Be specific. Be brief. Output in this exact format:\n"
                    "FAILURE NODE: <the step that caused the failure>\n"
                    "ROOT CAUSE: <one sentence explanation>\n"
                    "FIX: <concrete patch or instruction to fix it>"
                )
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    fake_snapshot = [
        {
            "id": "1",
            "type": "llm_call",
            "payload": {"model": "gpt-4", "messages": [{"role": "user", "content": "fetch data from api"}]},
            "cassette_size": 0,
            "timestamp": "2024-01-01T00:00:01"
        },
        {
            "id": "2",
            "type": "error",
            "payload": {"error_type": "TimeoutError", "message": "request timed out after 30s"},
            "cassette_size": 0,
            "timestamp": "2024-01-01T00:00:02"
        },
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