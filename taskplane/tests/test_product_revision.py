"""Same-run migration/revision fixtures; no fixture proves installed native behavior."""
from copy import deepcopy
import hashlib
import json
import sys
from pathlib import Path
import zipfile

import pytest

from taskplane import workflow as w, workflow_host, workflow_evidence as evidence, host_capabilities
from taskplane.context import digest
from taskplane.context_handoff import Session, consume_required
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decide, decision, present


def upgrade_fixture(tmp_path, *, revision_checks=False, components=False, source_writes=False,
                    artifact_path=None):
    template, _, _ = prepare(tmp_path)
    scope = deepcopy(template['scope']); scope['paths']['build'] = (
        ['build.json', 'build-report.txt', 'build-history.json'] if revision_checks else ['build.json'])
    tasks = [{'id': 'T1', 'phase': 'build', 'owner': 'root', 'paths': scope['paths']['build'],
              'read_inputs': ['app.py'], 'dependencies': [], 'criteria': ['AC1'],
              'verification': 'Verify the unchanged declared outcome.'}]
    if components:
        scope['criteria'] = ['AC1', 'AC2']
        for name in ('alpha', 'beta', 'independent'):
            (tmp_path/name).mkdir()
            (tmp_path/name/'code.py').write_text('value = 1\n')
            (tmp_path/name/'check.py').write_text(f'from {name}.code import value\nassert value == 1\n')
        tasks = [{'id': name.upper(), 'phase': 'build', 'owner': 'root', 'paths': [name+'/result.txt'],
                  'read_inputs': [name+'/code.py', name+'/check.py'],
                  'dependencies': ['ALPHA'] if name == 'beta' else [],
                  'criteria': ['AC2'] if name == 'independent' else ['AC1'],
                  'verification': 'Run the declared component verification.'} for name in ('alpha', 'beta', 'independent')]
        tasks.append({'id': 'FINAL', 'phase': 'build', 'owner': 'root', 'paths': scope['paths']['build'],
                      'read_inputs': ['app.py'], 'dependencies': ['ALPHA', 'BETA', 'INDEPENDENT'],
                      'criteria': ['AC1', 'AC2'], 'verification': 'Verify all accepted component results.'})
        if source_writes:
            next(row for row in tasks if row['id'] == 'INDEPENDENT')['paths'].append('independent/code.py')
        if artifact_path:
            artifact = tmp_path/artifact_path
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('{"result": "fixture evidence"}\n')
            tasks[0]['paths'].append(artifact_path)
            tasks[1]['read_inputs'].insert(1, artifact_path)
        scope['paths']['build'] = [path for row in tasks for path in row['paths']]
        scope['verification_inputs'] = sorted({path for row in tasks for path in row['read_inputs']})
    (tmp_path/'tasks.json').write_text(json.dumps({'tasks': tasks}))
    c = workflow_host.Controller(tmp_path, 'root', workflow_host.installed_adapter('codex'))
    state = c.start({'scope': scope, 'request_reference': 'fixture/original-user',
                     'goal': 'Preserve the approved outcome during a supported runtime upgrade.'})
    for phase, next_phase in [('product', 'design'), ('design', 'plan'), ('plan', 'build')]:
        _, output, _ = prepare(tmp_path, phase)
        (tmp_path/'tasks.json').write_text(json.dumps({'tasks': tasks}))
        output.update(run=state['run'], visit=w.current(state)['id'])
        if components:
            output['criteria'] = scope['criteria']
            if phase == 'product':
                output['acceptance_criteria'].append({'id': 'AC2', 'statement': 'Preserve the independent component outcome.'})
            elif phase == 'design':
                output['acceptance_test_map']['AC2'] = 'Check the independent component without changing its contract.'
        if phase == 'plan':
            output.update(task_dag=tasks, write_scope=scope['paths']['build'], ownership={row['id']: row['owner'] for row in tasks},
                          acceptance_coverage={criterion: [row['id'] for row in tasks if criterion in row['criteria']]
                                               for criterion in scope['criteria']},
                          integration_order=[row['id'] for row in tasks])
            if revision_checks:
                output['build_outputs'] = [{'kind': kind, 'path': path, 'task': tasks[-1]['id']} for kind, path in
                    zip(['packet', 'report', 'verification_history'], tasks[-1]['paths'])]
                output['verification_strategy'] = {'schema': evidence.VERIFICATION_STRATEGY, 'checks': [{
                    'id': 'CHECK-T1', 'name': 'Fixture requirement regression', 'task': 'T1',
                    'kind': 'unit', 'environment': 'fixture', 'required': True, 'criteria': ['AC1'],
                    'command': ['python3', '-m', 'pytest', 'app.py'],
                    'source_inputs': ['app.py'], 'test_inputs': ['app.py'], 'evidence_outputs': ['build-report.txt']}]}
                if components:
                    output['verification_strategy']['checks'] = [{
                        'id': 'CHECK-'+row['id'], 'name': 'Verify '+row['id'], 'task': row['id'],
                        'kind': 'unit', 'environment': 'fixture', 'required': True, 'criteria': row['criteria'],
                        'command': [sys.executable, '-c', f'exec(open({row["read_inputs"][-1]!r}).read())'], 'source_inputs': row['read_inputs'][:1],
                        'test_inputs': row['read_inputs'][-1:], 'evidence_outputs': [row['paths'][0]]} for row in tasks]
        output['context_receipt'] = consume_required(Session(tmp_path, c.report()))[0]
        (tmp_path/(phase+'.json')).write_text(json.dumps(output))
        state = c.apply('submit', state['run'], expected_revision=state['revision'],
                        output=phase+'.json', tasks='tasks.json')
        present(c, state)
        state = decide(c, state, decision(state, event='fixture-approve-'+phase))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=next_phase)
    runtime = host_capabilities.runtime_identity()
    folder = tmp_path/'.taskplane/upgrade-fixture'; folder.mkdir()
    archive = folder/'candidate.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name in runtime['member_sha256']:
            z.writestr(name, (Path(runtime['root'])/name).read_bytes())
    raw_check = folder/'raw-check.txt'
    raw_check.write_text('Isolated compatibility fixture: passing result, not native evidence.\n')
    pin = lambda path: {'path': str(path.relative_to(tmp_path)),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    check = {'exit': 0, 'command': ['fixture-only'], 'evidence': pin(raw_check)}
    verification = folder/'verification.json'
    verification.write_text(json.dumps({'schema': 'taskplane.context-upgrade-verification/v1',
        'runtime_digest': digest(runtime), 'checks': {k: check for k in
            ['v2_unchanged', 'v3_old_reader_refusal', 'migration_retry', 'authorization_refusals', 'quiescent_rollback']}}))
    selection = {'schema': 'taskplane.context-selection/v1', 'run': state['run'],
        'scope_digest': digest(scope), 'task_generation': state.get('task_generation', 0)+1,
        'tasks': {'T1': {'request_reference': 'fixture/original-user', 'criteria': {'AC1': ['outcome']},
            'prerequisites': {}, 'findings': [], 'inputs': [{'id': 'outcome', **pin(tmp_path/'product.json'),
                'kind': 'requirement', 'reason': 'AC1 preserves this accepted requirement.',
                'selector': {'version': 1, 'kind': 'json-pointer', 'pointer': '/acceptance_criteria/0'}}]}}}
    checkpoints = {v['phase']: {'visit': v['id'], 'checkpoint': v['packet']['checkpoint'],
                              'packet_digest': digest(v['packet'])} for v in state['visits'][:3]}
    request = {'schema': 'taskplane.context-upgrade-request/v1', 'request_id': 'upgrade-one',
        'request_reference': 'fixture/user-authorizes-exact-upgrade', 'run': state['run'],
        'expected_revision': state['revision'], 'expected_generation': state.get('task_generation', 0),
        'from_contract': 'bounded/v2', 'to_contract': 'bounded/v3', 'scope_digest': digest(scope),
        'accepted_checkpoints': checkpoints, 'runtime_digest': digest(runtime),
        'package': pin(archive), 'verification': pin(verification), 'selection': selection}
    return c, state, request


def revision_fixture(tmp_path, *, components=False, source_writes=False, artifact_path=None):
    from taskplane import depgraph
    c, state, _ = upgrade_fixture(tmp_path, revision_checks=True, components=components,
                                  source_writes=source_writes, artifact_path=artifact_path)
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    scope = deepcopy(state['scope'])
    if components:
        (tmp_path/'.taskplane/revision').mkdir()
    for phase in ('product', 'design', 'plan'):
        scope['paths'][phase] = (['.taskplane/revision/'+phase+'.json', '.taskplane/revision/tasks.json']
                                 if components else ['revised-'+phase+'.json', 'revised-tasks.json'])
    product = next(v['packet'] for v in state['visits'] if v['phase'] == 'product')
    request = {'schema': 'taskplane.requirements-revision/v1', 'request_id': 'revision-one',
        'request_reference': 'fixture/human-changes-exact-requirement', 'run': state['run'],
        'expected_revision': state['revision'], 'expected_generation': state.get('storage_generation', 0),
        'scope_digest': digest(state['scope']), 'requirements_digest': digest(product['output']),
        'plan_digest': digest(w.accepted_plan(state)['packet']),
        'changes': {'AC1': {'id': 'AC1', 'statement': 'Human accepts the amended declared outcome.'}},
        'affected_artifacts': {}, 'scope_amendment': scope,
        'reason': 'Fixture explicit change request; no real native authority.', 'return_phase': 'product'}
    return c, state, request


def test_revision_tracks_declared_internal_evidence_without_source_module(tmp_path):
    artifact = '.taskplane/evidence/alpha.json'
    c, state, request = revision_fixture(tmp_path, components=True, artifact_path=artifact)
    before = c._path().read_bytes()
    preview = c.revise(state['run'], 'preview', request)
    assert c._path().read_bytes() == before
    assert preview['tasks'] == ['ALPHA', 'BETA', 'FINAL']
    assert artifact in preview['files']
    assert preview['artifact_manifest'] == {
        artifact: hashlib.sha256((tmp_path/artifact).read_bytes()).hexdigest()}
    result = c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert result['phase'] == 'product' and result['approvals_created'] == 0
    current = c._read(c._path())['runs'][state['run']]
    assert current['decisions'] == state['decisions']


def test_revision_internal_evidence_change_invalidates_preview(tmp_path):
    artifact = '.taskplane/evidence/alpha.json'
    c, state, request = revision_fixture(tmp_path, components=True, artifact_path=artifact)
    preview = c.revise(state['run'], 'preview', request)
    (tmp_path/artifact).write_text('{"result": "changed evidence"}\n')
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert c._path().read_bytes() == before


def test_revision_still_refuses_unmapped_executable_in_evidence_directory(tmp_path):
    c, state, request = revision_fixture(tmp_path, components=True,
                                        artifact_path='.taskplane/evidence/hidden.py')
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='Selective closure is unknown for existing paths'):
        c.revise(state['run'], 'preview', request)
    assert c._path().read_bytes() == before


