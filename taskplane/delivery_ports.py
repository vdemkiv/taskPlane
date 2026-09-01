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
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable

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
TRUSTED_GIT_SNAPSHOT_SCHEMA = "taskplane.trusted-git-snapshot/v1"
_TRUSTED_GIT_SNAPSHOT_TOKEN = object()
_DASHBOARD_REFRESH_PUBLISHER = None


class DeliveryPortError(RuntimeError):
    """A delivery boundary refused an invalid or unsafe operation."""


class InjectedFault(DeliveryPortError):
    """A deterministic test fault at a named public seam."""


def configure_dashboard_refresh_publisher(publisher) -> None:
    """Attach the composition-root dashboard publisher to this leaf port."""
    global _DASHBOARD_REFRESH_PUBLISHER
    if publisher is not None and not callable(publisher):
        raise TypeError("dashboard refresh publisher must be callable")
    _DASHBOARD_REFRESH_PUBLISHER = publisher


def publish_dashboard_refresh(
    workspace: str,
    *,
    event_type: str,
    outcome: str,
    lifecycle_events: Sequence[str],
    trace: Callable[..., object],
    member_terminal: bool = False,
    publisher=None,
) -> None:
    """Publish one post-receipt lifecycle intent through an injected port."""
    if event_type not in lifecycle_events:
        raise ValueError(
            "dashboard lifecycle event is absent from canonical settings: "
            + str(event_type)
        )
    selected = publisher or _DASHBOARD_REFRESH_PUBLISHER
    if selected is None:
        trace(
            workspace,
            "dashboard_publication_deferred",
            event_type=event_type,
            outcome=outcome,
            member_terminal=member_terminal,
            error="dashboard refresh publisher is not configured",
        )
        return
    try:
        selected(workspace, event_type=event_type, outcome=outcome)
    except Exception as exc:
        trace(
            workspace,
            "dashboard_publication_deferred",
            event_type=event_type,
            outcome=outcome,
            member_terminal=member_terminal,
            error=f"{exc.__class__.__name__}: {exc}",
        )


def dispatch_task_name(kind: str, agent: str, ref: str | None = None) -> str:
    """Return a stable native task identity without erasing its role."""
    role = (agent or "agent").removeprefix("tp-")
    parts = ["tp", kind]
    if role != kind:
        parts.append(role)
    if ref:
        parts.append(str(ref))
    identity = "\0".join((str(kind), str(agent), str(ref or "")))
    raw = "_".join(parts).lower()
    name = re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "tp_agent"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    return name[:55].rstrip("_") + "_" + digest


def role_marker(agent: str) -> str:
    return "taskplane-role:" + str(agent)


