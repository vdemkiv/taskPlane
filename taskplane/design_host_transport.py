"""Host transport for dynamically selected Design lens workers.

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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from . import host_capabilities, run_artifacts, stage_entities, stage_migration, storage
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
DISPATCH_INTENT_SCHEMA = "taskplane.design-lens-dispatch-intent/v1"
HOST_AUTHORITY_SCHEMA = "taskplane.design-lens-host-authority/v1"
HOST_RECEIPT_SCHEMA = "taskplane.worker-host-receipt/v1"
HOST_RECEIPT_FIELDS = frozenset({
    "schema", "key_id", "receipt_id", "event", "workspace_fingerprint",
    "run_id", "stage_instance_id", "team_plan_fingerprint",
    "candidate_fingerprint", "lens", "task_name", "task_slot",
    "role_reference_fingerprint", "owner", "issued_at", "signature",
})
TERMINAL_FIELDS = frozenset({
    "schema", "key_id", "receipt_id", "release_action_id",
    "workspace_fingerprint", "slot", "contract_id", "stage", "task",
    "owner", "outcome", "submission_status", "terminal_at", "authority",
    "signature",
})
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
    keys: Mapping[str, object] = field(repr=False)
    key_id: str
    now: int
    expires_at: int
    durable_evidence: bool = False

    def require_current(self):
        key = self.keys[self.key_id]
        if key.status != "active" or not key.not_before <= self.now < min(key.not_after, self.expires_at):
            raise NativeEntryError("runtime signing key is disabled, not yet valid or stale")

    def verify(self, receipt, *, store=None, historical=False):
        from taskplane import stage_handoff
        key = self.keys.get(receipt.get("key_id"))
        if key is None or receipt.get("key_id") != self.key_id:
            raise NativeEntryError("runtime signing key is not admitted for this attempt")
        if (not historical or self.durable_evidence) and key.status != "active":
            raise NativeEntryError("runtime signing key is disabled")
        if self.durable_evidence and receipt.get("issued_at", self.now + 1) > self.now:
            raise NativeEntryError("runtime receipt is from the future")
        checked = stage_handoff.verify_contract(receipt, trusted_keys=self.keys,
            expected_schema=stage_entities.AGENT_RUNTIME_SCHEMA,
            expected_freshness=self.freshness, now=self.now,
            store=store, historical=historical or self.durable_evidence)
        result = checked["payload"]
        if result["status"] != "accepted" or any(result.get(k) != v for k, v in self.bindings.items()):
            raise NativeEntryError("runtime receipt differs from admitted attempt bindings")
        if receipt["expires_at"] > self.expires_at:
            raise NativeEntryError("runtime receipt exceeds admitted freshness interval")
        return checked

    def sign(self, result, *, store=None):
        from taskplane import stage_handoff
        self.require_current()
        if result.get("status") != "accepted" or any(result.get(k) != v for k, v in self.bindings.items()):
            raise NativeEntryError("runtime signing requires the exact accepted output")
        receipt = stage_handoff.sign_contract(result, key=self.keys[self.key_id],
            issued_at=self.now, expires_at=self.expires_at, freshness=self.freshness, store=store)
        self.verify(receipt, store=store)
        return receipt


def runtime_receipt_authority(kernel, workspace: str, *, bindings, freshness,
                              now: int, admit=False, authorize=None, collection_policy: str | None = None,
                              original_freshness=None):
    """Admit a purpose-limited key only through the incumbent host owner.

    The private policy is separate from worker lifecycle and nonce secrets.
    Admissions are immutable exact-operation records. Expiry or disabling
    cannot cause automatic reissuance, and payload/key IDs never add trust.
    The kernel's existing lock and atomic-write primitives retain custody.
    """
    from taskplane import stage_handoff
    from datetime import datetime
    fresh = stage_handoff._freshness(freshness)
    original_fresh = fresh if original_freshness is None else stage_handoff._freshness(original_freshness)
    if original_freshness is not None and (collection_policy is None or any(
            fresh[key] != original_fresh[key] for key in ("candidate_sha", "source_tree"))):
        raise NativeEntryError("current validation changed original source authority")
    operation = bindings.get("operation_id")
    if not isinstance(operation, str) or not operation:
        raise NativeEntryError("runtime signing operation missing")
    if type(now) is not int or now < 0:
        raise NativeEntryError("runtime signing time invalid")
    if collection_policy is not None and not re.fullmatch(r"[a-f0-9]{64}", collection_policy):
        raise NativeEntryError("runtime collection policy invalid")
    original_operation = operation
    if collection_policy is not None:
        operation += "-collection-" + collection_policy
    path = Path(kernel.tp_dir(workspace)) / "runtime-receipt-authority.json"
    identity = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()
    with kernel.file_lock(str(path)):
        if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.stat().st_mode)
                or path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid()):
            raise NativeEntryError("runtime signing custody is not private")
        policy = kernel.load_json(str(path), default=None, what="runtime receipt signing policy")
        if policy is None:
            if not admit:
                raise NativeEntryError("runtime signing authority missing")
            if authorize is None:
                raise NativeEntryError("runtime signing admission requires current host authority")
            if authorize() is False:
                raise NativeEntryError("runtime signing host authority refused admission")
            policy = {"schema": _RUNTIME_POLICY_SCHEMA, "purpose": _RUNTIME_PURPOSE,
                "workspace": identity, "keys": {}, "admissions": {}}
        if not isinstance(policy, dict) or set(policy) != {
                "schema", "purpose", "workspace", "keys", "admissions"} or \
                policy["schema"] != _RUNTIME_POLICY_SCHEMA or policy["purpose"] != _RUNTIME_PURPOSE or \
                policy["workspace"] != identity:
            raise NativeEntryError("runtime signing purpose or owner mismatch")
        if not isinstance(policy["keys"], dict) or not isinstance(policy["admissions"], dict):
            raise NativeEntryError("runtime signing key admission policy malformed")
        original = policy["admissions"].get(original_operation)
        if collection_policy is not None and original is not None and (
                original.get("bindings") != dict(bindings) or original.get("freshness") != original_fresh or
                policy["keys"].get(original.get("key_id"), {}).get("status") != "active"):
            raise NativeEntryError("original runtime signing authority changed or is disabled")
        admission = policy["admissions"].get(operation)
        if admission is None and admit:
            if authorize is None:
                raise NativeEntryError("runtime signing admission requires current host authority")
            if authorize() is False:
                raise NativeEntryError("runtime signing host authority refused admission")
            expires = int(datetime.fromisoformat(str(bindings["deadline"]).replace("Z", "+00:00")).timestamp())
            if collection_policy is not None:
                # Resource duration is not an authorization expiry. This new,
                # exact-operation signing admission is authorized by the run's
                # explicit human policy; original admissions remain immutable.
                # The independent cryptographic signing window is one day.
                expires = now + 86400
            if expires <= now:
                raise NativeEntryError("runtime signing admission is stale")
            secret = secrets.token_bytes(32)
            key_id = "runtime-" + secrets.token_hex(16)
            policy["keys"][key_id] = {"key_id": key_id,
                "secret": base64.b64encode(secret).decode("ascii"),
                "not_before": now, "not_after": expires, "status": "active", "changed_at": None}
            admission = {"bindings": dict(bindings), "freshness": fresh,
                "key_id": key_id, "expires_at": expires}
            policy["admissions"][operation] = admission
            kernel.atomic_write_json(str(path), policy, sort_keys=True, private=True)
        if not isinstance(admission, dict) or set(admission) != {
                "bindings", "freshness", "key_id", "expires_at"} or \
                admission["bindings"] != dict(bindings) or admission["freshness"] != fresh:
            raise NativeEntryError("runtime signing attempt is missing or changed")
        keys = {}
        for key_id, raw in policy["keys"].items():
            if not isinstance(raw, dict) or set(raw) != {
                    "key_id", "secret", "not_before", "not_after", "status", "changed_at"}:
                raise NativeEntryError("runtime signing key policy malformed")
            key = stage_handoff.SigningKey(**{**raw,
                "secret": base64.b64decode(raw["secret"], validate=True)})
            if key.key_id != key_id:
                raise NativeEntryError("runtime signing key identity mismatch")
            keys[key_id] = key
        if admission["key_id"] not in keys:
            raise NativeEntryError("runtime signing key is unadmitted")
        return RuntimeReceiptAuthority(dict(bindings), fresh, keys,
            admission["key_id"], now, admission["expires_at"], collection_policy is not None)


def disable_runtime_receipt_authority(kernel, workspace: str, *, bindings,
        freshness, now: int, authorize, status: str, changed_at: int):
    """Host-only monotone retirement/revocation; retain historical key bytes.

    New operations receive separate keys through normal admission. Neither
    rotation nor disabling rewrites an old admission or grants a new attempt.
    """
    if status not in {"retired", "revoked", "compromised"} or type(changed_at) is not int or changed_at < now:
        raise NativeEntryError("runtime signing disable policy invalid")
    current = runtime_receipt_authority(kernel, workspace, bindings=bindings,
        freshness=freshness, now=now)
    path = Path(kernel.tp_dir(workspace)) / "runtime-receipt-authority.json"
    with kernel.file_lock(str(path)):
        if authorize is None or authorize() is False:
            raise NativeEntryError("runtime signing host authority refused disabling")
        policy = kernel.load_json(str(path), what="runtime receipt signing policy")
        admission = policy["admissions"].get(bindings["operation_id"])
        if admission is None or admission["key_id"] != current.key_id or admission["bindings"] != dict(bindings):
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


def phase_nonce_source(kernel, workspace: str, run_id: str, *, existing_only: bool = False):
    """Compose existing private worker-key custody with the nonce owner.

    This opens no new authority or observation source and makes no native
    readiness claim. Admission remains with the current stage authority.
    """
    from taskplane import delivery_ports, producer_observation
    authority = kernel._worker_contract_authority(workspace, create=not existing_only)
    root = kernel.external_store_root(workspace)
    workspace_key = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()
    if existing_only:
        domain = Path(root) / ".taskplane-evidence" / workspace_key / run_id / "producer_observation"
        if not (domain / "nonce-state.json").is_file() or any(
                not (domain / child).is_dir() for child in ("intents", "receipts")):
            raise NativeEntryError("existing phase nonce custody is unavailable")
    evidence = delivery_ports.LocatorEvidenceStore(root,
        workspace_key, run_id)
    return producer_observation.AttemptNonceSource(evidence, key=authority["secret"])


def observe_phase_hook(kernel, workspace: str, contract: Mapping[str, object],
                       event: Mapping[str, object], *, nonce, bindings,
                       outputs=None):
    """Bind a claimed child hook to the existing enforced worker slot."""
    lifecycle = contract.get("worker_lifecycle")
    if not isinstance(lifecycle, Mapping) or contract.get("worker_scoped") is not True:
        raise NativeEntryError("phase worker contract missing")
    if (os.environ.get("TASKPLANE_HOOK_PATH") or "").lower() not in {"native", "bridge"}:
        raise NativeEntryError("claimed native phase hook required")
    if lifecycle.get("owner") != kernel._worker_event_owner(dict(event)):
        raise NativeEntryError("phase hook owner mismatch")
    issued = nonce.recover(bindings)
    return nonce.record_phase_hook(issued, bindings, workspace=workspace,
        task_name=str(lifecycle["expected_task_name"]), event=event, outputs=outputs)


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
    if TYPE_CHECKING or __package__:
        from . import tp as cli
        from . import stage_migration as migration
    else:
        import tp as flat_cli
        import stage_migration as flat_migration

        cli = flat_cli
        migration = flat_migration
    request = NativeEntryRequest.from_dict(request.to_dict())
    if len(request.engine_candidates) != 1:
        raise NativeEntryError("invalid_or_ambiguous_engine: exactly one candidate required")
    engine = Path(request.engine_candidates[0])
    root = engine.parent.parent
    candidate = cli._valid_plugin_root(str(root), str(root))
    if (
        candidate is None
        or not engine.is_absolute()
        or str(engine.resolve()) != candidate[2]
        or request.selected_engine != str(engine)
    ):
        raise NativeEntryError("invalid_or_ambiguous_engine: selected engine is invalid")
    try:
        retained = migration.read_compatible_contract(request.contract_bytes)
    except ValueError as exc:
        raise NativeEntryError("invalid_binding: contract reader refused input") from exc
    contract = retained.payload
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
        contract_bytes=retained.source_bytes,
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
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()


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
    material = {"schema": ROLE_REFERENCE_SCHEMA, "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
    return {**material, "fingerprint": _fp(material)}


def validate_role_reference(value: object, *, expected_agent: str) -> JsonDict:
    if not isinstance(value, dict) or set(value) != {
            "schema", "path", "bytes", "sha256", "fingerprint"} or \
            value.get("schema") != ROLE_REFERENCE_SCHEMA:
        raise ValueError("role reference shape is invalid")
    relative = str(value.get("path") or "")
    expected = f"agents/{str(expected_agent or '').strip()}.md"
    if (relative != expected or os.path.isabs(relative) or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("role reference is absolute, foreign, or unsafe")
    current = portable_role_reference(expected_agent)
    if value != current:
        raise ValueError("role reference content is stale or foreign")
    return current


def _plan_fingerprint(plan: JsonDict) -> str:
    return _fp({key: item for key, item in plan.items()
                if key not in {"fingerprint", "host_authority"}})


def _team_stage(plan: JsonDict) -> str:
    schema = plan.get("schema")
    if schema not in {"taskplane.design-team-plan/v1", "taskplane.plan-team-plan/v1"}:
        raise ValueError("lens team stage schema is invalid")
    return "plan" if schema == "taskplane.plan-team-plan/v1" else "design"


def _authority_key(lifecycle: JsonDict) -> str:
    return "plan_host_authority" if lifecycle.get("stage") == "plan-lens" else "design_host_authority"


def _validate_plan(kernel: Any, plan: object) -> list[JsonDict]:
    if not isinstance(plan, dict) or not _DIGEST.fullmatch(str(
            plan.get("fingerprint") or "")) or plan.get(
                "fingerprint") != _plan_fingerprint(plan):
        raise ValueError("Design lens team plan fingerprint is invalid")
    stage = _team_stage(plan)
    selected = [str(value) for value in plan.get("selected") or []]
    workers = [dict(value) for value in plan.get("workers") or []
               if isinstance(value, dict)]
    if (not selected or len(selected) != len(set(selected)) or
            len(selected) > 16 or len(workers) != len(selected) or
            {str(row.get("lens") or "") for row in workers} != set(selected)):
        raise ValueError("Design lens selected and worker sets differ")
    names: set[str] = set()
    slots: set[str] = set()
    for worker in workers:
        lens = str(worker.get("lens") or "")
        task_name = str(worker.get("task_name") or "")
        slot = str(worker.get("task_slot") or "")
        output = str(worker.get("output") or "")
        expected_output = f"{stage}/lenses/{lens}.json"
        if (not lens or not kernel._TASK_SLOT_RE.fullmatch(slot) or
                task_name != kernel.dispatch_task_name(
                    "lens", "tp-lens", f"{stage}-{lens}") or
                task_name in names or slot in slots or
                output != expected_output):
            raise ValueError("Design lens worker identity is invalid")
        contract = worker.get("contract")
        if (not isinstance(contract, dict) or
                contract.get("read_only") is not True or
                contract.get("write_allow") != [expected_output]):
            raise ValueError("Design lens worker contract is invalid")
        role = validate_role_reference(
            worker.get("role_reference"), expected_agent="tp-lens")
        intent = worker.get("dispatch_intent")
        intent_fields = {
            "schema", "run_id", "stage_instance_id",
            "candidate_fingerprint", "lens", "task_name", "task_slot",
            "role_reference_fingerprint", "model_tier", "model",
            "reasoning_effort", "settings_digest", "output", "fingerprint",
        }
        if (not isinstance(intent, dict) or set(intent) != intent_fields or
                intent.get("schema") != f"taskplane.{stage}-lens-dispatch-intent/v1" or
                intent.get("fingerprint") != _fp({
                    key: item for key, item in intent.items()
                    if key != "fingerprint"})):
            raise ValueError("Design lens dispatch intent shape is invalid")
        expected = {
            "run_id": plan.get("run_id"),
            "stage_instance_id": plan.get("stage_instance_id"),
            "candidate_fingerprint": plan.get("candidate_fingerprint"),
            "lens": lens, "task_name": task_name, "task_slot": slot,
            "model_tier": worker.get("model_tier"),
            "model": worker.get("model"),
            "reasoning_effort": worker.get("reasoning_effort"),
            "settings_digest": plan.get("settings_digest"),
            "output": output, "role_reference_fingerprint": role["fingerprint"],
        }
        if any(intent.get(key) != value for key, value in expected.items()):
            raise ValueError("Design lens dispatch intent is severed")
        names.add(task_name)
        slots.add(slot)
    return workers


def _receipt(kernel: Any, workspace: str, *, event: str, plan: JsonDict,
             worker: JsonDict, owner: JsonDict | None,
             now: int | None = None) -> JsonDict:
    if event not in {"assignment", "start"}:
        raise ValueError("worker host receipt event is unsupported")
    authority = kernel._worker_contract_authority(workspace, create=True)
    role = validate_role_reference(
        worker.get("role_reference"), expected_agent="tp-lens")
    issued_at = int(time.time() if now is None else now)
    identity = {
        "workspace": kernel._workspace_identity_fingerprint(workspace),
        "event": event, "team_plan": plan.get("fingerprint"),
        "lens": worker.get("lens"), "task_name": worker.get("task_name"),
        "task_slot": worker.get("task_slot"), "owner": owner,
        "issued_at": issued_at,
    }
    value = {
        "schema": HOST_RECEIPT_SCHEMA, "key_id": authority["key_id"],
        "receipt_id": "worker-host-" + _fp(identity)[:24], "event": event,
        "workspace_fingerprint": kernel._workspace_identity_fingerprint(
            workspace),
        "run_id": plan.get("run_id"),
        "stage_instance_id": plan.get("stage_instance_id"),
        "team_plan_fingerprint": plan.get("fingerprint"),
        "candidate_fingerprint": plan.get("candidate_fingerprint"),
        "lens": worker.get("lens"), "task_name": worker.get("task_name"),
        "task_slot": worker.get("task_slot"),
        "role_reference_fingerprint": role["fingerprint"],
        "owner": None if owner is None else dict(owner),
        "issued_at": issued_at,
    }
    value["signature"] = kernel._worker_signature(authority["secret"], value)
    return value


def _signed(kernel: Any, workspace: str, value: object, *, event: str) -> JsonDict:
    if (not isinstance(value, dict) or set(value) != HOST_RECEIPT_FIELDS or
            value.get("schema") != HOST_RECEIPT_SCHEMA or
            value.get("event") != event):
        raise kernel._worker_lifecycle_error(
            workspace, "worker host receipt schema is malformed")
    authority = kernel._worker_contract_authority(workspace, create=False)
    if (value.get("key_id") != authority["key_id"] or
            not hmac.compare_digest(str(value.get("signature") or ""),
                                    kernel._worker_signature(
                                        authority["secret"], value))):
        raise kernel._worker_lifecycle_error(
            workspace, "worker host receipt signature is invalid")
    return dict(value)


def verify_worker_host_receipt(kernel: Any, workspace: str, value: object,
                               *, event: str, plan: JsonDict,
                               worker: JsonDict,
                               owner: JsonDict | None = None) -> JsonDict:
    checked = _signed(kernel, workspace, value, event=event)
    role = validate_role_reference(
        worker.get("role_reference"), expected_agent="tp-lens")
    expected = {
        "workspace_fingerprint": kernel._workspace_identity_fingerprint(
            workspace),
        "run_id": plan.get("run_id"),
        "stage_instance_id": plan.get("stage_instance_id"),
        "team_plan_fingerprint": plan.get("fingerprint"),
        "candidate_fingerprint": plan.get("candidate_fingerprint"),
        "lens": worker.get("lens"), "task_name": worker.get("task_name"),
        "task_slot": worker.get("task_slot"),
        "role_reference_fingerprint": role["fingerprint"],
        "owner": None if owner is None else owner,
    }
    if any(checked.get(key) != value for key, value in expected.items()):
        raise kernel._worker_lifecycle_error(
            workspace, "worker host receipt is foreign, stale, or replayed")
    issued_at = checked.get("issued_at")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int) or \
            issued_at < 0:
        raise kernel._worker_lifecycle_error(
            workspace, "worker host receipt time is invalid")
    return checked


def _artifact_store(workspace: str, plan: JsonDict, root: str,
                    binding: JsonDict) -> tuple[str, JsonDict]:
    identity = storage.resolve_repository_identity(workspace)
    expected_root = storage.resolve_layout(
        identity, run_id=str(plan.get("run_id") or "")).artifact_root
    supplied = os.path.realpath(os.path.abspath(str(root)))
    if supplied != os.path.realpath(expected_root):
        raise ValueError("run artifact root is foreign to this run")
    checked = run_artifacts.validate_binding(binding)
    manifest = run_artifacts.load_manifest(supplied)
    if manifest.get("binding") != checked:
        raise ValueError("run artifact binding is foreign")
    if (checked.get("repository_id") != identity.repo_id or
            checked.get("run_id") != plan.get("run_id") or
            checked.get("stage_instance_id") != plan.get("stage_instance_id") or
            checked.get("settings_digest") != plan.get("settings_digest") or
            (checked.get("candidate") or {}).get("fingerprint") !=
            plan.get("candidate_fingerprint")):
        raise ValueError("run artifact binding is stale or severed")
    run_artifacts.verify_manifest(supplied, expected_binding=checked)
    return supplied, checked


def register_design_lens_dispatch_plan(
        kernel: Any, workspace: str, plan: JsonDict, *, artifact_root: str,
        artifact_binding: JsonDict,
        now: int | None = None) -> JsonDict:
    workers = _validate_plan(kernel, plan)
    stage = _team_stage(plan)
    authority_key = f"{stage}_host_authority"
    root, binding = _artifact_store(
        workspace, plan, artifact_root, artifact_binding)
    path = kernel._dispatch_path(workspace, "expected_dispatch.json")
    authorized: dict[str, JsonDict] = {}
    with kernel._file_lock(path):
        queue = kernel._load_queue_strict(path)
        for worker in workers:
            lens = str(worker["lens"])
            matches = [row for row in queue
                       if row.get("task_name") == worker["task_name"] and
                       isinstance(row.get(authority_key), dict) and
                       row[authority_key].get(
                           "team_plan_fingerprint") == plan["fingerprint"]]
            if len(matches) > 1:
                raise kernel._worker_lifecycle_error(
                    workspace, "Design lens dispatch registration is ambiguous")
            if matches:
                private = dict(matches[0][authority_key])
                assignment = verify_worker_host_receipt(
                    kernel, workspace, private.get("assignment_receipt"),
                    event="assignment", plan=plan, worker=worker)
            else:
                for prior in queue:
                    if (not prior.get("matched") and
                            prior.get("task_name") == worker["task_name"]):
                        prior["matched"] = True
                        prior["superseded"] = True
                assignment = _receipt(
                    kernel, workspace, event="assignment", plan=plan,
                    worker=worker, owner=None, now=now)
                private = {
                    "schema": f"taskplane.{stage}-lens-host-authority/v1",
                    "team_plan_fingerprint": plan["fingerprint"],
                    "artifact_root": root, "artifact_binding": binding,
                    "artifact_binding_fingerprint": binding["fingerprint"],
                    "dispatch_intent": dict(worker["dispatch_intent"]),
                    "plan_binding": {
                        "fingerprint": plan["fingerprint"],
                        "run_id": plan.get("run_id"),
                        "stage_instance_id": plan.get("stage_instance_id"),
                        "candidate_fingerprint": plan.get(
                            "candidate_fingerprint"),
                        "settings_digest": plan.get("settings_digest"),
                    },
                    "worker_binding": {key: worker.get(key) for key in (
                        "lens", "task_name", "task_slot", "output",
                        "role_reference", "model_tier", "model",
                        "reasoning_effort")},
                    "assignment_receipt": assignment,
                }
                queue.append({
                    "ts": kernel._now(), "kind": f"{stage}-lens",
                    "agent": "tp-lens",
                    "ref": f"{plan['fingerprint']}:{lens}",
                    "task_name": worker["task_name"],
                    "role_marker": worker["role_marker"],
                    "model_tier": worker.get("model_tier"),
                    "model": worker.get("model"),
                    "reasoning_effort": worker.get("reasoning_effort"),
                    "matched": False,
                    "intent_id": worker["dispatch_intent"]["fingerprint"],
                    "intent_run_id": plan.get("run_id"),
                    authority_key: private,
                })
            authorized[lens] = {
                "task_name": worker["task_name"],
                "task_slot": worker["task_slot"],
                "dispatch_intent_fingerprint": worker[
                    "dispatch_intent"]["fingerprint"],
                "role_reference_fingerprint": worker[
                    "role_reference"]["fingerprint"],
                "assignment_receipt": assignment,
            }
        kernel._save_queue(path, queue)
    material = {
        "schema": f"taskplane.{stage}-lens-host-authority/v1",
        "team_plan_fingerprint": plan["fingerprint"],
        "run_id": plan.get("run_id"),
        "stage_instance_id": plan.get("stage_instance_id"),
        "candidate_fingerprint": plan.get("candidate_fingerprint"),
        "artifact_binding_fingerprint": binding["fingerprint"],
        "workers": authorized,
    }
    return {**material, "fingerprint": _fp(material)}


def attach_design_lens_host_authority(
        contract: JsonDict, worker_authority: JsonDict, *, artifact_root: str,
        artifact_binding: JsonDict) -> JsonDict:
    if not isinstance(contract, dict) or contract.get("worker_scoped") is not True:
        raise ValueError("Design lens authority needs a prepared worker contract")
    lifecycle = contract.get("worker_lifecycle") or {}
    if lifecycle.get("stage") not in {"design-lens", "plan-lens"}:
        raise ValueError("lens authority stage is invalid")
    stage = str(lifecycle["stage"]).removesuffix("-lens")
    row = dict(worker_authority or {})
    assignment = row.get("assignment_receipt")
    if (not isinstance(assignment, dict) or
            lifecycle.get("expected_task_name") != row.get("task_name") or
            lifecycle.get("slot") != row.get("task_slot") or
            assignment.get("task_name") != row.get("task_name") or
            assignment.get("task_slot") != row.get("task_slot")):
        raise ValueError("Design lens contract and assignment are severed")
    decoded: object = json.loads(json.dumps(contract))
    if not isinstance(decoded, dict):
        raise ValueError("Design lens contract cannot be represented as an object")
    output: JsonDict = decoded
    output["worker_lifecycle"]["dispatch_intent_id"] = str(
        row.get("dispatch_intent_fingerprint") or "")
    output["worker_lifecycle"]["dispatch_intent_run_id"] = str(
        assignment.get("run_id") or "")
    output["worker_lifecycle"][f"{stage}_host_authority"] = {
        "schema": f"taskplane.{stage}-lens-host-authority/v1",
        "artifact_root": os.path.realpath(os.path.abspath(artifact_root)),
        "artifact_binding": json.loads(json.dumps(artifact_binding)),
        "worker_authority": row,
    }
    return output


def _contract_authority(kernel: Any, workspace: str,
                        contract: JsonDict) -> JsonDict | None:
    lifecycle = contract.get("worker_lifecycle") or {}
    key = _authority_key(lifecycle)
    if lifecycle.get("plan_host_authority") is not None and key != "plan_host_authority":
        raise ValueError("Plan lens authority has a foreign lifecycle stage")
    private = lifecycle.get(key)
    if private is None:
        return None
    if not isinstance(private, dict) or private.get("schema") != \
            f"taskplane.{key.removesuffix('_host_authority')}-lens-host-authority/v1":
        raise kernel._worker_lifecycle_error(
            workspace, "Design lens host authority is malformed")
    row = private.get("worker_authority")
    binding = run_artifacts.validate_binding(private.get("artifact_binding"))
    if not isinstance(row, dict):
        raise kernel._worker_lifecycle_error(
            workspace, "Design lens host authority binding is incomplete")
    assignment = _signed(
        kernel, workspace, row.get("assignment_receipt"), event="assignment")
    lifecycle = contract["worker_lifecycle"]
    if (assignment.get("task_name") != lifecycle.get("expected_task_name") or
            assignment.get("task_slot") != lifecycle.get("slot")):
        raise kernel._worker_lifecycle_error(
            workspace, "Design lens assignment does not match worker contract")
    root = os.path.realpath(os.path.abspath(str(private.get("artifact_root") or "")))
    manifest = run_artifacts.load_manifest(root)
    if (manifest.get("binding") != binding or
            binding.get("run_id") != assignment.get("run_id") or
            binding.get("stage_instance_id") != assignment.get(
                "stage_instance_id") or
            (binding.get("candidate") or {}).get("fingerprint") !=
            assignment.get("candidate_fingerprint")):
        raise kernel._worker_lifecycle_error(
            workspace, "Design lens artifact authority is foreign")
    run_artifacts.verify_manifest(root, expected_binding=binding)
    return {"root": root, "binding": binding, "row": row,
            "assignment": assignment}


def _append_once(kernel: Any, workspace: str, authority: JsonDict, *,
                 event_type: str, receipt: JsonDict,
                 owner: JsonDict | None,
                 details: JsonDict | None = None,
                 usage_reference: JsonDict | None = None,
                 evidence_references: list[JsonDict] | None = None) -> JsonDict:
    root = authority["root"]
    manifest = run_artifacts.load_manifest(root)
    receipt_id = str(receipt.get("receipt_id") or "")
    for entry in manifest["classes"]["agent-activity"]["entries"]:
        metadata = entry.get("metadata") or {}
        if (metadata.get("event_type") == event_type and
                (metadata.get("details") or {}).get("receipt_id") ==
                receipt_id):
            return dict(entry)
    assignment = authority["assignment"]
    appended: object = run_artifacts.append_activity(
        root, event_type=event_type, agent_attempt_id=receipt_id,
        worker_id=str((owner or {}).get("agent_id") or
                      assignment["task_name"]),
        task_id=str(assignment["task_slot"]), lens=str(assignment["lens"]),
        details={"receipt_id": receipt_id, "receipt": dict(receipt),
                 "team_plan_fingerprint": assignment[
                     "team_plan_fingerprint"], **dict(details or {})},
        usage_reference=usage_reference,
        evidence_references=evidence_references or [])
    if not isinstance(appended, dict):
        raise ValueError("Design lens activity append result is malformed")
    return dict(appended)


def record_design_dispatch_assignment_activity(
        kernel: Any, workspace: str, expected: JsonDict) -> JsonDict | None:
    private = (expected or {}).get("plan_host_authority") or (expected or {}).get("design_host_authority")
    if not isinstance(private, dict):
        return None
    plan = dict(private.get("plan_binding") or {})
    plan["fingerprint"] = private.get("team_plan_fingerprint")
    worker = dict(private.get("worker_binding") or {})
    assignment = verify_worker_host_receipt(
        kernel, workspace, private.get("assignment_receipt"),
        event="assignment", plan=plan, worker=worker)
    root, binding = _artifact_store(
        workspace, plan, str(private.get("artifact_root") or ""),
        dict(private.get("artifact_binding") or {}))
    return _append_once(
        kernel, workspace,
        {"root": root, "binding": binding, "assignment": assignment},
        event_type="assignment", receipt=assignment, owner=None)


def record_design_worker_start_activity(
        kernel: Any, workspace: str, binding: JsonDict, event: JsonDict,
        *, now: int | None = None) -> JsonDict | None:
    contract = binding.get("contract") if isinstance(binding, dict) else None
    if not isinstance(contract, dict):
        return None
    authority = _contract_authority(kernel, workspace, contract)
    if authority is None:
        return None
    lifecycle = contract["worker_lifecycle"]
    owner = kernel._worker_event_owner(event)
    existing = lifecycle.get(_authority_key(lifecycle).replace("authority", "start_receipt"))
    if existing is not None:
        start = _signed(kernel, workspace, existing, event="start")
        if start.get("owner") != owner:
            raise kernel._worker_lifecycle_error(
                workspace, "Design lens start receipt belongs to another child")
    else:
        assignment = authority["assignment"]
        start = _receipt(kernel, workspace, event="start", plan={
            "fingerprint": assignment["team_plan_fingerprint"],
            "run_id": assignment["run_id"],
            "stage_instance_id": assignment["stage_instance_id"],
            "candidate_fingerprint": assignment["candidate_fingerprint"],
        }, worker={
            "lens": assignment["lens"], "task_name": assignment["task_name"],
            "task_slot": assignment["task_slot"],
            "role_reference": portable_role_reference("tp-lens"),
        }, owner=owner, now=now)
        lifecycle[_authority_key(lifecycle).replace("authority", "start_receipt")] = start
        kernel.atomic_write_json(
            kernel.active_contract_path(workspace, binding["slot"]),
            contract, indent=2)
        binding["contract"] = contract
        authority = _contract_authority(kernel, workspace, contract) or authority
    _append_once(kernel, workspace, authority, event_type="worker-identity",
                 receipt=start, owner=owner,
                 details={"session_id": owner["session_id"],
                          "task_name": owner["task_name"]})
    started = _append_once(kernel, workspace, authority, event_type="start",
                           receipt=start, owner=owner)
    _append_once(kernel, workspace, authority, event_type="progress",
                 receipt=start, owner=owner, details={"state": "active"})
    return started


def record_design_worker_activity(kernel: Any, workspace: str,
                                  event: JsonDict, *,
                                  event_type: str) -> JsonDict | None:
    if event_type not in {"progress", "attention"}:
        raise ValueError("Design worker activity type is unsupported")
    contract = kernel.load_active_for_event(workspace, event)
    if not isinstance(contract, dict):
        return None
    authority = _contract_authority(kernel, workspace, contract)
    if authority is None:
        return None
    start = _signed(kernel, workspace, contract["worker_lifecycle"].get(
        _authority_key(contract["worker_lifecycle"]).replace("authority", "start_receipt")), event="start")
    owner = kernel._worker_event_owner(event)
    message = str(event.get("message") or event.get("reason") or "") \
        .replace("\x00", "")[:2048]
    return _append_once(
        kernel, workspace, authority, event_type=event_type, receipt=start,
        owner=owner, details={"message": message,
                              "turn_id": str(event.get("turn_id") or "")[:160]})


def _result_sha256(workspace: str, relative: str) -> str | None:
    path = os.path.abspath(os.path.join(workspace, relative))
    if os.path.realpath(path) != path or os.path.commonpath([path, os.path.abspath(workspace)]) != os.path.abspath(workspace):
        raise ValueError("lens result path is indirect or foreign")
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def design_terminal_activity(kernel: Any, workspace: str, contract: JsonDict,
                             receipt: JsonDict,
                             event: JsonDict | None) -> list[JsonDict]:
    authority = _contract_authority(kernel, workspace, contract)
    if authority is None:
        return []
    owner = contract["worker_lifecycle"].get("owner")
    observed = event if isinstance(event, dict) else {}
    usage = next((dict(observed[key]) for key in (
        "usage_reference", "usage", "token_usage")
                  if isinstance(observed.get(key), dict)), None)
    evidence = [dict(item) for item in
                observed.get("evidence_references") or []
                if isinstance(item, dict)]
    rows = []
    outcome = receipt.get("outcome")
    result_details = {}
    if contract["worker_lifecycle"].get("stage") == "plan-lens":
        lens = authority["assignment"]["lens"]
        result_details["result_sha256"] = _result_sha256(workspace, f"plan/lenses/{lens}.json")
    semantic = ({"cancellation": "cancel", "interruption": "interruption",
                 "handoff": "handoff"}.get(outcome)
                if isinstance(outcome, str) else None)
    if semantic:
        rows.append(_append_once(
            kernel, workspace, authority, event_type=semantic,
            receipt=receipt, owner=owner,
            details={"outcome": receipt["outcome"]}))
    if usage is not None:
        rows.append(_append_once(
            kernel, workspace, authority, event_type="usage-reference",
            receipt=receipt, owner=owner, usage_reference=usage,
            details={"outcome": receipt["outcome"]}))
    if evidence:
        rows.append(_append_once(
            kernel, workspace, authority, event_type="evidence-reference",
            receipt=receipt, owner=owner, evidence_references=evidence,
            details={"outcome": receipt["outcome"]}))
    rows.append(_append_once(
        kernel, workspace, authority, event_type="terminal", receipt=receipt,
        owner=owner, usage_reference=usage, evidence_references=evidence,
        details={"outcome": receipt["outcome"],
                 "submission_status": receipt["submission_status"],
                 "authority": receipt["authority"], **result_details}))
    return rows


def _authority_projection(kernel: Any, workspace: str, plan: JsonDict,
                          authority: object,
                          workers: list[JsonDict]) -> JsonDict:
    fields = {"schema", "team_plan_fingerprint", "run_id",
              "stage_instance_id", "candidate_fingerprint",
              "artifact_binding_fingerprint", "workers", "fingerprint"}
    if (not isinstance(authority, dict) or set(authority) != fields or
            authority.get("schema") != f"taskplane.{_team_stage(plan)}-lens-host-authority/v1" or
            authority.get("fingerprint") != _fp({
                key: item for key, item in authority.items()
                if key != "fingerprint"})):
        raise ValueError("Design lens host authority shape is invalid")
    for key in ("run_id", "stage_instance_id", "candidate_fingerprint"):
        if authority.get(key) != plan.get(key):
            raise ValueError(f"Design lens host authority {key} is severed")
    rows = authority.get("workers")
    if not isinstance(rows, dict) or set(rows) != {
            str(worker["lens"]) for worker in workers}:
        raise ValueError("Design lens host authority set is incomplete")
    for worker in workers:
        row = rows[str(worker["lens"])]
        expected = {
            "task_name": worker["task_name"], "task_slot": worker["task_slot"],
            "dispatch_intent_fingerprint": worker[
                "dispatch_intent"]["fingerprint"],
            "role_reference_fingerprint": worker[
                "role_reference"]["fingerprint"],
        }
        if (not isinstance(row, dict) or set(row) != {
                *expected, "assignment_receipt"} or
                any(row.get(key) != value for key, value in expected.items())):
            raise ValueError("Design lens worker authority is severed")
        verify_worker_host_receipt(
            kernel, workspace, row["assignment_receipt"], event="assignment",
            plan=plan, worker=worker)
    return dict(authority)


def _activities(workspace: str, plan: JsonDict,
                authority: JsonDict) -> list[JsonDict]:
    identity = storage.resolve_repository_identity(workspace)
    root = storage.resolve_layout(identity, run_id=str(plan["run_id"])).artifact_root
    manifest = run_artifacts.load_manifest(root)
    binding = manifest["binding"]
    if (binding.get("fingerprint") != authority.get(
            "artifact_binding_fingerprint") or
            binding.get("repository_id") != identity.repo_id or
            binding.get("run_id") != plan.get("run_id") or
            binding.get("stage_instance_id") != plan.get("stage_instance_id") or
            (binding.get("candidate") or {}).get("fingerprint") !=
            plan.get("candidate_fingerprint")):
        raise ValueError("Design lens activity artifact binding is foreign")
    run_artifacts.verify_manifest(root, expected_binding=binding)
    return list(manifest["classes"]["agent-activity"]["entries"])


def _event_receipts(entries: list[JsonDict], event_type: str,
                    lens: str, *, team_fingerprint: str | None = None) -> list[JsonDict]:
    receipts: list[JsonDict] = []
    for entry in entries:
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        details = metadata.get("details")
        if not isinstance(details, dict):
            continue
        if team_fingerprint is not None and details.get("team_plan_fingerprint") != team_fingerprint:
            continue
        receipt = details.get("receipt")
        if (metadata.get("event_type") == event_type and
                metadata.get("lens") == lens and isinstance(receipt, dict)):
            receipts.append(dict(receipt))
    return receipts


def _terminal(kernel: Any, workspace: str, receipt: object, *,
              worker: JsonDict, start: JsonDict, stage: str = "design") -> JsonDict:
    if (not isinstance(receipt, dict) or set(receipt) != TERMINAL_FIELDS or
            receipt.get("schema") != kernel.WORKER_TERMINAL_RECEIPT_SCHEMA):
        raise ValueError("Design lens terminal receipt shape is invalid")
    authority = kernel._worker_contract_authority(workspace, create=False)
    if (receipt.get("key_id") != authority["key_id"] or
            not hmac.compare_digest(str(receipt.get("signature") or ""),
                                    kernel._worker_signature(
                                        authority["secret"], receipt))):
        raise ValueError("Design lens terminal receipt signature is invalid")
    if (receipt.get("workspace_fingerprint") !=
            kernel._workspace_identity_fingerprint(workspace) or
            receipt.get("slot") != worker.get("task_slot") or
            receipt.get("contract_id") != worker.get("task_slot") or
            receipt.get("stage") != f"{stage}-lens" or
            receipt.get("task") != worker.get("lens") or
            receipt.get("owner") != start.get("owner") or
            receipt.get("authority") != "host-lifecycle" or
            receipt.get("outcome") != "success"):
        raise ValueError("Design lens terminal receipt is foreign or non-success")
    return dict(receipt)


def validate_design_lens_dispatch_completion(
        kernel: Any, workspace: str, plan: JsonDict,
        authority: object) -> JsonDict:
    errors: list[str] = []
    results: dict[str, JsonDict] = {}
    try:
        workers = _validate_plan(kernel, plan)
        checked = _authority_projection(
            kernel, workspace, plan, authority, workers)
        entries = _activities(workspace, plan, checked)
    except Exception as exc:
        return {"valid": False, "errors": [f"{type(exc).__name__}: {exc}"],
                "workers": {}}
    for worker in workers:
        lens = str(worker["lens"])
        try:
            rows = {event: _event_receipts(entries, event, lens,
                    team_fingerprint=plan["fingerprint"]) for event in
                    ("assignment", "worker-identity", "start", "terminal")}
            if any(len(value) != 1 for value in rows.values()):
                raise ValueError(
                    "needs exactly one assignment, identity, start, and terminal")
            assignment = verify_worker_host_receipt(
                kernel, workspace, rows["assignment"][0], event="assignment",
                plan=plan, worker=worker)
            if assignment != checked["workers"][lens]["assignment_receipt"]:
                raise ValueError("assignment activity differs from authority")
            owner = rows["start"][0].get("owner")
            start = verify_worker_host_receipt(
                kernel, workspace, rows["start"][0], event="start",
                plan=plan, worker=worker, owner=owner)
            if rows["worker-identity"][0] != start:
                raise ValueError("worker identity and start receipts differ")
            terminal = _terminal(
                kernel, workspace, rows["terminal"][0], worker=worker,
                start=start, stage=_team_stage(plan))
            result = kernel.load_json(
                os.path.join(workspace, str(worker["output"])), default=None,
                what="Design lens terminal result")
            material = ({key: item for key, item in result.items()
                         if key != "fingerprint"}
                        if isinstance(result, dict) else {})
            if (not isinstance(result, dict) or
                    result.get("schema") != f"taskplane.{_team_stage(plan)}-lens-result/v1" or
                    result.get("lens") != lens or
                    result.get("worker_identity") != worker["task_name"] or
                    result.get("team_plan_fingerprint") != plan["fingerprint"] or
                    result.get("candidate_fingerprint") != plan.get(
                        "candidate_fingerprint") or
                    result.get("outcome") not in {"pass", "changes-required"} or
                    result.get("fingerprint") != _fp(material)):
                raise ValueError("semantic result contract is invalid")
            if _team_stage(plan) == "plan":
                recorded = [entry["metadata"]["details"].get("result_sha256") for entry in entries
                    if (entry.get("metadata") or {}).get("event_type") == "terminal" and
                    ((entry.get("metadata") or {}).get("details") or {}).get("receipt") == terminal]
                if recorded != [_result_sha256(workspace, str(worker["output"]))] or not recorded[0]:
                    raise ValueError("Plan lens result bytes changed after native terminal")
            results[lens] = {
                "assignment_receipt_id": assignment["receipt_id"],
                "start_receipt_id": start["receipt_id"],
                "terminal_receipt_id": terminal["receipt_id"],
                "result_fingerprint": result["fingerprint"],
                "outcome": result["outcome"],
            }
        except Exception as exc:
            errors.append(f"Design lens {lens}: {type(exc).__name__}: {exc}")
    return {"valid": not errors, "errors": errors, "workers": results}


__all__ = [
    "NativeEntryError", "NativeEntryRequest", "NativeEntryPreflight",
    "prepare_native_entry", "consume_native_entry",
    "DISPATCH_INTENT_SCHEMA", "HOST_AUTHORITY_SCHEMA", "HOST_RECEIPT_SCHEMA",
    "ROLE_REFERENCE_SCHEMA", "attach_design_lens_host_authority",
    "design_terminal_activity", "portable_role_reference",
    "record_design_dispatch_assignment_activity",
    "record_design_worker_activity", "record_design_worker_start_activity",
    "register_design_lens_dispatch_plan", "validate_design_lens_dispatch_completion",
    "validate_role_reference", "verify_worker_host_receipt",
]
