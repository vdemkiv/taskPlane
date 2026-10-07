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


def compiled_review_evidence(workspace):
    """Exercise compiler/native/context seams in fixtures, never live acceptance."""
    from taskplane import depgraph
    from taskplane.tests.test_worker_runtime import reserve, launch, consume
    c, state, compiled = start(workspace)
    prepared = reserve(c, state, 'security')
    launch(c, state, prepared, 'fixture-security')
    consume(c, state, prepared['grant'], 'fixture-security')
    report = 'reports/first/security.md'
    (workspace / report).parent.mkdir(parents=True, exist_ok=True)
    (workspace / report).write_text('Fixture security review of main.py\n')
    c.observe(dict(hook_event_name='PostToolUse', session_id='root', call_id='fixture-terminal',
                   tool_name='collaboration.list_agents', tool_input={},
                   tool_response={'agents': [{'agent_id': 'fixture-security', 'status': 'completed'}]}), state['run'])
    c.worker(state['run'], 'accept-result', revision=state['revision'], task='security',
             grant=prepared['grant']['grant_id'], request=dict(outputs=[report], checks=[
                 dict(name='Fixture source inspection', status='pass', evidence=report)]))
    state = c.report()
    files = compiled['compilation']['phase_files']['engineering']
    (workspace / files['packet']).parent.mkdir(parents=True, exist_ok=True)
    (workspace / files['tasks']).write_text(json.dumps(compiled['compilation']['task_patterns']['engineering']))
    (workspace / files['report']).write_text('Fixture phase synthesis\n')
    (workspace / files['evidence']).write_text(json.dumps({'schema': 'taskplane.checks/v1', 'checks': []}))
    (workspace / 'reports/first/report.md').write_text('Fixture accepted reviewer synthesis\n')
    output = dict(schema='taskplane.phase-output/v1', run=state['run'],
                  visit=w.current(state)['id'], phase='engineering', criteria=['CR1'],
                  **{key: 'Observed fixture evidence' for key in w.OUTPUT_FIELDS['engineering']})
    output.update(findings=[], requirements_comparison={'CR1': 'Reviewed fixture source'},
                  lens_coverage=[dict(lens='security', task_id='security', reviewer='fixture-security',
                      grant=prepared['grant']['grant_id'], status='native_verified',
                      rationale='Independent fixture result through the harness')],
                  artifacts=[dict(path=files[name], kind=kind, schema=schema, phase='engineering',
                      visit=w.current(state)['id'], criteria=['CR1'], tasks=['workflow-engineering-checkpoint'])
                      for name, kind, schema in [('report', 'report', 'markdown/v1'),
                                                ('evidence', 'verification', 'taskplane.checks/v1')]])
    (workspace / files['packet']).write_text(json.dumps(output))
    (workspace / '.taskplane/dashboard.html').write_text('<html>Fixture dashboard</html>')
    depgraph.scan(str(workspace), decompose=True, strict=True)
    return state, compiled, files, output


def test_compiled_receipt_write_converges_through_prevalidate_and_seal(tmp_path):
    from taskplane import depgraph, workflow_evidence as evidence
    state, compiled, files, output = compiled_review_evidence(tmp_path)
    before = depgraph.source_inputs(str(tmp_path))
    session = ch.Session(tmp_path, state)
    receipt, returned = ch.consume_required(session)
    assert returned[-1]['remaining_required'] == 0
    output['context_receipt'] = receipt
    (tmp_path / files['packet']).write_text(json.dumps(output))
    # This exact write made the nested compiled packet stale its own graph,
    # then invalidated its handoff receipt on the next prevalidation.
    assert evidence.prevalidate(tmp_path, state, files['packet'], files['tasks'])['phase'] == 'engineering'
    assert depgraph.source_inputs(str(tmp_path)) == before
    assert ch.Session(tmp_path, state).handoff_ref == session.handoff_ref
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    ch.Session(tmp_path, state).validate(receipt)
    sealed = evidence.seal(tmp_path, state, files['packet'], files['tasks'])
    assert {files['packet'], files['report'], files['evidence'], 'reports/first/security.md'} <= set(sealed['manifest'])
    assert bc.verify_package(tmp_path, compiled['workflow_binding'])['status'] == 'valid'


