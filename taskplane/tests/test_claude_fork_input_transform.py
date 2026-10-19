"""Observed Claude fork framing; synthetic inputs, never live host certification."""
from copy import deepcopy

import pytest

from taskplane import claude_worker_observations as o
from taskplane.context import digest
from taskplane.tests.test_claude_worker_lifecycle import setup_native, encoded, select, observe

pytestmark = pytest.mark.skipif(not o.supported_reader(), reason='Requires native no-follow reads')


def fixture(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    call.update(version='2.1.296', type='assistant', isSidechain=False)
    call['message']['role'] = 'assistant'
    args = call['message']['content'][0]['input']
    args.update(subagent_type='taskplane:tp-executor', run_in_background='true')
    hook = {key: value for key, value in args.items() if key != 'run_in_background'}
    attempt['dispatch_digest'] = digest(hook)
    return parent, attempt, call, result, header, event, hook


def test_exact_fork_flag_omission_keeps_original_and_admitted_digests(tmp_path, monkeypatch):
    parent, attempt, call, result, header, event, hook = fixture(tmp_path, monkeypatch)
    parent.write_bytes(encoded(call) + encoded(result))
    source = select(event)
    answer = observe(attempt, source)
    assert answer['status'] == 'matched'
    projection = answer['proof']['records']['call'][0]['projection']
    transform = projection['input_transform']
    assert transform['hook_input_digest'] == attempt['dispatch_digest'] == digest(hook)
    assert transform['native_input_digest'] == projection['input_digest'] != attempt['dispatch_digest']
    assert answer['record_sha256']['call'] == digest(call)
    assert o.correlate_records('parent', attempt, [call, result], [header])['status'] == 'matched'
    assert observe(attempt, source, answer['cursor'])['status'] == 'matched'
    parent.write_bytes(encoded(call).replace(b'SECRET LAUNCH PROMPT', b'ALTERED CALL PROMPT') + encoded(result))
    assert observe(attempt, source, answer['cursor'])['status'] == 'conflict'


@pytest.mark.parametrize('change', ['version', 'boolean', 'false', 'resume', 'prompt', 'foreign', 'role', 'tool'])
def test_conversion_never_hides_other_argument_or_native_framing_changes(tmp_path, monkeypatch, change):
    parent, attempt, call, result, _, event, _ = fixture(tmp_path, monkeypatch)
    args = call['message']['content'][0]['input']
    if change == 'version': call['version'] = '2.1.297'
    if change == 'boolean': args['run_in_background'] = True
    if change == 'false': args['run_in_background'] = 'false'
    if change == 'resume': args['resume'] = 'another-child'
    if change == 'prompt': args['prompt'] += ' changed'
    if change == 'foreign': call['sessionId'] = 'foreign'
    if change == 'role': call['message']['role'] = 'user'
    if change == 'tool': call['message']['content'][0]['name'] = 'Task'
    parent.write_bytes(encoded(call) + encoded(result))
    source = select({**event, 'transcript_path': str(parent)}) if change != 'foreign' else None
    if source is None:
        assert o.correlate_records('parent', attempt, [call, result], [])['status'] == 'conflict'
    else:
        assert observe(attempt, source)['status'] == 'conflict'


def test_pretool_matching_uses_same_exact_conversion(tmp_path, monkeypatch):
    parent, attempt, call, _, _, event, hook = fixture(tmp_path, monkeypatch)
    parent.write_bytes(encoded(call))
    source = select(event)
    attempt.update(root='parent', transcript_admission_offset=0)
    event.update(tool_name='Agent', tool_use_id=attempt['call_id'], tool_input=hook)
    assert o.preflight_launch('parent', attempt, event, source=source)['status'] == 'matched'
    wrong = deepcopy(event)
    wrong['tool_input']['prompt'] += ' changed'
    assert o.preflight_launch('parent', attempt, wrong, source=source)['status'] == 'conflict'


@pytest.mark.parametrize('version', ['2.1.296', '2.1.297', '2.1.302'])
@pytest.mark.parametrize('change', ['', 'human', 'origin', 'extra', 'missing_id'])
def test_notification_delivery_run_id_is_metadata_not_authority(change, version):
    body = ('<task-notification>\n<task-id>child-1</task-id>\n<tool-use-id>call-1</tool-use-id>\n'
            '<output-file>/native/output</output-file>\n<status>completed</status>\n'
            '<summary>Finished</summary>\n<result>' + o.NO_REPORT_RESULT + '</result>\n</task-notification>')
    row = {'version': version, 'type': 'user', 'isSidechain': False,
           'origin': {'kind': 'task-notification', 'producer': 'session-task', 'runId': 'opaque-delivery-1'},
           'promptSource': 'system', 'turnOrigin': 'task_notification',
           'message': {'role': 'user', 'content': body}}
    if change == 'human': row.update(promptSource='user', turnOrigin='human')
    if change == 'origin': row['origin']['kind'] = 'human'
    if change == 'extra': row['origin']['unknown'] = 'field'
    if change == 'missing_id': row['origin']['runId'] = ''
    result = o._notification(row)
    if change:
        assert result is None
    else:
        assert result['no_report'] is True and result['record_sha256'] == digest(row)
