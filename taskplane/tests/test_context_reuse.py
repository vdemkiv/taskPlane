"""Matching provenance reuses observations; every relevant change invalidates it."""
import sys
import json
from contextlib import contextmanager
from threading import Event, Thread, current_thread
from pathlib import Path
from copy import deepcopy
import pytest
from taskplane import depgraph, primitives, workflow as w, workflow_evidence as evidence
from taskplane.context import Store
from taskplane.context_reuse import key, record, lookup, run_check, inherited, contribution_key, contribution_matches
from taskplane.context_reuse import same_inputs, revalidate


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


def test_same_unknown_dependency_fingerprint_cannot_be_reused(tmp_path):
    from taskplane.context_reuse import reusable
    prior = {'key': {'eligible': False, 'coverage': 'unknown'}, 'status': 'pass', 'result': {'returncode': 0}}
    assert not reusable(prior, deepcopy(prior['key']))


def test_bare_interpreter_records_actual_executable_and_reuses(tmp_path, monkeypatch):
    import os
    options = fixture(tmp_path)
    executable = Path(sys.executable).resolve()
    monkeypatch.setenv('PATH', str(executable.parent))
    options['command'][0] = executable.name
    before = key(tmp_path, **options)
    assert before['eligible'] and before['runtime']['executable'] == str(executable)
    assert before['runtime']['sha256'] != 'unknown'
    reference = run_check(tmp_path, before, producer='bare-interpreter')
    assert inherited(tmp_path, reference)['status'] == 'reused'
    monkeypatch.setenv('PATH', str(tmp_path/'missing') + os.pathsep + str(executable.parent))
    assert inherited(tmp_path, reference)['status'] == 'miss'


def installed_provider(site, name, *, requires=()):
    package = site/name
    package.mkdir(parents=True)
    (package/'__init__.py').write_text('value = 3\n')
    metadata = site/(name+'-1.0.dist-info')
    metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: '+name+'\nVersion: 1.0\n'
        + ''.join('Requires-Dist: '+item+'\n' for item in requires))
    (metadata/'top_level.txt').write_text(name+'\n')
    files = [name+'/__init__.py', metadata.name+'/METADATA', metadata.name+'/top_level.txt', metadata.name+'/RECORD']
    (metadata/'RECORD').write_text(''.join(file+',,\n' for file in files))
    return package


def test_provider_markers_normalize_release_padding_and_extra_names(monkeypatch):
    from taskplane import context_reuse as reuse
    monkeypatch.setattr(reuse.platform, 'python_version', lambda: '3.11.0')
    assert reuse._marker_applies('python_version >= "3.11.0"', set())
    assert not reuse._marker_applies('python_version < "3.11.0"', set())
    assert reuse._marker_applies('extra == "feature-name"', {'Feature_Name'})
    with pytest.raises(ValueError):
        reuse._marker_applies('__import__("os")', set())


@pytest.mark.parametrize('change', ['implementation', 'new-file', 'transitive', 'missing-file'])
def test_installed_provider_census_and_transitive_contents_bind_reuse(tmp_path, monkeypatch, change):
    workspace, site = tmp_path/'workspace', tmp_path/'site'
    workspace.mkdir(); site.mkdir()
    options = fixture(workspace)
    package = installed_provider(site, 'verified_rules', requires=[
        'verified_leaf>=1.0; python_version >= "3.0"', 'missing_extra; extra == "optional"'])
    leaf = installed_provider(site, 'verified_leaf')
    (workspace/'pkg/app.py').write_text('from verified_rules import value\n')
    monkeypatch.setenv('PYTHONPATH', str(site))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    depgraph.scan(str(workspace), decompose=True, strict=True)
    before = key(workspace, **options)
    assert before['eligible'] and before['unverified_dependencies'] == [], before
    assert set(before['provider_fingerprints']) == {'verified-rules', 'verified-leaf'}
    reference = run_check(workspace, before, producer='covered-provider')
    assert inherited(workspace, reference)['status'] == 'reused'
    if change == 'implementation': (package/'__init__.py').write_text('value = 400\n')
    elif change == 'new-file': (package/'new.py').write_text('new = True\n')
    elif change == 'transitive': (leaf/'__init__.py').write_text('value = 400\n')
    else: (package/'__init__.py').unlink()
    assert inherited(workspace, reference)['status'] == 'miss'


def test_shadowed_transitive_provider_remains_unknown(tmp_path, monkeypatch):
    import os
    workspace, site, shadow = tmp_path/'workspace', tmp_path/'site', tmp_path/'shadow'
    workspace.mkdir(); site.mkdir(); shadow.mkdir()
    options = fixture(workspace)
    installed_provider(site, 'verified_rules', requires=['verified_leaf'])
    installed_provider(site, 'verified_leaf')
    (workspace/'pkg/app.py').write_text('from verified_rules import value\n')
    (shadow/'verified_leaf.py').write_text('value = 999\n')
    monkeypatch.setenv('PYTHONPATH', str(shadow)+os.pathsep+str(site))
    depgraph.scan(str(workspace), decompose=True, strict=True)
    current = key(workspace, **options)
    assert not current['eligible'] and 'ext:verified_rules' in current['unverified_dependencies']


def test_shared_bytecode_directory_is_not_an_importable_provider(tmp_path, monkeypatch):
    import importlib.util
    # Exercise pip's default shared cache independently of CI's private prefix.
    monkeypatch.setattr(sys, 'pycache_prefix', None)
    monkeypatch.delenv('PYTHONPYCACHEPREFIX', raising=False)
    workspace, site = tmp_path/'workspace', tmp_path/'site'
    workspace.mkdir(); site.mkdir()
    options = fixture(workspace)
    package = installed_provider(site, 'verified_top')
    (package/'__init__.py').unlink(); package.rmdir()
    module = site/'verified_top.py'
    module.write_text('value = 3\n')
    cache = Path(importlib.util.cache_from_source(str(module)))
    cache.parent.mkdir()
    unrelated = cache.parent/'unrelated.cpython-test.pyc'
    unrelated.write_bytes(b'unrelated provider')
    inventory = site/'verified_top-1.0.dist-info/RECORD'
    inventory.write_text(inventory.read_text().replace('verified_top/__init__.py', 'verified_top.py')
                         + cache.relative_to(site).as_posix()+',,\n')
    (workspace/'pkg/app.py').write_text('from verified_top import value\n')
    monkeypatch.setenv('PYTHONPATH', str(site))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    depgraph.scan(str(workspace), decompose=True, strict=True)
    before = key(workspace, **options)
    assert before['eligible'], before.get('provider_diagnostics')
    reference = run_check(workspace, before, producer='top-level-provider')
    unrelated.write_bytes(b'changed unrelated provider')
    assert inherited(workspace, reference)['status'] == 'reused'
    cache.write_bytes(b'changed owned bytecode')
    assert inherited(workspace, reference)['status'] == 'miss'


