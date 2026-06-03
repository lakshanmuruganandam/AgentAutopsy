#!/usr/bin/env bash
set -euo pipefail

TEST_COMMAND="${INPUT_TEST_COMMAND:-pytest}"
export ANTHROPIC_API_KEY="${INPUT_ANTHROPIC_API_KEY:?INPUT_ANTHROPIC_API_KEY is required}"
export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN:?INPUT_GITHUB_TOKEN is required}"

ACTION_DIR="${GITHUB_ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO_ROOT="$(cd "${ACTION_DIR}/.." && pwd)"

if [ -f "${REPO_ROOT}/pyproject.toml" ]; then
  pip install -q "${REPO_ROOT}" >/dev/null 2>&1 || true
else
  pip install -q agentautopsy >/dev/null 2>&1 || true
fi

cd "${GITHUB_WORKSPACE:-$(pwd)}"

LOG_FILE="$(mktemp)"
trap 'rm -f "${LOG_FILE}"' EXIT

echo "[AgentAutopsy] Running tests: ${TEST_COMMAND}"
set +e
bash -lc "${TEST_COMMAND}" 2>&1 | tee "${LOG_FILE}"
TEST_EXIT=${PIPESTATUS[0]}
set -e

if [ "${TEST_EXIT}" -eq 0 ]; then
  echo "[AgentAutopsy] Tests passed."
  exit 0
fi

echo "[AgentAutopsy] Tests failed (exit ${TEST_EXIT}). Running agentautopsy analyze..."
export AGENTAUTOPSY_TEST_LOG="${LOG_FILE}"
export AGENTAUTOPSY_TEST_COMMAND="${TEST_COMMAND}"
export AGENTAUTOPSY_TEST_EXIT="${TEST_EXIT}"

python3 <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agentautopsy.analyzer import _parse_analysis, analyze
from agentautopsy.db import create_tables, get_db, insert_event, insert_run
from agentautopsy.detector import detect_failure, take_snapshot
from agentautopsy.pruner import prune


def _read_test_log() -> str:
    path = os.environ.get("AGENTAUTOPSY_TEST_LOG", "")
    if not path or not Path(path).is_file():
        return ""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return text[-12000:]


def _summarize_failure(log: str) -> tuple[str, str]:
    error_type = "TestFailure"
    message = "Test command failed"

    patterns = [
        r"^(E\s+[\w]+Error:.+)$",
        r"^(FAILED .+)$",
        r"^(AssertionError:.+)$",
        r"^(\w+Error:.+)$",
    ]
    for line in reversed(log.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in patterns:
            match = re.match(pattern, stripped)
            if match:
                message = match.group(1)[:4000]
                head = message.split(":", 1)[0]
                if head.endswith("Error"):
                    error_type = head
                return error_type, message

    lines = [line for line in log.splitlines() if line.strip()]
    if lines:
        message = lines[-1][:4000]
    return error_type, message


def run_agentautopsy_analyze() -> dict[str, str]:
    log = _read_test_log()
    error_type, message = _summarize_failure(log)
    command = os.environ.get("AGENTAUTOPSY_TEST_COMMAND", "pytest")

    db = get_db()
    create_tables(db)
    run_id = insert_run(db, agent_name="github-actions")
    insert_event(
        db,
        run_id,
        "http_request",
        {
            "source": "github-actions",
            "command": command,
            "exit_code": int(os.environ.get("AGENTAUTOPSY_TEST_EXIT", "1")),
        },
    )
    insert_event(
        db,
        run_id,
        "error",
        {
            "error_type": error_type,
            "message": message,
            "test_output": log[-8000:],
        },
    )

    failure = detect_failure(run_id, db)
    if not failure.get("failed"):
        return {
            "run_id": run_id,
            "analysis": "No failure event recorded.",
            "root_cause": message,
            "fix": "Review the test log and fix the failing assertion.",
        }

    snapshot = take_snapshot(run_id, db)
    pruned = prune(snapshot, failure["failure_event_id"])
    analysis = analyze(pruned, failure)
    root_cause, fix = _parse_analysis(analysis)
    if not root_cause:
        root_cause = f"{failure.get('error_type')}: {failure.get('message')}"
    if not fix:
        fix = analysis

    return {
        "run_id": run_id,
        "analysis": analysis,
        "root_cause": root_cause,
        "fix": fix,
    }


def _pull_request_number() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not Path(event_path).is_file():
        return None
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    return int(number) if number else None


def _post_pr_comment(body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    pr_number = _pull_request_number()

    if not token or not repo:
        print("[AgentAutopsy] Missing GITHUB_TOKEN or GITHUB_REPOSITORY; skipping PR comment.")
        return
    if not pr_number:
        print("[AgentAutopsy] Not a pull_request event; skipping PR comment.")
        print(body)
        return

    url = f"{api_url}/repos/{repo}/issues/{pr_number}/comments"
    payload = json.dumps({"body": body[:60000]}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "AgentAutopsy-GitHub-Action",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"[AgentAutopsy] Posted PR comment (HTTP {response.status}).")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"[AgentAutopsy] Failed to post PR comment: HTTP {exc.code} {detail}", file=sys.stderr)


def main() -> None:
    result = run_agentautopsy_analyze()
    command = os.environ.get("AGENTAUTOPSY_TEST_COMMAND", "pytest")
    body = "\n".join(
        [
            "## AgentAutopsy — test failure diagnosis",
            "",
            f"**Test command:** `{command}`",
            f"**Run ID:** `{result['run_id']}`",
            "",
            "### Root cause",
            result["root_cause"],
            "",
            "### Suggested fix",
            "```",
            result["fix"],
            "```",
            "",
            "<details>",
            "<summary>Full analysis</summary>",
            "",
            "```",
            result["analysis"],
            "```",
            "",
            "</details>",
            "",
            "---",
            "*Posted automatically by [AgentAutopsy](https://github.com/Abhisekhpatel/AgentAutopsy)*",
        ]
    )
    _post_pr_comment(body)
    print(result["analysis"])


if __name__ == "__main__":
    main()
PY

exit "${TEST_EXIT}"
