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
])
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
