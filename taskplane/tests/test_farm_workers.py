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
    monkeypatch.setattr(claude, '_read', unexpected_read)
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
    stop = {'host': 'claude', 'hook_event_name': 'SubagentStop', 'session_id': 'parent',
            'agent_id': 'child', 'event_id': 'stop-first'}
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
