"""AgentAutopsy — when your agent fails, this tells you exactly why."""

from __future__ import annotations

import atexit

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.interceptor import (start_anthropic_interceptor,
                                      start_http_interceptor,
                                      start_interceptor)
from agentautopsy.reporter import print_report

_watch_context: tuple[str, object] | None = None


def get_callback_handler():
    """Return a LangChain callback handler for the active watch() run."""
    if _watch_context is None:
        raise RuntimeError("Call agentautopsy.watch() before get_callback_handler()")
    run_id, db = _watch_context
    from agentautopsy.langchain_handler import AgentAutopsyCallbackHandler

    return AgentAutopsyCallbackHandler(run_id, db)


def get_langgraph_handler():
    """Return a LangGraph callback handler for the active watch() run."""
    if _watch_context is None:
        raise RuntimeError("Call agentautopsy.watch() before get_langgraph_handler()")
    run_id, db = _watch_context
    from agentautopsy.langgraph_handler import AgentAutopsyLangGraphHandler

    return AgentAutopsyLangGraphHandler(run_id, db)


def get_crewai_handler():
    """Return a CrewAI callback handler for the active watch() run."""
    if _watch_context is None:
        raise RuntimeError("Call agentautopsy.watch() before get_crewai_handler()")
    run_id, db = _watch_context
    from agentautopsy.crewai_handler import AgentAutopsyCrewAIHandler

    return AgentAutopsyCrewAIHandler(run_id, db)


def watch(
    agent_name: str | None = None,
    parent_run_id: str | None = None,
):
    global _watch_context

    db = get_db()
    create_tables(db)
    from agentautopsy.cache import setup_cache

    setup_cache(db)
    run_id = insert_run(
        db,
        agent_name=agent_name,
        parent_run_id=parent_run_id,
    )
    _watch_context = (run_id, db)
    start_interceptor(run_id, db)
    start_anthropic_interceptor(run_id, db)
    start_http_interceptor(run_id, db)
    import time
    label = agent_name or "agent"
    print("\n\033[38;5;39m" + "━" * 60 + "\033[0m"); time.sleep(0.1)
    print(f"\033[1;38;5;82m⚡ [AgentAutopsy] Engine Initialized\033[0m"); time.sleep(0.1)
    print(f"\033[38;5;244m▶ Target:  \033[1;37m{label}\033[0m"); time.sleep(0.1)
    print(f"\033[38;5;244m▶ Session: \033[38;5;141m{run_id}\033[0m"); time.sleep(0.1)
    if parent_run_id:
        print(f"\033[38;5;244m▶ Parent:  \033[38;5;141m{parent_run_id}\033[0m"); time.sleep(0.1)
    print(f"\033[38;5;244m▶ Status:  \033[38;5;11mIntercepting LLM & HTTP Traffic in real-time...\033[0m"); time.sleep(0.1)
    print("\033[38;5;39m" + "━" * 60 + "\033[0m\n")

    def on_exit():
        from agentautopsy.analyzer import analyze
        from agentautopsy.cache import lookup_fix, store_fix
        from agentautopsy.detector import detect_failure, take_snapshot
        from agentautopsy.pruner import prune
        from agentautopsy.replay import replay

        result = detect_failure(run_id, db)
        if not result["failed"]:
            from agentautopsy.db import mark_run_completed

            mark_run_completed(db, run_id)
            print("\n\033[38;5;39m" + "━" * 60 + "\033[0m"); time.sleep(0.1)
            print(f"\033[1;38;5;82m✅ [AgentAutopsy] Analysis Complete\033[0m"); time.sleep(0.1)
            print(f"\033[38;5;244m▶ Run \033[38;5;141m{run_id}\033[38;5;244m executed flawlessly.\033[0m"); time.sleep(0.1)
            print(f"\033[38;5;244m▶ Type \033[1;37magentautopsy ui\033[38;5;244m in your terminal to view the trace graph.\033[0m"); time.sleep(0.1)
            print("\033[38;5;39m" + "━" * 60 + "\033[0m\n")
            return

        from agentautopsy.db import mark_run_failed

        mark_run_failed(db, run_id)

        import time as _t
        _t.sleep(0.1)
        print(f"\n\033[1;38;5;196m❌ [AgentAutopsy] Critical Failure Intercepted\033[0m")
        _t.sleep(0.1)
        print(f"\033[38;5;244m▶ Error: \033[1;38;5;196m{result['error_type']}\033[0m")
        _t.sleep(0.1)
        print(f"\033[38;5;244m▶ Trace: \033[38;5;196m{result['message']}\033[0m")

        cached = lookup_fix(db, result["error_type"], result["message"])
        if cached:
            _t.sleep(0.8)
            print(f"\n\033[38;5;39m▶ \033[1;38;5;141mAI Root Cause Analysis Triggered...\033[0m")
            _t.sleep(0.8)
            print(f"\033[38;5;39m▶ \033[1;38;5;82mCache Hit — Fix Found Instantly\033[0m\n")
            _t.sleep(0.1)
            print(cached)
            return

        snapshot = take_snapshot(run_id, db)
        pruned = prune(snapshot, result["failure_event_id"])

        try:
            analysis = analyze(pruned, result)
            print(f"\n[AgentAutopsy] analysis:\n{analysis}")

            replay_result = replay(run_id, db, analysis)
            if replay_result["verified"]:
                print(f"\n[AgentAutopsy] fix verified ✓")
                print("✓ Replay passed")
                print("✓ Failure resolved")
                store_fix(
                    db, result["error_type"], result["message"], analysis, verified=True
                )
            else:
                print(f"\n[AgentAutopsy] fix not verified — review manually")
        except Exception as e:
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                print(
                    "\n[AgentAutopsy] Auto-fix bypassed: LLM authentication failed (check ANTHROPIC_API_KEY)."
                )
            else:
                print(f"\n[AgentAutopsy] Auto-fix failed: {e}")

        print_report(run_id, db)

    atexit.register(on_exit)
