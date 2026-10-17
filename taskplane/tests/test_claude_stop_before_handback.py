"""Claude 2.1.296 incident ordering, redacted report; no live-host certification."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from taskplane import claude_worker_observations as observations, flow, workflow as w, worker_runtime as workers
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_claude_worker_lifecycle import notification_record

pytestmark = pytest.mark.skipif(not observations.supported_reader(), reason='Requires native no-follow reads')


def test_captured_b5_projection_requires_original_chronology_and_report():
    fixture = json.loads((Path(__file__).parent / 'fixtures/claude/stop-before-handback-2.1.296.json').read_text())
    s, row, completed, peer, stop = (fixture[k] for k in ('state', 'row', 'notification', 'peer_handback', 'stop'))
    original = deepcopy(row)
    result = workers._redirect_delivery(s, row, completed, peer, stop)
    assert result['completion_sequence'] == 'claude.stop-before-handback/v1'
    assert result['envelope']['body_digest'] == 'fa6899f275a40336a34f958bc5a73275fb3596c2d0fab6b37e334f12421ba318'
    assert row == original and 'textless' not in stop
    assert workers._redirect_delivery(s, row, completed, {**peer, 'body_digest': '0' * 64}, stop) is None
    assert workers._redirect_delivery(s, row, completed, peer, {**stop, 'observed_at': completed['timestamp']}) is None


def delivered(host, *, defect=None):
    # Native incident: ordinary final -> automatic stop -> handback admission ->
    # peer -> acknowledgment -> redirect. There is no post-ack textless stop.
    host.stop('Ordinary final text, distinct from the redacted handback report.')
    event = host.event('SubagentHandback', {'message': 'Redacted independently checked worker report.'})
    flow.hook(event)
    peer = host.peer(event['tool_input']['message'], variant='attachment')
    peer['version'] = '2.1.296'
    host.append(peer)
    flow.hook({**event, 'hook_event_name': 'PostToolUse',
               'tool_response': {'success': True, 'message': 'Report delivered to your caller.'}})
    note = host.redirect()
    note['version'] = '2.1.296' if defect != 'version' else '2.1.297'
    host.append(note)
    return event, peer, note


def test_pre_handback_stop_reconciles_original_call_and_preserves_other_work(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready()
    event, peer, note = delivered(host)
    before = deepcopy(host.controller.report())
    result = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert result['state'] == 'result_pending' and not result['result_accepted']
    assert row['handback_call'] == before['workers'][host.grant]['handback_call']
    assert row['handback']['stop_event'] == before['workers'][host.grant]['stop_observation']['event_id']
    assert 'textless' not in row['stop_observation']
    assert row['handback']['notification']['record_sha256'] == workers.digest(note)
    assert row['handback_reconciliation']['completion_sequence'] == 'claude.stop-before-handback/v1'
    assert any(item['kind'] == 'stop_before_handback_joined' for item in row['reconciliation_events'].values())
    after = host.controller.report()
    for key in ('decisions', 'visits', 'scope', 'task_results'):
        assert after.get(key) == before.get(key)
    # Later no-report after a resumed call is a different launch, not permission
    # to replace the proven original completion or release the original result.
    later = notification_record(row, host.result, observations.NO_REPORT_RESULT)
    later['sessionId'] = 'root'
    later['message']['content'] = later['message']['content'].replace(row['call_id'], 'different-resume-call')
    host.append(later)
    host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    assert host.row()['handback']['notification']['record_sha256'] == workers.digest(note)
    host.accept()


@pytest.mark.parametrize('defect', ['version', 'missing_ack', 'runtime', 'foreign_stop', 'stop_path',
    'stop_after_ack', 'ack_after_completion', 'bad_digest', 'body', 'call', 'future', 'tampered_span'])
def test_pre_handback_sequence_does_not_relax_identity_or_delivery(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready(); delivered(host, defect=defect)
    target = host.controller._path(); db = json.loads(target.read_text()); s = db['runs'][host.state['run']]
    row = s['workers'][host.grant]; report = row['handback_call']
    stops = [*row['stop_observations'], row['stop_observation']]
    stops += [v for v in s['unbound_worker_events'].values() if v.get('event') == 'SubagentStop']
    if defect == 'missing_ack': report.pop('acknowledgment')
    elif defect == 'runtime': report['runtime']['root'] = '/other-runtime'
    elif defect == 'foreign_stop':
        for stop in stops: stop['worker_id'] = 'foreign'
    elif defect == 'stop_path':
        for stop in stops: stop['agent_transcript_path'] = '/other-child.jsonl'
    elif defect == 'stop_after_ack':
        for stop in stops: stop['observed_at'] = workers.now()
    elif defect == 'ack_after_completion':
        report['observed_at'] = report['acknowledgment']['observed_at'] = workers.now()
    elif defect == 'bad_digest':
        for stop in stops: stop['body_digest'] = 'not-a-digest'
    elif defect == 'body': report['envelope']['body_digest'] = '0' * 64
    elif defect == 'call': report['envelope']['call_id'] = 'competing-call'
    elif defect == 'future': report['admitted_at'] = '2999-01-01T00:00:00+00:00'
    elif defect == 'tampered_span':
        host.root_hook()
        # Re-read the current cursor before changing its pinned native span.
        db = json.loads(target.read_text())
        host.parent.write_bytes(host.parent.read_bytes().replace(b'Redacted independently', b'Mutated independently'))
    host.controller._write(target, db); before = target.read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert target.read_bytes() == before


def test_same_launch_competing_no_report_remains_conflict(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready(); delivered(host)
    note = notification_record(host.row(), host.result, observations.NO_REPORT_RESULT)
    note['sessionId'] = 'root'; host.append(note)
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert not host.controller.report().get('task_results')


@pytest.mark.parametrize('claimed', [False, True])
def test_selected_startup_report_audit_handles_claimed_and_preclaim_failure(tmp_path, monkeypatch, claimed):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    if claimed:
        host.ready()
    event, _, note = delivered(host)
    if claimed:
        target = host.controller._path(); db = json.loads(target.read_text())
        row = db['runs'][host.state['run']]['workers'][host.grant]
        # Preserve the real acknowledged admission while modeling a startup
        # failure after claim: no optional legacy handback_call slot exists.
        report = row.pop('handback_call')
        row['startup_handbacks'] = {event['tool_use_id']: report}
        host.controller._write(target, db)
    before = host.controller.report(diagnostics=False)
    assert 'handback_call' not in before['workers'][host.grant]
    result = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert row['state'] == 'failed' and row['handback']['status'] == 'startup_failed'
    assert not result['result_accepted']
    selected = row['startup_handbacks'][event['tool_use_id']]
    joined = [x for x in row['reconciliation_events'].values() if x['kind'] == 'stop_before_handback_joined']
    assert joined[-1]['value']['admission'] == workers.digest(selected)
    assert row['handback']['notification']['record_sha256'] == workers.digest(note)
    stable = deepcopy(row)
    host.controller.worker(host.state['run'], 'reconcile-handback', revision=host.state['revision'], grant=host.grant)
    after = host.controller.report(diagnostics=False)
    assert after['workers'][host.grant]['handback'] == stable['handback']
    for key in ('decisions', 'visits', 'scope', 'task_results'):
        assert after.get(key) == before.get(key)


@pytest.mark.parametrize('defect', ['missing_ack', 'body', 'terminal_status', 'startup_reports', 'no_report'])
def test_failed_startup_handback_replay_revalidates_original_native_evidence(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    event, _, _ = delivered(host)
    result = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    assert result['state'] == 'failed' and not result['result_accepted']
    target = host.controller._path(); db = json.loads(target.read_text())
    row = db['runs'][host.state['run']]['workers'][host.grant]
    if defect == 'missing_ack':
        row['startup_handbacks'][event['tool_use_id']].pop('acknowledgment')
    elif defect == 'body':
        row['startup_handbacks'][event['tool_use_id']]['envelope']['body_digest'] = '0' * 64
    elif defect == 'terminal_status':
        row['terminal_status'] = 'completed'
    elif defect == 'startup_reports':
        row['startup_handbacks'] = {}
    elif defect == 'no_report':
        row['handback']['status'] = 'no_report'
    host.controller._write(target, db)
    before = target.read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert target.read_bytes() == before
    assert not host.controller.report(diagnostics=False).get('task_results')
