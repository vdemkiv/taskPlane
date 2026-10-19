"""Analysis recording is optional, terminal-only and separate from authority."""
from copy import deepcopy
import json

import pytest

from taskplane import flow, run_journal, workflow as w
from taskplane.tests.test_workflow_local import setup


def test_record_refuses_live_run_and_does_not_change_state(tmp_path):
    c, state = setup(tmp_path)
    with pytest.raises(w.Refusal, match='ended'):
        run_journal.record(tmp_path, state)
    assert c.report(diagnostics=False)['revision'] == state['revision']
    assert not list((tmp_path / '.taskplane').glob('run-record-*.json'))
    assert not (tmp_path / flow.JOURNAL).exists()


def test_ended_record_is_optional_idempotent_and_observation_only(tmp_path):
    c, state = setup(tmp_path)
    ended = deepcopy(state)
    ended.update(retired={'reason': 'user stopped this run'})
    first = run_journal.record(tmp_path, ended, note='Useful lesson')
    second = run_journal.record(tmp_path, ended, note='Retry does not duplicate it')
    assert second == first and first['authority'] == 'analysis_only'
    assert len(list((tmp_path / '.taskplane').glob('run-record-*.json'))) == 1
    assert c.report(diagnostics=False)['revision'] == state['revision']


def test_record_failure_does_not_modify_terminal_outcome(tmp_path, monkeypatch):
    _, state = setup(tmp_path)
    state['finished'] = True
    before = json.dumps(state, sort_keys=True)
    def fail(*args):
        raise OSError('Analysis storage unavailable')
    monkeypatch.setattr(run_journal.primitives, 'atomic_json', fail)
    with pytest.raises(OSError):
        run_journal.record(tmp_path, state)
    assert json.dumps(state, sort_keys=True) == before


def test_later_record_keeps_actual_finished_outcome_after_source_changes(tmp_path, capsys):
    from taskplane.tests.test_workflow_local import submit, decide
    c, state = setup(tmp_path, standalone=True)
    state = decide(c, submit(c, state))
    state = c.apply('finish', state['run'], expected_revision=state['revision'])
    (tmp_path / 'app.py').write_text('value = 999\n')
    assert c.report(state['run'], diagnostics=False).get('invalidation_pending')
    before = c._path().read_bytes()
    capsys.readouterr()
    assert flow.main(['report', '--workspace', str(tmp_path), '--run', state['run'], '--record', '--full'],
                     compact=True, governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['outcome'] == 'finished' and result['finished_at'] == state['finished_at']
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_public_recording_is_post_run_optional_and_ignores_bad_legacy_journal(tmp_path, host):
    from taskplane.tests.test_native_workflow_cli import create, cli
    create(tmp_path)
    state = cli(tmp_path, host, 'start', '--standalone', '--phase', 'product',
                '--scope', '.taskplane/scope.json', '--request-reference', 'fixture/record')['workflow']
    assert not (tmp_path / flow.JOURNAL).exists()
    refused = cli(tmp_path, host, 'report', '--run', state['run'], '--record', code=2)
    assert refused['reason'] == 'state_unavailable'
    state = cli(tmp_path, host, 'retire', '--run', state['run'],
                '--expected-revision', str(state['revision']),
                '--request-reference', 'fixture/retire', '--note', 'Actual fixture user ended run')['workflow']
    (tmp_path / flow.JOURNAL).write_bytes(b'\xff\n')
    first = cli(tmp_path, host, 'report', '--run', state['run'], '--record', '--note', 'Lesson')
    assert first['schema'] == run_journal.SCHEMA and first['outcome'] == 'retired'
    assert first == cli(tmp_path, host, 'report', '--run', state['run'], '--record')
    assert len(list((tmp_path / '.taskplane').glob('run-record-*.json'))) == 1
    assert (tmp_path / flow.JOURNAL).read_bytes() == b'\xff\n'
