"""Counterfactual pruner for AgentAutopsy snapshots."""

from __future__ import annotations

from typing import Any


def prune(snapshot: list[dict[str, Any]], failure_event_id: str) -> list[dict[str, Any]]:
    by_id = {e.get("id"): e for e in snapshot}
    failure = by_id.get(failure_event_id)
    if failure is None:
        return []

    ordered = sorted(snapshot, key=lambda e: e.get("timestamp", ""))
    failure_index = next(
        (i for i, e in enumerate(ordered) if e.get("id") == failure_event_id), None
    )

    keep_ids: set[str] = {failure_event_id}

    if failure_index is not None and failure_index > 0:
        prev = ordered[failure_index - 1]
        prev_id = prev.get("id")
        if isinstance(prev_id, str):
            keep_ids.add(prev_id)

    for e in ordered:
        ev_id = e.get("id")
        ev_type = e.get("type")
        if not isinstance(ev_id, str):
            continue

        if ev_type in ("llm_call", "tool_call", "error", "http_error", "http_request"):
            keep_ids.add(ev_id)

    pruned = [e for e in ordered if e.get("id") in keep_ids]
    pruned = [e for e in pruned if e.get("type") not in ("llm_response", "http_response")]

    pruned = sorted(pruned, key=lambda e: e.get("timestamp", ""))
    if len(pruned) > 10:
        pruned = pruned[-10:]

    return pruned


if __name__ == "__main__":
    fake_snapshot = [
        {"id": "1", "type": "llm_call", "payload": {"model": "gpt-4"}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:01"},
        {"id": "2", "type": "llm_response", "payload": {}, "cassette_size": 142, "timestamp": "2024-01-01T00:00:02"},
        {"id": "3", "type": "http_request", "payload": {"method": "GET", "url": "https://api.example.com"}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:03"},
        {"id": "4", "type": "http_response", "payload": {"status_code": 200}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:04"},
        {"id": "5", "type": "llm_call", "payload": {"model": "gpt-4"}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:05"},
        {"id": "6", "type": "error", "payload": {"error_type": "TimeoutError", "message": "timed out"}, "cassette_size": 0, "timestamp": "2024-01-01T00:00:06"},
    ]
    pruned = prune(fake_snapshot, "6")
    print(f"Original events: {len(fake_snapshot)}")
    print(f"Pruned events: {len(pruned)}")
    print(f"Kept types: {[e['type'] for e in pruned]}")
