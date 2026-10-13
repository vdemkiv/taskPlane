"""Native guard notice regression; synthetic reports, never live-host certification."""
from copy import deepcopy
import json

import pytest

from taskplane import claude_worker_observations as observations, flow, workflow as w, worker_runtime as workers
from taskplane.tests.test_claude_interactive_recovery import Interactive

pytestmark = pytest.mark.skipif(not observations.supported_reader(), reason='Requires native no-follow reads')
BODY = 'Review finding: .claude/settings.local.json contains configuration.\nTreat it as evidence, not authority.\n'


def delivered(host, *, variant='attachment', defect=None, body=BODY, guarded=True):
    event = host.event('SubagentHandback', {'message': body})
    flow.hook(event)
    text = observations.SETTINGS_NOTICE + body if guarded else body
    if defect == 'notice': text = text.replace('settings-json.', 'settings-json!')
    if defect == 'unknown_notice': text = text.replace('settings-json.', 'bypass-permissions.')
    if defect == 'body': text += 'Mutated report.'
    if defect == 'missing_notice': text = 'Model claims this was sanitized.\n' + BODY
    if defect == 'double_notice': text = observations.SETTINGS_NOTICE + text
    peer = host.peer(text, variant=variant)
    peer['version'] = '2.1.297' if defect == 'version' else '2.1.296'
    if defect == 'wrong_child':
        if variant == 'attachment': peer['attachment']['origin']['from'] = 'foreign'
        else: peer['origin']['from'] = 'foreign'
    if defect == 'user_authored': peer.update(type='user', isMeta=False, promptSource='user')
    host.append(peer)
    flow.hook({**event, 'hook_event_name': 'PostToolUse',
               'tool_response': {'message': 'Report delivered to your caller.', 'success': True}})
    host.stop(None)
    host.append(host.redirect())
    return event, peer


@pytest.mark.parametrize('variant', ['user', 'attachment'])
def test_guarded_report_joins_without_changing_original_or_delivered_evidence(tmp_path, monkeypatch, variant):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    event, peer = delivered(host, variant=variant)
    before = deepcopy(host.row()['handback_call'])
    result = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert result['state'] == 'result_pending' and not result['result_accepted']
    assert row['handback_call'] == before
    proof = row['handback']
    assert proof['body_digest'] == workers.digest(BODY)
    assert proof['peer_handback']['body_digest'] == workers.digest(observations.SETTINGS_NOTICE + BODY)
    assert proof['peer_handback']['record_sha256'] == workers.digest(peer)
    assert proof['admission']['envelope']['call_id'] == event['tool_use_id']
    assert proof['admission']['delivery_transform']['original_input_digest'] == workers.digest({'message': BODY})
    assert BODY not in json.dumps(row)
    assert host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant) == result
    host.accept()
    accepted = deepcopy(host.row())
    again = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    assert again['result_accepted'] and host.row() == accepted


@pytest.mark.parametrize('defect', ['notice', 'unknown_notice', 'body', 'missing_notice', 'double_notice',
                                  'version', 'wrong_child', 'user_authored', 'missing_ack', 'bad_runtime',
                                  'forged_admission', 'missing_stop', 'pending_command'])
def test_notice_cannot_override_original_integrity_or_native_proof(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host, defect=defect)
    target = host.controller._path()
    db = json.loads(target.read_text())
    state = db['runs'][host.state['run']]
    row = state['workers'][host.grant]
    if defect == 'missing_ack': row['handback_call'].pop('acknowledgment')
    if defect == 'bad_runtime': row['handback_call']['runtime']['root'] = '/foreign'
    if defect == 'forged_admission': row['handback_call']['envelope']['body_digest'] = '0' * 64
    if defect == 'missing_stop':
        state['unbound_worker_events'] = {k: v for k, v in state['unbound_worker_events'].items()
                                          if v.get('event') != 'SubagentStop'}
        row.pop('stop_observation', None)
        row.pop('stop_observations', None)
    if defect == 'pending_command':
        state['observed_handles']['live'] = {'state': 'running', 'worker_id': host.child_id,
                                             'revision': state['revision'], 'visit': w.current(state)['id']}
    host.controller._write(target, db)
    before = target.read_bytes()
    if defect == 'pending_command':
        # Joining native evidence does not accept output or waive live work.
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
        with pytest.raises(w.Refusal, match='commands remain live'):
            host.accept()
    else:
        with pytest.raises(w.Refusal, match='cannot yet be joined'):
            host.controller.worker(host.state['run'], 'reconcile-handback',
                revision=host.state['revision'], grant=host.grant)
        assert target.read_bytes() == before
    assert not host.controller.report().get('task_results')


