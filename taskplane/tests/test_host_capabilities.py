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