def test_revision_waiting_for_worker_refuses_changed_internal_evidence(tmp_path):
    from taskplane.tests.test_worker_runtime import reserve
    artifact = '.taskplane/evidence/alpha.json'
    c, state, request = revision_fixture(tmp_path, components=True, artifact_path=artifact)
    item = reserve(c, state, 'ALPHA')
    preview = c.revise(state['run'], 'preview', request)
    result = c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert result['status'] == 'pending'
    (tmp_path/artifact).write_text('{"result": "changed while waiting"}\n')
    c.worker(state['run'], 'abandon', revision=state['revision'], grant=item['grant']['grant_id'])
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='Declared evidence artifacts changed'):
        c.revise(state['run'], 'resume', request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('path_kind', ['implementation_paths', 'test_paths'])
def test_revision_does_not_treat_declared_source_as_internal_evidence(tmp_path, path_kind):
    from taskplane.workflow_write_guard import revision_preview
    artifact = '.taskplane/evidence/alpha.json'
    c, original, request = revision_fixture(tmp_path, components=True, artifact_path=artifact)
    state = deepcopy(original)
    intent = {'kind': 'source', 'implementation_paths': [artifact], 'test_paths': ['alpha/check.py'],
              'build_outputs': {'packet': 'build.json', 'report': 'build-report.txt',
                                'verification_history': 'build-history.json'},
              'criteria': {criterion: {'paths': [artifact], 'verification': 'Check the declared source input.'}
                           for criterion in state['scope']['criteria']}}
    if path_kind == 'test_paths':
        intent['test_paths'].append(artifact)
        # Keep implementation coverage valid while isolating test-path classification.
        intent['implementation_paths'] = ['alpha/result.txt']
        for row in intent['criteria'].values(): row['paths'] = ['alpha/result.txt']
    for scope in [state['scope'], request['scope_amendment']]:
        scope.update(planning_contract='implementation/v1', implementation_intent=deepcopy(intent))
    request['scope_digest'] = digest(state['scope'])
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='Selective closure is unknown for existing paths'):
        revision_preview(tmp_path, state, request)
    assert c._path().read_bytes() == before


