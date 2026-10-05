"""Compiler-to-harness integration fixtures, separate from the WFB-LIVE gate."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import blueprint_compile as bc, context_handoff as ch, flow, flow_dashboard
from taskplane import workflow as w, workflow_host as host, workflow_local as local
from taskplane.tests.test_blueprint import definition
from taskplane.tests.test_blueprint_compile import delivery_definition


def package(workspace, *, delivery=False):
    (workspace / 'main.py').write_text('answer = 42\n')
    (workspace / 'dependency.py').write_text('dependency = True\n')
    data = delivery_definition() if delivery else definition()
    values = {'source_files': ['main.py', 'dependency.py'], 'output_prefix': 'reports/first'}
    if delivery:
        values['build_files'] = ['main.py', 'new.py']
    return bc.compile_package(workspace, data, values)


def start(workspace, compiled=None):
    compiled = compiled or package(workspace)
    c = host.Controller(workspace, 'root', host.installed_adapter('codex'))
    route = compiled['compilation']['entry_phase']
    request = {'scope': compiled['scope'], 'entry': route, 'standalone': route != 'product',
               'request_reference': 'fixture/user-request', 'tasks': compiled['package_path'] + '/tasks.json'}
    state = c.start(request)
    return c, state, compiled


def event(workspace, command=None, **changes):
    value = {'thread_id': 'root', 'cwd': str(workspace), 'hook_event_name': 'PreToolUse',
             'tool_name': 'exec_command', 'call_id': 'fixture-call',
             'tool_input': {'cmd': shlex.join([sys.executable, str(Path(flow.__file__).with_name('tp.py')),
                                             *(command or ['version'])])}}
    value.update(changes)
    return value


def test_start_requires_package_route_and_exact_tasks_before_initialization(tmp_path):
    compiled = package(tmp_path)
    c = host.Controller(tmp_path, 'root', host.installed_adapter('codex'))
    request = {'scope': compiled['scope'], 'request_reference': 'fixture/user'}
    with pytest.raises(w.Refusal, match='route'):
        c.start(request)
    assert not c.adapter.state_exists()
    request.update(entry='engineering', standalone=True)
    with pytest.raises(w.Refusal, match='entry tasks'):
        c.start(request)
    assert not c.adapter.state_exists()
    request['tasks'] = compiled['package_path'] + '/tasks.json'
    state = c.start(request)
    assert state['decisions'] == {} and state['scope']['workflow_binding'] == compiled['workflow_binding']


def test_pinned_context_is_required_and_snapshots_recheck(tmp_path):
    c, state, compiled = start(tmp_path)
    session = ch.Session(tmp_path, state, 'security', persist=False)
    assert {'workflow/definition', 'workflow/compilation', 'workflow/bindings'} <= {r['id'] for r in session.required}
    binding = next(i['body'] for i in session.items if i['id'] == 'workflow/bindings')
    assert binding['values']['source_files'] == ['dependency.py', 'main.py']
    assert binding['context_is_authority'] is False
    snapshot = session.frozen
    (tmp_path / compiled['package_path'] / 'definition.json').write_text('{}')
    with pytest.raises(w.Refusal, match='Pinned package member'):
        ch.Session(tmp_path, state, 'security', snapshot=snapshot, persist=False)
    with pytest.raises(w.Refusal):
        c.context(state['run'], task='security')


@pytest.mark.parametrize('drift', ['source', 'package', 'missing'])
def test_execution_blocked_but_diagnostics_and_retirement_preserved(tmp_path, drift):
    c, state, compiled = start(tmp_path)
    if drift == 'source':
        (tmp_path / 'main.py').write_text('changed = True\n')
    else:
        path = tmp_path / compiled['package_path'] / 'capabilities.json'
        if drift == 'missing': path.unlink()
        else: path.write_bytes(path.read_bytes() + b' ')
    reported = c.report(state['run'])
    assert reported['workflow_provenance']['status'] == 'blocked'
    assert reported['run'] == state['run'] and reported['decisions'] == {}
    with pytest.raises(w.Refusal):
        c.worker(state['run'], 'prepare', revision=0, task='security', request={})
    with pytest.raises(w.Refusal):
        flow.hook(event(tmp_path, tool_name='Write', tool_input={'path': 'reports/first/security.md'}), governor=c)
    assert flow.hook(event(tmp_path, ['workflow', 'check', '--workspace', str(tmp_path), '--run', state['run']]), governor=c) is not None
    assert flow.hook(event(tmp_path, tool_name='collaboration.list_agents', tool_input={},
                           call_id='fixture-status-call'), governor=c) is not None
    retired = c.apply('retire', state['run'], expected_revision=0,
                      native_reference=json.dumps({'request_reference': 'fixture/user-retire', 'reason': 'Preserve failed run'}))
    assert retired['retired'] and retired['decisions'] == {}


def test_flow_control_and_native_spawn_cannot_skip_verification(tmp_path, capsys):
    c, state, compiled = start(tmp_path)
    (tmp_path / compiled['package_path'] / 'preview.md').write_text('tampered')
    assert flow.main(['context', '--workspace', str(tmp_path), '--run', state['run']], governor=c) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'package_integrity'
    with pytest.raises(w.Refusal, match='Pinned package member'):
        flow.hook(event(tmp_path, tool_name='collaboration.spawn_agent',
                        tool_input={'task_name': 'review', 'message': 'untrusted', 'fork_turns': 'none'}), governor=c)
    assert c.report(state['run'])['revision'] == 0


def test_interruption_continues_same_run_and_reuse_gets_fresh_identity(tmp_path):
    c, state, compiled = start(tmp_path)
    repeated = c.start({'scope': compiled['scope'], 'entry': 'engineering', 'standalone': True,
                        'tasks': compiled['package_path'] + '/tasks.json',
                        'request_reference': 'fixture/user-request'})
    assert repeated['run'] == state['run'] and repeated['revision'] == state['revision']
    second = bc.compile_package(tmp_path, definition(), {'source_files': ['main.py', 'dependency.py'],
                                                        'output_prefix': 'reports/second'})
    request = {'scope': second['scope'], 'entry': 'engineering', 'standalone': True,
               'tasks': second['package_path'] + '/tasks.json', 'request_reference': 'fixture/second'}
    with pytest.raises(w.Refusal, match='Another run'):
        c.start(request)
    c.apply('retire', state['run'], expected_revision=0,
            native_reference=json.dumps({'request_reference': 'fixture/retire', 'reason': 'Finish interrupted fixture'}))
    fresh = c.start(request)
    assert fresh['run'] != state['run'] and w.current(fresh)['id'] != w.current(state)['id']
    assert fresh['decisions'] == {} and not fresh.get('workers')


def test_runtime_incompatibility_reports_named_blocker(tmp_path, monkeypatch):
    c, state, _ = start(tmp_path)
    from taskplane import blueprint_catalog as catalog
    original = catalog.catalog
    def incompatible(*args, **kwargs):
        result = original(*args, **kwargs)
        result.update(compatible=False, blockers=[{'code': 'unsupported_contract', 'location': '/runtime',
                       'message': 'bounded/v2 unavailable', 'remedy': 'Restore loaded runtime'}])
        return result
    monkeypatch.setattr(catalog, 'catalog', incompatible)
    assert c.report(state['run'])['workflow_provenance']['diagnostics'][0]['code'] == 'runtime_incompatible'
    with pytest.raises(w.Refusal, match='bounded/v2'):
        c.context(state['run'])


def test_standalone_task_republication_cannot_downgrade_native_review(tmp_path):
    _, state, _ = start(tmp_path)
    rows = deepcopy(state['initial_context_tasks'])
    rows[0].update(execution='root', owner='root', execution_reason='Bypass', execution_reference='untrusted')
    state['task_context'] = {'visit': w.current(state)['id'], 'tasks': rows}
    with pytest.raises(w.Refusal, match='standalone tasks'):
        local.verify_workflow(tmp_path, state)


def test_authoring_entry_and_exact_data_command_guard(tmp_path):
    assert local.execution_entry({'tool_name': 'Skill', 'tool_input': {'skill': 'taskplane:tp-workflow', 'args': 'create a workflow'}}) == 'tp-workflow'
    harness = local.Harness(tmp_path, 'root')
    harness.select('tp-workflow', 'fixture/author', {})
    assert '--standalone --phase design' in harness.guidance({})
    data = definition()
    (tmp_path / 'definition.json').write_text(json.dumps(data))
    save = ['workflow', 'save', '--workspace', str(tmp_path), '--definition', 'definition.json',
            '--out', 'workflows/change-risk-review.0.1.0.workflow.json']
    with pytest.raises(w.Refusal, match='authoring path'):
        local.workflow_command(tmp_path, event(tmp_path, save), {})
    phase_scope = {'criteria': ['C1'], 'paths': {p: [] for p in w.PHASES}}
    phase_scope['paths']['design'] = [save[-1]]
    state = w.new_state(str(tmp_path), 'root', 'authoring', phase_scope, entry='design', standalone=True)
    assert local.workflow_command(tmp_path, event(tmp_path, save), state)[0] == 'save'
    state['visits'][0]['decision'] = 'awaiting_human_approval'
    with pytest.raises(w.Refusal, match='sealed'):
        local.workflow_command(tmp_path, event(tmp_path, save), state)
    with pytest.raises(w.Refusal):
        local.workflow_command(tmp_path, event(tmp_path, save + ['--out', 'other']), state)


def test_bootstrap_compile_is_finite_and_no_preview_write_flags(tmp_path):
    (tmp_path / 'definition.json').write_text(json.dumps(definition()))
    command = ['workflow', 'compile', '--workspace', str(tmp_path), '--definition', 'definition.json',
               '--inputs', 'inputs.json', '--out', '.taskplane/bootstrap/workflow-first']
    harness = local.Harness(tmp_path, 'root')
    assert harness.bootstrap_command(event(tmp_path, command))
    with pytest.raises(w.Refusal):
        harness.bootstrap_command(event(tmp_path, command[:-1] + ['arbitrary']))
    with pytest.raises(w.Refusal):
        harness.bootstrap_command(event(tmp_path, ['workflow', 'preview', '--workspace', str(tmp_path),
                                  '--definition', 'definition.json', '--out', 'arbitrary']))
    assert not (tmp_path / '.taskplane').exists()


@pytest.mark.parametrize('intent', ['', 'preview saved', 'catalog', 'validate draft', 'check run', 'run saved'])
def test_inspection_and_run_handoff_do_not_activate_authoring(intent):
    assert local.execution_entry({'tool_name': 'Skill', 'tool_input': {
        'skill': 'taskplane:tp-workflow', 'args': intent}}) is None
    assert local.execution_entry({'hook_event_name': 'UserPromptSubmit', 'tool_input': {},
                                  'prompt': '/tp-workflow ' + intent}) is None
    assert local.execution_entry({'tool_name': 'Read', 'tool_input': {
        'path': str(Path(local.__file__).parents[1] / 'skills/tp-workflow/SKILL.md')}}) is None


def accepted_plan_state(workspace, compiled):
    """Use pure harness transitions with explicit fixture decisions, not host evidence."""
    state = w.new_state(str(workspace), 'root', 'fixture', compiled['scope'])
    state['index'] = 2
    packet = {'checkpoint': 'plan-checkpoint', 'phase': 'plan', 'visit': w.current(state)['id'],
              'manifest': {}, 'source_manifest': {}, 'context': {}, 'output': {'write_scope': ['main.py']}}
    state = w.submit(state, packet)
    state = w.decide(state, {'event_id': 'fixture-decision', 'human': True, 'automatic': False,
                            'choice': 'approved', 'binding': w.binding(state, packet)})
    return state


def test_accepted_plan_narrows_scope_and_permits_only_authorized_source_mutation(tmp_path):
    compiled = package(tmp_path, delivery=True)
    state = accepted_plan_state(tmp_path, compiled)
    assert local.verify_workflow(tmp_path, state)['status'] == 'valid'
    state = w.advance(state, 'build')
    (tmp_path / 'main.py').write_text('approved implementation\n')
    assert local.verify_workflow(tmp_path, state)['mutable_paths'] == ['main.py']
    (tmp_path / 'dependency.py').write_text('not approved\n')
    with pytest.raises(w.Refusal, match='Bound source'):
        local.verify_workflow(tmp_path, state)


@pytest.mark.parametrize('change', ['widen', 'criteria', 'verification', 'other_phase', 'stale', 'superseded', 'digest', 'foreign_decision', 'package'])
def test_plan_projection_never_widens_or_reuses_stale_authority(tmp_path, change):
    compiled = package(tmp_path, delivery=True)
    state = accepted_plan_state(tmp_path, compiled)
    state = w.advance(state, 'build')
    plan = state['visits'][2]
    if change == 'widen': state['scope']['paths']['build'].append('outside.py')
    elif change == 'criteria': state['scope']['criteria'].append('NEW')
    elif change == 'verification': state['scope']['verification_inputs'].append('outside.py')
    elif change == 'other_phase': state['scope']['paths']['design'].append('outside.md')
    elif change == 'stale': plan['decision'] = 'stale'
    elif change == 'superseded': plan['superseded'] = True
    elif change == 'foreign_decision': state['decisions']['fixture-decision']['binding']['run'] = 'foreign'
    elif change == 'package': (tmp_path / compiled['package_path'] / 'preview.md').write_text('tampered')
    else: plan['approved_scope_digest'] = '0' * 64
    with pytest.raises(w.Refusal):
        local.verify_workflow(tmp_path, state)


def test_dashboard_escapes_workflow_strings(tmp_path):
    _, state, _ = start(tmp_path)
    state['workflow_provenance'] = {'run': state['run'], 'name': '<script>alert(1)</script>',
        'status': 'blocked', 'unresolved_inputs': ['<img src=x>'],
        'binding': {'definition_version': '<v>', 'package_digest': '<digest>'},
        'diagnostics': [{'message': '<svg onload=bad>'}]}
    rendered = flow_dashboard._workflow({'workflow': state})
    assert '<script>' not in rendered and '<img src=x>' not in rendered and '<svg onload' not in rendered
    assert '&lt;script&gt;' in rendered and state['run'] in rendered
