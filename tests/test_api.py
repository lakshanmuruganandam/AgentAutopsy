"""Tests for AgentAutopsy HTTP API."""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from agentautopsy.api import _AnalyzeRequestHandler, analyze_request


class TestAnalyzeRequest(unittest.TestCase):
    def test_empty_logs_returns_low_confidence(self) -> None:
        result = analyze_request("debug failure", "")
        self.assertEqual(result["confidence"], "low")
        self.assertIn("No logs", result["root_cause"])

    @patch.dict(os.environ, {}, clear=True)
    @patch("agentautopsy.api._get_anthropic_client", return_value=None)
    def test_missing_api_key_returns_heuristic_result(self, _mock_client) -> None:
        logs = "openai.APIConnectionError: Connection error."
        result = analyze_request("debug this agent failure", logs)
        self.assertEqual(result["confidence"], "low")
        self.assertIn("APIConnectionError", result["root_cause"])
        self.assertIn("ANTHROPIC_API_KEY", result["fix"])

    @patch("agentautopsy.api._get_anthropic_client")
    def test_successful_analysis_parses_fields(self, mock_get_client) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=(
                    "ROOT CAUSE: OpenAI connection failed\n"
                    "FIX: Add timeout=60 and verify network access\n"
                    "CONFIDENCE: high"
                )
            )
        ]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = analyze_request(
            "debug this agent failure",
            "POST /v1/chat/completions\nERROR: APIConnectionError",
        )

        self.assertEqual(result["confidence"], "high")
        self.assertIn("OpenAI connection failed", result["root_cause"])
        self.assertIn("timeout=60", result["fix"])
        mock_client.messages.create.assert_called_once()


class TestAnalyzeHandler(unittest.TestCase):
    def test_post_analyze_endpoint(self) -> None:
        with patch(
            "agentautopsy.api.analyze_request",
            return_value={
                "root_cause": "timeout",
                "fix": "increase timeout",
                "confidence": "medium",
            },
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _AnalyzeRequestHandler)
            host, port = server.server_address[:2]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = HTTPConnection(host, port, timeout=5)
                payload = json.dumps(
                    {
                        "task": "debug this agent failure",
                        "logs": "TimeoutError: timed out",
                    }
                )
                conn.request(
                    "POST",
                    "/analyze",
                    body=payload,
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(body["confidence"], "medium")
                self.assertEqual(body["root_cause"], "timeout")
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
