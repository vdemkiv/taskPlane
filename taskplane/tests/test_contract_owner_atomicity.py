"""Activation and exact-owner completion serialize on one contract lock."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading

import pytest

from taskplane import taskplane_lite as kernel


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TASKPLANE_", "CLAUDE_", "CODEX_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "state"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return str(checkout)


def contract(goal):
    return kernel.build_contract(goal, read_only=True,
                                 write_allow=["docs/**"])


@pytest.mark.parametrize("slot", [None, "sibling"])
def test_empty_activation_preserves_root_and_sibling_owners(workspace, slot):
    existing = contract("existing owner")
    kernel.activate(workspace, existing, snapshot=None, task_slot_override=slot)
    path = Path(kernel.active_contract_path(workspace, slot))
    before = path.read_bytes()
    with pytest.raises(kernel.StateError, match="empty workspace contract set"):
        kernel.activate(workspace, contract("Product"), snapshot=None,
                        require_empty=True)
    assert path.read_bytes() == before


def test_empty_activation_has_only_one_concurrent_winner(workspace):
    barrier = threading.Barrier(2)
    outcomes = []

    def activate(goal):
        candidate = contract(goal)
        barrier.wait(timeout=5)
        try:
            kernel.activate(workspace, candidate, snapshot=None, require_empty=True)
        except kernel.StateError as exc:
            outcomes.append(("refused", str(exc)))
        else:
            outcomes.append(("activated", candidate["task_id"]))

    workers = [threading.Thread(target=activate, args=(goal,))
               for goal in ("first Product", "second Product")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert sorted(outcome[0] for outcome in outcomes) == ["activated", "refused"]
    winner = next(value for status, value in outcomes if status == "activated")
    assert kernel.load_active(workspace)["task_id"] == winner


def test_exact_clear_refuses_missing_or_replaced_owner(workspace):
    original = contract("original")
    with pytest.raises(kernel.StateError, match="missing"):
        kernel.clear(workspace, expected_task_id=original["task_id"])
    kernel.activate(workspace, original, snapshot=None)
    replacement = contract("replacement")
    kernel.activate(workspace, replacement, snapshot=None)
    with pytest.raises(kernel.StateError, match="owner changed"):
        kernel.clear(workspace, expected_task_id=original["task_id"])
    assert kernel.load_active(workspace)["task_id"] == replacement["task_id"]


def test_clear_guard_observes_current_contract_and_preserves_rejected_owner(workspace):
    owner = contract("current")
    kernel.activate(workspace, owner, snapshot=None)
    observed = []

    def refuse(current):
        observed.append(current)
        return False

    with pytest.raises(kernel.StateError, match="no longer permits"):
        kernel.clear(workspace, expected_task_id=owner["task_id"], guard=refuse)
    assert observed == [kernel.load_active(workspace)]


def test_replacement_waits_for_checked_clear_and_survives(workspace, monkeypatch):
    original = contract("original")
    replacement = contract("replacement")
    kernel.activate(workspace, original, snapshot=None)
    guard_entered = threading.Event()
    release_guard = threading.Event()
    activation_attempted = threading.Event()
    activation_finished = threading.Event()
    failures = []
    local = threading.local()
    real_lock = kernel.file_lock
    lock_path = os.path.join(kernel.tp_dir(workspace), "active_contract.json")

    @contextmanager
    def observed_lock(path, **kwargs):
        if path == lock_path and getattr(local, "activation", False):
            activation_attempted.set()
        with real_lock(path, **kwargs):
            yield

    monkeypatch.setattr(kernel, "file_lock", observed_lock)

    def guard(current):
        assert current["task_id"] == original["task_id"]
        guard_entered.set()
        assert release_guard.wait(timeout=5)
        return True

    def finish():
        try:
            kernel.clear(workspace, expected_task_id=original["task_id"], guard=guard)
        except BaseException as exc:
            failures.append(exc)

    def activate():
        local.activation = True
        try:
            kernel.activate(workspace, replacement, snapshot=None)
            activation_finished.set()
        except BaseException as exc:
            failures.append(exc)

    finisher = threading.Thread(target=finish)
    activator = threading.Thread(target=activate)
    finisher.start()
    try:
        assert guard_entered.wait(timeout=5)
        activator.start()
        assert activation_attempted.wait(timeout=5)
        assert not activation_finished.is_set()
        assert kernel.load_active(workspace)["task_id"] == original["task_id"]
    finally:
        release_guard.set()
        finisher.join(timeout=10)
        if activator.ident is not None:
            activator.join(timeout=10)
    assert not finisher.is_alive() and not activator.is_alive()
    assert not failures
    assert activation_finished.is_set()
    assert kernel.load_active(workspace)["task_id"] == replacement["task_id"]


def test_guarded_clear_preserves_sibling_and_documents(workspace, monkeypatch):
    owner, sibling = contract("owner"), contract("sibling")
    kernel.activate(workspace, owner, snapshot="owner-snapshot", task_slot_override="owner")
    kernel.activate(workspace, sibling, snapshot="sibling-snapshot", task_slot_override="sibling")
    artifact = Path(workspace) / "docs" / "assessment.md"
    artifact.parent.mkdir()
    artifact.write_text("Product DoD remains unverified", encoding="utf-8")
    monkeypatch.setenv("TASKPLANE_TASK", "owner")
    kernel.clear(workspace, expected_task_id=owner["task_id"], guard=lambda current: True)
    assert not Path(kernel.active_contract_path(workspace)).exists()
    assert kernel.list_task_slots(workspace) == ["sibling"]
    assert kernel.snapshot_ref(workspace, task_slot_override="sibling") == "sibling-snapshot"
    assert artifact.read_text(encoding="utf-8") == "Product DoD remains unverified"


def test_guarded_clear_refuses_corruption_but_operator_recovery_remains(workspace):
    owner = contract("owner")
    kernel.activate(workspace, owner, snapshot=None)
    path = Path(kernel.active_contract_path(workspace))
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(kernel.StateError):
        kernel.clear(workspace, expected_task_id=owner["task_id"])
    assert path.read_text(encoding="utf-8") == "not JSON"
    kernel.clear(workspace)
    assert not path.exists()
