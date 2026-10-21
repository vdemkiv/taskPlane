"""Lifecycle regression cases use observed fixture events, not live host claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from taskplane import flow, workflow as w, workflow_host as host, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decision, decide
from taskplane.workflow_approval import affirmative_consent


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


@pytest.mark.parametrize("explicit", [False, True])
def test_named_checkpoint_cannot_approve_another_checkpoint(tmp_path, explicit):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    value = decision(state, text="Approved " + "0" * 32)
    value["checkpoint_explicit"] = explicit
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal, match="different checkpoint"):
        decide(controller, state, value)
    assert controller._path().read_bytes() == before


def test_named_checkpoint_does_not_hide_unclassified_approval_suffix(tmp_path):
    controller, state = setup(tmp_path)
    state = submit(controller, state)
    value = decision(state, text="Approved " + w.binding(state, w.current(state)["packet"])["checkpoint"]
                     + ". I need more time to deliberate.")
    value.update(checkpoint_explicit=True, choice="approved")
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal, match="unclear"):
        decide(controller, state, value)
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


def test_closed_latest_journal_selection_never_resurrects_replaced_run():
    rows = [{"kind": "start", "run": "a", "session": "root"},
            {"kind": "start", "run": "b", "session": "root", "replaces": "a"},
            {"kind": "finish", "run": "b", "session": "root"}]
    assert flow.active_run(rows, "root") is None


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
    rows = [row for row in flow.read_events(tmp_path) if row.get('kind') == 'hook']
    assert len(rows) == 4 and all(row['run'] == state['run'] for row in rows)
    assert rows[1]['admission']['authority'] == 'phase_grant'
    assert rows[-1]['admission']['authority'] == 'observation_only'
    assert controller.report()['status'] == 'no_workflow'


def test_parent_runtime_mismatch_revokes_previous_pair(tmp_path):
    controller, state = setup(tmp_path)
    from taskplane.host_capabilities import runtime_identity
    first = event(tmp_path, 'first-parent-pair', taskplane_automatic_hook=True,
                  taskplane_runtime_identity=runtime_identity())
    controller.guard(first, state['run'])
    controller.complete_admission({**first, 'hook_event_name':'PostToolUse'})
    assert controller.report()['parent_hook_readiness']['admitted']
    second = {**first, 'call_id':'different-runtime', 'taskplane_runtime_identity':{'root':'foreign'}}
    controller.guard(second, state['run'])
    controller.complete_admission({**second, 'hook_event_name':'PostToolUse'})
    assert not controller.report().get('parent_hook_readiness', {}).get('admitted')


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


@pytest.mark.parametrize('action', ['finish', 'replacement_select', 'replacement_bind', 'next_start_bind'])
def test_post_commit_harness_failure_preserves_closure_and_success(tmp_path, monkeypatch, capsys, action):
    from taskplane import flow_telemetry
    controller, state = setup(tmp_path, standalone=True)
    local.Harness(tmp_path, 'root').bind(state)
    state = decide(controller, submit(controller, state))
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    flow.append(tmp_path, dict(kind='start', run=state['run'], session='root', phase='product',
                              at=state['started_at']))
    if action == 'next_start_bind':
        state = controller.apply('finish', state['run'], expected_revision=state['revision'])
        flow.append(tmp_path, dict(kind='finish', run=state['run'], session='root'))
    method = 'update' if action == 'finish' else 'select' if action.endswith('select') else 'bind'
    def failed_observation(*args, **kwargs):
        raise OSError('injected post-commit harness write failure')
    monkeypatch.setattr(local.Harness, method, failed_observation)
    if action == 'finish':
        args = ['finish', '--expected-revision', str(state['revision'])]
        transition = 'finish'
    else:
        (tmp_path/'restart-scope.json').write_text(json.dumps(state['scope']))
        args = ['start', '--standalone', '--phase', 'product', '--scope', 'restart-scope.json',
                '--request-reference', 'fixture/post-commit-restart']
        transition = 'interval_closed'
        if action.startswith('replacement'):
            args += ['--replace-run', state['run'], '--expected-revision', str(state['revision'])]
            transition = 'replacement'
    assert flow.main([*args, '--workspace', str(tmp_path)], governor=controller) == 0
    result = json.loads(capsys.readouterr().out)
    assert any('harness' in error for error in result['evidence_errors'])
    closures = flow_telemetry.read_boundaries(tmp_path, state['run'], 'root')
    assert len(closures) == 1 and closures[0]['transition'] == transition
    assert closures[0]['committed']
    if action == 'finish':
        assert controller.report(state['run'])['finished']
    else:
        assert controller.report()['run'] != state['run']


@pytest.mark.parametrize('action', ['finish', 'replacement', 'next_start'])
@pytest.mark.parametrize('failure', ['io', 'json', 'identity'])
def test_readiness_projection_cannot_fail_committed_lifecycle(tmp_path, monkeypatch, capsys, action, failure):
    from taskplane import flow_telemetry
    controller, state = setup(tmp_path, standalone=True)
    local.Harness(tmp_path, 'root').bind(state)
    state = decide(controller, submit(controller, state))
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    flow.append(tmp_path, dict(kind='start', run=state['run'], session='root', phase='product',
                              at=state['started_at']))
    if action == 'next_start':
        state = controller.apply('finish', state['run'], expected_revision=state['revision'])
        flow.append(tmp_path, dict(kind='finish', run=state['run'], session='root'))
    calls = []
    def unavailable_readiness(*args):
        calls.append(True)
        if failure == 'io':
            raise OSError('readiness unavailable')
        if failure == 'json':
            raise ValueError('invalid advisory JSON')
        raise w.Refusal('state_unavailable', 'invalid advisory identity')
    monkeypatch.setattr(local.Harness, 'readiness', unavailable_readiness)
    if action == 'finish':
        args = ['finish', '--expected-revision', str(state['revision'])]
        transition = 'finish'
    else:
        (tmp_path/'restart-scope.json').write_text(json.dumps(state['scope']))
        args = ['start', '--standalone', '--phase', 'product', '--scope', 'restart-scope.json',
                '--request-reference', 'fixture/readiness-restart']
        transition = 'interval_closed'
        if action == 'replacement':
            args += ['--replace-run', state['run'], '--expected-revision', str(state['revision'])]
            transition = 'replacement'
    assert flow.main([*args, '--workspace', str(tmp_path)], governor=controller) == 0
    result = json.loads(capsys.readouterr().out)
    closures = flow_telemetry.read_boundaries(tmp_path, state['run'], 'root')
    assert len(closures) == 1 and closures[0]['transition'] == transition and closures[0]['committed']
    if action == 'finish':
        assert controller.report(state['run'])['finished']
        assert not calls  # A historical result must not evaluate an unused empty response.
    else:
        assert controller.report()['run'] != state['run']
        assert len(calls) == 1 and result['harness']['status'] == 'unavailable'
        assert any('readiness' in error for error in result['evidence_errors'])


def test_empty_report_keeps_unavailable_readiness_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    def unavailable_readiness(*args):
        raise OSError('readiness unavailable')
    monkeypatch.setattr(local.Harness, 'readiness', unavailable_readiness)
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'no_workflow'
    assert result['harness']['status'] == 'unavailable'
    assert any('readiness' in error for error in result['evidence_errors'])


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


@pytest.mark.parametrize("text", ["This is an auto-approved flow all the way.",
                                  "This is auto-approved workflow", "this is an auto-approved run"])
def test_unconditional_declarative_consent(text):
    assert affirmative_consent(text)


@pytest.mark.parametrize("text", ["This is an auto-approved flow if I approve later",
    "This is an auto-approved flow. Keep manual approval.",
    "Maybe this is an auto-approved flow", "This is not an auto-approved flow",
    'Example: "This is an auto-approved flow all the way."',
    "This is an auto-approved flow after my approval", "This is an auto-approved flow?"])
def test_declarative_consent_keeps_qualifier_refusals(text):
    assert not affirmative_consent(text)


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
