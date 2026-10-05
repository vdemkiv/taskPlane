"""Synthetic transcript fixtures; live recovery still requires native observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from taskplane import flow, host_capabilities as hc, worker_runtime as wr, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve


def transcript_fixture(tmp_path, monkeypatch, worker=None):
    home = tmp_path.parent / (tmp_path.name + '-native-home')
    folder = home / 'sessions'
    folder.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    start = datetime.now(timezone.utc) - timedelta(days=1)
    if worker is None:
        worker = dict(workspace=str(tmp_path), grant_id='fixture-grant',
                      task_name='fixture__fixture-grant', prepared_at=start.isoformat())
    else:
        start = datetime.fromisoformat(worker['prepared_at'])
    name = '/root/' + worker['task_name']
    # Live controller fixtures use equal timestamps; historical parser fixtures
    # deliberately exceed recover-unavailable's 15-minute observation window.
    stamp = start.isoformat()
    def record(kind, **payload):
        return dict(type='response_item', timestamp=stamp, metadata={'client_authored': False},
                    payload=dict(type=kind, **payload))
    root = [dict(type='session_meta', timestamp=stamp, payload=dict(id='root', cwd=worker['workspace'])),
            record('function_call', namespace='collaboration', name='spawn_agent', call_id='launch',
                   arguments=json.dumps(dict(task_name=worker['task_name'], fork_turns='none', message='opaque'))),
            record('function_call_output', call_id='launch', output=json.dumps(dict(task_name=name))),
            record('function_call', namespace='collaboration', name='list_agents', call_id='terminal',
                   arguments=json.dumps(dict(path_prefix=name))),
            record('function_call_output', call_id='terminal', output=json.dumps(dict(agents=[
                dict(agent_name=name, agent_status={'completed': 'Claim failed; no task work.'})])))]
    child = [dict(type='session_meta', timestamp=stamp, payload=dict(
        id='child', timestamp=stamp, parent_thread_id='root', cwd=worker['workspace'],
        thread_source='subagent', source={'subagent': {'thread_spawn': dict(parent_thread_id='root', agent_path=name)}})),
        dict(type='event_msg', timestamp=stamp, payload={'type': 'task_complete'})]
    paths = (folder / 'rollout-root.jsonl', folder / 'rollout-child.jsonl')
    return worker, root, child, paths


def write_fixture(root, child, paths):
    for rows, path in zip((root, child), paths):
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def observe(worker):
    return hc.unbound_worker_observation('root', worker, 'launch', 'terminal')


def test_historical_native_observation_does_not_invent_admission(tmp_path, monkeypatch):
    worker, root, child, paths = transcript_fixture(tmp_path, monkeypatch)
    write_fixture(root, child, paths)
    proof = observe(worker)
    assert proof['worker_id'] == 'child'
    assert proof['native_status'] == 'completed'
    assert proof['process_exit'] == 'unknown' and proof['assurance'] == 'observed'
    assert proof['launch_call_id'] == 'launch' and proof['terminal_call_id'] == 'terminal'
    assert len(proof['sha256']) == 64
    assert 'call_id' not in worker and 'worker_id' not in worker


@pytest.mark.parametrize('defect', [
    'root_identity', 'root_workspace', 'missing_root', 'duplicate_root', 'root_symlink', 'oversized',
    'incomplete', 'bad_json', 'list_record', 'null_payload', 'duplicate_launch', 'duplicate_result',
    'missing_result', 'client_authored', 'null_metadata', 'wrong_namespace', 'wrong_tool',
    'null_arguments', 'invalid_output', 'list_output', 'wrong_task', 'wrong_grant', 'wrong_name',
    'wrong_fork', 'wrong_poll', 'null_agents', 'malformed_agent', 'missing_agent', 'duplicate_agent',
    'running', 'null_status', 'ambiguous_status', 'bad_timestamp', 'null_timestamp', 'naive_timestamp',
    'future_timestamp', 'old_launch', 'reordered_calls', 'conflicting_launch', 'later_followup',
    'later_message', 'later_interrupt', 'foreign_child', 'child_workspace', 'missing_child',
    'ambiguous_child', 'child_symlink', 'conflicting_lineage', 'forked_child', 'restarted_child',
    'later_child_activity', 'child_timestamp', 'root_restart', 'fifo',
])
def test_unbound_observation_refuses_ambiguous_or_malformed_proof(tmp_path, monkeypatch, defect):
    worker, root, child, paths = transcript_fixture(tmp_path, monkeypatch)
    name = '/root/' + worker['task_name']
    def arguments(index, **values):
        current = json.loads(root[index]['payload']['arguments'])
        root[index]['payload']['arguments'] = json.dumps({**current, **values})
    def inventory(agents):
        root[4]['payload']['output'] = json.dumps({'agents': agents})
    if defect == 'root_identity': root[0]['payload']['id'] = 'other'
    if defect == 'root_workspace': root[0]['payload']['cwd'] = '/other'
    if defect == 'duplicate_launch': root.insert(2, deepcopy(root[1]))
    if defect == 'duplicate_result': root.append(deepcopy(root[-1]))
    if defect == 'missing_result': root.pop(2)
    if defect == 'client_authored': root[2]['metadata']['client_authored'] = True
    if defect == 'null_metadata': root[1]['metadata'] = None
    if defect == 'wrong_namespace': root[1]['payload']['namespace'] = 'other'
    if defect == 'wrong_tool': root[3]['payload']['name'] = 'interrupt_agent'
    if defect == 'null_arguments': root[1]['payload']['arguments'] = None
    if defect == 'invalid_output': root[2]['payload']['output'] = 'malformed'
    if defect == 'list_output': root[2]['payload']['output'] = '[]'
    if defect == 'wrong_task': arguments(1, task_name='other')
    if defect == 'wrong_grant': worker['grant_id'] = 'other'
    if defect == 'wrong_name': root[2]['payload']['output'] = json.dumps({'task_name': '/root/other'})
    if defect == 'wrong_fork': arguments(1, fork_turns='all')
    if defect == 'wrong_poll': arguments(3, path_prefix='/other')
    if defect == 'null_agents': inventory(None)
    if defect == 'malformed_agent': inventory([None])
    if defect == 'missing_agent': inventory([])
    if defect == 'duplicate_agent': inventory([{'agent_name': name}] * 2)
    if defect == 'running': inventory([dict(agent_name=name, agent_status={'running': None})])
    if defect == 'null_status': inventory([dict(agent_name=name, agent_status=None)])
    if defect == 'ambiguous_status': inventory([dict(agent_name=name, agent_status={'completed': '', 'running': ''})])
    if defect == 'bad_timestamp': root[1]['timestamp'] = 'bad'
    if defect == 'null_timestamp': root[4]['timestamp'] = None
    if defect == 'naive_timestamp': root[4]['timestamp'] = '2026-01-01T00:00:00'
    if defect == 'future_timestamp': root[4]['timestamp'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    if defect == 'old_launch': root[1]['timestamp'] = (datetime.fromisoformat(worker['prepared_at']) - timedelta(seconds=1)).isoformat()
    if defect == 'reordered_calls': root = [root[0], *root[3:], *root[1:3]]
    if defect == 'conflicting_launch':
        duplicate = deepcopy(root[1]); duplicate['payload']['call_id'] = 'another-launch'; root.append(duplicate)
    if defect in ('later_followup', 'later_message', 'later_interrupt'):
        duplicate = deepcopy(root[1])
        duplicate['payload'].update(call_id='later-input', arguments=json.dumps({'target': name}),
                                    name={'later_followup': 'followup_task', 'later_message': 'send_message',
                                          'later_interrupt': 'interrupt_agent'}[defect])
        root.append(duplicate)
    if defect == 'foreign_child': child[0]['payload']['parent_thread_id'] = 'other'
    if defect == 'child_workspace': child[0]['payload']['cwd'] = '/other'
    if defect == 'conflicting_lineage': child[0]['payload']['source']['subagent']['thread_spawn']['parent_thread_id'] = 'other'
    if defect == 'forked_child': child[0]['payload']['forked_from_id'] = 'root'
    if defect == 'restarted_child': child[0]['payload']['history_base'] = dict(thread_id='child', end_ordinal_exclusive=1, end_byte_offset=1)
    if defect == 'later_child_activity': child[1]['timestamp'] = (datetime.fromisoformat(worker['prepared_at']) + timedelta(seconds=1)).isoformat()
    if defect == 'child_timestamp': child[0]['payload']['timestamp'] = 'bad'
    if defect == 'root_restart': root.append(deepcopy(root[0]))
    if defect == 'list_record': root.append([])
    if defect == 'null_payload': root[1]['payload'] = None
    write_fixture(root, child, paths)
    if defect == 'missing_root': paths[0].unlink()
    if defect == 'missing_child': paths[1].unlink()
    if defect == 'duplicate_root': paths[0].with_name('duplicate-root.jsonl').write_bytes(paths[0].read_bytes())
    if defect == 'ambiguous_child':
        child[0]['payload']['id'] = 'other-child'
        paths[1].with_name('rollout-other-child.jsonl').write_text(json.dumps(child[0]) + '\n')
    if defect in ('root_symlink', 'child_symlink'):
        path = paths[int(defect == 'child_symlink')]
        actual = path.with_suffix('.log'); path.rename(actual); path.symlink_to(actual)
    if defect == 'oversized': monkeypatch.setattr(hc, 'MAX_RECOVERY_BYTES', 16)
    if defect == 'incomplete': paths[0].write_bytes(paths[0].read_bytes().rstrip(b'\n'))
    if defect == 'bad_json': paths[0].write_bytes(paths[0].read_bytes() + b'bad\n')
    if defect == 'fifo':
        import os
        paths[0].unlink(); os.mkfifo(paths[0])
    with pytest.raises(w.Refusal): observe(worker)


def test_recovery_releases_only_failed_reservation_and_allows_fresh_attempt(tmp_path, monkeypatch, capsys):
    c, s = setup(tmp_path, count=2)
    prepared = reserve(c, s)
    grant = prepared['grant']['grant_id']
    sibling = reserve(c, s, task='T1')['grant']['grant_id']
    before = c.report()
    worker, root, child, paths = transcript_fixture(tmp_path, monkeypatch, prepared['grant'])
    write_fixture(root, child, paths)
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    request = {'request_reference': 'fixture/user-repair', 'launch_call_id': 'launch', 'terminal_call_id': 'terminal'}
    assert flow.main(['worker', '--workspace', str(tmp_path), '--run', s['run'], '--operation', 'recover-unbound',
                      '--grant', grant, '--expected-revision', str(s['revision']), '--worker-json', json.dumps(request)], governor=c) == 0
    capsys.readouterr()
    after = c.report()
    row = after['workers'][grant]
    assert row['state'] == 'failed' and row['terminal_status'] == 'unadmitted_launch_revoked'
    assert row['revoked_at'] and row['recovery']['worker_id'] == 'child'
    assert row['recovery']['process_exit'] == 'unknown' and not row.get('ended_at')
    assert row['worker_id'] is None and row['call_id'] is None and row['context_receipt'] is None
    assert not row.get('claimed_at') and not after.get('task_results')
    assert before['workers'][sibling] == after['workers'][sibling]
    for key in ('decisions', 'revision', 'scope', 'history'):
        assert before[key] == after[key]
    assert not wr.bind_worker(after, row, 'child', '/root/' + worker['task_name'])
    with pytest.raises(w.Refusal):
        c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0', grant=grant,
                 request={'outputs': ['T0.md'], 'checks': []})
    stable = c._path().read_bytes()
    # Replays refuse without changing the recorded recovery or resurrecting the grant.
    with pytest.raises(w.Refusal):
        c.worker(s['run'], 'recover-unbound', revision=s['revision'], grant=grant, request=request)
    assert c._path().read_bytes() == stable
    fresh = reserve(c, s, retry_reason='The missed native launch was verified terminal and revoked.')['grant']
    assert fresh['grant_id'] != grant and fresh['attempt'] == 2
    assert fresh['state'] == 'prepared' and fresh['worker_id'] is None


def test_revocation_can_record_failure_during_drift_but_cannot_bless_source_changes(tmp_path, monkeypatch):
    c, s = setup(tmp_path, count=1)
    prepared = reserve(c, s)
    _, root, child, paths = transcript_fixture(tmp_path, monkeypatch, prepared['grant'])
    write_fixture(root, child, paths)
    (tmp_path / 'outside.py').write_text('maintenance edit\n')
    before = c.report()['source_baseline']
    recovered = c.worker(s['run'], 'recover-unbound', revision=s['revision'], grant=prepared['grant']['grant_id'],
                         request={'request_reference': 'fixture/user-repair', 'launch_call_id': 'launch', 'terminal_call_id': 'terminal'})
    assert recovered['state'] == 'failed'
    assert c.report()['source_baseline'] == before
    with pytest.raises(w.Refusal, match='outside'):
        reserve(c, s, retry_reason='Verified failed launch; needs new context.')


@pytest.mark.parametrize('defect', ['revision', 'principal', 'profile', 'host', 'bound', 'admitted', 'claimed',
                                  'context', 'accepted', 'live_command', 'reference', 'call', 'malformed_native'])
def test_recovery_refusal_preserves_controller_bytes(tmp_path, monkeypatch, defect):
    c, s = setup(tmp_path, count=1)
    prepared = reserve(c, s); grant = prepared['grant']['grant_id']
    control_path = c._path()
    _, root, child, paths = transcript_fixture(tmp_path, monkeypatch, prepared['grant'])
    if defect == 'malformed_native': root[-1]['metadata'] = []
    write_fixture(root, child, paths)
    if defect == 'principal': c.principal = 'child'
    if defect == 'profile': monkeypatch.setattr(c.adapter, 'profile', 'protected_host')
    if defect == 'host': monkeypatch.setattr(c.adapter, 'name', 'claude')
    fields = dict(bound=('worker_id', 'child'), admitted=('call_id', 'launch'), claimed=('claimed_at', 'fixture'),
                  context=('context_receipt', {}), accepted=('state', 'accepted'))
    # Corrupt fixture state through the test controller only; production never edits state directly.
    if defect in fields or defect == 'live_command':
        db = c._read(c._path()); state = db['runs'][s['run']]
        if defect in fields:
            key, value = fields[defect]; state['workers'][grant][key] = value
        else:
            state['observed_handles'] = {'fixture': {'state': 'running'}}
        c._write(c._path(), db)
    before = control_path.read_bytes()
    with pytest.raises(w.Refusal):
        c.worker(s['run'], 'recover-unbound', revision=s['revision'] + int(defect == 'revision'), grant=grant,
                 request={'request_reference': '' if defect == 'reference' else 'fixture/user-repair',
                          'launch_call_id': [] if defect == 'call' else 'launch', 'terminal_call_id': 'terminal'})
    assert control_path.read_bytes() == before