def test_compiled_phase_evidence_writes_remain_within_exact_scope(tmp_path):
    c, state, compiled = start(tmp_path)
    files = compiled['compilation']['phase_files']['engineering']
    for name, relative in files.items():
        c.guard(event(tmp_path, tool_name='Write', call_id='fixture-phase-write-' + name,
                      tool_input={'file_path': relative}), state['run'])
    for relative in [str(Path(files['packet']).with_name('unreserved.json')),
                     '.taskplane/runtime-evidence/workflow-other/engineering/packet.json']:
        with pytest.raises(w.Refusal, match='scope'):
            c.guard(event(tmp_path, tool_name='Write', call_id='fixture-outside-write',
                          tool_input={'file_path': relative}), state['run'])
    assert c.report()['revision'] == state['revision'] and c.report()['decisions'] == {}


@pytest.mark.parametrize('name', ['packet', 'tasks', 'report', 'evidence', 'native-report'])
def test_compiled_hidden_and_native_evidence_remains_bound_after_seal(tmp_path, name):
    from taskplane import workflow_evidence as evidence
    state, _, files, output = compiled_review_evidence(tmp_path)
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    state = w.submit(state, evidence.seal(tmp_path, state, files['packet'], files['tasks']))
    assert evidence.changed(tmp_path, state) is None
    target = tmp_path / (files[name] if name != 'native-report' else 'reports/first/security.md')
    if name in {'tasks', 'packet', 'evidence'}:
        value = json.loads(target.read_text())
        if name == 'tasks':
            value['tasks'][0]['purpose'] += ' Altered after sealing.'
        elif name == 'packet':
            value['requirements_comparison']['CR1'] = 'Altered conclusion after sealing.'
        else:
            value['checks'].append(dict(name='Changed check', status='fail', evidence=files['report']))
        target.write_text(json.dumps(value))
    else:
        target.write_text(target.read_text() + '\nAltered evidence\n')
    assert evidence.changed(tmp_path, state)[0] == w.current(state)['id']


def test_compiled_receipt_and_seal_reject_real_source_and_accepted_result_drift(tmp_path):
    from taskplane import workflow_evidence as evidence
    state, _, files, output = compiled_review_evidence(tmp_path)
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    report = tmp_path / 'reports/first/security.md'
    original_report = report.read_bytes()
    report.write_text('Changed accepted native evidence\n')
    # Refresh root context so result freshness, not an old receipt, rejects it.
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    with pytest.raises(w.Refusal, match='fresh accepted native result'):
        evidence.prevalidate(tmp_path, state, files['packet'], files['tasks'])
    report.write_bytes(original_report)
    for name in ['main.py', 'dependency.py']:
        source = tmp_path / name
        original_source = source.read_bytes()
        source.write_text('changed_source = True\n')
        with pytest.raises(w.Refusal, match='Bound source/dependency/test bytes changed'):
            ch.Session(tmp_path, state)
        with pytest.raises(w.Refusal, match='Bound source/dependency/test bytes changed'):
            evidence.prevalidate(tmp_path, state, files['packet'], files['tasks'])
        source.write_bytes(original_source)


def test_compiled_internal_evidence_does_not_hide_other_graph_source_drift(tmp_path):
    from taskplane import depgraph, workflow_evidence as evidence
    state, _, files, output = compiled_review_evidence(tmp_path)
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    # This source is not a declared blueprint input, but remains graph-bound.
    (tmp_path / 'new_source.py').write_text('new_dependency = True\n')
    with pytest.raises(w.Refusal, match='current handoff'):
        evidence.prevalidate(tmp_path, state, files['packet'], files['tasks'])
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    with pytest.raises(w.Refusal, match='Source graph is stale'):
        evidence.prevalidate(tmp_path, state, files['packet'], files['tasks'])
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    output['context_receipt'], _ = ch.consume_required(ch.Session(tmp_path, state))
    (tmp_path / files['packet']).write_text(json.dumps(output))
    assert evidence.seal(tmp_path, state, files['packet'], files['tasks'])['phase'] == 'engineering'


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


