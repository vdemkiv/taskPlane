"""Attributable human scope changes, retained separately from agent results.

An amendment supersedes a phase's inputs; it never certifies its old worker or
lenses. The run transaction retains every previous head, attempt and workflow.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterator

Json = dict[str, Any]

from taskplane import phase_records, review_evidence, stage_entities, stage_handoff

SCHEMA = "taskplane.phase-amendment/v1"
_STEPS = {"pm", "design", "design_approval", "plan", "plan_approval", "execute",
          "fix", "evaluate", "em", "signoff"}
_FILES = ("design/contract.json", "design/test-strategy.json", "design/design.md")


def _snapshot(runtime: Any, ws: str, phase: str, requirement_id: str) -> Json:
    if phase not in {"product", "design"}:
        raise ValueError("amendment phase must be product or design")
    state = runtime.load(ws)
    if not state or state.get("step") not in _STEPS:
        raise ValueError("amendment requires an active delivery phase")
    if requirement_id != state.get("requirement_id"):
        raise ValueError("amend the current requirement; changing its identity requires a new goal")
    requirement = runtime.reqs.get_requirement(ws, requirement_id)
    if not requirement:
        raise ValueError("the current requirement is missing")
    context = runtime._stage_loop_context(ws, state)
    if context is None or not context.get("stage"):
        raise ValueError("amendment requires a current phase")
    files = {}
    if phase == "design":
        paths = list(_FILES)
        if (Path(ws) / "design/visual.html").exists():
            paths.append("design/visual.html")
        for path in paths:
            raw = runtime._stage_loop_read_output_no_follow(
                ws, path, required=True, remaining_bytes=2 * 1024 * 1024)
            files[path] = {"sha256": hashlib.sha256(raw).hexdigest(),
                           "text": raw.decode("utf-8")}
    requirement_fp = runtime._dc.requirement_fingerprint(ws, requirement_id)
    candidate_fp = review_evidence.content_fingerprint({
        "phase": phase, "requirement_fingerprint": requirement_fp,
        "files": {path: row["sha256"] for path, row in files.items()}})
    return {"state": state, "context": context, "requirement": requirement,
            "files": files, "stage_fingerprint": context["stage"]["fingerprint"],
            "requirement_fingerprint": requirement_fp,
            "candidate_fingerprint": candidate_fp}


def candidate(runtime: Any, ws: str, phase: str, requirement_id: str) -> Json:
    """Read the exact current proposal without authorizing or changing it."""
    try:
        value = _snapshot(runtime, ws, phase, requirement_id)
        return {"phase": phase, "requirement_id": requirement_id,
                **{key: value[key] for key in ("stage_fingerprint",
                    "requirement_fingerprint", "candidate_fingerprint")}}
    except (ValueError, OSError, KeyError) as exc:
        return {"error": str(exc)}


def _workers(runtime: Any, ws: str, state: Json) -> tuple[list[Json], list[Json]]:
    """Read actual host termination; a human stop flag is never a terminal."""
    from taskplane import codex_identity
    safe, unsafe = [], []
    workspaces = {ws} | {str(task["workspace"]) for task in (state.get("tasks") or [])
                        if task.get("workspace")}
    for workspace in sorted(workspaces):
        for slot, contract in runtime.tp._active_worker_contracts(workspace):
            bound_run = (contract.get("phase_runtime") or {}).get("run_id")
            if bound_run is not None and bound_run != state["run_id"]:
                continue
            life = contract.get("worker_lifecycle") or {}
            item = {"workspace": workspace, "slot": slot,
                    "owner": life.get("owner"), "status": life.get("status"),
                    "contract": contract}
            try:
                if bound_run != state["run_id"]:
                    raise ValueError("worker has no exact run binding; reconcile it before scope changes")
                if life.get("status") in {"terminal", "released"}:
                    terminal = life.get("terminal")
                    if not isinstance(terminal, dict):
                        raise ValueError("terminal worker lacks its exact receipt")
                    runtime.tp._verify_worker_terminal_receipt(
                        workspace, slot, terminal, contract, life["release_action"])
                    if terminal.get("authority") not in {"host-lifecycle", "phase-observation"} or \
                            terminal.get("owner") != life.get("owner"):
                        raise ValueError("worker release requires actual host stop proof")
                elif life.get("status") == "pending" and life.get("owner") is None:
                    # Explicit stop acknowledgement fences an unbound launch.
                    terminal = None
                else:
                    attempt = runtime._phase_bridge_attempt(workspace, contract)
                    if attempt is None:
                        raise ValueError("worker has no phase terminal binding")
                    source, dispatch = attempt[4].nonce, attempt[5]
                    hooks = source.terminal_hooks(dispatch.issued, dispatch.nonce_bindings)
                    if hooks is None:
                        if attempt[2]["stage"]["stage_kind"] == "build":
                            raise ValueError("Build needs its host terminal and effect release")
                        start = source._read_phase_hook(dispatch.issued, dispatch.nonce_bindings, "start")
                        terminal = codex_identity.completed_child(workspace, start)
                    else:
                        start, terminal = hooks
                    if life.get("owner") != {key: terminal["owner"][key]
                            for key in ("session_id", "agent_id", "task_name")}:
                        raise ValueError("host terminal belongs to another worker")
                    if attempt[2]["stage"]["stage_kind"] == "build":
                        raise ValueError("reconcile Build effect release before amending")
                safe.append({**item, "terminal": terminal})
            except (ValueError, OSError, KeyError) as exc:
                unsafe.append({key: item[key] for key in ("workspace", "slot", "owner", "status")} |
                              {"reason": str(exc), "bound_to_run": bound_run == state["run_id"]})
    return safe, unsafe


def _record(manifest: Json, reference: Json) -> Json:
    matches = [row for row in (manifest.get("stage_operations") or {}).values()
               if row.get("operation") == "amend_phase" and
               (row.get("result") or {}).get("amendment") == reference]
    if len(matches) != 1:
        raise ValueError("human amendment has no unique committed run decision")
    return cast(Json, matches[0])


def _read(store: Any, manifest: Json, reference: Json) -> Json:
    receipt = _record(manifest, reference)
    value = store.read(reference)
    if value.get("schema") != SCHEMA or value.get("run_id") != manifest["run_id"] or \
            value.get("request_fingerprint") != receipt["request_fingerprint"] or \
            value.get("successor_stage_id") not in receipt["stage_ids"] or \
            not str(value.get("actor", "")).startswith("human:"):
        raise ValueError("human amendment identity does not verify")
    return cast(Json, value)


def current(runtime: Any, ws: str, state: Json, *, verify_candidate: bool = True) -> Json | None:
    """Verify the amendment only while its own replacement phase is current."""
    projection = state.get("phase_amendment")
    if not projection:
        return None
    context = runtime._stage_loop_context(ws, state)
    if not context or context["stage"]["stage_id"] != projection.get("stage_id"):
        return None
    artifacts = review_evidence.ArtifactStore(ws)
    value = _read(artifacts, context["manifest"], projection["receipt"])
    if value["successor_stage_id"] != context["stage"]["stage_id"] or \
            value["authority"] != context["stage"]["authority"]:
        raise ValueError("human amendment stage authority changed")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    if verify_candidate:
        actual = _snapshot(runtime, ws, value["phase"], state["requirement_id"])
        if any(actual[key] != value[key] for key in ("requirement_fingerprint", "candidate_fingerprint")):
            raise ValueError("approved amendment candidate changed; record the new human amendment")
    return value


def _cleanup(runtime: Any, ws: str, value: Json, *, contracts_locked: bool = False) -> None:
    artifacts = review_evidence.ArtifactStore(ws)
    if not contracts_locked:
        with ExitStack() as locks:
            for reference in value["workers"]:
                saved = artifacts.read(reference)
                locks.enter_context(runtime.tp.file_lock(
                    runtime.tp.active_contract_path(saved["workspace"], saved["slot"])))
            _cleanup(runtime, ws, value, contracts_locked=True)
        return
    for reference in value["workers"]:
        saved = artifacts.read(reference)
        workspace, slot = saved["workspace"], saved["slot"]
        path = runtime.tp.active_contract_path(workspace, slot)
        if not os.path.exists(path):
            continue
        contract = runtime.tp.load_json(path)
        if contract != saved["contract"]:
            original = copy.deepcopy(saved["contract"])
            resumed = copy.deepcopy(contract)
            terminal = resumed["worker_lifecycle"].pop("terminal", None)
            resumed["worker_lifecycle"]["status"] = original["worker_lifecycle"]["status"]
            original["worker_lifecycle"].pop("terminal", None)
            if resumed != original or not terminal or terminal.get("authority") != "orphan-recovery" or \
                    terminal.get("submission_status") != "human-authorized-phase-amendment:" + value["request_fingerprint"]:
                raise ValueError("superseded worker changed; reconcile before replaying amendment cleanup")
        life = contract["worker_lifecycle"]
        terminal = life.get("terminal")
        if terminal is None:
            terminal = runtime.tp.record_worker_terminal(workspace, slot, event=None,
                outcome="interruption", submission_status="human-authorized-phase-amendment:" + value["request_fingerprint"],
                authority="orphan-recovery")
        runtime.tp.release_worker_contract(workspace, slot, action=life["release_action"],
                                          terminal_receipt=terminal)


@contextmanager
def _worker_fence(runtime: Any, workers: list[Json]) -> Iterator[None]:
    """A stopped slot cannot bind a new owner between amendment and retirement."""
    with ExitStack() as locks:
        for saved in sorted(workers, key=lambda row: (row["workspace"], row["slot"])):
            path = runtime.tp.active_contract_path(saved["workspace"], saved["slot"])
            locks.enter_context(runtime.tp.file_lock(path))
            if runtime.tp.load_json(path, default=None) != saved["contract"]:
                raise ValueError("affected worker changed before amendment; reconcile and preview again")
        yield


def require_cleanup(runtime: Any, ws: str, value: Json) -> None:
    artifacts = review_evidence.ArtifactStore(ws)
    for reference in value["workers"]:
        saved = artifacts.read(reference)
        if os.path.exists(runtime.tp.active_contract_path(saved["workspace"], saved["slot"])):
            raise ValueError("amendment worker cleanup incomplete; replay the exact amendment command")


def amend(runtime: Any, ws: str, *, phase: str, by: str, reason: str,
          requirement_id: str, expected_stage_fingerprint: str,
          requirement_fingerprint: str, candidate_fingerprint: str | None = None,
          worker_stopped: bool = False) -> Json:
    """Apply one exact human amendment; final Design approval stays pending."""
    try:
        return _amend(runtime, ws, phase=phase, by=by, reason=reason,
            requirement_id=requirement_id, expected_stage_fingerprint=expected_stage_fingerprint,
            requirement_fingerprint=requirement_fingerprint,
            candidate_fingerprint=candidate_fingerprint, worker_stopped=worker_stopped)
    except (ValueError, OSError, KeyError) as exc:
        return {"error": str(exc), "resolved": False, "dispatch_allowed": False}


def _amend(runtime: Any, ws: str, **request: Any) -> Json:
    if runtime.tp.task_slot() is not None:
        raise ValueError("human phase amendments are orchestrator-only")
    snapshot = _snapshot(runtime, ws, request["phase"], request["requirement_id"])
    state, context = snapshot["state"], snapshot["context"]
    stage, store, manifest = context["stage"], context["store"], context["manifest"]
    actor = str(request["by"] or "").strip()
    if actor != stage["authority"]["actor"] or not actor.startswith("human:"):
        raise ValueError("amendment requires the current run's human --by identity")
    session = str(os.environ.get("TASKPLANE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or
                  os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if not session or len(session.encode()) > 256 or any(ord(char) < 32 for char in session):
        raise ValueError("amendment requires an attributable host session")
    if not str(request["reason"] or "").strip():
        raise ValueError("amendment requires the human's approved change as --reason")
    for key in ("requirement_fingerprint", "candidate_fingerprint"):
        if request[key] != snapshot[key]:
            raise ValueError(f"amendment {key} changed; preview the current proposal")
    material = {key: request[key] for key in ("phase", "by", "reason", "requirement_id",
                "expected_stage_fingerprint", "requirement_fingerprint", "candidate_fingerprint")}
    request_fp = review_evidence.content_fingerprint(material)
    operation = "phase-amendment-" + request_fp[:32]
    artifacts = review_evidence.ArtifactStore(ws)
    prior = (manifest.get("stage_operations") or {}).get(operation)
    if prior:
        value = _read(artifacts, manifest, prior["result"]["amendment"])
        if prior["request_fingerprint"] != request_fp:
            raise ValueError("amendment replay changed")
        if value["successor_stage_id"] != stage["stage_id"]:
            raise ValueError("amendment was superseded; use the current phase proposal")
        _cleanup(runtime, ws, value)
        return {"resolved": "amendment", "replay": True, "step": state["step"],
                "phase_amendment": state["phase_amendment"], "dispatch_allowed": False}
    if request["expected_stage_fingerprint"] != stage["fingerprint"]:
        raise ValueError("phase changed since amendment preview")
    runtime._phase_bridge_authorize(ws, context, manifest)
    safe_workers, unsafe_workers = _workers(runtime, ws, state)
    if unsafe_workers or (safe_workers and not request["worker_stopped"]):
        blockers = unsafe_workers or [{key: item[key] for key in
                    ("workspace", "slot", "owner", "status")} | {"bound_to_run": True} for item in safe_workers]
        return {"error": "stop and reconcile affected workers before applying the amendment",
                "needs_stop": [row for row in blockers if row["bound_to_run"]],
                "unbound_workers": [row for row in blockers if not row["bound_to_run"]],
                "resolved": False, "dispatch_allowed": False}
    leases = state.get("attempt_leases") or {}
    unresolved = [{"stage_id": sid, "operation_id": (row.get("lease") or {}).get("operation_id"),
                   "effects": row.get("effects")} for sid, row in leases.items()
                  if row.get("released") is not True or not row.get("terminal_identity") or
                  not isinstance(row.get("effects"), dict) or
                  any(effect not in {"effect_free", "committed", "observed"}
                      for effect in row["effects"].values())]
    if unresolved:
        return {"error": "reconcile and release Build effects before applying the amendment",
                "needs_reconciliation": unresolved, "resolved": False, "dispatch_allowed": False}
    if request["phase"] == "design":
        errors = runtime._base_design_dod_errors(ws, state)
        if errors:
            return {"error": "amended Design is not ready for approval", "errors": errors,
                    "resolved": False, "dispatch_allowed": False}
    import json
    requirement = {"schema": "taskplane.requirement/v1",
                   **{key: snapshot["requirement"][key] for key in
                      ("id", "title", "functional", "contracts", "depends_on")},
                   "acceptance_criteria": snapshot["requirement"]["acceptance"]}
    runtime.validate_spec_phase_artifact(requirement)
    authored = {"requirement": requirement}
    if request["phase"] == "design":
        authored.update({"design": json.loads(snapshot["files"][_FILES[0]]["text"]),
                         "test-strategy": json.loads(snapshot["files"][_FILES[1]]["text"])})
    for value in authored.values():
        runtime.validate_spec_phase_artifact(value)
    # Stage-index and workflow changes share the incumbent aggregate transaction.
    with _worker_fence(runtime, safe_workers):
        with store.transaction(context["run_id"]):
            fresh = store.load(context["run_id"])
            if fresh != manifest:
                raise ValueError("run changed during amendment; preview again")
            old_state = artifacts.put("amendment-previous-workflow", state)
            workers = [artifacts.put("amendment-worker", row) for row in safe_workers]
            rows = [{"artifact_class": name, "artifact_schema_version": value["schema"],
                     "reference": review_evidence.portable_artifact_reference(artifacts,
                         artifacts.put(name, value))} for name, value in authored.items()]
            at = runtime.time.strftime("%Y-%m-%dT%H:%M:%SZ", runtime.time.gmtime())
            successor_id = "stage-" + request_fp[:32]
            new_requirement = {"id": request["requirement_id"], "revision": request["requirement_fingerprint"],
                               "fingerprint": request["requirement_fingerprint"]}
            authority = {**stage["authority"], "requirement_revision": request["requirement_fingerprint"],
                         "design_revision": None, "design_fingerprint": None, "session_id": session,
                         "worktree_revision": runtime.tp.git_head(ws),
                         "authority_revision": stage["authority"]["authority_revision"] + 1,
                         "authority_fingerprint": review_evidence.content_fingerprint({
                             "request": material, "session_id": session,
                             "previous": stage["authority"]["authority_fingerprint"]})}
            decision = review_evidence.portable_artifact_reference(artifacts,
                artifacts.put("phase-amendment-decision", {"schema": "taskplane.human-amendment-decision/v1",
                    "run_id": context["run_id"], "request_fingerprint": request_fp,
                    "request": material, "actor": actor, "session_id": session, "authority": authority}))
            lens_packet = {"schema": "taskplane.lens-evidence/v1", "entries": [],
                           "human_amendments": [decision]}
            lens_packet["fingerprint"] = review_evidence.content_fingerprint(lens_packet)
            rows.append({"artifact_class": "lens-evidence", "artifact_schema_version": lens_packet["schema"],
                         "reference": review_evidence.portable_artifact_reference(artifacts,
                             artifacts.put("lens-evidence", lens_packet))})
            active = fresh["active_stage_projection"]["active_stage_ids"]
            old_stages = [runtime._indexed_stage(store, fresh, context["run_id"], sid) for sid in active]
            value = {"schema": SCHEMA, "run_id": context["run_id"], "request_fingerprint": request_fp,
                     "phase": request["phase"], "actor": actor, "reason": request["reason"],
                     "session_id": session, "decided_at": at, "from_step": state["step"],
                     "previous_stages": [{"stage_id": old["stage_id"], "fingerprint": old["fingerprint"]}
                                         for old in old_stages],
                     "successor_stage_id": successor_id, "authority": authority,
                     "requirement_fingerprint": request["requirement_fingerprint"],
                     "candidate_fingerprint": request["candidate_fingerprint"],
                     "artifacts": rows, "files": snapshot["files"], "previous_workflow": old_state,
                     "workers": workers, "decision": decision, "review_basis": "human-directed-amendment",
                     "automated_review": "superseded-not-passed", "design_approval": "pending"}
            reference = review_evidence.portable_artifact_reference(artifacts,
                        artifacts.put("phase-amendment", value))
            authorization = {"actor": actor, "session_id": session, "authorized_at": at,
                             "operation_id": operation, "authority_record": {
                                 "schema": "taskplane.authority-record-reference/v1",
                                 "authority_schema": "taskplane.consolidated-authorization/v1",
                                 "revision": authority["authority_revision"],
                                 "fingerprint": authority["authority_fingerprint"]}}
            handoff = stage_handoff.create_manifest(artifacts, producer_stage_id=stage["stage_id"],
                producer_outcome="closed", requirement=new_requirement, design=None, target=None, commit=None,
                contracts={"provided": [], "consumed": [], "changed": []}, deliverables=["human scope amendment"],
                evidence_references=[reference], selected_artifacts=[row["reference"] for row in rows],
                exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS), authorization=authorization,
                allow_nonconsumable_reuse=True)
            handoff_ref = review_evidence.portable_artifact_reference(artifacts,
                            stage_handoff.store_manifest(artifacts, handoff))
            # Product changes reopen Design; the human owns the amended WHAT.
            successor = stage_entities.create_stage(run_id=context["run_id"], stage_id=successor_id,
                requirement=new_requirement, design=None, stage_kind="design", parent_stage_ids=[],
                predecessor_stage_ids=[stage["stage_id"]], input_manifest_ref=handoff_ref,
                execution_root_id="execution-" + successor_id,
                deliverables=runtime._stage_loop_deliverables("design", state),
                selected_artifacts=cast(list[Json], handoff["selected_artifacts"]), budget=stage["budget"],
                dependencies=[], contracts=stage["contracts"], authority=authority, created_at=at)
            def authorize(current_manifest: Json) -> None:
                runtime._phase_bridge_authorize(ws, context, current_manifest)
                if _snapshot(runtime, ws, request["phase"], request["requirement_id"])["candidate_fingerprint"] != request["candidate_fingerprint"]:
                    raise ValueError("candidate changed during amendment")
            def mutate(current_manifest: Json) -> Json:
                heads = copy.deepcopy(current_manifest["stage_heads"])
                for old in old_stages:
                    terminal = stage_entities.terminalize_stage(old, outcome="closed", actor=actor,
                        terminalized_at=at, reason_code="human-scope-amendment", reason=request["reason"],
                        completed_deliverables=[], completion_evidence=[reference])
                    heads[old["stage_id"]] = stage_entities._stage_head(store, context["run_id"], terminal)
                context["lifecycle"]._claim_execution_root(successor)
                heads[successor_id] = stage_entities._stage_head(store, context["run_id"], successor)
                lineage = stage_entities.validate_lineage(stage_entities._lineage_row(parent_stage_id=None,
                    child_stage_id=successor_id, input_manifest_ref=handoff_ref, operation_id=operation,
                    predecessor_stage_ids=[stage["stage_id"]]))
                return {"changes": {"stage_heads": heads,
                            "lineage": [*current_manifest["lineage"], lineage],
                            "active_stage_projection": stage_entities.active_stage_projection(heads, successor_id)},
                        "receipt": {"operation": "amend_phase", "stage_ids": sorted([*active, successor_id]),
                                    "result": {"amendment": reference, "successor_head": heads[successor_id]}}}
            store.commit_stage_operation(context["run_id"], expected_revision=manifest["revision"],
                operation_id=operation, request_fingerprint=request_fp, mutate=mutate,
                validate_authority=authorize)
            updated = copy.deepcopy(state)
            for key in ("design_fingerprint", "design_approved_by", "plan_fingerprint", "plan_approved_by",
                        "plan_approval", "approved_plan", "delivery_mode", "delivery_mode_receipt",
                        "_submission", "_stage_completion", "_stage_bindings", "evaluate_child_evidence",
                        "accepted_evaluations", "signoff", "signoff_evidence", "review_kernel_runs",
                        "design_control_plane_binding", "design_lens_policy", "authority_packet",
                        "authority_receipt", "authority_derivations", "authority_target_revision",
                        "attempt_leases", "approved_plan_receipt", "test_strategy_authority_receipt",
                        "engineering_review_request_changes", "signoff_dod"):
                updated.pop(key, None)
            updated.update(tasks=[], current_task=0, step="design_approval" if request["phase"] == "design" else "design",
                phase_amendment={"phase": request["phase"], "actor": actor, "reason": request["reason"],
                    "requirement_fingerprint": request["requirement_fingerprint"],
                    "candidate_fingerprint": request["candidate_fingerprint"],
                    "review_basis": "human-directed-amendment", "approval": "pending",
                    "stage_id": successor_id, "receipt": reference, "fingerprint": reference["fingerprint"]})
            updated.setdefault("phase_amendment_history", []).append(updated["phase_amendment"])
            runtime.save(ws, updated)
        _cleanup(runtime, ws, value, contracts_locked=True)
    runtime.tp.trace(ws, "loop_amend", phase=request["phase"], by=actor, reason=request["reason"],
                     amendment=reference["fingerprint"])
    return {"resolved": "amendment", "replay": False, "step": updated["step"],
            "phase_amendment": updated["phase_amendment"], "dispatch_allowed": False}


def verified_handoff(runtime: Any, lifecycle: Any, manifest: Json, stage: Json) -> Json | None:
    """The explicit amendment boundary permits the recorded revision change."""
    artifacts = lifecycle._artifact_store()
    for row in (manifest.get("stage_operations") or {}).values():
        if row.get("operation") != "amend_phase" or \
                stage["stage_id"] not in row["stage_ids"]:
            continue
        value = _read(artifacts, manifest, row["result"]["amendment"])
        if value["successor_stage_id"] != stage["stage_id"]:
            continue
        if row["result"]["successor_head"]["object"]["fingerprint"] != stage["fingerprint"]:
            # Resume adds execution state; the immutable input remains pinned.
            original = stage_entities._read_indexed_stage(lifecycle.store, stage["run_id"],
                stage["stage_id"], row["result"]["successor_head"])
            if any(original[key] != stage[key] for key in ("input_manifest_ref", "authority", "requirement")):
                raise ValueError("amendment input changed")
        handoff = stage_handoff.read_manifest(artifacts, stage["input_manifest_ref"],
            expected_authority_revision=stage["authority"]["authority_revision"],
            expected_authority_fingerprint=stage["authority"]["authority_fingerprint"],
            allow_nonconsumable_reuse=True)
        if handoff["evidence_references"] != [row["result"]["amendment"]] or \
                handoff["selected_artifacts"] != stage["selected_artifacts"] or \
                handoff["requirement"] != stage["requirement"] or value["authority"] != stage["authority"]:
            raise ValueError("amendment handoff changed")
        return handoff
    return None


@dataclass(frozen=True)
class AmendmentPackage(stage_handoff.PhasePackage):
    store: Any
    registry: Any
    value: Json
    amendment: Json

    def manifest(self) -> Json:
        return {**self.value, "produced_artifacts": [], "inherited_artifacts": self.amendment["artifacts"]}

    @property
    def artifacts(self) -> tuple[Any, ...]:
        from taskplane import agent_runtime
        selected = []
        for declaration in self.registry.admit(self.phase_id, ()).to_dict()["consumes"]:
            rows = [row for row in self.amendment["artifacts"] if row["artifact_class"] == declaration["artifact_class"]]
            if len(rows) > 1 or (not rows and declaration["required"]):
                raise ValueError("amendment lacks required phase input: " + declaration["artifact_class"])
            for row in rows:
                if row["artifact_schema_version"] != declaration["artifact_schema_version"]:
                    raise ValueError("amendment input schema differs from phase declaration")
                review_evidence.verify_portable_artifact_reference(self.store, row["reference"])
                selected.append(agent_runtime.Artifact(row["artifact_class"], row["artifact_schema_version"], row["reference"]))
        return tuple(selected)

    def read(self, artifact_class: str) -> Json:
        rows = [row for row in self.artifacts if row.artifact_class == artifact_class]
        if len(rows) != 1:
            raise ValueError("amendment input missing or ambiguous: " + artifact_class)
        return cast(Json, self.store.read(rows[0].reference))


def package(store: Any, reference: Json, *, registry: Any, phase_id: str,
            expected_authority_revision: int, expected_authority_fingerprint: str,
            expected_run_id: str, expected_candidate_fingerprint: str) -> AmendmentPackage | None:
    """Consume human-scoped input without inventing an accepted agent result."""
    value = store.read(reference)
    if value.get("schema") != stage_handoff.SCHEMA:
        return None
    refs = [ref for ref in value.get("evidence_references", []) if ref["kind"] == "phase-amendment"]
    if not refs:
        return None
    if len(refs) != 1 or phase_id not in {"design", "plan"}:
        raise ValueError("human amendment package is not a Design/Plan input")
    from taskplane import loop
    manifest = cast(Any, loop)._stage_store(store.workspace, expected_run_id).load(expected_run_id)
    amendment = _read(store, manifest, refs[0])
    stage_handoff.read_manifest(store, reference,
        expected_authority_revision=expected_authority_revision,
        expected_authority_fingerprint=expected_authority_fingerprint,
        allow_nonconsumable_reuse=True)
    authority = amendment["authority"]
    if authority["authority_revision"] != expected_authority_revision or \
            authority["authority_fingerprint"] != expected_authority_fingerprint:
        raise ValueError("human amendment authority differs from phase input")
    route = phase_records.phase_routing(manifest)
    if route is None or route["result"]["configuration"]["candidate_fingerprint"] != expected_candidate_fingerprint:
        raise ValueError("human amendment run candidate changed")
    if sorted(value["selected_artifacts"], key=review_evidence.canonical_bytes) != sorted(
            [row["reference"] for row in amendment["artifacts"]], key=review_evidence.canonical_bytes):
        raise ValueError("human amendment selected artifacts changed")
    if phase_id == "plan":
        state = manifest["workflow"]
        if not state.get("design_approved_by") or not state.get("design_fingerprint"):
            raise ValueError("amended Design requires separate human approval before Plan")
    result = AmendmentPackage(store=store, registry=registry, reference=reference,
        phase_id=phase_id, authority_revision=expected_authority_revision,
        authority_fingerprint=expected_authority_fingerprint, run_id=expected_run_id,
        candidate_fingerprint=expected_candidate_fingerprint, value=value, amendment=amendment)
    result.artifacts
    verify_human_evidence(store, [amendment["decision"]], expected_run_id)
    return result


def verify_human_evidence(store: Any, references: list[Json], run_id: str) -> None:
    """Human scope decisions must be committed, not worker-authored claims."""
    if not references:
        return
    from taskplane import loop
    manifest = cast(Any, loop)._stage_store(store.workspace, run_id).load(run_id)
    for reference in references:
        decisions = []
        for row in (manifest.get("stage_operations") or {}).values():
            if row.get("operation") == "amend_phase":
                value = _read(store, manifest, row["result"]["amendment"])
                if value.get("decision") == reference:
                    decisions.append(value)
        if len(decisions) != 1:
            raise ValueError("human amendment evidence has no committed scope decision")
        decision = store.read(reference)
        if decision.get("run_id") != run_id or decision.get("request_fingerprint") != decisions[0]["request_fingerprint"]:
            raise ValueError("human amendment evidence differs from its run decision")
