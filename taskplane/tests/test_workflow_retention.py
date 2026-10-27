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


@pytest.mark.parametrize('adapter', ['claude', 'codex'])
@pytest.mark.parametrize('large,native_name', [(False, True), (True, True), (True, False)])
def test_start_with_backup_inventory_at_production_watermark(tmp_path, adapter, large, native_name):
    from taskplane.context import encode
    workspace = tmp_path.resolve()
    backup = workspace / '.taskplane-backup'
    backup.mkdir()
    name = 'snapshot-' + 'a'*16 + '-' + 'b'*64 if native_name else 'ordinary-backup'
    for suffix in ('.html', '.json'):
        (backup / (name + suffix)).write_text('{}')
    scope = {'criteria': ['START'], 'paths': {phase: [] for phase in w.PHASES}}
    c = h.Controller(workspace, 'fixture-root', h.installed_adapter(adapter))
    first = c.start({'scope': scope, 'request_reference': 'fixture-first'})
    retired = c.apply('retire', first['run'], expected_revision=0,
        native_reference=json.dumps({'request_reference': 'fixture-retire', 'reason': 'historical run'}))
    db = c._read(c._path())
    assert retention.WATERMARK == 4 * 1024 * 1024
    if large:
        db['startup_size_fixture'] = 'x' * retention.WATERMARK
        c._path().write_bytes(encode(db))
    assert (len(c._path().read_bytes()) >= retention.WATERMARK) is large
    request = {'scope': scope, 'request_reference': 'fixture-second'}
    second = c.start(request)
    assert c.start(request)['run'] == second['run']
    db = c._read(c._path())
    assert db['active'] == second['run']
    assert set(db['runs']) | set(db.get('archives', {})) == {first['run'], second['run']}
    assert not set(db['runs']) & set(db.get('archives', {}))
    assert (first['run'] in db.get('archives', {})) is large
    original = retention.read(workspace, db, first['run'])
    assert original == retired
    assert original['source_baseline'] == first['source_baseline']
    assert original['decisions'] == first['decisions']
    assert all((backup / (name + suffix)).read_text() == '{}' for suffix in ('.html', '.json'))


@pytest.mark.parametrize('failure', [None, 'missing-reference', 'index-write', 'archive-mismatch'])
def test_archive_preserves_approved_history_and_failed_index_bytes(tmp_path, monkeypatch, failure):
    from taskplane import primitives
    from taskplane.context import Store, encode
    from taskplane.tests.test_workflow_local import setup, submit, decide
    c, first = setup(tmp_path)
    approved = decide(c, submit(c, first))
    retired = c.apply('retire', first['run'], expected_revision=approved['revision'],
        native_reference=json.dumps({'request_reference': 'fixture-retire', 'reason': 'historical run'}))
    db = c._read(c._path())
    db['startup_size_fixture'] = 'x' * retention.WATERMARK
    missing = str(tmp_path.resolve() / '.taskplane' / ('snapshot-' + 'a'*16 + '-' + 'c'*64 + '.html'))
    if failure == 'missing-reference':
        db['runs'][first['run']]['artifact'] = missing
    c._path().write_bytes(encode(db))
    before = c._path().read_bytes()
    if failure == 'index-write':
        atomic_json = primitives.atomic_json
        def failed_write(path, *args, **kwargs):
            if path == c._path():
                raise OSError('fixture index write failed')
            return atomic_json(path, *args, **kwargs)
        monkeypatch.setattr(primitives, 'atomic_json', failed_write)
    elif failure == 'archive-mismatch':
        monkeypatch.setattr(Store, 'resolve', lambda *args: {})
    request = {'scope': first['scope'], 'request_reference': 'fixture-second'}
    if failure:
        with pytest.raises(w.Refusal) as error:
            c.start(request)
        message = str(error.value)
        if failure == 'index-write':
            assert 'fixture index write failed' in message
        else:
            assert 'History archive failed for run ' + first['run'] in message
            assert ('Snapshot pin cannot read ' + missing if failure == 'missing-reference'
                    else 'History archive verification failed') in message
        assert c._path().read_bytes() == before
    else:
        second = c.start(request)
        archived = retention.read(c.workspace, c._read(c._path()), first['run'])
        assert encode(archived) == encode(retired)
        assert archived['decisions'] == approved['decisions'] and archived['decisions']
        assert archived['visits'] == retired['visits']
        archive_before = c._read(c._path())['archives']
        assert c.start(request)['run'] == second['run']
        assert c._read(c._path())['archives'] == archive_before