def test_actual_pytest_installation_can_be_reused_until_provider_resolution_changes(tmp_path, monkeypatch):
    import os
    workspace, shadow, site = tmp_path/'workspace', tmp_path/'shadow', tmp_path/'site'
    workspace.mkdir(); shadow.mkdir(); site.mkdir()
    options = fixture(workspace)
    (workspace/'pkg/app.py').write_text('import pytest\nvalue = 3\n')
    (workspace/'tests/check.py').write_text('from pkg.app import value\ndef test_value():\n    assert value == 3\n')
    plugin = installed_provider(site, 'verified_plugin')/'__init__.py'
    metadata = site/'verified_plugin-1.0.dist-info'
    (metadata/'entry_points.txt').write_text('[pytest11]\nverified = verified_plugin\n')
    with (metadata/'RECORD').open('a') as stream:
        stream.write(metadata.name+'/entry_points.txt,,\n')
    options['command'] = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/check.py']
    monkeypatch.setenv('PYTHONPATH', str(site))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    for name in ('PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTEST_PLUGINS', 'PYTEST_ADDOPTS'):
        monkeypatch.delenv(name, raising=False)
    depgraph.scan(str(workspace), decompose=True, strict=True)
    before = key(workspace, **options)
    if not before['eligible']:
        (tmp_path/'provider-coverage.json').write_text(json.dumps(before))
    assert before['eligible'], before.get('provider_diagnostics')
    assert 'pytest' in before['provider_fingerprints']
    assert 'verified-plugin' in before['provider_fingerprints']
    reference = run_check(workspace, before, producer='actual-pytest-provider')
    assert inherited(workspace, reference)['status'] == 'reused'
    original = plugin.read_bytes()
    plugin.write_text('value = 400\n')
    assert inherited(workspace, reference)['status'] == 'miss'
    plugin.write_bytes(original)
    assert inherited(workspace, reference)['status'] == 'reused'
    (shadow/'pytest.py').write_text('shadow = True\n')
    monkeypatch.setenv('PYTHONPATH', str(shadow)+os.pathsep+os.environ.get('PYTHONPATH', ''))
    assert inherited(workspace, reference)['status'] == 'miss'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group assertion; Windows uses taskkill tree cleanup')
def test_timeout_kills_descendant_before_it_can_write(tmp_path):
    import time
    from taskplane.context_reuse import _execute
    child = "import time; from pathlib import Path; time.sleep(.7); Path('late.txt').write_text('escaped')"
    command = [sys.executable, '-c', 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(10)']
    import subprocess
    with pytest.raises(subprocess.TimeoutExpired):
        _execute(command, tmp_path, .2)
    time.sleep(.8)
    assert not (tmp_path / 'late.txt').exists()


def test_actual_check_reuse_and_unrelated_edit(tmp_path):
    options = fixture(tmp_path)
    before = key(tmp_path, **options)
    assert before['eligible']
    ref = run_check(tmp_path, before, producer='fixture-build')
    result = lookup(Store(tmp_path), ref, key(tmp_path, **options))
    assert result['status'] == 'reused' and result['approval'] == 'not_transferred'
    (tmp_path/'unrelated/new.py').write_text('other = True\n')
    assert key(tmp_path, **options) == before
    assert inherited(tmp_path, ref)['status'] == 'reused'


def test_new_covered_configuration_invalidates_a_prior_pass_before_fresh_failure(tmp_path):
    options = fixture(tmp_path)
    (tmp_path/'pkg/app.py').write_text(
        "try:\n    value = int(open('pkg/config.json').read())\n"
        "except FileNotFoundError:\n    value = 3\n")
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options)
    assert before['eligible'] and before['coverage'] == 'complete'
    prior = run_check(tmp_path, before, producer='config-before')
    assert Store(tmp_path).resolve(prior)['status'] == 'pass'
    assert inherited(tmp_path, prior)['status'] == 'reused'
    (tmp_path/'pkg/config.json').write_text('400')
    current = key(tmp_path, **options)
    assert current != before and not current['eligible'] and current['coverage'] == 'unknown'
    assert 'pkg/config.json' in current['files']
    assert inherited(tmp_path, prior)['status'] == 'miss'
    fresh = run_check(tmp_path, current, producer='config-after')
    assert Store(tmp_path).resolve(fresh)['status'] == 'fail'


