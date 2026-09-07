"""Actual Plan/Build/acceptance owners; simulated host and test observations."""
import copy
import json
import os
from pathlib import Path
import runpy
import subprocess

import pytest

from taskplane import loop, review_evidence, stage_entities, test_strategy, wiring_closure
from taskplane.tests.test_r0001_phase_agents_spec import _registry, _run, _strategy
from taskplane.tests.test_r0001_j2_quality_handoff import _build_stage

POSITIVE = "taskplane/tests/test_r0001_j5_contributions.py::test_plan_package_reaches_build_with_multiple_tasks_proofs_and_joint_evidence"
NEGATIVE = "taskplane/tests/test_r0001_j5_contributions.py::test_removed_contribution_proof_joint_binding_or_candidate_change_blocks_journey"
PROOFS = ["taskplane/tests/test_r0001_j5_contributions.py::test_" + name + "_component_behavior" for name in ("left", "right")]
JOINT = "taskplane/tests/test_r0001_j5_contributions.py::test_joint_behavior"
SEVERED = "taskplane/tests/test_r0001_j5_contributions.py::test_joint_behavior_refuses_missing_component"
CRITERIA = ["FP-AC01 Both components contribute", "FP-AC02 Joint behavior is observed"]


def _source(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "left.py").write_text("def produce():\n    return 2\n")
    (root / "right.py").write_text("def consume(value):\n    return value * 3\n")
    return root


def _proof_source(tmp_path):
    supplied = os.environ.get("TASKPLANE_J5_PROOF_SOURCE")
    return Path(supplied) if supplied else _source(tmp_path / "proof-source")


def test_left_component_behavior(tmp_path):
    module = runpy.run_path(str(_proof_source(tmp_path) / "left.py"))
    assert module["produce"]() == 2


def test_right_component_behavior(tmp_path):
    module = runpy.run_path(str(_proof_source(tmp_path) / "right.py"))
    assert module["consume"](2) == 6


def test_joint_behavior(tmp_path):
    source = _proof_source(tmp_path)
    left, right = [runpy.run_path(str(source / (name + ".py"))) for name in ("left", "right")]
    assert right["consume"](left["produce"]()) == 6


def test_joint_behavior_refuses_missing_component(tmp_path):
    right = runpy.run_path(str(_proof_source(tmp_path) / "right.py"))
    with pytest.raises(TypeError):
        right["consume"](None)


def _seed(tmp_path):
    _source(tmp_path / "source")
    store, registry = review_evidence.ArtifactStore(str(tmp_path / "artifacts")), _registry()
    strategy = _strategy()
    strategy["acceptance_criteria"] = [
        {"id": "FP-AC01", "selectors": [PROOFS[0]]},
        {"id": "FP-AC02", "selectors": [PROOFS[1]]}]
    strategy = test_strategy.seal_strategy(strategy)
    product, _ = _run(tmp_path, store, registry, "product", {"requirement": {
        "schema": "taskplane.requirement/v1", "id": "R-T11", "acceptance_criteria": CRITERIA}})
    # Zero-based inventory metadata only: no withdrawn fresh-install execution.
    journey = {"id": "J0", "owner": "joint-owner", "criteria": ["FP-AC01", "FP-AC02"],
        "positive": JOINT, "severed": SEVERED, "evidence_mode": "simulated"}
    design = {"schema": "taskplane.design/v1", "requirement": "R-T11",
        "design_counts": {"criteria": 2, "contracts": 1, "journeys": 1, "proposed_edges": 1},
        "acceptance_map": [{"criterion_id": f"FP-AC0{i + 1}", "criterion": text,
            "owner": f"acceptance-owner-{i}", "tests": [PROOFS[i]]}
            for i, text in enumerate(CRITERIA)],
        "contracts": [{"id": "contract:joint", "relation": "provides"}],
        "journeys": [journey], "module_ownership": [{"responsibility": "joint", "owner": "joint-owner"}],
        "graph": {"proposed_edges": [{"from": "owned", "to": "contract:joint", "kind": "provides"}]},
        "test_strategy": {"authority": {"schema": "taskplane.design-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": strategy["contract_fingerprint_sha256"]}}}
    state = {"run_id": "run-t11", "design_required": True,
        "design_fingerprint": review_evidence.content_fingerprint(design)}
    design_ref, _ = _run(tmp_path, store, registry, "design", {"design": design, "test-strategy": strategy}, product, state=state)
    tasks = [{"id": name, "owner": name + "-owner", "scope": [name + ".py"],
        "tests": "python3 -m pytest -q " + " ".join(PROOFS if name == "left" else list(reversed(PROOFS))),
        "criteria": CRITERIA, "acceptance_refs": CRITERIA, "contracts": ["contract:joint"],
        "design_edges": ["owned->contract:joint:provides"],
        "test_contract": {"changed_producers": ["taskplane/loop.py"]},
        "test_strategy_authority": {"schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
            "criterion_ids": ["FP-AC01", "FP-AC02"], "changed_producer_ids": ["spec-package"]}}
        for name in ("left", "right")]
    plan = {"requirement": "R-T11", "tasks": tasks, "journeys": [{**journey, "task": "left"}],
        "wiring_manifest": [{"id": edge, "task": "left", "producer": "owned", "boundary": "joint", "consumer": "owned",
            "positive_selector": f"taskplane/tests/test_r0001_wiring_manifest.py::test_wiring_production_path[{edge}]",
            "severed_selector": f"taskplane/tests/test_r0001_wiring_manifest.py::test_wiring_severed_edge_fails_closed[{edge}]"}
            for edge in wiring_closure.PLAN_WIRING_EDGE_IDS]}
    plan_ref, _ = _run(tmp_path, store, registry, "plan", {"plan-task": plan}, design_ref, state=state)
    package, _ = _build(tmp_path, store, registry, state, plan_ref)
    build_ref, _ = _run(tmp_path, store, registry, "build", {"stage": _build_stage(package)}, plan_ref, state=state)
    return store, registry, state, plan_ref, build_ref


