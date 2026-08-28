"""Injected delivery boundaries and deterministic implementations for R-0001.

This module deliberately imports no Taskplane owner or transition adapter.  The
small protocols below are the public seams through which owners receive time,
events, host capabilities, persistence, platform facts, Git, and faults.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # Windows retains the in-process lock
    fcntl = None


HOST_ACTION_SCHEMA = "taskplane.host-action-capability/v1"
TASK_DISPATCH_SCHEMA = "taskplane.task-dispatch-capability/v1"
EVIDENCE_DOMAINS = frozenset(
    {"review_rebind", "producer_observation", "telemetry", "release_evidence", "remote_default"}
)
EVIDENCE_FAULT_SEAMS = (
    "after-prepare-intent",
    "after-immutable-bytes",
    "before-head-cas",
    "after-head-cas",
    "before-domain-state",
    "after-domain-state",
    "during-reconcile",
)
IRREVERSIBLE_TOOLS = frozenset({"push", "tag", "install", "publish", "credential-release"})


class DeliveryPortError(RuntimeError):
    """A delivery boundary refused an invalid or unsafe operation."""


class InjectedFault(DeliveryPortError):
    """A deterministic test fault at a named public seam."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def content_fingerprint(value: bytes | Any) -> str:
    raw = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(raw).hexdigest()


@runtime_checkable
class Clock(Protocol):
    def wall_time(self) -> float: ...

    def monotonic(self) -> float: ...


