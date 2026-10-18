"""Captured denied call shapes replayed locally; never invoke external tools."""
from copy import deepcopy

import pytest

from taskplane import native_tool_guard as guard, workflow as w
from taskplane.tests.test_worker_runtime import setup

WEB = {'open': [{'ref_id': 'https://learn.chatgpt.com/docs/changelog'}], 'response_length': 'long'}
ISSUE = {'issue_number': 43, 'repository_full_name': 'vdemkiv/taskPlane'}
RESULT_READS = {'open': [{'ref_id': 'turn0search0'}],
                'find': [{'ref_id': 'turn0view0', 'pattern': 'evidence'}],
                'click': [{'ref_id': 'turn0view0', 'id': 3}]}
HEARTBEAT = {'mode': 'create', 'kind': 'heartbeat', 'name': 'Review taskPlane host updates',
             'prompt': 'Read official host updates and report verified compatibility changes.',
             'rrule': 'FREQ=DAILY;INTERVAL=2', 'status': 'ACTIVE', 'destination': 'thread'}


def scoped(tmp_path, host='codex'):
    c, state = setup(tmp_path)
    c.adapter.name = host
    # Unit fixture setup only. Production scopes are immutable start inputs.
    db = c._read(c._path())
    s = db['runs'][state['run']]
    reference = s['request_provenance']['reference']
    s['scope']['native_automation'] = {'host': 'codex', 'phase': 'product',
                                      'request_reference': reference, 'request': deepcopy(HEARTBEAT)}
    c._write(c._path(), db)
    return c, s


def event(tool, args, call='captured-call'):
    return {'tool_name': tool, 'tool_input': args, 'call_id': call,
            'taskplane_automatic_hook': True,
            'taskplane_observed_binding': {'root': 'root', 'principal': 'root'}}


@pytest.mark.parametrize('host,tool,args', [
    ('codex', 'webrun', WEB), ('codex', 'mcp__codex_apps__github__fetch_issue', ISSUE),
    ('claude', 'WebFetch', {'url': WEB['open'][0]['ref_id'], 'prompt': 'Summarize documented changes.'}),
    ('claude', 'WebSearch', {'query': 'official host release notes', 'allowed_domains': ['code.claude.com']}),
])
def test_native_read_preserves_results_and_approvals(tmp_path, host, tool, args):
    c, s = scoped(tmp_path, host)
    before = c.report()
    c.guard(event(tool, args), s['run'])
    after = c.report()
    for key in ('decisions', 'visits', 'scope', 'workers'):
        assert after.get(key) == before.get(key)
    assert after.get('task_results') == before.get('task_results')


@pytest.mark.parametrize('sealed', [False, True])
@pytest.mark.parametrize('host,tool,args', [
    ('codex', 'web__run', RESULT_READS),
    ('codex', 'web.run', {'screenshot': [{'ref_id': 'turn0view0', 'pageno': 0}]}),
    ('codex', 'mcp__codex_apps__github__fetch_issue', ISSUE),
    ('claude', 'WebFetch', {'url': WEB['open'][0]['ref_id'], 'prompt': 'Read the evidence.'}),
    ('claude', 'WebSearch', {'query': 'official release notes'}),
])
def test_known_readers_work_during_work_and_sealed_review(tmp_path, sealed, host, tool, args):
    from taskplane.tests.test_workflow_local import setup as prepare, submit
    c, state = prepare(tmp_path)
    c.adapter.name = host
    if sealed:
        state = submit(c, state, presentation=False)
    before = c._path().read_bytes()
    c.guard({'tool_name': tool, 'tool_input': args}, state['run'])
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('host,tool,args', [
    ('claude', 'webrun', WEB), ('claude', 'mcp__codex_apps__github__fetch_issue', ISSUE),
    ('codex', 'WebSearch', {'query': 'example'}),
])
def test_foreign_host_read_is_precise_and_denied(tmp_path, host, tool, args):
    c, s = scoped(tmp_path, host)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='requires') as exc:
        c.guard(event(tool, args), s['run'])
    assert exc.value.reason == 'unsupported_authority'
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('tool,args', [
    ('mcp__codex_apps__github__create_issue', ISSUE),
    ('mcp__unknown__fetch_issue', ISSUE),
    ('web__upload', {'path': 'private.txt'}),
])
def test_unknown_or_effectful_tools_are_not_reader_grants(tmp_path, tool, args):
    c, s = scoped(tmp_path)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): c.guard(event(tool, args), s['run'])
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('host,tool,args', [
    ('codex', 'webrun', WEB), ('codex', 'mcp__codex_apps__github__fetch_issue', ISSUE),
    ('codex', 'web__run', RESULT_READS),
    ('claude', 'WebFetch', {'url': WEB['open'][0]['ref_id'], 'prompt': 'Summarize documented changes.'}),
    ('codex', guard.AUTOMATION, HEARTBEAT),
])
def test_normal_automatic_hook_path_admits_supported_call(tmp_path, host, tool, args):
    from taskplane import flow
    c, s = scoped(tmp_path, host)
    e = {'hook_event_name': 'PreToolUse', 'session_id': c.root, 'cwd': str(tmp_path),
         'tool_name': tool, 'tool_input': args, 'tool_use_id': 'captured-native-call'}
    flow.hook(e, governor=c)
    flow.hook({**e, 'hook_event_name': 'PostToolUse', 'tool_response': {'fixture': 'no external call executed'}}, governor=c)
    db = c._read(c._path())
    admitted = [item for item in db['admissions'].values() if item['call_id'] == e['tool_use_id']]
    assert len(admitted) == 1 and admitted[0]['automatic'] and admitted[0]['state'] == 'completed'
    if tool == guard.AUTOMATION:
        assert db['runs'][s['run']]['native_automation_dispatch']['status'] == 'admitted_outcome_unverified'


