"""Tests for automatic eval generation."""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path

from agentautopsy.db import create_tables, get_db, insert_event, insert_run
from agentautopsy.eval_generator import (
    EvalGenerator,
    generate_eval_for_run,
    get_active_generator,
)


class TestEvalGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._previous_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.output_dir = Path(self._tmpdir.name) / "tests" / "generated"
        self.db = get_db()
        create_tables(self.db)
        self.run_id = insert_run(self.db, agent_name="researcher")
        insert_event(
            self.db,
            self.run_id,
            "llm_call",
            {"model": "gpt-4", "messages": ["find the latest agents"]},
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
            "error",
            {"error_type": "TimeoutError", "message": "request timed out after 30s"},
        )
        self.gen = EvalGenerator(db=self.db, output_dir=self.output_dir)

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

    def test_generate_from_run_creates_file(self) -> None:
        path = self.gen.generate_from_run(self.run_id)
        self.assertIsNotNone(path)
        assert path is not None
        generated = Path(path)
        self.assertTrue(generated.exists())
        self.assertTrue(generated.name.startswith("test_auto_"))
        self.assertEqual(generated.parent, self.output_dir)

    def test_generated_file_is_valid_python(self) -> None:
        path = self.gen.generate_from_run(self.run_id)
        assert path is not None
        source = Path(path).read_text(encoding="utf-8")
        # Must compile without syntax errors.
        ast.parse(source)
        compile(source, path, "exec")

    def test_generated_test_captures_failure_details(self) -> None:
        path = self.gen.generate_from_run(self.run_id)
        assert path is not None
        source = Path(path).read_text(encoding="utf-8")
        # Captures the exact input that caused the failure.
        self.assertIn("CAPTURED_INPUT", source)
        self.assertIn("find the latest agents", source)
        # Captures the step it failed at.
        self.assertIn("FAILED_AT_STEP = 3", source)
        # Captures the call that broke.
        self.assertIn("FAILING_CALL", source)
        self.assertIn("search", source)
        # Captures an assertion that catches the failure again.
        self.assertIn("RECORDED_ERROR_TYPE", source)
        self.assertIn("TimeoutError", source)
        self.assertIn("assert", source)

    def test_docstring_explains_generation(self) -> None:
        path = self.gen.generate_from_run(self.run_id)
        assert path is not None
        source = Path(path).read_text(encoding="utf-8")
        module = ast.parse(source)
        docstring = ast.get_docstring(module) or ""
        self.assertIn("Generated at", docstring)
        self.assertIn("Catches", docstring)
        self.assertIn("Root cause", docstring)
        self.assertIn("TimeoutError", docstring)

    def test_no_failure_returns_none(self) -> None:
        clean_run = insert_run(self.db, agent_name="clean")
        insert_event(
            self.db,
            clean_run,
            "llm_call",
            {"model": "gpt-4", "messages": ["hi"]},
        )
        path = self.gen.generate_from_run(clean_run)
        self.assertIsNone(path)

    def test_generate_all_covers_every_failure(self) -> None:
        second = insert_run(self.db, agent_name="other")
        insert_event(
            self.db,
            second,
            "tool_call",
            {"tool": "fetch", "input": {"url": "https://x"}},
        )
        insert_event(
            self.db,
            second,
            "http_error",
            {"error_type": "ConnectionError", "message": "refused"},
        )
        paths = self.gen.generate_all()
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertTrue(Path(path).exists())

    def test_generate_all_creates_package_marker(self) -> None:
        self.gen.generate_all()
        self.assertTrue((self.output_dir / "__init__.py").exists())

    def test_watch_registers_active_generator(self) -> None:
        self.gen.watch()
        active = get_active_generator()
        self.assertIs(active, self.gen)

    def test_generate_eval_for_run_helper(self) -> None:
        self.gen.output_dir = self.output_dir
        self.gen.watch()
        path = generate_eval_for_run(self.run_id, self.db)
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(Path(path).exists())

    def test_unique_filenames_for_repeated_generation(self) -> None:
        first = self.gen.generate_from_run(self.run_id)
        second = self.gen.generate_from_run(self.run_id)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
