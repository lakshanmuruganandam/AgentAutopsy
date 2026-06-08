"""Fix cache for AgentAutopsy."""

import re
import uuid
from typing import Any

from sqlite_utils import Database


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w+", text) if w}


def _match_score(query_text: str, stored_text: str) -> float:
    query_words = _words(query_text)
    if not query_words:
        return 0.0
    stored_words = _words(stored_text)
    overlap = len(query_words & stored_words)
    return overlap / len(query_words)


def setup_cache(db: Database) -> None:
    db["fix_cache"].create(
        {
            "id": str,
            "failure_type": str,
            "failure_text": str,
            "patch": str,
            "verified": bool,
            "hits": int,
        },
        pk="id",
        if_not_exists=True,
    )


def store_fix(
    db: Database,
    failure_type: str,
    failure_text: str,
    patch: str,
    verified: bool = True,
) -> str:
    import datetime
    fix_id = str(uuid.uuid4())
    db["fix_cache"].insert(
        {
            "id": fix_id,
            "failure_type": failure_type,
            "failure_text": failure_text,
            "patch": patch,
            "verified": verified,
            "hits": 0,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        pk="id",
        alter=True,
    )
    return fix_id


def lookup_fix(
    db: Database,
    failure_type: str,
    failure_text: str,
    threshold: float = 0.6,
    ttl_days: int = 14,
) -> str | None:
    if not db["fix_cache"].exists():
        return None
        
    import datetime
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=ttl_days)).isoformat()
    try:
        db.execute("DELETE FROM fix_cache WHERE created_at < ?", [cutoff])
    except Exception:
        pass

    best_patch: str | None = None
    best_score = -1.0
    best_id: str | None = None

    for row in db["fix_cache"].rows_where(
        where="failure_type = ?",
        where_args=[failure_type],
    ):
        score = _match_score(failure_text, row["failure_text"])
        if score >= threshold and score > best_score:
            best_score = score
            best_patch = row["patch"]
            best_id = row["id"]

    if best_id is None:
        return None

    db.execute(
        "UPDATE fix_cache SET hits = hits + 1 WHERE id = ?",
        [best_id],
    )
    return best_patch


def cache_stats(db: Database) -> dict[str, int]:
    if not db["fix_cache"].exists():
        return {"total_fixes": 0, "total_hits": 0}

    total_fixes = db["fix_cache"].count
    row = db.execute("SELECT COALESCE(SUM(hits), 0) FROM fix_cache").fetchone()
    total_hits = int(row[0]) if row else 0
    return {"total_fixes": total_fixes, "total_hits": total_hits}


if __name__ == "__main__":
    from agentautopsy.db import create_tables, get_db

    db = get_db()
    create_tables(db)
    setup_cache(db)
    store_fix(db, "TimeoutError", "request timed out after 30s calling external api", "Add timeout=60 and retry logic")
    store_fix(db, "AuthenticationError", "invalid api key provided", "Check OPENAI_API_KEY environment variable")
    result = lookup_fix(db, "TimeoutError", "timed out calling api")
    print(f"Cache hit: {result}")
    miss = lookup_fix(db, "TimeoutError", "memory allocation failed")
    print(f"Cache miss: {miss}")
    stats = cache_stats(db)
    print(f"Stats: {stats}")