def _build(tmp_path, store, registry, state, plan_ref, candidate="a" * 64):
    package = loop.consume_phase_handoff(store, plan_ref, registry=registry, phase_id="build",
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64,
        expected_run_id=state["run_id"], expected_candidate_fingerprint=candidate)
    candidates = loop.produce_spec_phase_candidates(store, registry.admit("build", ()).to_dict(),
        {"stage": _build_stage(package)}, package=package, state=state, workspace=str(tmp_path / "source"))
    return package, candidates


def _observed_evidence(store, package, source):
    contract = package.read("plan-task")["acceptance"]
    rows, executions = [], {}
    for obligation in contract["obligations"]:
        command = obligation["command"]
        if command not in executions:
            executions[command] = subprocess.run(command.split(), cwd=Path(__file__).resolve().parents[2],
                env={**os.environ, "TASKPLANE_J5_PROOF_SOURCE": str(source)}, capture_output=True, text=True, timeout=60)
        observed = executions[command]
        assert observed.returncode == 0, observed.stdout + observed.stderr
        reference = store.put("acceptance-proof", {"schema": "taskplane.acceptance-proof/v1",
            "obligation": obligation, "binding": contract["binding"], "returncode": observed.returncode,
            "evidence_mode": "simulated", "output": observed.stdout + observed.stderr})
        rows.append({"id": obligation["id"], "reference": reference})
    return {"schema": "taskplane.acceptance-evidence/v1", "binding": contract["binding"],
        "contributions": contract["contributions"], "proofs": rows}


