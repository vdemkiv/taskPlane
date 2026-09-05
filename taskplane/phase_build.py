"""Native Plan authority and incumbent quality admission for repository Build.

No loop state is reconstructed. The approved repository artifacts supply the
task/strategy; the current checkout supplies the candidate and runtime binding.
Quality helpers never execute checks or synthesize green layers. Initialization
writes only empty current-candidate receipts and retains older evidence bytes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import build_quality, checkpoint, loop, phase_handoff, phase_inputs, phase_plan, taskplane_lite as kernel
else:
    import build_quality
    import checkpoint
    import loop
    import phase_handoff
    import phase_inputs
    import phase_plan
    import taskplane_lite as kernel


def resolve_native_task(workspace: str, handoff: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the exact approved task; only an explicit Markdown Plan is legacy."""
    phase_plan.validate_obligation_ownership(handoff, handoff["tasks"])
    reference = handoff["plan"]["artifact"]
    if [row for row in handoff["selected_artifacts"] if row["kind"] == "plan"] != [reference]:
        raise phase_inputs.PhasePickupError("authoring-invalid", "selected Plan authority is ambiguous")
    phase_handoff.validate_repository_artifact_reference(workspace, reference)
    if reference["media_type"] == "text/markdown":
        return None
    if reference["media_type"] != "application/json":
        raise phase_inputs.PhasePickupError("authoring-invalid", "Plan artifact format is unsupported")
    plan = phase_plan.selected_json(workspace, handoff, "plan")
    phase_plan.validate_identity(handoff, plan)
    projected = phase_plan.project_tasks(plan, handoff)
    matches = [row for row in projected if row["id"] == task["id"]]
    if projected != handoff["tasks"] or matches != [task]:
        raise phase_inputs.PhasePickupError("scope-widened", "native Plan task projection differs from sealed Build task")
    native = next(row for row in plan["tasks"] if row["id"] == task["id"])
    return {**loop._build_task_brief(native),
            **({"title": native["title"]} if "title" in native else {})}


def quality_path(handoff: dict[str, Any], task: dict[str, Any]) -> str:
    if kernel.plan_task_id_errors([task]) or not re.fullmatch(r"[0-9a-f]{64}", handoff["plan"]["fingerprint"]):
        raise phase_inputs.PhasePickupError("authoring-invalid", "quality receipt identity is invalid")
    return f".taskplane/phase-quality/{task['id']}-{handoff['plan']['fingerprint']}.json"