@runtime_checkable
class EventWaiter(Protocol):
    def wait(
        self, wait_policy: Mapping[str, Any], outstanding_members: Sequence[str]
    ) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class ProducerEventSource(Protocol):
    def events(self, *, host_session_id: str, host_turn_id: str) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class HostActionCapabilitySource(Protocol):
    def consume(
        self,
        handle: str,
        *,
        expected_bindings: Mapping[str, Any],
        now: float,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class TaskDispatchCapabilityFactory(Protocol):
    def create(self, **bindings: Any) -> "TaskDispatchCapability": ...


@dataclass(frozen=True)
class PreparedEvidence:
    token: str
    domain: str
    operation_id: str
    expected_head: str | None
    payload_fingerprint: str


@runtime_checkable
class EvidenceStore(Protocol):
    def prepare(
        self,
        domain: str,
        operation_id: str,
        payload: bytes | Mapping[str, Any],
        *,
        expected_head: str | None = None,
    ) -> PreparedEvidence: ...

    def commit(self, prepared: PreparedEvidence | str) -> bytes: ...

    def reconcile(self, domain: str | None = None) -> tuple[bytes, ...]: ...

    def teardown(self, namespace_token: str) -> None: ...


@runtime_checkable
class PlatformCiQuery(Protocol):
    def query(self, *, repository_id: str, pushed_sha: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class GitRunner(Protocol):
    def run(self, args: Sequence[str], *, cwd: str | os.PathLike[str] | None = None) -> GitResult: ...


@runtime_checkable
class FaultInjector(Protocol):
    def checkpoint(self, public_seam_name: str) -> None: ...


class SystemClock:
    def wall_time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    def __init__(self, wall_time: float = 0.0, monotonic: float = 0.0) -> None:
        self._wall = float(wall_time)
        self._monotonic = float(monotonic)

    def wall_time(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot move backwards")
        self._wall += seconds
        self._monotonic += seconds


class RecordedEventWaiter:
    """Returns recorded batches synchronously and advances only an injected clock."""

    def __init__(self, batches: Iterable[Sequence[Mapping[str, Any]]], clock: FakeClock) -> None:
        self._batches = iter(tuple(tuple(dict(event) for event in batch) for batch in batches))
        self.clock = clock
        self.invocations: list[tuple[dict[str, Any], tuple[str, ...]]] = []

    def wait(
        self, wait_policy: Mapping[str, Any], outstanding_members: Sequence[str]
    ) -> Sequence[Mapping[str, Any]]:
        self.invocations.append((dict(wait_policy), tuple(outstanding_members)))
        try:
            return next(self._batches)
        except StopIteration:
            return ()


class RecordedProducerEventSource:
    def __init__(self, events: Iterable[Mapping[str, Any]]) -> None:
        self._events = tuple(dict(event) for event in events)

    def events(self, *, host_session_id: str, host_turn_id: str) -> Sequence[Mapping[str, Any]]:
        return tuple(
            dict(event)
            for event in self._events
            if event.get("host_session_id") == host_session_id
            and event.get("host_turn_id") == host_turn_id
        )


class NoopFaultInjector:
    def checkpoint(self, public_seam_name: str) -> None:
        return None


class EnumeratingFaultInjector:
    """Fails once at selected seams and records every seam reached."""

    def __init__(self, fail_at: str | Iterable[str]) -> None:
        selected = {fail_at} if isinstance(fail_at, str) else set(fail_at)
        unknown = selected.difference(EVIDENCE_FAULT_SEAMS)
        if unknown:
            raise ValueError(f"unknown fault seams: {sorted(unknown)}")
        self._remaining = selected
        self.visited: list[str] = []

    def checkpoint(self, public_seam_name: str) -> None:
        if public_seam_name not in EVIDENCE_FAULT_SEAMS:
            raise ValueError(f"undeclared fault seam: {public_seam_name}")
        self.visited.append(public_seam_name)
        if public_seam_name in self._remaining:
            self._remaining.remove(public_seam_name)
            raise InjectedFault(public_seam_name)


class SubprocessGitRunner:
    def run(self, args: Sequence[str], *, cwd: str | os.PathLike[str] | None = None) -> GitResult:
        if not args or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
            raise DeliveryPortError("Git arguments must be non-empty strings without NUL bytes")
        completed = subprocess.run(
            ["git", *args], cwd=cwd, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False
        )
        return GitResult(completed.returncode, completed.stdout, completed.stderr)


class RecordedPlatformCiQuery:
    def __init__(self, responses: Iterable[Mapping[str, Any]]) -> None:
        self._responses = [dict(response) for response in responses]
        self.queries: list[tuple[str, str]] = []

    def query(self, *, repository_id: str, pushed_sha: str) -> Mapping[str, Any]:
        self.queries.append((repository_id, pushed_sha))
        if not self._responses:
            raise DeliveryPortError("no recorded platform response")
        return dict(self._responses.pop(0))


class RecordedHostActionCapabilitySource:
    """Hermetic model of a host-private, exact-bound, single-use channel."""

    def __init__(self) -> None:
        self._by_handle: dict[str, dict[str, Any]] = {}
        self._consumed: set[tuple[str, str]] = set()
        self._last_sequence: dict[str, int] = {}

    def issue(self, **bindings: Any) -> str:
        required = {
            "capability_id", "purpose", "sequence", "host_session_id", "host_turn_id",
            "run_id", "kernel_id", "task_id", "stage", "request_or_output_digest",
            "contract_fingerprint", "issued_at", "expires_at",
        }
        missing = required.difference(bindings)
        if missing:
            raise DeliveryPortError(f"missing host capability bindings: {sorted(missing)}")
        unknown = set(bindings).difference(required | {"nonce"})
        if unknown:
            raise DeliveryPortError(f"unknown host capability bindings: {sorted(unknown)}")
        if bindings["purpose"] not in {"review_rebind", "producer_observation"}:
            raise DeliveryPortError("invalid host capability purpose")
        projection = dict(bindings)
        deterministic_nonce = content_fingerprint(
            {key: projection[key] for key in sorted(required)}
        )[:32]
        projection.update(
            schema=HOST_ACTION_SCHEMA,
            nonce=bindings.get("nonce") or deterministic_nonce,
            cryptographic_authenticity_claimed=False,
        )
        handle = "host-private:" + content_fingerprint(projection)
        self._by_handle[handle] = projection
        return handle

    def consume(
        self,
        handle: str,
        *,
        expected_bindings: Mapping[str, Any],
        now: float,
    ) -> Mapping[str, Any]:
        capability = self._by_handle.get(handle)
        if capability is None:
            raise DeliveryPortError("missing host-private capability handle")
        identity = (str(capability["capability_id"]), str(capability["nonce"]))
        if identity in self._consumed:
            raise DeliveryPortError("host capability replay")
        for key, expected in expected_bindings.items():
            if key not in capability or capability[key] != expected:
                raise DeliveryPortError(f"host capability binding mismatch: {key}")
        if capability["cryptographic_authenticity_claimed"] is not False:
            raise DeliveryPortError("host capability must not claim actor authenticity")
        if now < float(capability["issued_at"]) or now >= float(capability["expires_at"]):
            raise DeliveryPortError("host capability expired or not yet valid")
        session = str(capability["host_session_id"])
        sequence = int(capability["sequence"])
        if sequence <= self._last_sequence.get(session, -1):
            raise DeliveryPortError("duplicate or non-monotonic host capability sequence")
        self._consumed.add(identity)
        self._last_sequence[session] = sequence
        return dict(capability)


@dataclass(frozen=True)
class TaskDispatchCapability:
    projection: Mapping[str, Any]

    def allows(self, surface: str, value: str) -> bool:
        fields = {
            "tool": "allowed_tools", "read_path": "read_paths", "write_path": "write_paths",
            "git_ref": "allowed_git_refs", "network_endpoint": "allowed_network_endpoints",
            "credential_handle": "credential_handles",
        }
        field = fields.get(surface)
        if field is None or value == "*":
            return False
        return value in self.projection[field]

    def require(self, surface: str, value: str, **bindings: Any) -> None:
        for key in ("run_id", "task_id", "stage"):
            if key in bindings and bindings[key] != self.projection[key]:
                raise DeliveryPortError(f"dispatch capability binding mismatch: {key}")
        if not self.allows(surface, value):
            raise DeliveryPortError(f"dispatch capability denies {surface}: {value}")


class RecordedTaskDispatchCapabilityFactory:
    _LIST_FIELDS = (
        "allowed_tools", "read_paths", "write_paths", "allowed_git_refs",
        "allowed_network_endpoints", "credential_handles",
    )

    def __init__(self) -> None:
        self.created: list[TaskDispatchCapability] = []

    def create(self, **bindings: Any) -> TaskDispatchCapability:
        required = {
            "run_id", "source_sha", "design_fingerprint", "plan_fingerprint", "task_id",
            "stage", "reservation_fingerprint", "predecessor_fingerprint",
        }
        missing = required.difference(bindings)
        if missing:
            raise DeliveryPortError(f"missing dispatch capability bindings: {sorted(missing)}")
        unknown = set(bindings).difference(required | set(self._LIST_FIELDS))
        if unknown:
            raise DeliveryPortError(f"unknown dispatch capability bindings: {sorted(unknown)}")
        projection = {key: bindings[key] for key in required}
        for field in self._LIST_FIELDS:
            values = tuple(sorted(set(bindings.get(field, ()))))
            if "*" in values:
                raise DeliveryPortError(f"wildcard authority is forbidden: {field}")
            projection[field] = values
        forbidden = IRREVERSIBLE_TOOLS.intersection(projection["allowed_tools"])
        if forbidden:
            raise DeliveryPortError(f"workers cannot receive irreversible tools: {sorted(forbidden)}")
        if any("release" in str(handle).lower() for handle in projection["credential_handles"]):
            raise DeliveryPortError("workers cannot receive release credentials")
        projection.update(
            schema=TASK_DISPATCH_SCHEMA,
            release_credentials_available=False,
            irreversible_actions_allowed=False,
            cryptographic_authenticity_claimed=False,
        )
        projection["capability_id"] = content_fingerprint(projection)
        capability = TaskDispatchCapability(projection)
        self.created.append(capability)
        return capability


class SandboxEvidenceStore:
    """Filesystem evidence store with deterministic, isolated CAS namespaces."""

    def __init__(
        self,
        caller_root: str | os.PathLike[str],
        repository_fingerprint: str,
        run_namespace: str,
        *,
        fault_injector: FaultInjector | None = None,
        disposable: bool = True,
    ) -> None:
        if not repository_fingerprint or not run_namespace:
            raise DeliveryPortError("repository fingerprint and run namespace are required")
        if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in (repository_fingerprint, run_namespace)):
            raise DeliveryPortError("evidence identity must be one safe path component")
        supplied_root = Path(caller_root)
        if supplied_root.is_symlink():
            raise DeliveryPortError("caller root cannot be a symlink")
        root = supplied_root.resolve(strict=True)
        self.caller_root = root
        self.repository_fingerprint = repository_fingerprint
        self.run_namespace = run_namespace
        self.path = root / ".taskplane-evidence" / repository_fingerprint / run_namespace
        self.path.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise DeliveryPortError("evidence namespace cannot be a symlink")
        self._resolved_path = self.path.resolve(strict=True)
        if not self._resolved_path.is_relative_to(root):
            raise DeliveryPortError("evidence namespace escapes caller root")
        self.namespace_token = content_fingerprint(
            {"caller_root": str(root), "repository_fingerprint": repository_fingerprint, "run_namespace": run_namespace}
        )
        self.fault_injector = fault_injector or NoopFaultInjector()
        self.disposable = disposable
        self._lock = threading.RLock()

    def _domain_dir(self, domain: str) -> Path:
        if domain not in EVIDENCE_DOMAINS:
            raise DeliveryPortError(f"unknown evidence domain: {domain}")
        path = self.path / domain
        for child in ("intents", "receipts"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @contextmanager
    def _domain_lock(self, domain_dir: Path):
        """Serialize one domain's entire read-check-publish transaction."""
        descriptor = os.open(domain_dir / ".cas.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with self._lock:
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def prepare(
        self,
        domain: str,
        operation_id: str,
        payload: bytes | Mapping[str, Any],
        *,
        expected_head: str | None = None,
    ) -> PreparedEvidence:
        if not operation_id:
            raise DeliveryPortError("operation id is required")
        raw = payload if isinstance(payload, bytes) else canonical_json(payload)
        descriptor = {
            "domain": domain, "operation_id": operation_id, "expected_head": expected_head,
            "payload": base64.b64encode(raw).decode(), "payload_fingerprint": content_fingerprint(raw),
        }
        token = content_fingerprint(descriptor)
        descriptor["token"] = token
        domain_dir = self._domain_dir(domain)
        intent_path = domain_dir / "intents" / f"{token}.json"
        encoded = canonical_json(descriptor)
        if intent_path.exists() and intent_path.read_bytes() != encoded:
            raise DeliveryPortError("evidence intent collision")
        if not intent_path.exists():
            self._write_atomic(intent_path, encoded)
        prepared = PreparedEvidence(token, domain, operation_id, expected_head, descriptor["payload_fingerprint"])
        self.fault_injector.checkpoint("after-prepare-intent")
        return prepared

    def _find_intent(self, token: str) -> tuple[Path, dict[str, Any]]:
        found = list(self.path.glob(f"*/intents/{token}.json"))
        if len(found) != 1:
            raise DeliveryPortError("prepared evidence token is missing or ambiguous")
        intent = json.loads(found[0].read_text())
        claimed_token = intent.pop("token", None)
        if claimed_token != token or content_fingerprint(intent) != token:
            raise DeliveryPortError("evidence intent fingerprint mismatch")
        intent["token"] = claimed_token
        payload = base64.b64decode(intent["payload"], validate=True)
        if content_fingerprint(payload) != intent["payload_fingerprint"]:
            raise DeliveryPortError("evidence intent payload fingerprint mismatch")
        return found[0], intent

    def commit(self, prepared: PreparedEvidence | str) -> bytes:
        token = prepared.token if isinstance(prepared, PreparedEvidence) else prepared
        _, intent = self._find_intent(token)
        domain_dir = self._domain_dir(intent["domain"])
        receipt = {
            "domain": intent["domain"], "operation_id": intent["operation_id"],
            "predecessor_fingerprint": intent["expected_head"],
            "payload": intent["payload"], "payload_fingerprint": intent["payload_fingerprint"],
            "prepare_token": token,
        }
        receipt["fingerprint"] = content_fingerprint(receipt)
        receipt_bytes = canonical_json(receipt)
        receipt_path = domain_dir / "receipts" / f"{receipt['fingerprint']}.json"
        head_path = domain_dir / "HEAD"
        state_path = domain_dir / "STATE"
        with self._domain_lock(domain_dir):
            if receipt_path.exists():
                existing_receipt = receipt_path.read_bytes()
                if existing_receipt != receipt_bytes:
                    raise DeliveryPortError("immutable evidence receipt collision")
                if state_path.exists() and state_path.read_text().strip() == receipt["fingerprint"]:
                    return existing_receipt
            actual_head = head_path.read_text().strip() if head_path.exists() else None
            if actual_head not in {intent["expected_head"], receipt["fingerprint"]}:
                raise DeliveryPortError("evidence head CAS mismatch")
            if not receipt_path.exists():
                self._write_atomic(receipt_path, receipt_bytes)
            self.fault_injector.checkpoint("after-immutable-bytes")
            self.fault_injector.checkpoint("before-head-cas")
            if actual_head != receipt["fingerprint"]:
                self._write_atomic(head_path, (receipt["fingerprint"] + "\n").encode())
            self.fault_injector.checkpoint("after-head-cas")
            self.fault_injector.checkpoint("before-domain-state")
            self._write_atomic(state_path, (receipt["fingerprint"] + "\n").encode())
            self.fault_injector.checkpoint("after-domain-state")
            return receipt_bytes

    def reconcile(self, domain: str | None = None) -> tuple[bytes, ...]:
        self.fault_injector.checkpoint("during-reconcile")
        domains = (domain,) if domain else tuple(sorted(EVIDENCE_DOMAINS))
        recovered: list[bytes] = []
        for name in domains:
            domain_dir = self._domain_dir(name)
            for intent_path in sorted((domain_dir / "intents").glob("*.json")):
                recovered.append(self.commit(intent_path.stem))
        return tuple(recovered)

    def teardown(self, namespace_token: str) -> None:
        if not self.disposable:
            raise DeliveryPortError("production evidence cannot be torn down")
        if namespace_token != self.namespace_token:
            raise DeliveryPortError("namespace teardown token mismatch")
        resolved = self.path.resolve(strict=True)
        if resolved != self._resolved_path or self.path.is_symlink() or not resolved.is_relative_to(self.caller_root):
            raise DeliveryPortError("evidence namespace containment changed")
        shutil.rmtree(resolved)


class LocatorEvidenceStore(SandboxEvidenceStore):
    """Non-disposable production projection of the incumbent managed run root."""

    def __init__(self, caller_root: str | os.PathLike[str], repository_fingerprint: str, run_namespace: str, *, fault_injector: FaultInjector | None = None) -> None:
        super().__init__(caller_root, repository_fingerprint, run_namespace, fault_injector=fault_injector, disposable=False)
