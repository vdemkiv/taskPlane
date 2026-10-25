"""Future-only retention, bounded restart progress and producer publication contracts."""
import json
from pathlib import Path, PureWindowsPath
import nturl2path
import subprocess
import sys
import time

import pytest

from taskplane import snapshot_retention as s
from taskplane.context import Store
from taskplane.tests.test_farm_telemetry import publish, model, catalog, state, measured, requires_collection
from taskplane import flow_telemetry as telemetry


def test_portable_publication_and_registration_preserve_pairs_when_collection_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'supports_collection', lambda: False)
    monkeypatch.setattr(s, 'LEASE_SECONDS', 0)
    monkeypatch.setattr(s, 'MAX_PAIRS', 0)
    monkeypatch.setattr(s, 'MAX_BYTES', 0)
    target = publish(tmp_path, 1, detached=True)
    selected = publish(tmp_path, 2, select=True)
    selection = json.loads(selected.with_suffix('.selection.json').read_text())
    assert Path(selection['snapshot']).exists()
    assert s.register_references(tmp_path, {'artifact': target.as_uri()}, 'portable')['pairs'] == 1
    assert s.register_references(tmp_path, {'note': 'snapshot-v1.json'}, 'prose')['pairs'] == 0
    Store(tmp_path).put('evidence', {'artifact': str(target)})
    unreferenced = publish(tmp_path, 3, detached=True)
    root = tmp_path / '.taskplane'
    originals = {path: path.read_bytes() for path in root.glob('snapshot-*.html')}
    originals.update({path: path.read_bytes() for path in root.glob('snapshot-*.json')})
    result = s.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time()+1000)
    assert result['status'] == 'skipped' and result['removed_pairs'] == 0
    assert 'no-follow' in result['reason']
    assert unreferenced.exists() and all(path.read_bytes() == raw for path, raw in originals.items())
    assert 'portable' in catalog(tmp_path)['pairs'][target.stem]['pins']
    # Automatic publication collection uses the same portable preservation path.
    with s._directory(root) as fd, s._managed(fd, root) as db:
        result = s._collect(fd, root, db, max_pairs=0, max_bytes=0, now=time.time()+1000)
        assert result['status'] == 'skipped' and result['removed_pairs'] == 0
        assert db.execute('SELECT count(*) FROM sweep').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM pairs WHERE deleting=1').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['path', 'uri', 'uri-percent', 'relative', 'foreign', 'traversal', 'foreign-uri', 'malformed'])
def test_windows_native_reference_paths_are_local_and_complete(monkeypatch, kind):
    root = PureWindowsPath('C:/work%20space/project/.taskplane' if kind == 'uri-percent'
                           else 'C:/work space/project/.taskplane')
    name = 'snapshot-' + 'a'*16 + '-' + 'b'*64 + '.html'
    target = root / name
    values = {'path': str(target), 'uri': target.as_uri(), 'uri-percent': target.as_uri(),
              'relative': str(PureWindowsPath('.taskplane') / name),
              'foreign': str(PureWindowsPath('D:/outside/.taskplane') / name),
              'traversal': str(root / '..' / '.taskplane' / name),
              'foreign-uri': (PureWindowsPath('D:/outside/.taskplane') / name).as_uri(),
              'malformed': r'.taskplane\snapshot-0123456789abcdef-bad.html'}
    # Exercise Windows path/URI semantics on every CI host without changing os.name.
    monkeypatch.setattr(s, '_root', lambda workspace: root)
    monkeypatch.setattr(s, 'Path', PureWindowsPath)
    monkeypatch.setattr(s, 'url2pathname', nturl2path.url2pathname)
    if kind in {'path', 'uri', 'uri-percent', 'relative'}:
        assert s.classify_snapshot_references(root.parent, {'artifact': values[kind]}) == {target.stem}
    else:
        with pytest.raises(ValueError):
            s.classify_snapshot_references(root.parent, {'artifact': values[kind]})


