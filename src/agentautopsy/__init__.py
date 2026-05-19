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
    run_id = insert_run(db)
    start_interceptor(run_id, db)
    start_anthropic_interceptor(run_id, db)
    start_http_interceptor(run_id, db)
    print(f"AgentAutopsy watching — run {run_id}")

    def on_exit():
        from agentautopsy.detector import detect_failure

        result = detect_failure(run_id, db)
        if result["failed"]:
            print(f"\n[AgentAutopsy] Failure detected: {result['error_type']}: {result['message']}")
            print_report(run_id, db)
        else:
            print(f"\n[AgentAutopsy] Run completed cleanly — {run_id}")

    atexit.register(on_exit)