@pytest.mark.parametrize('extension', ['toml', 'ini', 'txt'])
def test_existing_unparsed_configuration_contents_cannot_reuse_a_passing_check(tmp_path, extension):
    options = fixture(tmp_path)
    relative = 'pkg/settings.' + extension
    (tmp_path/relative).write_text('3')
    (tmp_path/'pkg/app.py').write_text(f'value = int(open({relative!r}).read())\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options)
    assert before['eligible'] and relative in before['files']
    prior = run_check(tmp_path, before, producer='unparsed-config-before')
    assert Store(tmp_path).resolve(prior)['status'] == 'pass'
    assert inherited(tmp_path, prior)['status'] == 'reused'
    (tmp_path/relative).write_text('400')
    current = key(tmp_path, **options)
    assert current != before
    assert inherited(tmp_path, prior)['status'] == 'miss'
    fresh = run_check(tmp_path, current, producer='unparsed-config-after')
    assert Store(tmp_path).resolve(fresh)['status'] == 'fail'


@pytest.mark.parametrize('relative', ['pkg/config.json', 'pkg/schema.sql', 'pkg/settings.yaml',
                                     'pkg/pom.xml', 'pkg/go.mod', 'package.json', 'go.mod'])
@pytest.mark.parametrize('change', ['add', 'delete', 'edit'])
def test_scanner_artifact_and_resolution_inputs_invalidate_covered_reuse(tmp_path, relative, change):
    options = fixture(tmp_path)
    target = tmp_path/relative
    if change != 'add': target.write_text('{}' if target.suffix == '.json' else 'old\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options)
    assert before['eligible']
    if change == 'delete': target.unlink()
    else: target.write_text('{}' if target.suffix == '.json' else 'new\n')
    if change == 'edit' and target.suffix == '.json': target.write_text('{"changed":true}')
    current = key(tmp_path, **options)
    assert current != before and not current['eligible']
    assert relative in current['files']


def test_component_mapping_change_makes_dependency_resolution_unknown(tmp_path):
    options = fixture(tmp_path)
    before = key(tmp_path, **options)
    (tmp_path/'components.yaml').write_text('exclude:\n  - pkg\n')
    current = key(tmp_path, **options)
    assert current != before and current['coverage'] == 'unknown'


@pytest.mark.parametrize('change', ['add', 'delete'])
def test_unparsed_covered_resolution_candidate_changes_require_fresh_coverage(tmp_path, change):
    options = fixture(tmp_path)
    target = tmp_path/'pkg/settings.toml'
    if change == 'delete': target.write_text('enabled = true\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options)
    assert before['eligible']
    if change == 'add': target.write_text('enabled = true\n')
    else: target.unlink()
    current = key(tmp_path, **options)
    assert current != before and current['coverage'] == 'unknown'


def test_unrelated_artifacts_documents_and_excluded_generated_outputs_preserve_reuse(tmp_path):
    options = fixture(tmp_path)
    (tmp_path/'README.md').write_text('Initial unrelated documentation\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options)
    assert before['eligible']
    (tmp_path/'README.md').write_text('Updated unrelated documentation\n')
    (tmp_path/'unrelated/config.json').write_text('{"new":true}')
    (tmp_path/'pkg/dist').mkdir()
    (tmp_path/'pkg/dist/generated.json').write_text('{"generated":true}')
    (tmp_path/'pkg/__pycache__').mkdir(exist_ok=True)
    (tmp_path/'pkg/__pycache__/generated.py').write_text('generated = True\n')
    assert key(tmp_path, **options) == before


def test_fresh_check_runs_without_optional_graph_or_reuse_coverage(tmp_path, monkeypatch):
    options = fixture(tmp_path)
    monkeypatch.setattr(depgraph, 'load', lambda _: {})
    monkeypatch.setattr(depgraph, 'source_inputs_current', lambda *a: pytest.fail('No whole-workspace freshness gate'))
    provenance = key(tmp_path, **options)
    assert provenance['coverage'] == 'unknown' and not provenance['eligible']
    reference = run_check(tmp_path, provenance, producer='fresh-check')
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    assert inherited(tmp_path, reference)['status'] == 'miss'


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
        executable = tmp_path / ('runtime-copy' + Path(sys.executable).suffix)
        executable.write_bytes(Path(sys.executable).read_bytes())
        executable.chmod(0o700)
        options['command'] = [str(executable), '-c', 'pass']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = contribution_key(tmp_path, **options)
    assert before['eligible'], before
    assert contribution_matches(before, contribution_key(tmp_path, **options))
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


def semantic_contract(tmp_path, *, declared_history=True):
    options = fixture(tmp_path)
    owned = ['pkg/app.py', 'pkg/history.json', 'pkg/history-a.json', 'pkg/history-b.json',
             'pkg/check-1.log', 'pkg/check-2.log', 'pkg/check-3.log', 'pkg/build.json']
    scope = {'criteria': ['AC1'], 'paths': {phase: [phase + '.json'] for phase in w.PHASES},
             'verification_inputs': ['pkg/app.py', 'tests/check.py']}
    scope['paths']['build'] = owned
    state = w.new_state(str(tmp_path), 'root', 'semantic-run', scope)
    state['index'] = w.PHASES.index('build')
    task = {'id': 'BUILD', 'phase': 'build', 'owner': 'root', 'dependencies': [],
            'paths': list(owned), 'criteria': ['AC1'],
            'read_inputs': ['pkg/app.py', 'tests/check.py'], 'verification': 'Run the assertion'}
    check = {'id': 'assertion', 'name': 'Actual source assertion', 'task': 'BUILD',
             'criteria': ['AC1'], 'kind': 'unit', 'environment': 'local', 'required': True,
             'source_inputs': options['paths'], 'test_inputs': options['tests'],
             'command': options['command'],
             'evidence_outputs': ['pkg/check-1.log', 'pkg/check-2.log', 'pkg/check-3.log']}
    plan = {'task_dag': [task], 'ownership': {'BUILD': 'root'}, 'write_scope': owned,
            'integration_order': ['BUILD'],
            'verification_strategy': {'schema': evidence.VERIFICATION_STRATEGY, 'checks': [check]}}
    if declared_history:
        plan['build_outputs'] = [{'kind': 'verification_history', 'path': 'pkg/history.json',
                                  'task': 'BUILD'}]
    state['visits'][w.PHASES.index('plan')].update(decision='approved', packet={'output': plan})
    return state, plan, check, options


def semantic_attempt(tmp_path, state, check, options, *, history='pkg/history.json',
                     log='pkg/check-1.log'):
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path=history, log_path=log)
    assert provenance['eligible']
    reference = run_check(tmp_path, provenance, producer='root', state=state,
        history_path=history, log_path=log, run=state['run'], visit=w.current(state)['id'],
        check_id=check['id'], kind=check['kind'], environment=check['environment'])
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    return reference


def test_latest_unknown_in_alternate_declared_history_cannot_reveal_old_pass(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    plan['build_outputs'] = [{'kind': 'verification_history', 'path': p, 'task': 'BUILD'}
                            for p in ['pkg/history-a.json', 'pkg/history-b.json']]
    check['command'] = options['command'] = [sys.executable, '-c', 'import time; time.sleep(.1)']
    first = semantic_attempt(tmp_path, state, check, options, history='pkg/history-a.json')
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path='pkg/history-b.json', log_path='pkg/check-2.log')
    second = run_check(tmp_path, provenance, producer='root', timeout=.01, state=state,
        history_path='pkg/history-b.json', log_path='pkg/check-2.log', run=state['run'],
        visit=w.current(state)['id'], check_id=check['id'], kind=check['kind'], environment=check['environment'])
    assert Store(tmp_path).resolve(first)['status'] == 'pass'
    assert Store(tmp_path).resolve(second)['status'] == 'unknown'
    with pytest.raises(w.Refusal, match='newest verification history'):
        evidence.validate_verification(tmp_path, state, {'verification_history': 'pkg/history-a.json'})


def test_root_check_can_append_to_declared_root_collector_without_accepting_it(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    task = plan['task_dag'][0]
    task['paths'].remove('pkg/history.json')
    collector = {'id': 'COLLECT', 'phase': 'build', 'owner': state['root'], 'dependencies': ['BUILD'],
                 'paths': ['pkg/history.json'], 'read_inputs': [], 'criteria': ['AC1'],
                 'verification': 'Collect the actual immutable attempts'}
    plan['task_dag'].append(collector)
    plan['build_outputs'][0]['task'] = 'COLLECT'
    reference = semantic_attempt(tmp_path, state, check, options)
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    assert not state.get('task_results')
    collector['owner'] = 'planned-native/other'
    with pytest.raises(w.Refusal, match='exact producing owner'):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path='pkg/check-2.log')


def test_repair_retry_preserves_both_immutable_logs_and_selects_new_result(tmp_path):
    state, _, check, options = semantic_contract(tmp_path)
    check['command'] = options['command'] = [sys.executable, '-c', 'import time; time.sleep(.05)']
    state = w.repair(state, ['BUILD'], {'human': True, 'automatic': False, 'choice': 'approved'})
    history = w.current(state)['artifact_paths'][2]
    log = next(p for p in w.current(state)['artifact_paths'] if p.endswith('.log'))
    references = []
    for timeout in (.001, 10):
        provenance = key(tmp_path, **options, state=state, check_id=check['id'], history_path=history, log_path=log)
        references.append(run_check(tmp_path, provenance, producer='root', timeout=timeout, state=state,
            history_path=history, log_path=log, run=state['run'], visit=w.current(state)['id'],
            check_id=check['id'], kind=check['kind'], environment=check['environment']))
    records = [Store(tmp_path).resolve(ref) for ref in references]
    assert [row['status'] for row in records] == ['unknown', 'pass']
    assert records[0]['log_ref'] != records[1]['log_ref']
    assert len(json.loads((tmp_path / history).read_text())['attempts']) == 2


def test_native_repair_task_uses_fresh_owned_evidence_projection(tmp_path):
    from taskplane import worker_runtime
    state, plan, _, _ = semantic_contract(tmp_path)
    plan['task_dag'][0]['owner'] = 'planned-native/verifier'
    state['visits'][2]['packet']['context'] = {'tasks': plan['task_dag']}
    old_log, old_history = 'pkg/check-1.log', 'pkg/history.json'
    state['visits'][state['index']]['packet'] = {'output_path': 'pkg/build.json',
        'verification': {'files': [old_log, old_history]}, 'context': {}}
    state = w.repair(state, ['BUILD'], {'human': True, 'automatic': False, 'choice': 'approved'})
    definition = worker_runtime.task(state, 'BUILD')
    assert old_log not in definition['paths'] and old_history not in definition['paths']
    assert any('/check-' in p and p.endswith('.log') for p in definition['paths'])
    assert set(definition['paths']) <= set(w.phase_write_paths(state))


@pytest.mark.parametrize('declared_read', [False, True])
def test_generated_check_artifact_consumed_as_source_stays_fingerprinted(tmp_path, declared_read):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/generated.md'
    (tmp_path / relative).write_text('3')
    (tmp_path / 'pkg/app.py').write_text(f'value = int(open({relative!r}).read())\n')
    plan['task_dag'][0]['paths'].append(relative)
    plan['write_scope'].append(relative)
    check['evidence_outputs'].append(relative)
    if declared_read:
        plan['task_dag'][0]['read_inputs'].append(relative)
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    reference = semantic_attempt(tmp_path, state, check, options)
    captured = Store(tmp_path).resolve(reference)
    assert relative in captured['key']['files']
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    (tmp_path / relative).write_text('400')
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'


@pytest.mark.parametrize('read_role', ['source', 'test', 'task_read'])
@pytest.mark.parametrize('output_role', ['history', 'log'])
def test_current_check_read_write_role_collision_refuses(tmp_path, read_role, output_role):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/history.json' if output_role == 'history' else 'pkg/check-1.log'
    (tmp_path/relative).write_text('3')
    if read_role == 'source':
        check['source_inputs'] = options['paths'] = options['paths'] + [relative]
    elif read_role == 'test':
        check['test_inputs'] = options['tests'] = options['tests'] + [relative]
    plan['task_dag'][0]['read_inputs'].append(relative)
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path='pkg/check-1.log')


@pytest.mark.parametrize('extension', ['log', 'json', 'lock'])
def test_real_config_with_evidence_like_suffix_stays_bound(tmp_path, extension):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/config.' + extension
    (tmp_path/relative).write_text('3')
    (tmp_path/'pkg/app.py').write_text(f'value = int(open({relative!r}).read())\n')
    check['source_inputs'] = options['paths'] = options['paths'] + [relative]
    plan['task_dag'][0]['read_inputs'].append(relative)
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    reference = semantic_attempt(tmp_path, state, check, options)
    assert relative in Store(tmp_path).resolve(reference)['key']['files']
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    (tmp_path/relative).write_text('400')
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'


def test_generated_evidence_read_by_downstream_task_remains_its_input(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/generated.json'
    (tmp_path/relative).write_text('3')
    plan['task_dag'][0]['paths'].append(relative)
    plan['write_scope'].append(relative)
    plan['build_outputs'].append({'kind': 'report', 'path': relative, 'task': 'BUILD'})
    consumer = {'id': 'CONSUMER', 'phase': 'build', 'owner': 'root', 'dependencies': ['BUILD'],
                'paths': ['pkg/consumer.log'], 'criteria': ['AC1'],
                'read_inputs': ['pkg/app.py', 'tests/check.py', relative],
                'verification': 'Read the prerequisite artifact'}
    plan['task_dag'].append(consumer)
    plan['ownership']['CONSUMER'] = 'root'
    plan['integration_order'].append('CONSUMER')
    plan['write_scope'].append('pkg/consumer.log')
    consumer_check = deepcopy(check)
    consumer_check.update(id='consume-evidence', task='CONSUMER',
                          source_inputs=['pkg/app.py', relative], evidence_outputs=['pkg/consumer.log'])
    plan['verification_strategy']['checks'].append(consumer_check)
    options['paths'] = consumer_check['source_inputs']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options, state=state, check_id=consumer_check['id'])
    assert before['eligible'] and relative in before['files']
    (tmp_path/relative).write_text('400')
    assert key(tmp_path, **options, state=state, check_id=consumer_check['id']) != before


def test_rescanned_metadata_references_cannot_expand_source_closure_or_contribution_edges(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    log = 'pkg/check-1.json'
    plan['task_dag'][0]['paths'].append(log)
    plan['write_scope'].append(log)
    check['evidence_outputs'][0] = log
    check['command'] = options['command'] = [sys.executable, '-c',
        'exec(open("tests/check.py").read()); print("unrelated/other.py")']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    contribution_options = {**options, 'criteria': {'AC1': {'statement': 'Source remains correct'}},
                            'outputs': ['pkg/app.py'], 'state': state, 'check_id': check['id'],
                            'history_path': 'pkg/history.json', 'log_path': log}
    before = contribution_key(tmp_path, **contribution_options)
    assert before['eligible'] and 'unrelated' not in before['verification']['modules']
    reference = semantic_attempt(tmp_path, state, check, options, log=log)
    assert 'unrelated/other.py' in (tmp_path/log).read_text()
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    after = contribution_key(tmp_path, **contribution_options)
    assert contribution_matches(before, after)
    assert 'unrelated' not in after['verification']['modules']
    (tmp_path/'pkg/app.py').write_text('value = 400\n')
    assert not contribution_matches(after, contribution_key(tmp_path, **contribution_options))


def test_independent_fallback_histories_remain_current_after_publication_and_reload(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path, declared_history=False)
    second_check = deepcopy(check)
    second_check.update(id='second-assertion', evidence_outputs=['pkg/check-2.log'])
    check['evidence_outputs'] = ['pkg/check-1.log']
    plan['verification_strategy']['checks'].append(second_check)
    first = semantic_attempt(tmp_path, state, check, options,
                             history='pkg/history-a.json', log='pkg/check-1.log')
    second = semantic_attempt(tmp_path, state, second_check, options,
                              history='pkg/history-b.json', log='pkg/check-2.log')
    reloaded = json.loads(json.dumps(state))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    assert inherited(tmp_path, first, state=reloaded)['status'] == 'reused'
    assert inherited(tmp_path, second, state=reloaded)['status'] == 'reused'
    assert inherited(tmp_path, first)['status'] == 'miss'


def test_source_inferred_generated_report_read_stays_bound_without_explicit_read(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/generated.json'
    (tmp_path/relative).write_text('3')
    (tmp_path/'pkg/app.py').write_text(f'value = int(open({relative!r}).read())\n')
    plan['task_dag'][0]['paths'].append(relative)
    plan['write_scope'].append(relative)
    plan['build_outputs'].append({'kind': 'report', 'path': relative, 'task': 'BUILD'})
    assert relative not in plan['task_dag'][0]['read_inputs']
    assert relative not in options['paths'] + options['tests']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    reference = semantic_attempt(tmp_path, state, check, options)
    assert relative in Store(tmp_path).resolve(reference)['key']['files']
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    (tmp_path/relative).write_text('400')
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'
    current = key(tmp_path, **options, state=state, check_id=check['id'],
                  history_path='pkg/history.json', log_path='pkg/check-2.log')
    fresh = run_check(tmp_path, current, producer='root', state=state,
        history_path='pkg/history.json', log_path='pkg/check-2.log', run=state['run'],
        visit=w.current(state)['id'], check_id=check['id'], kind=check['kind'],
        environment=check['environment'])
    assert Store(tmp_path).resolve(fresh)['status'] == 'fail'


def test_source_inferred_read_of_own_current_log_refuses(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    log = 'pkg/check-1.json'
    (tmp_path/log).write_text('3')
    (tmp_path/'pkg/app.py').write_text(f'value = int(open({log!r}).read())\n')
    plan['task_dag'][0]['paths'].append(log)
    plan['write_scope'].append(log)
    check['evidence_outputs'][0] = log
    assert log not in plan['task_dag'][0]['read_inputs']
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path=log)


def test_canonical_history_alias_cannot_hide_own_read_write_conflict(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    plan['task_dag'][0]['read_inputs'].append('pkg/./history.json')
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path='pkg/check-1.log')


def test_concurrent_first_history_publication_cannot_make_other_real_check_unknown(tmp_path, monkeypatch):
    state, plan, check, options = semantic_contract(tmp_path, declared_history=False)
    second_check = deepcopy(check)
    second_check.update(id='second-assertion', evidence_outputs=['pkg/check-2.log'])
    check['evidence_outputs'] = ['pkg/check-1.log']
    plan['verification_strategy']['checks'].append(second_check)
    selections = [(check, 'pkg/history-a.json', 'pkg/check-1.log'),
                  (second_check, 'pkg/history-b.json', 'pkg/check-2.log')]
    prepared = [key(tmp_path, **options, state=state, check_id=row['id'],
                    history_path=history, log_path=log) for row, history, log in selections]
    assert all(item['eligible'] for item in prepared)
    first_locked, release_first, second_at_lock, second_snapshot_acquired = (
        Event(), Event(), Event(), Event())
    original_lock = primitives.file_lock
    first_history = (tmp_path/'pkg/history-a.json').resolve()

    @contextmanager
    def schedule_publication(path):
        second_snapshot = (current_thread().name == 'semantic-check-b'
                           and Path(path).name == 'verification-publication')
        if second_snapshot:
            second_at_lock.set()
        with original_lock(path):
            if second_snapshot:
                second_snapshot_acquired.set()
            if Path(path).resolve() == first_history:
                assert Path(str(first_history) + '.lock').exists()
                assert not first_history.exists()
                first_locked.set()
                assert release_first.wait(15), 'First publication was not released'
            yield

    monkeypatch.setattr(primitives, 'file_lock', schedule_publication)
    references, errors = {}, []

    def execute(index):
        try:
            requirement, history, log = selections[index]
            references[index] = run_check(tmp_path, prepared[index], producer='root', timeout=10,
                state=state, history_path=history, log_path=log, run=state['run'],
                visit=w.current(state)['id'], check_id=requirement['id'],
                kind=requirement['kind'], environment=requirement['environment'])
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=execute, args=(0,), name='semantic-check-a', daemon=True)
    second = Thread(target=execute, args=(1,), name='semantic-check-b', daemon=True)
    first.start()
    try:
        assert first_locked.wait(10), 'First check never reached native history publication'
        second.start()
        assert second_at_lock.wait(10), 'Second check never reached its snapshot/publication lock'
        assert not second_snapshot_acquired.wait(0.1), 'Snapshot entered an unfinished evidence publication'
    finally:
        release_first.set()
        first.join(15)
        if second.ident is not None:
            second.join(15)
    assert not first.is_alive() and not second.is_alive(), 'Verification threads did not terminate'
    if errors:
        raise errors[0]
    assert set(references) == {0, 1}
    reloaded = json.loads(json.dumps(state))
    for reference in references.values():
        observed = Store(tmp_path).resolve(reference)
        assert observed['status'] == 'pass' and observed['result']['returncode'] == 0
        assert inherited(tmp_path, reference, state=reloaded)['status'] == 'reused'


@pytest.mark.parametrize('history', [
    '.taskplane/context-v1/verification-publication',
    './.taskplane/context-v1/verification-publication',
    '.taskplane/context-v1/verification-publication.lock',
], ids=['logical-history', 'canonical-alias', 'native-lock-file'])
def test_owned_history_cannot_reenter_or_overwrite_internal_publication_lock(tmp_path, history):
    state, plan, check, options = semantic_contract(tmp_path)
    plan['task_dag'][0]['paths'].append(history)
    plan['write_scope'].append(history)
    plan['build_outputs'][0]['path'] = history
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path=history, log_path='pkg/check-1.log')


def packet_draft(state, **changes):
    return {'schema': 'taskplane.phase-output/v1', 'run': state['run'],
            'visit': w.current(state)['id'], 'phase': w.current(state)['phase'],
            'value': 3, **changes}


def run_packet_check(tmp_path, state, check, provenance, *, log='pkg/check-1.log'):
    return run_check(tmp_path, provenance, producer='root', state=state,
        history_path='pkg/history.json', log_path=log, run=state['run'],
        visit=w.current(state)['id'], check_id=check['id'], kind=check['kind'],
        environment=check['environment'])


def test_bound_owned_packet_draft_stays_reusable_when_report_is_completed(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    (tmp_path/path).write_text(json.dumps(packet_draft(state)))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options, state=state, check_id=check['id'],
                 history_path='pkg/history.json', log_path='pkg/check-1.log')
    assert before['eligible']
    reference = run_packet_check(tmp_path, state, check, before)
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    (tmp_path/path).write_text(json.dumps(packet_draft(state, outcome='Check passed')))
    state['visits'][state['index']]['packet'] = {'output_path': path, 'output': packet_draft(state)}
    current = revalidate(tmp_path, Store(tmp_path).resolve(reference), state=state, output_path=path)
    assert same_inputs(before, current)
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'


@pytest.mark.parametrize('draft_exists_at_key', [True, False], ids=['existing-draft', 'created-after-key'])
def test_explicit_packet_context_reaches_execution_without_repeating_output_argument(tmp_path, draft_exists_at_key):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    if draft_exists_at_key:
        (tmp_path/path).write_text(json.dumps(packet_draft(state)))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path='pkg/history.json', log_path='pkg/check-1.log', output_path=path)
    if not draft_exists_at_key:
        (tmp_path/path).write_text(json.dumps(packet_draft(state)))
    # run_check receives the ordinary provenance, with no repeated output_path.
    reference = run_packet_check(tmp_path, state, check, provenance)
    observed = Store(tmp_path).resolve(reference)
    assert observed['status'] == 'pass' and observed['result']['returncode'] == 0
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'


def test_producing_packet_context_survives_later_evaluate_and_contribution_reuse(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    (tmp_path/path).write_text(json.dumps(packet_draft(state)))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    producing_visit = w.current(state)['id']
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path='pkg/history.json', log_path='pkg/check-1.log', output_path=path)
    contribution_options = {**options, 'criteria': {'AC1': {'statement': 'Source remains correct'}},
        'outputs': ['pkg/app.py'], 'state': state, 'check_id': check['id'],
        'history_path': 'pkg/history.json', 'log_path': 'pkg/check-1.log', 'visit': producing_visit}
    before = contribution_key(tmp_path, **contribution_options, output_path=path)
    reference = run_packet_check(tmp_path, state, check, provenance)
    state['visits'][state['index']].update(decision='approved',
        packet={'output_path': path, 'output': packet_draft(state)})
    state['index'] = w.PHASES.index('evaluate')
    (tmp_path/'evaluate.json').write_text(json.dumps(packet_draft(state)))
    current = revalidate(tmp_path, Store(tmp_path).resolve(reference), state=state,
                         output_path='evaluate.json')
    assert same_inputs(provenance, current)
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    after = contribution_key(tmp_path, **contribution_options, output_path='evaluate.json')
    assert contribution_matches(before, after)


@pytest.mark.parametrize('declared_read', [True, False], ids=['explicit-read', 'scanner-proven-read'])
def test_real_packet_read_remains_input_and_edit_invalidates_observed_pass(tmp_path, declared_read):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    (tmp_path/path).write_text(json.dumps(packet_draft(state)))
    (tmp_path/'pkg/app.py').write_text(f'value = eval(open({path!r}).read())["value"]\n')
    if declared_read:
        plan['task_dag'][0]['read_inputs'].append(path)
        check['source_inputs'] = options['paths'] = options['paths'] + [path]
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options, state=state, check_id=check['id'],
                 history_path='pkg/history.json', log_path='pkg/check-1.log', output_path=path)
    assert before['eligible']
    reference = run_packet_check(tmp_path, state, check, before)
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    (tmp_path/path).write_text(json.dumps(packet_draft(state, value=400)))
    current = key(tmp_path, **options, state=state, check_id=check['id'],
                  history_path='pkg/history.json', log_path='pkg/check-2.log', output_path=path)
    assert not same_inputs(before, current)
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'
    failed = run_packet_check(tmp_path, state, check, current, log='pkg/check-2.log')
    assert Store(tmp_path).resolve(failed)['status'] == 'fail'


