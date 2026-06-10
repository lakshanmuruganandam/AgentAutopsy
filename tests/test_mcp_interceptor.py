import json
import os
import subprocess

from agentautopsy.db import get_db


def test_mcp_interceptor():
    # Prepare requests
    req1 = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        + "\n"
    )
    req2 = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "fetch_data", "arguments": {"id": 123}},
            }
        )
        + "\n"
    )
    req3 = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "exit"}) + "\n"

    input_data = req1 + req2 + req3

    # Run the proxy via CLI
    env = os.environ.copy()
    proc = subprocess.run(
        ["agentautopsy", "mcp", "python", "tests/dummy_mcp_server.py"],
        input=input_data.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=5,
    )

    # Ensure proxy exited cleanly
    assert proc.returncode == 0

    # Ensure output is passed through cleanly
    stdout = proc.stdout.decode("utf-8").strip().split("\n")
    assert len(stdout) == 3
    resp1 = json.loads(stdout[0])
    resp2 = json.loads(stdout[1])
    assert resp1["id"] == 1
    assert "protocolVersion" in resp1["result"]
    assert resp2["id"] == 2
    assert resp2["result"]["content"][0]["text"] == "success"

    # Verify that the events were written to the database
    db = get_db()

    # We should have a run for the mcp_server
    runs = list(
        db["runs"].rows_where(
            "agent_name = ?", ["mcp_server"], order_by="start_time DESC"
        )
    )
    assert len(runs) >= 1
    run_id = runs[0]["id"]

    events = list(db["events"].rows_where("run_id = ?", [run_id]))

    # We expect 4 events: initialize, response(1), tool_call, response(2)
    # The order might not be strictly sequential due to threads, but they should exist
    event_types = [e["type"] for e in events]
    assert "mcp_initialize" in event_types
    assert "mcp_tool_call" in event_types
    assert "mcp_response" in event_types

    # Find the tool call event
    tool_calls = [e for e in events if e["type"] == "mcp_tool_call"]
    assert len(tool_calls) == 1
    tc_payload = json.loads(tool_calls[0]["payload"])
    assert tc_payload["tool_name"] == "fetch_data"
    assert tc_payload["arguments"]["id"] == 123
