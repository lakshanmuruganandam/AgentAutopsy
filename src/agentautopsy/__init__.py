"""AgentAutopsy — when your agent fails, this tells you exactly why."""

import atexit

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.interceptor import (
    start_interceptor,
    start_anthropic_interceptor,
    start_http_interceptor,
)
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
    label = agent_name or "agent"
    print(f"[AgentAutopsy] watching — {label} — run {run_id}")
    if parent_run_id:
        print(f"[AgentAutopsy] parent run {parent_run_id}")

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
            print("✓ Replay passed")
            print("✓ Failure resolved")
            store_fix(db, result["error_type"], result["message"], analysis, verified=True)
        else:
            print(f"\n[AgentAutopsy] fix not verified — review manually")

        print_report(run_id, db)

    atexit.register(on_exit)
