"""What a governed run actually COSTS, read from the host's transcript.

The action ceiling was the only budget this product had, and an action is a
terrible proxy for cost. Measured on one real review: 777k effective tokens
across 69 shell commands — about 11k per action — but the spread inside that
average is two orders of magnitude. `tp status` is a few hundred tokens; one
lens agent reading a large file is twenty-five thousand. The meter counted
them identically, so "raise the limit from 40 to 80" was never a fine-tune;
it was buying another ~440k tokens sight unseen.

Tokens are the thing that is actually scarce, and the host already writes
them down: every assistant message in the transcript JSONL carries a `usage`
block. This module reads it. Nothing here estimates, models, or predicts —
it sums what was recorded.

EFFECTIVE tokens, not raw. Cache reads are cheap and cache writes and output
are not, so a raw sum tells you almost nothing about cost: the same review
was ~22M raw and ~3.8M effective. The weights below are the ones the host's
own usage report uses, and they live in one place so a budget, a report and
a gate cannot disagree about what a token cost.

This is a CEILING input, never a source of truth about billing. It fails
OPEN in every direction — no transcript, an unreadable line, a missing usage
block — because a budget that blocks when its instrument breaks would make a
broken instrument into a broken product. The action ceiling still stands
underneath it.
"""
import json
import os
from typing import Any

# Cache reads ×0.1, cache writes ×2, output ×5, plain input ×1.
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 5.0}
USAGE_SCHEMA = "taskplane.token-usage/v2"


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None


def _first_number(mapping: dict, *keys: str) -> tuple[float | None, bool]:
    for key in keys:
        if key in mapping:
            return _nonnegative_number(mapping.get(key)), True
    return None, False


def _unavailable(provider: str, reason: str) -> dict:
    return {
        "schema": USAGE_SCHEMA, "provider": provider,
        "available": False, "reason": reason,
        "uncached_input_tokens": None, "cached_input_tokens": None,
        "cache_creation_tokens": None, "output_tokens": None,
        "raw_total_tokens": None, "effective_tokens": None,
    }


def normalize_usage(usage: dict, *, provider: str) -> dict:
    """Normalize one provider usage block without counting cache twice."""
    provider_name = str(provider or "").strip().lower()
    if provider_name.startswith("claude") or provider_name == "anthropic":
        provider_name = "claude"
    elif provider_name in {"openai", "codex"}:
        provider_name = "codex"
    else:
        return _unavailable(provider_name or "unknown",
                            "provider semantics are unavailable")
    if not isinstance(usage, dict):
        return _unavailable(provider_name, "usage block is not an object")

    output, output_present = _first_number(
        usage, "output_tokens", "completion_tokens")
    if not output_present or output is None:
        return _unavailable(provider_name,
                            "output token telemetry is missing or corrupt")

    if provider_name == "claude":
        uncached, input_present = _first_number(
            usage, "input_tokens", "uncached_input_tokens")
        cached, cached_present = _first_number(
            usage, "cache_read_input_tokens", "cached_input_tokens")
        created, creation_present = _first_number(
            usage, "cache_creation_input_tokens", "cache_creation_tokens")
        if not input_present or uncached is None:
            return _unavailable(provider_name,
                                "uncached input token telemetry is missing or corrupt")
        if not cached_present or cached is None or not creation_present or created is None:
            return _unavailable(provider_name,
                                "cache read/creation telemetry is missing or corrupt")
        raw = uncached + cached + created + output
    else:
        total_input, input_present = _first_number(
            usage, "input_tokens", "prompt_tokens")
        details = usage.get("input_tokens_details")
        if not isinstance(details, dict):
            details = usage.get("prompt_tokens_details")
        if not input_present or total_input is None:
            return _unavailable(provider_name,
                                "input token telemetry is missing or corrupt")
        if not isinstance(details, dict):
            return _unavailable(provider_name,
                                "cached input telemetry is unavailable")
        cached, cached_present = _first_number(
            details, "cached_tokens", "cache_read_input_tokens")
        if not cached_present or cached is None:
            return _unavailable(provider_name,
                                "cached input telemetry is missing or corrupt")
        if cached > total_input:
            return _unavailable(provider_name,
                                "cached input exceeds provider input total")
        created, creation_present = _first_number(
            usage, "cache_creation_input_tokens", "cache_creation_tokens")
        if creation_present and created is None:
            return _unavailable(provider_name,
                                "cache creation telemetry is corrupt")
        created = created or 0.0
        uncached = total_input - cached
        raw = total_input + output

    provider_total, total_present = _first_number(
        usage, "total_tokens", "raw_total_tokens")
    if total_present and (provider_total is None or provider_total != raw):
        return _unavailable(provider_name,
                            "provider total does not reconcile with token categories")
    effective = (uncached * WEIGHTS["input"]
                 + cached * WEIGHTS["cache_read"]
                 + created * WEIGHTS["cache_write"]
                 + output * WEIGHTS["output"])
    return {
        "schema": USAGE_SCHEMA, "provider": provider_name,
        "available": True, "reason": None,
        "uncached_input_tokens": int(uncached),
        "cached_input_tokens": int(cached),
        "cache_creation_tokens": int(created),
        "output_tokens": int(output), "raw_total_tokens": int(raw),
        "effective_tokens": int(effective),
    }