def test_revision_preview_and_same_run_return_preserve_approval_history(tmp_path):
    c, state, request = revision_fixture(tmp_path)
    before = c._path().read_bytes()
    preview = c.revise(state['run'], 'preview', request)
    assert c._path().read_bytes() == before
    assert preview['tasks'] == ['T1'] and preview['checks'] == ['CHECK-T1']
    result = c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    current = c._read(c._path())['runs'][state['run']]
    assert result['status'] == 'review' and result['phase'] == 'product'
    assert current['run'] == state['run'] and current['decisions'] == state['decisions']
    assert current['scope']['criteria'] == state['scope']['criteria']
    assert current['criterion_epochs'] == {'AC1': 1} and current['dispatch_paused']
    assert all(v['superseded'] for v in current['visits'][:len(state['visits'])])
    assert [v['packet'] for v in current['visits'][:len(state['visits'])]] == [v['packet'] for v in state['visits']]
    stable = c._path().read_bytes()
    assert c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest']) == result
    assert c._path().read_bytes() == stable


@pytest.mark.parametrize('defect', ['revision', 'generation', 'requirements', 'plan', 'scope', 'paths', 'changes', 'graph', 'child', 'preview'])
def test_revision_refusal_is_read_only(tmp_path, defect):
    from taskplane import depgraph
    c, state, request = revision_fixture(tmp_path)
    preview = c.revise(state['run'], 'preview', request)
    if defect == 'revision': request['expected_revision'] += 1
    elif defect == 'generation': request['expected_generation'] += 1
    elif defect in {'requirements', 'plan', 'scope'}: request[defect+'_digest'] = '0'*64
    elif defect == 'paths': request['scope_amendment'] = None
    elif defect == 'changes': request['changes'] = {'FOREIGN': {'id': 'FOREIGN', 'statement': 'Unknown criterion'}}
    elif defect == 'graph': Path(depgraph._path(str(tmp_path))).unlink()
    elif defect == 'child': c.principal = 'foreign-worker'
    else: preview['preview_digest'] = '0'*64
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert c._path().read_bytes() == before


def test_revision_abort_retains_fences_and_resume_requires_review(tmp_path):
    c, state, request = revision_fixture(tmp_path)
    preview = c.revise(state['run'], 'preview', request)
    c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    aborted = c.revise(state['run'], 'abort', request, note='Fixture user pauses the proposed revision.')
    assert aborted['status'] == 'aborted' and aborted['dispatch_paused']
    stable = c._path().read_bytes()
    assert c.revise(state['run'], 'abort', request, note='Fixture user pauses the proposed revision.') == aborted
    assert c._path().read_bytes() == stable
    with pytest.raises(w.Refusal, match='pending or aborted'):
        c.apply('submit', state['run'], expected_revision=aborted['revision'], output='revised-product.json', tasks='revised-tasks.json')
    resumed = c.revise(state['run'], 'resume', request)
    assert resumed['status'] == 'review' and resumed['phase'] == 'product' and resumed['dispatch_paused']
    assert c._read(c._path())['runs'][state['run']]['decisions'] == state['decisions']


def test_revision_fences_pending_reservation_until_supported_abandon(tmp_path):
    from taskplane.tests.test_worker_runtime import reserve
    from taskplane import worker_runtime as workers
    c, state, request = revision_fixture(tmp_path)
    item = reserve(c, state, 'T1')
    preview = c.revise(state['run'], 'preview', request)
    result = c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert result['status'] == 'pending' and result['phase'] == 'build'
    current = c._read(c._path())['runs'][state['run']]
    row = current['workers'][item['grant']['grant_id']]
    assert row['state'] == 'prepared' and row['stop_requested'] and not workers.joined(current)
    with pytest.raises(w.Refusal, match='paused'):
        workers.prepare(tmp_path, current, 'T1', {})
    with pytest.raises(w.Refusal, match='stopped'):
        from taskplane.workflow_write_guard import allow_work
        allow_work(row)
    pending = c.revise(state['run'], 'resume', request)
    assert pending['status'] == 'pending'
    c.worker(state['run'], 'abandon', revision=state['revision'], grant=row['grant_id'])
    opened = c.revise(state['run'], 'resume', request)
    assert opened['status'] == 'review' and opened['phase'] == 'product'
    after = c._read(c._path())['runs'][state['run']]
    assert after['workers'][row['grant_id']]['state'] == 'failed'
    assert after['workers'][row['grant_id']]['stop_requested']
    stable = c._path().read_bytes()
    assert c.revise(state['run'], 'resume', request) == opened
    assert c._path().read_bytes() == stable


@pytest.mark.parametrize('after_commit', [False, True])
def test_revision_crash_boundary_and_exact_retry(tmp_path, monkeypatch, after_commit):
    c, state, request = revision_fixture(tmp_path)
    preview = c.revise(state['run'], 'preview', request)
    before = c._path().read_bytes()
    original = c._write
    def failure(*args):
        if after_commit:
            original(*args)
        raise OSError('Fixture lost reply' if after_commit else 'Fixture before commit')
    monkeypatch.setattr(c, '_write', failure)
    with pytest.raises(OSError):
        c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    if not after_commit:
        assert c._path().read_bytes() == before
    monkeypatch.setattr(c, '_write', original)
    result = c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    current = c._read(c._path())['runs'][state['run']]
    assert result['status'] == 'review' and len(current['requirements_revisions']) == 1
    assert current['decisions'] == state['decisions']


