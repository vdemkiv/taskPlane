"""The reviewed lifecycle failures, including their adjacent authority boundaries."""
from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import flow, flow_usage, workflow as w, workflow_local as local
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
        refused(lambda: flow.hook(child | {'tool_input': {'path': 'product.json'}}), 'scope_violation')
        s = submit(c, s)
        refused(lambda: flow.hook(child), 'scope_violation')
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


def native(path, sid, points, parent=None, guardian=False, owned=False):
    meta = dict(id=sid, timestamp='2026-09-01T00:00:00Z', thread_source='guardian_review' if guardian else 'subagent')
    if parent: meta['parent_thread_id'] = parent
    rows = [dict(type='session_meta',payload=meta)]
    before = 0
    for ordinal, (second, total) in enumerate(points,1):
        usage = dict(input_tokens=total, cached_input_tokens=0, output_tokens=0, total_tokens=total)
        payload = dict(thread_id=sid,thread_token_usage=usage)
        if owned:
            payload.update(response_id=f'{sid}-{ordinal}',usage={**usage,'input_tokens':total-before,'total_tokens':total-before})
        rows.append(dict(type='token_usage_record',ordinal=ordinal,timestamp=f'2026-09-01T00:00:{second:02d}Z',payload=payload))
        before = total
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    return rows


def test_reused_guardian_150_170_becomes_50_20(tmp_path,monkeypatch):
    home=tmp_path/'codex'; sessions=home/'sessions';sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(home))
    native(sessions/'root.jsonl','root',[(5,100),(15,200),(25,300)])
    native(sessions/'guardian.jsonl','guardian',[(5,100),(15,150),(25,170)],'root',True)
    starts=[dict(kind='start',run='r'+str(i),session='root',at=f'2026-09-01T00:00:{second:02d}Z',
                 usage={'input_tokens':total,'output_tokens':0,'total_tokens':total},usage_status='observed')
            for i,(second,total) in enumerate([(10,100),(20,200)])]
    actual=[flow_usage.reconcile(run,starts)['host_approval_tokens']['total_tokens'] for run in starts]
    assert actual == [50,20]
    assert sum(actual) == 170-100


def test_new_child_boundary_response_is_owned_once(tmp_path,monkeypatch):
    home=tmp_path/'codex'; sessions=home/'sessions';sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(home))
    native(sessions/'root.jsonl','root',[(5,100),(15,200),(25,300)])
    rows=native(sessions/'child.jsonl','child',[(15,50),(20,70)],'root',owned=True)
    rows[0]['payload']['timestamp']='2026-09-01T00:00:12Z'
    (sessions/'child.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    starts=[dict(kind='start',run='r'+str(i),session='root',worker_sessions=['child'],at=f'2026-09-01T00:00:{second:02d}Z',
                 usage={'input_tokens':total,'output_tokens':0,'total_tokens':total},usage_status='observed')
            for i,(second,total) in enumerate([(10,100),(20,200)])]
    actual=[next(s for s in flow_usage.reconcile(run,starts)['sessions'] if s['session']=='child')['usage']['total_tokens'] for run in starts]
    assert actual == [50,20] and sum(actual)==70