def delivery_plan(workspace, compiled=None):
    """Candidate-source fixture with a complete typed Plan, never host evidence."""
    from taskplane import workflow_evidence as evidence
    compiled = compiled or package(workspace, delivery=True)
    state = w.new_state(str(workspace), 'root', 'fixture', compiled['scope'])
    state['index'] = 2
    state['initial_context_tasks'] = deepcopy(compiled['compilation']['task_patterns']['product']['tasks'])
    rows = deepcopy(compiled['compilation']['task_patterns']['build']['tasks'])
    checkpoint = next(row for row in rows if row['id'] == 'workflow-build-checkpoint')
    checkpoint['read_inputs'].append('dependency.py')
    state['task_context'] = {'visit': w.current(state)['id'], 'tasks': rows}
    phase = compiled['compilation']['phase_files']['build']
    output = {'schema': 'taskplane.phase-output/v1', 'run': state['run'],
              'phase': 'plan', 'visit': w.current(state)['id'], 'criteria': ['CR1'],
              'write_scope': list(compiled['scope']['paths']['build']), 'task_dag': rows,
              'ownership': {row['id']: row['owner'] for row in rows},
              'acceptance_coverage': {'CR1': [row['id'] for row in rows]},
              'integration_order': [row['id'] for row in rows],
              'build_outputs': [{'kind': kind, 'path': phase[name], 'task': checkpoint['id']}
                                for kind, name in [('packet', 'packet'), ('report', 'report'),
                                                   ('verification_history', 'evidence')]],
              'verification_strategy': {'schema': evidence.VERIFICATION_STRATEGY, 'checks': [{
                  'id': 'check', 'name': 'Check changed source', 'task': checkpoint['id'],
                  'kind': 'unit', 'environment': 'fixture', 'required': True, 'criteria': ['CR1'],
                  'command': ['python3', 'main.py'], 'source_inputs': ['main.py'],
                  'test_inputs': ['dependency.py'], 'evidence_outputs': [phase['evidence']]}]}}
    return state, compiled, rows, output


def accepted_plan_state(workspace, compiled):
    """Use pure harness transitions with explicit fixture decisions, not host evidence."""
    state, _, rows, output = delivery_plan(workspace, compiled)
    # Optional outer paths may be omitted while preserving a native producer.
    output['write_scope'].remove('new.py')
    next(row for row in rows if row['id'] == 'implementation')['paths'].remove('new.py')
    packet = {'checkpoint': 'plan-checkpoint', 'phase': 'plan', 'visit': w.current(state)['id'],
              'manifest': {}, 'source_manifest': {}, 'context': {'tasks': rows}, 'output': output}
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
    assert local.verify_workflow(tmp_path, state)['mutable_paths'] == state['scope']['paths']['build']
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


def test_delivery_candidate_publication_refuses_native_to_root_atomically(tmp_path):
    c, state, compiled = start(tmp_path, package(tmp_path, delivery=True))
    rows = deepcopy(compiled['compilation']['task_patterns']['build']['tasks'])
    implementation = next(row for row in rows if row['id'] == 'implementation')
    implementation.update(execution='root', owner='root', execution_reason='Serial integration',
                          execution_reference='fixture/serial')
    candidate = compiled['compilation']['phase_files']['product']['tasks']
    (tmp_path / candidate).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / candidate).write_text(json.dumps({'tasks': rows}))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='native obligation'):
        c.update_tasks(state['run'], state['revision'], candidate)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('change', ['rename', 'remove', 'empty', 'placeholder', 'duplicate', 'criteria', 'inputs'])
def test_delivery_publication_refusals_preserve_controller_bytes(tmp_path, change):
    c, state, compiled = start(tmp_path, package(tmp_path, delivery=True))
    rows = deepcopy(compiled['compilation']['task_patterns']['build']['tasks'])
    native = next(row for row in rows if row['id'] == 'implementation')
    checkpoint = next(row for row in rows if row['id'] == 'workflow-build-checkpoint')
    if change in {'rename', 'remove'}:
        rows.remove(native)
        checkpoint['dependencies'].remove('implementation')
        if change == 'rename':
            native.update(id='root-replacement', execution='root', owner='root',
                          execution_reason='Integration', execution_reference='fixture')
            rows.append(native)
            checkpoint['dependencies'].append(native['id'])
    elif change in {'empty', 'placeholder'}:
        checkpoint['paths'] += native['paths']
        native['paths'] = [] if change == 'empty' else [checkpoint['paths'].pop(0)]
    elif change == 'duplicate':
        checkpoint['paths'].append('main.py')
    elif change == 'criteria':
        native['criteria'] = []
    else:
        native['read_inputs'] = []
    candidate = compiled['compilation']['phase_files']['product']['tasks']
    (tmp_path / candidate).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / candidate).write_text(json.dumps({'tasks': rows}))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='native obligation'):
        c.update_tasks(state['run'], state['revision'], candidate)
    assert c._path().read_bytes() == before
    after = c.report(state['run'])
    assert after['revision'] == state['revision'] and after['decisions'] == state['decisions']
    assert after.get('task_context') == state.get('task_context')


