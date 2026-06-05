"""Tests for HTTP error capture and replay output."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import httpx

from agentautopsy.db import create_tables, get_db, insert_event, insert_run, mark_run_failed
from agentautopsy.detector import detect_failure
from agentautopsy.interceptor import start_http_interceptor
from agentautopsy.reporter import print_report


class TestHttpError(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="openai-client")

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        try:
            self.db.conn.close()
        except Exception:
            pass
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    def test_failed_http_call_records_http_error_and_failed_status(self) -> None:
        start_http_interceptor(self.run_id, self.db)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection failed", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        with self.assertRaises(httpx.ConnectError):
            client.post("https://api.openai.com/v1/chat/completions")

        event_types = [
            row["type"]
            for row in self.db["events"].rows_where(
                where="run_id = ?",
                where_args=[self.run_id],
                order_by="timestamp",
            )
        ]
        self.assertEqual(event_types.count("http_request"), 1)
        self.assertIn("http_error", event_types)

        failure = detect_failure(self.run_id, self.db)
        self.assertTrue(failure["failed"])
        self.assertEqual(failure["error_type"], "ConnectError")

        error_rows = list(
            self.db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[self.run_id, "http_error"],
            )
        )
        self.assertEqual(len(error_rows), 1)
        payload = json.loads(error_rows[0]["payload"])
        self.assertEqual(payload["exception_type"], "ConnectError")
        self.assertIn("connection failed", payload["message"])
        self.assertIn("traceback", payload)
        self.assertIn("chat/completions", payload["url"])

        run = self.db["runs"].get(self.run_id)
        self.assertEqual(run["status"], "failed")

    def test_replay_output_shows_http_requests_error_root_cause_and_status(self) -> None:
        insert_event(
            self.db,
            self.run_id,
            "http_request",
            {
                "method": "POST",
                "url": "https://api.openai.com/v1/chat/completions",
            },
        )
        insert_event(
            self.db,
            self.run_id,
            "http_request",
            {
                "method": "POST",
                "url": "https://api.openai.com/v1/chat/completions",
            },
        )
        insert_event(
            self.db,
            self.run_id,
            "http_error",
            {
                "exception_type": "APIConnectionError",
                "error_type": "APIConnectionError",
                "message": "Connection error.",
                "traceback": "Traceback (most recent call last):\n  ...",
                "url": "https://api.openai.com/v1/chat/completions",
                "method": "POST",
            },
        )
        mark_run_failed(self.db, self.run_id)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_report(self.run_id, self.db)
        output = buffer.getvalue()

        self.assertIn("POST /v1/chat/completions", output)
        self.assertEqual(output.count("POST /v1/chat/completions"), 2)
        self.assertIn("ERROR: APIConnectionError", output)
        self.assertIn("Root cause: OpenAI connection failed", output)
        self.assertIn("Run status: failed", output)


if __name__ == "__main__":
    unittest.main()
