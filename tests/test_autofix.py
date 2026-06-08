import unittest
from unittest.mock import MagicMock
from agentautopsy.autofix import _parse_json_response

class TestAutofix(unittest.TestCase):
    def test_parse_json_response(self):
        json_text = '{"file_path": "test.py", "line": 10, "search": "foo", "replace": "bar", "test_file": "test.py"}'
        result = _parse_json_response(json_text)
        self.assertEqual(result["file_path"], "test.py")
        
if __name__ == '__main__':
    unittest.main()
