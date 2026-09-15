"""Regression tests for the live failure, using explicitly simulated host events."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskplane import loop, phase_harness, phase_records, review_evidence, stage_artifacts
from taskplane import tp as cli
from taskplane.tests.phase_fixture import (
    _supporting_pristine_phase_run, _emit_host_hook, _authored_requirement,
)


def product(tmp_path, monkeypatch):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    action = loop.next_action(ws)
    assert not action.get("error"), action
    _emit_host_hook(ws, action, "SubagentStart", monkeypatch)
    context = loop._phase_bridge_context(ws, loop.load(ws))
    operation = phase_harness.operation_id(context)
    material = review_evidence.ArtifactStore(ws).read(
        phase_records.phase_records(store.load(run_id))[operation]["result"]["reference"])
    _authored_requirement(ws, context["stage"])
    return ws, store, run_id, action, operation, material


def test_invalid_candidate_creates_no_specialist_leases(tmp_path, monkeypatch):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    path = Path(ws) / "specs/requirement.json"
    value = json.loads(path.read_text())
    value["context_files"] = ["README.md"]
    path.write_text(json.dumps(value))
    artifacts = review_evidence.ArtifactStore(ws)
    before = artifacts.references("lens-plan")
    with pytest.raises(ValueError, match="unknown fields"):
        phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    assert artifacts.references("lens-plan") == before
    inputs = phase_harness.read_input(loop, ws, action["stage_runtime_dispatch"])
    assert stage_artifacts.requirement_shape() in inputs["instruction"]


def test_current_lens_collection_delivers_complete_results(tmp_path, monkeypatch):
    from taskplane.tests.phase_fixture import write_lens_results
    ws, _, _, action, _, _ = product(tmp_path, monkeypatch)
    envelope = action["stage_runtime_dispatch"]
    artifacts = review_evidence.ArtifactStore(ws)
    prepared = phase_harness.collect_lenses(loop, ws, envelope, prepare=True)
    write_lens_results(artifacts, prepared["plan"])

    collected = phase_harness.collect_lenses(loop, ws, envelope)

    assert collected["status"] == "complete"
    assert collected["collection_content"] == artifacts.read(collected["collection"])
    assert collected["collection_content"]["results"]
    assert collected["lens_dispositions"] == artifacts.read(prepared["plan"])["decision"]
    assert len(collected["lens_dispositions"]) == 26
    # Delivering new review results must not widen the initial input reader.
    with pytest.raises(ValueError, match="not selected"):
        phase_harness.read_artifact(loop, ws, {
            "stage_runtime_dispatch": envelope, "references": [collected["collection"]]})


def test_stopped_invalid_candidate_requires_a_fresh_authorized_attempt(tmp_path, monkeypatch):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    path = Path(ws) / "specs/requirement.json"
    value = json.loads(path.read_text())
    path.write_text(json.dumps({**value, "problem": "invalid closed-schema field"}))
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch, omit_lens_results=True)
    source = loop.design_host_transport.phase_nonce_source(loop.tp, ws, run_id, existing_only=True)
    issued = source.recover(material["nonce_bindings"])
    old_hooks = source.phase_hooks(issued, material["nonce_bindings"])
    assert old_hooks[1]["outputs"][0]["sha256"]
    observed_dispatch = next(row for row in loop.load(ws)["dispatch_telemetry"]["bindings"]
        if row["dispatch_id"] == material["bindings"]["attempt_id"])
    assert observed_dispatch["ended_at"] == old_hooks[1]["observed_at"]
    path.write_text(json.dumps(value))
    rejected = loop.resolve(ws, "reconcile", phase_operation=operation)
    assert "differ from their authenticated Stop" in rejected["error"]
    before = store.load(run_id)
    refused = loop.resolve(ws, "retry", phase_operation=operation,
        candidate_fingerprint="a" * 64, worker_stopped=False, by="human:simulated")
    assert refused.get("error") and store.load(run_id) == before
    result = loop.resolve(ws, "retry", phase_operation=operation,
        candidate_fingerprint="a" * 64, worker_stopped=True, by="human:simulated")
    assert not result.get("error"), result
    assert source.phase_hooks(issued, material["nonce_bindings"]) == old_hooks
    assert not Path(loop.tp.active_contract_path(ws, material["contract_slot"])).exists()
    next_action = loop.next_action(ws)
    assert not next_action.get("error"), next_action
    assert next_action["obligations"]["task_name"] != action["obligations"]["task_name"]
    fresh_context = loop._phase_bridge_context(ws, loop.load(ws))
    fresh = review_evidence.ArtifactStore(ws).read(phase_records.phase_records(store.load(run_id))[
        phase_harness.operation_id(fresh_context)]["result"]["reference"])
    assert fresh["bindings"]["budget"] == material["bindings"]["budget"]
    assert fresh["bindings"]["nonce_digest"] != material["bindings"]["nonce_digest"]


def test_terminal_interruption_retires_its_worker_immediately(tmp_path, monkeypatch):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    path = Path(ws) / "specs/requirement.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "problem": "invalid draft"}))
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch, omit_lens_results=True)
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker
    unrelated = _active_worker(Path(ws), stage="plan", task="other", name="tp_other_plan")
    other_path = Path(loop.tp.active_contract_path(ws, unrelated["task_slot"]))
    before = other_path.read_bytes()
    result = loop.terminalize_run(ws, "interruption", by="human:simulated")
    assert not result.get("error"), result
    assert result["terminal_cleanup"]["cleanup_status"] == "clean"
    assert not Path(loop.tp.active_contract_path(ws, material["contract_slot"])).exists()
    assert other_path.read_bytes() == before
    assert result["worker_releases"][0]["outcome"] == "interruption"
    again = loop.terminalize_run(ws, "interruption", by="human:simulated")
    assert again["fingerprint"] == result["fingerprint"]


def test_terminal_cleanup_cannot_release_a_worker_without_its_stop(tmp_path, monkeypatch):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    path = Path(loop.tp.active_contract_path(ws, material["contract_slot"]))
    before = path.read_bytes()
    result = loop.terminalize_run(ws, "interruption", by="human:simulated")
    assert result.get("error"), result
    assert path.read_bytes() == before
    assert not loop.load(ws).get("terminal_cleanup")


@pytest.mark.parametrize("expired", [False, True])
def test_interruption_revokes_unsubmitted_review_permissions_immediately(tmp_path, monkeypatch, expired):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    prepared = phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    activations = []
    for slot in prepared["dispatch"]:
        boot = slot["contract_bootstrap"]
        signed = boot["action"]
        if expired:
            signed = loop.tp.issue_review_contract_action(ws,
                **{key: signed[key] for key in ["run_id", "task_id", "role_marker",
                    "worker_identity", "action_id", "producer_contract", "result_path"]},
                lease=signed["lease_identity"], now=1, ttl_seconds=30)
        with monkeypatch.context() as worker:
            worker.setenv("TASKPLANE_TASK", boot["task_slot"])
            contract = loop.tp.activate_review_contract_action(ws, signed, **boot["expected"],
                **({"now": 2} if expired else {}))
        activations.append((boot, signed, Path(loop.tp.active_contract_path(ws, contract["task_slot"]))))
    assert all(path.exists() for _, _, path in activations)
    sample = activations[0][1]
    foreign_slot = "review-other-run"
    foreign_result = str(Path(sample["result_path"]).with_name("f" * 64 + ".json"))
    foreign = loop.tp.issue_review_contract_action(ws, run_id="another-run", task_id="other",
        role_marker="taskplane-role:tp-lens", worker_identity="tp_lens_other",
        action_id="another-review-action", lease={**sample["lease_identity"], "lease_fingerprint": "f" * 64},
        producer_contract={"task": "unrelated review", "task_slot": foreign_slot,
                           "read_only": True, "write_allow": [foreign_result]}, result_path=foreign_result)
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", foreign_slot)
        loop.tp.activate_review_contract_action(ws, foreign, run_id="another-run", task_id="other",
            role_marker=foreign["role_marker"], worker_identity=foreign["worker_identity"],
            action_id=foreign["action_id"], lens_ids=foreign["lease_identity"]["lens_ids"],
            target_fingerprint=foreign["lease_identity"]["target_fingerprint"],
            lease_fingerprint="f" * 64, canonical_revision=foreign["lease_identity"]["canonical_revision"])
    foreign_path = Path(loop.tp.active_contract_path(ws, foreign_slot))
    foreign_bytes = foreign_path.read_bytes()
    # No reviewer result or fabricated native lifecycle is needed to revoke
    # an unlaunched read-only capability on authorized whole-run interruption.
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch, omit_lens_results=True)
    result = loop.terminalize_run(ws, "interruption", by="human:simulated")
    assert not result.get("error"), result
    assert all(not path.exists() for _, _, path in activations)
    assert foreign_path.read_bytes() == foreign_bytes
    revocations = [row for row in result["worker_releases"]
                   if row.get("schema") == "taskplane.review-worker-revocation/v1"]
    assert len(revocations) == len(activations)
    assert all(row["native_completion_claimed"] is False for row in revocations)
    assert result["terminal_cleanup"]["cleanup_status"] == "clean"
    boot, signed, path = activations[0]
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", boot["task_slot"])
        with pytest.raises(loop.tp.StateError, match="closing run"):
            loop.tp.activate_review_contract_action(ws, signed, **boot["expected"],
                **({"now": 2} if expired else {}))
    assert not path.exists()
    assert loop.terminalize_run(ws, "interruption", by="human:simulated")["fingerprint"] == result["fingerprint"]


def test_interruption_refuses_tampered_review_authority(tmp_path, monkeypatch):
    ws, _, _, action, _, _ = product(tmp_path, monkeypatch)
    prepared = phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    boot = prepared["dispatch"][0]["contract_bootstrap"]
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", boot["task_slot"])
        contract = loop.tp.activate_review_contract_action(ws, boot["action"], **boot["expected"])
    contract["bootstrap_action"]["signature"] = "0" * 64
    path = Path(loop.tp.active_contract_path(ws, boot["task_slot"]))
    loop.tp.atomic_write_json(str(path), contract)
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch, omit_lens_results=True)
    result = loop.terminalize_run(ws, "interruption", by="human:simulated")
    assert "signature is invalid" in result["error"]
    assert path.exists()
    assert not loop.load(ws).get("terminal_cleanup")


def test_lens_dispatch_has_exact_idempotent_native_expectations(tmp_path, monkeypatch, capsys):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    result = phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    artifacts = review_evidence.ArtifactStore(ws)
    monkeypatch.setenv("TASKPLANE_ENFORCE_DISPATCH", "strict")
    for slot in result["dispatch"]:
        role = artifacts.read(slot["brief"])["role"]
        boot = slot["contract_bootstrap"]
        with monkeypatch.context() as worker:
            worker.setenv("TASKPLANE_TASK", boot["task_slot"])
            loop.tp.activate_review_contract_action(ws, boot["action"], **boot["expected"])
        expected = loop.tp.peek_expectation(ws, role["task_name"])
        assert expected["ref"] == artifacts.read(slot["lease"])["lease_fingerprint"]
        event = {"cwd": ws, "tool_name": "spawn_agent", "tool_input": {
            "task_name": role["task_name"], "reasoning_effort": role["reasoning_effort"],
            "fork_turns": "none", "message": "host-protected-prompt"}}
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert cli.cmd_screen_dispatch(SimpleNamespace()) == 0
        output = capsys.readouterr().out
        assert "deny" not in output, output
        assert loop.tp.peek_expectation(ws, role["task_name"]) is None
    phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    assert all(loop.tp.peek_expectation(ws, artifacts.read(slot["brief"])["role"]["task_name"])
        is None for slot in result["dispatch"])


@pytest.mark.parametrize("damage", ["missing-action", "foreign-worker", "wider-output", "bad-signature"])
def test_protected_lens_role_requires_exact_signed_activation(tmp_path, monkeypatch, damage):
    ws, store, run_id, action, operation, material = product(tmp_path, monkeypatch)
    result = phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    slot = result["dispatch"][0]
    boot = slot["contract_bootstrap"]
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", boot["task_slot"])
        contract = loop.tp.activate_review_contract_action(ws, boot["action"], **boot["expected"])
    name = boot["expected"]["worker_identity"]
    expected = loop.tp.peek_expectation(ws, name)
    assert loop.tp.native_worker_role_matches(ws, expected, name)
    if damage == "missing-action": contract.pop("bootstrap_action")
    if damage == "foreign-worker": contract["bootstrap_worker_identity"] = "foreign"
    if damage == "wider-output": contract["write_allow"] = ["**"]
    if damage == "bad-signature": contract["bootstrap_action"]["signature"] = "0" * 64
    loop.tp.atomic_write_json(loop.tp.active_contract_path(ws, boot["task_slot"]), contract)
    if damage == "bad-signature":
        with pytest.raises(loop.tp.StateError, match="signature is invalid"):
            loop.tp.native_worker_role_matches(ws, expected, name)
    else:
        assert not loop.tp.native_worker_role_matches(ws, expected, name)
