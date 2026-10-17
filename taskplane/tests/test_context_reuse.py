"""Matching provenance reuses observations; every relevant change invalidates it."""
import sys
from pathlib import Path
from copy import deepcopy
import pytest
from taskplane import depgraph
from taskplane.context import Store
from taskplane.context_reuse import key, record, lookup, run_check, inherited, contribution_key, contribution_matches


def fixture(tmp_path):
    for name in ('pkg', 'tests', 'unrelated'): (tmp_path/name).mkdir()
    (tmp_path/'pkg/app.py').write_text('value = 3\n')
    (tmp_path/'tests/check.py').write_text('from pkg.app import value\nassert value == 3\n')
    (tmp_path/'unrelated/other.py').write_text('value = 8\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    options = dict(paths=['pkg/app.py'], tests=['tests/check.py'], criteria=['AC1'],
                   command=[sys.executable, '-c', 'exec(open("tests/check.py").read())'],
                   tool='fixture-python', contract='pricing/v1')
    return options


def test_actual_check_reuse_and_unrelated_edit(tmp_path):
    options = fixture(tmp_path)
    before = key(tmp_path, **options)
    assert before['eligible']
    ref = run_check(tmp_path, before, producer='fixture-build')
    result = lookup(Store(tmp_path), ref, key(tmp_path, **options))
    assert result['status'] == 'reused' and result['approval'] == 'not_transferred'
    (tmp_path/'unrelated/new.py').write_text('other = True\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    assert key(tmp_path, **options) == before
    assert inherited(tmp_path, ref)['status'] == 'reused'


@pytest.mark.parametrize('change', ['source', 'test', 'added', 'deleted', 'criteria',
                                    'command', 'tool', 'contract', 'environment', 'runtime'])
def test_relevant_changes_never_reuse(tmp_path, change):
    options = fixture(tmp_path)
    before = key(tmp_path, **options, environment={'LANG': 'C'})
    ref = record(Store(tmp_path), before, status='pass', producer='fixture', result={'returncode': 0})
    env = {'LANG': 'C'}
    if change in ('source', 'test'):
        target = 'pkg/app.py' if change == 'source' else 'tests/check.py'
        (tmp_path/target).write_text('changed = True\n')
    elif change == 'added': (tmp_path/'pkg/extra.py').write_text('new = 1\n')
    elif change == 'deleted': (tmp_path/'pkg/app.py').unlink()
    elif change == 'criteria': options['criteria'] = ['AC2']
    elif change == 'command': options['command'] = [sys.executable, '-c', 'print(3)']
    elif change == 'environment': env['LANG'] = 'fr_FR'
    elif change == 'runtime': options['command'][0] = '/missing/runtime'
    else: options[change] = 'changed'
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    current = key(tmp_path, **options, environment=env)
    assert lookup(Store(tmp_path), ref, current)['status'] == 'miss'


@pytest.mark.parametrize('status', ['fail', 'unknown'])
def test_negative_and_incomplete_evidence_preserves_findings(tmp_path, status):
    options = fixture(tmp_path); provenance = key(tmp_path, **options)
    finding = {'id': 'blocker', 'severity': 'blocking', 'contract': 'retain-exact'}
    ref = record(Store(tmp_path), provenance, status=status, producer='fixture',
                 result={'returncode': 1}, findings=[finding])
    result = lookup(Store(tmp_path), ref, provenance)
    assert result['status'] == 'miss' and result['findings'] == [finding]
    changed = deepcopy(provenance); changed['eligible'] = False
    assert lookup(Store(tmp_path), ref, changed)['status'] == 'miss'


def test_missing_or_stale_graph_is_not_eligible(tmp_path):
    options = fixture(tmp_path)
    (tmp_path/'pkg/app.py').write_text('value = 9\n')
    assert key(tmp_path, **options)['eligible'] is False


@pytest.mark.parametrize('transitive', [False, True])
def test_changed_external_dependency_at_same_path_never_reuses(tmp_path, monkeypatch, transitive):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    library = tmp_path/'runtime-library'; library.mkdir()
    dependency = library/'external_rules.py'
    dependency.write_text('value = 3\n')
    monkeypatch.setenv('PYTHONPATH', str(library))
    options = fixture(workspace)
    if transitive:
        (workspace/'pkg/rules.py').write_text('from external_rules import value\n')
        (workspace/'pkg/app.py').write_text('from pkg.rules import value\n')
    else:
        (workspace/'pkg/app.py').write_text('from external_rules import value\n')
    depgraph.scan(str(workspace), decompose=True, strict=True)
    before = key(workspace, **options)
    assert not before['eligible'] and before['coverage'] == 'unknown'
    assert 'ext:external_rules' in before['unverified_dependencies']
    prior = run_check(workspace, before, producer='external-dependency-regression')
    assert Store(workspace).resolve(prior)['status'] == 'pass'
    # Different length also invalidates Python's timestamp/size bytecode cache.
    dependency.write_text('value = 300\n')
    assert key(workspace, **options) == before
    assert inherited(workspace, prior)['status'] == 'miss'
    fresh = run_check(workspace, key(workspace, **options), producer='external-dependency-recheck')
    assert Store(workspace).resolve(fresh)['status'] == 'fail'


def test_unrelated_external_dependency_does_not_disable_covered_reuse(tmp_path):
    options = fixture(tmp_path)
    (tmp_path/'unrelated/other.py').write_text('import unrelated_external_package\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    current = key(tmp_path, **options)
    assert current['eligible'] and current['unverified_dependencies'] == []
    ref = run_check(tmp_path, current, producer='covered-check')
    assert inherited(tmp_path, ref)['status'] == 'reused'


@pytest.mark.parametrize('change', ['source', 'dependency', 'output', 'test', 'runtime',
                                   'environment', 'command', 'criterion', 'tool', 'contract',
                                   'unlisted_environment', 'added_dependency', 'deleted_dependency'])
def test_contribution_capture_pins_each_actual_input_dimension(tmp_path, change):
    options = fixture(tmp_path)
    (tmp_path / 'pkg/rules.py').write_text('value = 3\n')
    (tmp_path / 'pkg/app.py').write_text('from pkg.rules import value\n')
    (tmp_path / 'pkg/result.txt').write_text('answer = 3\n')
    options['criteria'] = {'AC1': {'statement': 'answer equals three'}}
    options['outputs'] = ['pkg/result.txt']
    options['environment'] = {'LANG': 'C'}
    if change == 'runtime':
        executable = tmp_path / 'runtime-copy'
        executable.write_bytes(Path(sys.executable).read_bytes())
        executable.chmod(0o700)
        options['command'] = [str(executable), '-c', 'pass']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = contribution_key(tmp_path, **options)
    assert before['eligible'] and contribution_matches(before, contribution_key(tmp_path, **options))
    if change == 'source': (tmp_path / 'pkg/app.py').write_text('from pkg.rules import value\nvalue += 1\n')
    elif change == 'dependency': (tmp_path / 'pkg/rules.py').write_text('value = 4\n')
    elif change == 'output': (tmp_path / 'pkg/result.txt').write_text('answer = 4\n')
    elif change == 'test': (tmp_path / 'tests/check.py').write_text('assert False\n')
    elif change == 'runtime': executable.write_bytes(executable.read_bytes() + b'changed runtime identity')
    elif change == 'environment': options['environment'] = {'LANG': 'different'}
    elif change == 'unlisted_environment': options['environment']['APPLICATION_FEATURE'] = 'different'
    elif change == 'command': options['command'] = [sys.executable, '-c', 'print(4)']
    elif change == 'criterion': options['criteria']['AC1']['statement'] = 'answer equals four'
    elif change in ('tool', 'contract'): options[change] = 'changed'
    elif change == 'added_dependency': (tmp_path / 'pkg/new.py').write_text('value = 5\n')
    elif change == 'deleted_dependency': (tmp_path / 'pkg/rules.py').unlink()
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    assert not contribution_matches(before, contribution_key(tmp_path, **options))


def test_contribution_external_coverage_remains_unknown(tmp_path):
    options = fixture(tmp_path)
    (tmp_path / 'pkg/app.py').write_text('from unpinned_external_library import value\n')
    (tmp_path / 'pkg/result.txt').write_text('answer\n')
    options.update(criteria={'AC1': {'statement': 'answer'}}, outputs=['pkg/result.txt'])
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    current = contribution_key(tmp_path, **options)
    assert current['coverage'] == 'unknown' and not current['eligible']
    assert not contribution_matches(current, current)


@pytest.mark.parametrize('change', ['add', 'delete'])
def test_transitive_dependency_census_changes_are_captured(tmp_path, change):
    options = fixture(tmp_path)
    for directory in ('dependencies', 'leaf'):
        (tmp_path / directory).mkdir()
    (tmp_path / 'pkg/app.py').write_text('from dependencies.rules import value\n')
    (tmp_path / 'dependencies/rules.py').write_text('from leaf.implementation import value\n')
    (tmp_path / 'leaf/implementation.py').write_text('value = 3\n')
    (tmp_path / 'pkg/result.txt').write_text('answer\n')
    options.update(criteria={'AC1': {'statement': 'answer'}}, outputs=['pkg/result.txt'])
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = contribution_key(tmp_path, **options)
    assert before['eligible'] and 'leaf/implementation.py' in before['closure_census']
    if change == 'add': (tmp_path / 'leaf/new.py').write_text('extra = 2\n')
    else: (tmp_path / 'leaf/implementation.py').unlink()
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    assert not contribution_matches(before, contribution_key(tmp_path, **options))