def test_plan_package_reaches_build_with_multiple_tasks_proofs_and_joint_evidence(tmp_path, monkeypatch, record_property):
    store, registry, state, plan_ref, build_ref = _seed(tmp_path)
    package, candidates = _build(tmp_path, store, registry, state, plan_ref)
    assert candidates["realized-conformance"]["status"] == "conformant"
    planned = package.read("plan-task")
    assert {task["id"] for task in planned["plan"]["tasks"]} == {"left", "right"}
    assert all(row["tasks"] == ["left", "right"] for row in planned["traceability"]["criteria"].values())
    assert len(planned["acceptance"]["contributions"]) == 4
    assert len(planned["acceptance"]["obligations"]) == 6
    evidence = _observed_evidence(store, package, tmp_path / "source")
    accepted = loop.accept_phase_contributions(package, state, evidence, build_handoff=build_ref)
    assert accepted["status"] == "accepted"
    with pytest.raises(ValueError, match="proof|contribution|binding"):
        loop.accept_phase_contributions(package, state, {"tasks": [{"id": name, "status": "done"} for name in ("left", "right")]}, build_handoff=build_ref)
    # Simulate only current orchestrator stage/authority selection. The ordinary
    # aggregate DoD must itself find the actual retained Build -> Plan chain.
    built = _build_stage(package)
    aggregate_stage = stage_entities.create_stage(**{key: built[key] for key in (
        "run_id", "requirement", "design", "parent_stage_ids", "deliverables", "budget",
        "dependencies", "contracts", "authority", "created_at", "selected_artifacts")},
        stage_id="aggregate-j5", stage_kind="evaluate", predecessor_stage_ids=["stage-build"],
        input_manifest_ref=review_evidence.portable_artifact_reference(store, build_ref),
        execution_root_id="execution-aggregate-j5")
    monkeypatch.setattr(loop, "_phase_bridge_context", lambda ws, current: {
        "stage": aggregate_stage, "registry": registry, "artifacts": store, "run_id": package.run_id,
        "configuration": {"candidate_fingerprint": package.candidate_fingerprint}})
    verdict = {"criteria": [{"criterion": criterion, "status": "met", "evidence": json.dumps({
        "acceptance_evidence": accepted["evidence_reference"]})} for criterion in CRITERIA]}
    assert loop._acceptance_evidence_errors(str(tmp_path), state, {"criteria": CRITERIA}, verdict) == []
    prose_only = {"criteria": [{"criterion": criterion, "status": "met", "evidence": "Both tasks completed"}
        for criterion in CRITERIA]}
    assert loop._acceptance_evidence_errors(str(tmp_path), state, {"criteria": CRITERIA}, prose_only)
    record_property("evidence_mode", "local-production-with-simulated-host-authority-and-test-observations")
    record_property("plan_handoff", plan_ref["fingerprint"])


@pytest.mark.parametrize("case", ["missing_contribution", "missing_proof", "missing_joint", "candidate_change", "fixture_rewrite"])
def test_removed_contribution_proof_joint_binding_or_candidate_change_blocks_journey(tmp_path, record_property, case):
    store, registry, state, plan_ref, build_ref = _seed(tmp_path)
    package, _ = _build(tmp_path, store, registry, state, plan_ref)
    evidence = _observed_evidence(store, package, tmp_path / "source")
    assert loop.accept_phase_contributions(package, state, evidence, build_handoff=build_ref)["status"] == "accepted"
    contract = package.read("plan-task")["acceptance"]
    if case in {"missing_contribution", "missing_proof", "missing_joint"}:
        field = "contributions" if case == "missing_contribution" else "proofs"
        removed = [row for row in evidence[field] if field == "contributions" or
            next(item for item in contract["obligations"] if item["id"] == row["id"])["kind"] ==
                ("joint" if case == "missing_joint" else "task")]
        for row in removed:
            broken = copy.deepcopy(evidence)
            broken[field].remove(row)
            with pytest.raises(ValueError, match="contribution|proof"):
                loop.accept_phase_contributions(package, state, broken, build_handoff=build_ref)
            record_property("refused_removal", row)
        record_property("independent_removals", len(removed))
    elif case == "candidate_change":
        with pytest.raises(ValueError, match="candidate"):
            _build(tmp_path, store, registry, state, plan_ref, candidate="9" * 64)
    else:
        ref = next(row.reference for row in package.artifacts if row.artifact_class == "plan-task")
        path = Path(store.root) / ref["kind"] / (ref["fingerprint"] + ".json")
        original = path.read_bytes()
        path.write_bytes(original.replace(b'"left"', b'"fake"'))
        with pytest.raises(ValueError):
            _build(tmp_path, store, registry, state, plan_ref)
        path.write_bytes(original)
        # A newly stored observation cannot substitute a "similar" command for
        # the exact one Plan assigned, even with a valid artifact fingerprint.
        rewritten = copy.deepcopy(evidence)
        proof = store.read(rewritten["proofs"][0]["reference"])
        proof["obligation"]["command"] = proof["obligation"]["command"].replace("python3", "python", 1)
        rewritten["proofs"][0]["reference"] = store.put("acceptance-proof", proof)
        with pytest.raises(ValueError, match="proof"):
            loop.accept_phase_contributions(package, state, rewritten, build_handoff=build_ref)
    assert loop.accept_phase_contributions(package, state, evidence, build_handoff=build_ref)["status"] == "accepted"
    record_property("evidence_mode", "local-production-with-simulated-host-authority-and-test-observations")
