"""Tests for LangGraph handler integration."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.langgraph_handler import AgentAutopsyLangGraphHandler


class TestLangGraphHandler(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="researcher")

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

    def _event_types(self) -> list[str]:
        return [
            row["type"]
            for row in self.db["events"].rows_where(
                where="run_id = ?",
                where_args=[self.run_id],
                order_by="timestamp",
            )
        ]

    def test_mock_langgraph_run_records_nodes_edges_state_and_error(self) -> None:
        handler = AgentAutopsyLangGraphHandler(self.run_id, self.db)

        handler.on_graph_node_start("research", {"query": "market data"})
        handler.on_graph_state_change({"messages": [{"role": "user", "content": "analyze"}]})
        handler.on_graph_node_end("research", {"summary": "done"})
        handler.on_graph_edge("research", "writer")
        handler.on_graph_node_start("writer", {"draft": True})
        handler.on_graph_error(ValueError("graph execution failed"), node="writer")

        types = self._event_types()
        self.assertIn("langgraph_node_start", types)
        self.assertIn("langgraph_node_end", types)
        self.assertIn("langgraph_edge", types)
        self.assertIn("langgraph_state_change", types)
        self.assertIn("error", types)

        error_rows = list(
            self.db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[self.run_id, "error"],
            )
        )
        self.assertEqual(len(error_rows), 1)
        payload = json.loads(error_rows[0]["payload"])
        self.assertEqual(payload["error_type"], "ValueError")
        self.assertIn("graph execution failed", payload["message"])


if __name__ == "__main__":
    unittest.main()
