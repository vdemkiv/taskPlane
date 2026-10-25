"""Public regressions for the September Engineering findings and entry phases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import depgraph, flow, native_session_meter as meter, workflow
from taskplane.tests.test_workflow_host import controller, native as human_event
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_cli_delivery import run_cli
from contextlib import redirect_stdout
import io

ROOT = Path(__file__).resolve().parents[2]


def cli(ws, *args, root=ROOT):
    result = subprocess.run([sys.executable, str(root / "taskplane/tp.py"), *args],
                            cwd=ws, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def symlink(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        pytest.skip(f"Host cannot create a symlink: {exc}")


@pytest.mark.parametrize("target", ["directory", "flow-events.jsonl", "dashboard.html", ".gitignore", "flow-events.jsonl.lock"])
def test_flow_never_writes_through_runtime_symlinks(tmp_path, target):
    c, _, _ = controller(tmp_path)
    ws = c.workspace
    import shutil
    shutil.rmtree(ws / ".taskplane")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "sentinel"
    victim.write_bytes(b"KEEP THESE BYTES")
    if target == "directory":
        symlink(ws / ".taskplane", outside)
    else:
        (ws / ".taskplane").mkdir()
        symlink(ws / ".taskplane" / target, victim)
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = flow.main(["start", "--workspace", str(ws), "--goal", "Safe persistence"], governor=c)
    report = json.loads(stream.getvalue())
    assert code in (0, 2)
    assert victim.read_bytes() == b"KEEP THESE BYTES"
    assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]
    assert report.get("status") == "blocked" or report.get("evidence_errors")
    if target == "dashboard.html":
        result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"), "dashboard",
                                 "--workspace", str(ws)], capture_output=True, text=True)
        assert result.returncode != 0
        assert victim.read_bytes() == b"KEEP THESE BYTES"


def test_torn_journal_and_short_writes_preserve_finish(tmp_path, monkeypatch):
    flow.append(tmp_path, {"kind": "start", "run": "repair", "session": "root"})
    with (tmp_path / flow.JOURNAL).open("ab") as stream:
        stream.write(b'{"kind":"interrupted"')
    real_write = os.write
    monkeypatch.setattr(flow.os, "write", lambda fd, data: real_write(fd, data[:7]))
    flow.append(tmp_path, {"kind": "finish", "run": "repair", "session": "root", "note": "Done"})
    report = flow.report(tmp_path)
    assert report["observation_status"] == "finished"
    assert report["status"] == "legacy_unverified"
    assert report["outcome"] == "Done"
    assert [r["kind"] for r in flow.read_events(tmp_path)] == ["start", "finish"]


def test_concurrent_journal_writers_recover_one_torn_tail(tmp_path):
    flow.append(tmp_path, {"kind": "start", "run": "shared", "session": "root"})
    with (tmp_path / flow.JOURNAL).open("ab") as stream:
        stream.write(b'{"kind":')
    script = """from pathlib import Path
import sys
from taskplane import flow
for i in range(15):
    flow.append(Path(sys.argv[1]), {'kind':'hook','run':'shared','session':sys.argv[2], 'call_id':str(i)})
