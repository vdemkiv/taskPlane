"""Normalize native provider usage for advisory telemetry."""
from __future__ import annotations
from typing import Any

WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 5.0}

USAGE_SCHEMA = "taskplane.token-usage/v2"

def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number >= 0 else None

def _nonnegative_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value

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