def dispatch_envelope(
    kind: str,
    agent: str,
    ref: str,
    model_tier: str,
    *,
    role_instructions: str,
    requested_model: str | None,
    requested_effort: str,
    settings_digest: str,
    route: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal resolved settings and optional host routing into one brief."""
    fields = {
        "role": agent,
        "role_marker": role_marker(agent),
        "role_instructions": role_instructions,
        "task_name": dispatch_task_name(kind, agent, ref),
        "model_tier": model_tier,
        "model": route["effective_model"] if route else requested_model,
        "reasoning_effort": (
            route["effective_effort"] if route else requested_effort),
        "settings_digest": settings_digest,
    }
    if route is not None:
        fields["dispatch_route"] = dict(route)
        fields["dispatch_blocked"] = route["block_before_dispatch"]
    return fields


def normalize_worker_terminal_outcome(value: object) -> str:
    text = str(value or "success").strip().lower()
    for token, outcome in (
        ("handoff", "handoff"), ("transfer", "handoff"),
        ("cancel", "cancellation"), ("interrupt", "interruption"),
        ("abort", "interruption"), ("killed", "interruption"),
        ("fail", "failure"), ("error", "failure"),
        ("exception", "failure"),
    ):
        if token in text:
            return outcome
    return "success"


def task_test_timeout_seconds(
    task: object, *, default_seconds: int,
    validator: Callable[..., int],
) -> int:
    """Read one closed Plan timeout shape without owning its defaults."""
    field = "verification_runner.gate_timeout.aggregate_seconds"
    if not isinstance(task, dict):
        raise ValueError(f"{field} task container must be an object")
    if "verification_runner" not in task:
        return int(default_seconds)
    runner = task.get("verification_runner")
    if not isinstance(runner, dict):
        raise ValueError(f"{field} parent containers must be objects")
    if "gate_timeout" not in runner:
        raise ValueError(f"{field} is required when verification_runner is present")
    gate_timeout = runner.get("gate_timeout")
    if not isinstance(gate_timeout, dict):
        raise ValueError(f"{field} parent containers must be objects")
    if "aggregate_seconds" not in gate_timeout:
        raise ValueError(f"{field} is required when gate_timeout is present")
    return validator(
        gate_timeout.get("aggregate_seconds"), field=field, plan_minted=True)


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


@dataclass(frozen=True, slots=True)
class TrustedGitSnapshot:
    """Live, revalidatable identity for one exact clean repository state."""

    root: Path
    head_sha: str
    tree_sha: str
    git_executable: str
    git_executable_sha256: str
    environment_fingerprint: str
    evidence_sha256: Mapping[str, str]
    fingerprint: str
    _inspector: "TrustedGitInspector"
    _token: object

    def __reduce__(self):
        raise TypeError("trusted Git snapshots are not serializable")


class TrustedGitInspector:
    """Observe Git through one content-bound executable and closed environment.

    This boundary is intentionally narrower than the general ``GitRunner``:
    terminal evidence must not inherit aliases, external diff/textconv
    helpers, hooks, fsmonitor commands, or caller-selected Git configuration.
    """

    _BASE_ARGS = (
        "--no-optional-locks",
        "--literal-pathspecs",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.attributesFile=/dev/null",
        "-c", "diff.external=",
    )

    def __init__(self, git_executable: str | os.PathLike[str] | None = None) -> None:
        system_git = Path("/usr/bin/git")
        selected = Path(git_executable) if git_executable is not None else (
            system_git if system_git.is_file() else Path(shutil.which("git") or "")
        )
        if not selected.is_absolute() or selected.is_symlink():
            raise DeliveryPortError(
                "trusted Git executable must be an absolute non-symlink path"
            )
        try:
            resolved = selected.resolve(strict=True)
            executable_bytes = resolved.read_bytes()
        except OSError as exc:
            raise DeliveryPortError("trusted Git executable is unavailable") from exc
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise DeliveryPortError("trusted Git executable is not executable")
        self._git_executable = resolved
        self._git_executable_sha256 = hashlib.sha256(executable_bytes).hexdigest()
        self._environment = {
            "PATH": str(resolved.parent),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self._environment_fingerprint = content_fingerprint(self._environment)

    @property
    def executable_sha256(self) -> str:
        return self._git_executable_sha256

    @property
    def environment_fingerprint(self) -> str:
        return self._environment_fingerprint

    def _run(
        self,
        root: Path,
        args: Sequence[str],
        *,
        binary: bool = False,
    ) -> str | bytes:
        completed = subprocess.run(
            [str(self._git_executable), *self._BASE_ARGS, *args],
            cwd=root,
            env=dict(self._environment),
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr
            detail = (
                stderr.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes)
                else str(stderr or "")
            ).strip()
            raise DeliveryPortError(
                "trusted Git observation failed: " + (detail or str(args[0]))
            )
        if binary:
            return bytes(completed.stdout)
        return str(completed.stdout).strip()

    @staticmethod
    def _root(repository: str | os.PathLike[str]) -> Path:
        supplied = Path(repository)
        if supplied.is_symlink():
            raise DeliveryPortError("candidate repository cannot be a symlink")
        try:
            root = supplied.resolve(strict=True)
        except OSError as exc:
            raise DeliveryPortError("candidate repository is unavailable") from exc
        if not root.is_dir():
            raise DeliveryPortError("candidate repository is not a directory")
        return root

    @staticmethod
    def _contained_regular_file(root: Path, value: str | os.PathLike[str]) -> Path:
        supplied = Path(value)
        lexical = supplied if supplied.is_absolute() else root / supplied
        try:
            relative = lexical.relative_to(root)
        except ValueError as exc:
            raise DeliveryPortError("terminal evidence must be inside the candidate") from exc
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise DeliveryPortError("terminal evidence cannot traverse a symlink")
        try:
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise DeliveryPortError("terminal evidence escapes the candidate") from exc
        if not resolved.is_file():
            raise DeliveryPortError("terminal evidence must be a regular file")
        return resolved

    def _state(self, root: Path) -> tuple[str, str, str]:
        top = Path(str(self._run(root, ("rev-parse", "--show-toplevel")))).resolve()
        if top != root:
            raise DeliveryPortError("candidate repository must be the Git toplevel")
        head = str(self._run(root, ("rev-parse", "--verify", "HEAD")))
        tree = str(self._run(root, ("rev-parse", "--verify", "HEAD^{tree}")))
        status = str(
            self._run(
                root,
                ("status", "--porcelain=v1", "--untracked-files=all"),
            )
        )
        if status:
            raise DeliveryPortError(
                "candidate checkout must be clean, including untracked files"
            )
        return head, tree, status

    def snapshot(
        self,
        repository: str | os.PathLike[str],
        *,
        evidence_paths: Sequence[str | os.PathLike[str]] = (),
    ) -> TrustedGitSnapshot:
        """Bind exact HEAD/tree and tracked evidence, checking both ends."""
        root = self._root(repository)
        contained_evidence = tuple(
            self._contained_regular_file(root, value) for value in evidence_paths
        )
        before_head, before_tree, _ = self._state(root)
        evidence: dict[str, str] = {}
        for path in contained_evidence:
            relative = path.relative_to(root).as_posix()
            tracked = str(
                self._run(root, ("ls-files", "--error-unmatch", "--", relative))
            )
            if tracked != relative:
                raise DeliveryPortError("terminal evidence is not tracked at HEAD")
            head_bytes = self._run(root, ("show", f"HEAD:{relative}"), binary=True)
            assert isinstance(head_bytes, bytes)
            working_bytes = path.read_bytes()
            if working_bytes != head_bytes:
                raise DeliveryPortError("terminal evidence differs from candidate HEAD")
            evidence[relative] = hashlib.sha256(head_bytes).hexdigest()
        after_head, after_tree, _ = self._state(root)
        if (after_head, after_tree) != (before_head, before_tree):
            raise DeliveryPortError("candidate HEAD moved during observation")
        projection = {
            "schema": TRUSTED_GIT_SNAPSHOT_SCHEMA,
            "root_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
            "head_sha": before_head,
            "tree_sha": before_tree,
            "git_executable": str(self._git_executable),
            "git_executable_sha256": self._git_executable_sha256,
            "environment_fingerprint": self._environment_fingerprint,
            "evidence_sha256": evidence,
        }
        return TrustedGitSnapshot(
            root=root,
            head_sha=before_head,
            tree_sha=before_tree,
            git_executable=str(self._git_executable),
            git_executable_sha256=self._git_executable_sha256,
            environment_fingerprint=self._environment_fingerprint,
            evidence_sha256=evidence,
            fingerprint=content_fingerprint(projection),
            _inspector=self,
            _token=_TRUSTED_GIT_SNAPSHOT_TOKEN,
        )

    def assert_unchanged(self, snapshot: TrustedGitSnapshot) -> TrustedGitSnapshot:
        """Re-observe a live snapshot and reject movement, dirt, or replacement."""
        if not isinstance(snapshot, TrustedGitSnapshot) or \
                snapshot._token is not _TRUSTED_GIT_SNAPSHOT_TOKEN or \
                snapshot._inspector is not self:
            raise DeliveryPortError("live trusted Git snapshot is required")
        paths = tuple(snapshot.root / path for path in snapshot.evidence_sha256)
        observed = self.snapshot(snapshot.root, evidence_paths=paths)
        bindings = (
            observed.head_sha == snapshot.head_sha,
            observed.tree_sha == snapshot.tree_sha,
            observed.git_executable == snapshot.git_executable,
            observed.git_executable_sha256 == snapshot.git_executable_sha256,
            observed.environment_fingerprint == snapshot.environment_fingerprint,
            dict(observed.evidence_sha256) == dict(snapshot.evidence_sha256),
            observed.fingerprint == snapshot.fingerprint,
        )
        if not all(bindings):
            raise DeliveryPortError("candidate snapshot changed after observation")
        return snapshot


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

    @staticmethod
    def _claim_successor(
        domain_dir: Path,
        predecessor_fingerprint: str | None,
        successor_fingerprint: str,
    ) -> None:
        """Atomically bind one durable successor to one predecessor.

        ``fcntl`` is unavailable on supported Windows hosts and may also be
        absent in constrained runtimes.  An exclusive create is the portable
        cross-process CAS primitive; the claim remains durable so a crashed
        winner can resume, while every competing successor fails closed.
        """
        claims_dir = domain_dir / "claims"
        claims_dir.mkdir(parents=True, exist_ok=True)
        predecessor_key = content_fingerprint(
            {"predecessor_fingerprint": predecessor_fingerprint}
        )
        claim_path = claims_dir / f"{predecessor_key}.claim"
        payload = (successor_fingerprint + "\n").encode("ascii")
        try:
            descriptor = os.open(
                claim_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                claimed = claim_path.read_bytes()
            except OSError as exc:
                raise DeliveryPortError(
                    "evidence head CAS claim is unreadable"
                ) from exc
            if claimed != payload:
                raise DeliveryPortError("evidence head CAS mismatch")
            return
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("exclusive CAS claim write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_fd = os.open(claims_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

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
            self.fault_injector.checkpoint("before-head-cas")
            self._claim_successor(
                domain_dir,
                intent["expected_head"],
                receipt["fingerprint"],
            )
            if not receipt_path.exists():
                self._write_atomic(receipt_path, receipt_bytes)
            self.fault_injector.checkpoint("after-immutable-bytes")
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
