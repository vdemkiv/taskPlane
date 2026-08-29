"""Atomic exact-SHA terminal truth for R-0013 delivery.

Prepared projection bytes have no authority.  One immutable bundle becomes
the logical authority only when an orchestrator-bound object capability
advances ``head.json`` by compare-and-swap.  Readers remain fail-closed until
all eight derived projections reconcile byte-identically to that bundle.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # Windows retains the in-process lock and atomic replace
    fcntl = None

try:
    from taskplane import (
        delivery_ports,
        expanded_route_authority_provider,
        wiring_closure,
    )
except ImportError:  # direct executable/import compatibility
    import delivery_ports
    import expanded_route_authority_provider
    import wiring_closure


TERMINAL_BUNDLE_SCHEMA = "taskplane.exact-sha-terminal-bundle/v1"
TERMINAL_PROJECTION_SCHEMA = "taskplane.exact-sha-terminal-projection/v1"
TERMINAL_RECONCILIATION_SCHEMA = "taskplane.terminal-reconciliation/v1"
PRIVATE_USAGE_CLEANUP_SCHEMA = "taskplane.private-usage-cleanup/v1"
SELECTOR_EXECUTION_SCHEMA = "taskplane.terminal-selector-execution/v1"
EXACT_CANDIDATE_SUCCESSOR_SCHEMA = "taskplane.r0013-exact-candidate-successor/v1"
EXACT_CANDIDATE_TEMPLATE_SCHEMA = (
    "taskplane.r0013-exact-candidate-successor-template/v1"
)
EXACT_CANDIDATE_TEMPLATE_PATH = "exports/terminal/r0013/successor-template.json"
TERMINAL_STATUS = "feature-complete-not-externally-mutated"
SURFACE_IDS = (
    "git_head",
    "governed_progress",
    "run_journal",
    "tasks_and_gates",
    "public_report",
    "repository_verification_report",
    "release_evidence",
    "exports_terminal_evidence",
)
IDENTITY_FIELDS = (
    "full_source_sha",
    "terminal_status",
    "requirement_id",
    "design_fingerprint",
    "plan_fingerprint",
    "graph_fingerprint",
    "native_usage_fingerprint",
    "candidate_wiring_fingerprint",
    "full_suite_fingerprint",
    "predecessor_fingerprint",
)
_FINGERPRINT_FIELDS = frozenset(IDENTITY_FIELDS) - {
    "full_source_sha",
    "terminal_status",
    "requirement_id",
}
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PROJECTION_FIELDS = frozenset(
    {"schema", "surface_id", "identity", "payload", "payload_fingerprint", "fingerprint"}
)
_BUNDLE_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "operation_id",
        "identity",
        "surface_ids",
        "surface_digests",
        "fingerprint",
    }
)
_HEAD_FIELDS = frozenset(
    {"schema", "bundle_fingerprint", "predecessor_fingerprint", "operation_id"}
)
_HEAD_SCHEMA = "taskplane.exact-sha-terminal-head/v1"
_RECONCILIATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_fingerprint",
        "surface_digests",
        "native_usage_fingerprint",
        "repaired_surface_ids",
        "fingerprint",
    }
)
_AUTHORITY_FIELDS = frozenset(
    {"schema", "status", "bundle", "reconciliation", "fingerprint"}
)
_ISSUER_SCHEMA = "taskplane.terminal-orchestrator-issuer/v1"
_ISSUER_FIELDS = frozenset({"schema", "issuer_fingerprint"})
_CLEANUP_FIELDS = frozenset(
    {
        "schema", "status", "bundle_fingerprint",
        "native_usage_fingerprint", "fingerprint",
    }
)
_SELECTOR_EXECUTION_FIELDS = frozenset(
    {
        "schema", "status", "producer", "candidate_sha",
        "repository_snapshot_fingerprint", "git_executable_sha256",
        "git_environment_fingerprint", "selector", "test_source_sha256",
        "argv", "exit_code", "stdout_sha256", "stderr_sha256",
        "output_sha256", "fingerprint",
    }
)
_EXACT_CANDIDATE_FIELDS = frozenset(
    {
        "schema", "requirement_id", "finding_id", "status", "candidate_sha",
        "template_sha256", "repository_snapshot_fingerprint", "surfaces",
        "selectors", "evidence_state", "fingerprint",
    }
)
_EXACT_CANDIDATE_TEMPLATE_FIELDS = frozenset(
    {
        "schema", "requirement_id", "finding_id", "candidate_binding",
        "surface_ids", "required_selectors", "prepared_evidence_state",
    }
)
_EXACT_CANDIDATE_BINDING = {
    "source": "trusted-git-head-at-materialization",
    "field": "candidate_sha",
    "requires_full_object_id": True,
    "requires_clean_checkout": True,
    "output_name": "<candidate_sha>.json",
}
_EXACT_CANDIDATE_EVIDENCE_STATE = {
    "terminal_authority": "not-minted",
    "full_suite": "not-recorded",
    "release": "not-granted",
    "main_mutation": "not-granted",
    "publication": "not-granted",
}
_TERMINAL_RECEIPT_TOKEN = object()
_SELECTOR_RECEIPT_TOKEN = object()
_EXACT_CANDIDATE_RECEIPT_TOKEN = object()
_EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN = object()
_EXPANDED_ROUTE_PROVIDER_MAX_INPUT_BYTES = 128 * 1024
_EXPANDED_ROUTE_PROVIDER_MAX_OUTPUT_BYTES = 256 * 1024
_EXPANDED_ROUTE_PROVIDER_MAX_COMBINED_OUTPUT_BYTES = 256 * 1024
_EXPANDED_ROUTE_PROVIDER_TIMEOUT_SECONDS = 10
_NONTERMINAL_VALUES = frozenset(
    {
        "active", "blocked", "executing", "in_progress", "needs_user",
        "nonterminal", "pending", "queued", "running", "started", "waiting",
    }
)
_LIFECYCLE_KEYS = frozenset(
    {"lifecycle", "lifecycle_state", "phase", "state", "status", "step"}
)
_PRIVATE_EXPORT_KEYS = frozenset(
    {
        "access_token", "authorization", "completion", "cookie", "credential",
        "password", "per_session", "prompt", "provider_response", "raw_usage",
        "secret", "session_id", "session_ids", "transcript",
    }
)
_PRIVATE_EXPORT_KEYS_COMPACT = frozenset(
    key.replace("_", "") for key in _PRIVATE_EXPORT_KEYS
) | {"token"}


class TerminalTruthError(RuntimeError):
    """Terminal authority is missing, partial, contradictory, or unauthorized."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _ExpandedRouteProviderTransportOverflow(RuntimeError):
    """The isolated provider crossed a bounded output stream limit."""

    def __init__(self, stream: str):
        super().__init__(f"expanded-route provider {stream} output overflow")
        self.stream = stream


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    projection = dict(value)
    projection["fingerprint"] = _digest(projection)
    return projection


