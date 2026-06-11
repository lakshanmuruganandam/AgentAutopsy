import os
import unittest
from unittest.mock import MagicMock, patch

from agentautopsy.analyzer import _parse_analysis, analyze

_FAKE_SNAPSHOT = [
    {
        "id": "1",
        "type": "llm_call",
        "payload": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "fetch data"}],
        },
        "cassette_size": 0,
        "timestamp": "2024-01-01T00:00:01",
    },
    {
        "id": "2",
        "type": "error",
        "payload": {
            "error_type": "TimeoutError",
            "message": "request timed out after 30s",
        },
        "cassette_size": 0,
        "timestamp": "2024-01-01T00:00:02",
    },
]

_FAKE_FAILURE = {
    "failed": True,
    "error_type": "TimeoutError",
    "message": "request timed out after 30s",
    "run_id": "test-123",
    "failure_event_id": "2",
}


class TestAnalyzer(unittest.TestCase):
    def test_parse_analysis(self):
        text = "ROOT CAUSE: A network timeout occurred.\nFIX: Increase timeout to 60s."
        root_cause, fix = _parse_analysis(text)
        self.assertEqual(root_cause, "A network timeout occurred.")
        self.assertEqual(fix, "Increase timeout to 60s.")

    def test_parse_analysis_regex_fallback(self):
        text = "Here is my analysis.\nroot cause: Null pointer.\nfix: Check for null."
        root_cause, fix = _parse_analysis(text)
        self.assertEqual(root_cause, "Null pointer.")
        self.assertEqual(fix, "Check for null.")

    @patch.dict(os.environ, {}, clear=True)
    @patch("agentautopsy.analyzer._get_anthropic_client", return_value=None)
    def test_analyze_without_client_returns_warning(self, _mock_get_client):
        result = analyze(_FAKE_SNAPSHOT, _FAKE_FAILURE)
        self.assertIn("WARNING", result)
        self.assertIn("ANTHROPIC_API_KEY", result)

    @patch("agentautopsy.analyzer._get_anthropic_client")
    def test_analyze_with_client_returns_analysis(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="ROOT CAUSE: timeout\nFIX: Increase timeout to 60s.")
        ]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = analyze(_FAKE_SNAPSHOT, _FAKE_FAILURE)

        self.assertIn("ROOT CAUSE: timeout", result)
        mock_get_client.assert_called_once()
        mock_client.messages.create.assert_called_once()

    @patch("agentautopsy.analyzer._get_anthropic_client")
    def test_analyze_when_api_call_fails_returns_warning(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("service unavailable")
        mock_get_client.return_value = mock_client

        result = analyze(_FAKE_SNAPSHOT, _FAKE_FAILURE)

        self.assertIn("WARNING", result)
        self.assertIn("RuntimeError", result)
        self.assertIn("service unavailable", result)


if __name__ == "__main__":
    unittest.main()