@pytest.mark.parametrize('defect', ['malformed', 'wrong-run', 'wrong-visit', 'wrong-phase', 'unowned'])
def test_explicit_packet_role_refuses_unbound_or_unowned_draft(tmp_path, defect):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json' if defect != 'unowned' else 'pkg/unowned.json'
    draft = packet_draft(state)
    if defect == 'wrong-run': draft['run'] = 'foreign-run'
    if defect == 'wrong-visit': draft['visit'] = 'foreign-visit'
    if defect == 'wrong-phase': draft['phase'] = 'evaluate'
    (tmp_path/path).write_text('not a packet' if defect == 'malformed' else json.dumps(draft))
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path='pkg/check-1.log', output_path=path)


@pytest.mark.parametrize('draft_kind', ['ordinary-config', 'foreign-packet'])
def test_implicit_packet_discovery_cannot_hide_unbound_owned_config(tmp_path, draft_kind):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    contents = {'value': 3} if draft_kind == 'ordinary-config' else packet_draft(state, run='foreign-run')
    (tmp_path/path).write_text(json.dumps(contents))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options, state=state, check_id=check['id'],
                 history_path='pkg/history.json', log_path='pkg/check-1.log')
    contents['value'] = 400
    (tmp_path/path).write_text(json.dumps(contents))
    after = key(tmp_path, **options, state=state, check_id=check['id'],
                history_path='pkg/history.json', log_path='pkg/check-1.log')
    assert not same_inputs(before, after)


