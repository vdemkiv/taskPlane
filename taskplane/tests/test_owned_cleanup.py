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
TERMINAL_OUTCOME_CASES = (
    *(pytest.param(outcome, None, None, id=outcome) for outcome in OUTCOMES),
    pytest.param("success", "fresh", KeyboardInterrupt,
                 id="fresh-publication-keyboard-interrupt"),
    pytest.param("success", "fresh", SystemExit,
                 id="fresh-publication-system-exit"),
    pytest.param("success", "already-terminal", KeyboardInterrupt,
                 id="terminal-replay-keyboard-interrupt"),
    pytest.param("success", "already-terminal", SystemExit,
                 id="terminal-replay-system-exit"),
    pytest.param("recovery", "startup-recovery-abandoned", KeyboardInterrupt,
                 id="startup-recovery-abandoned-publisher-keyboard-interrupt"),
    pytest.param("recovery", "startup-recovery-abandoned", SystemExit,
                 id="startup-recovery-abandoned-publisher-system-exit"),
    pytest.param(
        "recovery", "startup-recovery-already-terminal", KeyboardInterrupt,
        id="startup-recovery-already-terminal-publisher-keyboard-interrupt"),
    pytest.param(
        "recovery", "startup-recovery-already-terminal", SystemExit,
        id="startup-recovery-already-terminal-publisher-system-exit"),
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


def _seal_startup_recovery_terminal(context: dict) -> None:
    """Leave one terminal manifest uncleaned for startup replay coverage."""
    manifest_path = Path(context["manifest"])
    cleanup.abandon_resource(manifest_path, context["process_resource_id"])
    manifest = cleanup.load_manifest(manifest_path)
    recovery_root = manifest_path.parent.parent / "recovery" / manifest_path.stem
    recovery_root.mkdir(parents=True, exist_ok=True)
    terminal_source = recovery_root / "terminal-state.json"
    handoff_source = recovery_root / "handoff.json"
    terminal_source.write_text(
        json.dumps({"owner": manifest["owner"], "outcome": "recovery"}) +
        "\n", encoding="utf-8")
    handoff_source.write_text(
        json.dumps({"owner": manifest["owner"], "trigger": "recovery"}) +
        "\n", encoding="utf-8")
    replay_source = (manifest_path.parent.parent / "publication" /
                     f"{manifest_path.stem}-startup.json")
    cleanup.write_publication_replay(
        replay_source, owner=manifest["owner"], outcome="recovery",
        source_revision=max(1, int(manifest["revision"])),
        source_fingerprint="d" * 64, trigger="recovery")
    cleanup.seal_terminal(
        manifest_path, outcome="recovery", evidence={
            "terminal-state": terminal_source,
            "handoff": handoff_source,
            "publication-replay": replay_source,
        })


@pytest.mark.parametrize(
    "outcome,publication_phase,publication_exception", TERMINAL_OUTCOME_CASES)
def test_cleanup_runs_on_every_terminal_outcome(
        tmp_path, outcome, publication_phase, publication_exception,
        monkeypatch):
    if publication_phase in {
            "startup-recovery-abandoned",
            "startup-recovery-already-terminal",
    }:
        recovery_kind = publication_phase.removeprefix("startup-recovery-")
        workspace = tmp_path / (
            f"{publication_phase}-{publication_exception.__name__.lower()}")
        workspace.mkdir()
        tokens = {
            ("abandoned", KeyboardInterrupt): "a" * 32,
            ("abandoned", SystemExit): "b" * 32,
            ("already-terminal", KeyboardInterrupt): "c" * 32,
            ("already-terminal", SystemExit): "d" * 32,
        }
        context = governed_commands.prepare_owned_cleanup(
            str(workspace), "agent:startup-recovery",
            run_id="run-startup-recovery", task_id="task-startup-recovery",
            attempt=1, token=tokens[(recovery_kind, publication_exception)])
        owned_root = workspace / "owned"
        artifact_id = _owned_file(
            Path(context["manifest"]), owned_root,
            f"{recovery_kind}.txt", evidence_ref="terminal-state")
        artifact_path = owned_root / f"{recovery_kind}.txt"
        if recovery_kind == "already-terminal":
            _seal_startup_recovery_terminal(context)
        interrupted = publication_exception(
            f"{publication_phase} publisher interrupted")
        publication_observations = []

        def interrupt_startup_publication(
                _workspace, *, outcome, **_kwargs):
            terminal = cleanup.load_manifest(context["manifest"])["terminal"]
            publication_observations.append({
                "outcome": outcome,
                "sealed_outcome": terminal["outcome"],
                "resource_live": artifact_path.is_file(),
            })
            raise interrupted

        recovered = governed_commands.recover_owned_cleanup(
            str(workspace), run_id="run-startup-recovery",
            task_id="task-startup-recovery", before_attempt=2,
            publisher=interrupt_startup_publication)
        assert len(recovered["recovered"]) == 1
        row = recovered["recovered"][0]
        receipt = row["cleanup_receipt"]
        assert row["action"] == (
            "failure-unwind" if recovery_kind == "abandoned" else
            "startup-recovery")
        if recovery_kind == "abandoned":
            assert row["error"] == \
                "startup recovered an abandoned older attempt"
        assert row["publication_result"] == {
            "status": "pending", "replay_required": True,
            "error": str(interrupted),
        }
        assert receipt["cleanup_status"] == "clean"
        assert receipt["original_outcome"] == "recovery"
        assert receipt["leak_count"] == recovered["leak_count"] == 0
        assert row["cleanup_evidence"] == \
            cleanup.cleanup_consumer_evidence(receipt)
        assert row["cleanup_evidence"]["original_outcome"] == "recovery"
        assert row["cleanup_evidence"]["leak_count"] == 0
        artifact_result = next(
            item for item in receipt["resources"]
            if item["resource_id"] == artifact_id)
        assert artifact_result["status"] == "cleaned"
        assert not artifact_path.exists()
        manifest = cleanup.load_manifest(context["manifest"])
        assert manifest["terminal"]["outcome"] == "recovery"
        assert {item["label"] for item in manifest["terminal"]["evidence"]} == {
            "terminal-state", "handoff", "publication-replay",
        }
        assert all(Path(item["sealed_path"]).is_file()
                   for item in manifest["terminal"]["evidence"])
        receipt_path = Path(context["manifest"]).with_name(
            Path(context["manifest"]).name + ".cleanup-receipt.json")
        assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
        assert publication_observations == [{
            "outcome": "recovery", "sealed_outcome": "recovery",
            "resource_live": True,
        }]

        if recovery_kind == "abandoned":
            # The triggering interruption remains primary when this same
            # abandoned-manifest unwind is entered from an interrupted launch.
            primary_context = governed_commands.prepare_owned_cleanup(
                str(workspace), "agent:startup-primary",
                run_id="run-startup-primary", task_id="task-startup-primary",
                attempt=1, token=("e" if publication_exception is
                                  KeyboardInterrupt else "f") * 32)
            primary_root = workspace / "primary-owned"
            primary_artifact_id = _owned_file(
                Path(primary_context["manifest"]), primary_root,
                "primary.txt", evidence_ref="terminal-state")
            primary = publication_exception("primary interruption")
            secondary = publication_exception("secondary publisher interrupt")

            def interrupt_secondary_publication(*_args, **_kwargs):
                raise secondary

            with pytest.raises(publication_exception) as caught:
                governed_commands.unwind_owned_failure(
                    primary_context, error=primary, outcome="interruption",
                    trigger="recovery",
                    publisher=interrupt_secondary_publication)
            assert caught.value is primary
            primary_result = primary.cleanup_result
            assert primary_result["publication_result"]["error"] == \
                str(secondary)
            assert primary_result["cleanup_receipt"][
                "original_outcome"] == "interruption"
            assert primary_result["cleanup_evidence"]["leak_count"] == 0
            assert next(
                item for item in primary_result["cleanup_receipt"]["resources"]
                if item["resource_id"] == primary_artifact_id
            )["status"] == "cleaned"
            assert not (primary_root / "primary.txt").exists()
            assert cleanup.load_manifest(primary_context["manifest"])[
                "terminal"]["outcome"] == "interruption"
        return

    if publication_phase is not None:
        workspace = tmp_path / (
            f"publication-{publication_phase}-" +
            publication_exception.__name__.lower())
        workspace.mkdir()
        contract_engine.activate(
            str(workspace), contract_engine.build_contract(
                "owned-cleanup-publication-interrupt",
                scope=[str(workspace)], tools=["exec_command"],
                plan_minted=True), snapshot=None)
        launched = governed_commands.execute(str(workspace), "launch", {
            "authorization": "agent:publication-interrupt",
            "run_id": "run-publication-interrupt",
            "task_id": "task-publication-interrupt", "attempt": 1,
            "argv": ["/bin/sleep", "5"],
        })
        manifest_path = launched["snapshot"]["owned_cleanup"]["manifest"]
        runtime = CommandRuntime(
            str(governed_commands.command_runtime_root(str(workspace))),
            workspace=str(workspace),
            authorization="agent:publication-interrupt")
        runtime.transition(launched["handle"], "succeeded", exit_code=0)
        if publication_phase == "already-terminal":
            terminal_result = governed_commands.execute(
                str(workspace), "wait", {
                    "authorization": "agent:publication-interrupt",
                    "handle": launched["handle"],
                    "consumer": "owned-cleanup:terminal-replay",
                    "timeout": 1,
                })
        interrupted = publication_exception(
            f"{publication_phase} publication interrupted")

        def interrupt_publication(*_args, **_kwargs):
            raise interrupted

        with monkeypatch.context() as publication_patch:
            publication_patch.setattr(
                governed_commands.owned_cleanup,
                ("replay_publication" if publication_phase == "fresh" else
                 "replay_terminal_publication"),
                interrupt_publication)
            with pytest.raises(publication_exception) as caught:
                if publication_phase == "fresh":
                    governed_commands.execute(str(workspace), "wait", {
                        "authorization": "agent:publication-interrupt",
                        "handle": launched["handle"],
                        "consumer": "owned-cleanup:fresh-publication",
                        "timeout": 1,
                    })
                else:
                    governed_commands.finalize_owned_result(
                        terminal_result, trigger="terminal")
        assert caught.value is interrupted
        cleanup_result = interrupted.cleanup_result
        assert cleanup_result["cleanup_receipt"]["cleanup_status"] == "clean"
        assert cleanup_result["cleanup_receipt"]["original_outcome"] == \
            "success"
        assert cleanup_result["cleanup_evidence"]["leak_count"] == 0
        assert cleanup_result["publication_result"] == {
            "status": "pending", "replay_required": True,
            "error": str(interrupted),
        }
        assert cleanup.load_manifest(manifest_path)["terminal"]["outcome"] == \
            "success"
        return

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

    if outcome == "interruption":
        # Preparation belongs to the same total unwind boundary as process
        # launch. Exercise both interruption-class outcomes after the real
        # manifest/reservation CAS, and prove the exact object is re-raised.
        original_prepare = governed_commands._prepare_owned_cleanup
        for index, interrupted in enumerate((
                KeyboardInterrupt("prepare interrupted"),
                SystemExit("prepare exited"))):
            interrupted_workspace = tmp_path / f"prepare-interrupt-{index}"
            interrupted_workspace.mkdir()
            contract_engine.activate(
                str(interrupted_workspace), contract_engine.build_contract(
                    "owned-cleanup-interrupt", scope=[
                        str(interrupted_workspace)], tools=["exec_command"],
                    plan_minted=True), snapshot=None)
            prepared = {}

            def interrupt_after_prepare(*args, **kwargs):
                prepared.update(original_prepare(*args, **kwargs))
                raise interrupted

            with monkeypatch.context() as interruption_patch:
                interruption_patch.setattr(
                    governed_commands, "_prepare_owned_cleanup",
                    interrupt_after_prepare)
                interruption_patch.setattr(
                    governed_commands.owned_cleanup, "replay_publication",
                    original_replay)
                with pytest.raises(type(interrupted)) as caught:
                    governed_commands.execute(
                        str(interrupted_workspace), "launch", {
                            "authorization": "agent:interrupt",
                            "run_id": "run-interrupt",
                            "task_id": f"task-interrupt-{index}",
                            "attempt": 1,
                            "argv": ["/usr/bin/printf", "never-launched"],
                        })
            assert caught.value is interrupted
            assert interrupted.cleanup_result[
                "cleanup_receipt"]["original_outcome"] == "interruption"
            assert interrupted.cleanup_result[
                "cleanup_evidence"]["leak_count"] == 0
            assert cleanup.load_manifest(prepared["manifest"])[
                "terminal"]["outcome"] == "interruption"

        # Deliberately interrupt the real checkpoint launcher after its owned
        # manifest exists. The checkpoint sandbox and every reservation must
        # unwind before the same KeyboardInterrupt propagates.
        checkpoint_workspace = tmp_path / "checkpoint-interrupt"
        checkpoint_workspace.mkdir()
        checkpoint_sandbox = (
            checkpoint_workspace / ".taskplane" / "checkpoint-sandbox" /
            "checkout")
        checkpoint_sandbox.mkdir(parents=True)
        checkpoint_interrupt = KeyboardInterrupt("checkpoint launch")
        checkpoint_authority = {
            "engine_bindings": {"governed_commands": {"path": "engine"}},
            "executable_binding": {"path": "/usr/bin/python3"},
            "runtime_environment": {},
            "fingerprint": "a" * 64,
        }
        checkpoint_spec = {"focused_proof": {"argv": ["python3", "proof"]},
                           "checkpoint_id": "cleanup-interrupt"}
        with monkeypatch.context() as interruption_patch:
            interruption_patch.setattr(
                governed_commands, "detached_process_groups_supported",
                lambda: True)
            interruption_patch.setattr(
                governed_commands,
                "_consume_semantic_checkpoint_authorization",
                lambda *_args, **_kwargs:
                    (checkpoint_spec, checkpoint_authority))
            interruption_patch.setattr(
                governed_commands, "_prepare_checkpoint_sandbox",
                lambda *_args, **_kwargs: str(checkpoint_sandbox))
            interruption_patch.setattr(
                governed_commands, "_recheck_regular_file_binding",
                lambda *_args, **_kwargs: None)
            interruption_patch.setattr(
                governed_commands.subprocess, "Popen",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    checkpoint_interrupt))
            interruption_patch.setattr(
                governed_commands.owned_cleanup, "replay_publication",
                original_replay)
            with pytest.raises(KeyboardInterrupt) as caught:
                governed_commands.execute(str(checkpoint_workspace),
                                            "checkpoint", {
                    "authorization": "checkpoint:interrupt",
                    "checkpoint_authority": "engine-minted-for-test",
                    "run_id": "run-checkpoint",
                    "task_id": "task-checkpoint", "attempt": 1,
                })
        assert caught.value is checkpoint_interrupt
        assert checkpoint_interrupt.cleanup_result.get("cleanup_error") is None
        assert checkpoint_interrupt.cleanup_result[
            "cleanup_receipt"]["original_outcome"] == "interruption"
        assert checkpoint_interrupt.cleanup_result[
            "cleanup_evidence"]["leak_count"] == 0
        assert not checkpoint_sandbox.parent.exists()


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

    # Exercise the real default publisher: it must authenticate a bound host
    # snapshot, deliver those exact canonical bytes, and derive its
    # acknowledgement from the read-back artifact rather than request echo.
    from taskplane import host_native, loop_status, storage, views

    dashboard_source = {
        "mode": "legacy", "status": "ready", "run_id": "cleanup-loop",
        "revision": "loop-revision", "target": "owned-cleanup",
        "state": {"goal": "cleanup", "step": "execute", "tasks": [],
                  "current_task": 0},
        "source_fingerprint": "e" * 64, "evidence": ["cleanup-source"],
    }

    def default_replay(case: str, revision: int, fingerprint: str):
        selected_workspace = tmp_path / case
        selected_workspace.mkdir()
        selected_manifest = _manifest(selected_workspace)
        selected_source = _publication(
            selected_manifest, selected_workspace / "publication.json",
            outcome="success", revision=revision)
        obligation = json.loads(selected_source.read_text(encoding="utf-8"))
        obligation["source_fingerprint"] = fingerprint
        obligation["fingerprint"] = cleanup._digest({
            key: value for key, value in obligation.items()
            if key != "fingerprint"})
        selected_source.write_text(
            json.dumps(obligation, sort_keys=True, separators=(",", ":")) +
            "\n", encoding="utf-8")
        return (selected_workspace, selected_source,
                cleanup.load_manifest(selected_manifest)["owner"])

    positive_workspace, positive_source, positive_owner = default_replay(
        "default-publication", 11, "d" * 64)
    with monkeypatch.context() as publication_patch:
        publication_patch.setattr(
            loop_status, "_select_dashboard_source",
            lambda _workspace: copy.deepcopy(dashboard_source))
        # A normal dashboard at the same source sequence may already be the
        # durable delivery head. The cleanup publication is its own revision
        # identity and must not collide with that incumbent snapshot.
        incumbent = loop_status.refresh_dashboard_snapshot(
            str(positive_workspace), event_type="incumbent", replay=True)
        incumbent_delivery = views.refresh_views(str(positive_workspace), {
            "outcome": "running", "dashboard_snapshot": incumbent})
        assert incumbent_delivery["dashboard"]["delivery"]["status"] == \
            "published"
        default_publication = cleanup.replay_publication(
            positive_source, workspace=str(positive_workspace),
            owner=positive_owner, outcome="success", mark_published=False)
    publisher_result = default_publication["publication"]
    expected_source = {
        "schema": cleanup.PUBLICATION_SOURCE_SCHEMA,
        "source_revision": 11, "source_fingerprint": "d" * 64,
    }
    source_verification = publisher_result["source_verification"]
    attestation = source_verification["attestation"]
    source_receipt = source_verification["receipt"]
    assert attestation["schema"] == cleanup.PUBLICATION_ATTESTATION_SCHEMA
    assert attestation["source"] == expected_source
    assert source_receipt["schema"] == cleanup.PUBLICATION_RECEIPT_SCHEMA
    assert source_receipt["source"] == expected_source
    assert source_receipt["attestation_fingerprint"] == \
        attestation["fingerprint"]
    canonical_snapshot = host_native.HostSurfaceSnapshot.from_dict(
        publisher_result["snapshot_publication"]["snapshot"])
    durable_snapshot = host_native.HostSurfaceSnapshot.from_dict(
        storage.load_dashboard_publication(str(positive_workspace))["current"])
    assert canonical_snapshot.to_dict() == durable_snapshot.to_dict()
    assert publisher_result["durable_publication"]["snapshot"] == \
        durable_snapshot.to_dict()
    canonical_event = host_native.HostSurfaceEvent.from_dict(
        publisher_result["snapshot_publication"]["event"])
    durable_events = json.loads((
        Path(storage.dashboard_snapshot_store_path(str(positive_workspace)))
        .parent / "events.json").read_text(encoding="utf-8"))["events"]
    assert canonical_event.to_dict() in durable_events
    assert attestation["snapshot_fingerprint"] == \
        durable_snapshot.fingerprint
    assert attestation["event_fingerprint"] == canonical_event.fingerprint
    delivered = publisher_result["dashboard_delivery"]["dashboard"][
        "delivery"]
    delivered_snapshot = host_native.HostSurfaceSnapshot.from_dict(json.loads(
        Path(delivered["artifacts"]["json"]["path"]).read_text(
            encoding="utf-8")))
    assert delivered_snapshot.to_dict() == durable_snapshot.to_dict()
    assert delivered["publication_receipt"]["snapshot"]["fingerprint"] == \
        delivered_snapshot.fingerprint
    assert source_receipt["snapshot_fingerprint"] == \
        delivered_snapshot.fingerprint

    # Named durable-return mismatch: substitute an authenticated return value
    # after loop_status committed its one canonical snapshot/event. The
    # obligation must remain pending and publication must refuse the mismatch.
    severed_workspace, severed_source, severed_owner = default_replay(
        "severed-snapshot", 12, "f" * 64)
    real_snapshot_refresh = loop_status.refresh_dashboard_snapshot

    def durable_snapshot_mismatch(*args, **kwargs):
        publication = real_snapshot_refresh(*args, **kwargs)
        snapshot = host_native.HostSurfaceSnapshot.from_dict(
            publication["snapshot"])
        values = dict(snapshot.values)
        values["durable_mismatch_severance"] = True
        conflict = host_native.HostSurfaceSnapshot.create(
            workflow_id=snapshot.workflow_id, run_id=snapshot.run_id,
            target=snapshot.target, revision=snapshot.revision,
            sequence=snapshot.sequence, stage=snapshot.stage,
            state=snapshot.state, values=values, evidence=snapshot.evidence,
            safe_actions=snapshot.safe_actions)
        event = host_native.HostSurfaceEvent.from_snapshot(
            conflict, event_type=publication["event"]["event_type"])
        return {**publication, "snapshot": conflict.to_dict(),
                "event": event.to_dict(),
                "surfaces": {key: conflict.fingerprint
                             for key in publication["surfaces"]}}

    with monkeypatch.context() as publication_patch:
        publication_patch.setattr(
            loop_status, "_select_dashboard_source",
            lambda _workspace: copy.deepcopy(dashboard_source))
        publication_patch.setattr(
            loop_status, "refresh_dashboard_snapshot",
            durable_snapshot_mismatch)
        with pytest.raises(cleanup.OwnedCleanupError,
                           match="durable dashboard snapshot does not match"):
            cleanup.replay_publication(
                severed_source, workspace=str(severed_workspace),
                owner=severed_owner, outcome="success")
    assert json.loads(severed_source.read_text(encoding="utf-8"))[
        "status"] == "pending"

    # Named delivery substitution: replace canonical JSON with a different,
    # authenticated HostSurfaceSnapshot and internally correct artifact hash.
    # Cross-checking against durable loop_status truth must still refuse it.
    delivery_workspace, delivery_source, delivery_owner = default_replay(
        "severed-delivery", 14, "9" * 64)
    real_views_refresh = views.refresh_views

    def substitute_delivery(*args, **kwargs):
        result = real_views_refresh(*args, **kwargs)
        delivery = result["dashboard"]["delivery"]
        artifact = delivery["artifacts"]["json"]
        original = host_native.HostSurfaceSnapshot.from_dict(json.loads(
            Path(artifact["path"]).read_text(encoding="utf-8")))
        substituted = host_native.HostSurfaceSnapshot.create(
            workflow_id=original.workflow_id, run_id=original.run_id,
            target=original.target, revision=original.revision + ":substitute",
            sequence=original.sequence, stage=original.stage,
            state=original.state, values=original.values,
            evidence=original.evidence, safe_actions=original.safe_actions)
        payload = views.canonical_dashboard_bytes(substituted.to_dict())
        Path(artifact["path"]).write_bytes(payload)
        artifact["bytes"] = len(payload)
        artifact["sha256"] = cleanup.file_sha256(artifact["path"])
        delivery["semantic_bytes"] = len(payload)
        delivery["semantic_sha256"] = artifact["sha256"]
        return result

    with monkeypatch.context() as publication_patch:
        publication_patch.setattr(
            loop_status, "_select_dashboard_source",
            lambda _workspace: copy.deepcopy(dashboard_source))
        publication_patch.setattr(views, "refresh_views", substitute_delivery)
        with pytest.raises(cleanup.OwnedCleanupError,
                           match="delivery substituted the durable snapshot"):
            cleanup.replay_publication(
                delivery_source, workspace=str(delivery_workspace),
                owner=delivery_owner, outcome="success")
    assert json.loads(delivery_source.read_text(encoding="utf-8"))[
        "status"] == "pending"

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
