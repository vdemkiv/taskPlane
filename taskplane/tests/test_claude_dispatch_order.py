"""Real file ordering through production hooks; not live Claude certification."""
from copy import deepcopy

import pytest

from taskplane import flow, workflow as w, worker_runtime as workers
from taskplane.context import digest
from taskplane.host_capabilities import runtime_identity
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_claude_worker_lifecycle import encoded
from taskplane.tests.test_worker_runtime import reserve


def prepared(tmp_path, monkeypatch, tool='Agent'):
    host = Interactive(tmp_path, monkeypatch)
    item = reserve(host.controller, host.state)
    host.grant = item['grant']['grant_id']
    host.child_id = 'child-buffered'
    host.child = host.parent.with_suffix('') / 'subagents' / ('agent-' + host.child_id + '.jsonl')
    event = host.root_control(tool, {'prompt': item['message'], 'description': 'Buffered launch',
                                    'subagent_type': 'taskplane:tp-lens'})
    call = dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
        message={'content': [dict(type='tool_use', id=event['tool_use_id'], name=tool,
                                 input=deepcopy(event['tool_input']))]})
    return host, event, call


def child_records(host, call_id):
    header = dict(sessionId='root', agentId=host.child_id, isSidechain=True,
                  cwd=str(host.workspace), timestamp=workers.now())
    result = dict(type='user', sessionId='root', cwd=str(host.workspace), timestamp=workers.now(),
        message={'content': [dict(type='tool_result', tool_use_id=call_id, content='Native launch')]},
        toolUseResult=dict(isAsync=True, status='async_launched', agentId=host.child_id))
    return header, result


def no_worker_authority(host, before):
    row = host.row()
    assert row['worker_id'] is None and not row.get('identity_observation')
    assert not row.get('claimed_at') and not row.get('context_receipt')
    assert workers.readiness(row)['status'] != 'ready'
    with pytest.raises(w.Refusal, match='exact structured native launch proof'):
        host.controller.claude_actor(host.child_id, host.event('Read', {'file_path': 'input.py'}))
    with pytest.raises(w.Refusal, match='Only an unlaunched reservation'):
        host.controller.worker(host.state['run'], 'abandon', revision=host.state['revision'], grant=host.grant)
    current = host.controller.report()
    assert current['decisions'] == before['decisions']
    assert current.get('task_results') == before.get('task_results')


@pytest.mark.parametrize('tool', ['Agent', 'Task'])
@pytest.mark.parametrize('order', ['call_first', 'hook_first'])
def test_launch_reservation_survives_either_flush_order_without_granting_child_access(tmp_path, monkeypatch, tool, order):
    host, event, call = prepared(tmp_path, monkeypatch, tool)
    before = host.controller.report()
    if order == 'call_first':
        host.append(call)
    flow.hook(event, governor=host.controller)
    pending = host.row()
    assert pending['state'] == 'launch_pending' and pending['call_id'] == event['tool_use_id']
    assert pending['dispatch_digest'] == digest(event['tool_input'])
    assert pending['launch_preflight']['status'] == ('matched' if order == 'call_first' else 'awaiting_transcript')
    status = host.controller.worker(host.state['run'], 'status')
    assert status['attempts'][0]['launch_preflight'] == pending['launch_preflight']
    with pytest.raises(w.Refusal, match='Conflicting or stale native launch'):
        flow.hook({**event, 'tool_use_id': 'duplicate-launch'}, governor=host.controller)
    if order == 'hook_first':
        assert pending['launch_preflight']['watermark'] == host.parent.stat().st_size
        assert 'reference' not in pending['launch_preflight']
    header, result = child_records(host, event['tool_use_id'])
    # A host post-hook result alone still cannot bind a child from its asserted ID.
    flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_response': result['toolUseResult']},
              governor=host.controller)
    no_worker_authority(host, before)
    if order == 'hook_first':
        host.append(call)
    host.root_hook()
    no_worker_authority(host, before)
    host.append(result)
    host.root_hook()
    no_worker_authority(host, before)
    host.child.parent.mkdir(parents=True)
    host.child.write_bytes(encoded(header))
    host.root_hook()
    bound = host.row()
    assert bound['state'] == 'bootstrapping' and bound['worker_id'] == host.child_id
    assert bound['identity_observation']['status'] == 'matched'
    assert not bound.get('claimed_at') and not bound.get('context_receipt')
    host.ready()  # Still requires the existing claim, context drain and automatic pre/post pair.
    assert workers.readiness(host.row())['status'] == 'ready'
    assert not host.controller.report().get('task_results')