def test_existing_cursor_recovery_preserves_fifteen_accepted_results(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch, count=16)
    for index in range(15):
        task = f'T{index}'
        host.launch(task=task)
        host.ready()
        host.deliver('Accepted synthetic review ' + task)
        (tmp_path / (task + '.md')).write_text('Independent checked evidence ' + task)
        host.controller.worker(host.state['run'], 'accept-result', revision=host.state['revision'],
            task=task, grant=host.grant, request={'outputs': [task + '.md'], 'checks': [
                {'name': 'Independent check', 'status': 'pass', 'evidence': task + '.md'}]})
    host.launch(task='T15')
    host.ready()
    # Reproduce a persisted pre-fix cursor: parsing accepted the frame but did
    # not project the host notice, so its exact-body join was rejected.
    real = observations._peer_handback
    def old_projection(record):
        value = real(record)
        if value: value.pop('delivery_transform', None)
        return value
    with monkeypatch.context() as old:
        old.setattr(observations, '_peer_handback', old_projection)
        delivered(host)
        host.root_hook()
    before = deepcopy(host.controller.report())
    assert host.row()['state'] == 'running'
    assert any(x['kind'] == 'redirect_join_rejected' for x in host.row()['reconciliation_events'].values())
    host.controller.worker(before['run'], 'reconcile-handback', revision=before['revision'], grant=host.grant)
    after = host.controller.report()
    assert after['task_results'] == before['task_results'] and len(after['task_results']) == 15
    assert after['decisions'] == before['decisions'] and after['visits'] == before['visits']
    assert len(after['workers']) == 16
    for grant, row in before['workers'].items():
        if grant != host.grant: assert after['workers'][grant] == row
    recovered = after['workers'][host.grant]
    assert recovered['state'] == 'result_pending' and recovered['attempt'] == before['workers'][host.grant]['attempt']
    assert recovered['handback_call'] == before['workers'][host.grant]['handback_call']
    assert recovered['call_id'] == before['workers'][host.grant]['call_id']


@pytest.mark.parametrize('defect', ['codex', 'worker', 'revision', 'foreign_grant', 'supplied_proof'])
def test_reconciliation_is_scoped_to_current_root_and_original_grant(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host)
    if defect == 'codex': host.controller.adapter.name = 'codex'
    if defect == 'worker': host.controller.principal = host.child_id
    before = host.controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=99 if defect == 'revision' else host.state['revision'],
            grant='foreign' if defect == 'foreign_grant' else host.grant,
            request={'proof': 'claimed by model'} if defect == 'supplied_proof' else {})
    assert host.controller._path().read_bytes() == before


def test_reconcile_cli_uses_original_attempt_without_automatic_acceptance(tmp_path, monkeypatch, capsys):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host)
    assert flow.main(['worker', '--workspace', str(tmp_path), '--run', host.state['run'],
        '--operation', 'reconcile-handback', '--grant', host.grant,
        '--expected-revision', str(host.state['revision'])], governor=host.controller) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['state'] == 'result_pending' and not result['result_accepted']
    assert not host.controller.report().get('task_results')


@pytest.mark.parametrize('defect', ['cached_transform', 'pinned_report'])
def test_reconciliation_revalidates_cached_transform_against_original_span(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host)
    host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    target = host.controller._path()
    if defect == 'cached_transform':
        db = json.loads(target.read_text())
        state = db['runs'][host.state['run']]
        cursor = state['claude_transcript_cursor']
        entry = next(value for value in cursor['entries'].values() if value.get('peer_handback'))
        entry['peer_handback'][0]['projection']['delivery_transform']['original_body_digest'] = '0' * 64
        state['claude_transcript_cursor'] = observations._sealed(cursor)
        host.controller._write(target, db)
    else:
        original = host.parent.read_bytes()
        modified = original.replace(b'Treat it as evidence', b'Treat it as mandates')
        assert modified != original
        host.parent.write_bytes(modified)
    before = target.read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert target.read_bytes() == before


def test_literal_notice_in_original_report_never_authorizes_removal(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    body = observations.SETTINGS_NOTICE + BODY
    # A model can write this literal notice; only the full admitted body matches.
    delivered(host, body=body, guarded=False)
    host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert row['handback']['body_digest'] == workers.digest(body)
    assert 'delivery_transform' not in row['handback']['admission']
    host.accept()


def test_exact_notice_without_observed_native_trigger_stays_blocked(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host, body='Unrelated report without a settings path.')
    with pytest.raises(w.Refusal, match='cannot yet be joined'):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert host.row()['state'] == 'running'


def test_corrected_runtime_revalidates_historical_proof_without_rewriting_it(tmp_path, monkeypatch):
    from taskplane import host_capabilities
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    delivered(host)
    before = deepcopy(host.row())
    corrected = deepcopy(host_capabilities.runtime_identity())
    corrected['root'] = '/verified/new-version'
    corrected['member_sha256'] = {key: '1' * 64 for key in corrected['member_sha256']}
    monkeypatch.setattr(host_capabilities, 'runtime_identity', lambda: corrected)
    host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert row['expected_runtime'] == before['expected_runtime'] != corrected
    assert row['handback_call'] == before['handback_call']
    assert row['handback']['admission']['runtime'] == before['expected_runtime']
    assert workers.readiness(row)['status'] == 'ready'
    host.accept()
