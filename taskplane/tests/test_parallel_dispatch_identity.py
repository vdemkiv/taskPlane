"""Parallel native attempts keep one identity from admission through claim."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

import pytest

from taskplane.tests import test_loop as fixtures
from taskplane.tests.test_loop import _isolate_loop_test_runtime  # noqa: F401

loop = fixtures.loop
tp = fixtures.tp


@pytest.fixture
def checkout(monkeypatch):
    ws = fixtures.TestParallelExecution()._ws()
    worker = os.path.join(ws, ".tp-work", "t1")
    subprocess.run(["git", "worktree", "add", "-q", worker, "-b", "tp/t1"],
                   cwd=ws, check=True)
    original = tp.prepare_worker_contract
    stamp = int(time.time())

    def prepare(*args, **kwargs):
        nonlocal stamp
        stamp += 1
        return original(*args, **kwargs, now=stamp)

    monkeypatch.setattr(tp, "prepare_worker_contract", prepare)
    return ws, worker


def _entry(ws):
    result = loop.wave(ws)
    assert "error" not in result, result
    return next(row for row in result["wave"] if row["task"]["id"] == "t1")


def _assert_identity(ws, worker, entry, claim):
    assert "error" not in claim, claim
    name = entry["task_name"]
    assert claim["contract_bootstrap"]["worker_identity"] == name
    assert claim["task_name"] == name
    assert claim["dispatch_intent"] == entry["dispatch_intent"]
    assert claim["native_dispatch_ready"] is True
    contract = tp.load_json(tp.active_contract_path(
        worker, claim["contract_bootstrap"]["task_slot"]))
    lifecycle = contract["worker_lifecycle"]
    assert lifecycle["expected_task_name"] == name
    assert lifecycle["dispatch_intent_id"] == entry["dispatch_intent"]["intent_id"]
    assert lifecycle["dispatch_intent_run_id"] == entry["dispatch_intent"]["identity"]["run_id"]
    payload = {
        "schema": "taskplane.native-agent-dispatch/v1", "step": "execute",
        "role": "tp-executor", "task_name": name,
        "wait_policy": entry["wait_policy"], "fork_turns": "none", "inherited_turns": 0,
    }
    expected = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()
    assert entry["dispatch_intent"]["payload_fingerprint"] == expected
    expectation = tp.peek_expectation(ws, name, strict=True)
    assert expectation["task_name"] == name
    assert expectation["intent_id"] == entry["dispatch_intent"]["intent_id"]
    assert expectation["intent_run_id"] == lifecycle["dispatch_intent_run_id"]
    assert contract["coding"]["scope_paths"] == ["src/a/**", "tests/test_t1.py"]
    return contract


def test_wave_and_unstarted_claim_replay_keep_one_attempt(checkout):
    ws, worker = checkout
    first_wave = _entry(ws)
    repeated_wave = _entry(ws)
    assert repeated_wave["task_name"] == first_wave["task_name"]
    assert repeated_wave["dispatch_intent"] == first_wave["dispatch_intent"]
    first = loop.claim(ws, "t1", worker)
    second = loop.claim(ws, "t1", worker)
    # The original implementation reserves attempt 2 here, losing the wave binding.
    assert second["contract_bootstrap"]["worker_identity"] == first_wave["task_name"]
    assert second["contract_bootstrap"] == first["contract_bootstrap"]
    _assert_identity(ws, worker, first_wave, second)
    assert loop.load(ws)["worker_dispatch_sequences"]["execute:t1"] == 1


def test_terminal_retry_requires_fresh_wave_admission(checkout):
    ws, worker = checkout
    first_wave = _entry(ws)
    first = loop.claim(ws, "t1", worker)
    owner = {"session_id": "test-loop-session", "agent_id": "native-first",
             "task_name": first_wave["task_name"]}
    tp.bind_worker_contract_event(worker, owner)
    tp.terminalize_worker_contract(
        worker, owner, outcome="interruption", submission_status="not-submitted")
    before = loop.load(ws)
    refused = loop.claim(ws, "t1", worker)
    assert "fresh wave" in refused.get("error", ""), refused
    assert loop.load(ws) == before
    retry_wave = _entry(ws)
    assert retry_wave["task_name"] != first_wave["task_name"]
    assert retry_wave["dispatch_intent"]["intent_id"] != first_wave["dispatch_intent"]["intent_id"]
    repeated = _entry(ws)
    assert repeated["task_name"] == retry_wave["task_name"]
    retry = loop.claim(ws, "t1", worker)
    _assert_identity(ws, worker, retry_wave, retry)
    assert retry["contract_bootstrap"]["task_slot"] != first["contract_bootstrap"]["task_slot"]


def test_legacy_pending_recovery_shares_wave_identity(checkout):
    ws, worker = checkout
    stale = tp.prepare_worker_contract(
        worker, tp.build_contract("EXECUTE: t1", scope=["src/a/**"],
                                  test_command="true", plan_minted=True,
                                  regression_gate=True),
        stage="execute", task="t1", task_name="tp_step_executor_t1_legacy",
        role_marker="taskplane-role:tp-executor")
    tp.activate(worker, stale, snapshot=tp.git_head(worker),
                task_slot_override=stale["task_slot"])
    with loop.mutate(ws) as state:
        state["tasks"][0].update(status="running", workspace=worker)
    entry = _entry(ws)
    claimed = loop.claim(ws, "t1", worker)
    _assert_identity(ws, worker, entry, claimed)
    assert loop.load(ws)["worker_dispatch_sequences"]["execute:t1"] == 2
    assert tp.list_task_slots(worker) == [claimed["contract_bootstrap"]["task_slot"]]
    quarantine = os.path.join(worker, ".taskplane", "quarantine", "contracts")
    archived = [tp.load_json(os.path.join(quarantine, name))
                for name in os.listdir(quarantine)]
    assert archived[0]["worker_lifecycle"]["terminal"]["submission_status"] == "superseded_pending_claim"


def test_bound_worker_cannot_be_replaced_by_claim_or_wave(checkout):
    ws, worker = checkout
    entry = _entry(ws)
    claimed = loop.claim(ws, "t1", worker)
    owner = {"session_id": "test-loop-session", "agent_id": "native-live",
             "task_name": entry["task_name"]}
    bound = tp.bind_worker_contract_event(worker, owner)
    before = loop.load(ws)
    refused = loop.claim(ws, "t1", worker)
    assert "active worker" in refused.get("error", ""), refused
    assert loop.load(ws) == before
    assert tp.load_json(tp.active_contract_path(worker, bound["slot"])) == bound["contract"]
    assert all(row["task"]["id"] != "t1" for row in loop.wave(ws)["wave"])
    assert claimed["contract_bootstrap"]["task_slot"] == bound["slot"]


def test_direct_legacy_claim_requires_wave_before_native_dispatch(checkout):
    ws, worker = checkout
    first = loop.claim(ws, "t1", worker)
    assert first["native_dispatch_ready"] is False
    assert "dispatch_intent" not in first
    entry = _entry(ws)
    repeated = loop.claim(ws, "t1", worker)
    _assert_identity(ws, worker, entry, repeated)
    assert repeated["contract_bootstrap"]["task_slot"] == first["contract_bootstrap"]["task_slot"]


@pytest.mark.parametrize("damage", ["missing-contract", "tampered-terminal"])
def test_missing_or_unverified_terminal_evidence_refuses_recovery(checkout, damage):
    ws, worker = checkout
    entry = _entry(ws)
    claimed = loop.claim(ws, "t1", worker)
    slot = claimed["contract_bootstrap"]["task_slot"]
    if damage == "missing-contract":
        os.rename(tp.active_contract_path(worker, slot),
                  os.path.join(worker, "missing-contract-fixture.json"))
    else:
        owner = {"session_id": "test-loop-session", "agent_id": "native-stopped",
                 "task_name": entry["task_name"]}
        tp.bind_worker_contract_event(worker, owner)
        tp.terminalize_worker_contract(
            worker, owner, outcome="interruption", submission_status="not-submitted")
        path = tp._worker_terminal_path(worker, slot)
        receipt = tp.load_json(path)
        receipt["signature"] = "0" * 64
        tp.atomic_write_json(path, receipt)
    before = loop.load(ws)
    refused = loop.wave(ws)
    assert "recovery refused" in refused.get("error", ""), refused
    assert loop.load(ws) == before
    assert "fresh wave" in loop.claim(ws, "t1", worker).get("error", "")


def test_terminal_retry_cannot_bypass_failed_root_admission(checkout):
    ws, worker = checkout
    entry = _entry(ws)
    loop.claim(ws, "t1", worker)
    owner = {"session_id": "test-loop-session", "agent_id": "native-stopped",
             "task_name": entry["task_name"]}
    tp.bind_worker_contract_event(worker, owner)
    tp.terminalize_worker_contract(
        worker, owner, outcome="interruption", submission_status="not-submitted")
    refused = loop.wave(ws, root_observation_authority=b"foreign")
    assert "root admission refused" in refused.get("error", ""), refused
    reservation = loop.load(ws)["parallel_worker_dispatches"]["execute:t1"]
    assert "dispatch_intent" not in reservation
    assert "fresh wave" in loop.claim(ws, "t1", worker).get("error", "")
    admitted = _entry(ws)
    _assert_identity(ws, worker, admitted, loop.claim(ws, "t1", worker))


def test_submitted_worker_goes_to_gate_not_another_attempt(checkout):
    ws, worker = checkout
    entry = _entry(ws)
    loop.claim(ws, "t1", worker)
    owner = {"session_id": "test-loop-session", "agent_id": "native-submitted",
             "task_name": entry["task_name"]}
    tp.bind_worker_contract_event(worker, owner)
    submission = loop.submit(ws, "fail", task_id="t1")
    assert submission.get("submitted") is True, submission
    tp.terminalize_worker_contract(
        worker, owner, outcome="failure", submission_status="submitted")
    before = loop.load(ws)
    assert "orchestrator gate" in loop.claim(ws, "t1", worker).get("error", "")
    assert loop.load(ws) == before
    assert all(row["task"]["id"] != "t1" for row in loop.wave(ws)["wave"])
    assert loop.load(ws)["tasks"][0]["_submission"] == before["tasks"][0]["_submission"]


def test_claim_refuses_task_scope_drift_after_wave(checkout):
    ws, worker = checkout
    _entry(ws)
    with loop.mutate(ws) as state:
        state["tasks"][0]["scope"].append("src/b/**")
    refused = loop.claim(ws, "t1", worker)
    assert "reservation mismatched" in refused.get("error", ""), refused
    assert tp.list_task_slots(worker) == []
