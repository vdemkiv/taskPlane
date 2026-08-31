"""Acceptance tests for the exact-owned, all-outcome cleanup protocol."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import owned_cleanup as cleanup
from taskplane import governed_commands
from taskplane.command_adapters import detached_process_binding
from taskplane.command_runtime import CommandRuntime
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


def _publication(manifest: Path, source: Path, *, outcome: str,
                 trigger: str = "terminal", revision: int = 1) -> Path:
    row = cleanup.load_manifest(manifest)
    cleanup.write_publication_replay(
        source, owner=row["owner"], outcome=outcome,
        source_revision=revision, source_fingerprint="c" * 64,
        trigger=trigger)
    return source


def _evidence(manifest: Path, *, outcome: str, label: str,
              source: Path, trigger: str = "terminal") -> dict[str, Path]:
    publication = source.parent / (
        manifest.name + "." + source.name + ".publication.json")
    return {
        label: source,
        "publication-replay": _publication(
            manifest, publication, outcome=outcome, trigger=trigger),
    }


def _owned_file(manifest: Path, root: Path, name: str = "artifact.txt", *,
                evidence_ref: str = "terminal") -> str:
    resource_id = cleanup.reserve_resource(
        manifest,
        kind="test-artifact",
        containment_root=root,
        relative_name=name,
        creator_nonce="creator-1",
        stable_identity={"producer": "pytest", "version": "1", "input": "case"},
        evidence_refs=(evidence_ref, "publication-replay"),
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
        evidence_refs=("terminal", "publication-replay"),
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
        manifest, outcome=outcome,
        evidence=_evidence(
            manifest, outcome=outcome, label="terminal", source=terminal,
            trigger="handoff" if outcome == "handoff" else "terminal"))

    assert receipt["original_outcome"] == outcome
    assert receipt["cleanup_status"] == "clean"
    assert receipt["leak_count"] == 0
    assert not Path(worker).exists()

    if outcome == "handoff":
        # Exercise the scoped production composition: process reservation is
        # before Popen, runtime reservation is before snapshot creation, the
        # handoff is an owned dependency, and terminalization publishes one
        # replay obligation before worktree-last cleanup.
        workspace = tmp_path / "command-workspace"
        workspace.mkdir()
        token = "d" * 32
        context = governed_commands._prepare_owned_cleanup(
            str(workspace), "agent:test", run_id="run-prod",
            task_id="task-prod", token=token)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            start_new_session=True)
        binding = detached_process_binding(process, token=token)
        governed_commands._activate_owned_process(context, binding)
        runtime = CommandRuntime(
            str(governed_commands._runtime_root(str(workspace))),
            workspace=str(workspace), authorization="agent:test",
            owned_cleanup_context=governed_commands._runtime_cleanup_context(
                context))
        handle = runtime.create(
            command_fingerprint="production-terminal",
            binding=binding,
            identity={"schema": "taskplane.governed-command-identity/v1",
                      "run_id": "run-prod", "task_id": "task-prod"})
        runtime.transition(handle, "running")
        handoff_id = governed_commands._reserve_owned_handoff(
            context, token=token, run_id="run-prod", task_id="task-prod",
            handle=handle)
        governed_commands._atomic_json(Path(context["handoff_path"]), {
            "schema": "taskplane.governed-command-handoff/v1",
            "workspace": str(workspace), "authorization": "agent:test",
            "root": str(runtime.root), "handle": handle, "argv": ["proof"],
            "cwd": str(workspace), "deadline": None,
            "identity": {"schema": "taskplane.governed-command-identity/v1",
                         "run_id": "run-prod", "task_id": "task-prod"},
            "authority": {"fingerprint": "proof"},
        })
        cleanup.activate_resource(context["manifest"], handoff_id)
        runtime.transition(handle, "succeeded", exit_code=0)
        production = governed_commands._finalize_owned_result({
            "schema": governed_commands.RESULT_SCHEMA,
            "action": "wait", "handle": handle,
            "snapshot": runtime.snapshot(handle),
        }, trigger="handoff")
        assert production["cleanup_receipt"]["cleanup_status"] == "clean", \
            production["cleanup_receipt"]["resources"]
        assert production["cleanup_receipt"]["leak_count"] == 0
        assert production["publication_replay"]["trigger"] == "handoff"
        recovered = governed_commands._finalize_owned_result({
            "schema": governed_commands.RESULT_SCHEMA,
            "action": "reconnect", "handle": handle,
            "snapshot": production["snapshot"],
        }, trigger="recovery")
        assert recovered["cleanup_receipt"] == production["cleanup_receipt"]
        process.wait(timeout=3)


def test_cleanup_preserves_evidence_and_proves_zero_leaks(tmp_path):
    manifest = _manifest(tmp_path)
    root = tmp_path / "owned"
    first = _owned_file(
        manifest, root, "result.txt", evidence_ref="result")
    second = cleanup.reserve_resource(
        manifest,
        kind="generated-state",
        containment_root=root,
        relative_name="state.json",
        creator_nonce="creator-2",
        stable_identity={"producer": "dashboard", "version": "1", "input": "snap"},
        evidence_refs=("result", "publication-replay"),
        dependencies=(first,),
    )
    (root / "state.json").write_text('{"state":"terminal"}\n', encoding="utf-8")
    cleanup.activate_resource(manifest, second)
    evidence = root / "result.txt"

    receipt = cleanup.seal_and_cleanup(
        manifest, outcome="failure", evidence=_evidence(
            manifest, outcome="failure", label="result", source=evidence))

    assert [row["resource_id"] for row in receipt["resources"]] == [second, first]
    assert all(row["status"] == "cleaned" for row in receipt["resources"])
    assert receipt["leaks"] == [] and receipt["leak_count"] == 0
    sealed = next(row for row in receipt["evidence"]
                  if row["label"] == "result")
    assert Path(sealed["sealed_path"]).read_text(encoding="utf-8") == "owned\n"
    assert sealed["sha256"] == cleanup.file_sha256(sealed["sealed_path"])
    assert receipt["receipt_digest"] == cleanup.receipt_digest(receipt)
    assert receipt["manifest_revision"] == cleanup.load_manifest(manifest)["revision"]
    assert {row["label"] for row in receipt["evidence"]} == {
        "result", "publication-replay"}
    assert all(Path(row["sealed_path"]).parent == tmp_path / "evidence"
               for row in receipt["evidence"])

    unsafe = tmp_path / "unsafe.json"
    cleanup.create_manifest(
        unsafe, repository_id="repo-1", workspace_fingerprint="a" * 64,
        settings_digest="b" * 64, run_id="run-unsafe", task_id="task-1",
        attempt=1, evidence_root=tmp_path / "deletable" / "evidence")
    with pytest.raises(cleanup.OwnedCleanupError,
                       match="inside a deletable target"):
        cleanup.reserve_resource(
            unsafe, kind="generated-state", containment_root=tmp_path,
            relative_name="deletable", creator_nonce="unsafe",
            stable_identity={"generation": 1},
            evidence_refs=("terminal", "publication-replay"))


@pytest.mark.parametrize("case", [
    "foreign", "dirty", "symlinked", "relocated", "pid-reused",
    "containment-invalid", "ambiguous", "stable-identity",
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
    elif case == "stable-identity":
        cleanup._rewrite_for_test(
            manifest, lambda row: row["resources"][resource_id][
                "stable_identity"].update(version="substituted"))

    terminal = tmp_path / "terminal.json"
    terminal.write_text('{"outcome":"failure"}\n', encoding="utf-8")
    receipt = cleanup.seal_and_cleanup(
        manifest, outcome="failure", evidence=_evidence(
            manifest, outcome="failure", label="terminal", source=terminal))

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

    evidence = _evidence(
        manifest, outcome="success", label="terminal", source=terminal)
    first = cleanup.seal_and_cleanup(
        manifest, outcome="success", evidence=evidence)
    second = cleanup.seal_and_cleanup(
        manifest, outcome="timeout", evidence=evidence)

    assert second == first
    assert second["original_outcome"] == "success"
    assert cleanup.load_manifest(manifest)["terminal"]["outcome"] == "success"
    assert second["replay_key"] == first["replay_key"]

    stale_receipt = copy.deepcopy(first)
    cleanup._rewrite_for_test(
        manifest, lambda row: row["journal"].append({"event": "audit"}))
    receipt_path = manifest.with_name(manifest.name + ".cleanup-receipt.json")
    receipt_path.write_text(json.dumps(stale_receipt), encoding="utf-8")
    with pytest.raises(cleanup.OwnedCleanupError, match="stale or bound"):
        cleanup.cleanup_manifest(manifest)

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
            crash_manifest, outcome="failure", evidence=_evidence(
                crash_manifest, outcome="failure", label="terminal",
                source=terminal))
    monkeypatch.setattr(cleanup, "_journal_event", original_journal)

    recovered = cleanup.seal_and_cleanup(
        crash_manifest, outcome="timeout", evidence=_evidence(
            crash_manifest, outcome="failure", label="terminal",
            source=terminal))
    assert recovered["original_outcome"] == "failure"
    assert recovered["cleanup_status"] == "clean"
    assert recovered["leak_count"] == 0

    race_manifest = _manifest(tmp_path, "terminal-race.json")
    race_root = tmp_path / "race-owned"
    _owned_file(race_manifest, race_root)
    race_evidence = {
        selected: _evidence(
            race_manifest, outcome=selected, label="terminal",
            source=terminal.with_name(f"terminal-{selected}.json"))
        for selected in ("failure", "timeout")
    }
    for selected in ("failure", "timeout"):
        Path(race_evidence[selected]["terminal"]).write_text(
            '{"outcome":"race"}\n', encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as executor:
        terminals = list(executor.map(
            lambda selected: cleanup.seal_terminal(
                race_manifest, outcome=selected,
                evidence=race_evidence[selected]),
            ("failure", "timeout")))
    assert len({row["terminal_digest"] for row in terminals}) == 1
    assert cleanup.load_manifest(race_manifest)["terminal"]["outcome"] in {
        "failure", "timeout"}
