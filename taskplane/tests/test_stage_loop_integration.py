"""The legacy loop opts into bounded v4 stage dispatch explicitly."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

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


def _start_real_stage_loop(tmp_path, monkeypatch, *, stage_kind="product",
                           stage_id=None, goal="exercise the real stage loop"):
    from taskplane.tests.test_stage_cross_host import _real_loop_stage

    tmp_path.mkdir(parents=True, exist_ok=True)
    stage_id = stage_id or f"stage-{stage_kind}-loop-root"
    workspace, store, stage = _real_loop_stage(
        tmp_path, stage_kind=stage_kind, stage_id=stage_id)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    initialized = loop.init(str(workspace), goal)
    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": f"start-{stage_id}",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in initialized, initialized
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
    ws = _workspace(tmp_path)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    initialized = loop.init(ws, "start the stage-native canary")
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-product-root-001",
        "stage_kind": "product", "fingerprint": "f" * 64,
        "predecessor_stage_ids": [], "selected_artifacts": [],
        "execution_root_id": "execution-stage-product-root-001",
        "authority": {"actor": "human:owner"},
    }
    receipt = {
        "schema": "taskplane.stage-operation-receipt/v1",
        "operation": "start_stage", "operation_id": "start-root-once",
        "stage_ids": [stage["stage_id"]],
    }
    manifest = {
        "schema": "taskplane.run/v3", "run_id": "run-r0004",
        "revision": 3,
    }
    committed = []

    class Store:
        @staticmethod
        def load(run_id):
            assert run_id == "run-r0004"
            return copy.deepcopy(manifest)

    class StageEntities:
        @staticmethod
        def validate_stage(value):
            assert value == stage
            return value

    class Lifecycle:
        @staticmethod
        def start_stage(candidate, **kwargs):
            assert candidate == stage
            assert kwargs["operation_id"] == "start-root-once"
            if not committed:
                committed.append("start-root-once")
                manifest.update({
                    "schema": "taskplane.run/v4", "revision": 4,
                    "stage_operations": {"start-root-once": receipt},
                })
            return receipt

    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())
    monkeypatch.setattr(
        loop, "_validate_stage_request", lambda _action, request: request)
    monkeypatch.setattr(
        loop, "_stage_lifecycle",
        lambda *_args: (StageEntities, Lifecycle()))
    monkeypatch.setattr(loop, "_verified_stage_handoff", lambda *_a: {})
    monkeypatch.setattr(loop, "_preflight_stage_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.tp, "verify_stage_receipt", lambda value, **_k: value)
    monkeypatch.setattr(
        loop, "_stage_dispatch",
        lambda *_a, **_k: {"schema": "taskplane.stage-dispatch/v1"})
    request = {
        "stage": stage, "expected_revision": 3,
        "operation_id": "start-root-once",
        "expected_predecessor_fingerprints": {},
        "authority": stage["authority"],
    }

    first = loop.stage_command(ws, "start", request)
    second = loop.stage_command(ws, "start", request)

    assert initialized["step"] == "pm"
    assert initialized["_stage_native_new_run_pristine"] is True
    assert "error" not in first
    assert second == first
    assert committed == ["start-root-once"]


def test_pristine_new_run_commits_root_before_dispatch_and_refuses_late_start(
        tmp_path, monkeypatch) -> None:
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
    ws, store, stage, started = _start_real_stage_loop(
        tmp_path / "pristine", monkeypatch,
        stage_id="stage-product-pristine-root")

    assert observed == [stage["stage_id"]]
    assert started["dispatch"]["startup"]["stage_id"] == stage["stage_id"]
    assert store.load(stage["run_id"])["schema"] == "taskplane.run/v4"

    from taskplane.tests.test_stage_cross_host import _real_loop_stage

    late_root = tmp_path / "late"
    late_root.mkdir()
    late_ws, late_store, _late_stage = _real_loop_stage(
        late_root, stage_kind="product", stage_id="stage-product-late-root")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    loop.init(str(late_ws), "author Product before stage start")
    specs = late_ws / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nThe singleton has already produced Product output.\n",
        encoding="utf-8")
    refused_gate = loop.gate.__wrapped__(str(late_ws), "pass")

    assert "committed root stage before" in refused_gate["error"]
    assert loop.load(str(late_ws))["step"] == "pm"
    assert late_store.load("run-cross-host-loop")["schema"] == \
        "taskplane.run/v3"

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


def test_parallel_wave_uses_distinct_child_stage_roots_and_bound_foreground(
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

    def dispatch(task_id):
        return {
            "schema": "taskplane.stage-dispatch/v1",
            "startup": {
                "stage_id": f"stage-build-{task_id}",
                "execution_claim": {
                    "execution_root_id": f"execution-stage-build-{task_id}",
                },
            },
        }

    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda _ws, _state, ready: {
            task["id"]: dispatch(task["id"]) for task in ready})

    result = loop.wave(ws)
    identities = {
        (entry["stage_runtime_dispatch"]["startup"]["stage_id"],
         entry["stage_runtime_dispatch"]["startup"]["execution_claim"][
             "execution_root_id"])
        for entry in result["wave"]
    }

    assert len(result["wave"]) == 2
    assert len(identities) == 2

    manifest = {
        "schema": "taskplane.run/v4", "run_id": "run-r0004",
        "active_stage_projection": {
            "active_stage_ids": ["stage-build-t01", "stage-build-t02"],
            "foreground_stage_id": None,
        },
    }

    class Store:
        @staticmethod
        def load(_run_id):
            return manifest

    bound = copy.deepcopy(state)
    bound["_stage_bindings"] = {
        "t01": {"build": "stage-build-t01"},
        "t02": {"build": "stage-build-t02"},
    }
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "run-r0004", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda *_a: Store())
    monkeypatch.setattr(
        loop, "_indexed_stage",
        lambda *_a: {
            "stage_id": "stage-build-t01", "stage_kind": "build",
            "authority": {},
        })
    monkeypatch.setattr(
        loop, "_stage_lifecycle", lambda *_a: (object(), object()))

    context = loop._stage_loop_context(ws, bound)

    assert context["stage"]["stage_id"] == "stage-build-t01"


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


def test_interim_evaluate_terminalizes_only_without_starting_bogus_build(
        tmp_path, monkeypatch) -> None:
    ws, store, root, _started = _start_real_stage_loop(
        tmp_path / "interim-evaluate", monkeypatch, stage_kind="build",
        stage_id="stage-build-interim-parent")
    tasks = [
        {"id": "t01", "scope": ["README.md"], "tests": "true",
         "deps": [], "status": "pending",
         "target_commit": loop.tp.git_head(ws)},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending",
         "target_commit": loop.tp.git_head(ws)},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": tasks,
    })
    loop.save(ws, state)
    loop._stage_loop_wave_dispatches(ws, state, tasks)
    workspace = Path(ws)
    (workspace / "README.md").write_text(
        "stage journey\nfirst task built\n", encoding="utf-8")
    monkeypatch.setattr(
        loop.runtime_eval, "guide_loop",
        lambda *_a, **_k: {"status": "on_path", "recovered": False})
    built_submission = loop.submit(ws, "pass", task_id="t01")
    assert "error" not in built_submission, built_submission
    build_state = loop.load(ws)
    build_state["current_task"] = 0
    build_completion = loop._stage_loop_gate_completion(
        ws, build_state, step="execute", outcome="pass",
        submission=built_submission["submission"])
    build_receipt = loop._stage_loop_transition(
        ws, build_state, from_step="execute", to_step="evaluate",
        completion=build_completion)
    evaluate_id = build_receipt["result"]["successor_head"]["summary"][
        "stage_id"]

    state = loop.load(ws)
    state["step"] = "evaluate"
    state["current_task"] = 0
    state["tasks"][0]["status"] = "built"
    state["tasks"][0].pop("_submission", None)
    state["tasks"][1]["status"] = "pending"
    state.setdefault("_stage_bindings", {}).setdefault("t01", {})[
        "evaluate"] = evaluate_id
    loop.save(ws, state)
    evaluation_path = Path(loop.runtime_storage.evaluation_path(ws))
    evaluation_path.parent.mkdir(parents=True, exist_ok=True)
    evaluation_path.write_text(json.dumps({
        "task": "t01", "requirement": "R-0004", "verdict": "pass",
        "status": "complete", "findings": [],
    }) + "\n", encoding="utf-8")
    evaluated_submission = loop.submit(ws, "pass")
    assert "error" not in evaluated_submission, evaluated_submission
    monkeypatch.setattr(loop, "_evaluation_errors", lambda *_a, **_k: [])
    monkeypatch.setattr(loop.tp, "engine_skew_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_submission_staleness", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_automatic_merge_cleanup", lambda *_a: None)

    gated = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in gated, gated
    assert gated["step"] == "execute"
    assert gated["stage_transition"]["operation"] == "terminalize"
    manifest = store.load(root["run_id"])
    active_ids = manifest["active_stage_projection"]["active_stage_ids"]
    assert active_ids == [loop.load(ws)["_stage_bindings"]["t02"]["build"]]
    assert {
        manifest["stage_heads"][stage_id]["summary"]["stage_kind"]
        for stage_id in active_ids
    } == {"build"}


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
    monkeypatch.setattr(loop.tp, "plan_ordering_refusal", lambda *_a, **_k: None)
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
    report = {"goal": state["goal"], "graph_true_up": {"changed": False}}
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
