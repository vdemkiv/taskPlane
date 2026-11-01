"""Real context object and transport boundaries, including hostile/missing data."""
from pathlib import Path
import json

import pytest

from taskplane.context import Store, digest, encode, OBJECT_LIMIT, PAGE_LIMIT
from taskplane.context_views import view, summary, PHASE_BYTES
from taskplane.workflow import Refusal


@pytest.mark.parametrize('value', [
    {'criteria': ['A'], 'text': 'unchanged'},
    '界λ' * 10000,
    [{'id': str(i), 'body': 'retain exact body ' * 50} for i in range(120)],
    {str(i): {'body': 'value' * 100, 'number': i} for i in range(150)},
], ids=['small-object', 'large-unicode', 'list-pages', 'mapping-pages'])
def test_immutable_tree_roundtrip_and_explicit_pages(tmp_path, value):
    store = Store(tmp_path)
    ref = store.put('fixture', value, 'same-source')
    assert store.put('fixture', value, 'same-source') == ref
    assert store.resolve(ref) == value
    children = store.descendants(ref)
    assert ref['sha256'] in children
    for key in children:
        page = store.page(key)
        assert len(encode(page)) <= PAGE_LIMIT
        assert page['untrusted_data'] is True
        assert store.path(key).stat().st_size <= OBJECT_LIMIT
    before = store.path(ref['sha256']).read_bytes()
    store.path(ref['sha256']).write_bytes(before + b' ')
    with pytest.raises(Refusal, match='digest'):
        store.resolve(ref)


def test_reference_metadata_missing_paths_and_symlinks(tmp_path):
    store = Store(tmp_path)
    ref = store.put('requirements', {'must': 'retain'})
    for field, replacement in [('kind', 'source'), ('bytes', 1), ('source_key', 'foreign')]:
        with pytest.raises(Refusal, match='metadata'):
            store.resolve({**ref, field: replacement})
    for key in ['../escape', '/tmp/source', 'a'*63, 'A'*64]:
        with pytest.raises(Refusal):
            store.node(key)
    store.path(ref['sha256']).unlink()
    with pytest.raises(Refusal):
        store.resolve(ref)
    store.path(ref['sha256']).symlink_to(tmp_path/'external')
    with pytest.raises(Refusal):
        store.resolve(ref)
    other = tmp_path/'other'
    other.mkdir()
    (other/'.taskplane').symlink_to(tmp_path/'.taskplane', target_is_directory=True)
    with pytest.raises(Refusal):
        Store(other)


def test_unknown_section_and_page_never_fall_back(tmp_path):
    store = Store(tmp_path)
    ref = store.put('source', {'a': 'exact', 'b': 'other'})
    assert store.page(ref['sha256'], section='a')['data'] == {'a': 'exact'}
    for options in [{'section': 'absent'}, {'page': -1}, {'page': 9}, {'page': True}]:
        with pytest.raises(Refusal):
            store.page(ref['sha256'], **options)


def test_large_small_entry_collection_uses_pages_not_one_read_per_item(tmp_path):
    values = [{'check': f'fixture-{i}', 'observations': {'status': 'pass', 'detail': ['retained', 'é']}}
              for i in range(3000)]
    store = Store(tmp_path)
    reference = store.put('check-records', values)
    assert store.resolve(reference) == values
    assert len(store.descendants(reference)) < 100


@pytest.mark.parametrize('phase', PHASE_BYTES)
def test_each_phase_bounds_context_and_retains_required_refs(tmp_path, phase):
    store = Store(tmp_path)
    inputs = [{'id': 'requirements', 'body': {'AC1': 'Retain the failed check'}},
              {'id': 'blocker', 'body': {'status': 'fail', 'detail': 'large ' * 40000}},
              {'id': 'duplicate', 'body': {'AC1': 'Retain the failed check'}}]
    result = view(store, {'run': 'one', 'visit': phase}, phase, ['T1'], ['AC1'],
                  {'decision': 'not_requested'}, inputs, {'graph': 'complete'})
    assert len(encode(result)) <= PHASE_BYTES[phase]
    assert result['digest'] == digest({k:v for k,v in result.items() if k != 'digest'})
    refs = store.resolve(result['required_inputs']['details'])
    assert len(refs) == 3
    assert store.resolve(next(r['ref'] for r in refs if r['id'] == 'blocker'))['status'] == 'fail'
    assert result['omissions'][0]['count'] >= 1
    assert len([v for v in result['inline'].values() if v == {'AC1': 'Retain the failed check'}]) == 1
    assert result == view(store, {'run': 'one', 'visit': phase}, phase, ['T1'], ['AC1'],
                          {'decision': 'not_requested'}, inputs, {'graph': 'complete'})