"""
    def worker(number):
        return subprocess.run([sys.executable, "-c", script, str(tmp_path), str(number)],
                              cwd=ROOT, capture_output=True, text=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    rows = flow.read_events(tmp_path)
    assert len(rows) == 61
    assert len({(r['session'], r['call_id']) for r in rows if r['kind'] == 'hook'}) == 60


def test_lens_cli_uses_saved_hub_and_contract_signals(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core/value.py").write_text("VALUE = 1\n")
    graph = {"modules": {n: {"kind": "module", "files": 1} for n in ["core", "a", "b", "c", "contract:API"]},
             "edges": [{"from": n, "to": "core", "kind": "imports"} for n in ["a", "b", "c"]]
                      + [{"from": "core", "to": "contract:API", "kind": "consumes"}]}
    depgraph.save(str(tmp_path), graph)
    result = cli(tmp_path, "lens", "--workspace", str(tmp_path), "--files", "core/value.py")
    assert result["architecture"]["verdict"] == "deep"
    assert any("hub module" in e for e in result["architecture"]["evidence"])
    assert any("contract:API" in e for e in result["architecture"]["evidence"])


def edge_set(graph):
    return {(e['from'], e['to'], e['kind']) for e in graph['edges']}


def test_incremental_imports_follow_target_additions_renames_and_exclusions(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "client").mkdir(parents=True)
    (ws / "client/main.js").write_text("import '../service/api.js';\n")
    depgraph.scan(str(ws))
    (ws / "service").mkdir()
    (ws / "service/api.js").write_text("export const api = 1;\n")
    assert ("client", "service", "imports") in edge_set(depgraph.scan(str(ws)))
    for name in ("service-v1", "service-v2"):
        (ws / "service/package.json").write_text(json.dumps({"name": name}))
        incremental = depgraph.scan(str(ws))
        assert ("client", name, "imports") in edge_set(incremental)
        assert depgraph.impact(str(ws), ["service/api.js"])["total_impacted"] == 1
    (ws / "components.yaml").write_text("exclude:\n  - service\n")
    assert not edge_set(depgraph.scan(str(ws)))
    (ws / "components.yaml").write_text("# restore service\n")
    restored = depgraph.scan(str(ws))
    fresh = tmp_path / "fresh"
    import shutil
    shutil.copytree(ws, fresh, ignore=shutil.ignore_patterns(".taskplane"))
    assert edge_set(restored) == edge_set(depgraph.scan(str(fresh)))
    assert ("client", "service-v2", "imports") in edge_set(restored)


def native(path, total, *, resumed=False, sid="root", timestamp="2026-09-01T00:00:05Z"):
    metadata = {"id": sid, "timestamp": "2026-09-01T00:00:00Z"}
    if resumed:
        metadata["history_base"] = {"thread_id": sid, "end_ordinal_exclusive": 1, "end_byte_offset": 1}
    rows = [{"type": "session_meta", "payload": metadata},
            {"type": "token_usage_record", "ordinal": 2, "timestamp": timestamp,
             "payload": {"thread_id": sid, "thread_token_usage": {
                 "input_tokens": total - 10, "cached_input_tokens": 0, "output_tokens": 10, "total_tokens": total}}}]
    if path.exists():
        prior = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]['ordinal'] = max(r.get('ordinal', 0) for r in prior) + 1
        rows = prior + [rows[-1]]
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_resumed_task_start_captures_one_logical_baseline(tmp_path, monkeypatch):
    sessions = Path(os.environ['CODEX_HOME']) / 'sessions'
    sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    native(sessions / 'part1-root.jsonl', 100)
    native(sessions / 'part2-root.jsonl', 160, resumed=True, timestamp="2026-09-01T00:00:06Z")
    native(sessions / 'foreign-root.jsonl', 999, sid='unrelated')
    from taskplane.tests.test_flow_integration import index_native
    index_native(sessions / 'part2-root.jsonl', 'root')
    index_native(sessions / 'foreign-root.jsonl', 'unrelated')
    from taskplane import workflow_host
    from taskplane.tests.test_workflow_host import FixtureHost
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    initial, _, _ = prepare(workspace)
    target = tmp_path / 'protected.json'
    target.write_text(json.dumps({'schema': 'taskplane.control/v1', 'workspace': str(workspace),
                                  'root': 'root', 'active': None, 'runs': {}}))
    c = workflow_host.Controller(workspace, 'root', FixtureHost(target, initial['scope']))
    # Public start must create the run; an existing run with lost history cannot
    # legitimately replace its missing baseline with current session counters.
    started = json.loads(run_cli(c.workspace, 'flow', 'start', '--workspace', str(c.workspace), '--goal', 'Resume', governor=c))
    assert started['tokens']['total_tokens'] == 0
    native(sessions / 'part2-root.jsonl', 220, resumed=True, timestamp="2026-09-01T00:00:07Z")
    report = cli(c.workspace, 'flow', 'report', '--workspace', str(c.workspace))
    assert report['tokens']['total_tokens'] == 60
    assert report['native_tokens']['total_tokens'] == 220
    assert report['token_coverage']['measured_sessions'] == 1


def test_partial_thread_counter_is_disclosed(tmp_path):
    valid, invalid = tmp_path / 'one.jsonl', tmp_path / 'two.jsonl'
    native(valid, 100)
    invalid.write_text('interrupted')
    result = meter.read_logical_snapshot([valid, invalid], 'root')
    assert result['usage']['total_tokens'] == 100
    assert result['partial']


def test_git_review_preserves_special_paths_and_literal_patch_scope(tmp_path):
    names = ['café.py', 'space name.py', '[literal].py']
    if os.name != 'nt':
        names += ['quote".py', 'tab\t.py', 'line\nbreak.py']
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    for i, name in enumerate(names):
        (tmp_path / name).write_text(f'VALUE = {i}\n')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'baseline'], cwd=tmp_path, check=True)
    cli(tmp_path, 'review', 'start', '--workspace', str(tmp_path), '--scope', 'repository')
    inventory = json.loads((tmp_path / '.taskplane/review-source.json').read_text())
    assert set(inventory['files']) == set(names)
    for i, name in enumerate(names):
        (tmp_path / name).write_text(f'VALUE = {i + 100}\n')
    cli(tmp_path, 'review', 'start', '--workspace', str(tmp_path), '--base', 'HEAD')
    inventory = json.loads((tmp_path / '.taskplane/review-source.json').read_text())
    assert set(inventory['files']) == set(names)
    assert all(f'+VALUE = {i+100}' in inventory['patch'] for i in range(len(names)))
    cli(tmp_path, 'review', 'start', '--workspace', str(tmp_path), '--paths', '[literal].py')
    selected = json.loads((tmp_path / '.taskplane/review-source.json').read_text())
    assert '+VALUE = 102' in selected['patch']
    assert '+VALUE = 100' not in selected['patch']


@pytest.mark.parametrize('entry', ['product', 'design', 'engineering'])
def test_standalone_entry_and_engineering_product_handoff_share_artifacts(tmp_path, monkeypatch, entry):
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    c, host, state = controller(tmp_path, {"entry": entry, "standalone": True})
    tmp_path = c.workspace
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/value.py').write_text('VALUE = 1\n')
    cli(tmp_path, 'graph', '--workspace', str(tmp_path), 'scan', '--decompose')
    (tmp_path / 'tasks.json').write_text(json.dumps({'tasks': [{'id':'F1', 'title':'Review input', 'paths':['app.py','build.json'], 'phase':'build', 'owner':'primary', 'verification':'Check F1 sentinel', 'criteria':['AC1'], 'status':'pending', 'dependencies':[]}]}))
    (tmp_path / 'reviews.json').write_text(json.dumps([{'lens':'security','agent':'root','evidence':'finding.md'}]))
    (tmp_path / 'finding.md').write_text('F1: protect the outside sentinel')
    started = json.loads(run_cli(tmp_path, 'flow', 'start', '--workspace', str(tmp_path), '--phase', entry, '--standalone',
                  '--tasks', 'tasks.json', '--reviews', 'reviews.json', '--evidence', 'finding.md', governor=c))
    assert started['phase'] == entry and started['entry_phase'] == entry
    assert len(started['tasks']) == 1 and len(started['reviews']) == 1
    page = (tmp_path / flow.DASHBOARD).read_text()
    assert f'Authorized route: {entry.title()}' in page
    assert page.count('<a class="stage ') == 1
    assert 'F1: protect' in page and 'srcdoc=' in page
    if entry == 'engineering':
        original_tasks = (tmp_path/'tasks.json').read_bytes()
        _, out, _ = prepare(tmp_path, 'engineering')
        (tmp_path/'tasks.json').write_bytes(original_tasks)
        out.update(run=state['run'], visit=workflow.current(state)['id'],
                   findings=[{'id':'F1','severity':'major','source':'app.py:1','evidence':'finding.md'}],
                   route_change={'kind':'delivery','scope':host.scope})
        (tmp_path/'engineering.json').write_text(json.dumps(out))
        state=c.apply('submit',state['run'],expected_revision=state['revision'],output='engineering.json',tasks='tasks.json')
        key=human_event(host,state)
        state=c.apply('decide',state['run'],expected_revision=state['revision'],native_reference=key)
        product = json.loads(run_cli(tmp_path, 'flow', 'advance', '--workspace', str(tmp_path), '--phase', 'product',
                      '--expected-revision',str(state['revision']), '--note', 'F1 defines the product criterion',governor=c))
        assert product['run'] == started['run'] and product['phase'] == 'product'
        assert product['artifacts'] == started['artifacts']
        assert 'Engineering → Product' in (tmp_path / flow.DASHBOARD).read_text()


def journal_fixture(workspace, rows):
    path = workspace / flow.JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path


def journal_read(workspace, run='r'):
    from taskplane import flow_journal as journal
    with journal.action_budget(workspace) as budget:
        view = journal.projection(workspace)
        start = view.start(run=run)
        result = view.summary(start) if start else None
        return result, view.coverage(), dict(source=budget.source_bytes, records=budget.records,
                                            queries=budget.query_rows, output=budget.output_bytes, reducers=budget.reducer_rows)


def test_journal_bounded_cold_warm_append_restart_and_late_duplicate(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'RECORDS', 31)
    rows = [dict(kind='start', run='r', session='root', at='2026-01-01T00:00:00Z')]
    rows += [dict(kind='hook', run='r', session='root', event='PreToolUse', call_id=str(i),
                  outcome='admitted', action='same', usage={'total_tokens': 10**30+i}) for i in range(200)]
    path = journal_fixture(tmp_path, rows)
    source = path.read_bytes()
    previous = -1
    for _ in range(12):
        result, coverage, cost = journal_read(tmp_path)
        assert cost['records'] <= 31 and cost['source'] <= journal.SOURCE_BYTES
        assert coverage['offset'] >= previous
        previous = coverage['offset']
        if coverage['caught_up']:
            break
        assert coverage['status'] == 'partial' and not result['advice']
    assert coverage['status'] == 'complete'
    expected = flow.summarize(rows, rows[0])
    assert {k:result[k] for k in expected} == expected
    _, _, warm = journal_read(tmp_path)
    assert warm['records'] == 0 and warm['source'] <= 2 * journal.ANCHOR_BYTES
    with path.open('a') as stream:
        stream.write(json.dumps({**rows[1], 'outcome':'denied', 'usage':{'total_tokens':10**40}})+'\n')
        stream.write(json.dumps(dict(kind='progress',run='r',session='root',phase='build',note='Milestone'))+'\n')
        stream.write(json.dumps({**rows[1], 'outcome':'error'})+'\n')
    after, _, cost = journal_read(tmp_path)
    assert cost['records'] == 3
    assert after['tool_calls'] == 200 and after['hook_outcomes']['denied'] == 0
    assert after['tokens'] == expected['tokens'] and not after['advice']
    assert path.read_bytes().startswith(source)
    script = '''from pathlib import Path
from taskplane import flow_journal as j
import json,sys
with j.action_budget() as b:
 p=j.projection(Path(sys.argv[1])); print(json.dumps([p.total('r'),b.records,b.source_bytes]))
'''
    completed = subprocess.run([sys.executable,'-c',script,str(tmp_path)],cwd=ROOT,capture_output=True,text=True)
    assert completed.returncode == 0, completed.stderr
    total, records, read_bytes = json.loads(completed.stdout)
    assert total['tools'] == 200 and total['raw'] == 204
    assert records == 0 and read_bytes <= 2 * journal.ANCHOR_BYTES


def test_journal_raw_evidence_survives_call_dedup_and_composite_paging(tmp_path):
    from taskplane import flow_journal as journal
    rows = [dict(kind='start',run='r',session='root')]
    hook = dict(kind='hook',run='r',session='root',event='PreToolUse',call_id='duplicate')
    rows += [hook, {**hook, 'artifacts':{'evidence':['a','b','c']},
                    'captured':{'evidence':{'a':{'text':'first'},'b':{'text':'second'}}}},
             {**hook,'artifacts':{'evidence':['b','d']},'captured':{'evidence':{'a':{'text':'last'}}}}]
    journal_fixture(tmp_path, rows)
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.summary(rows[0])['tool_calls'] == 1
        artifacts,captured = view.attachments('r')
        assert artifacts['evidence'] == ['a','b','c','d']
        assert captured['evidence']['a']['text'] == 'last'
        first,cursor = view.fields('r','artifact-evidence',limit=2)
        second,cursor2 = view.fields('r','artifact-evidence',after=cursor,limit=2)
        assert [k for _,k,_ in first+second] == ['a','b','c','d']
        assert cursor2 is None


def test_journal_torn_malformed_oversized_and_rotation_are_observable(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'RECORD_BYTES', 400)
    rows = [dict(kind='start',run='r',session='root')]
    path = journal_fixture(tmp_path, rows)
    with path.open('ab') as stream:
        stream.write(b'x'*1200+b'\n'+b'{broken}\n'+b'{"kind":"hook"')
    _, coverage, _ = journal_read(tmp_path)
    assert not coverage['caught_up']
    assert set(coverage['errors']) == {'oversized_record','invalid_record'}
    old_offset = coverage['offset']
    with path.open('ab') as stream:
        stream.write(b',"run":"r","session":"root","event":"PreToolUse"}\n')
    result, coverage, _ = journal_read(tmp_path)
    assert coverage['caught_up'] and coverage['offset'] > old_offset
    assert result['tool_calls'] == 1 and coverage['status'] == 'partial'
    path.rename(path.with_suffix('.original'))
    journal_fixture(tmp_path,[dict(kind='start',run='other',session='root')])
    result, coverage, _ = journal_read(tmp_path, 'other')
    assert result['run'] == 'other' and result['tool_calls'] == 0
    assert 'generation_changed' in coverage['errors']
    assert path.with_suffix('.original').exists()


def test_journal_nested_budget_is_not_multiplied(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'RECORDS', 7)
    journal_fixture(tmp_path,[dict(kind='hook',run='r',session='root',call_id=str(i)) for i in range(40)])
    with journal.action_budget() as outer:
        for _ in range(10):
            with journal.action_budget() as inner:
                assert inner is outer
                view = journal.projection(tmp_path)
        assert outer.records == 7 and not view.coverage()['caught_up']
    _, coverage, cost = journal_read(tmp_path)
    assert cost['records'] == 7 and coverage['offset'] > 0


def test_journal_invalid_reducer_is_atomic_and_corrupt_cache_never_replays_unbounded(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'RECORDS', 5)
    bad = dict(kind='hook',run='r',session='root',event='PreToolUse',call_id='bad',usage={'total_tokens':-1})
    rows = [dict(kind='start',run='r',session='root'),bad,{**bad,'usage':{'total_tokens':10}}]
    rows += [dict(kind='hook',run='r',session='root',event='PreToolUse',call_id=str(i)) for i in range(30)]
    journal_fixture(tmp_path, rows)
    result, coverage, cost = journal_read(tmp_path)
    assert result['tool_calls'] == 3 and 'invalid_record' in coverage['errors']
    (tmp_path/'.taskplane'/journal.CACHE).write_bytes(b'invalid sqlite')
    result, coverage, cost = journal_read(tmp_path)
    assert coverage['status'] == 'unavailable' and cost['records'] == 0
    _, coverage, cost = journal_read(tmp_path)
    assert cost['records'] == 5 and not coverage['caught_up']


def test_journal_query_output_and_sql_work_have_shared_limits(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path,[dict(kind='progress',run='r',session='root',note='n'*100) for _ in range(30)])
    with journal.action_budget():
        journal.projection(tmp_path)
    monkeypatch.setattr(journal,'OUTPUT_BYTES',500)
    with journal.action_budget() as budget:
        view=journal.projection(tmp_path)
        page=view.page('r',limit=30)
        assert not page['complete'] and view.detail_partial
        assert budget.output_bytes <= 500 and budget.query_rows <= journal.QUERY_ROWS
        assert budget.vm_steps <= journal.VM_STEPS + 1000


def test_journal_indexed_selection_matches_reverse_lineage_and_finished_latest(tmp_path):
    from taskplane import flow_journal as journal
    rows=[dict(kind='start',run='foreign',session='foreign'),
          dict(kind='hook',run='foreign',session='foreign',root='foreign',event='SubagentStart'),
          dict(kind='start',run='r',session='root'),
          dict(kind='hook',run='r',session='root',root='root',event='SubagentStart',child='child'),
          dict(kind='start',run='closed',session='root'),dict(kind='finish',run='closed',session='root')]
    journal_fixture(tmp_path,rows)
    with journal.action_budget():
        view=journal.projection(tmp_path)
        for session in ('root','child','foreign','unrelated'):
            assert view.active(session) == flow.active_run(rows,session)
        assert view.start(run='r') == rows[2]


def test_journal_latest_usage_and_chronological_boundary_do_not_depend_on_detail_page(tmp_path):
    from taskplane import flow_journal as journal
    first=dict(kind='start',run='r',session='root',at='2026-01-01T00:00:00Z')
    rows=[first]+[dict(kind='hook',run='r',session='root') for _ in range(300)]
    rows += [dict(kind='start',run='later',session='root',at='2026-01-01T00:00:20Z'),
             dict(kind='start',run='next',session='root',at='2026-01-01T00:00:10Z'),
             dict(kind='usage',run='r',session='root',measurement={'sessions':[]})]
    journal_fixture(tmp_path,rows)
    journal_read(tmp_path)
    with journal.action_budget():
        view=journal.projection(tmp_path)
        selected=view.usage_rows(first)
        assert rows[-1] in selected and rows[-2] in selected and rows[-3] not in selected
        assert not view.detail_partial and len(selected)<10


def test_large_journal_source_and_reducer_work_are_bounded(tmp_path):
    from taskplane import flow_journal as journal
    rows=[dict(kind='start',run='r',session='root')]
    rows += [dict(kind='hook',run='r',session='root',event='PreToolUse',call_id=str(i),note='x'*8000)
             for i in range(4500)]
    path=journal_fixture(tmp_path,rows)
    assert path.stat().st_size > 35_000_000
    for _ in range(20):
        result,coverage,cost=journal_read(tmp_path)
        assert cost['source'] <= journal.SOURCE_BYTES and cost['records'] <= journal.RECORDS
        assert cost['queries'] <= journal.QUERY_ROWS and cost['output'] <= journal.OUTPUT_BYTES
        assert cost['reducers'] <= journal.REDUCER_ROWS
        if coverage['caught_up']:
            break
    assert coverage['status']=='complete' and result['tool_calls']==4500
    _,_,cost=journal_read(tmp_path)
    assert cost['records']==0 and cost['source']<=2*journal.ANCHOR_BYTES


def test_journal_lock_contention_returns_explicit_unavailable(tmp_path):
    import sqlite3
    import time
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path,[dict(kind='start',run='r',session='root')])
    journal_read(tmp_path)
    locked=sqlite3.connect(str(tmp_path/'.taskplane'/journal.CACHE))
    locked.execute('BEGIN EXCLUSIVE')
    try:
        start=time.monotonic()
        _,coverage,cost=journal_read(tmp_path)
        assert time.monotonic()-start < 1
        assert coverage['status']=='unavailable' and cost['records']==0
    finally:
        locked.rollback()
        locked.close()
    assert journal_read(tmp_path)[1]['status']=='complete'


def test_evidence_union_preserves_input_order_within_one_record(tmp_path):
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path,[dict(kind='start',run='r',session='root',artifacts={'evidence':['z','a','m']})])
    with journal.action_budget():
        view=journal.projection(tmp_path)
        assert view.attachments('r')[0]['evidence']==['z','a','m']
        page,cursor=view.fields('r','artifact-evidence',limit=1)
        second,_=view.fields('r','artifact-evidence',after=cursor,limit=2)
        assert [key for _,key,_ in page+second]==['z','a','m']


def test_append_after_exhausted_action_never_claims_current_coverage(tmp_path,monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal,'RECORDS',1)
    path=journal_fixture(tmp_path,[dict(kind='start',run='r',session='root')])
    with journal.action_budget():
        view=journal.projection(tmp_path)
        assert view.coverage()['caught_up']
        with path.open('a') as stream:
            stream.write(json.dumps(dict(kind='finish',run='r',session='root'))+'\n')
        view.refresh()
        assert not view.coverage()['caught_up'] and view.coverage()['backlog_bytes'] > 0
    assert journal_read(tmp_path)[0]['status']=='finished'


def test_journal_query_exhaustion_is_unknown_not_zero(tmp_path,monkeypatch):
    from taskplane import flow_journal as journal
    start=dict(kind='start',run='r',session='root')
    journal_fixture(tmp_path,[start,dict(kind='hook',run='r',session='root',event='PreToolUse')])
    journal_read(tmp_path)
    monkeypatch.setattr(journal,'QUERY_ROWS',0)
    with journal.action_budget():
        view=journal.projection(tmp_path)
        result=view.summary(start)
        assert result['tool_calls'] is None and result['hook_outcomes']['attempted'] is None
        assert result['journal_coverage']['details_partial']


def test_journal_truncate_regrow_and_query_plan(tmp_path):
    from taskplane import flow_journal as journal
    path=journal_fixture(tmp_path,[dict(kind='start',run='r',session='root'),
                                  dict(kind='hook',run='r',session='root',event='PreToolUse')])
    journal_read(tmp_path)
    original=path.read_bytes()
    path.write_bytes(original.replace(b'"r"',b'"s"'))
    result,coverage,_=journal_read(tmp_path,'s')
    assert result['tool_calls']==1 and 'generation_changed' in coverage['errors']
    with journal.action_budget():
        view=journal.projection(tmp_path)
        plan=view.db.execute("EXPLAIN QUERY PLAN SELECT body FROM events WHERE g=? AND run=? AND kind='start' ORDER BY pos DESC LIMIT 1",(view.generation,'"s"')).fetchall()
        assert any('event_run_kind' in row[3] for row in plan)


def test_journal_projection_lock_covers_open_read_and_recovery(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path, [dict(kind='start', run='r', session='root')])
    first = journal.Projection(tmp_path, journal.Budget())
    location = tmp_path / '.taskplane' / journal.CACHE
    original = location.read_bytes()
    connect = journal.sqlite3.connect
    calls = []
    def observed(*args, **kwargs):
        calls.append(args)
        return connect(*args, **kwargs)
    monkeypatch.setattr(journal.sqlite3, 'connect', observed)
    try:
        second = journal.Projection(tmp_path, journal.Budget())
        assert second.coverage()['status'] == 'unavailable'
        assert second.db is None and second.budget.source_bytes == 0
        assert not calls and location.read_bytes() == original
    finally:
        first.close()
    assert journal_read(tmp_path)[1]['status'] == 'complete'


@pytest.mark.parametrize('failure', ['disk I/O error', 'database or disk is full',
                                      'database is locked', 'interrupted'])
def test_journal_operational_errors_never_quarantine_cache(tmp_path, monkeypatch, failure):
    import sqlite3
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path, [dict(kind='start', run='r', session='root')])
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    original = location.read_bytes()
    connect = journal.sqlite3.connect
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError(failure)
    monkeypatch.setattr(journal.sqlite3, 'connect', unavailable)
    _, coverage, cost = journal_read(tmp_path)
    assert coverage['status'] == 'unavailable' and cost['source'] == 0
    assert location.read_bytes() == original and not list(location.parent.glob('*.corrupt-*'))
    monkeypatch.setattr(journal.sqlite3, 'connect', connect)
    assert journal_read(tmp_path)[1]['status'] == 'complete'


def test_journal_corruption_quarantines_sidecars_before_bounded_rebuild(tmp_path, monkeypatch):
    import sqlite3
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'RECORDS', 3)
    source = journal_fixture(tmp_path, [dict(kind='start', run='r', session='root')]
                             + [dict(kind='progress', run='r', session='root') for _ in range(20)])
    source_bytes = source.read_bytes()
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    connect = journal.sqlite3.connect
    def corrupt(*args, **kwargs):
        for suffix in ('-journal', '-wal', '-shm'):
            Path(str(location) + suffix).write_bytes(b'orphan recovery bytes')
        raise sqlite3.DatabaseError('file is not a database')
    monkeypatch.setattr(journal.sqlite3, 'connect', corrupt)
    _, coverage, cost = journal_read(tmp_path)
    assert coverage['status'] == 'unavailable' and cost['records'] == 0
    for suffix in ('', '-journal', '-wal', '-shm'):
        assert not Path(str(location) + suffix).exists()
        assert len(list(location.parent.glob(location.name + suffix + '.corrupt-*'))) == 1
    monkeypatch.setattr(journal.sqlite3, 'connect', connect)
    _, coverage, cost = journal_read(tmp_path)
    assert coverage['status'] == 'partial' and cost['records'] == 3
    assert coverage['backlog_bytes'] > 0 and source.read_bytes() == source_bytes


@pytest.mark.parametrize('body', ['[]', '{broken'])
def test_journal_malformed_metadata_rebuilds_without_crashing(tmp_path, body):
    import sqlite3
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path, [dict(kind='start', run='r', session='root')])
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        db.execute('UPDATE meta SET body=?', (body,))
    assert journal_read(tmp_path)[1]['status'] == 'unavailable'
    assert journal_read(tmp_path)[1]['status'] == 'complete'


def test_journal_recovery_bytes_are_shared_and_checked_before_connect(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    workspaces = [tmp_path / 'one', tmp_path / 'two']
    for workspace in workspaces:
        journal_fixture(workspace, [dict(kind='start', run='r', session='root')])
        journal_read(workspace)
        (workspace / '.taskplane' / (journal.CACHE + '-journal')).write_bytes(b'\0' * 60)
    monkeypatch.setattr(journal, 'RECOVERY_BYTES', 100)
    with journal.action_budget() as budget:
        first = journal.projection(workspaces[0])
        assert first.coverage()['status'] == 'complete' and budget.recovery_bytes == 60
        second = journal.projection(workspaces[1])
        assert second.coverage()['status'] == 'unavailable' and budget.recovery_bytes == 60
        assert second.budget.records == 0  # Both source checkpoints were already warm.
    # Refusal keeps the bounded recovery data for later maintenance/retry.
    assert (workspaces[1] / '.taskplane' / (journal.CACHE + '-journal')).exists()


def test_journal_concurrent_corrupt_cache_recovery_converges_once(tmp_path):
    from taskplane import flow_journal as journal
    rows = [dict(kind='start', run='r', session='root')]
    rows += [dict(kind='hook', run='r', session='root', event='PreToolUse', call_id=str(i)) for i in range(90)]
    source = journal_fixture(tmp_path, rows)
    original = source.read_bytes()
    location = tmp_path / '.taskplane' / journal.CACHE
    location.write_bytes(b'corrupt sqlite database')
    with ThreadPoolExecutor(max_workers=6) as pool:
        outputs = list(pool.map(lambda _: journal_read(tmp_path), range(12)))
    assert all(cost['source'] <= journal.SOURCE_BYTES for _, _, cost in outputs)
    result, coverage, cost = journal_read(tmp_path)
    assert coverage['status'] == 'complete' and result['tool_calls'] == 90
    assert cost['records'] <= journal.RECORDS and source.read_bytes() == original
    assert journal_read(tmp_path)[2]['records'] == 0
    assert len(list(location.parent.glob(journal.CACHE + '.corrupt-*'))) == 1


@pytest.mark.parametrize('field,value', [('offset', 'invalid'), ('errors', None),
                                        ('identity', [1]), ('discard', 1)])
def test_journal_corrupt_metadata_fields_are_rebuilt(tmp_path, field, value):
    import sqlite3
    from taskplane import flow_journal as journal
    journal_fixture(tmp_path, [dict(kind='start', run='r', session='root')])
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        meta = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        meta[field] = value
        db.execute('UPDATE meta SET body=?', (json.dumps(meta),))
    assert journal_read(tmp_path)[1]['status'] == 'unavailable'
    assert journal_read(tmp_path)[1]['status'] == 'complete'


@pytest.mark.parametrize('patch', [
    {'admission': 'malformed'}, {'admission': []}, {'artifacts': []},
    {'artifacts': {'evidence': 'bad'}}, {'captured': []},
    {'captured': {'evidence': ['bad']}}, {'captured': {'reviews': 'bad'}},
    {'captured': {'reviews': {'engineering': {}}}},
    {'captured': {'reviews': ['bad']}}, {'usage': []}, {'tool': []},
    {'child': ['bad']},
])
def test_journal_nested_source_errors_skip_atomically_with_explicit_gap(tmp_path, patch):
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root', usage={'total_tokens': 10})
    hook = dict(kind='hook', run='r', session='root', event='PreToolUse',
                call_id='same', action='action', outcome='admitted', usage={'total_tokens': 20})
    malformed = {**hook, 'artifacts': {'marker': 'must rollback'}, **patch}
    path = journal_fixture(tmp_path, [start, malformed, hook, hook])
    original = path.read_bytes()
    result, coverage, cost = journal_read(tmp_path)
    assert result['tool_calls'] == 1 and result['tokens'] == {'total_tokens': 10}
    assert coverage['caught_up'] and coverage['status'] == 'partial'
    assert coverage['errors'] == ['invalid_record'] and cost['records'] == 4
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        assert view.total('r')['raw'] == 3
        assert view.attachments('r') == ({}, {})
        assert view.page('r')['rows'] == [start, hook, hook]
        assert budget.records == 0
        assert not view.db.in_transaction
    assert path.read_bytes() == original


def test_journal_late_invalid_source_rolls_back_all_record_contributions(tmp_path):
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root', usage={'total_tokens': 10})
    hook = dict(kind='hook', run='r', session='root', event='PreToolUse', call_id='same')
    # A valid counter object with a missing baseline category fails after event,
    # lineage, attachment, representative and dedup writes have begun.
    bad = {**hook, 'root': 'foreign', 'child': 'bad-child', 'closure_id': 'bad-close',
           'artifacts': {'marker': 'bad'}, 'usage': {'output_tokens': 5}}
    path = journal_fixture(tmp_path, [start, bad, {**hook, 'usage': {'total_tokens': 20}}])
    original = path.read_bytes()
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.summary(start)['tokens'] == {'total_tokens': 10}
        assert view.total('r')['raw'] == 2 and view.total('r')['tools'] == 1
        assert view.attachments('r') == ({}, {})
        assert view.db.execute('SELECT count(*) FROM lineage').fetchone()[0] == 0
        assert view.db.execute('SELECT count(*) FROM closure').fetchone()[0] == 0
        assert view.db.execute('SELECT count(*) FROM seen').fetchone()[0] == 1
        assert not view.db.in_transaction
        assert view.coverage()['errors'] == ['invalid_record']
    assert path.read_bytes() == original


@pytest.mark.parametrize('table,body', [
    ('totals', '{broken'), ('totals', '[]'), ('totals', '{}'),
    ('totals', 'null'), ('samples', '{broken'), ('samples', '[]'),
    ('samples', '{}'), ('samples', '{"first":[],"maximum":{}}'),
    ('samples', '{"first":{"total_tokens":10},"maximum":{}}'),
    ('samples', '{"first":null,"maximum":{"total_tokens":1}}'),
])
@pytest.mark.parametrize('access', ['append', 'warm'])
def test_journal_corrupt_reducer_bodies_never_skip_valid_source(tmp_path, monkeypatch, table, body, access):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root', usage={'total_tokens': 10})
    first = dict(kind='hook', run='r', session='root', event='PreToolUse',
                 call_id='first', action='action', usage={'total_tokens': 20})
    path = journal_fixture(tmp_path, [start, first])
    assert journal_read(tmp_path)[0]['tool_calls'] == 1
    location = tmp_path / '.taskplane' / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        prior = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        db.execute(f'UPDATE {table} SET body=?', (body,))
    later = {**first, 'call_id': 'later', 'usage': {'total_tokens': 30}}
    if access == 'append':
        with path.open('a') as stream:
            stream.write(''.join(json.dumps(row) + '\n' for row in [later, later]))
    original = path.read_bytes()
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        if access == 'warm' and table == 'samples':
            # Samples have no public warm summary consumer. Exercise the same
            # bounded query boundary without adding a whole-run sample scan.
            assert view._query('SELECT body FROM samples WHERE g=?',
                               (view.generation,), shape='samples') == []
        result = view.summary(start)
        assert result['tool_calls'] is None and result['tokens'] is None
        coverage = view.coverage()
        assert coverage['status'] == 'unavailable' and not coverage['caught_up']
        assert coverage['offset'] == prior['offset']
        assert 'invalid_record' not in coverage['errors']
        assert any(error.startswith('cache_unavailable:invalid_cache_body') for error in coverage['errors'])
        assert budget.records <= 1 and view.db is None
    quarantine = list(location.parent.glob(journal.CACHE + '.corrupt-*'))
    assert len(quarantine) == 1 and not location.exists()
    with closing(sqlite3.connect(quarantine[0])) as db, db:
        # The failed batch preserves its exact previous committed checkpoint,
        # indexed events, dedup keys and contributions in the quarantined copy.
        assert json.loads(db.execute('SELECT body FROM meta').fetchone()[0]) == prior
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM seen').fetchone()[0] == 1
    monkeypatch.setattr(journal, 'RECORDS', 1)
    offsets = []
    for _ in range(5):
        result, coverage, cost = journal_read(tmp_path)
        offsets.append(coverage['offset'])
        assert cost['records'] == 1 and cost['source'] <= journal.SOURCE_BYTES
        assert cost['reducers'] <= journal.REDUCER_ROWS
        assert cost['queries'] <= journal.QUERY_ROWS and cost['output'] <= journal.OUTPUT_BYTES
        if coverage['caught_up']:
            break
        assert coverage['status'] == 'partial'
    assert offsets == sorted(set(offsets))
    assert coverage['status'] == 'complete' and coverage['errors'] == []
    assert result['tool_calls'] == (2 if access == 'append' else 1)
    assert result['tokens'] == {'total_tokens': 20 if access == 'append' else 10}
    assert journal_read(tmp_path)[2]['records'] == 0
    script = '''from pathlib import Path
from taskplane import flow_journal as j
import json,sys
j.RECORDS = 1
with j.action_budget() as b:
 p=j.projection(Path(sys.argv[1])); print(json.dumps([p.total('r'),p.coverage(),b.records,b.source_bytes]))
'''
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    total, coverage, records, source_bytes = json.loads(completed.stdout)
    assert total['raw'] == (4 if access == 'append' else 2)
    assert total['tools'] == (2 if access == 'append' else 1)
    assert records == 0 and source_bytes <= 2 * journal.ANCHOR_BYTES
    assert coverage['status'] == 'complete' and path.read_bytes() == original


@pytest.mark.parametrize('field,value', [('raw', 'bad'), ('closed', 1), ('tokens', []),
                                        ('tokens', {'total_tokens': -1})])
def test_journal_total_field_corruption_is_unavailable(tmp_path, field, value):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root')
    journal_fixture(tmp_path, [start])
    journal_read(tmp_path)
    with closing(sqlite3.connect(tmp_path / '.taskplane' / journal.CACHE)) as db, db:
        total = json.loads(db.execute('SELECT body FROM totals').fetchone()[0])
        total[field] = value
        db.execute('UPDATE totals SET body=?', (json.dumps(total),))
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.summary(start)['tool_calls'] is None
        assert view.coverage()['status'] == 'unavailable'
    assert journal_read(tmp_path)[1]['status'] == 'complete'


@pytest.mark.parametrize('access', ['start', 'active', 'page', 'fields', 'post_completion', 'usage_rows'])
@pytest.mark.parametrize('body', ['{broken', '[]', '{"captured":{"reviews":"bad"}}'])
def test_journal_cached_detail_bodies_are_validated_before_return(tmp_path, access, body):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root')
    hook = dict(kind='hook', run='r', session='root', child='child', event='PreToolUse',
                admission={'authority': 'observation_only'})
    path = journal_fixture(tmp_path, [start, hook])
    original = path.read_bytes()
    journal_read(tmp_path)
    with closing(sqlite3.connect(tmp_path / '.taskplane' / journal.CACHE)) as db, db:
        if access in ('start', 'active', 'page'):
            db.execute("UPDATE events SET body=? WHERE kind='start'", (body,))
        else:
            family = 'child' if access in ('fields', 'usage_rows') else 'post-completion'
            db.execute('UPDATE fields SET body=? WHERE family=?', (body, family))
    with journal.action_budget():
        view = journal.projection(tmp_path)
        if access == 'start':
            assert view.start(run='r') is None
        elif access == 'active':
            assert view.active('root') is None
        elif access == 'page':
            assert view.page('r')['rows'] == []
        elif access == 'fields':
            assert view.fields('r', 'child')[0] == []
        elif access == 'post_completion':
            assert view.post_completion('r') == []
        else:
            view.usage_rows(start)
            assert not view.usage_complete
        assert view.coverage()['status'] == 'unavailable'
        assert view.summary(start)['tool_calls'] is None
    assert journal_read(tmp_path)[0]['tool_calls'] == 1
    assert path.read_bytes() == original


def test_journal_closes_paged_cursor_before_quarantining_corruption(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from taskplane import flow_journal as journal

    source = journal_fixture(tmp_path, [dict(kind='start', run='r', session='root'),
                                       dict(kind='progress', run='r', session='root')])
    original = source.read_bytes()
    journal_read(tmp_path)
    with closing(sqlite3.connect(tmp_path / '.taskplane' / journal.CACHE)) as db, db:
        db.execute("UPDATE events SET body='{broken' WHERE kind='start'")
    closed = []
    moved = []
    replace = journal.os.replace

    def quarantine(src, dst):
        # Windows cannot rename the database while an unfinished query holds it.
        assert closed == [True]
        moved.append(src)
        return replace(src, dst)

    with journal.action_budget():
        view = journal.projection(tmp_path)
        connection = view.db

        def execute(sql, args):
            cursor = connection.execute(sql, args)

            def close():
                cursor.close()
                closed.append(True)

            return SimpleNamespace(fetchone=cursor.fetchone, close=close)

        view.db = SimpleNamespace(execute=execute, rollback=connection.rollback, close=connection.close)
        monkeypatch.setattr(journal.os, 'replace', quarantine)
        assert view.page('r')['rows'] == []
        assert view.coverage()['status'] == 'unavailable'
    assert moved
    assert journal_read(tmp_path)[1]['status'] == 'complete'
    assert source.read_bytes() == original


@pytest.mark.parametrize('table', ['actions', 'child'])
def test_journal_other_cached_reducers_rebuild_without_advancing(tmp_path, table):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root')
    hook = dict(kind='hook', run='r', session='root', child='child', event='PreToolUse',
                action='action', call_id='one')
    path = journal_fixture(tmp_path, [start, hook])
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        before = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        if table == 'actions':
            db.execute("UPDATE actions SET n='bad'")
        else:
            db.execute("UPDATE fields SET body='[]' WHERE family='child'")
    with path.open('a') as stream:
        stream.write(json.dumps({**hook, 'call_id': 'two'}) + '\n')
    original = path.read_bytes()
    _, coverage, _ = journal_read(tmp_path)
    assert coverage['status'] == 'unavailable' and coverage['offset'] == before['offset']
    assert 'invalid_record' not in coverage['errors']
    assert journal_read(tmp_path)[0]['tool_calls'] == 2
    assert path.read_bytes() == original


@pytest.mark.parametrize('table', ['start', 'captured'])
def test_journal_cached_required_detail_fields_cannot_escape_validation(tmp_path, table):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root', captured={'reviews': []})
    journal_fixture(tmp_path, [start])
    journal_read(tmp_path)
    with closing(sqlite3.connect(tmp_path / '.taskplane' / journal.CACHE)) as db, db:
        if table == 'start':
            db.execute("UPDATE events SET body='{}'")
        else:
            db.execute('UPDATE fields SET body=? WHERE family=?', ('"bad"', 'captured'))
    with journal.action_budget():
        view = journal.projection(tmp_path)
        if table == 'start':
            assert view.start(run='r') is None
        else:
            assert view.attachments('r') == ({}, {})
        assert view.coverage()['status'] == 'unavailable'
    assert journal_read(tmp_path)[1]['status'] == 'complete'


def test_journal_cache_corruption_rolls_back_the_entire_uncommitted_batch(tmp_path):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root')
    path = journal_fixture(tmp_path, [start])
    journal_read(tmp_path)
    location = tmp_path / '.taskplane' / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        prior = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        db.execute("UPDATE totals SET body='[]'")
    with path.open('a') as stream:
        stream.write(json.dumps(dict(kind='start', run='other', session='other', hook_setup='host_adapter')) + '\n')
        stream.write(json.dumps(dict(kind='hook', run='r', session='root', event='PreToolUse')) + '\n')
    original = path.read_bytes()
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.meta == prior
        assert view.coverage()['status'] == 'unavailable'
    with closing(sqlite3.connect(next(location.parent.glob(journal.CACHE + '.corrupt-*')))) as db, db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        assert json.loads(db.execute('SELECT body FROM meta').fetchone()[0]) == prior
    result, coverage, _ = journal_read(tmp_path)
    assert result['tool_calls'] == 1 and coverage['status'] == 'complete'
    assert path.read_bytes() == original


def test_journal_total_counter_union_is_not_limited_to_one_sample(tmp_path):
    from taskplane import flow_journal as journal
    rows = [dict(kind='start', run='r', session='root')]
    for i in range(40):
        rows += [dict(kind='hook', run='r', session=str(i), usage={str(i): n}) for n in (1, 2)]
    journal_fixture(tmp_path, rows)
    result, coverage, _ = journal_read(tmp_path)
    assert result['tokens'] == {str(i): 1 for i in range(40)}
    assert coverage['status'] == 'complete'
    assert journal_read(tmp_path)[0]['tokens'] == result['tokens']


def journal_structural_fixture(tmp_path, rows):
    """Real UTF-8 source bytes, including values near the admission boundary."""
    from taskplane import flow_journal as journal
    path = journal_fixture(tmp_path, [])
    records = [json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode() for row in rows]
    assert all(len(record) <= journal.RECORD_BYTES for record in records)
    path.write_bytes(b'\n'.join(records) + b'\n')
    return path


def assert_journal_storage_closed(view):
    from taskplane import flow_journal as journal
    for table, shape in [('events', 'event'), ('totals', 'totals'), ('samples', 'samples'), ('parts', 'part')]:
        for (body,) in view.db.execute(f'SELECT body FROM {table} WHERE g=?', (view.generation,)):
            assert len(body.encode()) <= journal.RECORD_BYTES
            journal._decode(body, shape)
    for family, body in view.db.execute('SELECT family,body FROM fields WHERE g=?', (view.generation,)):
        journal._decode(body, family)
    for session, category, name, body in view.db.execute(
            'SELECT session,category,name,body FROM counters WHERE g=?', (view.generation,)):
        view._counter_name(category, name)
        journal._decode(body, 'counter' if session else 'counter-total')
    assert view.budget.reducer_bytes <= journal.REDUCER_BYTES
    assert view.budget.reducer_rows <= journal.REDUCER_ROWS
    assert view.budget.output_bytes <= journal.OUTPUT_BYTES
    assert view.budget.query_rows <= journal.QUERY_ROWS
    assert view.budget.query_bytes <= journal.QUERY_BYTES
    assert view.budget.source_bytes <= journal.SOURCE_BYTES


@pytest.mark.parametrize('name', ['x' * 524270, 'x' * 524271, 'x' * 600000,
                                 'é' * 300000, '😀' * 150000], ids=['old-boundary', 'old-overflow', '600k', 'unicode', 'astral'])
def test_journal_structural_sample_closure_restart_dedup(tmp_path, monkeypatch, name):
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root')
    first = dict(kind='hook', run='r', session='worker', event='PreToolUse', call_id='one', usage={name: 10})
    second = {**first, 'call_id': 'two', 'usage': {name: 20}}
    rows = [start, first, second, {**second, 'usage': {name: 999}},
            dict(kind='hook', run='r', session='later', event='PreToolUse', call_id='three')]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    monkeypatch.setattr(journal, 'RECORDS', 1)
    offsets = []
    for _ in rows:
        with journal.action_budget() as budget:
            view = journal.projection(tmp_path)
            offsets.append(view.coverage()['offset'])
            assert budget.records == 1
            assert_journal_storage_closed(view)
            assert view.coverage()['errors'] == []
    assert offsets == sorted(set(offsets)) and offsets[-1] == len(original)
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        summary = view.summary(start)
        assert summary['tool_calls'] == 3 and summary['tokens'] == {name: 10}
        assert summary['journal_coverage']['tokens_complete']
        assert budget.records == 0
    script = '''import json,sys
from taskplane import flow_journal as j
with j.action_budget() as b:
 p=j.projection(sys.argv[1]); t=p.total('r')
 print(json.dumps([t['tools'],list(t['tokens'].values()),p.coverage(),b.records]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tools, values, coverage, records = json.loads(result.stdout)
    assert (tools, values, records) == (3, [10], 0)
    assert coverage['status'] == 'complete'
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))
    assert path.read_bytes() == original


@pytest.mark.parametrize('sessions', [12, 26])
def test_journal_structural_counter_union_exact_storage_and_bounded_pages(tmp_path, monkeypatch, sessions):
    from taskplane import flow_journal as journal
    # Retain the small-budget partial-summary regression as defaults grow.
    monkeypatch.setattr(journal, 'OUTPUT_BYTES', 2 * 1024 * 1024)
    rows = [dict(kind='start', run='r', session='root')]
    expected = {}
    for i in range(sessions):
        name = str(i) + 'x' * 90000
        expected[name] = i + 1
        rows += [dict(kind='hook', run='r', session=str(i), call_id=str(n), event='PreToolUse',
                      usage={name: n}) for n in (10, 11 + i)]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    previous = -1
    for _ in range(20):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            coverage = view.coverage()
            assert coverage['offset'] > previous
            previous = coverage['offset']
            assert_journal_storage_closed(view)
            if coverage['caught_up']:
                break
    assert coverage['status'] == 'complete'
    with journal.action_budget():
        view = journal.projection(tmp_path)
        result = view.summary(rows[0])
        assert result['tool_calls'] == sessions * 2
        if sessions == 12:
            assert result['tokens'] == expected
        else:
            assert result['tokens'] is None
            assert not result['journal_coverage']['tokens_complete']
            assert result['journal_coverage']['details_partial']
        assert_journal_storage_closed(view)
    actual, cursor = {}, ''
    for _ in range(20):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            page = view.counter_page('r', after=cursor, limit=4)
            actual.update(page['tokens'])
            assert_journal_storage_closed(view)
            if page['next_category'] is None:
                assert page['complete']
                break
            assert page['next_category'] > cursor
            cursor = page['next_category']
    assert actual == expected
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_structural_metadata_merges_and_compact_totals(tmp_path):
    from taskplane import flow_journal as journal
    phase, note, session, transcript = 'p' * 600000, 'n' * 600000, 's' * 600000, 't' * 600000
    start = dict(kind='start', run='r', session='root')
    rows = [start, dict(kind='progress', run='r', phase=phase),
            dict(kind='hook', run='r', session='root', child='child', agent_transcript_path=transcript),
            dict(kind='hook', run='r', session=session, child='child'),
            dict(kind='finish', run='r', note=note)]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    for _ in range(10):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            assert_journal_storage_closed(view)
            if view.coverage()['caught_up']:
                break
    with journal.action_budget():
        view = journal.projection(tmp_path)
        total = view.total('r')
        assert total['raw'] == 5 and total['first_phase'] == total['last_phase'] == phase
        assert total['outcome'] == note and total['_metadata_available']
    with journal.action_budget():
        view = journal.projection(tmp_path)
        children, cursor = view.fields('r', 'child')
        assert cursor is None and len(children) == 1
        assert children[0][2]['session'] == session
        assert children[0][2]['agent_transcript_path'] == transcript
        assert view.coverage()['status'] == 'complete'
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        budget.output_bytes = journal.OUTPUT_BYTES - 1000
        total = view.total('r')
        assert total['raw'] == 5 and total['closed']
        assert not total['_metadata_available'] and view.detail_partial
        assert view.db is not None
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


@pytest.mark.parametrize('delta', [-1, 0])
def test_journal_structural_utf8_source_record_boundary(tmp_path, delta):
    from taskplane import flow_journal as journal
    row = dict(kind='hook', run='r', session='root', usage={'': 10})
    overhead = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode())
    name_bytes = journal.RECORD_BYTES + delta - overhead
    name = '😀' * (name_bytes // 4) + 'x' * (name_bytes % 4)
    row['usage'] = {name: 10}
    path = journal_structural_fixture(tmp_path, [row])
    original = path.read_bytes()
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.coverage()['status'] == 'complete'
        assert_journal_storage_closed(view)
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.total('r')['tokens'] == {name: 0}
    assert path.read_bytes() == original


def test_journal_structural_schema_upgrade_is_bounded_and_nonquarantining(tmp_path, monkeypatch):
    import sqlite3
    from taskplane import flow_journal as journal
    rows = [dict(kind='start', run='r', session='root', usage={'n': 1}),
            dict(kind='hook', run='r', session='root', usage={'n': 5}, call_id='one')]
    path = journal_structural_fixture(tmp_path, rows)
    journal_read(tmp_path)
    original = path.read_bytes()
    location = path.parent / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        meta = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        old_generation = meta['generation']
        meta['schema'] = journal.SCHEMA - 1
        db.execute('UPDATE meta SET body=?', (json.dumps(meta),))
        db.execute("UPDATE samples SET body='oversized legacy schema'")
    monkeypatch.setattr(journal, 'RECORDS', 1)
    for _ in rows:
        with journal.action_budget() as budget:
            view = journal.projection(tmp_path)
            assert budget.records == 1 and view.generation != old_generation
            assert view.coverage()['errors'] == []
            assert_journal_storage_closed(view)
    assert journal_read(tmp_path)[0]['tokens'] == {'n': 4}
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


@pytest.mark.parametrize('damage', ['sample-counter', 'total-counter', 'counter-name', 'child-part', 'total-part'])
def test_journal_structural_normalized_corruption_rebuilds(tmp_path, damage):
    import sqlite3
    from taskplane import flow_journal as journal
    start = dict(kind='start', run='r', session='root', usage={'n': 10})
    hook = dict(kind='hook', run='r', session='root', child='child', agent_transcript_path='path',
                event='PreToolUse', call_id='one', usage={'n': 20})
    path = journal_structural_fixture(tmp_path, [start, hook])
    journal_read(tmp_path)
    location = path.parent / journal.CACHE
    with closing(sqlite3.connect(location)) as db, db:
        before = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
        if damage == 'sample-counter':
            db.execute("UPDATE counters SET body='[]' WHERE session!=''")
        elif damage == 'total-counter':
            db.execute("UPDATE counters SET body='-1' WHERE session=''")
        elif damage == 'counter-name':
            db.execute("UPDATE counters SET name='\"foreign\"'")
        elif damage == 'child-part':
            db.execute("UPDATE parts SET body='{broken' WHERE family='child'")
        else:
            db.execute("UPDATE parts SET body='{broken' WHERE family='total'")
    if damage in ('sample-counter', 'counter-name'):
        with path.open('a') as stream:
            stream.write(json.dumps({**hook, 'call_id': 'two', 'usage': {'n': 30}}) + '\n')
    original = path.read_bytes()
    with journal.action_budget():
        view = journal.projection(tmp_path)
        if damage == 'child-part':
            assert view.fields('r', 'child')[0] == []
        else:
            view.summary(start)
        assert view.coverage()['status'] == 'unavailable'
        assert view.coverage()['offset'] == before['offset']
        assert 'invalid_record' not in view.coverage()['errors']
    result, coverage, _ = journal_read(tmp_path)
    assert coverage['status'] == 'complete'
    assert result['tokens'] == {'n': 20 if damage in ('sample-counter', 'counter-name') else 10}
    assert path.read_bytes() == original
    assert len(list(path.parent.glob(journal.CACHE + '.corrupt-*'))) == 1


def test_journal_structural_byte_deferral_commits_prefix_and_retries_record(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'REDUCER_BYTES', 25000)
    key = 'k' * 4000
    first = dict(kind='hook', run='r', session='root', call_id='one', event='PreToolUse', usage={key: 10})
    second = {**first, 'call_id': 'two', 'usage': {key: 20}}
    path = journal_structural_fixture(tmp_path, [first, second, second])
    original = path.read_bytes()
    previous = 0
    for _ in range(5):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            assert_journal_storage_closed(view)
            coverage = view.coverage()
            assert coverage['offset'] > previous
            previous = coverage['offset']
            assert coverage['errors'] == []
            if coverage['caught_up']:
                break
    assert coverage['status'] == 'complete'
    with journal.action_budget():
        result = journal.projection(tmp_path).summary({'run': 'r'})
    assert result['tool_calls'] == 2 and result['tokens'] == {key: 10}
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_structural_unsupported_work_is_a_stable_source_gap(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    monkeypatch.setattr(journal, 'REDUCER_BYTES', 5000)
    huge = dict(kind='hook', run='r', session='root', call_id='huge', event='PreToolUse', usage={'k' * 4000: 10})
    later = dict(kind='hook', run='r', session='later', call_id='later', event='PreToolUse')
    path = journal_structural_fixture(tmp_path, [huge, later])
    original = path.read_bytes()
    for _ in range(3):
        result, coverage, _ = journal_read(tmp_path)
        if coverage['caught_up']:
            break
    assert coverage['status'] == 'partial' and coverage['caught_up']
    assert coverage['errors'] == ['source_limit']
    with journal.action_budget():
        result = journal.projection(tmp_path).summary({'run': 'r'})
    assert result['tool_calls'] == 1
    assert journal_read(tmp_path)[2]['records'] == 0
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_structural_action_write_invariant_is_not_cache_corruption(tmp_path):
    import sqlite3
    from taskplane import flow_journal as journal
    row = dict(kind='hook', run='r', session='root', call_id='one', action='a', event='PreToolUse')
    path = journal_structural_fixture(tmp_path, [row])
    journal_read(tmp_path)
    with closing(sqlite3.connect(path.parent / journal.CACHE)) as db, db:
        db.execute('UPDATE actions SET n=?', ('9' * 64,))
        before = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
    with path.open('a') as stream:
        stream.write(json.dumps({**row, 'call_id': 'two'}) + '\n')
    original = path.read_bytes()
    with journal.action_budget():
        view = journal.projection(tmp_path)
        assert view.coverage()['errors'] == ['derived_representation:action']
        assert view.coverage()['offset'] == before['offset']
        assert view.db.execute('SELECT count(*) FROM seen').fetchone()[0] == 1
        assert view.db.execute('SELECT n FROM actions').fetchone()[0] == '9' * 64
        assert not view.db.in_transaction
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_structural_query_quota_preserves_compact_control_and_pagination(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    rows = [dict(kind='start', run='r', session='root')]
    for i in range(80):
        rows += [dict(kind='hook', run='r', session=str(i), usage={str(i): n}, at='2026-01-01')
                 for n in (1, 2)]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    for _ in range(10):
        if journal_read(tmp_path)[1]['caught_up']:
            break
    monkeypatch.setattr(journal, 'QUERY_ROWS', 64)
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        total = view.total('r')
        assert total['raw'] == 161 and total['last_at'] == '2026-01-01'
        assert not total['_tokens_available'] and total['tokens'] == {}
        assert budget.query_rows == 64
    actual, cursor = {}, ''
    for _ in range(6):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            page = view.counter_page('r', after=cursor, limit=16)
            actual.update(page['tokens'])
            if page['next_category'] is None:
                break
            cursor = page['next_category']
    assert actual == {str(i): 1 for i in range(80)}
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        budget.query_bytes = journal.QUERY_BYTES - 1
        assert view.counter_page('r')['complete'] is False
        assert budget.query_bytes <= journal.QUERY_BYTES
        assert view.db is not None
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_expanded_record_budget_progress_restart_and_exact_dedup(tmp_path):
    from taskplane import flow_journal as journal
    assert (journal.RECORD_BYTES, journal.SOURCE_BYTES, journal.QUERY_BYTES,
            journal.REDUCER_BYTES) == tuple(n * 1024 * 1024 for n in (8, 16, 64, 128))
    assert journal.OUTPUT_BYTES == journal.CHILD_ITEM_BYTES + (journal.FIELD_PAGE_LIMIT + 1) * journal.CHILD_FIELD_BYTES
    assert (journal.RECORDS, journal.REDUCER_ROWS, journal.QUERY_ROWS,
            journal.VM_STEPS) == (1024, 4096, 4096, 4_000_000)
    row = dict(kind='hook', run='r', session='root', event='PreToolUse', call_id='one', usage={'': 10})
    overhead = len(json.dumps(row, separators=(',', ':')).encode())
    name = 'x' * (journal.RECORD_BYTES - overhead)
    row['usage'] = {name: 10}
    second = {**row, 'call_id': 'two', 'usage': {name: 20}}
    duplicate = {**second, 'usage': {'ignored': 999}}
    later = dict(kind='hook', run='r', session='later', event='PreToolUse', call_id='three')
    path = journal_structural_fixture(tmp_path, [row, second, duplicate, later])
    original = path.read_bytes()
    assert len(json.dumps(row, separators=(',', ':')).encode()) == journal.RECORD_BYTES
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        first_offset = view.coverage()['offset']
        assert first_offset == journal.RECORD_BYTES + 1
        assert not view.coverage()['caught_up'] and not view.coverage()['errors']
        assert_journal_storage_closed(view)
        assert budget.source_bytes <= journal.SOURCE_BYTES
    # A fresh process must ingest the second maximum-sized sample, not stall or
    # rebuild its own normalized counter representation as corrupt state.
    script = '''import json,sys
from taskplane import flow_journal as j
with j.action_budget() as b:
 p=j.projection(sys.argv[1]); t=p.total('r')
 print(json.dumps([t['tools'],list(t['tokens'].values()),t['_tokens_available'],p.coverage(),vars(b)|{'projections':None}]))
'''
    resumed = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=ROOT,
                             capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    tools, values, exact, coverage, costs = json.loads(resumed.stdout)
    assert (tools, values, exact) == (3, [10], True)
    assert coverage['status'] == 'complete' and coverage['offset'] == len(original)
    assert costs['records'] == 3 and costs['source_bytes'] <= journal.SOURCE_BYTES
    assert costs['reducer_bytes'] <= journal.REDUCER_BYTES
    assert costs['query_bytes'] <= journal.QUERY_BYTES and costs['output_bytes'] <= journal.OUTPUT_BYTES
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        assert view.total('r')['tokens'] == {name: 10}
        assert budget.records == 0 and budget.source_bytes <= 2 * journal.ANCHOR_BYTES
        assert_journal_storage_closed(view)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_expanded_record_overflow_is_explicit_and_later_records_progress(tmp_path):
    from taskplane import flow_journal as journal
    row = dict(kind='hook', run='r', session='root', event='PreToolUse', call_id='too-large', note='')
    overhead = len(json.dumps(row, separators=(',', ':')).encode())
    row['note'] = 'x' * (journal.RECORD_BYTES + 1 - overhead)
    encoded = json.dumps(row, separators=(',', ':')).encode()
    assert len(encoded) == journal.RECORD_BYTES + 1
    path = journal_fixture(tmp_path, [])
    good = dict(kind='hook', run='r', session='root', event='PreToolUse', call_id='valid')
    original = encoded + b'\n' + json.dumps(good).encode() + b'\n'
    path.write_bytes(original)
    with journal.action_budget():
        view = journal.projection(tmp_path)
        coverage = view.coverage()
        assert coverage['caught_up'] and coverage['status'] == 'partial'
        assert coverage['errors'] == ['oversized_record']
        assert view.total('r')['tools'] == 1
        assert_journal_storage_closed(view)
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        assert view.total('r')['tools'] == 1 and budget.records == 0
        assert view.coverage()['errors'] == ['oversized_record']
    assert path.read_bytes() == original
    assert not list(path.parent.glob(journal.CACHE + '.corrupt-*'))


def test_journal_expanded_details_exceed_old_cap_and_continue_within_shared_budget(tmp_path, monkeypatch):
    from taskplane import flow_journal as journal
    # Preserve the original 16 MiB pagination boundary as defaults grow.
    monkeypatch.setattr(journal, 'OUTPUT_BYTES', 16 * 1024 * 1024)
    rows = [dict(kind='usage_boundary', run='r', note=str(i) + 'x' * (6 * 1024 * 1024))
            for i in range(3)]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    offsets = []
    for _ in range(3):
        with journal.action_budget():
            view = journal.projection(tmp_path)
            coverage = view.coverage()
            offsets.append(coverage['offset'])
            assert_journal_storage_closed(view)
            if coverage['caught_up']:
                break
    assert offsets == sorted(set(offsets)) and offsets[-1] == len(original)
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        page = view.page('r')
        assert page['rows'] == rows[:2] and not page['complete']
        assert page['next_offset'] is not None
        assert 2 * 1024 * 1024 < budget.output_bytes <= journal.OUTPUT_BYTES
        with journal.action_budget() as nested:
            assert nested is budget
            assert view.page('r', after=page['next_offset'])['rows'] == []
        assert_journal_storage_closed(view)
    with journal.action_budget() as budget:
        view = journal.projection(tmp_path)
        final = view.page('r', after=page['next_offset'])
        assert final['rows'] == rows[2:] and final['complete']
        assert final['next_offset'] is None
        assert budget.records == 0 and budget.source_bytes <= 2 * journal.ANCHOR_BYTES
        assert_journal_storage_closed(view)
    assert path.read_bytes() == original


def journal_query_fixture(tmp_path):
    rows = [dict(kind='start', run='r', session='root')]
    rows += [dict(kind='hook', run='r', session='root', event='PreToolUse',
                  call_id=str(i), usage={'a': i, 'b': i}, artifacts={str(i): 'x' * 100})
             for i in range(3)]
    journal_structural_fixture(tmp_path, rows)
    return rows


@pytest.mark.parametrize('api', ['fields', 'events', 'counters'])
@pytest.mark.parametrize('quota', ['query_rows', 'query_bytes', 'output_bytes'])
@pytest.mark.parametrize('prefix', [False, True])
def test_journal_local_exhaustion_retains_exact_position(tmp_path, monkeypatch, api, quota, prefix):
    from taskplane import flow_journal as j
    journal_query_fixture(tmp_path)
    with j.action_budget():
        j.projection(tmp_path)
    attr, cap = {'query_rows': ('QUERY_ROWS', 0), 'query_bytes': ('QUERY_BYTES', j.QUERY_RESERVATION_BYTES - 1),
                 'output_bytes': ('OUTPUT_BYTES', 0)}[quota]
    with j.action_budget() as budget:
        view = j.projection(tmp_path)
        # One delivered row then inability to fetch a lookahead is a local deferral.
        if prefix:
            if api == 'fields':
                raw = view.db.execute("SELECT pos,key,body,rank FROM fields WHERE family='artifact' ORDER BY pos,rank,key LIMIT 1").fetchone()
            elif api == 'events':
                raw = view.db.execute('SELECT pos,body FROM events ORDER BY pos LIMIT 1').fetchone()
            else:
                raw = view.db.execute("SELECT category,name,body FROM counters WHERE session='' ORDER BY category LIMIT 1").fetchone()
            size = sum(len(v.encode()) if isinstance(v, str) else 8 for v in raw)
            cap = 1 if quota == 'query_rows' else j.QUERY_RESERVATION_BYTES + size - 1 if quota == 'query_bytes' else size
        monkeypatch.setattr(j, attr, cap)
        if api == 'fields':
            page = view.fields_page('r', 'artifact'); values = page['items']; cursor = page['next_position']; incoming = (-1, -1, '')
        elif api == 'events':
            page = view.page('r'); values = page['rows']; cursor = page['next_offset']; incoming = -1
        else:
            page = view.counter_page('r'); values = page['tokens']; cursor = page['next_category']; incoming = ''
        assert len(values) == int(prefix)
        assert not page['query']['complete'] and cursor is not None
        assert (cursor != incoming) == prefix
        assert getattr(budget, quota) <= cap
        assert page['continuation'] == view.coverage()['continuations'][page['query_identity']]


def test_journal_composed_queries_complete_independently_and_clear_only_matching(tmp_path, monkeypatch):
    from taskplane import flow_journal as j
    journal_query_fixture(tmp_path)
    with j.action_budget():
        view = j.projection(tmp_path)
        counter = view.counter_page('r', limit=1)
        foreign = view.page('other', after=0, limit=1)
        assert foreign['complete']
        # The consumed allowance forces fields to defer after one whole item.
        monkeypatch.setattr(j, 'OUTPUT_BYTES', view.budget.output_bytes + 120)
        first = view.fields_page('r', 'artifact')
        assert len(first['items']) == 1 and first['continuation']
        assert counter['query_identity'] in view.continuations
    all_items = list(first['items'])
    continuation = json.loads(json.dumps(first['continuation']))
    monkeypatch.setattr(j, 'OUTPUT_BYTES', 10000)
    for _ in range(5):
        with j.action_budget():
            view = j.projection(tmp_path)
            page = view.fields_page('r', 'artifact', limit=1, continuation=continuation)
            all_items += page['items']
            continuation = json.loads(json.dumps(page['continuation']))
            if page['query']['complete']:
                assert page['query_identity'] not in view.continuations
                break
    assert [key for _, key, _ in all_items] == ['0', '1', '2']
    with j.action_budget():
        view = j.projection(tmp_path)
        first = view.counter_page('r', limit=1)
        other = view.page('r', limit=1, kind='hook')
        end = view.counter_page('r', limit=1, continuation=first['continuation'])
        assert end['complete'] and end['continuation'] is None
        assert first['query_identity'] not in view.continuations
        assert other['query_identity'] in view.continuations
        events = view.page('r', kind='start')
        assert events['complete'] and len(events['rows']) == 1
        assert view.detail_partial  # Sticky action coverage remains independent.


@pytest.mark.parametrize('api', ['fields', 'events', 'counters'])
def test_journal_bound_continuation_json_restart_and_identity(tmp_path, api):
    from taskplane import flow_journal as j
    rows = journal_query_fixture(tmp_path)
    def query(view, continuation=None, run='r', variant=False):
        if api == 'fields':
            return view.fields_page(run, 'captured' if variant else 'artifact', limit=1, continuation=continuation)
        if api == 'events':
            return view.page(run, kind='hook' if variant else None, limit=1, continuation=continuation)
        return view.counter_page(run, limit=1, continuation=continuation)
    with j.action_budget():
        view = j.projection(tmp_path)
        first = query(view)
        immediate = query(view, first['continuation'])
        assert immediate['query']['reason'] != 'cursor_mismatch'
        bound = json.loads(json.dumps(first['continuation']))
    script = '''import json,sys
from taskplane import flow_journal as j
with j.action_budget():
 p=j.projection(sys.argv[1]); c=json.loads(sys.argv[3]); api=sys.argv[2]
 result=p.fields_page('r','artifact',limit=1,continuation=c) if api=='fields' else p.page('r',limit=1,continuation=c) if api=='events' else p.counter_page('r',limit=1,continuation=c)
 print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), api, json.dumps(bound)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    resumed = json.loads(result.stdout)
    assert resumed['query']['reason'] != 'cursor_mismatch'
    assert resumed.get('items', resumed.get('rows', resumed.get('tokens'))) != first.get('items', first.get('rows', first.get('tokens')))
    with j.action_budget():
        view = j.projection(tmp_path)
        assert query(view, bound, run='other')['query']['reason'] == 'cursor_mismatch'
        if api != 'counters':
            assert query(view, bound, variant=True)['query']['reason'] == 'cursor_mismatch'
    path = tmp_path / '.taskplane' / 'flow-events.jsonl'
    path.rename(path.with_suffix('.old'))
    journal_structural_fixture(tmp_path, rows)
    with j.action_budget():
        view = j.projection(tmp_path)
        stale = query(view, bound)
        assert stale['query']['reason'] == 'cursor_mismatch'
        assert stale['continuation']['identity'] != bound['identity']
        # Compatibility primitives are unbound positions, before and after transport,
        # while the bound continuation above rejects the stale generation.
        assert 'identity' not in json.dumps(bound['position'])


@pytest.mark.parametrize('api', ['fields', 'events', 'counters'])
@pytest.mark.parametrize('transport', ['primitive', 'json_continuation'])
def test_journal_cursor_interfaces_drain_without_loss_or_duplicates(tmp_path, monkeypatch, api, transport):
    from taskplane import flow_journal as j
    journal_query_fixture(tmp_path)
    value_key, cursor_key, cursor_type = {
        'fields': ('items', 'next_position', tuple),
        'events': ('rows', 'next_offset', int),
        'counters': ('tokens', 'next_category', str),
    }[api]

    def query(view, **kwargs):
        if api == 'fields':
            return view.fields_page('r', 'artifact', **kwargs)
        return (view.page if api == 'events' else view.counter_page)('r', **kwargs)

    with j.action_budget():
        expected = query(j.projection(tmp_path))[value_key]
    actual, kwargs = [], {}
    for _ in range(10):
        with j.action_budget():
            view = j.projection(tmp_path)
            # Zero progress must preserve an initial or previously delivered
            # position through both public interfaces, without consuming data.
            with monkeypatch.context() as quota:
                quota.setattr(j, 'QUERY_ROWS', 0)
                deferred = query(view, limit=1, **kwargs)
            assert not deferred[value_key] and deferred['query']['reason'] == 'row_budget'
            cursor = deferred[cursor_key]
            assert type(cursor) is cursor_type
            kwargs = ({'after': cursor} if transport == 'primitive' else
                      {'continuation': json.loads(json.dumps(deferred['continuation']))})
            page = query(view, limit=1, **kwargs)
            values = page[value_key]
            actual.extend(values.items() if api == 'counters' else values)
            if page['query']['complete']:
                assert page[cursor_key] is None and page['continuation'] is None
                break
            cursor = page[cursor_key]
            assert type(cursor) is cursor_type
            kwargs = ({'after': cursor} if transport == 'primitive' else
                      {'continuation': json.loads(json.dumps(page['continuation']))})
    else:
        pytest.fail('Fresh budgets did not drain the finite query')
    assert actual == (list(expected.items()) if api == 'counters' else expected)


def test_journal_exact_full_empty_terminal_and_source_backlog(tmp_path, monkeypatch):
    from taskplane import flow_journal as j
    journal_query_fixture(tmp_path)
    monkeypatch.setattr(j, 'RECORDS', 1)
    with j.action_budget():
        view = j.projection(tmp_path)
        page = view.page('r', limit=1)
        assert len(page['rows']) == 1 and page['complete']
        assert not page['coverage']['caught_up']
        terminal = view.page('r', after=0, kind='absent', limit=1)
        assert terminal['complete'] and terminal['next_offset'] is None
    monkeypatch.setattr(j, 'RECORDS', 1024)
    with j.action_budget():
        view = j.projection(tmp_path)
        page = view.fields_page('r', 'artifact', limit=3)
        assert len(page['items']) == 3 and page['query']['complete']


def test_journal_child_temporary_hydration_deferral_advances_only_whole_items(tmp_path, monkeypatch):
    from taskplane import flow_journal as j
    rows = [dict(kind='hook', run='r', child=str(i), session='root', agent_transcript_path='x' * 1000) for i in range(3)]
    journal_structural_fixture(tmp_path, rows)
    with j.action_budget():
        j.projection(tmp_path)
    actual, continuation = [], None
    for i in range(5):
        with j.action_budget() as budget:
            view = j.projection(tmp_path)
            if i == 0:
                budget.output_bytes = j.OUTPUT_BYTES - 400
            page = view.fields_page('r', 'child', limit=1, continuation=continuation)
            if i == 0:
                assert page['items'] == [] and page['next_position'] == (-1, -1, '')
                assert page['query']['reason'] == 'hydration_budget'
            actual.extend(value['child'] for _, _, value in page['items'])
            continuation = page['continuation']
            if continuation is None:
                break
    assert actual == ['0', '1', '2']


def test_journal_five_part_child_default_envelope_from_real_source(tmp_path):
    from taskplane import flow_journal as j
    # Five retained parts, each introduced by admitted real source records.
    # Run/child are necessarily repeated to address the same logical item.
    run, child = 'r' * (1024 * 1024), 'c' * (1024 * 1024)
    rows = []
    expected = {'run': run, 'child': child}
    for name in ('kind', 'session', 'agent_transcript_path'):
        row = {'run': run, 'child': child, name: ''}
        value = name[0] * (j.RECORD_BYTES - len(j._json(row).encode()))
        row[name] = value
        assert len(j._json(row).encode()) == j.RECORD_BYTES
        expected[name] = value
        rows.append(row)
    path = journal_structural_fixture(tmp_path, rows)
    import hashlib
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    offsets = []
    for _ in range(5):
        with j.action_budget() as budget:
            view = j.projection(tmp_path)
            offsets.append(view.coverage()['offset'])
            assert budget.source_bytes <= j.SOURCE_BYTES
            if view.coverage()['caught_up']:
                break
    assert offsets == sorted(set(offsets)) and offsets[-1] == path.stat().st_size
    with j.action_budget() as budget:
        view = j.projection(tmp_path)
        page = view.fields_page(run, 'child', limit=j.FIELD_PAGE_LIMIT)
        assert [value for _, _, value in page['items']] == [expected]
        assert page['query']['complete'] and page['continuation'] is None
        assert 16 * 1024 * 1024 < budget.output_bytes <= j.OUTPUT_BYTES
        assert budget.query_bytes + j.QUERY_RESERVATION_BYTES <= j.QUERY_BYTES
        assert_journal_storage_closed(view)
        with j.action_budget() as nested:
            assert nested is budget
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert not list(path.parent.glob(j.CACHE + '.corrupt-*'))


def test_journal_maximum_stored_parts_with_manifest_lookahead_and_eof_reservation(tmp_path):
    from taskplane import flow_journal as j
    journal_fixture(tmp_path, [])
    # Exercise the conservative storage envelope, including five maximum part
    # cells, 1025 prefetched manifests and the terminal two-cell reservation.
    # Real records cannot simultaneously maximize every identity part; the test
    # above covers the stricter source constraints through refresh.
    with j.action_budget():
        view = j.projection(tmp_path)
        manifest = j._encode({'_parts': sorted(j.CHILD_PARTS)}, 'child')
        for i in range(j.FIELD_PAGE_LIMIT + 1):
            key = j._key(str(i))
            view.db.execute('INSERT INTO fields VALUES(?,?,?,?,?,?,?)',
                            (view.generation, j._key('r'), 'child', key, i, manifest, 0))
            for name in j.CHILD_PARTS:
                raw = j._encode('x' * (j.RECORD_BYTES - 2) if i == 0 else str(i), 'part')
                view.db.execute('INSERT INTO parts VALUES(?,?,?,?,?,?)',
                                (view.generation, j._key('r'), 'child', key, name, raw))
        view.db.commit()
    with j.action_budget() as budget:
        view = j.projection(tmp_path)
        page = view.fields_page('r', 'child', limit=j.FIELD_PAGE_LIMIT)
        assert page['items'] and page['items'][0][0] == 0
        assert len(page['items'][0][2]) == 5
        assert all(len(v) == j.RECORD_BYTES - 2 for v in page['items'][0][2].values())
        assert page['next_position'][0] >= 0
        assert budget.output_bytes <= j.OUTPUT_BYTES and budget.query_bytes <= j.QUERY_BYTES
        assert budget.query_rows <= j.QUERY_ROWS


@pytest.mark.parametrize('source', ['missing', 'empty', 'other', 'start', 'tools'])
def test_journal_known_run_report_uses_positive_observation(tmp_path, source):
    from taskplane import flow_journal as j
    c, _, state = controller(tmp_path)
    run = state['run']
    rows = [] if source in ('missing', 'empty') else [dict(kind='start', run='other' if source == 'other' else run, session='root')]
    if source == 'tools':
        rows.append(dict(kind='hook', run=run, session='root', event='PreToolUse', call_id='one'))
    if source != 'missing':
        journal_fixture(c.workspace, rows)
    result = flow.report(c.workspace, run, governor=c)
    assert result is not None and result['run'] == run
    observed = source in ('start', 'tools')
    assert result['tool_calls'] == (1 if source == 'tools' else 0 if observed else None)
    assert result['hook_outcomes']['coverage'] == ('identified' if observed else 'unknown')
    assert result['journal_coverage']['runs'][j._key(run)]['observed'] == observed
    assert result['journal_coverage']['source_present'] == (source != 'missing')
    if source in ('empty', 'other'):
        assert result['journal_coverage']['caught_up']
        assert not result['journal_coverage']['runs'][j._key(run)]['counts_complete']
    if not observed:
        assert result['tokens'] is None
    with j.action_budget():
        view = j.projection(c.workspace)
        view.usage_rows({'run': run, 'session': 'root'})
        assert view.usage_complete == observed


def test_journal_no_source_without_authoritative_run_is_absence(tmp_path):
    from taskplane import flow_journal as j
    class EmptyController:
        root = 'root'
        def report(self, run=None):
            return {'visits': []}
    assert flow.report(tmp_path, governor=EmptyController()) is None
    with j.action_budget():
        view = j.projection(tmp_path)
        assert view.start() is None
        assert view.coverage()['source_present'] is False
        assert not view.coverage()['caught_up']


@pytest.mark.parametrize('change', ['restore', 'rotate', 'truncate'])
def test_journal_cached_prefix_source_loss_and_generation_recovery(tmp_path, change):
    from taskplane import flow_journal as j
    start = dict(kind='start', run='r', session='root')
    rows = [start] + [dict(kind='hook', run='r', session='root', event='PreToolUse', call_id=str(i)) for i in range(2)]
    path = journal_structural_fixture(tmp_path, rows)
    original = path.read_bytes()
    with j.action_budget():
        view = j.projection(tmp_path)
        cursor = json.loads(json.dumps(view.page('r', limit=1)['continuation']))
        generation = view.generation
        assert view.summary(start)['tool_calls'] == 2
    saved = path.with_suffix('.saved')
    path.rename(saved)
    with j.action_budget():
        view = j.projection(tmp_path)
        summary = view.summary(start)
        assert summary['tool_calls'] == 2  # Retained measured prefix, visibly stale.
        assert not summary['journal_coverage']['run_counts_complete']
        assert summary['journal_coverage']['source_present'] is False
        assert 'source_missing' in summary['journal_coverage']['errors']
    if change == 'restore':
        saved.rename(path)
    elif change == 'rotate':
        journal_structural_fixture(tmp_path, [start])
    else:
        saved.rename(path)
        journal_structural_fixture(tmp_path, [start])
    with j.action_budget():
        view = j.projection(tmp_path)
        summary = view.summary(start)
        assert view.coverage()['caught_up'] and view.coverage()['source_present']
        assert summary['tool_calls'] == (2 if change == 'restore' else 0)
        resumed = view.page('r', continuation=cursor)
        if change == 'restore':
            assert view.generation == generation and len(resumed['rows']) == 2
            assert path.read_bytes() == original
        else:
            assert view.generation != generation
            assert resumed['query']['reason'] == 'cursor_mismatch'
            assert 'generation_changed' in view.coverage()['errors']


def test_journal_wrapper_order_preserves_usage_and_family_continuations(tmp_path):
    from taskplane import flow_journal as j
    start = dict(kind='start', run='r', session='root')
    rows = [start] + [dict(kind='progress', run='r', phase='build', note=str(i)) for i in range(260)]
    rows += [dict(kind='hook', run='r', session='root', child='worker', agent_transcript_path='transcript')]
    rows += [dict(kind='hook', run='r', artifacts={f'a{i}': i}) for i in range(260)]
    journal_structural_fixture(tmp_path, rows)
    for _ in range(8):
        with j.action_budget():
            if j.projection(tmp_path).coverage()['caught_up']:
                break
    with j.action_budget():
        view = j.projection(tmp_path)
        view.summary(start)
        milestone_id = view._query_identity('r', 'fields', 'milestone')
        assert milestone_id in view.continuations
        usage = view.usage_rows(start)
        assert view.usage_complete and any(row.get('child') == 'worker' for row in usage)
        artifacts, _ = view.attachments('r')
        assert len(artifacts) == 256
        artifact_id = view._query_identity('r', 'fields', 'artifact')
        assert artifact_id in view.continuations and milestone_id in view.continuations
        final = view.page('r', kind='final_snapshot')
        assert final['complete'] and not final['rows']
        continuation = json.loads(json.dumps(view.continuations[artifact_id]))
    with j.action_budget():
        view = j.projection(tmp_path)
        rest = view.fields_page('r', 'artifact', continuation=continuation)
        assert len(rest['items']) == 4 and rest['query']['complete']


def test_journal_zero_progress_noninitial_cursor_and_local_closure(tmp_path, monkeypatch):
    from taskplane import flow_journal as j
    rows = journal_query_fixture(tmp_path)
    path = tmp_path / '.taskplane' / 'flow-events.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(dict(kind='request', run='r', closure_id='closure')) + '\n')
    with j.action_budget():
        view = j.projection(tmp_path)
        field = view.fields_page('r', 'artifact', limit=1)
        event = view.page('r', limit=1)
        counter = view.counter_page('r', limit=1)
        assert view.has_closure('closure') is True
        assert view.active('root') == rows[0]
        monkeypatch.setattr(j, 'QUERY_ROWS', view.budget.query_rows)
        for first, again in (
            (field, view.fields_page('r', 'artifact', continuation=field['continuation'])),
            (event, view.page('r', continuation=event['continuation'])),
            (counter, view.counter_page('r', continuation=counter['continuation']))):
            assert again['continuation'] == first['continuation']
            assert again['query']['reason'] == 'row_budget'
        assert view.has_closure('closure') is None


def test_journal_same_family_runs_keep_separate_continuations(tmp_path):
    from taskplane import flow_journal as j
    rows = [dict(kind='hook', run=run, artifacts={str(i): i}) for run in ('r', 's') for i in range(2)]
    journal_structural_fixture(tmp_path, rows)
    with j.action_budget():
        view = j.projection(tmp_path)
        first = view.fields_page('r', 'artifact', limit=1)
        second = view.fields_page('s', 'artifact', limit=1)
        assert first['query_identity'] != second['query_identity']
        end = view.fields_page('r', 'artifact', continuation=first['continuation'])
        assert end['query']['complete']
        assert first['query_identity'] not in view.continuations
        assert view.continuations[second['query_identity']] == second['continuation']


def test_journal_continuation_metadata_has_fixed_identity_bound(tmp_path):
    from taskplane import flow_journal as j
    kind = 'k' * (1024 * 1024)
    journal_structural_fixture(tmp_path, [dict(kind=kind, run='r', note=i) for i in range(2)])
    with j.action_budget():
        view = j.projection(tmp_path)
        page = view.page('r', kind=kind, limit=1)
        assert len(page['query_identity']) == 64
        assert len(json.dumps(page['continuation'])) < 160
        assert view.page('r', kind=kind, continuation=page['continuation'])['complete']
