"""The reviewed lifecycle failures, including their adjacent authority boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import flow, workflow as w, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decide, decision, present
from taskplane.tests.test_workflow_autonomy import authorization, set_policy


def refused(action, reason=None):
    try:
        action()
    except w.Refusal as exc:
        assert reason is None or exc.reason == reason, exc
    else:
        raise AssertionError('Unsafe action was admitted')


def exercise_correction(root):
    for choice in ('Changes requested', 'Rejected'):
        workspace = root/choice.replace(' ', '-')
        workspace.mkdir(parents=True)
        c, s = setup(workspace)
        s = submit(c, s)
        sealed = deepcopy(w.current(s)['packet'])
        write = {'tool_name': 'Write', 'tool_input': {'path': 'product.json'}}
        refused(lambda: c.guard(write, s['run']), 'approval_required')
        s = decide(c, s, decision(s, text=choice))
        for index in range(2):
            c.guard(write, s['run'])
            data = json.loads((workspace/'product.json').read_text())
            data['scope'] = f'Requested correction {index}'
            (workspace/'product.json').write_text(json.dumps(data))
            assert not c.report().get('invalidation_pending')
        refused(lambda: c.guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, s['run']), 'scope_violation')
        from taskplane.context_handoff import Session, consume_required
        data['context_receipt'], _ = consume_required(Session(workspace, c.report()))
        (workspace/'product.json').write_text(json.dumps(data))
        command = shlex.join([sys.executable, str(Path(flow.__file__).with_name('tp.py')), 'flow', 'submit',
                              '--workspace', str(workspace), '--output', 'product.json', '--tasks', 'tasks.json',
                              '--expected-revision', str(s['revision'])])
        flow.hook({'cwd': str(workspace), 'thread_id': 'root', 'hook_event_name': 'PreToolUse',
                   'tool_name': 'exec_command', 'tool_input': {'cmd': command}}, governor=c)
        s = c.apply('submit', s['run'], expected_revision=s['revision'], output='product.json', tasks='tasks.json')
        assert any(row.get('packet') == sealed for row in s['history'])
        present(c, s)
        s = decide(c, s, decision(s, event='accept-correction'))
        s = c.apply('advance', s['run'], expected_revision=s['revision'], phase='design')
        s = submit(c, s)
        s = decide(c, s, decision(s, text='Changes requested', event='design-correction'))
        (workspace/'product.json').write_text('{}')
        assert c.report().get('invalidation_pending')
        refused(lambda: c.guard({'tool_name': 'Write', 'tool_input': {'path': 'design.json'}}, s['run']), 'stale_checkpoint')


def exercise_handles(root):
    for change in ('policy', 'approval'):
        workspace = root/change
        workspace.mkdir(parents=True)
        c, s = setup(workspace)
        s = set_policy(c, s) if change == 'policy' else submit(c, s)
        launch = {'hook_event_name': 'PostToolUse', 'tool_name': 'exec_command',
                  'tool_input': {'cmd': 'long verification command' if change == 'policy' else 'rg --files'},
                  'tool_response': {'session_id': 321}}
        c.observe(launch, s['run'])
        original_revision = s['revision']
        s = set_policy(c, s, authorization(s, mode='manual', event='revoke')) if change == 'policy' else decide(c, s)
        assert s['revision'] > original_revision
        for chars in ('', '\x03'):
            c.guard({'tool_name': 'write_stdin', 'tool_input': {'session_id': 321, 'chars': chars}}, s['run'])
        refused(lambda: c.guard({'tool_name': 'write_stdin', 'tool_input': {'session_id': 321, 'chars': 'new code'}}, s['run']))
        refused(lambda: c.guard({'tool_name': 'write_stdin', 'tool_input': {'session_id': 999}}, s['run']))
        action = 'submit' if change == 'policy' else 'advance'
        refused(lambda: c.apply(action, s['run'], expected_revision=s['revision'], phase='design', output='product.json', tasks='tasks.json'))
        c.observe(launch, s['run'])
        assert c.report()['observed_handles']['321']['revision'] == original_revision
        terminal = launch | {'tool_response': {'session_id': 321, 'exit_code': 0}}
        c.observe(terminal, s['run'])
        c.observe(terminal, s['run'])  # Duplicate terminal observations are idempotent.
        assert not c.report()['known_live_handles']
        refused(lambda: c.observe(launch, s['run']), 'scope_violation')
        refused(lambda: c.guard({'tool_name': 'write_stdin', 'tool_input': {'session_id': 321}}, s['run']))
        s = submit(c, s) if change == 'policy' else c.apply('advance', s['run'], expected_revision=s['revision'], phase='design')
        if change == 'approval':
            refused(lambda: c.observe(terminal, s['run']), 'scope_violation')
        # Pure adapter checks also reject future revisions and old visits.
        for field, value in [('revision', s['revision'] + 1), ('visit', 'foreign')]:
            invalid = deepcopy(s)
            invalid['observed_handles']['321'].update(state='running', **{field: value})
            refused(lambda: c.adapter.guard_input({'tool_input': {'session_id': 321}}, invalid), 'scope_violation')


def exercise_child_lineage(root):
    for lineage in ('transcript', 'metadata-only', 'metadata-no-journal', 'subagent-start', 'parent-field'):
        workspace = root/lineage
        workspace.mkdir(parents=True)
        c, s = setup(workspace)
        if lineage != 'metadata-no-journal':
            flow.append(workspace, {'kind': 'start', 'run': s['run'], 'session': 'root', 'phase': 'product'})
        child = {'cwd': str(workspace), 'thread_id': 'review-child', 'hook_event_name': 'PreToolUse',
                 'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}
        if lineage == 'transcript' or lineage.startswith('metadata'):
            transcript = workspace/'.taskplane/child.jsonl'
            transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {
                'id': 'review-child', 'parent_thread_id': 'root', 'thread_source': 'subagent',
                'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'root'}}}}})+'\n'+
                json.dumps({'type': 'event_msg', 'ordinal': 7, 'timestamp': '2026-09-01T00:00:01Z', 'payload': {
                    'type': 'token_count', 'info': {'total_token_usage': {
                        'input_tokens': 9, 'cached_input_tokens': 0, 'output_tokens': 1, 'total_tokens': 10}}}})+'\n')
            child['transcript_path'] = str(transcript)
            assert flow.counter(child, 'review-child')['parent'] == 'root'
            if lineage.startswith('metadata'):
                transcript.write_text(transcript.read_text().splitlines()[0]+'\n')
                assert flow.counter(child, 'review-child')['usage_status'] == 'unavailable'
            assert flow.observed_parent(child, 'review-child') == 'root'
            assert flow.observed_parent(child, 'unrelated') is None
        elif lineage == 'subagent-start':
            flow.hook({'cwd': str(workspace), 'thread_id': 'root', 'hook_event_name': 'SubagentStart', 'agent_id': 'review-child'})
        else:
            child['parent_session_id'] = 'root'
        refused(lambda: flow.hook(child), 'scope_violation')
        flow.hook(child | {'tool_input': {'path': 'product.json'}})
        s = submit(c, s)
        refused(lambda: flow.hook(child), 'approval_required')
        assert local.Harness(workspace, 'review-child').read() == {}
        unrelated = {k: v for k, v in child.items() if k not in ('transcript_path', 'parent_session_id')}
        unrelated['thread_id'] = 'unrelated'
        assert flow.hook(unrelated).get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    # A child-start event without an ID must not attach a finished root to an
    # unrelated open run through a missing root/child value in older journal rows.
    rows = [{'kind': 'start', 'run': 'foreign-run', 'session': 'foreign'},
            {'kind': 'hook', 'event': 'SubagentStart', 'root': 'foreign', 'session': 'foreign'},
            {'kind': 'start', 'run': 'closed-run', 'session': 'root'},
            {'kind': 'finish', 'run': 'closed-run', 'session': 'root'}]
    assert flow.active_run(rows, 'root') is None
    # A native task may itself have been forked, then start its own workflow.
    # Deleting its advisory journal must not redirect that active binding.
    workspace = root/'own-active-run'
    workspace.mkdir()
    c, s = setup(workspace)
    transcript = workspace/'.taskplane/own.jsonl'
    transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'id': 'root', 'parent_thread_id': 'foreign'}})+'\n')
    event = {'cwd': str(workspace), 'thread_id': 'root', 'hook_event_name': 'PreToolUse',
             'transcript_path': str(transcript), 'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}
    refused(lambda: flow.hook(event), 'scope_violation')
    s = submit(c, s)
    refused(lambda: flow.hook(event), 'approval_required')


def exercise_routing():
    for prefix in ('taskplane ', '[@taskplane](plugin://taskplane@openai-curated-remote) ',
                   'Use taskplane to ', 'Run taskplane ', 'Please invoke the taskplane to '):
        for action, expected in [('build', 'taskplane'), ('implement', 'taskplane'), ('design', 'tp-design'),
                                 ('review', 'tp-engineering'), ('audit', 'tp-engineering'), ('product', 'tp-product')]:
            prompt = prefix+action+' the export with design, product requirements and an engineering review.'
            assert local.execution_entry({'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}) == expected, prompt
    for prompt in ('Use taskplane to explain build and review', 'Run taskplane status of review',
                   '"Use taskplane to build"', 'How do I use taskplane to build?'):
        assert local.execution_entry({'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}) is None


@pytest.mark.parametrize('exercise', [exercise_correction, exercise_handles, exercise_child_lineage])
def test_review_regressions(tmp_path, exercise):
    exercise(tmp_path)


def test_leading_action_equivalence():
    exercise_routing()
