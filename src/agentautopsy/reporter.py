"""Terminal reporter for AgentAutopsy."""

import json
from typing import Any


def print_report(run_id: str, db: Any) -> None:
    sep = "═══════════════════════════════════"
    rows = list(
        db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        )
    )

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

        if ev_type == "llm_call":
            detail = f"model: {payload.get('model')}"
        elif ev_type == "llm_response":
            detail = f"cassette: {len(blob)} bytes"
        elif ev_type == "http_request":
            detail = f"{payload.get('method')} {payload.get('url')}"
        elif ev_type == "http_response":
            detail = f"status: {payload.get('status_code')}"
        elif ev_type == "error":
            detail = f"{payload.get('error_type')}: {payload.get('message')}"
        else:
            detail = ""

        tag = f"[{ev_type}]"
        label = tag.ljust(17)
        if detail:
            print(f"{label}{detail}")
        else:
            print(tag)

    print(sep)
    print(f"Total events: {len(rows)}")
