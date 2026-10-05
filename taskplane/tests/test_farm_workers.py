"""F6 adversarial fixtures. These never certify a live Claude installation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest

from taskplane import claude_worker_observations as claude, host_capabilities as caps
from taskplane import host_native, worker_runtime as workers, workflow as w
from taskplane.context import digest
from taskplane.context_handoff import binding

requires_native_reader = pytest.mark.skipif(
    not claude.supported_reader(), reason='Native transcript identity requires no-follow reads')


def fixture(tmp_path):
    observed = datetime.now(timezone.utc)
    stamp = lambda seconds: (observed - timedelta(seconds=seconds)).isoformat()
    state = {'workspace': str(tmp_path), 'root': 'parent', 'run': 'run', 'revision': 3,
             'scope': {}, 'visits': [{'id': 'visit', 'phase': 'build'}], 'index': 0}
    args = {'prompt': 'private prompt is never persisted', 'description': 'review',
            'subagent_type': 'general-purpose', 'run_in_background': True}
    row = {'grant_id': 'grant', 'attempt': 1, 'call_id': 'toolu_exact', 'host': 'claude',
           'root': 'parent', 'run': 'run', 'workspace': str(tmp_path), 'state': 'launch_pending',
           'binding': binding(state), 'dispatch_digest': digest(args), 'prepared_at': stamp(60),
           'launch_requested_at': stamp(50), 'worker_id': None, 'events': {}}
    state['workers'] = {'grant': row}
    call = {'sessionId': 'parent', 'cwd': str(tmp_path), 'timestamp': stamp(40), 'type': 'assistant',
            'message': {'content': [{'type': 'tool_use', 'id': 'toolu_exact', 'name': 'Agent', 'input': args}]}}
    result = {'sessionId': 'parent', 'cwd': str(tmp_path), 'timestamp': stamp(30), 'type': 'user',
              'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_exact',
                                      'content': 'Visible text is deliberately not parsed.'}]},
              'toolUseResult': {'isAsync': True, 'status': 'async_launched', 'agentId': 'child'}}
    header = {'sessionId': 'parent', 'agentId': 'child', 'isSidechain': True, 'cwd': str(tmp_path),
              'timestamp': stamp(35), 'message': {'content': 'private child prompt'}}
    return state, row, [call, result], [header]


def native_files(tmp_path, monkeypatch, state, records, headers):
    home = tmp_path / 'native-home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    project = re.sub(r'[^A-Za-z0-9]', '-', state['workspace'])
    parent = home / '.claude' / 'projects' / project / 'parent.jsonl'
    parent.parent.mkdir(parents=True)
    parent.write_text(''.join(json.dumps(row) + '\n' for row in records))
    children = parent.with_suffix('') / 'subagents'
    children.mkdir(parents=True)
    for header in headers:
        (children / ('agent-' + header['agentId'] + '.jsonl')).write_text(json.dumps(header) + '\n')
    return parent, children


def test_structured_launch_uses_exact_call_and_headers_not_text(tmp_path):
    state, row, records, headers = fixture(tmp_path)
    answer = claude.correlate_records('parent', row, records, headers)
    assert answer['status'] == 'matched' and answer['worker_id'] == 'child'
    assert answer['call_id'] == row['call_id'] and answer['dispatch_digest'] == row['dispatch_digest']
    assert len(answer['record_sha256']) == 3
    serialized = json.dumps(answer)
    assert records[0]['message']['content'][0]['input']['prompt'] not in serialized
    assert headers[0]['message']['content'] not in serialized
    assert records[1]['message']['content'][0]['content'] not in serialized
    # Identical delivery is idempotent; it does not fabricate a second launch.
    assert claude.correlate_records('parent', row, records + records, headers + headers) == answer
    workers.reconcile(tmp_path, state, row, answer)
    assert row['worker_id'] == 'child' and row['state'] == 'bootstrapping'


@pytest.mark.parametrize('defect', ['call', 'arguments', 'parent', 'workspace', 'root_child',
    'header_parent', 'header_workspace', 'header_sidechain', 'future', 'naive', 'backwards',
    'duplicate_result', 'duplicate_call', 'error', 'wrong_child', 'foreign_prepared_child'])
def test_launch_conflicts_never_bind(tmp_path, defect):
    state, row, records, headers = fixture(tmp_path)
    if defect == 'call': records[0]['message']['content'][0]['name'] = 'Bash'
    if defect == 'arguments': records[0]['message']['content'][0]['input']['description'] = 'changed'
    if defect == 'parent': records[1]['sessionId'] = 'foreign'
    if defect == 'workspace': records[0]['cwd'] = '/foreign'
    if defect == 'root_child': records[1]['toolUseResult']['agentId'] = 'parent'
    if defect == 'header_parent': headers[0]['sessionId'] = 'foreign'
    if defect == 'header_workspace': headers[0]['cwd'] = '/foreign'
    if defect == 'header_sidechain': headers[0]['isSidechain'] = False
    if defect == 'future': records[1]['timestamp'] = '2999-01-01T00:00:00+00:00'
    if defect == 'naive': records[0]['timestamp'] = '2020-01-01T00:00:00'
    if defect == 'backwards': records[1]['timestamp'] = row['prepared_at']
    if defect == 'duplicate_result': records += [deepcopy(records[1])]; records[-1]['toolUseResult']['agentId'] = 'other'
    if defect == 'duplicate_call': records += [deepcopy(records[0])]; records[-1]['message']['content'][0]['input']['model'] = 'other'
    if defect == 'error': records[1]['message']['content'][0]['is_error'] = True
    if defect == 'wrong_child': records[1]['toolUseResult']['agentId'] = '../escape'
    if defect == 'foreign_prepared_child': row['worker_id'] = 'other'
    answer = claude.correlate_records('parent', row, records, headers)
    assert answer['status'] == 'conflict'
    workers.reconcile(tmp_path, state, row, answer)
    assert row['state'] == 'unknown' and row.get('identity_conflict')


@pytest.mark.parametrize('missing', ['call', 'result', 'header', 'structured', 'status'])
def test_incomplete_launch_is_unknown_not_completion(tmp_path, missing):
    state, row, records, headers = fixture(tmp_path)
    if missing == 'call': records.pop(0)
    if missing == 'result': records.pop()
    if missing == 'header': headers = []
    if missing == 'structured': records[1].pop('toolUseResult')
    if missing == 'status': records[1]['toolUseResult']['status'] = 'unrecognized'
    answer = claude.correlate_records('parent', row, records, headers)
    assert answer['status'] in {'not_yet_available', 'unsupported'}
    workers.reconcile(tmp_path, state, row, answer)
    assert row['worker_id'] is None and row['state'] == 'launch_pending'


@requires_native_reader
def test_bounded_reader_ignores_output_path_and_rejects_unsafe_files(tmp_path, monkeypatch):
    state, row, records, headers = fixture(tmp_path)
    records[1]['toolUseResult']['outputFile'] = '/unrelated/private/output'
    parent, children = native_files(tmp_path, monkeypatch, state, records, headers)
    selection = claude.select_source('parent', {'session_id':'parent', 'hook_event_name':'PreToolUse',
        'transcript_path':str(parent)}, automatic=True)
    row['transcript_source'] = selection['source']
    answer = caps.worker_identity_observation('claude', 'parent', row, {'transcript_path': str(parent)})
    assert answer['status'] == 'matched' and len(answer['references']) == 2
    assert all('/unrelated' not in ref['source'] for ref in answer['references'])
    assert caps.worker_identity_observation('claude', 'parent', row,
        {'transcript_path': '/foreign/session.jsonl'})['status'] == 'conflict'
    child = children / 'agent-child.jsonl'
    saved = child.read_text(); child.unlink(); child.symlink_to(parent)
    assert claude.observe('parent', row, {})['status'] == 'conflict'
    child.unlink(); child.write_text(saved.rstrip('\n'))
    assert claude.observe('parent', row, {})['status'] == 'not_yet_available'
    child.write_text(saved)
    parent.write_bytes(b'x' * (claude.MAX_LINE_BYTES + 1) + b'\n')
    assert claude.observe('parent', row, {})['status'] == 'conflict'


def test_unsupported_native_reader_never_reads_or_admits_identity(tmp_path, monkeypatch):
    state, row, _, _ = fixture(tmp_path)
    monkeypatch.setattr(claude, 'supported_reader', lambda: False)
    def unexpected_read(*args, **kwargs):
        raise AssertionError('Unsupported native reader attempted to open a transcript')
    monkeypatch.setattr(claude, '_open_source', unexpected_read)
    answer = claude.observe('parent', row, {})
    assert answer['status'] == 'unsupported' and 'no-follow' in answer['reason']
    workers.reconcile(tmp_path, state, row, answer)
    assert row['worker_id'] is None


@requires_native_reader
def test_callless_stop_before_result_reconciles_exact_attempt(tmp_path, monkeypatch):
    state, row, records, headers = fixture(tmp_path)
    other = deepcopy(row); other.update(grant_id='other', call_id='other-call')
    state['workers']['other'] = other
    parent, _ = native_files(tmp_path, monkeypatch, state, records[:1], headers)
    selection = claude.select_source('parent', {'session_id':'parent', 'hook_event_name':'PreToolUse',
        'transcript_path':str(parent)}, automatic=True)
    state['claude_transcript_source'] = selection['source']
    stop = {'host': 'claude', 'hook_event_name': 'SubagentStop', 'session_id': 'parent',
            'agent_id': 'child', 'event_id': 'stop-first', 'status':'completed'}
    workers.observe(state, stop)
    assert row['worker_id'] is None and other['worker_id'] is None
    parent.write_text(''.join(json.dumps(record) + '\n' for record in records))
    workers.observe(state, {'host': 'claude', 'hook_event_name': 'PostToolUse', 'session_id': 'parent',
                           'tool_name': 'Agent', 'tool_use_id': 'toolu_exact', 'tool_response': 'visible text'})
    assert row['state'] == 'result_pending' and row['worker_id'] == 'child'
    assert not row.get('claimed_at') and not row.get('context_receipt')
    assert other['worker_id'] is None and other['state'] == 'launch_pending'
    assert not state.get('task_results')


def test_reused_child_and_foreign_call_cannot_join_new_attempt(tmp_path):
    state, row, records, headers = fixture(tmp_path)
    prior = deepcopy(row); prior.update(grant_id='prior', attempt=0, call_id='old-call',
                                       worker_id='child', state='accepted')
    state['workers']['prior'] = prior
    state['unbound_worker_events'] = {'old-stop': {'worker_id': 'child', 'parent': 'parent',
        'call_id': None, 'event': 'SubagentStop', 'status': 'completed', 'observed_at': workers.now()}}
    answer = claude.correlate_records('parent', row, records, headers)
    workers.reconcile(tmp_path, state, row, answer)
    assert row['state'] == 'bootstrapping'
    state['unbound_worker_events']['old-stop']['call_id'] = 'old-call'
    workers.reconcile(tmp_path, state, row, answer)
    assert row['state'] == 'bootstrapping'
    state['unbound_worker_events']['old-stop']['call_id'] = row['call_id']
    workers.reconcile(tmp_path, state, row, answer)
    assert row['state'] == 'result_pending'


def test_late_exact_identity_resolves_unbound_unknown_attempt(tmp_path):
    state, row, records, headers = fixture(tmp_path)
    row['state'] = 'unknown'
    answer = claude.correlate_records('parent', row, records, headers)
    workers.reconcile(tmp_path, state, row, answer)
    assert row['state'] == 'bootstrapping' and row['worker_id'] == 'child'


def test_revocation_and_stale_binding_are_not_revived(tmp_path):
    state, row, records, headers = fixture(tmp_path)
    answer = claude.correlate_records('parent', row, records, headers)
    row.update(state='failed', terminal_status='unavailable', revoked_at=workers.now())
    workers.reconcile(tmp_path, state, row, answer)
    workers.terminal(row, 'completed', 'late-stop')
    assert not workers.bind_worker(state, row, 'child')
    assert row['state'] == 'failed' and row['terminal_status'] == 'unavailable' and row['worker_id'] is None
    assert len(row['reconciliation_events']) == 3
    row.pop('revoked_at'); row.update(state='launch_pending', terminal_status=None)
    state['revision'] += 1
    assert workers.reconcile(tmp_path, state, row, answer)['status'] == 'historical'
    assert row['worker_id'] is None


def test_parent_readiness_requires_current_actual_pair_and_zero_reservations(tmp_path):
    state, _, _, _ = fixture(tmp_path)
    state['workers'] = {}
    before = deepcopy(state)
    with pytest.raises(w.Refusal, match='ordinary admitted command'):
        workers.prepare(tmp_path, state, 'task', {})
    assert state == before
    runtime = caps.runtime_identity()
    proof = {'binding': binding(state), 'workspace': state['workspace'], 'root': state['root'],
             'runtime': runtime, 'matched_call': 'parent-call', 'automatic': True,
             'admitted': True, 'reference': 'automatic-hook/parent-call'}
    state['parent_hook_readiness'] = proof
    assert workers.parent_readiness(state)['status'] == 'ready'
    for delta in [{'root': 'foreign'}, {'workspace': '/foreign'}, {'automatic': False},
                  {'admitted': False}, {'matched_call': None}, {'mismatch': True},
                  {'runtime': {'root': runtime['root'], 'member_sha256': {'old': 'bytes'}}},
                  {'binding': {**binding(state), 'revision': 2}}]:
        state['parent_hook_readiness'] = {**proof, **delta}
        assert workers.parent_readiness(state)['status'] != 'ready'


def test_installed_claude_invocation_never_uses_inherited_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'parent')
    monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID', 'parent')
    assert caps.native_invocation_identity('claude', tmp_path, ['flow', 'worker', '--grant', 'echoed']) is None


def test_host_owned_invocation_requires_exact_actor_pending_call_and_revocation(tmp_path):
    session = {'host': 'claude', 'version': 'fixture', 'workspace': str(tmp_path), 'root': 'parent'}
    value = {'session': session, 'reference': 'native/invocation', 'kind': 'tool_invocation',
             'automatic': True, 'resolved': True, 'pending': True, 'argv': ['flow', 'context'],
             'call_id': 'call', 'principal': 'child', 'hook_principal': 'child'}

    class Owner:
        revoked = False
        def identity(self): return session
        def capabilities(self): return {name: not self.revoked for name in caps.CAPABILITIES}
        def read_event(self, reference): return value

    owner = Owner()
    native = host_native.NativeSession(owner, host='claude', version='fixture', workspace=tmp_path, root='parent')
    assert native.verify_invocation('native/invocation', ['flow', 'context'])['principal'] == 'child'
    for field, bad in [('pending', False), ('hook_principal', 'parent'), ('argv', ['other']),
                       ('automatic', False), ('call_id', '')]:
        previous = value[field]; value[field] = bad
        with pytest.raises(w.Refusal): native.verify_invocation('native/invocation', ['flow', 'context'])
        value[field] = previous
    owner.revoked = True
    with pytest.raises(w.Refusal): native.verify_invocation('native/invocation', ['flow', 'context'])


def integrated_claude(tmp_path, monkeypatch, count=2):
    """Automatic hook fixtures with actual pinned JSONL; never live certification."""
    from taskplane import flow, claude_worker_invocation as inv
    from taskplane.tests.test_worker_runtime import setup, reserve
    c, state = setup(tmp_path, count=count)
    c.adapter.name = 'claude'
    home = tmp_path.parent/(tmp_path.name+'-native-home')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'root')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    # OS boot probe may fail inside the test sandbox. This is an explicit fixture.
    monkeypatch.setattr(inv, '_boot_identity', lambda: 'fixture:controller-boot')
    parent = home/'.claude/projects/original-project/root.jsonl'
    parent.parent.mkdir(parents=True)
    parent.write_text(json.dumps({'sessionId':'root', 'type':'user', 'cwd':str(tmp_path)})+'\n')
    children = parent.with_suffix('')/'subagents'; children.mkdir(parents=True)
    items = []
    for index in range(count):
        item = reserve(c, state, f'T{index}')
        event = {'hook_event_name':'PreToolUse', 'host':'claude', 'session_id':'root',
                 'cwd':str(tmp_path), 'transcript_path':str(parent), 'tool_name':'Task',
                 'tool_use_id':f'launch-{index}', 'tool_input':{'prompt':item['message'],
                    'description':'fixture', 'subagent_type':'general-purpose', 'run_in_background':True}}
        flow.hook(event, governor=c)
        offset = parent.stat().st_size
        assert c.report()['workers'][item['grant']['grant_id']]['transcript_admission_offset'] == offset
        called = workers.now()
        header = {'sessionId':'root', 'agentId':f'child-{index}', 'isSidechain':True,
                  'cwd':str(tmp_path), 'timestamp':workers.now()}
        records = [dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=called,
                        message={'content':[dict(type='tool_use', id=f'launch-{index}', name='Task', input=event['tool_input'])]}),
                   dict(type='user', sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
                        message={'content':[dict(type='tool_result', tool_use_id=f'launch-{index}', content='untrusted')]},
                        toolUseResult={'isAsync':True, 'status':'async_launched', 'agentId':f'child-{index}'})]
        with parent.open('a') as stream:
            stream.write(''.join(json.dumps(row)+'\n' for row in records))
        (children/f'agent-child-{index}.jsonl').write_text(json.dumps(header)+'\n')
        flow.hook({**event, 'hook_event_name':'PostToolUse'}, governor=c)
        items.append(item)
    return c, state, parent, items


def invocation_event(c, state, parent, *, actor='child-0', grant=None, task=None, call='invoke', extra=()):
    import shlex
    import sys
    words = [str(Path(sys.executable).resolve()), str(Path(w.__file__).with_name('tp.py').resolve()), 'flow',
             'worker' if grant else 'context', '--workspace', str(c.workspace), '--run', state['run']]
    if grant: words += ['--operation', 'claim', '--grant', grant]
    if task: words += ['--task', task]
    words += list(extra)
    event = {'hook_event_name':'PreToolUse', 'host':'claude', 'session_id':'root',
             'cwd':str(c.workspace), 'transcript_path':str(parent), 'tool_name':'Bash',
             'tool_use_id':call, 'tool_input':{'command':shlex.join(words), 'description':'fixture', 'timeout':10000}}
    if actor != 'root': event['agent_id'] = actor
    return event


def rewritten(event):
    import shlex
    from taskplane import flow
    result = flow.hook(deepcopy(event))
    output = result['hookSpecificOutput']
    assert 'permissionDecision' not in output
    return output['updatedInput'], shlex.split(output['updatedInput']['command'])[1:]


@requires_native_reader
def test_controller_concurrent_identity_claim_context_and_replay(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from taskplane import workflow_host as host, flow
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch)
    calls = [invocation_event(c, state, parent, actor=f'child-{i}', grant=item['grant']['grant_id'], call=f'claim-{i}')
             for i, item in enumerate(items)]
    issued = [rewritten(event) for event in calls]
    assert issued[0][1][-1] != issued[1][1][-1]
    assert rewritten(calls[0])[1] == issued[0][1]  # duplicate pre retains one reference
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(host.Controller.invoke_claude, [issued[1][1], issued[0][1]]))
    assert [r['task_id'] for r in results] == ['T1','T0']
    for (_, argv), event in zip(issued, calls):
        with pytest.raises(w.Refusal): host.Controller.invoke_claude(argv)
        with pytest.raises(w.Refusal): rewritten(event)  # consumed pre cannot issue again
    for i, ((updated, _), event) in enumerate(zip(issued, calls)):
        flow.hook({**event, 'hook_event_name':'PostToolUse', 'tool_input':updated})
        assert workers.readiness(c.report()['workers'][items[i]['grant']['grant_id']])['status'] == 'pending'
        context = invocation_event(c, state, parent, actor=f'child-{i}', task=f'T{i}', call=f'context-{i}')
        _, argv = rewritten(context)
        descriptor = host.Controller.invoke_claude(argv)
        drain = invocation_event(c, state, parent, actor=f'child-{i}', task=f'T{i}', call=f'drain-{i}',
                                 extra=('--drain', descriptor['handoff_ref']['sha256']))
        _, argv = rewritten(drain)
        result = host.Controller.invoke_claude(argv)
        assert result['done']
        row = c.report()['workers'][items[i]['grant']['grant_id']]
        assert row['worker_id'] == f'child-{i}' and row['context_receipt']
        ordinary = {**context, 'tool_name':'Read', 'tool_use_id':f'ready-{i}', 'tool_input':{'file_path':'input.py'}}
        flow.hook(ordinary); flow.hook({**ordinary, 'hook_event_name':'PostToolUse'})
        assert workers.readiness(c.report()['workers'][items[i]['grant']['grant_id']])['status'] == 'ready'
    saved = c.report()
    assert saved['claude_transcript_cursor']['offset'] == parent.stat().st_size
    assert saved['workers'][items[1]['grant']['grant_id']]['transcript_admission_offset'] > saved['claude_transcript_source']['selected_size']
    for row in saved['workers'].values():
        assert row['launch_proof_refs'] and row['launch_proof_ref'] in row['launch_proof_refs']


@requires_native_reader
@pytest.mark.parametrize('defect', ['unclaimed', 'foreign-grant', 'root-grant', 'missing-task', 'foreign-task', 'background', 'extra-field'])
def test_controller_invocation_scope_refusals(tmp_path, monkeypatch, defect):
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch)
    grant = items[1 if defect == 'foreign-grant' else 0]['grant']['grant_id']
    event = invocation_event(c, state, parent, grant=grant if defect != 'unclaimed' else None,
                             task='T0' if defect == 'unclaimed' else None)
    if defect in {'missing-task','foreign-task'}:
        from taskplane.workflow_host import Controller
        Controller.invoke_claude(rewritten(event)[1])
        event = invocation_event(c, state, parent, task='T1' if defect == 'foreign-task' else None, call='context')
    if defect == 'root-grant': event.pop('agent_id')
    if defect == 'background': event['tool_input']['run_in_background'] = True
    if defect == 'extra-field': event['tool_input']['recipient'] = 'other'
    if defect == 'root-grant':
        # Root claim is rejected in the CLI; no worker invocation is minted.
        from taskplane import flow
        assert 'updatedInput' not in flow.hook(event).get('hookSpecificOutput', {})
    else:
        with pytest.raises(w.Refusal): rewritten(event)


@requires_native_reader
def test_cli_locator_precedes_inherited_identity_and_missing_reference_refuses(tmp_path, monkeypatch, capsys):
    import sys
    from taskplane import flow
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=1)
    event = invocation_event(c, state, parent, grant=items[0]['grant']['grant_id'])
    _, argv = rewritten(event)
    monkeypatch.setenv('CLAUDE_SESSION_ID','foreign-inherited-root')
    monkeypatch.setattr(sys, 'argv', argv)
    assert flow.main(argv[2:]) == 0
    assert json.loads(capsys.readouterr().out)['task_id'] == 'T0'
    assert flow.main(argv[2:]) == 2
    capsys.readouterr()
    assert flow.main(['context','--workspace',str(tmp_path),'--run',state['run']]) == 2
    assert 'reference' in capsys.readouterr().out


@requires_native_reader
def test_invocation_concurrent_double_consume_and_foreign_reference(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from taskplane.workflow_host import Controller
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=1)
    _, argv = rewritten(invocation_event(c, state, parent, grant=items[0]['grant']['grant_id']))
    def consume_once(_):
        try: return Controller.invoke_claude(argv)['task_id']
        except w.Refusal: return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume_once, range(2))) == ['T0', 'refused']
    foreign = argv[:-1]+[argv[-1][:-64]+'0'*64]
    with pytest.raises(w.Refusal, match='unknown or foreign'): Controller.invoke_claude(foreign)


@requires_native_reader
@pytest.mark.parametrize('representation', ['original','rewritten','mismatch','runtime'])
def test_invocation_post_requires_exact_recorded_input_and_runtime(tmp_path, monkeypatch, representation):
    from taskplane import flow, workflow_host as host
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=1)
    event = invocation_event(c, state, parent, grant=items[0]['grant']['grant_id'])
    updated, argv = rewritten(event)
    host.Controller.invoke_claude(argv)
    post = {**event, 'hook_event_name':'PostToolUse', 'tool_input':event['tool_input'] if representation == 'original' else updated}
    if representation == 'mismatch': post['tool_input'] = {**updated,'description':'later plugin rewrite'}
    if representation == 'runtime':
        monkeypatch.setattr(caps, 'runtime_identity', lambda:{'root':'foreign','member_sha256':{}})
    if representation in {'mismatch','runtime'}:
        with pytest.raises(w.Refusal): flow.hook(post)
    else:
        flow.hook(post)
        db = c._read(c._path())
        admission = next(row for row in db['admissions'].values() if row['call_id'] == 'invoke')
        assert admission['post_input_representation'] == representation


@requires_native_reader
def test_genuine_root_context_gets_distinct_one_use_transport(tmp_path, monkeypatch):
    from taskplane.workflow_host import Controller
    c, state, parent, _ = integrated_claude(tmp_path, monkeypatch, count=1)
    event = invocation_event(c, state, parent, actor='root')
    _, argv = rewritten(event)
    result = Controller.invoke_claude(argv)
    assert result['binding']['root'] == 'root'
    db = c._read(c._path())
    record = next(row['invocation'] for row in db['admissions'].values() if row['call_id']=='invoke')
    assert record['binding']['kind'] == 'root-context' and record['binding']['grant_id'] is None


@requires_native_reader
def test_handback_implicit_parent_readiness_and_unsupported_terminal(tmp_path, monkeypatch):
    from taskplane import flow
    from taskplane.tests.test_worker_runtime import consume
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=1)
    consume(c, state, items[0]['grant'], 'child-0')
    base = invocation_event(c, state, parent)
    handback = {**base, 'tool_name':'SubagentHandback', 'tool_use_id':'return', 'tool_input':{'message':'T0.md contains fixture evidence'}}
    with pytest.raises(w.Refusal, match='readiness'): flow.hook(handback)
    ready = {**base, 'tool_name':'Read', 'tool_use_id':'ready', 'tool_input':{'file_path':'input.py'}}
    flow.hook(ready); flow.hook({**ready,'hook_event_name':'PostToolUse'})
    for delta in [{'tool_input':{'message':'report','recipient':'sibling'}}, {'agent_id':'foreign'}, {'agent_id':None}]:
        with pytest.raises(w.Refusal): flow.hook({**handback, **delta})
    flow.hook(handback); flow.hook(handback)
    with pytest.raises(w.Refusal, match='competing'): flow.hook({**handback,'tool_use_id':'competing'})
    c.observe({'host':'claude','hook_event_name':'SubagentStop','session_id':'root','agent_id':'child-0'},state['run'])
    row = c.report()['workers'][items[0]['grant']['grant_id']]
    assert row['state']=='running' and row['handback']['status']=='delivery_unknown'
    assert c.report()['claude_terminal_observation']['status']=='unsupported'
    c.observe({'host':'claude','hook_event_name':'SubagentStop','session_id':'root','agent_id':'child-0','status':'completed'},state['run'])
    flow.hook({**handback,'hook_event_name':'PostToolUse','tool_response':{'message':'text is not delivery proof'}})
    row = c.report()['workers'][items[0]['grant']['grant_id']]
    assert row['state']=='result_pending' and row['handback']['status']=='delivery_unknown'
    with pytest.raises(w.Refusal): flow.hook({**ready,'tool_use_id':'after-stop'})
    with pytest.raises(w.Refusal, match='handback'):
        c.worker(state['run'],'accept-result',revision=state['revision'],task='T0',request={'grant':row['grant_id']})


@requires_native_reader
@pytest.mark.parametrize('defect', ['revoked', 'generation', 'source-missing', 'runtime', 'foreign-workspace'])
def test_pending_reference_revalidates_current_attempt_and_source(tmp_path, monkeypatch, defect):
    from taskplane import workflow_host as host
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=1)
    _, argv = rewritten(invocation_event(c, state, parent, grant=items[0]['grant']['grant_id']))
    if defect in {'revoked','generation'}:
        db = c._read(c._path())
        saved = db['runs'][state['run']]
        if defect == 'revoked': saved['workers'][items[0]['grant']['grant_id']]['revoked_at'] = workers.now()
        else: saved['revision'] += 1
        c._write(c._path(), db)
    if defect == 'source-missing': parent.unlink()
    if defect == 'runtime': monkeypatch.setattr(caps,'runtime_identity',lambda:{'root':'foreign','member_sha256':{}})
    if defect == 'foreign-workspace': argv[argv.index('--workspace')+1] = str(tmp_path.parent)
    with pytest.raises(w.Refusal): host.Controller.invoke_claude(argv)


@requires_native_reader
def test_direct_worker_context_requires_explicit_claim(tmp_path, monkeypatch):
    from taskplane import workflow_host as host
    c, state, _, _ = integrated_claude(tmp_path, monkeypatch, count=1)
    worker = host.Controller(tmp_path,'root',host.installed_adapter('claude'),principal='child-0')
    with pytest.raises(w.Refusal, match='claimed task'):
        worker.context(state['run'], task='T0')
