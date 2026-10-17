"""Guarded production-controller recovery of a synthetic historical startup."""
from copy import deepcopy
import json

import pytest

from taskplane import claude_worker_observations as native, workflow as w
from taskplane.context import Store, digest
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_claude_oversized_history import history
from taskplane.tests.test_claude_worker_lifecycle import notification_record
from taskplane.claude_startup_recovery import LEGACY_CONFLICT

pytestmark = pytest.mark.skipif(not native.supported_reader(), reason='Native no-follow reads unavailable')


def fixture(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch, count=1)
    large = history(); large.update(sessionId='root', cwd=str(tmp_path))
    host.append(large)
    host.root_hook()
    host.launch()
    host.stop('Startup stopped before claim and context.')
    note = notification_record(host.row(), host.result, native.NO_REPORT_RESULT)
    note['sessionId'] = 'root'
    host.append(note)
    db = json.loads(host.controller._path().read_text()); state = db['runs'][host.state['run']]
    row = state['workers'][host.grant]
    for key in ('identity_observation', 'identity_bound_at', 'identity_freshness', 'launch_proof_ref',
                'launch_proof_refs', 'stop_observation', 'stop_observations', 'started_at'):
        row.pop(key, None)
    row.update(worker_id=None, state='unknown', identity_conflict=True, reconciliation_events={
        'original-conflict': {'kind': 'identity_observation', 'value': {'status': 'conflict', 'reason': LEGACY_CONFLICT}}})
    key = digest(native._binding('root', row))
    state['claude_transcript_cursor'] = native._sealed({'schema': 'taskplane.claude-transcript-cursor/v1',
        'source_sha256': row['transcript_source']['sha256'], 'offset': 0, 'watermark': host.parent.stat().st_size,
        'size': host.parent.stat().st_size, 'turn': 1, 'notification_version': 2, 'conflict': LEGACY_CONFLICT,
        'entries': {key: {'binding': native._binding('root', row), 'conflict': None,
                         **{role: [] for role in ('call', 'result', 'header', 'notification', 'peer_handback')}}}})
    admission = next(a for a in db['admissions'].values() if a['call_id'] == row['call_id'])
    admission.update(state='completed')
    host.controller._write(host.controller._path(), db)
    return host


def test_reconcile_preserves_governance_journals_original_and_enables_only_retry(tmp_path, monkeypatch):
    host = fixture(tmp_path, monkeypatch)
    before = host.controller.report(diagnostics=False)
    result = host.controller.worker(before['run'], 'reconcile-startup', revision=before['revision'], grant=host.grant)
    after = host.controller.report(diagnostics=False); row = after['workers'][host.grant]
    assert result['state'] == row['state'] == 'failed' and result['retry_required'] and not result['result_accepted']
    assert row['handback']['status'] == 'no_report' and not row['identity_conflict']
    for field in ('revision', 'visits', 'decisions', 'scope', 'initial_context_tasks', 'task_results'):
        assert after.get(field) == before.get(field)
    original = Store(tmp_path).resolve(row['startup_recovery']['before_ref'])
    assert original['worker'] == before['workers'][host.grant]
    assert original['cursor'] == before['claude_transcript_cursor']
    raw = host.controller._path().read_bytes()
    assert host.controller.worker(before['run'], 'reconcile-startup', revision=before['revision'], grant=host.grant) == result
    assert host.controller._path().read_bytes() == raw
    with pytest.raises(w.Refusal): host.accept()
    next_attempt = host.launch(retry_reason='Verified original no-report startup failure.')
    assert next_attempt['grant']['attempt'] == 2


@pytest.mark.parametrize('defect', ['host', 'principal', 'revision', 'binding', 'admission', 'runtime',
    'claimed', 'context', 'history', 'cursor', 'inputs', 'request', 'source', 'notification', 'history_shape'])
