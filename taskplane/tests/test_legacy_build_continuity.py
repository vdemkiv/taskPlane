"""Legacy saved-shape regressions; generated data and temporary stores only."""
import copy
import hashlib
import json
from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest

from taskplane import delivery_policy, loop, loop_recovery, run_context, settings


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def sealed(value):
    return dict(value, fingerprint=fingerprint(value))


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    ws = str(tmp_path / "repo")
    root = tmp_path / "repo"
    (root / "plan").mkdir(parents=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TASKPLANE_TASK_ID", raising=False)
    monkeypatch.setattr(loop.tp, "task_slot", lambda: None)
    monkeypatch.setattr(loop.tp, "git_head", lambda _: "a" * 40)
    monkeypatch.setattr(loop.tp, "workspace_fingerprint", lambda _: "b" * 64)
    config = settings.load_settings(environment={})
    snapshot = config.to_dict()
    snapshot.pop("phase_definitions")
    digest = settings.settings_digest(snapshot)
    requirement = {"id": "R-0001", "title": "Original requirement", "contracts": [
        {"id": "contract:required", "relation": "changes"}]}
    monkeypatch.setattr(loop.reqs, "get_requirement", lambda *_: copy.deepcopy(requirement))
    declared = [{"id": f"T{i:02}", "req": "R-0001", "scope": [f"src/t{i}.py"],
                 "tests": f"pytest tests/test_t{i}.py", "deps": [], "type": "code", "contracts": ["contract:local"]}
                for i in range(23)]
    plan = {"requirement": "R-0001", "design_fingerprint": "d" * 64,
            "delivery_mode": "build", "automatic_lenses": [],
            "plan_authority": "original human approval", "tasks": declared}
    policy = sealed({"schema": "taskplane.run-review-timing/v1", "run_id": "legacy-run",
        "design_fingerprint": "d" * 64, "plan_fingerprint": fingerprint(plan),
        "by": "human:test", "instruction": "Review Build at Engineering",
        "mode": "build-tests-then-em-lenses"})
    tasks = copy.deepcopy(declared)
    for i, task in enumerate(tasks):
        task["contracts"] = copy.deepcopy(requirement["contracts"]) + task["contracts"]
        task.update(status="passed" if i < 19 else "pending", fix_cycles=0)
        if 2 <= i < 19:
            task["evaluation"] = {"task": task["id"], "status": "deferred",
                "verdict": "non-judged", "reason_code": "human-deferred-to-em",
                "policy_fingerprint": policy["fingerprint"], "build_candidate": "c" * 40,
                "suite_key": "e" * 64}
        elif i < 2:
            task.update(evaluation={"task": task["id"], "status": "unavailable",
                "verdict": "non-judged", "outage_identity": {"fingerprint": "f" * 64}},
                human_resolution={"decision": "pass", "actor": "human:test",
                    "outage_fingerprint": "f" * 64}, reanchor_authority={"fingerprint": "e" * 64},
                target_commit="c" * 40)
    state = {"run_id": "legacy-run", "requirement_id": "R-0001", "step": "execute",
        "current_task": 19, "tasks": tasks, "baseline": "c" * 40,
        "design_fingerprint": "d" * 64, "settings_digest": digest,
        "run_artifact_binding": {"run_id": "legacy-run", "settings_digest": digest},
        "review_timing_override": policy, "review_timing_override_history": [],
        "deferred_review_tasks": [t["id"] for t in tasks[2:19]], "checkpoints": ["plan", "em"],
        "dispatch_telemetry": {"total_tokens": 500000, "unknown": None},
        "delivery_mode_receipt": delivery_policy.validate_plan_mode(plan,
            plan_fingerprint=fingerprint(plan), source_sha="a" * 40)}
    loop.save(ws, state)
    amended = copy.deepcopy(plan)
    amended["tasks"][19]["scope"].append("taskplane/run_context.py")
    after_bytes = (json.dumps(amended, indent=2) + "\n").encode()
    (root / "plan/tasks.json").write_bytes(after_bytes)
    request = {"schema": "taskplane.legacy-build-amendment/v1", "run_id": "legacy-run",
        "requirement_id": "R-0001", "task_id": "T19", "baseline": "c" * 40,
        "design_fingerprint": "d" * 64, "settings_digest": digest,
        "before_state_fingerprint": loop_recovery.legacy_state_fingerprint(state), "candidate": "a" * 40,
        "source_fingerprint": "b" * 64, "before_plan": plan,
        "after_plan_sha256": hashlib.sha256(after_bytes).hexdigest(),
        "settings_snapshot": snapshot, "resource_limits": "advisory", "observation_checkpoint": None,
        "requirement_fingerprint": loop_recovery._requirement_fingerprint(requirement)}
    return ws, root, state, request


def invoke(legacy, request=None, **kwargs):
    ws, root, _, original = legacy
    supplied = request or original
    path = root / "amendment.json"
    path.write_text(json.dumps(supplied))
    return loop.continue_build(ws, source=str(path), by=kwargs.get("by", "human:test"),
        request=kwargs.get("instruction", "Append the exact repair scope; limits are advisory"),
        expected_fingerprint=kwargs.get("expected_fingerprint", fingerprint(supplied)), check=kwargs.get("check", False),
        observation_authority=kwargs.get("observation_authority"))


def test_actual_legacy_shape_continues_without_reanchoring_deferred_passes(legacy):
    ws, _, before, _ = legacy
    result = invoke(legacy)
    assert not result.get("error"), result
    assert result["continued"] is True and result["independent_review_passed"] is False
    after = loop._load_raw(ws)
    assert after["tasks"][:19] == before["tasks"][:19]
    assert after["tasks"][19]["status"] == "pending"
    for key in ("run_id", "requirement_id", "baseline", "design_fingerprint", "current_task",
                "dispatch_telemetry", "deferred_review_tasks", "checkpoints", "run_artifact_binding"):
        assert after[key] == before[key]
    assert not after.get("_stage_run_binding") and not after.get("_stage_native_root_authority")
    assert invoke(legacy)["replay"] is True
    assert loop._load_raw(ws) == after
    assert run_context.selected(after) is False
    with run_context.bind(ws, after):
        recovered = settings.load_settings(environment={"TASKPLANE_MODEL_DEEP": "changed"})
        assert recovered.digest == before["settings_digest"]
        assert recovered.to_dict() == after["settings_snapshot"]
        assert "phase_definitions" not in recovered.to_dict()
    assert run_context.resource_limits_advisory(ws) is True


@pytest.mark.parametrize("damage", ["scope", "tests", "completed-declaration", "plan-metadata", "plan-bytes"])
def test_altered_plan_refuses_without_changing_saved_work(legacy, damage):
    ws, root, before, packet = legacy
    plan = json.loads((root / "plan/tasks.json").read_text())
    if damage == "scope": plan["tasks"][19]["scope"] = ["unrelated.py"]
    if damage == "tests": plan["tasks"][19]["tests"] = "true"
    if damage == "completed-declaration": plan["tasks"][1]["scope"].append("unrelated.py")
    if damage == "plan-metadata": plan["design_fingerprint"] = "e" * 64
    raw = json.dumps(plan).encode()
    (root / "plan/tasks.json").write_bytes(raw)
    if damage != "plan-bytes": packet["after_plan_sha256"] = hashlib.sha256(raw).hexdigest()
    assert invoke(legacy).get("error")
    assert loop._load_raw(ws) == before


@pytest.mark.parametrize("damage", ["forged-verdict", "forged-authority", "stale-policy", "missing-suite",
    "foreign-run", "foreign-binding", "settings", "foreign-actor", "worker", "source", "candidate", "request", "packet",
    "extra-contract", "foreign-requirement"])
def test_foreign_stale_or_forged_inputs_fail_closed(legacy, monkeypatch, damage):
    ws, _, before, packet = legacy
    state = copy.deepcopy(before)
    kwargs = {}
    if damage == "forged-verdict": state["tasks"][2]["evaluation"]["verdict"] = "pass"
    if damage == "forged-authority": state["tasks"][2]["reanchor_authority"] = {"fingerprint": "f" * 64}
    if damage == "stale-policy": state["tasks"][2]["evaluation"]["policy_fingerprint"] = "f" * 64
    if damage == "missing-suite": state["tasks"][2]["evaluation"].pop("suite_key")
    if damage == "foreign-run": packet["run_id"] = "another-run"
    if damage == "foreign-binding": state["run_artifact_binding"]["run_id"] = "another-run"
    if damage == "settings": packet["settings_snapshot"]["limits"]["budgets"]["max_actions"] += 1
    if damage == "foreign-actor": kwargs["by"] = "human:another"
    if damage == "worker": monkeypatch.setattr(loop.tp, "task_slot", lambda: "worker-slot")
    if damage == "source": packet["source_fingerprint"] = "f" * 64
    if damage == "candidate": packet["candidate"] = "f" * 40
    if damage == "request": kwargs["instruction"] = ""
    if damage == "packet": kwargs["expected_fingerprint"] = "f" * 64
    if damage == "extra-contract": state["tasks"][19]["contracts"].append("contract:foreign")
    if damage == "foreign-requirement": packet["requirement_fingerprint"] = "f" * 64
    # Even a freshly fingerprinted malformed proposal cannot mint pass evidence.
    loop.save(ws, state)
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    assert invoke(legacy, **kwargs).get("error")
    assert loop._load_raw(ws) == state


@pytest.mark.parametrize("damage", ["result", "settings", "resource", "run", "revocation", "plan"])
def test_fresh_pickup_and_policy_guard_refuse_post_approval_drift(legacy, damage):
    ws, root, _, _ = legacy
    assert not invoke(legacy).get("error")
    state = loop._load_raw(ws)
    if damage == "result": state["tasks"][2]["evaluation"]["verdict"] = "pass"
    if damage == "settings": state["settings_snapshot"]["limits"]["budgets"]["max_actions"] += 1
    if damage == "resource": state["resource_policy"]["actor"] = "human:another"
    if damage == "run": state["run_id"] = "foreign"
    if damage == "revocation": state["legacy_build_continuation"]["revoked"] = True
    if damage == "plan": (root / "plan/tasks.json").write_text("{}")
    loop.save(ws, state)
    with pytest.raises(ValueError):
        with run_context.bind(ws, state):
            pytest.fail("admitted changed authority")
    with pytest.raises(ValueError):
        run_context.resource_limits_advisory(ws)
    assert invoke(legacy).get("error")
    assert loop._load_raw(ws) == state


def test_check_and_interrupted_plan_to_state_commit_are_recoverable(legacy, monkeypatch):
    ws, root, before, _ = legacy
    plan_bytes = (root / "plan/tasks.json").read_bytes()
    result = invoke(legacy, check=True)
    assert result["checked"] is True and result["continued"] is False
    assert loop._load_raw(ws) == before
    with pytest.raises(ValueError, match="continue-build"):
        with run_context.bind(ws, before):
            pytest.fail("an externally prepared Plan became authority")
    original_save = loop.save
    with monkeypatch.context() as patch:
        patch.setattr(loop, "save", lambda *_: (_ for _ in ()).throw(OSError("interrupted atomic commit")))
        assert "interrupted atomic commit" in invoke(legacy)["error"]
    assert loop._load_raw(ws) == before
    assert (root / "plan/tasks.json").read_bytes() == plan_bytes
    result = invoke(legacy)
    assert result["continued"] is True and result["replay"] is False
    committed = loop._load_raw(ws)
    with monkeypatch.context() as patch:
        patch.setattr(loop, "save", lambda *_: pytest.fail("replay wrote loop state"))
        assert invoke(legacy)["replay"] is True
    assert loop._load_raw(ws) == committed
    assert loop.save == original_save
    assert invoke(legacy, instruction="different approval").get("error")


def test_concurrent_scope_change_is_not_lost(legacy, monkeypatch):
    ws, _, before, _ = legacy
    original = loop.mutate
    @contextmanager
    def race(workspace):
        changed = copy.deepcopy(before)
        changed["tasks"][0]["scope"].append("unrelated.py")
        loop.save(workspace, changed)
        with original(workspace) as state:
            yield state
    monkeypatch.setattr(loop, "mutate", race)
    assert "concurrently" in invoke(legacy)["error"]
    assert loop._load_raw(ws)["tasks"][0]["scope"][-1] == "unrelated.py"


def _add_meter(legacy):
    from taskplane import dispatch_telemetry, native_session_meter
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, root, state, packet = legacy
    authority = b"generated-test-authority-only"
    segment = root / "root.jsonl"
    def observation(sequence, total):
        _write_segment(segment, session_id="root", total=total, output=1, ordinal=sequence * 3)
        return native_session_meter.seal_root_observation(native_session_meter.read_snapshot(str(segment)),
            sequence=sequence, session_role="root", status_receipt_fingerprint="e" * 64,
            terminal_reason=None, authority=authority)
    meter = native_session_meter.fold_root_observations([observation(1, 100)], authority=authority)
    ledger = dispatch_telemetry.new_ledger(run_id=state["run_id"], source_sha="a" * 40,
        design_fingerprint=state["design_fingerprint"], plan_fingerprint=fingerprint(packet["before_plan"]), started_at=1)
    dispatch_telemetry.configure_root_admission(ledger,
        root_session_settings=packet["settings_snapshot"]["workflow"]["root_session"], settings_digest=state["settings_digest"])
    dispatch_telemetry.record_root_meter(ledger, meter, observation_authority=authority)
    state["dispatch_telemetry"] = ledger
    state["root_hygiene"] = {"status": "open", "meter": meter, "policy": "original",
        "observation_authority_fingerprint": hashlib.sha256(authority).hexdigest()}
    loop.save(ws, state)
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    packet["observation_checkpoint"] = loop_recovery.legacy_observation_checkpoint(state)
    def advance():
        current = loop._load_raw(ws)
        newer = native_session_meter.fold_root_observations([observation(2, 200)], authority=authority,
            prior=current["root_hygiene"]["meter"]["watermark"])
        dispatch_telemetry.record_root_meter(current["dispatch_telemetry"], newer, observation_authority=authority)
        current["root_hygiene"]["meter"] = newer
        loop.save(ws, current)
        return current
    return authority, advance


def test_authenticated_meter_can_advance_between_check_and_commit(legacy, monkeypatch):
    ws, _, _, packet = legacy
    authority, advance = _add_meter(legacy)
    assert invoke(legacy, check=True, observation_authority=authority)["checked"] is True
    newer = advance()
    assert loop_recovery.legacy_state_fingerprint(newer) == packet["before_state_fingerprint"]
    result = invoke(legacy, observation_authority=authority)
    assert not result.get("error"), result
    after = loop._load_raw(ws)
    assert after["root_hygiene"]["meter"] == newer["root_hygiene"]["meter"]
    assert after["dispatch_telemetry"] == newer["dispatch_telemetry"]
    assert "authenticator" not in json.dumps(packet)


@pytest.mark.parametrize("damage", ["policy", "ledger-policy", "worker-binding", "forged-meter", "authority", "backwards"])
def test_meter_exemption_cannot_hide_authority_or_usage_drift(legacy, damage):
    ws, _, _, packet = legacy
    authority, _ = _add_meter(legacy)
    state = loop._load_raw(ws)
    if damage == "policy": state["root_hygiene"]["policy"] = "forged"
    if damage == "ledger-policy": state["dispatch_telemetry"]["root_admission"]["policy"]["root_budget_tokens"] += 1
    if damage == "worker-binding": state["dispatch_telemetry"]["bindings"].append({"forged": True})
    if damage == "forged-meter": state["root_hygiene"]["meter"]["watermark"]["authenticator"] = "f" * 64
    if damage == "authority": authority = b"foreign-test-authority"
    if damage == "backwards": packet["observation_checkpoint"]["sequence"] += 1
    loop.save(ws, state)
    assert invoke(legacy, observation_authority=authority).get("error")
    assert loop._load_raw(ws) == state


def test_original_review_timing_advances_only_verified_build_and_keeps_em_due(legacy):
    ws, _, _, _ = legacy
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state["step"] = "evaluate"
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="passing Build tests"):
        loop._advance_build_with_review_deferred(ws, state)
    assert state == before
    state["_suite_evidence"] = {"T19": {"returncode": 0,
        "command": state["tasks"][19]["tests"], "key": "a" * 64}}
    assert loop._advance_build_with_review_deferred(ws, state) is True
    assert state["step"] == "execute" and state["current_task"] == 20
    result = state["tasks"][19]
    assert result["status"] == "passed"
    assert result["evaluation"]["verdict"] == "non-judged"
    assert result["evaluation"]["status"] == "deferred"
    assert "target_commit" not in result and "reanchor_authority" not in result
    assert "T19" in state["deferred_review_tasks"]
    assert loop_recovery.legacy_review_policy(state) is not None
    assert state["baseline"] == before["baseline"]
    assert state["legacy_build_continuation"]["independent_review_passed"] is False


