"""Host entry, phase runtime receipts, and pinned role references.

The enforcement kernel supplies its existing local signer, contract slots,
dispatch queue, and durable JSON primitives.  This module owns the Design
protocol: portable role references, exact-set dispatch authority, lifecycle
activity publication, replay resistance, and completion conservation.
"""

from __future__ import annotations

import hashlib
import base64
import hmac
import json
import os
from pathlib import Path
import re
import stat
import secrets
import time
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, TypeAlias, cast

if TYPE_CHECKING:
    from . import host_capabilities, run_artifacts, stage_entities, storage
    from . import producer_observation, review_evidence, stage_handoff
else:
    try:
        from . import host_capabilities, run_artifacts, stage_entities, storage
    except (ImportError, ValueError):  # direct-module compatibility
        import run_artifacts
        import storage
        import host_capabilities
        import stage_entities


JsonDict: TypeAlias = dict[str, Any]


ROLE_REFERENCE_SCHEMA = "taskplane.role-reference/v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class NativeEntryError(ValueError):
    """A diagnostic entry binding was refused before engine execution."""


_RUNTIME_PURPOSE = "accepted-runtime-receipt"
_RUNTIME_POLICY_SCHEMA = "taskplane.host-runtime-signing-policy/v1"


@dataclass(frozen=True)
class RuntimeReceiptAuthority:
    """Host-local purpose and exact-attempt admission, never worker authority.

    This value contains HMAC trust and must never enter a portable package.
    Reload it at each effect boundary so rotation and emergency disabling are
    current. Mathematical historical verification grants no current effect.
    """

    bindings: Mapping[str, object]
    freshness: Mapping[str, object]
    keys: Mapping[str, stage_handoff.SigningKey] = field(repr=False)
    key_id: str
    now: int
    expires_at: int
    durable_evidence: bool = False

    def require_current(self) -> None:
        key = self.keys[self.key_id]
        if key.status != "active" or not key.not_before <= self.now < min(
            key.not_after, self.expires_at
        ):
            raise NativeEntryError("runtime signing key is disabled, not yet valid or stale")

    def verify(
        self,
        receipt: JsonDict,
        *,
        store: review_evidence.ArtifactStore | None = None,
        historical: bool = False,
    ) -> JsonDict:
        from taskplane import stage_handoff

        key = self.keys.get(cast(str, receipt.get("key_id")))
        if key is None or receipt.get("key_id") != self.key_id:
            raise NativeEntryError("runtime signing key is not admitted for this attempt")
        if (not historical or self.durable_evidence) and key.status != "active":
            raise NativeEntryError("runtime signing key is disabled")
        if self.durable_evidence and receipt.get("issued_at", self.now + 1) > self.now:
            raise NativeEntryError("runtime receipt is from the future")
        checked = stage_handoff.verify_contract(
            receipt,
            trusted_keys=self.keys,
            expected_schema=stage_entities.AGENT_RUNTIME_SCHEMA,
            expected_freshness=self.freshness,
            now=self.now,
            store=store,
            historical=historical or self.durable_evidence,
        )
        result = cast(JsonDict, checked["payload"])
        if result["status"] != "accepted" or any(
            result.get(k) != v for k, v in self.bindings.items()
        ):
            raise NativeEntryError("runtime receipt differs from admitted attempt bindings")
        if receipt["expires_at"] > self.expires_at:
            raise NativeEntryError("runtime receipt exceeds admitted freshness interval")
        return checked

    def sign(
        self, result: Mapping[str, object], *, store: review_evidence.ArtifactStore | None = None
    ) -> JsonDict:
        from taskplane import stage_handoff

        self.require_current()
        if result.get("status") != "accepted" or any(
            result.get(k) != v for k, v in self.bindings.items()
        ):
            raise NativeEntryError("runtime signing requires the exact accepted output")
        receipt = stage_handoff.sign_contract(
            result,
            key=self.keys[self.key_id],
            issued_at=self.now,
            expires_at=self.expires_at,
            freshness=self.freshness,
            store=store,
        )
        self.verify(receipt, store=store)
        return receipt


