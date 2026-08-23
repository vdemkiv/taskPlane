"""Durable, replay-safe command state and meaningful-event delivery.

This module deliberately does not own a subprocess implementation.  It owns
the host-neutral state that adapters bind to: opaque handles, transitions,
output artifacts, consumer delivery leases, and reconnect accounting.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from contextlib import contextmanager
from typing import Callable, Mapping

try:
    import recovery
except ImportError:  # package import path
    from taskplane import recovery

try:
    import fcntl as _file_lock
except ImportError:  # pragma: no cover - exercised by windows-latest
    _file_lock = None
    import msvcrt as _windows_lock


SCHEMA = "taskplane.command-state/v1"
MAX_EVENT_OUTPUT = 16 * 1024
DEFAULT_DELIVERY_LEASE_SECONDS = 30.0
TERMINAL_STATES = frozenset({
    "succeeded", "failed", "timed_out", "cancelled",
})
ATTENTION_STATES = frozenset({
    "approval_required", "input_required", "milestone",
})
MEANINGFUL_STATES = TERMINAL_STATES | ATTENTION_STATES
VALID_STATES = MEANINGFUL_STATES | {"created", "running"}

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


class CommandRuntime:
    """Filesystem-backed authority for durable command lifecycle records."""

    def __init__(self, root: str, *, workspace: str, authorization: str,
                 clock: Callable[[], float] | None = None,
                 delivery_lease_seconds: float =
                 DEFAULT_DELIVERY_LEASE_SECONDS):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._workspace = _fingerprint(str(workspace))
        self._authorization = _fingerprint(str(authorization))
        self._clock = clock or time.time
        self._delivery_lease_seconds = max(0.001,
                                           float(delivery_lease_seconds))

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
        return snapshot

    def _save(self, handle: str, snapshot: dict, event: dict | None = None) -> None:
        directory = self._dir(handle)
        directory.mkdir(parents=True, exist_ok=True)
        # Journal first: replay can reconstruct the last intended transition
        # after a crash before the snapshot replacement.
        if event is not None:
            journal = directory / "transitions.jsonl"
            with journal.open("a", encoding="utf-8", newline="") as target:
                target.write(json.dumps({"event": event, "snapshot": snapshot},
                                        sort_keys=True,
                                        separators=(",", ":")) + "\n")
                target.flush()
                os.fsync(target.fileno())
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
        event = self._build_event(snapshot)
        snapshot["lifecycle"].append(event)
        self._save(handle, snapshot, event)
        return handle

    def snapshot(self, handle: str) -> dict:
        return self._load(handle)

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
                   expected_revision: int | None = None) -> dict:
        if state not in VALID_STATES:
            raise InvalidTransition(f"unknown command state: {state}")
        with self._state_lock(handle):
            snapshot = self._load(handle)
            return self._transition_locked(
                handle, snapshot, state, exit_code=exit_code, reason=reason,
                expected_revision=expected_revision)

    def _transition_locked(self, handle: str, snapshot: dict, state: str, *,
                           exit_code: int | None = None,
                           reason: str | None = None,
                           expected_revision: int | None = None) -> dict:
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
                safe_reason, reason_redactions = _redact(str(reason)) \
                    if reason is not None else (None, 0)
                snapshot["revision"] = revision
                snapshot["updated_at"] = now
                snapshot["metrics"]["output_redactions"] += reason_redactions
                event = self._build_event(
                    snapshot, state=state, reason=safe_reason or state)
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
        safe_reason, reason_redactions = _redact(str(reason)) \
            if reason is not None else (None, 0)
        snapshot.update({
            "state": state, "revision": revision, "updated_at": now,
            "exit_code": exit_code, "reason": safe_reason,
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
                     reason: str | None = None) -> dict:
        revision = int(snapshot["revision"])
        event_state = state or snapshot["state"]
        artifact = snapshot.get("artifact")
        return {
            "schema": "taskplane.command-event/v1",
            "handle": snapshot["handle"],
            "revision": revision,
            "state": event_state,
            "reason": reason or snapshot.get("reason") or event_state,
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

    def append_output(self, handle: str, output: str) -> dict:
        with self._state_lock(handle):
            snapshot = self._load(handle)
            redacted, redactions = _redact(str(output))
            digest = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
            if digest == snapshot.get("output_digest"):
                return snapshot
            artifact_dir = self._dir(handle) / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "output.log"
            # One canonical artifact per command. Output is appended once per
            # new digest; repeated identical chunks are ignored.
            with artifact_path.open("a", encoding="utf-8", newline="") as target:
                target.write(redacted)
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
                "truncated": len(redacted.encode("utf-8")) > MAX_EVENT_OUTPUT,
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
                    handle, snapshot, "failed", reason="binding_lost")
            if ownership_check is not None and not ownership_check(binding):
                return self._transition_locked(
                    handle, snapshot, "input_required",
                    reason="detached_worker_ownership_lost")
            snapshot["metrics"]["reconnect_count"] += 1
            snapshot["updated_at"] = float(self._clock())
            self._save(handle, snapshot, {
                "revision": snapshot["revision"], "state": "reconnected",
                "at": snapshot["updated_at"],
            })
            return snapshot

    def cancel(self, handle: str, *, expected_revision: int | None = None) -> dict:
        return self.transition(handle, "cancelled",
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
