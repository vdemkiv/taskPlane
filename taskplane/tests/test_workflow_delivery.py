"""Public operations and hooks share guarded policy; optional telemetry does not."""
import io
import json
import os
from unittest.mock import patch
import pytest
from taskplane import flow, flow_dashboard, workflow as w, workflow_host as h
from taskplane.tests.test_workflow_host import controller, native
from taskplane.tests.test_workflow_evidence import prepare

requires_relay_reader = pytest.mark.skipif(
    not (hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW')
         and os.open in os.supports_dir_fd),
    reason='Accepting original-source relay evidence requires secure descriptor-relative reads')


def cli(c, capsys, *args):
    code = flow.main([*args, "--workspace", str(c.workspace)], governor=c)
    return code, json.loads(capsys.readouterr().out)


def output(c, s):
    phase = w.current(s)["phase"]
    _, out, _ = prepare(c.workspace, phase)
    out.update(run=s["run"], visit=w.current(s)["id"])
    (c.workspace/(phase+".json")).write_text(json.dumps(out))
    return phase+".json"


@pytest.mark.parametrize('kind', ['source', 'documentation'])
def test_full_delivery_scope_is_usable_before_any_checkpoint(tmp_path, kind):
    from taskplane.tests.test_workflow import implementation_scope
    from taskplane import workflow_evidence as evidence
    scope = implementation_scope(kind)
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    request = dict(scope=scope, request_reference='user/repair-request')
    if kind == 'source':
        original = list(scope['paths']['build'])
        # This is the captured setup error: only packet/report files were allowed.
        scope['paths']['build'] = ['build/output.json', 'build/report.md']
        with pytest.raises(w.Refusal, match='before Product'):
            c.start(request)
        assert not c.adapter.state_exists(allow_pending=True)
        scope['paths']['build'] = original
    state = c.start(request)
    assert w.current(state)['phase'] == 'product'
    assert all(visit['decision'] == 'not_requested' for visit in state['visits'])
    assert w.scope_preflight(state['scope'])['status'] == 'declared_feasible'
    if kind == 'source':
        # Revalidation also refuses before reading/sealing a Product packet.
        state['scope']['paths']['build'] = ['build/output.json', 'build/report.md']
        with pytest.raises(w.Refusal, match='before Product'):
            evidence.prevalidate(tmp_path, state, 'product.json', 'tasks.json')


def test_all_public_phase_boundaries_require_human_decisions(tmp_path, capsys, monkeypatch):
    c, host, _ = controller(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    assert cli(c, capsys, "start")[0] == 0
    for i, phase in enumerate(w.PHASES):
        s = c.report()
        target = output(c, s)
        code, report = cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
                           "--expected-revision", str(s["revision"]))
        assert code == 0 and report["workflow"]["status"] == "awaiting_human_approval"
        s = c.report()
        before = host.store.read_bytes()
        code, refused = cli(c, capsys, "finish", "--expected-revision", str(s["revision"]))
        assert code == 2 and refused["reason"] == "approval_required"
        assert host.store.read_bytes() == before
        assert cli(c, capsys, "progress", "--phase", phase, "--note", "Work produced")[0] == 0
        assert c.report()["status"] == "awaiting_human_approval"
        key = native(host, s, key="human-"+phase)
        assert cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))[0] == 0
        s = c.report()
        if phase != "retro":
            assert cli(c, capsys, "advance", "--phase", w.PHASES[i+1], "--expected-revision", str(s["revision"]))[0] == 0
        else:
            code, report = cli(c, capsys, "finish", "--expected-revision", str(s["revision"]))
            assert code == 0 and report["status"] == "accepted"
    assert len(report["workflow"]["decisions"]) == 7
    page = (c.workspace/".taskplane/dashboard.html").read_text()
    assert "Task decomposition" in page and "Dependency graph" in page