def test_benign_snapshot_prose_does_not_block_collection(tmp_path):
    target = publish(tmp_path, detached=True)
    notes = ["Fixture is named snapshot-v1.json; this is not a dashboard artifact reference.",
             "tests/fixtures/dashboard-contract/snapshot-v1.json",
             "single-effective-settings-snapshot-before-root-start", '<p id="snapshot-identity">text</p>']
    for text in notes:
        assert s.register_references(tmp_path, {"note": text}, "note")["pairs"] == 0
    (tmp_path / '.taskplane' / 'note.json').write_text(json.dumps(notes))
    assert s._scan(tmp_path / '.taskplane', references=True)['complete']
    result = s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert result['removed_pairs'] == int(s.supports_collection())
    assert target.exists() is (not s.supports_collection())


def test_raw_writer_requires_registration_and_legacy_bytes_are_unchanged(tmp_path):
    root = tmp_path / '.taskplane'
    root.mkdir()
    legacy = root / ('snapshot-' + 'a'*16 + '-' + 'b'*64)
    legacy.with_suffix('.html').write_bytes(b'old html')
    legacy.with_suffix('.json').write_bytes(b'{"old": true}')
    (root / s.CATALOG).write_bytes(b'{"preserved": "even an invalid legacy catalog"}')
    history = root / 'workflow-history.json'
    history.write_bytes(b'{"approvals":["original"]}')
    originals = {p: p.read_bytes() for p in root.iterdir()}
    protected = publish(tmp_path, 1, detached=True)
    raw_only = publish(tmp_path, 2, detached=True)
    s.register_references(tmp_path, {"artifact": str(protected)}, 'external-writer')
    (root / 'raw.json').write_text(json.dumps([str(protected), str(raw_only)]))
    s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert protected.exists()
    assert raw_only.exists() is (not s.supports_collection())
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert legacy.name not in catalog(tmp_path)['pairs']


@requires_collection
def test_restart_sweep_progress_with_new_publications_and_mid_sweep_pin(tmp_path, monkeypatch):
    targets = [publish(tmp_path, i, detached=True) for i in range(12)]
    pinned = targets[1]
    root = tmp_path / '.taskplane'
    # Historical/unrelated inventory is irrelevant; hot paths may not scan it.
    (root / 'unrelated').mkdir()
    for i in range(100):
        (root / 'unrelated' / str(i)).write_bytes(b'x')
    code = """import json,sys,time
from taskplane import snapshot_retention as s
s.MAX_CANDIDATES=2
s._scan=lambda *a,**k: (_ for _ in ()).throw(AssertionError('scan'))
print(json.dumps(s.collect(sys.argv[1],max_pairs=0,max_bytes=0,now=time.time()+1000)))
"""
    reports = []
    for attempt in range(8):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        reports.append(report)
        assert report['candidates'] <= 2 and report['verified_bytes'] <= s.MAX_VERIFY_BYTES
        if attempt == 0:
            s.pin(tmp_path, pinned, 'arrived-during-sweep')
        # Publications must not reset the old finite watermark. Disable automatic
        # collection here so every observed step is the fresh-process call above.
        with monkeypatch.context() as patch:
            patch.setattr(s, '_collect', lambda *a, **k: {})
            publish(tmp_path, 100+attempt, detached=True)
        if report['status'] == 'complete':
            break
    assert reports[-1]['status'] == 'complete'
    assert len({r['highwater'] for r in reports}) == 1
    assert all(b['cursor'] <= a['cursor'] for a,b in zip(reports,reports[1:]))
    assert pinned.exists() and not targets[0].exists()
    assert all(not t.exists() for t in targets if t != pinned)


