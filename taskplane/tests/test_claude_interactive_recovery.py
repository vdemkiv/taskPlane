"""Production-shaped interactive hooks and transcript replay, not live host certification."""
from copy import deepcopy
import json
from pathlib import Path
import shlex

import pytest

from taskplane import claude_worker_invocation as invocation
from taskplane import claude_worker_observations as observations
from taskplane import flow, workflow as w, workflow_host as h, worker_runtime as workers
from taskplane.tests.test_claude_worker_lifecycle import encoded, notification_record, attachment_notification_record
from taskplane.tests.test_worker_runtime import setup, reserve

pytestmark = pytest.mark.skipif(not observations.supported_reader(), reason='Requires native no-follow reads')


def attachment_peer_record(peer):
    """Mid-turn system attachment observed in native Claude 2.1.289."""
    reminder = '42c0fdbd542aa236'
    prompt = f'<agent-message from="{peer["origin"]["from"]}">\n' + peer['origin']['body'] + '\n</agent-message>'
    content = (f'<system-reminder id="{reminder}">\n'
        + 'Another Claude session sent a message while you were working:\n' + prompt + '\n\n'
        + observations.HANDBACK_FOOTER
        + ' After completing your current task, decide whether/how to respond '
        + '(reply via SendMessage to the `from=` address).\n'
        + f'</system-reminder id="{reminder}">')
    return dict(type='attachment', sessionId=peer['sessionId'], session_id=peer['sessionId'],
        cwd=peer['cwd'], isSidechain=peer['isSidechain'], timestamp=peer['timestamp'], renderedRole='system',
        attachment=dict(type='queued_command', commandMode='prompt', isMeta=peer['isMeta'],
            source_uuid='d35fde74-36f8-4458-9eb0-e00c476c6168',
            delivery_id='ded47894-c1d7-4f37-b707-db530629b93b',
            reminderId=reminder, timestamp=peer['timestamp'], origin=peer['origin'], prompt=prompt),
        rendered=[{'content': content}])


