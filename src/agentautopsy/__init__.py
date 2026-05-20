"""AgentAutopsy — when your agent fails, this tells you exactly why."""

import atexit

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.interceptor import (
    start_interceptor,
    start_anthropic_interceptor,
    start_http_interceptor,
)
from agentautopsy.reporter import print_report


def watch():
    db = get_db()
    create_tables(db)
    from agentautopsy.cache import setup_cache

    setup_cache(db)
    run_id = insert_run(db)
    start_interceptor(run_id, db)
    start_anthropic_interceptor(run_id, db)
    start_http_interceptor(run_id, db)
    print(f"[AgentAutopsy] watching — run {run_id}")

    def on_exit():
        from agentautopsy.detector import detect_failure, take_snapshot
        from agentautopsy.pruner import prune
        from agentautopsy.analyzer import analyze
        from agentautopsy.replay import replay
        from agentautopsy.cache import lookup_fix, store_fix

        result = detect_failure(run_id, db)
        if not result["failed"]:
            print(f"[AgentAutopsy] run completed cleanly — {run_id}")
            return

        print(f"\n[AgentAutopsy] failure detected: {result['error_type']}: {result['message']}")

        cached = lookup_fix(db, result["error_type"], result["message"])
        if cached:
            print(f"[AgentAutopsy] cache hit — fix found instantly:")
            print(cached)
            return

        snapshot = take_snapshot(run_id, db)
        pruned = prune(snapshot, result["failure_event_id"])
        analysis = analyze(pruned, result)
        print(f"\n[AgentAutopsy] analysis:\n{analysis}")

        replay_result = replay(run_id, db, analysis)
        if replay_result["verified"]:
            print(f"\n[AgentAutopsy] fix verified ✓")
            store_fix(db, result["error_type"], result["message"], analysis, verified=True)
        else:
            print(f"\n[AgentAutopsy] fix not verified — review manually")

        print_report(run_id, db)

    atexit.register(on_exit)
