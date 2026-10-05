"""Source policy reachability and isolated archive parity/behavior evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import inspect
import io
import json
import os
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
from taskplane.tests.test_native_workflow_cli import CLAUDE_TRANSPORT_SUPPORTED, exercise_unsupported_claude_context
from taskplane.tests import test_harness_review_regressions as review_regressions

ROOT = Path(__file__).resolve().parents[2]


def workflow_candidate_checks(root, workspace, host):
    """Run inside one isolated extracted interpreter; this is not WFB-LIVE."""
    from contextlib import redirect_stdout
    from copy import deepcopy
    import hashlib
    import io
    import json
    from pathlib import Path
    import subprocess
    import sys
    from taskplane import flow, tp, workflow as w

    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    workspace.mkdir()

    def snapshot(path):
        return {p.relative_to(path).as_posix(): p.read_bytes() if p.is_file() else None
                for p in path.rglob('*')}

    def command(action, *args, code=0):
        stream = io.StringIO()
        with redirect_stdout(stream):
            actual = tp.main(['workflow', action, '--workspace', str(workspace), *args])
        result = json.loads(stream.getvalue())
        assert actual == code, (action, args, actual, result)
        return result

    def git(*args):
        return subprocess.run(['git', *args], cwd=workspace, check=True,
                              capture_output=True, text=True).stdout.strip()

    git('init')
    git('config', 'core.autocrlf', 'false')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Workflow package fixture')
    for name, body in {'main.py': 'answer = 41\n', 'dependency.py': 'limit = 42\n',
                       'test_main.py': 'assert True\n'}.items():
        (workspace/name).write_text(body)
    git('add', '.')
    git('commit', '-m', 'fixture base')
    base = git('rev-parse', 'HEAD')
    (workspace/'main.py').write_text('answer = 42\n')
    git('add', 'main.py')
    git('commit', '-m', 'fixture head')
    head = git('rev-parse', 'HEAD')
    before = snapshot(workspace)
    catalog = command('catalog')
    runtime = catalog['runtime']
    assert catalog['compatible'] and not catalog['blockers']
    assert runtime['runtime_root'] == str(root)
    assert runtime['interpreter']['path'] == str(Path(sys.executable).resolve())
    for name, member in runtime['modules'].items():
        assert Path(member['path']) == root/name
        assert member['sha256'] == hashlib.sha256((root/name).read_bytes()).hexdigest()
        assert len(member['loaded_code_sha256']) == 64
    assert {'taskplane/blueprint.py', 'taskplane/blueprint_catalog.py',
            'taskplane/blueprint_compile.py', 'taskplane/workflow_local.py'} <= set(runtime['modules'])
    for module_name, module in tuple(sys.modules.items()):
        if module_name == 'taskplane' or module_name.startswith('taskplane.'):
            if module.__file__ is not None:
                assert Path(module.__file__).resolve().is_relative_to(root), module_name
            else:
                assert {Path(p).resolve() for p in module.__path__} == {root/'taskplane'}, module_name
    assert snapshot(workspace) == before

    compiled_review = None
    for seed in ('change-risk-review', 'design-brief', 'feature-delivery'):
        definition = root/'workflows'/f'{seed}.workflow.json'
        data = json.loads(definition.read_text())
        values = {'request': 'Inspect this isolated package fixture.',
                  'source_files': ['main.py'], 'dependency_files': ['dependency.py'],
                  'test_files': ['test_main.py'], 'output_prefix': f'reports/{seed}',
                  'criteria': [{'id': 'C1', 'statement': 'Return scoped evidence.'}]}
        if seed == 'change-risk-review':
            values.update(base_ref=base, head_ref=head)
        if seed == 'feature-delivery':
            values['build_files'] = ['main.py', 'new.py', 'checks/history.json',
                                     'checks/attempt-1.log', 'checks/attempt-2.log']
        inputs = workspace/f'{seed}-inputs.json'
        inputs.write_text(json.dumps(values))
        before = snapshot(workspace)
        validated = command('validate', '--definition', str(definition))
        assert validated['status'] == 'valid' and 'runtime' not in validated
        incomplete = command('preview', '--definition', str(definition))
        assert incomplete['status'] == 'incomplete' and incomplete['unresolved_inputs']
        preview = command('preview', '--definition', str(definition), '--inputs', inputs.name)
        assert preview['status'] == 'ready' and preview['runnable']
        assert preview['effects']['workers_dispatched'] == 0
        assert preview['effects']['preview_writes'] == preview['effects']['external_actions'] == []
        assert preview['decisions']['execution_authorized'] is False
        assert preview['decisions']['approval_default'] == 'manual'
        assert set(preview['source_manifest']) == {'main.py', 'dependency.py', 'test_main.py'}
        assert snapshot(workspace) == before
        published = f"workflows/{seed}.{data['version']}.workflow.json"
        assert command('save', '--definition', str(definition), '--out', published)['status'] == 'created'
        saved = snapshot(workspace)
        assert command('save', '--definition', str(definition), '--out', published)['status'] == 'unchanged'
        assert snapshot(workspace) == saved
        out = f'.taskplane/bootstrap/workflow-{seed}'
        compiled = command('compile', '--definition', published, '--inputs', inputs.name, '--out', out)
        assert compiled['status'] == 'created' and compiled['package_path'] == out
        assert compiled['start_arguments'][:2] == ['flow', 'start']
        assert compiled['scope']['execution_contract'] == 'native-default/v1'
        expected_members = {'definition.json', 'bindings.json', 'capabilities.json', 'compilation.json',
                            'scope.json', 'tasks.json', 'preview.json', 'preview.md'}
        assert set(snapshot(workspace/out)) == expected_members
        pinned = json.loads((workspace/out/'capabilities.json').read_text())
        assert pinned['runtime']['runtime_root'] == str(root)
        after = snapshot(workspace)
        assert {name for name in after.keys() - saved.keys() if after[name] is not None} == {
            f'{out}/{name}' for name in expected_members}
        assert command('compile', '--definition', published, '--inputs', inputs.name,
                       '--out', out)['status'] == 'unchanged'
        assert snapshot(workspace) == after
        # Finite CLI destinations reject before creating any additional files.
        for destination in ('outside', '.taskplane/bootstrap/workflow-../escape'):
            refused = command('compile', '--definition', published, '--inputs', inputs.name,
                              '--out', destination, code=2)
            assert refused['diagnostics'][0]['code'] == 'invalid_package_path'
        assert snapshot(workspace) == after
        phases = compiled['scope']['paths']
        if seed == 'feature-delivery':
            assert set(compiled['compilation']['phase_files']) == set(w.PHASES)
            assert set(values['build_files']) <= set(phases['build'])
        else:
            assert all(not paths for phase, paths in phases.items() if phase != data['route']['phase'])
        if seed == 'change-risk-review':
            compiled_review = compiled
            tasks = {task['id']: task for task in compiled['preview']['tasks']['engineering']['tasks']}
            reviewers = [tasks['security-review'], tasks['quality-review']]
            assert {t['review_lens'] for t in reviewers} == {'security', 'code-quality'}
            assert all(t['execution'] == 'native_required' for t in reviewers)
            assert set(tasks['review-synthesis']['dependencies']) == {t['id'] for t in reviewers}

    assert {p.name for p in (workspace/'.taskplane').iterdir()} == {'bootstrap'}
    definition = root/'workflows/change-risk-review.workflow.json'
    inputs = workspace/'change-risk-review-inputs.json'
    # Static validation remains available when a selected runtime asset is missing.
    asset = root/'lenses/security.md'
    hidden = asset.with_suffix('.fixture-hidden')
    before = snapshot(workspace)
    asset.rename(hidden)
    try:
        assert command('validate', '--definition', str(definition))['status'] == 'valid'
        unavailable = command('catalog', code=2)
        assert any(d['code'] == 'capability_asset_unavailable' for d in unavailable['blockers'])
        assert command('preview', '--definition', str(definition), '--inputs', inputs.name,
                       code=2)['status'] == 'blocked'
        refused = command('compile', '--definition', str(definition), '--inputs', inputs.name,
                          '--out', '.taskplane/bootstrap/workflow-unavailable', code=2)
        assert refused['diagnostics'][0]['code'] == 'runtime_incompatible'
        assert snapshot(workspace) == before
    finally:
        hidden.rename(asset)
    compiled = compiled_review
    assert compiled is not None
    published = 'workflows/change-risk-review.0.1.0.workflow.json'
    altered = deepcopy(json.loads(definition.read_text()))
    altered['description'] += ' Changed version content.'
    (workspace/'altered.json').write_text(json.dumps(altered))
    before = snapshot(workspace)
    refused = command('save', '--definition', 'altered.json', '--out', published, code=2)
    assert refused['diagnostics'][0]['code'] == 'published_version_conflict'
    refused = command('compile', '--definition', 'altered.json', '--inputs', inputs.name,
                      '--out', compiled['package_path'], code=2)
    assert refused['diagnostics'][0]['code'] == 'package_integrity', refused
    assert snapshot(workspace) == before
    # Reuse the saved definition with a fresh namespace and different bound source.
    values = json.loads(inputs.read_text())
    values.update(source_files=['dependency.py'], dependency_files=['main.py'],
                  output_prefix='reports/review-second')
    (workspace/'second-inputs.json').write_text(json.dumps(values))
    second = command('compile', '--definition', published, '--inputs', 'second-inputs.json',
                     '--out', '.taskplane/bootstrap/workflow-review-second')
    assert second['package_digest'] != compiled['package_digest']
    assert set(second['scope']['paths']['engineering']).isdisjoint(compiled['scope']['paths']['engineering'])
    # Check names an existing fixture run; no real host, worker or checkpoint is claimed.
    before = snapshot(workspace)
    assert command('check', '--run', 'missing', code=2)['diagnostics'][0]['code'] == 'state_unavailable'
    assert snapshot(workspace) == before
    c = flow._controller(workspace, flow.session_id({}))
    request = {'scope': compiled['scope'], 'entry': 'engineering', 'standalone': True,
               'request_reference': 'fixture/package-workflow',
               'tasks': compiled['package_path'] + '/tasks.json'}
    state = c.start(request)
    assert c.adapter.name == ('codex' if host == 'openai' else 'claude')
    assert state['decisions'] == {} and not state.get('workers')
    assert c.start(request)['run'] == state['run']
    before = snapshot(workspace)
    checked = command('check', '--run', state['run'])
    assert checked['status'] == 'valid' and checked['run'] == state['run']
    assert snapshot(workspace) == before
    member = workspace/compiled['package_path']/'preview.md'
    original = member.read_bytes()
    member.write_bytes(original + b'changed fixture package\n')
    before = snapshot(workspace)
    blocked = command('check', '--run', state['run'], code=2)
    assert blocked['status'] == 'blocked' and blocked['diagnostics'][0]['code'] == 'package_integrity'
    assert snapshot(workspace) == before
    member.write_bytes(original)
    (workspace/'dependency.py').write_text('changed bound dependency\n')
    before = snapshot(workspace)
    blocked = command('check', '--run', state['run'], code=2)
    assert blocked['status'] == 'blocked' and blocked['diagnostics'][0]['code'] == 'source_drift'
    assert snapshot(workspace) == before
    return {'runtime_root': str(root), 'modules': sorted(runtime['modules']),
            'seeds': 3, 'host': host, 'WFB-LIVE': 'not_run'}


def exercise_workflow_candidate(extracted, workspace, host):
    """No checkout imports or ambient native session identities in the child."""
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('CODEX_', 'CLAUDE_', 'TASKPLANE_', 'PLUGIN_ROOT'))}
    environment.update(CODEX_HOME=str(workspace.parent/(host+'-workflow-codex-home')),
                       CLAUDE_CONFIG_DIR=str(workspace.parent/(host+'-workflow-claude-home')))
    environment['CODEX_THREAD_ID' if host == 'openai' else 'TASKPLANE_CLAUDE_SESSION_ID'] = 'package-fixture'
    script = ('import sys, json\nsys.path.insert(0, sys.argv[1])\n'
              + inspect.getsource(workflow_candidate_checks)
              + '\nprint(json.dumps(workflow_candidate_checks(*sys.argv[1:])))\n')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                             str(extracted.resolve()), str(workspace.resolve()), host],
                            cwd=workspace.parent, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['runtime_root'] == str(extracted.resolve())
    assert evidence['host'] == host and evidence['seeds'] == 3 and evidence['WFB-LIVE'] == 'not_run'


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
        assert {'taskplane/blueprint.py', 'taskplane/blueprint_catalog.py', 'taskplane/blueprint_compile.py',
                'skills/tp-workflow/SKILL.md', 'agents/tp-workflow-builder.md', 'docs/workflow-builder.md',
                'workflows/change-risk-review.workflow.json', 'workflows/design-brief.workflow.json',
                'workflows/feature-delivery.workflow.json', 'taskplane/claude_worker_invocation.py',
                'taskplane/claude_worker_observations.py', 'scripts/verify_claude_workers.py',
                'scripts/verify_claude_interactive.py',
                'docs/claude-worker-recovery.md', 'docs/claude-interactive-recovery.md',
                'docs/test-candidate.md'} <= set(archive.namelist())
        dispatch = archive.read('skills/tp-go/references/codex-native-dispatch.md').decode()
        assert 'operation prepare' in dispatch and 'minimum live acceptance test' in dispatch
        assert 'hooks/hooks.json' in archive.namelist()
        archive.extractall(extracted)
    exercise_workflow_candidate(extracted, tmp_path/(host+'-workflow-builder'), host)
    # Successful Claude context transport requires the native POSIX adapter.
    # Windows still verifies the extracted package, refusal and legacy surfaces.
    if host != 'claude' or CLAUDE_TRANSPORT_SUPPORTED:
        exercise((tmp_path/(host+'-native')).resolve(), 'claude' if host=='claude' else 'codex', root=extracted)
        exercise_autonomous((tmp_path/(host+'-autonomous')).resolve(),
                            'claude' if host=='claude' else 'codex', root=extracted)
        exercise_state_repairs((tmp_path/(host+'-state-repairs')).resolve(),
                               'claude' if host=='claude' else 'codex', root=extracted)
        exercise_correction_cli((tmp_path/(host+'-review-correction')).resolve(),
                                'claude' if host=='claude' else 'codex', root=extracted)
    else:
        exercise_unsupported_claude_context((tmp_path/(host+'-unsupported')).resolve(), root=extracted)
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


def test_native_default_instruction_contract():
    sources = ['skills/taskplane/SKILL.md', 'skills/tp-engineering/SKILL.md',
               'skills/tp-go/references/shared-flow.md', 'skills/tp-go/references/codex-native-dispatch.md']
    for name in sources:
        text = (ROOT/name).read_text()
        assert 'execution_contract: "native-default/v1"' in text, name
        assert 'one worker per selected' in text, name
        assert 'native_required' in text and 'serial_scope' in text, name
        assert 'Explicit user serial/no-delegation constraints take priority' in text, name
        assert 'Observe host capacity' in text and 'prerequisites' in text, name
        assert 'required native tasks remain incomplete and block sealing' in text, name
        assert 'returned `next_action`' in text and 'Only the root verifies' in text, name
        assert 'When authorized, use the installed native dispatch protocol' not in text, name
    assert 'Native worker dispatch remains unsupported by the current cooperative adapter' not in (ROOT/'docs/cli-reference.md').read_text()


def test_claude_capture_handles_nested_schema_name_objects(tmp_path):
    from scripts import verify_claude_workers as capture

    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(json.dumps({
        'name': {'description': 'A schema property, not a tool name'},
        'tools': [{'name': 'Agent', 'input_schema': {
            'type': 'object', 'properties': {'name': {'type': 'string'}}}}],
        'content': [{'type': 'tool_use', 'id': 'call-1', 'name': 'Agent', 'input': {}}],
    }) + '\n')
    native, _ = capture.transcript_evidence([
        {'input': {'session_id': 'session', 'transcript_path': str(transcript)}}
    ], 'session')
    assert native['errors'] == []
    assert [schema['name'] for schema in native['tool_schemas']] == ['Agent']
    assert native['calls'][0]['call']['id'] == 'call-1'


def test_claude_live_completion_requires_native_identity():
    from scripts import verify_claude_workers as capture

    valid = {'type': 'system', 'subtype': 'task_notification', 'session_id': 'root',
             'task_id': 'child', 'tool_use_id': 'launch', 'status': 'completed',
             'summary': 'Returned a review finding.'}
    assert capture.native_completion([valid], 'root', 'child', 'launch') == [valid]
    for key, value in [('type', 'assistant'), ('session_id', 'other'), ('task_id', 'other'),
                       ('tool_use_id', 'old'), ('status', 'running'), ('summary', '')]:
        assert capture.native_completion([{**valid, key: value}], 'root', 'child', 'launch') == []
    assert capture.native_completion([valid], 'root', None, None) == []


@pytest.mark.skipif(not CLAUDE_TRANSPORT_SUPPORTED, reason='Requires the POSIX Claude invocation parser')
def test_live_launcher_uses_identity_parser_canonical_interpreter(tmp_path, monkeypatch):
    from scripts import verify_claude_workers as capture
    from taskplane import claude_worker_invocation as invocation

    executable = Path(sys.executable).resolve()
    alias = tmp_path / 'python-alias'
    alias.symlink_to(executable)
    monkeypatch.setattr(capture.sys, 'executable', str(alias))
    plugin = tmp_path / 'plugin'
    project = tmp_path / 'project'
    bootstrap = project / '.taskplane/bootstrap'
    bootstrap.mkdir(parents=True)
    (bootstrap / 'scope.json').write_text(json.dumps({'paths': {'engineering': ['report.md']}}))
    permissions = capture.live_permissions(plugin, project)
    context_rule = next(rule for rule in permissions if ' flow context ' in rule)
    command = context_rule[len('Bash('):-len(' *)')] + ' --workspace ' + str(project) + ' --run ' + 'a' * 32
    parsed = invocation.parse_command(command, python=str(alias), script=str(plugin / 'taskplane/tp.py'))
    assert parsed.argv[0] == str(executable)
    prompt = capture.live_prompt(plugin, project, project, False)
    assert str(alias) not in prompt
    assert parsed.argv[0] in prompt


def test_claude_plugin_selection_preserves_each_initialization_snapshot():
    from scripts import verify_claude_workers as capture

    plugin = {'name': 'taskplane', 'path': '/candidate', 'version': '2.32.1'}
    row = {'type': 'system', 'subtype': 'init', 'session_id': 'root', 'plugins': [plugin]}
    assert capture.candidate_selections([row, row, row], 'root') == [[plugin], [plugin], [plugin]]
    assert capture.candidate_selections([{**row, 'plugins': [plugin, plugin]}], 'root') == [[plugin, plugin]]
    assert capture.candidate_selections([{**row, 'session_id': 'other'}], 'root') == []
    changed = {**plugin, 'path': '/competing'}
    assert capture.candidate_selections([row, {**row, 'plugins': [changed]}], 'root') == [[plugin], [changed]]


def test_claude_live_overlap_ends_at_native_stop():
    from scripts import verify_claude_workers as capture

    a = {'claimed_at': '2026-10-05T04:19:48+00:00', 'ended_at': '2026-10-05T04:23:00+00:00',
         'stop_observation': {'observed_at': '2026-10-05T04:20:50+00:00'}}
    b = {'task_id': 'CW-LIVE-B', 'claimed_at': '2026-10-05T04:20:52+00:00',
         'stop_observation': {'observed_at': '2026-10-05T04:21:32+00:00'}}
    assert capture.overlapping_workers(a, [b]) == []
    earlier = {**b, 'claimed_at': '2026-10-05T04:20:30+00:00'}
    assert capture.overlapping_workers(a, [earlier]) == [earlier]
    assert capture.overlapping_workers({**a, 'stop_observation': {}}, [earlier]) == []


def test_claude_capture_records_system_midturn_delivery(tmp_path):
    from scripts import verify_claude_workers as capture

    valid = {'type': 'attachment', 'renderedRole': 'system', 'sessionId': 'session',
             'attachment': {'type': 'queued_command', 'commandMode': 'task-notification',
                            'origin': {'kind': 'task-notification', 'producer': 'session-task'},
                            'prompt': '<task-notification>native content</task-notification>'}}
    rows = [valid, {**valid, 'sessionId': 'other'}, {**valid, 'renderedRole': 'user'},
            {**valid, 'attachment': {**valid['attachment'], 'type': 'queue-operation'}}]
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    native, _ = capture.transcript_evidence([
        {'input': {'session_id': 'session', 'transcript_path': str(transcript)}}
    ], 'session')
    assert native['errors'] == []
    assert [item['event'] for item in native['completion_notifications']] == [valid]
