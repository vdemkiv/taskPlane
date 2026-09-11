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
import re
from types import ModuleType
from collections.abc import Iterator
from typing import cast

Json = dict[str, object]

from taskplane import agent_runtime, phase_records, review_evidence, stage_entities, stage_handoff

SCHEMA = "taskplane.phase-amendment/v1"
_STEPS = {
    "pm",
    "design",
    "design_approval",
    "plan",
    "plan_approval",
    "execute",
    "fix",
    "evaluate",
    "em",
    "signoff",
}
_FILES = ("design/contract.json", "design/test-strategy.json", "design/design.md")


def _object(value: object, label: str) -> Json:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(label + " must be an object with string keys")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(label + " must be text")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(label + " must be an integer")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(label + " must be a list")
    return value


def _table(value: object, label: str) -> dict[str, Json]:
    return {key: _object(row, label) for key, row in _object(value, label).items()}


def _lifecycle(context: Json) -> stage_entities.StageLifecycle:
    value = context["lifecycle"]
    owner = context["stage_entities"]
    if not isinstance(owner, ModuleType) or not isinstance(value, owner.StageLifecycle):
        raise ValueError("amendment requires the stage lifecycle owner")
    # Flat and package imports expose the same API through distinct classes;
    # the selected owner's runtime check above precedes this static annotation.
    return cast(stage_entities.StageLifecycle, value)


def _objects(value: object, label: str) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError(label + " must be a list")
    return [_object(item, label) for item in value]


def _snapshot(runtime: ModuleType, ws: str, phase: str, requirement_id: str) -> Json:
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
    _objects(state.get("tasks") or [], "tasks")
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
                ws, path, required=True, remaining_bytes=2 * 1024 * 1024
            )
            files[path] = {"sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8")}
    requirement_fp = runtime._dc.requirement_fingerprint(ws, requirement_id)
    candidate_fp = review_evidence.content_fingerprint(
        {
            "phase": phase,
            "requirement_fingerprint": requirement_fp,
            "files": {path: row["sha256"] for path, row in files.items()},
        }
    )
    return {
        "state": state,
        "context": context,
        "requirement": requirement,
        "files": files,
        "stage_fingerprint": context["stage"]["fingerprint"],
        "requirement_fingerprint": requirement_fp,
        "candidate_fingerprint": candidate_fp,
    }


def candidate(runtime: ModuleType, ws: str, phase: str, requirement_id: str) -> Json:
    """Read the exact current proposal without authorizing or changing it."""
    try:
        value = _snapshot(runtime, ws, phase, requirement_id)
        return {
            "phase": phase,
            "requirement_id": requirement_id,
            **{
                key: value[key]
                for key in ("stage_fingerprint", "requirement_fingerprint", "candidate_fingerprint")
            },
        }
    except (ValueError, OSError, KeyError) as exc:
        return {"error": str(exc)}


def _authorize_amendment_revision(
    runtime: ModuleType, ws: str, context: Json, manifest: Json, *, revision: str
) -> None:
    """Authorize only the explicit source change on the exact human parent."""
    stage = _object(context["stage"], "stage")
    indexed = runtime._indexed_stage(
        context["store"], manifest, _text(context["run_id"], "run id"), stage["stage_id"]
    )
    if indexed != stage:
        raise ValueError("phase stage changed before amendment")
    if runtime.tp.git_head(ws) != revision:
        raise ValueError("source revision changed during amendment; preview again")
    # This is an explicit amendment proposal, not ordinary phase authority.
    # Keep every other parent identity exact and resolve live facts against
    # the genuinely observed revision rather than pretending HEAD stayed old.
    proposed = {**_object(stage["authority"], "authority"), "worktree_revision": revision}
    current = runtime._current_stage_authority(ws, manifest, proposed)
    try:
        _lifecycle(context).authority_validator(proposed, current)
    except RuntimeError as exc:
        raise ValueError("human amendment authority refused: " + str(exc)) from exc