class Interactive:
    def __init__(self, tmp_path, monkeypatch, *, count=4, scoped=False):
        self.workspace = tmp_path
        self.controller, self.state = setup(tmp_path, count=count)
        self.slots = count + 1
        if scoped:
            tasks = deepcopy(self.state['initial_context_tasks'])
            for task in tasks:
                task.update(execution='native_required', read_inputs=['input.py'],
                    purpose='Useful independent fixture review', context_budget_bytes=131072)
            (tmp_path / '.taskplane/scoped-tasks.json').write_text(json.dumps({'tasks': tasks}))
            self.state = self.controller.update_tasks(self.state['run'], self.state['revision'], '.taskplane/scoped-tasks.json')
        self.controller.adapter.name = 'claude'
        home = tmp_path.parent / (tmp_path.name + '-native-home')
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
        monkeypatch.setenv('CLAUDE_SESSION_ID', 'root')
        monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
        monkeypatch.setattr(invocation, '_boot_identity', lambda: 'fixture-boot')
        self.parent = home / '.claude/projects/interactive/root.jsonl'
        self.parent.parent.mkdir(parents=True)
        self.parent.write_bytes(encoded(dict(sessionId='root', cwd=str(tmp_path), type='user')))
        self.sequence = 0
        self.root_hook()
        flow.append(tmp_path, dict(kind='start', run=self.state['run'], session='root', phase='product', host='claude'))

    def unique(self):
        self.sequence += 1
        return 'interactive-' + str(self.sequence)

    def root_hook(self):
        event = dict(hook_event_name='PreToolUse', session_id='root', cwd=str(self.workspace),
            transcript_path=str(self.parent), tool_use_id=self.unique(), tool_name='Read',
            tool_input={'file_path': 'input.py'})
        flow.hook(event, governor=self.controller)
        flow.hook({**event, 'hook_event_name': 'PostToolUse'}, governor=self.controller)

    def launch(self, task='T0', **extra):
        item = reserve(self.controller, self.state, task=task, slots=self.slots, **extra)
        self.grant = item['grant']['grant_id']
        self.child_id = 'child-' + self.unique()
        event = dict(hook_event_name='PreToolUse', session_id='root', cwd=str(self.workspace),
            transcript_path=str(self.parent), tool_use_id=self.unique(), tool_name='Agent',
            tool_input={'prompt': item['message'], 'description': 'interactive recovery', 'run_in_background': True,
                        'subagent_type': 'taskplane:tp-lens'})
        call = dict(type='assistant', sessionId='root', cwd=str(self.workspace), timestamp=workers.now(),
            message={'content': [dict(type='tool_use', id=event['tool_use_id'], name='Agent', input=event['tool_input'])]})
        self.append(call)
        flow.hook(event, governor=self.controller)
        self.child = self.parent.with_suffix('') / 'subagents' / ('agent-' + self.child_id + '.jsonl')
        self.child.parent.mkdir(parents=True, exist_ok=True)
        self.child.write_bytes(encoded(dict(sessionId='root', agentId=self.child_id, isSidechain=True,
            cwd=str(self.workspace), timestamp=workers.now())))
        self.result = dict(type='user', sessionId='root', cwd=str(self.workspace), timestamp=workers.now(),
            message={'content': [dict(type='tool_result', tool_use_id=event['tool_use_id'], content='native launch')]},
            toolUseResult=dict(isAsync=True, status='async_launched', agentId=self.child_id, outputFile='/fixture/' + self.child_id))
        self.append(self.result)
        self.controller.observe(dict(hook_event_name='SubagentStart', host='claude', session_id='root',
            agent_id=self.child_id), self.state['run'])
        return item

    def row(self):
        return self.controller.report()['workers'][self.grant]

    def append(self, record):
        with self.parent.open('ab') as stream:
            stream.write(encoded(record))

    def event(self, tool, args):
        return dict(hook_event_name='PreToolUse', session_id='root', agent_id=self.child_id,
            cwd=str(self.workspace), transcript_path=str(self.parent), tool_use_id=self.unique(),
            tool_name=tool, tool_input=args)

    def invoke(self, command):
        event = self.event('Bash', {'command': command})
        answer = flow.hook(event)
        updated = answer['hookSpecificOutput']['updatedInput']
        result = h.Controller.invoke_claude(shlex.split(updated['command'])[1:])
        flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_input': updated, 'tool_response': result})
        return result

    def consume(self):
        claim, context = workers.startup_commands(self.controller.report(), self.row())
        self.invoke(claim)
        descriptor = self.invoke(context)
        result = self.invoke(context + ' --drain ' + descriptor['handoff_ref']['sha256'])
        while result['remaining_required']:
            runtime = shlex.join(invocation._runtime(None, None))
            result = self.invoke(runtime + ' ' + result['next_action'])
        assert result['done']

    def stop(self, body, **extra):
        event = dict(hook_event_name='SubagentStop', session_id='root', agent_id=self.child_id,
            cwd=str(self.workspace), transcript_path=str(self.parent), agent_transcript_path=str(self.child),
            last_assistant_message=body, stop_hook_active=False, event_id=self.unique())
        if body is None:
            event.pop('last_assistant_message')
            event['agent_type'] = 'taskplane:tp-lens'
        flow.hook({**event, **extra}, governor=self.controller)

    def notify(self, body, **extra):
        note = notification_record(self.row(), self.result, body)
        note['sessionId'] = 'root'
        self.append({**note, **extra})
        self.root_hook()

    def accept(self):
        (self.workspace / 'T0.md').write_text('Fixture verified result')
        return self.controller.worker(self.state['run'], 'accept-result', revision=self.state['revision'],
            task='T0', grant=self.grant, request={'outputs': ['T0.md'],
                'checks': [{'name': 'Fixture verification', 'status': 'pass', 'evidence': 'T0.md'}]})

    def ready(self):
        self.consume()
        event = self.event('Read', {'file_path': 'input.py'})
        flow.hook(event)
        flow.hook({**event, 'hook_event_name': 'PostToolUse'})
        assert workers.readiness(self.row())['status'] == 'ready'

    def peer(self, body, variant='user'):
        frame = observations.HANDBACK_HEADER + '\n' + '\n'.join('  ' + line for line in body.split('\n'))
        peer = dict(type='user', sessionId='root', cwd=str(self.workspace), isSidechain=False, isMeta=True,
            timestamp=workers.now(), promptSource='system', turnOrigin='peer',
            origin={'kind': 'peer', 'from': self.child_id, 'senderTaskId': self.child_id,
                    'name': 'taskplane:tp-lens', 'handback': True, 'body': frame},
            message={'role': 'user', 'content': 'Another Claude session sent a message:\n'
                + f'<agent-message from="{self.child_id}">\n' + frame + '\n</agent-message>\n\n'
                + observations.HANDBACK_FOOTER})
        return attachment_peer_record(peer) if variant == 'attachment' else peer

    def redirect(self):
        note = notification_record(self.row(), self.result, observations.handback_redirect(self.child_id))
        note['sessionId'] = 'root'
        return attachment_notification_record(note)

    def deliver(self, body, *, terminal=True, variant='user'):
        event = self.event('SubagentHandback', {'message': body})
        flow.hook(event)
        peer = self.peer(body, variant)
        self.append(peer)
        flow.hook({**event, 'hook_event_name': 'PostToolUse',
                   'tool_response': {'message': 'Report delivered to your caller.', 'success': True}})
        self.stop(None)
        if terminal:
            self.append(self.redirect())
            self.root_hook()
        return event, peer


@pytest.mark.parametrize('wrapper', [' 2>&1 | head -50', ' | head -80', ' && true', ' > /tmp/output'])
def test_wrapped_startup_denial_has_exact_recovery_and_valid_retry(tmp_path, monkeypatch, wrapper):
    host = Interactive(tmp_path, monkeypatch)
    item = host.launch()
    claim, context = workers.startup_commands(host.controller.report(), host.row())
    assert 'without pipes' in item['message']
    for command in (claim, context):
        with pytest.raises(w.Refusal) as failure:
            flow.hook(host.event('Bash', {'command': command + wrapper}))
        assert claim in str(failure.value) and context in str(failure.value)
        assert 'without pipes' in str(failure.value)
    assert not host.row().get('claimed_at') and not host.row()['context_receipt']
    host.consume()
    assert host.row()['state'] == 'running'
    assert host.row()['context_receipt']