def test_retained_packet_sidecar_cannot_hide_dynamic_runtime_config(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    relative = 'pkg/config.json'
    (tmp_path/relative).write_text('3')
    plan['task_dag'][0]['paths'].append(relative)
    plan['write_scope'].append(relative)
    # Assemble the path at runtime so static literal references do not prove
    # this dependency; the admitted component census must still bind it.
    (tmp_path/'pkg/app.py').write_text(
        f'name = "".join(map(chr, {list(relative.encode())!r}))\n'
        'value = int(open(name).read())\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before = key(tmp_path, **options, state=state, check_id=check['id'],
                 history_path='pkg/history.json', log_path='pkg/check-1.log')
    reference = run_packet_check(tmp_path, state, check, before)
    assert Store(tmp_path).resolve(reference)['status'] == 'pass'
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    with pytest.raises(w.Refusal):
        key(tmp_path, **options, state=state, check_id=check['id'],
            history_path='pkg/history.json', log_path='pkg/check-2.log',
            packet_context=[{'visit': w.current(state)['id'], 'path': relative}])
    (tmp_path/relative).write_text('400')
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'
    current = key(tmp_path, **options, state=state, check_id=check['id'],
                  history_path='pkg/history.json', log_path='pkg/check-2.log')
    fresh = run_packet_check(tmp_path, state, check, current, log='pkg/check-2.log')
    assert Store(tmp_path).resolve(fresh)['status'] == 'fail'


def test_captured_packet_role_survives_same_visit_resubmission_history(tmp_path):
    state, plan, check, options = semantic_contract(tmp_path)
    path = 'pkg/build.json'
    revised = 'pkg/revised-build.json'
    plan['write_scope'].append(revised)
    plan['task_dag'][0]['paths'].append(revised)
    draft = packet_draft(state)
    (tmp_path/path).write_text(json.dumps(draft))
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path='pkg/history.json', log_path='pkg/check-1.log')
    reference = run_packet_check(tmp_path, state, check, provenance)
    # Submission history remains authoritative when the current packet changes.
    state['history'].append({'id': w.current(state)['id'], 'phase': 'build',
        'packet': {'visit': w.current(state)['id'], 'output_path': path, 'output': draft}})
    state['visits'][state['index']]['packet'] = {'visit': w.current(state)['id'],
                                               'output_path': revised, 'output': draft}
    (tmp_path/revised).write_text(json.dumps(draft))
    (tmp_path/path).write_text(json.dumps({'review_note': 'Earlier captured report'}))
    assert inherited(tmp_path, reference, state=json.loads(json.dumps(state)))['status'] == 'reused'


