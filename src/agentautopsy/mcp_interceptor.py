import json
import subprocess
import sys
import threading
from typing import Any

from agentautopsy.db import create_tables, get_db, insert_event, insert_run


def _log_event(db: Any, run_id: str, message: dict):
    """Parse JSON-RPC and log it to the AgentAutopsy DB."""
    try:
        # Check for tool call request
        if message.get("method") == "tools/call":
            params = message.get("params", {})
            insert_event(
                db,
                run_id,
                "mcp_tool_call",
                {
                    "tool_name": params.get("name"),
                    "arguments": params.get("arguments"),
                    "message_id": message.get("id"),
                },
            )
        # Check for response
        elif "result" in message or "error" in message:
            insert_event(
                db,
                run_id,
                "mcp_response",
                {
                    "message_id": message.get("id"),
                    "result": message.get("result"),
                    "error": message.get("error"),
                },
            )
        # Check for initialize
        elif message.get("method") == "initialize":
            insert_event(db, run_id, "mcp_initialize", message.get("params", {}))
    except Exception:
        # Ignore logging errors so we don't break the proxy
        pass


def _stream_reader(in_stream, out_stream, run_id, db, direction):
    """Read line by line from in_stream, parse, and write to out_stream."""
    for line in iter(in_stream.readline, b""):
        try:
            line_str = line.decode("utf-8").strip()
            if line_str:
                message = json.loads(line_str)
                _log_event(db, run_id, message)
        except Exception:
            pass  # Skip non-JSON lines or parsing errors

        try:
            out_stream.write(line)
            out_stream.flush()
        except OSError:
            break


def run_mcp_proxy(command: list[str]):
    """Run the MCP server as a subprocess and proxy its stdio."""
    db = get_db()
    create_tables(db)
    run_id = insert_run(db, agent_name="mcp_server")

    print(
        f"[AgentAutopsy] Intercepting MCP server: {' '.join(command)}", file=sys.stderr
    )

    # Start the subprocess
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # pass stderr directly through
        bufsize=0,  # unbuffered
    )

    # Thread to proxy Client stdin -> Server stdin
    t_in = threading.Thread(
        target=_stream_reader,
        args=(sys.stdin.buffer, proc.stdin, run_id, db, "client_to_server"),
        daemon=True,
    )

    # Thread to proxy Server stdout -> Client stdout
    t_out = threading.Thread(
        target=_stream_reader,
        args=(proc.stdout, sys.stdout.buffer, run_id, db, "server_to_client"),
        daemon=True,
    )

    t_in.start()
    t_out.start()

    proc.wait()
