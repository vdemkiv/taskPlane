import json
from pathlib import Path
import shlex
import pytest
from taskplane import host_capabilities as h


def executable(tmp_path, monkeypatch):
    workspace = tmp_path/'repo'; workspace.mkdir()
    cli = tmp_path/'codex'; cli.write_text('not executed')
    monkeypatch.setenv('CODEX_THREAD_ID', 'session')
    monkeypatch.setattr(h.shutil, 'which', lambda name: str(cli))
    for key in list(h.os.environ):
        if key.startswith(('LD_', 'DYLD_', 'BASH_FUNC_')) or key in {'ENV','BASH_ENV','SHELLOPTS','BASHOPTS'}:
            monkeypatch.delenv(key)
    return workspace, cli


def test_fixed_profile_roundtrip_and_shell_effects_refuse(tmp_path, monkeypatch):
    ws, cli = executable(tmp_path, monkeypatch)
    if h.os.name != 'posix':
        # This restored profile deliberately uses /bin/sh and is POSIX-only.
        assert h.codex_readonly_runtime(str(ws)) is None
        with pytest.raises(ValueError, match='unavailable'):
            h.codex_readonly_command(['python3', '-c', 'print(1)'], str(ws))
        return
    request = h.codex_readonly_command(['python3', '-c', 'print("$HOME; $(id)")'], str(ws))
    assert h.is_codex_readonly_invocation('exec_command', request, str(ws))
    assert not h.is_codex_readonly_invocation('Bash', {'command':request['cmd']}, str(ws))
    assert h.is_codex_readonly_invocation('Bash', {'command':request['cmd']}, str(ws), pending=request)
    for delta in [{'cmd':request['cmd']+'; touch bad'}, {'login':True}, {'shell':'/bin/zsh'}, {'workdir':str(tmp_path)}, {'extra':'unsafe'}]:
        assert not h.is_codex_readonly_invocation('exec_command', request|delta, str(ws))
    assert shlex.split(request['cmd'])[0] == str(cli)


def test_workspace_and_injected_executables_are_not_native_authority(tmp_path, monkeypatch):
    ws, _ = executable(tmp_path, monkeypatch)
    local = ws/'codex'; local.write_text('not executed')
    monkeypatch.setattr(h.shutil, 'which', lambda name: str(local))
    assert h.codex_readonly_runtime(str(ws)) is None
    monkeypatch.setenv('BASH_ENV', '/untrusted')
    assert h.codex_readonly_runtime(str(ws)) is None


def call(identity='one', name='exec_command', raw='{"cmd":"read"}'):
    return {'type':'response_item','payload':{'type':'function_call','name':name,'arguments':raw,'call_id':identity}}


def test_pending_call_requires_complete_unambiguous_records():
    done = {'type':'response_item','payload':{'type':'function_call_output','call_id':'one'}}
    assert h.pending_codex_tool_call('exec_command',[call()]) == {'cmd':'read'}
    for rows in [[call(),done], [call(),call('two')], [call(),done,call()], [done,call()], [call(raw='{cmd: execute()}')]]:
        assert h.pending_codex_tool_call('exec_command',rows) is None
    direct = call(name='functions.exec', raw='text(await tools.exec_command({"cmd":"read"}));')
    assert h.pending_codex_tool_call('exec_command',[direct]) == {'cmd':'read'}
    direct['payload']['arguments'] += ' do_more();'
    assert h.pending_codex_tool_call('exec_command',[direct]) is None


def plugin(path, version, host='codex'):
    (path/'taskplane').mkdir(parents=True)
    (path/'taskplane/tp.py').write_text('not executed')
    manifest=path/('.codex-plugin' if host=='codex' else '.claude-plugin')
    manifest.mkdir();(manifest/'plugin.json').write_text(json.dumps({'name':'taskplane','version':version}))


def test_plugin_family_order_and_symlink_rejection(tmp_path):
    for version in ['2.23.1', '2.25.0+codex.20260916010000', '2.25.0+codex.20260916020000']:
        plugin(tmp_path/version, version)
    assert '20260916020000' in h.resolve_plugin_engine(str(tmp_path))
    latest = tmp_path/'2.25.0+codex.20260916020000/taskplane/tp.py'
    latest.unlink();latest.symlink_to(tmp_path/'2.23.1/taskplane/tp.py')
    assert '20260916010000' in h.resolve_plugin_engine(str(tmp_path))
    assert h.valid_plugin_root(str(tmp_path.parent),str(tmp_path)) is None