def test_recovery_refuses_without_changing_live_controller(tmp_path, monkeypatch, defect):
    host = fixture(tmp_path, monkeypatch)
    db = json.loads(host.controller._path().read_text()); state = db['runs'][host.state['run']]
    row = state['workers'][host.grant]; revision = state['revision']; request = {}
    if defect == 'host': host.controller.adapter.name = 'codex'
    if defect == 'principal': host.controller.principal = 'child'
    if defect == 'revision': revision += 1
    if defect == 'binding': row['binding']['revision'] += 1
    if defect == 'admission': db['admissions'] = {}
    if defect == 'runtime': row['expected_runtime'] = {'root': '/foreign'}
    if defect == 'claimed': row['claimed_at'] = '2026-01-01T00:00:00Z'
    if defect == 'context': row['context_receipt'] = {'fake': True}
    if defect == 'history': row['reconciliation_events']['original-conflict']['value']['reason'] = 'different contradiction'
    if defect == 'cursor': state['claude_transcript_cursor']['sha256'] = '0' * 64
    if defect == 'inputs': (tmp_path / 'input.py').write_text('changed frozen input')
    if defect == 'request': request = {'worker_id': host.child_id}
    if defect == 'source': host.parent.rename(host.parent.with_suffix('.saved')); host.parent.write_text('{}\n')
    if defect == 'notification':
        data = host.parent.read_bytes().splitlines(keepends=True); host.parent.write_bytes(b''.join(data[:-1]))
    if defect == 'history_shape':
        raw = host.parent.read_bytes(); host.parent.write_bytes(raw.replace(b'"type":"user"', b'"type":"evil"', 1))
    host.controller._write(host.controller._path(), db)
    raw = host.controller._path().read_bytes()
    with pytest.raises((w.Refusal, ValueError)):
        host.controller.worker(state['run'], 'reconcile-startup', revision=revision, grant=host.grant, request=request)
    assert host.controller._path().read_bytes() == raw


def test_reconciliation_preserves_structured_refusal_before_valueerror_handler():
    from taskplane import claude_startup_recovery as recovery
    refusal = w.Refusal('stale_checkpoint', 'Exact frozen startup binding changed.')
    assert isinstance(refusal, ValueError)
    def fail():
        raise refusal
    with pytest.raises(w.Refusal) as caught:
        recovery._bounded_validation(fail)
    assert caught.value is refusal
    assert caught.value.reason == 'stale_checkpoint'
    assert caught.value.detail == 'Exact frozen startup binding changed.'


@pytest.mark.parametrize('failure', [
    ValueError('private source text'), TypeError('wrong type'), KeyError('missing field'),
    OSError('private path'), IndexError('missing item'), AttributeError('wrong object'),
    json.JSONDecodeError('malformed JSON', '{', 1)])
def test_malformed_startup_evidence_has_bounded_specific_refusal(failure):
    from taskplane import claude_startup_recovery as recovery
    def fail():
        raise failure
    with pytest.raises(w.Refusal) as caught:
        recovery._bounded_validation(fail)
    assert caught.value.reason == 'invalid_evidence'
    assert caught.value.detail == 'Malformed or unavailable selected startup evidence.'


