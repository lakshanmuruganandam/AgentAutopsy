"""Database layer for AgentAutopsy."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlite_utils import Database

OBSERVABILITY_COLUMNS: dict[str, type] = {
    "latency_ms": int,
    "token_input": int,
    "token_output": int,
    "cost_usd": float,
}


def get_db() -> Database:
    return Database(Path.cwd() / "agentautopsy.db")


def _ensure_events_observability_columns(db: Database) -> None:
    if not db["events"].exists():
        return
    existing = {column.name for column in db["events"].columns}
    for name, col_type in OBSERVABILITY_COLUMNS.items():
        if name not in existing:
            db["events"].add_column(name, col_type)


def create_tables(db: Database) -> None:
    db["runs"].create(
        {
            "id": str,
            "start_time": str,
            "status": str,
            "framework": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["events"].create(
        {
            "id": str,
            "run_id": str,
            "timestamp": str,
            "type": str,
            "payload": str,
            "cassette": bytes,
            "latency_ms": int,
            "token_input": int,
            "token_output": int,
            "cost_usd": float,
        },
        pk="id",
        if_not_exists=True,
    )
    _ensure_events_observability_columns(db)


def insert_run(db: Database) -> str:
    run_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).isoformat()
    db["runs"].insert(
        {
            "id": run_id,
            "start_time": start_time,
            "status": "running",
            "framework": "unknown",
        },
        pk="id",
    )
    return run_id


def insert_event(
    db: Database,
    run_id: str,
    type: str,
    payload: dict,
    cassette: bytes | None = None,
    latency_ms: int | None = None,
    token_input: int | None = None,
    token_output: int | None = None,
    cost_usd: float | None = None,
) -> None:
    event_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    row: dict[str, object] = {
        "id": event_id,
        "run_id": run_id,
        "timestamp": timestamp,
        "type": type,
        "payload": json.dumps(payload),
        "cassette": cassette,
    }
    if latency_ms is not None:
        row["latency_ms"] = latency_ms
    if token_input is not None:
        row["token_input"] = token_input
    if token_output is not None:
        row["token_output"] = token_output
    if cost_usd is not None:
        row["cost_usd"] = cost_usd
    db["events"].insert(row, pk="id")


if __name__ == "__main__":
    db = get_db()
    create_tables(db)
    run_id = insert_run(db)
    insert_event(db, run_id, "test", {"msg": "day 2 works"})
    print(f"Run created: {run_id}")
    print(f"Events in db: {db['events'].count}")