@pytest.mark.parametrize('notification', ['no_report', 'failure_report'])
def test_startup_failure_handback_is_bounded_failure_and_fresh_retry_works(tmp_path, monkeypatch, notification):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    failed_grant = host.grant
    for index in range(4):
        event = host.event('SubagentHandback', {'message': f'Unable to start; retry {index}.'})
        flow.hook(event)
        flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_response': {'status': 'unknown'}})
        host.stop(f'Interim attempted report {index}.')
        assert host.row()['state'] == 'bootstrapping'
        assert not host.row().get('completion_conflict')
        assert workers.readiness(host.row())['status'] == 'pending'
    assert len(host.row()['startup_handbacks']) == 4
    assert not host.row().get('claimed_at') and not host.row()['context_receipt']
    with pytest.raises(w.Refusal):
        flow.hook(host.event('Write', {'file_path': 'T0.md', 'content': 'forbidden'}))
    body = observations.NO_REPORT_RESULT if notification == 'no_report' else 'Startup failure report delivered.'
    if notification != 'no_report':
        host.stop(body)
    host.notify(body)
    failed = host.row()
    assert failed['state'] == 'failed' and failed['terminal_status'] == 'failed'
    assert failed['handback']['status'] == ('no_report' if notification == 'no_report' else 'startup_failed')
    assert not failed.get('completion_conflict')
    with pytest.raises(w.Refusal, match='not joined'):
        host.accept()
    host.launch(retry_reason='Observed startup failure after invalid wrapped claim; use exact commands.')
    assert host.grant != failed_grant and host.row()['attempt'] == 2
    host.consume()
    event = host.event('Read', {'file_path': 'input.py'})
    flow.hook(event)
    flow.hook({**event, 'hook_event_name': 'PostToolUse'})
    assert workers.readiness(host.row())['status'] == 'ready'
    host.stop('Interim stop; preparing final report.')
    host.stop('Verified fresh result.')
    host.notify('Verified fresh result.')
    assert host.row()['state'] == 'result_pending'
    host.accept()
    assert host.row()['state'] == 'accepted'
    assert host.controller.report()['workers'][failed_grant]['state'] == 'failed'
    host.launch(task='T1')


@pytest.mark.parametrize('defect', ['extra_target', 'empty', 'oversize', 'wrong_actor', 'wrong_runtime',
    'stale_binding', 'revoked', 'identity_conflict', 'not_fresh', 'nonautomatic', 'missing_call', 'changed_body'])
