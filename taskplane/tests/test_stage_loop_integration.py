"""The legacy loop opts into bounded v4 stage dispatch explicitly."""
from __future__ import annotations

import copy
import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import loop, taskplane_lite
from taskplane.settings import load_settings
from tests.root_session_fixture import open_delivery_root


def _content_inventory(root: Path) -> dict[str, str]:
    """Bind a no-mutation assertion to names and semantic file content."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("stage loop\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    return str(workspace)


def _initialize_real_new_run(tmp_path, monkeypatch, *, stage_kind="product",
                             stage_id=None,
                             goal="exercise the real stage loop",
                             parallel=False):
    from taskplane import requirements as reqs
    from taskplane.tests.test_stage_cross_host import (
        _real_loop_stage, _record_bootstrap_requirement)

    tmp_path.mkdir(parents=True, exist_ok=True)
    stage_id = stage_id or f"stage-{stage_kind}-loop-root"
    workspace, store, stage = _real_loop_stage(
        tmp_path, stage_kind=stage_kind, stage_id=stage_id)
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    requirement = reqs.amend_requirement(
        str(workspace), requirement["id"],
        nfr={
            "reliability": "stage transitions fail closed without evidence",
            "security": "stage output stays inside governed storage",
            "architecture": "the immutable stage aggregate owns transitions",
        })
    assert requirement["id"] == stage["requirement"]["id"] == "R-0004"
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    initialized = loop.init(
        str(workspace), goal, requirement_id=requirement["id"],
        parallel=parallel, by=stage["authority"]["actor"])
    assert "error" not in initialized, initialized
    return str(workspace), store, stage, initialized


def _start_real_stage_loop(tmp_path, monkeypatch, *, stage_kind="product",
                           stage_id=None, goal="exercise the real stage loop"):
    workspace, store, stage, _initialized = _initialize_real_new_run(
        tmp_path, monkeypatch, stage_kind=stage_kind, stage_id=stage_id,
        goal=goal)
    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": f"start-{stage['stage_id']}",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in started, started
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    return str(workspace), store, stage, started


def _successor_handoff(ws, store, receipt):
    successor_head = receipt["result"]["successor_head"]
    successor_id = successor_head["summary"]["stage_id"]
    manifest = store.load(successor_head["summary"]["run_id"])
    successor = store.read_stage_object(
        manifest["run_id"], manifest["stage_heads"][successor_id]["object"])
    _entities, lifecycle = loop._stage_lifecycle(
        ws, store, manifest, successor["authority"])
    handoff = loop._verified_stage_handoff(
        lifecycle, store, manifest, successor)
    return successor, handoff


def _handoff_payload_text(ws, handoff):
    from taskplane import review_evidence

    artifact_store = review_evidence.ArtifactStore(ws)
    payloads = [artifact_store.read(reference) for reference in
                handoff["selected_artifacts"] +
                handoff["evidence_references"]]
    return json.dumps(payloads, sort_keys=True)


def test_phase_export_refuses_forged_build_green_receipt_evidence(
        monkeypatch) -> None:
    monkeypatch.setattr(
        loop.phase_handoff, "create_phase_handoff", lambda **values: values)
    material = {
        "repository": {}, "source": {}, "requirement": {},
        "design": {}, "plan": {},
        "obligations": [{"id": "T-004"}],
        "tasks": [{"id": "T-004"}],
        "contracts": [], "acceptance": [], "selected_artifacts": [],
        "authority_receipts": [], "progress_receipts": [],
        "lineage": {"predecessor_handoff_fingerprint": None,
                    "predecessor_receipt_head": None},
        "exclusions": [],
    }
    durable = {"phase": "build", "state": "terminal", "outcome": "done"}

    def export(receipt_evidence=None):
        return loop.project_phase_export(
            material, phase="build", outcome="done",
            durable_progress=durable, receipt_evidence=receipt_evidence)

    def green_receipt(producer):
        return loop.phase_handoff.create_progress_receipt(
            producer=producer, sequence=1, phase="build",
            obligation_id="T-004", task_id="T-004", status="green",
            predecessor_receipt_fingerprint=None,
            checkpoint_receipt_digest="a" * 64,
            integration_receipt_fingerprint="b" * 64)

    with pytest.raises(ValueError, match="Build.*receipt evidence"):
        export({"T-004": {
            "task_id": "T-004", "checkpoint_receipt_digest": "a" * 64,
            "integration_receipt_fingerprint": "b" * 64}})

    with pytest.raises(ValueError, match="Build completion lacks"):
        export()

    forged = green_receipt("engine:taskplane.loop/v1")
    material["progress_receipts"] = [forged]
    material["lineage"]["predecessor_receipt_head"] = forged["fingerprint"]
    with pytest.raises(ValueError, match="exact phase-pickup/BUILD-C"):
        export()

    admitted = green_receipt("engine:taskplane.phase-pickup/v1")
    material["progress_receipts"] = [admitted]
    material["lineage"]["predecessor_receipt_head"] = admitted["fingerprint"]
    exported = export()
    assert exported["progress"] == {
        "completed": ["T-004"], "remaining": []}
    assert exported["progress_receipts"] == material["progress_receipts"]


def test_disabled_loop_stage_context_does_not_open_a_locator(
        monkeypatch) -> None:
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: (_ for _ in ()).throw(AssertionError("locator opened")))

    assert loop._stage_loop_context("/repo") is None


def test_unmigrated_loop_stage_context_is_a_read_only_noop(monkeypatch) -> None:
    manifest = {"schema": "taskplane.run/v3", "run_id": "legacy", "revision": 9}
    before = copy.deepcopy(manifest)

    class Store:
        def load(self, _run_id):
            return manifest

    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "legacy", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    assert loop._stage_loop_context("/repo") is None
    assert manifest == before


def test_disabled_migrated_loop_refuses_mutation_without_state_change(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve the migrated run")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("migrated run\n", encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "run-r0004",
        "revision": 4, "stage_heads": {}, "stage_operations": {},
        "active_stage_projection": {
            "active_stage_ids": [], "foreground_stage_id": None,
        },
    }

    class Store:
        @staticmethod
        def load(run_id):
            assert run_id == "run-r0004"
            return copy.deepcopy(manifest)

    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "run-r0004", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "disabled" in refused["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize("corruption", ["locator", "store"])
def test_corrupt_stage_locator_or_store_refuses_without_singleton_mutation(
        tmp_path, monkeypatch, corruption) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "corrupt-state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve singleton on stage metadata corruption")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nDo not mutate through corrupt stage state.\n",
        encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    state_path = Path(loop._loop_path(ws))
    before_bytes = state_path.read_bytes()
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    if corruption == "locator":
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: (_ for _ in ()).throw(
                RuntimeError("corrupt stage locator")))
    else:
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: {"run_id": "run-corrupt", "home": "/run-store"})

        class CorruptStore:
            @staticmethod
            def load(_run_id):
                raise RuntimeError("corrupt stage manifest")

        monkeypatch.setattr(
            loop, "_stage_store", lambda *_args: CorruptStore())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "stage" in refused["error"].lower()
    assert "corrupt" in refused["error"].lower() or \
        "unavailable" in refused["error"].lower()
    assert loop.load(ws) == before
    assert state_path.read_bytes() == before_bytes


def test_new_run_can_start_after_normal_loop_init_and_replay_once(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, first = _start_real_stage_loop(
        tmp_path / "explicit-replay", monkeypatch,
        stage_id="stage-product-explicit-replay")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    request = {
        "schema": "taskplane.stage-command/v1", "stage": stage,
        "expected_revision": 1,
        "operation_id": "start-stage-product-explicit-replay",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    }
    second = loop.stage_command(ws, "start", request)

    assert second == first
    manifest = store.load(stage["run_id"])
    assert list(manifest["stage_operations"]).count(
        "start-stage-product-explicit-replay") == 1


def test_pristine_new_run_next_bootstraps_once_before_public_dispatch(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    worker_clock = iter(range(1_800_000_000, 1_800_001_000))
    monkeypatch.setattr(
        taskplane_lite._time, "time", lambda: next(worker_clock))
    observed = []
    real_dispatch = loop._stage_dispatch

    def observe_committed_root(store, lifecycle, receipt, stage, **kwargs):
        manifest = store.load(stage["run_id"])
        summary = manifest["stage_heads"][stage["stage_id"]]["summary"]
        assert manifest["schema"] == "taskplane.run/v4"
        assert summary["state"] == "active"
        assert manifest["stage_operations"][receipt["operation_id"]] == receipt
        observed.append(stage["stage_id"])
        return real_dispatch(
            store, lifecycle, receipt, stage, **kwargs)

    monkeypatch.setattr(loop, "_stage_dispatch", observe_committed_root)
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace, store, initial = _real_pristine_run(pristine)
    requirement = _record_bootstrap_requirement(workspace)
    ws = str(workspace)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "bootstrap Product without caller stage JSON", parallel=True,
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert "error" not in initialized, initialized
    specs = workspace / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nBootstrap Product before any stage dispatch.\n",
        encoding="utf-8")
    refused_wave = loop.wave(ws)

    assert "first `loop next`" in refused_wave["error"]
    assert store.load(initial["run_id"])["schema"] == "taskplane.run/v3"
    assert initialized["_stage_native_new_run_pristine"] is True
    assert initialized["_stage_native_root_authority"]["actor"] == \
        "human:vdemkiv"

    first = loop.next_action.__wrapped__(ws)

    assert "error" not in first, first
    dispatch = first["stage_runtime_dispatch"]
    root_id = dispatch["startup"]["stage_id"]
    assert observed == [root_id]
    manifest = store.load(initial["run_id"])
    assert manifest["schema"] == "taskplane.run/v4"
    assert manifest["stage_heads"][root_id]["summary"]["state"] == "active"
    assert loop.load(ws)["_stage_run_binding"]["root_stage_id"] == root_id

    second = loop.next_action.__wrapped__(ws)

    assert "error" not in second, second
    assert second["stage_runtime_dispatch"]["startup"]["stage_id"] == root_id
    assert store.load(initial["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [root_id]


def test_new_run_refuses_unmarked_existing_singleton(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import _real_loop_stage

    unmarked_root = tmp_path / "unmarked"
    unmarked_root.mkdir()
    unmarked_ws, unmarked_store, unmarked_stage = _real_loop_stage(
        unmarked_root, stage_kind="product",
        stage_id="stage-product-unmarked-root")
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    loop.init(str(unmarked_ws), "pre-existing singleton")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")

    refused = loop.stage_command(str(unmarked_ws), "start", {
        "schema": "taskplane.stage-command/v1", "stage": unmarked_stage,
        "expected_revision": 1, "operation_id": "start-too-late",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": unmarked_stage["authority"],
    })

    assert refused["enabled"] is False
    assert "existing singleton" in refused["error"] or \
        "pristine" in refused["error"]
    assert unmarked_store.load(unmarked_stage["run_id"])["schema"] == \
        "taskplane.run/v3"


def test_dispatch_uses_one_deterministic_verified_resume_attempt(
        monkeypatch) -> None:
    calls = []
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-build-001",
        "fingerprint": "f" * 64, "stage_kind": "build",
        "selected_artifacts": [],
    }

    class Lifecycle:
        def resume_stage(self, run_id, **kwargs):
            calls.append((run_id, kwargs))
            return {"operation": "resume_stage", "result": {
                "attempt_id": kwargs["attempt_id"]}}

    context = {
        "store": object(), "manifest": {"revision": 7},
        "lifecycle": Lifecycle(), "stage": stage,
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_stage_dispatch",
        lambda _store, _lifecycle, receipt, current, **kwargs: {
            "schema": "taskplane.stage-dispatch/v1",
            "receipt": receipt["operation"],
            "stage": current["stage_id"],
            "attempt": kwargs["attempt_id"],
            "scope": kwargs["declared_scope"],
        })
    state = {"step": "execute"}
    scope = {"scope_paths": ["taskplane/loop.py"],
             "out_of_scope_paths": []}

    first = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)
    second = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)

    assert first == second
    assert first["schema"] == "taskplane.stage-dispatch/v1"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][1]["operation_id"].startswith("loop-dispatch-")
    assert calls[0][1]["attempt_id"].startswith("attempt-")


def test_same_stage_kind_transition_does_not_mutate_stage_store(
        monkeypatch) -> None:
    monkeypatch.setattr(
        loop, "_stage_loop_context",
        lambda _ws: (_ for _ in ()).throw(AssertionError("store opened")))

    assert loop._stage_loop_transition(
        "/repo", {"step": "design_approval"},
        from_step="design", to_step="design_approval") is None


def test_terminal_transition_replays_the_existing_operation(monkeypatch) -> None:
    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    receipt = {"schema": "taskplane.stage-operation-receipt/v1",
               "operation_id": "loop-transition-" + "a" * 32}
    context = {
        "run_id": "run-r0004", "store": object(), "stage": None,
        "lifecycle": None, "stage_entities": StageEntities,
        "manifest": {"stage_operations": {
            receipt["operation_id"]: receipt}},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(loop.tp, "verify_stage_receipt", lambda value: value)

    assert loop._stage_loop_transition(
        "/repo", {"step": "done"},
        from_step="retro", to_step="done") is receipt


def test_terminal_transition_refuses_predecessor_input_as_completion_evidence(
        monkeypatch) -> None:
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-retro-001",
        "fingerprint": "f" * 64, "stage_kind": "retro",
        "deliverables": ["retrospective"],
        "authority": {"actor": "human:owner"},
        "created_at": "2026-08-21T20:00:00Z",
    }

    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    class Lifecycle:
        @staticmethod
        def terminalize(*_args, **_kwargs):
            raise AssertionError("predecessor input evidence was reused")

    context = {
        "run_id": stage["run_id"], "store": object(), "stage": stage,
        "lifecycle": Lifecycle(), "stage_entities": StageEntities,
        "manifest": {"revision": 7, "stage_operations": {}},
    }
    predecessor_handoff = {
        "evidence_references": [{
            "schema": "taskplane.artifact-reference/v1",
            "kind": "input-evidence", "fingerprint": "e" * 64,
        }],
        "authorization": {"authorized_at": "2026-08-21T19:00:00Z"},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_verified_stage_handoff", lambda *_a: predecessor_handoff)

    with pytest.raises(ValueError, match="completion"):
        loop._stage_loop_transition(
            "/repo", {"step": "done"},
            from_step="retro", to_step="done")


def test_selection_stage_failure_rolls_back_the_legacy_choice(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "choose one bounded variant", parallel=True)
    state.update({
        "step": "selection", "ab": True,
        "baseline": loop.tp.git_head(ws),
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "selection", "to_step": "em"}
        raise RuntimeError("selection stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.select(ws, "A")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize(
    ("decision", "to_step"), [("retry", "fix"), ("abort", "failed")])
def test_resolve_stage_failure_rolls_back_retry_or_abort(
        tmp_path, monkeypatch, decision, to_step) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "resolve one bounded escalation")
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "escalated", "to_step": to_step}
        raise RuntimeError("recovery stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.resolve(ws, decision)

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_replan_stage_failure_preserves_the_frozen_plan(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "replan one bounded task")
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "execute", "to_step": "plan"}
        raise RuntimeError("replan stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.replan(ws, by="human:owner", reason="invalid task graph")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_retro_attaches_one_idempotent_terminal_receipt(monkeypatch) -> None:
    receipt = {"operation": "terminalize", "operation_id": "retro-done"}
    monkeypatch.setattr(
        loop.retro_engine, "run", lambda *_args, **_kwargs: {"goal": "done"})
    monkeypatch.setattr(loop, "load", lambda _ws: {"step": "done"})
    calls = []

    def transition(*_args, **kwargs):
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(loop, "_stage_loop_transition", transition)

    first = loop.retro("/repo")
    second = loop.retro("/repo")

    assert first["stage_transition"] == receipt
    assert second == first
    assert calls == [
        {"from_step": "retro", "to_step": "done"},
        {"from_step": "retro", "to_step": "done"},
    ]


def test_next_action_attaches_stage_runtime_dispatch(tmp_path, monkeypatch) \
        -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    marker = {"schema": "taskplane.stage-dispatch/v1", "startup": {}}
    monkeypatch.setattr(loop, "_stage_loop_dispatch", lambda *_a, **_k: marker)

    result = loop.next_action.__wrapped__(ws)

    assert result["step"] == "pm"
    assert result["stage_runtime_dispatch"] is marker


def test_wave_emits_native_intent_without_stage_runtime_dispatch(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "build the bounded handoff", spec_path="spec.md",
                      parallel=True)
    state.update({
        "step": "execute",
        "tasks": [{"id": "t01", "scope": ["README.md"],
                   "tests": "true", "deps": [], "status": "pending"}],
    })
    loop.save(ws, state)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native delivery must not use StageLifecycle")

    monkeypatch.setattr(loop, "_stage_loop_dispatch", forbidden)
    monkeypatch.setattr(loop, "_stage_loop_wave_dispatches", forbidden)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert len(result["wave"]) == 1
    assert result["wave"][0]["dispatch_intent"]["intent_id"]
    assert "stage_runtime_dispatch" not in result["wave"][0]
    assert result["wait_invocation"]["outstanding_members"] == ["t01"]
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_emits_one_native_set_without_execution_roots(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)

    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Taskplane child-stage scheduler was invoked")))

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == [
        "t01", "t02"]
    assert len({entry["dispatch_intent"]["intent_id"]
                for entry in result["wave"]}) == 2
    assert result["wait_invocation"]["outstanding_members"] == [
        "t01", "t02"]
    assert result["held"] == []
    encoded = json.dumps(result, sort_keys=True).lower()
    assert "stage_runtime_dispatch" not in encoded
    assert "execution_root" not in encoded
    assert "tranche" not in encoded
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_honors_configured_build_concurrency(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)
    effective = load_settings(environment={})
    capped = replace(
        effective, build=replace(effective.build, concurrency=1))
    monkeypatch.setattr(
        loop.operational_settings, "load_settings", lambda **_kwargs: capped)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == ["t01"]
    assert result["held"] == [{
        "task": "t02",
        "reason": "configured build concurrency cap — next wave",
        "shared_owner": "settings",
    }]


def test_real_wave_recovers_task_bindings_after_post_split_crash(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "split-recovery", monkeypatch, stage_kind="build",
        stage_id="stage-build-split-parent")
    state = loop.load(ws)
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_mutate = loop.mutate

    @contextlib.contextmanager
    def crash_before_binding_commit(_workspace):
        raise RuntimeError("crash after split before singleton binding")
        yield

    monkeypatch.setattr(loop, "mutate", crash_before_binding_commit)
    with pytest.raises(RuntimeError, match="after split"):
        loop._stage_loop_wave_dispatches(ws, state, ready)
    split = store.load(stage["run_id"])
    assert len(split["active_stage_projection"]["active_stage_ids"]) == 2
    assert "_stage_bindings" not in loop.load(ws)

    monkeypatch.setattr(loop, "mutate", real_mutate)
    recovered = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(recovered) == {"t01", "t02"}
    bindings = loop.load(ws)["_stage_bindings"]
    child_ids = {bindings[task_id]["build"] for task_id in recovered}
    assert len(child_ids) == 2
    assert child_ids == set(
        store.load(stage["run_id"])["active_stage_projection"][
            "active_stage_ids"])
    assert {
        dispatch["startup"]["stage_id"] for dispatch in recovered.values()
    } == child_ids


def test_gate_rolls_back_on_stage_failure_then_returns_transition_receipt(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("bounded handoff\n", encoding="utf-8")
    monkeypatch.setattr(
        loop, "_stage_loop_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stage refused")))

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "stage-native loop transition failed closed" in refused["error"]
    assert loop.load(ws)["step"] == "pm"

    receipt = {"operation": "terminalize_and_start",
               "operation_id": "pm-to-plan"}
    monkeypatch.setattr(
        loop, "_stage_loop_transition", lambda *_a, **_k: receipt)
    accepted = loop.gate.__wrapped__(ws, "pass")

    assert accepted["stage_transition"] is receipt
    assert loop.load(ws)["step"] == "plan"


def test_real_product_gate_and_plan_approval_seal_exact_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "gate-approve", monkeypatch,
        stage_id="stage-product-gate-root")
    spec_path = "specs/spec.md"
    spec = "# Product\n\nShip the bounded stage journey.\n"
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(spec, encoding="utf-8")

    gated = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in gated, gated
    assert gated["step"] == "plan"
    assert gated["stage_transition"]["operation"] == \
        "terminalize_and_start"
    _plan_stage, product_handoff = _successor_handoff(
        ws, store, gated["stage_transition"])
    product_payloads = _handoff_payload_text(ws, product_handoff)
    assert spec_path in product_payloads
    assert hashlib.sha256(spec.encode()).hexdigest() in product_payloads

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    plan_text = "# Plan\n\nImplement the bounded handoff.\n"
    tasks_value = {"tasks": [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "pending",
    }]}
    (plan_dir / "plan.md").write_text(plan_text, encoding="utf-8")
    (plan_dir / "tasks.json").write_text(
        json.dumps(tasks_value, sort_keys=True) + "\n", encoding="utf-8")
    state = loop.load(ws)
    state.update({
        "step": "plan_approval", "tasks": tasks_value["tasks"],
        "graph_dor": {"ready": True, "blockers": []},
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "_design_current_errors", lambda *_a: [])
    monkeypatch.setattr(loop, "_refinement_report", lambda *_a: [])
    monkeypatch.setattr(loop.tp, "plan_task_id_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_consolidated_enabled", lambda: False)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    approved = loop.approve(ws, by="human:owner")

    assert "error" not in approved, approved
    assert approved["step"] == "execute"
    assert approved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    build_stage, plan_handoff = _successor_handoff(
        ws, store, approved["stage_transition"])
    plan_payloads = _handoff_payload_text(ws, plan_handoff)
    assert build_stage["stage_kind"] == "build"
    for path, content in (
            ("plan/plan.md", plan_text),
            ("plan/tasks.json", json.dumps(tasks_value, sort_keys=True) + "\n")):
        assert path in plan_payloads
        assert hashlib.sha256(content.encode()).hexdigest() in plan_payloads


@pytest.mark.parametrize(
    ("stage_kind", "from_step", "to_step", "outputs"), [
        ("design", "design", "plan", {
            "design/design.md": "# Design\n\nA bounded immutable stage.\n",
            "design/contract.json": "{\"schema\":\"test-design\"}\n",
        }),
    ])
def test_real_stage_completion_seals_design_outputs(
        tmp_path, monkeypatch, stage_kind, from_step, to_step, outputs) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / stage_kind, monkeypatch, stage_kind=stage_kind,
        stage_id=f"stage-{stage_kind}-output-root")
    workspace = Path(ws)
    for relative, content in outputs.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    state = loop.load(ws)
    state["step"] = from_step
    loop.save(ws, state)

    completion = loop._stage_loop_gate_completion(
        ws, state, step=from_step, outcome="pass")
    receipt = loop._stage_loop_transition(
        ws, state, from_step=from_step, to_step=to_step,
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    for relative, content in outputs.items():
        assert relative in payloads
        assert hashlib.sha256(content.encode()).hexdigest() in payloads


def test_real_build_completion_propagates_exact_target_commit_and_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "build-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-output-root")
    workspace = Path(ws)
    build_text = "stage journey\nreal build output\n"
    (workspace / "README.md").write_text(build_text, encoding="utf-8")
    target_commit = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running", "target_commit": target_commit,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(
        loop.runtime_eval, "guide_loop",
        lambda *_a, **_k: {"status": "on_path", "recovered": False})
    submitted = loop.submit(ws, "pass")
    assert "error" not in submitted, submitted
    state = loop.load(ws)

    completion = loop._stage_loop_gate_completion(
        ws, state, step="execute", outcome="pass",
        submission=submitted["submission"])
    receipt = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="evaluate",
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "evaluate"
    assert handoff["target"] is not None
    assert handoff["commit"] is not None
    assert handoff["commit"]["sha"] == target_commit
    assert handoff["commit"]["target_fingerprint"] == \
        handoff["target"]["fingerprint"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "README.md" in payloads
    assert hashlib.sha256(build_text.encode()).hexdigest() in payloads
    assert stage["authority"]["target_revision"] == target_commit


def test_retro_retries_sealed_report_after_real_terminalization_failure(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "retro-retry", monkeypatch, stage_kind="retro",
        stage_id="stage-retro-retry-root")
    state = loop.load(ws)
    state["step"] = "retro"
    loop.save(ws, state)
    report = {
        "goal": state["goal"],
        "graph_true_up": {"changed": False},
        "evaluator_summary": loop.retro_engine.evaluator_summary([]),
    }
    computations = []

    def sealed_retro(workspace, *, load_state, mutate_state, **_kwargs):
        current = load_state(workspace)
        if (current.get("retro") or {}).get("status") == "complete":
            return report
        computations.append("computed")
        with mutate_state(workspace) as locked:
            locked["retro"] = {
                "id": "retro-001", "status": "complete", "report": report,
            }
            locked["step"] = "done"
        return report

    real_lifecycle_factory = loop._stage_lifecycle
    terminal_attempts = []

    class FailOnceLifecycle:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def terminalize(self, *args, **kwargs):
            terminal_attempts.append(kwargs["operation_id"])
            if len(terminal_attempts) == 1:
                raise RuntimeError("simulated terminal store interruption")
            return self._wrapped.terminalize(*args, **kwargs)

    def lifecycle_factory(*args, **kwargs):
        entities, lifecycle = real_lifecycle_factory(*args, **kwargs)
        return entities, FailOnceLifecycle(lifecycle)

    monkeypatch.setattr(loop.retro_engine, "run", sealed_retro)
    monkeypatch.setattr(loop, "_stage_lifecycle", lifecycle_factory)

    refused = loop.retro(ws)
    prepared = loop.load(ws)

    assert "stage-native" in refused["error"]
    assert prepared["step"] == "retro"
    assert prepared["_retro_terminal_step"] == "done"
    assert prepared["retro"]["status"] == "complete"
    assert prepared["retro"]["report"] == report
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [stage["stage_id"]]

    completed = loop.retro(ws)

    assert "error" not in completed, completed
    assert completed["stage_transition"]["operation"] == "terminalize"
    assert loop.load(ws)["step"] == "done"
    assert "_retro_terminal_step" not in loop.load(ws)
    assert computations == ["computed"]
    assert len(terminal_attempts) == 2
    assert terminal_attempts[0] == terminal_attempts[1]
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == []


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_missing_locator_refuses_mutation_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / mode, monkeypatch,
        stage_id=f"stage-product-bound-{mode}")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nPreserve a bound v4 singleton.\n",
        encoding="utf-8")
    state = loop.load(ws)
    assert state["_stage_run_binding"]["run_schema"] == \
        "taskplane.run/v4"
    state_path = Path(loop._loop_path(ws))
    before = state_path.read_bytes()

    state_root = state_path.parents[2]
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator", lambda _ws: None)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "locator" in refused["error"].lower()
    assert state_path.read_bytes() == before


def test_selection_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "selection-output", monkeypatch, stage_kind="evaluate",
        stage_id="stage-evaluate-selection-root")
    revision = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "selection", "ab": True, "baseline": revision,
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    selected = loop.select(ws, "A", note="ship the bounded winner")

    assert "error" not in selected, selected
    assert selected["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, selected["stage_transition"])
    assert successor["stage_kind"] == "engineering"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-selection-result/v1" in payloads
    assert "variant-a" in payloads
    assert "ship the bounded winner" in payloads


def test_resolve_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-output", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-root")
    state = loop.load(ws)
    evidence = {"selector": "public acceptance journey", "returncode": 1}
    product_failure = loop.failure_routing.route_failure_records([{
        "schema": "taskplane.failure-record/v1",
        "id": "failure-t01-product",
        "source": "evaluate",
        "stage": "evaluate",
        "repro": "public acceptance journey still fails",
        "evidence": evidence,
        "evidence_digest": loop.failure_routing.evidence_digest(evidence),
        "class": "product",
        "reason": "the current product behavior violates acceptance",
        "owner": "task:t01",
        "cluster": "acceptance",
        "route": "fix",
        "candidate": {"id": "t01@candidate", "fingerprint": "b" * 64},
    }])
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3, "failure_routing": product_failure,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    assert resolved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "build"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "retry"' in payloads
    assert '"resulting_step": "fix"' in payloads


def test_unclassified_escalated_retry_cannot_enter_product_fix(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-unclassified", monkeypatch,
        stage_kind="engineering",
        stage_id="stage-engineering-resolve-unclassified")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "evaluate"
    payloads = _handoff_payload_text(ws, handoff)
    assert '"resulting_step": "evaluate"' in payloads


def test_replan_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-root")
    state = loop.load(ws)
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    replanned = loop.replan(
        ws, by="human:owner", reason="replace the invalid task graph")

    assert "error" not in replanned, replanned
    manifest = store.load("run-cross-host-loop")
    receipts = [receipt for receipt in manifest["stage_operations"].values()
                if receipt.get("operation") == "terminalize_and_start"]
    assert len(receipts) == 1
    successor, handoff = _successor_handoff(ws, store, receipts[0])
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-replan-result/v1" in payloads
    assert "replace the invalid task graph" in payloads
    assert '"to_step": "plan"' in payloads


@pytest.mark.parametrize(
    "source_case", ["absolute", "outside", "intermediate-symlink"])
def test_stage_output_sealing_rejects_untrusted_sources_without_capture(
        tmp_path, monkeypatch, source_case) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / source_case, monkeypatch, stage_kind="engineering",
        stage_id=f"stage-engineering-hostile-{source_case}")
    workspace = Path(ws)
    if source_case == "absolute":
        source = str(workspace / "README.md")
    elif source_case == "outside":
        outside = workspace.parent / "outside.txt"
        outside.write_text("outside stage output\n", encoding="utf-8")
        source = "../outside.txt"
    else:
        outside = workspace.parent / "linked-output"
        outside.mkdir()
        (outside / "secret.txt").write_text(
            "symlinked stage output\n", encoding="utf-8")
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
        source = "linked/secret.txt"

    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    artifact_store = review_evidence.ArtifactStore(ws)
    before = artifact_store.references("stage-output")
    manifest_before = copy.deepcopy(store.load(stage["run_id"]))
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": source, "required": True,
    }]

    with pytest.raises(ValueError):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert artifact_store.references("stage-output") == before
    assert store.load(stage["run_id"]) == manifest_before


def test_replan_transition_identity_includes_each_predecessor_head(
        tmp_path, monkeypatch) -> None:
    ws, _store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-heads", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-head-one")
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running",
        }],
    })
    loop.save(ws, state)
    replan_completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-replan-result/v1",
        step="execute", outcome="replan",
        result={"from_step": "execute", "to_step": "plan",
                "by": "human:owner", "reason": "replace invalid graph"})

    first = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)
    state.update({"step": "plan", "tasks": None, "current_task": 0})
    loop.save(ws, state)

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.md").write_text(
        "# Plan\n\nTry the corrected graph.\n", encoding="utf-8")
    tasks = [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "running",
    }]
    (plan_dir / "tasks.json").write_text(
        json.dumps({"tasks": tasks}, sort_keys=True) + "\n",
        encoding="utf-8")
    state.update({"step": "plan", "tasks": tasks, "current_task": 0})
    loop.save(ws, state)
    plan_completion = loop._stage_loop_gate_completion(
        ws, state, step="plan", outcome="pass")
    advanced = loop._stage_loop_transition(
        ws, state, from_step="plan", to_step="execute",
        completion=plan_completion)
    state["step"] = "execute"
    loop.save(ws, state)

    second = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)

    assert first["operation_id"] != second["operation_id"]
    assert first["result"]["predecessor_head"]["summary"]["stage_id"] == \
        "stage-build-replan-head-one"
    assert second["result"]["predecessor_head"]["summary"]["stage_id"] == \
        advanced["result"]["successor_head"]["summary"]["stage_id"]


def test_partial_wave_split_retry_reuses_children_and_resumes_only_missing(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "partial-split", monkeypatch, stage_kind="build",
        stage_id="stage-build-partial-split-parent")
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_dispatch = loop._stage_loop_dispatch
    attempted = []
    first_dispatch = {}

    def interrupt_second_dispatch(*args, **kwargs):
        attempted.append(str(kwargs.get("stage_id") or ""))
        if len(attempted) == 2:
            raise RuntimeError("crash after the first child resume")
        result = real_dispatch(*args, **kwargs)
        first_dispatch["runtime"] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(
        loop, "_stage_loop_dispatch", interrupt_second_dispatch)
    with pytest.raises(RuntimeError, match="first child resume"):
        loop._stage_loop_wave_dispatches(ws, state, ready)

    after_crash = store.load(stage["run_id"])
    bindings = copy.deepcopy(loop.load(ws)["_stage_bindings"])
    child_ids = {bindings[task_id]["build"] for task_id in ("t01", "t02")}
    roots = {
        child_id: store.read_stage_object(
            stage["run_id"], after_crash["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    }
    split_operations = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    }
    resumed_before_retry = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert len(split_operations) == 1
    assert len(resumed_before_retry) == 1

    monkeypatch.setattr(loop, "_stage_loop_dispatch", real_dispatch)
    retried = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(retried) == {"t01", "t02"}
    assert retried["t01"] == first_dispatch["runtime"]
    final = store.load(stage["run_id"])
    assert loop.load(ws)["_stage_bindings"] == bindings
    assert set(final["active_stage_projection"]["active_stage_ids"]) == \
        child_ids
    assert {
        child_id: store.read_stage_object(
            stage["run_id"], final["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    } == roots
    assert {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    } == split_operations
    resumed_after_retry = {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert resumed_before_retry < resumed_after_retry
    assert len(resumed_after_retry) == 2


def test_new_run_init_rejects_spaced_actor_without_state_or_run_mutation(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    root = tmp_path / "spaced-actor"
    root.mkdir()
    workspace, store, initial = _real_pristine_run(root)
    requirement = _record_bootstrap_requirement(workspace)
    state_path = Path(loop._loop_path(str(workspace)))
    before_run = copy.deepcopy(store.load(initial["run_id"]))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "reject ambiguous actor authority",
        requirement_id=requirement["id"], by="Volodymyr Demkiv")

    assert refused["refused"] is True
    assert "actor" in refused["error"]
    assert "match" in refused["error"]
    assert not state_path.exists()
    assert store.load(initial["run_id"]) == before_run


@pytest.mark.parametrize(
    ("step", "force"), [("pm", True), ("done", False), ("done", True)])
def test_new_run_init_refuses_any_existing_singleton_even_force_or_terminal(
        tmp_path, monkeypatch, step, force) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _record_bootstrap_requirement)

    root = tmp_path / f"existing-{step}-{force}"
    root.mkdir()
    workspace = Path(_workspace(root))
    requirement = _record_bootstrap_requirement(workspace)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    existing = loop.init(str(workspace), "preserve prior singleton history")
    assert "error" not in existing, existing
    if step == "done":
        existing["step"] = "done"
        loop.save(str(workspace), existing)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    before_files = sorted(path.name for path in state_path.parent.iterdir())
    store_root = Path(os.environ["TASKPLANE_HOME"])
    before_store = _content_inventory(store_root)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "never replace existing singleton history",
        requirement_id=requirement["id"], by="human:vdemkiv", force=force)

    assert refused["refused"] is True
    assert "existing singleton" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert sorted(path.name for path in state_path.parent.iterdir()) == \
        before_files
    assert _content_inventory(store_root) == before_store


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_refuses_same_id_stale_cloned_store_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / f"stale-{mode}", monkeypatch,
        stage_id=f"stage-product-stale-store-{mode}")
    state_path = Path(loop._loop_path(ws))
    state_root = state_path.parents[2]
    before_state = state_path.read_bytes()
    before_original = copy.deepcopy(store.load(stage["run_id"]))
    original_locator = loop.runtime_storage.load_workspace_locator(ws)
    alternate_home = tmp_path / f"alternate-home-{mode}"
    source_run = Path(store.home) / "runs" / stage["run_id"]
    cloned_run = alternate_home / "runs" / stage["run_id"]
    cloned_run.parent.mkdir(parents=True)
    shutil.copytree(source_run, cloned_run)
    cloned_manifest_path = cloned_run / "manifest.json"
    before_clone = cloned_manifest_path.read_bytes()
    identity = loop.runtime_storage.resolve_repository_identity(ws)
    layout = loop.runtime_storage.resolve_layout(
        identity, home=str(alternate_home), run_id=stage["run_id"])
    stale_locator = {
        **copy.deepcopy(original_locator), "home": layout.home,
        "paths": {
            "state": layout.state_root, "graph": layout.graph_root,
            "evidence": layout.evidence_root, "lenses": layout.lens_root,
            "artifacts": layout.artifact_root,
        },
    }
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: copy.deepcopy(stale_locator))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "store identity changed" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert store.load(stage["run_id"]) == before_original
    assert cloned_manifest_path.read_bytes() == before_clone


def test_stage_output_rejects_caller_controlled_source_workspace(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "caller-source", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-caller-source")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    alternate = tmp_path / "caller-controlled-source"
    alternate.mkdir()
    (alternate / "result.txt").write_text(
        "caller-selected bytes\n", encoding="utf-8")
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"].update({
        "source_workspace": str(alternate),
        "sources": [{"path": "result.txt", "required": True}],
    })
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    opened = []
    monkeypatch.setattr(
        loop, "_before_stage_output_component_open",
        lambda *_args: opened.append(_args))

    with pytest.raises(ValueError, match="workspace"):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert opened == []
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_stage_output_intermediate_symlink_swap_hook_fails_closed(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "symlink-swap", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-symlink-swap")
    workspace = Path(ws)
    safe = workspace / "safe"
    safe.mkdir()
    (safe / "result.txt").write_text(
        "validated output\n", encoding="utf-8")
    outside = tmp_path / "swap-target"
    outside.mkdir()
    (outside / "result.txt").write_text(
        "substituted output\n", encoding="utf-8")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": "safe/result.txt", "required": True,
    }]
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    swaps = []

    def swap_intermediate(root, relative_path, component_index):
        if relative_path == "safe/result.txt" and component_index == 0 and \
                not swaps:
            swaps.append((root, relative_path, component_index))
            safe.rename(workspace / "safe-validated")
            safe.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        loop, "_before_stage_output_component_open", swap_intermediate)

    with pytest.raises((ValueError, OSError)):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert swaps == [(ws, "safe/result.txt", 0)]
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_same_kind_resolve_seals_decision_through_new_engineering_head(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "same-kind-resolve", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-skip")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "skip")

    assert "error" not in resolved, resolved
    assert resolved["step"] == "em"
    receipt = resolved["stage_transition"]
    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "engineering"
    assert successor["stage_id"] != stage["stage_id"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "skip"' in payloads
    assert '"resulting_step": "em"' in payloads


def test_transition_reconciles_receipt_after_stage_commit_before_singleton(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "transition-crash", monkeypatch,
        stage_id="stage-product-transition-crash")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nReconcile the committed Product transition.\n",
        encoding="utf-8")
    state_path = Path(loop._loop_path(ws))
    before_state = state_path.read_bytes()
    real_save = loop.save
    failures = []

    def fail_first_plan_singleton(workspace, value):
        if value.get("step") == "plan" and not failures:
            failures.append("post-stage/pre-singleton")
            raise RuntimeError("crash before singleton transition commit")
        return real_save(workspace, value)

    monkeypatch.setattr(loop, "save", fail_first_plan_singleton)
    with pytest.raises(RuntimeError, match="before singleton"):
        loop.gate.__wrapped__(ws, "pass")

    assert state_path.read_bytes() == before_state
    after_stage_commit = store.load(stage["run_id"])
    prior = [
        receipt for receipt in after_stage_commit["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]
    assert len(prior) == 1
    monkeypatch.setattr(loop, "save", real_save)

    retried = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in retried, retried
    assert retried["step"] == "plan"
    assert retried["stage_transition"]["operation_id"] == \
        prior[0]["operation_id"]
    final = store.load(stage["run_id"])
    assert len([
        receipt for receipt in final["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]) == 1


def test_native_wave_ignores_historical_stage_replay_state(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "wave-pre-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-wave-pre-output")
    tasks = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": tasks,
    })
    loop.save(ws, state)
    historical_manifest = copy.deepcopy(store.load(stage["run_id"]))
    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("historical StageLifecycle replay was invoked")))

    authority = open_delivery_root(ws)
    first = loop.wave(ws, root_observation_authority=authority)
    second = loop.wave(ws, root_observation_authority=authority)

    for emitted in (first, second):
        assert "error" not in emitted, emitted
        assert [entry["task"]["id"] for entry in emitted["wave"]] == [
            "t01", "t02"]
        assert emitted["wait_invocation"]["outstanding_members"] == [
            "t01", "t02"]
        assert emitted["held"] == []
        encoded = json.dumps(emitted, sort_keys=True).lower()
        assert "stage_runtime_dispatch" not in encoded
        assert "execution_root" not in encoded
        assert "replay" not in encoded
        assert "tranche" not in encoded
    assert [entry["dispatch_intent"]["intent_id"]
            for entry in first["wave"]] == [
                entry["dispatch_intent"]["intent_id"]
                for entry in second["wave"]]
    assert not (loop.load(ws) or {}).get("_stage_bindings")
    assert store.load(stage["run_id"]) == historical_manifest
