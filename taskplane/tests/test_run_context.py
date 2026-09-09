"""Stateless controller regressions; all stores/host inputs here are simulated."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import loop, phase_harness, run_context, settings
from taskplane.tests.test_r0001_j1_native import _supporting_pristine_phase_run


ROOT = Path(__file__).resolve().parents[2]


def _state(configuration=None):
    value = configuration or settings.load_settings(environment={})
    return {"run_id": "isolated-run", "_stage_native_root_authority": {"actor": "human:test"},
            "settings_snapshot": value.to_dict(), "settings_digest": value.digest}


def test_snapshot_is_complete_and_does_not_read_package_defaults(monkeypatch):
    configuration = settings.load_settings(environment={"TASKPLANE_MODEL_DEEP": "saved-model"})
    state = _state(configuration)
    read_json = settings._read_json
    monkeypatch.setattr(settings, "_read_json", lambda path: (
        pytest.fail("read today's defaults") if path == settings.DEFAULT_SETTINGS_PATH else read_json(path)))
    with run_context.bind("/workspace", state):
        observed = settings.load_settings(environment={"TASKPLANE_MODEL_DEEP": "replacement-model"})
        assert observed.digest == configuration.digest
        assert observed.stages["product"].model == "saved-model"
        assert observed.to_dict() == state["settings_snapshot"]
        # Both import modes are supported by the production CLI.
        import settings as flat_settings
        assert flat_settings.load_settings().digest == configuration.digest
    assert run_context.current_settings() is None


@pytest.mark.parametrize("damage", ["missing", "partial", "changed", "wrong-digest", "binding-drift"])
def test_bad_snapshot_cannot_fall_back_to_environment_or_defaults(damage):
    state = _state()
    if damage == "missing": state.pop("settings_snapshot")
    if damage == "partial": state["settings_snapshot"].pop("limits")
    if damage == "changed": state["settings_snapshot"]["limits"]["budgets"]["max_actions"] += 1
    if damage == "wrong-digest": state["settings_digest"] = "f" * 64
    if damage == "binding-drift": state["run_artifact_binding"] = {"settings_digest": "f" * 64}
    with pytest.raises(ValueError):
        with run_context.bind("/workspace", state):
            pytest.fail("entered a corrupt run context")
    assert run_context.current_settings() is None


def test_context_cannot_leak_across_runs_or_replace_policy_with_an_overlay():
    first = _state()
    second = dict(first, run_id="another-run")
    with run_context.bind("/first", first):
        with pytest.raises(run_context.RunContextError, match="nested"):
            with run_context.bind("/second", second):
                pass
        with pytest.raises(settings.SettingsError, match="transient overlay"):
            settings.load_settings(overlay={"build": {"concurrency": 1}})
        assert settings.load_settings().digest == first["settings_digest"]
    with run_context.bind("/second", second):
        assert settings.load_settings().digest == second["settings_digest"]


def test_restore_exact_old_configuration_never_upgrades_policy(tmp_path, monkeypatch):
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    original = loop._load_raw(ws)
    old_file = tmp_path / "original-settings.json"
    old_file.write_text(json.dumps(original["settings_snapshot"]))
    legacy = copy.deepcopy(original)
    legacy.pop("settings_snapshot")
    loop.save(ws, legacy)
    manifest = store.load(run_id)
    assert "snapshot is missing" in loop.next_action(ws)["error"]
    assert store.load(run_id) == manifest
    assert loop.resume(ws)["goal"] == original["goal"]
    changed = tmp_path / "changed-settings.json"
    other = copy.deepcopy(original["settings_snapshot"])
    other["limits"]["budgets"]["max_actions"] += 1
    changed.write_text(json.dumps(other))
    assert run_context.restore_settings(loop, ws, str(changed)).get("error")
    assert loop._load_raw(ws) == legacy
    restored = run_context.restore_settings(loop, ws, str(old_file))
    assert restored["policy_changed"] is False and restored["dispatch_allowed"] is False
    assert loop._load_raw(ws) == original
    assert store.load(run_id) == manifest
    assert run_context.restore_settings(loop, ws, str(old_file))["replay"] is True
    assert loop._load_raw(ws) == original


def test_new_process_without_prior_environment_prepares_same_run(tmp_path, monkeypatch):
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    original = loop._load_raw(ws)
    code = """
