"""Production startup/recovery regressions with simulated host input.

These exercise the real CLI, launcher, storage and hook handlers. They do not
constitute a real-host J1 pass or manufacture evidence for an active run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import host_capabilities as caps
import storage
import tp as cli
from taskplane.tests.test_codex_child_identity import native as native_child


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("damage", [None, "missing", "foreign-child", "foreign-turn", "later-active", "earlier-completion"])
def test_completed_child_uses_only_exact_provider_lifecycle_metadata(native_child, monkeypatch, damage):
    from taskplane import codex_identity
    from taskplane.tests.test_codex_child_identity import NOW, NAME
    home, path, metadata, event = native_child
    monkeypatch.setenv("CODEX_HOME", str(home))
    start = {"owner": {**{key:event[key] for key in ("agent_id", "session_id", "agent_type")}, "task_name":NAME},
        "turn_id":event["turn_id"], "observed_at":NOW.timestamp() + 5}
    complete = {"type":"event_msg", "payload":{"type":"task_complete", "turn_id":event["turn_id"],
        "started_at":NOW.timestamp(), "completed_at":NOW.timestamp() + 20, "duration_ms":20000,
        "last_agent_message":"must not be returned or retained"}}
    if damage == "foreign-child": metadata["payload"]["id"] = event["session_id"]
    if damage == "foreign-turn": complete["payload"]["turn_id"] = "other"
    if damage == "earlier-completion": complete["payload"]["completed_at"] = NOW.timestamp()
    rows = [metadata] + ([] if damage == "missing" else [complete])
    if damage == "later-active": rows.append({"type":"event_msg", "payload":{"type":"task_started", "turn_id":"later"}})
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    if damage:
        with pytest.raises(ValueError): codex_identity.completed_child(event["cwd"], start)
    else:
        result = codex_identity.completed_child(event["cwd"], start)
        assert result["source"] == "codex-task-complete"
        assert result["outcome"] == "complete"
        assert result["turn_id"] == event["turn_id"]
        assert "must not be returned" not in json.dumps(result)




def test_early_producer_load_error_cannot_disappear(tmp_path, monkeypatch, capsys):
    import loop
    from types import SimpleNamespace
    state_calls = []
    def load(*a):
        state_calls.append(True)
        if len(state_calls) == 1: return {}
        raise ValueError("unreadable producer state")
    contract = {"producer_dispatch":{"stage":"evaluate", "producer":"tp-evaluator"}}
    monkeypatch.setattr(cli, "_subagent_event", lambda:{"cwd":str(tmp_path)})
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *a:contract)
    monkeypatch.setattr(loop, "load", load)
    monkeypatch.setattr(loop, "complete_observed_evaluate_evidence_child", lambda *a:None)
    monkeypatch.setattr(cli, "_submission_stop_check", lambda *a:None)
    trace = []
    monkeypatch.setattr(cli.tp, "trace", lambda ws,event,**kw:trace.append((event,kw)))
    assert cli.cmd_subagent_stop(SimpleNamespace()) == 2
    assert "unreadable producer state" in capsys.readouterr().out
    assert any(event == "producer_observation_failed" for event,_ in trace)


@pytest.mark.parametrize("damage", [None, "foreign-agent", "foreign-session", "foreign-task",
    "missing-owner", "foreign-metadata", "parent-only", "symlink"])
def test_terminal_seal_selects_only_bound_native_child(native_child, monkeypatch, damage):
    from taskplane import run_context
    home, path, metadata, event = native_child
    owner = {"agent_id":event["agent_id"], "session_id":event["session_id"],
        "task_name":metadata["payload"]["agent_path"].split("/")[-1]}
    event.update(hook_event_name="SubagentStop", task_name=owner["task_name"], host="codex",
        transcript_path="must-not-read-parent", agent_transcript_path=str(path))
    contract = {"worker_scoped":True, "worker_lifecycle":{"task":"T19", "status":"active",
        "dispatch_intent_id":"exact-dispatch", "expected_task_name":owner["task_name"], "owner":dict(owner)}}
    if damage == "foreign-agent": event["agent_id"] = event["session_id"]
    if damage == "foreign-session": event["session_id"] = "foreign"
    if damage == "foreign-task": event["task_name"] = "foreign"
    if damage == "missing-owner": contract["worker_lifecycle"].pop("owner")
    if damage == "foreign-metadata":
        metadata["payload"]["parent_thread_id"] = "foreign"
        path.write_text(json.dumps(metadata) + "\n")
    if damage == "parent-only": event["agent_transcript_path"] = event["transcript_path"]
    if damage == "symlink":
        actual = path.with_suffix(".actual")
        path.rename(actual)
        path.symlink_to(actual)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(run_context, "resource_limits_advisory", lambda _:True)
    monkeypatch.setattr(cli, "_dispatch_usage_observation_required", lambda *a:True)
    class Selected(Exception): pass
    def project(ws, selected, provider):
        assert selected == str(path), "terminal selected parent instead of exact child"
        assert damage is None, "invalid child reached counter observation"
        raise Selected()
    monkeypatch.setattr(cli, "_bounded_transcript_projection", project)
    with pytest.raises(Selected if damage is None else ValueError):
        cli._seal_terminal_dispatch_telemetry(event["cwd"], contract, event, outcome="failure")


@pytest.mark.parametrize("damage", [None, "parent-snapshot", "metadata-race"])
def test_terminal_counter_rechecks_authenticated_child_after_projection(native_child, monkeypatch, damage):
    import loop
    from taskplane import run_context
    home, path, metadata, event = native_child
    counter = {"type":"event_msg", "ordinal":5, "timestamp":"2026-09-07T00:00:00Z",
        "payload":{"type":"token_count", "info":{"total_token_usage":{
            "input_tokens":95, "cached_input_tokens":70, "output_tokens":5,
            "reasoning_output_tokens":1, "total_tokens":100}}}}
    path.write_text(json.dumps(metadata) + "\n" + json.dumps(counter) + "\n")
    owner = {"agent_id":event["agent_id"], "session_id":event["session_id"],
        "task_name":metadata["payload"]["agent_path"].split("/")[-1]}
    event.update(hook_event_name="SubagentStop", task_name=owner["task_name"], host="codex",
        transcript_path="must-not-read-parent", agent_transcript_path=str(path))
    contract = {"worker_scoped":True, "worker_lifecycle":{"task":"T19", "status":"active",
        "dispatch_intent_id":"exact-dispatch", "expected_task_name":owner["task_name"], "owner":owner}}
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_HOME", str(home.parent / "state"))
    monkeypatch.setattr(run_context, "resource_limits_advisory", lambda _:True)
    monkeypatch.setattr(cli, "_dispatch_usage_observation_required", lambda *a:True)
    project = cli._bounded_transcript_projection
    def projection(*args):
        value = project(*args)
        assert value["status"] == "available", value
        if damage == "parent-snapshot": value["native_session"]["session_id"] = owner["session_id"]
        if damage == "metadata-race": value["native_session"]["source"]["metadata_record_sha256"] = "f" * 64
        return value
    monkeypatch.setattr(cli, "_bounded_transcript_projection", projection)
    class VerifiedCounter(Exception): pass
    def record(ws, **kwargs):
        assert damage is None, "foreign counter reached ledger mutation"
        assert kwargs["snapshot"]["usage"]["total_tokens"] == 100
        assert kwargs["snapshot"]["usage"]["cached_input_tokens"] == 70
        raise VerifiedCounter()
    monkeypatch.setattr(loop, "record_native_session_snapshot", record)
    with pytest.raises(VerifiedCounter if damage is None else ValueError):
        cli._seal_terminal_dispatch_telemetry(event["cwd"], contract, event, outcome="failure")


@pytest.fixture
def onboarded(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane Test", "-c",
        "user.email=test@example.invalid", "commit", "--allow-empty",
        "-qm", "baseline",
    ], cwd=workspace, check=True)
    home = tmp_path / "dedicated-state"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    assert cli._install_codex_hooks(str(workspace))["ok"]
    return workspace, home


def _fresh_process(workspace, *arguments, event=None, hook_path=None, home=None,
                   session="fresh-session"):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("TASKPLANE_")
                   and key not in caps._ENV_OBSERVATIONS}
    for key in ("CODEX_THREAD_ID", "CLAUDE_SESSION_ID"):
        environment.pop(key, None)
    if event or session:
        environment["CODEX_THREAD_ID"] = event["session_id"] if event else session
    if hook_path:
        environment["TASKPLANE_HOOK_PATH"] = hook_path
    if home is not None:
        environment["TASKPLANE_HOME"] = str(home)
    environment["TASKPLANE_ENFORCE_SCREEN"] = "strict"
    if arguments[:2] == ("loop", "init"):
        from taskplane import requirements
        record = requirements.record_requirement(str(workspace), arguments[2],
            functional=["retain exact run scope across sessions"],
            acceptance=["fresh sessions consume only the selected run"])
        arguments = (*arguments, "--req", record["id"], "--by", "human:simulated")
    result = subprocess.run([
        sys.executable, str(workspace / ".taskplane/codex-hook.py"),
        *arguments,
    ], cwd=workspace, env=environment, input=json.dumps(event or {}),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_initialization_persists_scope_before_hook_readiness(onboarded):
    workspace, home = onboarded
    assert storage.load_workspace_locator(str(workspace)) is None
    initialized = json.loads(_fresh_process(
        workspace, "loop", "init", "Durable scope before dispatch", home=home))
    assert initialized["initialized"] is True
    locator = storage.load_workspace_locator(str(workspace))
    assert locator["run_id"] == initialized["run_id"]
    environment = {}
    assert storage.bind_hook_taskplane_home(
        str(workspace), environment, hook_path="bridge") == str(home)
    assert environment["TASKPLANE_HOME"] == str(home)
    assert not (home / "host-receipts").exists()
    recovered = json.loads(_fresh_process(workspace, "loop", "resume", session=None))
    assert recovered["run_id"] == initialized["run_id"]
    assert recovered["goal"] == "Durable scope before dispatch"
    assert recovered["read_only"] is True


def test_fresh_process_retains_strict_policy_without_environment(onboarded):
    workspace, home = onboarded
    initialized = json.loads(_fresh_process(
        workspace, "loop", "init", "Strict policy survives restart", home=home))
    import loop
    before = loop._load_raw(str(workspace))
    assert before["enforcement"]["current"]["mode"] == "strict"
    assert before["enforcement"]["current"]["status"] == "unproven"
    assert before["enforcement"]["current"]["run_id"] == initialized["run_id"]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("TASKPLANE_")
                   and key not in caps._ENV_OBSERVATIONS
                   and key not in ("CODEX_THREAD_ID", "CLAUDE_SESSION_ID")}
    environment["CODEX_THREAD_ID"] = "replacement-with-no-policy-env"
    result = subprocess.run([
        sys.executable, str(workspace / ".taskplane/codex-hook.py"), "loop", "next",
    ], cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 1, result.stdout + result.stderr
    refusal = json.loads(result.stdout)
    assert refusal["schema"] == "taskplane.enforcement-refusal/v1"
    assert refusal["enforcement"]["mode"] == "strict"
    assert refusal["enforcement"]["status"] == "unproven"
    assert loop._load_raw(str(workspace)) == before


def test_fresh_session_reads_original_run_and_emits_valid_context(onboarded):
    workspace, home = onboarded
    # The run is produced normally before the process/session is replaced.
    initialized = json.loads(_fresh_process(
        workspace, "loop", "init", "Repair the native harness with durable scope", home=home))
    assert not initialized.get("error"), initialized
    import loop
    saved = loop.load(str(workspace))
    state_path = Path(storage.load_workspace_locator(str(workspace))["home"]) / "runs" / loop.load(str(workspace))["run_id"] / "manifest.json"
    state_bytes = state_path.read_bytes()
    event = {"hook_event_name": "SessionStart", "session_id": "fresh-session",
             "turn_id": "resume-1", "source": "resume", "cwd": str(workspace)}
    output = json.loads(_fresh_process(
        workspace, "context", event=event, hook_path="bridge"))
    context = output["hookSpecificOutput"]
    assert context["hookEventName"] == "SessionStart"
    assert "Repair the native harness with durable scope" in context["additionalContext"]
    assert saved["run_id"] in context["additionalContext"]
    assert "loop next" in context["additionalContext"]
    assert caps.runtime_hook_observations(
        str(home), session_id="fresh-session", workspace=str(workspace))[
            "repository_bridge_loaded"].status == "supported"
    assert state_path.read_bytes() == state_bytes
    # The other installed hook sees the same event. It must restore useful
    # context while its lifecycle effects remain claimed exactly once.
    duplicate = json.loads(_fresh_process(
        workspace, "context", event=event, hook_path="native"))
    assert saved["run_id"] in duplicate["hookSpecificOutput"]["additionalContext"]
    assert state_path.read_bytes() == state_bytes


def test_context_uses_event_workspace_instead_of_host_process_cwd(
        onboarded, tmp_path, monkeypatch, capsys):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "Event workspace scope", home=home)
    import io
    from argparse import Namespace
    monkeypatch.chdir(tmp_path)
    event = {"hook_event_name": "SessionStart", "session_id": "event-session",
             "turn_id": "event-turn", "cwd": str(workspace)}
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "bridge")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    seen = []

    def handler(args):
        seen.append(args.workspace)
        print("[taskplane] context")
        return 0

    assert cli._run_hook_command(Namespace(
        cmd="context", workspace=None, fn=handler)) == 0
    assert seen == [str(workspace)]
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "hookEventName"] == "SessionStart"


def test_receipts_survive_another_live_session(tmp_path):
    home = str(tmp_path / "receipts")
    workspace = str(tmp_path / "workspace")
    for session in ("session-a", "session-b"):
        for path in ("native", "bridge"):
            caps.record_runtime_hook_receipt(home, hook_path=path, event={
                "session_id": session, "hook_event_name": "SessionStart",
                "turn_id": session, "cwd": workspace,
            })
    for session in ("session-a", "session-b"):
        observations = caps.runtime_hook_observations(
            home, session_id=session, workspace=workspace)
        assert observations["repository_bridge_loaded"].status == "supported"
        assert observations["stable_event_identity"].status == "supported"
    assert caps.runtime_hook_observations(
        home, session_id="foreign", workspace=workspace) == {}


def test_late_bridge_converges_on_a_later_real_event(tmp_path):
    home = str(tmp_path / "receipts")
    event = {"session_id": "session", "hook_event_name": "PreToolUse",
             "tool_use_id": "before-bridge", "cwd": str(tmp_path)}
    caps.record_runtime_hook_receipt(home, hook_path="native", event=event)
    event = {**event, "tool_use_id": "after-bridge"}
    for path in ("native", "bridge"):
        caps.record_runtime_hook_receipt(home, hook_path=path, event=event)
    observations = caps.runtime_hook_observations(
        home, session_id="session", workspace=str(tmp_path))
    assert observations["stable_event_identity"].status == "supported"


def test_bridge_receipts_preserve_both_workspaces_in_one_session(tmp_path):
    home = str(tmp_path / "receipts")
    for workspace in ("first", "second"):
        caps.record_runtime_hook_receipt(home, hook_path="bridge", event={
            "session_id": "session", "hook_event_name": "SessionStart",
            "cwd": str(tmp_path / workspace),
        })
    for workspace in ("first", "second"):
        assert caps.runtime_hook_observations(
            home, session_id="session", workspace=str(tmp_path / workspace))[
                "repository_bridge_loaded"].status == "supported"


@pytest.mark.parametrize("case", ["foreign-checkout", "relative-home", "corrupt"])
def test_invalid_run_locator_refuses_before_receipt(onboarded, tmp_path, case):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "Bound scope", home=home)
    path = Path(storage._locator_path(str(workspace)))
    value = json.loads(path.read_text())
    if case == "foreign-checkout":
        value["checkout"] = str(tmp_path / "foreign")
    elif case == "relative-home":
        value["home"] = "../state"
    if case == "corrupt":
        path.write_text("{broken")
    else:
        path.write_text(json.dumps(value))
    with pytest.raises(storage.StorageIdentityError):
        storage.bind_hook_taskplane_home(str(workspace), {}, hook_path="bridge")
    assert not (home / "host-receipts").exists()


def test_resume_cannot_retarget_existing_state(onboarded, tmp_path):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "Bound scope", home=home)
    path = Path(storage._locator_path(str(workspace)))
    before = path.read_bytes()
    with pytest.raises(storage.StorageIdentityError, match="does not match"):
        storage.bind_hook_taskplane_home(
            str(workspace), {"TASKPLANE_HOME": str(tmp_path / "foreign")},
            hook_path="bridge")
    assert path.read_bytes() == before


def test_stateless_phase_bootstrap_retains_authority_and_pending_operation(tmp_path, monkeypatch):
    from taskplane.tests.phase_fixture import _supporting_pristine_phase_run
    from taskplane import loop
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    original = loop.load(ws)
    authority = original["_stage_native_root_authority"]
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "replacement-root-session")

    first = loop.next_action(ws)
    assert "error" not in first, first
    first_phase = loop._phase_bridge_pending(ws, loop.load(ws))["phase_runtime"]
    assert first_phase, first
    after = store.load(run_id)
    assert loop.load(ws)["_stage_native_root_authority"] == authority
    scope = loop.resume(ws)
    assert scope["requirement_id"] == original["requirement_id"]
    assert store.load(run_id) == after
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "another-root-session")
    second = loop.next_action(ws)
    assert "error" not in second, second
    assert loop._phase_bridge_pending(ws, loop.load(ws))["phase_runtime"]["status"] == "pending"
    assert loop._phase_bridge_pending(ws, loop.load(ws))["phase_runtime"]["operation_id"] == first_phase["operation_id"]
    assert store.load(run_id) == after


def test_read_only_resume_preserves_exact_saved_task_scopes(onboarded):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "Exact recovery scope", home=home)
    import loop
    # A persisted-plan fixture exercises projection only, never dispatch or
    # live authority. No test fixture enters the user's active run store.
    state = loop.load(str(workspace))
    state["tasks"] = [{"id": "T1", "scope": ["taskplane/storage.py"], "status": "running"},
                      {"id": "T2", "scope": ["taskplane/tp.py"], "status": "pending"}]
    loop.save(str(workspace), state)
    path = Path(storage.load_workspace_locator(str(workspace))["home"]) / "runs" / loop.load(str(workspace))["run_id"] / "manifest.json"
    before = path.read_bytes()
    recovered = json.loads(_fresh_process(workspace, "loop", "resume", session=None))
    assert recovered["tasks"] == state["tasks"]
    assert path.read_bytes() == before


def test_initialization_does_not_waive_dispatch_enforcement(onboarded, monkeypatch, capsys):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "No fake dispatch readiness", home=home)
    from argparse import Namespace
    import loop
    monkeypatch.setenv("TASKPLANE_ENFORCE_SCREEN", "strict")
    for key in caps._ENV_OBSERVATIONS:
        monkeypatch.delenv(key, raising=False)
    # Isolate the transport gate from unrelated graph prerequisites.
    monkeypatch.setattr(cli, "_graph_quality_refusal", lambda *args: None)
    called = []
    monkeypatch.setattr(loop, "next_action", lambda *args, **kwargs: called.append(True))
    before = (Path(storage.load_workspace_locator(str(workspace))["home"]) / "runs" / loop.load(str(workspace))["run_id"] / "manifest.json").read_bytes()
    result = cli.cmd_loop(Namespace(workspace=str(workspace), loop_action="next",
                                    advisory=False, by=None))
    assert result != 0
    assert json.loads(capsys.readouterr().out)["error"]
    assert not called
    assert (Path(storage.load_workspace_locator(str(workspace))["home"]) / "runs" / loop.load(str(workspace))["run_id"] / "manifest.json").read_bytes() == before


def test_locator_repair_remains_reachable_but_home_conflicts_refuse(onboarded, tmp_path):
    workspace, home = onboarded
    _fresh_process(workspace, "loop", "init", "Recover binding", home=home)
    from argparse import Namespace
    called = []
    args = Namespace(cmd="repository", repository_action="prepare", workspace=str(workspace),
                     fn=lambda args: called.append(args))
    original = dict(os.environ)
    try:
        os.environ["TASKPLANE_HOME"] = str(tmp_path / "wrong-home")
        with pytest.raises(storage.StorageIdentityError, match="does not match"):
            cli._run_hook_command(args)
        assert not called
        Path(storage._locator_path(str(workspace))).write_text("{corrupt")
        cli._run_hook_command(args)
        assert called == [args]
    finally:
        os.environ.clear()
        os.environ.update(original)


@pytest.mark.parametrize("command", ["help", "version"])
def test_discovery_is_available_without_resolving_run_storage(monkeypatch, command):
    from argparse import Namespace
    monkeypatch.setattr(storage, "load_workspace_locator",
                        lambda workspace: pytest.fail("help inspected run state"))
    assert cli._run_hook_command(Namespace(cmd=command, fn=lambda args: 0)) == 0


def test_expired_phase_returns_recovery_not_an_infinite_wait(tmp_path, monkeypatch):
    from datetime import datetime
    from taskplane.tests.phase_fixture import _supporting_pristine_phase_run
    from taskplane import loop, review_evidence
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    prepared = loop.next_action(ws)
    prepared_phase = loop._phase_bridge_pending(ws, loop.load(ws))["phase_runtime"]
    reference = prepared_phase["reference"]
    material = review_evidence.ArtifactStore(ws).read(reference)
    before = store.load(run_id)
    deadline = datetime.fromisoformat(material["bindings"]["deadline"]).timestamp()
    monkeypatch.setattr(loop.time, "time", lambda: deadline + 1)
    pending = loop._phase_bridge_pending(ws, loop._load_raw(ws))
    assert pending["phase_runtime"]["status"] == "recovery_required"
    assert pending["phase_runtime"]["operation_id"] == prepared_phase["operation_id"]
    assert pending["phase_runtime"]["reference"] == reference
    assert pending["dispatch_allowed"] is False
    assert "wait_policy" not in pending and "task_name" not in pending
    assert loop._phase_bridge_pending(ws, loop._load_raw(ws)) == pending
    assert store.load(run_id) == before


@pytest.mark.parametrize("case", ["worker", "changed-authority"])
def test_resumed_root_still_rejects_invalid_authority(tmp_path, monkeypatch, case):
    from taskplane.tests.phase_fixture import _supporting_pristine_phase_run
    from taskplane import loop
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "replacement-session")

    state = loop.load(ws)
    if case == "worker":
        monkeypatch.setattr(loop.tp, "task_slot", lambda: "worker-slot")
    else:
        state["_stage_native_root_authority"]["actor"] = "foreign-actor"
        loop.save(ws, state)
    before = store.load(run_id)
    result = loop.next_action(ws)
    assert result["obligations"].get("error"), result
    assert result["stage_runtime_dispatch"] is None
    assert store.load(run_id) == before