def complete_revision_review(c, state, request, preview):
    from taskplane import depgraph
    tmp_path = c.workspace
    originals = {visit['phase']: deepcopy(visit['packet']['output']) for visit in state['visits'] if visit.get('packet')}
    tasks = originals['plan']['task_dag']
    task_path = preview['scope']['paths']['product'][1]
    (tmp_path/task_path).write_text(json.dumps({'tasks': tasks}))
    for phase, following in [('product', 'design'), ('design', 'plan'), ('plan', 'build')]:
        current = c.report(diagnostics=False)
        assert current['dispatch_paused'] and w.current(current)['phase'] == phase
        with pytest.raises(w.Refusal, match='approval'):
            c.apply('advance', state['run'], expected_revision=current['revision'], phase=following)
        output = deepcopy(originals[phase])
        output.update(run=state['run'], visit=w.current(current)['id'], requirements_revision=request['request_id'])
        if phase == 'product':
            output['acceptance_criteria'] = preview['requirements']
        path = preview['scope']['paths'][phase][0]
        output.pop('context_receipt', None)
        (tmp_path/path).write_text(json.dumps(output))
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
        output['context_receipt'] = consume_required(Session(tmp_path, c.report(diagnostics=False)))[0]
        (tmp_path/path).write_text(json.dumps(output))
        current = c.apply('submit', state['run'], expected_revision=current['revision'], output=path, tasks=task_path)
        present(c, current)
        current = decide(c, current, decision(current, event='fixture-revised-approval-'+phase))
        c.apply('advance', state['run'], expected_revision=current['revision'], phase=following)


def test_revision_requires_three_fresh_acceptances_before_build(tmp_path):
    from taskplane import depgraph
    c, state, request = revision_fixture(tmp_path)
    preview = c.revise(state['run'], 'preview', request)
    c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])
    complete_revision_review(c, state, request, preview)
    current = c.report(diagnostics=False)
    assert w.current(current)['phase'] == 'build' and not current['dispatch_paused']
    assert not current.get('active_requirements_revision')
    assert len(current['decisions']) == len(state['decisions']) + 3
    assert all(current['decisions'][key] == value for key, value in state['decisions'].items())


def test_upgrade_preview_read_only_commit_once_preserves_approvals(tmp_path):
    c, state, request = upgrade_fixture(tmp_path)
    before = c._path().read_bytes()
    preview = c.context_upgrade(state['run'], 'preview', request)
    assert c._path().read_bytes() == before
    assert preview['selection']['criteria'] == ['AC1']
    assert preview['selection']['briefs'][0]['preflight']['startup_packing']['normal_responses'] <= 8
    result = c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    current = c._read(c._path())['runs'][state['run']]
    assert current['context_contract'] == 'bounded/v3' and current['revision'] == state['revision']+1
    assert current['decisions'] == state['decisions'] and current['visits'] == state['visits']
    assert current['scope'] == state['scope'] and len(current['context_upgrades']) == 1
    assert not current.get('parent_hook_readiness')
    committed = c._path().read_bytes()
    assert c.context_upgrade(state['run'], 'commit', request, preview['preview_digest']) == result
    assert c._path().read_bytes() == committed
    with pytest.raises(w.Refusal, match='Conflicting'):
        c.context_upgrade(state['run'], 'commit', {**request, 'request_reference': 'different'}, preview['preview_digest'])
    assert c._path().read_bytes() == committed


@pytest.mark.parametrize('defect', ['child', 'revision', 'generation', 'scope', 'runtime', 'package',
                                  'verification', 'criterion', 'selector', 'plan', 'live_handle', 'preview'])
def test_upgrade_refusal_never_changes_authority(tmp_path, defect):
    c, state, request = upgrade_fixture(tmp_path)
    preview = c.context_upgrade(state['run'], 'preview', request)
    if defect == 'child': c.principal = 'foreign-child'
    elif defect == 'revision': request['expected_revision'] += 1
    elif defect == 'generation': request['expected_generation'] += 1
    elif defect in {'scope', 'runtime'}: request[defect+'_digest'] = '0'*64
    elif defect in {'package', 'verification'}: request[defect]['sha256'] = '0'*64
    elif defect == 'criterion': request['selection']['tasks']['T1']['criteria'] = {}
    elif defect == 'selector': request['selection']['tasks']['T1']['inputs'][0]['selector']['pointer'] = '/absent'
    elif defect == 'plan': request['accepted_checkpoints']['plan']['packet_digest'] = '0'*64
    elif defect == 'live_handle':
        db = c._read(c._path()); db['runs'][state['run']]['observed_handles']['live'] = {
            'state': 'running', 'revision': state['revision'], 'visit': w.current(state)['id']}
        c._write(c._path(), db)
    else: preview['preview_digest'] = '0'*64
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    assert c._path().read_bytes() == stable


def test_upgrade_crash_before_commit_retains_v2_and_retry_has_one_event(tmp_path, monkeypatch):
    c, state, request = upgrade_fixture(tmp_path)
    preview = c.context_upgrade(state['run'], 'preview', request)
    original = c._write
    monkeypatch.setattr(c, '_write', lambda *a: (_ for _ in ()).throw(OSError('fixture before commit')))
    before = c._path().read_bytes()
    with pytest.raises(OSError):
        c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    assert c._path().read_bytes() == before
    monkeypatch.setattr(c, '_write', original)
    c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    assert len(c._read(c._path())['runs'][state['run']]['context_upgrades']) == 1


def test_upgrade_lost_reply_retries_the_committed_event_without_new_approval(tmp_path, monkeypatch):
    c, state, request = upgrade_fixture(tmp_path)
    preview = c.context_upgrade(state['run'], 'preview', request)
    original = c._write
    def lost_reply(*args):
        original(*args)
        raise OSError('fixture lost reply after durable commit')
    monkeypatch.setattr(c, '_write', lost_reply)
    with pytest.raises(OSError):
        c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    monkeypatch.setattr(c, '_write', original)
    result = c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    current = c._read(c._path())['runs'][state['run']]
    assert result['status'] == 'committed' and len(current['context_upgrades']) == 1
    assert current['decisions'] == state['decisions']


