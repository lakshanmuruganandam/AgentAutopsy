"""Auto-fix applier for AgentAutopsy."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import anthropic

from agentautopsy.analyzer import _parse_analysis, analyze
from agentautopsy.cache import lookup_fix, setup_cache
from agentautopsy.db import create_tables, get_db
from agentautopsy.detector import detect_failure, take_snapshot
from agentautopsy.pruner import prune


def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _get_run_fix_context(db: Any, run_id: str) -> dict[str, Any]:
    setup_cache(db)
    failure = detect_failure(run_id, db)
    if not failure.get("failed"):
        raise ValueError(f"Run {run_id} did not fail — nothing to fix")

    patch = lookup_fix(db, failure["error_type"], failure["message"])
    if not patch:
        snapshot = take_snapshot(run_id, db)
        pruned = prune(snapshot, failure["failure_event_id"])
        patch = analyze(pruned, failure)

    root_cause, fix = _parse_analysis(patch)
    if not root_cause:
        root_cause = f"{failure['error_type']}: {failure['message']}"
    if not fix:
        fix = patch

    trace_lines: list[str] = []
    if db["events"].exists():
        for row in db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        ):
            trace_lines.append(f"[{row['type']}] {row.get('payload', '')}")

    return {
        "failure": failure,
        "root_cause": root_cause,
        "fix": fix,
        "analysis": patch,
        "trace_summary": "\n".join(trace_lines[-20:]),
    }


def _list_project_files(root: Path, limit: int = 40) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".ts", ".js", ".tsx", ".jsx", ".json", ".yaml", ".yml"}:
            continue
        files.append(str(path.relative_to(root)).replace("\\", "/"))
        if len(files) >= limit:
            break
    return files


def _identify_fix_location(context: dict[str, Any]) -> dict[str, Any]:
    root = Path.cwd()
    project_files = _list_project_files(root)
    failure = context["failure"]
    prompt = (
        f"Error type: {failure.get('error_type')}\n"
        f"Error message: {failure.get('message')}\n"
        f"Root cause: {context['root_cause']}\n"
        f"Suggested fix: {context['fix']}\n\n"
        f"Recent trace:\n{context['trace_summary']}\n\n"
        f"Project files:\n" + "\n".join(project_files)
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=(
            "You identify where to apply a code fix in a local project. "
            "Respond with ONLY valid JSON using this schema:\n"
            '{"file_path": "relative/path.py", "line": 42, '
            '"search": "exact text to replace (or empty to insert)", '
            '"replace": "replacement text", "test_file": "tests/test_example.py"}'
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    location = _parse_json_response(next((getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"), ""))
    if not location.get("file_path"):
        raise ValueError("Claude could not identify a file to patch")
    return location


def _apply_file_patch(location: dict[str, Any]) -> Path:
    file_path = Path.cwd() / str(location["file_path"])
    if not file_path.exists():
        raise FileNotFoundError(f"Target file not found: {file_path}")

    original = file_path.read_text(encoding="utf-8")
    search = str(location.get("search") or "")
    replace = str(location.get("replace") or "")

    if search and search in original:
        updated = original.replace(search, replace, 1)
    else:
        line_no = int(location.get("line") or 1)
        lines = original.splitlines(keepends=True)
        index = max(0, min(len(lines), line_no - 1))
        insertion = replace if replace.endswith("\n") else replace + "\n"
        lines.insert(index, insertion)
        updated = "".join(lines)

    if updated == original:
        raise ValueError("Fix did not change the target file")

    file_path.write_text(updated, encoding="utf-8")
    return file_path


def _run_tests(test_file: str | None) -> dict[str, Any]:
    cmd: list[str]
    if test_file and Path(test_file).exists():
        cmd = [sys.executable, "-m", "pytest", test_file, "-q"]
    else:
        cmd = [sys.executable, "-m", "pytest", "-q"]

    try:
        result = subprocess.run(
            cmd,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return {
            "passed": False,
            "output": "pytest not available",
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "output": "Tests timed out after 120s",
            "command": " ".join(cmd),
        }

    output = (result.stdout or "") + (result.stderr or "")
    return {
        "passed": result.returncode == 0,
        "output": output.strip() or f"exit code {result.returncode}",
        "command": " ".join(cmd),
    }


def apply_fix(run_id: str) -> dict[str, Any]:
    """Apply an automated fix for a failed run and verify with tests."""
    db = get_db()
    create_tables(db)
    context = _get_run_fix_context(db, run_id)
    failure = context["failure"]

    try:
        location = _identify_fix_location(context)
        file_path = _apply_file_patch(location)
        test_result = _run_tests(location.get("test_file"))
        success = test_result["passed"]
        return {
            "success": success,
            "run_id": run_id,
            "error_type": failure.get("error_type"),
            "root_cause": context["root_cause"],
            "fix": context["fix"],
            "file_path": str(file_path),
            "line": location.get("line"),
            "test_passed": test_result["passed"],
            "test_output": test_result["output"],
            "test_command": test_result["command"],
            "details": (
                f"Patched {file_path} at line {location.get('line', '?')}. "
                f"Tests {'passed' if success else 'failed'}."
            ),
        }
    except Exception as exc:
        return {
            "success": False,
            "run_id": run_id,
            "error_type": failure.get("error_type"),
            "root_cause": context["root_cause"],
            "fix": context["fix"],
            "file_path": None,
            "line": None,
            "test_passed": False,
            "test_output": "",
            "test_command": "",
            "details": str(exc),
        }