def test_startup_failure_admission_retains_identity_schema_and_binding_boundaries(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    state = host.controller.report()
    row = state['workers'][host.grant]
    event = host.event('SubagentHandback', {'message': 'Cannot start.'})
    event.update(taskplane_automatic_hook=True, taskplane_observed_binding={'root': 'root', 'principal': host.child_id},
        taskplane_runtime_identity=deepcopy(row['expected_runtime']))
    if defect == 'extra_target': event['tool_input']['target'] = 'root'
    if defect == 'empty': event['tool_input']['message'] = ''
    if defect == 'oversize': event['tool_input']['message'] = 'é' * 16385
    if defect == 'wrong_actor': event['taskplane_observed_binding']['principal'] = 'foreign'
    if defect == 'wrong_runtime': event['taskplane_runtime_identity']['root'] = '/foreign'
    if defect == 'stale_binding': row['binding']['revision'] = -1
    if defect == 'revoked': row['revoked_at'] = workers.now()
    if defect == 'identity_conflict': row['identity_conflict'] = True
    if defect == 'not_fresh': row['identity_freshness']['status'] = 'not_yet_available'
    if defect == 'nonautomatic': event['taskplane_automatic_hook'] = False
    if defect == 'missing_call': event.pop('tool_use_id')
    if defect == 'changed_body':
        workers.admit_handback(state, row, event)
        event['tool_input']['message'] = 'Changed body for the same call.'
    with pytest.raises(w.Refusal):
        workers.admit_handback(state, row, event)


def test_stop_history_and_failure_handback_attempts_have_bounds(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    for index in range(40):
        host.stop('Interim stop ' + str(index))
    row = host.row()
    assert len(row['stop_observations']) == 32 and not row.get('completion_conflict')
    assert row['stop_observations'][0]['body_digest'] == workers.digest('Interim stop 8')
    for index in range(8):
        flow.hook(host.event('SubagentHandback', {'message': 'Failure ' + str(index)}))
    with pytest.raises(w.Refusal, match='bound reached'):
        flow.hook(host.event('SubagentHandback', {'message': 'Ninth failure'}))
    host.notify(observations.NO_REPORT_RESULT)
    assert host.row()['state'] == 'failed'


@pytest.mark.parametrize('ready', [False, True])
@pytest.mark.parametrize('variant', ['user', 'attachment'])
def test_native_peer_handback_textless_stop_and_redirect_complete_exact_attempt(tmp_path, monkeypatch, ready, variant):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    if ready:
        host.ready()
    body = 'Verified useful report.\n<&> literal entities &amp; and "quotes"\n\nFinal line.\n'
    event, peer = host.deliver(body, variant=variant)
    row = host.row()
    assert row['state'] == ('result_pending' if ready else 'failed'), row
    assert row['terminal_status'] == ('completed' if ready else 'failed')
    assert row['handback']['status'] == ('delivered' if ready else 'startup_failed')
    assert row['handback']['body_digest'] == workers.digest(body)
    assert row['handback']['peer_handback']['record_sha256'] == workers.digest(peer)
    assert row['handback']['admission']['envelope']['call_id'] == event['tool_use_id']
    assert row['stop_observation']['textless'] is True
    assert 'notification_body_digest' not in row['stop_observation']
    assert body not in json.dumps(row)
    if ready:
        # Repeated exact automatic post delivery is idempotent after completion.
        flow.hook({**event, 'hook_event_name': 'PostToolUse',
                   'tool_response': {'message': 'Report delivered to your caller.', 'success': True}})
        host.accept()
        assert host.row()['state'] == 'accepted'
        host.launch(task='T1')
    else:
        with pytest.raises(w.Refusal, match='not joined'):
            host.accept()
        host.launch(retry_reason='Verified textless stop and native startup diagnostic; retry with full startup.')
        host.ready()
        host.deliver('Fresh ready worker report.', variant=variant)
        host.accept()
        assert host.row()['state'] == 'accepted'


@pytest.mark.parametrize('defect', ['missing_peer', 'foreign_peer', 'sender', 'name', 'kind', 'handback',
    'origin_extra', 'meta', 'sidechain', 'session', 'workspace', 'prompt_source', 'turn_origin', 'user_text',
    'queue_row', 'frame_header', 'indentation', 'wrapper', 'footer', 'wrong_body', 'old_timestamp',
    'future_timestamp', 'naive_timestamp', 'missing_stop', 'active_stop', 'stop_type', 'stop_path',
    'stop_child', 'missing_ack', 'success_only', 'ack_text', 'ack_extra', 'ack_error', 'ack_false',
    'ack_number', 'redirect_child', 'redirect_newline', 'notification_call', 'notification_queue'])
def test_peer_redirect_requires_every_native_proof_part(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    body = 'Deliberate startup failure before claim/context; no review or files produced.'
    event = host.event('SubagentHandback', {'message': body})
    flow.hook(event)
    peer = host.peer('Different report' if defect == 'wrong_body' else body)
    if defect == 'foreign_peer':
        peer['origin']['from'] = peer['origin']['senderTaskId'] = 'foreign-child'
        peer['message']['content'] = peer['message']['content'].replace(host.child_id, 'foreign-child')
    if defect == 'sender': peer['origin']['senderTaskId'] = 'foreign-child'
    if defect == 'name': peer['origin']['name'] = 'foreign-type'
    if defect == 'kind': peer['origin']['kind'] = 'user'
    if defect == 'handback': peer['origin']['handback'] = False
    if defect == 'origin_extra': peer['origin']['trusted'] = True
    if defect == 'meta': peer['isMeta'] = False
    if defect == 'sidechain': peer['isSidechain'] = True
    if defect == 'session': peer['sessionId'] = 'foreign-parent'
    if defect == 'workspace': peer['cwd'] += '-foreign'
    if defect == 'prompt_source': peer['promptSource'] = 'user'
    if defect == 'turn_origin': peer['turnOrigin'] = 'user'
    if defect == 'user_text': peer.pop('origin')
    if defect == 'queue_row': peer['type'] = 'queue-operation'
    if defect == 'frame_header':
        peer['origin']['body'] = peer['origin']['body'].replace('[Subagent hand-back]', '[Agent report]')
        peer['message']['content'] = peer['message']['content'].replace('[Subagent hand-back]', '[Agent report]')
    if defect == 'indentation':
        peer['origin']['body'] = peer['origin']['body'].replace('\n  ', '\n')
        peer['message']['content'] = peer['message']['content'].replace('\n  ', '\n')
    if defect == 'wrapper': peer['message']['content'] = peer['origin']['body']
    if defect == 'footer': peer['message']['content'] += '\n'
    if defect == 'old_timestamp': peer['timestamp'] = '2000-01-01T00:00:00+00:00'
    if defect == 'future_timestamp': peer['timestamp'] = '2999-01-01T00:00:00+00:00'
    if defect == 'naive_timestamp': peer['timestamp'] = '2026-10-05T12:04:11'
    # The automatic post hook is independent of parent transcript flushing.
    response = {'message': 'Report delivered to your caller.', 'success': True}
    if defect == 'success_only': response.pop('message')
    if defect == 'ack_text': response['message'] += ' Changed.'
    if defect == 'ack_extra': response['status'] = 'completed'
    if defect == 'ack_false': response['success'] = False
    if defect == 'ack_number': response['success'] = 1
    if defect != 'missing_ack':
        flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_response': response,
                   'is_error': defect == 'ack_error'})
    if defect != 'missing_stop':
        host.stop(None, **({'stop_hook_active': True} if defect == 'active_stop' else
            {'agent_type': 'foreign-type'} if defect == 'stop_type' else
            {'agent_transcript_path': str(host.child) + '-foreign'} if defect == 'stop_path' else
            {'agent_id': 'foreign-child'} if defect == 'stop_child' else {}))
    if defect != 'missing_peer': host.append(peer)
    note = host.redirect()
    if defect == 'redirect_child':
        note['attachment']['prompt'] = note['attachment']['prompt'].replace(
            f'message from "{host.child_id}"', 'message from "foreign-child"')
    if defect == 'redirect_newline':
        note['attachment']['prompt'] = note['attachment']['prompt'].replace('here.\n</result>', 'here.</result>')
    if defect == 'notification_call':
        note['attachment']['prompt'] = note['attachment']['prompt'].replace(
            '<tool-use-id>' + host.row()['call_id'], '<tool-use-id>foreign-call')
    if defect == 'notification_queue': note['type'] = 'queue-operation'
    host.append(note)
    host.root_hook()
    row = host.row()
    assert not row.get('terminal_status'), defect
    assert row.get('handback', {}).get('status') not in {'delivered', 'startup_failed'}, defect
    with pytest.raises(w.Refusal): host.accept()


@pytest.mark.parametrize(('path', 'value'), [
    (('type',), 'queue-operation'), (('type',), 'user'), (('type',), 'system'),
    (('isSidechain',), True), (('isSidechain',), None),
    (('renderedRole',), 'user'), (('session_id',), 'foreign-parent'),
    (('session_id',), None), (('sessionId',), 'foreign-parent'), (('sessionId',), None),
    (('attachment',), None), (('attachment', 'type'), 'hook_additional_context'),
    (('attachment', 'commandMode'), 'task-notification'), (('attachment', 'isMeta'), False),
    (('attachment', 'isMeta'), 1), (('attachment', 'timestamp'), '2000-01-01T00:00:00Z'),
    (('attachment', 'source_uuid'), 'invalid'), (('attachment', 'source_uuid'), None),
    (('attachment', 'delivery_id'), 'invalid'), (('attachment', 'delivery_id'), None),
    (('attachment', 'reminderId'), '42c0fdbd542aa237'), (('attachment', 'reminderId'), None),
    (('attachment', 'reminderId'), '42c0fdbd542aa23z'),
    (('attachment', 'unknown_metadata'), True), (('attachment', 'prompt'), 'ordinary text'),
    (('attachment', 'origin'), None), (('attachment', 'origin', 'kind'), 'user'),
    (('attachment', 'origin', 'handback'), False), (('attachment', 'origin', 'handback'), 1),
    (('attachment', 'origin', 'from'), 'foreign-child'),
    (('attachment', 'origin', 'senderTaskId'), 'foreign-child'),
    (('attachment', 'origin', 'body'), '  Unframed report'),
    (('attachment', 'origin', 'extra'), True),
    (('rendered',), None), (('rendered',), []), (('rendered',), [{'content': 'ordinary text'}]),
])
def test_mid_turn_peer_attachment_requires_exact_native_envelope(path, value):
    body = 'Useful report.'
    peer = attachment_peer_record(dict(sessionId='root', cwd='/fixture', isSidechain=False, isMeta=True,
        timestamp='2026-10-05T12:28:04.256Z', origin=dict(kind='peer', handback=True,
            senderTaskId='child', **{'from': 'child'}, name='taskplane:tp-lens',
            body=observations.HANDBACK_HEADER + '\n  ' + body)))
    assert observations._peer_handback(peer)['input_digest'] == workers.digest({'message': body})
    target = peer
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    assert observations._peer_handback(peer) is None


@pytest.mark.parametrize('defect', ['prefix', 'footer', 'closing_reminder', 'reply_instruction',
    'extra_rendered', 'extra_content_metadata', 'prompt_sender', 'prompt_frame', 'rendered_frame',
    'unindented_body', 'missing_header', 'empty_body', 'oversized_body'])
def test_mid_turn_peer_attachment_rejects_malformed_report_and_rendering(defect):
    body = '' if defect == 'empty_body' else 'x' * 32769 if defect == 'oversized_body' else 'Useful report.'
    frame = observations.HANDBACK_HEADER + '\n  ' + body
    if defect == 'unindented_body': frame = frame.replace('\n  ', '\n')
    if defect == 'missing_header': frame = frame.replace('[Subagent hand-back]', '[Agent report]')
    peer = attachment_peer_record(dict(sessionId='root', cwd='/fixture', isSidechain=False, isMeta=True,
        timestamp='2026-10-05T12:28:04.256Z', origin=dict(kind='peer', handback=True,
            senderTaskId='child', **{'from': 'child'}, name='taskplane:tp-lens', body=frame)))
    rendered = peer['rendered'][0]
    if defect == 'prefix': rendered['content'] = rendered['content'].replace(' while you were working', '')
    if defect == 'footer': rendered['content'] = rendered['content'].replace(observations.HANDBACK_FOOTER, '')
    if defect == 'closing_reminder': rendered['content'] = rendered['content'].replace(
        '</system-reminder id="42c0fdbd542aa236">', '</system-reminder>')
    if defect == 'reply_instruction': rendered['content'] = rendered['content'].replace('After completing', 'Before completing')
    if defect == 'extra_rendered': peer['rendered'].append({'content': 'ordinary user text'})
    if defect == 'extra_content_metadata': rendered['type'] = 'text'
    if defect == 'prompt_sender': peer['attachment']['prompt'] = peer['attachment']['prompt'].replace('from="child"', 'from="other"')
    if defect == 'prompt_frame': peer['attachment']['prompt'] += '\n'
    if defect == 'rendered_frame': rendered['content'] = rendered['content'].replace('Useful report.', 'Forged report.')
    assert observations._peer_handback(peer) is None


@pytest.mark.parametrize('defect', ['wrong_body', 'missing_ack', 'missing_stop', 'foreign_peer',
    'foreign_parent', 'wrong_name', 'old_timestamp', 'future_timestamp', 'naive_timestamp',
    'no_redirect', 'wrong_call', 'queue_row', 'ordinary_user'])
def test_mid_turn_delivery_preserves_bound_handback_and_completion_requirements(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    body = 'Bounded startup diagnostic.'
    event = host.event('SubagentHandback', {'message': body})
    flow.hook(event)
    peer = host.peer('Changed report.' if defect == 'wrong_body' else body)
    if defect == 'foreign_peer': peer['origin']['from'] = peer['origin']['senderTaskId'] = 'foreign-child'
    if defect == 'foreign_parent': peer['sessionId'] = 'foreign-parent'
    if defect == 'wrong_name': peer['origin']['name'] = 'foreign-type'
    if defect == 'old_timestamp': peer['timestamp'] = '2000-01-01T00:00:00Z'
    if defect == 'future_timestamp': peer['timestamp'] = '2999-01-01T00:00:00Z'
    if defect == 'naive_timestamp': peer['timestamp'] = '2026-10-05T12:28:04'
    peer = attachment_peer_record(peer)
    if defect == 'queue_row': peer['type'] = 'queue-operation'
    if defect == 'ordinary_user':
        peer['type'] = 'user'
        peer['message'] = {'role': 'user', 'content': peer['rendered'][0]['content']}
    host.append(peer)
    if defect != 'missing_ack':
        flow.hook({**event, 'hook_event_name': 'PostToolUse',
                   'tool_response': {'message': 'Report delivered to your caller.', 'success': True}})
    if defect != 'missing_stop': host.stop(None)
    if defect != 'no_redirect':
        note = host.redirect()
        if defect == 'wrong_call': note['attachment']['prompt'] = note['attachment']['prompt'].replace(
            '<tool-use-id>' + host.row()['call_id'], '<tool-use-id>foreign-call')
        host.append(note)
    host.root_hook()
    assert not host.row().get('terminal_status')
    assert host.row().get('handback', {}).get('status') not in {'delivered', 'startup_failed'}
    with pytest.raises(w.Refusal): host.accept()


@pytest.mark.parametrize('defect', ['runtime', 'automatic', 'principal', 'root', 'binding', 'call', 'input',
    'admission_runtime', 'admission_automatic', 'admission_run', 'revoked', 'replay_response'])
def test_handback_acknowledgment_keeps_exact_automatic_admission(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    event = host.event('SubagentHandback', {'message': 'Startup diagnostic.'})
    flow.hook(event)
    state = host.controller.report()
    row = state['workers'][host.grant]
    report = row['startup_handbacks'][event['tool_use_id']]
    admission = dict(tool='SubagentHandback', principal=host.child_id, root='root', run=state['run'],
        workspace=str(tmp_path), binding=deepcopy(row['binding']), call_id=event['tool_use_id'],
        runtime=deepcopy(row['expected_runtime']), host='claude', automatic=True,
        input_digest=report['envelope']['input_digest'])
    event.update(hook_event_name='PostToolUse', taskplane_automatic_hook=True,
        taskplane_runtime_identity=deepcopy(row['expected_runtime']),
        taskplane_observed_binding={'root': 'root', 'principal': host.child_id},
        tool_response={'message': 'Report delivered to your caller.', 'success': True})
    if defect == 'runtime': event['taskplane_runtime_identity']['root'] = '/foreign'
    if defect == 'automatic': event['taskplane_automatic_hook'] = False
    if defect == 'principal': event['taskplane_observed_binding']['principal'] = 'foreign'
    if defect == 'root': event['taskplane_observed_binding']['root'] = 'foreign'
    if defect == 'binding': admission['binding']['revision'] = -1
    if defect == 'call': event['tool_use_id'] = 'foreign'
    if defect == 'input': event['tool_input']['message'] += ' Changed.'
    if defect == 'admission_runtime': admission['runtime']['root'] = '/foreign'
    if defect == 'admission_automatic': admission['automatic'] = False
    if defect == 'admission_run': admission['run'] = 'foreign'
    if defect == 'revoked': row['revoked_at'] = workers.now()
    if defect == 'replay_response':
        workers.observe_handback(state, admission, event)
        event['tool_response'] = {'success': True}
    with pytest.raises(w.Refusal): workers.observe_handback(state, admission, event)


@pytest.mark.parametrize('defect', ['peer_span', 'source_replaced', 'source_missing', 'source_truncated',
    'competing_peer', 'peer_projection', 'ack_runtime', 'ack_binding', 'ack_call', 'ack_removed', 'stop_runtime'])
@pytest.mark.parametrize('variant', ['user', 'attachment'])
def test_retained_peer_delivery_is_revalidated_before_acceptance(tmp_path, monkeypatch, defect, variant):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    event, peer = host.deliver('Verified useful report.', variant=variant)
    assert host.row()['state'] == 'result_pending'
    if defect == 'peer_span':
        raw = host.parent.read_bytes()
        host.parent.write_bytes(raw.replace(b'Verified useful report.', b'Verified forged report.'))
    if defect == 'source_replaced':
        old = host.parent.with_suffix('.old'); host.parent.rename(old); host.parent.write_bytes(old.read_bytes())
    if defect == 'source_missing': host.parent.unlink()
    if defect == 'source_truncated': host.parent.write_bytes(encoded({'sessionId': 'root'}))
    if defect == 'competing_peer':
        peer['timestamp'] = workers.now()
        if variant == 'attachment': peer['attachment']['timestamp'] = peer['timestamp']
        host.append(peer)
    if defect in {'peer_projection', 'ack_runtime', 'ack_binding', 'ack_call', 'ack_removed', 'stop_runtime'}:
        state = host.controller.report()
        row = state['workers'][host.grant]
        if defect == 'peer_projection':
            entry = next(iter(state['claude_transcript_cursor']['entries'].values()))
            entry['peer_handback'][0]['projection']['body_digest'] = workers.digest('Forged report')
            state['claude_transcript_cursor'] = observations._sealed(state['claude_transcript_cursor'])
        if defect == 'ack_runtime': row['handback_call']['acknowledgment']['runtime']['root'] = '/foreign'
        if defect == 'ack_binding': row['handback_call']['envelope']['binding']['revision'] = -1
        if defect == 'ack_call': row['handback_call']['envelope']['call_id'] = 'foreign'
        if defect == 'ack_removed': row['handback_call'].pop('acknowledgment')
        if defect == 'stop_runtime':
            for stop in state['unbound_worker_events'].values():
                if stop.get('textless'): stop['runtime']['root'] = '/foreign'
            row['stop_observations'][0]['runtime']['root'] = '/foreign'
        (tmp_path / 'T0.md').write_text('Fixture verified result')
        with pytest.raises(w.Refusal):
            workers.accept_result(tmp_path, state, 'T0', {'grant': host.grant, 'outputs': ['T0.md'],
                'checks': [{'name': 'Fixture verification', 'status': 'pass', 'evidence': 'T0.md'}]})
    else:
        with pytest.raises(w.Refusal): host.accept()


@pytest.mark.parametrize('variant', ['user', 'attachment'])
def test_peer_completion_waits_for_bounded_parent_scan_and_retains_full_proof(tmp_path, monkeypatch, variant):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.deliver('Bounded peer report.', terminal=False, variant=variant)
    with host.parent.open('ab') as stream:
        stream.write(encoded({'type': 'progress', 'data': 'x' * 8192}) * 550)
    host.append(host.redirect())
    state = host.controller.report()
    row = state['workers'][host.grant]
    first = observations.observe('root', row, {}, source=state['claude_transcript_source'],
                                 cursor=state['claude_transcript_cursor'])
    assert first['status'] == 'not_yet_available' and 'completion' not in first
    assert first['budget']['bytes'] <= observations.MAX_BYTES
    second = observations.observe('root', row, {}, source=state['claude_transcript_source'],
                                  cursor=json.loads(json.dumps(first['cursor'])))
    assert second['status'] == 'matched' and second['completion']['handback_redirect']
    assert second['peer_handback']['body_digest'] == workers.digest('Bounded peer report.')
    assert set(second['proof']['records']) == {'call', 'result', 'header', 'notification', 'peer_handback'}
    assert second['proof']['records']['peer_handback'][0]['reference'] == second['peer_handback']['reference']
    assert 'Bounded peer report.' not in json.dumps(second)


def test_same_body_multiple_acknowledged_calls_cannot_claim_one_peer(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    body = 'Identical diagnostic across competing calls.'
    for index in range(2):
        event = host.event('SubagentHandback', {'message': body})
        flow.hook(event)
        if index == 1: host.append(host.peer(body))
        flow.hook({**event, 'hook_event_name': 'PostToolUse',
                   'tool_response': {'message': 'Report delivered to your caller.', 'success': True}})
    host.stop(None)
    host.append(host.redirect())
    host.root_hook()
    assert host.row()['state'] == 'bootstrapping'
    assert not host.row().get('terminal_status')


@pytest.mark.parametrize('defect', ['missing_stop', 'wrong_stop_path', 'active_stop', 'wrong_child', 'wrong_call',
    'wrong_output', 'wrong_origin', 'missing_newline', 'changed_body', 'old_notification', 'source_replaced'])
def test_no_report_terminal_requires_exact_native_envelopes(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    if defect != 'missing_stop':
        host.stop('Unsent body differs from the host sentinel.', **(
            {'agent_transcript_path': str(host.child) + '-other'} if defect == 'wrong_stop_path' else
            {'stop_hook_active': True} if defect == 'active_stop' else {}))
    note = notification_record(host.row(), host.result, observations.NO_REPORT_RESULT)
    note['sessionId'] = 'root'
    content = note['message']['content']
    if defect == 'wrong_child': content = content.replace(f'<task-id>{host.child_id}', '<task-id>foreign')
    if defect == 'wrong_call': content = content.replace(f'<tool-use-id>{host.row()["call_id"]}', '<tool-use-id>foreign')
    if defect == 'wrong_output': content = content.replace('/fixture/' + host.child_id, '/fixture/foreign')
    if defect == 'wrong_origin': note['origin']['producer'] = 'user'
    if defect == 'missing_newline': content = content.replace('report.\n</result>', 'report.</result>')
    if defect == 'changed_body': content = content.replace('no report was delivered', 'a report was delivered')
    if defect == 'old_notification': note['timestamp'] = host.row()['prepared_at']
    note['message']['content'] = content
    host.append(note)
    if defect == 'source_replaced':
        saved = host.parent.with_suffix('.saved')
        host.parent.rename(saved)
        host.parent.write_bytes(saved.read_bytes())
    host.root_hook()
    row = host.row()
    assert row['state'] in workers.LIVE
    assert row.get('handback', {}).get('status') not in {'delivered', 'no_report'}
    with pytest.raises(w.Refusal):
        host.accept()


@pytest.mark.parametrize('defect', [None, 'report', 'identity', 'claimed', 'binding', 'body_tamper', 'delivered'])
def test_legacy_preclaim_stop_conflict_only_recovers_with_fresh_exact_no_report(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.stop('First host-forced stop.')
    host.stop('Second host-forced stop.')
    state = host.controller.report()
    row = state['workers'][host.grant]
    # Model the persisted 2.32.1 conflict, without rewriting the live controller.
    row.update(state='unknown', completion_conflict=True)
    row.pop('stop_observations')
    if defect == 'identity': row['identity_conflict'] = True
    if defect == 'claimed': row['claimed_at'] = workers.now()
    if defect == 'binding': row['binding']['revision'] = -1
    if defect == 'delivered': row['handback'] = {'status': 'delivered', 'notification': {'old': 'proof'}}
    body = 'A different reported result.' if defect == 'report' else observations.NO_REPORT_RESULT
    note = notification_record(row, host.result, body)
    note['sessionId'] = 'root'
    host.append(note)
    parsed = observations.observe_many('root', [row], {}, source=state['claude_transcript_source'],
        cursor=state['claude_transcript_cursor'])
    cursor = parsed['cursor']
    for entry in cursor['entries'].values():
        for value in entry['notification']:
            value['projection'].pop('no_report', None)
    state['claude_transcript_cursor'] = observations._sealed(cursor)
    if defect == 'body_tamper':
        host.parent.write_text(host.parent.read_text().replace('no report was delivered', 'a report was delivered'))
    workers.observe_claude_launches(state, [row], {})
    if defect is None:
        assert row['state'] == 'failed' and row['completion_conflict'] is False
        assert row['handback']['status'] == 'no_report'
        assert any(event['kind'] == 'legacy_no_report_conflict_reclassified'
                   for event in row['reconciliation_events'].values())
    else:
        assert row['state'] == 'unknown' and row['completion_conflict'] is True


def test_failed_startup_then_ready_retry_releases_ten_task_cohort(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch, count=10, scoped=True)
    host.launch()
    with pytest.raises(w.Refusal, match='Cohort startup'):
        reserve(host.controller, host.state, task='T1', slots=11)
    host.stop('Could not claim this attempt.')
    host.notify(observations.NO_REPORT_RESULT)
    assert host.row()['state'] == 'failed'
    with pytest.raises(w.Refusal, match='Cohort startup'):
        reserve(host.controller, host.state, task='T1', slots=11)
    host.launch(retry_reason='Exact native no-report proves first startup failed; retry corrected commands.')
    host.consume()
    with pytest.raises(w.Refusal, match='Cohort startup'):
        reserve(host.controller, host.state, task='T1', slots=11)
    event = host.event('Read', {'file_path': 'input.py'})
    flow.hook(event)
    flow.hook({**event, 'hook_event_name': 'PostToolUse'})
    assert workers.readiness(host.row())['status'] == 'ready'
    grants = [reserve(host.controller, host.state, task='T' + str(index), slots=11)['grant']
              for index in range(1, 10)]
    assert len({grant['grant_id'] for grant in grants}) == 9
    assert {grant['task_id'] for grant in grants} == {'T' + str(index) for index in range(1, 10)}
    assert len([row for row in host.controller.report()['workers'].values() if row['state'] in workers.LIVE]) == 10
