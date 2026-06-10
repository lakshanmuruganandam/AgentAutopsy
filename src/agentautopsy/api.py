"""HTTP API for AgentAutopsy — Monadix provider agent."""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from agentautopsy.analyzer import _get_anthropic_client, _parse_analysis

API_DEFAULT_HOST = "127.0.0.1"
API_DEFAULT_PORT = 8787

_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})


def _parse_confidence(text: str, root_cause: str, fix: str) -> str:
    for line in text.splitlines():
        if line.upper().startswith("CONFIDENCE:"):
            value = line.split(":", 1)[-1].strip().lower()
            if value in _CONFIDENCE_VALUES:
                return value
    match = re.search(r"CONFIDENCE:\s*(high|medium|low)", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if root_cause and fix and not root_cause.startswith("WARNING"):
        return "high"
    if root_cause or fix:
        return "medium"
    return "low"


def _summarize_logs(logs: str) -> tuple[str, str]:
    error_type = "AgentFailure"
    message = "Agent run failed"

    patterns = [
        r"^(E\s+[\w]+Error:.+)$",
        r"^(FAILED .+)$",
        r"^(AssertionError:.+)$",
        r"^(\w+Error:.+)$",
        r"^(ERROR:.+)$",
    ]
    for line in reversed(logs.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in patterns:
            match = re.match(pattern, stripped)
            if match:
                message = match.group(1)[:2000]
                head = message.split(":", 1)[0]
                if head.endswith("Error"):
                    error_type = head
                return error_type, message

    lines = [line.strip() for line in logs.splitlines() if line.strip()]
    if lines:
        message = lines[-1][:2000]
    return error_type, message


def analyze_request(task: str, logs: str) -> dict[str, str]:
    """Analyze pasted logs and return root cause, fix, and confidence."""
    task = (task or "debug this agent failure").strip()
    logs = (logs or "").strip()
    if not logs:
        return {
            "root_cause": "No logs provided",
            "fix": "Paste the agent failure logs in the logs field",
            "confidence": "low",
        }

    error_type, message = _summarize_logs(logs)
    client = _get_anthropic_client()
    if client is None:
        return {
            "root_cause": f"{error_type}: {message}",
            "fix": (
                "Set ANTHROPIC_API_KEY to enable AI analysis. "
                "Review the error in the logs and fix the failing step."
            ),
            "confidence": "low",
        }

    user_message = (
        f"Task: {task}\n\n"
        f"Detected error: {error_type}: {message}\n\n"
        f"Logs:\n{logs[-12000:]}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=(
                "You are AgentAutopsy, an expert AI agent debugger. "
                "Given a debugging task and failure logs, output exactly:\n"
                "ROOT CAUSE: <one sentence>\n"
                "FIX: <concrete patch or instruction>\n"
                "CONFIDENCE: <high|medium|low>"
            ),
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return {
            "root_cause": f"{error_type}: {message}",
            "fix": f"Analysis failed ({type(exc).__name__}: {exc})",
            "confidence": "low",
        }

    if not response.content:
        return {
            "root_cause": f"{error_type}: {message}",
            "fix": "Anthropic returned an empty response",
            "confidence": "low",
        }

    analysis = response.content[0].text
    root_cause, fix = _parse_analysis(analysis)
    if not root_cause:
        root_cause = f"{error_type}: {message}"
    if not fix:
        fix = analysis.strip() or "Review the logs and fix the failing step manually"
    confidence = _parse_confidence(analysis, root_cause, fix)
    return {
        "root_cause": root_cause,
        "fix": fix,
        "confidence": confidence,
    }


class _AnalyzeRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/health"):
            self._send_json(200, {"status": "ok", "service": "agentautopsy"})
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/analyze":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        task = str(body.get("task") or "")
        logs = str(body.get("logs") or "")
        result = analyze_request(task, logs)
        self._send_json(200, result)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_api_server(
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    """Start the AgentAutopsy HTTP API (blocks until interrupted)."""
    bind_host = host or os.environ.get("AGENTAUTOPSY_API_HOST", API_DEFAULT_HOST)
    bind_port = int(port or os.environ.get("AGENTAUTOPSY_API_PORT", API_DEFAULT_PORT))
    server = ThreadingHTTPServer((bind_host, bind_port), _AnalyzeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"AgentAutopsy API listening on http://{bind_host}:{bind_port}")
    print('POST /analyze  —  {"task": "...", "logs": "..."}')
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("\nAPI stopped.")
    finally:
        server.shutdown()
    return server


if __name__ == "__main__":
    start_api_server()
