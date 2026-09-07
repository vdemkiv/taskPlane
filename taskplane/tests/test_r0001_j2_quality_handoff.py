"""J2 actual package producers, with explicitly simulated host/authority inputs.

No predecessor runtime is available to Build. Substitution is refused by the
same immutable package reader; this does not establish J1/J6 native execution.
"""
from pathlib import Path

import pytest

from taskplane import build_quality, loop, review_evidence, stage_entities, stage_handoff
from taskplane.tests.test_r0001_phase_agents_spec import _journey, _run, _consume


SELECTORS = ["taskplane/tests/test_r0001_j2_quality_handoff.py::" + name for name in (
    "test_fresh_design_package_reaches_plan_and_build_through_public_boundary",
    "test_severed_design_artifact_fails_the_same_plan_build_connection")]


def _build_stage(package):
    previous = package.manifest()
    authority = {"schema": "taskplane.stage-authority-binding/v1", "run_id": package.run_id,
        "repository_id": "github.com/vdemkiv/taskplane", "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "simulated-worktree", "target_revision": "1" * 40, "worktree_revision": "1" * 40,
        "requirement_id": "R-T11", "requirement_revision": "1", "design_revision": "1",
        "design_fingerprint": previous["design"]["fingerprint"], "actor": "human:simulated",
        "session_id": "simulated-session", "authority_revision": 1, "authority_fingerprint": "f" * 64}
    return stage_entities.create_stage(run_id=package.run_id, stage_id="stage-build",
        requirement=previous["requirement"], design=previous["design"], stage_kind="build",
        parent_stage_ids=[], predecessor_stage_ids=[previous["producer"]["stage_id"]],
        input_manifest_ref=review_evidence.portable_artifact_reference(package.store, package.reference),
        execution_root_id="execution-stage-build", deliverables=["stage"],
        selected_artifacts=previous["selected_artifacts"], budget={"tokens": 1000},
        dependencies=[], contracts=[], authority=authority, created_at="2026-09-06T00:00:00Z")


def _connection(tmp_path):
    store, registry, state, design_ref, plan_ref, design_result = _journey(tmp_path, seam_selectors=SELECTORS)
    package, authority = _consume(store, registry, state, plan_ref)
    build_ref, build_result = _run(tmp_path, store, registry, "build",
        {"stage": _build_stage(package)}, plan_ref, state=state)
    return store, registry, state, design_ref, plan_ref, build_ref, design_result, build_result, authority


def _fresh_build_inputs(store, registry, state, plan_ref, source):
    package, authority = _consume(store, registry, state, plan_ref)
    quality = build_quality.begin_receipt(package.read("test-strategy"),
        binding={"candidate": {"id": "T11", "fingerprint": package.candidate_fingerprint},
            "run_id": package.run_id, "stage_instance": "build-j2", "settings_digest": "b" * 64,
            "runtime_digest": "c" * 64, "environment_digest": "d" * 64},
        criterion_ids=authority["selection"]["criterion_ids"],
        changed_producer_ids=authority["selection"]["changed_producer_ids"], changed_paths=["taskplane/loop.py"])
    candidates = loop.produce_spec_phase_candidates(store, registry.admit("build", ()).to_dict(),
        {"stage": _build_stage(package)}, package=package, state=state, workspace=str(source))
    return package, quality, candidates


def test_fresh_design_package_reaches_plan_and_build_through_public_boundary(tmp_path, record_property):
    store, registry, state, design_ref, plan_ref, build_ref, design_result, build_result, authority = _connection(tmp_path)
    package, quality, candidates = _fresh_build_inputs(store, registry, state, plan_ref, tmp_path / "source")
    design = stage_handoff.read_v2_manifest(store, design_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
    built = stage_handoff.read_v2_manifest(store, build_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
    strategy_ref = next(row["reference"] for row in design["produced_artifacts"] if row["artifact_class"] == "test-strategy")
    assert package.read("test-strategy") == store.read(strategy_ref)
    assert quality["selectors"] == authority["selection"]["selectors"]
    assert candidates["realized-conformance"]["status"] == "conformant"
    assert design["phase_result"] == design_result
    assert built["phase_result"] == build_result
    assert build_result["status"] == "accepted"
    assert {row["artifact_class"] for row in built["produced_artifacts"]} == {"stage", "realized-conformance"}
    assert any(row["reference"] == strategy_ref for row in built["inherited_artifacts"])
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
    record_property("design_handoff", design_ref["fingerprint"])
    record_property("build_handoff", build_ref["fingerprint"])


@pytest.mark.parametrize("case", ["removed_strategy", "altered_output", "missing_quality_authority", "simulated_substitution"])
def test_severed_design_artifact_fails_the_same_plan_build_connection(tmp_path, record_property, case):
    store, registry, state, _, plan_ref, _, _, _, _ = _connection(tmp_path)
    package, quality, positive = _fresh_build_inputs(store, registry, state, plan_ref, tmp_path / "source")
    baseline = package.manifest()
    artifact_class = {"removed_strategy": "test-strategy", "altered_output": "design",
        "missing_quality_authority": "plan-task", "simulated_substitution": "stage-handoff"}[case]
    ref = plan_ref if case == "simulated_substitution" else next(row.reference for row in package.artifacts if row.artifact_class == artifact_class)
    path = Path(store.root) / ref["kind"] / (ref["fingerprint"] + ".json")
    original = path.read_bytes()
    try:
        if case == "simulated_substitution":
            # Attempt to relabel an actual simulated producer as a native host.
            # The original handoff remains the authoritative reference.
            assert b"simulated:local-test" in original
            path.write_bytes(original.replace(b"simulated:local-test", b"codex:native"))
        elif case == "altered_output":
            path.write_bytes(original + b" ")
        else:
            path.unlink()
        with pytest.raises((ValueError, OSError)):
            _fresh_build_inputs(store, registry, state, plan_ref, tmp_path / "source")
    finally:
        path.write_bytes(original)
    restored, restored_quality, restored_candidates = _fresh_build_inputs(store, registry, state, plan_ref, tmp_path / "source")
    assert restored.manifest() == baseline
    assert restored_quality == quality
    assert restored_candidates["stage"] == positive["stage"]
    for field in ("status", "binding", "manifest_fingerprint", "missing", "unexpected"):
        assert restored_candidates["realized-conformance"][field] == positive["realized-conformance"][field]
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
    record_property("severed_artifact", artifact_class)
