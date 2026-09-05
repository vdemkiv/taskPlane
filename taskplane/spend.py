"""What a governed pickup actually consumed, read from native counters.

The action ceiling was the only budget this product had, and an action is a
terrible proxy for cost. Measured on one real review: 777k effective tokens
across 69 shell commands — about 11k per action — but the spread inside that
average is two orders of magnitude. `tp status` is a few hundred tokens; one
lens agent reading a large file is twenty-five thousand. The meter counted
them identically, so "raise the limit from 40 to 80" was never a fine-tune;
it was buying another ~440k tokens sight unseen.

Tokens are the thing that is actually scarce, and the host already writes a
cumulative native counter.  The Codex adapter consumes that counter directly;
legacy provider projections remain compatibility inputs.  Nothing estimates
or reconstructs a native total.

The hard pickup ceiling uses native total tokens. Effective tokens remain a
separate cost projection for reports; cache/output weights never redefine the
provider-owned counter used by the gate.

This is a CEILING input, never billing truth. Governed pickups fail closed when
their native counter is missing, null, zero at dispatch, or unavailable at
terminal release; no missing value becomes a fictional zero.
"""
import json
import os
from typing import Any

# Cache reads ×0.1, cache writes ×2, output ×5, plain input ×1.
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 5.0}
USAGE_SCHEMA = "taskplane.token-usage/v2"
COMMAND_EFFICIENCY_SCHEMA = "taskplane.command-efficiency/v1"

_COMMAND_COUNTERS = (
    "launches", "elapsed_ms", "meaningful_wakes", "unchanged_model_polls",
    "polling_raw_tokens", "total_raw_tokens", "avoided_polling_raw_tokens",
    "baseline_polling_raw_tokens", "timeouts", "cancellations",
)


def dispatch_usage(normalized: dict) -> dict:
    """Adapt normalized provider usage to the closed dispatch owner input."""
    if not isinstance(normalized, dict) or normalized.get("schema") != \
            USAGE_SCHEMA or normalized.get("available") is not True:
        raise ValueError("observed provider usage is required for dispatch telemetry")
    cached = _nonnegative_integer(normalized.get("cached_input_tokens"))
    uncached = _nonnegative_integer(normalized.get("uncached_input_tokens"))
    output = _nonnegative_integer(normalized.get("output_tokens"))
    total = _nonnegative_integer(normalized.get("raw_total_tokens"))
    if None in {cached, uncached, output, total}:
        raise ValueError("provider usage counters are incomplete")
    reasoning_value = normalized.get("reasoning_tokens", 0)
    reasoning = _nonnegative_integer(reasoning_value)
    if reasoning is None:
        raise ValueError("provider reasoning token telemetry is malformed")
    input_tokens = cached + uncached
    if total < input_tokens + output:
        raise ValueError("provider usage totals do not reconcile")
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": uncached,
        "output_tokens": output,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
    }


def observed_dispatch_usage(usage: dict, *, provider: str) -> dict:
    """Normalize one live provider block and require dispatch-grade truth."""
    normalized = normalize_usage(usage, provider=provider)
    if normalized.get("available") is not True:
        raise ValueError(
            "observed provider usage is unavailable: "
            + str(normalized.get("reason") or "unknown measurement failure"))
    return dispatch_usage(normalized)


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None


