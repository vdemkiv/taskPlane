"""J7 uses production packages; host observations and authority are simulated."""
import copy

import pytest

from taskplane import loop, review_evidence, stage_entities
from taskplane.tests.test_r0001_phase_agents_spec import _journey, _consume

SELECTORS = ["taskplane/tests/test_r0001_j7_decomposition_pipeline.py::" + name for name in (
    "test_source_coverage_drives_decomposition_seam_manifest_build_and_conformance_end_to_end",
    "test_one_severed_seam_fails_conformance_through_the_same_pipeline")]


def _build(package, workspace):
    previous = package.manifest()
    authority = {"schema": "taskplane.stage-authority-binding/v1", "run_id": "run-t11",
        "repository_id": "github.com/vdemkiv/taskplane", "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "simulated-worktree", "target_revision": "1" * 40, "worktree_revision": "1" * 40,
        "requirement_id": "R-T11", "requirement_revision": "1", "design_revision": "1",
        "design_fingerprint": previous["design"]["fingerprint"], "actor": "human:simulated", "session_id": "simulated-session",
        "authority_revision": 1, "authority_fingerprint": "f" * 64}
    stage = stage_entities.create_stage(run_id="run-t11", stage_id="stage-build",
        requirement=previous["requirement"], design=previous["design"], stage_kind="build",
        parent_stage_ids=[], predecessor_stage_ids=[previous["producer"]["stage_id"]],
        input_manifest_ref=review_evidence.portable_artifact_reference(package.store, package.reference),
        execution_root_id="execution-stage-build", deliverables=["stage"],
        selected_artifacts=previous["selected_artifacts"], budget={"tokens": 1000},
        dependencies=[], contracts=[], authority=authority, created_at="2026-09-06T00:00:00Z")
    definition = package.registry.admit("build", ()).to_dict()
    candidates = loop.produce_spec_phase_candidates(package.store, definition, {"stage": stage},
        package=package, state={}, workspace=str(workspace))
    outputs = loop.store_spec_phase_outputs(package.store, definition, candidates)
    return package.store.read(next(row.reference for row in outputs if row.artifact_class == "realized-conformance"))


def _pipeline(tmp_path):
    store, registry, state, _, plan_ref, _ = _journey(tmp_path, seam_selectors=SELECTORS)
    package, _ = _consume(store, registry, state, plan_ref)
    planned = package.read("seam-manifest")
    result = _build(package, tmp_path / "source")
    return package, planned, result


def test_source_coverage_drives_decomposition_seam_manifest_build_and_conformance_end_to_end(tmp_path, record_property):
    package, manifest, result = _pipeline(tmp_path)
    assert package.read("source-coverage")["complete"] is True
    decomposition = package.read("decomposition")
    assert len(decomposition["tasks"]) == 2
    assert len(manifest["seams"]) == 1
    assert [manifest["seams"][0][key] for key in ("positive", "severed")] == SELECTORS
    assert manifest["seams"][0]["producer_owner"] != manifest["seams"][0]["consumer_owner"]
    assert result["status"] == "conformant"
    assert result["manifest_fingerprint"] == manifest["fingerprint"]
    assert result["binding"] == manifest["binding"]
    assert result["missing"] == result["unexpected"] == []
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


def test_one_severed_seam_fails_conformance_through_the_same_pipeline(tmp_path, record_property):
    package, manifest, positive = _pipeline(tmp_path)
    baseline = copy.deepcopy(package.manifest())
    source = tmp_path / "source" / "consumer" / "use.py"
    original = source.read_bytes()
    source.write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="realized seam conformance.*missing.*provider.*consumer"):
        _build(package, tmp_path / "source")
    assert package.manifest() == baseline
    source.write_bytes(original)
    restored = _build(package, tmp_path / "source")
    assert restored["binding"] == positive["binding"] == manifest["binding"]
    record_property("severed_edge", "provider->consumer:imports")
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
