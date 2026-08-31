"""Durable, replay-safe command state and meaningful-event delivery.

This module deliberately does not own a subprocess implementation.  It owns
the host-neutral state that adapters bind to: opaque handles, transitions,
output artifacts, consumer delivery leases, and reconnect accounting.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from contextlib import contextmanager
from typing import Callable, Mapping

try:
    import recovery
except ImportError:  # package import path
    from taskplane import recovery

try:
    import dispatch_telemetry
except ImportError:  # package import path
    from taskplane import dispatch_telemetry

try:
    import fcntl as _file_lock
except ImportError:  # pragma: no cover - exercised by windows-latest
    _file_lock = None
    import msvcrt as _windows_lock


SCHEMA = "taskplane.command-state/v1"
MAX_EVENT_OUTPUT = 16 * 1024
MAX_DURABLE_OUTPUT = 64 * 1024
MAX_JOURNAL_BYTES = 128 * 1024
MAX_JOURNAL_ROWS = 32
COMMAND_RETENTION_SECONDS = 24 * 60 * 60
COMMAND_RETENTION_MAX_HANDLES = 128
COMMAND_RETENTION_MAX_BYTES = 8 * 1024 * 1024
COMMAND_RETENTION_SCHEMA = "taskplane.command-retention/v1"
MAX_SNAPSHOT_BYTES = 512 * 1024
DEFAULT_DELIVERY_LEASE_SECONDS = 30.0
TERMINAL_STATES = frozenset({
    "succeeded", "failed", "timed_out", "cancelled",
})
ATTENTION_STATES = frozenset({
    "approval_required", "input_required", "milestone",
})
MEANINGFUL_STATES = TERMINAL_STATES | ATTENTION_STATES
VALID_STATES = MEANINGFUL_STATES | {"created", "running"}
NATIVE_ADAPTER_SCHEMA = "taskplane.codex-native-adapter/v1"
NATIVE_WAIT_OBSERVATION_SCHEMA = "taskplane.native-wait-observation/v1"
_COMMAND_REASON_CODES = frozenset({
    "authority_change", "binding_lost", "detached_worker_ownership_lost",
    "measurable_convergence", "no_progress", "non_routine_failure",
    "oscillation", "repeated_fingerprint", "replan_required",
    "retry_budget_exhausted", "routine_retry", "unsafe_recovery", "worsening",
})

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,255}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,255}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,255}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
               r"[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(
        r"(?i)\b(authorization|token|password|secret|api[_-]?key)"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
)

_PERSONAL_DATA_PATTERNS = (
    # Durable logs are operational evidence, not a contact directory.  Keep
    # the surrounding diagnostic useful while removing common direct
    # identifiers before either the snapshot journal or output artifact sees
    # them.
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
     "[REDACTED_EMAIL]"),
    (re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^\s'\"`]+"),
     "[REDACTED_PATH]"),
    (re.compile(r"(?<![A-Za-z0-9])/(?:private/var/folders|private/tmp|tmp)/"
                r"[^\s'\"`]+"), "[REDACTED_PATH]"),
    (re.compile(r"(?i)\b[A-Z]:\\(?:Users|Documents and Settings)\\"
                r"[^\r\n\t'\"`]+"), "[REDACTED_PATH]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
)


def _lock_file(handle) -> None:
    if _file_lock is not None:
        _file_lock.flock(handle.fileno(), _file_lock.LOCK_EX)
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    _windows_lock.locking(handle.fileno(), _windows_lock.LK_LOCK, 1)


def _unlock_file(handle) -> None:
    if _file_lock is not None:
        _file_lock.flock(handle.fileno(), _file_lock.LOCK_UN)
        return
    handle.seek(0)
    _windows_lock.locking(handle.fileno(), _windows_lock.LK_UNLCK, 1)


class CommandRuntimeError(RuntimeError):
    """Base command runtime failure."""


class UnknownHandle(CommandRuntimeError):
    pass


class BindingMismatch(CommandRuntimeError):
    pass


class RevisionConflict(CommandRuntimeError):
    pass


class InvalidTransition(CommandRuntimeError):
    pass


class InterruptedWait(CommandRuntimeError):
    """The caller stopped waiting; the command continues unchanged."""


class NativeObservationUnavailable(CommandRuntimeError):
    """A one-shot native wait returned neither an event nor its deadline."""


def dispatch_event(snapshot: Mapping) -> dict:
    """Adapt a durable command transition to the shared event protocol."""
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != SCHEMA:
        raise ValueError("command snapshot is invalid")
    identity = snapshot.get("identity") or {}
    task_id = str(identity.get("task_id") or snapshot.get("handle") or "")
    state = str(snapshot.get("state") or "")
    if state in {"created", "running", "milestone"}:
        kind = "progress"
    elif state == "succeeded":
        kind = "complete"
    elif state in ATTENTION_STATES:
        kind = "attention"
    elif state == "cancelled":
        kind = "cancelled"
    else:
        kind = "failed"
    return dispatch_telemetry.dispatch_event(
        dispatch_id=str(snapshot.get("handle") or ""),
        thread_id=str(snapshot.get("handle") or ""),
        thread_type="worker", task_id=task_id,
        sequence=int(snapshot.get("revision") or 0), kind=kind,
        at=float(snapshot.get("updated_at") or snapshot.get("created_at") or 0),
        payload={"wave_id": snapshot.get("wave_id"), "state": state},
    )


def _finite_native(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRuntimeError(f"{label} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CommandRuntimeError(f"{label} must be finite")
    return normalized


def native_outstanding_fingerprint(members: list[str]) -> str:
    """Fingerprint one ordered, unique native outstanding set."""
    if not members or any(not isinstance(member, str) or not member.strip()
                          for member in members) or \
            len(set(members)) != len(members):
        raise CommandRuntimeError("native outstanding members are invalid")
    return _canonical_digest({"members": list(members)})


def native_wait_request(*, run_id: str, outstanding_set: str,
                        members: list[str], intent_fingerprint: str,
                        source_sha: str, deadline_at: float,
                        idempotency_key: str) -> dict:
    """Create the closed host request for one event-driven native wait."""
    member_fingerprint = native_outstanding_fingerprint(members)
    material = {
        "schema": NATIVE_ADAPTER_SCHEMA,
        "operation": "wait_for_events",
        "run_id": str(run_id),
        "task_name": str(outstanding_set),
        "intent_fingerprint": str(intent_fingerprint),
        "source_sha": str(source_sha),
        "deadline_at": _finite_native(deadline_at, "native wait deadline"),
        "idempotency_key": str(idempotency_key),
        "outstanding_set_fingerprint": member_fingerprint,
    }
    request = {
        **material,
        "request_fingerprint": _canonical_digest(material),
    }
    _validate_native_wait_request(request, members=members)
    return request


def _validate_native_wait_request(request: Mapping[str, object], *,
                                  members: list[str]) -> dict:
    required = {
        "schema", "operation", "run_id", "task_name",
        "intent_fingerprint", "source_sha", "deadline_at",
        "idempotency_key", "outstanding_set_fingerprint",
        "request_fingerprint",
    }
    if not isinstance(request, Mapping) or set(request) != required or \
            request.get("schema") != NATIVE_ADAPTER_SCHEMA or \
            request.get("operation") != "wait_for_events":
        raise CommandRuntimeError("native wait request is invalid")
    for key in required - {"schema", "operation", "deadline_at"}:
        if not str(request.get(key) or "").strip():
            raise CommandRuntimeError(
                f"native wait request {key} is missing")
    _finite_native(request.get("deadline_at"), "native wait deadline")
    expected_members = native_outstanding_fingerprint(members)
    if request.get("outstanding_set_fingerprint") != expected_members:
        raise CommandRuntimeError(
            "native wait request outstanding set is mixed")
    material = {key: value for key, value in request.items()
                if key != "request_fingerprint"}
    if request.get("request_fingerprint") != _canonical_digest(material):
        raise CommandRuntimeError(
            "native wait request fingerprint is invalid")
    return dict(request)


def _validate_native_wait_result(
        result: Mapping[str, object], *, request: Mapping[str, object]) -> dict:
    required = {
        "schema", "operation", "status", "observed_at",
        "native_agent_id", "run_id", "task_name",
        "outstanding_set_fingerprint", "intent_fingerprint", "source_sha",
        "idempotency_key", "request_fingerprint", "result_fingerprint",
    }
    allowed = required | {"attention_kind"}
    if not isinstance(result, Mapping) or not required.issubset(result) or \
            set(result) - allowed or \
            result.get("schema") != NATIVE_ADAPTER_SCHEMA or \
            result.get("operation") != request.get("operation"):
        raise CommandRuntimeError("native wait result is invalid")
    status = str(result.get("status") or "")
    if status not in {"completion", "attention"}:
        raise CommandRuntimeError("native wait result status is invalid")
    if status == "attention" and not str(
            result.get("attention_kind") or "").strip():
        raise CommandRuntimeError("native attention kind is missing")
    if status == "completion" and "attention_kind" in result:
        raise CommandRuntimeError("native completion contains attention data")
    if not str(result.get("native_agent_id") or "").strip():
        raise CommandRuntimeError("native wait result agent is missing")
    material = {key: value for key, value in result.items()
                if key != "result_fingerprint"}
    if result.get("result_fingerprint") != _canonical_digest(material):
        raise CommandRuntimeError("native wait result fingerprint is invalid")
    binding_fields = {
        "run_id", "task_name", "outstanding_set_fingerprint",
        "intent_fingerprint", "source_sha", "idempotency_key",
        "request_fingerprint",
    }
    foreign = sorted(key for key in binding_fields
                     if result.get(key) != request.get(key))
    if foreign:
        raise CommandRuntimeError(
            "native wait result is foreign to its request: " +
            ", ".join(foreign))
    observed_at = _finite_native(
        result.get("observed_at"), "native wait observed_at")
    if observed_at > float(request["deadline_at"]):
        raise CommandRuntimeError(
            "native wait result was observed after its deadline")
    return dict(result)


def consume_native_wait(
        request: Mapping[str, object], result: Mapping[str, object] | None, *,
        members: list[str], now: float, elapsed_seconds: float,
        usage_identity: Mapping[str, object]) -> dict:
    """Consume one completion/attention wake or one bounded deadline.

    This is a pure observation boundary: it creates no queue, timer, retry or
    replacement work.  Replaying identical input returns byte-identical
    evidence, while a silent pre-deadline return fails instead of polling.
    """
    checked_request = _validate_native_wait_request(request, members=members)
    observed_now = _finite_native(now, "native wait clock")
    elapsed = _finite_native(elapsed_seconds, "native wait elapsed")
    if elapsed < 0 or not isinstance(usage_identity, Mapping) or \
            not usage_identity:
        raise CommandRuntimeError("native wait attribution is invalid")
    usage_fingerprint = _canonical_digest(dict(usage_identity))
    common = {
        "schema": NATIVE_WAIT_OBSERVATION_SCHEMA,
        "run_id": checked_request["run_id"],
        "outstanding_set": checked_request["task_name"],
        "outstanding_members": list(members),
        "outstanding_set_fingerprint": checked_request[
            "outstanding_set_fingerprint"],
        "intent_fingerprint": checked_request["intent_fingerprint"],
        "source_sha": checked_request["source_sha"],
        "deadline_at": checked_request["deadline_at"],
        "idempotency_key": checked_request["idempotency_key"],
        "elapsed_seconds": elapsed,
        "usage_fingerprint": usage_fingerprint,
        "scheduled_polling": False,
        "reissue": False,
        "replacement": False,
    }
    if result is None:
        if observed_now < float(checked_request["deadline_at"]):
            raise NativeObservationUnavailable(
                "native event transport returned before completion, "
                "attention, or deadline")
        material = {
            **common,
            "kind": "attention",
            "attention_kind": "NATIVE_WAIT_DEADLINE",
            # Attribute the deterministic contract deadline, not a later
            # caller clock, so replay remains byte-identical.
            "observed_at": float(checked_request["deadline_at"]),
            "stop_required": True,
            "human_actions": [
                "reduce-scope", "end-wave", "architecture-review"],
        }
    else:
        checked_result = _validate_native_wait_result(
            result, request=checked_request)
        if float(checked_result["observed_at"]) > observed_now:
            raise CommandRuntimeError(
                "native wait result was observed after the caller clock")
        material = {
            **common,
            "kind": checked_result["status"],
            "attention_kind": checked_result.get("attention_kind"),
            "observed_at": float(checked_result["observed_at"]),
            "native_agent_id": checked_result.get("native_agent_id"),
            "native_result_fingerprint": checked_result[
                "result_fingerprint"],
            "stop_required": checked_result["status"] == "attention",
            "human_actions": ([] if checked_result["status"] == "completion"
                              else ["bounded-correction", "human-escalation"]),
        }
    return {**material, "fingerprint": _canonical_digest(material)}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _redact(value: str) -> tuple[str, int]:
    count = 0
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            redacted, hits = pattern.subn(
                lambda match: f"{match.group(1)}=[REDACTED]", redacted)
        else:
            redacted, hits = pattern.subn("[REDACTED]", redacted)
        count += hits
    for pattern, replacement in _PERSONAL_DATA_PATTERNS:
        redacted, hits = pattern.subn(replacement, redacted)
        count += hits
    return redacted, count


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _minimized_text(value: object, *, label: str) -> tuple[str, int]:
    """Project arbitrary caller text to a closed durable schema.

    Names and health information cannot be recognized completely with regular
    expressions.  Command output and free-form reasons therefore retain only
    a size and digest; durable runtime evidence is not a second log store.
    """
    raw = str(value)
    scrubbed, hits = _redact(raw)
    digest = hashlib.sha256(scrubbed.encode("utf-8", "replace")).hexdigest()
    return (f"[REDACTED]\n[{label}_MINIMIZED bytes="
            f"{len(raw.encode('utf-8', 'replace'))} sha256={digest}]", hits)


def _closed_reason_code(value: object | None) -> str | None:
    """Validate one bounded machine reason without admitting free-form text."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in _COMMAND_REASON_CODES:
        raise ValueError("command reason_code is not a closed runtime reason")
    return value