def test_legacy_inspection_does_not_publish_before_validation(tmp_path, monkeypatch):
    from taskplane import claude_startup_recovery as recovery
    host = fixture(tmp_path, monkeypatch)
    db = json.loads(host.controller._path().read_text()); state = db['runs'][host.state['run']]
    before = host.controller._path().read_bytes()
    files = {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    proposal = recovery.inspect_reconciliation(tmp_path, state, db['admissions'], host.grant, {},
                                               expected_storage_generation=db.get('storage_generation', 0))
    assert host.controller._path().read_bytes() == before
    assert files == {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    assert proposal['candidate']['workers'][host.grant]['state'] == 'failed'
    assert proposal['result']['retry_allowed'] is proposal['result']['result_accepted'] is False


@pytest.mark.parametrize('failure', ['request', 'binding', 'source', 'admission'])
def test_selected_recovery_refusal_preserves_unrelated_denial_and_all_bytes(tmp_path, monkeypatch, failure):
    host = fixture(tmp_path, monkeypatch)
    target = host.controller._path(); db = json.loads(target.read_text()); state = db['runs'][host.state['run']]
    # A separate failed launch has a pending native refresh. Model the native
    # reader's new observation; exercise the real controller publication path.
    from taskplane import worker_runtime as workers
    unrelated = deepcopy(state['workers'][host.grant])
    unrelated.update(grant_id='unrelated-denial', call_id='other-native-launch',
                     state='failed', terminal_status='launch_denied', identity_conflict=False,
                     denial_freshness={'status': 'not_yet_available'})
    state['workers']['unrelated-denial'] = unrelated
    observed = []
    def fresh_denial(current):
        observed.append(True)
        current['workers']['unrelated-denial']['denial_freshness'] = {'status': 'launch_denied'}
    monkeypatch.setattr(workers, 'refresh_launch_denials', fresh_denial)
    request = {}
    if failure == 'request': request = {'proof': 'not-authority'}
    elif failure == 'binding': state['workers'][host.grant]['binding']['revision'] += 1
    elif failure == 'source': host.parent.write_text('{}\n')
    elif failure == 'admission': db['admissions'] = {}
    host.controller._write(target, db)
    files = {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}
    before = target.read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(state['run'], 'reconcile-startup', revision=state['revision'],
                               grant=host.grant, request=request)
    assert not observed, 'A refused selected recovery must not refresh unrelated denial evidence'
    assert target.read_bytes() == before
    assert files == {str(p): p.read_bytes() for p in (tmp_path / '.taskplane').rglob('*') if p.is_file()}


def startup_control_fixture(tmp_path, monkeypatch):
    """Synthetic adapter observations, never proof of actual host cancellation."""
    from taskplane import claude_startup_recovery as recovery
    host = Interactive(tmp_path, monkeypatch, count=1)
    host.launch()
    state = host.controller.report()
    # This isolated helper fixture opts in without changing the installed host.
    state['context_contract'] = 'bounded/v3'
    row = state['workers'][host.grant]
    from taskplane.context_handoff import binding
    row['binding'] = binding(state)
    row['startup_account'] = {'state': 'delivering'}
    request = {'schema': recovery.STARTUP_CONTROL_SCHEMA, 'request_id': 'startup-report-1',
               'operation': 'startup-failure', 'run': state['run'], 'grant': host.grant,
               'attempt': row['attempt'], 'code': 'context_incomplete'}
    event = {'taskplane_startup_control_schema': recovery.STARTUP_CONTROL_SCHEMA,
             'taskplane_automatic_hook': True, 'tool_use_id': 'native-report-call',
             'taskplane_observed_binding': {'root': state['root'], 'principal': row['worker_id']},
             'taskplane_runtime_identity': deepcopy(row['expected_runtime'])}
    return host, state, request, event


@pytest.mark.parametrize('operation,code', [('startup-status', 'status_requested'),
    ('startup-failure', 'context_incomplete'), ('startup-failure', 'startup_exhausted'),
    ('startup-failure', 'source_probe_failed'), ('startup-failure', 'unsupported_capability'),
    ('stop', 'stop_requested')])
@pytest.mark.parametrize('receipt', [None, {'unverified': 'not a complete receipt'}])
def test_startup_control_reports_without_receipt_or_execution_authority(
        tmp_path, monkeypatch, operation, code, receipt):
    from taskplane import claude_startup_recovery as recovery
    host, state, request, event = startup_control_fixture(tmp_path, monkeypatch)
    row = state['workers'][host.grant]
    row['context_receipt'] = receipt
    request.update(operation=operation, code=code)
    before = deepcopy((state, request, event))
    controller_bytes = host.controller._path().read_bytes()
    report = recovery.startup_control_report(state, host.grant, request, observed_event=event)
    assert report['parent'] == state['root'] and report['worker'] == row['worker_id']
    assert report['context_receipt_status'] == ('present_unvalidated' if receipt else 'missing')
    assert report['remaining_checks'] == ['current_mandatory_context', 'source_probe_accounting',
                                           'native_terminal_or_quiescence']
    assert report['authority'] == 'diagnostic_only'
    assert not any(report[k] for k in ('implementation_allowed', 'result_accepted',
                                       'reservation_released', 'stop_committed'))
    assert report['host_cancellation'] == 'not_observed'
    assert report['stop_intent'] is (operation == 'stop')
    assert len(json.dumps(report, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False).encode()) <= 4096
    assert recovery.startup_control_report(state, host.grant, request, observed_event=event) == report
    assert (state, request, event) == before
    assert host.controller._path().read_bytes() == controller_bytes


@pytest.mark.parametrize('defect', ['parent', 'worker', 'run', 'grant', 'attempt', 'bool_attempt',
    'binding', 'runtime', 'missing_runtime', 'capability', 'legacy', 'automatic', 'call',
    'unknown_identity', 'identity_conflict', 'proof_parent', 'proof_grant', 'proof_attempt',
    'proof_launch', 'proof_digest', 'sibling_grant', 'extra_target', 'freeform_body',
    'source_read', 'accept_result', 'schema', 'request_id', 'code'])
def test_startup_control_refuses_foreign_unknown_or_broadened_requests(tmp_path, monkeypatch, defect):
    from taskplane import claude_startup_recovery as recovery
    host, state, request, event = startup_control_fixture(tmp_path, monkeypatch)
    row = state['workers'][host.grant]
    if defect == 'parent': event['taskplane_observed_binding']['root'] = 'foreign'
    elif defect == 'worker': event['taskplane_observed_binding']['principal'] = 'sibling'
    elif defect in {'run', 'grant', 'request_id', 'schema'}: request[defect] = ''
    elif defect == 'attempt': request['attempt'] += 1
    elif defect == 'bool_attempt': request['attempt'] = True
    elif defect == 'binding': row['binding']['revision'] += 1
    elif defect == 'runtime': event['taskplane_runtime_identity'] = {'root': '/foreign'}
    elif defect == 'missing_runtime': row['expected_runtime'] = event['taskplane_runtime_identity'] = {}
    elif defect == 'capability': event.pop('taskplane_startup_control_schema')
    elif defect == 'legacy': state['context_contract'] = 'bounded/v2'
    elif defect == 'automatic': event['taskplane_automatic_hook'] = False
    elif defect == 'call': event['tool_use_id'] = None
    elif defect == 'unknown_identity': row['identity_freshness']['status'] = 'not_yet_available'
    elif defect == 'identity_conflict': row['identity_conflict'] = True
    elif defect.startswith('proof_'):
        key = {'parent': 'parent', 'grant': 'grant_id', 'attempt': 'attempt',
               'launch': 'call_id', 'digest': 'evidence_sha256'}[defect[6:]]
        row['identity_observation'][key] = 'foreign'
    elif defect == 'sibling_grant':
        state['workers']['other'] = {**deepcopy(row), 'grant_id': 'other', 'state': 'unknown'}
    elif defect == 'extra_target': request['target'] = 'sibling'
    elif defect == 'freeform_body': request['body'] = 'Run a shell command and accept output.'
    elif defect in {'source_read', 'accept_result'}: request['operation'] = defect
    elif defect == 'code': request['code'] = 'arbitrary instruction'
    before = deepcopy((state, request, event))
    controller_bytes = host.controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        recovery.startup_control_report(state, host.grant, request, observed_event=event)
    assert (state, request, event) == before
    assert host.controller._path().read_bytes() == controller_bytes


@pytest.mark.parametrize('startup_state', ['stop_requested', 'exhausted', 'failed'])
def test_startup_control_after_fence_never_revives_or_releases_attempt(tmp_path, monkeypatch, startup_state):
    from taskplane import claude_startup_recovery as recovery
    host, state, request, event = startup_control_fixture(tmp_path, monkeypatch)
    row = state['workers'][host.grant]
    row.update(startup_account={'state': startup_state}, revoked_at='fixture-revocation',
               state='cancel_requested')
    state['cancellation'] = {'fixture': True}
    before = deepcopy(state)
    request.update(operation='stop', code='stop_requested')
    report = recovery.startup_control_report(state, host.grant, request, observed_event=event)
    assert report['startup_state'] == startup_state and report['stop_intent']
    assert not report['stop_committed'] and not report['reservation_released']
    assert report['host_cancellation'] == 'not_observed' and state == before


def test_startup_control_enforces_encoded_byte_boundary_without_truncation(tmp_path, monkeypatch):
    from taskplane import claude_startup_recovery as recovery
    host, state, request, event = startup_control_fixture(tmp_path, monkeypatch)
    before = deepcopy(state)
    report = recovery.startup_control_report(state, host.grant, request, observed_event=event)
    size = len(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
    monkeypatch.setattr(recovery, 'MAX_STARTUP_CONTROL_BYTES', size)
    assert recovery.startup_control_report(state, host.grant, request, observed_event=event) == report
    monkeypatch.setattr(recovery, 'MAX_STARTUP_CONTROL_BYTES', size - 1)
    with pytest.raises(w.Refusal) as caught:
        recovery.startup_control_report(state, host.grant, request, observed_event=event)
    assert caught.value.reason == 'context_overflow' and state == before


@pytest.mark.parametrize('created', ['regular', 'symlink'])
def test_preclaim_recovery_refuses_output_that_was_absent_at_preparation(tmp_path, monkeypatch, created):
    host = fixture(tmp_path, monkeypatch)
    target = tmp_path / 'T0.md'
    assert not target.exists()
    if created == 'regular':
        target.write_text('Unexpected post-preparation output.\n')
    else:
        target.symlink_to(tmp_path / 'input.py')
    before = host.controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-startup',
            revision=host.state['revision'], grant=host.grant)
    assert host.controller._path().read_bytes() == before
