"""Public regressions for the September Engineering findings and entry phases."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import depgraph, flow, native_session_meter as meter, workflow
from taskplane.tests.test_workflow_host import controller, native as human_event
from taskplane.tests.test_workflow_evidence import prepare
from contextlib import redirect_stdout
import io

ROOT = Path(__file__).resolve().parents[2]


def cli(ws, *args, root=ROOT):
    result = subprocess.run([sys.executable, str(root / "taskplane/tp.py"), *args],
                            cwd=ws, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def flow_call(controller, *arguments):
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = flow.main([*arguments, '--workspace', str(controller.workspace)], governor=controller)
    assert code == 0, stream.getvalue()
    return json.loads(stream.getvalue())


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
    # Exercise the selected optional writer directly. A valid workflow start
    # need not touch a dashboard, journal, ignore file or observation lock.
    with pytest.raises((ValueError, OSError, workflow.Refusal, flow.primitives.StateError)):
        if target == 'dashboard.html':
            flow.publish_dashboard(ws, c.report()['run'], governor=c)
        else:
            flow.append(ws, {'kind': 'start', 'run': 'fixture', 'session': 'root'})
    assert victim.read_bytes() == b"KEEP THESE BYTES"
    assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]


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
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_resumed_thread_measurement_uses_one_explicit_logical_baseline(tmp_path, monkeypatch):
    sessions = Path(os.environ['CODEX_HOME']) / 'sessions'
    sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    first, resumed = sessions / 'part1-root.jsonl', sessions / 'part2-root.jsonl'
    native(first, 100)
    native(resumed, 160, resumed=True, timestamp="2026-09-01T00:00:06Z")
    native(sessions / 'foreign-root.jsonl', 999, sid='unrelated')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    baseline = meter.read_logical_snapshot([first, resumed], 'root')
    assert baseline['usage']['total_tokens'] == 160
    flow.append(workspace, {'kind': 'start', 'run': 'measurement-fixture', 'session': 'root',
        'at': '2026-09-01T00:00:06Z', 'usage': baseline['usage'], 'usage_status': 'observed'})
    with resumed.open('a') as stream:
        stream.write(json.dumps({'type': 'token_usage_record', 'ordinal': 3,
            'timestamp': '2026-09-01T00:00:07Z', 'payload': {'thread_id': 'root',
            'thread_token_usage': {'input_tokens': 210, 'cached_input_tokens': 0,
                                  'output_tokens': 10, 'total_tokens': 220}}}) + '\n')
    report = flow.report(workspace)
    if sys.platform not in ('darwin', 'linux'):
        assert report['tokens'] is None
        assert not report['usage_refresh']['fully_discovered']
    else:
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
    started = flow_call(c, 'start', '--phase', entry, '--standalone')['workflow']
    flow_call(c, 'attach', '--run', started['run'], '--tasks', 'tasks.json',
              '--reviews', 'reviews.json', '--evidence', 'finding.md')
    view = flow.report(tmp_path, started['run'], governor=c)
    assert view['phase'] == entry and view['entry_phase'] == entry
    assert len(view['tasks']) == 1 and len(view['reviews']) == 1
    flow.publish_dashboard(tmp_path, started['run'], governor=c, supplied=view)
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
        product = flow_call(c, 'advance', '--phase', 'product', '--run', state['run'],
                            '--expected-revision', str(state['revision']))['workflow']
        assert product['run'] == started['run'] and workflow.current(product)['phase'] == 'product'
        next_view = flow.report(tmp_path, product['run'], governor=c)
        assert next_view['artifacts'] == view['artifacts']
        flow.publish_dashboard(tmp_path, product['run'], governor=c, supplied=next_view)
        assert 'Engineering → Product' in (tmp_path / flow.DASHBOARD).read_text()
