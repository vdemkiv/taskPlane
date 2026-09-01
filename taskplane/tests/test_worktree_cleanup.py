"""R-0003 t07: preservation, revalidation, no-force cleanup, and replay."""
from __future__ import annotations

import copy
import os
import subprocess

import pytest

from repository import RepositoryManager
import storage
import taskplane_lite as kernel
import worktree_cleanup as cleanup


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
        check=check)


def _fixture(tmp_path):
    primary = tmp_path / "repo"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "config", "user.email", "t@example.com")
    _git(primary, "config", "user.name", "T")
    (primary / ".gitignore").write_text(".tp-work/\n.taskplane/\n",
                                         encoding="utf-8")
    (primary / "shared.txt").write_text("base\n", encoding="utf-8")
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
    with open(os.path.join(worker, "task.txt"), "w", encoding="utf-8") as h:
        h.write("task\n")
    _git(worker, "add", "task.txt")
    _git(worker, "commit", "-qm", "task")
    storage.bind_worker_locator(str(primary), worker, "task-1")
    receipt = RepositoryManager(home=str(tmp_path / "home")).merge_registered_task(
        str(primary), task_id="task-1", run_id="run-1")
    return primary, worker, receipt, layout


def _released(**changes):
    value = {"status": "passed", "released": True, "active": False,
             "evidence_needed": False, "variant": None}
    value.update(changes)
    return value


@pytest.mark.parametrize("case", [
    "dirty", "untracked", "staged", "unmerged", "unregistered",
    "selected_variant", "unreleased", "active", "locked", "symlinked",
    "path_mismatch", "missing_ref", "ambiguous_main",
    "merge_in_progress", "evidence_needed",
])
def test_preservation_matrix_fails_closed(tmp_path, case):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    lifecycle = _released()
    if case == "dirty":
        with open(os.path.join(worker, "task.txt"), "a", encoding="utf-8") as h:
            h.write("dirty\n")
    elif case == "untracked":
        with open(os.path.join(worker, "untracked"), "w", encoding="utf-8") as h:
            h.write("x")
    elif case == "staged":
        with open(os.path.join(worker, "staged"), "w", encoding="utf-8") as h:
            h.write("x")
        _git(worker, "add", "staged")
    elif case == "unmerged":
        # A real unresolved index entry. The recorded tip also changes; both
        # independently point in the preservation direction.
        _git(primary, "checkout", "-q", "-b", "conflict-side")
        (primary / "shared.txt").write_text("primary\n", encoding="utf-8")
        _git(primary, "commit", "-qam", "primary conflict")
        _git(primary, "checkout", "-q", "main")
        (os.path.join(worker, "shared.txt"))
        with open(os.path.join(worker, "shared.txt"), "w", encoding="utf-8") as h:
            h.write("worker\n")
        _git(worker, "commit", "-qam", "worker conflict")
        _git(worker, "merge", "conflict-side", check=False)
    elif case == "unregistered":
        os.unlink(storage.task_worktree_registration_path(
            str(primary), "task-1"))
    elif case == "selected_variant":
        lifecycle["selected_variant"] = True
    elif case == "unreleased":
        lifecycle["released"] = False
    elif case == "active":
        lifecycle.update(status="running", active=True, released=False)
    elif case == "locked":
        _git(primary, "worktree", "lock", worker)
    elif case == "symlinked":
        moved = str(worker) + "-real"
        os.rename(worker, moved)
        os.symlink(moved, worker)
    elif case == "path_mismatch":
        receipt = copy.deepcopy(receipt)
        receipt["managed_path"] = str(tmp_path / "wrong")
        payload = {key: value for key, value in receipt.items()
                   if key not in {"receipt_id", "merged_at"}}
        receipt["receipt_id"] = "merge-" + cleanup._fingerprint(payload)[:24]
    elif case == "missing_ref":
        receipt = copy.deepcopy(receipt)
        receipt["primary_ref"] = "refs/heads/missing"
        payload = {key: value for key, value in receipt.items()
                   if key not in {"receipt_id", "merged_at"}}
        receipt["receipt_id"] = "merge-" + cleanup._fingerprint(payload)[:24]
    elif case == "ambiguous_main":
        _git(primary, "checkout", "-q", "--detach")
    elif case == "merge_in_progress":
        git_dir = _git(primary, "rev-parse", "--git-dir").stdout.strip()
        git_dir = git_dir if os.path.isabs(git_dir) else os.path.join(primary,
                                                                       git_dir)
        with open(os.path.join(git_dir, "MERGE_HEAD"), "w", encoding="utf-8") as h:
            h.write(receipt["branch_tip"] + "\n")
    elif case == "evidence_needed":
        lifecycle["evidence_needed"] = True

    result = cleanup.cleanup(receipt, lifecycle=lifecycle)
    assert result["outcome"] in {"preserved", "manual-attention"}, result
    assert os.path.lexists(worker)
    rows = cleanup._worktree_rows(str(primary))
    assert any(row["worktree"] == os.path.realpath(worker) for row in rows)


