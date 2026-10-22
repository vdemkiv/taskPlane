"""Native hook output -> actual CLI subprocess fixtures, never live host proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from taskplane import claude_invocation as invocation, flow, worker_runtime as workers, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve
from taskplane.tests.test_farm_workers import requires_native_reader, fixture
from taskplane import claude_worker_observations as observations
from taskplane.context import digest


@pytest.fixture
def launched(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path.parent / (tmp_path.name + '-home')))
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'root')
    monkeypatch.setenv('CLAUDECODE', '1')
    monkeypatch.chdir(tmp_path)
    controller, state = setup(tmp_path)
    controller.adapter.name = 'claude'
    item = reserve(controller, state)
    event = dict(hook_event_name='PreToolUse', tool_name='Agent', tool_use_id='launch',
                 tool_input=dict(prompt=item['message'], description='review', subagent_type='general-purpose'))
    controller.guard(event, state['run'])
    parent = Path.home() / '.claude/projects' / re.sub(r'[^A-Za-z0-9]', '-', str(tmp_path)) / 'root.jsonl'
    parent.parent.mkdir(parents=True)
    called = workers.now()
    header = dict(sessionId='root', agentId='child', isSidechain=True, cwd=str(tmp_path), timestamp=workers.now())
    records = [dict(sessionId='root', cwd=str(tmp_path), timestamp=called,
                    message={'content':[dict(type='tool_use', id='launch', name='Agent',
                                             input={**event['tool_input'], 'run_in_background':'true'})]}),
               dict(sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
                    message={'content':[dict(type='tool_result', tool_use_id='launch')]},
                    toolUseResult=dict(isAsync=True, status='async_launched', agentId='child'))]
    parent.write_text(''.join(json.dumps(row)+'\n' for row in records))
    child = parent.with_suffix('') / 'subagents/agent-child.jsonl'
    child.parent.mkdir(parents=True)
    child.write_text(json.dumps(header)+'\n')
    return controller, state, item, parent


def command_event(launched, *, action='claim', call='claim', extra=()):
    controller, state, item, parent = launched
    args = (['worker', '--operation', 'claim', '--grant', item['grant']['grant_id']] if action == 'claim'
            else ['context', '--task', 'T0'])
    words = [str(Path(sys.executable).resolve()), str(Path(flow.__file__).with_name('tp.py').resolve()),
             'flow', *args, '--workspace', str(controller.workspace), '--run', state['run'], *extra]
    return dict(host='claude', session_id='root', agent_id='child', cwd=str(controller.workspace),
                hook_event_name='PreToolUse', tool_name='Bash', tool_use_id=call,
                transcript_path=str(parent), tool_input={'command':shlex.join(words), 'description':'Scoped CLI'})


def execute(updated, cwd, **options):
    return subprocess.run(shlex.split(updated['command']), cwd=cwd, capture_output=True,
                          text=True, timeout=30, **options)


@requires_native_reader
def test_hook_actual_cli_claim_context_and_exact_completion(launched):
    controller, state, item, _ = launched
    event = command_event(launched)
    result = flow.hook(event)
    assert 'permissionDecision' not in result['hookSpecificOutput']
    updated = result['hookSpecificOutput']['updatedInput']
    completed = execute(updated, controller.workspace)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    claimed = json.loads(completed.stdout)
    assert claimed['grant_id'] == item['grant']['grant_id']
    assert execute(updated, controller.workspace).returncode == 2  # one use, across processes
    flow.hook({**event, 'tool_input':updated, 'hook_event_name':'PostToolUse', 'tool_response':{'stdout':completed.stdout}})
    sha = claimed['context']['handoff_ref']['sha256']
    for index in range(10):
        context = command_event(launched, action='context', call=f'context-{index}', extra=['--drain',sha])
        output = flow.hook(context)['hookSpecificOutput']['updatedInput']
        completed = execute(output, controller.workspace)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        delivered = json.loads(completed.stdout)
        flow.hook({**context, 'tool_input':output, 'hook_event_name':'PostToolUse'})
        if delivered['done']:
            break
    else:
        pytest.fail('Context did not finish in bounded fixture')
    worker = controller.report()['workers'][item['grant']['grant_id']]
    assert worker['state'] == 'running' and worker['context_receipt']
    assert workers.readiness(worker)['status'] == 'ready'
    # The inherited parent environment never received claim or context ownership.
    assert worker['worker_id'] == 'child'


@requires_native_reader
@pytest.mark.parametrize('variant', ['original', 'updated', 'mutated', 'denied'])
def test_completion_accepts_only_exact_inputs_and_closes_unused_reference(launched, variant):
    controller, _, _, _ = launched
    event = command_event(launched)
    updated = flow.hook(event)['hookSpecificOutput']['updatedInput']
    inputs = event['tool_input'] if variant == 'original' else updated
    if variant == 'mutated':
        inputs = {**updated, 'description':'changed'}
        with pytest.raises(w.Refusal):
            flow.hook({**event, 'tool_input':inputs, 'hook_event_name':'PostToolUse'})
        return
    flow.hook({**event, 'tool_input':inputs, 'hook_event_name':'PostToolUse',
               'tool_response':{'error':'permission denied'} if variant == 'denied' else {}})
    assert execute(updated, controller.workspace).returncode == 2
    assert not controller.report()['workers'][launched[2]['grant']['grant_id']].get('claimed_at')


@requires_native_reader
@pytest.mark.parametrize('defect', ['argv', 'cwd', 'workspace', 'nonce', 'stale', 'runtime', 'expired', 'root', 'foreign_child', 'revoked'])
def test_invocation_adversarial_subprocess_refusals(launched, tmp_path, defect):
    controller, state, item, _ = launched
    event = command_event(launched)
    if defect in {'root', 'foreign_child'}:
        event['agent_id'] = 'root' if defect == 'root' else 'other-child'
        with pytest.raises(w.Refusal): flow.hook(event)
        return
    updated = flow.hook(event)['hookSpecificOutput']['updatedInput']
    words = shlex.split(updated['command'])
    cwd = controller.workspace
    if defect == 'argv': words[words.index('--grant')+1] = '0'*32
    if defect == 'cwd':
        cwd = tmp_path / 'other'; cwd.mkdir()
    if defect == 'workspace': words[words.index('--workspace')+1] = str(tmp_path / 'other')
    if defect == 'nonce': words[-1] = words[-1][:-1] + ('0' if words[-1][-1] != '0' else '1')
    if defect in {'stale', 'runtime', 'expired', 'revoked'}:
        db = controller._read(controller._path())
        row = next(value for value in db['admissions'].values() if value.get('claude_invocation'))
        if defect == 'stale': db['runs'][state['run']]['revision'] += 1
        if defect == 'runtime': row['runtime']['root'] = '/foreign-runtime'
        if defect == 'expired': row['claude_invocation']['issued_at'] = (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
        if defect == 'revoked': db['runs'][state['run']]['workers'][item['grant']['grant_id']]['revoked_at'] = workers.now()
        controller._write(controller._path(), db)
    completed = execute({'command':shlex.join(words)}, cwd)
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert not controller.report()['workers'][item['grant']['grant_id']].get('claimed_at')


@pytest.mark.parametrize('legacy', [True, False])
@pytest.mark.parametrize('admitted', ['omitted', True, 'true'])
@pytest.mark.parametrize('observed', ['omitted', True, 'true', False, 'false', None, 1, 'TRUE'])
def test_async_argument_representations_preserve_substantive_and_legacy(tmp_path, legacy, admitted, observed):
    _, row, records, headers = fixture(tmp_path)
    arguments = records[0]['message']['content'][0]['input']
    normalized = {key:value for key,value in arguments.items() if key != 'run_in_background'}
    row['dispatch_digest'] = digest(normalized if admitted == 'omitted' else {**normalized, 'run_in_background':admitted})
    if not legacy:
        row.update(dispatch_argument_schema=observations.ARGUMENT_SCHEMA, dispatch_semantic_digest=digest(normalized))
    arguments.pop('run_in_background')
    if observed != 'omitted': arguments['run_in_background'] = observed
    before = deepcopy(row)
    answer = observations.correlate_records('parent', row, records, headers)
    supported = observed == 'omitted' or observed is True or observed == 'true'
    assert (answer['status'] == 'matched') == supported
    assert row == before
    if supported:
        arguments['model'] = 'changed'
        assert observations.correlate_records('parent', row, records, headers)['status'] == 'conflict'


def test_normalization_needs_independent_async_result(tmp_path):
    _, row, records, headers = fixture(tmp_path)
    records[0]['message']['content'][0]['input'].pop('run_in_background')
    records[1]['toolUseResult']['isAsync'] = False
    assert observations.correlate_records('parent', row, records, headers)['status'] == 'unsupported'


@requires_native_reader
@pytest.mark.parametrize('action', ['claim', 'context'])
def test_missing_channel_is_nonretryable_and_inherited_parent_cannot_consume(launched, action):
    controller, _, _, _ = launched
    event = command_event(launched, action=action)
    result = execute(event['tool_input'], controller.workspace)
    assert result.returncode == 2
    failure = json.loads(result.stdout)
    assert failure['reason'] == 'unsupported_authority' and failure['retryable'] is False
    assert 'next_action' not in failure


@requires_native_reader
@pytest.mark.parametrize('action', ['claim', 'context'])
def test_observed_child_cannot_mint_sibling_grant_or_context(launched, action):
    event = command_event(launched, action=action)
    words = shlex.split(event['tool_input']['command'])
    option = '--grant' if action == 'claim' else '--task'
    words[words.index(option)+1] = 'sibling'
    event['tool_input']['command'] = shlex.join(words)
    with pytest.raises(w.Refusal, match='own grant or task'):
        flow.hook(event)


@requires_native_reader
def test_simultaneous_consumption_only_one_subprocess_wins(launched):
    from concurrent.futures import ThreadPoolExecutor
    controller, _, _, _ = launched
    updated = flow.hook(command_event(launched))['hookSpecificOutput']['updatedInput']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(updated, controller.workspace), range(2)))
    assert sorted(result.returncode for result in results) == [0, 2]


@requires_native_reader
def test_observed_failure_event_closes_unused_reference(launched):
    controller, _, _, _ = launched
    event = command_event(launched)
    updated = flow.hook(event)['hookSpecificOutput']['updatedInput']
    flow.hook({**event, 'tool_input':updated, 'hook_event_name':'PostToolUseFailure', 'error':'denied'})
    assert execute(updated, controller.workspace).returncode == 2


@requires_native_reader
def test_concurrent_siblings_receive_distinct_exact_principals(launched):
    from concurrent.futures import ThreadPoolExecutor
    controller, state, first, parent = launched
    second = reserve(controller, state, task='T1')
    launch = dict(hook_event_name='PreToolUse', tool_name='Agent', tool_use_id='second-launch',
                  tool_input=dict(prompt=second['message'], description='second', run_in_background=True))
    controller.guard(launch, state['run'])
    called = workers.now()
    header = dict(sessionId='root', agentId='sibling', isSidechain=True,
                  cwd=str(controller.workspace), timestamp=workers.now())
    records = [dict(sessionId='root', cwd=str(controller.workspace), timestamp=called,
                    message={'content':[dict(type='tool_use', id='second-launch', name='Agent', input=launch['tool_input'])]}),
               dict(sessionId='root', cwd=str(controller.workspace), timestamp=workers.now(),
                    message={'content':[dict(type='tool_result', tool_use_id='second-launch')]},
                    toolUseResult=dict(isAsync=True, status='async_launched', agentId='sibling'))]
    with parent.open('a') as stream:
        stream.write(''.join(json.dumps(row)+'\n' for row in records))
    (parent.with_suffix('')/'subagents/agent-sibling.jsonl').write_text(json.dumps(header)+'\n')
    events = [command_event(launched), command_event((controller,state,second,parent),call='sibling-claim')]
    events[1]['agent_id'] = 'sibling'
    updated = [flow.hook(event)['hookSpecificOutput']['updatedInput'] for event in events]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: execute(value, controller.workspace), updated))
    assert all(result.returncode == 0 for result in results), [result.stdout for result in results]
    rows = controller.report()['workers']
    assert rows[first['grant']['grant_id']]['worker_id'] == 'child'
    assert rows[second['grant']['grant_id']]['worker_id'] == 'sibling'
    assert {json.loads(result.stdout)['task_id'] for result in results} == {'T0','T1'}
    cross = command_event(launched, call='cross-grant')
    cross['agent_id'] = 'sibling'
    with pytest.raises(w.Refusal, match='own grant or task'): flow.hook(cross)


@requires_native_reader
def test_root_hook_does_not_issue_child_reference(launched):
    event = command_event(launched)
    event.pop('agent_id')
    result = flow.hook(event)
    assert 'updatedInput' not in result.get('hookSpecificOutput', {})
    assert execute(event['tool_input'], launched[0].workspace).returncode == 2


@pytest.mark.parametrize('value', [False, 'false', 1, None])
def test_legacy_invalid_background_never_normalizes(tmp_path, value):
    _, row, records, headers = fixture(tmp_path)
    arguments = records[0]['message']['content'][0]['input']
    arguments['run_in_background'] = value
    row['dispatch_digest'] = digest(arguments)
    before = deepcopy(row)
    assert observations.correlate_records('parent', row, records, headers)['status'] == 'conflict'
    assert row == before


@requires_native_reader
def test_foreign_launcher_importing_same_modules_cannot_redeem(launched, tmp_path):
    controller, _, _, _ = launched
    updated = flow.hook(command_event(launched))['hookSpecificOutput']['updatedInput']
    foreign = tmp_path.parent / (tmp_path.name + '-launcher.py')
    repository = str(Path(flow.__file__).resolve().parents[1])
    foreign.write_text('import sys\nsys.path.insert(0, ' + repr(repository) + ')\n'
                       'from taskplane.tp import main\nraise SystemExit(main())\n')
    words = shlex.split(updated['command'])
    words[1] = str(foreign)
    result = execute({'command':shlex.join(words)}, controller.workspace)
    assert result.returncode == 2
    assert json.loads(result.stdout)['reason'] == 'unsupported_authority'
    # A mismatched attempt does not consume the legitimate pending invocation.
    assert execute(updated, controller.workspace).returncode == 0


@requires_native_reader
def test_invocation_cannot_enter_early_root_diagnostics(launched):
    controller, _, _, _ = launched
    updated = flow.hook(command_event(launched))['hookSpecificOutput']['updatedInput']
    words = shlex.split(updated['command'])
    changed = [*words[:3], 'diagnose', '--workspace', str(controller.workspace), *words[-2:]]
    result = execute({'command':shlex.join(changed)}, controller.workspace)
    assert result.returncode == 2
    assert json.loads(result.stdout)['reason'] == 'unsupported_authority'