def test_upgrade_refuses_an_unresolved_prepared_reservation(tmp_path):
    from taskplane.tests.test_worker_runtime import reserve
    c, state, request = upgrade_fixture(tmp_path)
    reserve(c, state, 'T1')
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='unresolved reservations'):
        c.context_upgrade(state['run'], 'preview', request)
    assert c._path().read_bytes() == stable


def test_upgrade_cli_previews_exact_request_and_does_not_opt_in_on_read(tmp_path, capsys):
    from taskplane import flow
    c, state, request = upgrade_fixture(tmp_path)
    path = tmp_path/'.taskplane/upgrade-fixture/request.json'; path.write_text(json.dumps(request))
    before = c._path().read_bytes()
    args = ['context-upgrade', '--workspace', str(tmp_path), '--run', state['run'],
            '--upgrade-mode', 'preview', '--upgrade-request', str(path.relative_to(tmp_path)),
            '--upgrade-sha256', hashlib.sha256(path.read_bytes()).hexdigest()]
    assert flow.main(args, governor=c, compact=True) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['preview_digest'] and value['approvals_created'] == 0
    assert c._path().read_bytes() == before



def rollback_fixture(tmp_path):
    c, before, upgrade = upgrade_fixture(tmp_path)
    preview = c.context_upgrade(before['run'], 'preview', upgrade)
    c.context_upgrade(before['run'], 'commit', upgrade, preview['preview_digest'])
    current = c._read(c._path())['runs'][before['run']]
    verification = tmp_path/upgrade['verification']['path']
    original = json.loads(verification.read_text())
    check = next(iter(original['checks'].values()))
    verification.write_text(json.dumps({'schema': 'taskplane.context-rollback-verification/v1',
        'runtime_digest': upgrade['runtime_digest'], 'checks': {name: check for name in
            ['compatible_contract', 'after_state_reader', 'obligations_preserved', 'old_reader_refusal', 'crash_retry']}}))
    request = {key: deepcopy(value) for key, value in upgrade.items() if key != 'selection'}
    request.update(schema='taskplane.context-rollback-request/v1', request_id='rollback-one',
        request_reference='fixture/user-authorizes-compatible-rollback', expected_revision=current['revision'],
        expected_generation=current['task_generation'], from_contract='bounded/v3', to_contract='bounded/v3',
        prior_runtime_digest=current['context_runtime_digest'], reason='Fixture compatible repair rollback preserves current obligations.')
    request['verification']['sha256'] = hashlib.sha256(verification.read_bytes()).hexdigest()
    return c, current, request


def test_compatible_rollback_preserves_current_history_and_never_rewinds(tmp_path):
    c, current, request = rollback_fixture(tmp_path)
    before = c._path().read_bytes()
    preview = c.context_rollback(current['run'], 'preview', request)
    assert c._path().read_bytes() == before
    result = c.context_rollback(current['run'], 'commit', request, preview['preview_digest'])
    after = c._read(c._path())['runs'][current['run']]
    assert after['revision'] == current['revision'] + 1
    assert after['task_generation'] == current['task_generation'] + 1
    assert after['context_contract'] == 'bounded/v3'
    for key in ('visits', 'decisions', 'history', 'scope', 'context_upgrades', 'workers', 'task_results'):
        assert after.get(key) == current.get(key)
    assert result['approvals_created'] == 0 and not after.get('parent_hook_readiness')
    stable = c._path().read_bytes()
    assert c.context_rollback(current['run'], 'commit', request, preview['preview_digest']) == result
    assert c._path().read_bytes() == stable


@pytest.mark.parametrize('defect', ['child', 'revision', 'generation', 'prior_runtime', 'runtime', 'contract',
                                  'package', 'verification', 'scope', 'plan', 'live_handle', 'preview'])
def test_compatible_rollback_refuses_unsafe_or_stale_request(tmp_path, defect):
    c, current, request = rollback_fixture(tmp_path)
    preview = c.context_rollback(current['run'], 'preview', request)
    if defect == 'child': c.principal = 'foreign-child'
    elif defect == 'revision': request['expected_revision'] += 1
    elif defect == 'generation': request['expected_generation'] += 1
    elif defect in {'prior_runtime', 'runtime', 'scope'}: request[defect+'_digest'] = '0'*64
    elif defect == 'contract': request['to_contract'] = 'bounded/v2'
    elif defect in {'package', 'verification'}: request[defect]['sha256'] = '0'*64
    elif defect == 'plan': request['accepted_checkpoints']['plan']['packet_digest'] = '0'*64
    elif defect == 'live_handle':
        db = c._read(c._path()); db['runs'][current['run']]['observed_handles']['live'] = {
            'state': 'running', 'revision': current['revision'], 'visit': w.current(current)['id']}
        c._write(c._path(), db)
    else: preview['preview_digest'] = '0'*64
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.context_rollback(current['run'], 'commit', request, preview['preview_digest'])
    assert c._path().read_bytes() == stable


@pytest.mark.parametrize('after_commit', [False, True])
def test_compatible_rollback_crash_retry_commits_one_event(tmp_path, monkeypatch, after_commit):
    c, current, request = rollback_fixture(tmp_path)
    preview = c.context_rollback(current['run'], 'preview', request)
    before = c._path().read_bytes()
    original = c._write
    def failure(*args):
        if after_commit: original(*args)
        raise OSError('Fixture rollback interruption')
    monkeypatch.setattr(c, '_write', failure)
    with pytest.raises(OSError):
        c.context_rollback(current['run'], 'commit', request, preview['preview_digest'])
    if not after_commit: assert c._path().read_bytes() == before
    monkeypatch.setattr(c, '_write', original)
    result = c.context_rollback(current['run'], 'commit', request, preview['preview_digest'])
    assert result['status'] == 'committed'
    after = c._read(c._path())['runs'][current['run']]
    assert len(after['context_rollbacks']) == 1 and after['decisions'] == current['decisions']


