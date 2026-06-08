import os
import tempfile
import unittest
from unittest.mock import patch

from agentautopsy.autofix import _get_run_fix_context
from agentautopsy.db import create_tables, get_db, insert_event, insert_run


class TestAutofix(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db)

    def tearDown(self):
        os.chdir(self._previous_cwd)
        try:
            self.db.conn.close()
        except Exception:
            pass
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    def test_parse_json_response(self):
        from agentautopsy.autofix import _parse_json_response

        json_text = (
            '{"file_path": "test.py", "line": 10, "search": "foo", '
            '"replace": "bar", "test_file": "test.py"}'
        )
        result = _parse_json_response(json_text)
        self.assertEqual(result["file_path"], "test.py")

    @patch("agentautopsy.autofix.analyze", return_value="WARNING: ANTHROPIC_API_KEY is not set.")
    def test_get_run_fix_context_when_analyze_returns_warning(self, _mock_analyze):
        insert_event(
            self.db,
            self.run_id,
            "error",
            {"error_type": "TimeoutError", "message": "request timed out after 30s"},
        )

        context = _get_run_fix_context(self.db, self.run_id)

        self.assertEqual(context["failure"]["error_type"], "TimeoutError")
        self.assertIn("WARNING", context["analysis"])
        self.assertIn("WARNING", context["fix"])

    @patch("agentautopsy.autofix.analyze")
    def test_get_run_fix_context_uses_analyze_when_cache_miss(self, mock_analyze):
        mock_analyze.return_value = "ROOT CAUSE: timeout\nFIX: Increase timeout to 60s."
        insert_event(
            self.db,
            self.run_id,
            "error",
            {"error_type": "TimeoutError", "message": "request timed out after 30s"},
        )

        context = _get_run_fix_context(self.db, self.run_id)

        mock_analyze.assert_called_once()
        self.assertEqual(context["root_cause"], "timeout")
        self.assertEqual(context["fix"], "Increase timeout to 60s.")


if __name__ == "__main__":
    unittest.main()
