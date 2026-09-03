"""Deterministic, reference-only bootstrap seed for a fresh delivery root."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

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
_BUDGET_FIELDS = frozenset({
    "max_actions", "target_tokens", "max_tokens",
    "seed_budget_tokens", "root_budget_tokens",
})
_BINDING_FIELDS = frozenset({
    "candidate_sha", "run_id", "wave_id", "settings_fingerprint",
    "seed_fingerprint",
})
_PREPARE_RECEIPT_FIELDS = frozenset({
    "schema", "status", "seed_ref", "seed_fingerprint", "binding",
    "prepared_at", "operation_id",
})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_WINDOWS_FORBIDDEN = frozenset('<>:"\\|?*')
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})


class RootSeedError(ValueError):
    """The root seed is unsafe, ambiguous, or not contract-bound."""


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RootSeedError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise RootSeedError(f"{label} keys must be strings")
    return dict(value)


def _exact(
    value: object, fields: frozenset[str], label: str,
) -> dict[str, object]:
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
    if not isinstance(value, str) or not value or len(
            value.encode("utf-8")) > 1024 or "\\" in value:
        raise RootSeedError(f"{label} must be a portable relative path")
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."}
                                    for part in parts):
        raise RootSeedError(f"{label} must be a portable relative path")
    for part in parts:
        invalid_character = any(
            ord(character) < 32 or character in _WINDOWS_FORBIDDEN
            for character in part)
        reserved = part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        if invalid_character or reserved or part.endswith((" ", ".")) or \
                len(part.encode("utf-8")) > 255:
            raise RootSeedError(
                f"{label} must be a portable relative path")
    return value


def _scope_pattern(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(
            value.encode("utf-8")) > 1024 or "\\" in value:
        raise RootSeedError(f"{label} must be a portable relative glob")
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."}
                                    for part in parts):
        raise RootSeedError(f"{label} must be a portable relative glob")
    for part in parts:
        invalid_character = any(
            ord(character) < 32 or character in (_WINDOWS_FORBIDDEN - {"*", "?"})
            for character in part)
        reserved = part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        if invalid_character or reserved or part.endswith((" ", ".")) or \
                len(part.encode("utf-8")) > 255 or \
                ("**" in part and part != "**"):
            raise RootSeedError(f"{label} must be a portable relative glob")
        index = 0
        while index < len(part):
            if part[index] == "]":
                raise RootSeedError(
                    f"{label} must be a portable relative glob")
            if part[index] != "[":
                index += 1
                continue
            end = part.find("]", index + 1)
            content = part[index + 1:end] if end >= 0 else ""
            if end < 0 or not content or content in {"!", "^"} or \
                    "[" in content:
                raise RootSeedError(
                    f"{label} must be a portable relative glob")
            index = end + 1
    return value


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


def _utc_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or \
            _UTC_TIMESTAMP.fullmatch(value) is None:
        raise RootSeedError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RootSeedError(
            f"{label} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise RootSeedError(f"{label} must be a canonical UTC timestamp")
    return value


def _pickups(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise RootSeedError("seed inputs.pickups must be a non-empty list")
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(value):
        row = _exact(item, _PICKUP_FIELDS, f"seed pickup {index}")
        scopes = row["write_scopes"]
        if not isinstance(scopes, list) or not scopes:
            raise RootSeedError("seed pickup write_scopes must be non-empty")
        normalized_scopes = sorted({
            _scope_pattern(scope, "seed pickup write scope")
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
    return sorted(normalized, key=lambda row: str(row["id"]))


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


def _validated_predecessor(value: object) -> dict[str, str]:
    row = _mapping(value, "predecessor_terminal_projection")
    if row == {"status": "none"}:
        return {"status": "none"}
    present = _exact(
        row, frozenset({"status", "path", "fingerprint"}),
        "predecessor_terminal_projection")
    if present["status"] != "present":
        raise RootSeedError(
            "predecessor_terminal_projection.status must be present or none")
    reference = _reference({
        "path": present["path"],
        "fingerprint": present["fingerprint"],
    }, "predecessor_terminal_projection")
    return {"status": "present", **reference}


def _validated_budgets(
    value: object, settings: OperationalSettings | None = None,
) -> dict[str, int]:
    row = _exact(value, _BUDGET_FIELDS, "root seed budgets")
    budgets = {
        key: _positive_int(row[key], f"root seed budgets.{key}")
        for key in _BUDGET_FIELDS
    }
    if budgets["target_tokens"] >= budgets["max_tokens"]:
        raise RootSeedError(
            "root seed budgets.target_tokens must be below max_tokens")
    if budgets["seed_budget_tokens"] >= budgets["root_budget_tokens"]:
        raise RootSeedError(
            "root seed budgets.seed_budget_tokens must be below "
            "root_budget_tokens")
    if settings is not None:
        policy = settings.workflow.root_session.consumer_projection(
            "root-seed.prepare")
        expected = {
            key: _positive_int(settings.limits.budgets[key], f"settings {key}")
            for key in ("max_actions", "target_tokens", "max_tokens")
        }
        expected.update({
            "seed_budget_tokens": policy["seed_budget_tokens"],
            "root_budget_tokens": policy["root_budget_tokens"],
        })
        if budgets != expected:
            raise RootSeedError(
                "root seed budgets do not match the effective settings")
    return budgets


def validate_root_seed(
    seed: object, *, settings: OperationalSettings | None = None,
) -> dict[str, object]:
    """Validate the sole closed seed contract at every producer/consumer."""
    row = _exact(seed, ROOT_SEED_FIELDS, "root seed")
    if row["schema"] != ROOT_SEED_SCHEMA:
        raise RootSeedError("root seed schema is unsupported")
    if isinstance(row["version"], bool) or row["version"] != 1:
        raise RootSeedError("root seed version must be 1")
    candidate_sha = row["candidate_sha"]
    if not isinstance(candidate_sha, str) or _GIT_SHA.fullmatch(
            candidate_sha) is None:
        raise RootSeedError("candidate_sha must be a full git object id")
    normalized = {
        "schema": ROOT_SEED_SCHEMA,
        "version": 1,
        "candidate_sha": candidate_sha,
        "run_id": _safe_id(row["run_id"], "run_id"),
        "wave_id": _safe_id(row["wave_id"], "wave_id"),
        "settings_fingerprint": _fingerprint(
            row["settings_fingerprint"], "effective settings fingerprint"),
        "delivery_mode": _safe_id(row["delivery_mode"], "delivery_mode"),
        "approved_design": _reference(
            row["approved_design"], "approved design"),
        "sealed_plan": _reference(row["sealed_plan"], "sealed plan"),
        "pickups": _pickups(row["pickups"]),
        "budgets": _validated_budgets(row["budgets"], settings),
        "outstanding_human_gates": _gates(
            row["outstanding_human_gates"]),
        "predecessor_terminal_projection": _validated_predecessor(
            row["predecessor_terminal_projection"]),
        "prepared_at": _utc_timestamp(row["prepared_at"], "prepared_at"),
        "operation_id": _safe_id(row["operation_id"], "operation_id"),
    }
    if settings is not None and normalized["settings_fingerprint"] != \
            settings.digest:
        raise RootSeedError(
            "root seed settings fingerprint does not match effective settings")
    material = {key: value for key, value in row.items()
                if key != "seed_fingerprint"}
    if material != normalized:
        raise RootSeedError("root seed content is not canonical")
    claimed = _fingerprint(row["seed_fingerprint"], "seed_fingerprint")
    if claimed != _digest(normalized):
        raise RootSeedError("root seed fingerprint does not match its content")
    validated = {**normalized, "seed_fingerprint": claimed}
    if len(_canonical(validated)) > MAX_SEED_BYTES:
        raise RootSeedError("root seed exceeds the 65536-byte bound")
    return validated


def build_root_seed(context: object, inputs: object) -> dict[str, object]:
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
    prepared_at = _utc_timestamp(ctx["prepared_at"], "prepared_at")

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
    return validate_root_seed(seed, settings=settings)


def seed_binding(seed: object) -> dict[str, str]:
    row = validate_root_seed(seed)
    return {key: str(row[key]) for key in sorted(_BINDING_FIELDS)}


def verify_seed_binding(seed: object, binding: object, *, surface: str) -> None:
    expected = seed_binding(seed)
    actual = _exact(binding, _BINDING_FIELDS, f"{surface} binding")
    if actual != expected:
        raise RootSeedError(f"{surface} binding does not match root seed")


def verify_prepare_receipt(
    seed: object,
    receipt: object,
    *,
    settings: OperationalSettings | None = None,
    expected_seed_ref: str | None = None,
) -> None:
    """Verify one authenticated producer receipt for later P13 consumers."""
    validated_seed = validate_root_seed(seed, settings=settings)
    row = _exact(
        receipt, _PREPARE_RECEIPT_FIELDS, "root seed prepare receipt")
    if row["schema"] != PREPARE_RECEIPT_SCHEMA or row["status"] != "prepared":
        raise RootSeedError("root seed prepare receipt is not prepared")
    receipt_seed_ref = _relative_path(
        row["seed_ref"], "root seed prepare receipt.seed_ref")
    if expected_seed_ref is not None and receipt_seed_ref != _relative_path(
            expected_seed_ref, "expected seed_ref"):
        raise RootSeedError("root seed prepare receipt seed_ref is stale")
    if row["seed_fingerprint"] != validated_seed["seed_fingerprint"] or \
            row["prepared_at"] != validated_seed["prepared_at"] or \
            row["operation_id"] != validated_seed["operation_id"]:
        raise RootSeedError("root seed prepare receipt is stale")
    verify_seed_binding(
        validated_seed, row["binding"], surface="prepare receipt")
    if len(_canonical(row)) > MAX_RECEIPT_BYTES:
        raise RootSeedError("root seed prepare receipt exceeds 4096 bytes")


def _seed_target(
    repository_root: str | Path, seed_ref: str, *, create: bool,
) -> tuple[str, Path]:
    """Resolve lexically and reject links in every repository-relative parent."""
    portable_ref = _relative_path(seed_ref, "seed_ref")
    root = Path(repository_root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    parent = root
    for part in portable_ref.split("/")[:-1]:
        parent /= part
        try:
            status = parent.lstat()
        except FileNotFoundError:
            if not create:
                raise RootSeedError("persisted root seed is unreadable") from None
            parent.mkdir()
            status = parent.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise RootSeedError(
                "seed_ref contains a symlink or non-directory component")
    return portable_ref, parent / portable_ref.rsplit("/", 1)[-1]


def _read_persisted_seed(
    target: Path, *, unreadable_label: str,
) -> tuple[bytes, dict[str, object]]:
    """Open one regular identity without following its final component."""
    try:
        before = target.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RootSeedError(
                f"{unreadable_label} must be a regular file, not a symlink")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        opened = os.fstat(descriptor)
        after = target.lstat()
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(
                before, opened) or not os.path.samestat(opened, after):
            raise RootSeedError(f"{unreadable_label} identity changed")
        body = os.read(descriptor, MAX_SEED_BYTES + 1)
    except RootSeedError:
        raise
    except OSError as exc:
        raise RootSeedError(f"{unreadable_label} is unreadable") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if len(body) > MAX_SEED_BYTES:
        raise RootSeedError("root seed exceeds the 65536-byte bound")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RootSeedError(f"{unreadable_label} is unreadable") from exc
    return body, validate_root_seed(parsed)


def load_root_seed(
    repository_root: str | Path, seed_ref: str,
) -> dict[str, object]:
    """Read one bounded persisted seed through the same strict validator."""
    _, target = _seed_target(
        repository_root, seed_ref, create=False)
    _, seed = _read_persisted_seed(
        target, unreadable_label="persisted root seed")
    return seed


def prepare_root_seed(
    repository_root: str | Path,
    seed_ref: str,
    context: object,
    inputs: object,
) -> dict[str, object]:
    """Persist one owned seed atomically and return a bounded receipt."""
    portable_ref, target = _seed_target(
        repository_root, seed_ref, create=True)
    seed = build_root_seed(context, inputs)
    payload = _canonical(seed)
    try:
        target.lstat()
    except FileNotFoundError:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".root-seed-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_name, target)
            except FileExistsError:
                pass
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        prior_body, prior = _read_persisted_seed(
            target, unreadable_label="existing root seed")
    else:
        prior_body, prior = _read_persisted_seed(
            target, unreadable_label="existing root seed")
    if prior_body != payload or prior != seed:
        raise RootSeedError("root seed target already contains other data")
    binding = seed_binding(seed)
    receipt: dict[str, object] = {
        "schema": PREPARE_RECEIPT_SCHEMA,
        "status": "prepared",
        "seed_ref": portable_ref,
        "seed_fingerprint": seed["seed_fingerprint"],
        "binding": binding,
        "prepared_at": seed["prepared_at"],
        "operation_id": seed["operation_id"],
    }
    verify_prepare_receipt(
        seed, receipt, expected_seed_ref=portable_ref)
    return receipt


__all__ = [
    "MAX_RECEIPT_BYTES", "MAX_SEED_BYTES", "PREPARE_RECEIPT_SCHEMA",
    "ROOT_SEED_FIELDS", "ROOT_SEED_SCHEMA", "RootSeedError",
    "build_root_seed", "load_root_seed", "prepare_root_seed", "seed_binding",
    "validate_root_seed", "verify_prepare_receipt", "verify_seed_binding",
]
