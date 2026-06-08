import unittest
from unittest.mock import patch, MagicMock
from agentautopsy.analyzer import _parse_analysis

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
            
if __name__ == '__main__':
    unittest.main()
