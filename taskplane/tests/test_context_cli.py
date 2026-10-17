"""Actual shipped CLI defaults; explicit full output remains opt-in."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from taskplane import flow
from taskplane.tests.test_native_workflow_cli import create
from taskplane.context import Store

ROOT = Path(__file__).resolve().parents[2]

def command(workspace, *args, code=0):
    p = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', *args,
                        '--workspace', str(workspace)], capture_output=True, text=True,
                       env={**os.environ, 'CODEX_THREAD_ID': 'context-cli-root'})
    assert p.returncode == code, p.stdout+p.stderr
    return json.loads(p.stdout), len(p.stdout.encode())


def test_default_commands_context_and_full_details(tmp_path):
    create(tmp_path)
    start, size = command(tmp_path, 'start', '--scope', '.taskplane/scope.json',
                          '--request-reference', 'fixture/user-request')
    assert size <= 16384 and start['schema'] == 'taskplane.command-summary/v1'
    assert start['context']['status'] == 'ready'
    full, _ = command(tmp_path, 'report', '--full')
    assert full['workflow']['context_contract'] == 'bounded/v2'
    report, size = command(tmp_path, 'report')
    assert Store(tmp_path).resolve(report['details'])['workflow']['run'] == start['run']
    detail, size = command(tmp_path, 'context', '--read', report['details']['sha256'])
    assert detail['page']['sha256'] == report['details']['sha256'] and size <= 16384
    selected, _ = command(tmp_path, 'context', '--run', start['run'])
    consumed, size = command(tmp_path, 'context', '--consume', selected['handoff_ref']['sha256'])
    assert consumed['context_receipt'] and size <= 16384
    for args in [('advance', '--phase', 'design', '--expected-revision', '0'),
                 ('context', '--read', '0'*64), ('context', '--run', 'foreign')]:
        failed, size = command(tmp_path, *args, code=2)
        assert size <= 16384 and failed['errors']['blocking']
    for args in [('activate', '--phase', 'tp-go', '--request-reference', 'fixture/resume'),
                 ('wait', '--note', 'Fixture human input is needed.')]:
        result, size = command(tmp_path, *args)
        assert size <= 16384 and result['schema'] == 'taskplane.command-summary/v1'


def test_derived_storage_failure_preserves_committed_status(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs): raise OSError('fixture storage failure')
    monkeypatch.setattr(Store, 'put', fail)
    payload = {'status': 'approved', 'run': 'one', 'revision': 4}
    flow.emit(payload, tmp_path, 'decide')
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'approved' and result['binding']['revision'] == 4
    assert result['details'] is None and 'Do not replay' in result['next_action']


def test_cli_initial_tasks_and_batch_selection(tmp_path):
    create(tmp_path)
    tasks = {'tasks':[{'id':'OWN','phase':'product','owner':'root','dependencies':[],
                       'paths':['product.json'],'criteria':['AC1'],'verification':'Read required bodies'}]}
    (tmp_path/'initial.json').write_text(json.dumps(tasks))
    result, _ = command(tmp_path, 'start', '--scope', '.taskplane/scope.json', '--tasks', 'initial.json', '--request-reference', 'fixture/initial')
    prepared, _ = command(tmp_path, 'context', '--task', 'OWN')
    batch, size = command(tmp_path, 'context', '--task', 'OWN', '--read-required', prepared['handoff_ref']['sha256'])
    assert size < 16384 and batch['schema'] == 'taskplane.context-read-batch/v1'
    assert batch['pages'] and batch['remaining_required'] == 0
    full, _ = command(tmp_path, 'report', '--full')
    assert full['workflow']['decisions'] == {}
    assert full['workflow']['initial_context_tasks'][0]['id'] == 'OWN'
    failed, _ = command(tmp_path, 'context', '--read-required', '0'*64, code=2)
    assert failed['errors']['blocking']


@pytest.mark.parametrize('args', [
    ['context','--read-required','0'*64,'--page','0'],
    ['context','--read-required','0'*64,'--section',''],
    ['report','--read-required',''],
    ['context','--read-required','0'*64,'--consume','0'*64],
])
def test_batch_arguments_never_silently_ignore_selectors(tmp_path, args):
    result = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', *args,
                             '--workspace',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode == 2 and 'error:' in result.stderr
    assert not (tmp_path/'.taskplane').exists()


def test_cli_drain_is_bounded_and_terminal(tmp_path):
    create(tmp_path)
    start, _ = command(tmp_path, 'start', '--scope', '.taskplane/scope.json', '--request-reference', 'fixture/drain')
    key = start['context']['handoff_ref']['sha256']
    result, size = command(tmp_path, 'context', '--drain', key)
    assert size <= 32768 and result['done'] and result['next_action'] is None
    assert result['pages'] and result['remaining_required'] == 0
    final, size = command(tmp_path, 'context', '--drain', key)
    assert final['pages'] == [] and final['done'] and size <= 32768


def test_emitted_context_actions_execute_verbatim_outside_workspace(tmp_path, monkeypatch):
    import shlex
    from taskplane.tests.test_worker_runtime import setup, reserve, launch
    from taskplane.tests.test_native_worker_dispatch import metadata
    from taskplane import workflow_host as h
    workspace = tmp_path/'workspace with spaces'; workspace.mkdir()
    home = tmp_path/'codex'; logs = home/'sessions'; logs.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    c, s = setup(workspace, count=1)
    rows = json.loads((workspace/'tasks.json').read_text())
    rows['tasks'][0]['verification'] = 'Read every required section. ' * 1500
    (workspace/'.taskplane/large-tasks.json').write_text(json.dumps(rows))
    s = c.update_tasks(s['run'], s['revision'], '.taskplane/large-tasks.json')
    item = reserve(c, s); launch(c, s, item)
    metadata(logs/'native-0.jsonl', s, item['grant'], 'native-0')
    worker = h.Controller(workspace, 'root', h.installed_adapter('codex'), principal='native-0')
    claim = worker.worker(s['run'], 'claim', grant=item['grant']['grant_id'])
    action = claim['context']['next_action']
    continuations = []
    for _ in range(20):
        continuations.append(action)
        response = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *shlex.split(action)],
            cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
        assert response.returncode == 0, response.stdout + response.stderr
        delivered = json.loads(response.stdout)
        if delivered['remaining_required'] == 0: break
        action = delivered['next_action']
    else: pytest.fail('Bound continuations never delivered all required context')
    assert sum('--read-required' in command for command in continuations) >= 2
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'running'
    for task in ('T0', None):
        selected = c.context(s['run'], task=task)
        args = shlex.split(selected['next_action'])
        assert args[args.index('--workspace')+1] == str(workspace)
        assert args[args.index('--run')+1] == s['run']
        assert ('--task' in args) == bool(task)
        action = selected['next_action']
        for _ in range(20):
            delivered = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *shlex.split(action)],
                cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'root'}, capture_output=True, text=True)
            assert delivered.returncode == 0, delivered.stdout + delivered.stderr
            body = json.loads(delivered.stdout)
            if not body['remaining_required']: break
            action = body['next_action']
        else: pytest.fail('Root continuation did not finish required inputs')

    bad = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', 'context',
        '--workspace', str(workspace), '--run', s['run'], '--task', 'DEP'],
        cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
    assert bad.returncode == 2
    refused = json.loads(bad.stdout)
    assert refused['context']['status'] == 'blocked'
    assert refused['context']['next_action'] == refused['next_action']
    recovery = shlex.split(refused['next_action'])
    assert recovery[recovery.index('--task')+1] == 'T0'
    fresh = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *recovery], cwd=tmp_path,
        env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr


def test_fresh_process_indexed_context_and_concurrent_consumers(tmp_path):
    """LOCAL-SCA-01:1,2 — real restarts and concurrent journal writers."""
    from concurrent.futures import ThreadPoolExecutor
    from scripts.benchmark_context import isolated_environment
    script = ROOT/'scripts/benchmark_context.py'
    env = isolated_environment(tmp_path/'hosts')
    common = [sys.executable, str(script), '--workspace', str(tmp_path), '--nodes', '64']
    def invoke(operation, consumer='concurrent'):
        command = common + ['--operation', operation, '--sample-id', consumer]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, {'command': command, 'stdout': result.stdout, 'stderr': result.stderr}
        return json.loads(result.stdout)
    built = invoke('build')
    observed = set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, 'drain') for _ in range(2)]
        for result in [future.result() for future in futures]:
            for page in result['response']['pages']:
                key = page['sha256'], page['page']
                assert key not in observed
                observed.add(key)
    for _ in range(20):
        response = invoke('drain')['response']
        for page in response['pages']:
            key = page['sha256'], page['page']
            assert key not in observed
            observed.add(key)
        if response['done']: break
    else: pytest.fail('Concurrent fresh CLI drain failed to complete')
    validated = invoke('validate')
    assert set(validated['coverage_observed']) == set(built['coverage_expected'])
    assert len(observed) == built['index']['pages']
    independent = invoke('drain', 'independent')['response']
    assert independent['pages']  # previous consumer's receipts confer no coverage


@pytest.mark.taskplane_capacity
def test_context_measurement_grid(record_property, tmp_path):
    """LOCAL-SCA-01:3 — all raw samples, including failing bounds, are retained."""
    from scripts.benchmark_context import benchmark
    result = benchmark(tmp_path)
    record_property('remediation_measurement', json.dumps({
        'schema': 'taskplane.remediation-measurement/v1', 'subcheck': 'LOCAL-SCA-01',
        'scenario': 'LOCAL-SCA-01:3', 'kind': 'context', 'samples': result['samples'],
        'execution_status': result['execution_status'],
        'design_scenario_ids': ['SCA01-drain-grid', 'SCA01-cost']}))
    assert result['execution_status'] == 'pass', result
    assert len(result['samples']) == 33
    from scripts.ci_local import validate_measurements
    checked = validate_measurements('context', [{'schema': 'taskplane.remediation-measurement/v1',
        'subcheck': 'LOCAL-SCA-01', 'scenario': 'LOCAL-SCA-01:3', 'kind': 'context',
        'execution_status': result['execution_status'], 'samples': result['samples']}])
    assert checked['status'] == 'pass', checked['problems']
    for row in result['samples']:
        assert row['measurement_scope'] == 'full-controller-cli'
        assert set(row['coverage_expected']) == set(row['coverage_observed'])
        assert row['operations'] <= 12*(row['nodes'] + row['edges'] + row['pages']) + 32*row['invocations']
        assert row['bytes_read'] <= 3*row['unique_body_bytes'] + row['transport_bytes'] + 262144*row['invocations']
        assert row['max_response_pages'] <= 64 and row['max_response_bytes'] <= 32768
        assert row['max_index_rebuilds_per_invocation'] <= 1
    for row in result['timing']:
        assert row['timing_conclusive'], 'Resolution-dominated adjusted timing is inconclusive'
        assert row['adjusted_growth'] is None or row['adjusted_growth'] <= 6


@pytest.mark.taskplane_capacity
@pytest.mark.parametrize('shape', ['shared', 'high-edge', 'multipage'])
def test_context_measurement_variants(shape, record_property, tmp_path):
    """LOCAL-SCA-01:1 — variants are reported separately from the fixed primary grid."""
    from scripts.benchmark_context import sample
    row = sample(tmp_path, nodes=16, mode='cold', repetition=0, shape=shape)
    record_property('context_variant_measurement', json.dumps({
        'schema': 'taskplane.remediation-measurement/v1', 'subcheck': 'LOCAL-SCA-01',
        'scenario': 'LOCAL-SCA-01:1', 'kind': 'context', 'samples': [row],
        'design_scenario_ids': ['SCA01-drain-grid']}))
    assert row['execution_status'] == 'pass', row
    assert row['measurement_scope'] == 'full-controller-cli'
    assert set(row['coverage_expected']) == set(row['coverage_observed'])
    assert row['max_response_pages'] <= 64 and row['max_response_bytes'] <= 32768


def test_benchmark_isolation_precedes_candidate_children(tmp_path, monkeypatch):
    from scripts import benchmark_context as benchmark
    monkeypatch.setenv('HOME', '/ambient/home')
    monkeypatch.setenv('CODEX_HOME', '/ambient/codex')
    monkeypatch.setenv('XDG_CACHE_HOME', '/ambient/cache')
    monkeypatch.setenv('PYTHONPATH', '/ambient/imports')
    env = benchmark.isolated_environment(tmp_path/'isolated')
    for name in ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME',
                 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'TEMP'):
        assert Path(env[name]).is_relative_to(tmp_path)
        assert Path(env[name]).is_dir()
    assert 'PYTHONPATH' not in env and env['PYTHONDONTWRITEBYTECODE'] == '1'
    with pytest.raises(ValueError, match='outside candidate'):
        benchmark.isolated_environment(ROOT/'must-not-create')


def test_benchmark_retains_exact_failed_process_channels(tmp_path):
    import base64
    from scripts.benchmark_context import _process, isolated_environment
    env = isolated_environment(tmp_path/'hosts')
    argv = [sys.executable, '-c', "import os,sys;os.write(1,b'out\\x00\\xff');os.write(2,b'err\\xfe');sys.exit(7)"]
    record, stdout = _process(argv, cwd=tmp_path, env=env, evidence_root=tmp_path,
                              ordinal=0, purpose='failure-test')
    assert record['argv'] == argv and record['cwd'] == str(tmp_path.resolve())
    assert record['exit_code'] == 7
    assert stdout == base64.b64decode(record['stdout_base64']) == b'out\x00\xff'
    assert base64.b64decode(record['stderr_base64']) == b'err\xfe'
    retained = json.loads((tmp_path/'00000-failure-test.json').read_bytes())
    assert retained == record


def test_direct_benchmark_entry_isolates_before_candidate_imports(tmp_path):
    """Exercise the direct entry point without a pre-isolated child environment."""
    command = [sys.executable, str(ROOT/'scripts/benchmark_context.py'),
               '--operation', 'noop', '--workspace', str(tmp_path)]
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT')}
    env.update(HOME=str(tmp_path/'ambient-home'), CODEX_HOME=str(tmp_path/'ambient-codex'),
               CLAUDE_CONFIG_DIR=str(tmp_path/'ambient-claude'))
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=90)
    assert result.returncode == 0, {'argv': command, 'stdout': result.stdout, 'stderr': result.stderr}
    response = json.loads(result.stdout)
    assert response['bytecode_disabled'] is True
    for name, value in response['runtime_environment'].items():
        assert Path(value).is_relative_to(tmp_path/'isolated-hosts')
        assert Path(value).is_dir() and value != env.get(name)


def test_benchmark_missing_dependencies_are_retained_unknown(tmp_path, monkeypatch):
    from scripts import benchmark_context as benchmark
    monkeypatch.setattr(benchmark, 'CONTROLLER_DEPENDENCIES', ('missing-controller-for-test.py',))
    def forbidden(*args, **kwargs):
        pytest.fail('Candidate subprocess started before dependency preflight')
    monkeypatch.setattr(benchmark.subprocess, 'run', forbidden)
    row = benchmark.sample(tmp_path, nodes=64, mode='cold', repetition=0)
    assert row['execution_status'] == 'unknown' and row['raw_executions'] == []
    assert row['missing_dependencies'] == ['missing-controller-for-test.py']
    assert 'operations' not in row and 'elapsed_seconds' not in row
    assert json.loads((tmp_path/'benchmark-evidence/primary-64-cold-0.json').read_bytes()) == row