def _workers(runtime: ModuleType, ws: str, state: Json) -> tuple[list[Json], list[Json]]:
    """Read actual host termination; a human stop flag is never a terminal."""
    from taskplane import codex_identity

    safe, unsafe = [], []
    workspaces = {ws} | {
        str(task["workspace"])
        for task in _objects(state.get("tasks") or [], "tasks")
        if task.get("workspace")
    }
    for workspace in sorted(workspaces):
        for slot, contract in runtime.tp._active_worker_contracts(workspace):
            bound_run = (contract.get("phase_runtime") or {}).get("run_id")
            if bound_run is not None and bound_run != state["run_id"]:
                continue
            life = contract.get("worker_lifecycle") or {}
            item = {
                "workspace": workspace,
                "slot": slot,
                "owner": life.get("owner"),
                "status": life.get("status"),
                "contract": contract,
            }
            try:
                if bound_run != state["run_id"]:
                    raise ValueError(
                        "worker has no exact run binding; reconcile it before scope changes"
                    )
                if life.get("status") in {"terminal", "released"}:
                    terminal = life.get("terminal")
                    if not isinstance(terminal, dict):
                        raise ValueError("terminal worker lacks its exact receipt")
                    runtime.tp._verify_worker_terminal_receipt(
                        workspace, slot, terminal, contract, life["release_action"]
                    )
                    if terminal.get("authority") not in {
                        "host-lifecycle",
                        "phase-observation",
                    } or terminal.get("owner") != life.get("owner"):
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
                        start = source._read_phase_hook(
                            dispatch.issued, dispatch.nonce_bindings, "start"
                        )
                        terminal = codex_identity.completed_child(workspace, start)
                    else:
                        start, terminal = hooks
                    if life.get("owner") != {
                        key: terminal["owner"][key]
                        for key in ("session_id", "agent_id", "task_name")
                    }:
                        raise ValueError("host terminal belongs to another worker")
                    if attempt[2]["stage"]["stage_kind"] == "build":
                        raise ValueError("reconcile Build effect release before amending")
                safe.append({**item, "terminal": terminal})
            except (ValueError, OSError, KeyError) as exc:
                unsafe.append(
                    {key: item[key] for key in ("workspace", "slot", "owner", "status")}
                    | {"reason": str(exc), "bound_to_run": bound_run == state["run_id"]}
                )
    return safe, unsafe


def _record(manifest: Json, reference: Json) -> Json:
    matches = [
        row
        for row in _table(manifest.get("stage_operations") or {}, "stage operations").values()
        if row.get("operation") == "amend_phase"
        and _object(row.get("result") or {}, "operation result").get("amendment") == reference
    ]
    if len(matches) != 1:
        raise ValueError("human amendment has no unique committed run decision")
    return _object(matches[0], "amendment operation")


def _read(store: review_evidence.ArtifactStore, manifest: Json, reference: Json) -> Json:
    receipt = _record(manifest, reference)
    value = _object(store.read(reference), "human amendment")
    if (
        value.get("schema") != SCHEMA
        or value.get("run_id") != manifest["run_id"]
        or value.get("request_fingerprint") != receipt["request_fingerprint"]
        or value.get("successor_stage_id") not in _list(receipt["stage_ids"], "stage ids")
        or not str(value.get("actor", "")).startswith("human:")
    ):
        raise ValueError("human amendment identity does not verify")
    return value


def current(
    runtime: ModuleType, ws: str, state: Json, *, verify_candidate: bool = True
) -> Json | None:
    """Verify the amendment only while its own replacement phase is current."""
    projection = state.get("phase_amendment")
    if not projection:
        return None
    projection = _object(projection, "amendment projection")
    context = runtime._stage_loop_context(ws, state)
    if not context or context["stage"]["stage_id"] != projection.get("stage_id"):
        return None
    artifacts = review_evidence.ArtifactStore(ws)
    value = _read(
        artifacts, context["manifest"], _object(projection["receipt"], "amendment receipt")
    )
    if (
        value["successor_stage_id"] != context["stage"]["stage_id"]
        or value["authority"] != context["stage"]["authority"]
    ):
        raise ValueError("human amendment stage authority changed")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    if verify_candidate:
        actual = _snapshot(
            runtime,
            ws,
            _text(value["phase"], "phase"),
            _text(state["requirement_id"], "requirement id"),
        )
        if any(
            actual[key] != value[key]
            for key in ("requirement_fingerprint", "candidate_fingerprint")
        ):
            raise ValueError("approved amendment candidate changed; record the new human amendment")
    return value


