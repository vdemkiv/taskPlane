"""Contracts and lifecycle replay are isolated to one exact Git worktree."""

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

import taskplane_lite as tp  # noqa: E402
import tp as cli  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def _worktrees(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "nested.txt").write_text("nested\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", "src/nested.txt")
    _git(repo, "commit", "-qm", "base")
    a, b = tmp_path / "worktree-a", tmp_path / "worktree-b"
    _git(repo, "worktree", "add", "-q", "-b", "work-a", str(a))
    _git(repo, "worktree", "add", "-q", "-b", "work-b", str(b))
    return repo, a, b


def test_contract_and_lifecycle_claims_bind_exact_worktree(tmp_path, monkeypatch):
    _, worktree_a, worktree_b = _worktrees(tmp_path)
    slot = "review-a"
    monkeypatch.setenv("TASKPLANE_TASK", slot)
    contract = tp.build_contract("review A", scope=["tracked.txt"])
    tp.activate(str(worktree_a), contract, snapshot=tp.git_head(str(worktree_a)))

    event = {
        "hook_event_name": "SubagentStart",
        "session_id": "same-host-session",
        "turn_id": "same-turn",
        "agent_id": "same-child",
        "cwd": str(worktree_a / "."),
    }
    first = tp.claim_hook_event(
        str(worktree_a), "subagent-start", event, hook_path="native")
    assert first["execute"] is True
    tp.complete_hook_event(str(worktree_a), first, response_class="context")

    duplicate = tp.claim_hook_event(
        str(worktree_a / "src"), "subagent-start",
        {**event, "cwd": str(worktree_a / "src")}, hook_path="bridge")
    assert duplicate["execute"] is False
    assert duplicate["status"] == "replay"

    sibling_event = {**event, "cwd": str(worktree_b)}
    sibling = tp.claim_hook_event(
        str(worktree_b), "subagent-start", sibling_event, hook_path="native")
    assert sibling["execute"] is True
    assert sibling["claim_id"] != first["claim_id"]

    # A task slot inherited by the host process belongs to A. It must not
    # turn an otherwise ungoverned sibling worktree into a corrupt/blocked
    # lifecycle. A remains governed throughout.
    assert tp.load_active(str(worktree_b)) is None
    assert tp.load_active(str(worktree_a))["task_id"] == contract["task_id"]
    assert cli._governed_root(str(worktree_b)) == str(worktree_b)
    assert cli._governed_root(str(worktree_a / "src")) == str(worktree_a)


def test_sibling_lifecycle_does_not_clear_or_weaken_owner(tmp_path, monkeypatch):
    _, worktree_a, worktree_b = _worktrees(tmp_path)
    monkeypatch.setenv("TASKPLANE_TASK", "owner-slot")
    contract = tp.build_contract("owner", scope=["tracked.txt"])
    tp.activate(str(worktree_a), contract, snapshot=tp.git_head(str(worktree_a)))

    # A sibling Stop-like lookup is advisory because no contract is active in
    # that exact worktree. It neither consumes nor removes A's slot.
    assert cli._submission_stop_check(
        {"cwd": str(worktree_b), "hook_event_name": "SubagentStop",
         "session_id": "s", "agent_id": "child"}) is None
    active = tp.load_active(str(worktree_a))
    assert active is not None
    assert active["task_id"] == contract["task_id"]
    assert os.path.exists(tp.active_contract_path(str(worktree_a)))
