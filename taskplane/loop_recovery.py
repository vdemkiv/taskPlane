"""Audited, human-attributed recovery transitions for the delivery loop."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
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
                 usage: Callable[[], Mapping[str, int | None]], limits: Mapping[str, int],
                 stage_id: str | None = None) -> None:
        self.workspace = workspace
        self.mutate_state = mutate_state
        self.clock = clock
        self.authorize = authorize
        self.usage = usage
        self.limits = dict(limits)
        self.stage_id = stage_id

    def _records(self, state):
        return state if self.stage_id is None else state.setdefault("attempt_leases", {})

    @property
    def _key(self):
        return self.stage_id or "attempt_lease"

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
        value = self._records(state).get(self._key)
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
            current = self._records(state).get(self._key)
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
            self._records(state)[self._key] = fresh
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
                self._records(state)[self._key] = record
        return delivery_ports.perform_lease_effect(lease, revalidate=self.revalidate,
            nonce_action=nonce_action, action=action, paths=paths)

    def cancel(self, lease: delivery_ports.AttemptLease) -> None:
        with self.mutate_state(self.workspace) as state:
            record = self._record(state, lease)
            record["cancel_requested"] = True
            if isinstance(state, dict):
                self._records(state)[self._key] = record

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
                self._records(state)[self._key] = record

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
                self._records(state)[self._key] = record

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
