"""Synthetic two-cycle startup incident; never an installed-host certification."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from taskplane import claude_startup_recovery as recovery
from taskplane import claude_worker_observations as native, flow, workflow as w, worker_runtime as workers
from taskplane.context import Store, digest
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_claude_worker_lifecycle import encoded

pytestmark = pytest.mark.skipif(not native.supported_reader(), reason='Native no-follow reads unavailable')

# Fixed host boilerplate, no private incident messages or paths.
DENIAL = (
    'Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Auto-Mode Bypass]. '
    "If you have other tasks that don't depend on this action, continue working on those. IMPORTANT: You *may* "
    'attempt to accomplish this action using other tools that might naturally be used to accomplish this goal, '
    'e.g. using head instead of cat. But you *should not* attempt to work around this denial in malicious ways, '
    'e.g. do not use your ability to run tests to execute non-test actions. You should only try to work around '
    'this restriction in reasonable ways that do not attempt to bypass the intent behind this denial. '
    "If you believe this capability is essential to complete the user's request, first try a safer method. "
    'Get as much of the rest of the task done as you can, then STOP and explain to the user what you were trying '
    'to do and why you need this permission. Let the user decide how to proceed. To allow this type of action '
    'in the future, the user can add a permission rule for Bash to their settings.'
)


def call(host, name, args, *, child=True, call_id=None):
    return dict(type='assistant', uuid=str(uuid.uuid4()), sessionId='root', cwd=str(host.workspace),
        isSidechain=child, version='2.1.296', timestamp=workers.now(),
        **({'agentId': host.child_id} if child else {}),
        message={'role': 'assistant', 'content': [{'type': 'tool_use', 'id': call_id or host.unique(),
                                                 'name': name, 'input': args}]})


def result(record, body, *, error=False):
    item = record['message']['content'][0]
    structured = isinstance(body, dict)
    return {**{k: v for k, v in record.items() if k not in {'uuid', 'message', 'type', 'timestamp'}},
        'type': 'user', 'uuid': str(uuid.uuid4()), 'parentUuid': record['uuid'],
        'sourceToolAssistantUUID': record['uuid'], 'timestamp': workers.now(),
        'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': item['id'],
            'content': [{'type': 'text', 'text': json.dumps(body)}] if structured else body,
            **({'is_error': True} if error else {})}]},
        'toolUseResult': body if structured else 'Error: ' + body}


def fixture(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch, count=1)
    (tmp_path / 'T0.md').write_text('Previously completed work, unchanged by this startup.\n')
    host.launch()
    child_rows, parent_rows = [], []
    claim, _ = workers.startup_commands(host.controller.report(), host.row())
    for _ in range(3):
        event = call(host, 'Bash', {'command': claim})
        child_rows.extend([event, result(event, 'PreToolUse:Bash hook error: '
                                        'Claude child actor needs exact structured native launch proof.', error=True)])
    event = call(host, 'SubagentHandback', {'message': 'Startup could not claim; no work occurred.'})
    child_rows.extend([event, result(event, 'PreToolUse:SubagentHandback hook error: '
                                    'Claude child actor needs exact structured native launch proof.', error=True)])
    host.stop('Ordinary unsuccessful startup final text.')
    first_stop_ids = set(host.controller.report()['unbound_worker_events'])

    def handback(text):
        event = host.event('SubagentHandback', {'message': text})
        native_call = call(host, 'SubagentHandback', event['tool_input'], call_id=event['tool_use_id'])
        flow.hook(event)
        peer = host.peer(text, variant='attachment'); peer['version'] = '2.1.296'
        flow.hook({**event, 'hook_event_name': 'PostToolUse',
                   'tool_response': {'success': True, 'message': 'Report delivered to your caller.'}})
        child_rows.extend([native_call, result(native_call, {'success': True, 'message': 'Report delivered to your caller.'})])
        parent_rows.append(peer)
        return event['tool_use_id']

    first_report = handback('First startup failed before claim.')
    first = host.redirect(); first['version'] = '2.1.296'; parent_rows.append(first)
    resume = call(host, 'SendMessage', {'to': host.child_id, 'message': 'Continue the same startup.'}, child=False)
    parent_rows.extend([resume, result(resume, {'success': True, 'resumedAgentId': host.child_id})])
    denied_event = host.event('Bash', {'command': claim})
    denied_call = call(host, 'Bash', denied_event['tool_input'], call_id=denied_event['tool_use_id'])
    flow.hook(denied_event)
    child_rows.extend([denied_call, result(denied_call, DENIAL, error=True)])
    second_report = handback('Permission denied; still no claim, context, writes or tests.')
    host.stop(None)
    second = host.redirect(); second['version'] = '2.1.296'
    # The resumed native task completion belongs to SendMessage, not Agent.
    second['attachment']['prompt'] = second['attachment']['prompt'].replace(
        host.row()['call_id'], resume['message']['content'][0]['id'])
    parent_rows.append(second)
    with host.child.open('ab') as stream:
        for record in child_rows: stream.write(encoded(record))
    for record in parent_rows: host.append(record)
    db = json.loads(host.controller._path().read_text()); state = db['runs'][host.state['run']]
    row = state['workers'][host.grant]
    # Model the captured legacy controller; no production invocation is consumed.
    for admission in db['admissions'].values():
        if admission.get('call_id') == denied_event['tool_use_id']:
            admission.update(state='expired', completed_at=workers.now())
        if admission.get('call_id') == row['call_id']:
            admission.update(state='completed')
    resume_item = resume['message']['content'][0]
    state.setdefault('worker_controls', {})[resume_item['id']] = {'tool': 'SendMessage', 'grant': host.grant,
        'attempt': row['attempt'], 'binding': row['binding'], 'input_digest': digest(resume_item['input'])}
    observed = native.observe(state['root'], row, {}, source=state['claude_transcript_source'],
                              cursor=state['claude_transcript_cursor'])
    assert observed['status'] == 'conflict' and observed['reason'] == recovery.PEER_CONFLICT
    state['claude_transcript_cursor'] = observed['cursor']
    row.update(state='unknown', identity_conflict=True)
    workers._audit(row, 'identity_observation', {'status': 'conflict', 'reason': recovery.PEER_CONFLICT})
    bind_fixture_cycles(host, state, row, resume_item['id'], first_stop_ids,
                        {row['call_id']: first_report, resume_item['id']: second_report})
    host.controller._write(host.controller._path(), db)
    host.denied_call = denied_event['tool_use_id']
    return host



def bind_fixture_cycles(host, state, row, continuation, first_stop_ids, reports_by_delivery):
    """Fixture adapter supplies explicit links as events are created, never nearest times."""
    records = {}
    for path in (host.parent, host.child):
        offset = 0
        for raw in path.read_bytes().splitlines(keepends=True):
            value = json.loads(raw)
            records[digest(value)] = (value, native._reference(path, path.stat(), offset, raw))
            offset += len(raw)
    cycles, events = [], []
    previous_completion = None
    for index, delivery_call in enumerate((row['call_id'], continuation)):
        report = row['startup_handbacks'][reports_by_delivery[delivery_call]]
        report_call = report['envelope']['call_id']
        report_record, report_ref = next((value, ref) for value, ref in records.values()
            if any(item.get('type') == 'tool_use' and item.get('id') == report_call
                   for item in value.get('message', {}).get('content', []) if isinstance(item, dict)))
        control_record, control_ref = next((value, ref) for value, ref in records.values()
            if any(item.get('type') == 'tool_use' and item.get('id') == delivery_call
                   for item in value.get('message', {}).get('content', []) if isinstance(item, dict)))
        note, note_ref = next((native._notification(value), ref) for value, ref in records.values()
                             if native._notification(value) and native._notification(value)['call_id'] == delivery_call)
        stop_key, stop = next((key, value) for key, value in state['unbound_worker_events'].items()
            if value.get('event') == 'SubagentStop' and (key in first_stop_ids) == (index == 0))
        stop['delivery_call_id'] = delivery_call
        cycle_id = native.delivery_cycle_id(row, delivery_call)
        base = {'cycle_id': cycle_id, 'delivery_call_id': delivery_call, 'binding': row['binding'],
            'attempt_id': row['attempt'], 'native_child_id': row['worker_id'], 'parent': state['root'],
            'host_adapter_version': '2.1.296', 'source_spans': [report_ref]}
        cycle = {'schema': native.DELIVERY_SCHEMA, 'cycle_id': cycle_id, 'binding': row['binding'],
            'attempt_id': row['attempt'], 'native_child_id': row['worker_id'],
            'original_launch_call_id': row['call_id'], 'delivery_call_id': delivery_call,
            'ordinal': index, 'parent_cycle_id': cycles[-1]['cycle_id'] if cycles else None,
            'host_adapter_version': '2.1.296', 'source_spans': [control_ref]}
        group = [
            {**base, 'kind': 'admitted', 'call_id': delivery_call, 'result_call_id': delivery_call,
             'result_child_id': row['worker_id'], 'tool': 'Agent' if not index else 'SendMessage',
             'result_status': 'async_launched' if not index else 'resumed',
             'parent_completion_sha256': digest(previous_completion) if previous_completion else None,
             'source_spans': [control_ref], 'observed_at': control_record['timestamp']},
            {**base, 'kind': 'acknowledged', 'report_call_id': report_call,
             'envelope_digest': digest(report['envelope']), 'response_digest': report['response_digest'],
             'observed_at': report['observed_at']},
            {**base, 'kind': 'handback_observed', 'report_call_id': report_call,
             'body_digest': report['envelope']['body_digest'], 'report': deepcopy(report),
             'observed_at': report['admitted_at']},
            {**base, 'kind': 'stop_observed', 'report_call_id': report_call, 'event_id': stop_key,
             'status': 'stopped', 'observed_at': stop['observed_at']},
            {**base, 'kind': 'completion_observed', 'report_call_id': report_call, 'call_id': delivery_call,
             'stop_event_id': stop_key, 'status': 'completed', 'source_spans': [note_ref],
             'observed_at': note['timestamp']}]
        cycles.append(cycle); events.extend(group); previous_completion = group[-1]
    row['delivery_cycle_observation'] = native.correlate_delivery_cycles(state['root'], row, cycles, events)
    assert row['delivery_cycle_observation']['status'] == 'matched'


def inspected(host):
    db = json.loads(host.controller._path().read_text())
    state = db['runs'][host.state['run']]
    from taskplane.storage import storage_generation
    proposal = recovery.inspect_reconciliation(host.workspace, state, db['admissions'], host.grant, {},
        expected_storage_generation=storage_generation(db))
    return db, state, proposal


def test_b9_exact_readonly_proposal_commits_inline_proof_before_optional_publication(tmp_path, monkeypatch):
    host = fixture(tmp_path, monkeypatch)
    before = host.controller._path().read_bytes()
    artifacts = {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    db, state, proposal = inspected(host)
    assert host.controller._path().read_bytes() == before
    assert artifacts == {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    candidate, result = recovery.revalidate_reconciliation(tmp_path, state, db['admissions'], proposal,
        current_storage_generation=db.get('storage_generation', 0))
    assert candidate['workers'][host.grant]['identity_conflict'] is True
    assert candidate['workers'][host.grant]['terminal_status'] == 'startup_failed'
    assert result['permission_status'] == 'native_permission_required'
    assert result['retry_allowed'] is result['retry_authorized'] is result['accepted'] is result['result_accepted'] is False
    assert {k: v for k, v in state.items() if k != 'workers'} == {k: v for k, v in candidate.items() if k != 'workers'}
    from taskplane.storage import commit_controller
    generation = db.get('storage_generation', 0)
    db['runs'][state['run']] = candidate
    committed = commit_controller(host.controller._path(), db, expected_generation=generation)
    assert committed == generation + 1 and candidate['revision'] == state['revision']
    # Durable inline proof makes replay independent of optional publication.
    replay = recovery.inspect_reconciliation(tmp_path, candidate, db['admissions'], host.grant, {},
                                            expected_storage_generation=committed)
    assert replay['candidate'] == candidate and not replay['artifacts']
    recovery.publish_reconciliation_artifacts(tmp_path, proposal)
    record = candidate['workers'][host.grant]['startup_recovery']
    proof = Store(tmp_path).resolve(record['proof_ref'])
    assert proof['denial']['call_id'] == host.denied_call and proof['denial']['authority'] == 'none'
    replay = recovery.inspect_reconciliation(tmp_path, candidate, db['admissions'], host.grant, {},
                                            expected_storage_generation=committed)
    assert replay['candidate'] == candidate and replay['result'] == result and not replay['artifacts']


@pytest.mark.parametrize('defect', ['claimed', 'context', 'owned_file', 'snapshot', 'running_handle',
    'open_admission', 'consumed_claim', 'foreign_admission', 'late_control', 'late_child', 'extra_peer',
    'missing_stop', 'missing_resume_note', 'source_replaced', 'missing_ack', 'unrelated_conflict',
    'task_stop', 'ambiguous_cycle', 'missing_denial', 'changed_denial', 'runtime', 'missing_report',
    'missing_first_completion', 'changed_first_stop', 'changed_second_stop', 'live_peer', 'changed_inputs'])
def test_b9_each_predicate_refuses_without_writes(tmp_path, monkeypatch, defect):
    host = fixture(tmp_path, monkeypatch)
    target = host.controller._path(); db = json.loads(target.read_text()); state = db['runs'][host.state['run']]
    row = state['workers'][host.grant]
    admission = next(a for a in db['admissions'].values() if a.get('call_id') == host.denied_call)
    if defect == 'claimed': row['claimed_at'] = workers.now()
    elif defect == 'context': row['context_receipt'] = {'fixture': True}
    elif defect == 'owned_file': (tmp_path / 'T0.md').write_text('changed')
    elif defect == 'snapshot': Store(tmp_path).path(row['snapshot']['sha256']).write_text('{}')
    elif defect == 'running_handle': state['observed_handles'] = {'live': {'state': 'running'}}
    elif defect == 'open_admission': admission['state'] = 'pending'
    elif defect == 'consumed_claim': admission['invocation']['state'] = 'consumed'
    elif defect == 'foreign_admission': admission['principal'] = 'other'
    elif defect in {'late_control', 'task_stop'}:
        name = 'SendMessage' if defect == 'late_control' else 'TaskStop'
        args = {'to': host.child_id, 'message': 'Late continuation'} if name == 'SendMessage' else {'task_id': host.child_id}
        event = call(host, name, args, child=False)
        host.append(event); host.append(result(event, {'success': True, 'resumedAgentId': host.child_id}))
    elif defect == 'late_child':
        event = call(host, 'Write', {'file_path': 'T0.md', 'content': 'changed'})
        with host.child.open('ab') as stream: stream.write(encoded(event) + encoded(result(event, {'success': True})))
    elif defect == 'extra_peer':
        peer = host.peer('Third startup report.'); peer['version'] = '2.1.296'; host.append(peer)
    elif defect == 'missing_stop':
        del state['unbound_worker_events'][next(k for k, e in state['unbound_worker_events'].items() if e['event'] == 'SubagentStop')]
    elif defect in {'missing_resume_note', 'missing_first_completion'}:
        records = [json.loads(line) for line in host.parent.read_text().splitlines()]
        records = [r for r in records if not (native._notification(r) and
            (native._notification(r)['call_id'] == row['call_id']) == (defect == 'missing_first_completion'))]
        host.parent.write_bytes(b''.join(encoded(r) for r in records))
    elif defect == 'source_replaced':
        data = host.parent.read_bytes(); host.parent.rename(host.parent.with_suffix('.old')); host.parent.write_bytes(data)
    elif defect == 'missing_ack': next(iter(row['startup_handbacks'].values())).pop('acknowledgment')
    elif defect == 'unrelated_conflict':
        row['reconciliation_events']['foreign'] = {'kind': 'identity_observation', 'value': {'status': 'conflict', 'reason': 'Other conflict'}}
    elif defect == 'ambiguous_cycle': row.pop('delivery_cycle_observation')
    elif defect == 'missing_denial':
        db['admissions'] = {k: a for k, a in db['admissions'].items() if a.get('call_id') != host.denied_call}
    elif defect == 'changed_denial': host.child.write_bytes(host.child.read_bytes().replace(b'Auto-Mode Bypass', b'Other-Denial---'))
    elif defect == 'runtime': row['expected_runtime'] = {'root': '/foreign'}
    elif defect == 'missing_report': row['startup_handbacks'].pop(next(iter(row['startup_handbacks'])))
    elif defect in {'changed_first_stop', 'changed_second_stop'}:
        stops = [v for v in state['unbound_worker_events'].values() if v['event'] == 'SubagentStop']
        stops[0 if defect == 'changed_first_stop' else 1]['delivery_call_id'] = 'foreign'
    elif defect == 'live_peer': state['workers']['live-peer'] = {'state': 'running'}
    elif defect == 'changed_inputs': row['input_manifest'] = {'foreign': '0' * 64}
    before = target.read_bytes()
    artifacts = {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    with pytest.raises(w.Refusal):
        recovery.inspect_reconciliation(tmp_path, state, db['admissions'], host.grant, {},
                                        expected_storage_generation=db.get('storage_generation', 0))
    assert target.read_bytes() == before
    assert artifacts == {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}


@pytest.mark.parametrize('changed', ['parent', 'child', 'owned_file', 'generation', 'admissions', 'binding'])
def test_b9_revalidation_refuses_changes_between_inspect_and_commit(tmp_path, monkeypatch, changed):
    host = fixture(tmp_path, monkeypatch)
    db, state, proposal = inspected(host)
    generation = db.get('storage_generation', 0)
    if changed in {'parent', 'child'}:
        with (host.parent if changed == 'parent' else host.child).open('ab') as stream:
            stream.write(encoded({'type': 'user', 'message': {'role': 'user', 'content': 'Later activity'}}))
    elif changed == 'owned_file': (tmp_path / 'T0.md').write_text('changed')
    elif changed == 'generation': generation += 1
    elif changed == 'admissions': db['admissions']['new'] = {'state': 'pending'}
    elif changed == 'binding': state['workers'][host.grant]['binding']['revision'] += 1
    before = host.controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        recovery.revalidate_reconciliation(tmp_path, state, db['admissions'], proposal,
                                           current_storage_generation=generation)
    assert host.controller._path().read_bytes() == before