def _legacy_reason_code(value: object | None) -> str | None:
    """Recover only known codes from pre-field minimized representations."""
    if not isinstance(value, str):
        return None
    representations = {
        "binding_lost": "binding_lost",
        "detached_worker_ownership_lost":
            "detached_worker_ownership_lost",
    }
    representations.update({
        f"automatic command recovery stopped: {code}": code
        for code in _COMMAND_REASON_CODES
    })
    for text, code in representations.items():
        if value in {text, _minimized_text(text, label="REASON")[0]}:
            return code
    return None


def _journal_projection(snapshot: dict) -> dict:
    """Return a recovery-complete snapshot without repeated output text."""
    projected = json.loads(json.dumps(snapshot))
    projected["output_summary"] = ""
    for field in ("events", "lifecycle"):
        rows = []
        for source in projected.get(field) or []:
            row = dict(source)
            row["output_delta"] = ""
            rows.append(row)
        projected[field] = rows
    return projected


def _privacy_retention(snapshot: Mapping, *, terminal_at: float | None = None) \
        -> dict:
    """Return the closed retention policy stored with every command."""
    created_at = float(snapshot.get("created_at") or 0.0)
    if terminal_at is None and snapshot.get("state") in TERMINAL_STATES:
        terminal_at = float(snapshot.get("updated_at") or created_at)
    return {
        "schema": COMMAND_RETENTION_SCHEMA,
        "created_at": created_at,
        "terminal_at": terminal_at,
        "expires_at": (terminal_at + COMMAND_RETENTION_SECONDS
                       if terminal_at is not None else None),
        "max_handles": COMMAND_RETENTION_MAX_HANDLES,
        "max_bytes": COMMAND_RETENTION_MAX_BYTES,
        "delete_on_expiry": True,
    }


