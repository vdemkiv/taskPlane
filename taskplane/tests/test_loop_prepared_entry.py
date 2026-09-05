"""Repository preparation and ordinary loop entry share one run owner.

These are storage/command integration checks, not native host receipts.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from taskplane import loop, preflight, storage


def _prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("prepared entry\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=Taskplane", "-c",
                    "user.email=taskplane@example.invalid", "commit", "-qm", "base"],
                   cwd=workspace, check=True)
    engine = preflight.RepositoryPreflight(tools_provider=lambda: {
        "git": {"present": True}, "gh": {"present": False}})
    result = engine.prepare(str(workspace), workspace=str(workspace),
                            host={"kind": "test-storage-transport"})
    assert result["status"] == "ready", result
    return workspace, engine, result


@pytest.mark.parametrize("entry", ["pm", "design", "plan"])
def test_prepared_checkout_enters_ordinary_loop_with_canonical_run(
        tmp_path, monkeypatch, entry):
    workspace, engine, prepared = _prepared(tmp_path, monkeypatch)
    locator = storage.load_workspace_locator(str(workspace))
    before = engine.store.load(prepared["run_id"])
    result = loop.init(str(workspace), "bounded prepared-entry canary",
                       design=entry != "plan",
                       spec_path=None if entry == "pm" else "README.md")

    assert "error" not in result, result
    assert result["step"] == entry
    assert result["run_id"] == prepared["run_id"]
    assert result["run_artifact_binding"]["run_id"] == prepared["run_id"]
    assert storage.load_workspace_locator(str(workspace)) == locator
    assert engine.store.load(prepared["run_id"])["target"] == before["target"]
    assert not any(Path(engine.store.home, "runs").glob("loop-*"))
    assert "_stage_native_new_run_pristine" not in result


@pytest.mark.parametrize("defect", ["stale-target", "cancelled", "wrong-home"])
def test_prepared_entry_refuses_changed_owner_without_rebinding(
        tmp_path, monkeypatch, defect):
    workspace, engine, prepared = _prepared(tmp_path, monkeypatch)
    locator = storage.load_workspace_locator(str(workspace))
    if defect == "stale-target":
        subprocess.run(["git", "-c", "user.name=Taskplane", "-c",
                        "user.email=taskplane@example.invalid", "commit",
                        "--allow-empty", "-qm", "new candidate"],
                       cwd=workspace, check=True)
    elif defect == "cancelled":
        current = engine.store.load(prepared["run_id"])
        engine.store.commit(prepared["run_id"],
                            expected_revision=current["revision"],
                            changes={"status": "cancelled"})
    else:
        monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "foreign-home"))
    before = engine.store.load(prepared["run_id"])

    result = loop.init(str(workspace), "bounded prepared-entry canary", design=True)

    assert result.get("refused") is True, result
    assert "prepared" in result["error"].lower(), result
    assert storage.load_workspace_locator(str(workspace)) == locator
    assert engine.store.load(prepared["run_id"]) == before
    assert not Path(loop._loop_path(str(workspace))).exists()