def evaluation_contract(tmp_path):
    state, plan, first, options = semantic_contract(tmp_path)
    second = deepcopy(first)
    second.update(id='second-assertion', evidence_outputs=['pkg/check-2.log'])
    first['evidence_outputs'] = ['pkg/check-1.log']
    plan['verification_strategy']['checks'].append(second)
    state['visits'][state['index']]['decision'] = 'approved'
    state['index'] = w.PHASES.index('evaluate')
    state['scope']['paths']['evaluate'] = [
        'pkg/evaluate-history.json', 'pkg/eval-one.json', 'pkg/eval-two.json', 'pkg/eval-three.json',
        'pkg/reader-history.json', 'pkg/reader-one.json', 'pkg/reader-two.json']
    return state, plan, first, second, options


def evaluation_attempt(tmp_path, state, check, options, log, *,
                       history='pkg/evaluate-history.json', expected='pass'):
    provenance = key(tmp_path, **options, state=state, check_id=check['id'],
                     history_path=history, log_path=log)
    reference = run_check(tmp_path, provenance, producer='root', state=state,
        history_path=history, log_path=log, run=state['run'], visit=w.current(state)['id'],
        check_id=check['id'], kind=check['kind'], environment=check['environment'])
    observed = Store(tmp_path).resolve(reference)
    assert observed['status'] == expected
    assert observed['result']['returncode'] == (0 if expected == 'pass' else 1)
    return reference


