"""Cassette serialization for AgentAutopsy LLM responses."""

import json


def save_cassette(response_object: object) -> bytes:
    try:
        dumped = response_object.model_dump()
        return json.dumps(dumped).encode()
    except Exception:
        return str(response_object).encode()


def load_cassette(cassette_bytes: bytes) -> dict:
    try:
        data = json.loads(cassette_bytes.decode())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


if __name__ == "__main__":
    test_bytes = save_cassette(
        type("R", (), {"model_dump": lambda self: {"id": "test", "content": "hello"}})()
    )
    print(f"Cassette saved: {len(test_bytes)} bytes")
    result = load_cassette(test_bytes)
    print(f"Cassette loaded: {result}")
    bad = load_cassette(b"not json at all")
    print(f"Bad cassette returns: {bad}")
