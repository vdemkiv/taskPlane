"""Deterministic, reference-only bootstrap seed for a fresh delivery root."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from taskplane.settings import OperationalSettings, SettingsError


ROOT_SEED_SCHEMA = "taskplane.root-seed/v1"
PREPARE_RECEIPT_SCHEMA = "taskplane.root-seed-prepare/v1"
MAX_SEED_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 4 * 1024
ROOT_SEED_FIELDS = frozenset({
    "schema", "version", "candidate_sha", "run_id", "wave_id",
    "settings_fingerprint", "delivery_mode", "approved_design",
    "sealed_plan", "pickups", "budgets", "outstanding_human_gates",
    "predecessor_terminal_projection", "prepared_at", "operation_id",
    "seed_fingerprint",
})
_CONTEXT_FIELDS = frozenset({
    "run_id", "wave_id", "candidate_sha", "settings", "delivery_mode",
    "design", "plan", "prepared_at", "operation_id",
})
_INPUT_FIELDS = frozenset({
    "pickups", "wave_budgets", "outstanding_human_gates",
    "predecessor_terminal_projection",
})
_REFERENCE_FIELDS = frozenset({"path", "fingerprint"})
_PICKUP_FIELDS = frozenset({
    "id", "write_scopes", "disjointness_receipt_fingerprint"})
_GATE_FIELDS = frozenset({"id", "owner"})
_BINDING_FIELDS = frozenset({
    "candidate_sha", "run_id", "wave_id", "settings_fingerprint",
    "seed_fingerprint",
})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class RootSeedError(ValueError):
    """The root seed is unsafe, ambiguous, or not contract-bound."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RootSeedError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _exact(value: object, fields: frozenset[str], label: str) -> dict[str, Any]:
    row = _mapping(value, label)
    if set(row) != set(fields):
        missing = sorted(set(fields) - set(row))
        extra = sorted(set(row) - set(fields))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        raise RootSeedError(f"{label} has " + "; ".join(detail))
    return row


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RootSeedError("root seed must contain portable JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise RootSeedError(f"{label} must be a bounded portable identifier")
    return value


def _fingerprint(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise RootSeedError(f"{label} must be a sha256 fingerprint")
    return value


def _relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or \
            "\x00" in value:
        raise RootSeedError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RootSeedError(f"{label} must be a portable relative path")
    if re.match(r"^[A-Za-z]:", value):
        raise RootSeedError(f"{label} must be a portable relative path")
    return path.as_posix()


def _reference(value: object, label: str) -> dict[str, str]:
    row = _exact(value, _REFERENCE_FIELDS, label)
    return {
        "path": _relative_path(row["path"], f"{label}.path"),
        "fingerprint": _fingerprint(
            row["fingerprint"], f"{label}.fingerprint"),
    }


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RootSeedError(f"{label} must be a positive integer")
    return value


def _pickups(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RootSeedError("seed inputs.pickups must be a non-empty list")
    normalized = []
    for index, item in enumerate(value):
        row = _exact(item, _PICKUP_FIELDS, f"seed pickup {index}")
        scopes = row["write_scopes"]
        if not isinstance(scopes, list) or not scopes:
            raise RootSeedError("seed pickup write_scopes must be non-empty")
        normalized_scopes = sorted({
            _relative_path(scope, "seed pickup write scope")
            for scope in scopes
        })
        if len(normalized_scopes) != len(scopes):
            raise RootSeedError("seed pickup write_scopes contain duplicates")
        normalized.append({
            "id": _safe_id(row["id"], "seed pickup id"),
            "write_scopes": normalized_scopes,
            "disjointness_receipt_fingerprint": _fingerprint(
                row["disjointness_receipt_fingerprint"],
                "seed pickup disjointness receipt fingerprint"),
        })
    if len({row["id"] for row in normalized}) != len(normalized):
        raise RootSeedError("seed pickup ids contain duplicates")
    return sorted(normalized, key=lambda row: row["id"])


def _gates(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise RootSeedError("outstanding_human_gates must be a list")
    normalized = []
    for index, item in enumerate(value):
        row = _exact(item, _GATE_FIELDS, f"outstanding gate {index}")
        normalized.append({
            "id": _safe_id(row["id"], "outstanding gate id"),
            "owner": _safe_id(row["owner"], "outstanding gate owner"),
        })
    pairs = {(row["id"], row["owner"]) for row in normalized}
    if len(pairs) != len(normalized):
        raise RootSeedError("outstanding human gates contain duplicates")
    return sorted(normalized, key=lambda row: (row["id"], row["owner"]))


def _predecessor(value: object) -> dict[str, str]:
    if value is None or value == {"status": "none"}:
        return {"status": "none"}
    reference = _reference(value, "predecessor_terminal_projection")
    return {"status": "present", **reference}


def build_root_seed(context: object, inputs: object) -> dict[str, Any]:
    """Build one exact deterministic seed without filesystem side effects."""
    ctx = _exact(context, _CONTEXT_FIELDS, "root seed context")
    supplied = _exact(inputs, _INPUT_FIELDS, "unknown seed input")
    settings = ctx["settings"]
    if not isinstance(settings, OperationalSettings):
        raise RootSeedError("root seed context.settings must be typed")
    try:
        root_policy = settings.workflow.root_session.consumer_projection(
            "root-seed.prepare")
    except SettingsError as exc:
        raise RootSeedError(str(exc)) from exc
    if root_policy["resume"] != "forbidden" or \
            root_policy["seed"] != "digest-only":
        raise RootSeedError("root seed policy is not Part A conforming")

    wave_budgets = _exact(
        supplied["wave_budgets"],
        frozenset({"max_actions", "target_tokens", "max_tokens"}),
        "wave_budgets")
    canonical_wave_budgets = {
        key: _positive_int(settings.limits.budgets[key], f"settings {key}")
        for key in ("max_actions", "target_tokens", "max_tokens")
    }
    if wave_budgets != canonical_wave_budgets:
        raise RootSeedError(
            "wave_budgets must equal the effective settings snapshot")

    candidate_sha = ctx["candidate_sha"]
    if not isinstance(candidate_sha, str) or _GIT_SHA.fullmatch(
            candidate_sha) is None:
        raise RootSeedError("candidate_sha must be a full git object id")
    prepared_at = ctx["prepared_at"]
    if not isinstance(prepared_at, str) or not prepared_at.endswith("Z") or \
            len(prepared_at) > 64:
        raise RootSeedError("prepared_at must be a bounded UTC timestamp")

    material = {
        "schema": ROOT_SEED_SCHEMA,
        "version": 1,
        "candidate_sha": candidate_sha,
        "run_id": _safe_id(ctx["run_id"], "run_id"),
        "wave_id": _safe_id(ctx["wave_id"], "wave_id"),
        "settings_fingerprint": _fingerprint(
            settings.digest, "effective settings fingerprint"),
        "delivery_mode": _safe_id(ctx["delivery_mode"], "delivery_mode"),
        "approved_design": _reference(ctx["design"], "approved design"),
        "sealed_plan": _reference(ctx["plan"], "sealed plan"),
        "pickups": _pickups(supplied["pickups"]),
        "budgets": {
            **canonical_wave_budgets,
            "seed_budget_tokens": root_policy["seed_budget_tokens"],
            "root_budget_tokens": root_policy["root_budget_tokens"],
        },
        "outstanding_human_gates": _gates(
            supplied["outstanding_human_gates"]),
        "predecessor_terminal_projection": _predecessor(
            supplied["predecessor_terminal_projection"]),
        "prepared_at": prepared_at,
        "operation_id": _safe_id(ctx["operation_id"], "operation_id"),
    }
    seed = {**material, "seed_fingerprint": _digest(material)}
    encoded = _canonical(seed)
    if len(encoded) > MAX_SEED_BYTES:
        raise RootSeedError("root seed exceeds the 65536-byte bound")
    return seed


def seed_binding(seed: object) -> dict[str, str]:
    row = _exact(seed, ROOT_SEED_FIELDS, "root seed")
    claimed = row["seed_fingerprint"]
    material = {key: value for key, value in row.items()
                if key != "seed_fingerprint"}
    if not isinstance(claimed, str) or claimed != _digest(material):
        raise RootSeedError("root seed fingerprint does not match its content")
    return {key: str(row[key]) for key in sorted(_BINDING_FIELDS)}


def verify_seed_binding(seed: object, binding: object, *, surface: str) -> None:
    expected = seed_binding(seed)
    actual = _exact(binding, _BINDING_FIELDS, f"{surface} binding")
    if actual != expected:
        raise RootSeedError(f"{surface} binding does not match root seed")


def prepare_root_seed(
    repository_root: str | Path,
    seed_ref: str,
    context: object,
    inputs: object,
) -> dict[str, Any]:
    """Persist one owned seed atomically and return a bounded receipt."""
    portable_ref = _relative_path(seed_ref, "seed_ref")
    root = Path(repository_root).resolve()
    target = (root / portable_ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RootSeedError("seed_ref must be a portable relative path") from exc
    seed = build_root_seed(context, inputs)
    payload = _canonical(seed) + b"\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            prior = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RootSeedError("existing root seed is unreadable") from exc
        if prior != seed:
            raise RootSeedError("root seed target already contains other data")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".root-seed-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    receipt = {
        "schema": PREPARE_RECEIPT_SCHEMA,
        "status": "prepared",
        "seed_ref": portable_ref,
        "seed_fingerprint": seed["seed_fingerprint"],
        "binding": seed_binding(seed),
        "prepared_at": seed["prepared_at"],
        "operation_id": seed["operation_id"],
    }
    if len(_canonical(receipt)) > MAX_RECEIPT_BYTES:
        raise RootSeedError("root seed prepare receipt exceeds 4096 bytes")
    return receipt


__all__ = [
    "MAX_RECEIPT_BYTES", "MAX_SEED_BYTES", "PREPARE_RECEIPT_SCHEMA",
    "ROOT_SEED_FIELDS", "ROOT_SEED_SCHEMA", "RootSeedError",
    "build_root_seed", "prepare_root_seed", "seed_binding",
    "verify_seed_binding",
]
