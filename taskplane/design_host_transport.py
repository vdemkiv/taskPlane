"""Host transport for dynamically selected Design lens workers.

The enforcement kernel supplies its existing local signer, contract slots,
dispatch queue, and durable JSON primitives.  This module owns the Design
protocol: portable role references, exact-set dispatch authority, lifecycle
activity publication, replay resistance, and completion conservation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from . import run_artifacts, storage
else:
    try:
        from . import run_artifacts, storage
    except (ImportError, ValueError):  # direct-module compatibility
        import run_artifacts
        import storage


JsonDict: TypeAlias = dict[str, Any]


ROLE_REFERENCE_SCHEMA = "taskplane.role-reference/v1"
DISPATCH_INTENT_SCHEMA = "taskplane.design-lens-dispatch-intent/v1"
RESULT_SCHEMA = "taskplane.design-lens-result/v1"
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


def _fp(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()


def design_worker_brief(plan: JsonDict, worker: JsonDict) -> JsonDict:
    """Project the existing native child and its bound result protocol.

    The projection is derived after the team fingerprint is sealed: embedding
    that fingerprint inside the plan's own workers would be self-referential.
    It creates no lease, authority, activation, or default review verdict.
    """
    identity = {
        "schema": RESULT_SCHEMA,
        "lens": worker["lens"],
        "worker_identity": worker["task_name"],
        "team_plan_fingerprint": plan["fingerprint"],
        "candidate_fingerprint": plan["candidate_fingerprint"],
    }
    properties: JsonDict = {key: {"const": value} for key, value in identity.items()}
    properties.update({
        "outcome": {"enum": ["pass", "changes-required"]},
        "findings": {"type": "array", "items": {"type": "object"}},
        "fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    })
    return {
        **worker,
        **({"stage_evidence": json.loads(json.dumps(plan["stage_evidence"]))}
           if "stage_evidence" in plan else {}),
        "contract_bootstrap": {
            "schema": "taskplane.worker-contract-bootstrap/v1",
            "task_slot": worker["task_slot"],
            "worker_identity": worker["task_name"],
            "environment": {"TASKPLANE_TASK": worker["task_slot"]},
            "activation": "pending_subagent_start_binding",
        },
        "result_path": worker["output"],
        "result_template": identity,
        "result_schema": {
            "$id": RESULT_SCHEMA, "type": "object",
            "required": list(properties), "properties": properties,
        },
        "result_fingerprint": {
            "algorithm": "sha256",
            "input": "all result fields except fingerprint",
            "encoding": "utf-8", "sort_keys": True,
            "separators": [",", ":"], "ensure_ascii": False,
            "allow_nan": False,
        },
        "result_transport": {
            "codex": "apply_patch", "claude": "Write",
            "scope": worker["output"],
        },
    }


def validate_design_worker_result(plan: JsonDict, worker: JsonDict,
                                  result: object) -> JsonDict:
    """Use the same bound protocol for producer instructions and collection."""
    brief = design_worker_brief(plan, worker)
    identity = brief["result_template"]
    if (not isinstance(result, dict) or
            any(result.get(key) != value for key, value in identity.items()) or
            result.get("outcome") not in {"pass", "changes-required"} or
            not isinstance(result.get("findings"), list) or
            any(not isinstance(row, dict) for row in result["findings"])):
        raise ValueError("Design lens result contract is invalid")
    material = {key: value for key, value in result.items() if key != "fingerprint"}
    if result.get("fingerprint") != _fp(material):
        raise ValueError("Design lens result fingerprint is invalid")
    return dict(result)


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


def _validate_plan(kernel: Any, plan: object) -> list[JsonDict]:
    if not isinstance(plan, dict) or not _DIGEST.fullmatch(str(
            plan.get("fingerprint") or "")) or plan.get(
                "fingerprint") != _plan_fingerprint(plan):
        raise ValueError("Design lens team plan fingerprint is invalid")
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
        expected_output = f"design/lenses/{lens}.json"
        if (not lens or not kernel._TASK_SLOT_RE.fullmatch(slot) or
                task_name != kernel.dispatch_task_name(
                    "lens", "tp-lens", f"design-{lens}",
                    namespace=plan.get("dispatch_namespace")) or
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
                intent.get("schema") != DISPATCH_INTENT_SCHEMA or
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
                       isinstance(row.get("design_host_authority"), dict) and
                       row["design_host_authority"].get(
                           "team_plan_fingerprint") == plan["fingerprint"]]
            if len(matches) > 1:
                raise kernel._worker_lifecycle_error(
                    workspace, "Design lens dispatch registration is ambiguous")
            if matches:
                private = dict(matches[0]["design_host_authority"])
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
                    "schema": HOST_AUTHORITY_SCHEMA,
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
                    "ts": kernel._now(), "kind": "design-lens",
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
                    "design_host_authority": private,
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
        "schema": HOST_AUTHORITY_SCHEMA,
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
    output["worker_lifecycle"]["design_host_authority"] = {
        "schema": HOST_AUTHORITY_SCHEMA,
        "artifact_root": os.path.realpath(os.path.abspath(artifact_root)),
        "artifact_binding": json.loads(json.dumps(artifact_binding)),
        "worker_authority": row,
    }
    return output


def _contract_authority(kernel: Any, workspace: str,
                        contract: JsonDict) -> JsonDict | None:
    private = (contract.get("worker_lifecycle") or {}).get(
        "design_host_authority")
    if private is None:
        return None
    if not isinstance(private, dict) or private.get("schema") != \
            HOST_AUTHORITY_SCHEMA:
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
    private = (expected or {}).get("design_host_authority")
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
    existing = lifecycle.get("design_host_start_receipt")
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
        lifecycle["design_host_start_receipt"] = start
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
        "design_host_start_receipt"), event="start")
    owner = kernel._worker_event_owner(event)
    message = str(event.get("message") or event.get("reason") or "") \
        .replace("\x00", "")[:2048]
    return _append_once(
        kernel, workspace, authority, event_type=event_type, receipt=start,
        owner=owner, details={"message": message,
                              "turn_id": str(event.get("turn_id") or "")[:160]})


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
                 "authority": receipt["authority"]}))
    return rows


def _authority_projection(kernel: Any, workspace: str, plan: JsonDict,
                          authority: object,
                          workers: list[JsonDict]) -> JsonDict:
    fields = {"schema", "team_plan_fingerprint", "run_id",
              "stage_instance_id", "candidate_fingerprint",
              "artifact_binding_fingerprint", "workers", "fingerprint"}
    if (not isinstance(authority, dict) or set(authority) != fields or
            authority.get("schema") != HOST_AUTHORITY_SCHEMA or
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
                    lens: str) -> list[JsonDict]:
    receipts: list[JsonDict] = []
    for entry in entries:
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        details = metadata.get("details")
        if not isinstance(details, dict):
            continue
        receipt = details.get("receipt")
        if (metadata.get("event_type") == event_type and
                metadata.get("lens") == lens and isinstance(receipt, dict)):
            receipts.append(dict(receipt))
    return receipts


def _terminal(kernel: Any, workspace: str, receipt: object, *,
              worker: JsonDict, start: JsonDict) -> JsonDict:
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
            receipt.get("stage") != "design-lens" or
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
            rows = {event: _event_receipts(entries, event, lens) for event in
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
                start=start)
            result = kernel.load_json(
                os.path.join(workspace, str(worker["output"])), default=None,
                what="Design lens terminal result")
            result = validate_design_worker_result(plan, worker, result)
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
    "DISPATCH_INTENT_SCHEMA", "HOST_AUTHORITY_SCHEMA", "HOST_RECEIPT_SCHEMA",
    "ROLE_REFERENCE_SCHEMA", "attach_design_lens_host_authority",
    "design_terminal_activity", "portable_role_reference",
    "record_design_dispatch_assignment_activity",
    "record_design_worker_activity", "record_design_worker_start_activity",
    "register_design_lens_dispatch_plan", "validate_design_lens_dispatch_completion",
    "validate_role_reference", "verify_worker_host_receipt",
]