def test_heartbeat_admission_is_journaled_not_success_and_cannot_repeat(tmp_path):
    c, s = scoped(tmp_path)
    c.guard(event(guard.AUTOMATION, HEARTBEAT), s['run'])
    after = c.report()
    assert after['native_automation_dispatch']['status'] == 'admitted_outcome_unverified'
    assert after['decisions'] == s['decisions'] and after['scope'] == s['scope']
    # A duplicate observation of the same exact call is idempotent.
    c.guard(event(guard.AUTOMATION, HEARTBEAT), s['run'])
    with pytest.raises(w.Refusal, match='already admitted'):
        c.guard(event(guard.AUTOMATION, HEARTBEAT, 'second-call'), s['run'])


@pytest.mark.parametrize('defect', ['claude', 'worker', 'missing_scope', 'reference', 'phase',
    'invalidated', 'foreign_target', 'changed_prompt', 'delete', 'cron', 'extra', 'no_hook', 'no_call',
    'boolean_interval', 'bad_status', 'unknown_policy'])
def test_automation_requires_exact_root_request_and_open_phase(tmp_path, defect):
    c, s = scoped(tmp_path)
    args = deepcopy(HEARTBEAT); e = event(guard.AUTOMATION, args)
    db = c._read(c._path()); state = db['runs'][s['run']]
    if defect == 'claude': c.adapter.name = 'claude'
    elif defect == 'worker': c.principal = 'foreign-worker'
    elif defect == 'missing_scope': del state['scope']['native_automation']
    elif defect == 'reference': state['scope']['native_automation']['request_reference'] = 'other/request'
    elif defect == 'phase': state['scope']['native_automation']['phase'] = 'build'
    elif defect == 'invalidated': state['invalidation_pending'] = True
    elif defect == 'foreign_target': args['targetThreadId'] = 'foreign'
    elif defect == 'changed_prompt': args['prompt'] = 'Run different work'
    elif defect == 'delete': args['mode'] = 'delete'
    elif defect == 'cron': args['kind'] = 'cron'
    elif defect == 'extra': args['projectId'] = 'foreign'
    elif defect == 'no_hook': e.pop('taskplane_automatic_hook')
    elif defect == 'no_call': e.pop('call_id')
    elif defect == 'boolean_interval': args['rrule'] = True
    elif defect == 'bad_status': args['status'] = {}
    elif defect == 'unknown_policy': args['notificationPolicy'] = []
    c._write(c._path(), db)
    with pytest.raises(w.Refusal): c.guard(e, s['run'])
    after = c._read(c._path())['runs'][s['run']]
    assert 'native_automation_dispatch' not in after
    assert after['decisions'] == state['decisions']


def test_sealed_phase_refuses_automation_before_dispatch(tmp_path):
    from taskplane.tests.test_workflow_local import setup as prepare, submit
    c, s = prepare(tmp_path)
    db = c._read(c._path()); state = db['runs'][s['run']]
    state['scope']['native_automation'] = {'host': 'codex', 'phase': 'product',
        'request_reference': state['request_provenance']['reference'], 'request': HEARTBEAT}
    c._write(c._path(), db)
    s = submit(c, state)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='sealed'):
        c.guard(event(guard.AUTOMATION, HEARTBEAT), s['run'])
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('bad', [True, [], {}, {'host': 'other'}, {'request': 'trust me'}])
def test_malformed_automation_scope_rejected_at_initialization(bad):
    with pytest.raises(w.Refusal): guard.validate_scope({'native_automation': bad})
