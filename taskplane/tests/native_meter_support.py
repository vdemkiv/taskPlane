"""Small native Codex counter fixture for real hook-path tests."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def attach_native_counter(
        event: dict[str, Any], workspace: str, *, total_tokens: int = 3,
        label: str = "screen") -> dict[str, Any]:
    """Attach a valid, non-zero Codex counter to a host hook event.

    Tests using this helper exercise the same fail-closed native projection as
    production.  The counter lives inside the owned temporary workspace and
    contains no prompt, message, or model-output content.
    """
    if total_tokens <= 0:
        raise ValueError("native test counter must be non-zero")
    source = os.path.join(
        workspace, ".taskplane", "test-native-counters", f"{label}.jsonl")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    session_id = "test-" + hashlib.sha256(
        (os.path.realpath(workspace) + "\0" + label).encode("utf-8")
    ).hexdigest()[:24]
    with open(source, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "timestamp": "2026-09-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "id": session_id,
                "timestamp": "2026-09-01T00:00:00Z",
                "thread_source": "test",
            },
        }, sort_keys=True) + "\n")
        stream.write(json.dumps({
            "timestamp": "2026-09-01T00:00:01Z",
            "ordinal": 1,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": total_tokens,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total_tokens,
                }},
            },
        }, sort_keys=True) + "\n")
    return {
        **event,
        "turn_id": str(event.get("turn_id") or f"test-turn-{label}"),
        "transcript_path": source,
    }
