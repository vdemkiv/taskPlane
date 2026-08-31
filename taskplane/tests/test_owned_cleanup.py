"""Acceptance tests for the exact-owned, all-outcome cleanup protocol."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

import owned_cleanup as cleanup
from taskplane.tests.test_worktree_cleanup import _fixture as _worktree_fixture


OUTCOMES = (
    "success", "failure", "cancellation", "interruption", "timeout",
    "handoff",
)


def _manifest(tmp_path: Path, name: str = "manifest.json") -> Path:
    path = tmp_path / "durable" / name
    cleanup.create_manifest(
        path,
        repository_id="repo-1",
        workspace_fingerprint="a" * 64,
        settings_digest="b" * 64,
        run_id="run-1",
        task_id="task-1",
        attempt=1,
        evidence_root=tmp_path / "evidence",
    )
    return path


def _owned_file(manifest: Path, root: Path, name: str = "artifact.txt") -> str:
    resource_id = cleanup.reserve_resource(
        manifest,
        kind="test-artifact",
        containment_root=root,
        relative_name=name,
        creator_nonce="creator-1",
        stable_identity={"producer": "pytest", "version": "1", "input": "case"},
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("owned\n", encoding="utf-8")
    cleanup.activate_resource(manifest, resource_id)
    return resource_id


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_cleanup_runs_on_every_terminal_outcome(tmp_path, outcome):
    manifest = _manifest(tmp_path, f"{outcome}.json")
    git_fixture = tmp_path / "git"
    git_fixture.mkdir()
    _primary, worker, merge_receipt, _layout = _worktree_fixture(git_fixture)
    lifecycle_status = {
        "success": "passed", "failure": "failed",
        "cancellation": "cancelled", "interruption": "interrupted",
        "timeout": "timed_out", "handoff": "handoff",
    }[outcome]
    resource_id = cleanup.reserve_resource(
        manifest,
        kind="worktree",
        containment_root=Path(worker).parent,
        relative_name=Path(worker).name,
        creator_nonce="worktree-creator",
        stable_identity={"branch_tip": merge_receipt["branch_tip"]},
        policy={
            "merge_receipt": merge_receipt,
            "lifecycle": {
                "status": lifecycle_status, "released": True,
                "active": False, "evidence_needed": False, "variant": None,
            },
        },
    )
    cleanup.activate_resource(manifest, resource_id)
    terminal = tmp_path / f"terminal-{outcome}.json"
    terminal.write_text('{"proof":"survives"}\n', encoding="utf-8")

    receipt = cleanup.seal_and_cleanup(
        manifest, outcome=outcome, evidence={"terminal": terminal})

    assert receipt["original_outcome"] == outcome
    assert receipt["cleanup_status"] == "clean"
    assert receipt["leak_count"] == 0
    assert not Path(worker).exists()


def test_cleanup_preserves_evidence_and_proves_zero_leaks(tmp_path):
    manifest = _manifest(tmp_path)
    root = tmp_path / "owned"
    first = _owned_file(manifest, root, "result.txt")
    second = cleanup.reserve_resource(
        manifest,
        kind="generated-state",
        containment_root=root,
        relative_name="state.json",
        creator_nonce="creator-2",
        stable_identity={"producer": "dashboard", "version": "1", "input": "snap"},
        dependencies=(first,),
    )
    (root / "state.json").write_text('{"state":"terminal"}\n', encoding="utf-8")
    cleanup.activate_resource(manifest, second)
    evidence = root / "result.txt"

    receipt = cleanup.seal_and_cleanup(
        manifest, outcome="failure", evidence={"result": evidence})

    assert [row["resource_id"] for row in receipt["resources"]] == [second, first]
    assert all(row["status"] == "cleaned" for row in receipt["resources"])
    assert receipt["leaks"] == [] and receipt["leak_count"] == 0
    sealed = receipt["evidence"][0]
    assert Path(sealed["sealed_path"]).read_text(encoding="utf-8") == "owned\n"
    assert sealed["sha256"] == cleanup.file_sha256(sealed["sealed_path"])
    assert receipt["receipt_digest"] == cleanup.receipt_digest(receipt)


@pytest.mark.parametrize("case", [
    "foreign", "dirty", "symlinked", "relocated", "pid-reused",
    "containment-invalid", "ambiguous",
])
def test_cleanup_refuses_ambiguous_or_unowned_targets(tmp_path, case, monkeypatch):
    manifest = _manifest(tmp_path)
    root = tmp_path / "owned"
    resource_id = _owned_file(manifest, root)
    target = root / "artifact.txt"

    if case == "foreign":
        cleanup._rewrite_for_test(
            manifest, lambda row: row["resources"][resource_id]["owner"].update(
                task_id="foreign-task"))
    elif case == "dirty":
        target.write_text("changed after activation\n", encoding="utf-8")
    elif case == "symlinked":
        target.unlink()
        foreign = tmp_path / "foreign"
        foreign.write_text("foreign\n", encoding="utf-8")
        target.symlink_to(foreign)
    elif case == "relocated":
        target.rename(root / "moved.txt")
    elif case == "pid-reused":
        cleanup._rewrite_for_test(
            manifest,
            lambda row: row["resources"][resource_id].update(
                kind="process-group",
                observed_identity={
                    "schema": "taskplane.detached-command-binding/v1",
                    "pid": os.getpid(), "pgid": os.getpgrp(),
                    "started": "wrong-generation", "token": "owned-token",
                }),
        )
    elif case == "containment-invalid":
        cleanup._rewrite_for_test(
            manifest,
            lambda row: row["resources"][resource_id].update(
                relative_name="../foreign"),
        )
    elif case == "ambiguous":
        clone = copy.deepcopy(cleanup.load_manifest(manifest)["resources"][resource_id])
        clone["resource_id"] = "res-" + "f" * 32
        cleanup._rewrite_for_test(
            manifest, lambda row: row["resources"].update(
                {clone["resource_id"]: clone}))

    terminal = tmp_path / "terminal.json"
    terminal.write_text('{"outcome":"failure"}\n', encoding="utf-8")
    receipt = cleanup.seal_and_cleanup(
        manifest, outcome="failure", evidence={"terminal": terminal})

    assert receipt["original_outcome"] == "failure"
    assert receipt["cleanup_status"] == "attention"
    assert receipt["leak_count"] >= 1
    assert receipt["resources"][0]["status"] == "refused"
    assert target.exists() or target.is_symlink() or (root / "moved.txt").exists()


def test_cleanup_replay_is_exact_and_idempotent(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    root = tmp_path / "owned"
    _owned_file(manifest, root)
    terminal = tmp_path / "terminal.json"
    terminal.write_text('{"outcome":"success"}\n', encoding="utf-8")

    first = cleanup.seal_and_cleanup(
        manifest, outcome="success", evidence={"terminal": terminal})
    second = cleanup.seal_and_cleanup(
        manifest, outcome="timeout", evidence={"ignored": terminal})

    assert second == first
    assert second["original_outcome"] == "success"
    assert cleanup.load_manifest(manifest)["terminal"]["outcome"] == "success"
    assert second["replay_key"] == first["replay_key"]

    crash_manifest = _manifest(tmp_path, "crash.json")
    crash_root = tmp_path / "crash-owned"
    _owned_file(crash_manifest, crash_root)
    original_journal = cleanup._journal_event
    crashed = False

    def crash_before_clean_postcheck(path, event):
        nonlocal crashed
        if event.get("event") == "action-cleaned" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after exact deletion")
        return original_journal(path, event)

    monkeypatch.setattr(cleanup, "_journal_event", crash_before_clean_postcheck)
    with pytest.raises(RuntimeError, match="simulated crash"):
        cleanup.seal_and_cleanup(
            crash_manifest, outcome="failure", evidence={"terminal": terminal})
    monkeypatch.setattr(cleanup, "_journal_event", original_journal)

    recovered = cleanup.seal_and_cleanup(
        crash_manifest, outcome="timeout", evidence={"ignored": terminal})
    assert recovered["original_outcome"] == "failure"
    assert recovered["cleanup_status"] == "clean"
    assert recovered["leak_count"] == 0