def review_comparison(runtime: ModuleType, ws: str, state: Json) -> str | None:
    """Resolve retained review scope from committed amendments on this stage's lineage."""
    projection = state.get("phase_amendment")
    if not projection:
        return None
    context = runtime._stage_loop_context(ws, state)
    if not context or context["run_id"] != state.get("run_id"):
        raise ValueError("review comparison requires its current run and stage")
    manifest = _object(context["manifest"], "manifest")
    artifacts = review_evidence.ArtifactStore(ws)
    amendments = {}
    for row in _table(manifest.get("stage_operations") or {}, "stage operations").values():
        if row.get("operation") == "amend_phase":
            ref = _object(_object(row["result"], "result")["amendment"], "amendment")
            value = _read(artifacts, manifest, ref)
            amendments[_text(value["successor_stage_id"], "successor")] = (ref, value)
    # The indexed stages and their committed lineage, not a mutable workflow
    # baseline or history list, establish which amendment can supply the comparison.
    pending = [_text(_object(context["stage"], "stage")["stage_id"], "stage id")]
    seen: set[str] = set()
    inherited = []
    while pending:
        sid = pending.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        stage = runtime._indexed_stage(context["store"], manifest, context["run_id"], sid)
        predecessors = stage["predecessor_stage_ids"]
        if predecessors:
            rows = [
                row
                for row in _objects(manifest["lineage"], "lineage")
                if row["child_stage_id"] == sid
            ]
            if len(rows) != 1 or rows[0]["predecessor_stage_ids"] != predecessors:
                raise ValueError("review comparison lineage changed")
            if rows[0]["handoff_fingerprint"] != stage["input_manifest_ref"]["fingerprint"]:
                raise ValueError("review comparison input changed")
        if sid in amendments:
            ref, value = amendments[sid]
            if value["authority"] != stage["authority"]:
                raise ValueError("review comparison authority changed")
            inherited.append((ref, value))
        pending.extend(predecessors)
    comparison = None
    for ref, value in inherited:
        selected = _object(projection, "review comparison projection")
        if (
            selected.get("receipt") != ref
            or selected.get("stage_id") != value["successor_stage_id"]
        ):
            raise ValueError("review comparison is foreign or stale")
        previous = _object(
            artifacts.read(_object(value["previous_workflow"], "previous workflow")),
            "previous workflow",
        )
        if previous.get("run_id") != state.get("run_id") or previous.get(
            "requirement_id"
        ) != state.get("requirement_id"):
            raise ValueError("review comparison workflow identity changed")
        if value["from_step"] in {"execute", "fix", "evaluate", "em", "signoff", "plan_approval"}:
            comparison = _text(previous["baseline"], "review comparison baseline")
        projection = previous.get("phase_amendment")
    if not inherited or projection:
        raise ValueError("review comparison has no complete amendment lineage")
    return comparison


def _cleanup(runtime: ModuleType, ws: str, value: Json, *, contracts_locked: bool = False) -> None:
    artifacts = review_evidence.ArtifactStore(ws)
    if not contracts_locked:
        with ExitStack() as locks:
            for reference in _objects(value["workers"], "workers"):
                saved = artifacts.read(reference)
                locks.enter_context(
                    runtime.tp.file_lock(
                        runtime.tp.active_contract_path(saved["workspace"], saved["slot"])
                    )
                )
            _cleanup(runtime, ws, value, contracts_locked=True)
        return
    for reference in _objects(value["workers"], "workers"):
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
            if (
                resumed != original
                or not terminal
                or terminal.get("authority") != "orphan-recovery"
                or terminal.get("submission_status")
                != "human-authorized-phase-amendment:"
                + _text(value["request_fingerprint"], "request fingerprint")
            ):
                raise ValueError(
                    "superseded worker changed; reconcile before replaying amendment cleanup"
                )
        life = contract["worker_lifecycle"]
        terminal = life.get("terminal")
        if terminal is None:
            terminal = runtime.tp.record_worker_terminal(
                workspace,
                slot,
                event=None,
                outcome="interruption",
                submission_status="human-authorized-phase-amendment:"
                + _text(value["request_fingerprint"], "request fingerprint"),
                authority="orphan-recovery",
            )
        runtime.tp.release_worker_contract(
            workspace, slot, action=life["release_action"], terminal_receipt=terminal
        )


@contextmanager
def _worker_fence(runtime: ModuleType, workers: list[Json]) -> Iterator[None]:
    """A stopped slot cannot bind a new owner between amendment and retirement."""
    with ExitStack() as locks:
        for saved in sorted(workers, key=lambda row: (row["workspace"], row["slot"])):
            path = runtime.tp.active_contract_path(saved["workspace"], saved["slot"])
            locks.enter_context(runtime.tp.file_lock(path))
            if runtime.tp.load_json(path, default=None) != saved["contract"]:
                raise ValueError(
                    "affected worker changed before amendment; reconcile and preview again"
                )
        yield


def require_cleanup(runtime: ModuleType, ws: str, value: Json) -> None:
    artifacts = review_evidence.ArtifactStore(ws)
    for reference in _objects(value["workers"], "workers"):
        saved = artifacts.read(reference)
        if os.path.exists(runtime.tp.active_contract_path(saved["workspace"], saved["slot"])):
            raise ValueError(
                "amendment worker cleanup incomplete; replay the exact amendment command"
            )


def amend(
    runtime: ModuleType,
    ws: str,
    *,
    phase: str,
    by: str,
    reason: str,
    requirement_id: str,
    expected_stage_fingerprint: str,
    requirement_fingerprint: str,
    candidate_fingerprint: str | None = None,
    worker_stopped: bool = False,
) -> Json:
    """Apply one exact human amendment; final Design approval stays pending."""
    try:
        return _amend(
            runtime,
            ws,
            phase=phase,
            by=by,
            reason=reason,
            requirement_id=requirement_id,
            expected_stage_fingerprint=expected_stage_fingerprint,
            requirement_fingerprint=requirement_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            worker_stopped=worker_stopped,
        )
    except (ValueError, OSError, KeyError) as exc:
        return {"error": str(exc), "resolved": False, "dispatch_allowed": False}


