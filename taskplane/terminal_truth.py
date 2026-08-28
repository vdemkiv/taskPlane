"""Atomic exact-SHA terminal truth for R-0013 delivery.

Prepared projection bytes have no authority.  One immutable bundle becomes
the logical authority only when an orchestrator-bound object capability
advances ``head.json`` by compare-and-swap.  Readers remain fail-closed until
all eight derived projections reconcile byte-identically to that bundle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # Windows retains the in-process lock and atomic replace
    fcntl = None

try:
    from taskplane import wiring_closure
except ImportError:  # direct executable/import compatibility
    import wiring_closure


TERMINAL_BUNDLE_SCHEMA = "taskplane.exact-sha-terminal-bundle/v1"
TERMINAL_PROJECTION_SCHEMA = "taskplane.exact-sha-terminal-projection/v1"
TERMINAL_RECONCILIATION_SCHEMA = "taskplane.terminal-reconciliation/v1"
PRIVATE_USAGE_CLEANUP_SCHEMA = "taskplane.private-usage-cleanup/v1"
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
_TERMINAL_RECEIPT_TOKEN = object()
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

    def cleanup_receipt_path(self, bundle_fingerprint: str) -> Path:
        return self._root / "cleanup" / f"{_fingerprint(bundle_fingerprint, 'bundle_fingerprint')}.json"

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
