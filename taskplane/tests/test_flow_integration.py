"""Advisory runs share evidence, graph and actual native lens usage."""
import json
import pytest
from datetime import datetime
from pathlib import Path

from taskplane import flow, flow_usage, native_session_meter as meter
from taskplane import dashboard, depgraph
from taskplane.tests.test_native_session_meter import _write_segment


def native(path, sid, total, *, parent=None, at='2026-09-01T00:00:00Z', guardian=False):
    _write_segment(path, session_id=sid, total=total, parent=parent)
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    rows[0]['payload']['timestamp']=at
    if guardian:
        rows[0]['payload']['thread_source']='guardian_review'
        rows[0]['payload']['source']={'subagent':{'other':'guardian'}}
    path.write_text('\n'.join(json.dumps(x) for x in rows)+'\n')


def setup_run(tmp_path, monkeypatch):
    home=tmp_path/'codex';sessions=home/'sessions';sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(home))
    monkeypatch.setenv('CODEX_THREAD_ID','root')
    ws=tmp_path/'workspace';ws.mkdir()
    native(sessions/'root.jsonl','root',100)
    run={'kind':'start','run':'shared','session':'root','goal':'Shared delivery <script>',
         'at':'2026-09-01T00:00:01Z','usage':meter.read_snapshot(str(sessions/'root.jsonl'))['usage']}
    flow.append(ws,run)
    return ws,sessions,run


def test_discovers_unhooked_lenses_and_retains_final_usage(tmp_path,monkeypatch,capsys):
    ws,sessions,run=setup_run(tmp_path,monkeypatch)
    native(sessions/'root.jsonl','root',400)
    native(sessions/'lens.jsonl','lens',90,parent='root',at='2026-09-01T00:00:02Z')
    native(sessions/'other.jsonl','other',900,parent='unrelated',at='2026-09-01T00:00:02Z')
    native(sessions/'old.jsonl','old',700,parent='root')
    native(sessions/'guardian.jsonl','guardian',20,parent='root',guardian=True)
    (ws/'reviews.json').write_text(json.dumps([{'lens':'quality','agent':'/root/lens'}]))
    flow.main(['attach','--workspace',str(ws),'--run','shared','--reviews','reviews.json'])
    capsys.readouterr()
    flow.append(ws,{'kind':'finish','run':'shared','session':'root','note':'Done'})
    native(sessions/'lens.jsonl','lens',130,parent='root',at='2026-09-01T00:00:02Z')
    before=(ws/flow.JOURNAL).read_bytes()
    r=flow.report(ws)
    assert r['tokens']['total_tokens']==430
    assert r['native_tokens']['total_tokens']==530
    assert r['host_approval_tokens']['total_tokens']==20
    assert r['token_coverage']['measured_sessions']==2
    assert r['token_coverage']['unmeasured_sessions']==0
    assert (ws/flow.JOURNAL).read_bytes()==before # reports never mutate evidence
    flow.main(['attach','--workspace',str(ws),'--run','shared','--reviews','reviews.json'])
    capsys.readouterr()
    (sessions/'lens.jsonl').unlink()
    recovered=flow.report(ws)
    assert recovered['tokens']['total_tokens']==430
    assert 'recorded' in next(s for s in recovered['sessions'] if s['agent']=='/root/lens')['status']
    assert 'private conversation' not in (ws/flow.JOURNAL).read_text()


def test_missing_reviewer_is_unknown_not_zero_and_artifacts_are_run_scoped(tmp_path,monkeypatch,capsys):
    ws,sessions,run=setup_run(tmp_path,monkeypatch)
    (ws/'reviews.json').write_text(json.dumps([{'lens':'qa','agent':'/root/missing'}]))
    flow.main(['attach','--workspace',str(ws),'--run','shared','--reviews','reviews.json'])
    capsys.readouterr()
    r=flow.report(ws)
    assert r['token_coverage']['unmeasured_sessions']==1
    assert next(s for s in r['sessions'] if s['agent']=='/root/missing')['usage'] is None
    flow.append(ws,{'kind':'start','run':'other','session':'other-root','goal':'Other','at':'2026-09-02T00:00:00Z'})
    assert flow.report(ws)['reviews']==[]
    assert len(flow.report(ws,'shared')['reviews'])==1
    outside=tmp_path/'outside.md';outside.write_text('private')
    flow.main(['attach','--workspace',str(ws),'--run','shared','--evidence','../outside.md'])
    assert json.loads(capsys.readouterr().out)['status']=='blocked'
    assert 'outside.md' not in (ws/flow.JOURNAL).read_text()