@pytest.mark.parametrize('defect', ['input', 'workspace', 'child_header', 'duplicate_call', 'source_replaced'])
def test_deferred_native_conflict_never_binds_or_clears_pending_attempt(tmp_path, monkeypatch, defect):
    host, event, call = prepared(tmp_path, monkeypatch)
    before = host.controller.report()
    flow.hook(event, governor=host.controller)
    if defect == 'input':
        call['message']['content'][0]['input']['run_in_background'] = 'true'
    if defect == 'workspace':
        call['cwd'] += '-foreign'
    header, result = child_records(host, event['tool_use_id'])
    if defect == 'child_header':
        header['sessionId'] = 'foreign'
    host.append(call)
    if defect == 'duplicate_call':
        host.append({**call, 'uuid': 'competing-native-record'})
    host.append(result)
    host.child.parent.mkdir(parents=True)
    host.child.write_bytes(encoded(header))
    if defect == 'source_replaced':
        old = host.parent.with_suffix('.saved')
        host.parent.rename(old)
        host.parent.write_bytes(old.read_bytes())
    host.root_hook()
    assert host.row()['state'] == 'unknown' and host.row()['identity_conflict']
    no_worker_authority(host, before)


@pytest.mark.parametrize('defect', ['manual', 'post', 'host', 'principal', 'root', 'profile', 'session',
                                  'workspace', 'child', 'parent', 'runtime', 'missing_source'])
def test_missing_transcript_call_requires_current_automatic_root_provenance(tmp_path, monkeypatch, defect):
    host, event, _ = prepared(tmp_path, monkeypatch)
    state = host.controller.report()
    event.update(host='claude', taskplane_automatic_hook=True, taskplane_runtime_identity=runtime_identity(),
        taskplane_claude_root_source=True,
        taskplane_observed_binding={'root': 'root', 'principal': 'root', 'profile': 'native_workflow'})
    if defect == 'manual': event['taskplane_automatic_hook'] = False
    if defect == 'post': event['hook_event_name'] = 'PostToolUse'
    if defect == 'host': event['host'] = 'codex'
    if defect in {'principal', 'root', 'profile'}: event['taskplane_observed_binding'][defect] = 'foreign'
    if defect == 'session': event['session_id'] = 'foreign'
    if defect == 'workspace': event['cwd'] += '-foreign'
    if defect == 'child': event['agent_id'] = 'child'
    if defect == 'parent': event['parent_session_id'] = 'foreign'
    if defect == 'runtime': event['taskplane_runtime_identity'] = {'root': 'foreign'}
    if defect == 'missing_source': event.pop('taskplane_claude_root_source')
    before = deepcopy(state['workers'][host.grant])
    with pytest.raises(w.Refusal) as exc:
        workers.admit(state, event)
    assert exc.value.reason == 'unsupported_authority'
    assert state['workers'][host.grant] == before


def test_caller_source_hint_cannot_survive_failed_automatic_selection(tmp_path, monkeypatch):
    host, event, _ = prepared(tmp_path, monkeypatch)
    original = host.row()
    event.update(transcript_path=str(host.parent.with_name('foreign.jsonl')), taskplane_claude_root_source=True)
    with pytest.raises(w.Refusal, match='prepared and fresh automatic root transcript source'):
        flow.hook(event, governor=host.controller)
    assert host.row() == original