def test_saved_legacy_guardian_is_not_run_usage(tmp_path,monkeypatch):
    monkeypatch.setattr(flow_usage,'_codex_sessions',lambda *_:([],0))
    run=dict(run='r',session='root',at='2026-09-01T00:00:10Z')
    prior=dict(session='guardian',agent='guardian',role='host_approval_review',usage={'total_tokens':150},
               native_usage={'total_tokens':150},basis='child created during flow')
    saved=dict(kind='usage',run='r',measurement={'sessions':[prior]})
    result=flow_usage.reconcile(run,[saved])
    assert result['host_approval_tokens'] is None
    assert result['sessions'][0]['usage'] is None and result['sessions'][0]['status']=='unavailable'
    prior.update(attribution_schema='taskplane.owned-interval/v1',interval={'start':datetime.fromisoformat(run['at']).timestamp(),'end_exclusive':None})
    assert flow_usage.reconcile(run,[saved])['host_approval_tokens']['total_tokens']==150
    prior['interval']['start']-=1
    assert flow_usage.reconcile(run,[saved])['host_approval_tokens'] is None


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_saved_partial_child_interval_remains_partial_after_native_file_loss(tmp_path, monkeypatch, host):
    run = dict(run='r', session='root', at='2026-09-01T00:00:10Z', worker_sessions=['child'])
    if host == 'codex':
        home = tmp_path/'codex'; sessions = home/'sessions'; sessions.mkdir(parents=True)
        monkeypatch.setenv('CODEX_HOME', str(home))
        child = sessions/'child.jsonl'
        rows = native(child, 'child', [(5,100),(10,150),(15,170)], 'root', owned=True)
        del rows[2]['payload']['usage']
        child.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    else:
        from taskplane.tests.test_claude_flow import message, write
        root = tmp_path/'claude/root.jsonl'; write(root, [])
        child = root.with_suffix('')/'subagents/agent-child.jsonl'
        incomplete = message('missing', agent='child', at='2026-09-01T00:00:15Z')
        del incomplete['message']['usage']['cache_read_input_tokens']
        write(child, [incomplete, message('known', agent='child', at='2026-09-01T00:00:16Z')])
        run.update(host='claude', transcript_path=str(root))
    before = flow_usage.reconcile(run, [])
    prior = next(s for s in before['sessions'] if s['session'] == 'child')
    assert prior['status'] == 'partial' and prior['usage'] is not None
    child.unlink()
    for _ in range(2):
        recovered = flow_usage.reconcile(run, [dict(kind='usage',run='r',measurement=before)])
        row = next(s for s in recovered['sessions'] if s['session'] == 'child')
        assert row['usage'] == prior['usage'] and row['status'] == 'partial'
        assert row['recovery_status'] == 'recorded; native counter unavailable'
        assert recovered['token_coverage']['partial_sessions'] >= 1
        before = recovered


@pytest.mark.parametrize('payload',['{not json','[]'])
def test_malformed_hook_inputs_record_outcome(payload,monkeypatch,capsys):
    import io
    recorded=[]
    monkeypatch.setattr(sys,'stdin',io.StringIO(payload))
    monkeypatch.setattr(flow,'_observe_hook',lambda event,**kwargs:recorded.append((event,kwargs)))
    assert flow.run_hook('screen')==0
    assert json.loads(capsys.readouterr().out)['hookSpecificOutput']['permissionDecision']=='deny'
    assert len(recorded)==1 and recorded[0][1]['reason']=='malformed_hook_input'
    assert recorded[0][0]=={'hook_event_name':'PreToolUse'}


def test_child_outcome_does_not_require_usage_counter(tmp_path,monkeypatch):
    c,s=setup(tmp_path)
    flow.append(tmp_path,dict(kind='start',run=s['run'],session='root',phase='product'))
    monkeypatch.setattr(flow,'counter',lambda *_:dict(usage=None,usage_status='unavailable'))
    monkeypatch.setattr(flow,'observed_parent',lambda *_:'root')
    event=dict(cwd=str(tmp_path),thread_id='child',hook_event_name='PreToolUse',tool_name='Write',call_id='child-denial',tool_input={'path':'app.py'})
    with pytest.raises(w.Refusal): flow.hook(event)
    rows=[r for r in flow.read_events(tmp_path) if r.get('call_id')=='child-denial']
    assert len(rows)==1 and rows[0]['session']=='child' and rows[0]['outcome']=='denied'
    assert rows[0]['usage'] is None