def test_native_thread_counter_wins_over_stale_legacy_summary(tmp_path):
    path=tmp_path/'native.jsonl';native(path,'root',100)
    usage={'input_tokens':200,'cached_input_tokens':100,'output_tokens':10,'total_tokens':210}
    with path.open('a') as stream:
        stream.write(json.dumps({'ordinal':8,'timestamp':'2026-09-01T00:00:08Z','type':'token_usage_record','payload':{'thread_id':'root','thread_token_usage':usage}})+'\n')
        stream.write(json.dumps({'ordinal':9,'timestamp':'2026-09-01T00:00:09Z','type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':dict(usage,total_tokens=150,input_tokens=140)}}})+'\n')
    assert meter.read_snapshot(str(path))['usage']['total_tokens']==210
    assert meter.read_snapshot(str(path),at_or_before=datetime.fromisoformat("2026-09-01T00:00:07+00:00").timestamp())['usage']['total_tokens']==100


def test_unbound_child_cannot_publish_root_progress(tmp_path,monkeypatch,capsys):
    ws,sessions,run=setup_run(tmp_path,monkeypatch)
    flow.append(ws,{'kind':'progress','run':'shared','session':'root','phase':'engineering','note':'Historical observation, no approval'})
    native(sessions/'child.jsonl','child',40,parent='root',at='2026-09-01T00:00:02Z')
    monkeypatch.setenv('CODEX_THREAD_ID','child')
    (ws/'review.md').write_text('Reviewed shared T1 dependency scope')
    flow.main(['progress','--workspace',str(ws),'--phase','engineering','--note','T1 reviewed','--evidence','review.md'])
    r=json.loads(capsys.readouterr().out)
    assert r['reason']=='scope_violation'
    assert sum(e['kind']=='start' for e in flow.read_events(ws))==1
    assert not any(e.get('note')=='T1 reviewed' for e in flow.read_events(ws))


def test_dashboard_renders_multiple_review_evidence_paths_from_immutable_snapshot(tmp_path,monkeypatch,capsys):
    from taskplane import flow_dashboard
    ws,_,_=setup_run(tmp_path,monkeypatch)
    (ws/'one.md').write_text('First <finding>')
    (ws/'two.md').write_text('Second finding')
    (ws/'reviews.json').write_text(json.dumps({'reviews':[
        {'lens':'quality','agent':'root','evidence':['one.md','two.md']}]}))
    assert flow.main(['attach','--workspace',str(ws),'--reviews','reviews.json',
                      '--evidence','one.md','--evidence','two.md'])==0
    result=json.loads(capsys.readouterr().out)
    assert not any('dashboard refresh' in error for error in result['evidence_errors'])
    page=(ws/flow.DASHBOARD).read_text()
    assert 'First &lt;finding&gt;' in page and 'Second finding' in page
    (ws/'one.md').write_text('Changed after snapshot')
    assert 'Changed after snapshot' not in flow_dashboard.render(str(ws),result)


def test_dashboard_displays_shared_tasks_lenses_stages_graph_and_escaped_evidence(tmp_path,monkeypatch,capsys):
    ws,sessions,run=setup_run(tmp_path,monkeypatch)
    tasks={'tasks':[{'id':'T1','title':'Core','status':'complete','dependencies':[],'paths':['src/a.py']},
                    {'id':'T2','title':'CLI','status':'complete','dependencies':['T1'],'paths':['cli/b.py']} ]}
    (ws/'tasks.json').write_text(json.dumps(tasks))
    (ws/'review.md').write_text('<script>alert("unsafe")</script> Real finding fixed')
    (ws/'reviews.json').write_text(json.dumps({'engineering':[{'lens':'quality','agent':'/root/lens','evidence':'review.md'}]}))
    native(sessions/'lens.jsonl','lens',50,parent='root',at='2026-09-01T00:00:02Z')
    for phase in ('product','design','plan','build','evaluate','engineering','retro'):
        flow.append(ws,{'kind':'progress','run':'shared','session':'root','phase':phase,'note':phase+' evidence'})
    flow.append(ws,{'kind':'finish','run':'shared','session':'root','note':'Product passed; original dashboard failed'})
    flow.main(['attach','--workspace',str(ws),'--tasks','tasks.json','--reviews','reviews.json','--evidence','review.md'])
    capsys.readouterr()
    depgraph.save(str(ws),{'modules':{'src':{'kind':'module','files':1},'cli':{'kind':'module','files':1}},'edges':[{'from':'cli','to':'src','kind':'imports'}]})
    page=dashboard.report_widget(str(ws))
    assert 'no active loop' not in page and 'no active tasks' not in page
    assert all(x in page for x in ['T1','T2','/root/lens','Real finding fixed','Product passed; original dashboard failed'])
    assert '<script>alert' not in page and '&lt;script&gt;' in page
    assert all('id="phase-'+p+'"' in page for p in ('product','design','plan','build','evaluate','engineering','retro'))
    assert 'srcdoc=' in page and 'Module dependency graph' in page
    assert 'Unknown tokens' not in page
    assert 'legacy_unverified' in page


