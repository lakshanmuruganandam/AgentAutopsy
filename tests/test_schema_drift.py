"""Tests for schema drift detection."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.schema_drift import (
    SchemaDriftDetector,
    diff_schemas,
    extract_openai_tool_schemas,
    infer_schema_from_serialized,
    load_schema_drift_events,
    schema_from_tool_input,
)


class TestSchemaDriftLogic(unittest.TestCase):
    def test_diff_detects_added_removed_and_type_changes(self) -> None:
        previous = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
        current = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "string"},
                "offset": {"type": "integer"},
            },
            "required": ["query", "offset"],
        }
        drift = diff_schemas(previous, current)
        self.assertTrue(drift["has_drift"])
        self.assertIn("offset", drift["added_fields"])
        self.assertEqual(drift["type_changes"][0]["field"], "limit")

    def test_extract_openai_tool_schemas(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ]
        schemas = extract_openai_tool_schemas(tools)
        self.assertEqual(schemas[0][0], "search")
        self.assertIn("query", schemas[0][1]["properties"])

    def test_infer_schema_from_serialized_args(self) -> None:
        schema = infer_schema_from_serialized(
            {"name": "lookup", "args": {"id": "string", "verbose": "bool"}}
        )
        self.assertIn("id", schema["properties"])
        self.assertIn("verbose", schema["properties"])

    def test_schema_from_tool_input(self) -> None:
        schema = schema_from_tool_input({"query": "agents", "limit": 5})
        self.assertEqual(schema["properties"]["query"]["type"], "string")
        self.assertEqual(schema["properties"]["limit"]["type"], "integer")


class TestSchemaDriftDetector(unittest.TestCase):
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

    def test_baseline_on_first_call_and_drift_on_change(self) -> None:
        detector = SchemaDriftDetector(
            run_id=self.run_id,
            db=self.db,
            agent_name="researcher",
        )
        detector.watch()
        schema_v1 = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        schema_v2 = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query", "limit"],
        }
        self.assertIsNone(
            detector.record_schema("search", schema_v1, source="openai")
        )
        result = detector.record_schema("search", schema_v2, source="openai")
        self.assertIsNotNone(result)
        self.assertTrue(result["drift"]["has_drift"])
        self.assertIn("researcher", result["affected_agents"])

        events = list(
            self.db["events"].rows_where(
                where='run_id = ? AND type = ?',
                where_args=[self.run_id, "schema_drift"],
            )
        )
        self.assertEqual(len(events), 1)
        payload = json.loads(events[0]["payload"])
        self.assertEqual(payload["tool"], "search")
        self.assertIn("recommendation", payload)

    def test_load_schema_drift_events_for_ui(self) -> None:
        detector = SchemaDriftDetector(run_id=self.run_id, db=self.db)
        detector.watch()
        schema_v1 = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        schema_v2 = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        detector.record_schema("rename_me", schema_v1, source="langchain")
        detector.record_schema("rename_me", schema_v2, source="langchain")
        events = load_schema_drift_events(self.db)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["tool"], "rename_me")


if __name__ == "__main__":
    unittest.main()