@pytest.mark.parametrize('operation', ['guard', 'context', 'dispatch'])
def test_delivery_current_context_downgrade_blocks_use(tmp_path, operation):
    c, state, compiled = start(tmp_path, package(tmp_path, delivery=True))
    rows = deepcopy(compiled['compilation']['task_patterns']['build']['tasks'])
    native = next(row for row in rows if row['id'] == 'implementation')
    native.update(execution='root', owner='root', execution_reason='Integration', execution_reference='fixture')
    state['task_context'] = {'visit': w.current(state)['id'], 'tasks': rows}
    # Deliberately persisted old vulnerable state inside this isolated fixture.
    db = c._read(c._path())
    db['runs'][state['run']] = state
    c._write(c._path(), db)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='native obligation'):
        if operation == 'guard':
            local.verify_workflow(tmp_path, state)
        elif operation == 'context':
            c.context(state['run'])
        else:
            c.worker(state['run'], 'prepare', revision=state['revision'], task='product-analysis', request={})
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('change', ['downgrade', 'removed_dag'])
def test_accepted_plan_native_obligations_are_checked_separately_from_current_context(tmp_path, change):
    state, _, rows, output = delivery_plan(tmp_path)
    # Model a historical Plan already accepted by the old implementation. Its
    # fixture decision is internally consistent, while current context is native.
    bad_output = deepcopy(output)
    if change == 'downgrade':
        native = next(row for row in bad_output['task_dag'] if row['id'] == 'implementation')
        native.update(execution='root', owner='root', execution_reason='Integration', execution_reference='fixture')
        bad_output['ownership'][native['id']] = 'root'
    else:
        bad_output.pop('task_dag')
    packet = {'checkpoint': 'fixture-plan', 'phase': 'plan', 'visit': w.current(state)['id'],
              'manifest': {}, 'source_manifest': {}, 'context': {'tasks': rows}, 'output': bad_output}
    state = w.submit(state, packet)
    state = w.decide(state, {'event_id': 'fixture-approval', 'human': True, 'automatic': False,
                            'choice': 'approved', 'binding': w.binding(state, packet)})
    state = w.advance(state, 'build')
    before = deepcopy(state)
    with pytest.raises(w.Refusal, match='native obligation'):
        local.verify_workflow(tmp_path, state)
    assert state == before


def test_compiled_build_requires_accepted_plan_equality_but_ignores_progress(tmp_path):
    from taskplane import workflow_evidence as evidence
    compiled = package(tmp_path, delivery=True)
    state = w.advance(accepted_plan_state(tmp_path, compiled), 'build')
    rows = deepcopy(w.accepted_plan(state)['packet']['output']['task_dag'])
    native = next(row for row in rows if row['id'] == 'implementation')
    native['status'] = 'running'
    state['task_context'] = {'visit': w.current(state)['id'], 'tasks': rows}
    assert local.verify_workflow(tmp_path, state)['status'] == 'valid'
    assert evidence.freeze_tasks(tmp_path, state, {'tasks': rows})
    native['verification'] += ' New normative requirement.'
    for action in (lambda: local.verify_workflow(tmp_path, state),
                   lambda: evidence.freeze_tasks(tmp_path, state, {'tasks': rows})):
        with pytest.raises(w.Refusal, match='accepted Plan'):
            action()


def test_new_delivery_phase_can_publish_required_future_tasks(tmp_path):
    from taskplane import workflow_evidence as evidence
    state, compiled, rows, _ = delivery_plan(tmp_path)
    state.pop('task_context')
    state['index'] = 1
    # Inherited Product context must stay usable to obtain the new task inputs.
    assert local.verify_workflow(tmp_path, state)['status'] == 'valid'
    candidate = compiled['compilation']['task_patterns']['design']['tasks']
    assert evidence.freeze_tasks(tmp_path, state, {'tasks': candidate})
    state['index'] = 2
    with pytest.raises(w.Refusal, match='native obligation implementation'):
        evidence.freeze_tasks(tmp_path, state, compiled['compilation']['task_patterns']['plan'])
    assert evidence.freeze_tasks(tmp_path, state, {'tasks': rows})