def test_runtime_pin_refuses_hot_switch_but_preserves_read_access(tmp_path, monkeypatch):
    from taskplane import worker_runtime as workers
    c, current, request = rollback_fixture(tmp_path)
    runtime = host_capabilities.runtime_identity()
    changed = deepcopy(runtime); changed['member_sha256']['taskplane/tp.py'] = '0'*64
    monkeypatch.setattr(host_capabilities, 'runtime_identity', lambda: changed)
    stable = c._path().read_bytes()
    assert c.report(diagnostics=False)['run'] == current['run']
    with pytest.raises(w.Refusal, match='enforcing runtime'):
        c.adapter.before_action(current, 'submit')
    with pytest.raises(w.Refusal, match='enforcing runtime'):
        workers.prepare(tmp_path, current, 'T1', {})
    assert c._path().read_bytes() == stable


def test_compatible_rollback_cli_is_source_pinned_and_read_only_preview(tmp_path, capsys):
    from taskplane import flow
    c, current, request = rollback_fixture(tmp_path)
    path = tmp_path/'.taskplane/upgrade-fixture/rollback-request.json'; path.write_text(json.dumps(request))
    args = ['context-rollback', '--workspace', str(tmp_path), '--run', current['run'], '--rollback-mode', 'preview',
            '--rollback-request', str(path.relative_to(tmp_path)), '--rollback-sha256', hashlib.sha256(path.read_bytes()).hexdigest()]
    before = c._path().read_bytes()
    assert flow.main(args, governor=c, compact=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['schema'] == 'taskplane.context-rollback-preview/v1' and result['approvals_created'] == 0
    assert c._path().read_bytes() == before
    path.write_text(json.dumps({**request, 'reason': 'Changed after pinning'}))
    assert flow.main(args, governor=c, compact=True) != 0
    assert c._path().read_bytes() == before



def test_actual_legacy_reader_before_upgrade_and_after_compatible_rollback(tmp_path):
    """Use the retained v2 runtime or its immutable Git source, never an imitation."""
    import os
    import subprocess
    import sys
    legacy = os.environ.get('TEST_TASKPLANE_LEGACY_RUNTIME')
    if not legacy:
        # CI has no workstation-specific retained runtime. Reconstruct the
        # historical reader from the exact published 2.33.10 commit instead.
        # Missing history is a failure, not permission to skip compatibility.
        source = Path(__file__).resolve().parents[2]
        commit = '4a46e645ec628dc439cd9d163dbd63ee4906c3e6'
        archive = tmp_path / 'legacy-source.zip'
        result = subprocess.run(['git', '-C', str(source), 'archive', '--format=zip',
                                 '--output', str(archive), commit],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        legacy = tmp_path / 'legacy-source'
        with zipfile.ZipFile(archive) as bundle:
            # Git generated this archive from the pinned tree; additionally
            # confine extraction and reject symlink entries before executing it.
            for member in bundle.infolist():
                target = (legacy / member.filename).resolve()
                assert target.is_relative_to(legacy.resolve())
                assert (member.external_attr >> 16) & 0o170000 != 0o120000
            bundle.extractall(legacy)
    legacy = Path(legacy).resolve()
    assert (legacy/'taskplane/workflow_host.py').is_file()
    c, initial, upgrade = upgrade_fixture(tmp_path)
    code = ("import json,sys; from pathlib import Path; from taskplane import workflow_host as h, workflow as w; "
            "c=h.Controller(Path(sys.argv[1]),'root',h.installed_adapter('codex')); "
            "state=c._read(c._path())['runs'][sys.argv[2]]; "
            "print(json.dumps({'contract':state['context_contract'],'revision':state['revision'],'decisions':len(state['decisions'])}))")
    def read_legacy(expected):
        before = c._path().read_bytes()
        result = subprocess.run([sys.executable, '-B', '-c', code, str(tmp_path), initial['run']],
            cwd=legacy, env={k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}},
            capture_output=True, text=True, timeout=30)
        if expected:
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout)['contract'] == 'bounded/v2'
        else:
            assert result.returncode != 0 and 'Unsupported context contract' in result.stderr, result.stdout + result.stderr
        assert c._path().read_bytes() == before
    read_legacy(True)
    preview = c.context_upgrade(initial['run'], 'preview', upgrade)
    c.context_upgrade(initial['run'], 'commit', upgrade, preview['preview_digest'])
    read_legacy(False)
    current = c.report(diagnostics=False)
    assert current['context_contract'] == 'bounded/v3' and current['decisions'] == initial['decisions']
    verification = tmp_path/upgrade['verification']['path']
    old = json.loads(verification.read_text()); check = next(iter(old['checks'].values()))
    verification.write_text(json.dumps({'schema': 'taskplane.context-rollback-verification/v1',
        'runtime_digest': upgrade['runtime_digest'], 'checks': {name: check for name in
            ['compatible_contract', 'after_state_reader', 'obligations_preserved', 'old_reader_refusal', 'crash_retry']}}))
    request = {key: deepcopy(value) for key, value in upgrade.items() if key != 'selection'}
    request.update(schema='taskplane.context-rollback-request/v1', request_id='actual-reader-rollback',
        expected_revision=current['revision'], expected_generation=current['task_generation'],
        from_contract='bounded/v3', prior_runtime_digest=current['context_runtime_digest'],
        reason='Actual legacy/new reader check against disposable state.')
    request['verification']['sha256'] = hashlib.sha256(verification.read_bytes()).hexdigest()
    preview = c.context_rollback(initial['run'], 'preview', request)
    c.context_rollback(initial['run'], 'commit', request, preview['preview_digest'])
    read_legacy(False)
    after = c.report(diagnostics=False)
    assert after['context_contract'] == 'bounded/v3' and after['decisions'] == initial['decisions']
    assert after['revision'] == initial['revision'] + 2