def test_no_force_revalidation_preserves_last_moment_dirty_tree(
        tmp_path, monkeypatch):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    original = cleanup._git
    calls = []

    def mutate_before_remove(cwd, *args, **kwargs):
        calls.append(args)
        if args[:3] == ("worktree", "remove", "--"):
            with open(os.path.join(worker, "late"), "w", encoding="utf-8") as h:
                h.write("arrived after eligibility")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(cleanup, "_git", mutate_before_remove)
    result = cleanup.cleanup(receipt, lifecycle=_released())
    assert result["outcome"] == "manual-attention"
    assert "no force retry" in result["reason"]
    assert os.path.isdir(worker)
    remove = [args for args in calls if args and args[0] == "worktree"][-1]
    assert "--force" not in remove and "-f" not in remove


def test_success_removes_only_linked_tree_and_replay_is_already_clean(tmp_path):
    primary, worker, receipt, layout = _fixture(tmp_path)
    durable = {
        "requirement": primary / "specs" / "spec.md",
        "design": primary / "design" / "design.md",
        "plan": primary / "plan" / "plan.md",
        "submission": layout.evidence_root + "/submission.json",
        "review": layout.evidence_root + "/review.json",
        "audit": layout.state_root + "/audit.json",
    }
    for path in durable.values():
        path = str(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("retained\n")
    branch_tip = receipt["branch_tip"]

    first = cleanup.cleanup(receipt, lifecycle=_released())
    second = cleanup.cleanup(receipt, lifecycle=_released())

    assert first["outcome"] == "removed", first
    assert first["checked"]["force"] is False
    assert first["checked"]["branch_deleted"] is False
    assert second["outcome"] == "already-clean"
    assert not os.path.exists(worker)
    assert _git(primary, "rev-parse", "refs/heads/tp/task-1").stdout.strip() \
        == branch_tip
    assert _git(primary, "cat-file", "-e", branch_tip).returncode == 0
    assert all(os.path.exists(str(path)) for path in durable.values())
    assert storage.load_task_worktree_registration(
        str(primary), "task-1")["branch_tip"] == branch_tip


def test_crash_after_git_removal_replays_as_already_clean(tmp_path):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    removed = _git(primary, "worktree", "remove", "--", worker)
    assert removed.returncode == 0, removed.stderr
    replay = cleanup.cleanup(receipt, lifecycle=_released())
    assert replay["outcome"] == "already-clean"


def test_partial_absence_requires_manual_attention_and_no_retry(tmp_path):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    moved = str(worker) + "-unexpected"
    os.rename(worker, moved)
    result = cleanup.cleanup(receipt, lifecycle=_released())
    assert result["outcome"] == "manual-attention"
    assert "disagree" in result["reason"]
    assert any(row["worktree"] == os.path.realpath(worker)
               for row in cleanup._worktree_rows(str(primary)))
