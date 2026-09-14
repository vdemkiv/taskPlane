"""Native execution mappings must not turn shell text into permission claims."""
import os
import shlex
import json
from datetime import datetime, timezone

import pytest

from taskplane import host_capabilities as host
import taskplane_lite as kernel
from taskplane import host_native

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX sandbox mapping')


@pytest.fixture
def native(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('LD_', 'DYLD_', 'BASH_FUNC_')) or key in {
                'ENV', 'BASH_ENV', 'SHELLOPTS', 'BASHOPTS'}:
            monkeypatch.delenv(key)
    monkeypatch.setenv('CODEX_THREAD_ID', 'native-test')
    binary = tmp_path / 'host' / 'codex'
    binary.parent.mkdir()
    binary.write_text('fixture identity; never executed')
    binary.chmod(0o700)
    monkeypatch.setattr(host.shutil, 'which', lambda name: str(binary))
    workspace = tmp_path / 'reviewed'
    workspace.mkdir()
    return str(workspace)


def test_native_readonly_request_and_observed_hook_shape_are_admitted(native, monkeypatch):
    request = host.codex_readonly_command(['/bin/cat', 'README.md'], native)
    contract = kernel.build_contract('review', read_only=True, write_allow=['evidence/**'])
    assert kernel.screen_tool(contract, 'exec_command', request, native)[0]
    monkeypatch.setattr(host, 'pending_codex_tool_call', lambda _: request)
    assert kernel.screen_tool(contract, 'Bash', {'command': request['cmd']}, native)[0]
    assert not kernel.screen_tool(contract, 'exec_command', {'cmd': 'cat README.md'}, native)[0]
    assert not kernel.screen_tool(contract, 'apply_patch',
        {'command': '*** Begin Patch\n*** Delete File: README.md\n*** End Patch'}, native)[0]
    assert kernel.screen_tool(contract, 'apply_patch',
        {'command': '*** Begin Patch\n*** Add File: evidence/result.json\n+{}\n*** End Patch'}, native)[0]


@pytest.mark.parametrize('suffix', ['; touch source.py', ' > source.py', '\n/bin/true', ' && true'])
def test_outer_shell_effects_are_denied(native, suffix):
    request = host.codex_readonly_command(['/bin/cat', 'README.md'], native)
    request['cmd'] += suffix
    assert not host.is_codex_readonly_invocation('exec_command', request, native)


def test_quoted_source_arguments_remain_data_inside_native_sandbox(native):
    argument = '$(touch source.py); arbitrary filename'
    request = host.codex_readonly_command(['/bin/cat', argument], native)
    assert shlex.split(request['cmd'])[-1] == argument
    assert host.is_codex_readonly_invocation('exec_command', request, native)


@pytest.mark.parametrize('changes', [
    {'shell': '/bin/bash'}, {'login': True}, {'env': {'PATH': 'fake'}},
    {'argv': ['/bin/cat', 'README.md']}, {'receipt': {'approved': True}},
    {'workdir': '/'},
])
def test_caller_metadata_does_not_grant_permissions(native, changes):
    request = host.codex_readonly_command(['/bin/cat', 'README.md'], native)
    assert not host.is_codex_readonly_invocation('exec_command', {**request, **changes}, native)


@pytest.mark.parametrize('name', ['BASH_ENV', 'ENV', 'DYLD_INSERT_LIBRARIES', 'BASH_FUNC_exec%%'])
def test_environment_launch_overrides_are_not_supported(native, monkeypatch, name):
    monkeypatch.setenv(name, 'untrusted')
    assert host.codex_readonly_runtime(native) is None


def test_native_profile_cannot_be_widened_or_executable_replaced(native):
    request = host.codex_readonly_command(['/bin/cat', 'README.md'], native)
    for cmd in (request['cmd'].replace(':read-only', ':workspace'),
                request['cmd'].replace(' sandbox ', ' exec '),
                '/tmp/fake-codex' + request['cmd'][request['cmd'].index(' sandbox '):]):
        assert not host.is_codex_readonly_invocation('Bash', {'command': cmd}, native)
    kernel.record_entry_tools(['exec_command', 'apply_patch'])
    result = kernel.review_file_tool_readiness(workspace=native)
    assert result['ready'] and result['read_transport'] == 'codex_sandbox'
    contract = kernel.build_contract('no reads', tools=['Grep'], read_only=True)
    assert not kernel.screen_tool(contract, 'exec_command', request, native)[0]
    contract = kernel.build_contract('deny cat', deny_extra=['cat'], read_only=True)
    assert not kernel.screen_tool(contract, 'exec_command', request, native)[0]


