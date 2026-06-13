"""Tests for LoopDetector and cost kill switch."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agentautopsy.db import create_tables, get_db, insert_event, insert_run
from agentautopsy.loop_detector import (
    LoopDetector,
    LoopKillException,
    _cost_usd,
    ensure_loop_tables,
    get_active_detector,
    load_loop_events,
    record_call_event,
)


class TestLoopDetector(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="test-agent")
        self.det = LoopDetector(
            db=self.db,
            run_id=self.run_id,
            max_iterations=10,
            max_cost_usd=1.00,
            max_tokens=1000,
            max_repeat_calls=3,
            max_repeat_llm=2,
            max_recursion=3,
            warn_at_fraction=0.80,
            kill_on_loop=False,  # don't raise in most tests
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

    # ── Loop detection by tool repetition ────────────────────────────────────

    def test_repeated_tool_call_triggers_loop(self) -> None:
        for _ in range(3):
            result = self.det.record_event("tool_call", {"tool": "search"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "repeated_tool_call")

    def test_different_tools_do_not_trigger_loop(self) -> None:
        for name in ("search", "fetch", "summarize"):
            result = self.det.record_event("tool_call", {"tool": name})
        self.assertTrue(result["ok"])

    def test_tool_repeat_resets_on_llm_call(self) -> None:
        self.det.record_event("tool_call", {"tool": "search"})
        self.det.record_event("tool_call", {"tool": "search"})
        self.det.record_event("llm_call", {"messages": ["hi"]})
        # Only 2 consecutive before the reset — should not trigger
        result = self.det.record_event("tool_call", {"tool": "search"})
        self.assertTrue(result["ok"])

    # ── Stuck LLM loop ────────────────────────────────────────────────────────

    def test_repeated_llm_input_triggers_stuck_loop(self) -> None:
        payload = {"messages": ["same question"]}
        self.det.record_event("llm_call", payload)
        result = self.det.record_event("llm_call", payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "stuck_llm_loop")

    def test_different_llm_inputs_do_not_trigger_stuck_loop(self) -> None:
        self.det.record_event("llm_call", {"messages": ["question 1"]})
        result = self.det.record_event("llm_call", {"messages": ["question 2"]})
        self.assertTrue(result["ok"])

    # ── Max iterations hard stop ──────────────────────────────────────────────

    def test_max_iterations_triggers_loop(self) -> None:
        result = None
        for i in range(12):
            result = self.det.record_event("tool_call", {"tool": f"tool_{i}"})
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "max_iterations")

    def test_exactly_at_limit_is_fine(self) -> None:
        for i in range(10):
            result = self.det.record_event("llm_call", {"messages": [f"msg {i}"]})
        # step 10 == max_iterations, so it's still ok (trigger is > not >=)
        self.assertTrue(result["ok"])

    # ── Cost kill switch ──────────────────────────────────────────────────────

    def test_cost_threshold_triggers_kill(self) -> None:
        # gpt-4o: $5/1M input, $15/1M output → 50000 in + 50000 out = $1.00
        result = self.det.record_event(
            "llm_call",
            {"messages": ["big request"]},
            token_input=50_000,
            token_output=50_000,
            model="gpt-4o",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "cost_exceeded")

    def test_cost_warning_at_80_percent(self) -> None:
        # $0.80 cost on a $1.00 limit → should warn (not kill)
        result = self.det.record_event(
            "llm_response",
            {},
            token_input=40_000,
            token_output=40_000,
            model="gpt-4o",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "cost_warning")
        self.assertTrue(result["warning"])

    def test_cost_warning_only_fires_once(self) -> None:
        # First call crosses 80% → warning
        self.det.record_event("llm_response", {}, token_input=40_000, token_output=40_000, model="gpt-4o")
        # Second call at same cost level should NOT warn again
        result = self.det.record_event("llm_response", {}, token_input=1_000, token_output=1_000, model="gpt-4o")
        # result should be ok (not another warning, and not yet at 100%)
        self.assertTrue(result["ok"] or result.get("loop_type") != "cost_warning")

    # ── Token kill switch ─────────────────────────────────────────────────────

    def test_token_threshold_triggers_kill(self) -> None:
        result = self.det.record_event(
            "llm_response",
            {},
            token_input=600,
            token_output=600,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "tokens_exceeded")

    def test_token_warning_at_80_percent(self) -> None:
        # 800 tokens on a 1000-token limit → 80%
        result = self.det.record_event(
            "llm_response",
            {},
            token_input=400,
            token_output=400,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["loop_type"], "token_warning")
        self.assertTrue(result["warning"])

    # ── LoopKillException ─────────────────────────────────────────────────────

    def test_kill_on_loop_raises_exception(self) -> None:
        killer = LoopDetector(
            db=self.db,
            run_id=self.run_id,
            max_repeat_calls=3,
            kill_on_loop=True,
        )
        with self.assertRaises(LoopKillException) as ctx:
            for _ in range(4):
                killer.record_event("tool_call", {"tool": "repeat"})
        self.assertEqual(ctx.exception.loop_type, "repeated_tool_call")

    def test_kill_exception_not_raised_when_disabled(self) -> None:
        # kill_on_loop=False — no exception even after threshold
        for _ in range(5):
            self.det.record_event("tool_call", {"tool": "same"})
        # reached here without raising — pass

    # ── Eval auto-generation on kill ─────────────────────────────────────────

    def test_eval_generated_on_loop_kill(self) -> None:
        output_dir = Path(self._tmpdir.name) / "tests" / "generated"
        killer = LoopDetector(
            db=self.db,
            run_id=self.run_id,
            max_repeat_calls=3,
            kill_on_loop=True,
        )
        # Seed a real event so the eval generator has something to work with
        insert_event(self.db, self.run_id, "tool_call", {"tool": "search", "input": {"q": "x"}})
        insert_event(self.db, self.run_id, "error", {"error_type": "LoopKillException", "message": "loop"})

        from agentautopsy.eval_generator import EvalGenerator
        EvalGenerator(db=self.db, output_dir=output_dir).watch()

        with self.assertRaises(LoopKillException):
            for _ in range(4):
                killer.record_event("tool_call", {"tool": "repeat"})

        # Eval should have been generated
        generated = list(output_dir.glob("test_auto_*.py"))
        self.assertGreater(len(generated), 0)

    # ── Persistence in DB ─────────────────────────────────────────────────────

    def test_loop_event_persisted_in_db(self) -> None:
        for _ in range(3):
            self.det.record_event("tool_call", {"tool": "fetch"})
        events = load_loop_events(self.db)
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0]["loop_type"], "repeated_tool_call")

    def test_loop_event_has_correct_fields(self) -> None:
        for _ in range(3):
            self.det.record_event("tool_call", {"tool": "parse"})
        events = load_loop_events(self.db)
        ev = events[0]
        self.assertIn("run_id", ev)
        self.assertIn("trigger_step", ev)
        self.assertIn("total_tokens", ev)
        self.assertIn("total_cost_usd", ev)
        self.assertIn("trigger_label", ev)

    # ── current_stats ─────────────────────────────────────────────────────────

    def test_current_stats_structure(self) -> None:
        self.det.record_event("llm_call", {"messages": ["hi"]}, token_input=100, token_output=50)
        stats = self.det.current_stats()
        self.assertIn("steps", stats)
        self.assertIn("total_tokens", stats)
        self.assertIn("total_cost_usd", stats)
        self.assertIn("cost_pct", stats)
        self.assertIn("token_pct", stats)
        self.assertEqual(stats["total_tokens"], 150)

    # ── watch() / active detector ─────────────────────────────────────────────

    def test_watch_registers_active_detector(self) -> None:
        self.det.watch()
        self.assertIs(get_active_detector(), self.det)

    def test_record_call_event_uses_active_detector(self) -> None:
        self.det.watch()
        result = record_call_event("llm_call", {"messages": ["hi"]})
        self.assertTrue(result["ok"])

    # ── Pricing ───────────────────────────────────────────────────────────────

    def test_cost_calculation_gpt4o(self) -> None:
        cost = _cost_usd("gpt-4o", 1_000_000, 0)
        self.assertAlmostEqual(cost, 5.00, places=2)

    def test_cost_calculation_claude_sonnet(self) -> None:
        cost = _cost_usd("claude-sonnet-4-6", 0, 1_000_000)
        self.assertAlmostEqual(cost, 15.00, places=2)

    def test_cost_calculation_unknown_model_uses_default(self) -> None:
        cost = _cost_usd("unknown-model-xyz", 1_000_000, 1_000_000)
        self.assertGreater(cost, 0)

    # ── reset() ──────────────────────────────────────────────────────────────

    def test_reset_clears_counters(self) -> None:
        self.det.record_event("llm_call", {}, token_input=500, token_output=500)
        self.det.reset()
        stats = self.det.current_stats()
        self.assertEqual(stats["steps"], 0)
        self.assertEqual(stats["total_tokens"], 0)
        self.assertAlmostEqual(stats["total_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