def test_resumed_thread_totals_are_not_double_counted(tmp_path):
    snapshots=[]
    for i,total in enumerate((100,160)):
        path=tmp_path/f"part{i}.jsonl"
        _write_segment(path,session_id='root',total=total,resumed=bool(i))
        with path.open('a') as stream:
            stream.write(json.dumps({'type':'token_usage_record','ordinal':10+i,
                'timestamp':f'2026-09-01T00:00:{10+i}Z','payload':{'thread_id':'root',
                'thread_token_usage':{'input_tokens':total-5,'cached_input_tokens':0,
                                     'output_tokens':5,'total_tokens':total}}})+'\n')
        snapshots.append(meter.read_snapshot(str(path)))
    assert meter.aggregate(snapshots)['usage']['total_tokens']==160


def test_unsequenced_host_records_are_advisory_only(tmp_path):
    import pytest
    path=tmp_path/'guardian.jsonl';native(path,'guardian',20,parent='root',guardian=True)
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    rows[-1].pop('ordinal')
    rows.append({'type':'token_usage_record','timestamp':'2026-09-01T00:00:08Z',
                 'payload':{'thread_id':'guardian','thread_token_usage':{
                     'input_tokens':19,'cached_input_tokens':0,'output_tokens':1,'total_tokens':20}}})
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    with pytest.raises(meter.NativeSessionMeterError):meter.read_snapshot(str(path))
    advisory=meter.read_snapshot(str(path),allow_unsequenced=True)
    assert advisory['usage']['total_tokens']==20
    assert advisory['ordinal_basis']=='bounded tail position'


@pytest.mark.parametrize("thread_environment", [True, False])
def test_codex_compatibility_plugin_root_keeps_native_hook_counters(tmp_path,monkeypatch,thread_environment):
    ws,sessions,_=setup_run(tmp_path,monkeypatch)
    monkeypatch.setenv('CLAUDE_PLUGIN_ROOT','/installed/taskplane')
    for key in ('CLAUDECODE','TASKPLANE_CLAUDE_SESSION_ID','CLAUDE_SESSION_ID'):
        monkeypatch.delenv(key,raising=False)
    native(sessions/'root.jsonl','root',140)
    event={'hook_event_name':'PostToolUse','session_id':'root','cwd':str(ws),
           'transcript_path':str(sessions/'root.jsonl'),'tool_name':'Bash',
           'tool_input':{'command':'cat app.py'}}
    if not thread_environment:monkeypatch.delenv('CODEX_THREAD_ID')
    assert not flow.claude_session(event)
    if thread_environment:assert not flow.claude_session({})
    assert flow._controller(ws,'root',event=event).adapter.name == 'codex'
    assert flow._observe_hook(event)=={}
    observed=flow.read_events(ws)[-1]
    assert observed['kind']=='hook' and observed.get('host')!='claude'
    assert observed['usage_status']=='observed' and observed['usage']['total_tokens']==140
    assert flow.report(ws)['tokens']['total_tokens']==40
    # An explicit host identity remains stronger than inherited environment.
    assert flow.claude_session({'host':'claude'})
    monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID','nested-claude')
    assert flow.claude_session({})
    assert not flow.claude_session({'thread_id':'root'})
def test_archived_sessions_and_unrelated_metadata_errors_are_scoped(tmp_path, monkeypatch):
    ws, sessions, run = setup_run(tmp_path, monkeypatch)
    archive = sessions.parent/'archived_sessions'; archive.mkdir()
    native(archive/'lens.jsonl', 'lens', 90, parent='root', at='2026-09-01T00:00:02Z')
    native(sessions/'broken.jsonl', 'unrelated', 999)
    path = sessions/'broken.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['payload']['history_base'] = {'thread_id': 'wrong', 'end_ordinal_exclusive': 1, 'end_byte_offset': 1}
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    result = flow_usage.reconcile(run, [])
    assert next(s for s in result['sessions'] if s['session'] == 'lens')['usage']['total_tokens'] == 90
    assert result['token_coverage']['discovery_errors'] == 0
    assert result['token_coverage']['inventory_errors'] == 1
    assert result['token_coverage']['discovery_diagnostics'][0]['scope'] == 'unrelated_inventory'


def test_counter_reset_is_unknown_not_zero(tmp_path, monkeypatch):
    _, sessions, run = setup_run(tmp_path, monkeypatch)
    native(sessions/'root.jsonl', 'root', 40)
    result = flow_usage.reconcile(run, [])
    root = next(s for s in result['sessions'] if s['session'] == 'root')
    assert root['usage'] is None and root['status'] == 'partial'
    assert 'reset' in root['errors'][0]
    assert result['tokens'] is None