def test_hooks_deny_with_protected_binding_even_after_journal_deleted(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    (c.workspace/flow.JOURNAL).write_text("")
    event = {"hook_event_name": "PreToolUse", "cwd": str(c.workspace), "session_id": "root",
             "tool_name": "Write", "tool_input": {"path": "app.py"}}
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    host.store.unlink()
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_prompt_shape_cannot_forge_a_decision_and_stop_can_wait(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    event = {"hook_event_name": "UserPromptSubmit", "cwd": str(c.workspace), "session_id": "root",
             "turn_id": "forged", "prompt": "approved", "actor": "human"}
    before = host.store.read_bytes()
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert host.store.read_bytes() == before
    event["hook_event_name"] = "Stop"
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out).get("decision") != "block"


def test_usage_failure_does_not_erase_a_valid_governance_decision(tmp_path, capsys, monkeypatch):
    c, host, s = controller(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    cli(c, capsys, "start")
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    key = native(host, s)
    with patch.object(flow.flow_usage, "reconcile", side_effect=OSError("unavailable")):
        code, r = cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))
    assert code == 0 and r["workflow"]["status"] == "approved"
    assert r["evidence_errors"]


@pytest.mark.parametrize("phase", w.ENTRY_PHASES)
def test_standalone_public_entry_has_shared_artifacts_and_no_fake_predecessors(tmp_path, capsys, phase):
    c, _, _ = controller(tmp_path, {"entry": phase, "standalone": True})
    code, r = cli(c, capsys, "start", "--phase", phase, "--standalone")
    assert code == 0
    assert [v["phase"] for v in r["workflow"]["visits"]] == [phase]
    assert r["tasks"] and (c.workspace/".taskplane/knowledge/graph.json").exists()
    assert (c.workspace/".taskplane/dashboard.html").exists()


def test_unverified_production_controller_refuses_legacy_progress_and_finish(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    flow.append(tmp_path, {"kind": "start", "run": "legacy", "session": "root", "phase": "product"})
    for action in (["progress", "--phase", "build"], ["finish"], ["start"], ["decide", "--native-event", "approved"]):
        assert flow.main([*action, "--workspace", str(tmp_path), "--profile", "protected_host"]) == 2
        assert json.loads(capsys.readouterr().out)["reason"] in {"unsupported_authority", "approval_required"}
    assert not any(r["kind"] == "finish" for r in flow.read_events(tmp_path))
    assert not h.installed_adapter("codex").capabilities()["human_origin"]


@pytest.mark.parametrize("choice", ["approved", "changes_requested", "rejected", "cancelled"])
def test_dashboard_uses_protected_decision_even_without_journal(tmp_path, choice):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Work: Produced" in page and "Evidence: Validated" in page
    assert "Human decision: Awaiting human approval" in page
    key = native(host, s, choice=choice)
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Human decision: " + choice.replace("_", " ").capitalize() in page
    assert 'class="stage recorded"' in page if choice == "approved" else 'class="stage recorded"' not in page
    (c.workspace/target).write_text("{}")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Human decision: Stale" in page and "Evidence: Stale" in page
    assert 'class="stage recorded"' not in page


def test_legacy_finish_is_never_displayed_as_acceptance(tmp_path):
    flow.append(tmp_path, {"kind": "start", "run": "legacy", "session": "root", "phase": "product"})
    flow.append(tmp_path, {"kind": "finish", "run": "legacy", "session": "root", "phase": "retro", "note": "done"})
    page = flow_dashboard.render(str(tmp_path), flow.report(tmp_path, "legacy"))
    assert "Legacy observations are unverified" in page
    assert 'class="stage recorded"' not in page


def test_public_standalone_repair_cannot_skip_prerequisite_approvals(tmp_path, capsys):
    c, host, s = controller(tmp_path, {"entry": "engineering", "standalone": True})
    target = output(c, s)
    proposal = json.loads((c.workspace/target).read_text())
    proposal["route_change"] = {"kind": "repair"}
    (c.workspace/target).write_text(json.dumps(proposal))
    assert cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
               "--expected-revision", str(s["revision"]))[0] == 0
    s = c.report()
    key = native(host, s)
    before = host.store.read_bytes()
    code, refused = cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))
    assert code == 2 and refused["reason"] == "approval_required"
    assert host.store.read_bytes() == before
    assert cli(c, capsys, "advance", "--phase", "build", "--expected-revision", str(s["revision"]))[0] == 2
    assert host.store.read_bytes() == before


