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
