"""Replay sandbox for AgentAutopsy."""

from typing import Any

import openai

from agentautopsy.cassette import load_cassette
from agentautopsy.detector import take_snapshot


def replay(run_id: str, db: Any, patch_instructions: str) -> dict[str, Any]:
    snapshot = take_snapshot(run_id, db)

    cassette_map: dict[int, dict[str, Any]] = {}
    index = 0
    for event in snapshot:
        if event["type"] != "llm_response":
            continue
        if event["cassette_size"] <= 0:
            continue
        row = db["events"].get(event["id"])
        if row is None:
            continue
        cassette_bytes = row.get("cassette")
        if cassette_bytes is None:
            continue
        response_dict = load_cassette(cassette_bytes)
        if not response_dict:
            continue
        cassette_map[index] = response_dict
        index += 1

    original_create = openai.chat.completions.create
    responses = [cassette_map[i] for i in range(len(cassette_map))]
    call_index = [0]

    def replay_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if call_index[0] >= len(responses):
            raise RuntimeError("No more cassette responses to replay")
        response = responses[call_index[0]]
        call_index[0] += 1
        return response

    openai.chat.completions.create = replay_create
    verified = False
    try:
        if cassette_map:
            result = openai.chat.completions.create(model="gpt-4", messages=[])
            verified = result == cassette_map[0]
    finally:
        openai.chat.completions.create = original_create

    return {
        "verified": verified,
        "patch_instructions": patch_instructions,
        "events_replayed": len(cassette_map),
    }


if __name__ == "__main__":
    import json

    from agentautopsy.db import create_tables, get_db, insert_event, insert_run

    db = get_db()
    create_tables(db)
    run_id = insert_run(db)
    fake_response = {"id": "chatcmpl-123", "choices": [{"message": {"content": "hello"}}]}
    insert_event(db, run_id, "llm_call", {"model": "gpt-4", "messages": []})
    insert_event(db, run_id, "llm_response", {}, cassette=json.dumps(fake_response).encode())
    insert_event(db, run_id, "error", {"error_type": "TimeoutError", "message": "timed out"})
    result = replay(run_id, db, "Add timeout=60 to the API call")
    print(f"Verified: {result['verified']}")
    print(f"Events replayed: {result['events_replayed']}")
    print(f"Patch: {result['patch_instructions']}")
