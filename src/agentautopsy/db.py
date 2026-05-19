"""Database layer for AgentAutopsy."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlite_utils import Database


def get_db() -> Database:
    return Database(Path.cwd() / "agentautopsy.db")


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
        },
        pk="id",
        if_not_exists=True,
    )


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
    db: Database, run_id: str, type: str, payload: dict, cassette: bytes | None = None
) -> None:
    event_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    db["events"].insert(
        {
            "id": event_id,
            "run_id": run_id,
            "timestamp": timestamp,
            "type": type,
            "payload": json.dumps(payload),
            "cassette": cassette,
        },
        pk="id",
    )


if __name__ == "__main__":
    db = get_db()
    create_tables(db)
    run_id = insert_run(db)
    insert_event(db, run_id, "test", {"msg": "day 2 works"})
    print(f"Run created: {run_id}")
    print(f"Events in db: {db['events'].count}")
