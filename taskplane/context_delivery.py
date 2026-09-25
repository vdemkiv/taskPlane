"""Transport helpers for clients of the bounded context CLI.

Adapters must return every response body to their consumer before requesting the
next one. These helpers never consume context, approve work or hide tool output.
"""
from __future__ import annotations

import json
from typing import Any


class CommandOutput:
    """Accumulate subprocess/tool chunks and parse only a terminal response.

    A running handle is not JSON completion, including when a chunk happens to
    contain valid JSON. Keep this object across host waits; use a long host wait
    within the user's progress-update interval rather than short model polls.
    """

    def __init__(self, limit_bytes: int = 65536):
        if type(limit_bytes) is not int or not 1024 <= limit_bytes <= 1048576:
            raise ValueError("Invalid combined output budget")
        self.limit_bytes = limit_bytes
        self.parts: list[str] = []
        self.size = 0
        self.done = False

    def feed(self, output: str, *, exit_code: int | None = None) -> dict[str, Any] | None:
        if self.done:
            raise ValueError("Command already terminated")
        self.size += len(output.encode("utf-8"))
        if self.size > self.limit_bytes:
            self.done = True
            raise ValueError("Combined command output exceeds budget; do not discard and reread context")
        self.parts.append(output)
        if exit_code is None:
            return None
        self.done = True
        if exit_code != 0:
            raise ValueError(f"Context command failed with exit code {exit_code}")
        result = json.loads("".join(self.parts))
        if not isinstance(result, dict):
            raise ValueError("Context command must return an object")
        return result


def continuation(response: dict[str, Any]) -> str | None:
    """Terminal responses need no next_action; incomplete ones must supply it."""
    remaining = response.get("remaining_required")
    if type(remaining) is not int or remaining < 0:
        raise ValueError("Missing context completion count")
    if "done" in response and response["done"] is not (remaining == 0):
        raise ValueError("Conflicting context terminal state")
    if remaining == 0:
        return None
    action = response.get("next_action")
    if not isinstance(action, str) or not action.startswith("flow context "):
        raise ValueError("Incomplete context has no bound continuation")
    return action