@pytest.mark.parametrize('damage', ['marker-missing','marker-corrupt','db-missing','db-corrupt','db-link','journal-link'])
def test_generation_damage_never_rebuilds_deletion_authority(tmp_path, damage):
    target = publish(tmp_path, detached=True)
    root = tmp_path / '.taskplane'
    path = root / (s.MARKER if damage.startswith('marker') else s.MANAGED)
    if damage.endswith('missing'):
        path.unlink()
    elif damage.endswith('corrupt'):
        path.write_bytes(b'broken')
    else:
        if damage == 'journal-link':
            path = root / (s.MANAGED+'-journal')
        else:
            path.unlink()
        external = tmp_path / 'external'
        external.write_bytes(b'untouched')
        path.symlink_to(external)
    assert s.collect(tmp_path, max_pairs=0, now=time.time()+1000)['status'] == 'skipped'
    assert target.exists()
    with pytest.raises((OSError, ValueError, s.sqlite3.Error)):
        publish(tmp_path, 2, detached=True)
    if damage == 'marker-missing':
        assert not (root / s.MARKER).exists()


def test_complete_context_before_splitting_and_pure_preview(tmp_path):
    target = publish(tmp_path, detached=True)
    store = Store(tmp_path)
    value = {'text': 'x'*1020 + str(target) + 'y'*2000}
    before = (tmp_path / '.taskplane' / s.MANAGED).read_bytes()
    store.reference('preview', value)
    assert (tmp_path / '.taskplane' / s.MANAGED).read_bytes() == before
    # Use delimiters so this remains a complete native token in a long string.
    value['text'] = 'x'*1020 + ' ' + str(target) + ' ' + 'y'*10000
    store.put('evidence', value)
    s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert target.exists()


@pytest.mark.parametrize('kind', ['path','uri','markdown','html','encoded','prose','prose-period','structured-prose'])
def test_complete_native_reference_forms_pin(tmp_path, kind):
    target = publish(tmp_path, detached=True)
    values = {'path': {'artifact':str(target)}, 'uri': {'href':target.as_uri()},
              'markdown': f'[view]({target.as_uri()})', 'html':f'<a href="{target.as_uri()}">view</a>',
              'encoded':json.dumps({'artifact':str(target)}),
              'prose':f'See {target.as_uri()}', 'prose-period':f'See {target.as_uri()}.',
              'structured-prose':{'evidence':f'See {target.as_uri()}'}}
    s.register_references(tmp_path, values[kind], 'reference')
    s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert target.exists()


@pytest.mark.parametrize('kind', ['malformed','missing','foreign','traversal','remote','json','depth','bytes','mixed'])
def test_registration_refuses_incomplete_or_unverifiable_input(tmp_path, monkeypatch, kind):
    target = publish(tmp_path, detached=True)
    value = {'artifact':str(target)}
    fmt = 'structured'
    if kind == 'malformed': value = {'artifact':'.taskplane/snapshot-0123456789abcdef-bad.html'}
    if kind == 'missing': value = {'artifact':str(target).replace(target.stem[-64:], 'c'*64)}
    if kind == 'foreign': value = {'artifact':str(tmp_path.parent / '.taskplane' / target.name)}
    if kind == 'traversal': value = {'artifact':str(target.parent / '..' / '.taskplane' / target.name)}
    if kind == 'remote': value = {'href':'https://example.invalid/'+target.name}
    if kind == 'json': value, fmt = '{broken', 'json'
    if kind == 'depth': monkeypatch.setattr(s, 'MAX_SCAN_DEPTH', 0)
    if kind == 'bytes': monkeypatch.setattr(s, 'MAX_REFERENCE_BYTES', 1)
    if kind == 'mixed': value = {'artifact':str(target)+' .taskplane/snapshot-invalid.html'}
    published = tmp_path / 'must-not-publish.json'
    with pytest.raises((ValueError, OSError)):
        s.register_references(tmp_path, value, 'test', format=fmt)
        published.write_text('published')
    assert not published.exists() and target.exists()


