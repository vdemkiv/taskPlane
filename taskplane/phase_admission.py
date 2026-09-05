"""Current-attempt native admission; immutable phase handoffs remain authority.

This is a storage/host adapter for the existing root seed, meter and dispatch
policy. It never loads a loop or promotes an intent into observed execution.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING or __package__:
    from . import dispatch_telemetry, governed_commands, host_native, native_session_meter
    from . import phase_handoff, phase_pickup, root_seed, settings, taskplane_lite as kernel
    from .delivery_ports import SystemClock
else:
    import dispatch_telemetry
    import governed_commands
    import host_native
    import native_session_meter
    import phase_handoff
    import phase_pickup
    import root_seed
    # Root seed enforces the canonical package type even in direct CLI mode.
    from taskplane import settings
    import taskplane_lite as kernel
    from delivery_ports import SystemClock

SCHEMA = "taskplane.phase-native-admission/v1"
_PREFIX = ".taskplane/phase-admission-v1/"


def _digest(value: object) -> str:
    return hashlib.sha256(kernel.canonical_json_bytes(value)).hexdigest()


def _identity(handoff: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    checked = cast(dict[str, Any], phase_handoff.validate_manifest(handoff))
    producer = brief.get("producer_contract") or {}
    attempt = producer.get("attempt_id")
    if brief.get("phase") != checked["successor"]["phase"] or \
            producer.get("handoff_fingerprint") != checked["fingerprint"] or \
            not isinstance(attempt, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", attempt) or \
            not isinstance(brief.get("task_name"), str) or \
            not brief["task_name"] or brief.get("fork_turns") != "none" or \
            brief.get("inherited_turns") != 0:
        raise ValueError("phase native intent identity is invalid")
    subjects = {name: ({"path": checked[name]["artifact"]["destination"],
                        "fingerprint": checked[name]["fingerprint"]} if checked[name] else None)
                for name in ("design", "plan")}
    return {"handoff_id": checked["handoff_id"], "handoff_fingerprint": checked["fingerprint"],
            "phase": checked["successor"]["phase"], "attempt_id": attempt,
            "task_name": brief["task_name"], "source": copy.deepcopy(checked["source"]), **subjects}


def create_intent(workspace: str, handoff: dict[str, Any], brief: dict[str, Any], *,
                  wait_policy: dict[str, Any]) -> dict[str, Any]:
    """Use the existing intent store with explicit phase/attempt identity."""
    identity = _identity(handoff, brief)
    run_id = "phase-" + _digest(identity)
    task_id = str((brief.get("task") or {}).get("id") or brief["role"])
    return governed_commands.execute(workspace, "dispatch", {
        "authorization": "phase-dispatch:" + handoff["fingerprint"],
        "consumer": f"{brief['role']}:{task_id}", "host": "native-agent",
        "run_id": run_id, "task_id": task_id, "wave_id": identity["attempt_id"],
        "payload": {"schema": "taskplane.native-agent-dispatch/v1",
                    "phase": identity["phase"], "role": brief["role"],
                    "task_name": brief["task_name"], "phase_authority": identity,
                    "wait_policy": copy.deepcopy(wait_policy),
                    "fork_turns": "none", "inherited_turns": 0}})


def _cache_path(workspace: str, reference: str, *, create: bool = False) -> Path:
    if not re.fullmatch(re.escape(_PREFIX) + r"[0-9a-f]{64}\.json", reference):
        raise ValueError("phase admission reference is invalid")
    root = Path(workspace)
    if root.absolute() != root.resolve():
        raise ValueError("phase admission workspace is not canonical")
    parent = root
    for part in reference.split("/")[:-1]:
        parent /= part
        if parent.is_symlink():
            raise ValueError("phase admission cache cannot follow symlinks")
        if create:
            parent.mkdir(exist_ok=True)
        if not parent.is_dir():
            raise ValueError("phase admission cache directory is unavailable")
    path = root / reference
    if path.is_symlink():
        raise ValueError("phase admission cache cannot follow symlinks")
    return path


def _load(workspace: str, reference: str) -> dict[str, Any]:
    _cache_path(workspace, reference)
    _, data = phase_handoff._safe_regular_file(workspace, reference, code="artifact-integrity")
    if len(data) > kernel.MAX_STAGE_STARTUP_BYTES:
        raise ValueError("phase admission cache exceeds its bound")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {
            "schema", "identity", "intent", "seed_ref", "prepare_receipt", "ledger", "host_start"} or \
            value.get("schema") != SCHEMA or reference != _PREFIX + _digest(value["identity"]) + ".json":
        raise ValueError("phase admission cache identity is invalid")
    if value["seed_ref"] != _PREFIX + _digest(value["identity"]) + "/root-seed.json":
        raise ValueError("phase admission seed reference is foreign")
    seed = cast(dict[str, Any], root_seed.load_root_seed(workspace, value["seed_ref"]))
    effective = settings.load_settings(environment=os.environ)
    root_seed.verify_prepare_receipt(seed, value["prepare_receipt"], settings=effective,
                                    expected_seed_ref=value["seed_ref"])
    identity = value["identity"]
    if seed["run_id"] != "phase-" + _digest(identity) or seed["wave_id"] != identity["attempt_id"] or \
            seed["candidate_sha"] != identity["source"]["commit"] or \
            seed["approved_design"] != identity["design"] or seed["sealed_plan"] != identity["plan"]:
        raise ValueError("phase admission seed is foreign")
    dispatch_telemetry.validate_ledger(value["ledger"])
    if any(value["ledger"].get(field) != expected for field, expected in {
            "run_id": seed["run_id"], "source_sha": seed["candidate_sha"],
            "design_fingerprint": seed["approved_design"]["fingerprint"],
            "plan_fingerprint": seed["sealed_plan"]["fingerprint"]}.items()):
        raise ValueError("phase admission ledger is foreign")
    return value


def _save(path: Path, value: dict[str, Any]) -> None:
    if len(kernel.canonical_json_bytes(value)) > kernel.MAX_STAGE_STARTUP_BYTES:
        raise ValueError("phase admission cache exceeds its bound")
    kernel.atomic_write_json(str(path), value)


def prepare(workspace: str, handoff: dict[str, Any], startup: dict[str, Any],
            brief: dict[str, Any], *, wait_policy: dict[str, Any]) -> dict[str, Any]:
    """Prepare one Build root, without granting native dispatch admission."""
    identity = _identity(handoff, brief)
    if identity["phase"] != "build":
        raise ValueError("fresh phase root admission applies to Build only")
    phase_pickup.validate_build_assignment(startup, handoff, checkout=workspace)
    reference = _PREFIX + _digest(identity) + ".json"
    path = _cache_path(workspace, reference, create=True)
    intent = create_intent(workspace, handoff, brief, wait_policy=wait_policy)
    with kernel.file_lock(str(path)):
        if path.exists():
            prior = _load(workspace, reference)
            if prior["identity"] != identity or prior["intent"] != intent:
                raise ValueError("phase root preparation conflicts with its attempt")
        else:
            effective = settings.load_settings(environment=os.environ)
            task = startup["task"]
            seed_ref = _PREFIX + _digest(identity) + "/root-seed.json"
            receipt = root_seed.prepare_root_seed(workspace, seed_ref, {
                "run_id": intent["identity"]["run_id"], "wave_id": identity["attempt_id"],
                "candidate_sha": handoff["source"]["commit"], "settings": effective,
                "delivery_mode": "iteration", "prepared_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "operation_id": "prepare-" + _digest(identity),
                "design": {"path": handoff["design"]["artifact"]["destination"],
                           "fingerprint": handoff["design"]["fingerprint"]},
                "plan": {"path": handoff["plan"]["artifact"]["destination"],
                         "fingerprint": handoff["plan"]["fingerprint"]}}, {
                "pickups": [{"id": task["id"], "write_scopes": task["scope"],
                             "disjointness_receipt_fingerprint": _digest({
                                 "handoff": handoff["fingerprint"], "sole_task": task})}],
                "wave_budgets": {key: effective.limits.budgets[key]
                                 for key in ("max_actions", "target_tokens", "max_tokens")},
                "outstanding_human_gates": [],
                "predecessor_terminal_projection": {
                    "path": phase_handoff.handoff_path(handoff["handoff_id"]),
                    "fingerprint": handoff["fingerprint"]}})
            ledger = dispatch_telemetry.new_ledger(
                run_id=intent["identity"]["run_id"], source_sha=handoff["source"]["commit"],
                design_fingerprint=handoff["design"]["fingerprint"],
                plan_fingerprint=handoff["plan"]["fingerprint"], started_at=time.time())
            dispatch_telemetry.configure_root_admission(
                ledger, root_session_settings=effective.workflow.root_session.to_dict(),
                settings_digest=effective.digest)
            _save(path, {"schema": SCHEMA, "identity": identity, "intent": intent,
                         "seed_ref": seed_ref, "prepare_receipt": receipt,
                         "ledger": ledger, "host_start": None})
    return {"reference": reference, "intent": intent, "status": "prepared",
            "dispatch_allowed": False}


def admit(workspace: str, handoff: dict[str, Any], startup: dict[str, Any],
          brief: dict[str, Any], *, reference: str,
          observation_authority: bytes) -> dict[str, Any]:
    """Atomically consume authenticated host observations, never intent alone."""
    identity = _identity(handoff, brief)
    phase_pickup.validate_build_assignment(startup, handoff, checkout=workspace)
    path = _cache_path(workspace, reference)
    with kernel.file_lock(str(path)):
        value = _load(workspace, reference)
        if value["identity"] != identity:
            raise ValueError("phase admission attempt is foreign")
        if value["host_start"] is None:
            return {"status": "waiting-for-root-observation", "dispatch_allowed": False,
                    "reference": reference,
                    "instruction": "The current phase is prepared. Wait for its authenticated "
                                   "fresh-root host observation before dispatching the worker."}
        seed = root_seed.load_root_seed(workspace, value["seed_ref"])
        host_native.validate_root_session_start(
            value["host_start"], authority=observation_authority, seed=seed)
        task = startup["task"]
        dispatch = {"dispatch_id": value["intent"]["intent_id"],
                    "thread_id": brief["task_name"], "thread_type": "worker", "task_id": task["id"],
                    "dependencies": list(task["dependencies"]), "shared_owner": None,
                    "started_at": 0, "ended_at": 0, "wait_duration_seconds": 0,
                    "correction_count": 0, "events": []}
        decision = dispatch_telemetry.screen_dispatch(
            value["ledger"], SystemClock(), current_stage="execute",
            outstanding_set_fingerprint=_digest({"handoff": handoff["fingerprint"], "task": task}),
            preserved_context_fingerprint=_digest(identity),
            observation_authority=observation_authority,
            admission_operation_id=dispatch["dispatch_id"], dispatch=dispatch)
        _save(path, value)
        return {**decision, "reference": reference}


def pending_contract(workspace: str) -> dict[str, Any] | None:
    """Find one explicit current-phase meter owner without ambient loop state."""
    matches = []
    for slot in kernel.list_task_slots(workspace):
        contract = kernel.load_json(kernel.active_contract_path(workspace, slot))
        lifecycle = (contract or {}).get("worker_lifecycle") or {}
        if lifecycle.get("stage") == "phase-build" and lifecycle.get("status") in {"pending", "active"} \
                and (contract or {}).get("phase_admission_reference"):
            matches.append(contract)
    if len(matches) > 1:
        raise ValueError("current phase root observation owner is ambiguous")
    return matches[0] if matches else None


def resolve_expected(workspace: str, expected: dict[str, Any], *,
                     native_task_name: str) -> dict[str, Any] | None:
    """Revalidate a phase intent against its one exact pending native slot."""
    matches = []
    for slot in kernel.list_task_slots(workspace):
        contract = kernel.load_json(kernel.active_contract_path(workspace, slot))
        lifecycle = (contract or {}).get("worker_lifecycle") or {}
        if str(lifecycle.get("stage") or "").startswith("phase-") and \
                lifecycle.get("expected_task_name") == native_task_name:
            matches.append(contract)
    if not matches and not str(expected.get("intent_run_id") or "").startswith("phase-"):
        return None
    if len(matches) != 1:
        raise ValueError("phase native intent has no unique pending contract")
    contract = matches[0]
    lifecycle = contract["worker_lifecycle"]
    if lifecycle["status"] != "pending" or expected.get("task_name") != native_task_name:
        raise ValueError("phase native intent is not the exact pending worker")
    startup, brief = contract["phase_startup"], contract["phase_dispatch"]
    if brief.get("task_name") != native_task_name or contract.get("task_id") != brief.get("task_slot"):
        raise ValueError("phase native slot differs from its emitted brief")
    review = brief.get("protocol") == "repository-phase-review"
    if brief.get("protocol") not in {"repository-phase", "repository-phase-review"} or \
            lifecycle.get("stage") != "phase-" + brief["phase"] + ("-review" if review else ""):
        raise ValueError("phase native worker protocol differs from its lifecycle")
    handoff_id = startup["full_envelope_reference"]["handoff_id"]
    handoff = cast(dict[str, Any], phase_handoff.load_manifest(
        workspace, phase_handoff.handoff_path(handoff_id), require_clean=True,
        allow_phase_output=review))
    if brief["phase"] == "build":
        phase_pickup.validate_build_assignment(startup, handoff, checkout=workspace)
    else:
        kernel.validate_stateless_phase_startup(startup, handoff)
    if review:
        if TYPE_CHECKING or __package__:
            from . import phase_review_host
        else:
            import phase_review_host
        canonical = phase_review_host.validate_dispatch(workspace, handoff, contract)
    else:
        if TYPE_CHECKING or __package__:
            from . import phase_dispatch
        else:
            import phase_dispatch
        canonical = phase_dispatch._hydrated_brief(workspace, handoff, startup)
        policy = phase_dispatch._native_contract(workspace, canonical, handoff)
        if any(contract.get(key) != policy.get(key) for key in (
                "coding", "read_only", "write_allow", "allowed_tools", "budget", "submission_contract")):
            raise ValueError("phase native worker contract is stale or widened")
    if brief != canonical:
        raise ValueError("phase native worker brief is stale or widened")
    if contract.get("phase_handoff_fingerprint") != handoff["fingerprint"] or \
            expected.get("agent") != brief["role"] or expected.get("ref") != handoff_id or \
            lifecycle.get("dispatch_intent_id") != expected.get("intent_id") or \
            lifecycle.get("dispatch_intent_run_id") != expected.get("intent_run_id"):
        raise ValueError("phase native intent authority is foreign")
    intent = contract.get("phase_dispatch_intent")
    if not isinstance(intent, dict):
        raise ValueError("phase native intent is missing from its contract")
    rebuilt = create_intent(workspace, handoff, brief, wait_policy=intent["wait_policy"])
    if intent != rebuilt or expected.get("intent_id") != intent["intent_id"] or \
            expected.get("intent_run_id") != intent["identity"]["run_id"]:
        raise ValueError("phase native intent is stale or tampered")
    return {"contract": contract, "handoff": handoff, "startup": startup, "brief": brief}


def observe_pending(workspace: str, contract: dict[str, Any], *, snapshot: dict[str, Any],
                    capability: dict[str, Any], observation_authority: bytes) -> None:
    """Private host-hook adapter; only native provider snapshots enter here."""
    reference = contract["phase_admission_reference"]
    path = _cache_path(workspace, reference)
    startup = contract["phase_startup"]
    handoff = phase_handoff.load_manifest(workspace, phase_handoff.handoff_path(
        startup["handoff_id"]), require_clean=False, allowed_task_id=startup["task"]["id"])
    phase_pickup.validate_build_assignment(startup, handoff, checkout=workspace)
    identity = _identity(handoff, contract["phase_dispatch"])
    with kernel.file_lock(str(path)):
        value = _load(workspace, reference)
        if value["identity"] != identity or contract.get("phase_handoff_fingerprint") != handoff["fingerprint"]:
            raise ValueError("phase root observation authority is foreign")
        seed = root_seed.load_root_seed(workspace, value["seed_ref"])
        prior_meter = value["ledger"]["root_admission"]["meter"]
        prior = (prior_meter or {}).get("watermark")
        start = value["host_start"]
        if start is None:
            start = host_native.start_root_session(
                capability, seed, run_id=str(seed["run_id"]), wave_id=str(seed["wave_id"]),
                candidate_sha=str(seed["candidate_sha"]), settings_digest=str(seed["settings_fingerprint"]),
                session_pseudonym=hashlib.sha256(
                    observation_authority + str(snapshot.get("session_id") or "").encode()).hexdigest(),
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                issuer_sequence=1, authority=observation_authority)
        else:
            host_native.validate_root_session_start(start, authority=observation_authority, seed=seed)
        if prior and snapshot.get("source_identity_fingerprint") == prior.get("source_identity_fingerprint") \
                and snapshot.get("usage") == prior.get("usage"):
            return
        observation = native_session_meter.seal_root_observation(
            snapshot, sequence=int((prior or {}).get("last_sequence") or 0) + 1,
            session_role="root", status_receipt_fingerprint=start["fingerprint"],
            authority=observation_authority)
        meter = native_session_meter.fold_root_observations(
            [observation], authority=observation_authority, prior=prior)
        dispatch_telemetry.record_root_meter(value["ledger"], meter, observation_authority=observation_authority)
        value["host_start"] = start
        _save(path, value)
