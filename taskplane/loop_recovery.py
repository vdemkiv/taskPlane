"""Audited, human-attributed recovery transitions for the delivery loop."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from contextlib import AbstractContextManager
from typing import Callable, Mapping, Sequence, TypeVar

if __package__:
    from . import delivery_ports, recovery
else:
    import delivery_ports
    import recovery


class LeaseRefusal(ValueError):
    """Portable recovery truth; no private adapter errors or implied readiness."""

    def __init__(self, reason: str, lease: delivery_ports.AttemptLease,
                 effects: Mapping[str, str], continuation: str) -> None:
        self.result = {
            "reason": reason, "phase_id": lease.phase_id,
            "attempt_id": lease.attempt_id, "operation_id": lease.operation_id,
            "effects": dict(effects), "continuation": continuation,
            "retry_semantics": "same_attempt" if continuation == "retry_same_attempt" else "no_relaunch",
            "wait_mechanism": "host_terminal_event" if continuation == "observe_wait_reconcile" else None,
            "prerequisites": (["original_operation_terminal", "effects_reconciled", "lease_released"]
                if continuation == "observe_wait_reconcile" else [continuation]),
        }
        super().__init__(reason)


_Result = TypeVar("_Result")


class LeaseRecovery:
    """Loop-owned recovery using its existing locked, atomic state mutation.

    The composition root supplies loop.mutate, current scoped authorization,
    canonical clock/budgets, and trusted host/nonce adapters. No worker gets
    this control-plane object and no second persistence owner is introduced.
    """

    def __init__(self, workspace: str, *,
                 mutate_state: Callable[[str], AbstractContextManager[dict[str, object] | None]],
                 clock: delivery_ports.Clock,
                 authorize: Callable[[delivery_ports.AttemptLease], bool],
                 usage: Callable[[], Mapping[str, int | None]], limits: Mapping[str, int]) -> None:
        self.workspace = workspace
        self.mutate_state = mutate_state
        self.clock = clock
        self.authorize = authorize
        self.usage = usage
        self.limits = dict(limits)

    @staticmethod
    def _effects(record: Mapping[str, object], lease: delivery_ports.AttemptLease) -> dict[str, str]:
        effects = record.get("effects")
        if isinstance(effects, dict) and set(effects) == set(lease.effect_scope) and all(
                value in {"effect_free", "committed", "observed", "pending", "uncertain"}
                for value in effects.values()):
            return dict(effects)
        # Historic/malformed state cannot become proof of absence of effects.
        return {scope: "uncertain" for scope in lease.effect_scope}

    def _refuse(self, reason: str, lease: delivery_ports.AttemptLease,
                record: Mapping[str, object], continuation: str = "observe_wait_reconcile") -> None:
        raise LeaseRefusal(reason, lease, self._effects(record, lease), continuation)

    def _record(self, state: dict[str, object] | None,
                lease: delivery_ports.AttemptLease) -> dict[str, object]:
        if state is None or state.get("run_id") != lease.run_id:
            self._refuse("run_binding_mismatch", lease, {}, "request_authority")
        if not isinstance(state, dict):
            raise ValueError("active loop state required")
        value = state.get("attempt_lease")
        if not isinstance(value, dict) or value.get("lease") != lease.projection():
            self._refuse("stale_lease", lease, value if isinstance(value, dict) else {})
        return dict(value)

    def _check(self, lease: delivery_ports.AttemptLease, record: dict[str, object]) -> None:
        if self.authorize(lease) is not True:
            self._refuse("authority_revoked", lease, record, "request_authority")
        if record.get("cancel_requested") is True or record.get("released") is True:
            self._refuse("lease_released_or_cancelled", lease, record)
        now = self.clock.wall_time()
        if not lease.issued_at <= now < min(lease.expires_at, lease.heartbeat_deadline):
            self._refuse("lease_deadline_expired", lease, record)
        used = self.usage()
        if not self.limits or set(used) != set(self.limits) or any(
                type(limit) is not int or limit < 1 or type(used[name]) is not int or
                not 0 <= used[name] < limit for name, limit in self.limits.items()):
            self._refuse("budget_exhausted", lease, record, "request_authority")
        failure = record.get("failure")
        if isinstance(failure, dict) and failure.get("status") == "escalate":
            self._refuse(str(failure["reason"]), lease, record, "artifact_correction")

    def admit(self, lease: delivery_ports.AttemptLease, *, expected_fence: int) -> delivery_ports.AttemptLease:
        """CAS admission; expiry alone can reclaim only an effect-free lease."""
        with self.mutate_state(self.workspace) as state:
            if state is None or state.get("run_id") != lease.run_id:
                self._refuse("run_binding_mismatch", lease, {}, "request_authority")
            if not isinstance(state, dict):
                raise ValueError("active loop state required")
            current = state.get("attempt_lease")
            record = dict(current) if isinstance(current, dict) else {}
            previous = record.get("lease")
            fence = previous.get("fencing_token") if isinstance(previous, dict) else 0
            if type(expected_fence) is not int or expected_fence != fence:
                self._refuse("lease_cas_conflict", lease, record)
            if previous == lease.projection():
                self._check(lease, record)
                if any(value != "effect_free" for value in self._effects(record, lease).values()):
                    self._refuse("effects_require_reconciliation", lease, record)
                return lease
            history = list(record.get("history", []))
            if current is not None:
                if not isinstance(previous, dict) or type(fence) is not int or lease.fencing_token <= fence:
                    self._refuse("stale_fence", lease, record)
                if previous.get("run_id") != lease.run_id or previous.get("phase_id") != lease.phase_id or \
                        previous.get("effect_scope") != list(lease.effect_scope):
                    self._refuse("lease_binding_changed", lease, record, "request_authority")
                same = previous.get("attempt_id") == lease.attempt_id
                if same:
                    immutable = ("lease_id", "operation_id", "owner")
                    if any(previous.get(name) != lease.projection()[name] for name in immutable):
                        self._refuse("attempt_input_changed", lease, record, "artifact_correction")
                    if record.get("cancel_requested") or record.get("released") or any(
                            value != "effect_free" for value in self._effects(record, lease).values()):
                        self._refuse("effects_require_reconciliation", lease, record)
                elif not record.get("terminal_identity") or record.get("released") is not True or any(
                        value in {"pending", "uncertain"} for value in self._effects(record, lease).values()):
                    self._refuse("terminal_released_receipt_required", lease, record)
                elif previous.get("operation_id") == lease.operation_id or previous.get("lease_id") == lease.lease_id:
                    self._refuse("replacement_identity_required", lease, record, "request_authority")
                history.append({key: value for key, value in record.items() if key != "history"})
            fresh = {"lease": lease.projection(), "effects": {scope: "effect_free" for scope in lease.effect_scope},
                     "cancel_requested": False, "released": False, "terminal_identity": None,
                     "history": history, "failures": record.get("failures", [])}
            if isinstance(previous, dict) and previous.get("attempt_id") == lease.attempt_id:
                fresh["failure"] = record.get("failure")
            self._check(lease, fresh)
            state["attempt_lease"] = fresh
        return lease

    def revalidate(self, lease: delivery_ports.AttemptLease) -> None:
        with self.mutate_state(self.workspace) as state:
            self._check(lease, self._record(state, lease))

    def execute(self, lease: delivery_ports.AttemptLease, *,
                nonce_action: Callable[[Callable[[], _Result]], _Result],
                action: Callable[[delivery_ports.AttemptLease], _Result],
                paths: Sequence[delivery_ports.EffectPath] = ()) -> _Result:
        # Reserve durably before entering the nonce/host boundary, including
        # callback failure or process loss. Never hold the loop lock over I/O.
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            self._check(lease, record)
            if any(value != "effect_free" for value in self._effects(record, lease).values()):
                self._refuse("effects_require_reconciliation", lease, record)
            record["effects"] = {scope: "uncertain" for scope in lease.effect_scope}
            if isinstance(state, dict):
                state["attempt_lease"] = record
        return delivery_ports.perform_lease_effect(lease, revalidate=self.revalidate,
            nonce_action=nonce_action, action=action, paths=paths)

    def cancel(self, lease: delivery_ports.AttemptLease) -> None:
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            record["cancel_requested"] = True
            if isinstance(state, dict):
                state["attempt_lease"] = record

    def reconcile(self, lease: delivery_ports.AttemptLease,
                  observation: delivery_ports.LeaseTerminalObservation | None) -> None:
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            if observation is None or observation.lease != lease or not observation.terminal_identity or \
                    observation.released is not True or set(observation.effects) != set(lease.effect_scope) or \
                    any(value not in {"effect_free", "committed", "observed"} for value in observation.effects.values()):
                self._refuse("terminal_released_receipt_required", lease, record)
            record.update(effects=dict(observation.effects), released=True,
                          terminal_identity=observation.terminal_identity)
            if isinstance(state, dict):
                state["attempt_lease"] = record

    def record_failure(self, lease: delivery_ports.AttemptLease,
                       failure_class: str, fingerprint: str) -> None:
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            fingerprints = list(record.get("failures", []))
            fingerprints.append(fingerprint)
            decision = recovery.decide_recovery(failure_class=failure_class, attempt=len(fingerprints),
                fingerprints=fingerprints, max_routine_attempts=self.limits.get("attempts", 1))
            record.update(failures=fingerprints, failure=decision)
            if isinstance(state, dict):
                state["attempt_lease"] = record

    def pickup(self, lease: delivery_ports.AttemptLease) -> dict[str, object]:
        """Project the existing record without granting retry or new authority."""
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            try:
                self._check(lease, record)
            except LeaseRefusal as exc:
                return exc.result
            if any(effect != "effect_free" for effect in self._effects(record, lease).values()):
                return LeaseRefusal("effects_require_reconciliation", lease,
                    self._effects(record, lease), "observe_wait_reconcile").result
            failure = record.get("failure")
            kind = failure.get("failure_class") if isinstance(failure, dict) else None
            continuation = {"artifact": "artifact_correction", "setup": "setup_repair"}.get(
                kind, "retry_same_attempt")
            return LeaseRefusal("effect_free_pickup", lease,
                self._effects(record, lease), continuation).result

    def wait(self, lease: delivery_ports.AttemptLease,
             waiter: delivery_ports.EventWaiter) -> Sequence[Mapping[str, object]]:
        """One bounded event wait; events still require authoritative reconciliation."""
        result = self.pickup(lease)
        if result["continuation"] != "observe_wait_reconcile":
            return ()
        return waiter.wait({"mechanism": "host_terminal_event", "operation_id": lease.operation_id},
                           (lease.operation_id,))


REPLANNABLE_STEPS = frozenset({
    "plan_approval", "execute", "evaluate", "fix", "escalated",
})


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _sealed(value: dict) -> dict:
    return {**value, "fingerprint": _fingerprint(value)}


def _meter_identity(meter: Mapping) -> dict:
    from taskplane import native_session_meter
    checked = native_session_meter.validate_root_meter_projection(meter)
    if checked["status"] != "available":
        return checked
    watermark = checked["watermark"]
    return {key: watermark[key] for key in ("schema", "session_role", "session_pseudonym",
        "source_identity_fingerprint", "status_receipt_fingerprint", "first_observed_input_tokens", "resumed")}


def legacy_observation_checkpoint(state: Mapping) -> dict | None:
    """Public counters only; packets never copy a native authenticator."""
    root = state.get("root_hygiene") or {}
    meter = root.get("meter")
    if not meter:
        return None
    _meter_identity(meter)
    return {"ledger_revision": state["dispatch_telemetry"]["revision"],
        "meter_fingerprint": meter["fingerprint"], "identity": _meter_identity(meter),
        "counters": {key: meter.get(key) for key in ("turns", "peak_context_tokens", "context_rent_tokens", "usage")},
        "sequence": (meter.get("watermark") or {}).get("last_sequence")}


def legacy_state_fingerprint(state: Mapping) -> str:
    """Bind workflow authority while independently arriving measurements grow.

    Only root meter observations and their ledger revision can advance. All
    policy, source, settings, identity and worker ledger bindings stay bound.
    """
    projection = copy.deepcopy(dict(state))
    root = projection.get("root_hygiene") or {}
    if root.get("meter"):
        identity = _meter_identity(root["meter"])
        root["meter"] = identity
        ledger = projection["dispatch_telemetry"]
        ledger.pop("revision", None)
        admission = ledger.get("root_admission") or {}
        if admission.get("meter"):
            admission["meter"] = _meter_identity(admission["meter"])
    return _fingerprint(projection)


def _validate_observations(state: Mapping, checkpoint, authority: bytes | None) -> None:
    from taskplane import dispatch_telemetry, native_session_meter
    current = legacy_observation_checkpoint(state)
    if current is None and checkpoint is None:
        return
    if current is None or not isinstance(checkpoint, dict) or set(checkpoint) != set(current):
        raise ValueError("root observation checkpoint changed")
    ledger = state["dispatch_telemetry"]
    dispatch_telemetry.validate_ledger(ledger)
    admission = dispatch_telemetry._validate_root_admission(ledger["root_admission"],
        observation_authority=authority, require_authenticated_meter=True)
    meter = state["root_hygiene"]["meter"]
    native_session_meter.validate_root_meter(meter, authority=authority)
    if meter != admission["meter"] or current["identity"] != checkpoint["identity"]:
        raise ValueError("root meter identity or ledger binding changed")
    for key in ("sequence", "ledger_revision"):
        if type(checkpoint[key]) is not int or type(current[key]) is not int or current[key] < checkpoint[key]:
            raise ValueError("root observation counters moved backwards")
    if current["sequence"] == checkpoint["sequence"] and current["meter_fingerprint"] != checkpoint["meter_fingerprint"]:
        raise ValueError("root observation sequence has conflicting evidence")
    previous = checkpoint["counters"]
    # The meter owns context rent as cached_input_tokens / turns, an average
    # that may fall while every cumulative counter grows. Its authenticated
    # value stays observable, but only cumulative quantities are monotonic.
    for key in ("turns", "peak_context_tokens", "usage"):
        value = current["counters"][key]
        pairs = ((value[name], previous[key][name]) for name in value) if isinstance(value, dict) else [(value, previous[key])]
        if any(isinstance(old, bool) or not isinstance(old, (int, float)) or new < old for new, old in pairs):
            raise ValueError("measured root usage moved backwards")


def _verified(value: object, label: str) -> dict:
    if not isinstance(value, dict) or value.get("fingerprint") != _fingerprint(
            {key: item for key, item in value.items() if key != "fingerprint"}):
        raise ValueError(label + " fingerprint changed")
    return value


def legacy_review_policy(state: Mapping) -> dict | None:
    """Recognize the historical human decision without upgrading its verdict."""
    value = state.get("review_timing_override")
    if value is None:
        return None
    policy = _verified(value, "legacy review policy")
    fields = {"schema", "run_id", "design_fingerprint", "plan_fingerprint", "by",
              "instruction", "mode", "fingerprint"}
    if (set(policy) != fields or policy["schema"] != "taskplane.run-review-timing/v1"
            or policy["run_id"] != state.get("run_id")
            or policy["design_fingerprint"] != state.get("design_fingerprint")
            or policy["plan_fingerprint"] != (state.get("plan_fingerprint") or
                (state.get("delivery_mode_receipt") or {}).get("plan_fingerprint"))
            or not str(policy["by"]).startswith("human:") or not policy["instruction"]
            or policy["mode"] != "build-tests-then-em-lenses" or state.get("parallel")
            or state.get("_stage_run_binding") or state.get("_stage_native_root_authority")):
        raise ValueError("legacy review policy is stale, foreign or unsupported")
    return policy


def _requirement_fingerprint(requirement: Mapping) -> str:
    return _fingerprint({key: requirement.get(key) for key in ("id", "title", "functional", "nfr", "acceptance",
        "open_questions", "contracts", "depends_on", "context_files", "review_policy")})


def _declared_task_matches(task: Mapping, declaration: Mapping, requirement: Mapping) -> bool:
    from taskplane import loop
    if not isinstance(declaration, dict) or task.get("req") != requirement.get("id"):
        return False
    expanded = loop._expanded_task_contracts(requirement, declaration)
    for key, value in declaration.items():
        if key == "contracts":
            if task.get(key) not in (value, expanded):
                return False
        elif task.get(key) != value:
            return False
    return True


def _legacy_results(state: Mapping, plan: Mapping, requirement: Mapping) -> None:
    """Validate existing declarations and non-judged history, never reanchor it."""
    declared = plan.get("tasks")
    tasks = state.get("tasks")
    if not isinstance(tasks, list) or not isinstance(declared, list) or len(tasks) != len(declared):
        raise ValueError("legacy task inventory changed")
    ids = [task.get("id") for task in tasks]
    if len(set(ids)) != len(ids) or any(not isinstance(name, str) or not name for name in ids):
        raise ValueError("legacy task identities are ambiguous")
    policy = legacy_review_policy(state)
    if policy is None:
        raise ValueError("explicit legacy review policy required")
    policies = {policy["fingerprint"]}
    for historic in state.get("review_timing_override_history", []):
        legacy_review_policy({**state, "review_timing_override": historic,
                              "plan_fingerprint": historic.get("plan_fingerprint")})
        policies.add(historic["fingerprint"])
    deferred = []
    passed = {task["id"] for task in tasks if task.get("status") == "passed"}
    for task, declaration in zip(tasks, declared):
        if not _declared_task_matches(task, declaration, requirement):
            raise ValueError("saved task declaration differs from original Plan")
        if task.get("status") == "pending":
            if task.get("evaluation") or task.get("target_commit") or task.get("reanchor_authority"):
                raise ValueError("pending task carries unexpected pass evidence")
            continue
        evaluation = task.get("evaluation") or {}
        if (task.get("status") != "passed" or evaluation.get("verdict") != "non-judged"
                or evaluation.get("task") != task["id"]
                or not set(task.get("deps") or []) <= passed):
            raise ValueError("legacy completion is not unchanged, dependency-closed non-judged Build")
        if evaluation.get("status") == "deferred":
            if (evaluation.get("reason_code") != "human-deferred-to-em"
                    or evaluation.get("policy_fingerprint") not in policies
                    or not re.fullmatch(r"[a-f0-9]{40}", str(evaluation.get("build_candidate", "")))
                    or not re.fullmatch(r"[a-f0-9]{64}", str(evaluation.get("suite_key", "")))
                    or task.get("target_commit") or task.get("reanchor_authority")):
                raise ValueError("deferred Build evidence is missing, stale or promoted to a pass")
            deferred.append(task["id"])
        elif evaluation.get("status") == "unavailable":
            human = task.get("human_resolution") or {}
            if (human.get("decision") != "pass" or not human.get("actor")
                    or not human.get("outage_fingerprint")
                    or human.get("outage_fingerprint") != (evaluation.get("outage_identity") or {}).get("fingerprint")
                    or not task.get("reanchor_authority") or not task.get("target_commit")):
                raise ValueError("historical outage resolution is incomplete")
        else:
            raise ValueError("legacy completion has no attributable non-judged disposition")
    if len(set(state.get("deferred_review_tasks", []))) != len(deferred) or \
            set(state.get("deferred_review_tasks", [])) != set(deferred):
        raise ValueError("deferred review obligations changed")


def legacy_continuation(state: Mapping, workspace: str) -> dict | None:
    """Validate the durable amendment for fresh invocations and resource guards."""
    receipt = state.get("legacy_build_continuation")
    if receipt is None:
        return None
    receipt = _verified(receipt, "legacy continuation")
    if (receipt.get("schema") != "taskplane.legacy-build-continuation/v1"
            or receipt.get("revoked") is not False
            or state.get("_stage_run_binding") or state.get("_stage_native_root_authority")):
        raise ValueError("legacy continuation is revoked or no longer legacy")
    for field in ("run_id", "requirement_id", "baseline", "design_fingerprint", "settings_digest"):
        if receipt.get(field) != state.get(field):
            raise ValueError("legacy continuation identity or policy changed: " + field)
    if receipt.get("artifact_binding_fingerprint") != _fingerprint(state.get("run_artifact_binding")):
        raise ValueError("legacy original artifact binding changed")
    path = Path(workspace) / "plan/tasks.json"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != receipt["after_plan_sha256"]:
        raise ValueError("legacy approved Plan bytes changed")
    plan = json.loads(path.read_bytes())
    if _fingerprint(plan) != receipt["after_plan_fingerprint"]:
        raise ValueError("legacy approved Plan fingerprint changed")
    from taskplane import loop, settings
    requirement = loop.reqs.get_requirement(workspace, state["requirement_id"])
    if not requirement or _requirement_fingerprint(requirement) != receipt.get("requirement_fingerprint"):
        raise ValueError("original requirement content changed")
    tasks = state.get("tasks") or []
    if len(tasks) != len(plan["tasks"]) or any(not _declared_task_matches(task, declaration, requirement)
            or _fingerprint(task.get("contracts")) != receipt["runtime_contract_fingerprints"].get(task["id"])
            for task, declaration in zip(tasks, plan["tasks"])):
        raise ValueError("legacy approved task declarations changed")
    retained = receipt.get("retained_results")
    if not isinstance(retained, dict) or any(
            _fingerprint(next((task for task in tasks if task.get("id") == task_id), None)) != digest
            for task_id, digest in retained.items()):
        raise ValueError("historical Build result changed or was promoted to independent pass")
    if settings.settings_digest(state.get("settings_snapshot") or {}) != receipt["settings_digest"]:
        raise ValueError("legacy settings snapshot changed")
    policy = legacy_review_policy(state)
    if not policy or policy["fingerprint"] != receipt["review_policy_fingerprint"]:
        raise ValueError("legacy review policy changed after continuation")
    resource = _verified(state.get("resource_policy"), "legacy resource policy")
    if (resource.get("schema") != "taskplane.resource-policy/v1"
            or resource.get("run_id") != state.get("run_id") or resource.get("mode") != "advisory"
            or resource.get("actor") != receipt.get("by")
            or resource.get("authority_fingerprint") != receipt["approval_fingerprint"]
            or resource["fingerprint"] != receipt["resource_policy_fingerprint"]):
        raise ValueError("legacy resource policy is stale or foreign")
    return receipt


def continue_build(runtime, workspace: str, *, source: str, by: str, request: str,
                   expected_fingerprint: str, check: bool = False,
                   observation_authority: bytes | None = None) -> dict:
    """Consume an exact human scope append in one existing loop-state transaction.

    Plan preparation is external and grants no authority. A crash after that
    file edit or before this atomic commit is safely recovered with the same
    packet. No command writes both Plan and loop state, dispatches, or clears
    a pending worker. Completed records and the independent-pass verifier
    are untouched.
    """
    from taskplane import delivery_policy, settings
    try:
        if runtime.tp.task_slot() is not None:
            raise ValueError("legacy continuation is orchestrator-only")
        if not isinstance(by, str) or not by.startswith("human:") or not by[6:].strip() or not request.strip():
            raise ValueError("explicit human --by and --request are required")
        path = Path(source)
        if path.is_symlink() or path.stat().st_size > 4_000_000:
            raise ValueError("amendment packet must be a bounded regular file")
        packet = json.loads(path.read_bytes())
        fields = {"schema", "run_id", "requirement_id", "task_id", "baseline", "design_fingerprint",
            "settings_digest", "before_state_fingerprint", "candidate", "source_fingerprint",
            "before_plan", "after_plan_sha256", "settings_snapshot", "resource_limits", "observation_checkpoint",
            "requirement_fingerprint"}
        if (not isinstance(packet, dict) or set(packet) != fields
                or packet["schema"] != "taskplane.legacy-build-amendment/v1"
                or _fingerprint(packet) != expected_fingerprint or packet["resource_limits"] != "advisory"):
            raise ValueError("amendment packet fields or approved fingerprint changed")
        approval = _fingerprint({"packet_fingerprint": expected_fingerprint, "by": by, "request": request})
        # The controller lock matches run_context.operation; the state lock is
        # still the sole persistence owner. --check never enters mutate/save.
        lock = os.path.join(runtime.tp.tp_dir(workspace), "controller-operation")
        with runtime.tp.file_lock(lock):
            state = runtime._load_raw(workspace)
            if state is None:
                raise ValueError("no active legacy loop")
            prior = legacy_continuation(state, workspace)
            if prior is not None:
                if prior["approval_fingerprint"] != approval:
                    raise ValueError("continuation already consumed a different approval; no renewal")
                return {"continued": True, "replay": True, "read_only": True,
                    "run_id": state["run_id"], "dispatch_allowed": False,
                    "independent_review_passed": False, "receipt": prior}
            if legacy_state_fingerprint(state) != packet["before_state_fingerprint"]:
                raise ValueError("before-state fingerprint changed")
            _validate_observations(state, packet["observation_checkpoint"], observation_authority)
            for field in ("run_id", "requirement_id", "baseline", "design_fingerprint", "settings_digest"):
                if packet[field] != state.get(field):
                    raise ValueError("amendment is foreign or stale: " + field)
            if state.get("step") != "execute" or state.get("parallel"):
                raise ValueError("continuation requires serial pending Build")
            if any(state.get(key) for key in ("_submission", "evaluate_child_evidence", "attempt_lease")):
                raise ValueError("active effects must be reconciled before a scope amendment")
            current = runtime._current_task(state)
            if not current or current.get("id") != packet["task_id"] or current.get("status") != "pending":
                raise ValueError("amendment does not name the pending current task")
            if runtime.tp.worker_contract_for_stage(workspace, stage="execute", task=packet["task_id"]) is not None:
                raise ValueError("pending worker contract must be released before scope amendment")
            original = packet["before_plan"]
            requirement = runtime.reqs.get_requirement(workspace, state["requirement_id"])
            if not requirement or _requirement_fingerprint(requirement) != packet["requirement_fingerprint"]:
                raise ValueError("original requirement content differs from approved packet")
            _legacy_results(state, original, requirement)
            policy = legacy_review_policy(state)
            if policy["by"] != by:
                raise ValueError("continuation must identify the original human policy owner")
            delivery = delivery_policy.validate_delivery_mode_receipt(state["delivery_mode_receipt"])
            if delivery["plan_fingerprint"] != _fingerprint(original):
                raise ValueError("original Plan does not match saved approval")
            plan_path = Path(workspace) / "plan/tasks.json"
            if plan_path.is_symlink() or plan_path.stat().st_size > 4_000_000:
                raise ValueError("amended Plan must be a bounded regular file")
            raw = plan_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != packet["after_plan_sha256"]:
                raise ValueError("amended Plan bytes differ from the approved packet")
            amended = json.loads(raw)
            expected = copy.deepcopy(original)
            target = expected["tasks"][state["current_task"]]
            scope = amended["tasks"][state["current_task"]]["scope"]
            old_scope = target["scope"]
            if (not isinstance(scope, list) or scope[:len(old_scope)] != old_scope
                    or len(scope) <= len(old_scope) or len(set(scope)) != len(scope)):
                raise ValueError("only a nonempty exact scope append is supported")
            for name in scope[len(old_scope):]:
                if (not isinstance(name, str) or not name or "\\" in name
                        or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                        or any(part in {".git", ".env", "secrets"} for part in PurePosixPath(name).parts)):
                    raise ValueError("scope append contains an unsafe path")
            target["scope"] = scope
            if amended != expected:
                raise ValueError("scope amendment changed unrelated Plan declarations")
            if (runtime.tp.git_head(workspace) != packet["candidate"]
                    or runtime.tp.workspace_fingerprint(workspace) != packet["source_fingerprint"]):
                raise ValueError("approved source candidate or bytes changed")
            binding = state.get("run_artifact_binding") or {}
            if binding.get("run_id") != state["run_id"] or binding.get("settings_digest") != state["settings_digest"]:
                raise ValueError("original artifact settings binding changed")
            configured = settings.from_snapshot(packet["settings_snapshot"],
                expected_digest=state["settings_digest"], allow_legacy=True)
            if state.get("settings_snapshot") not in (None, configured.to_dict()):
                raise ValueError("existing settings snapshot cannot be replaced")
            fresh = copy.deepcopy(state)
            fresh["settings_snapshot"] = configured.to_dict()
            fresh["tasks"][fresh["current_task"]]["scope"] = list(scope)
            fresh["delivery_mode_receipt"] = delivery_policy.validate_plan_mode(amended,
                plan_fingerprint=_fingerprint(amended), source_sha=packet["candidate"],
                predecessor_fingerprint=delivery["fingerprint"])
            fresh["plan_fingerprint"] = _fingerprint(amended)
            fresh.setdefault("review_timing_override_history", []).append(copy.deepcopy(policy))
            fresh["review_timing_override"] = _sealed({**{k: v for k, v in policy.items() if k != "fingerprint"},
                "plan_fingerprint": _fingerprint(amended)})
            resource = _sealed({"schema": "taskplane.resource-policy/v1", "run_id": state["run_id"],
                "mode": "advisory", "actor": by, "authority_fingerprint": approval, "decided_at": int(time.time())})
            if state.get("resource_policy") is not None:
                raise ValueError("existing resource policy must not be replaced")
            fresh["resource_policy"] = resource
            receipt = _sealed({"schema": "taskplane.legacy-build-continuation/v1", "revoked": False,
                **{k: packet[k] for k in ("run_id", "requirement_id", "task_id", "baseline", "design_fingerprint",
                    "settings_digest", "candidate", "source_fingerprint", "before_state_fingerprint", "after_plan_sha256")},
                "artifact_binding_fingerprint": _fingerprint(binding), "by": by, "request": request,
                "requirement_fingerprint": packet["requirement_fingerprint"],
                "runtime_contract_fingerprints": {task["id"]: _fingerprint(task.get("contracts")) for task in state["tasks"]},
                "packet_fingerprint": expected_fingerprint, "approval_fingerprint": approval,
                "before_plan_fingerprint": _fingerprint(original), "after_plan_fingerprint": _fingerprint(amended),
                "original_delivery_mode_receipt": delivery,
                "after_state_fingerprint": legacy_state_fingerprint(fresh),
                "review_policy_fingerprint": fresh["review_timing_override"]["fingerprint"],
                "resource_policy_fingerprint": resource["fingerprint"], "independent_review_passed": False,
                "retained_results": {task["id"]: _fingerprint(task) for task in state["tasks"]
                    if task.get("status") == "passed"},
                "review_due": "EM", "approved_at": resource["decided_at"]})
            fresh["legacy_build_continuation"] = receipt
            legacy_continuation(fresh, workspace)
            if not check:
                with runtime.mutate(workspace) as locked:
                    if locked is None or legacy_state_fingerprint(locked) != legacy_state_fingerprint(state) \
                            or plan_path.read_bytes() != raw or runtime.tp.git_head(workspace) != packet["candidate"] \
                            or runtime.tp.workspace_fingerprint(workspace) != packet["source_fingerprint"]:
                        raise ValueError("run, Plan or source changed concurrently before commit")
                    if runtime.reqs.get_requirement(workspace, state["requirement_id"]) != requirement:
                        raise ValueError("requirement changed concurrently before commit")
                    _validate_observations(locked, packet["observation_checkpoint"], observation_authority)
                    if legacy_observation_checkpoint(locked) is not None:
                        fresh["root_hygiene"]["meter"] = copy.deepcopy(locked["root_hygiene"]["meter"])
                        fresh["dispatch_telemetry"] = copy.deepcopy(locked["dispatch_telemetry"])
                    locked.clear()
                    locked.update(fresh)
            return {"continued": not check, "checked": check, "replay": False, "read_only": check,
                "run_id": state["run_id"], "dispatch_allowed": False,
                "independent_review_passed": False, "receipt": receipt}
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        return {"error": "legacy continuation refused: " + str(exc), "dispatch_allowed": False}


def replan(ws: str, *, by: str, reason: str, load_state, mutate_state,
           clear_contract, trace, record_decision) -> dict:
    """Return frozen delivery configuration to a fresh Plan approval.

    This is the governed escape hatch for configuration defects discovered
    after approval. It preserves the frozen tasks in append-only loop history,
    requires human attribution, and forces the replacement plan through the
    human Plan checkpoint even when the original loop omitted that checkpoint.
    """
    by = str(by or "").strip()
    reason = str(reason or "").strip()
    if not by:
        return {"error": "replan requires --by with the human approver"}
    if not reason:
        return {"error": "replan requires --reason describing the defect"}

    state = load_state(ws)
    if state is None:
        return {"error": "no active loop"}
    entry_step = state.get("step")
    if entry_step not in REPLANNABLE_STEPS:
        return {
            "error": "replan is available only after a plan was frozen "
                     "(plan_approval/execute/evaluate/fix/escalated); current "
                     f"step is '{entry_step}'",
            "step": entry_step,
        }

    prior_tasks = []
    with mutate_state(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        if locked.get("step") != entry_step:
            return {
                "error": "the loop advanced concurrently during replan "
                         f"(was '{entry_step}', now '{locked.get('step')}')",
                "step": locked.get("step"),
            }
        # Snapshot from the locked state, not the optimistic read above: a
        # parallel task can settle while this human transition is waiting for
        # the lock without changing the top-level execute step.
        prior_tasks = copy.deepcopy(locked.get("tasks") or [])
        record = {
            "from_step": entry_step,
            "by": by,
            "reason": reason,
            "ts": time.time(),
            "baseline": locked.get("baseline"),
            "tasks": prior_tasks,
        }
        locked.setdefault("replan_history", []).append(record)
        locked["step"] = "plan"
        locked["tasks"] = None
        locked["current_task"] = 0
        checkpoints = list(locked.get("checkpoints") or [])
        if "plan" not in checkpoints:
            checkpoints.append("plan")
        locked["checkpoints"] = checkpoints
        for key in ("baseline", "ab", "selection", "_submission",
                    "_suite_evidence", "_validated_suite_evidence",
                    "_build_failed"):
            locked.pop(key, None)

    # The old worker contract governed the frozen task. Release it only after
    # the new state is durable so an interruption never leaves an unrecorded
    # transition. Parallel contracts remain isolated to retired worktrees.
    clear_error = None
    try:
        clear_contract(ws)
    except Exception as exc:
        clear_error = f"{exc.__class__.__name__}: {exc}"
        trace(ws, "loop_replan_contract_release_failed", error=clear_error)
    trace(ws, "loop_replan", from_step=entry_step, by=by, reason=reason,
          archived_tasks=len(prior_tasks))
    try:
        record_decision(
            ws, "Delivery returned to Plan",
            context=f"From: {entry_step}\nBy: {by}\nReason: {reason}",
            decision="The frozen task configuration was archived; a new plan "
                     "and fresh human approval are required.",
            tags=["replan", "human-gate"],
            links={"loop": "replan", "from_step": entry_step})
    except Exception:
        # Loop-state history is authoritative. A KB projection failure must
        # not re-strand delivery after the transition committed.
        trace(ws, "loop_replan_kb_projection_failed", from_step=entry_step,
              by=by)
    out = {
        "step": "plan",
        "replanned": True,
        "from_step": entry_step,
        "archived_tasks": len(prior_tasks),
        "instruction": "Revise plan/tasks.json, run loop next and the Plan "
                       "gate, then obtain fresh human plan approval.",
    }
    if clear_error:
        out["error"] = (
            "replan state committed, but the old contract could not be "
            f"released ({clear_error}); run `tp clear --workspace <repo>` "
            "from the ungoverned orchestrator before `loop next`")
    return out