def capture_evaluation_history(tmp_path, state):
    relative = 'pkg/evaluate-history.json'
    state['visits'][state['index']]['packet'] = {'verification': {
        'history_path': relative, 'history': json.loads((tmp_path/relative).read_text())}}


@pytest.mark.parametrize('history_role', ['current', 'declared', 'captured', 'discovered'])
def test_shared_evaluate_history_sibling_logs_have_consistent_roles(tmp_path, history_role):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    relative = 'pkg/evaluate-history.json'
    if history_role == 'declared':
        plan['write_scope'].append(relative)
        plan['task_dag'][0]['paths'].append(relative)
        plan['build_outputs'].append({'kind': 'verification_history', 'path': relative, 'task': 'BUILD'})
    one = evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-one.json')
    if history_role == 'captured':
        capture_evaluation_history(tmp_path, state)
    two = evaluation_attempt(tmp_path, state, second, options, 'pkg/eval-two.json')
    assert inherited(tmp_path, one, state=state)['status'] == 'reused'
    assert inherited(tmp_path, two, state=state)['status'] == 'reused'
    if history_role == 'discovered':
        current = key(tmp_path, **options, state=state, check_id=first['id'])
        assert same_inputs(Store(tmp_path).resolve(one)['key'], current)


def test_captured_history_retains_sibling_log_roles_when_mutable_history_is_missing(tmp_path):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    one = evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-one.json')
    two = evaluation_attempt(tmp_path, state, second, options, 'pkg/eval-two.json')
    capture_evaluation_history(tmp_path, state)
    (tmp_path/'pkg/evaluate-history.json').unlink()
    reloaded = json.loads(json.dumps(state))
    assert inherited(tmp_path, one, state=reloaded)['status'] == 'reused'
    assert inherited(tmp_path, two, state=reloaded)['status'] == 'reused'