@pytest.mark.parametrize('defect', ['inventory', 'request', 'preview', 'result', 'status', 'fence', 'active', 'opened', 'binding'])
def test_corrupt_revision_history_refuses_before_grant(tmp_path, defect):
    c, current, request = revision_fixture(tmp_path)
    preview = c.revise(current['run'], 'preview', request)
    c.revise(current['run'], 'begin', request, preview_digest=preview['preview_digest'])
    db = c._read(c._path())
    state = db['runs'][current['run']]
    event = state['requirements_revisions'][request['request_id']]
    if defect == 'inventory': state['requirements_revisions'] = []
    elif defect == 'request': event['request']['reason'] = 'Corrupt request after digest'
    elif defect == 'preview': event['preview']['tasks'] = []
    elif defect == 'result': event['result']['run'] = 'foreign-run'
    elif defect == 'status': event['status'] = 'completed'
    elif defect == 'fence': state['dispatch_paused'] = False
    elif defect == 'active': state['active_requirements_revision'] = None
    elif defect == 'binding':
        event['preview']['binding'] = []
        event['preview']['preview_digest'] = digest({k: v for k, v in event['preview'].items() if k != 'preview_digest'})
    else: event['opened'] = False
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c._validate_database(db)
    assert c._path().read_bytes() == before


def test_native_revision_dashboard_distinguishes_review_and_historical_approval(tmp_path):
    from taskplane.flow_dashboard import revision_details
    c, current, request = revision_fixture(tmp_path)
    request['reason'] = 'Change <script>unsafe()</script> text in this fixture.'
    preview = c.revise(current['run'], 'preview', request)
    c.revise(current['run'], 'begin', request, preview_digest=preview['preview_digest'])
    state = c._read(c._path())['runs'][current['run']]
    html = revision_details(state)
    assert 'New work is paused.' in html and 'Returning to Product creates no approval.' in html
    assert 'Affected tasks: T1' in html and 'Pending affected workers: 0' in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    c.revise(current['run'], 'abort', request, note='Fixture pauses review.')
    state = c._read(c._path())['runs'][current['run']]
    assert 'aborted' in revision_details(state) and 'Resume this review explicitly' in revision_details(state)



@pytest.mark.parametrize('disposition', ['accept', 'reject'])
def test_selective_revision_preserves_unaffected_worker_until_result_review(tmp_path, disposition, monkeypatch):
    from taskplane import worker_runtime as workers
    from taskplane.tests.test_worker_runtime import reserve, launch, consume
    c, current, request = revision_fixture(tmp_path, components=True)
    prepared = reserve(c, current, 'INDEPENDENT')
    launch(c, current, prepared, 'native-independent')
    worker = consume(c, current, prepared['grant'], 'native-independent')
    before = c._read(c._path())['runs'][current['run']]
    grant = prepared['grant']['grant_id']
    receipt = deepcopy(before['workers'][grant]['context_receipt'])
    preview = c.revise(current['run'], 'preview', request)
    assert preview['tasks'] == ['ALPHA', 'BETA', 'FINAL']
    assert preview['criteria'] == ['AC1'] and grant not in preview['affected_grants']
    result = c.revise(current['run'], 'begin', request, preview_digest=preview['preview_digest'])
    assert result['status'] == 'pending'
    pending = c._read(c._path())['runs'][current['run']]
    row = pending['workers'][grant]
    assert not row.get('stop_requested') and row['context_receipt'] == receipt
    workers.worker_session(tmp_path, pending, row).validate(receipt)
    worker.guard({'hook_event_name': 'PreToolUse', 'session_id': 'native-independent', 'tool_name': 'Write',
                  'tool_input': {'file_path': 'independent/result.txt', 'content': 'Checked independent result'}}, current['run'])
    (tmp_path/'independent/result.txt').write_text('Checked independent result')
    c.observe({'hook_event_name': 'PostToolUse', 'session_id': 'root', 'call_id': 'join-independent',
        'tool_name': 'collaboration.list_agents', 'tool_input': {},
        'tool_response': {'agents': [{'agent_id': 'native-independent', 'status': 'completed'}]}}, current['run'])
    pending = c.revise(current['run'], 'resume', request)
    assert pending['status'] == 'pending' and pending['awaiting_result_acceptance'] == [grant]
    if disposition == 'accept':
        from taskplane import context_reuse, depgraph
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
        options = {'paths': ['independent/code.py'], 'tests': ['independent/check.py'], 'criteria': ['AC2'],
                   'command': [sys.executable, '-c', "exec(open('independent/check.py').read())"],
                   'tool': 'fixture-python', 'contract': 'independent/v1'}
        provenance = context_reuse.key(tmp_path, **options)
        assert provenance['eligible']
        reference = context_reuse.run_check(tmp_path, provenance, producer='fixture-independent')
        c.worker(current['run'], 'accept-result', revision=current['revision'], task='INDEPENDENT', grant=grant,
            request={'outputs': ['independent/result.txt'], 'checks': [{'name': 'Independent check', 'status': 'pass',
                                                                     'evidence': 'independent/result.txt', 'reuse_ref': reference}]})
    else:
        rejection = {'request_reference': 'fixture/root-review', 'reason': 'The independent result needs a fresh verification attempt.'}
        rejected = c.worker(current['run'], 'reject-result', revision=current['revision'], grant=grant, request=rejection)
        stable = c._path().read_bytes()
        assert c.worker(current['run'], 'reject-result', revision=current['revision'], grant=grant, request=rejection) == rejected
        assert c._path().read_bytes() == stable
        assert rejected['terminal_status'] == 'completed' and rejected['accepted'] is False
        with pytest.raises(w.Refusal, match='current source graph'):
            c.revise(current['run'], 'resume', request)
        assert c._path().read_bytes() == stable
        from taskplane import depgraph
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
    opened = c.revise(current['run'], 'resume', request)
    assert opened['status'] == 'review' and opened['phase'] == 'product'
    final = c._read(c._path())['runs'][current['run']]
    assert ('INDEPENDENT' in final.get('task_results', {})) == (disposition == 'accept')
    assert final['workers'][grant]['terminal_status'] == 'completed'
    assert final['criterion_epochs'] == {'AC1': 1}
    if disposition == 'accept':
        complete_revision_review(c, current, request, preview)
        reviewed = c.report(diagnostics=False)
        saved = reviewed['task_results']['INDEPENDENT']
        current_key = context_reuse.key(tmp_path, **options)
        from taskplane.context import Store
        prior_check = Store(tmp_path).resolve(reference)
        assert prior_check['status'] == 'pass', prior_check['result']
        assert current_key == prior_check['key'], [k for k in current_key if current_key[k] != prior_check['key'].get(k)]
        basis = workers.retained_authority(tmp_path, reviewed, 'INDEPENDENT', saved)
        assert basis is not None, {'verification_plan': (saved['verification_plan'], workers.verification_plan(reviewed, 'INDEPENDENT'))}
        workers.worker_session(tmp_path, basis, reviewed['workers'][grant]).validate(receipt)
        assert workers.native_result_valid(tmp_path, reviewed, 'INDEPENDENT', saved)
        assert workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
        assert reviewed['task_results']['INDEPENDENT'] == final['task_results']['INDEPENDENT']
        with pytest.raises(w.Refusal, match='old authority'):
            workers.current(reviewed, reviewed['workers'][grant])
        for relative in ('independent/code.py', 'independent/check.py'):
            target = tmp_path/relative; original = target.read_bytes()
            target.write_bytes(original+b'\n# changed source or verification input\n')
            depgraph.scan(str(tmp_path), decompose=True, strict=True)
            assert not workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
            target.write_bytes(original)
            depgraph.scan(str(tmp_path), decompose=True, strict=True)
            assert workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
        with monkeypatch.context() as patch:
            patch.setenv('LANG', 'different-verification-environment')
            assert not workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
        with monkeypatch.context() as patch:
            changed_runtime = deepcopy(host_capabilities.runtime_identity())
            changed_runtime['member_sha256']['taskplane/tp.py'] = '0'*64
            patch.setattr(host_capabilities, 'runtime_identity', lambda: changed_runtime)
            assert not workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
        for dimension in ('criterion', 'command'):
            changed = deepcopy(reviewed)
            if dimension == 'criterion': changed['criterion_epochs']['AC2'] = 1
            else:
                checks = w.accepted_plan(changed)['packet']['output']['verification_strategy']['checks']
                next(check for check in checks if check['task'] == 'INDEPENDENT')['command'] = [sys.executable, '-c', 'pass']
            assert not workers.result_valid(tmp_path, changed, 'INDEPENDENT')
        # A passing command for a different purpose cannot substitute for the accepted check.
        wrong_options = {**options, 'command': [sys.executable, '-c', 'pass']}
        wrong_ref = context_reuse.run_check(tmp_path, context_reuse.key(tmp_path, **wrong_options), producer='fixture-other-check')
        wrong_result = deepcopy(saved)
        wrong_result['checks'][0]['reuse_ref'] = wrong_ref
        assert workers.retained_authority(tmp_path, reviewed, 'INDEPENDENT', wrong_result) is None
        assert workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')
        approved_product = tmp_path/preview['scope']['paths']['product'][0]
        approved_product.write_text('{}')
        assert not workers.result_valid(tmp_path, reviewed, 'INDEPENDENT')




