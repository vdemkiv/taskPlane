"""Real recovery producers in isolated stores; never native acceptance evidence."""

from taskplane import phase_records
from datetime import datetime
from pathlib import Path

import pytest

from taskplane import loop, review_evidence
from taskplane.tests.phase_fixture import _supporting_pristine_phase_run, phase_pending


@pytest.fixture
def expired(tmp_path, monkeypatch):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    first = loop.next_action(ws)
    assert not first.get("error"), first
    assert set(first) == {"schema", "stage_runtime_dispatch", "obligations"}
    context = loop._phase_bridge_context(ws, loop.load(ws))
    assert context["stage"]["requirement"]["id"] == requirement["id"]
    material = review_evidence.ArtifactStore(ws).read(phase_pending(ws)["reference"])
    deadline = datetime.fromisoformat(material["bindings"]["deadline"]).timestamp()
    monkeypatch.setattr(loop.time, "time", lambda: deadline + 1)
    args = dict(by="human:simulated", phase_operation=phase_pending(ws)["operation_id"],
                candidate_fingerprint="a" * 64, worker_stopped=True)
    return ws, store, run_id, first, material, args


def test_retry_preserves_run_scope_and_old_proof_with_one_new_attempt(expired):
    ws, store, run_id, first, material, args = expired
    original = loop.resume(ws)
    manifest = store.load(run_id)
    old_preparation = phase_records.phase_records(manifest)[args["phase_operation"]]
    slot_path = Path(loop.tp.active_contract_path(ws, material["contract_slot"]))
    old_contract = loop.tp.load_json(str(slot_path))
    resolved = loop.resolve(ws, "retry", **args)
    assert not resolved.get("error"), resolved
    assert not slot_path.exists()
    grant = resolved["phase_retry"]["result"]
    assert review_evidence.ArtifactStore(ws).read(grant["contract_reference"]) == old_contract
    assert phase_records.phase_records(store.load(run_id))[args["phase_operation"]] == old_preparation
    assert grant["old_result"] == "uncollected-not-passed"
    after_grant = store.load(run_id)
    assert loop.resolve(ws, "retry", **args)["replay"] is True
    assert store.load(run_id) == after_grant
    second = loop.next_action(ws)
    assert not second.get("error"), second
    assert second["obligations"]["task_name"] != first["obligations"]["task_name"]
    assert phase_pending(ws)["operation_id"] == grant["next_operation"]
    new_material = review_evidence.ArtifactStore(ws).read(phase_pending(ws)["reference"])
    assert new_material["bindings"]["nonce_digest"] != material["bindings"]["nonce_digest"]
    assert new_material["bindings"]["candidate_fingerprint"] == "a" * 64
    assert new_material["stage_fingerprint"] == material["stage_fingerprint"]
    assert new_material["bindings"]["budget"] == material["bindings"]["budget"]
    assert new_material["contract_slot"] != material["contract_slot"]
    assert store.load(run_id)["stage_heads"] == manifest["stage_heads"]
    resumes = [row for row in store.load(run_id)["stage_operations"].values()
               if row["operation"] == "resume_stage"]
    assert len({row["result"]["attempt_id"] for row in resumes}) == 2
    for key in ("run_id", "goal", "requirement_id", "tasks"):
        assert loop.resume(ws)[key] == original[key]
    after_dispatch = store.load(run_id)
    assert phase_pending(ws)["operation_id"] == grant["next_operation"]
    assert loop.resolve(ws, "retry", **args)["replay"] is True
    assert store.load(run_id) == after_dispatch


@pytest.mark.parametrize("case", ["missing-human", "foreign-human", "missing-stop", "wrong-operation",
                                  "wrong-candidate", "unexpired", "active-worker", "worker-caller"])
def test_retry_refuses_without_exact_recovery_authority(expired, monkeypatch, case):
    ws, store, run_id, first, material, args = expired
    if case == "missing-human": args["by"] = None
    if case == "foreign-human": args["by"] = "human:foreign"
    if case == "missing-stop": args["worker_stopped"] = False
    if case == "wrong-operation": args["phase_operation"] = "phase-attempt-foreign"
    if case == "wrong-candidate": args["candidate_fingerprint"] = "not-a-fingerprint"
    if case == "unexpired": monkeypatch.setattr(loop.time, "time", lambda: 1)
    if case == "worker-caller": monkeypatch.setattr(loop.tp, "task_slot", lambda: "child")
    if case == "active-worker":
        loop.tp.bind_worker_contract_event(ws, {"session_id": "simulated", "agent_id": "simulated-child",
            "agent_type": first["obligations"]["task_name"], "task_name": first["obligations"]["task_name"]})
    before = store.load(run_id)
    contract_path = Path(loop.tp.active_contract_path(ws, material["contract_slot"]))
    contract_bytes = contract_path.read_bytes()
    result = loop.resolve(ws, "retry", **args)
    assert result.get("error"), result
    assert store.load(run_id) == before
    assert contract_path.read_bytes() == contract_bytes


def test_retry_crash_before_release_cannot_dispatch_and_replay_completes(expired, monkeypatch):
    ws, store, run_id, first, material, args = expired
    with monkeypatch.context() as patch:
        patch.setattr(loop, "_phase_retry_release", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
        assert loop.resolve(ws, "retry", **args).get("error")
    grant_state = store.load(run_id)
    assert "cleanup incomplete" in loop.next_action(ws)["obligations"]["error"]
    assert store.load(run_id) == grant_state
    replay = loop.resolve(ws, "retry", **args)
    assert replay.get("replay") is True, replay
    second = loop.next_action(ws)
    assert not second.get("error"), second
    assert second["obligations"]["task_name"] != first["obligations"]["task_name"]
    changed = dict(args, candidate_fingerprint="b" * 64)
    assert "replay changed" in loop.resolve(ws, "retry", **changed)["error"]


def test_retries_stop_at_the_existing_stage_attempt_limit(expired, monkeypatch):
    ws, store, run_id, first, material, args = expired
    context = loop._phase_bridge_context(ws, loop.load(ws))
    limit = context["stage"]["budget"]["attempt_limit"]
    for _ in range(limit - 1):
        assert loop.resolve(ws, "retry", **args).get("resolved") == "retry"
        following = loop.next_action(ws)
        assert following["obligations"]["dispatch_allowed"], following
        material = review_evidence.ArtifactStore(ws).read(phase_pending(ws)["reference"])
        deadline = datetime.fromisoformat(material["bindings"]["deadline"]).timestamp()
        monkeypatch.setattr(loop.time, "time", lambda: deadline + 1)
        args = dict(args, phase_operation=phase_pending(ws)["operation_id"])
    before = store.load(run_id)
    result = loop.resolve(ws, "retry", **args)
    assert "attempt limit exhausted" in result.get("error", ""), result
    assert store.load(run_id) == before