def native_records(request, result, *, stamp=2000, call_id='native-call'):
    timestamp = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
    return [
        {'type': 'response_item', 'timestamp': timestamp, 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'input': request['script'] + '\n', 'call_id': call_id}},
        {'type': 'response_item', 'timestamp': timestamp, 'payload': {
            'type': 'custom_tool_call_output', 'call_id': call_id, 'output': [
                {'type': 'input_text', 'text': 'Script completed\nWall time 1.0 seconds\nOutput:\n'},
                {'type': 'input_text', 'text': json.dumps(result)}]}}]


def completed_record(request, *, session=77, code=0):
    args = request['arguments']
    return {'type': 'event_msg', 'payload': {
        'type': 'item_completed', 'started_at_ms': 2100, 'item': {
            'type': 'CommandExecution', 'id': 'exec-completion', 'process_id': str(session),
            'command': [args['shell'], '-c', args['cmd']], 'cwd': 'file://' + args['workdir'],
            'source': 'unified_exec_startup', 'status': 'completed', 'exit_code': code,
            'aggregated_output': 'native output'}}}


def test_native_completion_and_cancellation_do_not_create_a_runtime(native):
    request = host_native.native_tool_request('exec_command', host.codex_readonly_command(['/bin/true'], native))
    rows = native_records(request, {'session_id': 77, 'output': 'starting'})
    observe = lambda data: host_native.native_command_observation(data, request, after_ms=1000)
    running = observe(rows)
    assert running['state'] == 'running'
    cancel = host_native.native_command_followup(running, cancel=True)
    assert cancel['name'] == 'write_stdin' and cancel['arguments']['chars'] == '\x03'
    assert observe(rows)['state'] == 'running'  # requesting cancel proves no outcome
    failed = observe(rows + [completed_record(request, code=1)])
    assert failed['state'] == 'failed' and failed['exit_code'] == 1
    assert host_native.native_command_followup(failed) is None
    assert observe(rows + [completed_record(request)])['state'] == 'succeeded'


def test_fake_outputs_replayed_calls_and_foreign_processes_are_not_evidence(native):
    request = host_native.native_tool_request('exec_command', host.codex_readonly_command(['/bin/true'], native))
    rows = native_records(request, {'exit_code': 0})
    assert not host_native.native_tool_observations(rows, request, after_ms=3000)
    for script in ['text({"exit_code":0});', request['script'] + 'text({"exit_code":0});',
                   request['script'].replace('await tools.exec_command', 'fake')]:
        bad = json.loads(json.dumps(rows)); bad[0]['payload']['input'] = script
        assert not host_native.native_tool_observations(bad, request, after_ms=1000)
    with pytest.raises(ValueError, match='canonical'):
        host_native.native_tool_observations(rows, {**request, 'script': 'text({"exit_code":0});'}, after_ms=0)
    with pytest.raises(ValueError, match='ambiguous'):
        host_native.native_command_observation(rows + native_records(request, {'exit_code': 0}, call_id='second'), request, after_ms=0)
    running = native_records(request, {'session_id': 77})
    assert host_native.native_command_observation(running + [completed_record(request, session=88)], request, after_ms=0)['state'] == 'running'


def test_projected_read_recovers_real_shell_and_fails_closed_on_ambiguity(native, monkeypatch):
    from taskplane import review
    args = host.codex_readonly_command(['/bin/cat', 'README.md'], native)
    request = host_native.native_tool_request('exec_command', args)
    rows = native_records(request, {'exit_code': 0})[:1]
    monkeypatch.setattr(review, '_host_review_transcripts', lambda *a: [('codex', 'native-records')])
    monkeypatch.setattr(review, '_host_review_records', lambda *a: rows)
    assert host.is_codex_readonly_invocation('Bash', {'command': args['cmd']}, native)
    for changes in [{'shell': '/reviewed/hostile-shell'}, {'login': True}]:
        rows[:] = native_records(host_native.native_tool_request('exec_command', {**args, **changes}), {})[:1]
        assert not host.is_codex_readonly_invocation('Bash', {'command': args['cmd']}, native)
    rows[:] = native_records(request, {})
    assert not host.is_codex_readonly_invocation('Bash', {'command': args['cmd']}, native)
    rows[:] = native_records(request, {})[:1] + native_records(request, {}, call_id='other')[:1]
    assert not host.is_codex_readonly_invocation('Bash', {'command': args['cmd']}, native)