def test_observations_never_certify_protection(tmp_path):
    row={'host':'codex','session':'s','workspace':str(tmp_path),'event':'PreToolUse','human_origin':True}
    assert h.runtime_hook_observations([row],host='codex',session='s',workspace=str(tmp_path))['events_seen']==['PreToolUse']
    assert h.runtime_hook_observations([row],host='codex',session='other',workspace=str(tmp_path))['events_seen']==[]
    assert not h.runtime_hook_observations([row],host='codex',session='s',workspace=str(tmp_path))['authority_verified']
    assert not h.inspect_native('codex',tmp_path,'s')['authority_verified']


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('version', ['2.33.4-policy-reply.1', '2.33.4-initialization.1'])
def test_explicit_candidate_runtime_is_valid_without_changing_family_selection(tmp_path, host, version):
    candidate = tmp_path/version
    plugin(candidate, version, host)
    selected = h.valid_plugin_root(str(candidate), str(candidate), host)
    assert selected == ((2, 33, 4), version, str(candidate/'taskplane/tp.py'))
    # Explicit runtime diagnostics must not opt a family into prerelease discovery.
    assert h.resolve_plugin_engine(str(tmp_path), host) is None
    plugin(tmp_path/'2.33.3', '2.33.3', host)
    assert h.resolve_plugin_engine(str(tmp_path), host) == str(tmp_path/'2.33.3/taskplane/tp.py')


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('version', ['2.33.4/other', '2.33.4-', '2.33.4 candidate'])
def test_explicit_runtime_rejects_malformed_versions(tmp_path, host, version):
    plugin(tmp_path, version, host)
    assert h.valid_plugin_root(str(tmp_path), str(tmp_path), host) is None


def _large_recovery_fixture(tmp_path, monkeypatch, *, records=6, padding=500000):
    from taskplane.tests.test_unbound_worker_recovery import transcript_fixture
    worker, root, child, paths = transcript_fixture(tmp_path, monkeypatch)
    filler = {'type': 'event_msg', 'timestamp': child[1]['timestamp'],
              'payload': {'type': 'fixture', 'padding': 'x' * padding}}
    for rows, path in zip((root, child), paths):
        with path.open('w') as stream:
            stream.write(json.dumps(rows[0]) + '\n')
            for _ in range(records):
                stream.write(json.dumps(filler) + '\n')
            for row in rows[1:]:
                stream.write(json.dumps(row) + '\n')
    return worker, paths


@pytest.mark.skipif(not h.supported_recovery_reader(), reason='Requires no-follow nonblocking reader')
def test_complete_parent_and_child_over_64mib_use_bounded_continuations(tmp_path, monkeypatch):
    from taskplane.tests.test_unbound_worker_recovery import transcript_fixture, write_fixture
    worker, root, child, paths = transcript_fixture(tmp_path, monkeypatch)
    write_fixture(root, child, paths)
    oracle = h.unbound_worker_observation('root', worker, 'launch', 'terminal')
    filler = {'type': 'event_msg', 'timestamp': child[1]['timestamp'],
              'payload': {'type': 'fixture', 'padding': 'x' * 900000}}
    for rows, path in zip((root, child), paths):
        with path.open('w') as stream:
            stream.write(json.dumps(rows[0]) + '\n')
            for _ in range(76):
                stream.write(json.dumps(filler) + '\n')
            for row in rows[1:]:
                stream.write(json.dumps(row) + '\n')
        assert path.stat().st_size > 64 * 1024 * 1024
    cursor = None
    operations = []
    for _ in range(12):
        result = h.unbound_worker_observation('root', worker, 'launch', 'terminal', cursor=cursor)
        operation = result.get('operation', result.get('scan', {}).get('operation'))
        operations.append(operation)
        assert operation['bytes'] <= 64 * 1024 * 1024
        assert operation['records'] <= h.MAX_RECOVERY_RECORDS
        if result.get('status') != 'incomplete':
            break
        assert not result['reservation_released'] and not result['result_accepted']
        cursor = json.loads(json.dumps(result['cursor']))  # Fresh process serialization.
        assert len(json.dumps(cursor).encode()) <= h.MAX_RECOVERY_CURSOR_BYTES
    else:
        pytest.fail('Bounded recovery did not complete')
    assert len(operations) >= 3
    for key in ('worker_id', 'canonical_name', 'native_status', 'sha256', 'assurance', 'process_exit'):
        assert result[key] == oracle[key]
    assert all(s['records'] >= 78 for s in result['scan']['snapshots'])