def test_failed_or_dispatched_build_cannot_be_deferred(legacy):
    ws, _, _, _ = legacy
    invoke(legacy)
    state = loop._load_raw(ws)
    state.update(step="evaluate", _build_failed=True)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        loop._advance_build_with_review_deferred(ws, state)
    assert state == before


def test_public_cli_check_and_apply_use_same_explicit_command_without_outbox_flush(legacy, monkeypatch, capsys):
    from taskplane import tp as cli
    ws, root, before, packet = legacy
    path = root / "amendment.json"
    path.write_text(json.dumps(packet))
    monkeypatch.setitem(sys.modules, "loop", loop)
    monkeypatch.setattr(loop, "load", lambda *_: pytest.fail("read-only check flushed the authority outbox"))
    args = SimpleNamespace(cmd="loop", fn=cli.cmd_loop, loop_action="continue-build", workspace=ws,
        amendment_from=str(path), by="human:test", request="Append the exact repair scope; limits are advisory",
        fingerprint=fingerprint(packet), check=True)
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["checked"] is True
    assert loop._load_raw(ws) == before
    args.check = False
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["continued"] is True
    committed = loop._load_raw(ws)
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["replay"] is True
    assert loop._load_raw(ws) == committed


def test_authenticated_meter_arriving_at_state_lock_is_preserved(legacy, monkeypatch):
    ws, _, _, _ = legacy
    authority, advance = _add_meter(legacy)
    original = loop.mutate
    latest = []
    @contextmanager
    def race(workspace):
        latest.append(advance())
        with original(workspace) as state:
            yield state
    monkeypatch.setattr(loop, "mutate", race)
    result = invoke(legacy, observation_authority=authority)
    assert not result.get("error"), result
    assert loop._load_raw(ws)["dispatch_telemetry"] == latest[0]["dispatch_telemetry"]
    assert loop._load_raw(ws)["root_hygiene"]["meter"] == latest[0]["root_hygiene"]["meter"]