def runtime_receipt_authority(
    kernel: Any,
    workspace: str,
    *,
    bindings: Mapping[str, object],
    freshness: Mapping[str, object],
    now: int,
    admit: bool = False,
    authorize: Callable[[], object] | None = None,
    collection_policy: str | None = None,
    resource_policy_fingerprint: str | None = None,
    original_freshness: Mapping[str, object] | None = None,
) -> RuntimeReceiptAuthority:
    """Admit a purpose-limited key only through the incumbent host owner.

    The private policy is separate from worker lifecycle and nonce secrets.
    Admissions are immutable exact-operation records. Expiry or disabling
    cannot cause automatic reissuance, and payload/key IDs never add trust.
    The kernel's existing lock and atomic-write primitives retain custody.
    """
    from taskplane import stage_handoff
    from datetime import datetime

    fresh = stage_handoff._freshness(freshness)
    original_fresh = (
        fresh if original_freshness is None else stage_handoff._freshness(original_freshness)
    )
    if original_freshness is not None and (
        collection_policy is None
        or (
            bindings.get("phase_id") != "build"
            and any(fresh[key] != original_fresh[key] for key in ("candidate_sha", "source_tree"))
        )
    ):
        raise NativeEntryError("current validation changed original source authority")
    operation = bindings.get("operation_id")
    if not isinstance(operation, str) or not operation:
        raise NativeEntryError("runtime signing operation missing")
    if type(now) is not int or now < 0:
        raise NativeEntryError("runtime signing time invalid")
    if collection_policy is not None and not re.fullmatch(r"[a-f0-9]{64}", collection_policy):
        raise NativeEntryError("runtime collection policy invalid")
    if resource_policy_fingerprint is not None and (
        collection_policy is None
        or bindings.get("phase_id") != "build"
        or not re.fullmatch(r"[a-f0-9]{64}", resource_policy_fingerprint)
    ):
        raise NativeEntryError("runtime Build collection resource policy invalid")
    original_operation = operation
    if collection_policy is not None:
        operation += "-collection-" + collection_policy
    path = Path(kernel.tp_dir(workspace)) / "runtime-receipt-authority.json"
    identity = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()
    with kernel.file_lock(str(path)):
        if path.exists() and (
            path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o077
            or path.stat().st_uid != os.getuid()
        ):
            raise NativeEntryError("runtime signing custody is not private")
        policy = kernel.load_json(str(path), default=None, what="runtime receipt signing policy")
        if policy is None:
            if not admit:
                raise NativeEntryError("runtime signing authority missing")
            if authorize is None:
                raise NativeEntryError("runtime signing admission requires current host authority")
            if authorize() is False:
                raise NativeEntryError("runtime signing host authority refused admission")
            policy = {
                "schema": _RUNTIME_POLICY_SCHEMA,
                "purpose": _RUNTIME_PURPOSE,
                "workspace": identity,
                "keys": {},
                "admissions": {},
            }
        if (
            not isinstance(policy, dict)
            or set(policy) != {"schema", "purpose", "workspace", "keys", "admissions"}
            or policy["schema"] != _RUNTIME_POLICY_SCHEMA
            or policy["purpose"] != _RUNTIME_PURPOSE
            or policy["workspace"] != identity
        ):
            raise NativeEntryError("runtime signing purpose or owner mismatch")
        if not isinstance(policy["keys"], dict) or not isinstance(policy["admissions"], dict):
            raise NativeEntryError("runtime signing key admission policy malformed")
        original = policy["admissions"].get(original_operation)
        if (
            collection_policy is not None
            and original is not None
            and (
                original.get("bindings") != dict(bindings)
                or original.get("freshness") != original_fresh
                or policy["keys"].get(original.get("key_id"), {}).get("status") != "active"
            )
        ):
            raise NativeEntryError("original runtime signing authority changed or is disabled")
        admission = policy["admissions"].get(operation)
        if admission is None and admit:
            if authorize is None:
                raise NativeEntryError("runtime signing admission requires current host authority")
            if authorize() is False:
                raise NativeEntryError("runtime signing host authority refused admission")
            expires = int(
                datetime.fromisoformat(str(bindings["deadline"]).replace("Z", "+00:00")).timestamp()
            )
            if collection_policy is not None and (
                bindings.get("phase_id") != "build" or resource_policy_fingerprint is not None
            ):
                # Resource duration is not an authorization expiry. This new,
                # exact-operation signing admission is authorized by the run's
                # explicit human policy; original admissions remain immutable.
                # The independent cryptographic signing window is one day.
                expires = now + 86400
            if expires <= now:
                raise NativeEntryError("runtime signing admission is stale")
            secret = secrets.token_bytes(32)
            key_id = "runtime-" + secrets.token_hex(16)
            policy["keys"][key_id] = {
                "key_id": key_id,
                "secret": base64.b64encode(secret).decode("ascii"),
                "not_before": now,
                "not_after": expires,
                "status": "active",
                "changed_at": None,
            }
            admission = {
                "bindings": dict(bindings),
                "freshness": fresh,
                "key_id": key_id,
                "expires_at": expires,
            }
            policy["admissions"][operation] = admission
            kernel.atomic_write_json(str(path), policy, sort_keys=True, private=True)
        if (
            not isinstance(admission, dict)
            or set(admission) != {"bindings", "freshness", "key_id", "expires_at"}
            or admission["bindings"] != dict(bindings)
            or admission["freshness"] != fresh
        ):
            raise NativeEntryError("runtime signing attempt is missing or changed")
        keys = {}
        for key_id, raw in policy["keys"].items():
            if not isinstance(raw, dict) or set(raw) != {
                "key_id",
                "secret",
                "not_before",
                "not_after",
                "status",
                "changed_at",
            }:
                raise NativeEntryError("runtime signing key policy malformed")
            key = stage_handoff.SigningKey(
                **{**raw, "secret": base64.b64decode(raw["secret"], validate=True)}
            )
            if key.key_id != key_id:
                raise NativeEntryError("runtime signing key identity mismatch")
            keys[key_id] = key
        if admission["key_id"] not in keys:
            raise NativeEntryError("runtime signing key is unadmitted")
        return RuntimeReceiptAuthority(
            dict(bindings),
            fresh,
            keys,
            admission["key_id"],
            now,
            admission["expires_at"],
            collection_policy is not None,
        )


