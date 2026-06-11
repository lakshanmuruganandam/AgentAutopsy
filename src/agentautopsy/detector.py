"""Failure detection and trace snapshots for AgentAutopsy."""

from __future__ import annotations

import json
from typing import Any

from sqlite_utils import Database


def detect_failure(run_id: str, db: Database) -> dict[str, Any]:
    failure_types = ("error", "http_error")
    for failure_type in failure_types:
        errors = list(
            db["events"].rows_where(
                where='run_id = ? AND "type" = ?',
                where_args=[run_id, failure_type],
                order_by="timestamp",
            )
        )
        if not errors:
            continue

        row = errors[0]
        payload: dict[str, Any]
        raw_payload = row.get("payload")
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        return {
            "failed": True,
            "run_id": run_id,
            "error_type": payload.get("exception_type") or payload.get("error_type"),
            "message": payload.get("message"),
            "failure_event_id": row["id"],
            "failure_event_type": failure_type,
        }

    return {"failed": False, "run_id": run_id}


def take_snapshot(run_id: str, db: Database) -> list[dict[str, Any]]:
    rows = list(
        db["events"].rows_where(
            where="run_id = ?",
            where_args=[run_id],
            order_by="timestamp",
        )
    )
    snapshot: list[dict[str, Any]] = []
    for row in rows:
        raw_payload = row.get("payload")
        try:
            payload_obj: Any = (
                json.loads(raw_payload) if raw_payload is not None else {}
            )
            if not isinstance(payload_obj, dict):
                payload_obj = {}
        except (json.JSONDecodeError, TypeError):
            payload_obj = {}

        cassette = row.get("cassette")
        cassette_size = len(cassette) if cassette is not None else 0

        snapshot.append(
            {
                "id": row["id"],
                "type": row["type"],
                "payload": payload_obj,
                "cassette_size": cassette_size,
                "timestamp": row["timestamp"],
            }
        )
    return snapshot


if __name__ == "__main__":
    from agentautopsy.db import create_tables, get_db, insert_event, insert_run

    db = get_db()
    create_tables(db)
    run_id = insert_run(db)
    insert_event(db, run_id, "llm_call", {"model": "gpt-4", "messages": []})
    insert_event(
        db,
        run_id,
        "error",
        {"error_type": "TimeoutError", "message": "request timed out"},
    )
    result = detect_failure(run_id, db)
    print(f"Failed: {result['failed']}")
    print(f"Error: {result['error_type']}: {result['message']}")
    snapshot = take_snapshot(run_id, db)
    print(f"Snapshot has {len(snapshot)} events")
    print(f"Event types: {[e['type'] for e in snapshot]}")
