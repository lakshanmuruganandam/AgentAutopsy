import unittest

import agentautopsy


class TestSmoke(unittest.TestCase):
    def test_watch_does_not_raise(self):
        agentautopsy.watch()


if __name__ == "__main__":
    unittest.main()
