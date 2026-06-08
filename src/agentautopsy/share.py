"""Shareable trace exports for AgentAutopsy."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from agentautopsy.db import get_db


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    payload_raw = row.get("payload")
    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except (json.JSONDecodeError, TypeError):
        payload = payload_raw

    cassette = row.get("cassette")
    cassette_b64 = None
    if cassette is not None:
        cassette_b64 = base64.b64encode(cassette).decode("ascii")

    event: dict[str, Any] = {
        "id": row.get("id"),
        "run_id": row.get("run_id"),
        "timestamp": row.get("timestamp"),
        "type": row.get("type"),
        "payload": payload,
    }
    if cassette_b64 is not None:
        event["cassette_b64"] = cassette_b64
    for key in ("latency_ms", "token_input", "token_output", "cost_usd"):
        if row.get(key) is not None:
            event[key] = row.get(key)
    return event


def share_run(run_id: str) -> Path:
    """Export a run's full events to shares/ and return the file path."""
    db = get_db()
    if not db["runs"].exists():
        raise ValueError(f"Run not found: {run_id}")

    # pylint: disable=no-member
    run_row = db["runs"].get(run_id)
    if run_row is None:
        raise ValueError(f"Run not found: {run_id}")

    events: list[dict[str, Any]] = []
    if db["events"].exists():
        events = [
            _serialize_event(dict(row))
            for row in db["events"].rows_where(
                where="run_id = ?",
                where_args=[run_id],
                order_by="timestamp",
            )
        ]

    export = {
        "run_id": run_id,
        "run": {
            "id": run_row["id"],
            "start_time": run_row.get("start_time"),
            "status": run_row.get("status"),
            "framework": run_row.get("framework"),
        },
        "events": events,
    }

    shares_dir = Path.cwd() / "shares"
    shares_dir.mkdir(parents=True, exist_ok=True)
    output_path = shares_dir / f"{run_id}.json"
    output_path.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")
    return output_path.resolve()