def test_parent_keyed_hook_uses_only_verified_native_child_environment(monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID','native-child')
    monkeypatch.delenv('CLAUDECODE',raising=False)
    monkeypatch.delenv('CLAUDE_SESSION_ID',raising=False)
    monkeypatch.delenv('TASKPLANE_CLAUDE_SESSION_ID',raising=False)
    monkeypatch.setattr(flow,'observed_parent',lambda event,session:'root' if session=='native-child' else None)
    observed=[]
    monkeypatch.setattr(flow,'_hook',lambda event,**kwargs:observed.append(event) or {})
    monkeypatch.setattr(flow,'_observe_hook',lambda event,**kwargs:{})
    flow.hook(dict(thread_id='root',hook_event_name='PreToolUse'))
    assert observed[-1]['thread_id']=='native-child' and observed[-1]['parent_session_id']=='root'
    flow.hook(dict(thread_id='foreign',hook_event_name='PreToolUse'))
    assert observed[-1]['thread_id']=='foreign'


def test_denials_early_returns_and_journal_failure_keep_original_decision(tmp_path,monkeypatch):
    c,s=setup(tmp_path,entry='engineering',standalone=True)
    flow.append(tmp_path,dict(kind='start',run=s['run'],session='root',phase='engineering'))
    event=dict(cwd=str(tmp_path),thread_id='root',hook_event_name='PreToolUse',tool_name='Write',call_id='deny',tool_input={'path':'app.py'})
    with pytest.raises(w.Refusal): flow.hook(event,governor=c)
    flow.hook({**event,'tool_name':'Read','call_id':'read'},governor=c)
    flow.hook(dict(cwd=str(tmp_path),thread_id='root',hook_event_name='Stop'),governor=c)
    rows=[r for r in flow.read_events(tmp_path) if r.get('kind')=='hook']
    assert [(r.get('call_id'),r['outcome']) for r in rows[:2]] == [('deny','denied'),('read','admitted')]
    assert any(r['event']=='Stop' for r in rows)
    def fail(*args,**kwargs): raise OSError('optional journal unavailable')
    monkeypatch.setattr(flow,'append',fail)
    with pytest.raises(w.Refusal,match='outside'): flow.hook(event,governor=c)


def test_late_task_publication_is_explicit_run_bound_and_idempotent(tmp_path,monkeypatch,capsys):
    c,s=setup(tmp_path)
    tasks={'tasks':[dict(id='LATE',phase='product',owner='root',dependencies=[],paths=['product.json'],criteria=['AC1'],verification='inspect')]}
    (tmp_path/'.taskplane/late-tasks.json').write_text(json.dumps(tasks))
    flow.append(tmp_path,dict(kind='start',run=s['run'],session='root',phase='product'))
    assert flow.main(['attach','--workspace',str(tmp_path),'--run',s['run'],'--tasks','.taskplane/late-tasks.json'],governor=c)==0
    capsys.readouterr()
    with pytest.raises(w.Refusal): c.context(s['run'],task='LATE')
    command=['attach','--workspace',str(tmp_path),'--run',s['run'],'--tasks','.taskplane/late-tasks.json','--update-context','--expected-revision',str(s['revision'])]
    assert flow.main(command,governor=c)==0
    capsys.readouterr()
    assert c.context(s['run'],task='LATE')['phase']=='product'
    assert flow.main(command,governor=c)==0
    capsys.readouterr()
    assert c.report()['revision']==s['revision']+1
    tasks['tasks'][0]['paths']=['foreign.py']
    (tmp_path/'.taskplane/late-tasks.json').write_text(json.dumps(tasks))
    with pytest.raises(w.Refusal): c.update_tasks(s['run'],c.report()['revision'],'.taskplane/late-tasks.json')


def test_replay_deduplicates_owned_responses_and_uses_exclusive_end(tmp_path):
    from taskplane.native_session_meter import read_owned_interval
    source=tmp_path/'source.jsonl'
    rows=native(source,'worker',[(5,100),(10,150),(20,170)],'root',owned=True)
    source.write_text(source.read_text()+json.dumps(rows[2])+'\n')
    stamp=lambda s:datetime.fromisoformat(f'2026-09-01T00:00:{s:02d}+00:00').timestamp()
    first=read_owned_interval([source],'worker',start=stamp(10),end=stamp(20))
    second=read_owned_interval([source],'worker',start=stamp(20))
    assert first['usage']['total_tokens']==50 and first['responses']==1
    assert second['usage']['total_tokens']==20 and first['status']=='measured'


def test_missing_owned_response_is_partial_when_cumulative_total_does_not_reconcile(tmp_path):
    from taskplane.native_session_meter import read_owned_interval
    source=tmp_path/'source.jsonl'
    rows=native(source,'worker',[(5,100),(10,150),(15,170)],'root',owned=True)
    del rows[2]['payload']['usage']
    source.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=read_owned_interval([source],'worker',start=start)
    assert result['usage']['total_tokens']==20
    assert result['status']=='partial' and 'owned_counter_mismatch' in result['errors']


def test_resumed_owned_history_without_prior_baseline_stays_partial(tmp_path):
    from taskplane.native_session_meter import read_owned_interval
    source=tmp_path/'resumed.jsonl'
    rows=native(source,'worker',[(15,20)],'root',owned=True)
    rows[0]['payload']['history_base']={'thread_id':'worker','end_ordinal_exclusive':8,'end_byte_offset':200}
    source.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=read_owned_interval([source],'worker',start=start)
    assert result['usage']['total_tokens']==20 and result['status']=='partial'
    assert 'resumed_baseline_unavailable' in result['errors']