@pytest.mark.parametrize("change", ["map", "task", "observation"])
def test_public_build_is_bound_to_accepted_plan_tasks(tmp_path, capsys, change):
    c, host, s = controller(tmp_path)
    for phase, next_phase in zip(w.PHASES[:3], w.PHASES[1:4]):
        target = output(c, s)
        s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
        key = native(host, s, key="plan-lineage-"+phase)
        s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
        s = c.apply("advance", s["run"], expected_revision=s["revision"], phase=next_phase)
    target = output(c, s)
    out = json.loads((c.workspace/target).read_text())
    tasks = json.loads((c.workspace/"tasks.json").read_text())
    if change in ("map", "task"):
        out["task_acceptance_map"] = {"AC1": ["NEW-TASK"]}
        if change == "task":
            tasks["tasks"][0]["id"] = "NEW-TASK"
    else:
        tasks["tasks"][0].update(status="complete", completed_at="2026-09-16T12:00:00Z")
    (c.workspace/target).write_text(json.dumps(out))
    (c.workspace/"tasks.json").write_text(json.dumps(tasks))
    before = host.store.read_bytes()
    code, report = cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
                       "--expected-revision", str(s["revision"]))
    if change == "observation":
        assert code == 0 and report["workflow"]["status"] == "awaiting_human_approval"
    else:
        assert code == 2 and report["reason"] == "invalid_evidence"
        assert host.store.read_bytes() == before


@pytest.mark.parametrize("route", ["delivery", "repair"])
def test_extended_routes_accept_changed_build_without_losing_history(tmp_path, route):
    request = {"entry": "engineering", "standalone": True} if route == "delivery" else {}
    c, host, s = controller(tmp_path, request)
    original = s["run"]
    if route == "repair":
        for phase in w.PHASES[:5]:
            target = output(c, s)
            s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
            key = native(host, s, key="initial-"+phase)
            s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
            s = c.apply("advance", original, expected_revision=s["revision"], phase=w.PHASES[w.PHASES.index(phase)+1])
    target = output(c, s)
    proposal = json.loads((c.workspace/target).read_text())
    proposal["route_change"] = {"kind": route, **({"scope": host.scope} if route == "delivery" else {})}
    (c.workspace/target).write_text(json.dumps(proposal))
    s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
    key = native(host, s, key="route-human")
    s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
    phases = w.PHASES if route == "delivery" else w.PHASES[3:]
    for phase in phases:
        s = c.apply("advance", original, expected_revision=s["revision"], phase=phase)
        target = output(c, s)
        if phase == "build":
            (c.workspace/"app.py").write_text("value = 2 # accepted Build scope\n")
            from taskplane import depgraph
            depgraph.scan(str(c.workspace), decompose=True, strict=True)
        s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
        key = native(host, s, key="new-"+phase)
        s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
    s = c.apply("finish", original, expected_revision=s["revision"])
    assert s["finished"] and s["run"] == original and s["history"]
    assert s["decisions"]["route-human"]["choice"] == "approved"
    assert (c.workspace/"app.py").read_text().startswith("value = 2")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, original, governor=c))
    assert "Superseded history" in page
    assert page.count('class="stage recorded"') == 7


def test_committed_actions_survive_journal_failure_and_decision_replay(tmp_path, capsys):
    c, host, initial = controller(tmp_path)
    for i, phase in enumerate(w.PHASES):
        s = c.report()
        target = output(c, s)
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            code, result = cli(c, capsys, 'submit', '--output', target, '--tasks', 'tasks.json',
                               '--expected-revision', str(s['revision']))
        assert code == 0 and result['workflow']['status'] == 'awaiting_human_approval'
        assert any('action committed' in message for message in result['evidence_errors'])
        s = c.report()
        key = native(host, s, key='journal-'+phase)
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            for _ in range(2):
                code, result = cli(c, capsys, 'decide', '--native-event', key,
                                   '--expected-revision', str(s['revision']))
                assert code == 0 and result['workflow']['status'] == 'approved'
                assert len(result['workflow']['decisions']) == i+1
                assert result['evidence_errors']
        s = c.report()
        action = ['finish'] if phase == 'retro' else ['advance', '--phase', w.PHASES[i+1]]
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            code, result = cli(c, capsys, *action, '--expected-revision', str(s['revision']))
        assert code == 0 and result['evidence_errors']
        assert result['workflow']['status'] == ('accepted' if phase == 'retro' else 'not_requested')
    assert c.report(initial['run'])['status'] == 'accepted'
    assert len(c.report(initial['run'])['decisions']) == 7


