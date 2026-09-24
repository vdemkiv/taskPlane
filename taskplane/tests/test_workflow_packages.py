"""Source policy reachability and isolated archive parity/behavior evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import inspect
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile
import pytest

from scripts import package_plugin
from taskplane.tests.test_workflow_host import FixtureHost, controller, native
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_delivery import output
from taskplane.tests.test_native_workflow_cli import exercise_harness_entry, shipped_execution_prompts
from taskplane.tests.test_native_workflow_cli import exercise, exercise_state_repairs, exercise_counter_freshness, exercise_dashboard_publication_order
from taskplane.tests.test_workflow_local import task_observation_checkpoint, decision, decide, present
from taskplane.tests.test_workflow_autonomy import exercise_autonomous, exercise_nonconsent_cli
from taskplane.tests.test_native_workflow_cli import exercise_correction_cli
from taskplane.tests import test_harness_review_regressions as review_regressions

ROOT = Path(__file__).resolve().parents[2]


def test_source_instruction_contract():
    shared = ROOT/'skills/tp-go/references/shared-flow.md'
    # Every execution entry and worker can discover the same maintained policy.
    sources = [*ROOT.glob('skills/*/SKILL.md'), *ROOT.glob('agents/*.md')]
    for p in sources:
        text = p.read_text()
        links = re.findall(r'\]\(([^)]+\.md)\)', text)
        targets = [(p.parent/x).resolve() for x in links]
        assert targets and all(t.is_file() for t in targets), p
        assert shared in targets or ROOT/'skills/tp-go/SKILL.md' in targets, p
        assert 'human' in text.casefold(), p
    forbidden = ['orchestrator judges that evidence and moves on',
                 'observations of work, not gates', 'never decide\n  whether a tool call may run',
                 'No mandatory lens count, separate phase worker, contract activation']
    for p in sources + [ROOT/'README.md', ROOT/'docs/cli-reference.md', shared]:
        for phrase in forbidden:
            assert phrase not in p.read_text(), (p, phrase)
    before = {p: p.read_bytes() for p in ROOT.glob('lenses/*.md')}
    result = subprocess.run([sys.executable, 'lenses/_generate_lens_prompts.py'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert before == {p: p.read_bytes() for p in before}, 'Generated lens policy drift'
    for p in before:
        footer = p.read_text().split('## How this lens runs')[1]
        assert 'shared-flow.md' in footer and 'human' in footer and 'source component' in footer


@pytest.mark.taskplane_packages
@pytest.mark.parametrize("host", ["openai", "claude"])
def test_generated_archives_match_verified_source(tmp_path, request, host):
    supplied = request.config.getoption('--taskplane-archive-dir')
    fresh = package_plugin.package(host, tmp_path/'fresh')
    expected = Path(fresh['archive'])
    target = Path(supplied).resolve()/expected.name if supplied else expected
    assert target.is_file(), f'Missing final archive: {target}'
    assert target.read_bytes() == expected.read_bytes(), f'Archive differs from current source: {target}'
    sidecar = json.loads(target.with_suffix(target.suffix+'.json').read_text())
    assert sidecar['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    extracted = tmp_path/host
    with zipfile.ZipFile(target) as archive:
        assert set(sidecar['member_sha256']) == set(archive.namelist())
        for name in archive.namelist():
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            assert archive.read(name) == (ROOT/name).read_bytes(), name
            assert sidecar['member_sha256'][name] == hashlib.sha256(archive.read(name)).hexdigest()
        for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', archive.read('README.md').decode()):
            path = link.split('#', 1)[0]
            if path and not re.match(r'\w+://', path):
                assert path in archive.namelist(), f'Broken packaged README link: {path}'
        assert 'docs/assets/taskplane-cowork-flow.gif' in archive.namelist()
        assert 'docs/assets/taskplane-flow-source.html' in archive.namelist()
        assert 'taskplane/workflow_host.py' in archive.namelist()
        assert 'taskplane/worker_runtime.py' in archive.namelist()
        dispatch = archive.read('skills/tp-go/references/codex-native-dispatch.md').decode()
        assert 'operation prepare' in dispatch and 'minimum live acceptance test' in dispatch
        assert 'hooks/hooks.json' in archive.namelist()
        archive.extractall(extracted)
    # The ordinary shipped profile must work without the protected fixture.
    exercise((tmp_path/(host+'-native')).resolve(), 'claude' if host=='claude' else 'codex', root=extracted)
    exercise_autonomous((tmp_path/(host+'-autonomous')).resolve(),
                        'claude' if host=='claude' else 'codex', root=extracted)
    for entry in ('product','design','engineering'):
        exercise_harness_entry((tmp_path/(host+'-entry-'+entry)).resolve(),
                               'claude' if host=='claude' else 'codex',entry,root=extracted)
    for name,prompt,phase,standalone in shipped_execution_prompts():
        exercise_harness_entry((tmp_path/(host+'-shipped-'+name)).resolve(),
                               'claude' if host=='claude' else 'codex',phase,root=extracted,
                               prompt=prompt,standalone=standalone)
    exercise_nonconsent_cli((tmp_path/(host+'-nonconsent')).resolve(),
                            'claude' if host=='claude' else 'codex', root=extracted)
    exercise_counter_freshness((tmp_path/(host+'-counter-freshness')).resolve(),
                               'claude' if host=='claude' else 'codex', root=extracted)
    exercise_state_repairs((tmp_path/(host+'-state-repairs')).resolve(),
                           'claude' if host=='claude' else 'codex', root=extracted)
    exercise_correction_cli((tmp_path/(host+'-review-correction')).resolve(),
                            'claude' if host=='claude' else 'codex', root=extracted)
    # The child interpreter sees only the extracted runtime and the standard library.
    # Test-only adapter code is embedded here, never exported by either package.
    helpers = '\n\n'.join(inspect.getsource(f) for f in (FixtureHost, prepare, controller, native, output, decision, decide, present, task_observation_checkpoint, exercise_dashboard_publication_order))
    helpers += '\n\n' + '\n\n'.join(inspect.getsource(getattr(review_regressions, name)) for name in (
        'setup', 'submit', 'authorization', 'set_policy', 'refused', 'exercise_correction',
        'exercise_handles', 'exercise_child_lineage', 'exercise_routing'))
    script = '''import sys, json, io, shlex
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
from contextlib import redirect_stdout
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from taskplane import flow, workflow as w, workflow_host as h, workflow_local as local
from taskplane import workflow_approval as approval
assert Path(flow.__file__).resolve().is_relative_to(Path(sys.argv[1]))
''' + helpers + '''
temp = Path(sys.argv[2]); temp.mkdir()
c, host, initial = controller(temp)
assert not h.installed_adapter('codex' if sys.argv[3] == 'openai' else sys.argv[3]).capabilities()['human_origin']
def command(*args):
    global last_result
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = flow.main([*args, '--workspace', str(c.workspace)], governor=c)
    last_result = json.loads(stream.getvalue())
    return code
assert command('start') == 0
before = host.store.read_bytes()
for hook_command in ('screen', 'session-verify'):
    stream = io.StringIO()
    with patch('sys.stdin', io.StringIO('[]')), redirect_stdout(stream):
        assert flow.run_hook(command=hook_command, governor=c) == 0
    result = json.loads(stream.getvalue())
    if hook_command == 'screen':
        assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    else:
        assert result.get('systemMessage') and 'decision' not in result
assert host.store.read_bytes() == before
for i, phase in enumerate(w.PHASES):
    s = c.report()
    target = output(c, s)
    before = host.store.read_bytes()
    if phase == 'product':
        graph_path = c.workspace/'.taskplane/knowledge/graph.json'
        graph_bytes = graph_path.read_bytes()
        graph = json.loads(graph_bytes)
        graph['meta']['graph_scan_quality']['degraded'] = True
        graph_path.write_text(json.dumps(graph))
        assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 2
        assert host.store.read_bytes() == before
        graph_path.write_bytes(graph_bytes)
    if phase == 'plan':
        tasks_path = c.workspace/'tasks.json'
        tasks = json.loads(tasks_path.read_text())
        for task in tasks['tasks']:
            task['acceptance_criteria'] = task.pop('criteria')
        tasks_path.write_text(json.dumps(tasks))
        out = json.loads((c.workspace/target).read_text())
        out['task_dag'] = tasks['tasks']
        (c.workspace/target).write_text(json.dumps(out))
    if phase == 'build':
        source_path = c.workspace/'app.py'
        source_bytes = source_path.read_bytes()
        source_path.write_text('value = 999')
        assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 2
        assert last_result['reason'] == 'invalid_evidence' and 'stale' in last_result['detail']
        assert host.store.read_bytes() == before
        source_path.write_bytes(source_bytes)
        out_path = c.workspace/target
        out_bytes = out_path.read_bytes()
        out = json.loads(out_bytes)
        tasks_path = c.workspace/'tasks.json'
        task_bytes = tasks_path.read_bytes()
        tasks = json.loads(task_bytes)
        out['task_acceptance_map'] = {'AC1': ['UNAPPROVED']}
        out_path.write_text(json.dumps(out))
        assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 2
        assert host.store.read_bytes() == before
        tasks['tasks'][0]['id'] = 'UNAPPROVED'
        tasks_path.write_text(json.dumps(tasks))
        assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 2
        assert host.store.read_bytes() == before
        out_path.write_bytes(out_bytes)
        tasks = json.loads(task_bytes)
        tasks['tasks'][0].update(status='complete', elapsed_seconds=5)
        tasks_path.write_text(json.dumps(tasks))
    assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 0
    s = c.report()
    before = host.store.read_bytes()
    assert command('finish', '--expected-revision', str(s['revision'])) == 2
    assert host.store.read_bytes() == before
    key = native(host, s, key='package-human-'+phase)
    with patch.object(flow, 'append', side_effect=OSError('journal unavailable')):
        assert command('decide', '--native-event', key, '--expected-revision', str(s['revision'])) == 0
        assert last_result['workflow']['status'] == 'approved' and last_result['evidence_errors']
        assert command('decide', '--native-event', key, '--expected-revision', str(s['revision'])) == 0
        assert len(last_result['workflow']['decisions']) == i+1
    s = c.report()
    if phase != 'retro':
        assert command('advance', '--phase', w.PHASES[i+1], '--expected-revision', str(s['revision'])) == 0
assert command('finish', '--expected-revision', str(s['revision'])) == 0
assert c.report(initial['run'])['status'] == 'accepted'
assert len(c.report(initial['run'])['decisions']) == 7
# Standalone review cannot label a new delivery as repair to obtain Build writes.
standalone = temp/'standalone'; standalone.mkdir()
c, host, s = controller(standalone, {'entry': 'engineering', 'standalone': True})
target = output(c, s)
out = json.loads((c.workspace/target).read_text())
out['route_change'] = {'kind': 'repair'}
(c.workspace/target).write_text(json.dumps(out))
assert command('submit', '--output', target, '--tasks', 'tasks.json', '--expected-revision', str(s['revision'])) == 0
s = c.report()
before = host.store.read_bytes()
key = native(host, s, key='standalone-repair')
assert command('decide', '--native-event', key, '--expected-revision', str(s['revision'])) == 2
assert command('advance', '--phase', 'build', '--expected-revision', str(s['revision'])) == 2
try:
    c.guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, s['run'])
except w.Refusal:
    pass
else:
    raise AssertionError('Standalone review obtained a Build write grant')
assert host.store.read_bytes() == before
# Production entry refuses despite forged environment and command-shaped input.
import os
os.environ['TASKPLANE_TEST_APPROVAL'] = 'approved'
with redirect_stdout(io.StringIO()):
    assert flow.main(['start', '--workspace', str(c.workspace)]) == 2
# EV-F02: exercise the extracted native adapter with progress and normative edits.
task_ws = temp/'task-observations'; task_ws.mkdir()
c, task_state, task_relative = task_observation_checkpoint(task_ws, host='claude' if sys.argv[3]=='claude' else 'codex')
task_file=task_ws/task_relative
task_data=json.loads(task_file.read_text());task_data['tasks'][0]['status']='working';task_file.write_text(json.dumps(task_data))
assert not c.report().get('invalidation_pending')
task_data['tasks'][0]['owner']='different';task_file.write_text(json.dumps(task_data))
assert c.report().get('invalidation_pending')
legacy_ws = temp/'legacy-task-observations'; legacy_ws.mkdir()
c, task_state, task_relative = task_observation_checkpoint(legacy_ws, legacy=True, host='claude' if sys.argv[3]=='claude' else 'codex')
assert not c.report().get('invalidation_pending')
task_file=legacy_ws/task_relative;task_data=json.loads(task_file.read_text());task_data['tasks'][0]['status']='working';task_file.write_text(json.dumps(task_data))
assert c.report().get('invalidation_pending')
print('EV-F02 progress, normative and legacy packet regressions passed')
publication_ws = temp/'publication-order'; publication_ws.mkdir()
c, publication_state, _ = task_observation_checkpoint(publication_ws, host='claude' if sys.argv[3]=='claude' else 'codex')
for select in (False, True):
    exercise_dashboard_publication_order(c, publication_state, select=select)
print('ENG-F01 concurrent publication regressions passed')
print('seven accepted fixture checkpoints; production authority refused')
print('EV-F01 EV-F02 EV-F03 archive regressions passed')
print('EM-F01 EM-F02 EM-F03 EM-F04 archive regressions passed')
for exercise_review in (exercise_correction, exercise_handles, exercise_child_lineage):
    exercise_review(temp/exercise_review.__name__)
exercise_routing()
print('HR-01 HR-02 HR-03 HR-04 archive regressions passed')
'''
    harness = tmp_path/(host+'-harness.py')
    harness.write_text(script)
    result = subprocess.run([sys.executable, '-I', str(harness), str(extracted),
                             str(tmp_path/(host+'-behavior')), host],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'EV-F02 progress, normative and legacy packet regressions passed' in result.stdout
    assert 'ENG-F01 concurrent publication regressions passed' in result.stdout
    assert 'seven accepted fixture checkpoints; production authority refused' in result.stdout
    assert 'EV-F01 EV-F02 EV-F03 archive regressions passed' in result.stdout
    assert 'EM-F01 EM-F02 EM-F03 EM-F04 archive regressions passed' in result.stdout
    assert 'HR-01 HR-02 HR-03 HR-04 archive regressions passed' in result.stdout


def test_package_receipt_distinguishes_bytes_from_commit_and_unrelated_dirt(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()
    git('init')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Package fixture')
    (tmp_path/'member.txt').write_bytes(b'committed member')
    git('add', 'member.txt')
    git('commit', '-m', 'fixture')
    monkeypatch.setattr(package_plugin, 'ROOT', tmp_path)
    exact = package_plugin.source_identity({'member.txt': b'committed member'})
    assert exact['matches_source_commit'] is True
    assert exact['source_member_differences'] == []
    (tmp_path/'unrelated.txt').write_text('not packaged')
    dirty = package_plugin.source_identity({'member.txt': b'committed member'})
    assert dirty['source_dirty'] is True and dirty['matches_source_commit'] is True
    different = package_plugin.source_identity({'member.txt': b'candidate', 'new.txt': b'new'})
    assert different['source_commit'] == git('rev-parse', 'HEAD')
    assert different['matches_source_commit'] is False
    assert different['source_member_differences'] == ['member.txt', 'new.txt']


def test_package_receipt_without_git_has_unknown_commit_parity(tmp_path, monkeypatch):
    monkeypatch.setattr(package_plugin, 'ROOT', tmp_path)
    receipt = package_plugin.source_identity({'file': b'bytes'})
    assert receipt['source_commit'] is None
    assert receipt['source_dirty'] is None
    assert receipt['matches_source_commit'] is None
    assert receipt['source_member_differences'] is None
