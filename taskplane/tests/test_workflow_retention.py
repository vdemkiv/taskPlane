"""History retention and retirement never create checkpoint acceptance."""
from copy import deepcopy
import json
import pytest
from taskplane import workflow as w, workflow_host as h, workflow_retention as retention
from taskplane.tests.test_workflow_evidence import prepare


def controller(tmp_path):
    state, _, _ = prepare(tmp_path)
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    s = c.start({'scope': state['scope'], 'request_reference': 'actual-fixture-request'})
    return c, s


def test_retire_preserves_unaccepted_output_and_rejects_stale_revision(tmp_path):
    c, s = controller(tmp_path)
    note = json.dumps({'request_reference': 'fixture-user-retire', 'reason': 'obsolete run'})
    with pytest.raises(w.Refusal, match='revision'):
        c.apply('retire', s['run'], expected_revision=99, native_reference=note)
    with pytest.raises(w.Refusal, match='reference'):
        c.apply('retire', s['run'], expected_revision=0, native_reference='{}')
    retired = c.apply('retire', s['run'], expected_revision=0, native_reference=note)
    assert retired['decisions'] == s['decisions'] == {}
    assert not retired['finished'] and retired['visits'] == s['visits']
    assert c.report(s['run'])['status'] == 'retired'
    assert c.report()['status'] == 'no_workflow'
    with pytest.raises(w.Refusal, match='retired'):
        c.guard({'tool_name': 'exec_command', 'tool_input': {'cmd': 'touch app.py'}}, s['run'])


def test_retention_roundtrip_tamper_and_failed_index_write(tmp_path, monkeypatch):
    c, first = controller(tmp_path)
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    second = c.start({'scope': first['scope'], 'request_reference': 'new-user-request',
                      'replace_run': first['run'], 'expected_revision': 0})
    db = c._read(c._path())
    assert list(db['runs']) == [second['run']]
    assert first['run'] in db['archives']
    old = c.report(first['run'])
    assert old['archived'] and old['superseded_by'] == second['run']
    assert old['decisions'] == first['decisions'] and not old['finished']
    assert c.start({'scope': first['scope'], 'request_reference': 'new-user-request',
                    'replace_run': first['run'], 'expected_revision': 0})['run'] == second['run']
    ref = db['archives'][first['run']]
    from taskplane.context import Store
    Store(tmp_path).path(ref['sha256']).write_text('{}')
    with pytest.raises(w.Refusal, match='digest'):
        c.report(first['run'])
    # Archive corruption never promotes history to active grants.
    assert c.report()['run'] == second['run']


def test_archive_failure_keeps_original_index(tmp_path, monkeypatch):
    c, s = controller(tmp_path)
    before = c._path().read_bytes()
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    from taskplane.context import Store
    monkeypatch.setattr(Store, 'put', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(w.Refusal, match='disk full'):
        c.start({'scope': s['scope'], 'request_reference': 'next', 'replace_run': s['run'], 'expected_revision': 0})
    assert c._path().read_bytes() == before


def test_active_and_protected_history_never_auto_archive(tmp_path, monkeypatch):
    c, s = controller(tmp_path)
    db = c._read(c._path())
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    assert retention.compact(tmp_path, db) == db
    protected = deepcopy(db); protected['profile'] = 'protected_host'
    protected['active'] = None; protected['runs'][s['run']]['superseded_by'] = 'new'
    assert retention.compact(tmp_path, protected) == protected
