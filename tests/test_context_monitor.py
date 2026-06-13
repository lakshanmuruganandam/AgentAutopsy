"""Tests for ContextMonitor."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentautopsy.context_monitor import (
    MODEL_CONTEXT_LIMITS,
    ContextMonitor,
    _count_tokens_rough,
    _detect_truncation,
    _generate_suggestions,
    _resolve_context_limit,
    _tokens_in_message,
    _tokens_in_payload,
    ensure_context_tables,
    get_active_monitor,
    load_context_snapshots,
    load_context_ui_data,
    record_llm_call_event,
)
from agentautopsy.db import create_tables, get_db, insert_run


def _make_payload(
    system: str = "",
    user: str = "Hello",
    assistant: str = "",
    num_user_turns: int = 1,
) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    for _ in range(num_user_turns):
        messages.append({"role": "user", "content": user})
    if assistant:
        messages.append({"role": "assistant", "content": assistant})
    return {"messages": messages}


class TestTokenCounting(unittest.TestCase):
    def test_rough_count_non_zero(self):
        tok = _count_tokens_rough("Hello world, this is a test sentence.")
        self.assertGreater(tok, 0)

    def test_rough_count_scales_with_length(self):
        short = _count_tokens_rough("Hi")
        long = _count_tokens_rough("Hi " * 100)
        self.assertGreater(long, short)

    def test_tokens_in_message_string(self):
        tok = _tokens_in_message("This is a test string.")
        self.assertGreater(tok, 0)

    def test_tokens_in_message_dict(self):
        tok = _tokens_in_message({"role": "user", "content": "What is 2+2?"})
        self.assertGreater(tok, 0)

    def test_tokens_in_payload_basic(self):
        payload = _make_payload(user="What is the capital of France?")
        total, breakdown = _tokens_in_payload(payload)
        self.assertGreater(total, 0)
        self.assertTrue(any(b["role"] == "user" for b in breakdown))

    def test_tokens_in_payload_roles(self):
        payload = _make_payload(system="You are an assistant.", user="Hi", assistant="Hello!")
        _, breakdown = _tokens_in_payload(payload)
        roles = {b["role"] for b in breakdown}
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_tokens_in_payload_explicit_token_input(self):
        payload = {"messages": [{"role": "user", "content": "Hi"}], "token_input": 500}
        monitor = ContextMonitor()
        result = monitor.record_llm_call(payload, model="gpt-4o")
        self.assertEqual(result["tokens_used"], 500)


class TestContextLimits(unittest.TestCase):
    def test_known_models(self):
        self.assertEqual(_resolve_context_limit("gpt-4o"), 128_000)
        self.assertEqual(_resolve_context_limit("gpt-4o-mini"), 128_000)
        self.assertEqual(_resolve_context_limit("claude-opus-4-6"), 200_000)
        self.assertEqual(_resolve_context_limit("claude-sonnet-4-6"), 200_000)
        self.assertEqual(_resolve_context_limit("claude-haiku-4-5"), 200_000)
        self.assertEqual(_resolve_context_limit("gemini-1.5-pro"), 1_000_000)
        self.assertEqual(_resolve_context_limit("llama-3"), 128_000)

    def test_unknown_model_returns_default(self):
        limit = _resolve_context_limit("totally-unknown-model-xyz")
        self.assertEqual(limit, 128_000)

    def test_case_insensitive(self):
        self.assertEqual(_resolve_context_limit("GPT-4O"), _resolve_context_limit("gpt-4o"))


class TestTruncationDetection(unittest.TestCase):
    def test_no_truncation_when_growing(self):
        self.assertFalse(_detect_truncation(100, 200))

    def test_no_truncation_when_same(self):
        self.assertFalse(_detect_truncation(100, 100))

    def test_truncation_when_shrinks_40_pct(self):
        self.assertTrue(_detect_truncation(200, 100))

    def test_no_truncation_on_none(self):
        self.assertFalse(_detect_truncation(None, 50))
        self.assertFalse(_detect_truncation(50, None))

    def test_no_truncation_on_tiny_prev(self):
        self.assertFalse(_detect_truncation(5, 1))


class TestSuggestions(unittest.TestCase):
    def test_no_suggestions_for_tiny_context(self):
        breakdown = [{"index": 0, "role": "user", "label": "User message", "tokens": 10}]
        sugs = _generate_suggestions(breakdown, 10, 128_000)
        self.assertIsInstance(sugs, list)

    def test_suggests_summarize_for_large_tool_response(self):
        breakdown = [
            {"index": 0, "role": "tool", "label": "Tool response", "tokens": 20_000},
        ]
        sugs = _generate_suggestions(breakdown, 20_000, 128_000)
        self.assertTrue(any("summariz" in s.lower() for s in sugs))

    def test_warns_about_big_system_prompt(self):
        limit = 128_000
        sys_tokens = int(limit * 0.25)
        breakdown = [
            {"index": -1, "role": "system", "label": "System prompt", "tokens": sys_tokens},
        ]
        sugs = _generate_suggestions(breakdown, sys_tokens, limit)
        self.assertTrue(any("system prompt" in s.lower() for s in sugs))

    def test_warns_history_windowing(self):
        breakdown = [
            {"index": i, "role": "user", "label": "User message", "tokens": 50}
            for i in range(6)
        ]
        sugs = _generate_suggestions(breakdown, 300, 128_000)
        self.assertTrue(any("window" in s.lower() for s in sugs))

    def test_critical_context_ranks_top_hogs(self):
        limit = 128_000
        tokens = int(limit * 0.95)
        breakdown = [
            {"index": 0, "role": "tool", "label": "Tool response", "tokens": tokens - 200},
            {"index": 1, "role": "user", "label": "User message", "tokens": 200},
        ]
        sugs = _generate_suggestions(breakdown, tokens, limit)
        self.assertTrue(len(sugs) > 0)


class TestContextMonitor(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="test-agent")
        self.monitor = ContextMonitor(db=self.db, run_id=self.run_id)

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

    # ── Basic record ─────────────────────────────────────────────────────────

    def test_record_llm_call_returns_dict(self):
        result = self.monitor.record_llm_call(
            _make_payload(user="Hello"), model="gpt-4o"
        )
        self.assertIn("ok", result)
        self.assertIn("pct_used", result)
        self.assertIn("tokens_used", result)
        self.assertIn("context_limit", result)
        self.assertIn("alert_level", result)
        self.assertIn("suggestions", result)
        self.assertIn("truncation_suspected", result)

    def test_alert_level_ok_for_low_usage(self):
        result = self.monitor.record_llm_call(
            _make_payload(user="Hi"), model="gpt-4o"
        )
        self.assertEqual(result["alert_level"], "ok")

    def test_alert_level_warn_at_70_pct(self):
        # Build a payload that lands near 70% of gpt-4o's 128k limit
        limit = MODEL_CONTEXT_LIMITS["gpt-4o"]
        target_tokens = int(limit * 0.72)
        payload = {"token_input": target_tokens, "messages": [{"role": "user", "content": "hi"}]}
        result = self.monitor.record_llm_call(payload, model="gpt-4o")
        self.assertIn(result["alert_level"], ("warn", "critical"))

    def test_alert_level_critical_at_90_pct(self):
        limit = MODEL_CONTEXT_LIMITS["gpt-4o"]
        target_tokens = int(limit * 0.92)
        payload = {"token_input": target_tokens, "messages": [{"role": "user", "content": "hi"}]}
        result = self.monitor.record_llm_call(payload, model="gpt-4o")
        self.assertEqual(result["alert_level"], "critical")

    def test_pct_used_increases_with_more_tokens(self):
        r1 = self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o")
        limit = MODEL_CONTEXT_LIMITS["gpt-4o"]
        r2 = self.monitor.record_llm_call(
            {"token_input": int(limit * 0.5), "messages": [{"role": "user", "content": "hi"}]},
            model="gpt-4o",
        )
        self.assertGreater(r2["pct_used"], r1["pct_used"])

    # ── Truncation detection ──────────────────────────────────────────────────

    def test_truncation_detected_when_output_shrinks(self):
        self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o", output_tokens=500)
        result = self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o", output_tokens=100)
        self.assertTrue(result["truncation_suspected"])

    def test_no_truncation_when_output_grows(self):
        self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o", output_tokens=100)
        result = self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o", output_tokens=500)
        self.assertFalse(result["truncation_suspected"])

    # ── get_usage ─────────────────────────────────────────────────────────────

    def test_get_usage_returns_list(self):
        self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o")
        self.monitor.record_llm_call(_make_payload(user="There"), model="gpt-4o")
        usage = self.monitor.get_usage()
        self.assertEqual(len(usage), 2)
        self.assertEqual(usage[0]["step"], 1)
        self.assertEqual(usage[1]["step"], 2)

    def test_get_usage_from_db_by_run_id(self):
        ensure_context_tables(self.db)
        self.monitor.record_llm_call(_make_payload(user="Hey"), model="gpt-4o")
        snapshots = load_context_snapshots(self.db, self.run_id)
        self.assertEqual(len(snapshots), 1)
        self.assertIn("pct_used", snapshots[0])

    # ── current_pct ───────────────────────────────────────────────────────────

    def test_current_pct_zero_before_any_call(self):
        mon = ContextMonitor()
        self.assertEqual(mon.current_pct(), 0.0)

    def test_current_pct_nonzero_after_call(self):
        self.monitor.record_llm_call(_make_payload(user="Hello world"), model="gpt-4o")
        self.assertGreater(self.monitor.current_pct(), 0.0)

    # ── reset ─────────────────────────────────────────────────────────────────

    def test_reset_clears_state(self):
        self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o")
        self.monitor.reset()
        self.assertEqual(self.monitor.current_pct(), 0.0)
        self.assertEqual(len(self.monitor.get_usage()), 0)

    # ── watch() ───────────────────────────────────────────────────────────────

    def test_watch_registers_active_monitor(self):
        mon = ContextMonitor(db=self.db, run_id=self.run_id)
        mon.watch()
        self.assertIs(get_active_monitor(), mon)

    # ── record_llm_call_event module helper ───────────────────────────────────

    def test_record_llm_call_event_no_monitor_returns_ok(self):
        # Unregister any monitor
        from agentautopsy.context_monitor import _context_monitor_ctx
        _context_monitor_ctx.pop("monitor", None)
        result = record_llm_call_event({"messages": []})
        self.assertTrue(result["ok"])

    def test_record_llm_call_event_with_active_monitor(self):
        self.monitor.watch()
        result = record_llm_call_event(_make_payload(user="Hello"), model="gpt-4o")
        self.assertIn("pct_used", result)

    # ── load_context_ui_data ──────────────────────────────────────────────────

    def test_load_context_ui_data_structure(self):
        ensure_context_tables(self.db)
        self.monitor.record_llm_call(_make_payload(user="Hello"), model="gpt-4o")
        ui = load_context_ui_data(self.db)
        self.assertIn("by_run", ui)
        self.assertIn(self.run_id, ui["by_run"])
        run_info = ui["by_run"][self.run_id]
        self.assertIn("steps", run_info)
        self.assertIn("latest_pct", run_info)
        self.assertIn("alert_level", run_info)
        self.assertIn("model", run_info)

    # ── model context limits ──────────────────────────────────────────────────

    def test_gemini_has_1m_context(self):
        result = self.monitor.record_llm_call(
            {"messages": [{"role": "user", "content": "Hi"}]},
            model="gemini-1.5-pro",
        )
        self.assertEqual(result["context_limit"], 1_000_000)

    def test_claude_has_200k_context(self):
        result = self.monitor.record_llm_call(
            {"messages": [{"role": "user", "content": "Hi"}]},
            model="claude-opus-4-6",
        )
        self.assertEqual(result["context_limit"], 200_000)

    # ── suggestions in response ───────────────────────────────────────────────

    def test_suggestions_in_result(self):
        result = self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o")
        self.assertIsInstance(result["suggestions"], list)

    def test_high_usage_generates_suggestions(self):
        limit = MODEL_CONTEXT_LIMITS["gpt-4o"]
        payload = {"token_input": int(limit * 0.95), "messages": [{"role": "user", "content": "hi"}]}
        result = self.monitor.record_llm_call(payload, model="gpt-4o")
        self.assertGreater(len(result["suggestions"]), 0)

    # ── DB persistence ────────────────────────────────────────────────────────

    def test_persists_to_db(self):
        ensure_context_tables(self.db)
        self.monitor.record_llm_call(_make_payload(user="Hi"), model="gpt-4o")
        rows = list(self.db["context_snapshots"].rows_where("run_id = ?", [self.run_id]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "gpt-4o")

    def test_step_increments(self):
        ensure_context_tables(self.db)
        self.monitor.record_llm_call(_make_payload(user="First"), model="gpt-4o")
        self.monitor.record_llm_call(_make_payload(user="Second"), model="gpt-4o")
        rows = list(
            self.db["context_snapshots"].rows_where("run_id = ?", [self.run_id], order_by="step")
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["step"], 1)
        self.assertEqual(rows[1]["step"], 2)


if __name__ == "__main__":
    unittest.main()
