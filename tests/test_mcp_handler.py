"""Tests for MCP post-mortem tracing."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.mcp_handler import (
    MCPAutopsy,
    compare_input_to_schema,
    diff_schemas,
)


class TestMCPSchemaLogic(unittest.TestCase):
    def test_detects_missing_and_renamed_fields(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        comparison = compare_input_to_schema(schema, {"qery": "market"})
        self.assertTrue(comparison["has_mismatch"])
        self.assertIn("query", comparison["missing_fields"])
        self.assertTrue(comparison["renamed_fields"])

    def test_schema_drift_detects_type_change(self) -> None:
        previous = {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
        }
        current = {
            "type": "object",
            "properties": {"limit": {"type": "string"}},
            "required": ["limit"],
        }
        drift = diff_schemas(previous, current)
        self.assertTrue(drift["has_drift"])
        self.assertEqual(drift["type_changes"][0]["field"], "limit")


class TestMCPAutopsy(unittest.TestCase):
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

    def test_records_mcp_failure_and_contamination(self) -> None:
        autopsy = MCPAutopsy(self.run_id, self.db, server_name="demo")
        autopsy.register_tool_schema(
            "search",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        autopsy.on_tool_call("search", {"qery": "agents"})
        autopsy.on_tool_result("search", {"qery": "agents"}, {"results": []})
        autopsy.on_tool_call("summarize", {"text": "bad upstream data"})

        event_types = [
            row["type"]
            for row in self.db["events"].rows_where(
                where="run_id = ?",
                where_args=[self.run_id],
                order_by="timestamp",
            )
        ]
        self.assertIn("mcp_tool_call", event_types)
        self.assertIn("mcp_schema_mismatch", event_types)
        self.assertIn("mcp_failure", event_types)
        self.assertGreaterEqual(event_types.count("mcp_tool_call"), 2)

        failure_rows = list(
            self.db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[self.run_id, "mcp_failure"],
            )
        )
        payload = json.loads(failure_rows[0]["payload"])
        self.assertEqual(payload["tool"], "search")
        self.assertIn("report", payload)

    def test_schema_drift_report_is_recorded(self) -> None:
        autopsy = MCPAutopsy(self.run_id, self.db, server_name="demo")
        schema_v1 = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        schema_v2 = {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query", "limit"],
        }
        autopsy.register_tool_schema("search", schema_v1)
        drift = autopsy.register_tool_schema("search", schema_v2)
        self.assertIsNotNone(drift)
        self.assertTrue(drift["drift"]["has_drift"])

        drift_rows = list(
            self.db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[self.run_id, "mcp_schema_drift"],
            )
        )
        self.assertEqual(len(drift_rows), 1)


if __name__ == "__main__":
    unittest.main()