def _row_usage(row: dict) -> tuple[dict | None, str | None]:
    """Return one usage block and a stable host identity from one JSONL row."""
    containers = []
    for key in ("message", "response", "payload", "event"):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    containers.append(row)
    for container in containers:
        usage = container.get("usage")
        if isinstance(usage, dict):
            identity = next((str(container.get(key)) for key in
                             ("id", "message_id", "response_id", "request_id")
                             if container.get(key) not in (None, "")), None)
            if identity is None:
                identity = next((str(row.get(key)) for key in
                                 ("id", "message_id", "response_id", "request_id")
                                 if row.get(key) not in (None, "")), None)
            return usage, identity
    return None, None


def read_provider_transcript(path: str, *, provider: str) -> dict:
    """Reconcile provider usage rows, deduplicating only stable identities."""
    if not path or not os.path.isfile(path):
        return {**_unavailable(provider, "no transcript at that path"),
                "messages": 0, "duplicates_removed": 0}
    totals = {"uncached_input_tokens": 0, "cached_input_tokens": 0,
              "cache_creation_tokens": 0, "output_tokens": 0,
              "raw_total_tokens": 0, "effective_tokens": 0}
    messages = duplicates = 0
    seen: set[str] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                usage, identity = _row_usage(row)
                if usage is None:
                    continue
                if identity is not None and identity in seen:
                    duplicates += 1
                    continue
                normalized = normalize_usage(usage, provider=provider)
                if not normalized["available"]:
                    return {**normalized, "messages": messages,
                            "duplicates_removed": duplicates}
                if identity is not None:
                    seen.add(identity)
                for key in totals:
                    totals[key] += int(normalized[key])
                messages += 1
    except OSError as exc:
        return {**_unavailable(provider, exc.__class__.__name__),
                "messages": messages, "duplicates_removed": duplicates}
    if messages == 0:
        return {**_unavailable(provider, "no valid usage rows"),
                "messages": 0, "duplicates_removed": duplicates}
    return {"schema": USAGE_SCHEMA,
            "provider": "claude" if str(provider).startswith("claude")
            else "codex", "available": True, "reason": None,
            **totals, "effective": totals["effective_tokens"],
            "messages": messages, "duplicates_removed": duplicates}


def weigh(usage: dict) -> float:
    """One usage block -> effective tokens."""
    if not isinstance(usage, dict):
        return 0.0
    def n(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0
    return (n("input_tokens") * WEIGHTS["input"]
            + n("cache_read_input_tokens") * WEIGHTS["cache_read"]
            + n("cache_creation_input_tokens") * WEIGHTS["cache_write"]
            + n("output_tokens") * WEIGHTS["output"])


def read_transcript(path: str) -> dict:
    """Sum a transcript's recorded usage. Never raises."""
    total, messages = 0.0, 0
    if not path or not os.path.isfile(path):
        return {"available": False, "reason": "no transcript at that path",
                "effective": 0, "messages": 0}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue          # a torn last line is normal, not fatal
                msg = row.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if usage is None:
                    usage = row.get("usage")
                if isinstance(usage, dict):
                    w = weigh(usage)
                    if w:
                        total += w
                        messages += 1
    except OSError as e:
        return {"available": False, "reason": e.__class__.__name__,
                "effective": 0, "messages": 0}
    return {"available": True, "effective": int(total), "messages": messages}


def event_transcript(event: dict) -> "str | None":
    """The transcript path a hook event names, under any of the keys hosts
    have used for it."""
    for k in ("transcript_path", "agent_transcript_path", "transcript"):
        v = (event or {}).get(k)
        if isinstance(v, str) and v:
            return v
    return None


def status(contract: dict, spent: int) -> tuple:
    """(ok, reason). A token ceiling is advisory-by-absence: a contract that
    sets none is governed by the action ceiling alone, exactly as before."""
    cap = (contract.get("budget") or {}).get("max_tokens")
    if cap is None:
        return True, "no token ceiling set"
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        return True, "unreadable token ceiling — ignored"
    if cap <= 0:
        return True, "no token ceiling set"
    if spent >= cap:
        return False, (
            f"TOKEN BUDGET exhausted ({spent:,}/{cap:,} effective tokens) — "
            f"STOP. This ceiling counts what the host recorded, weighted the "
            f"way cost actually falls (cache reads x0.1, cache writes x2, "
            f"output x5), so it tracks spend rather than tool-call count. A "
            f"human raises it from OUTSIDE this workspace: `tp.py budget "
            f"--grant-tokens N --workspace <ws>`, or ends the task with "
            f"`tp.py clear --workspace <ws>`. You cannot grant yourself "
            f"budget; do not retry.")
    return True, f"{spent:,}/{cap:,} effective tokens"


def cost_per_action(spent: int, actions: int) -> float:
    """What an action cost on this run. The number that makes the action
    ceiling legible: 11,261 on the measured review, not a constant."""
    return (float(spent) / actions) if actions else 0.0
