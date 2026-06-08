import unittest
from unittest.mock import patch, MagicMock
from agentautopsy.analyzer import _parse_analysis

class TestAnalyzer(unittest.TestCase):
    def test_parse_analysis_json(self):
        json_text = '{"file_path": "test.py", "line": 10, "search": "foo", "replace": "bar", "test_file": "test.py"}'
        result = _parse_analysis(json_text)
        self.assertEqual(result["file_path"], "test.py")
        self.assertEqual(result["search"], "foo")
        
    def test_parse_analysis_markdown(self):
        md_text = '''Here is the analysis:
```json
{"file_path": "test2.py", "line": 20, "search": "a", "replace": "b", "test_file": "test2.py"}
```'''
        result = _parse_analysis(md_text)
        self.assertEqual(result["file_path"], "test2.py")
        
    def test_parse_analysis_invalid(self):
        with self.assertRaises(ValueError):
            _parse_analysis("This is just text without JSON")
            
if __name__ == '__main__':
    unittest.main()
