"""R-0003 t06: durable merge receipt and read-only cleanup eligibility."""
from __future__ import annotations

import os
import subprocess

import pytest

from repository import RepositoryAcquisitionError, RepositoryManager
import storage
import worktree_cleanup as cleanup


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
        check=check)


@pytest.fixture
def merged_task(tmp_path):
    primary = tmp_path / "repo"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "config", "user.email", "t@example.com")
    _git(primary, "config", "user.name", "T")
    (primary / ".gitignore").write_text(".tp-work/\n.taskplane/\n",
                                         encoding="utf-8")
    (primary / "base.txt").write_text("base\n", encoding="utf-8")
    _git(primary, "add", ".")
    _git(primary, "commit", "-qm", "base")
    identity = storage.resolve_repository_identity(str(primary))
    layout = storage.resolve_layout(
        identity, home=str(tmp_path / "home"), run_id="run-1")
    storage.write_workspace_locator(
        str(primary), identity=identity, layout=layout, run_id="run-1")
    worker = storage.task_worktree_path(str(primary), "task-1")
    os.makedirs(os.path.dirname(worker), exist_ok=True)
    _git(primary, "worktree", "add", "-q", "-b", "tp/task-1", worker)
    (os.path.join(worker, "change.txt"))
    with open(os.path.join(worker, "change.txt"), "w", encoding="utf-8") as h:
        h.write("change\n")
    _git(worker, "add", "change.txt")
    _git(worker, "commit", "-qm", "task")
    registration_path = storage.bind_worker_locator(
        str(primary), worker, "task-1")
    registration = storage.load_task_worktree_registration(
        str(primary), "task-1")
    assert registration_path
    receipt = RepositoryManager(home=str(tmp_path / "home")).merge_registered_task(
        str(primary), task_id="task-1", run_id="run-1")
    return primary, worker, registration, receipt


def _released():
    return {"status": "passed", "released": True, "active": False,
            "evidence_needed": False}


def test_local_repository_identity_is_shared_by_linked_worktrees(merged_task):
    primary, worker, _registration, _receipt = merged_task
    assert storage.resolve_repository_identity(str(primary)).repo_id == \
        storage.resolve_repository_identity(worker).repo_id


def test_registration_records_exact_managed_branch_tip_and_owner(merged_task):
    primary, worker, registration, receipt = merged_task
    assert registration["schema"] == "taskplane.managed-task-worktree/v1"
    assert registration["path"] == os.path.realpath(worker)
    assert registration["branch_ref"] == "refs/heads/tp/task-1"
    assert registration["branch_tip"] == receipt["branch_tip"]
    assert registration["task_id"] == receipt["task_id"] == "task-1"
    assert receipt["schema"] == "taskplane.task-merge/v1"
    assert receipt["primary_ref"] == "refs/heads/main"
    assert receipt["primary_tip"] == _git(
        primary, "rev-parse", "HEAD").stdout.strip()
    assert cleanup.validate_merge_receipt(receipt) == receipt


def test_eligibility_proves_registration_clean_lifecycle_and_ancestry(
        merged_task):
    _primary, _worker, _registration, receipt = merged_task
    proof = cleanup.eligibility(receipt, lifecycle=_released())
    assert proof["outcome"] == "pending", proof["reason"]
    assert proof["reason"] == "eligible"
    assert proof["checked"]["clean"] is True
    assert proof["checked"]["ancestor"] is True
    assert proof["checked"]["lifecycle"] == "terminal_released"


def test_task_status_branch_name_or_path_prefix_alone_never_qualifies(
        merged_task):
    primary, worker, _registration, receipt = merged_task
    os.unlink(storage.task_worktree_registration_path(
        str(primary), "task-1"))
    proof = cleanup.eligibility(receipt, lifecycle=_released())
    assert proof["outcome"] == "preserved"
    assert "registration is missing" in proof["reason"]
    assert os.path.isdir(worker)


def test_merge_receipt_refuses_unmerged_registered_tip(tmp_path):
    primary = tmp_path / "repo"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "config", "user.email", "t@example.com")
    _git(primary, "config", "user.name", "T")
    (primary / ".gitignore").write_text(".tp-work/\n.taskplane/\n",
                                         encoding="utf-8")
    (primary / "base").write_text("x", encoding="utf-8")
    _git(primary, "add", ".")
    _git(primary, "commit", "-qm", "base")
    worker = storage.task_worktree_path(str(primary), "task")
    os.makedirs(os.path.dirname(worker), exist_ok=True)
    _git(primary, "worktree", "add", "-q", "-b", "tp/task", worker)
    (os.path.join(worker, "new"))
    with open(os.path.join(worker, "new"), "w", encoding="utf-8") as h:
        h.write("new")
    _git(worker, "add", "new")
    _git(worker, "commit", "-qm", "task")
    storage.bind_worker_locator(str(primary), worker, "task")
    with pytest.raises(cleanup.CleanupError, match="not merged"):
        cleanup.record_merge_receipt(
            str(primary), task_id="task", run_id="run")


def test_merge_boundary_refreshes_committed_tip_after_registration(merged_task):
    primary, worker, _registration, _receipt = merged_task
    with open(os.path.join(worker, "later.txt"), "w", encoding="utf-8") as h:
        h.write("later\n")
    _git(worker, "add", "later.txt")
    _git(worker, "commit", "-qm", "moved after registration")
    tip = _git(worker, "rev-parse", "HEAD").stdout.strip()
    receipt = RepositoryManager().merge_registered_task(
        str(primary), task_id="task-1", run_id="run-1")
    assert receipt["branch_tip"] == tip
    assert _git(primary, "rev-parse", "HEAD").stdout.strip() == tip


def test_merge_boundary_refuses_branch_identity_change(merged_task):
    primary, worker, _registration, _receipt = merged_task
    _git(worker, "switch", "-qc", "tp/replacement")
    with pytest.raises(RepositoryAcquisitionError,
                       match="managed task branch changed"):
        RepositoryManager().merge_registered_task(
            str(primary), task_id="task-1", run_id="run-1")