@requires_collection
def test_verification_budget_defers_then_verifies_complete_pair(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    amount = target.stat().st_size + target.with_suffix('.json').stat().st_size
    with monkeypatch.context() as patch:
        patch.setattr(s, 'MAX_VERIFY_BYTES', amount-1)
        result = s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
        assert result['reason'] == 'verification_budget' and result['verified_bytes'] == 0
        assert target.exists()
    result = s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert result['removed_pairs'] == 1 and result['verified_bytes'] == amount
    assert s.MAX_FILE_BYTES == 64*1024*1024 and s.MAX_VERIFY_BYTES >= 2*s.MAX_FILE_BYTES


def test_pair_above_four_mib_remains_managed_and_collectable(tmp_path):
    view = model(tmp_path)
    target = s.publish_pair(tmp_path, view, 'x'*(4*1024*1024+1), detached=True)
    assert target.stem in catalog(tmp_path)['pairs']
    assert s.collect(tmp_path, max_pairs=0, now=time.time()+1000)['removed_pairs'] == int(s.supports_collection())
    assert target.exists() is (not s.supports_collection())


@pytest.mark.parametrize('prose', [False, True])
def test_snapshot_and_closure_producers_register_before_publication(tmp_path, prose):
    referenced = publish(tmp_path, 1, detached=True)
    view = model(tmp_path, 2)
    reference = f'See {referenced.as_uri()}' if prose else str(referenced)
    view['evidence'] = reference
    s.publish_pair(tmp_path, view, 'second', detached=True)
    before = state(tmp_path)
    after = dict(before, revision=1, finished=True, artifact=reference)
    telemetry.commit_boundary(tmp_path, before, after, 'finish', measured(1), '2026-09-01T00:02:00Z')
    s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert referenced.exists()
    pins = catalog(tmp_path)['pairs'][referenced.stem]['pins']
    assert 'snapshot-reference' in pins and 'closure-reference' in pins


def test_deletion_proof_failure_or_pin_failure_publishes_nothing(tmp_path, monkeypatch):
    referenced = publish(tmp_path, detached=True)
    referenced.write_bytes(b'tamper')
    store = Store(tmp_path)
    with pytest.raises(ValueError, match='verify'):
        store.put('evidence', {'artifact':str(referenced)})
    assert not (tmp_path / '.taskplane' / 'context-v1').exists()
    assert s.collect(tmp_path, max_pairs=0, now=time.time()+1000)['removed_pairs'] == 0


def test_full_maximum_admitted_pair_fits_one_verification_budget(tmp_path):
    view = model(tmp_path)
    view['padding'] = ''
    overhead = len(s.primitives.canonical_bytes(view, ensure_ascii=True, trailing_newline=True))
    view['padding'] = 'x'*(s.MAX_FILE_BYTES-overhead)
    target = s.publish_pair(tmp_path, view, 'x'*s.MAX_FILE_BYTES, detached=True)
    assert target.stat().st_size == target.with_suffix('.json').stat().st_size == s.MAX_FILE_BYTES
    result = s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert result['removed_pairs'] == int(s.supports_collection())
    if s.supports_collection():
        assert result['verified_bytes'] == result['removed_bytes'] == 2*s.MAX_FILE_BYTES
    else:
        assert result['status'] == 'skipped'
        assert target.exists() and target.with_suffix('.json').exists()


def test_owned_query_plans_and_vm_cap_are_bounded(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    root = tmp_path / '.taskplane'
    with s._directory(root) as fd, s._managed(fd, root) as db:
        for query, args in [
            ('SELECT * FROM pairs WHERE seq<? AND seq<=? ORDER BY seq DESC LIMIT 1',(100,100)),
            ('SELECT * FROM pairs WHERE deleting=1 ORDER BY seq LIMIT 1',()),
            ('SELECT * FROM pairs WHERE content_key=? AND deleting=0 LIMIT 1',('key',)),
            ('SELECT 1 FROM protections WHERE name=? LIMIT 1',(target.stem,))]:
            plans = db.execute('EXPLAIN QUERY PLAN '+query,args).fetchall()
            assert all('SCAN ' not in row['detail'] for row in plans)
    monkeypatch.setattr(s, 'MAX_SQL_STEPS', 0)
    assert s.collect(tmp_path, max_pairs=0, now=time.time()+1000)['status'] == 'skipped'
    assert target.exists()


def test_pin_commit_failure_and_selection_crash_preserve_targets(tmp_path, monkeypatch):
    selected = publish(tmp_path, 1, select=True)
    old = Path(json.loads(selected.with_suffix('.selection.json').read_text())['snapshot'])
    original_write = s._write
    def fail_selection(fd,name,raw,**kwargs):
        if name.endswith('.selection.json'):
            raise OSError('selection crash')
        return original_write(fd,name,raw,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(s,'_write',fail_selection)
        with pytest.raises(OSError,match='selection crash'):
            publish(tmp_path,2,select=True)
    s.collect(tmp_path,max_pairs=0,now=time.time()+1000)
    assert old.exists()
    # The new pending pair stays protected even if the next publication fails.
    pending = set(catalog(tmp_path)['pairs']) - {old.stem}
    with monkeypatch.context() as patch:
        patch.setattr(s,'_write',fail_selection)
        with pytest.raises(OSError): publish(tmp_path,3,select=True)
    s.collect(tmp_path,max_pairs=0,now=time.time()+1000)
    assert all((tmp_path/'.taskplane'/(name+'.html')).exists() for name in pending)


def test_evidence_preview_is_pure_and_sealing_pins_complete_artifact(tmp_path):
    from taskplane import workflow_evidence as evidence
    from taskplane.tests.test_workflow_evidence import prepare
    state_value, _, _ = prepare(tmp_path)
    output, tasks = "product.json", "tasks.json"
    target = publish(tmp_path, detached=True)
    output_path = tmp_path / output
    data = json.loads(output_path.read_text())
    data['snapshot_evidence'] = str(target)
    output_path.write_text(json.dumps(data))
    before = (tmp_path/'.taskplane'/s.MANAGED).read_bytes()
    packet = evidence.prevalidate(tmp_path,state_value,output,tasks)
    assert target.stem in packet['snapshot_references']
    assert (tmp_path/'.taskplane'/s.MANAGED).read_bytes() == before
    evidence.seal(tmp_path,state_value,output,tasks)
    s.collect(tmp_path,max_pairs=0,now=time.time()+1000)
    assert target.exists() and 'sealed-evidence' in catalog(tmp_path)['pairs'][target.stem]['pins']


def test_opaque_binary_attachment_and_candidate_decoding(tmp_path):
    assert s.register_references(tmp_path,b'\xff\x00binary','attachment',format='text')['pairs'] == 0
    target = publish(tmp_path,detached=True)
    with pytest.raises(ValueError,match='Undecodable'):
        s.register_references(tmp_path,b'\xff'+target.name.encode(),'attachment',format='text')
    with pytest.raises(ValueError):
        s.register_references(tmp_path,b'\xff','artifact',format='json')
    s.register_references(tmp_path,'Read '+target.name+'.','prose',format='text')
    assert 'prose' in catalog(tmp_path)['pairs'][target.stem]['pins']


@requires_collection
def test_deleting_pair_cannot_be_republished_or_pinned(tmp_path, monkeypatch):
    target = publish(tmp_path,detached=True)
    unlink = s.os.unlink
    def fail_json(name,*args,**kwargs):
        if name == target.with_suffix('.json').name: raise OSError('crash')
        return unlink(name,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(s.os,'unlink',fail_json)
        s.collect(tmp_path,max_pairs=0,now=time.time()+1000)
    assert not target.exists()
    with pytest.raises(ValueError,match='deleting'):
        publish(tmp_path,detached=True)
    with pytest.raises(ValueError,match='verify'):
        s.pin(tmp_path,target,'too-late')
    assert not target.exists()


@pytest.mark.parametrize('body',[[], {'files':[]}, {'files':{'bad':1}}])
def test_malformed_ownership_preserves_pair(tmp_path,body):
    target = publish(tmp_path,detached=True)
    root = tmp_path/'.taskplane'
    with s._directory(root) as fd,s._managed(fd,root) as db:
        db.execute('UPDATE pairs SET item=?',(json.dumps(body),))
        db.commit()
    assert s.collect(tmp_path,max_pairs=0,now=time.time()+1000)['status'] == 'skipped'
    assert target.exists()


def test_legacy_catalog_links_remain_readable_without_adoption(tmp_path):
    from taskplane.tests.test_farm_telemetry import frozen_snapshot_index
    current, artifact, _ = frozen_snapshot_index(tmp_path)
    root = tmp_path/'.taskplane'
    old_item = catalog(tmp_path)['pairs'][artifact.stem]
    old_item.update(order=1,pins=['legacy-reason-'+str(i) for i in range(40)])
    # An isolated fixture for the exact pre-generation storage format.
    (root/s.MANAGED).unlink()
    (root/s.MARKER).unlink()
    old_catalog = {'schema':s.SCHEMA,'workspace':str(tmp_path),'pairs':{artifact.stem:old_item},
                   'selected':{},'sequence':1}
    (root/s.CATALOG).write_text(json.dumps(old_catalog))
    before = (root/s.CATALOG).read_bytes(),artifact.read_bytes(),artifact.with_suffix('.json').read_bytes()
    publish(tmp_path,99,detached=True)
    s.collect(tmp_path,max_pairs=0,now=time.time()+1000)
    links = s.final_snapshot_links(str(tmp_path),'root','old',current['final_snapshots'])
    assert links and links[0]['href'] == artifact.as_uri()
    assert artifact.stem not in catalog(tmp_path)['pairs']
    assert before == ((root/s.CATALOG).read_bytes(),artifact.read_bytes(),artifact.with_suffix('.json').read_bytes())


def test_large_supported_reference_payload_keeps_late_pin(tmp_path):
    target = publish(tmp_path, detached=True)
    value = [0] * 110000 + [{"artifact": str(target)}]
    assert len(json.dumps(value).encode()) < s.MAX_REFERENCE_BYTES
    assert s.classify_snapshot_references(tmp_path, value) == {target.stem}
    s.register_references(tmp_path, value, "large complete input")
    s.collect(tmp_path, max_pairs=0, now=time.time()+1000)
    assert target.exists()


@pytest.mark.parametrize("value", [[0]*33, {"k"*32: 0}, [[], [], []]*11])
def test_reference_budget_counts_keys_scalars_and_containers(tmp_path, monkeypatch, value):
    monkeypatch.setattr(s, "MAX_REFERENCE_BYTES", 32)
    with pytest.raises(ValueError, match="byte limit"):
        s.classify_snapshot_references(tmp_path, value)


def test_reference_cycle_and_unsupported_values_refuse(tmp_path):
    cycle = []
    cycle.append(cycle)
    for value in [cycle, {"value": object()}, {1: "value"}]:
        with pytest.raises(ValueError):
            s.classify_snapshot_references(tmp_path, value)
    shared = ["plain"]
    assert s.classify_snapshot_references(tmp_path, [shared, shared]) == set()


def test_text_and_json_byte_admission_precede_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "MAX_REFERENCE_BYTES", 32)
    assert s.classify_snapshot_references(tmp_path, "x"*32, format="text") == set()
    with pytest.raises(ValueError, match="byte limit"):
        s.classify_snapshot_references(tmp_path, " "*33, format="json")
    with pytest.raises(ValueError, match="byte limit"):
        s.classify_snapshot_references(tmp_path, "é"*17)
