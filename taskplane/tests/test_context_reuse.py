"""Matching provenance reuses observations; every relevant change invalidates it."""
import sys
from copy import deepcopy
import pytest
from taskplane import depgraph
from taskplane.context import Store
from taskplane.context_reuse import key, record, lookup, run_check, inherited


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


def _command_check(tmp_path, command, **kwargs):
    options = fixture(tmp_path)
    options['command'] = [sys.executable, '-c', command]
    provenance = key(tmp_path, **options)
    reference = run_check(tmp_path, provenance, producer='bounded-output-regression', **kwargs)
    return Store(tmp_path).resolve(reference), reference, provenance


def test_combined_output_limit_stops_execution_and_bounds_typed_evidence(tmp_path, monkeypatch):
    import json
    from taskplane import context_reuse as reuse
    monkeypatch.setattr(reuse, 'MAX_OUTPUT_BYTES', 8192, raising=False)
    command = "import os,time; os.write(1,b'a'*5000); os.write(2,b'b'*5000); time.sleep(1); open('finished','w').close()"
    row, reference, provenance = _command_check(tmp_path, command, timeout=5,
        history_path='.taskplane/checks/history.json', log_path='.taskplane/checks/output.json',
        run='run', visit='visit', check_id='bounded', kind='unit', environment='local')
    assert row['status'] == 'unknown'
    assert row['result']['reason'] == 'output_limit'
    assert row['result']['incomplete'] is True
    assert row['result']['captured_output_bytes'] <= 8192
    assert 'stdout' not in row['result'] and 'stderr' not in row['result']
    assert not (tmp_path/'finished').exists()
    assert lookup(Store(tmp_path), reference, provenance)['status'] == 'miss'
    raw = (tmp_path/'.taskplane/checks/output.json').read_bytes()
    assert len(raw) < 10000 and json.loads(raw.splitlines()[-1]) == row['result']
    history = json.loads((tmp_path/'.taskplane/checks/history.json').read_text())
    assert history['attempts'][0]['status'] == 'unknown'
    assert Store(tmp_path).resolve(row['log_ref'])['text'] == raw.decode()


def test_both_pipes_are_drained_and_exact_combined_boundary_passes(tmp_path, monkeypatch):
    from taskplane import context_reuse as reuse
    monkeypatch.setattr(reuse, 'MAX_OUTPUT_BYTES', 262144, raising=False)
    command = ("import os,threading; "
        "t=threading.Thread(target=lambda: os.write(2,b'b'*131072)); "
        "t.start(); os.write(1,b'a'*131072); t.join()")
    row, _, _ = _command_check(tmp_path, command, timeout=5)
    assert row['status'] == 'pass'
    assert row['result']['stdout'] == 'a'*131072
    assert row['result']['stderr'] == 'b'*131072


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group regression')
@pytest.mark.parametrize('overflow', [False, True])
def test_incomplete_check_stops_descendants_and_reaps_direct_child(tmp_path, monkeypatch, overflow):
    import os
    import time
    from taskplane import context_reuse as reuse
    monkeypatch.setattr(reuse, 'MAX_OUTPUT_BYTES', 8192, raising=False)
    # The child inherits both pipes and records a heartbeat. It must stop even
    # when its direct parent exits before the timeout or output limit fires.
    child = ("import os,time; open('descendant.pid','w').write(str(os.getpid())); "
             "[(open('heartbeat','w').write(str(i)),time.sleep(.02)) for i in range(150)]")
    command = ("import subprocess,sys,os,time; open('parent.pid','w').write(str(os.getpid())); "
               f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
               "time.sleep(.2); " + ("os.write(1,b'x'*10000)" if overflow else "pass"))
    row, _, _ = _command_check(tmp_path, command, timeout=.5)
    assert row['status'] == 'unknown'
    assert row['result']['reason'] == ('output_limit' if overflow else 'timeout')
    before = (tmp_path/'heartbeat').read_text()
    time.sleep(.15)
    assert (tmp_path/'heartbeat').read_text() == before
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path/'parent.pid').read_text()), 0)


def test_launch_error_and_input_drift_remain_nonpass(tmp_path):
    options = fixture(tmp_path)
    options['command'] = ['/definitely/missing/taskplane-check']
    provenance = key(tmp_path, **options)
    reference = run_check(tmp_path, provenance, producer='launch-error')
    row = Store(tmp_path).resolve(reference)
    assert row['status'] == 'unknown' and row['result']['reason'] == 'launch_error'
    options['command'] = [sys.executable, '-c', "open('pkg/app.py','w').write('value = 4\\n')"]
    provenance = key(tmp_path, **options)
    reference = run_check(tmp_path, provenance, producer='input-drift')
    row = Store(tmp_path).resolve(reference)
    assert row['status'] == 'unknown' and row['result']['reason'] == 'input_drift_during_execution'


