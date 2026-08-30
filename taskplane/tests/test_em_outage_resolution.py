import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import em_outage  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git_ws(tmp_path: Path) -> str:
    ws = tmp_path / "ws"
    (ws / "plan").mkdir(parents=True)
    (ws / "src").mkdir()
    (ws / "src" / "value.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws,
                   check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws,
                   check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
    return str(ws)


def _outputs(tmp_path: Path) -> tuple[str, str]:
    findings = tmp_path / "findings.json"
    report = tmp_path / "report.md"
    findings.write_text('{"meta":{"gate":{"verdict":"pass"}},"findings":[]}')
    report.write_text("# Engineering review\n\nGreen.\n")
    return str(findings), str(report)


def _identity(tmp_path: Path, **updates) -> dict:
    findings, report = _outputs(tmp_path)
    values = {
        "repository": {"repo_id": "repo-1", "repository_key": "key-1"},
        "store": str(tmp_path / "store"),
        "worktree": str(tmp_path / "worktree"),
        "run_id": "review-run-1",
        "slot": "task_em_1",
        "expected_worker": "tp_step_engineering_1",
        "output_contract_fingerprint": "a" * 64,
        "producer_dispatch_fingerprint": "b" * 64,
        "integration_revision": "c" * 40,
        "outputs": em_outage.output_hashes(findings, report),
        "review_kernel": {"run_id": "review-run-1", "slots": []},
    }
    values.update(updates)
    return em_outage.outage_identity(**values)


def _guarded_task(status="pending") -> dict:
    return {
        "id": "REL-2181", "status": status, "deps": [],
        "scope": ["src/**"], "tests": "true", "criteria": ["green"],
        "staged_dispatch_guard": {
            "schema": "taskplane.staged-task-dispatch-guard/v1",
            "reason": "mandatory_replan_required",
            "task": "REL-2181", "required_predecessor": "EM-OUTAGE",
        },
    }


def test_identity_is_deterministic_and_every_bound_input_invalidates(tmp_path):
    first = _identity(tmp_path)
    assert _identity(tmp_path) == first

    mutations = {
        "repository": {"repo_id": "repo-2"},
        "store": str(tmp_path / "other-store"),
        "worktree": str(tmp_path / "other-worktree"),
        "run_id": "review-run-2",
        "slot": "task_em_2",
        "expected_worker": "tp_step_engineering_2",
        "output_contract_fingerprint": "d" * 64,
        "producer_dispatch_fingerprint": "e" * 64,
        "integration_revision": "f" * 40,
        "review_kernel": {"run_id": "review-run-1", "slots": [],
                          "revision": 2},
    }
    for field, changed in mutations.items():
        assert _identity(tmp_path, **{field: changed})["fingerprint"] != \
            first["fingerprint"], field

    tampered = dict(first, integration_revision="0" * 40)
    with pytest.raises(em_outage.EmOutageError, match="fingerprint mismatch"):
        em_outage.validate_outage_identity(tampered)


def test_exact_output_hashing_rejects_symlinks_and_empty_files(tmp_path):
    findings, report = _outputs(tmp_path)
    link = tmp_path / "findings-link.json"
    link.symlink_to(findings)
    with pytest.raises(em_outage.EmOutageError, match="regular file"):
        em_outage.output_hashes(str(link), report)
    Path(report).write_bytes(b"")
    with pytest.raises(em_outage.EmOutageError, match="non-empty"):
        em_outage.output_hashes(findings, report)


def test_public_output_hashes_rejects_swap_after_path_attestation(
        tmp_path, monkeypatch):
    findings, report = _outputs(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"meta":{"gate":{"verdict":"fail"}}}')
    real_lstat = em_outage.os.lstat
    swapped = False

    def swap_after_lstat(path):
        nonlocal swapped
        observed = real_lstat(path)
        if os.path.abspath(os.fspath(path)) == os.path.abspath(findings) \
                and not swapped:
            os.replace(replacement, findings)
            swapped = True
        return observed

    monkeypatch.setattr(em_outage.os, "lstat", swap_after_lstat)
    with pytest.raises(em_outage.EmOutageError,
                       match="output changed while hashing"):
        em_outage.output_hashes(findings, report)
    assert swapped is True


def test_resolution_receipt_binds_human_control_plane_and_outage(tmp_path):
    identity = _identity(tmp_path)
    authority = {
        "schema": em_outage.CONTROL_PLANE_SCHEMA,
        "authority": "slotless-loop-control-plane",
        "fingerprint": "d" * 64,
    }
    first = em_outage.resolution_receipt(
        identity, actor="human:vdemkiv", control_plane=authority)
    assert first == em_outage.resolution_receipt(
        identity, actor="human:vdemkiv", control_plane=authority)
    assert first["consumed"] is True
    assert first["outage_fingerprint"] == identity["fingerprint"]
    assert first["accepted_drift"] == "D-0014"


def test_timeout_bootstrap_is_plan_only_and_bounded():
    assert tp.task_test_timeout_seconds({
        "verification_runner": {
            "gate_timeout": {"aggregate_seconds": 14400}}
    }) == 14400
    with pytest.raises(ValueError, match="14400"):
        tp.task_test_timeout_seconds({
            "verification_runner": {
                "gate_timeout": {"aggregate_seconds": 14401}}
        })
    with pytest.raises(ValueError, match="3600"):
        tp.build_contract(
            "untrusted", scope=["src/**"], test_command="true",
            test_timeout_seconds=3601)
    assert tp.build_contract(
        "planned", scope=["src/**"], test_command="true", plan_minted=True,
        test_timeout_seconds=14400)["coding"]["dod"][
            "test_timeout_seconds"] == 14400
    assert tp.task_test_timeout_seconds({}) == 600


def test_timeout_never_converts_failure_to_pass(tmp_path, monkeypatch):
    contract = tp.build_contract(
        "planned", scope=["src/**"], test_command="long-suite",
        plan_minted=True, test_timeout_seconds=14400)
    contract["coding"]["dod"]["require_clean_scope_diff"] = False
    monkeypatch.setattr(
        tp, "run_suite_command",
        lambda *_a, **_k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("long-suite", 14400)))
    errors = tp.dod_check(contract, str(tmp_path), None)
    assert any("could not start" in error for error in errors)


