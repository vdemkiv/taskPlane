"""Host-observed producer receipts for evaluator and EM submissions."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import math
import os
import secrets
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict, TypeVar

if TYPE_CHECKING or __package__:
    from .delivery_ports import (
        Clock, DeliveryPortError, EvidenceStore, HostActionCapabilitySource,
        ProducerEventSource, content_fingerprint, LocatorEvidenceStore, SystemClock,
    )
else:
    from delivery_ports import (
        Clock, DeliveryPortError, EvidenceStore, HostActionCapabilitySource,
        ProducerEventSource, content_fingerprint, LocatorEvidenceStore, SystemClock,
    )


PRODUCER_OBSERVATION_SCHEMA = "taskplane.producer-observation/v1"
HOST_PRODUCER_EVENT_SCHEMA = "taskplane.host-producer-event/v1"
PRODUCER_CONSUMPTION_SCHEMA = "taskplane.producer-observation-consumption/v1"
PRODUCER_OBSERVATION_INTENT_SCHEMA = \
    "taskplane.producer-observation-intent/v2"
MAX_EVENT_AGE_SECONDS = 300.0

_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "task_id",
        "stage",
        "producer",
        "host",
        "host_session_or_turn",
        "output_path",
        "output_bytes",
        "output_sha256",
        "output_schema_id",
        "output_contract_fingerprint",
        "source_sha",
        "observed_at",
        "fingerprint",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "event_id",
        "host",
        "host_session_id",
        "host_turn_id",
        "run_id",
        "task_id",
        "stage",
        "producer",
        "output_path",
        "output_bytes",
        "output_sha256",
        "output_schema_id",
        "output_contract_fingerprint",
        "source_sha",
        "observed_at",
    }
)
_DISPATCH_FIELDS = frozenset(
    {"run_id", "task_id", "stage", "producer", "task_name",
     "role_marker", "model", "reasoning_effort", "fingerprint"}
)
_STOP_IDENTITY_FIELDS = frozenset(
    {"session_id", "turn_id", "agent_id", "agent_type", "task_name"}
)
_CONSUMPTION_IDENTITY_FIELDS = frozenset(
    {"host", "host_session_or_turn", "run_id", "task_id", "stage",
     "producer", "output_sha256", "output_contract_fingerprint",
     "source_sha", "observed_at"}
)
_CONSUMPTION_FIELDS = frozenset(
    {"schema", "receipt_fingerprint", "observation_identity",
     "evidence_receipt_fingerprint", "evidence_predecessor_fingerprint",
     "evidence_state_fingerprint", "store_namespace_fingerprint",
     "consumed_at", "fingerprint"}
)
_EVIDENCE_RECEIPT_FIELDS = frozenset(
    {"domain", "operation_id", "predecessor_fingerprint", "payload",
     "payload_fingerprint", "prepare_token", "fingerprint"}
)
_OBSERVATION_INTENT_FIELDS = frozenset(
    {"schema", "operation_id", "expected_head", "receipt", "phase",
     "capability_receipt_fingerprint", "evidence_receipt_fingerprint",
     "fingerprint"}
)


class ProducerObservationError(ValueError):
    """A submission lacks one exact, fresh host observation."""


_NONCE_BINDINGS = frozenset({
    "run_id", "phase_id", "attempt_id", "operation_id", "candidate_fingerprint",
    "definition_set_fingerprint", "phase_definition_fingerprint",
    "sealed_package_fingerprint", "knowledge_fingerprint", "authority_fingerprint",
    "host_kind", "host_version", "deadline",
})
_NONCE_STATES = frozenset({
    "issued", "dispatch_uncertain", "dispatched", "effect_uncertain", "effect_observed",
})
_NONCE_RECEIPT_SCHEMA = "taskplane.attempt-nonce/v1"
_NONCE_STATE_SCHEMA = "taskplane.attempt-nonce-state/v1"
_EffectResult = TypeVar("_EffectResult")
ReconciledEffect = Literal["observed", "effect_free", "uncertain"]


@dataclass(frozen=True)
class IssuedAttemptNonce:
    """Private attempt material; only receipt is portable, never authority."""

    secret: bytes = field(repr=False)
    receipt: dict[str, object]


class _NonceRecord(TypedDict):
    secret: str
    receipt: dict[str, object]
    effect_state: str


class _NonceState(TypedDict):
    schema: str
    keys: dict[str, str]
    attempts: dict[str, _NonceRecord]


def _nonce_bindings(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != _NONCE_BINDINGS:
        raise ProducerObservationError("nonce bindings must be closed")
    for name in _NONCE_BINDINGS - {"deadline"}:
        _text(value[name], name)
        if name.endswith("_fingerprint"):
            _fingerprint(value[name], name)
    _number(value["deadline"], "deadline")
    return dict(value)


class AttemptNonceSource:
    """Attempt-scoped nonce controls within the incumbent evidence store.

    The composition root supplies a private signing key and explicitly activates
    it. Neither the key nor nonce secret belongs in portable evidence. This
    primitive does not activate hooks, grant lifecycle authority, or establish
    native success. Callers must retain their existing authority checks.
    """

    def __init__(self, store: LocatorEvidenceStore, *, key: bytes,
                 clock: Clock | None = None) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ProducerObservationError("nonce key requires at least 256 bits")
        self.store = store
        self._key = key
        self.key_id = hashlib.sha256(key).hexdigest()
        self.clock = clock or SystemClock()
        self._directory = store._domain_dir("producer_observation")
        for path in (store.path, self._directory):
            if path.is_symlink() or not path.resolve().is_relative_to(store.path.resolve()):
                raise ProducerObservationError("nonce state directory is not confined")
        self._path = self._directory / "nonce-state.json"

    def _read(self) -> _NonceState:
        if self._path.is_symlink():
            raise ProducerObservationError("nonce state cannot be a symlink")
        try:
            with self._path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                    raise ProducerObservationError("nonce state must be private")
                value = json.load(stream)
        except FileNotFoundError:
            return {"schema": _NONCE_STATE_SCHEMA, "keys": {}, "attempts": {}}
        except (OSError, ValueError) as exc:
            raise ProducerObservationError("nonce state is unavailable or corrupt") from exc
        if not isinstance(value, dict) or set(value) != {"schema", "keys", "attempts"} or \
                value["schema"] != _NONCE_STATE_SCHEMA or \
                not isinstance(value["keys"], dict) or not isinstance(value["attempts"], dict):
            raise ProducerObservationError("nonce state is not closed")
        keys: dict[str, str] = {}
        attempts: dict[str, _NonceRecord] = {}
        for key_id, status in value["keys"].items():
            _fingerprint(key_id, "key_id")
            if status not in {"active", "disabled"}:
                raise ProducerObservationError("nonce key state is invalid")
            keys[key_id] = status
        for operation, record in value["attempts"].items():
            if not isinstance(operation, str) or not isinstance(record, dict) or \
                    set(record) != {"secret", "receipt", "effect_state"} or \
                    not isinstance(record["secret"], str) or \
                    not isinstance(record["receipt"], dict) or \
                    record["effect_state"] not in _NONCE_STATES:
                raise ProducerObservationError("nonce attempt state is corrupt")
            attempts[operation] = {
                "secret": record["secret"], "receipt": record["receipt"],
                "effect_state": record["effect_state"],
            }
        return {"schema": _NONCE_STATE_SCHEMA, "keys": keys, "attempts": attempts}

    def _write(self, state: _NonceState) -> None:
        self.store._write_atomic(self._path, _canonical_bytes(state))

    def activate_key(self) -> None:
        """Explicit local provisioning; a disabled key can never be reactivated."""
        with self.store._domain_lock(self._directory):
            state = self._read()
            if state["keys"].get(self.key_id) == "disabled":
                raise ProducerObservationError("nonce key is disabled")
            state["keys"][self.key_id] = "active"
            self._write(state)

    def disable_key(self) -> None:
        """Durably stop issuance, dispatch, and effects, including on restart."""
        with self.store._domain_lock(self._directory):
            state = self._read()
            state["keys"][self.key_id] = "disabled"
            self._write(state)

    def _active(self, state: _NonceState) -> None:
        status = state["keys"].get(self.key_id, "inactive")
        if status != "active":
            raise ProducerObservationError(f"nonce key is {status}")

    def _sign(self, value: Mapping[str, object]) -> str:
        return hmac.new(self._key, _canonical_bytes(value), hashlib.sha256).hexdigest()

    def _checked(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
                 state: _NonceState, *, active: bool = True, enforce_deadline: bool = True) -> _NonceRecord:
        checked = _nonce_bindings(bindings)
        if active:
            self._active(state)
        record = state["attempts"].get(str(checked["operation_id"]))
        if record is None:
            raise ProducerObservationError("nonce issuance is missing")
        receipt = issued.receipt
        if set(receipt) != _NONCE_BINDINGS | {"schema", "key_id", "nonce_digest", "signature"}:
            raise ProducerObservationError("nonce receipt is not closed")
        if receipt["schema"] != _NONCE_RECEIPT_SCHEMA or receipt["key_id"] != self.key_id:
            raise ProducerObservationError("nonce key or schema mismatch")
        projection = {name: item for name, item in receipt.items() if name != "signature"}
        signature = _fingerprint(receipt["signature"], "nonce signature")
        if not hmac.compare_digest(signature, self._sign(projection)):
            raise ProducerObservationError("nonce signature mismatch")
        if any(receipt[name] != checked[name] for name in _NONCE_BINDINGS):
            raise ProducerObservationError("nonce operation bindings changed")
        if not isinstance(issued.secret, bytes) or len(issued.secret) < 32 or \
                not hmac.compare_digest(hashlib.sha256(issued.secret).hexdigest(),
                                        _fingerprint(receipt["nonce_digest"], "nonce digest")):
            raise ProducerObservationError("nonce secret mismatch")
        if not hmac.compare_digest(issued.secret.hex(), record["secret"]) or \
                not hmac.compare_digest(_canonical_bytes(receipt), _canonical_bytes(record["receipt"])):
            raise ProducerObservationError("nonce durable issuance mismatch")
        if active and enforce_deadline and _number(self.clock.wall_time(), "clock.wall_time") >= \
                _number(checked["deadline"], "deadline"):
            raise ProducerObservationError("nonce attempt deadline expired")
        return record

    def issue(self, bindings: Mapping[str, object]) -> IssuedAttemptNonce:
        checked = _nonce_bindings(bindings)
        with self.store._domain_lock(self._directory):
            state = self._read()
            self._active(state)
            operation = str(checked["operation_id"])
            record = state["attempts"].get(operation)
            if record is not None:
                try:
                    issued = IssuedAttemptNonce(bytes.fromhex(record["secret"]), dict(record["receipt"]))
                except ValueError as exc:
                    raise ProducerObservationError("nonce secret is corrupt") from exc
                self._checked(issued, checked, state)
                if record["effect_state"] != "issued":
                    raise ProducerObservationError(
                        f"nonce cannot be reissued: {record['effect_state']}; reconcile original operation")
                return issued
            if _number(self.clock.wall_time(), "clock.wall_time") >= _number(checked["deadline"], "deadline"):
                raise ProducerObservationError("nonce attempt deadline expired")
            secret = secrets.token_bytes(32)
            projection = {**checked, "schema": _NONCE_RECEIPT_SCHEMA,
                          "key_id": self.key_id, "nonce_digest": hashlib.sha256(secret).hexdigest()}
            receipt = {**projection, "signature": self._sign(projection)}
            state["attempts"][operation] = {
                "secret": secret.hex(), "receipt": receipt, "effect_state": "issued",
            }
            self._write(state)
            return IssuedAttemptNonce(secret, receipt)

    def validate(self, issued: IssuedAttemptNonce,
                 bindings: Mapping[str, object], *, enforce_deadline: bool = True) -> dict[str, object]:
        with self.store._domain_lock(self._directory):
            self._checked(issued, bindings, self._read(), enforce_deadline=enforce_deadline)
            return dict(issued.receipt)

    def effect_state(self, bindings: Mapping[str, object]) -> str:
        checked = _nonce_bindings(bindings)
        with self.store._domain_lock(self._directory):
            record = self._read()["attempts"].get(str(checked["operation_id"]))
            if record is None or any(record["receipt"].get(name) != checked[name] for name in _NONCE_BINDINGS):
                raise ProducerObservationError("nonce issuance missing or bindings changed")
            return record["effect_state"]

    def unreserved_issuances(self, operation: str) -> list[dict[str, object]]:
        """Inspect unused operation-family receipts without renewing authority.

        A receipt alone is not permission to replace an attempt: the caller
        must also prove cancellation and absence of preparation/effect owners.
        """
        _text(operation, "operation_id")
        with self.store._domain_lock(self._directory):
            state = self._read()
            result = []
            for name, record in state["attempts"].items():
                if name != operation and not name.startswith(operation + "-"):
                    continue
                receipt = record["receipt"]
                bindings = {key: receipt.get(key) for key in _NONCE_BINDINGS}
                issued = IssuedAttemptNonce(bytes.fromhex(record["secret"]), dict(receipt))
                self._checked(issued, bindings, state, enforce_deadline=False)
                if bindings["operation_id"] != name or record["effect_state"] != "issued" or any(
                        path.exists() or path.is_symlink() for path in
                        (self._hook_path(issued, "start"), self._hook_path(issued, "terminal"))):
                    raise ProducerObservationError("prior preparation has nonce activity; reconcile original operation")
                result.append(dict(receipt))
            return result

    def _perform(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
                 action: Callable[[], _EffectResult], *, expected: str,
                 uncertain: str) -> _EffectResult:
        with self.store._domain_lock(self._directory):
            state = self._read()
            record = self._checked(issued, bindings, state)
            if record["effect_state"] != expected:
                raise ProducerObservationError(f"nonce effect state is {record['effect_state']}")
            record["effect_state"] = uncertain
            self._write(state)
            # A callback response is not authoritative remote reconciliation.
            # Persist uncertainty before crossing the boundary, even on success.
            return action()

    def dispatch(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
                 launch: Callable[[], _EffectResult]) -> _EffectResult:
        return self._perform(issued, bindings, launch, expected="issued", uncertain="dispatch_uncertain")

    def reserve_dispatch(self, issued: IssuedAttemptNonce,
                         bindings: Mapping[str, object]) -> None:
        """Fence external dispatch before returning a host launch request.

        The reservation is uncertainty, never an observed launch identity.
        Loss of the response requires reconciliation of this same operation.
        """
        self._perform(issued, bindings, lambda: None,
                      expected="issued", uncertain="dispatch_uncertain")

    def recover(self, bindings: Mapping[str, object]) -> IssuedAttemptNonce:
        """Recover the original private nonce for observation, never reissue."""
        checked = _nonce_bindings(bindings)
        with self.store._domain_lock(self._directory):
            state = self._read()
            record = state["attempts"].get(str(checked["operation_id"]))
            if record is None:
                raise ProducerObservationError("nonce issuance missing")
            issued = IssuedAttemptNonce(bytes.fromhex(record["secret"]), dict(record["receipt"]))
            self._checked(issued, checked, state, active=False)
            return issued

    def _hook_path(self, issued: IssuedAttemptNonce, kind: str) -> Path:
        if kind not in {"start", "terminal"}:
            raise ProducerObservationError("invalid phase hook kind")
        return self._directory / (str(issued.receipt["nonce_digest"]) + "-" + kind + ".json")

    def record_phase_hook(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object], *,
                          workspace: str, task_name: str, event: Mapping[str, object],
                          outputs: list[dict[str, object]] | None = None) -> dict[str, object]:
        """Seal facts from an exact claimed native hook in existing nonce custody.

        Only the composition root reads engine-selected output bytes. Portable
        receipts contain references and the nonce digest, never the secret.
        """
        from taskplane import taskplane_lite as host_policy
        kind = {"SubagentStart": "start", "SubagentStop": "terminal"}.get(event.get("hook_event_name"))
        if kind is None:
            raise ProducerObservationError("phase lifecycle hook required")
        identity = host_policy.hook_event_identity(workspace, "subagent-" + ("stop" if kind == "terminal" else "start"), dict(event))
        claim = hashlib.sha256(identity.encode()).hexdigest() if identity else None
        if not claim or event.get("_taskplane_hook_claim_id") != claim:
            raise ProducerObservationError("phase hook claim mismatch")
        owner = {key: _text(event.get(key), key) for key in ("agent_id", "agent_type", "task_name")}
        owner["session_id"] = _text(event.get("session_id") or event.get("thread_id"), "session_id")
        turn = _text(event.get("turn_id"), "turn_id")
        if owner["task_name"] != task_name:
            raise ProducerObservationError("stale_or_foreign_event")
        usage = event.get("usage")
        tokens = usage.get("total_tokens") if isinstance(usage, Mapping) else None
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            raise ProducerObservationError("invalid phase usage")
        released = event.get("lease_terminal")
        if released is not None:
            if kind != "terminal" or not isinstance(released, Mapping) or set(released) != {
                    "lease_id", "attempt_id", "operation_id", "fencing_token", "released", "effects"} or \
                    released["attempt_id"] != bindings["attempt_id"] or released["operation_id"] != bindings["operation_id"] or \
                    type(released["released"]) is not bool or type(released["fencing_token"]) is not int or \
                    not isinstance(released["effects"], Mapping) or len(_canonical_bytes(released)) > 16384:
                raise ProducerObservationError("invalid released phase effect observation")
            released = dict(released)
        now = float(self.clock.wall_time())
        if "observed_at" in event:
            _freshness(event["observed_at"], now)
        with self.store._domain_lock(self._directory):
            state = self._read()
            record = self._checked(issued, bindings, state, active=False)
            if record["effect_state"] not in {"dispatch_uncertain", "dispatched"}:
                raise ProducerObservationError("phase dispatch is not reserved")
            path = self._hook_path(issued, kind)
            if path.exists():
                prior = self._read_phase_hook(issued, bindings, kind)
                if prior["claim"] != claim or prior["owner"] != owner or prior["outputs"] != (outputs or []) or prior["tokens"] != tokens or prior.get("lease_terminal") != released:
                    raise ProducerObservationError("conflicting phase hook replay")
                return prior
            if kind == "terminal":
                start = self._read_phase_hook(issued, bindings, "start")
                if start["owner"] != owner:
                    raise ProducerObservationError("stale_or_foreign_event")
            value = {"schema": "taskplane.attempt-hook-receipt/v1", "bindings": dict(bindings),
                "nonce_digest": issued.receipt["nonce_digest"], "kind": kind,
                "sequence": 1 if kind == "start" else 2, "claim": claim, "owner": owner,
                "turn_id": turn, "observed_at": now, "outputs": outputs or [], "tokens": tokens,
                "lease_terminal": released,
                "outcome": None if kind == "start" else host_policy.normalize_worker_terminal_outcome(
                    event.get("outcome") or event.get("status") or event.get("stop_reason") or "unknown")}
            value["signature"] = self._sign(value)
            self.store._write_atomic(path, _canonical_bytes(value))
            return value

    def _read_phase_hook(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
                         kind: str) -> dict[str, object]:
        path = self._hook_path(issued, kind)
        if path.is_symlink():
            raise ProducerObservationError("unsafe phase hook receipt")
        try:
            raw = path.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ProducerObservationError("oversized phase hook receipt")
            value = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise ProducerObservationError("missing_" + kind) from exc
        if not isinstance(value, dict) or value.get("bindings") != dict(bindings) or \
                value.get("nonce_digest") != issued.receipt["nonce_digest"] or value.get("kind") != kind or \
                not hmac.compare_digest(str(value.get("signature") or ""),
                    self._sign({key: item for key, item in value.items() if key != "signature"})):
            raise ProducerObservationError("phase hook binding or signature mismatch")
        return value

    def phase_hooks(self, issued: IssuedAttemptNonce,
                    bindings: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        """Read both authenticated receipts; absence remains an observation gap."""
        with self.store._domain_lock(self._directory):
            self._checked(issued, bindings, self._read(), active=False)
            start = self._read_phase_hook(issued, bindings, "start")
            terminal = self._read_phase_hook(issued, bindings, "terminal")
            if start["owner"] != terminal["owner"] or terminal["observed_at"] < start["observed_at"]:
                raise ProducerObservationError("stale_or_foreign_event")
            return start, terminal

    def terminal_hooks(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object]
                       ) -> tuple[dict[str, object], dict[str, object]] | None:
        """Read a completed host observation, distinguishing absence from corruption."""
        path = self._hook_path(issued, "terminal")
        if not path.exists() and not path.is_symlink():
            return None
        return self.phase_hooks(issued, bindings)

    def effect(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
               action: Callable[[], _EffectResult]) -> _EffectResult:
        return self._perform(issued, bindings, action, expected="dispatched", uncertain="effect_uncertain")

    def reconcile(self, issued: IssuedAttemptNonce, bindings: Mapping[str, object],
                  observe: Callable[[str], ReconciledEffect] | None) -> str:
        """Use the caller's authoritative host observation port, never a timeout.

        This port must reconcile the original operation, including any still
        running request, before reporting effect_free. Disabled/expired attempts
        may still gather truth, but may not initiate new effects.
        """
        with self.store._domain_lock(self._directory):
            state = self._read()
            record = self._checked(issued, bindings, state, active=False)
            current = record["effect_state"]
            if current not in {"dispatch_uncertain", "effect_uncertain"}:
                return current
            if observe is None:
                raise ProducerObservationError("nonce reconciliation unavailable: original operation observation required")
            try:
                result = observe(str(bindings["operation_id"]))
            except Exception as exc:
                raise ProducerObservationError("nonce reconciliation unavailable") from exc
            if result not in {"observed", "effect_free", "uncertain"}:
                raise ProducerObservationError("nonce reconciliation result is invalid")
            if result != "uncertain":
                record["effect_state"] = (
                    ("dispatched" if result == "observed" else "issued")
                    if current == "dispatch_uncertain" else
                    ("effect_observed" if result == "observed" else "dispatched"))
                self._write(state)
            return record["effect_state"]


class _NativeEventSource:
    """One host-owned event, constructed inside the lifecycle adapter."""

    def __init__(self, event: Mapping[str, Any]) -> None:
        self._event = dict(event)

    def events(self, *, host_session_id: str,
               host_turn_id: str) -> tuple[dict[str, Any], ...]:
        if self._event.get("host_session_id") != host_session_id or \
                self._event.get("host_turn_id") != host_turn_id:
            return ()
        return (dict(self._event),)


class _OneUseNativeCapability:
    """Process-private exact-bound capability for one claimed hook event."""

    def __init__(self, handle: str, bindings: Mapping[str, Any], *,
                 issued_at: float, expires_at: float) -> None:
        self._handle = handle
        self._bindings = dict(bindings)
        self._issued_at = float(issued_at)
        self._expires_at = float(expires_at)
        self._consumed = False

    def consume(self, handle: str, *, expected_bindings: Mapping[str, Any],
                now: float) -> dict[str, Any]:
        if self._consumed:
            raise DeliveryPortError("host capability replay")
        if not hmac.compare_digest(handle.encode("utf-8"), self._handle.encode("utf-8")):
            raise DeliveryPortError("missing host-private capability handle")
        if not self._issued_at <= float(now) < self._expires_at:
            raise DeliveryPortError("host capability expired or not yet valid")
        if dict(expected_bindings) != self._bindings:
            raise DeliveryPortError("host capability binding mismatch")
        self._consumed = True
        return {**self._bindings, "cryptographic_authenticity_claimed": False}


def _production_store(evidence_root: str, workspace: str,
                      run_id: str) -> LocatorEvidenceStore:
    root = Path(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    repository = hashlib.sha256(
        os.path.realpath(workspace).encode("utf-8")).hexdigest()
    namespace = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()
    store = LocatorEvidenceStore(root, repository, namespace)
    reconcile_observation_intents(store)
    return store


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write_observation_intent(path: Path,
                                     value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as stream:
            stream.write(_canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _observation_intent_path(
        evidence_store: EvidenceStore, operation_id: str,
        receipt: Mapping[str, Any], expected_head: str | None) -> Path | None:
    root = getattr(evidence_store, "path", None)
    if not isinstance(root, Path):
        return None
    identity = {
        "schema": PRODUCER_OBSERVATION_INTENT_SCHEMA,
        "operation_id": operation_id,
        "receipt_fingerprint": receipt["fingerprint"],
        "expected_head": expected_head,
    }
    transaction_id = content_fingerprint(identity)
    return (root / "producer_observation" / "authority-intents" /
            f"{transaction_id}.json")


def _validate_observation_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != \
            _OBSERVATION_INTENT_FIELDS:
        raise ProducerObservationError(
            "producer observation durable intent is not closed")
    if value.get("schema") != PRODUCER_OBSERVATION_INTENT_SCHEMA:
        raise ProducerObservationError(
            "producer observation durable intent schema is invalid")
    _text(value.get("operation_id"), "operation_id")
    expected_head = value.get("expected_head")
    if expected_head is not None:
        _fingerprint(expected_head, "expected_head")
    receipt = validate_producer_observation(value.get("receipt"))
    phase = value.get("phase")
    if phase not in {"prepared", "capability_consumed", "committed"}:
        raise ProducerObservationError(
            "producer observation durable intent phase is invalid")
    capability_fingerprint = value.get("capability_receipt_fingerprint")
    evidence_fingerprint = value.get("evidence_receipt_fingerprint")
    if phase == "prepared":
        if capability_fingerprint is not None or evidence_fingerprint is not None:
            raise ProducerObservationError(
                "unconsumed producer observation intent claims completion")
    else:
        _fingerprint(
            capability_fingerprint, "capability_receipt_fingerprint")
        if phase == "capability_consumed" and evidence_fingerprint is not None:
            raise ProducerObservationError(
                "uncommitted producer observation intent claims evidence")
        if phase == "committed":
            _fingerprint(
                evidence_fingerprint, "evidence_receipt_fingerprint")
    projection = {key: value[key]
                  for key in _OBSERVATION_INTENT_FIELDS - {"fingerprint"}}
    if value.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError(
            "producer observation durable intent fingerprint mismatch")
    return {**dict(value), "receipt": receipt}


def _observation_intent_value(
        *, operation_id: str, expected_head: str | None,
        receipt: Mapping[str, Any], phase: str,
        capability_receipt_fingerprint: str | None = None,
        evidence_receipt_fingerprint: str | None = None) -> dict[str, Any]:
    projection = {
        "schema": PRODUCER_OBSERVATION_INTENT_SCHEMA,
        "operation_id": operation_id,
        "expected_head": expected_head,
        "receipt": dict(receipt),
        "phase": phase,
        "capability_receipt_fingerprint": capability_receipt_fingerprint,
        "evidence_receipt_fingerprint": evidence_receipt_fingerprint,
    }
    value = {**projection, "fingerprint": content_fingerprint(projection)}
    return _validate_observation_intent(value)


def _load_observation_intent(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProducerObservationError(
            "producer observation durable intent is corrupt") from exc
    return _validate_observation_intent(value)


def _prepare_observation_intent(
        evidence_store: EvidenceStore, *, operation_id: str,
        receipt: Mapping[str, Any], expected_head: str | None,
        checkpoint: bool = True) -> tuple[Path, dict[str, Any]] | None:
    path = _observation_intent_path(
        evidence_store, operation_id, receipt, expected_head)
    if path is None:
        return None
    if path.exists():
        value = _load_observation_intent(path)
        expected = (operation_id, expected_head, receipt["fingerprint"])
        actual = (value["operation_id"], value["expected_head"],
                  value["receipt"]["fingerprint"])
        if actual != expected:
            raise ProducerObservationError(
                "producer observation durable intent collision")
        return path, value
    value = _observation_intent_value(
        operation_id=operation_id, expected_head=expected_head,
        receipt=receipt, phase="prepared")
    _atomic_write_observation_intent(path, value)
    if checkpoint:
        injector = getattr(evidence_store, "fault_injector", None)
        if injector is not None:
            injector.checkpoint("after-prepare-intent")
    return path, value


def _advance_observation_intent(
        path: Path, value: Mapping[str, Any], *, phase: str,
        capability_receipt_fingerprint: str,
        evidence_receipt_fingerprint: str | None = None) -> dict[str, Any]:
    advanced = _observation_intent_value(
        operation_id=value["operation_id"],
        expected_head=value["expected_head"], receipt=value["receipt"],
        phase=phase,
        capability_receipt_fingerprint=capability_receipt_fingerprint,
        evidence_receipt_fingerprint=evidence_receipt_fingerprint)
    _atomic_write_observation_intent(path, advanced)
    return advanced


def _evidence_receipt_fingerprint(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode("utf-8"))
        fingerprint = value.get("fingerprint")
    except (UnicodeDecodeError, ValueError, AttributeError) as exc:
        raise ProducerObservationError(
            "producer observation evidence receipt is corrupt") from exc
    return _fingerprint(fingerprint, "evidence_receipt_fingerprint")


def reconcile_observation_intents(
        evidence_store: EvidenceStore) -> tuple[bytes, ...]:
    """Commit only intents with durable proof of capability consumption."""
    root = getattr(evidence_store, "path", None)
    if not isinstance(root, Path):
        return ()
    directory = root / "producer_observation" / "authority-intents"
    recovered: list[bytes] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        value = _load_observation_intent(path)
        if value["phase"] in {"prepared", "committed"}:
            continue
        prepared = evidence_store.prepare(
            "producer_observation", value["operation_id"], value["receipt"],
            expected_head=value["expected_head"])
        raw = evidence_store.commit(prepared)
        _advance_observation_intent(
            path, value, phase="committed",
            capability_receipt_fingerprint=
                value["capability_receipt_fingerprint"],
            evidence_receipt_fingerprint=
                _evidence_receipt_fingerprint(raw))
        recovered.append(raw)
    return tuple(recovered)


def exact_output_bundle(paths: list[tuple[str, bytes]]) -> bytes:
    """Frame one or more exact output files without JSON/text re-encoding."""
    framed = bytearray()
    for path, raw in paths:
        if not isinstance(path, str) or not path or not isinstance(raw, bytes):
            raise ProducerObservationError("output bundle entries are invalid")
        name = path.encode("utf-8")
        framed.extend(len(name).to_bytes(8, "big"))
        framed.extend(name)
        framed.extend(len(raw).to_bytes(8, "big"))
        framed.extend(raw)
    return bytes(framed)


def validate_producer_dispatch(
        dispatch: Mapping[str, Any], *, run_id: str, task_id: str,
        stage: str, producer: str) -> dict[str, Any]:
    """Validate the exact engine-emitted native producer dispatch."""
    if not isinstance(dispatch, Mapping) or set(dispatch) != _DISPATCH_FIELDS:
        raise ProducerObservationError(
            "external host producer receipt cannot be matched: producer "
            "dispatch identity is missing or not closed")
    expected = {"run_id": run_id, "task_id": task_id, "stage": stage,
                "producer": producer}
    if any(dispatch.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("producer dispatch identity mismatched")
    for field in ("run_id", "task_id", "producer", "task_name",
                  "role_marker"):
        _text(dispatch.get(field), field)
    for field in ("model", "reasoning_effort"):
        if dispatch.get(field) is not None:
            _text(dispatch.get(field), field)
    projection = {key: dispatch[key]
                  for key in _DISPATCH_FIELDS - {"fingerprint"}}
    if dispatch.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError("producer dispatch fingerprint mismatch")
    return dict(dispatch)


def _stopping_identity(
        event: Mapping[str, Any], dispatch: Mapping[str, Any], *,
        session_id: str, turn_id: str) -> dict[str, str]:
    """Bind host-owned child identity to the exact emitted task name."""
    identity = {
        "session_id": session_id,
        "turn_id": turn_id,
        "agent_id": str(event.get("agent_id") or "").strip(),
        "agent_type": str(event.get("agent_type") or "").strip(),
        "task_name": str(event.get("task_name") or "").strip(),
    }
    if any(not value for value in identity.values()):
        raise ProducerObservationError(
            "Codex SubagentStop stopping-agent identity is required")
    expected_name = str(dispatch["task_name"])
    if identity["task_name"] != expected_name:
        raise ProducerObservationError(
            "Codex SubagentStop stopping agent does not match emitted "
            "producer dispatch")
    return identity


def _decode_stopping_identity(
        value: Any, dispatch: Mapping[str, Any]) -> dict[str, str]:
    try:
        identity = json.loads(_text(value, "host_session_or_turn"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProducerObservationError(
            "producer observation stopping-agent identity is invalid") from exc
    if not isinstance(identity, dict) or set(identity) != _STOP_IDENTITY_FIELDS:
        raise ProducerObservationError(
            "producer observation stopping-agent identity is not closed")
    for field in _STOP_IDENTITY_FIELDS:
        _text(identity.get(field), field)
    expected_name = str(dispatch["task_name"])
    if identity["task_name"] != expected_name:
        raise ProducerObservationError(
            "producer observation stopping agent mismatched")
    return identity


def _freshness(observed_at: Any, now: float) -> None:
    observed = _number(observed_at, "observed_at")
    if observed > now or now - observed > MAX_EVENT_AGE_SECONDS:
        raise ProducerObservationError("stale host producer observation")


def _load_stored_observation(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one immutable receipt envelope and its schema-valid payload."""
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or \
                set(envelope) != _EVIDENCE_RECEIPT_FIELDS:
            raise ValueError("receipt envelope is not closed")
        projection = {key: envelope[key]
                      for key in _EVIDENCE_RECEIPT_FIELDS - {"fingerprint"}}
        if envelope.get("domain") != "producer_observation" or \
                envelope.get("fingerprint") != content_fingerprint(projection) or \
                path.name != f"{envelope['fingerprint']}.json":
            raise ValueError("receipt envelope fingerprint mismatch")
        raw = base64.b64decode(envelope["payload"], validate=True)
        if envelope.get("payload_fingerprint") != content_fingerprint(raw):
            raise ValueError("receipt payload fingerprint mismatch")
        payload = json.loads(raw.decode("utf-8"))
        receipt = validate_producer_observation(payload)
    except Exception as exc:
        raise ProducerObservationError(
            "producer observation store is corrupt") from exc
    return receipt, envelope