@pytest.mark.parametrize('cleanup_times_out', [False, True])
def test_windows_cleanup_failure_is_bounded_and_never_complete(tmp_path, monkeypatch, cleanup_times_out):
    import os
    import subprocess
    from taskplane import context_reuse as reuse
    class Process:
        pid = 12345
        returncode = None
        killed = False
        def __init__(self):
            self.stdout = open(os.devnull, 'rb')
            self.stderr = open(os.devnull, 'rb')
        def poll(self):
            return self.returncode
        def kill(self):
            self.killed = True
            self.returncode = -9
        def wait(self, timeout=None):
            assert timeout is not None, 'cleanup wait must have a deadline'
            if cleanup_times_out:
                raise subprocess.TimeoutExpired('fixture', timeout)
            return self.returncode
    process = Process()
    calls = []
    def failed_taskkill(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(reuse.os, 'name', 'nt')
            patch.setattr(reuse.subprocess, 'Popen', lambda *args, **kwargs: process)
            patch.setattr(reuse.subprocess, 'run', failed_taskkill)
            result = reuse._bounded_command(tmp_path, ['fixture'], 0)
        assert process.killed and calls == [['taskkill', '/PID', '12345', '/T', '/F']]
        assert result['incomplete'] is True
        if cleanup_times_out:
            assert 'process_wait_timeout' in result['cleanup_errors']
    finally:
        process.stdout.close()
        process.stderr.close()


def test_typed_output_is_visible_during_execution_without_duplicate_payload(tmp_path):
    import json
    command = '''import pathlib,time
print('visible-before-exit', flush=True)
p = pathlib.Path('.taskplane/checks/live.log')
end = time.monotonic() + 1
while time.monotonic() < end:
    if p.exists() and 'visible-before-exit' in p.read_text():
        break
    time.sleep(.01)
else:
    raise SystemExit(7)
'''
    row, _, _ = _command_check(tmp_path, command, timeout=3,
        history_path='.taskplane/checks/history.json', log_path='.taskplane/checks/live.log',
        run='run', visit='visit', check_id='streaming', kind='unit', environment='local')
    assert row['status'] == 'pass'
    assert 'visible-before-exit' not in json.dumps(row['result'])
    raw = (tmp_path/'.taskplane/checks/live.log').read_text()
    assert 'visible-before-exit' in raw
    assert Store(tmp_path).resolve(row['log_ref'])['text'] == raw


@pytest.mark.parametrize('payload,limit', [(b'\xe2\x82\xac', 3), (b'\xff'*20, 20), (b'\xe2\x82\xac', 2)])
def test_typed_stream_handles_split_utf8_and_raw_byte_limit(tmp_path, monkeypatch, payload, limit):
    import json
    from taskplane import context_reuse as reuse
    monkeypatch.setattr(reuse, 'MAX_OUTPUT_BYTES', limit)
    command = f'import os,time; [(os.write(1,bytes([b])),time.sleep(.02)) for b in {payload!r}]'
    row, _, _ = _command_check(tmp_path, command, timeout=3,
        history_path='.taskplane/checks/history.json', log_path='.taskplane/checks/utf8.log',
        run='run', visit='visit', check_id='unicode', kind='unit', environment='local')
    raw = (tmp_path/'.taskplane/checks/utf8.log').read_bytes()
    body, footer = raw.decode().rsplit('\n', 2)[:2]
    assert body == payload[:limit].decode('utf-8', errors='replace')
    assert len(body.encode()) <= 3*limit
    assert json.loads(footer) == row['result']
    assert row['result']['captured_output_bytes'] == limit
    assert row['status'] == ('unknown' if len(payload) > limit else 'pass')


@pytest.mark.parametrize('change', ['launch', 'before', 'during'])
def test_typed_failed_attempt_keeps_immutable_log(tmp_path, change):
    import json
    options = fixture(tmp_path)
    if change == 'launch':
        options['command'] = ['/definitely/missing/taskplane-check']
    if change == 'during':
        options['command'] = [sys.executable, '-c', "open('pkg/app.py','w').write('value = 4\\n')"]
    provenance = key(tmp_path, **options)
    if change == 'before':
        (tmp_path/'pkg/app.py').write_text('value = 5\n')
    kwargs = dict(producer='failure', history_path='.taskplane/checks/history.json', log_path='.taskplane/checks/failed.log',
        run='run', visit='visit', check_id='failure', kind='unit', environment='local')
    row = Store(tmp_path).resolve(run_check(tmp_path, provenance, **kwargs))
    assert row['status'] == 'unknown'
    raw = (tmp_path/'.taskplane/checks/failed.log').read_bytes()
    assert json.loads(raw.splitlines()[-1]) == row['result']
    assert Store(tmp_path).resolve(row['log_ref'])['text'].encode() == raw
    with pytest.raises(Exception, match='new distinct declared log path'):
        run_check(tmp_path, provenance, **kwargs)
    assert (tmp_path/'.taskplane/checks/failed.log').read_bytes() == raw