def _nonnegative_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def command_efficiency(values: dict | None) -> dict:
    """Normalize bounded command counters and enforce the hard cost gates.

    Missing, malformed, and zero denominators are measurement failures.  They
    never become convenient zeroes: a cost gate with no instrument is
    ``unproven`` and therefore cannot qualify a frozen evaluation.
    """
    raw = values if isinstance(values, dict) else {}
    counters = {name: _nonnegative_integer(raw.get(name))
                for name in _COMMAND_COUNTERS}
    total = counters["total_raw_tokens"]
    baseline = counters["baseline_polling_raw_tokens"]
    polling = counters["polling_raw_tokens"]
    avoided = counters["avoided_polling_raw_tokens"]
    share = (polling / total
             if polling is not None and total is not None and total > 0
             else None)
    reduction = (avoided / baseline
                 if avoided is not None and baseline is not None
                 and baseline > 0 else None)

    missing = [name for name, value in counters.items() if value is None]
    unproven = []
    if reduction is None:
        unproven.append("polling token reduction is unproven")
    if share is None:
        unproven.append("polling token share is unproven")
    if (polling is not None and total is not None and total > 0
            and polling > total):
        share = None
        unproven.append("polling token total does not reconcile")
    if (avoided is not None and baseline is not None and polling is not None
            and baseline > 0 and avoided + polling != baseline):
        reduction = None
        unproven.append("polling baseline does not reconcile")
    if missing and not unproven:
        unproven.append("command efficiency counters are unproven: "
                        + ", ".join(missing))

    failures = []
    unchanged = counters["unchanged_model_polls"]
    if unchanged is not None and unchanged != 0:
        failures.append("unchanged model polls must equal zero")
    if reduction is not None and reduction < .90:
        failures.append("polling token reduction must be at least 90%")
    if share is not None and share >= .01:
        failures.append(
            "polling raw tokens must be less than 1% of total raw tokens")

    if missing or unproven:
        status = "unproven"
        gate_failures = unproven
    elif failures:
        status = "fail"
        gate_failures = failures
    else:
        status = "pass"
        gate_failures = []
    return {
        "schema": COMMAND_EFFICIENCY_SCHEMA,
        **counters,
        "polling_token_reduction": reduction,
        "polling_raw_token_share": share,
        "measurement_status": ("measured" if not missing and not unproven
                               else "unproven"),
        "gate": {"status": status, "failures": gate_failures},
    }


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
    reasoning = usage.get("reasoning_tokens", 0)
    if provider_name == "codex":
        output_details = usage.get("output_tokens_details")
        if isinstance(output_details, dict) and "reasoning_tokens" in output_details:
            reasoning = output_details.get("reasoning_tokens")
    reasoning = _nonnegative_integer(reasoning)
    if reasoning is None:
        return _unavailable(provider_name,
                            "reasoning token telemetry is corrupt")
    return {
        "schema": USAGE_SCHEMA, "provider": provider_name,
        "available": True, "reason": None,
        "uncached_input_tokens": int(uncached),
        "cached_input_tokens": int(cached),
        "cache_creation_tokens": int(created),
        "output_tokens": int(output), "raw_total_tokens": int(raw),
        "reasoning_tokens": reasoning,
        "effective_tokens": int(effective),
    }


def provider_cost_projection(usage: dict, *, provider: str,
                             rates_per_million: dict | None = None) -> dict:
    """Return provider-correct normalized tokens and optional observed cost.

    Rates are supplied by the caller because price tables change independently
    of the engine.  Missing usage or a missing rate is ``unavailable`` rather
    than a fabricated zero.  A real zero-token category does not require a
    rate and remains distinguishable from unavailable telemetry.
    """
    normalized = normalize_usage(usage, provider=provider)
    if normalized.get("available") is not True:
        return {
            "usage": normalized,
            "cost": {"available": False, "usd": None,
                     "reason": normalized.get("reason") or
                     "usage telemetry is unavailable"},
        }
    rates = rates_per_million if isinstance(rates_per_million, dict) else {}
    categories = {
        "uncached_input": normalized["uncached_input_tokens"],
        "cached_input": normalized["cached_input_tokens"],
        "cache_creation": normalized["cache_creation_tokens"],
        "output": normalized["output_tokens"],
    }
    missing = []
    total = 0.0
    for category, tokens in categories.items():
        rate = rates.get(category)
        valid_rate = (isinstance(rate, (int, float))
                      and not isinstance(rate, bool) and rate >= 0)
        if tokens and not valid_rate:
            missing.append(category)
        elif valid_rate:
            total += tokens * float(rate) / 1_000_000
    if missing:
        return {
            "usage": normalized,
            "cost": {"available": False, "usd": None,
                     "reason": "provider rates unavailable: "
                     + ", ".join(sorted(missing))},
        }
    return {
        "usage": normalized,
        "cost": {"available": True, "usd": round(total, 12),
                 "reason": None},
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
    keys = ("transcript_path", "agent_transcript_path", "transcript")
    if (event or {}).get("hook_event_name") == "SubagentStop":
        # The common path belongs to the parent at this boundary. Attribute
        # the completed child's counter to the child when both are present.
        keys = ("agent_transcript_path", "transcript_path", "transcript")
    for k in keys:
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
            f"TOKEN BUDGET exhausted ({spent:,}/{cap:,} native tokens) — "
            f"STOP. This per-pickup ceiling uses the host's cumulative native "
            f"counter rather than a reconstructed estimate. A "
            f"human raises it from OUTSIDE this workspace: `tp.py budget "
            f"--grant-tokens N --workspace <ws>`, or ends the task with "
            f"`tp.py clear --workspace <ws>`. You cannot grant yourself "
            f"budget; do not retry.")
    return True, f"{spent:,}/{cap:,} native tokens"


def cost_per_action(spent: int, actions: int) -> float:
    """What an action cost on this run. The number that makes the action
    ceiling legible: 11,261 on the measured review, not a constant."""
    return (float(spent) / actions) if actions else 0.0