@pytest.mark.parametrize('change', ['append', 'truncate', 'replace', 'prefix', 'duplicate', 'foreign_cursor'])
def test_recovery_continuation_refuses_changed_snapshot(tmp_path, monkeypatch, change):
    from taskplane import workflow as w
    if not h.supported_recovery_reader():
        pytest.skip('Requires no-follow nonblocking reader')
    worker, paths = _large_recovery_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(h, 'MAX_RECOVERY_BYTES', 2 * 1024 * 1024)
    first = h.unbound_worker_observation('root', worker, 'launch', 'terminal')
    assert first['status'] == 'incomplete'
    cursor = first['cursor']; path = paths[0]
    if change == 'append':
        with path.open('a') as stream: stream.write('{}\n')
    elif change == 'truncate':
        path.write_bytes(path.read_bytes()[:100])
    elif change == 'replace':
        raw = path.read_bytes(); path.unlink(); path.write_bytes(raw)
    elif change == 'prefix':
        with path.open('r+b') as stream: stream.write(b'X')
    elif change == 'duplicate':
        path.with_name('duplicate-root.jsonl').write_bytes(path.read_bytes())
    else:
        cursor['key'] = '0' * 64
    before = json.loads(json.dumps(cursor))
    with pytest.raises(w.Refusal):
        h.unbound_worker_observation('root', worker, 'launch', 'terminal', cursor=cursor)
    assert cursor == before


def test_recovery_retry_has_deterministic_progress_and_detects_late_conflict(tmp_path, monkeypatch):
    from taskplane import workflow as w
    if not h.supported_recovery_reader():
        pytest.skip('Requires no-follow nonblocking reader')
    worker, paths = _large_recovery_fixture(tmp_path, monkeypatch)
    with paths[0].open('a') as stream:
        stream.write(json.dumps({'type': 'response_item', 'timestamp': worker['prepared_at'],
            'metadata': {'client_authored': False}, 'payload': {'type': 'function_call',
            'namespace': 'collaboration', 'name': 'send_message', 'call_id': 'late',
            'arguments': json.dumps({'target': '/root/' + worker['task_name']})}}) + '\n')
    monkeypatch.setattr(h, 'MAX_RECOVERY_BYTES', 2 * 1024 * 1024)
    one = h.unbound_worker_observation('root', worker, 'launch', 'terminal')
    replay = h.unbound_worker_observation('root', worker, 'launch', 'terminal')
    assert one['cursor'] == replay['cursor']
    cursor = one['cursor']
    for _ in range(12):
        try:
            result = h.unbound_worker_observation('root', worker, 'launch', 'terminal', cursor=cursor)
        except w.Refusal as exc:
            assert 'Later native input' in exc.detail
            break
        assert result['status'] == 'incomplete'
        cursor = result['cursor']
    else:
        pytest.fail('Later activity was not detected')


def test_full_startup_timing_is_unavailable_without_supported_native_provider(monkeypatch):
    from taskplane import host_capabilities as caps
    monkeypatch.setenv('TASKPLANE_FULL_NATIVE_TIMING', 'true')
    runtime = {'root': 'fixture', 'member_sha256': {'tp.py': 'abc'}}
    for host in ('codex', 'claude', 'unknown'):
        observed = caps.startup_timing_capability(host, runtime)
        assert observed['status'] == 'unavailable' and observed['clock'] is None
        assert not caps.qualified_startup_timing(observed, host, runtime)
        assert not caps.qualified_startup_timing({**observed, 'status': 'qualified'}, host, runtime)


def test_timing_qualification_rejects_partial_boundary_foreign_runtime_and_flags():
    from taskplane import host_capabilities as caps
    from taskplane.tests.test_context_startup_budget import qualified_capability
    runtime = {'root': 'fixture-runtime'}
    capability = qualified_capability('codex', runtime)
    assert caps.qualified_startup_timing(capability, 'codex', runtime)
    for change in ({'boundary': ['process_start']}, {'runtime_sha256': 'foreign'},
                   {'gaps': ['teardown']}, {'clock': None}, {'boundary_test_reference': ''},
                   {'host': 'claude'}, {'coverage': 'partial'}):
        assert not caps.qualified_startup_timing({**capability, **change}, 'codex', runtime)