def disable_runtime_receipt_authority(
    kernel: Any,
    workspace: str,
    *,
    bindings: Mapping[str, object],
    freshness: Mapping[str, object],
    now: int,
    authorize: Callable[[], object] | None,
    status: str,
    changed_at: int,
) -> None:
    """Host-only monotone retirement/revocation; retain historical key bytes.

    New operations receive separate keys through normal admission. Neither
    rotation nor disabling rewrites an old admission or grants a new attempt.
    """
    if (
        status not in {"retired", "revoked", "compromised"}
        or type(changed_at) is not int
        or changed_at < now
    ):
        raise NativeEntryError("runtime signing disable policy invalid")
    current = runtime_receipt_authority(
        kernel, workspace, bindings=bindings, freshness=freshness, now=now
    )
    path = Path(kernel.tp_dir(workspace)) / "runtime-receipt-authority.json"
    with kernel.file_lock(str(path)):
        if authorize is None or authorize() is False:
            raise NativeEntryError("runtime signing host authority refused disabling")
        policy = kernel.load_json(str(path), what="runtime receipt signing policy")
        admission = policy["admissions"].get(bindings["operation_id"])
        if (
            admission is None
            or admission["key_id"] != current.key_id
            or admission["bindings"] != dict(bindings)
        ):
            raise NativeEntryError("runtime signing admission changed before disabling")
        raw = policy["keys"][current.key_id]
        if raw["status"] != "active":
            if raw["status"] == status and raw["changed_at"] == changed_at:
                return
            raise NativeEntryError("runtime signing disabling is immutable")
        if changed_at < raw["not_before"]:
            raise NativeEntryError("runtime signing disabling predates admission")
        raw.update(status=status, changed_at=changed_at)
        kernel.atomic_write_json(str(path), policy, sort_keys=True, private=True)