def _minimize_legacy_snapshot(snapshot: dict) -> dict:
    """Migrate authority state without retaining pre-policy free text."""
    migrated = json.loads(json.dumps(snapshot))
    migrated["reason"] = (_minimized_text(
        migrated["reason"], label="REASON")[0]
        if migrated.get("reason") is not None else None)
    migrated["reason_code"] = (_closed_reason_code(migrated["reason_code"])
                               if migrated.get("reason_code") is not None else
                               _legacy_reason_code(migrated.get("reason")))
    migrated["output_summary"] = ""
    migrated["artifact"] = None
    for field in ("events", "lifecycle"):
        rows = []
        for source in migrated.get(field) or []:
            row = dict(source)
            row["output_delta"] = ""
            if row.get("reason") is not None:
                row["reason"] = _minimized_text(
                    row["reason"], label="REASON")[0]
            row["reason_code"] = (_closed_reason_code(row["reason_code"])
                                  if row.get("reason_code") is not None else
                                  _legacy_reason_code(row.get("reason")))
            row["artifact"] = None
            rows.append(row)
        migrated[field] = rows
    migrated["privacy_retention"] = _privacy_retention(migrated)
    return migrated


def _bounded_tree_size(directory: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs
                   if not (Path(root) / name).is_symlink()]
        for name in files:
            path = Path(root) / name
            if path.is_symlink():
                return COMMAND_RETENTION_MAX_BYTES + 1
            try:
                total += path.stat().st_size
            except OSError:
                return COMMAND_RETENTION_MAX_BYTES + 1
            if total > COMMAND_RETENTION_MAX_BYTES:
                return total
    return total


def _purge_private_path(path: Path, root: Path) -> None:
    """Quarantine one owned path by rename, then irreversibly remove it."""
    if not os.path.lexists(path):
        return
    staged = root / f".privacy-purge-{path.name}-{secrets.token_hex(8)}"
    os.replace(path, staged)
    if staged.is_dir() and not staged.is_symlink():
        shutil.rmtree(staged)
    else:
        staged.unlink(missing_ok=True)


def _valid_retention(value: object, snapshot: Mapping) -> bool:
    if not isinstance(value, dict) or set(value) != {
            "schema", "created_at", "terminal_at", "expires_at",
            "max_handles", "max_bytes", "delete_on_expiry"} or \
            value.get("schema") != COMMAND_RETENTION_SCHEMA or \
            value.get("max_handles") != COMMAND_RETENTION_MAX_HANDLES or \
            value.get("max_bytes") != COMMAND_RETENTION_MAX_BYTES or \
            value.get("delete_on_expiry") is not True:
        return False
    try:
        created_at = float(value["created_at"])
        if not math.isfinite(created_at) or created_at != float(
                snapshot.get("created_at") or 0.0):
            return False
        terminal_at = value["terminal_at"]
        expires_at = value["expires_at"]
        if snapshot.get("state") in TERMINAL_STATES:
            terminal = float(terminal_at)
            expires = float(expires_at)
            return math.isfinite(terminal) and math.isfinite(expires) and \
                expires == terminal + COMMAND_RETENTION_SECONDS
        return terminal_at is None and expires_at is None
    except (TypeError, ValueError):
        return False


