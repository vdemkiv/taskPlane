"""T15C blocking regression: real T11 packages reach the incumbent lifecycle.

Host authorship and current authority are simulated in an isolated workspace.
This diagnostic does not claim cutover, live-entry wiring, rollback or native
success. The expected v2 transaction is red until its reader owner is connected.
"""
from taskplane import review_evidence, run_store, stage_entities, stage_handoff, storage
from taskplane.tests.test_r0001_phase_agents_spec import _journey


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