def _observation_identity(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {key: receipt[key] for key in _CONSUMPTION_IDENTITY_FIELDS}


def _validate_consumption_marker(
        marker: Mapping[str, Any], *, receipt: Mapping[str, Any],
        evidence_receipt: Mapping[str, Any], state_fingerprint: str,
        store_namespace_fingerprint: str, now: float) -> dict[str, Any]:
    if not isinstance(marker, Mapping) or set(marker) != _CONSUMPTION_FIELDS:
        raise ProducerObservationError(
            "producer observation consumption marker is not closed")
    if marker.get("schema") != PRODUCER_CONSUMPTION_SCHEMA:
        raise ProducerObservationError(
            "producer observation consumption marker schema is invalid")
    identity = marker.get("observation_identity")
    if not isinstance(identity, Mapping) or \
            set(identity) != _CONSUMPTION_IDENTITY_FIELDS or \
            dict(identity) != _observation_identity(receipt):
        raise ProducerObservationError(
            "producer observation consumption identity mismatched")
    expected = {
        "receipt_fingerprint": receipt["fingerprint"],
        "evidence_receipt_fingerprint": evidence_receipt["fingerprint"],
        "evidence_predecessor_fingerprint":
            evidence_receipt.get("predecessor_fingerprint"),
        "evidence_state_fingerprint": state_fingerprint,
        "store_namespace_fingerprint": store_namespace_fingerprint,
    }
    if any(marker.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError(
            "producer observation consumption marker mismatched")
    consumed_at = _number(marker.get("consumed_at"), "consumed_at")
    if consumed_at < float(receipt["observed_at"]) or consumed_at > now:
        raise ProducerObservationError(
            "producer observation consumption time is invalid")
    projection = {key: marker[key]
                  for key in _CONSUMPTION_FIELDS - {"fingerprint"}}
    if marker.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError(
            "producer observation consumption marker fingerprint mismatch")
    return dict(marker)


def record_codex_subagent_stop(
    *, workspace: str, evidence_root: str, event: Mapping[str, Any],
    hook_claim_id: str, run_id: str, task_id: str, stage: str,
    producer: str, output_path: str, output_bytes: bytes,
    output_schema_id: str, output_contract_fingerprint: str,
    source_sha: str, producer_dispatch: Mapping[str, Any],
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Mint one production receipt from an already-claimed Codex stop.

    Caller text and transcripts are deliberately absent.  The adapter binds
    only native lifecycle identity and bytes read from the engine-selected
    result path.  Native/bridge duplicates share ``hook_claim_id`` and the
    outer hook claim journal executes this function only once.
    """
    if not isinstance(event, Mapping) or \
            event.get("hook_event_name") != "SubagentStop":
        raise ProducerObservationError("Codex SubagentStop event is required")
    session_id = str(event.get("session_id") or event.get("thread_id") or
                     os.environ.get("CODEX_THREAD_ID") or "").strip()
    turn_id = str(event.get("turn_id") or "").strip()
    if not session_id or not turn_id:
        raise ProducerObservationError(
            "Codex SubagentStop session and turn identity are required")
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    stopping_identity = _stopping_identity(
        event, dispatch, session_id=session_id, turn_id=turn_id)
    if not isinstance(hook_claim_id, str) or len(hook_claim_id) != 64 or \
            any(ch not in "0123456789abcdef" for ch in hook_claim_id):
        raise ProducerObservationError("stable hook claim identity is required")
    try:
        from taskplane import taskplane_lite as host_policy
        hook_identity = host_policy.hook_event_identity(
            workspace, "subagent-stop", dict(event))
    except Exception as exc:
        raise ProducerObservationError(
            "Codex SubagentStop hook identity cannot be verified") from exc
    expected_claim_id = hashlib.sha256(hook_identity.encode("utf-8")).hexdigest() \
        if hook_identity else ""
    if hook_claim_id != expected_claim_id:
        raise ProducerObservationError(
            "Codex SubagentStop hook claim does not match stopping agent")
    active_clock = clock or SystemClock()
    now = float(active_clock.wall_time())
    output_digest = hashlib.sha256(output_bytes).hexdigest()
    host_event = {
        "schema": HOST_PRODUCER_EVENT_SCHEMA,
        "event_id": hook_claim_id,
        "host": "codex",
        "host_session_id": session_id,
        "host_turn_id": turn_id,
        "run_id": run_id,
        "task_id": task_id,
        "stage": stage,
        "producer": producer,
        "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": output_digest,
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
        "observed_at": now,
    }
    bindings = {
        "purpose": "producer_observation",
        "host_session_id": session_id,
        "host_turn_id": turn_id,
        "run_id": run_id,
        "kernel_id": None,
        "task_id": task_id,
        "stage": stage,
        "request_or_output_digest": output_digest,
        "contract_fingerprint": output_contract_fingerprint,
    }
    handle = "host-private:" + content_fingerprint({
        "claim": hook_claim_id, "bindings": bindings})
    capability = _OneUseNativeCapability(
        handle, bindings, issued_at=now - 1.0, expires_at=now + 60.0)
    return observe_submission(
        run_id=run_id, task_id=task_id, stage=stage, producer=producer,
        host="codex", host_session_id=session_id, host_turn_id=turn_id,
        output_path=output_path, output_bytes=output_bytes,
        output_schema_id=output_schema_id,
        output_contract_fingerprint=output_contract_fingerprint,
        source_sha=source_sha, capability_handle=handle,
        event_source=_NativeEventSource(host_event),
        capability_source=capability,
        evidence_store=_production_store(
            evidence_root, workspace, run_id), clock=active_clock,
        host_producer_identity=json.dumps(
            stopping_identity, sort_keys=True, separators=(",", ":")))


def consume_matching_observation(
    *, workspace: str, evidence_root: str, run_id: str, task_id: str,
    stage: str, producer: str, output_path: str, output_bytes: bytes,
    output_schema_id: str, output_contract_fingerprint: str,
    source_sha: str, producer_dispatch: Mapping[str, Any],
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Locate, validate, and durably consume exactly one fresh receipt."""
    store = _production_store(evidence_root, workspace, run_id)
    receipt_dir = store.path / "producer_observation" / "receipts"
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else ():
        receipt, envelope = _load_stored_observation(path)
        if receipt["run_id"] == run_id and receipt["task_id"] == task_id \
                and receipt["stage"] == stage:
            rows.append((receipt, envelope))
    if not rows:
        raise ProducerObservationError("missing host producer observation")
    if len(rows) != 1:
        raise ProducerObservationError("ambiguous host producer observations")
    receipt, evidence_receipt = rows[0]
    now = float((clock or SystemClock()).wall_time())
    _freshness(receipt["observed_at"], now)
    _decode_stopping_identity(receipt["host_session_or_turn"], dispatch)
    expected = {
        "producer": producer, "host": "codex", "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("mismatched host producer observation")
    consumed = store.path / "producer_observation" / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    marker = consumed / f"{receipt['fingerprint']}.json"
    state_path = store.path / "producer_observation" / "STATE"
    try:
        state_fingerprint = state_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProducerObservationError(
            "producer observation durable state is missing") from exc
    if state_fingerprint != evidence_receipt["fingerprint"]:
        raise ProducerObservationError(
            "producer observation durable state mismatched")
    marker_projection = {
        "schema": PRODUCER_CONSUMPTION_SCHEMA,
        "receipt_fingerprint": receipt["fingerprint"],
        "observation_identity": _observation_identity(receipt),
        "evidence_receipt_fingerprint": evidence_receipt["fingerprint"],
        "evidence_predecessor_fingerprint":
            evidence_receipt.get("predecessor_fingerprint"),
        "evidence_state_fingerprint": state_fingerprint,
        "store_namespace_fingerprint": store.namespace_token,
        "consumed_at": now,
    }
    marker_value = {**marker_projection,
                    "fingerprint": content_fingerprint(marker_projection)}
    marker_bytes = (json.dumps(marker_value, sort_keys=True,
                               separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ProducerObservationError("producer observation replay") from exc
    try:
        view = memoryview(marker_bytes)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def validate_consumed_matching_observation(
    receipt: Mapping[str, Any], *, workspace: str, evidence_root: str,
    run_id: str, task_id: str, stage: str, producer: str,
    output_path: str, output_bytes: bytes, output_schema_id: str,
    output_contract_fingerprint: str, source_sha: str,
    producer_dispatch: Mapping[str, Any], clock: Clock | None = None,
) -> dict[str, Any]:
    """Re-attest that a submission names the exact consumed native receipt."""
    checked = validate_producer_observation(receipt)
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    _decode_stopping_identity(checked["host_session_or_turn"], dispatch)
    now = float((clock or SystemClock()).wall_time())
    _freshness(checked["observed_at"], now)
    expected = {
        "run_id": run_id, "task_id": task_id, "stage": stage,
        "producer": producer, "host": "codex", "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    if any(checked.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("mismatched host producer observation")
    store = _production_store(evidence_root, workspace, run_id)
    receipt_dir = store.path / "producer_observation" / "receipts"
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else ():
        payload, envelope = _load_stored_observation(path)
        if payload == checked:
            matches.append((path, envelope))
    if len(matches) != 1:
        raise ProducerObservationError(
            "consumed producer observation is missing or ambiguous")
    evidence_receipt = matches[0][1]
    state_path = store.path / "producer_observation" / "STATE"
    head_path = store.path / "producer_observation" / "HEAD"
    try:
        state_fingerprint = state_path.read_text(encoding="utf-8").strip()
        head_fingerprint = head_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProducerObservationError(
            "producer observation durable state is missing") from exc
    if state_fingerprint != evidence_receipt["fingerprint"] or \
            head_fingerprint != state_fingerprint:
        raise ProducerObservationError(
            "producer observation durable state mismatched")
    marker = (store.path / "producer_observation" / "consumed" /
              f"{checked['fingerprint']}.json")
    if not marker.is_file():
        raise ProducerObservationError(
            "producer observation was not consumed")
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProducerObservationError(
            "producer observation consumption marker is corrupt") from exc
    _validate_consumption_marker(
        marker_value, receipt=checked, evidence_receipt=evidence_receipt,
        state_fingerprint=state_fingerprint,
        store_namespace_fingerprint=store.namespace_token, now=now)
    return checked


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProducerObservationError(f"{field} is required")
    return value


def _fingerprint(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ProducerObservationError(f"{field} must be a lowercase SHA-256 fingerprint")
    return text


def _source_sha(value: Any) -> str:
    text = _text(value, "source_sha")
    if len(text) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ProducerObservationError("source_sha must be an exact lowercase Git SHA")
    return text


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProducerObservationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ProducerObservationError(f"{field} must be a finite number")
    return result


def validate_producer_observation(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the strict checked-in v1 producer-observation schema."""
    if not isinstance(receipt, Mapping):
        raise ProducerObservationError("producer observation must be a mapping")
    if set(receipt) != _OBSERVATION_FIELDS:
        raise ProducerObservationError("producer observation fields are not closed")
    if receipt.get("schema") != PRODUCER_OBSERVATION_SCHEMA:
        raise ProducerObservationError("producer observation schema is invalid")
    for field in (
        "run_id",
        "task_id",
        "producer",
        "host_session_or_turn",
        "output_path",
        "output_schema_id",
    ):
        _text(receipt.get(field), field)
    if receipt.get("stage") not in {"evaluate", "em"}:
        raise ProducerObservationError("stage must be evaluate or em")
    if receipt.get("host") not in {"codex", "claude"}:
        raise ProducerObservationError("host must be codex or claude")
    output_size = receipt.get("output_bytes")
    if isinstance(output_size, bool) or not isinstance(output_size, int) or output_size < 0:
        raise ProducerObservationError("output_bytes must be a non-negative integer")
    _fingerprint(receipt.get("output_sha256"), "output_sha256")
    _fingerprint(
        receipt.get("output_contract_fingerprint"),
        "output_contract_fingerprint",
    )
    _source_sha(receipt.get("source_sha"))
    _number(receipt.get("observed_at"), "observed_at")
    projection = {key: receipt[key] for key in _OBSERVATION_FIELDS - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError("producer observation fingerprint mismatch")
    return dict(receipt)


def observe_submission(
    *,
    run_id: str,
    task_id: str,
    stage: str,
    producer: str,
    host: str,
    host_session_id: str,
    host_turn_id: str,
    output_path: str,
    output_bytes: bytes,
    output_schema_id: str,
    output_contract_fingerprint: str,
    source_sha: str,
    capability_handle: str,
    event_source: ProducerEventSource,
    capability_source: HostActionCapabilitySource,
    evidence_store: EvidenceStore,
    clock: Clock,
    predecessor_fingerprint: str | None = None,
    host_producer_identity: str | None = None,
) -> dict[str, Any]:
    """Bind one exact host event and capability to immutable output identity."""
    values = {
        "run_id": _text(run_id, "run_id"),
        "task_id": _text(task_id, "task_id"),
        "stage": stage,
        "producer": _text(producer, "producer"),
        "host": host,
        "host_session_id": _text(host_session_id, "host_session_id"),
        "host_turn_id": _text(host_turn_id, "host_turn_id"),
        "output_path": _text(output_path, "output_path"),
        "output_schema_id": _text(output_schema_id, "output_schema_id"),
        "output_contract_fingerprint": _fingerprint(
            output_contract_fingerprint, "output_contract_fingerprint"
        ),
        "source_sha": _source_sha(source_sha),
    }
    if stage not in {"evaluate", "em"}:
        raise ProducerObservationError("stage must be evaluate or em")
    if host not in {"codex", "claude"}:
        raise ProducerObservationError("host must be codex or claude")
    if not isinstance(output_bytes, bytes):
        raise ProducerObservationError("output_bytes must be exact bytes")
    output_digest = hashlib.sha256(output_bytes).hexdigest()
    expected_event = {
        "schema": HOST_PRODUCER_EVENT_SCHEMA,
        "host": host,
        "host_session_id": host_session_id,
        "host_turn_id": host_turn_id,
        "run_id": run_id,
        "task_id": task_id,
        "stage": stage,
        "producer": producer,
        "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": output_digest,
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    try:
        events = tuple(
            event_source.events(
                host_session_id=host_session_id, host_turn_id=host_turn_id
            )
        )
    except Exception as exc:
        raise ProducerObservationError("host producer event source failed") from exc
    if len(events) > 1:
        raise ProducerObservationError("ambiguous host producer events")
    if len(events) != 1 or not isinstance(events[0], Mapping):
        raise ProducerObservationError("missing host producer event")
    event = dict(events[0])
    if set(event) != _EVENT_FIELDS or any(
        event.get(field) != expected for field, expected in expected_event.items()
    ):
        raise ProducerObservationError("mismatched host producer event")
    _text(event.get("event_id"), "event_id")
    observed_at = _number(event.get("observed_at"), "observed_at")
    now = _number(clock.wall_time(), "clock.wall_time")
    if observed_at > now or now - observed_at > MAX_EVENT_AGE_SECONDS:
        raise ProducerObservationError("stale host producer event")

    expected_capability = {
        "purpose": "producer_observation",
        "host_session_id": host_session_id,
        "host_turn_id": host_turn_id,
        "run_id": run_id,
        "kernel_id": None,
        "task_id": task_id,
        "stage": stage,
        "request_or_output_digest": output_digest,
        "contract_fingerprint": output_contract_fingerprint,
    }
    projection = {
        "schema": PRODUCER_OBSERVATION_SCHEMA,
        "run_id": values["run_id"],
        "task_id": values["task_id"],
        "stage": stage,
        "producer": values["producer"],
        "host": host,
        "host_session_or_turn": (host_producer_identity or
                                 f"{host_session_id}:{host_turn_id}"),
        "output_path": values["output_path"],
        "output_bytes": len(output_bytes),
        "output_sha256": output_digest,
        "output_schema_id": values["output_schema_id"],
        "output_contract_fingerprint": values["output_contract_fingerprint"],
        "source_sha": values["source_sha"],
        "observed_at": observed_at,
    }
    receipt = {**projection, "fingerprint": content_fingerprint(projection)}
    validate_producer_observation(receipt)
    operation_id = f"{run_id}:{task_id}:{stage}:{event['event_id']}"
    durable_intent = _prepare_observation_intent(
        evidence_store, operation_id=operation_id, receipt=receipt,
        expected_head=predecessor_fingerprint)
    if durable_intent is not None:
        intent_path, intent_value = durable_intent
        if intent_value["phase"] in {"capability_consumed", "committed"}:
            reconcile_observation_intents(evidence_store)
            return receipt
        prepared = None
    else:
        # Compatibility fallback for injected stores which expose only the
        # protocol. Production and sandbox stores use the phase-aware intent.
        prepared = evidence_store.prepare(
            "producer_observation", operation_id, receipt,
            expected_head=predecessor_fingerprint)

    # The exact reconciliation input is durable before authority is consumed,
    # but it is not eligible for evidence commit until the consumption result
    # itself has been durably acknowledged.
    try:
        capability_receipt = capability_source.consume(
            capability_handle,
            expected_bindings=expected_capability,
            now=now,
        )
    except DeliveryPortError as exc:
        raise ProducerObservationError(str(exc)) from exc
    if not isinstance(capability_receipt, Mapping):
        raise ProducerObservationError(
            "host capability consumption receipt is invalid")
    capability_receipt_fingerprint = content_fingerprint(
        dict(capability_receipt))
    if durable_intent is not None:
        intent_value = _advance_observation_intent(
            intent_path, intent_value, phase="capability_consumed",
            capability_receipt_fingerprint=capability_receipt_fingerprint)
        prepared = evidence_store.prepare(
            "producer_observation", operation_id, receipt,
            expected_head=predecessor_fingerprint)
    committed = evidence_store.commit(prepared)
    if durable_intent is not None:
        _advance_observation_intent(
            intent_path, intent_value, phase="committed",
            capability_receipt_fingerprint=capability_receipt_fingerprint,
            evidence_receipt_fingerprint=
                _evidence_receipt_fingerprint(committed))
    return receipt
