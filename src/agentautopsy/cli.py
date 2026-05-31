"""Command-line interface for AgentAutopsy."""

import json
import sys

from agentautopsy.cache import cache_stats, setup_cache
from agentautopsy.db import create_tables, get_db
from agentautopsy.reporter import print_report


def _usage() -> None:
    print(
        """Usage: agentautopsy <command>

Commands:
  runs              List all runs (id, start_time, status)
  replay <run_id>   Print the event report for a run
  share <run_id>    Export a run trace to a shareable JSON file
  fix <run_id>      Apply an automated fix for a failed run
  stats             Show fix cache statistics
  ui                Open the web UI in your browser

Examples:
  agentautopsy runs
  agentautopsy replay abc-123-def
  agentautopsy share abc-123-def
  agentautopsy fix abc-123-def
  agentautopsy fix abc-123-def --create-pr
  agentautopsy stats
  agentautopsy ui"""
    )


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        _usage()
        return

    cmd = argv[0]
    db = get_db()
    create_tables(db)

    if cmd == "runs":
        if not db["runs"].exists():
            print("No runs table yet.")
            return
        rows = list(db["runs"].rows_where(order_by="start_time desc"))
        if not rows:
            print("No runs found.")
            return
        for row in rows:
            print(f"{row['id']}\t{row['start_time']}\t{row['status']}")
        return

    if cmd == "replay":
        if len(argv) < 2:
            print("usage: agentautopsy replay <run_id>", file=sys.stderr)
            sys.exit(2)
        run_id = argv[1]
        print_report(run_id, db)
        return

    if cmd == "share":
        if len(argv) < 2:
            print("usage: agentautopsy share <run_id>", file=sys.stderr)
            sys.exit(2)
        run_id = argv[1]
        from agentautopsy.share import share_run

        try:
            path = share_run(run_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        print(path)
        return

    if cmd == "fix":
        if len(argv) < 2:
            print("usage: agentautopsy fix <run_id> [--create-pr]", file=sys.stderr)
            sys.exit(2)
        run_id = argv[1]
        create_pr_flag = "--create-pr" in argv[2:]
        from agentautopsy.autofix import apply_fix
        from agentautopsy.github_pr import create_pr

        result = apply_fix(run_id)
        print(json.dumps(result, indent=2))
        if create_pr_flag:
            if not result.get("success"):
                print("Skipping PR creation because fix did not succeed.", file=sys.stderr)
                sys.exit(1)
            try:
                pr = create_pr(
                    run_id,
                    result.get("fix", ""),
                    error_type=str(result.get("error_type") or "agent failure"),
                    root_cause=str(result.get("root_cause") or ""),
                    fix_applied=str(result.get("fix") or ""),
                    test_results=str(result.get("test_output") or ""),
                    file_path=result.get("file_path"),
                )
                print(json.dumps(pr, indent=2))
            except Exception as exc:
                print(f"PR creation failed: {exc}", file=sys.stderr)
                sys.exit(1)
        sys.exit(0 if result.get("success") else 1)

    if cmd == "stats":
        setup_cache(db)
        stats = cache_stats(db)
        print(f"total_fixes: {stats['total_fixes']}")
        print(f"total_hits: {stats['total_hits']}")

        if db["events"].exists():
            row = db.execute(
                """
                SELECT
                    COALESCE(SUM(token_input), 0),
                    COALESCE(SUM(token_output), 0),
                    COALESCE(SUM(cost_usd), 0.0)
                FROM events
                """
            ).fetchone()
            token_input = int(row[0]) if row else 0
            token_output = int(row[1]) if row else 0
            total_cost = float(row[2]) if row else 0.0
            print(f"total_tokens_input: {token_input}")
            print(f"total_tokens_output: {token_output}")
            print(f"total_tokens: {token_input + token_output}")
            print(f"total_cost_usd: {total_cost:.6f}")
        else:
            print("total_tokens_input: 0")
            print("total_tokens_output: 0")
            print("total_tokens: 0")
            print("total_cost_usd: 0.000000")
        return

    if cmd == "ui":
        from agentautopsy.ui import start_ui

        path = start_ui()
        print(f"Opened {path}")
        return

    print(f"Unknown command: {cmd}", file=sys.stderr)
    _usage()
    sys.exit(2)