def _amend(runtime: ModuleType, ws: str, **request: object) -> Json:
    if runtime.tp.task_slot() is not None:
        raise ValueError("human phase amendments are orchestrator-only")
    snapshot = _snapshot(
        runtime,
        ws,
        _text(request["phase"], "phase"),
        _text(request["requirement_id"], "requirement id"),
    )
    state = _object(snapshot["state"], "workflow")
    context = _object(snapshot["context"], "stage context")
    stage = _object(context["stage"], "stage")
    store = context["store"]
    if not isinstance(store, runtime.run_store_engine.RunStore):
        raise ValueError("amendment requires the run store owner")
    manifest = _object(context["manifest"], "manifest")
    actor = str(request["by"] or "").strip()
    if actor != _object(stage["authority"], "authority")["actor"] or not actor.startswith("human:"):
        raise ValueError("amendment requires the current run's human --by identity")
    session = str(
        os.environ.get("TASKPLANE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()
    if not session or len(session.encode()) > 256 or any(ord(char) < 32 for char in session):
        raise ValueError("amendment requires an attributable host session")
    if not str(request["reason"] or "").strip():
        raise ValueError("amendment requires the human's approved change as --reason")
    revision = runtime.tp.git_head(ws)
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        raise ValueError("amendment requires the current committed source revision")
    for key in ("requirement_fingerprint", "candidate_fingerprint"):
        if request[key] != snapshot[key]:
            raise ValueError(f"amendment {key} changed; preview the current proposal")
    material = {
        key: request[key]
        for key in (
            "phase",
            "by",
            "reason",
            "requirement_id",
            "expected_stage_fingerprint",
            "requirement_fingerprint",
            "candidate_fingerprint",
        )
    }
    request_fp = review_evidence.content_fingerprint(material)
    operation = "phase-amendment-" + request_fp[:32]
    artifacts = review_evidence.ArtifactStore(ws)
    prior = _table(manifest.get("stage_operations") or {}, "stage operations").get(operation)
    if prior:
        value = _read(
            artifacts,
            manifest,
            _object(_object(prior["result"], "result")["amendment"], "amendment"),
        )
        if prior["request_fingerprint"] != request_fp:
            raise ValueError("amendment replay changed")
        if value["successor_stage_id"] != stage["stage_id"]:
            raise ValueError("amendment was superseded; use the current phase proposal")
        _authorize_amendment_revision(
            runtime,
            ws,
            context,
            manifest,
            revision=_text(
                _object(value["authority"], "authority")["worktree_revision"], "revision"
            ),
        )
        _cleanup(runtime, ws, value)
        return {
            "resolved": "amendment",
            "replay": True,
            "step": state["step"],
            "phase_amendment": state["phase_amendment"],
            "dispatch_allowed": False,
        }
    if request["expected_stage_fingerprint"] != stage["fingerprint"]:
        raise ValueError("phase changed since amendment preview")
    _authorize_amendment_revision(runtime, ws, context, manifest, revision=revision)
    safe_workers, unsafe_workers = _workers(runtime, ws, state)
    if unsafe_workers or (safe_workers and not request["worker_stopped"]):
        blockers = unsafe_workers or [
            {key: item[key] for key in ("workspace", "slot", "owner", "status")}
            | {"bound_to_run": True}
            for item in safe_workers
        ]
        return {
            "error": "stop and reconcile affected workers before applying the amendment",
            "needs_stop": [row for row in blockers if row["bound_to_run"]],
            "unbound_workers": [row for row in blockers if not row["bound_to_run"]],
            "resolved": False,
            "dispatch_allowed": False,
        }
    leases = _table(state.get("attempt_leases") or {}, "attempt leases")
    unresolved = [
        {
            "stage_id": sid,
            "operation_id": _object(row.get("lease") or {}, "lease").get("operation_id"),
            "effects": row.get("effects"),
        }
        for sid, row in leases.items()
        if row.get("released") is not True
        or not row.get("terminal_identity")
        or not isinstance(row.get("effects"), dict)
        or any(
            effect not in {"effect_free", "committed", "observed"}
            for effect in _object(row["effects"], "effects").values()
        )
    ]
    if unresolved:
        return {
            "error": "reconcile and release Build effects before applying the amendment",
            "needs_reconciliation": unresolved,
            "resolved": False,
            "dispatch_allowed": False,
        }
    if request["phase"] == "design":
        errors = runtime._base_design_dod_errors(ws, state)
        if errors:
            return {
                "error": "amended Design is not ready for approval",
                "errors": errors,
                "resolved": False,
                "dispatch_allowed": False,
            }
    import json

    requirement = {
        "schema": "taskplane.requirement/v1",
        **{
            key: _object(snapshot["requirement"], "requirement")[key]
            for key in ("id", "title", "functional", "contracts", "depends_on")
        },
        "acceptance_criteria": _object(snapshot["requirement"], "requirement")["acceptance"],
    }
    runtime.validate_spec_phase_artifact(requirement)
    authored = {"requirement": requirement}
    if request["phase"] == "design":
        authored.update(
            {
                "design": json.loads(
                    _text(
                        _object(_object(snapshot["files"], "files")[_FILES[0]], "file")["text"],
                        "file text",
                    )
                ),
                "test-strategy": json.loads(
                    _text(
                        _object(_object(snapshot["files"], "files")[_FILES[1]], "file")["text"],
                        "file text",
                    )
                ),
            }
        )
    for value in authored.values():
        runtime.validate_spec_phase_artifact(value)
    # Stage-index and workflow changes share the incumbent aggregate transaction.
    with _worker_fence(runtime, safe_workers):
        with store.transaction(_text(context["run_id"], "run id")):
            fresh = store.load(_text(context["run_id"], "run id"))
            if fresh != manifest:
                raise ValueError("run changed during amendment; preview again")
            old_state = artifacts.put("amendment-previous-workflow", state)
            workers = [artifacts.put("amendment-worker", row) for row in safe_workers]
            rows = [
                {
                    "artifact_class": name,
                    "artifact_schema_version": value["schema"],
                    "reference": review_evidence.portable_artifact_reference(
                        artifacts, artifacts.put(name, value)
                    ),
                }
                for name, value in authored.items()
            ]
            at = runtime.time.strftime("%Y-%m-%dT%H:%M:%SZ", runtime.time.gmtime())
            successor_id = "stage-" + request_fp[:32]
            new_requirement = {
                "id": request["requirement_id"],
                "revision": request["requirement_fingerprint"],
                "fingerprint": request["requirement_fingerprint"],
            }
            authority = {
                **_object(stage["authority"], "authority"),
                "requirement_revision": request["requirement_fingerprint"],
                "design_revision": None,
                "design_fingerprint": None,
                "session_id": session,
                "worktree_revision": revision,
                "authority_revision": _integer(
                    _object(stage["authority"], "authority")["authority_revision"],
                    "authority revision",
                )
                + 1,
                "authority_fingerprint": review_evidence.content_fingerprint(
                    {
                        "request": material,
                        "session_id": session,
                        "worktree_revision": revision,
                        "previous": _object(stage["authority"], "authority")[
                            "authority_fingerprint"
                        ],
                    }
                ),
            }
            decision = review_evidence.portable_artifact_reference(
                artifacts,
                artifacts.put(
                    "phase-amendment-decision",
                    {
                        "schema": "taskplane.human-amendment-decision/v1",
                        "run_id": _text(context["run_id"], "run id"),
                        "request_fingerprint": request_fp,
                        "request": material,
                        "actor": actor,
                        "session_id": session,
                        "authority": authority,
                    },
                ),
            )
            lens_packet = {
                "schema": "taskplane.lens-evidence/v1",
                "entries": [],
                "human_amendments": [decision],
            }
            lens_packet["fingerprint"] = review_evidence.content_fingerprint(lens_packet)
            rows.append(
                {
                    "artifact_class": "lens-evidence",
                    "artifact_schema_version": lens_packet["schema"],
                    "reference": review_evidence.portable_artifact_reference(
                        artifacts, artifacts.put("lens-evidence", lens_packet)
                    ),
                }
            )
            active = fresh["active_stage_projection"]["active_stage_ids"]
            old_stages = [
                runtime._indexed_stage(store, fresh, _text(context["run_id"], "run id"), sid)
                for sid in active
            ]
            value = {
                "schema": SCHEMA,
                "run_id": _text(context["run_id"], "run id"),
                "request_fingerprint": request_fp,
                "phase": request["phase"],
                "actor": actor,
                "reason": request["reason"],
                "session_id": session,
                "decided_at": at,
                "from_step": state["step"],
                "previous_stages": [
                    {"stage_id": old["stage_id"], "fingerprint": old["fingerprint"]}
                    for old in old_stages
                ],
                "successor_stage_id": successor_id,
                "authority": authority,
                "requirement_fingerprint": request["requirement_fingerprint"],
                "candidate_fingerprint": request["candidate_fingerprint"],
                "artifacts": rows,
                "files": snapshot["files"],
                "previous_workflow": old_state,
                "workers": workers,
                "decision": decision,
                "review_basis": "human-directed-amendment",
                "automated_review": "superseded-not-passed",
                "design_approval": "pending",
            }
            reference = review_evidence.portable_artifact_reference(
                artifacts, artifacts.put("phase-amendment", value)
            )
            authorization = {
                "actor": actor,
                "session_id": session,
                "authorized_at": at,
                "operation_id": operation,
                "authority_record": {
                    "schema": "taskplane.authority-record-reference/v1",
                    "authority_schema": "taskplane.consolidated-authorization/v1",
                    "revision": authority["authority_revision"],
                    "fingerprint": authority["authority_fingerprint"],
                },
            }
            handoff = stage_handoff.create_manifest(
                artifacts,
                producer_stage_id=_text(stage["stage_id"], "stage id"),
                producer_outcome="closed",
                requirement=new_requirement,
                design=None,
                target=None,
                commit=None,
                contracts={"provided": [], "consumed": [], "changed": []},
                deliverables=["human scope amendment"],
                evidence_references=[reference],
                selected_artifacts=[
                    _object(row["reference"], "artifact reference") for row in rows
                ],
                exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
                authorization=authorization,
                allow_nonconsumable_reuse=True,
            )
            handoff_ref = review_evidence.portable_artifact_reference(
                artifacts, stage_handoff.store_manifest(artifacts, handoff)
            )
            # Product changes reopen Design; the human owns the amended WHAT.
            successor = stage_entities.create_stage(
                run_id=_text(context["run_id"], "run id"),
                stage_id=successor_id,
                requirement=new_requirement,
                design=None,
                stage_kind="design",
                parent_stage_ids=[],
                predecessor_stage_ids=[_text(stage["stage_id"], "stage id")],
                input_manifest_ref=handoff_ref,
                execution_root_id="execution-" + successor_id,
                deliverables=runtime._stage_loop_deliverables("design", state),
                selected_artifacts=_objects(handoff["selected_artifacts"], "selected artifacts"),
                budget=_object(stage["budget"], "stage budget"),
                dependencies=[],
                contracts=[
                    _text(item, "contract") for item in _list(stage["contracts"], "contracts")
                ],
                authority=authority,
                created_at=at,
            )

            def authorize(current_manifest: Json) -> None:
                _authorize_amendment_revision(
                    runtime, ws, context, current_manifest, revision=revision
                )
                if (
                    _snapshot(
                        runtime,
                        ws,
                        _text(request["phase"], "phase"),
                        _text(request["requirement_id"], "requirement id"),
                    )["candidate_fingerprint"]
                    != request["candidate_fingerprint"]
                ):
                    raise ValueError("candidate changed during amendment")

            def mutate(current_manifest: Json) -> Json:
                heads = copy.deepcopy(_table(current_manifest["stage_heads"], "stage heads"))
                for old in old_stages:
                    terminal = stage_entities.terminalize_stage(
                        old,
                        outcome="closed",
                        actor=actor,
                        terminalized_at=at,
                        reason_code="human-scope-amendment",
                        reason=_text(request["reason"], "reason"),
                        completed_deliverables=[],
                        completion_evidence=[reference],
                    )
                    heads[old["stage_id"]] = stage_entities._stage_head(
                        store, _text(context["run_id"], "run id"), terminal
                    )
                _lifecycle(context)._claim_execution_root(successor)
                heads[successor_id] = stage_entities._stage_head(
                    store, _text(context["run_id"], "run id"), successor
                )
                lineage = stage_entities.validate_lineage(
                    stage_entities._lineage_row(
                        parent_stage_id=None,
                        child_stage_id=successor_id,
                        input_manifest_ref=handoff_ref,
                        operation_id=operation,
                        predecessor_stage_ids=[_text(stage["stage_id"], "stage id")],
                    )
                )
                return {
                    "changes": {
                        "stage_heads": heads,
                        "lineage": [*_list(current_manifest["lineage"], "lineage"), lineage],
                        "active_stage_projection": stage_entities.active_stage_projection(
                            heads, successor_id
                        ),
                    },
                    "receipt": {
                        "operation": "amend_phase",
                        "stage_ids": sorted([*active, successor_id]),
                        "result": {"amendment": reference, "successor_head": heads[successor_id]},
                    },
                }

            store.commit_stage_operation(
                _text(context["run_id"], "run id"),
                expected_revision=_integer(manifest["revision"], "manifest revision"),
                operation_id=operation,
                request_fingerprint=request_fp,
                mutate=mutate,
                validate_authority=authorize,
            )
            updated = copy.deepcopy(state)
            for key in (
                "design_fingerprint",
                "design_approved_by",
                "plan_fingerprint",
                "plan_approved_by",
                "plan_approval",
                "approved_plan",
                "delivery_mode",
                "delivery_mode_receipt",
                "_submission",
                "_stage_completion",
                "_stage_bindings",
                "evaluate_child_evidence",
                "accepted_evaluations",
                "signoff",
                "signoff_evidence",
                "review_kernel_runs",
                "design_control_plane_binding",
                "design_lens_policy",
                "authority_packet",
                "authority_receipt",
                "authority_derivations",
                "authority_target_revision",
                "attempt_leases",
                "approved_plan_receipt",
                "test_strategy_authority_receipt",
                "engineering_review_request_changes",
                "signoff_dod",
            ):
                updated.pop(key, None)
            updated.update(
                tasks=[],
                current_task=0,
                step="design_approval" if request["phase"] == "design" else "design",
                phase_amendment={
                    "phase": request["phase"],
                    "actor": actor,
                    "reason": request["reason"],
                    "requirement_fingerprint": request["requirement_fingerprint"],
                    "candidate_fingerprint": request["candidate_fingerprint"],
                    "review_basis": "human-directed-amendment",
                    "approval": "pending",
                    "stage_id": successor_id,
                    "receipt": reference,
                    "fingerprint": reference["fingerprint"],
                },
            )
            _list(updated.setdefault("phase_amendment_history", []), "amendment history").append(
                updated["phase_amendment"]
            )
            runtime.save(ws, updated)
        _cleanup(runtime, ws, value, contracts_locked=True)
    runtime.tp.trace(
        ws,
        "loop_amend",
        phase=request["phase"],
        by=actor,
        reason=_text(request["reason"], "reason"),
        amendment=reference["fingerprint"],
    )
    return {
        "resolved": "amendment",
        "replay": False,
        "step": updated["step"],
        "phase_amendment": updated["phase_amendment"],
        "dispatch_allowed": False,
    }


def verified_handoff(
    runtime: ModuleType, lifecycle: stage_entities.StageLifecycle, manifest: Json, stage: Json
) -> Json | None:
    """The explicit amendment boundary permits the recorded revision change."""
    artifacts = lifecycle._artifact_store()
    for row in _table(manifest.get("stage_operations") or {}, "stage operations").values():
        if row.get("operation") != "amend_phase" or stage["stage_id"] not in _list(
            row["stage_ids"], "stage ids"
        ):
            continue
        value = _read(
            artifacts,
            manifest,
            _object(_object(row["result"], "operation result")["amendment"], "amendment reference"),
        )
        if value["successor_stage_id"] != stage["stage_id"]:
            continue
        if (
            _object(
                _object(
                    _object(row["result"], "operation result")["successor_head"], "successor head"
                )["object"],
                "head object",
            )["fingerprint"]
            != stage["fingerprint"]
        ):
            # Resume adds execution state; the immutable input remains pinned.
            original = stage_entities._read_indexed_stage(
                lifecycle.store,
                _text(stage["run_id"], "run id"),
                _text(stage["stage_id"], "stage id"),
                _object(
                    _object(row["result"], "operation result")["successor_head"], "successor head"
                ),
            )
            if any(
                original[key] != stage[key]
                for key in ("input_manifest_ref", "authority", "requirement")
            ):
                raise ValueError("amendment input changed")
        handoff = stage_handoff.read_manifest(
            artifacts,
            _object(stage["input_manifest_ref"], "input manifest reference"),
            expected_authority_revision=_integer(
                _object(stage["authority"], "authority")["authority_revision"], "authority revision"
            ),
            expected_authority_fingerprint=_text(
                _object(stage["authority"], "authority")["authority_fingerprint"],
                "authority fingerprint",
            ),
            allow_nonconsumable_reuse=True,
        )
        if (
            handoff["evidence_references"]
            != [
                _object(
                    _object(row["result"], "operation result")["amendment"], "amendment reference"
                )
            ]
            or handoff["selected_artifacts"] != stage["selected_artifacts"]
            or handoff["requirement"] != stage["requirement"]
            or value["authority"] != stage["authority"]
        ):
            raise ValueError("amendment handoff changed")
        return handoff
    return None


@dataclass(frozen=True)
class AmendmentPackage(stage_handoff.PhasePackage):
    store: review_evidence.ArtifactStore
    registry: agent_runtime.Registry
    value: Json
    amendment: Json

    def manifest(self) -> Json:
        return {
            **self.value,
            "produced_artifacts": [],
            "inherited_artifacts": self.amendment["artifacts"],
        }

    @property
    def artifacts(self) -> tuple[agent_runtime.Artifact, ...]:
        from taskplane import agent_runtime

        selected = []
        consumes = self.registry.admit(self.phase_id, ()).to_dict()["consumes"]
        if not isinstance(consumes, list):
            raise ValueError("phase consumes must be a list")
        for raw_declaration in consumes:
            declaration = _object(raw_declaration, "phase input declaration")
            rows = [
                row
                for row in _objects(self.amendment["artifacts"], "amendment artifacts")
                if row["artifact_class"] == declaration["artifact_class"]
            ]
            if len(rows) > 1 or (not rows and declaration["required"]):
                raise ValueError(
                    "amendment lacks required phase input: "
                    + _text(declaration["artifact_class"], "artifact class")
                )
            for row in rows:
                if row["artifact_schema_version"] != declaration["artifact_schema_version"]:
                    raise ValueError("amendment input schema differs from phase declaration")
                review_evidence.verify_portable_artifact_reference(
                    self.store, _object(row["reference"], "artifact reference")
                )
                selected.append(
                    agent_runtime.Artifact(
                        _text(row["artifact_class"], "artifact class"),
                        _text(row["artifact_schema_version"], "artifact schema"),
                        _object(row["reference"], "artifact reference"),
                    )
                )
        return tuple(selected)

    def read(self, artifact_class: str) -> Json:
        rows = [row for row in self.artifacts if row.artifact_class == artifact_class]
        if len(rows) != 1:
            raise ValueError("amendment input missing or ambiguous: " + artifact_class)
        return _object(self.store.read(dict(rows[0].reference)), "amendment input")


def package(
    store: review_evidence.ArtifactStore,
    reference: Json,
    *,
    registry: agent_runtime.Registry,
    phase_id: str,
    expected_authority_revision: int,
    expected_authority_fingerprint: str,
    expected_run_id: str,
    expected_candidate_fingerprint: str,
) -> AmendmentPackage | None:
    """Consume human-scoped input without inventing an accepted agent result."""
    value = _object(store.read(reference), "amendment handoff")
    if value.get("schema") != stage_handoff.SCHEMA:
        return None
    refs = [
        ref
        for ref in _objects(value.get("evidence_references", []), "evidence references")
        if ref["kind"] == "phase-amendment"
    ]
    if not refs:
        return None
    if len(refs) != 1 or phase_id not in {"design", "plan"}:
        raise ValueError("human amendment package is not a Design/Plan input")
    from taskplane import loop, stage_loop

    manifest = stage_loop._stage_store(loop, store.workspace, expected_run_id).load(expected_run_id)
    amendment = _read(store, manifest, refs[0])
    stage_handoff.read_manifest(
        store,
        reference,
        expected_authority_revision=expected_authority_revision,
        expected_authority_fingerprint=expected_authority_fingerprint,
        allow_nonconsumable_reuse=True,
    )
    authority = _object(amendment["authority"], "authority")
    if (
        authority["authority_revision"] != expected_authority_revision
        or authority["authority_fingerprint"] != expected_authority_fingerprint
    ):
        raise ValueError("human amendment authority differs from phase input")
    route = phase_records.phase_routing(manifest)
    if (
        route is None
        or route["result"]["configuration"]["candidate_fingerprint"]
        != expected_candidate_fingerprint
    ):
        raise ValueError("human amendment run candidate changed")
    if sorted(
        _objects(value["selected_artifacts"], "selected artifacts"),
        key=review_evidence.canonical_bytes,
    ) != sorted(
        [row["reference"] for row in _objects(amendment["artifacts"], "amendment artifacts")],
        key=review_evidence.canonical_bytes,
    ):
        raise ValueError("human amendment selected artifacts changed")
    if phase_id == "plan":
        state = manifest["workflow"]
        if not state.get("design_approved_by") or not state.get("design_fingerprint"):
            raise ValueError("amended Design requires separate human approval before Plan")
    result = AmendmentPackage(
        store=store,
        registry=registry,
        reference=reference,
        phase_id=phase_id,
        authority_revision=expected_authority_revision,
        authority_fingerprint=expected_authority_fingerprint,
        run_id=expected_run_id,
        candidate_fingerprint=expected_candidate_fingerprint,
        value=value,
        amendment=amendment,
    )
    result.artifacts
    verify_human_evidence(
        store, [_object(amendment["decision"], "amendment decision")], expected_run_id
    )
    return result


def verify_human_evidence(
    store: review_evidence.ArtifactStore, references: list[Json], run_id: str
) -> None:
    """Human scope decisions must be committed, not worker-authored claims."""
    if not references:
        return
    from taskplane import loop, stage_loop

    manifest = stage_loop._stage_store(loop, store.workspace, run_id).load(run_id)
    for reference in references:
        decisions = []
        for row in _table(manifest.get("stage_operations") or {}, "stage operations").values():
            if row.get("operation") == "amend_phase":
                value = _read(
                    store,
                    manifest,
                    _object(
                        _object(row["result"], "operation result")["amendment"],
                        "amendment reference",
                    ),
                )
                if value.get("decision") == reference:
                    decisions.append(value)
        if len(decisions) != 1:
            raise ValueError("human amendment evidence has no committed scope decision")
        decision = store.read(reference)
        if (
            decision.get("run_id") != run_id
            or decision.get("request_fingerprint") != decisions[0]["request_fingerprint"]
        ):
            raise ValueError("human amendment evidence differs from its run decision")
