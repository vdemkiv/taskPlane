"""Repository Build retains native task contracts and current quality admission.

Quality layers below are synthetic validator fixtures, not real host evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import build_quality, checkpoint, loop, phase_handoff, phase_pickup, phase_plan, test_strategy
from taskplane.tests.test_build_quality import _complete_build, _strategy, PRODUCER_ID
from taskplane.tests.test_stage_non_build_handoffs import _resume_handoff
from taskplane.tests.test_stateless_phase_pickup import _authority_chain


def _task(handoff):
    return {"id": "T-001", "title": "Keep the task-local negative path",
            "scope": ["owned.py"], "deps": [], "contracts": ["contract:phase-startup"],
            "criteria": ["Task-local negative path remains covered"],
            "acceptance_refs": [row["criterion"] for row in handoff["acceptance"]],
            "tests": handoff["acceptance"][0]["proofs"][0]}


@pytest.mark.parametrize("field,value", [
    ("id", []), ("id", "bad/id"), ("scope", [7]), ("scope", "owned.py"),
    ("deps", [None]), ("criteria", [False]), ("test_contract", []),
    ("test_strategy_authority", "foreign"), ("contracts", [{"id": []}]),
])
def test_native_plan_malformed_rows_refuse_before_projection(field, value):
    handoff = _resume_handoff("plan")
    task = _task(handoff)
    task[field] = value
    with pytest.raises(phase_pickup.PhasePickupError, match="native Plan task"):
        phase_plan.project_tasks({"tasks": [task]}, handoff)


def test_native_plan_shared_obligation_ownership_refuses():
    handoff = _resume_handoff("plan")
    first = _task(handoff)
    second = {**first, "id": "T-002", "deps": ["T-001"]}
    with pytest.raises(phase_pickup.PhasePickupError, match="one task owner"):
        phase_plan.project_tasks({"tasks": [first, second]}, handoff)


def test_existing_build_handoff_shared_ownership_refuses():
    handoff = _resume_handoff("plan")
    handoff["tasks"].append({**handoff["tasks"][0], "id": "T-002", "ordinal": 2,
                             "dependencies": ["T-001"]})
    handoff["handoff_id"] = phase_handoff.handoff_identity(handoff)
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)
    with pytest.raises(phase_pickup.PhasePickupError, match="one task owner"):
        phase_pickup.select_ready_build_task(handoff)


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True,
                                   encoding="utf-8", errors="replace").strip()


@pytest.fixture
def native_build(tmp_path):
    from taskplane import phase_build

    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Isolated fixture")
    handoff = _resume_handoff("plan")
    strategy = _strategy()
    for producer in strategy["producers"]:
        if producer["id"] == PRODUCER_ID:
            producer["interface_kind"] = "in-process"
            producer["interface_fixtures"] = []
    strategy = test_strategy.seal_strategy(strategy)
    selectors = strategy["acceptance_criteria"][0]["selectors"]
    proof = "python3 -m pytest -q " + " ".join(selectors)
    for field in ("acceptance", "obligations", "tasks"):
        for row in handoff[field]:
            row["proofs"] = [proof]
    task = _task(handoff)
    task.update(test_contract={"changed_producers": ["owned.py"]},
                test_strategy_authority={
                    "schema": "taskplane.plan-test-strategy-reference/v1",
                    "path": "design/test-strategy.json",
                    "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
                    "criterion_ids": [strategy["acceptance_criteria"][0]["id"]],
                    "changed_producer_ids": [PRODUCER_ID]})
    plan = {**loop._plan_output_contract({"requirement_id": handoff["requirement"]["id"],
        "design_fingerprint": handoff["design"]["fingerprint"]})["template"],
        "replan_history": [], "tasks": [task]}
    design = {"requirement": handoff["requirement"]["id"],
              "acceptance_map": [{"criterion": row["criterion"], "tests": selectors}
                                 for row in handoff["acceptance"]],
              "test_strategy": {"authority": {key: value for key, value in
                  task["test_strategy_authority"].items()
                  if key not in {"criterion_ids", "changed_producer_ids"}}}}
    design["test_strategy"]["authority"]["schema"] = "taskplane.design-test-strategy-reference/v1"
    requirement = {"id": handoff["requirement"]["id"],
                   "acceptance": [row["criterion"] for row in handoff["acceptance"]],
                   "contracts": handoff["contracts"], "depends_on": [], "open_questions": []}
    values = {"plan": ("plan/tasks.json", plan), "design": ("design/contract.json", design),
              "test-strategy": ("design/test-strategy.json", strategy),
              "requirement": ("inputs/requirement.json", requirement),
              "graph": ("inputs/graph.json", {"modules": {}, "edges": [], "meta": {}})}
    (root / ".gitignore").write_text(".taskplane/\n", encoding="utf-8")
    (root / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    for path, value in values.values():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
    for selector in selectors:
        target = root / selector.split("::", 1)[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Tracked selector-path fixture; no test execution is claimed.\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "seal isolated native inputs")
    handoff["repository"]["id"] = phase_handoff.repository_identity(root)
    handoff["source"] = {"commit": _git(root, "rev-parse", "HEAD"),
                         "tree": _git(root, "rev-parse", "HEAD^{tree}")}
    handoff["authority_receipts"] = _authority_chain(
        handoff["repository"]["id"], handoff["source"]["commit"], handoff["source"]["tree"],
        [("initial-authorization", handoff["requirement"]["fingerprint"]),
         ("design-approval", handoff["design"]["fingerprint"]),
         ("plan-approval", handoff["plan"]["fingerprint"])])
    refs = {kind: phase_handoff.create_repository_artifact_reference(root, path, kind=kind)
            for kind, (path, _) in values.items()}
    for kind in ("requirement", "design", "plan"):
        handoff[kind]["artifact"] = refs[kind]
    handoff["selected_artifacts"] = sorted(refs.values(), key=lambda row: (row["kind"], row["digest"]))
    handoff["tasks"] = phase_plan.project_tasks(plan, handoff)
    handoff["producer"] = {"phase": "plan", "outcome": "done"}
    handoff["successor"] = {"phase": "build", "mode": "next-phase"}
    handoff["progress_receipts"][1] = phase_handoff.create_progress_receipt(
        producer="engine:fixture", sequence=2, phase="plan", obligation_id="O2",
        task_id=None, status="green", predecessor_receipt_fingerprint=handoff["progress_receipts"][0]["fingerprint"])
    handoff["lineage"]["predecessor_receipt_head"] = handoff["progress_receipts"][-1]["fingerprint"]
    handoff["progress"] = {"completed": ["O1", "O2"], "remaining": []}
    handoff["handoff_id"] = phase_handoff.handoff_identity(handoff)
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)
    phase_handoff.publish_phase_handoff(root, handoff)
    _git(root, "add", "exports")
    _git(root, "commit", "-qm", "publish isolated native handoff")
    assignment = phase_pickup._build_assignment(handoff, task=handoff["tasks"][0], base_revision=_git(root, "rev-parse", "HEAD"))
    (root / "owned.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(root, "add", "owned.py")
    _git(root, "commit", "-qm", "author isolated candidate")
    return root, handoff, assignment, strategy, phase_build


def test_native_build_preserves_task_contract_and_begins_only_empty_quality(native_build, monkeypatch):
    root, handoff, assignment, strategy, module = native_build
    def forbidden(*_args, **_kwargs):
        raise AssertionError("predecessor private state was read")
    monkeypatch.setattr(loop, "load", forbidden)
    monkeypatch.setattr(loop.reqs, "get_requirement", forbidden)
    monkeypatch.setattr(loop.depgraph, "load", forbidden)
    brief = module.completion_brief(str(root), handoff, assignment["task"])
    assert brief["native_task"]["criteria"] == ["Task-local negative path remains covered"]
    assert brief["native_task"]["title"] == "Keep the task-local negative path"
    assert brief["quality_admission"]["required_before_submit"] is True
    receipt = module.begin_quality_receipt(str(root), handoff, assignment)
    assert receipt["layers"] == [] and receipt["build_complete"] is False
    assert receipt["binding"]["candidate"]["id"].endswith(_git(root, "rev-parse", "HEAD"))
    assert receipt["changed_paths"] == ["owned.py"]
    assert receipt["strategy_fingerprint"] == strategy["contract_fingerprint_sha256"]


@pytest.mark.parametrize("condition", ["missing", "incomplete", "stale", "wrong-selection", "wrong-paths", "valid"])
def test_native_build_quality_admission_is_exact(native_build, condition, monkeypatch):
    root, handoff, assignment, strategy, module = native_build
    authored = checkpoint.mint_phase_authoring_result(str(root), task=assignment["task"], assignment=assignment)
    receipt = module.begin_quality_receipt(str(root), handoff, assignment)
    if condition == "stale":
        receipt["binding"]["candidate"]["id"] = "foreign"
        receipt["binding"]["candidate"]["fingerprint"] = "e" * 64
    elif condition == "wrong-selection":
        receipt["criterion_ids"] = [strategy["acceptance_criteria"][1]["id"]]
    elif condition == "wrong-paths":
        receipt["changed_paths"] = ["foreign.py"]
    receipt = build_quality.begin_receipt(strategy, binding=receipt["binding"],
        criterion_ids=receipt["criterion_ids"], changed_producer_ids=receipt["changed_producer_ids"],
        changed_paths=receipt["changed_paths"])
    if condition != "incomplete":
        receipt = _complete_build(strategy, receipt)
    path = root / module.quality_path(handoff, assignment["task"])
    path.parent.mkdir(parents=True)
    if condition != "missing":
        path.write_text(json.dumps(receipt), encoding="utf-8")
    if condition != "valid":
        def forbidden(*_args, **_kwargs):
            raise AssertionError("BUILD-C ran without native quality admission")
        monkeypatch.setattr(phase_pickup.build_c, "run_phase_pickup", forbidden)
        with pytest.raises(phase_pickup.PhasePickupError):
            phase_pickup.submit_build_pickup(str(root), handoff, assignment=assignment,
                                            authoring_result=authored)
    else:
        admitted = module.admit_quality(str(root), handoff, assignment, authored)
        assert admitted["receipt"] == receipt
        assert admitted["artifact"]["digest"] == phase_handoff.canonical_fingerprint(receipt)


def test_native_plan_projection_drift_cannot_use_legacy_fallback(native_build):
    root, handoff, assignment, _, module = native_build
    changed = copy.deepcopy(assignment["task"])
    changed["scope"] = ["foreign.py"]
    with pytest.raises(phase_pickup.PhasePickupError, match="projection"):
        module.resolve_native_task(str(root), handoff, changed)


@pytest.mark.parametrize("payload", [[], {"tasks": [7]}, {"tasks": [{"id": "T-001", "scope": [7]}]}])
def test_malformed_selected_json_plan_never_uses_legacy_path(native_build, payload):
    root, handoff, assignment, _, module = native_build
    path = root / "plan/tasks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _git(root, "add", "plan/tasks.json")
    reference = phase_handoff.create_repository_artifact_reference(root, "plan/tasks.json", kind="plan")
    _git(root, "add", "exports")
    handoff["plan"]["artifact"] = reference
    handoff["selected_artifacts"] = [reference if row["kind"] == "plan" else row for row in handoff["selected_artifacts"]]
    with pytest.raises(phase_pickup.PhasePickupError):
        module.resolve_native_task(str(root), handoff, assignment["task"])


def test_explicit_markdown_plan_remains_legacy(native_build):
    root, handoff, assignment, _, module = native_build
    path = root / "plan/legacy.md"
    path.write_text("# Existing legacy Plan\n", encoding="utf-8")
    _git(root, "add", "plan/legacy.md")
    reference = phase_handoff.create_repository_artifact_reference(root, "plan/legacy.md", kind="plan", media_type="text/markdown")
    _git(root, "add", "exports")
    handoff["plan"]["artifact"] = reference
    handoff["selected_artifacts"] = [reference if row["kind"] == "plan" else row for row in handoff["selected_artifacts"]]
    assert module.resolve_native_task(str(root), handoff, assignment["task"]) is None
    # A second native Plan cannot be hidden behind the legacy media type.
    native_reference = phase_handoff.create_repository_artifact_reference(root, "plan/tasks.json", kind="plan")
    handoff["selected_artifacts"].append(native_reference)
    with pytest.raises(phase_pickup.PhasePickupError, match="ambiguous"):
        module.resolve_native_task(str(root), handoff, assignment["task"])


def test_quality_evidence_is_carried_only_after_build_c(native_build, monkeypatch):
    root, handoff, assignment, strategy, module = native_build
    receipt = _complete_build(strategy, module.begin_quality_receipt(str(root), handoff, assignment))
    path = root / module.quality_path(handoff, assignment["task"])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    authored = checkpoint.mint_phase_authoring_result(str(root), task=assignment["task"], assignment=assignment)
    reached = []
    # Synthetic BUILD-C seam: this test checks result wiring, not integration proof.
    def built(*_args, **_kwargs):
        reached.append(True)
        return {"fixture": True}
    monkeypatch.setattr(phase_pickup.build_c, "run_phase_pickup", built)
    monkeypatch.setattr(phase_pickup.build_c, "validate_phase_pickup_evidence",
        lambda *_args, **_kwargs: ({"receipt_digest": "7" * 64}, {"fingerprint": "8" * 64}))
    result = phase_pickup.submit_build_pickup(str(root), handoff, assignment=assignment, authoring_result=authored)
    assert reached == [True]
    assert result["build_quality"]["receipt"] == receipt
    assert [row["obligation_id"] for row in result["progress_receipts"]] == ["O1", "O2"]


def _quality_command(root, handoff):
    request = root / ".git/quality-request.json"
    request.write_text(json.dumps({"handoff": phase_handoff.handoff_path(handoff["handoff_id"]),
                                   "task_id": handoff["tasks"][0]["id"]}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "tp.py"),
        "phase", "quality", "--request", ".git/quality-request.json", "--workspace", str(root)],
        cwd=root, env={**os.environ, "TASKPLANE_HOME": str(root.parent / "empty-runtime")},
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    assert result.stdout, result.stderr
    return result, json.loads(result.stdout)


def test_committed_quality_cli_preserves_current_and_retains_prior_bytes(native_build):
    root, handoff, assignment, strategy, module = native_build
    command, first = _quality_command(root, handoff)
    assert command.returncode == 0, command.stdout + command.stderr
    assert first["reused"] is False and first["receipt"]["layers"] == []
    path = root / first["path"]
    populated = _complete_build(strategy, first["receipt"])
    prior_bytes = json.dumps(populated, indent=3).encode("utf-8") + b"\n"
    path.write_bytes(prior_bytes)
    command, replay = _quality_command(root, handoff)
    assert command.returncode == 0, command.stdout + command.stderr
    assert replay["reused"] is True and replay["receipt"] == populated
    assert path.read_bytes() == prior_bytes
    (root / "owned.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(root, "add", "owned.py")
    _git(root, "commit", "-qm", "revise isolated candidate")
    command, fresh = _quality_command(root, handoff)
    assert command.returncode == 0, command.stdout + command.stderr
    assert fresh["reused"] is False and fresh["receipt"]["layers"] == []
    assert fresh["receipt"]["build_complete"] is False
    assert fresh["receipt"]["binding"]["candidate"] != populated["binding"]["candidate"]
    assert fresh["retained_path"] == f".taskplane/phase-quality/history/{hashlib.sha256(prior_bytes).hexdigest()}.json"
    assert (root / fresh["retained_path"]).read_bytes() == prior_bytes
    assert json.loads(path.read_bytes()) == fresh["receipt"]


@pytest.mark.parametrize("condition", ["invalid", "oversized", "symlink", "unignored", "tracked"])
def test_committed_quality_cli_refuses_unsafe_existing_receipts(native_build, condition):
    root, handoff, assignment, _, module = native_build
    path = root / module.quality_path(handoff, assignment["task"])
    path.parent.mkdir(parents=True)
    if condition == "symlink":
        path.symlink_to(root / "owned.py")
    else:
        path.write_bytes(b"x" * (1024 * 1024 + 1) if condition == "oversized" else b"invalid receipt")
    if condition == "unignored":
        (root / ".gitignore").write_text("", encoding="utf-8")
        _git(root, "add", ".gitignore")
        _git(root, "commit", "-qm", "fixture removes ignore rule")
    elif condition == "tracked":
        _git(root, "add", "-f", str(path.relative_to(root)))
        _git(root, "commit", "-qm", "fixture tracks forbidden receipt")
    before = path.read_bytes()
    command, refused = _quality_command(root, handoff)
    assert command.returncode != 0
    assert refused["status"] == "refused"
    assert path.read_bytes() == before


@pytest.mark.parametrize("condition", ["current-binding-drift", "history-conflict"])
def test_committed_quality_cli_never_silently_resets_existing_evidence(native_build, condition):
    root, handoff, assignment, strategy, module = native_build
    receipt = module.begin_quality_receipt(str(root), handoff, assignment)
    if condition == "current-binding-drift":
        receipt["binding"]["environment_digest"] = "e" * 64
        receipt = build_quality.begin_receipt(strategy, binding=receipt["binding"],
            criterion_ids=receipt["criterion_ids"], changed_producer_ids=receipt["changed_producer_ids"],
            changed_paths=receipt["changed_paths"])
    receipt = _complete_build(strategy, receipt)
    path = root / module.quality_path(handoff, assignment["task"])
    path.parent.mkdir(parents=True)
    original = json.dumps(receipt, indent=2).encode("utf-8")
    path.write_bytes(original)
    if condition == "history-conflict":
        history = path.parent / "history" / (hashlib.sha256(original).hexdigest() + ".json")
        history.parent.mkdir()
        history.write_bytes(b"unrelated history bytes")
        (root / "owned.py").write_text("VALUE = 3\n", encoding="utf-8")
        _git(root, "add", "owned.py")
        _git(root, "commit", "-qm", "revise isolated candidate")
    command, refused = _quality_command(root, handoff)
    assert command.returncode != 0 and refused["status"] == "refused"
    assert path.read_bytes() == original


def test_phase_dependency_direction_has_no_pickup_plan_quality_cycle():
    from taskplane import import_cycles
    owned = {"taskplane.phase_pickup", "taskplane.phase_plan", "taskplane.phase_build",
             "taskplane.phase_inputs"}
    inventory = import_cycles.build_inventory(Path(__file__).resolve().parents[2])
    assert [row for row in inventory["sccs"] if owned.intersection(row["members"])] == []


def test_phase_pickup_keeps_shared_error_and_assignment_api_identity():
    from taskplane import phase_inputs
    assert phase_pickup.PhasePickupError is phase_inputs.PhasePickupError
    assert phase_pickup._build_assignment is phase_inputs._build_assignment
    assert phase_pickup.validate_build_assignment is phase_inputs.validate_build_assignment
    error = phase_pickup.PhasePickupError("proof-invalid", "exact proof required")
    assert error.public_result() == phase_inputs.PhasePickupError(
        "proof-invalid", "exact proof required").public_result()
