"""Durable, host-neutral command-wave aggregation."""
from __future__ import annotations


COMMAND_WAVE_SCHEMA = "taskplane.command-wave/v1"
_TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
_ATTENTION = frozenset({
    "approval_required", "input_required", "failed", "timed_out", "cancelled",
})
_RESUME = {
    "authorization_granted": "approval_required",
    "input_provided": "input_required",
}


def create(wave_id: str, members: list[str], *,
           handles: dict | None = None) -> dict:
    """Create the JSON-safe command-wave authority with sealed membership."""
    ordered = [str(member) for member in members]
    if not ordered or len(set(ordered)) != len(ordered):
        raise ValueError("command wave membership must be non-empty and unique")
    bindings = {str(key): str(value) for key, value in (handles or {}).items()}
    if set(bindings) - set(ordered):
        raise ValueError("command handle is not a wave member")
    return {
        "schema": COMMAND_WAVE_SCHEMA,
        "wave_id": str(wave_id),
        "sealed_members": ordered,
        "members": {member: "running" for member in ordered},
        "handles": bindings,
        "launches": len(bindings),
        "interrupted": False,
        "delivered_attention": [],
        "ordinary_completion_deliveries": 0,
    }


def resume(wave: dict, members: list[str]) -> dict:
    """Validate and return durable wave state without changing bindings."""
    if not isinstance(wave, dict) or wave.get("schema") != COMMAND_WAVE_SCHEMA:
        raise ValueError("unsupported command wave")
    if list(map(str, members)) != wave.get("sealed_members"):
        raise ValueError("command wave membership changed after sealing")
    if set((wave.get("handles") or {})) - set(wave["sealed_members"]):
        raise ValueError("command wave contains an unbound member")
    return wave


def update(wave: dict, member: str, state: str) -> list[dict]:
    """Record one observation and return only meaningful model events."""
    resume(wave, wave.get("sealed_members") or [])
    member = str(member)
    if member not in wave["members"]:
        raise KeyError(member)
    state = str(state)
    events = []
    if state in _RESUME:
        if wave["members"][member] == _RESUME[state]:
            wave["members"][member] = "running"
        return events
    attention_key = f"{member}:{state}"
    if state in _ATTENTION and attention_key not in wave["delivered_attention"]:
        wave["delivered_attention"].append(attention_key)
        events.append({"schema": "taskplane.command-wave-event/v1",
                       "wave_id": wave["wave_id"], "member": member,
                       "state": state, "attention": True})
    if state in _TERMINAL:
        wave["members"][member] = state
    elif wave["members"][member] not in _TERMINAL:
        wave["members"][member] = state
    if (wave["ordinary_completion_deliveries"] == 0 and
            all(value in _TERMINAL for value in wave["members"].values())):
        wave["ordinary_completion_deliveries"] = 1
        events.append({"schema": "taskplane.command-wave-event/v1",
                       "wave_id": wave["wave_id"],
                       "state": "wave_completed", "attention": False,
                       "members": dict(wave["members"])})
    return events
