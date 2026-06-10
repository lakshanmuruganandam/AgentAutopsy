"""Tests for CrewAI handler integration."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentautopsy.crewai_handler import AgentAutopsyCrewAIHandler
from agentautopsy.db import create_tables, get_db, insert_run


class TestCrewAIHandler(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="crew-lead")

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

    def test_mock_crewai_run_records_tasks_tools_handoff_output_and_error(self) -> None:
        handler = AgentAutopsyCrewAIHandler(self.run_id, self.db)

        handler.on_task_start("researcher", "Find market trends")
        handler.on_tool_start("researcher", "web_search", {"query": "AI agents"})
        handler.on_tool_end("researcher", "web_search", {"results": ["a", "b"]})
        handler.on_task_end("researcher", "Find market trends", "Trend report ready")
        handler.on_agent_handoff("researcher", "writer", {"brief": "use trend report"})
        handler.on_task_start("writer", "Draft blog post")
        handler.on_error(RuntimeError("crew task failed"), agent="writer")
        handler.on_crew_output({"final": "partial output"})

        types = self._event_types()
        self.assertIn("crewai_task_start", types)
        self.assertIn("crewai_task_end", types)
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertIn("crewai_handoff", types)
        self.assertIn("crewai_output", types)
        self.assertIn("error", types)

    def test_step_callback_dispatches_failure(self) -> None:
        handler = AgentAutopsyCrewAIHandler(self.run_id, self.db)
        handler.step_callback(
            {
                "type": "task_error",
                "agent": "analyst",
                "error": ValueError("step callback failure"),
            }
        )

        error_rows = list(
            self.db["events"].rows_where(
                where="run_id = ? AND type = ?",
                where_args=[self.run_id, "error"],
            )
        )
        self.assertEqual(len(error_rows), 1)
        payload = json.loads(error_rows[0]["payload"])
        self.assertEqual(payload["error_type"], "ValueError")
        self.assertEqual(payload["agent"], "analyst")


if __name__ == "__main__":
    unittest.main()