def test_authoritative_store_failure_still_refuses_without_saved_decision(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply('submit', s['run'], expected_revision=s['revision'], output=target, tasks='tasks.json')
    key = native(host, s)
    before = host.store.read_bytes()
    with patch.object(c, '_write', side_effect=OSError('authoritative store unavailable')):
        code, result = cli(c, capsys, 'decide', '--native-event', key, '--expected-revision', str(s['revision']))
    assert code == 2 and result['status'] == 'blocked' and result['reason'] == 'state_unavailable'
    assert host.store.read_bytes() == before
    assert c.report()['status'] == 'awaiting_human_approval'
    assert not c.report()['decisions']


@pytest.mark.parametrize('command', ['screen', 'context', 'session-verify', None, 'unknown'])
@pytest.mark.parametrize('payload', ['[]', 'null', 'false', '17', '"text"', '{broken'])
def test_malformed_hook_input_returns_named_refusal_without_mutation(tmp_path, capsys, command, payload):
    c, host, _ = controller(tmp_path)
    before = host.store.read_bytes()
    with patch('sys.stdin', io.StringIO(payload)):
        code = flow.run_hook(command=command, governor=c)
    result = json.loads(capsys.readouterr().out)
    if command == 'screen':
        assert code == 0 and result['hookSpecificOutput']['permissionDecision'] == 'deny'
        assert result['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    elif command == 'session-verify':
        assert code == 0 and result.get('systemMessage') and 'decision' not in result
    else:
        assert code == 2 and result['decision'] == 'block'
    assert host.store.read_bytes() == before


def test_named_screen_command_keeps_its_contract_when_payload_names_stop(tmp_path, capsys):
    c, host, _ = controller(tmp_path)
    before = host.store.read_bytes()
    payload = {'hook_event_name':'Stop', 'cwd':str(c.workspace), 'session_id':'root',
               'tool_name':'Write', 'tool_input':{'path':'app.py'}}
    with patch('sys.stdin', io.StringIO(json.dumps(payload))):
        assert flow.run_hook(command='screen', governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert host.store.read_bytes() == before


def test_public_compiled_delivery_publication_refusal_is_atomic(tmp_path, capsys):
    from copy import deepcopy
    from taskplane.tests.test_blueprint_harness import start, package
    c, state, compiled = start(tmp_path, package(tmp_path, delivery=True))
    rows = deepcopy(compiled['compilation']['task_patterns']['build']['tasks'])
    native = next(row for row in rows if row['id'] == 'implementation')
    native.update(execution='root', owner='root', execution_reason='Serial integration',
                  execution_reference='fixture/serial')
    target = compiled['compilation']['phase_files']['product']['tasks']
    (tmp_path / target).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / target).write_text(json.dumps({'tasks': rows}))
    before = c._path().read_bytes()
    code, result = cli(c, capsys, 'attach', '--run', state['run'], '--tasks', target,
                       '--update-context', '--expected-revision', str(state['revision']))
    assert code == 2 and result['reason'] == 'binding_mismatch'
    assert 'native obligation implementation' in result['detail']
    assert c._path().read_bytes() == before
    assert c.report(state['run'])['decisions'] == state['decisions']


def relay_fixture(tmp_path, monkeypatch):
    """Synthetic native frames test the contract, never provide live authority."""
    import hashlib
    import shlex
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from taskplane import workflow_local as local
    from taskplane.tests.test_workflow_local import setup, submit
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    c, state = setup(workspace)
    state = submit(c, state)
    origin = '11111111-2222-3333-4444-555555555555'
    session = home / '.codex/sessions/2026/10/06' / ('rollout-2026-10-06T12-00-00-' + origin + '.jsonl')
    session.parent.mkdir(parents=True)
    shown = datetime.now(timezone.utc)
    start = local.timestamp(state['started_at'])
    presentation = local.Harness(workspace, c.root).read()['presentation']
    binding = w.binding(state, w.current(state)['packet'])
    def message(role, text, moment, identity):
        return {'type': 'response_item', 'timestamp': moment.isoformat(), 'payload': {
            'type': 'message', 'role': role, 'id': identity,
            'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}],
            'internal_chat_message_metadata_passthrough': {'content_item_kinds': ['user.text'] if role == 'user' else ['unknown']}},
            'metadata': {'retained_source': {'complete': True, 'id': {'message_id': identity, 'role': role}}}}
    args = {'cmd': shlex.join(['/usr/local/bin/claude', '--session-id', c.root, 'Review this change.']),
            'workdir': str(workspace), 'tty': True}
    rows = [
        {'type': 'session_meta', 'timestamp': (start - timedelta(seconds=2)).isoformat(), 'payload': {'id': origin, 'source': 'vscode'}},
        {'type': 'response_item', 'timestamp': (start - timedelta(seconds=1)).isoformat(), 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'launch-1',
            'input': 'text(await tools.exec_command(' + json.dumps(args) + '));'}},
        {'type': 'response_item', 'timestamp': start.isoformat(), 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'launch-1', 'output': [
                {'type': 'input_text', 'text': json.dumps({'session_id': 123, 'output': ''})}]}},
        message('assistant', 'Review checkpoint ' + binding['checkpoint'] + '\n[Snapshot](' + presentation['artifact'] + ')', shown, 'shown-1'),
        message('user', 'approve\n', shown + timedelta(microseconds=1), 'human-1')]
    value = {'schema': 'taskplane.observed-decision/v2', 'event_id': 'parent-human-1', 'choice': 'approved',
             'binding': binding, 'excerpt': 'approve\n', 'recorder': 'root_orchestrator',
             'source': {'kind': 'conversation', 'actor': 'user', 'automatic': False, 'conversation': origin},
             'presentation': {'checkpoint': binding['checkpoint']},
             'relay': {'schema': 'taskplane.original-source-relay/v1', 'launch_flag': '--session-id'}}
    def rewrite():
        refs, offset, bodies = [], 0, []
        for row in rows:
            raw = (json.dumps(row) + '\n').encode()
            refs.append({'path': str(session), 'offset': offset, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            bodies.append(raw); offset += len(raw)
        session.write_bytes(b''.join(bodies))
        value['relay'].update(session_meta=refs[0], launch={'segments': refs[1:3]}, presentation=refs[3], human=refs[4])
        for key, suffix in [('html', '.html'), ('snapshot', '.json')]:
            target = Path(presentation['artifact']).with_suffix(suffix)
            raw = target.read_bytes()
            value['relay'][key] = {'path': str(target), 'offset': 0, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        def reference(ref):
            return f"{ref['path']}#offset={ref['offset']}&bytes={ref['bytes']}"
        value['source'].update(reference=reference(refs[4]), observed_at=rows[4]['timestamp'])
        value['presentation'].update(reference=reference(refs[3]), at=rows[3]['timestamp'])
    rewrite()
    return c, state, value, rows, rewrite


@requires_relay_reader
def test_original_source_relay_preserves_origin_and_exact_replay(tmp_path, monkeypatch):
    from copy import deepcopy
    from taskplane.tests.test_workflow_local import decide
    c, state, value, _, _ = relay_fixture(tmp_path, monkeypatch)
    before = c._path().read_bytes()
    legacy = deepcopy(value); legacy['schema'] = 'taskplane.observed-decision/v1'
    with pytest.raises(w.Refusal, match='provenance'):
        decide(c, state, legacy)
    assert c._path().read_bytes() == before
    accepted = decide(c, state, value)
    recorded = accepted['decisions'][value['event_id']]
    assert recorded['binding']['root'] == c.root
    assert recorded['provenance']['source'] == value['source']
    assert recorded['provenance']['relay'] == value['relay']
    assert recorded['provenance']['schema'] == value['schema']
    assert recorded['assurance'] == 'observed'
    assert decide(c, state, value)['decisions'] == accepted['decisions']
    advanced = c.apply('advance', state['run'], expected_revision=accepted['revision'], phase='design')
    assert c.adapter.verify_decision_context(json.dumps(value), value['binding'], advanced['decisions']) == recorded


@pytest.mark.parametrize('bad', [
    'assistant', 'tool', 'automation', 'origin', 'incomplete', 'kind', 'message_id', 'foreign_meta', 'subagent_meta',
    'source_conversation', 'source_time', 'excerpt', 'source_reference', 'binding', 'fresh_checkpoint',
    'presentation_reference', 'presentation_time', 'presentation_text', 'presentation_link', 'ordering',
    'presentation_foreign_checkpoint', 'presentation_foreign_snapshot',
    'launch_target', 'launch_prompt_only', 'launch_tty', 'launch_workdir', 'launch_wrapper', 'launch_result',
    'launch_pair', 'launch_quoted_wrapper', 'launch_unknown_flag', 'frame_hash', 'frame_boundary', 'frame_multiple',
    'frame_boolean', 'frame_oversize', 'source_drift', 'symlink', 'parent_symlink', 'copied_session',
    'snapshot_hash', 'snapshot_binding', 'snapshot_missing', 'native_presentation', 'relay_missing',
    'nul_path', 'missing_nofollow'])
def test_original_source_relay_refuses_atomically(tmp_path, monkeypatch, bad):
    import hashlib
    import os
    from pathlib import Path
    from taskplane import workflow_local as local
    from taskplane.tests.test_workflow_local import decide
    c, state, value, rows, rewrite = relay_fixture(tmp_path, monkeypatch)
    human, shown, call, result = rows[4], rows[3], rows[1], rows[2]
    if bad == 'assistant': human['payload']['role'] = 'assistant'
    elif bad == 'tool': human['payload']['type'] = 'function_call_output'
    elif bad == 'automation': human['metadata']['automation_id'] = 'scheduled'
    elif bad == 'origin': human['payload']['internal_chat_message_metadata_passthrough']['turnOrigin'] = 'tool'
    elif bad == 'incomplete': human['metadata']['retained_source']['complete'] = False
    elif bad == 'kind': human['payload']['internal_chat_message_metadata_passthrough']['content_item_kinds'] = ['tool.text']
    elif bad == 'message_id': human['metadata']['retained_source']['id']['message_id'] = 'foreign'
    elif bad == 'foreign_meta': rows[0]['payload']['id'] = 'foreign'
    elif bad == 'subagent_meta': rows[0]['payload']['source'] = {'subagent': 'other'}
    elif bad == 'presentation_text': shown['payload']['content'][0]['text'] = '[Snapshot](' + value['relay']['html']['path'] + ')'
    elif bad == 'presentation_link': shown['payload']['content'][0]['text'] = value['binding']['checkpoint'] + '\n[Dashboard](' + str(c.workspace / '.taskplane/dashboard.html') + ')'
    elif bad == 'presentation_foreign_checkpoint': shown['payload']['content'][0]['text'] += '\nOther checkpoint ' + 'b' * 32
    elif bad == 'presentation_foreign_snapshot': shown['payload']['content'][0]['text'] += '\n[Other](/foreign/snapshot-1234.html)'
    elif bad == 'ordering': shown['timestamp'] = rows[0]['timestamp']
    elif bad.startswith('launch_'):
        args = json.loads(call['payload']['input'].removeprefix('text(await tools.exec_command(').removesuffix('));'))
        if bad in {'launch_target', 'launch_prompt_only'}:
            args['cmd'] = args['cmd'].replace('--session-id root', '--session-id foreign')
            if bad == 'launch_prompt_only': args['cmd'] += " 'Mention --session-id root'"
        elif bad == 'launch_tty': args['tty'] = False
        elif bad == 'launch_workdir': args['workdir'] = '/foreign'
        elif bad == 'launch_result': result['payload']['output'][0]['text'] = json.dumps({'output': 'session_id 123', 'exit_code': 1})
        elif bad == 'launch_pair': result['payload']['call_id'] = 'other'
        elif bad == 'launch_unknown_flag': args['cmd'] = args['cmd'].replace('--session-id', '--unknown option --session-id')
        call['payload']['input'] = 'text(await tools.exec_command(' + json.dumps(args) + '));'
        if bad == 'launch_wrapper': call['payload']['input'] = 'const fake = ' + json.dumps(call['payload']['input']) + ';'
        if bad == 'launch_quoted_wrapper': call['payload']['input'] = 'text(await tools.exec_command(' + json.dumps({'cmd': 'echo ' + args['cmd'], 'tty': True, 'workdir': str(c.workspace)}) + '));'
    rewrite()
    relay = value['relay']
    if bad == 'source_conversation': value['source']['conversation'] = c.root
    elif bad == 'source_time': value['source']['observed_at'] = rows[0]['timestamp']
    elif bad == 'excerpt': value['excerpt'] = 'approved'
    elif bad == 'source_reference': value['source']['reference'] += 'fake'
    elif bad == 'binding': value['binding']['root'] = 'foreign'
    elif bad == 'fresh_checkpoint': value['binding']['checkpoint'] = '0' * 32
    elif bad == 'presentation_reference': value['presentation']['reference'] = value['source']['reference']
    elif bad == 'presentation_time': value['presentation']['at'] = rows[0]['timestamp']
    elif bad == 'frame_hash': relay['human']['sha256'] = '0' * 64
    elif bad == 'frame_boolean': relay['human']['offset'] = True
    elif bad == 'frame_oversize': relay['human']['bytes'] = 131073
    elif bad in {'frame_boundary', 'frame_multiple'}:
        ref = relay['human'] if bad == 'frame_boundary' else relay['presentation']
        if bad == 'frame_boundary': ref['offset'] += 1; ref['bytes'] -= 1
        else: ref['bytes'] += relay['human']['bytes']
        with open(ref['path'], 'rb') as stream:
            stream.seek(ref['offset']); ref['sha256'] = hashlib.sha256(stream.read(ref['bytes'])).hexdigest()
    elif bad in {'source_drift', 'symlink', 'copied_session', 'parent_symlink', 'nul_path'}:
        path = Path(relay['human']['path'])
        if bad == 'source_drift': path.write_bytes(path.read_bytes().replace(b'approve', b'reject!'))
        elif bad == 'symlink':
            moved = path.with_suffix('.saved'); path.rename(moved); path.symlink_to(moved)
        elif bad == 'parent_symlink':
            parent = path.parent; moved = parent.with_name('saved'); parent.rename(moved); parent.symlink_to(moved)
        else:
            copied = tmp_path / path.name
            copied.write_bytes(path.read_bytes())
            for ref in [relay['session_meta'], *relay['launch']['segments'], relay['presentation'], relay['human']]:
                ref['path'] = str(copied) if bad == 'copied_session' else ref['path'].replace('rollout-', '\x00rollout-')
    elif bad == 'snapshot_hash': relay['snapshot']['sha256'] = '0' * 64
    elif bad == 'snapshot_binding':
        path = Path(relay['snapshot']['path']); model = json.loads(path.read_text())
        model['snapshot']['run'] = 'foreign'; raw = json.dumps(model).encode(); path.write_bytes(raw)
        relay['snapshot'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    elif bad == 'snapshot_missing': Path(relay['snapshot']['path']).unlink()
    elif bad == 'native_presentation': local.Harness(c.workspace, c.root).update(presentation=None)
    elif bad == 'relay_missing': value.pop('relay')
    elif bad == 'missing_nofollow': monkeypatch.delattr(os, 'O_NOFOLLOW', raising=False)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, value)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('flag', ['--session-id', '--resume'])
@pytest.mark.parametrize('native', [False, True])
@requires_relay_reader
def test_original_source_relay_exact_launch_forms(tmp_path, monkeypatch, flag, native):
    from taskplane.tests.test_workflow_local import decide
    c, state, value, rows, rewrite = relay_fixture(tmp_path, monkeypatch)
    call, result = rows[1]['payload'], rows[2]['payload']
    call['input'] = call['input'].replace('--session-id', flag)
    value['relay']['launch_flag'] = flag
    if native:
        call.update(type='function_call', name='exec_command', arguments=call.pop('input').removeprefix('text(await tools.exec_command(').removesuffix('));'))
        result.update(type='function_call_output', output=result['output'][0]['text'])
    rewrite()
    accepted = decide(c, state, value)
    assert accepted['decisions'][value['event_id']]['provenance']['relay'] == value['relay']


@requires_relay_reader
def test_original_source_relay_replay_pins_original_frames(tmp_path, monkeypatch):
    from taskplane.tests.test_workflow_local import decide
    c, state, value, rows, rewrite = relay_fixture(tmp_path, monkeypatch)
    accepted = decide(c, state, value)
    # Semantically equivalent rewritten bytes are not the original recorded event.
    rows[4]['metadata']['extra'] = 'changed'
    rewrite()
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='replay'):
        decide(c, accepted, value)
    assert c._path().read_bytes() == before


def test_original_source_relay_old_human_cannot_approve_fresh_checkpoint(tmp_path, monkeypatch):
    from taskplane import workflow_local as local
    c, state, value, _, _ = relay_fixture(tmp_path, monkeypatch)
    # A prospective fresh state uses a new submitted checkpoint; it is never
    # written into the fixture's controller, nor into any live controller.
    fresh = w.submit(state, {**w.current(state)['packet'], 'checkpoint': 'a' * 32})
    assert w.current(fresh)['packet']['checkpoint'] != value['binding']['checkpoint']
    value['binding'] = w.binding(fresh, w.current(fresh)['packet'])
    value['presentation']['checkpoint'] = value['binding']['checkpoint']
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        local.verify_decision_relay(value, value['binding'], fresh)
    assert c._path().read_bytes() == before


def test_original_source_relay_duplicate_json_is_refused(tmp_path, monkeypatch):
    c, state, value, _, _ = relay_fixture(tmp_path, monkeypatch)
    raw = json.dumps(value).replace('"choice": "approved"', '"choice": "rejected", "choice": "approved"')
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='Duplicate'):
        c.apply('decide', state['run'], expected_revision=state['revision'], native_reference=raw)
    assert c._path().read_bytes() == before


def test_original_source_relay_refuses_without_secure_file_support(tmp_path, monkeypatch):
    from taskplane import workflow_local as local
    from taskplane.tests.test_workflow_local import decide
    c, state, value, _, _ = relay_fixture(tmp_path, monkeypatch)
    monkeypatch.delattr(os, 'O_NOFOLLOW', raising=False)
    before = c._path().read_bytes()
    with pytest.raises(local.DecisionRefusal, match='cannot safely open'):
        decide(c, state, value)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('bad', ['index', 'missing', 'nonfinite', 'nul', 'fifo'])
def test_original_source_relay_malformed_snapshot_has_named_refusal(tmp_path, monkeypatch, bad):
    import hashlib
    import os
    from pathlib import Path
    from taskplane import workflow_local as local
    from taskplane.tests.test_workflow_local import decide
    c, state, value, _, _ = relay_fixture(tmp_path, monkeypatch)
    ref = value['relay']['snapshot']; path = Path(ref['path'])
    if bad == 'nul': ref['path'] = str(path).replace('snapshot-', '\x00snapshot-')
    elif bad == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('FIFO snapshots require POSIX named pipes')
        path.unlink(); os.mkfifo(path)
    else:
        model = json.loads(path.read_text())
        if bad == 'index': model['workflow']['index'] = 9999
        elif bad == 'missing': model['workflow'] = {'visits': [{}], 'index': 0}
        else: model['snapshot']['revision'] = float('nan')
        raw = json.dumps(model).encode(); path.write_bytes(raw)
        ref.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    before = c._path().read_bytes()
    with pytest.raises(local.DecisionRefusal) as refusal:
        decide(c, state, value)
    assert refusal.value.category == 'decision_relay'
    assert c._path().read_bytes() == before
