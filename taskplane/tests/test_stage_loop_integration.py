"""The legacy loop opts into bounded v4 stage dispatch explicitly."""
from __future__ import annotations

import copy
import subprocess

from taskplane import loop


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
    monkeypatch.setattr(loop, "_stage_loop_context", lambda _ws: context)
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
    monkeypatch.setattr(loop, "_stage_loop_context", lambda _ws: context)
    monkeypatch.setattr(loop.tp, "verify_stage_receipt", lambda value: value)

    assert loop._stage_loop_transition(
        "/repo", {"step": "done"},
        from_step="retro", to_step="done") is receipt


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


def test_wave_attaches_stage_runtime_dispatch_to_each_entry(
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
    marker = {"schema": "taskplane.stage-dispatch/v1", "startup": {}}
    monkeypatch.setattr(loop, "_stage_loop_dispatch", lambda *_a, **_k: marker)

    result = loop.wave(ws)

    assert len(result["wave"]) == 1
    assert result["wave"][0]["stage_runtime_dispatch"] is marker


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
