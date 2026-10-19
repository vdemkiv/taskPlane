"""Lifecycle regression cases use observed fixture events, not live host claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from taskplane import flow, workflow as w, workflow_host as host, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decision, decide


@pytest.mark.parametrize("bad", ["future", "before_run", "before_checkpoint", "naive", "presentation"])
@pytest.mark.parametrize("explicit", [False, True])
def test_decision_chronology_is_checked_before_mutation(tmp_path, bad, explicit):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    value = decision(state)
    if explicit:
        value["checkpoint_explicit"] = True
        value["excerpt"] = "Approved " + value["binding"]["checkpoint"]
    submitted = datetime.fromisoformat(w.current(state)["submitted_at"])
    if bad == "future":
        value["source"]["observed_at"] = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    elif bad == "before_run":
        value["source"]["observed_at"] = (datetime.fromisoformat(state["started_at"]) - timedelta(seconds=1)).isoformat()
    elif bad == "before_checkpoint":
        value["source"]["observed_at"] = (submitted - timedelta(microseconds=1)).isoformat()
    elif bad == "naive":
        value["source"]["observed_at"] = datetime.now().isoformat()
    else:
        if explicit:
            value["presentation"]["at"] = (submitted - timedelta(days=1)).isoformat()
            assert w.current(decide(controller, state, value))["decision"] == "approved"
            return  # Explicit identity removes presentation ordering only.
        value["presentation"]["at"] = (submitted - timedelta(microseconds=1)).isoformat()
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(controller, state, value)
    assert controller._path().read_bytes() == before


def test_decision_exact_replay_keeps_recording_time(tmp_path):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    value = decision(state)
    accepted = decide(controller, state, value)
    provenance = accepted["decisions"][value["event_id"]]["provenance"]
    assert provenance["chronology"] == "verified/v1"
    assert provenance["recorded_at"] >= value["source"]["observed_at"]
    assert decide(controller, state, value) == accepted
    conflict = deepcopy(value)
    conflict["source"]["observed_at"] = datetime.now(timezone.utc).isoformat()
    with pytest.raises(w.Refusal, match="replay"):
        decide(controller, state, conflict)


def test_explicit_checkpoint_reference_must_match_bound_checkpoint(tmp_path):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    value = decision(state, text="Approved " + "0" * 32)
    value["checkpoint_explicit"] = True
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal) as failure:
        decide(controller, state, value)
    assert failure.value.reason == "invalid_evidence"
    assert controller._path().read_bytes() == before




def event(workspace, call="call-1", **extra):
    return {"hook_event_name": "PreToolUse", "cwd": str(workspace), "session_id": "root",
            "tool_name": "Read", "tool_input": {"file_path": "product.json"},
            "call_id": call, **extra}


def test_admitted_call_keeps_original_run_through_replacement_and_restart(tmp_path):
    controller, first = setup(tmp_path)
    before = event(tmp_path)
    controller.guard(before, first["run"])
    second = controller.start({"scope": first["scope"], "request_reference": "user/restart",
                               "replace_run": first["run"], "expected_revision": first["revision"]})
    restarted = host.Controller(tmp_path, "root", host.installed_adapter("codex"))
    post = {**before, "hook_event_name": "PostToolUse", "tool_response": {"content": "ok"}}
    pair = restarted.complete_admission(post)
    assert pair["run"] == first["run"] and pair["state"] == "completed"
    assert restarted.report()["run"] == second["run"]
    assert restarted.complete_admission(post) == pair
    conflict = deepcopy(post)
    conflict["tool_input"]["file_path"] = "other.json"
    with pytest.raises(w.Refusal, match="call"):
        restarted.complete_admission(conflict)


def test_call_id_conflict_and_missing_identity_do_not_invent_pairing(tmp_path):
    controller, state = setup(tmp_path)
    before = event(tmp_path)
    controller.guard(before, state["run"])
    changed = deepcopy(before)
    changed["tool_input"]["file_path"] = "another.json"
    with pytest.raises(w.Refusal, match="call"):
        controller.guard(changed, state["run"])
    assert controller.complete_admission({**before, "call_id": "unknown"}) is None
    assert controller.complete_admission({k: v for k, v in before.items() if k != "call_id"}) is None


def test_finished_followup_is_observational_and_cannot_reopen_authority(tmp_path):
    controller, state = setup(tmp_path, standalone=True)
    state = decide(controller, submit(controller, state))
    state = controller.apply("finish", state["run"], expected_revision=state["revision"])
    observed = event(tmp_path, "after-finish")
    pair = controller.followup_admission(observed, state["run"])
    assert pair["run"] == state["run"] and pair["authority"] == "observation_only"
    controller.complete_admission({**observed, "hook_event_name": "PostToolUse"})
    assert controller.report()["status"] == "no_workflow"
    assert controller.report(state["run"])["finished"]
    with pytest.raises(w.Refusal):
        controller.guard({**observed, "tool_name": "Write", "tool_input": {"path": "product.json"}}, state["run"])


def test_hook_pair_attribution_survives_finish_and_records_followup(tmp_path):
    import shlex
    import sys
    from pathlib import Path
    controller, state = setup(tmp_path, standalone=True)
    local.Harness(tmp_path, 'root').bind(state)
    state = decide(controller, submit(controller, state))
    flow.append(tmp_path, dict(kind='start', run=state['run'], session='root', phase='product'))
    command = shlex.join([sys.executable, str(Path(flow.__file__).with_name('tp.py')), 'flow', 'finish',
                         '--workspace', str(tmp_path), '--run', state['run']])
    admitted = event(tmp_path, 'finish-call', tool_name='exec_command', tool_input={'cmd': command})
    flow.hook(admitted, governor=controller)
    controller.apply('finish', state['run'], expected_revision=state['revision'])
    flow.append(tmp_path, dict(kind='finish', run=state['run'], session='root'))
    flow.hook({**admitted, 'hook_event_name':'PostToolUse'}, governor=controller)
    followup = event(tmp_path, 'followup-call')
    flow.hook(followup, governor=controller)
    flow.hook({**followup, 'hook_event_name':'PostToolUse'}, governor=controller)
    admissions = list(controller._read(controller._path())['admissions'].values())
    assert any(row['authority'] == 'phase_grant' and row['state'] == 'completed' for row in admissions)
    assert any(row['authority'] == 'observation_only' and row['state'] == 'completed' for row in admissions)
    assert not any(row.get('kind') == 'hook' for row in flow.read_events(tmp_path))
    assert controller.report()['status'] == 'no_workflow'




def test_claude_cli_cannot_claim_child_from_inherited_root_environment(tmp_path, monkeypatch, capsys):
    controller, state = setup(tmp_path)
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'root')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    before = controller._path().read_bytes()
    assert flow.main(['worker', '--workspace', str(tmp_path), '--run', state['run'],
                      '--operation', 'claim', '--grant', 'unproven-child']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['reason'] == 'unsupported_authority'
    assert 'invocation identity is unavailable' in result['detail']
    assert controller._path().read_bytes() == before


@pytest.mark.parametrize('action', ['finish', 'replacement', 'next_start'])
def test_optional_harness_failure_preserves_committed_lifecycle(tmp_path, monkeypatch, capsys, action):
    controller, state = setup(tmp_path, standalone=True)
    state = decide(controller, submit(controller, state))
    if action == 'next_start':
        state = controller.apply('finish', state['run'], expected_revision=state['revision'])
    def unavailable(*args, **kwargs):
        raise OSError('injected optional harness write failure')
    monkeypatch.setattr(local.Harness, 'update' if action == 'finish' else 'bind', unavailable)
    if action == 'finish':
        args = ['finish', '--expected-revision', str(state['revision'])]
    else:
        (tmp_path/'restart-scope.json').write_text(json.dumps(state['scope']))
        args = ['start', '--standalone', '--phase', 'product', '--scope', 'restart-scope.json',
                '--request-reference', 'fixture/post-commit-restart']
        if action == 'replacement':
            args += ['--replace-run', state['run'], '--expected-revision', str(state['revision'])]
    assert flow.main([*args, '--workspace', str(tmp_path)], governor=controller) == 0
    result = json.loads(capsys.readouterr().out)['workflow']
    if action == 'finish':
        assert result['finished'] and result['observation_errors']
        assert controller.report(state['run'])['finished']
    else:
        assert result['run'] != state['run'] and result['start_observation_errors']
        assert controller.report()['run'] == result['run']
        assert not result['decisions']


def test_lifecycle_never_requires_readiness_projection(tmp_path, monkeypatch, capsys):
    controller, state = setup(tmp_path, standalone=True)
    state = decide(controller, submit(controller, state))
    def unexpected(*args, **kwargs):
        pytest.fail('Default lifecycle consulted retired readiness projection')
    monkeypatch.setattr(local.Harness, 'readiness', unexpected)
    assert flow.main(['finish', '--workspace', str(tmp_path), '--expected-revision', str(state['revision'])], governor=controller) == 0
    assert controller.report(state['run'])['finished']


def test_plan_scope_narrowing_keeps_checkpoint_presentation_identity(tmp_path):
    from taskplane.primitives import content_fingerprint
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    harness = local.Harness(tmp_path, "root")
    original = harness.checkpoint(state)
    accepted = deepcopy(state)
    stage = accepted["visits"][accepted["index"]]
    stage["phase"] = "plan"
    stage["decision"] = "approved"
    accepted["scope"]["paths"]["build"] = []
    stage["approved_scope_digest"] = content_fingerprint(accepted["scope"])
    assert harness.checkpoint(accepted) == original
    accepted["scope"]["paths"]["build"] = ["unexpected.py"]
    assert harness.checkpoint(accepted) != original






def test_sealed_contract_inspection_is_bounded_and_read_only(tmp_path):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    before = controller._path().read_bytes()
    result = controller.inspect(state["run"], "contract", "cli-reference", offset=0, limit=128)
    assert result["returned_bytes"] <= 128 and result["text"]
    for ref in ("../README.md", "/etc/passwd", "unknown"):
        with pytest.raises(w.Refusal):
            controller.inspect(state["run"], "contract", ref, offset=0, limit=128)
    with pytest.raises(w.Refusal):
        controller.inspect(state["run"], "contract", "cli-reference", offset=0, limit=32769)
    assert controller._path().read_bytes() == before


def test_prevalidation_and_submission_share_checks_without_prevalidation_writes(tmp_path):
    controller, state = setup(tmp_path)
    from taskplane.tests.test_workflow_evidence import prepare
    _, output, _ = prepare(tmp_path)
    output.update(run=state["run"], visit=w.current(state)["id"])
    (tmp_path / "product.json").write_text(json.dumps(output))
    before = controller._path().read_bytes()
    result = controller.prevalidate(state["run"], revision=state["revision"],
                                    output="product.json", tasks="tasks.json")
    assert result["valid"] and not result.get("checkpoint")
    assert controller._path().read_bytes() == before
    output["criteria"] = ["foreign"]
    (tmp_path / "product.json").write_text(json.dumps(output))
    with pytest.raises(w.Refusal):
        controller.prevalidate(state["run"], revision=state["revision"],
                               output="product.json", tasks="tasks.json")
    with pytest.raises(w.Refusal):
        controller.apply("submit", state["run"], expected_revision=state["revision"],
                         output="product.json", tasks="tasks.json")