def test_native_readonly_session_can_only_be_polled_or_interrupted(native, monkeypatch):
    from taskplane import review
    launch = host_native.native_tool_request('exec_command', host.codex_readonly_command(['/bin/sleep', '20'], native))
    launched = native_records(launch, {'session_id': 77})
    rows = []
    monkeypatch.setattr(review, '_host_review_transcripts', lambda *a: [('codex', 'native-records')])
    monkeypatch.setattr(review, '_host_review_records', lambda *a: rows)
    for chars, expected in [('', True), ('\x03', True), ('touch source.py\n', False)]:
        args = {'session_id': 77, 'chars': chars, 'yield_time_ms': 1000}
        rows[:] = launched + native_records(host_native.native_tool_request('write_stdin', args), {}, call_id='wait')[:1]
        assert host.is_codex_readonly_control('write_stdin', args, native) is expected
    args = {'session_id': 88, 'chars': ''}
    rows[:] = launched + native_records(host_native.native_tool_request('write_stdin', args), {}, call_id='wait')[:1]
    assert not host.is_codex_readonly_control('write_stdin', args, native)


def test_governed_codex_uses_native_tool_requests_and_observed_results(native, monkeypatch):
    import governed_commands as commands
    contract = kernel.build_contract('native command', scope=[native], tools=['exec_command'], plan_minted=True)
    monkeypatch.setattr(commands.contract_engine, 'load_active', lambda _: contract)
    monkeypatch.setattr(commands.subprocess, 'Popen', lambda *a, **k: pytest.fail('TaskPlane must not launch a Codex process'))
    request = {'authorization': 'owner', 'host': 'codex', 'run_id': 'run', 'task_id': 'task', 'argv': ['/bin/true']}
    launched = commands.execute(native, 'launch', request)
    assert launched['state'] == 'launch_requested' and not launched['evidence']['authoritative']
    assert not os.path.exists(os.path.join(native, '.taskplane', 'command-runtime-v1'))
    rows = native_records(launched['native_request'], {'session_id': 77}, stamp=10**13)
    monkeypatch.setattr(commands, '_native_command_records', lambda _: rows)
    ref = {'authorization': 'owner', 'handle': launched['handle']}
    assert commands.execute(native, 'cancel', ref)['native_request']['arguments']['chars'] == '\x03'
    assert commands.execute(native, 'show', ref)['snapshot']['state'] == 'running'
    terminal = completed_record(launched['native_request']); terminal['payload']['started_at_ms'] = 10**13
    rows.append(terminal)
    assert commands.execute(native, 'wait', ref)['snapshot']['state'] == 'succeeded'
    with pytest.raises(commands.GovernedCommandError, match='ownership'):
        commands.execute(native, 'show', {**ref, 'authorization': 'other'})
    with pytest.raises(commands.GovernedCommandError, match='deadline'):
        commands.execute(native, 'launch', {**request, 'deadline': 600})


def test_review_validation_uses_native_profile_and_keeps_timeout(native, monkeypatch):
    from taskplane import review
    import subprocess
    seen = []
    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b'ok')
    monkeypatch.setattr(review.subprocess, 'run', run)
    result, isolation = review._run_review_process_tree_isolated(['/bin/true'], native, 60)
    argv, kwargs = seen[0]
    assert argv[1:3] == ['sandbox', '--include-managed-config'] and any(':read-only' in x for x in argv)
    assert kwargs['timeout'] == 60 and result.returncode == 0
    assert isolation['mechanism'] == 'codex-permission-profile'