def test_existing_public_cli_exposes_only_the_approved_resolve_shape():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "taskplane" / "tp.py"), "loop",
         "resolve", "--help"], cwd=root, text=True, capture_output=True,
        check=True)
    help_text = completed.stdout
    assert "--accept-producer-receipt-outage" in help_text
    assert "--outage-fingerprint" in help_text
    assert "--by" in help_text


def test_staged_guard_blocks_serial_wave_and_claim(tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    task = _guarded_task()
    loop.init(ws, "g", spec_path="s", parallel=False)
    state = loop.load(ws)
    state.update({"step": "execute", "tasks": [task], "current_task": 0,
                  "parallel": False})
    loop.save(ws, state)
    serial = loop.next_action(ws)
    assert serial["reason"] == "mandatory_replan_required"

    state = loop.load(ws)
    state["parallel"] = True
    loop.save(ws, state)
    monkeypatch.setattr(loop, "_ensure_dispatch_telemetry", lambda _ws: {})
    monkeypatch.setattr(loop.dispatch_telemetry, "budget_projection",
                        lambda *_: {"dispatch_allowed": True,
                                    "triggered": []})
    waved = loop.wave(ws)
    assert waved["reason"] == "mandatory_replan_required"
    assert waved["wave"] == []
    claimed = loop.claim(ws, "REL-2181", str(tmp_path / "agent"))
    assert claimed["reason"] == "mandatory_replan_required"


def _outage_state(identity: dict) -> dict:
    return {
        "step": "em", "tasks": [{"id": "engineering-signoff",
                                    "status": "passed"}],
        "current_task": 0,
        "engineering_review_outage": {
            "schema": em_outage.OUTAGE_SCHEMA,
            "reason_code": em_outage.REASON_CODE,
            "identity": identity,
            "terminal_contract": {"worker_scoped": True},
            "consumed": False,
        },
    }


def _patch_resolution(monkeypatch, identity):
    evidence = {"schema": "taskplane.signoff-evidence/v1",
                "integration_revision": identity["integration_revision"],
                "dod": {"passed": True, "errors": []}}
    monkeypatch.setattr(loop, "_em_outage_candidate",
                        lambda *_args, **_kwargs:
                        (identity, {"worker_scoped": True}, evidence))
    monkeypatch.setattr(loop, "_em_outage_control_plane_identity",
                        lambda *_: {
                            "schema": em_outage.CONTROL_PLANE_SCHEMA,
                            "authority": "slotless-loop-control-plane",
                            "fingerprint": "e" * 64})
    monkeypatch.setattr(loop, "_stage_loop_transition", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {"step": "signoff"})


def test_aggregate_em_outage_exact_acceptance_is_atomic_and_one_use(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    identity = _identity(tmp_path)
    loop.save(ws, _outage_state(identity))
    _patch_resolution(monkeypatch, identity)
    before = json.dumps(loop.load(ws), sort_keys=True)

    wrong = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint="0" * 64)
    assert "error" in wrong
    assert json.dumps(loop.load(ws), sort_keys=True) == before

    accepted = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert accepted["step"] == "signoff"
    state = loop.load(ws)
    assert state["engineering_review_outage"]["consumed"] is True
    assert state["signoff_evidence"]["accepted_drift"] == {
        "id": "D-0014", "accepted_by": "human:vdemkiv"}
    assert Path(state["engineering_review_outage"]["audit_path"]).is_file()

    replay = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert "error" in replay


def test_worker_context_actor_spoof_mutation_and_audit_failure_do_not_advance(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    identity = _identity(tmp_path)
    _patch_resolution(monkeypatch, identity)

    loop.save(ws, _outage_state(identity))
    monkeypatch.setattr(loop, "_em_outage_control_plane_identity",
                        lambda *_: (_ for _ in ()).throw(
                            em_outage.EmOutageError("worker context")))
    spoofed = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert "error" in spoofed
    assert loop.load(ws)["step"] == "em"


def test_control_plane_identity_rejects_real_worker_slot_context(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    monkeypatch.setenv("TASKPLANE_TASK", "task_worker_1")
    with pytest.raises(em_outage.EmOutageError, match="worker TASKPLANE_TASK"):
        loop._em_outage_control_plane_identity(ws, {"step": "em"})


def test_outage_sealing_refuses_non_producer_blockers_without_state_change(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    loop.save(ws, {"step": "em", "tasks": [], "current_task": 0})
    before = json.dumps(loop.load(ws), sort_keys=True)
    monkeypatch.setattr(
        loop, "_em_outage_candidate",
        lambda *_a, **_k: (_ for _ in ()).throw(
            em_outage.EmOutageError("EM has non-producer blockers")))
    refused = loop._record_em_producer_receipt_outage(
        ws, loop.load(ws), None,
        loop.producer_observation_policy.ProducerObservationError(
            "missing host producer observation"))
    assert "fail-closed" in refused["error"]
    assert json.dumps(loop.load(ws), sort_keys=True) == before


def test_stage_transition_failure_rolls_back_consumption_and_signoff(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    identity = _identity(tmp_path)
    loop.save(ws, _outage_state(identity))
    _patch_resolution(monkeypatch, identity)
    audit_path = Path(loop.runtime_storage.review_public_path(
        ws, "em-outage-resolution.json"))
    assert not os.path.lexists(audit_path)
    monkeypatch.setattr(
        loop, "_stage_loop_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stage red")))
    failed = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert "failed closed" in failed["error"]
    state = loop.load(ws)
    assert state["step"] == "em"
    assert state["engineering_review_outage"]["consumed"] is False
    assert "signoff_evidence" not in state
    assert not os.path.lexists(audit_path)

    changed = _identity(tmp_path, integration_revision="d" * 40)
    monkeypatch.setattr(loop, "_em_outage_candidate",
                        lambda *_a, **_k:
                        (changed, {}, {"dod": {"passed": True}}))
    stale = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert "stale" in stale["error"]
    assert loop.load(ws)["step"] == "em"

    _patch_resolution(monkeypatch, identity)
    monkeypatch.setattr(loop.tp, "atomic_write_json",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            OSError("audit unavailable")))
    failed = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])
    assert "failed closed" in failed["error"]
    assert loop.load(ws)["step"] == "em"
    assert not os.path.lexists(audit_path)


