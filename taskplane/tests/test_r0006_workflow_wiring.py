import subprocess

import loop


def _workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "service.py").write_text(
        "value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.email=e@e", "-c", "user.name=t",
        "commit", "-qm", "initial",
    ], cwd=workspace, check=True)
    loop.init(str(workspace), "refresh repaired target")
    state = loop.load(str(workspace))
    worker = tmp_path / "managed-worker"
    worker.mkdir()
    state.update({
        "step": "fix", "parallel": True, "current_task": 0,
        "submission_required": False,
        "tasks": [{
            "id": "t02", "scope": ["src/service.py"], "tests": "true",
            "deps": [], "status": "built", "workspace": str(worker),
            "target_commit": "1" * 40,
        }],
    })
    loop.save(str(workspace), state)
    return str(workspace)


def test_successful_fix_gate_refreshes_target_without_legacy_transition(
        tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    repaired_tip = "2" * 40
    refreshed = []

    def refresh(primary, task_id):
        refreshed.append((primary, task_id))
        return {"branch_tip": repaired_tip}

    def forbidden_transition(*_args, **_kwargs):
        raise AssertionError("Fix must not call the legacy stage transition")

    monkeypatch.setattr(loop, "_task_dod_errors", lambda *_args: [])
    monkeypatch.setattr(
        loop.runtime_storage, "refresh_task_worktree_tip", refresh)
    monkeypatch.setattr(loop, "_stage_loop_transition", forbidden_transition)

    result = loop.gate.__wrapped__(workspace, "pass")

    assert "error" not in result, result
    state = loop.load(workspace)
    assert state["step"] == "evaluate"
    assert state["tasks"][0]["target_commit"] == repaired_tip
    assert refreshed == [(workspace, "t02")]


def test_fix_gate_fails_closed_when_repaired_target_cannot_be_bound(
        tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)

    def refuse(*_args):
        raise loop.runtime_storage.StorageIdentityError("branch moved")

    monkeypatch.setattr(loop, "_task_dod_errors", lambda *_args: [])
    monkeypatch.setattr(
        loop.runtime_storage, "refresh_task_worktree_tip", refuse)

    result = loop.gate.__wrapped__(workspace, "pass")

    assert "could not bind the repaired managed-worktree target" in \
        result["error"]
    state = loop.load(workspace)
    assert state["step"] == "fix"
    assert state["tasks"][0]["target_commit"] == "1" * 40
