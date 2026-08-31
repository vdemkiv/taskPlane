"""Acceptance tests for the exact-owned, all-outcome cleanup protocol."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

import owned_cleanup as cleanup
from taskplane import governed_commands
from taskplane.command_runtime import CommandRuntime
from taskplane.repository import RepositoryManager
from taskplane import taskplane_lite as contract_engine
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
def test_cleanup_runs_on_every_terminal_outcome(tmp_path, outcome, monkeypatch):
    manifest = _manifest(tmp_path, f"{outcome}.json")
    git_fixture = tmp_path / "git"
    git_fixture.mkdir()
    primary, worker, merge_receipt, _layout = _worktree_fixture(git_fixture)
    lifecycle_status = {
        "success": "passed", "failure": "failed",
        "cancellation": "cancelled", "interruption": "interrupted",
        "timeout": "timed_out", "handoff": "handoff",
    }[outcome]
    lifecycle = {
        "status": lifecycle_status, "released": True,
        "active": False, "evidence_needed": False, "variant": None,
    }
    resource_id = RepositoryManager().register_owned_worktree(
        str(manifest), primary, task_id="task-1",
        merge_receipt=merge_receipt, lifecycle=lifecycle,
        creator_nonce="worktree-creator",
        evidence_refs=("terminal", "publication-replay"))
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

    # Exercise the public production composition for every declared outcome.
    workspace = tmp_path / "command-workspace"
    workspace.mkdir()
    contract_engine.activate(
        str(workspace), contract_engine.build_contract(
            "owned-cleanup-production", scope=[str(workspace)],
            tools=["exec_command"], plan_minted=True), snapshot=None)
    launched = governed_commands.execute(str(workspace), "launch", {
        "authorization": "agent:test", "run_id": "run-prod",
        "task_id": "task-prod", "attempt": 3,
        "argv": ["/bin/sleep", "5"],
    })
    handle = launched["handle"]
    assert launched["snapshot"]["identity"]["attempt"] == 3
    runtime = CommandRuntime(
        str(governed_commands.command_runtime_root(str(workspace))),
        workspace=str(workspace), authorization="agent:test")
    production_cleanup = governed_commands.owned_cleanup
    original_replay = production_cleanup.replay_publication
    publication_order = []

    def require_sealed_terminal(*args, **kwargs):
        manifest_path = launched["snapshot"]["owned_cleanup"]["manifest"]
        terminal_row = production_cleanup.load_manifest(
            manifest_path).get("terminal")
        publication_order.append(
            terminal_row.get("outcome") if terminal_row else None)
        return original_replay(*args, **kwargs)

    monkeypatch.setattr(
        production_cleanup, "replay_publication", require_sealed_terminal)
    if outcome in {"success", "failure", "timeout"}:
        state = {"success": "succeeded", "failure": "failed",
                 "timeout": "timed_out"}[outcome]
        runtime.transition(
            handle, state, exit_code=0 if state == "succeeded" else 1)
        production = governed_commands.execute(str(workspace), "wait", {
            "authorization": "agent:test", "handle": handle,
            "consumer": "owned-cleanup:" + outcome, "timeout": 1,
        })
    else:
        action = {"cancellation": "cancel", "interruption": "interrupt",
                  "handoff": "handoff"}[outcome]
        production = governed_commands.execute(str(workspace), action, {
            "authorization": "agent:test", "handle": handle,
        })
    assert publication_order == [outcome]
    assert production["cleanup_receipt"]["cleanup_status"] == "clean", \
        production["cleanup_receipt"]["resources"]
    assert production["cleanup_evidence"]["leak_count"] == 0
    assert production["cleanup_receipt"]["original_outcome"] == outcome
    assert production["publication_replay"]["trigger"] == (
        "handoff" if outcome == "handoff" else "terminal")


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
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace=str(tmp_path),
        authorization="ci:owned-cleanup")
    cache_id = runtime.reserve_owned_path(
        str(manifest), kind="cache", relative_name="ci-cache",
        producer="ci-shard", version="1", input_identity={"candidate": "abc"},
        creator_nonce="ci-cache", evidence_refs=(
            "result", "publication-replay"), dependencies=(second,))
    (runtime.root / "ci-cache").mkdir()
    (runtime.root / "ci-cache" / "entry.json").write_text(
        '{"candidate":"abc"}\n', encoding="utf-8")
    runtime.activate_owned_path(str(manifest), cache_id)
    browser_id = runtime.reserve_owned_path(
        str(manifest), kind="test-artifact", relative_name="browser-dom.json",
        producer="browser-shard", version="chromium-1",
        input_identity={"candidate": "abc", "selector": "dashboard"},
        creator_nonce="browser-artifact", evidence_refs=(
            "result", "publication-replay"), dependencies=(cache_id,))
    (runtime.root / "browser-dom.json").write_text(
        '{"dom":"verified"}\n', encoding="utf-8")
    runtime.activate_owned_path(str(manifest), browser_id)
    evidence = root / "result.txt"

    receipt = cleanup.seal_and_cleanup(
        manifest, outcome="failure", evidence=_evidence(
            manifest, outcome="failure", label="result", source=evidence))

    assert [row["resource_id"] for row in receipt["resources"]] == [
        browser_id, cache_id, second, first]
    assert all(row["status"] == "cleaned" for row in receipt["resources"])
    assert receipt["leaks"] == [] and receipt["leak_count"] == 0
    sealed = next(row for row in receipt["evidence"]
                  if row["label"] == "result")
    assert Path(sealed["sealed_path"]).read_text(encoding="utf-8") == "owned\n"
    assert sealed["sha256"] == cleanup.file_sha256(sealed["sealed_path"])
    assert receipt["receipt_digest"] == cleanup.receipt_digest(receipt)
    consumer = cleanup.cleanup_consumer_evidence(receipt)
    assert consumer["schema"] == cleanup.CLEANUP_EVIDENCE_SCHEMA
    assert consumer["leak_count"] == 0
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
    "directory-content",
])
def test_cleanup_refuses_ambiguous_or_unowned_targets(tmp_path, case, monkeypatch):
    manifest = _manifest(tmp_path)
    root = tmp_path / "owned"
    if case == "directory-content":
        runtime = CommandRuntime(
            str(root), workspace=str(tmp_path), authorization="cache:test")
        resource_id = runtime.reserve_owned_path(
            str(manifest), kind="cache", relative_name="cache",
            producer="suite-cache", version="1",
            input_identity={"case": "directory-content"},
            creator_nonce="cache-creator",
            evidence_refs=("terminal", "publication-replay"))
        target = root / "cache"
        target.mkdir(parents=True)
        (target / "entry.txt").write_text("first\n", encoding="utf-8")
        runtime.activate_owned_path(str(manifest), resource_id)
    else:
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
    elif case == "directory-content":
        # Root inode/mode/link-count stay fixed; only the live cache content
        # changes, so removing the kind-specific content edge makes this red.
        (target / "entry.txt").write_text("substituted\n", encoding="utf-8")

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

    # The public publisher must receive and verify the exact immutable source
    # identity before it may mark an obligation published.
    identity_manifest = _manifest(tmp_path, "publication-identity.json")
    identity_source = _publication(
        identity_manifest, tmp_path / "publication-identity-replay.json",
        outcome="success", revision=7)
    identity_owner = cleanup.load_manifest(identity_manifest)["owner"]
    publisher_calls = []

    def mismatched_publisher(selected_workspace, **kwargs):
        publisher_calls.append((selected_workspace, kwargs))
        return {
            "source_revision": kwargs["source_revision"] + 1,
            "source_fingerprint": kwargs["source_fingerprint"],
        }

    with pytest.raises(cleanup.OwnedCleanupError,
                       match="did not verify source identity"):
        cleanup.replay_publication(
            identity_source, workspace=str(tmp_path), owner=identity_owner,
            outcome="success", publisher=mismatched_publisher)
    assert json.loads(identity_source.read_text(encoding="utf-8"))["status"] \
        == "pending"
    assert publisher_calls[0][1]["source_revision"] == 7
    assert publisher_calls[0][1]["source_fingerprint"] == "c" * 64

    def verifying_publisher(_selected_workspace, **kwargs):
        return {
            "source_revision": kwargs["source_revision"],
            "source_fingerprint": kwargs["source_fingerprint"],
            "status": "published",
        }

    identity_publication = cleanup.replay_publication(
        identity_source, workspace=str(tmp_path), owner=identity_owner,
        outcome="success", publisher=verifying_publisher)
    assert identity_publication["source_revision"] == 7
    assert identity_publication["source_fingerprint"] == "c" * 64

    # Deliberately sever publication during failure unwind. Original terminal
    # truth must already be sealed, and the real omitted-attempt execute path
    # must later discover/recover only this run/task's older manifests.
    workspace = tmp_path / "recovery-workspace"
    workspace.mkdir()
    context = governed_commands.prepare_owned_cleanup(
        str(workspace), "agent:recovery", run_id="run-recovery",
        task_id="task-recovery", attempt=1, token="9" * 32)
    terminal_at_publication = []

    def severed_publisher(_workspace, **_kwargs):
        terminal = cleanup.load_manifest(context["manifest"]).get("terminal")
        terminal_at_publication.append(
            terminal.get("outcome") if terminal else None)
        raise RuntimeError("publisher edge severed")

    unwind = governed_commands.unwind_owned_failure(
        context, error="launch failed before create",
        publisher=severed_publisher)
    assert unwind["cleanup_receipt"]["cleanup_status"] == "clean"
    assert unwind["publication_result"]["replay_required"] is True
    assert unwind["cleanup_evidence"]["leak_count"] == 0
    assert terminal_at_publication == ["failure"]
    orphan = governed_commands.prepare_owned_cleanup(
        str(workspace), "agent:recovery", run_id="run-recovery",
        task_id="task-recovery", attempt=1, token="8" * 32)
    assert cleanup.load_manifest(orphan["manifest"])["terminal"] is None
    foreign_run = governed_commands.prepare_owned_cleanup(
        str(workspace), "agent:foreign", run_id="run-foreign",
        task_id="task-recovery", attempt=1, token="7" * 32)
    foreign_task = governed_commands.prepare_owned_cleanup(
        str(workspace), "agent:foreign", run_id="run-recovery",
        task_id="task-foreign", attempt=1, token="6" * 32)
    foreign_before = {
        row["manifest"]: cleanup.load_manifest(row["manifest"])
        for row in (foreign_run, foreign_task)
    }

    with pytest.raises(governed_commands.GovernedCommandError,
                       match="exact active contract"):
        governed_commands.execute(str(workspace), "launch", {
            "authorization": "agent:recovery", "run_id": "run-recovery",
            "task_id": "task-recovery",
            "argv": ["/usr/bin/printf", "recovered"],
        })

    for recovered_context in (context, orphan):
        recovered_manifest = cleanup.load_manifest(
            recovered_context["manifest"])
        assert recovered_manifest["terminal"] is not None
        receipt_path = Path(recovered_context["manifest"]).with_name(
            Path(recovered_context["manifest"]).name +
            ".cleanup-receipt.json")
        recovered_receipt = json.loads(
            receipt_path.read_text(encoding="utf-8"))
        assert recovered_receipt["cleanup_status"] == "clean"
        assert recovered_receipt["leak_count"] == 0
    assert all(cleanup.load_manifest(path) == value
               for path, value in foreign_before.items())
