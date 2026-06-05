"""Terminal reporter for AgentAutopsy."""

import json
from typing import Any

from agentautopsy.interceptor import _http_display_path, infer_http_root_cause

RESET = "\033[0m"
CYAN = "\033[96m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
RED_BOLD = "\033[91;1m"

EVENT_COLORS: dict[str, str] = {
    "llm_call": CYAN,
    "error": RED,
    "http_error": RED,
    "http_request": YELLOW,
    "http_response": GREEN,
    "llm_response": BLUE,
}

TAG_WIDTH = 17


def _colored_event_tag(ev_type: str) -> str:
    color = EVENT_COLORS.get(ev_type)
    if color:
        return f"{color}[{ev_type}]{RESET}"
    return f"[{ev_type}]"


def _print_event_line(ev_type: str, detail: str) -> None:
    plain_tag = f"[{ev_type}]"
    colored_tag = _colored_event_tag(ev_type)
    padding = " " * max(0, TAG_WIDTH - len(plain_tag))
    if detail:
        print(f"{colored_tag}{padding}{detail}")
    else:
        print(colored_tag)


def _failure_payload(rows: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
    for row in rows:
        if row["type"] in ("http_error", "error"):
            try:
                payload = json.loads(row["payload"]) if row.get("payload") else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if isinstance(payload, dict):
                return row["type"], payload
    return None, {}


def print_report(run_id: str, db: Any) -> None:
    sep = "═══════════════════════════════════"
    rows = list(
        db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        )
    )
    run_row = db["runs"].get(run_id) if db["runs"].exists() else None
    run_status = run_row.get("status", "unknown") if run_row else "unknown"

    print(sep)
    print(" AgentAutopsy — Run Report")
    print(f" Run ID: {run_id}")
    print(sep)

    for row in rows:
        ev_type = row["type"]
        try:
            payload = json.loads(row["payload"]) if row.get("payload") else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        cassette = row.get("cassette")
        blob = cassette if cassette is not None else b""

        if ev_type == "http_request":
            print(f"{payload.get('method')} {_http_display_path(payload.get('url'))}")
            continue
        if ev_type == "http_error":
            exc_type = payload.get("exception_type") or payload.get("error_type")
            print(f"ERROR: {exc_type}")
            continue

        if ev_type == "llm_call":
            detail = f"model: {payload.get('model')}"
        elif ev_type == "llm_response":
            detail = f"cassette: {len(blob)} bytes"
        elif ev_type == "http_response":
            detail = f"status: {payload.get('status_code')}"
        elif ev_type == "error":
            detail = f"{payload.get('error_type')}: {payload.get('message')}"
        else:
            detail = ""

        _print_event_line(ev_type, detail)

    failure_type, error_payload = _failure_payload(rows)
    if failure_type is not None:
        root_sep = "══════════════════════════════════════"
        if failure_type == "http_error":
            root_cause = infer_http_root_cause(error_payload)
        else:
            error_type = error_payload.get("error_type")
            message = error_payload.get("message")
            root_cause = f"{error_type} — {message}"
        print(root_sep)
        print(f"{RED_BOLD}Root cause: {root_cause}{RESET}")
        print(f"Run status: {run_status}")
        print(root_sep)

    print(sep)
    print(f"Total events: {len(rows)}")