import json, os, sys
from taskplane import storage, loop
storage.bind_workspace_taskplane_home(sys.argv[1], os.environ)
action = loop.next_action(sys.argv[1])
print(json.dumps(action))
"""
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith("TASKPLANE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_SESSION_ID"}}
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "taskplane")])
    environment["TASKPLANE_SESSION_ID"] = "replacement-controller"
    environment["TASKPLANE_MODEL_DEEP"] = "must-not-replace-saved-model"
    first = subprocess.run([sys.executable, "-c", code, ws], env=environment,
        cwd=ws, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    assert first.returncode == 0, first.stderr
    prepared = json.loads(first.stdout)
    assert not prepared.get("error"), prepared
    assert prepared["phase_runtime"]["run_id"] == run_id
    assert prepared["settings_digest"] == original["settings_digest"]
    after = store.load(run_id)
    environment["TASKPLANE_SESSION_ID"] = "third-controller"
    second = subprocess.run([sys.executable, "-c", code, ws], env=environment,
        cwd=ws, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    assert second.returncode == 0, second.stderr
    pending = json.loads(second.stdout)
    assert pending["phase_runtime"]["operation_id"] == prepared["phase_runtime"]["operation_id"]
    assert "task_name" not in pending
    assert store.load(run_id) == after
    assert loop._load_raw(ws)["_stage_native_root_authority"] == original["_stage_native_root_authority"]


def test_overlapping_controllers_reserve_only_one_attempt(tmp_path, monkeypatch):
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: loop.next_action(ws), range(2)))
    assert all(not result.get("error") for result in results), results
    assert sum("task_name" in result for result in results) == 1
    assert len({result["phase_runtime"]["operation_id"] for result in results}) == 1
    records = store.load(run_id)["phase_records"]
    assert sum(row["operation"] == "phase_prepare" for row in records.values()) == 1


@pytest.mark.parametrize("phase", ["product", "design", "plan", "build", "evaluate", "engineering", "retro"])
def test_every_declared_phase_uses_same_brief_compiler(phase):
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    definition = _registry().admit(phase, ()).to_dict()
    before = copy.deepcopy(definition)
    produced = definition["produces"]
    worker_outputs = {produced[0]["artifact_class"]: "declared/output.json"}
    action = {"instruction": "Existing domain evidence and submission obligations.",
              "phase_runtime": {"outputs": worker_outputs}}
    requirement = {"id": "R-0001", "acceptance": ["preserve scope"]}
    phase_harness.admit_lens_plan({"definition": definition})
    phase_harness.compile_brief({"definition": definition}, action, requirement)
    assert action["execution_mode"] == "stateless-phase"
    assert action["phase_definition"] == before == definition
    assert action["requirement"] == requirement
    assert action["phase_outputs"][0]["owner"] == "worker"
    assert all(row["owner"] == "runtime" for row in action["phase_outputs"][1:])
    assert "orchestrator" in action["instruction"]
    assert "Existing domain evidence" not in action["instruction"]
    assert "review_kernel.slots" not in action["instruction"]
    if phase == "plan":
        assert "not an already-sealed" in action["instruction"]


def test_public_pending_pickup_needs_neither_settings_nor_host_admission(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    from taskplane import tp as cli
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    prepared = loop.next_action(ws)
    assert not prepared.get("error"), prepared
    state = loop._load_raw(ws)
    state.pop("settings_snapshot")  # Digest-only historical run, already in flight.
    loop.save(ws, state)
    before = Path(loop._loop_path(ws)).read_bytes()
    manifest = store.load(run_id)
    for name in ("_graph_quality_refusal", "_enforcement_check"):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: pytest.fail("admitted a read"))
    monkeypatch.setenv("TASKPLANE_ENFORCE_SCREEN", "strict")
    monkeypatch.setenv("TASKPLANE_INLINE_MAX", "invalid-today")
    monkeypatch.delenv("TASKPLANE_SESSION_ID", raising=False)
    args = Namespace(cmd="loop", workspace=ws, loop_action="next", fn=cli.cmd_loop)
    assert cli._invoke_run_command(args, ws) == 0
    picked = json.loads(capsys.readouterr().out)
    assert picked["read_only"] is True and picked["dispatch_allowed"] is False
    assert picked["phase_runtime"]["operation_id"] == prepared["phase_runtime"]["operation_id"]
    assert loop.next_action(ws)["phase_runtime"] == picked["phase_runtime"]
    assert Path(loop._loop_path(ws)).read_bytes() == before
    assert store.load(run_id) == manifest

    # Exercise the actual executable parser, not just an imported CLI handler.
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith("TASKPLANE_") and key not in {"CODEX_THREAD_ID", "CLAUDE_SESSION_ID"}}
    environment["TASKPLANE_INLINE_MAX"] = "invalid-today"
    completed = subprocess.run([sys.executable, str(ROOT / "taskplane" / "tp.py"),
        "loop", "--workspace", ws, "next"], env=environment,
        cwd=ws, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["phase_runtime"] == picked["phase_runtime"]
    assert Path(loop._loop_path(ws)).read_bytes() == before
    assert store.load(run_id) == manifest


def test_public_unprepared_run_reports_missing_snapshot_without_traceback(tmp_path, monkeypatch):
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    state = loop._load_raw(ws)
    state.pop("settings_snapshot")
    loop.save(ws, state)
    manifest = store.load(run_id)
    completed = subprocess.run([sys.executable, str(ROOT / "taskplane" / "tp.py"),
        "loop", "--workspace", ws, "next"], cwd=ws,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    assert completed.returncode == 1, completed.stderr
    assert "snapshot is missing" in json.loads(completed.stdout)["error"]
    assert "Traceback" not in completed.stderr
    assert store.load(run_id) == manifest


def test_nonempty_phase_lenses_cannot_disappear_into_legacy_routing():
    with pytest.raises(ValueError, match="phase-owned"):
        phase_harness.admit_lens_plan({"definition": {
            "working_lenses": ["security"], "evaluation_lenses": []}})


def test_advisory_run_prepares_without_session_owned_signing_and_keeps_waiting(tmp_path, monkeypatch):
    import time
    from taskplane.tests.test_r0001_phase_cutover import _normal_phase_workspace
    ws, store, stage, _, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    run_id = stage["run_id"]
    original = loop._load_raw(ws)
    actor = original["_stage_native_root_authority"]["actor"]
    policy = loop.resolve(ws, "limits-advisory", by=actor)
    assert not policy.get("error"), policy
    assert policy["resource_policy"]["mode"] == "advisory"
    action = loop.next_action(ws)
    assert not action.get("error"), action
    assert action["resource_policy"]["mode"] == "advisory"
    assert "advisory" in action["instruction"]
    manifest = store.load(run_id)
    future = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: future)
    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["status"] == "pending", pending
    assert pending["dispatch_allowed"] is False
    assert store.load(run_id) == manifest
    assert loop._load_raw(ws)["settings_snapshot"] == original["settings_snapshot"]
    monkeypatch.setenv("TASKPLANE_TASK", "worker-test")
    assert loop.resolve(ws, "limits-advisory", by=actor).get("error")


@pytest.mark.parametrize("total", [100, 100000, None])
def test_native_shaped_stop_without_inline_usage_keeps_real_budget(tmp_path, monkeypatch, total):
    """Production phase path with simulated host files; not native proof."""
    from datetime import datetime, timezone
    from taskplane import stage_migration
    from taskplane.tests.test_r0001_phase_cutover import (
        _normal_phase_workspace, _emit_host_hook, _authored_requirement)
    ws, store, stage, artifacts, route, authorize = _normal_phase_workspace(tmp_path, monkeypatch)
    config = dict(route["result"]["configuration"], host_kind="codex")
    stage_migration.change_phase_routing(store, stage["run_id"], owner="agent-runtime",
        configuration=config, expected_previous=route["result_fingerprint"],
        expected_revision=store.load(stage["run_id"])["revision"], operation_id="native-shaped-host",
        validate_authority=authorize)
    action = loop.next_action(ws)
    assert not action.get("error"), action
    child = "01a07da3-a886-7261-aae9-1126caff4b6c"
    # No PreToolUse dispatch observation: native Start itself must bind the ledger.
    parent = "simulated-session"
    name = action["task_name"]
    now = datetime.now(timezone.utc)
    home = tmp_path / "codex"
    path = home / "sessions" / now.strftime("%Y/%m/%d") / f"rollout-test-{child}.jsonl"
    path.parent.mkdir(parents=True)
    metadata = {"type": "session_meta", "payload": {
        "id": child, "session_id": parent, "parent_thread_id": parent,
        "cwd": ws, "agent_path": f"/root/{name}", "timestamp": now.isoformat(),
        "source": {"subagent": {"thread_spawn": {
            "parent_thread_id": parent, "agent_path": f"/root/{name}"}}}}}
    counter = {"type": "event_msg", "ordinal": 4, "timestamp": now.isoformat(),
        "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": (total or 1) - 1, "cached_input_tokens": 0,
            "output_tokens": 1, "reasoning_output_tokens": 0, "total_tokens": total}}}}
    path.write_text(json.dumps(metadata) + "\n" + (json.dumps(counter) + "\n" if total else ""))
    monkeypatch.setenv("CODEX_HOME", str(home))
    fields = {"agent_id": child, "agent_type": "default", "usage": None,
              "agent_transcript_path": str(path)}
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch, **fields) == 0
    _authored_requirement(ws, stage)
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch, **fields)
    pending = loop.next_action(ws)
    completion = pending["phase_runtime"]["completion"]
    if total == 100:
        assert completion is not None, pending
        assert artifacts.read(completion["runtime_result"])["status"] == "accepted"
    else:
        assert completion is None
        refusals = [json.loads(p.read_text()) for p in
            Path(artifacts.root, "phase-collection-refusal").glob("*.json")]
        assert refusals[-1]["reason_code"] == (
            "budget_exhausted" if total else "observation_unavailable")
    assert loop._load_raw(ws)["settings_snapshot"]["phase_definitions"][0]["budget"]["tokens"] == 100000
    if total == 100000:
        import time
        original = loop._load_raw(ws)
        saved_snapshot = copy.deepcopy(original["settings_snapshot"])
        actor = original["_stage_native_root_authority"]["actor"]
        operation = action["phase_runtime"]["operation_id"]
        assert loop.resolve(ws, "limits-advisory", by="human:foreign").get("error")
        # The real-shaped terminal is already recorded. Only resource time
        # passes; neither the worker nor a Stop callback is run again.
        future = time.time() + 3600
        monkeypatch.setattr(time, "time", lambda: future)
        policy = loop.resolve(ws, "limits-advisory", by=actor)
        assert "error" not in policy, policy
        assert policy["dispatch_allowed"] is False
        monkeypatch.setenv("TASKPLANE_SESSION_ID", "replacement-after-terminal")
        assert loop.resolve(ws, "limits-advisory", by=actor)["replay"] is True
        reconciled = loop.resolve(ws, "reconcile", phase_operation=operation)
        assert reconciled.get("status") == "collected", reconciled
        assert reconciled["worker_released"] is True
        assert loop.resolve(ws, "reconcile", phase_operation=operation)["replay"] is True
        completed = loop.next_action(ws)["phase_runtime"]["completion"]
        assert completed["resource_usage"]["tokens"] == total
        assert completed["resource_usage"]["advisory"] is True
        assert artifacts.read(completed["runtime_result"])["budget"]["tokens"] == 100000
        assert loop._load_raw(ws)["settings_snapshot"] == saved_snapshot
        assert loop._load_raw(ws)["requirement_id"] == original["requirement_id"]
        assert len([row for row in stage_migration.phase_records(store.load(stage["run_id"])).values()
            if row["operation"] == "phase_prepare"]) == 1
        # A sealed result is durable evidence, not a lease on a conversation.
        # Its signing window may end, but current key revocation still applies.
        from dataclasses import replace
        material = artifacts.read(completed["preparation"])
        signer = loop._phase_bridge_signing(ws, material)
        signed = artifacts.read(completed["runtime_receipt"])
        later = future + 172800
        monkeypatch.setattr(time, "time", lambda: later)
        assert loop.resolve(ws, "reconcile", phase_operation=operation)["replay"] is True
        assert run_context.resource_limits_advisory(ws) is True
        key = signer.keys[signer.key_id]
        revoked = replace(signer, now=int(later), keys={**signer.keys,
            signer.key_id: replace(key, status="revoked", changed_at=int(later))})
        with pytest.raises(ValueError, match="disabled"):
            revoked.verify(signed)