def test_attempts_appended_after_capture_supply_new_sibling_log_roles(tmp_path):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    one = evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-one.json')
    capture_evaluation_history(tmp_path, state)
    two = evaluation_attempt(tmp_path, state, second, options, 'pkg/eval-two.json')
    three = evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-three.json')
    reloaded = json.loads(json.dumps(state))
    for reference in [one, two, three]:
        assert inherited(tmp_path, reference, state=reloaded)['status'] == 'reused'


@pytest.mark.parametrize('read_role', ['explicit', 'scanner'])
@pytest.mark.parametrize('input_role', ['sibling-log', 'sibling-history'])
def test_real_reads_of_sibling_verification_artifacts_remain_dependencies(tmp_path, read_role, input_role):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-one.json')
    relative = 'pkg/eval-one.json' if input_role == 'sibling-log' else 'pkg/evaluate-history.json'
    condition = 'data["returncode"] == 0' if input_role == 'sibling-log' else 'data["run"] == "semantic-run"'
    (tmp_path/'pkg/app.py').write_text(
        f'data = eval(open({relative!r}).read())\nvalue = 3 if {condition} else 400\n')
    if read_role == 'explicit':
        plan['task_dag'][0]['read_inputs'].append(relative)
        second['source_inputs'] = options['paths'] = options['paths'] + [relative]
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    reference = evaluation_attempt(tmp_path, state, second, options, 'pkg/reader-one.json',
                                   history='pkg/reader-history.json')
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    data = json.loads((tmp_path/relative).read_text())
    if input_role == 'sibling-log': data['returncode'] = 1
    else: data['run'] = 'foreign-run'
    (tmp_path/relative).write_text(json.dumps(data))
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'
    evaluation_attempt(tmp_path, state, second, options, 'pkg/reader-two.json',
                       history='pkg/reader-history.json', expected='fail')


@pytest.mark.parametrize('history_kind', ['malformed', 'foreign'])
def test_unproven_history_does_not_hide_owned_runtime_config(tmp_path, history_kind):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    relative = 'pkg/ordinary-config.json'
    state['scope']['paths']['evaluate'].append(relative)
    ordinary = {'value': 3}
    if history_kind == 'foreign':
        ordinary.update(schema=evidence.VERIFICATION_HISTORY, run='foreign-run',
                        visit=w.current(state)['id'], attempts=[])
    else:
        ordinary.update(schema=evidence.VERIFICATION_HISTORY, run=state['run'], attempts='not attempts')
    (tmp_path/relative).write_text(json.dumps(ordinary))
    (tmp_path/'pkg/app.py').write_text(f'value = eval(open({relative!r}).read())["value"]\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    reference = evaluation_attempt(tmp_path, state, second, options, 'pkg/reader-one.json',
                                   history='pkg/reader-history.json')
    assert inherited(tmp_path, reference, state=state)['status'] == 'reused'
    ordinary['value'] = 400
    (tmp_path/relative).write_text(json.dumps(ordinary))
    assert inherited(tmp_path, reference, state=state)['status'] == 'miss'
    evaluation_attempt(tmp_path, state, second, options, 'pkg/reader-two.json',
                       history='pkg/reader-history.json', expected='fail')


@pytest.mark.parametrize('overlay_defect', ['nonobject-record', 'nonobject-context'])
def test_malformed_current_history_cannot_override_captured_roles_or_hide_config(tmp_path, overlay_defect):
    state, plan, first, second, options = evaluation_contract(tmp_path)
    relative = 'pkg/ordinary-config.json'
    state['scope']['paths']['evaluate'].append(relative)
    (tmp_path/relative).write_text('3')
    (tmp_path/'pkg/app.py').write_text(
        f'name = "".join(map(chr, {list(relative.encode())!r}))\n'
        'value = int(open(name).read())\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    one = evaluation_attempt(tmp_path, state, first, options, 'pkg/eval-one.json')
    two = evaluation_attempt(tmp_path, state, second, options, 'pkg/eval-two.json')
    capture_evaluation_history(tmp_path, state)
    mutable_history = tmp_path/'pkg/evaluate-history.json'
    history = json.loads(mutable_history.read_text())
    malformed = [] if overlay_defect == 'nonobject-record' else Store(tmp_path).resolve(two)
    if overlay_defect == 'nonobject-context':
        malformed['source_context'] = []
        malformed['log_path'] = relative
    history['attempts'][-1]['record_ref'] = Store(tmp_path).put('verification-record', malformed)
    history['attempts'][-1]['evidence'] = relative
    mutable_history.write_text(json.dumps(history))
    reloaded = json.loads(json.dumps(state))
    assert inherited(tmp_path, one, state=reloaded)['status'] == 'reused'
    assert inherited(tmp_path, two, state=reloaded)['status'] == 'reused'
    (tmp_path/relative).write_text('400')
    assert inherited(tmp_path, one, state=reloaded)['status'] == 'miss'
    evaluation_attempt(tmp_path, reloaded, second, options, 'pkg/reader-one.json',
                       history='pkg/reader-history.json', expected='fail')