def test_summary_retains_blocking_spine_under_growth(tmp_path):
    store = Store(tmp_path)
    payload = {'status': 'blocked', 'reason': 'approval_required', 'detail': 'A'*50000,
               'run': 'run', 'root': 'root', 'workspace': str(tmp_path), 'revision': 3, 'index': 0,
               'scope': {'criteria': ['AC1']},
               'phase': 'design', 'visits': [{'id': 'v', 'decision': 'awaiting_human_approval'}],
               'pending_checkpoint': {'checkpoint': 'exact', 'revision': 3},
               'source_baseline': {f'file-{i}': 'digest' for i in range(10000)},
               'history': [{'checkpoint': i, 'body': 'details'*1000} for i in range(100)]}
    result = summary(store, payload, 'advance')
    assert len(encode(result)) <= 16384
    assert result['binding']['pending_checkpoint'] == payload['pending_checkpoint']
    assert result['errors']['blocking'] and result['errors']['reason'] == 'approval_required'
    assert result['errors']['total'] == 1
    assert store.resolve(result['details']) == payload
    assert store.resolve(result['errors']['details']) == [payload['detail']]
    assert 'source_baseline' not in result


def test_summary_preserves_legacy_and_graph_coverage(tmp_path):
    payload = {'status': 'legacy_unverified', 'phase': 'product',
               'workflow': {'status': 'no_workflow'}, 'graph': {'status': 'legacy unverified'}}
    result = summary(Store(tmp_path), payload, 'report')
    assert result['status'] == 'legacy_unverified' and result['phase'] == 'product'
    assert result['coverage']['graph'] == 'legacy unverified'
    assert 'Initialize' in result['next_action']


@pytest.mark.parametrize('crash_point', ['create', 'synced'])
def test_interrupted_object_write_can_retry(tmp_path, crash_point):
    import os
    import subprocess
    import sys

    script = '''import os,sys
from pathlib import Path
from taskplane.context import Store
real_open=os.open
real_sync=os.fsync
def crash(path, flags, *args, **kwargs):
    fd=real_open(path, flags, *args, **kwargs)
    if sys.argv[2]=="create" and flags & os.O_EXCL and str(Path(path).parent).endswith("context-v1/objects"):
        os._exit(73)
    return fd
def crash_sync(fd):
    real_sync(fd)
    if sys.argv[2]=="synced": os._exit(73)
os.fsync=crash_sync
os.open=crash
Store(Path(sys.argv[1]))._put("crash", {"value": "complete"})
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path), crash_point],
                            timeout=10, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 73
    store = Store(tmp_path)
    ref = store._put('crash', {'value': 'complete'})
    assert store.resolve(ref) == {'value': 'complete'}
    assert all(path.stat().st_size for path in store.path(ref['sha256']).parent.glob('*.json'))


def test_object_publication_syncs_bytes_and_directory_ancestry(tmp_path, monkeypatch):
    import os
    from taskplane import primitives

    events = []
    original_fsync, original_link = os.fsync, os.link
    monkeypatch.setattr(os, 'fsync', lambda fd: (events.append('file-sync'), original_fsync(fd))[1])
    def link(source, target, **kwargs):
        assert Path(source).read_bytes()
        assert 'file-sync' in events
        events.append('publish')
        return original_link(source, target, **kwargs)
    monkeypatch.setattr(os, 'link', link)
    monkeypatch.setattr(primitives, '_fsync_directory', lambda path: events.append(Path(path)))
    store = Store(tmp_path)
    ref = store._put('sync', {'complete': True})
    parent = store.path(ref['sha256']).parent
    assert events == ['file-sync', 'publish', parent, parent.parent, parent.parent.parent, tmp_path]


def test_object_concurrent_winner_is_validated(tmp_path, monkeypatch):
    import os

    real_link = os.link
    def winner(source, target, **kwargs):
        real_link(source, target, **kwargs)
        raise FileExistsError(str(target))
    monkeypatch.setattr(os, 'link', winner)
    store = Store(tmp_path)
    ref = store._put('winner', {'value': 1})
    assert store.resolve(ref) == {'value': 1}
    store.path(ref['sha256']).write_bytes(b'corrupt')
    with pytest.raises(Refusal, match='digest'):
        store._put('winner', {'value': 1})


def test_object_publish_failure_never_exposes_partial_digest(tmp_path, monkeypatch):
    import errno
    import os

    def unsupported(*args, **kwargs):
        raise OSError(errno.ENOTSUP, 'atomic publication unsupported')
    store = Store(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'link', unsupported)
        with pytest.raises(OSError, match='publication unsupported'):
            store._put('unsupported', {'value': 1})
    assert not list((store.root / 'objects').glob('*.json'))
    assert not list((store.root / 'objects').glob('*.tmp'))
    ref = store._put('unsupported', {'value': 1})
    assert store.resolve(ref) == {'value': 1}


def test_object_existing_publication_retries_directory_sync(tmp_path, monkeypatch):
    from taskplane import primitives

    store = Store(tmp_path)
    with monkeypatch.context() as patch:
        def fail(path):
            raise OSError('directory sync interrupted')
        patch.setattr(primitives, '_fsync_directory', fail)
        with pytest.raises(OSError, match='directory sync interrupted'):
            store._put('retry-sync', {'value': 1})
    paths = []
    monkeypatch.setattr(primitives, '_fsync_directory', lambda path: paths.append(Path(path)))
    ref = store._put('retry-sync', {'value': 1})
    assert store.resolve(ref) == {'value': 1}
    assert paths == [store.root / 'objects', store.root, store.root.parent, tmp_path]
