"""T15C boundary corrections and the retained production-entry blocker.

Host authorship and current authority are simulated in an isolated workspace.
These checks do not claim cutover, rollback or native success. The real T11
package crosses lifecycle and startup readers; the supported native entry
remains blocked on its missing runtime bridge.
"""
import pytest

from taskplane import loop, review_evidence, run_store, stage_entities, stage_handoff, stage_migration, storage
from taskplane.tests.test_r0001_phase_agents_spec import _journey
from taskplane.tests.test_r0001_native_entry import _request, _snapshot
from taskplane import design_host_transport
from taskplane import taskplane_lite


def test_atomic_phase_cutover(tmp_path, record_property):
    artifacts, _, _, design_ref, plan_ref, _ = _journey(tmp_path)
    package = stage_handoff.read_v2_manifest(artifacts, plan_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
    identity = storage.identity_from_remote("https://github.com/vdemkiv/taskplane.git")
    store = run_store.RunStore(home=str(tmp_path / "managed"))
    initial = store.create(identity, run_id="run-t11", checkout=str(tmp_path),
        host={"kind": "simulated", "session_id": "simulated-session"},
        target={"kind": "workspace"})
    authority = {
        "schema": "taskplane.stage-authority-binding/v1", "run_id": "run-t11",
        "repository_id": "github.com/vdemkiv/taskplane",
        "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "simulated-worktree", "target_revision": "1" * 40,
        "worktree_revision": "1" * 40, "requirement_id": package["requirement"]["id"],
        "requirement_revision": package["requirement"]["revision"],
        "design_revision": package["design"]["revision"],
        "design_fingerprint": package["design"]["fingerprint"],
        "actor": "human:simulated", "session_id": "simulated-session",
        "authority_revision": 1, "authority_fingerprint": "f" * 64,
    }

    def check_authority(expected, current):
        assert expected == current == authority

    lifecycle = stage_entities.StageLifecycle(store, artifact_store=artifacts,
        authority_resolver=lambda manifest: authority,
        authority_validator=check_authority)

    def stage(phase, reference, predecessors):
        return stage_entities.create_stage(run_id="run-t11", stage_id="stage-" + phase,
            requirement=package["requirement"], design=package["design"], stage_kind=phase,
            parent_stage_ids=[], predecessor_stage_ids=predecessors,
            input_manifest_ref=review_evidence.portable_artifact_reference(artifacts, reference),
            execution_root_id="execution-stage-" + phase, deliverables=["plan-task"],
            selected_artifacts=package["selected_artifacts"], budget={"tokens": 1000},
            dependencies=[], contracts=[], authority=authority,
            created_at="2026-09-06T00:00:00Z")

    predecessor = stage("plan", design_ref, [])
    lifecycle.start_stage(predecessor, expected_revision=initial["revision"],
        operation_id="start-simulated-plan")
    before = store.load("run-t11")
    record_property("evidence_mode", "blocking-regression-simulated-host-and-authority")
    record_property("producer_reference", plan_ref["fingerprint"])
    record_property("producer_edge", "produce_phase_handoff -> StageLifecycle.terminalize_and_start")
    loop._preflight_stage_dispatch(stage("build", plan_ref, ["stage-plan"]), package)
    try:
        receipt = lifecycle.terminalize_and_start("stage-plan", stage("build", plan_ref, ["stage-plan"]),
            expected_head_fingerprint=predecessor["fingerprint"], expected_revision=before["revision"],
            operation_id="consume-actual-v2-package", outcome="done", actor=authority["actor"],
            terminalized_at="2026-09-06T00:01:00Z", completed_deliverables=["plan-task"],
            completion_evidence=package["evidence_references"])
    except Exception:
        assert store.load("run-t11") == before
        assert stage_handoff.read_v2_manifest(artifacts, plan_ref,
            expected_authority_revision=1, expected_authority_fingerprint="f" * 64) == package
        raise
    assert receipt["operation"] == "terminalize_and_start"
    successor = stage("build", plan_ref, ["stage-plan"])
    dispatch = taskplane_lite.stage_runtime_dispatch(successor, receipt, package,
        successor["selected_artifacts"])
    startup = taskplane_lite.stage_startup_bytes(dispatch)
    projected = dispatch["startup"]["input_handoff"]
    assert projected["schema"] == taskplane_lite.STAGE_HANDOFF_V2_DISPATCH_SCHEMA
    assert projected["source_fingerprint"] == package["fingerprint"]
    assert projected["phase_result"] == package["phase_result"]
    assert dispatch["telemetry"]["manifest_bytes"] == plan_ref["bytes"]
    assert dispatch["telemetry"]["predecessor_root_opens"] == 0
    assert b"human:simulated" not in startup
    assert b"simulated-session" not in startup
    assert stage_handoff.read_manifest(artifacts, plan_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64) == package


@pytest.mark.parametrize("case", ["v2-current", "stale-authority", "retained-read"])
def test_mixed_version_matrix(tmp_path, case):
    artifacts, _, _, _, reference, _ = _journey(tmp_path)
    assert stage_migration.active_producer_schema("taskplane.stage-handoff") == stage_handoff.SCHEMA
    if case == "stale-authority":
        with pytest.raises(stage_handoff.StaleAuthorityError):
            stage_handoff.read_manifest(artifacts, reference,
                expected_authority_revision=2, expected_authority_fingerprint="f" * 64)
    elif case == "retained-read":
        original = artifacts.read(reference)
        retained = stage_migration.read_compatible_contract(review_evidence.canonical_bytes(original), store=artifacts)
        assert retained.payload == original
        assert retained.progression_authority is False
    else:
        original = stage_handoff.read_manifest(artifacts, reference,
            expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
        assert stage_handoff.store_manifest(artifacts, original) == reference


def test_native_entry_diagnostic_refuses_without_real_capability(tmp_path, record_property):
    """Diagnostic refusal only; this cannot earn supported-entry/native proof."""
    request = _request(tmp_path)
    prepared = design_host_transport.prepare_native_entry(request, _snapshot(request, stable=True))
    observation = prepared.observation
    record_property("evidence_mode", "simulated-capability-input-production-native-preflight")
    record_property("missing_capabilities", str(observation["missing_capabilities"]))
    assert observation["native_identity_claimed"] is False
    assert observation["effect_state"] == "none"
    assert observation["ready"] is False
    assert observation["success"] is False
    assert observation["action"] == "refusal"
    assert observation["evidence_mode"] == "degraded_observation"
    assert "real_canary" in observation["missing_capabilities"]