class CommandRuntime:
    """Filesystem-backed authority for durable command lifecycle records."""

    def __init__(self, root: str, *, workspace: str, authorization: str,
                 clock: Callable[[], float] | None = None,
                 delivery_lease_seconds: float =
                 DEFAULT_DELIVERY_LEASE_SECONDS,
                 owned_cleanup_context: Mapping[str, object] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._workspace = _fingerprint(str(workspace))
        self._authorization = _fingerprint(str(authorization))
        self._clock = clock or time.time
        self._delivery_lease_seconds = max(0.001,
                                           float(delivery_lease_seconds))
        self._owned_cleanup_context = (
            dict(owned_cleanup_context) if owned_cleanup_context is not None
            else None)
        if self._owned_cleanup_context is not None:
            required = {"manifest", "process_resource_id", "handoff_path"}
            if (set(self._owned_cleanup_context) != required or
                    any(not str(self._owned_cleanup_context.get(key) or "")
                        for key in required)):
                raise ValueError("owned cleanup runtime context is invalid")
        self.enforce_retention()

    def enforce_retention(self, *, now: float | None = None) -> dict:
        """Migrate legacy logs and purge terminal state by age/count/bytes."""
        observed_at = float(self._clock() if now is None else now)
        removed = []
        migrated = []
        retained = []
        lock_path = self.root / ".retention.lock"
        with lock_path.open("a+b") as root_lock:
            _lock_file(root_lock)
            try:
                for stale in list(self.root.glob(".privacy-purge-*")):
                    _purge_private_path(stale, self.root)
                for legacy in list(self.root.iterdir()):
                    if legacy.is_file() and legacy.name != ".retention.lock" \
                            and legacy.suffix in {".log", ".jsonl"}:
                        _purge_private_path(legacy, self.root)
                        removed.append(legacy.name)
                for directory in sorted(self.root.iterdir()):
                    if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                        continue
                    if directory.is_symlink() or not directory.is_dir():
                        _purge_private_path(directory, self.root)
                        removed.append(directory.name)
                        continue
                    lock = directory / "delivery.lock"
                    with lock.open("a+b") as handle_lock:
                        _lock_file(handle_lock)
                        try:
                            snapshot_path = directory / "snapshot.json"
                            raw = snapshot_path.read_bytes()
                            if len(raw) > MAX_SNAPSHOT_BYTES:
                                raise ValueError("command snapshot is oversized")
                            snapshot = json.loads(raw.decode("utf-8"))
                            if snapshot.get("schema") != SCHEMA or \
                                    snapshot.get("handle") != directory.name:
                                raise ValueError("command snapshot is invalid")
                            if not _valid_retention(
                                    snapshot.get("privacy_retention"), snapshot):
                                snapshot = _minimize_legacy_snapshot(snapshot)
                                artifacts = directory / "artifacts"
                                if os.path.lexists(artifacts):
                                    _purge_private_path(artifacts, directory)
                                _atomic_json(snapshot_path, snapshot)
                                _atomic_bytes(
                                    directory / "transitions.jsonl",
                                    (json.dumps({
                                        "event": {
                                            "state": "privacy_migrated",
                                            "revision": snapshot.get("revision"),
                                        },
                                        "snapshot": _journal_projection(snapshot),
                                    }, sort_keys=True,
                                        separators=(",", ":")) + "\n").encode())
                                migrated.append(directory.name)
                            size = _bounded_tree_size(directory)
                            retention = snapshot["privacy_retention"]
                            expires_at = retention.get("expires_at")
                            if snapshot.get("state") in TERMINAL_STATES and \
                                    expires_at is not None and \
                                    float(expires_at) <= observed_at:
                                retained.append((float("inf"), size, directory))
                            elif snapshot.get("state") in TERMINAL_STATES:
                                retained.append((float(snapshot.get(
                                    "updated_at") or 0.0), size, directory))
                        except (OSError, UnicodeError, ValueError, TypeError,
                                KeyError, AttributeError):
                            retained.append((float("inf"),
                                             COMMAND_RETENTION_MAX_BYTES + 1,
                                             directory))
                        finally:
                            _unlock_file(handle_lock)

                terminal = sorted(retained, key=lambda row: (
                    row[0] != float("inf"), row[0], row[2].name), reverse=True)
                kept_count = 0
                kept_bytes = 0
                for updated_at, size, directory in terminal:
                    invalid_or_expired = updated_at == float("inf")
                    excess = (kept_count >= COMMAND_RETENTION_MAX_HANDLES or
                              kept_bytes + size > COMMAND_RETENTION_MAX_BYTES)
                    if invalid_or_expired or excess:
                        _purge_private_path(directory, self.root)
                        removed.append(directory.name)
                    else:
                        kept_count += 1
                        kept_bytes += size
                return {"removed": sorted(set(removed)),
                        "migrated": sorted(set(migrated)),
                        "retained": kept_count,
                        "retained_bytes": kept_bytes,
                        "retention_seconds": COMMAND_RETENTION_SECONDS,
                        "max_handles": COMMAND_RETENTION_MAX_HANDLES,
                        "max_bytes": COMMAND_RETENTION_MAX_BYTES}
            finally:
                _unlock_file(root_lock)

    def _dir(self, handle: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", str(handle)):
            raise UnknownHandle("command handle is invalid")
        return self.root / str(handle)

    def _path(self, handle: str) -> Path:
        return self._dir(handle) / "snapshot.json"

    @contextmanager
    def _state_lock(self, handle: str):
        """Serialize every read-modify-write for one command handle."""
        directory = self._dir(handle)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "delivery.lock").open("a+b") as lock:
            _lock_file(lock)
            try:
                yield
            finally:
                _unlock_file(lock)

    def _load(self, handle: str) -> dict:
        try:
            with self._path(handle).open(encoding="utf-8") as source:
                snapshot = json.load(source)
        except (OSError, ValueError) as exc:
            # A transition is fsynced before its snapshot replacement.  If a
            # process dies inside that boundary, recover the last complete,
            # secret-safe snapshot from the append-only journal.
            try:
                with (self._dir(handle) / "transitions.jsonl").open(
                        encoding="utf-8") as source:
                    rows = [json.loads(line) for line in source if line.strip()]
                snapshot = next(row["snapshot"] for row in reversed(rows)
                                if isinstance(row.get("snapshot"), dict))
                _atomic_json(self._path(handle), snapshot)
            except (OSError, ValueError, KeyError, StopIteration) as recovery:
                raise UnknownHandle("command handle is unavailable") from recovery
        if snapshot.get("schema") != SCHEMA:
            raise UnknownHandle("command snapshot has an unsupported schema")
        if (snapshot.get("workspace_fingerprint") != self._workspace or
                snapshot.get("authorization_fingerprint") !=
                self._authorization):
            raise BindingMismatch(
                "command handle is not bound to this workspace and actor")
        if "reason_code" not in snapshot:
            snapshot["reason_code"] = _legacy_reason_code(
                snapshot.get("reason"))
        for field in ("events", "lifecycle"):
            for row in snapshot.get(field) or []:
                if "reason_code" not in row:
                    row["reason_code"] = _legacy_reason_code(
                        row.get("reason"))
        return snapshot

    def _save(self, handle: str, snapshot: dict, event: dict | None = None) -> None:
        directory = self._dir(handle)
        directory.mkdir(parents=True, exist_ok=True)
        existing_retention = snapshot.get("privacy_retention")
        terminal_at = None
        if snapshot.get("state") in TERMINAL_STATES:
            if isinstance(existing_retention, dict):
                terminal_at = existing_retention.get("terminal_at")
            terminal_at = float(terminal_at if terminal_at is not None else
                                snapshot.get("updated_at") or self._clock())
        snapshot["privacy_retention"] = _privacy_retention(
            snapshot, terminal_at=terminal_at)
        # Journal first: replay can reconstruct the last intended transition
        # after a crash before the snapshot replacement.
        if event is not None:
            journal = directory / "transitions.jsonl"
            safe_event = dict(event)
            safe_event["output_delta"] = ""
            encoded = (json.dumps(
                {"event": safe_event,
                 "snapshot": _journal_projection(snapshot)},
                sort_keys=True, separators=(",", ":")) + "\n").encode()
            prior = []
            try:
                prior = [line + b"\n" for line in journal.read_bytes().splitlines()
                         if line.strip()]
            except OSError:
                pass
            rows = (prior + [encoded])[-MAX_JOURNAL_ROWS:]
            while len(rows) > 1 and sum(map(len, rows)) > MAX_JOURNAL_BYTES:
                rows.pop(0)
            _atomic_bytes(journal, b"".join(rows))
        _atomic_json(self._path(handle), snapshot)

    def create(self, *, command_fingerprint: str, binding: Mapping | None,
               deadline: float | None = None, wave_id: str | None = None,
               identity: Mapping | None = None,
               review_session: Mapping | None = None,
               review_sandbox: Mapping | None = None,
               preview: Mapping | None = None) -> str:
        handle = secrets.token_hex(16)
        now = float(self._clock())
        if identity is not None:
            identity = dict(identity)
            if (set(identity) != {"schema", "run_id", "task_id"} or
                    identity.get("schema") !=
                    "taskplane.governed-command-identity/v1" or
                    not all(str(identity.get(key) or "").strip()
                            for key in ("run_id", "task_id"))):
                raise ValueError("governed command identity is invalid")
        if review_session is not None:
            review_session = dict(review_session)
            required = {"schema", "run_id", "target_fingerprint",
                        "consent_fingerprint"}
            if set(review_session) != required or review_session.get(
                    "schema") != "taskplane.review-session-binding/v1" or any(
                    not str(review_session.get(key) or "").strip()
                    for key in required):
                raise ValueError("review session binding is invalid")
        if review_sandbox is not None:
            review_sandbox = dict(review_sandbox)
            required = {"schema", "sandbox_id", "root_fingerprint",
                        "push_disabled"}
            optional = {"isolation_fingerprint"}
            if not required.issubset(review_sandbox) or \
                    set(review_sandbox) - required - optional or \
                    review_sandbox.get(
                    "schema") != "taskplane.review-sandbox-binding/v1" or \
                    review_sandbox.get("push_disabled") is not True or any(
                    not str(review_sandbox.get(key) or "").strip()
                    for key in required - {"push_disabled"}) or \
                    ("isolation_fingerprint" in review_sandbox and
                     not str(review_sandbox["isolation_fingerprint"]).strip()):
                raise ValueError("review sandbox binding is invalid")
        if preview is not None:
            preview = dict(preview)
            required = {"schema", "preview_id", "target", "revision",
                        "sandbox_id", "push_disabled", "network"}
            if (not required.issubset(preview) or
                    preview.get("schema") != "taskplane.host-preview/v1" or
                    preview.get("push_disabled") is not True or
                    not isinstance(preview.get("revision"), int) or
                    not all(str(preview.get(key) or "").strip()
                            for key in ("preview_id", "target", "sandbox_id")) or
                    not isinstance(preview.get("network"), dict) or
                    preview["network"].get("mode") != "deny"):
                raise ValueError("preview binding is invalid")
            preview = {key: preview[key] for key in required}
        snapshot = {
            "schema": SCHEMA,
            "handle": handle,
            "workspace_fingerprint": self._workspace,
            "authorization_fingerprint": self._authorization,
            "command_fingerprint": _fingerprint(str(command_fingerprint)),
            "binding_digest": _canonical_digest(binding) if binding else None,
            "state": "created",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "deadline": deadline,
            "wave_id": wave_id,
            **({"identity": identity} if identity is not None else {}),
            **({"review_session": review_session}
               if review_session is not None else {}),
            **({"review_sandbox": review_sandbox}
               if review_sandbox is not None else {}),
            **({"preview": preview} if preview is not None else {}),
            "exit_code": None,
            "reason": None,
            "reason_code": None,
            "events": [],
            "lifecycle": [],
            "recovery": [],
            "deliveries": {},
            "delivery_leases": {},
            "artifact": None,
            "output_summary": "",
            "output_digest": None,
            "metrics": {
                "launch_count": 1,
                "reconnect_count": 0,
                "model_delivery_count": 0,
                "unchanged_model_polls": 0,
                "output_redactions": 0,
            },
        }
        cleanup_resource_id = None
        if self._owned_cleanup_context is not None:
            try:
                import owned_cleanup
            except ImportError:
                from taskplane import owned_cleanup
            cleanup_resource_id = owned_cleanup.reserve_resource(
                str(self._owned_cleanup_context["manifest"]),
                kind="worker-contract",
                containment_root=str(self.root.resolve()),
                relative_name=handle,
                creator_nonce="command-runtime:" + handle,
                stable_identity={
                    "handle": handle,
                    "run_id": (identity or {}).get("run_id"),
                    "task_id": (identity or {}).get("task_id"),
                    "workspace_fingerprint": self._workspace,
                    "authorization_fingerprint": self._authorization,
                    "command_fingerprint": snapshot["command_fingerprint"],
                    "binding_digest": snapshot.get("binding_digest"),
                    "created_at": snapshot["created_at"],
                },
                evidence_refs=(
                    "terminal-state", "handoff", "publication-replay"),
                policy={"active": True},
            )
            snapshot["owned_cleanup"] = {
                **self._owned_cleanup_context,
                "worker_resource_id": cleanup_resource_id,
            }
        event = self._build_event(snapshot)
        snapshot["lifecycle"].append(event)
        self._save(handle, snapshot, event)
        if cleanup_resource_id is not None:
            owned_cleanup.activate_resource(
                str(self._owned_cleanup_context["manifest"]),
                cleanup_resource_id)
            owned_cleanup.bind_resource_dependency(
                str(self._owned_cleanup_context["manifest"]),
                str(self._owned_cleanup_context["process_resource_id"]),
                cleanup_resource_id)
        return handle

    def snapshot(self, handle: str) -> dict:
        return self._load(handle)

    def owned_resource_descriptor(self, handle: str, *,
                                  kind: str = "worker-contract") -> dict:
        """Identify one runtime directory for reserve-before-use cleanup.

        The orchestrator supplies repository/settings ownership when it
        appends this descriptor to the manifest.  This method contributes
        only facts owned by the command runtime and never deletes the path.
        """
        if kind not in {"worker-contract", "generated-state",
                        "test-artifact"}:
            raise ValueError("command runtime resource kind is invalid")
        snapshot = self._load(handle)
        identity = snapshot.get("identity") or {}
        if (identity.get("schema") !=
                "taskplane.governed-command-identity/v1" or
                not identity.get("run_id") or not identity.get("task_id")):
            raise CommandRuntimeError(
                "owned runtime resource requires exact run/task identity")
        return {
            "kind": kind,
            "containment_root": str(self.root.resolve()),
            "relative_name": str(handle),
            "stable_identity": {
                "handle": str(handle),
                "run_id": str(identity["run_id"]),
                "task_id": str(identity["task_id"]),
                "workspace_fingerprint": self._workspace,
                "authorization_fingerprint": self._authorization,
                "command_fingerprint": snapshot["command_fingerprint"],
                "binding_digest": snapshot.get("binding_digest"),
                "created_at": snapshot["created_at"],
            },
        }

    def record_recovery(self, handle: str, *, failure_class: str,
                        detail: str, progress: float | None = None,
                        safe: bool = True,
                        authority_changed: bool = False,
                        replan_required: bool = False) -> dict:
        """Persist and return one secret-safe canonical recovery decision."""
        with self._state_lock(handle):
            snapshot = self._load(handle)
            history = list(snapshot.get("recovery") or [])
            safe_detail, redactions = _redact(str(detail))
            related = [row for row in history
                       if row.get("failure_class") == str(failure_class)]
            fingerprints = [str(row.get("fingerprint") or "")
                            for row in related]
            fingerprints.append(_fingerprint(safe_detail))
            progress_values = [float(row["progress"]) for row in related
                               if row.get("progress") is not None]
            if progress is not None:
                progress_values.append(float(progress))
            decision = recovery.decide_recovery(
                failure_class=failure_class, attempt=len(related) + 1,
                fingerprints=fingerprints, progress=progress_values,
                safe=safe, authority_changed=authority_changed,
                replan_required=replan_required)
            row = {"schema": "taskplane.command-recovery/v1",
                   "fingerprint": fingerprints[-1],
                   "failure_class": str(failure_class),
                   "progress": progress, "decision": decision,
                   "at": float(self._clock())}
            history.append(row)
            snapshot["recovery"] = history
            snapshot["revision"] = int(snapshot["revision"]) + 1
            snapshot["updated_at"] = row["at"]
            snapshot["metrics"]["output_redactions"] += redactions
            event = {"revision": snapshot["revision"], "state": "recovery",
                     "at": row["at"], "decision": decision}
            self._save(handle, snapshot, event)
            return decision

    def transition(self, handle: str, state: str, *, exit_code: int | None = None,
                   reason: str | None = None,
                   reason_code: str | None = None,
                   expected_revision: int | None = None) -> dict:
        if state not in VALID_STATES:
            raise InvalidTransition(f"unknown command state: {state}")
        with self._state_lock(handle):
            snapshot = self._load(handle)
            return self._transition_locked(
                handle, snapshot, state, exit_code=exit_code, reason=reason,
                reason_code=reason_code,
                expected_revision=expected_revision)

    def _transition_locked(self, handle: str, snapshot: dict, state: str, *,
                           exit_code: int | None = None,
                           reason: str | None = None,
                           reason_code: str | None = None,
                           expected_revision: int | None = None) -> dict:
        safe_reason_code = _closed_reason_code(reason_code)
        if expected_revision is not None and snapshot["revision"] != expected_revision:
            raise RevisionConflict(
                f"command revision is {snapshot['revision']}, expected "
                f"{expected_revision}")
        current = snapshot["state"]
        if current in TERMINAL_STATES:
            if current == state:
                return self._event_for_state(snapshot, state)
            if state in ATTENTION_STATES:
                # Attention is an audit/wake event, not a terminal-state
                # reversal. A host may observe approval/input concurrently
                # with completion and serialize it second. Preserve it once
                # at a new revision while retaining the terminal authority.
                existing = next((event for event in snapshot.get("events") or []
                                 if event.get("state") == state), None)
                if existing is not None:
                    return existing
                revision = int(snapshot["revision"]) + 1
                now = float(self._clock())
                safe_reason, reason_redactions = _minimized_text(
                    reason, label="REASON") \
                    if reason is not None else (None, 0)
                snapshot["revision"] = revision
                snapshot["updated_at"] = now
                snapshot["metrics"]["output_redactions"] += reason_redactions
                event = self._build_event(
                    snapshot, state=state, reason=safe_reason or state,
                    reason_code=safe_reason_code)
                snapshot["events"].append(event)
                snapshot.setdefault("lifecycle", []).append(event)
                self._save(handle, snapshot, event)
                return event
            raise InvalidTransition(
                f"terminal command cannot move {current} -> {state}")
        if current == state:
            return self._event_for_state(snapshot, state)

        revision = int(snapshot["revision"]) + 1
        now = float(self._clock())
        safe_reason, reason_redactions = _minimized_text(
            reason, label="REASON") \
            if reason is not None else (None, 0)
        snapshot.update({
            "state": state, "revision": revision, "updated_at": now,
            "exit_code": exit_code, "reason": safe_reason,
            "reason_code": safe_reason_code,
        })
        snapshot["metrics"]["output_redactions"] += reason_redactions
        event = self._build_event(snapshot)
        snapshot.setdefault("lifecycle", []).append(event)
        if state in MEANINGFUL_STATES:
            snapshot["events"].append(event)
        self._save(handle, snapshot, event)
        return event

    def _event_for_state(self, snapshot: dict, state: str) -> dict:
        for event in reversed(snapshot.get("events") or []):
            if event.get("state") == state:
                return event
        return self._build_event(snapshot)

    def _build_event(self, snapshot: dict, *, state: str | None = None,
                     reason: str | None = None,
                     reason_code: str | None = None) -> dict:
        revision = int(snapshot["revision"])
        event_state = state or snapshot["state"]
        artifact = snapshot.get("artifact")
        event = {
            "schema": "taskplane.command-event/v1",
            "handle": snapshot["handle"],
            "revision": revision,
            "state": event_state,
            "reason": reason or snapshot.get("reason") or event_state,
            "reason_code": (reason_code if reason_code is not None else
                            snapshot.get("reason_code")),
            "exit_code": snapshot.get("exit_code"),
            "elapsed_ms": max(0, int((float(snapshot["updated_at"]) -
                                      float(snapshot["created_at"])) * 1000)),
            "output_delta": snapshot.get("output_summary") or "",
            "artifact": artifact,
            "delivery_key": _canonical_digest({
                "handle": snapshot["handle"], "revision": revision,
            }),
            **({"review_session": dict(snapshot["review_session"])}
               if snapshot.get("review_session") else {}),
            **({"identity": dict(snapshot["identity"])}
               if snapshot.get("identity") else {}),
            **({"preview": dict(snapshot["preview"])}
               if snapshot.get("preview") else {}),
        }
        telemetry_snapshot = dict(snapshot)
        telemetry_snapshot["state"] = event_state
        event["dispatch_event"] = dispatch_event(telemetry_snapshot)
        return event

    def append_output(self, handle: str, output: str) -> dict:
        with self._state_lock(handle):
            snapshot = self._load(handle)
            raw_output = str(output)
            redacted, redactions = _minimized_text(
                raw_output, label="OUTPUT")
            digest = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
            if digest == snapshot.get("output_digest"):
                return snapshot
            artifact_dir = self._dir(handle) / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "output.log"
            # One bounded, privacy-scrubbed artifact per command.  Output is
            # appended once per new digest; repeated identical chunks are
            # ignored.  The cap bounds retention rather than just the event
            # projection: bytes beyond it are represented by the digest and
            # ``truncated`` flag, never durably copied to disk.
            retained_before = artifact_path.stat().st_size \
                if artifact_path.exists() else 0
            encoded = redacted.encode("utf-8")
            original_size = len(raw_output.encode("utf-8", "replace"))
            room = max(0, MAX_DURABLE_OUTPUT - retained_before)
            retained = encoded[:room]
            with artifact_path.open("a", encoding="utf-8", newline="") as target:
                target.write(retained.decode("utf-8", errors="ignore"))
                target.flush()
                os.fsync(target.fileno())
            all_bytes = artifact_path.read_bytes()
            artifact_digest = hashlib.sha256(all_bytes).hexdigest()
            summary = redacted.encode("utf-8")[:MAX_EVENT_OUTPUT].decode(
                "utf-8", errors="ignore")
            snapshot["output_summary"] = summary
            snapshot["output_digest"] = digest
            snapshot["artifact"] = {
                "path": f"artifacts/{artifact_path.name}",
                "sha256": artifact_digest,
                "bytes": len(all_bytes),
                "truncated": (original_size > MAX_EVENT_OUTPUT or
                              len(encoded) > room),
            }
            snapshot["metrics"]["output_redactions"] += redactions
            snapshot["updated_at"] = float(self._clock())
            snapshot["revision"] += 1
            self._save(handle, snapshot, {
                "revision": snapshot["revision"], "state": "output_changed",
                "at": snapshot["updated_at"],
                "artifact_sha256": artifact_digest,
            })
            return snapshot

    def read_artifact(self, handle: str) -> str:
        snapshot = self._load(handle)
        artifact = snapshot.get("artifact")
        if not artifact:
            return ""
        path = (self._dir(handle) / artifact["path"]).resolve()
        if self._dir(handle).resolve() not in path.parents:
            raise CommandRuntimeError("artifact path escaped command storage")
        return path.read_text(encoding="utf-8")

    def pending(self, handle: str, *, consumer: str) -> dict | None:
        consumer = str(consumer)
        with self._state_lock(handle):
            snapshot = self._load(handle)
            acknowledged = int((snapshot.get("deliveries") or {}).get(
                consumer, 0))
            leases = snapshot.setdefault("delivery_leases", {})
            now = float(self._clock())
            for event in snapshot.get("events") or []:
                revision = int(event["revision"])
                if revision <= acknowledged:
                    continue
                lease = leases.get(consumer)
                if (lease and int(lease.get("revision", -1)) == revision and
                        float(lease.get("expires_at", 0)) > now):
                    return None
                # Claim is persisted before the event can become model-visible.
                # An unacknowledged claim is replayable only after deterministic
                # expiry, allowing recovery from a crash-before-ack boundary.
                leases[consumer] = {
                    "revision": revision,
                    "delivery_key": event["delivery_key"],
                    "claimed_at": now,
                    "expires_at": now + self._delivery_lease_seconds,
                }
                self._save(handle, snapshot)
                return event
            return None

    def receive(self, handle: str, *, consumer: str,
                delivery_key: str) -> dict | None:
        """Durably deduplicate a candidate before making it model-visible.

        ``pending`` only leases an internal candidate.  A host adapter MUST
        use the event returned here as its wake payload.  The receipt is
        persisted before return, so replay after a crash-before-receive is
        safe and a crash-after-return cannot cause a second model wake.
        This is consumer idempotency, not a claim of process-level exactly
        once execution.
        """
        consumer = str(consumer)
        with self._state_lock(handle):
            snapshot = self._load(handle)
            event = next((row for row in snapshot.get("events") or []
                          if row.get("delivery_key") == delivery_key), None)
            if event is None:
                raise CommandRuntimeError("delivery key is unknown")
            revision = int(event["revision"])
            previous = int(snapshot["deliveries"].get(consumer, 0))
            if revision <= previous:
                return None
            lease = (snapshot.get("delivery_leases") or {}).get(consumer)
            if (lease is None or lease.get("delivery_key") != delivery_key):
                raise CommandRuntimeError("delivery is not claimed by consumer")
            snapshot["deliveries"][consumer] = revision
            if consumer == "model":
                snapshot["metrics"]["model_delivery_count"] += 1
            snapshot["delivery_leases"].pop(consumer, None)
            self._save(handle, snapshot)
            delivered = dict(event)
            delivered["delivery_receipt"] = {
                "schema": "taskplane.command-delivery-receipt/v1",
                "consumer": consumer,
                "delivery_key": delivery_key,
                "revision": revision,
            }
            return delivered

    def ack(self, handle: str, *, consumer: str,
            delivery_key: str) -> dict | None:
        """Compatibility name for the durable consumer receipt boundary."""
        return self.receive(handle, consumer=consumer,
                            delivery_key=delivery_key)

    def wait_next(self, handle: str, *, consumer: str,
                  interrupted: Callable[[], bool] | None = None,
                  timeout: float | None = None,
                  interval: float = 0.05) -> dict | None:
        """Block runtime-side until a meaningful event or caller interrupt."""
        started = time.monotonic()
        while True:
            event = self.pending(handle, consumer=consumer)
            if event is not None:
                return event
            if interrupted is not None and interrupted():
                raise InterruptedWait("command wait was interrupted")
            if timeout is not None and time.monotonic() - started >= timeout:
                return None
            time.sleep(interval)

    def reconnect(self, handle: str, *, binding: Mapping | None,
                  ownership_check: Callable[[Mapping], bool] | None = None) \
            -> dict:
        with self._state_lock(handle):
            snapshot = self._load(handle)
            if snapshot["state"] in TERMINAL_STATES:
                return self._event_for_state(snapshot, snapshot["state"])
            supplied = _canonical_digest(binding) if binding else None
            if not supplied or supplied != snapshot.get("binding_digest"):
                return self._transition_locked(
                    handle, snapshot, "failed", reason="binding_lost",
                    reason_code="binding_lost")
            if ownership_check is not None and not ownership_check(binding):
                return self._transition_locked(
                    handle, snapshot, "input_required",
                    reason="detached_worker_ownership_lost",
                    reason_code="detached_worker_ownership_lost")
            snapshot["metrics"]["reconnect_count"] += 1
            snapshot["updated_at"] = float(self._clock())
            self._save(handle, snapshot, {
                "revision": snapshot["revision"], "state": "reconnected",
                "at": snapshot["updated_at"],
            })
            return snapshot

    def cancel(self, handle: str, *, expected_revision: int | None = None) -> dict:
        with self._state_lock(handle):
            snapshot = self._load(handle)
            if expected_revision is not None and \
                    snapshot["revision"] != expected_revision:
                raise RevisionConflict(
                    f"command revision is {snapshot['revision']}, expected "
                    f"{expected_revision}")
            if (snapshot["state"] == "input_required" and
                    snapshot.get("reason_code") ==
                    "detached_worker_ownership_lost"):
                # Reconnect has already established that this process is no
                # longer ours to signal.  Preserve the durable attention
                # event: reporting cancellation here would claim authority
                # the host no longer has and consume the only recovery wake.
                raise InvalidTransition(
                    "detached command process ownership no longer matches")
            return self._transition_locked(
                handle, snapshot, "cancelled",
                expected_revision=expected_revision)


class WaveState:
    """Deterministic primitive for later workflow wave aggregation."""

    def __init__(self, members: list[str]):
        if not members or len(set(members)) != len(members):
            raise ValueError("wave membership must be non-empty and unique")
        self.members = tuple(members)
        self.states = {member: "running" for member in members}
        self.aggregate_delivered = False

    def update(self, member: str, state: str) -> dict | None:
        if member not in self.states:
            raise KeyError(member)
        self.states[member] = state
        if (not self.aggregate_delivered and
                all(value in TERMINAL_STATES for value in self.states.values())):
            self.aggregate_delivered = True
            return {"state": "wave_completed", "members": dict(self.states)}
        return None


def efficiency_snapshot(*, launches: int = 0, model_wakes: int = 0,
                        unchanged_model_polls: int = 0,
                        polling_raw_tokens: int = 0,
                        total_raw_tokens: int | None = None) -> dict:
    """Create bounded counters; hard budget evaluation is owned downstream."""
    share = None
    if total_raw_tokens is not None and total_raw_tokens > 0:
        share = polling_raw_tokens / total_raw_tokens
    return {
        "schema": "taskplane.command-efficiency/v1",
        "launches": max(0, int(launches)),
        "model_wakes": max(0, int(model_wakes)),
        "unchanged_model_polls": max(0, int(unchanged_model_polls)),
        "polling_raw_tokens": max(0, int(polling_raw_tokens)),
        "total_raw_tokens": total_raw_tokens,
        "polling_raw_token_share": share,
        "measurement_status": "measured" if share is not None else "unproven",
    }