def test_upgrade_revalidates_root_results_without_complete_current_check_provenance(tmp_path):
    from taskplane import worker_runtime as workers
    c, current, request = upgrade_fixture(tmp_path, revision_checks=True)
    (tmp_path/'build-report.txt').write_text('Observed legacy check result without a complete reusable key.')
    c.worker(current['run'], 'accept-result', revision=current['revision'], task='T1',
        request={'outputs': ['build-report.txt'], 'checks': [{'name': 'Legacy root check', 'status': 'pass',
                                                            'evidence': 'build-report.txt'}]})
    before = c.report(diagnostics=False)
    assert workers.result_valid(tmp_path, before, 'T1')
    preview = c.context_upgrade(current['run'], 'preview', request)
    c.context_upgrade(current['run'], 'commit', request, preview['preview_digest'])
    after = c.report(diagnostics=False)
    assert after['task_results'] == before['task_results']
    assert not workers.result_valid(tmp_path, after, 'T1')


@pytest.mark.parametrize('new_dependency', [False, True])
def test_pending_revision_rechecks_current_graph_before_opening_product(tmp_path, new_dependency):
    from taskplane import depgraph
    from taskplane.tests.test_worker_runtime import reserve
    c, state, request = revision_fixture(tmp_path, components=True, source_writes=True)
    item = reserve(c, state, 'INDEPENDENT')
    preview = c.revise(state['run'], 'preview', request)
    assert 'INDEPENDENT' not in preview['tasks']
    assert c.revise(state['run'], 'begin', request, preview_digest=preview['preview_digest'])['status'] == 'pending'
    source = tmp_path/'independent/code.py'; original = source.read_bytes()
    source.write_text('from alpha.code import value\n' if new_dependency else 'value = 2\n')
    c.worker(state['run'], 'abandon', revision=state['revision'], grant=item['grant']['grant_id'])
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='current source graph'):
        c.revise(state['run'], 'resume', request)
    assert c._path().read_bytes() == stable
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    if new_dependency:
        with pytest.raises(w.Refusal, match='dependency closure changed'):
            c.revise(state['run'], 'resume', request)
        assert c._path().read_bytes() == stable
        source.write_bytes(original)
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
    opened = c.revise(state['run'], 'resume', request)
    assert opened['status'] == 'review'
    event = c._read(c._path())['runs'][state['run']]['requirements_revisions'][request['request_id']]
    assert event['opening_preview']['tasks'] == preview['tasks']
    assert event['opening_preview']['graph_digest'] == digest(depgraph.load(str(tmp_path)))


@pytest.mark.parametrize('field,value', [('prior_revision', '3'), ('prior_revision', None),
                                        ('prior_revision', True), ('revision', {}), ('revision', False)])
def test_context_transition_rejects_malformed_revision_authority(tmp_path, field, value):
    from taskplane.workflow_write_guard import validate_control_history
    c, state, request = upgrade_fixture(tmp_path)
    preview = c.context_upgrade(state['run'], 'preview', request)
    c.context_upgrade(state['run'], 'commit', request, preview['preview_digest'])
    current = c.report(diagnostics=False)
    event = current['context_upgrades'][request['request_id']]
    (event['result'] if field == 'revision' else event)[field] = value
    with pytest.raises(w.Refusal, match='committed context transition'):
        validate_control_history(current)