def completion_brief(workspace: str, handoff: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    native = resolve_native_task(workspace, handoff, task)
    result: dict[str, Any] = {"native_task": native, "legacy_plan": native is None}
    if native is not None and loop._build_quality_required(native):
        _quality_authority(workspace, handoff, native)
        result["quality_admission"] = {
            "required_before_submit": True,
            "receipt_schema": build_quality.BUILD_QUALITY_RECEIPT_SCHEMA_ID,
            "path": quality_path(handoff, task),
            "strategy_reference": copy.deepcopy(native["test_strategy_authority"]),
            "test_contract": copy.deepcopy(native["test_contract"]),
            "command": ["phase", "quality", "--request", "<prepared-build-request-path>"],
            "instruction": "Commit only the scoped implementation first. Run phase quality using the same "
                           "prepared Build request to begin an empty receipt for that exact revision. "
                           "Populate required layers using observed checks and build_quality.advance_validation; "
                           "never synthesize green evidence. Keep this local receipt untracked and ignored; "
                           "a new candidate requires new checks. Then use the existing phase submit request.",
        }
    return result


def _quality_authority(workspace: str, handoff: dict[str, Any], native: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    # Pin every canonical file read by the existing authority helper to the
    # selected approved input. No req/graph/loop private-state lookup is used.
    phase_plan.selected_inputs(workspace, handoff)
    state = {"design_required": True, "design_fingerprint": handoff["design"]["fingerprint"],
             "run_id": "phase:" + handoff["fingerprint"]}
    try:
        authority = loop._seal_task_test_strategy_authority(workspace, state, native)
        if authority is None:
            raise ValueError("native quality authority is missing")
        _, strategy = loop._test_strategy_artifact(workspace, native["test_strategy_authority"])
    except (ValueError, OSError) as exc:
        raise phase_inputs.PhasePickupError("proof-invalid", "approved native quality authority is invalid") from exc
    return state, authority, strategy


def _quality_context(workspace: str, handoff: dict[str, Any], assignment: dict[str, Any],
                     authored: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    task = assignment["task"]
    native = resolve_native_task(workspace, handoff, task)
    if native is None or not loop._build_quality_required(native):
        return None
    state, authority, strategy = _quality_authority(workspace, handoff, native)
    native["test_strategy_authority_receipt"] = authority
    if kernel.git_head(workspace) != authored["revision"]:
        raise phase_inputs.PhasePickupError("proof-invalid", "quality candidate is stale")
    binding = loop._build_quality_binding(workspace, state, native, "build")
    expected = build_quality.begin_receipt(
        strategy, binding=binding,
        criterion_ids=authority["selection"]["criterion_ids"],
        changed_producer_ids=authority["selection"]["changed_producer_ids"],
        changed_paths=authored["changed_paths"])
    return authority, strategy, expected


def begin_quality_receipt(workspace: str, handoff: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    admitted = phase_inputs.validate_build_assignment(assignment, handoff, checkout=workspace)
    authored = checkpoint.mint_phase_authoring_result(workspace, task=admitted["task"], assignment=admitted)
    context = _quality_context(workspace, handoff, admitted, authored)
    if context is None:
        raise phase_inputs.PhasePickupError("proof-invalid", "this task has no approved native quality contract")
    return context[2]


def _local_quality_path(workspace: str, relative: str) -> str:
    path = os.path.abspath(os.path.join(workspace, relative))
    if os.path.realpath(path) != path or os.path.commonpath((workspace, path)) != workspace:
        raise phase_inputs.PhasePickupError("proof-invalid", "quality receipt path is unsafe")
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", relative], cwd=workspace,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    tracked = phase_inputs._git(workspace, "ls-files", "--", relative)
    if ignored.returncode or tracked:
        raise phase_inputs.PhasePickupError("proof-invalid", "quality receipt must remain ignored and untracked")
    if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
        raise phase_inputs.PhasePickupError("proof-invalid", "quality receipt path is not a regular file")
    return path


def _quality_bytes(workspace: str, relative: str) -> bytes:
    path = _local_quality_path(workspace, relative)
    if os.path.getsize(path) > 1024 * 1024:
        raise ValueError("oversized quality receipt")
    return phase_handoff._safe_regular_file(workspace, relative, code="proof-invalid")[1]


def _prior_candidate(workspace: str, receipt: dict[str, Any], expected: dict[str, Any],
                     assignment: dict[str, Any], authority: dict[str, Any]) -> None:
    """Verify old evidence belongs to an earlier committed candidate of this task."""
    binding = receipt["binding"]
    candidate = binding["candidate"]
    match = re.fullmatch(re.escape(assignment["task"]["id"]) + r"@([0-9a-f]{40,64})", candidate["id"])
    if (match is None or candidate["fingerprint"] != hashlib.sha256(candidate["id"].encode("utf-8")).hexdigest()
            or binding["run_id"] != expected["binding"]["run_id"]):
        raise ValueError("foreign quality candidate")
    revision = match.group(1)
    if revision == kernel.git_head(workspace):
        raise ValueError("current candidate quality cannot be reset")
    phase_inputs._git(workspace, "merge-base", "--is-ancestor", assignment["base_revision"], revision)
    phase_inputs._git(workspace, "merge-base", "--is-ancestor", revision, "HEAD")
    changed = sorted(filter(None, phase_inputs._git(
        workspace, "diff", "--name-only", "-z", assignment["base_revision"], revision, "--").split("\0")))
    if receipt["changed_paths"] != changed:
        raise ValueError("prior quality changed paths differ")
    # The incumbent binding's stage identity, recomputed for its recorded
    # prior candidate/settings rather than falsely labeling it current proof.
    stage_material = {"run_id": binding["run_id"], "stage": "build",
                      "task": assignment["task"]["id"], "candidate": candidate,
                      "settings_digest": binding["settings_digest"],
                      "test_strategy_authority": authority["fingerprint"]}
    fingerprint = hashlib.sha256(json.dumps(stage_material, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
    if binding["stage_instance"] != "build-" + fingerprint[:24]:
        raise ValueError("prior quality stage binding differs")


def prepare_committed_quality(workspace: str, handoff: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Begin/replay quality for the same committed handoff base as submit.

    Populated current evidence is never reset. An older candidate's valid
    receipt is retained byte-for-byte locally before an empty seed replaces it.
    """
    workspace = os.path.realpath(workspace)
    checked = phase_inputs._validated_repository_handoff(workspace, handoff, allowed_task_id=task_id)
    phase_inputs._validate_plan_proofs(workspace, checked)
    task = phase_inputs.select_ready_build_task(checked, requested_task=task_id)
    manifest_path = phase_handoff.handoff_path(checked["handoff_id"])
    assignment = phase_inputs._build_assignment(checked, task=task, base_revision=phase_inputs._git(
        workspace, "log", "-1", "--format=%H", "--", manifest_path))
    try:
        expected = begin_quality_receipt(workspace, checked, assignment)
        native = resolve_native_task(workspace, checked, task)
        if native is None:
            raise ValueError("native quality task is missing")
        _, authority, strategy = _quality_authority(workspace, checked, native)
        relative = quality_path(checked, task)
        path = _local_quality_path(workspace, relative)
        phase_handoff._ensure_safe_parents(workspace, relative)
        _local_quality_path(workspace, relative + ".lock")
        lockdir = path + ".lockdir"
        if os.path.lexists(lockdir) and (os.path.islink(lockdir) or not os.path.isdir(lockdir)):
            raise ValueError("quality lock path is unsafe")
        with kernel.file_lock(path):
            retained = None
            if os.path.lexists(path):
                old_bytes = _quality_bytes(workspace, relative)
                prior = build_quality.validate_receipt(strategy, json.loads(old_bytes.decode("utf-8")))
                for field in ("criterion_ids", "selectors", "changed_producer_ids", "changed_producers"):
                    if prior[field] != expected[field]:
                        raise ValueError("quality evidence belongs to another task selection")
                if prior["binding"]["candidate"] == expected["binding"]["candidate"]:
                    if prior["binding"] != expected["binding"] or prior["changed_paths"] != expected["changed_paths"]:
                        raise ValueError("current quality binding differs; preserve evidence and recover explicitly")
                    return {"path": relative, "receipt": prior, "reused": True, "retained_path": None}
                _prior_candidate(workspace, prior, expected, assignment, authority)
                retained = ".taskplane/phase-quality/history/" + hashlib.sha256(old_bytes).hexdigest() + ".json"
                _local_quality_path(workspace, retained)
                phase_handoff._create_if_absent(workspace, retained, old_bytes)
                if _quality_bytes(workspace, relative) != old_bytes:
                    raise ValueError("quality receipt changed during recovery")
                kernel.atomic_write_bytes(path, phase_handoff.canonical_bytes(expected))
            else:
                phase_handoff._create_if_absent(workspace, relative, phase_handoff.canonical_bytes(expected))
            return {"path": relative, "receipt": expected, "reused": False, "retained_path": retained}
    except (ValueError, OSError, checkpoint.PhaseAuthoringError, phase_handoff.PhaseHandoffError) as exc:
        raise phase_inputs.PhasePickupError("proof-invalid", "quality receipt initialization refused; preserve existing evidence and repair its exact input") from exc


def admit_quality(workspace: str, handoff: dict[str, Any], assignment: dict[str, Any],
                  authoring_result: dict[str, Any]) -> dict[str, Any] | None:
    context = _quality_context(workspace, handoff, assignment, authoring_result)
    if context is None:
        return None
    authority, strategy, expected = context
    path = quality_path(handoff, assignment["task"])
    # An evidence file committed into the candidate creates circular identity
    # and broadens its approved scope. Only the declared ignored local file is read.
    try:
        data = _quality_bytes(os.path.realpath(workspace), path)
        receipt = json.loads(data.decode("utf-8"))
        admitted = build_quality.admit_build_quality(strategy, receipt, expected_binding=expected["binding"])
        for field in ("criterion_ids", "selectors", "changed_producer_ids", "changed_producers", "changed_paths"):
            if admitted[field] != expected[field]:
                raise ValueError("quality evidence scope differs from approved task")
    except (ValueError, OSError, phase_handoff.PhaseHandoffError) as exc:
        raise phase_inputs.PhasePickupError("proof-invalid", "current native Build-quality receipt is missing, incomplete, or stale") from exc
    data = phase_handoff.canonical_bytes(admitted)
    digest = phase_handoff.canonical_fingerprint(admitted)
    artifact = {"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
                "kind": "build-quality", "digest": digest, "bytes": len(data),
                "media_type": "application/json", "destination": phase_handoff.artifact_destination(digest),
                "locator": f"repo-artifact://sha256/{digest}"}
    return {"receipt": admitted, "strategy_authority": authority, "artifact": artifact}