def test_public_resolve_restores_exact_prior_audit_bytes_on_transition_failure(
        tmp_path, monkeypatch):
    ws = _git_ws(tmp_path)
    identity = _identity(tmp_path)
    loop.save(ws, _outage_state(identity))
    _patch_resolution(monkeypatch, identity)
    authority = {
        "schema": em_outage.CONTROL_PLANE_SCHEMA,
        "authority": "slotless-loop-control-plane",
        "fingerprint": "e" * 64,
    }
    receipt = em_outage.resolution_receipt(
        identity, actor="human:vdemkiv", control_plane=authority)
    exact = json.dumps(receipt, indent=1, sort_keys=True).encode("utf-8")
    audit_path = Path(loop.runtime_storage.review_public_path(
        ws, "em-outage-resolution.json"))
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_bytes(exact)
    monkeypatch.setattr(
        loop, "_stage_loop_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stage red")))

    failed = loop.resolve(
        ws, "pass", by="human:vdemkiv",
        accept_producer_receipt_outage=True,
        outage_fingerprint=identity["fingerprint"])

    assert "failed closed" in failed["error"]
    assert audit_path.read_bytes() == exact
    state = loop.load(ws)
    assert state["step"] == "em"
    assert state["engineering_review_outage"]["consumed"] is False


@pytest.mark.parametrize("terminal_outcome", [
    "success", "failure", "cancellation", "interruption", "handoff"
])
def test_outage_cleanup_preserves_exact_terminal_lifecycle_outcome(
        tmp_path, terminal_outcome):
    ws = _git_ws(tmp_path)
    contract = tp.prepare_worker_contract(
        ws, tp.build_contract("EM", read_only=True), stage="em",
        task="engineering-signoff", task_name="tp_step_engineering_1",
        role_marker="taskplane-role:tp-engineering")
    slot = contract["task_slot"]
    tp.activate(ws, contract, task_slot_override=slot)
    receipt = tp.record_worker_terminal(
        ws, slot, event=None, outcome=terminal_outcome,
        submission_status="producer_receipt_unavailable",
        authority="loop-gate")
    released = tp.release_worker_contracts_for_gate(
        ws, stage="em", task="engineering-signoff",
        submission_status="producer_receipt_unavailable")
    assert len(released) == 1
    assert released[0]["outcome"] == terminal_outcome
    archived = tp.load_json(released[0]["quarantine"])
    assert archived["worker_lifecycle"]["terminal"] == receipt
    assert tp.worker_contract_for_stage(
        ws, stage="em", task="engineering-signoff") is None