def phase_nonce_source(
    kernel: Any, workspace: str, run_id: str, *, existing_only: bool = False
) -> producer_observation.AttemptNonceSource:
    """Compose existing private worker-key custody with the nonce owner.

    This opens no new authority or observation source and makes no native
    readiness claim. Admission remains with the current stage authority.
    """
    from taskplane import delivery_ports, producer_observation

    authority = kernel._worker_contract_authority(workspace, create=not existing_only)
    root = kernel.external_store_root(workspace)
    workspace_key = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()
    if existing_only:
        domain = (
            Path(root) / ".taskplane-evidence" / workspace_key / run_id / "producer_observation"
        )
        if not (domain / "nonce-state.json").is_file() or any(
            not (domain / child).is_dir() for child in ("intents", "receipts")
        ):
            raise NativeEntryError("existing phase nonce custody is unavailable")
    evidence = delivery_ports.LocatorEvidenceStore(root, workspace_key, run_id)
    return producer_observation.AttemptNonceSource(evidence, key=authority["secret"])


def observe_phase_hook(
    kernel: Any,
    workspace: str,
    contract: Mapping[str, object],
    event: Mapping[str, object],
    *,
    nonce: producer_observation.AttemptNonceSource,
    bindings: Mapping[str, object],
    outputs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Bind a claimed child hook to the existing enforced worker slot."""
    lifecycle = contract.get("worker_lifecycle")
    if not isinstance(lifecycle, Mapping) or contract.get("worker_scoped") is not True:
        raise NativeEntryError("phase worker contract missing")
    if (os.environ.get("TASKPLANE_HOOK_PATH") or "").lower() not in {"native", "bridge"}:
        raise NativeEntryError("claimed native phase hook required")
    if lifecycle.get("owner") != kernel._worker_event_owner(dict(event)):
        raise NativeEntryError("phase hook owner mismatch")
    issued = nonce.recover(bindings)
    return nonce.record_phase_hook(
        issued,
        bindings,
        workspace=workspace,
        task_name=str(lifecycle["expected_task_name"]),
        event=event,
        outputs=outputs,
    )


def _entry_text(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise NativeEntryError("invalid_binding: nonempty text required")
    return value


def _entry_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise NativeEntryError("invalid_binding: string sequence required")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or "\x00" in item:
            raise NativeEntryError("invalid_binding: string sequence required")
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class NativeEntryRequest:
    """Diagnostic request only; never a capability or permission to launch."""

    workspace: str
    engine_candidates: tuple[str, ...]
    selected_engine: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    contract_bytes: bytes
    expected_contract_fingerprint: str
    run_id: str
    attempt_id: str
    owner_attempt_id: str
    operation_id: str
    task_slot: str
    enforcement_mode: str
    host_kind: str
    host_version: str
    session_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "taskplane.native-entry-request/v1",
            "workspace": self.workspace,
            "engine_candidates": list(self.engine_candidates),
            "selected_engine": self.selected_engine,
            "arguments": list(self.arguments),
            "environment": [list(row) for row in self.environment],
            "contract_bytes": self.contract_bytes.decode("utf-8"),
            "expected_contract_fingerprint": self.expected_contract_fingerprint,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "owner_attempt_id": self.owner_attempt_id,
            "operation_id": self.operation_id,
            "task_slot": self.task_slot,
            "enforcement_mode": self.enforcement_mode,
            "host_kind": self.host_kind,
            "host_version": self.host_version,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> NativeEntryRequest:
        fields = set(cls.__dataclass_fields__) | {"schema"}
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value.get("schema") != "taskplane.native-entry-request/v1"
        ):
            raise NativeEntryError("invalid_binding: request shape")
        pairs = value["environment"]
        if not isinstance(pairs, list):
            raise NativeEntryError("invalid_binding: environment")
        environment: list[tuple[str, str]] = []
        for pair in pairs:
            row = _entry_strings(pair)
            if len(row) != 2:
                raise NativeEntryError("invalid_binding: environment pair")
            environment.append((_entry_text(row[0]), row[1]))
        return cls(
            workspace=_entry_text(value["workspace"]),
            engine_candidates=_entry_strings(value["engine_candidates"]),
            selected_engine=_entry_text(value["selected_engine"]),
            arguments=_entry_strings(value["arguments"]),
            environment=tuple(environment),
            contract_bytes=_entry_text(value["contract_bytes"]).encode("utf-8"),
            expected_contract_fingerprint=_entry_text(value["expected_contract_fingerprint"]),
            run_id=_entry_text(value["run_id"]),
            attempt_id=_entry_text(value["attempt_id"]),
            owner_attempt_id=_entry_text(value["owner_attempt_id"]),
            operation_id=_entry_text(value["operation_id"]),
            task_slot=_entry_text(value["task_slot"]),
            enforcement_mode=_entry_text(value["enforcement_mode"]),
            host_kind=_entry_text(value["host_kind"]),
            host_version=_entry_text(value["host_version"]),
            session_id=_entry_text(value["session_id"]),
        )

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True)
class NativeEntryPreflight:
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    contract_bytes: bytes
    enforcement_mode: str
    request_fingerprint: str
    observation: Mapping[str, object]


def prepare_native_entry(
    request: NativeEntryRequest,
    snapshot: host_capabilities.HostCapabilitySnapshot | None,
) -> NativeEntryPreflight:
    """Inspect the incumbent inputs without activating a phase writer.

    S0 has no verified complete native canary. Even a structurally live hook
    snapshot therefore yields degraded observation, never executable readiness.
    A future host adapter must prove the complete envelope before changing this.
    """
    request = NativeEntryRequest.from_dict(request.to_dict())
    if len(request.engine_candidates) != 1:
        raise NativeEntryError("invalid_or_ambiguous_engine: exactly one candidate required")
    engine = Path(request.engine_candidates[0])
    root = engine.parent.parent
    candidate = host_capabilities.valid_plugin_root(str(root), str(root))
    if (
        candidate is None
        or not engine.is_absolute()
        or str(engine.resolve()) != candidate[2]
        or request.selected_engine != str(engine)
    ):
        raise NativeEntryError("invalid_or_ambiguous_engine: selected engine is invalid")
    try:
        contract = stage_entities.read_contract_json(request.contract_bytes)
    except ValueError as exc:
        raise NativeEntryError("invalid_binding: contract reader refused input") from exc
    authority = contract.get("authority")
    if (
        contract.get("schema") != stage_entities.SCHEMA
        or contract.get("fingerprint") != request.expected_contract_fingerprint
        or contract.get("run_id") != request.run_id
        or not isinstance(authority, dict)
        or authority.get("session_id") != request.session_id
        or request.enforcement_mode not in {"strict", "warn", "off"}
        or not re.fullmatch(r"task_[a-zA-Z0-9_-]+", request.task_slot)
        or len(dict(request.environment)) != len(request.environment)
        or dict(request.environment).get("TASKPLANE_TASK") != request.task_slot
    ):
        raise NativeEntryError("invalid_binding: contract, owner, task or enforcement is foreign")
    if snapshot is not None and (
        snapshot.workspace_fingerprint
        != hashlib.sha256(
            os.path.normcase(os.path.realpath(request.workspace)).encode("utf-8")
        ).hexdigest()
        or snapshot.session_fingerprint != hashlib.sha256(request.session_id.encode()).hexdigest()
        or snapshot.host != request.host_kind
        or snapshot.host_version != request.host_version
    ):
        raise NativeEntryError("invalid_binding: host capability is foreign")
    available: list[str] = []
    missing = [
        "real_canary",
        "host_owned_start_identity",
        "host_owned_terminal_identity",
        "collected_output",
        "duplicate_convergence",
        "missing_event_refusal",
    ]
    requirements = {
        "hook_execution": "native_plugin_hooks_loaded",
        "stable_event_identity": "stable_event_identity",
        "managed_policy_permission": "managed_policy_permission",
    }
    for name, capability in requirements.items():
        row = snapshot.capabilities.get(capability) if snapshot else None
        (available if row and row.status == "supported" else missing).append(name)
    if snapshot is None:
        missing.append("current_host_snapshot")
    fingerprint = request.fingerprint
    observation: dict[str, object] = {
        "schema": "taskplane.native-entry-preflight/v1",
        "action": "refusal",
        "request_fingerprint": fingerprint,
        "receipt_id": "entry-" + fingerprint,
        "run_id": request.run_id,
        "stage_id": contract["stage_id"],
        "attempt_id": request.attempt_id,
        "owner_attempt_id": request.owner_attempt_id,
        "operation_id": request.operation_id,
        "task_slot": request.task_slot,
        "contract_fingerprint": request.expected_contract_fingerprint,
        "host_kind": request.host_kind,
        "host_version": request.host_version,
        "capability_fingerprint": snapshot.fingerprint if snapshot else None,
        "available_capabilities": sorted(available),
        "missing_capabilities": sorted(missing),
        "last_observed_event": None,
        "effect_state": "none",
        "reconciliation_action": "repair_capability_then_run_real_canary",
        "evidence_mode": "degraded_observation",
        "error_code": "unsupported_capability",
        "blocking_journeys": ["J0", "J1", "J6", "sign-off", "publication"],
        "native_identity_claimed": False,
        "ready": False,
        "success": False,
    }
    return NativeEntryPreflight(
        command=(sys.executable, request.selected_engine, *request.arguments),
        environment=request.environment,
        contract_bytes=request.contract_bytes,
        enforcement_mode=request.enforcement_mode,
        request_fingerprint=fingerprint,
        observation=observation,
    )


def consume_native_entry(
    request: NativeEntryRequest,
    prepared: NativeEntryPreflight,
    snapshot: host_capabilities.HostCapabilitySnapshot | None,
) -> dict[str, object]:
    """Recheck the exact producer edge; copied receipt identity grants nothing."""
    # Recompute the diagnostic expectation from its exact external inputs.
    expected = _prepare_native_entry(request, snapshot)
    if prepared != expected:
        raise NativeEntryError("invalid_binding: foreign or contradictory observation")
    return dict(prepared.observation)


_prepare_native_entry = prepare_native_entry


def _fp(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def portable_role_reference(agent: str) -> JsonDict:
    role = str(agent or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", role):
        raise ValueError("role reference agent is invalid")
    relative = f"agents/{role}.md"
    path = Path(__file__).resolve().parent.parent / relative
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("role reference is not a regular package file")
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("role reference is unavailable") from exc
    material = {
        "schema": ROLE_REFERENCE_SCHEMA,
        "path": relative,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    return {**material, "fingerprint": _fp(material)}


def validate_role_reference(value: object, *, expected_agent: str) -> JsonDict:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "path", "bytes", "sha256", "fingerprint"}
        or value.get("schema") != ROLE_REFERENCE_SCHEMA
    ):
        raise ValueError("role reference shape is invalid")
    relative = str(value.get("path") or "")
    expected = f"agents/{str(expected_agent or '').strip()}.md"
    if (
        relative != expected
        or os.path.isabs(relative)
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("role reference is absolute, foreign, or unsafe")
    current = portable_role_reference(expected_agent)
    if value != current:
        raise ValueError("role reference content is stale or foreign")
    return current
