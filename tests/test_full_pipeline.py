import os
import tempfile
import unittest
from unittest.mock import patch

import agentautopsy
from agentautopsy.db import get_db, insert_event


class TestFullPipeline(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)

    def tearDown(self):
        os.chdir(self._previous_cwd)
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    @patch("agentautopsy.analyzer._get_anthropic_client", return_value=None)
    def test_failed_run_records_events_without_anthropic_client(self, _mock_get_client):
        agentautopsy.watch()
        db = get_db()
        runs = list(db["runs"].rows)
        run_id = runs[-1]["id"]
        insert_event(
            db,
            run_id,
            "llm_call",
            {"model": "gpt-4", "messages": [{"role": "user", "content": "fetch data"}]},
        )
        insert_event(
            db,
            run_id,
            "error",
            {"error_type": "TimeoutError", "message": "request timed out after 30s"},
        )

        from agentautopsy.detector import detect_failure

        failure = detect_failure(run_id, db)
        self.assertTrue(failure["failed"])
        self.assertEqual(failure["error_type"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
