"""Public phase briefs and their result consumer agree without loop history."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import loop, phase_dispatch, phase_handoff, phase_pickup, phase_plan, taskplane_lite as kernel
from taskplane.tests.test_design_portable_validation import design_inputs  # noqa: F401
from taskplane.tests.test_stage_non_build_handoffs import _resume_handoff
from taskplane.tests.test_stateless_phase_pickup import _authority_chain, _published_checkout, _git, PROOF

ROOT = Path(__file__).resolve().parents[2]
EXACT_PROOF = PROOF + "::test_canonical_manifest_is_deterministic_and_content_addressed"


def _command(checkout, *args):
    result = subprocess.run(
        [sys.executable, str(ROOT / "taskplane/tp.py"), "phase", *args,
         "--workspace", str(checkout)], cwd=checkout,
        env={**os.environ, "TASKPLANE_HOME": str(checkout.parent / "empty-home")},
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    return result, json.loads(result.stdout)


def _request(checkout, name, value):
    path = f".git/{name}.json"
    (checkout / path).write_text(json.dumps(value), encoding="utf-8")
    return path


def _fixture_native_completion(checkout, dispatch):
    """Synthetic host lifecycle fixture, not a claim of real native execution."""
    event = {"session_id": "isolated-fixture-session", "agent_id": "fixture-child",
             "task_name": dispatch["task_name"]}
    kernel.bind_worker_contract_event(str(checkout), event)
    kernel.terminalize_worker_contract(
        str(checkout), event, outcome="success", submission_status="fixture-output")


def _submit_with_fixture_reviews(checkout, request):
    command, collected = _command(checkout, "submit", "--request", _request(checkout, "submit", request))
    assert command.returncode == 0, command.stdout + command.stderr
    if collected["code"] == "phase-review-required":
        paths = []
        for brief in collected["dispatches"]:
            assert brief["dispatch_allowed"] is True
            value = {**brief["result_template"], "outcome": "pass", "findings": [],
                     "evidence": "Synthetic protocol fixture, not actual native review judgment."}
            path = checkout / brief["result_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
            paths.append(brief["result_path"])
        _git(checkout, "add", "--", *paths)
        _git(checkout, "commit", "-qm", "retain synthetic focused-review results")
        for brief in collected["dispatches"]:
            _fixture_native_completion(checkout, brief)
        command, collected = _command(checkout, "submit", "--request",
            _request(checkout, "submit", collected["resume_request"]))
        assert command.returncode == 0, command.stdout + command.stderr
    assert collected["code"] == "phase-approval-required"
    return command, collected


def _with_graph(checkout, handoff, graph):
    graph = copy.deepcopy(graph)
    graph["meta"]["module_ids"] = {"taskplane": "core"}
    graph["modules"].update({row["id"]: {} for row in handoff["contracts"]})
    graph["edges"] = [{"from": "core", "to": row["id"], "kind": "provides"}
                      for row in handoff["contracts"]]
    path = "exports/pickup/graph-input.json"
    (checkout / path).write_text(json.dumps(graph), encoding="utf-8")
    requirement_path = "exports/pickup/requirement-input.json"
    requirement = {"id": handoff["requirement"]["id"],
                   "acceptance": [row["criterion"] for row in handoff["acceptance"]],
                   "contracts": handoff["contracts"], "depends_on": [], "open_questions": []}
    (checkout / requirement_path).write_text(json.dumps(requirement), encoding="utf-8")
    _git(checkout, "add", "-f", "--", path, requirement_path)
    _git(checkout, "commit", "-qm", "seal fixture baseline graph")
    reference = phase_handoff.create_repository_artifact_reference(
        checkout, path, kind="graph", media_type="application/json")
    requirement_reference = phase_handoff.create_repository_artifact_reference(
        checkout, requirement_path, kind="requirement", media_type="application/json")
    material = {key: copy.deepcopy(value) for key, value in handoff.items()
                if key not in {"schema", "handoff_id", "fingerprint"}}
    for field in ("acceptance", "obligations", "tasks"):
        for row in material[field]:
            row["proofs"] = [EXACT_PROOF]
    material["source"] = {"commit": _git(checkout, "rev-parse", "HEAD"),
                          "tree": _git(checkout, "rev-parse", "HEAD^{tree}")}
    material["requirement"] = {"id": requirement["id"], "fingerprint": requirement_reference["digest"],
                               "artifact": requirement_reference}
    material["authority_receipts"] = _authority_chain(
        handoff["repository"]["id"], material["source"]["commit"], material["source"]["tree"],
        [("initial-authorization", requirement_reference["digest"])])
    material["selected_artifacts"] = sorted(
        [*(row for row in material["selected_artifacts"] if row["kind"] != "requirement"),
         requirement_reference, reference], key=lambda row: (row["kind"], row["digest"]))
    revised = phase_handoff.create_phase_handoff(**material)
    phase_handoff.publish_phase_handoff(checkout, revised)
    _git(checkout, "add", "-f", "--", "exports/pickup")
    _git(checkout, "commit", "-qm", "publish graph-bound fixture input")
    return revised


def _author(checkout, phase, handoff, design_template):
    paths = phase_handoff.phase_output_paths(phase)
    value = (copy.deepcopy(design_template) if phase == "design" else {
             **loop._plan_output_contract({"requirement_id": handoff["requirement"]["id"],
                 "design_fingerprint": handoff["design"]["fingerprint"]})["template"],
             "replan_history": [], "tasks": [{
                 "id": "T-001", "req": handoff["requirement"]["id"],
                 "scope": ["taskplane/phase_handoff.py"], "deps": [],
                 "contracts": [row["id"] for row in handoff["contracts"]],
                 "criteria": [row["criterion"] for row in handoff["acceptance"]],
                 "acceptance_refs": [row["criterion"] for row in handoff["acceptance"]],
                 "tests": EXACT_PROOF,
                 "design_edges": [f"core->{row['id']}:provides" for row in handoff["contracts"]],
                 "impact_policy": {"local_depth": 1, "boundary_mode": "contract-only",
                                   "contract_depth": 1, "requirement_depth": 0},
             }]})
    if phase == "design":
        value["requirement"] = handoff["requirement"]["id"]
        value["contracts"] = [{**row, "description": "Sealed fixture interface"}
                              for row in handoff["contracts"]]
        value["graph"]["proposed_edges"] = [
            {"from": "core", "to": row["id"], "kind": "provides", "reason": "Fixture edge"}
            for row in handoff["contracts"]]
        value["acceptance_map"] = [{
            "criterion": row["criterion"], "design_element": "Fixture module",
            "validation": "Exact sealed test", "tests": [proof.split(" -q ", 1)[1]
                                                        for proof in row["proofs"]],
        } for row in handoff["acceptance"]]
    for path, text in ((paths[0], json.dumps(value)),
                       (paths[1], f"# Fixture {phase}\nThe current phase owns these files.\n")):
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if phase == "design":
        # Model-authored judgment stays intact; the read-only worker need not
        # gain an executable tool just to compute engine-owned hash metadata.
        value["lens_evidence"][0].pop("content_fingerprint", None)
        (checkout / paths[0]).write_text(json.dumps(value), encoding="utf-8")
    _git(checkout, "add", "--", *paths[:2])
    _git(checkout, "commit", "-qm", f"author fixture {phase} output")


def _complete_artifact_phase(checkout, handoff_path, design_template):
    handoff = phase_handoff.load_phase_handoff(checkout, handoff_path)
    command, started = _command(checkout, "pickup", handoff_path)
    assert command.returncode == 0, command.stdout + command.stderr
    phase = handoff["successor"]["phase"]
    _author(checkout, phase, handoff, design_template)
    _fixture_native_completion(checkout, started["dispatch"])
    request = {**started["dispatch"]["completion"]["seal_request"], "status": "done"}
    command, collected = _submit_with_fixture_reviews(checkout, request)
    assert command.returncode == 0, command.stdout + command.stderr
    _git(checkout, "add", "--", *collected["commit_paths"])
    _git(checkout, "commit", "-qm", "seal fixture output")
    request = collected["export_request"]
    request["decision"] = {
        "schema": "taskplane.human-gate-decision/v1", "gate": phase + "-approval",
        "actor": "human:fixture", "context": "isolated integration fixture",
        "decision": "approved", "subject_fingerprint": collected["subject_fingerprint"],
    }
    command, exported = _command(checkout, "export", "--request",
                                 _request(checkout, "export", request))
    assert command.returncode == 0, command.stdout + command.stderr
    _git(checkout, "add", "-f", "exports/pickup")
    _git(checkout, "commit", "-qm", "publish fixture phase handoff")
    return exported["handoff_path"]


def test_design_plan_build_public_chain_without_predecessor_runtime(tmp_path, design_inputs):
    checkout, initial = _published_checkout(tmp_path, "design")
    initial = _with_graph(checkout, initial, design_inputs[3])
    handoff_path = phase_handoff.handoff_path(initial["handoff_id"])
    for phase in ("design", "plan"):
        handoff_path = _complete_artifact_phase(checkout, handoff_path, design_inputs[1])
        successor = tmp_path / (phase + "-successor")
        _git(checkout, "clone", "-q", str(checkout), str(successor))
        _git(successor, "config", "user.email", "phase-test@example.invalid")
        _git(successor, "config", "user.name", "Phase test")
        checkout = successor
    command, started = _command(checkout, "pickup", handoff_path)
    assert command.returncode == 0, command.stdout + command.stderr
    assert started["phase"] == "build"
    source = checkout / "taskplane/phase_handoff.py"
    source.write_text(source.read_text(encoding="utf-8") +
                      "\n# Fixture implementation through the public phase chain.\n",
                      encoding="utf-8")
    _git(checkout, "add", "--", "taskplane/phase_handoff.py")
    _git(checkout, "commit", "-qm", "implement fixture task")
    command, submitted = _command(checkout, "submit", "--request", _request(
        checkout, "submit", started["dispatch"]["completion"]["submit_request"]))
    assert command.returncode == 0, command.stdout + command.stderr
    assert submitted["code"] == "build-integrated"
    assert submitted["next_handoff"]["outcome"] == "done"
    assert submitted["progress_receipt"]["phase"] == "build"
    assert submitted["progress_receipt"]["status"] == "green"


@pytest.mark.parametrize("phase", ["design", "plan"])
@pytest.mark.parametrize("outcome", ["done", "interrupted"])
def test_public_artifact_phase_collect_export_and_fresh_successor(
        tmp_path, phase, outcome, design_inputs):
    checkout, handoff = _published_checkout(tmp_path, "design")
    handoff = _with_graph(checkout, handoff, design_inputs[3])
    handoff_path = phase_handoff.handoff_path(handoff["handoff_id"])
    if phase == "plan":
        handoff_path = _complete_artifact_phase(checkout, handoff_path, design_inputs[1])
        handoff = phase_handoff.load_phase_handoff(checkout, handoff_path)
    command, started = _command(checkout, "pickup", handoff_path)
    assert command.returncode == 0, command.stderr + command.stdout
    dispatch = started["dispatch"]
    assert dispatch["role"] == ("tp-designer" if phase == "design" else "tp-planner")
    assert dispatch["selected_artifact_content"]
    assert not kernel.load_active(str(checkout))  # root is not the child
    replay_command, replay = _command(checkout, "pickup", handoff_path)
    assert replay_command.returncode == 0, replay_command.stdout
    assert replay["dispatch"] == dispatch
    assert replay["startup_fingerprint"] == started["startup_fingerprint"]

    _author(checkout, phase, handoff, design_inputs[1])
    if phase == "plan" and outcome == "interrupted":
        # A genuinely partial first Plan has no executable tasks yet.
        draft_path = checkout / "plan/tasks.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["tasks"] = []
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        _git(checkout, "add", "--", "plan/tasks.json")
        _git(checkout, "commit", "-qm", "retain incomplete Plan draft")
    _fixture_native_completion(checkout, dispatch)
    submit = {**dispatch["completion"]["seal_request"], "status": outcome}
    command, collected = _submit_with_fixture_reviews(checkout, submit)
    assert command.returncode == 0, command.stderr + command.stdout
    assert collected["code"] == "phase-approval-required"
    assert collected["approval_granted"] is False
    _git(checkout, "add", "--", *collected["commit_paths"])
    _git(checkout, "commit", "-qm", "record exact phase output result")

    export = collected["export_request"]
    # Explicit fixture authority, not a native human observation.
    export["decision"] = {
        "schema": "taskplane.human-gate-decision/v1", "gate": phase + "-approval",
        "actor": "human:fixture", "context": "isolated integration fixture",
        "decision": "approved", "subject_fingerprint": collected["subject_fingerprint"],
    }
    command, exported = _command(checkout, "export", "--request",
                                 _request(checkout, "export", export))
    assert command.returncode == 0, command.stderr + command.stdout
    _git(checkout, "add", "-f", "exports/pickup")
    _git(checkout, "commit", "-qm", "publish successor handoff")

    successor = tmp_path / "successor"
    _git(checkout, "clone", "-q", str(checkout), str(successor))
    next_handoff = phase_handoff.load_phase_handoff(successor, exported["handoff_path"])
    expected_phase = phase if outcome == "interrupted" else {
        "design": "plan", "plan": "build"}[phase]
    command, fresh = _command(successor, "resume" if outcome == "interrupted" else "pickup",
                             exported["handoff_path"])
    assert command.returncode == 0, command.stderr + command.stdout
    assert fresh["phase"] == expected_phase
    assert fresh["dispatch"]["protocol"] == "repository-phase"
    assert next_handoff["lineage"]["predecessor_handoff_fingerprint"] == handoff["fingerprint"]
    assert next_handoff["progress_receipts"][:len(handoff["progress_receipts"])] == \
        handoff["progress_receipts"]


def test_empty_plan_draft_can_only_resume_plan():
    handoff = _resume_handoff("plan")
    handoff["tasks"] = []
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)
    assert phase_handoff.validate_manifest(handoff)["tasks"] == []
    for producer, successor in [
            ({"phase": "plan", "outcome": "done"}, {"phase": "build", "mode": "next-phase"}),
            ({"phase": "build", "outcome": "interrupted"}, {"phase": "build", "mode": "same-phase-resume"})]:
        changed = {**handoff, "producer": producer, "successor": successor}
        changed["handoff_id"] = phase_handoff.handoff_identity(changed)
        changed["fingerprint"] = phase_handoff.manifest_fingerprint(changed)
        with pytest.raises(phase_handoff.PhaseHandoffError, match="tasks must be empty"):
            phase_handoff.validate_manifest(changed)


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_phase_owner_brief_names_real_outputs_and_current_result(phase):
    handoff = _resume_handoff(phase)
    startup = kernel.create_stateless_phase_startup(handoff, attempt_id="attempt-current")
    brief = phase_dispatch.worker_brief(handoff, startup)
    producer = startup["workers"][0]["producer_contract"]
    assert producer["write_allow"] == phase_handoff.phase_output_paths(phase)
    assert brief["output_paths"] == producer["write_allow"]
    assert brief["task_slot"] == producer["task_slot"]
    assert brief["task_name"] == startup["workers"][0]["task_name"]
    assert brief["role"] == ("tp-designer" if phase == "design" else "tp-planner")
    assert brief["fork_turns"] == "none"
    assert brief["result_template"]["attempt_id"] == "attempt-current"
    assert brief["result_template"]["handoff_fingerprint"] == handoff["fingerprint"]
    assert brief["completion"]["worker_may_approve"] is False
    assert brief["completion"]["submit_request"]["result"] == f"{phase}/result.json"
    assert "lease" not in json.dumps(brief)
    assert "contract_bootstrap" not in brief


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_result_identity_and_exact_evidence_cannot_drift(phase):
    handoff = _resume_handoff(phase)
    startup = kernel.create_stateless_phase_startup(handoff, attempt_id="attempt-current")
    brief = phase_dispatch.worker_brief(handoff, startup)
    refs = [
        {"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
         "kind": kind, "digest": digest, "bytes": 1,
         "media_type": media,
         "destination": phase_handoff.artifact_destination(digest),
         "locator": f"repo-artifact://sha256/{digest}"}
        for kind, digest, media in [(phase, "1" * 64, "application/json"),
                                    (f"{phase}-narrative", "2" * 64, "text/markdown")]
    ]
    result = phase_dispatch.seal_result(brief, status="done", evidence=refs)
    assert phase_dispatch.validate_result(brief, result, refs) == result
    for field, value in [("attempt_id", "attempt-foreign"),
                         ("handoff_fingerprint", "9" * 64),
                         ("artifact_fingerprint", "8" * 64),
                         ("evidence", refs[:1]), ("status", "approved")]:
        altered = copy.deepcopy(result)
        altered[field] = value
        altered["fingerprint"] = phase_handoff.manifest_fingerprint(altered)
        with pytest.raises(ValueError):
            phase_dispatch.validate_result(brief, altered, refs)


@pytest.mark.parametrize("tamper", ["startup", "content", "scope", "tools"])
def test_current_native_cache_is_revalidated_without_scope_or_input_drift(tmp_path, tamper):
    checkout, handoff = _published_checkout(tmp_path, "design")
    startup = kernel.create_stateless_phase_startup(handoff)
    _, dispatch = phase_dispatch.bind_native_worker(str(checkout), handoff, startup)
    contract_path = kernel.active_contract_path(str(checkout), dispatch["task_slot"])
    contract = kernel.load_json(contract_path)
    if tamper == "startup":
        contract["phase_startup"]["workers"][0]["lease"]["nonce"] = "0" * 32
    elif tamper == "content":
        contract["phase_dispatch"]["selected_artifact_content"][0]["text"] = "foreign input"
    elif tamper == "scope":
        contract["write_allow"].append("taskplane/**")
    else:
        contract["allowed_tools"].append("Bash")
    kernel.atomic_write_json(contract_path, contract)
    with pytest.raises((ValueError, kernel.StageDispatchError)):
        phase_dispatch.bind_native_worker(str(checkout), handoff, startup)
    assert kernel.load_json(contract_path) == contract  # no silent repair/widening


def test_public_collection_rejects_unobserved_or_foreign_attempt(tmp_path, design_inputs):
    checkout, handoff = _published_checkout(tmp_path, "design")
    handoff = _with_graph(checkout, handoff, design_inputs[3])
    startup = kernel.create_stateless_phase_startup(handoff)
    _, dispatch = phase_dispatch.bind_native_worker(str(checkout), handoff, startup)
    _author(checkout, "design", handoff, design_inputs[1])
    evidence = phase_dispatch.output_references(str(checkout), "design")
    result = phase_dispatch.seal_result(dispatch, status="done", evidence=evidence)
    with pytest.raises(phase_pickup.PhasePickupError, match="no observed current worker completion"):
        phase_dispatch.collect_output(str(checkout), handoff, result)
    _fixture_native_completion(checkout, dispatch)
    with pytest.raises(phase_pickup.PhasePickupError, match="no prepared current attempt"):
        phase_dispatch.collect_output(str(checkout), handoff, result)
    assert phase_dispatch.collect_output(
        str(checkout), handoff, result, require_reviews=False)["result"] == result
    result["attempt_id"] = "foreign-attempt"
    result["fingerprint"] = phase_handoff.manifest_fingerprint(result)
    with pytest.raises(phase_pickup.PhasePickupError, match="no observed current worker completion"):
        phase_dispatch.collect_output(str(checkout), handoff, result)


@pytest.mark.parametrize("case", ["wrong-hash", "missing-judgment", "post-stop-drift"])
def test_mechanical_design_hash_never_repairs_judgment_or_observation(tmp_path, design_inputs, case):
    checkout, handoff = _published_checkout(tmp_path, "design")
    handoff = _with_graph(checkout, handoff, design_inputs[3])
    startup = kernel.create_stateless_phase_startup(handoff)
    _, dispatch = phase_dispatch.bind_native_worker(str(checkout), handoff, startup)
    _author(checkout, "design", handoff, design_inputs[1])
    path = checkout / "design/contract.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if case == "post-stop-drift":
        _fixture_native_completion(checkout, dispatch)
        artifact["open_questions"] = ["Unobserved change"]
    elif case == "wrong-hash":
        artifact["lens_evidence"][0]["content_fingerprint"] = "f" * 64
    else:
        artifact["lens_evidence"][0].pop("verdict")
    path.write_text(json.dumps(artifact), encoding="utf-8")
    _git(checkout, "add", "--", "design/contract.json")
    _git(checkout, "commit", "-qm", "retain negative producer fixture")
    if case != "post-stop-drift":
        _fixture_native_completion(checkout, dispatch)
    before = path.read_bytes()
    result = phase_dispatch.seal_result(dispatch, status="done",
        evidence=phase_dispatch.output_references(str(checkout), "design"))
    with pytest.raises((ValueError, phase_pickup.PhasePickupError)):
        phase_dispatch.collect_output(str(checkout), handoff, result, require_reviews=False)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field, value", [("requirement", "R-FOREIGN"),
    ("delivery_mode", "iteration"), ("automatic_lenses", ["security"]),
    ("plan_authority", "design:foreign"), ("replan_history", None), ("tasks", {})])
def test_repository_plan_cannot_replace_native_authority_with_transport_fields(field, value):
    handoff = _resume_handoff("plan")
    plan = {**loop._plan_output_contract({"requirement_id": handoff["requirement"]["id"],
        "design_fingerprint": handoff["design"]["fingerprint"]})["template"], "replan_history": []}
    phase_plan.validate_identity(handoff, plan)
    plan[field] = value
    with pytest.raises(phase_pickup.PhasePickupError, match="native task schema"):
        phase_plan.validate_identity(handoff, plan)


def test_plan_projection_preserves_task_ownership_and_refuses_ambiguous_proofs():
    handoff = {"acceptance": [
        {"id": "AC1", "criterion": "first", "proofs": ["pytest tests/first.py"]},
        {"id": "AC2", "criterion": "second", "proofs": ["pytest tests/second.py"]}],
        "obligations": [{"id": "O1", "acceptance": ["AC1"]}, {"id": "O2", "acceptance": ["AC2"]}]}
    plan = {"tasks": [{"id": "T2", "deps": ["T1"], "scope": ["second.py"],
                       "criteria": ["second"], "tests": "pytest tests/second.py", "contracts": ["contract:second"]},
                      {"id": "T1", "deps": [], "scope": ["first.py"],
                       "criteria": ["first"], "tests": "pytest tests/first.py", "contracts": ["contract:first"]}]}
    tasks = phase_plan.project_tasks(plan, handoff)
    assert [(task["id"], task["ordinal"], task["acceptance"], task["proofs"]) for task in tasks] == [
        ("T1", 1, ["AC1"], ["pytest tests/first.py"]), ("T2", 2, ["AC2"], ["pytest tests/second.py"])]
    plan["tasks"][0]["tests"] = "pytest tests"
    with pytest.raises(phase_pickup.PhasePickupError, match="exactly match"):
        phase_plan.project_tasks(plan, handoff)


def test_old_markdown_plan_inputs_refuse_before_native_activation(tmp_path, monkeypatch):
    checkout, handoff = _published_checkout(tmp_path, "plan")
    startup = kernel.create_stateless_phase_startup(handoff)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("incompatible Plan input must not activate a worker")

    monkeypatch.setattr(kernel, "activate", forbidden)
    with pytest.raises(phase_pickup.PhasePickupError, match="sealed requirement JSON"):
        phase_dispatch.bind_native_worker(str(checkout), handoff, startup)