def _normalize_exact_candidate_template(value: Any) -> dict[str, Any]:
    """Validate the closed, tracked H-32 template contract."""
    if not isinstance(value, Mapping) or set(value) != _EXACT_CANDIDATE_TEMPLATE_FIELDS or \
            value.get("schema") != EXACT_CANDIDATE_TEMPLATE_SCHEMA or \
            value.get("requirement_id") != "R-0013" or \
            value.get("finding_id") != "H-32" or \
            value.get("candidate_binding") != _EXACT_CANDIDATE_BINDING or \
            tuple(value.get("surface_ids") or ()) != SURFACE_IDS or \
            value.get("prepared_evidence_state") != _EXACT_CANDIDATE_EVIDENCE_STATE:
        raise TerminalTruthError("candidate", "exact-candidate template is invalid")
    selectors = value.get("required_selectors")
    if not isinstance(selectors, list) or not selectors or \
            len(selectors) != len(set(selectors)) or \
            any(not isinstance(selector, str) for selector in selectors):
        raise TerminalTruthError("selector", "required selector inventory is invalid")
    return dict(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TerminalTruthError("identity", f"{field} is required")
    return value


def _fingerprint(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _FINGERPRINT.fullmatch(text):
        raise TerminalTruthError("identity", f"{field} must be a SHA-256 fingerprint")
    return text


def _object_id(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _OBJECT_ID.fullmatch(text):
        raise TerminalTruthError("identity", f"{field} must be a full Git SHA")
    return text


def normalize_terminal_identity(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate the closed identity shared byte-for-byte by all surfaces."""
    if not isinstance(value, Mapping) or set(value) != set(IDENTITY_FIELDS):
        raise TerminalTruthError("identity", "terminal identity fields are not closed")
    result: dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        if field == "full_source_sha":
            result[field] = _object_id(value.get(field), field)
        elif field == "terminal_status":
            result[field] = _text(value.get(field), field)
            if result[field] != TERMINAL_STATUS:
                raise TerminalTruthError("nonterminal", "terminal status is not complete")
        elif field == "requirement_id":
            result[field] = _text(value.get(field), field)
        else:
            result[field] = _fingerprint(value.get(field), field)
    return result


def _validate_terminal_payload(value: Any, *, path: tuple[str, ...] = ()) -> None:
    """Reject private export material and recursively retained active state."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            compact_key = re.sub(r"[^a-z0-9]", "", normalized_key)
            if compact_key in _PRIVATE_EXPORT_KEYS_COMPACT:
                raise TerminalTruthError(
                    "privacy",
                    "exports terminal evidence contains private detail at "
                    + ".".join((*path, str(key))),
                )
            lifecycle_key = normalized_key in _LIFECYCLE_KEYS or any(
                normalized_key.endswith(f"_{suffix}")
                for suffix in _LIFECYCLE_KEYS
            ) or any(
                compact_key.endswith(suffix.replace("_", ""))
                for suffix in _LIFECYCLE_KEYS
            )
            if lifecycle_key and isinstance(item, str) and \
                    item.strip().lower().replace("-", "_") in _NONTERMINAL_VALUES:
                raise TerminalTruthError(
                    "nonterminal",
                    "terminal surface retains executing/nonterminal state at "
                    + ".".join((*path, str(key))),
                )
            _validate_terminal_payload(item, path=(*path, str(key)))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_terminal_payload(item, path=(*path, str(index)))


def prepare_terminal_surface(
    surface_id: str,
    identity: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Prepare one immutable projection; this grants no terminal authority."""
    if surface_id not in SURFACE_IDS:
        raise TerminalTruthError("surface", f"unknown terminal surface: {surface_id}")
    if not isinstance(payload, Mapping):
        raise TerminalTruthError("surface", "terminal surface payload must be an object")
    normalized_identity = normalize_terminal_identity(identity)
    normalized_payload = dict(payload)
    _validate_terminal_payload(normalized_payload)
    if surface_id == "exports_terminal_evidence":
        if normalized_payload.get("redacted") is not True:
            raise TerminalTruthError(
                "privacy", "exports terminal evidence must be explicitly redacted"
            )
    return _seal(
        {
            "schema": TERMINAL_PROJECTION_SCHEMA,
            "surface_id": surface_id,
            "identity": normalized_identity,
            "payload": normalized_payload,
            "payload_fingerprint": _digest(normalized_payload),
        }
    )


def validate_terminal_surface(
    value: Mapping[str, Any],
    *,
    expected_surface_id: str,
    expected_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PROJECTION_FIELDS:
        raise TerminalTruthError("surface", "terminal surface fields are not closed")
    if value.get("schema") != TERMINAL_PROJECTION_SCHEMA:
        raise TerminalTruthError("surface", "terminal surface schema is invalid")
    if value.get("surface_id") != expected_surface_id:
        raise TerminalTruthError("mixed", "terminal surface id is contradictory")
    identity = normalize_terminal_identity(value.get("identity"))
    if identity != normalize_terminal_identity(expected_identity):
        raise TerminalTruthError("mixed", "terminal surface identity is mixed")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise TerminalTruthError("surface", "terminal surface payload must be an object")
    _validate_terminal_payload(payload)
    if value.get("payload_fingerprint") != _digest(payload):
        raise TerminalTruthError("surface", "terminal surface payload fingerprint mismatch")
    unsigned = {key: item for key, item in value.items() if key != "fingerprint"}
    if value.get("fingerprint") != _digest(unsigned):
        raise TerminalTruthError("surface", "terminal surface fingerprint mismatch")
    return dict(value)


@dataclass(frozen=True, slots=True)
class PreparedTerminalDelivery:
    bundle: dict[str, Any]
    bundle_bytes: bytes
    surface_bytes: dict[str, bytes]
    candidate_wiring_receipt: wiring_closure.CandidateCheckoutReceipt


class FinalizationCapability:
    """Live, non-serializable authority bound to one root operation."""

    __slots__ = (
        "_issuer",
        "run_id",
        "full_source_sha",
        "design_fingerprint",
        "plan_fingerprint",
        "expected_predecessor_fingerprint",
        "operation_id",
    )

    def __init__(
        self,
        *,
        issuer: object,
        run_id: str,
        full_source_sha: str,
        design_fingerprint: str,
        plan_fingerprint: str,
        expected_predecessor_fingerprint: str,
        operation_id: str,
    ) -> None:
        self._issuer = issuer
        self.run_id = run_id
        self.full_source_sha = full_source_sha
        self.design_fingerprint = design_fingerprint
        self.plan_fingerprint = plan_fingerprint
        self.expected_predecessor_fingerprint = expected_predecessor_fingerprint
        self.operation_id = operation_id

    def __reduce__(self):
        raise TypeError("finalization capability is not serializable")


class OrchestratorIssuer:
    """Non-serializable issuer bound durably to exactly one authority root."""

    __slots__ = ("_secret", "fingerprint")

    def __init__(self, token: object, secret: bytes | None = None) -> None:
        if token is not _TERMINAL_RECEIPT_TOKEN:
            raise TypeError("orchestrator issuers are created by TerminalCoordinator")
        self._secret = secret if secret is not None else os.urandom(32)
        if not isinstance(self._secret, bytes) or len(self._secret) != 32:
            raise TypeError("orchestrator issuer secret is invalid")
        self.fingerprint = hashlib.sha256(self._secret).hexdigest()

    def __reduce__(self):
        raise TypeError("orchestrator issuer is not serializable")


class TerminalAuthorityReceipt(dict):
    """A terminal projection carrying a live link back to its CAS authority."""

    __slots__ = ("_coordinator", "_token")

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        coordinator: "TerminalCoordinator",
        token: object,
    ) -> None:
        if token is not _TERMINAL_RECEIPT_TOKEN:
            raise TypeError("terminal authority receipts are coordinator-produced")
        super().__init__(value)
        self._coordinator = coordinator
        self._token = token

    def __reduce__(self):
        raise TypeError("live terminal authority receipt is not serializable")


class _ImmutableReceipt(dict):
    """Dictionary-shaped receipt whose public mutation API is closed."""

    __slots__ = ()

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("coordinator-produced receipts are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class ExpandedRouteProviderReceipt(_ImmutableReceipt):
    """Live terminal result minted only by an orchestrator-owned client."""

    __slots__ = ("_client", "_request_fingerprint", "_token")

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        client: "ExpandedRouteProviderClient",
        request_fingerprint: str,
        token: object,
    ) -> None:
        if token is not _EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN:
            raise TypeError(
                "expanded-route receipts are orchestrator-provider-produced")
        dict.__init__(self, value)
        self._client = client
        self._request_fingerprint = request_fingerprint
        self._token = token

    def __reduce__(self):
        raise TypeError(
            "live expanded-route provider receipts are not serializable")


class ExpandedRouteProviderClient:
    """Launch and authenticate one protected expanded-route provider.

    Construction is restricted to a live ``TerminalCoordinator``.  The
    worker-visible adapter never receives the locator, package path, process
    runner, provider HMAC material, or the live receipt seal maintained here.
    """

    __slots__ = (
        "_coordinator", "_locator_path", "_package_path", "_authority_root",
        "_locator_fingerprint", "_receipt_seals", "_token",
    )

    def __init__(
        self,
        locator_path: str,
        *,
        coordinator: "TerminalCoordinator",
        token: object,
    ) -> None:
        if token is not _EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN or \
                coordinator._issuer is None:
            raise TypeError(
                "expanded-route provider clients are orchestrator-produced")
        try:
            package_path = \
                expanded_route_authority_provider._configured_package_path(
                    locator_path)
            state = expanded_route_authority_provider._validate_installation(
                locator_path, execution_path=package_path)
        except expanded_route_authority_provider.ProviderError as exc:
            raise TerminalTruthError(
                "provider-provenance", exc.detail) from exc
        self._coordinator = coordinator
        self._locator_path = str(Path(locator_path).resolve(strict=True))
        self._package_path = str(state["package_path"])
        self._authority_root = Path(state["root"])
        self._locator_fingerprint = str(state["locator_fingerprint"])
        self._receipt_seals: dict[
            int, tuple[ExpandedRouteProviderReceipt, str]
        ] = {}
        self._token = token

    @staticmethod
    def _run_provider(
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        payload: bytes,
    ) -> subprocess.CompletedProcess[bytes]:
        """Capture the isolated provider without unbounded pipe buffering."""
        command = list(argv)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if process.stdin is None or process.stdout is None or \
                process.stderr is None:  # pragma: no cover - Popen contract
            ExpandedRouteProviderClient._terminate_and_reap(process)
            raise OSError("expanded-route provider pipes are unavailable")

        stdout = bytearray()
        stderr = bytearray()
        combined_size = [0]
        overflow: list[str] = []
        reader_errors: list[BaseException] = []
        lock = threading.Lock()
        stop_reading = threading.Event()

        def read_capped(
            pipe, target: bytearray, stream: str,
        ) -> None:
            try:
                while not stop_reading.is_set():
                    chunk = os.read(pipe.fileno(), 64 * 1024)
                    if not chunk:
                        return
                    with lock:
                        if len(target) + len(chunk) > \
                                _EXPANDED_ROUTE_PROVIDER_MAX_OUTPUT_BYTES:
                            overflow.append(stream)
                            stop_reading.set()
                            return
                        if combined_size[0] + len(chunk) > \
                                _EXPANDED_ROUTE_PROVIDER_MAX_COMBINED_OUTPUT_BYTES:
                            overflow.append("combined")
                            stop_reading.set()
                            return
                        target.extend(chunk)
                        combined_size[0] += len(chunk)
            except OSError as exc:
                if not stop_reading.is_set():
                    reader_errors.append(exc)
                    stop_reading.set()

        def write_request() -> None:
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(process.stdin.fileno(), view)
                    view = view[written:]
            except BrokenPipeError:
                pass
            except OSError as exc:
                if process.poll() is None:
                    reader_errors.append(exc)
                    stop_reading.set()
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        threads = (
            threading.Thread(
                target=read_capped,
                args=(process.stdout, stdout, "stdout"),
                name="expanded-route-provider-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=read_capped,
                args=(process.stderr, stderr, "stderr"),
                name="expanded-route-provider-stderr",
                daemon=True,
            ),
            threading.Thread(
                target=write_request,
                name="expanded-route-provider-stdin",
                daemon=True,
            ),
        )
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + \
            _EXPANDED_ROUTE_PROVIDER_TIMEOUT_SECONDS
        timed_out = False
        try:
            while process.poll() is None:
                if overflow or reader_errors:
                    ExpandedRouteProviderClient._terminate_and_reap(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    ExpandedRouteProviderClient._terminate_and_reap(process)
                    break
                try:
                    process.wait(timeout=min(remaining, 0.05))
                except subprocess.TimeoutExpired:
                    continue
            if process.poll() is not None:
                process.wait()
        finally:
            if timed_out or overflow or reader_errors:
                stop_reading.set()
            for thread in threads:
                thread.join(timeout=1)
            if any(thread.is_alive() for thread in threads):
                stop_reading.set()
            for pipe in (process.stdin, process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=1)

        if timed_out:
            raise subprocess.TimeoutExpired(
                command, _EXPANDED_ROUTE_PROVIDER_TIMEOUT_SECONDS,
                output=bytes(stdout), stderr=bytes(stderr))
        if overflow:
            raise _ExpandedRouteProviderTransportOverflow(overflow[0])
        if reader_errors:
            error = reader_errors[0]
            if isinstance(error, OSError):
                raise error
            raise OSError("expanded-route provider transport failed") from error
        if any(thread.is_alive() for thread in threads):
            raise OSError("expanded-route provider transport did not close")
        return subprocess.CompletedProcess(
            command, int(process.returncode), bytes(stdout), bytes(stderr))

    @staticmethod
    def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
        """Terminate one provider and synchronously reap it, escalating once."""
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        else:
            process.wait()

    def _installation(self) -> dict[str, Any]:
        try:
            state = expanded_route_authority_provider._validate_installation(
                self._locator_path, execution_path=self._package_path)
        except expanded_route_authority_provider.ProviderError as exc:
            raise TerminalTruthError(
                "provider-provenance", exc.detail) from exc
        if str(state["package_path"]) != self._package_path or \
                Path(state["root"]) != self._authority_root or \
                state["locator_fingerprint"] != self._locator_fingerprint:
            raise TerminalTruthError(
                "provider-provenance",
                "expanded-route provider installation identity changed",
            )
        return state

    def authorize(
        self,
        request: Mapping[str, Any],
        approval: Mapping[str, Any],
    ) -> ExpandedRouteProviderReceipt:
        """Return only an authenticated live result from the exact package."""
        self._installation()
        try:
            payload = _canonical_bytes({
                "request": dict(request), "approval": dict(approval),
            }) + b"\n"
        except (TypeError, ValueError) as exc:
            raise TerminalTruthError(
                "provider-request",
                "expanded-route provider request is not canonical JSON",
            ) from exc
        if len(payload) > _EXPANDED_ROUTE_PROVIDER_MAX_INPUT_BYTES:
            raise TerminalTruthError(
                "provider-request",
                "expanded-route provider request exceeds transport limit",
            )
        executable = Path(sys.executable).resolve(strict=True)
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:" + str(executable.parent),
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
        }
        argv = (
            str(executable), "-I", self._package_path, "authorize",
            "--locator", self._locator_path,
        )
        try:
            completed = self._run_provider(
                argv, cwd=self._authority_root, environment=environment,
                payload=payload)
        except _ExpandedRouteProviderTransportOverflow as exc:
            raise TerminalTruthError(
                "provider-transport",
                f"expanded-route provider {exc.stream} output exceeds "
                "transport limit",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TerminalTruthError(
                "provider-timeout",
                "expanded-route provider exceeded its execution deadline",
            ) from exc
        except OSError as exc:
            raise TerminalTruthError(
                "provider-process",
                "expanded-route provider could not be launched",
            ) from exc
        stdout = completed.stdout
        stderr = completed.stderr
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            raise TerminalTruthError(
                "provider-transport",
                "expanded-route provider transport returned non-byte output",
            )
        if len(stdout) > _EXPANDED_ROUTE_PROVIDER_MAX_OUTPUT_BYTES or \
                len(stderr) > _EXPANDED_ROUTE_PROVIDER_MAX_OUTPUT_BYTES or \
                len(stdout) + len(stderr) > \
                _EXPANDED_ROUTE_PROVIDER_MAX_COMBINED_OUTPUT_BYTES:
            raise TerminalTruthError(
                "provider-transport",
                "expanded-route provider output exceeds transport limit",
            )
        if completed.returncode != 0:
            raise TerminalTruthError(
                "provider-process",
                "expanded-route provider rejected the request",
            )
        if stderr or not stdout.endswith(b"\n") or stdout.count(b"\n") != 1:
            raise TerminalTruthError(
                "provider-output",
                "expanded-route provider terminal output is malformed",
            )
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TerminalTruthError(
                "provider-output",
                "expanded-route provider terminal output is unreadable",
            ) from exc
        if not isinstance(decoded, Mapping):
            raise TerminalTruthError(
                "provider-output",
                "expanded-route provider terminal output is not canonical",
            )
        try:
            canonical_output = _canonical_bytes(decoded) + b"\n"
        except (TypeError, ValueError) as exc:
            raise TerminalTruthError(
                "provider-output",
                "expanded-route provider terminal output is not canonical",
            ) from exc
        if canonical_output != stdout:
            raise TerminalTruthError(
                "provider-output",
                "expanded-route provider terminal output is not canonical",
            )
        if decoded.get("schema") != \
                expanded_route_authority_provider.CONSUMPTION_SCHEMA or \
                decoded.get("provider_protocol_version") != \
                expanded_route_authority_provider.PROTOCOL_VERSION:
            raise TerminalTruthError(
                "provider-protocol",
                "expanded-route provider terminal protocol mismatches",
            )
        self._installation()
        try:
            authenticated = \
                expanded_route_authority_provider._authenticate_terminal_receipt(
                    self._locator_path, request, decoded)
        except expanded_route_authority_provider.ProviderError as exc:
            raise TerminalTruthError(
                "provider-authentication", exc.detail) from exc
        request_fingerprint = _digest(request)
        receipt = ExpandedRouteProviderReceipt(
            authenticated,
            client=self,
            request_fingerprint=request_fingerprint,
            token=_EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN,
        )
        private_seal = hmac.new(
            self._coordinator.orchestrator_issuer._secret,
            _canonical_bytes({
                "request_fingerprint": request_fingerprint,
                "provider_receipt": authenticated,
                "locator_fingerprint": self._locator_fingerprint,
            }),
            hashlib.sha256,
        ).hexdigest()
        self._receipt_seals[id(receipt)] = (receipt, private_seal)
        return receipt

    def assert_authenticated(
        self,
        receipt: ExpandedRouteProviderReceipt,
        request: Mapping[str, Any],
    ) -> None:
        """Reject copied, reconstructed, cross-client, or mutated receipts."""
        request_fingerprint = _digest(request)
        if not isinstance(receipt, ExpandedRouteProviderReceipt) or \
                receipt._token is not _EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN or \
                receipt._client is not self or \
                receipt._request_fingerprint != request_fingerprint:
            raise TerminalTruthError(
                "provider-authentication",
                "live expanded-route provider receipt is required",
            )
        stored = self._receipt_seals.get(id(receipt))
        expected = hmac.new(
            self._coordinator.orchestrator_issuer._secret,
            _canonical_bytes({
                "request_fingerprint": request_fingerprint,
                "provider_receipt": dict(receipt),
                "locator_fingerprint": self._locator_fingerprint,
            }),
            hashlib.sha256,
        ).hexdigest()
        if stored is None or stored[0] is not receipt or \
                not hmac.compare_digest(stored[1], expected):
            raise TerminalTruthError(
                "provider-authentication",
                "expanded-route provider receipt authentication changed",
            )


class SelectorExecutionReceipt(_ImmutableReceipt):
    """Content-addressed result minted only by a terminal coordinator run."""

    __slots__ = ("_coordinator", "_snapshot", "_path", "_token")

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        coordinator: "TerminalCoordinator",
        snapshot: delivery_ports.TrustedGitSnapshot,
        path: Path,
        token: object,
    ) -> None:
        if token is not _SELECTOR_RECEIPT_TOKEN:
            raise TypeError("selector receipts are terminal-coordinator-produced")
        super().__init__(value)
        self._coordinator = coordinator
        self._snapshot = snapshot
        self._path = path
        self._token = token

    def __reduce__(self):
        raise TypeError("live selector execution receipts are not serializable")


class ExactCandidateExportReceipt(_ImmutableReceipt):
    """Prepared successor retaining live coordinator and Git bindings."""

    __slots__ = (
        "_coordinator", "_snapshot", "_selector_receipts",
        "_surface_documents", "_path", "_token",
    )

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        coordinator: "TerminalCoordinator",
        snapshot: delivery_ports.TrustedGitSnapshot,
        selector_receipts: Sequence[SelectorExecutionReceipt],
        surface_documents: Mapping[str, Mapping[str, Any]],
        path: Path,
        token: object,
    ) -> None:
        if token is not _EXACT_CANDIDATE_RECEIPT_TOKEN:
            raise TypeError("candidate exports are terminal-coordinator-produced")
        super().__init__(value)
        self._coordinator = coordinator
        self._snapshot = snapshot
        self._selector_receipts = tuple(selector_receipts)
        self._surface_documents = dict(surface_documents)
        self._path = path
        self._token = token

    def __reduce__(self):
        raise TypeError("live exact-candidate exports are not serializable")


class TerminalCoordinator:
    """Own the immutable terminal bundle store and its one CAS head."""

    def __init__(
        self,
        authority_root: str | Path,
        *,
        exports_root: str | Path | None = None,
        orchestrator_issuer: OrchestratorIssuer | None = None,
        _recover_authority: bool = False,
    ):
        self._root = Path(authority_root).resolve()
        self._exports_root = (
            Path(exports_root).resolve()
            if exports_root is not None
            else self._root.parent / "exports" / "terminal" / "r0013"
        )
        self._lock = threading.RLock()
        self._issuer = self._bind_orchestrator_issuer(
            orchestrator_issuer, recover_authority=_recover_authority
        )
        # Live object identity and issuer-keyed seals are intentionally held by
        # the coordinator, not by caller-visible receipt objects or files.
        self._selector_receipt_seals: dict[int, tuple[object, str]] = {}
        self._candidate_export_seals: dict[
            int, tuple[
                object, str, object, tuple[str, ...], tuple[str, ...]
            ]
        ] = {}

    @classmethod
    def recover(
        cls,
        authority_root: str | Path,
        *,
        exports_root: str | Path | None = None,
    ) -> "TerminalCoordinator":
        """Rebind the orchestrator from root-private durable custody."""
        return cls(
            authority_root,
            exports_root=exports_root,
            _recover_authority=True,
        )

    @property
    def orchestrator_issuer(self) -> OrchestratorIssuer:
        if self._issuer is None:
            raise TerminalTruthError(
                "unauthorized", "coordinator is not the authority-root orchestrator"
            )
        return self._issuer

    def expanded_route_provider_client(
        self, locator_path: str,
    ) -> ExpandedRouteProviderClient:
        """Bind the protected provider selected by this orchestrator."""
        if self._issuer is None:
            raise TerminalTruthError(
                "unauthorized", "bound orchestrator authority is required")
        return ExpandedRouteProviderClient(
            locator_path,
            coordinator=self,
            token=_EXPANDED_ROUTE_PROVIDER_RECEIPT_TOKEN,
        )

    @property
    def issuer_path(self) -> Path:
        return self._root / "issuer.json"

    @property
    def _issuer_key_path(self) -> Path:
        return self._root / ".issuer.key"

    def _bind_orchestrator_issuer(
        self,
        supplied: OrchestratorIssuer | None,
        *,
        recover_authority: bool,
    ) -> OrchestratorIssuer | None:
        """Claim a new root or explicitly recover its root-private issuer."""
        self._root.mkdir(parents=True, exist_ok=True)
        with self._authority_lock():
            if self.issuer_path.exists():
                try:
                    payload = json.loads(self.issuer_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise TerminalTruthError(
                        "issuer", "terminal authority issuer binding is unreadable"
                    ) from exc
                if not isinstance(payload, Mapping) or set(payload) != _ISSUER_FIELDS or \
                        payload.get("schema") != _ISSUER_SCHEMA or \
                        not _FINGERPRINT.fullmatch(
                            str(payload.get("issuer_fingerprint") or "")
                        ):
                    raise TerminalTruthError(
                        "issuer", "terminal authority issuer binding is invalid"
                    )
                issuer = supplied
                if issuer is None and recover_authority:
                    try:
                        issuer = OrchestratorIssuer(
                            _TERMINAL_RECEIPT_TOKEN,
                            self._issuer_key_path.read_bytes(),
                        )
                    except (OSError, TypeError) as exc:
                        raise TerminalTruthError(
                            "issuer", "durable terminal authority is unavailable"
                        ) from exc
                if not isinstance(issuer, OrchestratorIssuer) or \
                        issuer.fingerprint != payload["issuer_fingerprint"]:
                    return None
                return issuer
            if supplied is not None and not isinstance(supplied, OrchestratorIssuer):
                raise TerminalTruthError("issuer", "orchestrator issuer is invalid")
            issuer = supplied
            if issuer is None and recover_authority and self._issuer_key_path.exists():
                try:
                    issuer = OrchestratorIssuer(
                        _TERMINAL_RECEIPT_TOKEN,
                        self._issuer_key_path.read_bytes(),
                    )
                except (OSError, TypeError) as exc:
                    raise TerminalTruthError(
                        "issuer", "durable terminal authority is unavailable"
                    ) from exc
            issuer = issuer or OrchestratorIssuer(_TERMINAL_RECEIPT_TOKEN)
            self._write_immutable(self._issuer_key_path, issuer._secret)
            payload = {
                "schema": _ISSUER_SCHEMA,
                "issuer_fingerprint": issuer.fingerprint,
            }
            self._write_immutable(self.issuer_path, _canonical_bytes(payload))
            self._fsync_directory(self._root)
            return issuer

    @property
    def head_path(self) -> Path:
        return self._root / "head.json"

    def projection_path(self, surface_id: str) -> Path:
        if surface_id not in SURFACE_IDS:
            raise TerminalTruthError("surface", f"unknown terminal surface: {surface_id}")
        return self._root / "projections" / f"{surface_id}.json"

    def export_path(self, full_source_sha: str) -> Path:
        return self._exports_root / f"{_object_id(full_source_sha, 'full_source_sha')}.json"

    def _private_receipt_seal(self, payload: bytes) -> str:
        if self._issuer is None:
            raise TerminalTruthError(
                "unauthorized", "bound orchestrator authority is required"
            )
        return hmac.new(self._issuer._secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _assert_snapshot_unchanged(
        snapshot: delivery_ports.TrustedGitSnapshot,
    ) -> None:
        try:
            snapshot._inspector.assert_unchanged(snapshot)
        except delivery_ports.DeliveryPortError as exc:
            raise TerminalTruthError("candidate", str(exc)) from exc

    def _load_snapshot_candidate_template(
        self,
        snapshot: delivery_ports.TrustedGitSnapshot,
    ) -> dict[str, Any]:
        """Load the sole selector inventory from tracked bytes at the live SHA."""
        if not isinstance(snapshot, delivery_ports.TrustedGitSnapshot):
            raise TerminalTruthError("candidate", "trusted Git snapshot is required")
        self._assert_snapshot_unchanged(snapshot)
        expected_sha256 = snapshot.evidence_sha256.get(
            EXACT_CANDIDATE_TEMPLATE_PATH
        )
        if expected_sha256 is None:
            raise TerminalTruthError(
                "candidate", "canonical exact-candidate template is not sealed"
            )
        path = snapshot.root / EXACT_CANDIDATE_TEMPLATE_PATH
        try:
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != expected_sha256:
                raise TerminalTruthError(
                    "candidate", "canonical exact-candidate template changed"
                )
            decoded = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TerminalTruthError(
                "candidate", "canonical exact-candidate template is unreadable"
            ) from exc
        template = _normalize_exact_candidate_template(decoded)
        for selector in template["required_selectors"]:
            self._selector_path(selector)
        self._assert_snapshot_unchanged(snapshot)
        return template

    def cleanup_receipt_path(self, bundle_fingerprint: str) -> Path:
        return self._root / "cleanup" / f"{_fingerprint(bundle_fingerprint, 'bundle_fingerprint')}.json"

    @staticmethod
    def _selector_path(selector: str) -> str:
        value = _text(selector, "selector")
        parts = value.split("::")
        if len(parts) not in {2, 3} or any(not part for part in parts):
            raise TerminalTruthError(
                "selector", "selector must be an exact pytest node identity"
            )
        relative = parts[0]
        path = Path(relative)
        if path.is_absolute() or path.suffix != ".py" or \
                "\\" in relative or any(part in {"", ".", ".."} for part in path.parts):
            raise TerminalTruthError(
                "selector", "selector test path must be safe and repository-relative"
            )
        if any(not symbol.isidentifier() for symbol in parts[1:]):
            raise TerminalTruthError("selector", "selector symbol is not exact")
        return path.as_posix()

    @staticmethod
    def _selector_exists(source: bytes, selector: str) -> bool:
        try:
            tree = ast.parse(source.decode("utf-8"), filename=selector.split("::", 1)[0])
        except (SyntaxError, UnicodeError) as exc:
            raise TerminalTruthError("selector", "selector source is not collectable") from exc
        symbols = selector.split("::")[1:]
        for node in tree.body:
            if len(symbols) == 1 and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name == symbols[0]:
                return True
            if len(symbols) == 2 and isinstance(node, ast.ClassDef) and \
                    node.name == symbols[0]:
                return any(
                    isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == symbols[1]
                    for member in node.body
                )
        return False

    def _run_selector(
        self,
        snapshot: delivery_ports.TrustedGitSnapshot,
        argv: Sequence[str],
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        """Private process boundary overridden only by hermetic unit fixtures."""
        return subprocess.run(
            list(argv),
            cwd=snapshot.root,
            env=dict(environment),
            capture_output=True,
            check=False,
        )

    def execute_candidate_selectors(
        self,
        snapshot: delivery_ports.TrustedGitSnapshot,
        selectors: Sequence[str],
    ) -> tuple[SelectorExecutionReceipt, ...]:
        """Execute and durably mint the exact selector set at one clean SHA."""
        if not isinstance(snapshot, delivery_ports.TrustedGitSnapshot):
            raise TerminalTruthError("selector", "trusted Git snapshot is required")
        try:
            snapshot._inspector.assert_unchanged(snapshot)
        except delivery_ports.DeliveryPortError as exc:
            raise TerminalTruthError("candidate", str(exc)) from exc
        normalized = tuple(_text(value, "selector") for value in selectors)
        if not normalized or len(normalized) != len(set(normalized)):
            raise TerminalTruthError("selector", "selector inventory is empty or duplicated")
        executable = Path(sys.executable).resolve(strict=True)
        executable_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:" + str(executable.parent),
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
        environment_fingerprint = _digest(environment)
        receipts: list[SelectorExecutionReceipt] = []
        for selector in normalized:
            relative = self._selector_path(selector)
            expected_source_sha256 = snapshot.evidence_sha256.get(relative)
            if expected_source_sha256 is None:
                raise TerminalTruthError(
                    "selector", "selector source is not trusted candidate evidence"
                )
            source = (snapshot.root / relative).read_bytes()
            if hashlib.sha256(source).hexdigest() != expected_source_sha256 or \
                    not self._selector_exists(source, selector):
                raise TerminalTruthError(
                    "selector", "selector source changed or selector is missing"
                )
            try:
                snapshot._inspector.assert_unchanged(snapshot)
            except delivery_ports.DeliveryPortError as exc:
                raise TerminalTruthError("candidate", str(exc)) from exc
            argv = (
                str(executable), "-m", "pytest", "-q", "-p",
                "no:cacheprovider", selector,
            )
            completed = self._run_selector(snapshot, argv, environment)
            try:
                snapshot._inspector.assert_unchanged(snapshot)
            except delivery_ports.DeliveryPortError as exc:
                raise TerminalTruthError("candidate", str(exc)) from exc
            if completed.returncode != 0:
                raise TerminalTruthError(
                    "selector", f"selector did not pass: {selector}"
                )
            stdout = bytes(completed.stdout or b"")
            stderr = bytes(completed.stderr or b"")
            unsigned = {
                "schema": SELECTOR_EXECUTION_SCHEMA,
                "status": "passed",
                "producer": "taskplane.terminal-coordinator-selector-runner/v1",
                "candidate_sha": snapshot.head_sha,
                "repository_snapshot_fingerprint": snapshot.fingerprint,
                "git_executable_sha256": snapshot.git_executable_sha256,
                "git_environment_fingerprint": snapshot.environment_fingerprint,
                "selector": selector,
                "test_source_sha256": expected_source_sha256,
                "argv": list(argv),
                "exit_code": 0,
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "output_sha256": hashlib.sha256(stdout + b"\0" + stderr).hexdigest(),
            }
            receipt = _seal(unsigned)
            receipt_path = (
                self._root / "selector-executions" / f"{receipt['fingerprint']}.json"
            )
            self._write_immutable(receipt_path, _canonical_bytes(receipt))
            live_receipt = SelectorExecutionReceipt(
                receipt,
                coordinator=self,
                snapshot=snapshot,
                path=receipt_path,
                token=_SELECTOR_RECEIPT_TOKEN,
            )
            self._selector_receipt_seals[id(live_receipt)] = (
                live_receipt,
                self._private_receipt_seal(_canonical_bytes(live_receipt)),
            )
            receipts.append(live_receipt)
        return tuple(receipts)

    def _validate_selector_receipts(
        self,
        snapshot: delivery_ports.TrustedGitSnapshot,
        selectors: Sequence[str],
        receipts: Sequence[SelectorExecutionReceipt],
    ) -> list[dict[str, Any]]:
        required = tuple(selectors)
        if len(receipts) != len(required):
            raise TerminalTruthError("selector", "all required selectors must execute")
        by_selector: dict[str, SelectorExecutionReceipt] = {}
        for receipt in receipts:
            if not isinstance(receipt, SelectorExecutionReceipt) or \
                    receipt._token is not _SELECTOR_RECEIPT_TOKEN or \
                    receipt._coordinator is not self or \
                    receipt._snapshot is not snapshot:
                raise TerminalTruthError(
                    "selector", "live coordinator-produced selector receipt is required"
                )
            seal = self._selector_receipt_seals.get(id(receipt))
            if seal is None or seal[0] is not receipt or not hmac.compare_digest(
                seal[1], self._private_receipt_seal(_canonical_bytes(receipt))
            ):
                raise TerminalTruthError(
                    "selector", "selector receipt coordinator seal is invalid"
                )
            selector = str(receipt.get("selector") or "")
            if selector in by_selector:
                raise TerminalTruthError("selector", "selector receipt is replayed")
            by_selector[selector] = receipt
        if tuple(by_selector) != required:
            raise TerminalTruthError("selector", "selector receipts are incomplete or reordered")
        rows: list[dict[str, Any]] = []
        for selector in required:
            receipt = by_selector[selector]
            if set(receipt) != _SELECTOR_EXECUTION_FIELDS or \
                    receipt.get("schema") != SELECTOR_EXECUTION_SCHEMA or \
                    receipt.get("status") != "passed" or \
                    receipt.get("producer") != \
                    "taskplane.terminal-coordinator-selector-runner/v1" or \
                    receipt.get("candidate_sha") != snapshot.head_sha or \
                    receipt.get("repository_snapshot_fingerprint") != snapshot.fingerprint or \
                    receipt.get("git_executable_sha256") != snapshot.git_executable_sha256 or \
                    receipt.get("git_environment_fingerprint") != \
                    snapshot.environment_fingerprint or \
                    receipt.get("exit_code") != 0:
                raise TerminalTruthError("selector", "selector receipt binding is invalid")
            unsigned = {key: value for key, value in receipt.items() if key != "fingerprint"}
            if receipt.get("fingerprint") != _digest(unsigned):
                raise TerminalTruthError("selector", "selector receipt was redigested")
            try:
                persisted = receipt._path.read_bytes()
            except OSError as exc:
                raise TerminalTruthError("selector", "selector receipt is unavailable") from exc
            if persisted != _canonical_bytes(receipt):
                raise TerminalTruthError("selector", "selector receipt bytes were tampered")
            rows.append(
                {
                    "selector": selector,
                    "candidate_sha": snapshot.head_sha,
                    "receipt_fingerprint": receipt["fingerprint"],
                    "output_sha256": receipt["output_sha256"],
                }
            )
        try:
            snapshot._inspector.assert_unchanged(snapshot)
        except delivery_ports.DeliveryPortError as exc:
            raise TerminalTruthError("candidate", str(exc)) from exc
        return rows

    def compose_exact_candidate_export(
        self,
        *,
        snapshot: delivery_ports.TrustedGitSnapshot,
        template: Mapping[str, Any],
        surface_documents: Mapping[str, Mapping[str, Any]],
    ) -> ExactCandidateExportReceipt:
        """Production consumer for one non-authoritative exact-SHA successor."""
        canonical_template = self._load_snapshot_candidate_template(snapshot)
        if not isinstance(template, Mapping) or dict(template) != canonical_template:
            raise TerminalTruthError(
                "candidate", "caller template differs from canonical tracked template"
            )
        expected_state = dict(_EXACT_CANDIDATE_EVIDENCE_STATE)
        selectors = tuple(canonical_template["required_selectors"])
        receipts = self.execute_candidate_selectors(snapshot, selectors)
        selector_rows = self._validate_selector_receipts(
            snapshot, selectors, receipts
        )
        if not isinstance(surface_documents, Mapping) or \
                tuple(surface_documents) != SURFACE_IDS:
            raise TerminalTruthError("partial", "all terminal surfaces are required")
        identity: Mapping[str, Any] | None = None
        surface_rows: dict[str, dict[str, str]] = {}
        for surface_id in SURFACE_IDS:
            document = surface_documents[surface_id]
            candidate_identity = document.get("identity") \
                if isinstance(document, Mapping) else None
            if not isinstance(candidate_identity, Mapping) or \
                    candidate_identity.get("full_source_sha") != snapshot.head_sha:
                raise TerminalTruthError("stale", f"{surface_id} surface names another SHA")
            identity = candidate_identity if identity is None else identity
            normalized = validate_terminal_surface(
                document,
                expected_surface_id=surface_id,
                expected_identity=identity,
            )
            surface_rows[surface_id] = {
                "candidate_sha": snapshot.head_sha,
                "sha256": _digest(normalized),
            }
        candidate = _seal(
            {
                "schema": EXACT_CANDIDATE_SUCCESSOR_SCHEMA,
                "requirement_id": "R-0013",
                "finding_id": "H-32",
                "status": "prepared-not-authoritative",
                "candidate_sha": snapshot.head_sha,
                "template_sha256": _digest(canonical_template),
                "repository_snapshot_fingerprint": snapshot.fingerprint,
                "surfaces": surface_rows,
                "selectors": selector_rows,
                "evidence_state": expected_state,
            }
        )
        path = self._root / "candidate-exports" / f"{candidate['fingerprint']}.json"
        self._write_immutable(path, _canonical_bytes(candidate))
        live_receipt = ExactCandidateExportReceipt(
            candidate,
            coordinator=self,
            snapshot=snapshot,
            selector_receipts=receipts,
            surface_documents=surface_documents,
            path=path,
            token=_EXACT_CANDIDATE_RECEIPT_TOKEN,
        )
        self._candidate_export_seals[id(live_receipt)] = (
            live_receipt,
            self._private_receipt_seal(_canonical_bytes(live_receipt)),
            snapshot,
            selectors,
            tuple(receipt["fingerprint"] for receipt in receipts),
        )
        return live_receipt

    def validate_exact_candidate_export(
        self,
        receipt: Mapping[str, Any],
        *,
        expected_sha: str,
        expected_template_sha256: str,
    ) -> dict[str, Any]:
        """Revalidate the live Git state and every immutable producer receipt."""
        if not isinstance(receipt, ExactCandidateExportReceipt) or \
                receipt._token is not _EXACT_CANDIDATE_RECEIPT_TOKEN or \
                receipt._coordinator is not self:
            raise TerminalTruthError("candidate", "live exact-candidate receipt is required")
        snapshot = receipt._snapshot
        canonical_template = self._load_snapshot_candidate_template(snapshot)
        canonical_template_sha256 = _digest(canonical_template)
        if _fingerprint(
            expected_template_sha256, "expected_template_sha256"
        ) != canonical_template_sha256:
            raise TerminalTruthError(
                "candidate", "expected template is not the canonical tracked template"
            )
        required_selectors = tuple(canonical_template["required_selectors"])
        private_seal = self._candidate_export_seals.get(id(receipt))
        selector_fingerprints = tuple(
            str(selector_receipt.get("fingerprint") or "")
            for selector_receipt in receipt._selector_receipts
        )
        if private_seal is None or private_seal[0] is not receipt or \
                not hmac.compare_digest(
                    private_seal[1],
                    self._private_receipt_seal(_canonical_bytes(receipt)),
                ) or private_seal[2] is not snapshot or \
                private_seal[3] != required_selectors or \
                private_seal[4] != selector_fingerprints:
            raise TerminalTruthError(
                "candidate", "exact-candidate coordinator seal is invalid"
            )
        if set(receipt) != _EXACT_CANDIDATE_FIELDS or \
                receipt.get("schema") != EXACT_CANDIDATE_SUCCESSOR_SCHEMA or \
                receipt.get("status") != "prepared-not-authoritative" or \
                receipt.get("candidate_sha") != _object_id(expected_sha, "expected_sha") or \
                receipt.get("candidate_sha") != snapshot.head_sha or \
                receipt.get("template_sha256") != canonical_template_sha256 or \
                receipt.get("repository_snapshot_fingerprint") != snapshot.fingerprint:
            raise TerminalTruthError("candidate", "exact-candidate binding is stale or invalid")
        unsigned = {key: value for key, value in receipt.items() if key != "fingerprint"}
        if receipt.get("fingerprint") != _digest(unsigned):
            raise TerminalTruthError("candidate", "exact-candidate receipt was redigested")
        try:
            persisted = receipt._path.read_bytes()
        except OSError as exc:
            raise TerminalTruthError("candidate", "exact-candidate receipt is unavailable") from exc
        if persisted != _canonical_bytes(receipt):
            raise TerminalTruthError("candidate", "exact-candidate receipt was tampered")
        selector_rows = receipt.get("selectors")
        if not isinstance(selector_rows, list) or \
                any(not isinstance(row, Mapping) for row in selector_rows):
            raise TerminalTruthError(
                "selector", "candidate selector rows are invalid"
            )
        selectors = tuple(row.get("selector") for row in selector_rows)
        if selectors != required_selectors:
            raise TerminalTruthError(
                "selector",
                "candidate selectors differ from canonical required inventory",
            )
        rebuilt_rows = self._validate_selector_receipts(
            snapshot, required_selectors, receipt._selector_receipts
        )
        if rebuilt_rows != selector_rows:
            raise TerminalTruthError("selector", "candidate selector bindings changed")
        if tuple(receipt._surface_documents) != SURFACE_IDS:
            raise TerminalTruthError("partial", "candidate surface evidence is unavailable")
        for surface_id in SURFACE_IDS:
            document = receipt._surface_documents[surface_id]
            binding = receipt["surfaces"].get(surface_id)
            if not isinstance(binding, Mapping) or \
                    binding.get("candidate_sha") != snapshot.head_sha or \
                    binding.get("sha256") != _digest(document):
                raise TerminalTruthError("mixed", "candidate surface binding changed")
        return dict(receipt)

    @contextmanager
    def _authority_lock(self):
        """Serialize CAS/reconciliation across coordinator processes."""
        self._root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._root / ".authority.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def issue_capability(
        self,
        *,
        run_id: str,
        full_source_sha: str,
        design_fingerprint: str,
        plan_fingerprint: str,
        expected_predecessor_fingerprint: str,
        operation_id: str,
    ) -> FinalizationCapability:
        """Issue authority from the root-owned coordinator instance only."""
        if self._issuer is None:
            raise TerminalTruthError(
                "unauthorized", "only the bound root orchestrator may issue capability"
            )
        return FinalizationCapability(
            issuer=self._issuer,
            run_id=_text(run_id, "run_id"),
            full_source_sha=_object_id(full_source_sha, "full_source_sha"),
            design_fingerprint=_fingerprint(design_fingerprint, "design_fingerprint"),
            plan_fingerprint=_fingerprint(plan_fingerprint, "plan_fingerprint"),
            expected_predecessor_fingerprint=_fingerprint(
                expected_predecessor_fingerprint, "expected_predecessor_fingerprint"
            ),
            operation_id=_text(operation_id, "operation_id"),
        )

    def _authorize(
        self,
        capability: FinalizationCapability,
        *,
        bundle: Mapping[str, Any],
    ) -> None:
        if self._issuer is None or \
                not isinstance(capability, FinalizationCapability) or \
                capability._issuer is not self._issuer:
            raise TerminalTruthError("unauthorized", "root finalization capability is required")
        identity = bundle.get("identity") if isinstance(bundle, Mapping) else None
        normalized = normalize_terminal_identity(identity)
        bindings = (
            capability.run_id == bundle.get("run_id"),
            capability.operation_id == bundle.get("operation_id"),
            capability.full_source_sha == normalized["full_source_sha"],
            capability.design_fingerprint == normalized["design_fingerprint"],
            capability.plan_fingerprint == normalized["plan_fingerprint"],
            capability.expected_predecessor_fingerprint
            == normalized["predecessor_fingerprint"],
        )
        if not all(bindings):
            raise TerminalTruthError("unauthorized", "finalization capability binding mismatch")

    def prepare_delivery(
        self,
        *,
        run_id: str,
        operation_id: str,
        identity: Mapping[str, Any],
        surfaces: Mapping[str, Mapping[str, Any]],
        candidate_wiring_receipt: Mapping[str, Any],
        fault_at: str | None = None,
    ) -> PreparedTerminalDelivery:
        """Validate and content-address exactly eight immutable surface bytes."""
        normalized_identity = normalize_terminal_identity(identity)
        if not isinstance(surfaces, Mapping) or tuple(surfaces) != SURFACE_IDS:
            raise TerminalTruthError(
                "partial", "terminal delivery requires exactly eight ordered surfaces"
            )
        try:
            wiring = wiring_closure.validate_candidate_checkout_receipt(
                candidate_wiring_receipt,
                expected_head_sha=normalized_identity["full_source_sha"],
                expected_requirement_id=normalized_identity["requirement_id"],
            )
        except wiring_closure.WiringClosureError as exc:
            raise TerminalTruthError("wiring", str(exc)) from exc
        if wiring["fingerprint"] != normalized_identity["candidate_wiring_fingerprint"]:
            raise TerminalTruthError("wiring", "terminal identity names another wiring receipt")
        surface_bytes: dict[str, bytes] = {}
        surface_digests: dict[str, str] = {}
        for surface_id in SURFACE_IDS:
            projection = validate_terminal_surface(
                surfaces[surface_id],
                expected_surface_id=surface_id,
                expected_identity=normalized_identity,
            )
            encoded = _canonical_bytes(projection)
            surface_bytes[surface_id] = encoded
            surface_digests[surface_id] = hashlib.sha256(encoded).hexdigest()
            if fault_at == f"prepare:{surface_id}":
                raise TerminalTruthError("fault", f"fault injected at prepare:{surface_id}")
        bundle = _seal(
            {
                "schema": TERMINAL_BUNDLE_SCHEMA,
                "run_id": _text(run_id, "run_id"),
                "operation_id": _text(operation_id, "operation_id"),
                "identity": normalized_identity,
                "surface_ids": list(SURFACE_IDS),
                "surface_digests": surface_digests,
            }
        )
        if fault_at == "prepare:bundle":
            raise TerminalTruthError("fault", "fault injected at prepare:bundle")
        return PreparedTerminalDelivery(
            bundle=bundle,
            bundle_bytes=_canonical_bytes(bundle),
            surface_bytes=surface_bytes,
            candidate_wiring_receipt=wiring,
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise TerminalTruthError("collision", f"immutable evidence collision: {path.name}")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise TerminalTruthError(
                        "collision", f"immutable evidence collision: {path.name}"
                    )
            TerminalCoordinator._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        TerminalCoordinator._fsync_directory(path.parent)

    def _load_head(self) -> dict[str, Any] | None:
        if not self.head_path.exists():
            return None
        try:
            value = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TerminalTruthError("head", "terminal head is unreadable") from exc
        if not isinstance(value, Mapping) or set(value) != _HEAD_FIELDS or \
                value.get("schema") != _HEAD_SCHEMA:
            raise TerminalTruthError("head", "terminal head is invalid")
        return dict(value)

    def _validate_prepared(self, prepared: PreparedTerminalDelivery) -> None:
        if not isinstance(prepared, PreparedTerminalDelivery):
            raise TerminalTruthError("prepared", "prepared terminal delivery is required")
        bundle = prepared.bundle
        if not isinstance(bundle, Mapping) or set(bundle) != _BUNDLE_FIELDS or \
                bundle.get("schema") != TERMINAL_BUNDLE_SCHEMA:
            raise TerminalTruthError("prepared", "terminal bundle fields are invalid")
        unsigned = {key: value for key, value in bundle.items() if key != "fingerprint"}
        if bundle.get("fingerprint") != _digest(unsigned) or \
                prepared.bundle_bytes != _canonical_bytes(bundle):
            raise TerminalTruthError("prepared", "terminal bundle fingerprint mismatch")
        if tuple(bundle.get("surface_ids") or ()) != SURFACE_IDS or \
                set(prepared.surface_bytes) != set(SURFACE_IDS):
            raise TerminalTruthError("partial", "prepared terminal surfaces are incomplete")
        expected_digests = {
            surface_id: hashlib.sha256(prepared.surface_bytes[surface_id]).hexdigest()
            for surface_id in SURFACE_IDS
        }
        if bundle.get("surface_digests") != expected_digests:
            raise TerminalTruthError("partial", "prepared terminal digest set is contradictory")
        try:
            wiring = wiring_closure.validate_candidate_checkout_receipt(
                prepared.candidate_wiring_receipt,
                expected_head_sha=bundle["identity"]["full_source_sha"],
                expected_requirement_id=bundle["identity"]["requirement_id"],
            )
        except wiring_closure.WiringClosureError as exc:
            raise TerminalTruthError("wiring", str(exc)) from exc
        if wiring["fingerprint"] != \
                bundle["identity"]["candidate_wiring_fingerprint"]:
            raise TerminalTruthError(
                "wiring", "prepared terminal delivery wiring authority changed"
            )

    def commit_delivery(
        self,
        capability: FinalizationCapability,
        prepared: PreparedTerminalDelivery,
        *,
        observed_head_sha: str,
        checkout_clean: bool,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        """Persist immutable bytes and CAS the sole terminal authority head."""
        self._validate_prepared(prepared)
        self._authorize(capability, bundle=prepared.bundle)
        identity = prepared.bundle["identity"]
        if _object_id(observed_head_sha, "observed_head_sha") != identity["full_source_sha"]:
            raise TerminalTruthError("stale", "candidate Git HEAD changed before finalization")
        if checkout_clean is not True:
            raise TerminalTruthError("dirty", "candidate checkout is not clean")
        bundle_fingerprint = prepared.bundle["fingerprint"]
        with self._lock, self._authority_lock():
            current = self._load_head()
            if current is not None and current["bundle_fingerprint"] == bundle_fingerprint:
                if current["operation_id"] != capability.operation_id:
                    raise TerminalTruthError("collision", "bundle operation identity collision")
                return current
            predecessor = identity["predecessor_fingerprint"]
            current_fingerprint = (
                current["bundle_fingerprint"] if current is not None else "0" * 64
            )
            if current_fingerprint != predecessor:
                raise TerminalTruthError("cas", "terminal predecessor compare-and-swap failed")
            bundle_dir = self._root / "bundles" / bundle_fingerprint
            self._write_immutable(bundle_dir / "bundle.json", prepared.bundle_bytes)
            for surface_id in SURFACE_IDS:
                self._write_immutable(
                    bundle_dir / "surfaces" / f"{surface_id}.json",
                    prepared.surface_bytes[surface_id],
                )
                if fault_at == f"fsync:{surface_id}":
                    raise TerminalTruthError("fault", f"fault injected at fsync:{surface_id}")
            self._fsync_directory(bundle_dir / "surfaces")
            self._fsync_directory(bundle_dir)
            if fault_at == "before_cas":
                raise TerminalTruthError("fault", "fault injected before terminal CAS")
            head = {
                "schema": _HEAD_SCHEMA,
                "bundle_fingerprint": bundle_fingerprint,
                "predecessor_fingerprint": predecessor,
                "operation_id": capability.operation_id,
            }
            self._replace(self.head_path, _canonical_bytes(head))
            if fault_at == "after_cas":
                raise TerminalTruthError("fault", "fault injected after terminal CAS")
            return head

    def _head_bundle(self) -> tuple[dict[str, Any], dict[str, Any], Path]:
        head = self._load_head()
        if head is None:
            raise TerminalTruthError("nonterminal", "terminal head does not exist")
        bundle_dir = self._root / "bundles" / head["bundle_fingerprint"]
        try:
            payload = (bundle_dir / "bundle.json").read_bytes()
            bundle = json.loads(payload)
        except (OSError, ValueError) as exc:
            raise TerminalTruthError("partial", "terminal head bundle is unavailable") from exc
        unsigned = ({key: value for key, value in bundle.items()
                     if key != "fingerprint"} if isinstance(bundle, Mapping) else {})
        if not isinstance(bundle, Mapping) or set(bundle) != _BUNDLE_FIELDS or \
                bundle.get("fingerprint") != _digest(unsigned) or \
                bundle.get("fingerprint") != head["bundle_fingerprint"] or \
                payload != _canonical_bytes(bundle):
            raise TerminalTruthError("mixed", "terminal head and bundle disagree")
        return head, dict(bundle), bundle_dir

    @staticmethod
    def _validate_reconciliation(
        bundle: Mapping[str, Any], receipt: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping) or set(receipt) != _RECONCILIATION_FIELDS:
            raise TerminalTruthError("mixed", "terminal reconciliation receipt is invalid")
        if receipt.get("schema") != TERMINAL_RECONCILIATION_SCHEMA or \
                receipt.get("status") != "complete" or \
                receipt.get("bundle_fingerprint") != bundle.get("fingerprint") or \
                receipt.get("surface_digests") != bundle.get("surface_digests") or \
                receipt.get("native_usage_fingerprint") != \
                bundle.get("identity", {}).get("native_usage_fingerprint"):
            raise TerminalTruthError("mixed", "terminal reconciliation receipt disagrees")
        repaired = receipt.get("repaired_surface_ids")
        if not isinstance(repaired, Sequence) or isinstance(repaired, (str, bytes)) or \
                any(item not in SURFACE_IDS for item in repaired) or \
                len(repaired) != len(set(repaired)):
            raise TerminalTruthError("mixed", "reconciliation repair set is invalid")
        unsigned = {key: value for key, value in receipt.items() if key != "fingerprint"}
        if receipt.get("fingerprint") != _digest(unsigned):
            raise TerminalTruthError("mixed", "reconciliation receipt fingerprint mismatch")
        return dict(receipt)

    def reconcile_delivery(
        self,
        capability: FinalizationCapability,
        prepared: PreparedTerminalDelivery,
        *,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        """Restore only missing/dirty derived projections from immutable bytes."""
        self._validate_prepared(prepared)
        self._authorize(capability, bundle=prepared.bundle)
        with self._lock, self._authority_lock():
            head, bundle, bundle_dir = self._head_bundle()
            if head["bundle_fingerprint"] != prepared.bundle["fingerprint"]:
                raise TerminalTruthError("stale", "prepared bundle is not terminal head")
            repaired: list[str] = []
            for surface_id in SURFACE_IDS:
                immutable = (bundle_dir / "surfaces" / f"{surface_id}.json").read_bytes()
                if hashlib.sha256(immutable).hexdigest() != \
                        bundle["surface_digests"][surface_id]:
                    raise TerminalTruthError("collision", "immutable surface digest mismatch")
                destination = self.projection_path(surface_id)
                if not destination.exists() or destination.read_bytes() != immutable:
                    self._replace(destination, immutable)
                    repaired.append(surface_id)
                if fault_at == f"reconcile:{surface_id}":
                    raise TerminalTruthError(
                        "fault", f"fault injected at reconcile:{surface_id}"
                    )
            immutable_export = (
                bundle_dir / "surfaces" / "exports_terminal_evidence.json"
            ).read_bytes()
            export_path = self.export_path(bundle["identity"]["full_source_sha"])
            if not export_path.exists() or export_path.read_bytes() != immutable_export:
                self._replace(export_path, immutable_export)
            receipt = _seal(
                {
                    "schema": TERMINAL_RECONCILIATION_SCHEMA,
                    "status": "complete",
                    "bundle_fingerprint": bundle["fingerprint"],
                    "surface_digests": dict(bundle["surface_digests"]),
                    "native_usage_fingerprint": bundle["identity"]["native_usage_fingerprint"],
                    "repaired_surface_ids": repaired,
                }
            )
            receipt_path = self._root / "reconciliation" / f"{bundle['fingerprint']}.json"
            # The authoritative receipt is deterministic across replay.  A
            # replay that needs no repair retains the first immutable receipt.
            if receipt_path.exists():
                receipt = self._validate_reconciliation(
                    bundle, json.loads(receipt_path.read_text(encoding="utf-8"))
                )
            else:
                self._write_immutable(receipt_path, _canonical_bytes(receipt))
            if fault_at == "after_reconcile":
                raise TerminalTruthError("fault", "fault injected after reconciliation")
            return receipt

    def _read_terminal_mapping(self) -> dict[str, Any]:
        """Revalidate CAS, immutable store, projections, export, and receipt."""
        _, bundle, bundle_dir = self._head_bundle()
        for surface_id in SURFACE_IDS:
            immutable = (bundle_dir / "surfaces" / f"{surface_id}.json").read_bytes()
            if hashlib.sha256(immutable).hexdigest() != \
                    bundle["surface_digests"][surface_id]:
                raise TerminalTruthError("collision", "immutable surface digest mismatch")
            projection = self.projection_path(surface_id)
            if not projection.exists() or projection.read_bytes() != immutable:
                raise TerminalTruthError("reconciling", "terminal projections are partial")
        export_path = self.export_path(bundle["identity"]["full_source_sha"])
        immutable_export = (
            bundle_dir / "surfaces" / "exports_terminal_evidence.json"
        ).read_bytes()
        if not export_path.exists() or export_path.read_bytes() != immutable_export:
            raise TerminalTruthError(
                "reconciling", "exports/terminal/r0013 exact-SHA projection is partial"
            )
        receipt_path = self._root / "reconciliation" / f"{bundle['fingerprint']}.json"
        if not receipt_path.exists():
            raise TerminalTruthError("reconciling", "terminal reconciliation is incomplete")
        receipt = self._validate_reconciliation(
            bundle, json.loads(receipt_path.read_text(encoding="utf-8"))
        )
        return {
            "schema": "taskplane.exact-sha-terminal-authority/v1",
            "status": "complete",
            "bundle": bundle,
            "reconciliation": receipt,
            "fingerprint": _digest(
                {"bundle": bundle["fingerprint"], "reconciliation": receipt["fingerprint"]}
            ),
        }

    def read_terminal_receipt(self) -> TerminalAuthorityReceipt:
        """Return authority with a live binding to the actual CAS/store/head."""
        return TerminalAuthorityReceipt(
            self._read_terminal_mapping(),
            coordinator=self,
            token=_TERMINAL_RECEIPT_TOKEN,
        )

    def cleanup_private_usage(
        self,
        capability: FinalizationCapability,
        prepared: PreparedTerminalDelivery,
        reconciliation_receipt: Mapping[str, Any],
        cleanup: Callable[[], None],
    ) -> dict[str, Any]:
        """Delete private detail only after successful complete reconciliation."""
        self._validate_prepared(prepared)
        self._authorize(capability, bundle=prepared.bundle)
        terminal = self.read_terminal_receipt()
        canonical = terminal["reconciliation"]
        if not isinstance(reconciliation_receipt, Mapping) or \
                dict(reconciliation_receipt) != canonical:
            raise TerminalTruthError("cleanup", "successful reconciliation receipt is required")
        receipt_path = self.cleanup_receipt_path(prepared.bundle["fingerprint"])
        with self._lock, self._authority_lock():
            if receipt_path.exists():
                try:
                    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise TerminalTruthError(
                        "cleanup", "cleanup receipt is unreadable"
                    ) from exc
                return self._validate_cleanup_receipt(prepared.bundle, persisted)
            cleanup()
            receipt = _seal(
                {
                    "schema": PRIVATE_USAGE_CLEANUP_SCHEMA,
                    "status": "complete",
                    "bundle_fingerprint": prepared.bundle["fingerprint"],
                    "native_usage_fingerprint": prepared.bundle["identity"]["native_usage_fingerprint"],
                }
            )
            self._write_immutable(receipt_path, _canonical_bytes(receipt))
            self._fsync_directory(receipt_path.parent)
            return receipt

    @staticmethod
    def _validate_cleanup_receipt(
        bundle: Mapping[str, Any], receipt: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping) or set(receipt) != _CLEANUP_FIELDS or \
                receipt.get("schema") != PRIVATE_USAGE_CLEANUP_SCHEMA or \
                receipt.get("status") != "complete" or \
                receipt.get("bundle_fingerprint") != bundle.get("fingerprint") or \
                receipt.get("native_usage_fingerprint") != \
                bundle.get("identity", {}).get("native_usage_fingerprint"):
            raise TerminalTruthError("cleanup", "cleanup receipt is invalid")
        unsigned = {key: value for key, value in receipt.items() if key != "fingerprint"}
        if receipt.get("fingerprint") != _digest(unsigned):
            raise TerminalTruthError("cleanup", "cleanup receipt fingerprint mismatch")
        return dict(receipt)


def finalize_terminal_delivery(
    authority_root: str | Path,
    *,
    exports_root: str | Path | None = None,
    run_id: str,
    operation_id: str,
    identity: Mapping[str, Any],
    surfaces: Mapping[str, Mapping[str, Any]],
    candidate_wiring_receipt: Mapping[str, Any],
    observed_head_sha: str,
    checkout_clean: bool,
    commit_fault_at: str | None = None,
) -> TerminalAuthorityReceipt:
    """Compose, commit, reconcile, and return one live terminal authority."""
    coordinator = TerminalCoordinator.recover(
        authority_root, exports_root=exports_root
    )
    prepared = coordinator.prepare_delivery(
        run_id=run_id,
        operation_id=operation_id,
        identity=identity,
        surfaces=surfaces,
        candidate_wiring_receipt=candidate_wiring_receipt,
    )
    normalized = normalize_terminal_identity(identity)
    capability = coordinator.issue_capability(
        run_id=run_id,
        full_source_sha=normalized["full_source_sha"],
        design_fingerprint=normalized["design_fingerprint"],
        plan_fingerprint=normalized["plan_fingerprint"],
        expected_predecessor_fingerprint=normalized["predecessor_fingerprint"],
        operation_id=operation_id,
    )
    coordinator.commit_delivery(
        capability,
        prepared,
        observed_head_sha=observed_head_sha,
        checkout_clean=checkout_clean,
        fault_at=commit_fault_at,
    )
    coordinator.reconcile_delivery(capability, prepared)
    return coordinator.read_terminal_receipt()


def assert_terminal_authority(
    receipt: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_requirement_id: str | None = None,
) -> dict[str, Any]:
    """Re-read actual CAS/store/head; sealed caller mappings grant no authority."""
    if not isinstance(receipt, TerminalAuthorityReceipt) or \
            receipt._token is not _TERMINAL_RECEIPT_TOKEN or \
            not isinstance(receipt._coordinator, TerminalCoordinator):
        raise TerminalTruthError(
            "nonterminal", "live coordinator-bound terminal authority is required"
        )
    actual = receipt._coordinator._read_terminal_mapping()
    if dict(receipt) != actual:
        raise TerminalTruthError("stale", "terminal authority changed after it was read")
    if not isinstance(receipt, Mapping) or set(receipt) != _AUTHORITY_FIELDS or \
            receipt.get("schema") != \
            "taskplane.exact-sha-terminal-authority/v1" or \
            receipt.get("status") != "complete":
        raise TerminalTruthError("nonterminal", "complete terminal authority is required")
    bundle = receipt.get("bundle")
    if not isinstance(bundle, Mapping) or set(bundle) != _BUNDLE_FIELDS:
        raise TerminalTruthError("nonterminal", "terminal bundle is unavailable")
    bundle_unsigned = {
        key: value for key, value in bundle.items() if key != "fingerprint"
    }
    if bundle.get("fingerprint") != _digest(bundle_unsigned):
        raise TerminalTruthError("mixed", "terminal bundle fingerprint mismatch")
    identity = normalize_terminal_identity(bundle.get("identity"))
    if identity["full_source_sha"] != _object_id(expected_sha, "expected_sha"):
        raise TerminalTruthError("stale", "terminal authority names another SHA")
    if expected_requirement_id is not None and identity["requirement_id"] != \
            _text(expected_requirement_id, "expected_requirement_id"):
        raise TerminalTruthError("mixed", "terminal authority names another requirement")
    if tuple(bundle.get("surface_ids") or ()) != SURFACE_IDS or \
            set(bundle.get("surface_digests") or {}) != set(SURFACE_IDS):
        raise TerminalTruthError("partial", "terminal bundle surface set is incomplete")
    reconciliation = TerminalCoordinator._validate_reconciliation(
        bundle, receipt.get("reconciliation")
    )
    expected_authority_fingerprint = _digest(
        {"bundle": bundle["fingerprint"],
         "reconciliation": reconciliation["fingerprint"]}
    )
    if receipt.get("fingerprint") != expected_authority_fingerprint:
        raise TerminalTruthError("mixed", "terminal authority fingerprint mismatch")
    return dict(receipt)