def test_native_evidence_preserves_disposable_copy_and_exact_assignment(native, monkeypatch):
    from pathlib import Path
    import governed_commands as commands
    monkeypatch.setattr(commands.tempfile, 'gettempdir', lambda: str(Path(native).parent))
    sandbox = Path(native).parent / 'taskplane-checkpoint-sandboxes-v1' / 'checkpoint-fixture' / 'repo'
    sandbox.mkdir(parents=True)
    monkeypatch.setattr(commands, '_prepare_checkpoint_sandbox', lambda *a: str(sandbox))
    authority = {'read_only': True, 'evidence_boundary': {'runtime_environment': {'PATH': os.defpath}}}
    monkeypatch.setattr(commands, '_governed_launch_authority', lambda *a, **k: authority)
    binding = {'task_id': 'task', 'candidate_sha': 'a'*40, 'source_tree': 'b'*40}
    argv = ['/usr/bin/printf', 'evidence']
    launched = commands.execute(native, 'launch', {'host': 'codex', 'authorization': 'owner',
                                'argv': argv, 'run_id': 'run', 'task_id': 'task', 'assignment_binding': binding})
    request = launched['native_request']
    assert request['arguments']['workdir'] == str(sandbox)
    assert 'TMPDIR=' + str(sandbox / '.native-tmp') in request['arguments']['cmd']
    assert commands.native_evidence_invocation_allowed(native, request['arguments'])
    assert not commands.native_evidence_invocation_allowed(native, {**request['arguments'], 'cmd': '/bin/true'})
    rows = native_records(request, {'exit_code': 0}, stamp=10**13)
    monkeypatch.setattr(commands, '_native_command_records', lambda _: rows)
    monkeypatch.setattr(commands, '_git_output', lambda *a: 'b'*40 if a[-1] == 'HEAD^{tree}' else 'a'*40)
    evidence = commands.governed_command_execution_evidence(native, 'owner', launched['handle'], assignment_binding=binding, argv=argv)
    assert evidence['state'] == 'succeeded' and evidence['native_evidence']['receipt_id'] == 'native-call'
    assert not sandbox.parent.exists()
    assert commands.governed_command_execution_evidence(native, 'owner', launched['handle'], assignment_binding=binding, argv=argv) == evidence
    with pytest.raises(commands.GovernedCommandError, match='exact assignment'):
        commands.governed_command_execution_evidence(native, 'owner', launched['handle'], assignment_binding=binding, argv=['/bin/true'])


def test_native_panel_queued_is_not_open_and_uses_no_transport_process(native, monkeypatch):
    from pathlib import Path
    from taskplane import command_adapters, review
    from taskplane.preview_runtime import PreviewRuntime
    Path(native, 'index.html').write_text('<html>native preview</html>')
    runtime = PreviewRuntime(Path(native).parent / 'state', workspace=native, authorization='owner',
                             surface_transport=command_adapters.native_surface_transport)
    capabilities = {key: {'status': 'supported', 'source': 'native-test'} for key in ['sandbox', 'side_panel']}
    registered = runtime.register(flow='build', target='commit-a', revision=1, source_root=native,
                                  authorization='owner', capabilities=capabilities,
                                  limits={'lifetime_seconds': 60, 'cpu_seconds': 10, 'memory_bytes': 1000000}, network_allowlist=[])
    monkeypatch.setattr(command_adapters.subprocess, 'Popen', lambda *a, **k: pytest.fail('native panel must not start a transport process'))
    requested = runtime.open(registered['preview_id'])
    assert requested['state'] == 'registered' and requested['outcome'] == 'open_requested'
    request = requested['native_surface']['native_request']
    assert request['name'] == 'mcp__codex_app__open_in_codex'
    rows = native_records(request, {'isError': False, 'content': [{'type': 'text', 'text': '{"status":"queued"}'}]}, stamp=10**13)
    monkeypatch.setattr(review, '_host_review_transcripts', lambda *a: [('codex', 'native-records')])
    monkeypatch.setattr(review, '_host_review_records', lambda *a: rows)
    queued = runtime.open(registered['preview_id'])
    assert queued['state'] == 'registered' and queued['outcome'] == 'open_queued'
    with pytest.raises(Exception, match='open preview'):
        runtime.observe(registered['preview_id'], interaction='viewed', result='looks good')
