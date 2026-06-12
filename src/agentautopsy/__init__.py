"""AgentAutopsy — when your agent fails, this tells you exactly why."""

from __future__ import annotations

import atexit

from agentautopsy.db import create_tables, get_db, insert_run
from agentautopsy.interceptor import (
    start_anthropic_interceptor,
    start_http_interceptor,
    start_interceptor,
)
from agentautopsy.mcp_handler import MCPAutopsy
from agentautopsy.reporter import print_report
from agentautopsy.dvr_replay import DVRReplay
from agentautopsy.eval_generator import EvalGenerator
from agentautopsy.schema_drift import SchemaDriftDetector

__all__ = [
    "DVRReplay",
    "EvalGenerator",
    "MCPAutopsy",
    "SchemaDriftDetector",
    "get_callback_handler",
    "get_crewai_handler",
    "get_langgraph_handler",
    "watch",
    "watch_mcp",
]

_watch_context: tuple[str, object] | None = None
_mcp_autopsy: object | None = None


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


def watch_mcp(
    server_name: str | None = None,
    *,
    agent_name: str | None = None,
    parent_run_id: str | None = None,
):
    """Start MCP post-mortem tracing — one import, one line."""
    global _mcp_autopsy

    _mcp_autopsy = MCPAutopsy.start(
        server_name=server_name,
        agent_name=agent_name,
        parent_run_id=parent_run_id,
    )
    return _mcp_autopsy


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
    SchemaDriftDetector(run_id=run_id, db=db, agent_name=agent_name or "agent").watch()
    DVRReplay(db=db, run_id=run_id).watch()
    EvalGenerator(db=db, run_id=run_id, agent_name=agent_name or "agent").watch()

    import sys

    _original_excepthook = sys.excepthook

    def _autopsy_excepthook(exc_type, exc_value, exc_traceback):
        from agentautopsy.db import insert_event

        insert_event(
            db,
            run_id,
            "error",
            {"error_type": exc_type.__name__, "message": str(exc_value)},
        )
        _original_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _autopsy_excepthook
    import time

    label = agent_name or "agent"
    print("\n\033[38;5;39m" + "━" * 60 + "\033[0m")
    time.sleep(0.1)
    print("\033[1;38;5;82m⚡ [AgentAutopsy] Engine Initialized\033[0m")
    time.sleep(0.1)
    print(f"\033[38;5;244m▶ Target:  \033[1;37m{label}\033[0m")
    time.sleep(0.1)
    print(f"\033[38;5;244m▶ Session: \033[38;5;141m{run_id}\033[0m")
    time.sleep(0.1)
    if parent_run_id:
        print(f"\033[38;5;244m▶ Parent:  \033[38;5;141m{parent_run_id}\033[0m")
        time.sleep(0.1)
    print(
        "\033[38;5;244m▶ Status:  \033[38;5;11mIntercepting LLM & HTTP Traffic in real-time...\033[0m"
    )
    time.sleep(0.1)
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
            print("\n\033[38;5;39m" + "━" * 60 + "\033[0m")
            time.sleep(0.1)
            print("\033[1;38;5;82m✅ [AgentAutopsy] Analysis Complete\033[0m")
            time.sleep(0.1)
            print(
                f"\033[38;5;244m▶ Run \033[38;5;141m{run_id}\033[38;5;244m executed flawlessly.\033[0m"
            )
            time.sleep(0.1)
            print(
                "\033[38;5;244m▶ Type \033[1;37magentautopsy ui\033[38;5;244m in your terminal to view the trace graph.\033[0m"
            )
            time.sleep(0.1)
            print("\033[38;5;39m" + "━" * 60 + "\033[0m\n")
            return

        from agentautopsy.db import mark_run_failed

        mark_run_failed(db, run_id)

        from agentautopsy.eval_generator import generate_eval_for_run

        eval_path = generate_eval_for_run(run_id, db)

        time.sleep(0.1)
        print("\n\033[1;38;5;196m❌ [AgentAutopsy] Critical Failure Intercepted\033[0m")
        time.sleep(0.1)
        print(f"\033[38;5;244m▶ Error: \033[1;38;5;196m{result['error_type']}\033[0m")
        time.sleep(0.1)
        print(f"\033[38;5;244m▶ Trace: \033[38;5;196m{result['message']}\033[0m")
        if eval_path:
            time.sleep(0.1)
            print(
                f"\033[38;5;244m▶ Eval:  \033[1;38;5;82mRegression test generated → {eval_path}\033[0m"
            )

        cached = lookup_fix(db, result["error_type"], result["message"])
        if cached:
            time.sleep(0.8)
            print(
                "\n\033[38;5;39m▶ \033[1;38;5;141mAI Root Cause Analysis Triggered...\033[0m"
            )
            time.sleep(0.8)
            print(
                "\033[38;5;39m▶ \033[1;38;5;82mCache Hit — Fix Found Instantly\033[0m\n"
            )
            time.sleep(0.1)
            print(cached)
            return

        snapshot = take_snapshot(run_id, db)
        pruned = prune(snapshot, result["failure_event_id"])

        try:
            analysis = analyze(pruned, result)
            print(f"\n[AgentAutopsy] analysis:\n{analysis}")

            replay_result = replay(run_id, db, analysis)
            if replay_result["verified"]:
                print("\n[AgentAutopsy] fix verified ✓")
                print("✓ Replay passed")
                print("✓ Failure resolved")
                store_fix(
                    db, result["error_type"], result["message"], analysis, verified=True
                )
            else:
                print("\n[AgentAutopsy] fix not verified — review manually")
        except Exception as e:
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                print(
                    "\n[AgentAutopsy] Auto-fix bypassed: LLM authentication failed (check ANTHROPIC_API_KEY)."
                )
            else:
                print(f"\n[AgentAutopsy] Auto-fix failed: {e}")

        print_report(run_id, db)

    atexit.register(on_exit)
