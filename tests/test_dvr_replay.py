"""Tests for DVR fork and replay."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentautopsy.db import create_tables, get_db, insert_event, insert_run
from agentautopsy.dvr_replay import DVRReplay, load_dvr_ui_data


class TestDVRReplay(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="researcher")
        self.dvr = DVRReplay(db=self.db, run_id=self.run_id)
        self.dvr.watch()
        insert_event(
            self.db,
            self.run_id,
            "llm_call",
            {"model": "gpt-4", "messages": ["hello"]},
        )
        insert_event(
            self.db,
            self.run_id,
            "tool_call",
            {"tool": "search", "input": {"query": "agents"}},
        )
        insert_event(
            self.db,
            self.run_id,
            "tool_result",
            {"output": {"results": ["a"]}},
            token_input=10,
            token_output=5,
        )
        insert_event(
            self.db,
            self.run_id,
            "error",
            {"error_type": "TimeoutError", "message": "timed out"},
        )

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

    def test_list_runs_and_timeline(self) -> None:
        runs = self.dvr.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["step_count"], 5)
        timeline = self.dvr.load_timeline(self.run_id)
        self.assertEqual(timeline[0]["step"], 1)
        self.assertEqual(timeline[0]["type"], "dvr_recording_start")
        self.assertEqual(timeline[2]["type"], "tool_call")

    def test_fork_run_creates_branch(self) -> None:
        fork_id = self.dvr.fork_run(self.run_id, 3)
        fork_timeline = self.dvr.load_timeline(fork_id)
        self.assertEqual(len(fork_timeline), 4)
        fork_run = self.db["runs"].get(fork_id)
        self.assertEqual(fork_run["parent_run_id"], self.run_id)

    def test_replay_with_fix_patches_input(self) -> None:
        result = self.dvr.replay_with_fix(
            self.run_id,
            3,
            {"query": "patched prompt"},
        )
        fork_id = result["replay_run_id"]
        fork_timeline = self.dvr.load_timeline(fork_id)
        tool_step = next(step for step in fork_timeline if step["type"] == "tool_call")
        self.assertTrue(tool_step["payload"].get("dvr_patched"))
        self.assertEqual(tool_step["payload"]["input"]["query"], "patched prompt")

    def test_diff_runs_detects_changes_and_improvement(self) -> None:
        fork_id = self.dvr.fork_run(self.run_id, 2)
        diff = self.dvr.diff_runs(self.run_id, fork_id)
        self.assertGreater(diff["change_count"], 0)
        self.assertTrue(diff["original_failed"])
        self.assertTrue(diff["improved"])

    def test_replay_from_step_without_cassettes(self) -> None:
        result = self.dvr.replay_from_step(self.run_id, 2)
        self.assertEqual(result["from_step"], 2)
        self.assertIn("replay_run_id", result)
        self.assertEqual(result["events_replayed"], 0)

    def test_load_dvr_ui_data(self) -> None:
        payload = load_dvr_ui_data(self.db)
        self.assertIn(self.run_id, payload["timelines"])
        self.assertGreaterEqual(len(payload["runs"]), 1)

    def test_fork_alias(self) -> None:
        fork_id = self.dvr.fork(self.run_id, at_step=3, new_input={"query": "changed"})
        timeline = self.dvr.load_timeline(fork_id)
        patched = next(step for step in timeline if step["type"] == "tool_call")
        self.assertEqual(patched["payload"]["patched_input"], {"query": "changed"})


if __name__ == "__main__":
    unittest.main()
