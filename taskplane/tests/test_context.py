"""Real context object and transport boundaries, including hostile/missing data."""
from pathlib import Path
import json

import pytest

from taskplane.context import Store, digest, encode, OBJECT_LIMIT, PAGE_LIMIT
from taskplane.context_views import summary
from taskplane.workflow import Refusal


def test_killed_publication_never_leaves_a_partial_canonical_object(tmp_path):
    import subprocess
    import sys
    script = ("import os; from pathlib import Path; from taskplane.context import Store; "
              "os.replace=lambda *a: os._exit(41); Store(Path(" + repr(str(tmp_path)) + ")).put('fixture', {'answer':42})")
    result = subprocess.run([sys.executable, '-B', '-c', script], check=False)
    assert result.returncode == 41
    objects = tmp_path / '.taskplane/context-v1/objects'
    assert not list(objects.glob('*.json'))
    store = Store(tmp_path)
    reference = store.put('fixture', {'answer': 42})
    assert store.resolve(reference) == {'answer': 42}


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


@pytest.mark.parametrize('observation', ['initialization_pending', 'initialization_stale', 'unavailable', 'active'])
@pytest.mark.parametrize('status, next_action', [
    ('not_requested', 'complete phase evidence'),
    ('awaiting_human_approval', 'human decision'),
    ('approved', 'Advance to the next authorized phase'),
])
def test_summary_callback_observation_cannot_block_root_progress(tmp_path, observation, status, next_action):
    payload = {'status': status, 'phase': 'build',
               'harness': {'status': observation, 'run_created': True,
                           'parent_hook': {'status': 'missing', 'detail': 'No matching callback pair observed.'}}}
    result = summary(Store(tmp_path), payload, 'report')
    assert result['status'] == status and next_action in result['next_action']
    assert result['errors']['blocking'] is False and result['errors']['reason'] is None
    assert result['errors']['total'] == 0
    assert result['initialization']['parent_hook'] == payload['harness']['parent_hook']
    assert 'advisory for root work' in result['initialization']['detail']
    assert 'native workers still require readiness' in result['initialization']['detail']


@pytest.mark.parametrize('reason', ['approval_required', 'native_readiness', 'scope_violation'])
def test_summary_callback_observation_preserves_actual_controller_refusal(tmp_path, reason):
    payload = {'status': 'blocked', 'reason': reason, 'detail': 'Actual controller refusal.',
               'harness': {'status': 'unavailable', 'run_created': True,
                           'parent_hook': {'status': 'missing'}}}
    store = Store(tmp_path)
    result = summary(store, payload, 'report')
    assert result['errors']['blocking'] is True and result['errors']['reason'] == reason
    assert result['next_action'] == 'Resolve the reported refusal before continuing.'
    assert result['errors']['total'] == 1 and result['errors']['items'] == []
    assert store.resolve(result['errors']['details']) == ['Actual controller refusal.']
