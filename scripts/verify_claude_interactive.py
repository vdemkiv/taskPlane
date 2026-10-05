#!/usr/bin/env python3
"""Prepare and inspect a real interactive Claude recovery session.

Launch uses a terminal (never --print). Hooks delegate unchanged to an isolated
candidate. The inspector reads automatic hook captures and native transcripts;
it never authors lifecycle events or edits a workflow controller.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import uuid

if __package__:
    from . import verify_claude_workers as base
else:
    import verify_claude_workers as base


def prompt(plugin: Path, workspace: Path) -> str:
    runtime = shlex.join([str(Path(sys.executable).resolve()), str(plugin / 'taskplane/tp.py')])
    return f"""Run the explicitly authorized bounded Taskplane standalone Engineering
integration check in {workspace}. Use native Agent workers, not shell or serial
root reviewers. The scoped fixture tests interactive startup-failure recovery.
Use this exact candidate launcher: {runtime}
Read {plugin / "skills/tp-go/references/shared-flow.md"} and
{plugin / "skills/tp-go/references/codex-native-dispatch.md"} using Read with
absolute paths. Keep the shell working directory at {workspace}; never cd into
the plugin to read its documentation. Run pwd immediately before every worker
preparation and native dispatch. If it differs, first execute cd {workspace}
as a separate foreground command and verify pwd. Native worker identity requires
this exact workspace. Execute exactly:
{runtime} flow activate --workspace {workspace} --phase engineering --request-reference interactive-recovery-test
{runtime} flow start --workspace {workspace} --standalone --phase engineering --scope .taskplane/bootstrap/scope.json --tasks .taskplane/bootstrap/tasks.json --request-reference interactive-recovery-test --goal 'Interactive worker failure recovery and two independent reviews'
Consume root context and an ordinary Read, then prepare CW-LIVE-A. Use actual
host capacity; when not exposed use host_slots:null, includes_root:false,
configured_limit:2 with a reference to the actual Agent schema. Two is this
test's configured budget, not a claimed host slot count.

For A's FIRST attempt, pass the prepared dispatch prefix unchanged and then
explicitly instruct this fault-injection exception: do not claim or read source;
call the actually exposed SubagentHandback with only message text reporting
'Deliberate startup failure before claim/context; no review or files produced.'
This single bounded implicit-parent failure report is the behavior under test.
If that tool is absent, report interactive capability unavailable and stop.
Wait for its actual terminal notification and inspect worker status. Preserve
the failed attempt; never accept it as completed review. Only after actual
terminal evidence releases it, prepare ONE fresh CW-LIVE-A grant with retry_reason
describing deliberate startup failure and now removing that injection.

Fresh A must claim exactly, consume every context next_action until done, Read
averages.py, run python3 -c 'import time; time.sleep(60)' to permit concurrent
work, reproduce average([]) using python3 -c 'from averages import average; print(average([]))',
write .taskplane/review-a.md with the defect, source line and actual result, then
deliver the report using the actual SubagentHandback tool. Observe readiness
(claim, full context, matching automatic hook pair) before preparing CW-LIVE-B while A
is active. Use at most six readiness checks over 45 seconds, without a background
watcher. Wait for host completion notifications instead of shell polling loops.

B independently reviews settings.py and reproduces out-of-range port acceptance
using python3 -c 'from settings import listener_port; print(listener_port("70000"))'.
To exercise actionable startup recovery, instruct B to make its FIRST claim
attempt with ' 2>&1 | head -50' appended, observe the expected hook refusal, then
retry the exact original claim unmodified. This is one deliberate invalid call,
not a request to bypass hooks. If the exact retry is refused, report failure and
stop. B must fully consume context, Read source, run reproduction, write
.taskplane/review-b.md, and deliver using SubagentHandback.

Keep all runtime commands exact, without redirection/pipes except B's one deliberate
negative call. Root must inspect actual output, independent terminal evidence,
then accept each successful grant through flow worker --operation accept-result
with outputs:[its review path] and checks:[{{name:'Actual independent review',status:'pass',evidence:its review path}}].
The failure attempt never counts as a reviewed lens. Do not modify sources,
plugin code, scope/tasks, permissions, hooks, native transcripts or controller
files. Use normal Write/Edit for scoped reports. Never manually invoke hooks.
If a required operation is denied, preserve the exact failure and stop, except
the explicitly planned single invalid B command followed by its exact retry.

After both useful reviews are accepted, write .taskplane/review.md with coverage
and findings and report 'INTERACTIVE_RECOVERY_COMPLETE'. Do not submit a phase,
approve a checkpoint, advance, retire a run or disable the harness. This is a
bounded integration check; workflow completion is not a test objective. Leave
the captured terminal/session and all evidence available to the outer inspector.
"""


def ten_worker_prompt(plugin: Path, workspace: Path) -> str:
    request = prompt(plugin, workspace)
    request = request[:request.index('\nAfter both useful reviews are accepted')]
    request = request.replace('two independent reviews', 'ten independent reviews')
    request = request.replace('configured_limit:2', 'configured_limit:10')
    request = request.replace("Two is this", "Ten is this")
    control = """
While fresh A is active and ready, discover actual tools using
ToolSearch with query 'select:SendMessage,TaskStop,ListAgents', max_results:3.
Call the exposed read-only ListAgents once with its exact empty input. Its status
labels are observations, not substitutes for Taskplane's completion proof. Send A one
plain-text scoped instruction through SendMessage using its actual native worker
ID: 'Please include the exact reproduced result in your assigned report.'
Use the actually exposed schema; do not guess recipients. Discovery of TaskStop
does not authorize cancelling a useful review. Preserve both automatic hook events.
"""
    request = request.replace('Use at most six readiness checks', control + '\nUse at most six readiness checks')
    cases = '\n'.join(
        f"CW-LIVE-{suffix}: Read {source}; reproduce with {shlex.join(['python3', '-c', reproduction])}; "
        f"write .taskplane/review-{suffix.lower()}.md; lens {lens}."
        for suffix, source, lens, _, reproduction in base.EXTRA_REVIEWS)
    return request + f"""

Also dispatch EIGHT additional independent useful native reviews, C through J,
using the exact prepared task/grant/prefix and normal complete startup contract.
They may run concurrently within the configured budget after fresh A is ready.
Each must reproduce its distinct source defect and use actual SubagentHandback.
{cases}
Together with A and B this requires TEN distinct accepted useful native workers,
plus A's preserved failed startup attempt. No duplicate reviewer or serial root
review can substitute. Do not stop after A and B.

After all ten useful reviews are accepted, use the native AskUserQuestion once
to exercise the displayed decision labels. Ask 'Which label should this
integration fixture exercise?' with options 'Approve as is' and
'Request changes: reject deleted IDs; never normalize them'. This is a UI fixture
selection, not a human checkpoint approval. Preserve its actual tool response;
do not pass it to flow decide, policy or auto-decide or invent human provenance.

Write .taskplane/review.md with ten-lens coverage and actual findings. Write the
normal Engineering phase packet at .taskplane/review.json and its passing
review-check evidence at .taskplane/review-checks.json. Use all ten exact task
IDs, criteria and review_lens names from the prepared tasks; include real
accepted native-result coverage. Read the candidate Engineering skill if needed.
Refresh the strict decomposed graph, consume current required root context,
prevalidate and submit the Engineering packet. Present its native dashboard and
stop at the PENDING human checkpoint. Do not approve, advance, finish, retire,
disable hooks or change any prepared inputs. Report TEN_WORKER_REVIEW_COMPLETE
only after ten useful accepted results and a sealed pending checkpoint exist.
"""


def prepare(candidate: Path, output: Path, worker_count: int = 2) -> None:
    folder = Path(tempfile.mkdtemp(prefix='taskplane-claude-interactive-')).resolve()
    capture = folder / 'hooks'
    capture.mkdir()
    plugin = base.stage_plugin(candidate, folder, capture, None)
    original, workspace, git = base.make_project(folder, False, worker_count)
    session = str(uuid.uuid4())
    (folder / 'prompt.txt').write_text(
        ten_worker_prompt(plugin, workspace) if worker_count == 10 else prompt(plugin, workspace))
    manifest = {'schema': 'taskplane.claude-interactive-fixture/v1',
                'created_at': base.stamp(), 'session_id': session, 'worker_count': worker_count,
                'folder': str(folder), 'workspace': str(workspace),
                'plugin': str(plugin), 'candidate': base.fingerprint(candidate),
                'staged_candidate': base.fingerprint(plugin), 'git_setup': git,
                'input_sha256': {p: base.digest(base.read_regular(workspace / p)) for p in
                    [*base.load(workspace / '.taskplane/bootstrap/scope.json')['verification_inputs'],
                     '.taskplane/bootstrap/scope.json', '.taskplane/bootstrap/tasks.json']},
                'harness_sha256': base.digest(base.read_regular(Path(__file__))),
                'prompt_sha256': base.digest(base.read_regular(folder / 'prompt.txt'))}
    base.save(folder / 'prepared.json', manifest)
    base.save(output, {'status': 'prepared', 'fixture': str(folder), 'session_id': session,
                       'workspace': str(workspace), 'plugin': str(plugin)})
    print(json.dumps({'fixture': str(folder), 'session_id': session, 'workspace': str(workspace)}))


def validate(folder: Path) -> dict:
    manifest = base.load(folder / 'prepared.json')
    if Path(manifest['folder']) != folder:
        raise ValueError('Fixture directory mismatch')
    for key in ('workspace', 'plugin'):
        if not Path(manifest[key]).is_relative_to(folder):
            raise ValueError('Fixture path escapes prepared directory')
    if base.fingerprint(Path(manifest['plugin'])) != manifest['staged_candidate']:
        raise ValueError('Staged runtime changed; prepare a fresh fixture')
    if base.digest(base.read_regular(folder / 'prompt.txt')) != manifest['prompt_sha256']:
        raise ValueError('Prepared request changed')
    for name, expected in manifest['input_sha256'].items():
        if base.digest(base.read_regular(Path(manifest['workspace']) / name)) != expected:
            raise ValueError('Prepared source/scope/task inputs changed')
    return manifest


def launch(folder: Path, claude: str) -> None:
    m = validate(folder)
    workspace, plugin = Path(m['workspace']), Path(m['plugin'])
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Interactive validation requires a real terminal; refusing headless fallback')
    # Handback is exposed by this host in auto mode (the incident's mode).
    # Normal scoped permissions remain active; no bypass or feature flag override.
    rules = [r for r in base.live_permissions(plugin, workspace) if not r.startswith('Write(')]
    rules += ['Agent', 'SubagentHandback', 'ToolSearch', 'SendMessage', 'TaskStop', 'ListAgents', 'AskUserQuestion']
    if m.get('worker_count') == 10:
        rules += [f"Bash({shlex.join(['python3', '-c', row[4]])})" for row in base.EXTRA_REVIEWS]
    argv = [claude, '--ax-screen-reader', '--plugin-dir', str(plugin), '--session-id', m['session_id'],
            '--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--permission-mode', 'auto', '--add-dir', str(plugin),
            '--allowedTools', *rules, '--', (folder / 'prompt.txt').read_text()]
    base.save(folder / 'launch.json', {'started_at': base.stamp(), 'argv': argv,
              'mode': 'interactive-tty', 'session_id': m['session_id']})
    os.chdir(workspace)
    os.execvpe(claude, argv, base.clean_environment(workspace))


def native_state(manifest: dict) -> dict:
    states = []
    for path in (Path(manifest['workspace']) / '.taskplane').glob('workflow-*.json'):
        if path.name.endswith('.initialized.json'):
            continue
        db = base.load(path)
        if db.get('root') == manifest['session_id']:
            states.extend(db.get('runs', {}).values())
    if len(states) != 1:
        raise ValueError('Expected one matching native root run')
    return states[0]


def continuity(state: dict) -> dict:
    """Only durable workflow identity and decisions; automatic reads may add telemetry."""
    stage = state['visits'][state['index']]
    return {key: state.get(key) for key in ('root', 'run', 'revision', 'index', 'finished')} | {
        'visit': stage['id'], 'phase': stage['phase'], 'decision': stage['decision'],
        'packet': base.digest(json.dumps(stage.get('packet'), sort_keys=True).encode()),
        'decisions': base.digest(json.dumps(state.get('decisions'), sort_keys=True).encode()),
        'task_results': base.digest(json.dumps(state.get('task_results'), sort_keys=True).encode())}


def concurrent_reviews(rows: list[dict]) -> list[list[str]]:
    """Corroborate distinct useful reviewers using actual claim and stop times."""
    pairs = []
    for index, first in enumerate(rows):
        for second in rows[index + 1:]:
            if any(not first.get(key) or not second.get(key) or first[key] == second[key]
                   for key in ('task_id', 'worker_id', 'grant_id')):
                continue
            starts = [row.get('claimed_at') for row in (first, second)]
            stops = [row.get('stop_observation', {}).get('observed_at') for row in (first, second)]
            if all(starts + stops) and max(starts) < min(stops):
                pairs.append([first['grant_id'], second['grant_id']])
    return pairs


def resume(folder: Path, claude: str) -> None:
    manifest = validate(folder)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Native resume requires a real terminal')
    if (folder / 'resume.json').exists():
        raise ValueError('Resume evidence already exists; do not overwrite an earlier attempt')
    state = native_state(manifest)
    original = base.load(folder / 'launch.json')
    argv = list(original['argv'])
    index = argv.index('--session-id')
    if argv[index + 1] != manifest['session_id'] or original['session_id'] != manifest['session_id']:
        raise ValueError('Original launch session mismatch')
    argv[0], argv[index] = claude, '--resume'
    runtime = shlex.join([str(Path(sys.executable).resolve()), str(Path(manifest['plugin']) / 'taskplane/tp.py')])
    report = f"{runtime} flow report --workspace {manifest['workspace']} --run {state['run']}"
    argv[-1] = f"""Continue this original native session for a read-only integration check.
The original root is {manifest['session_id']} and run is {state['run']}.
Execute exactly: {report}
From its actual returned command summary, choose a registered result reference
and read it using the same launcher and workspace/run with flow inspect --kind
result --reference ACTUAL_SHA256. Follow any returned page cursor needed to read
that result. Report the original run, phase and decision, then stop.
Do not start or replace a run, claim another root, write source/reports, approve
or advance a checkpoint, or invent a human decision/resume envelope. This checks
native --resume continuity, not cross-session authority transfer. If any required
read is refused, preserve that failure and stop."""
    base.save(folder / 'resume.json', {'started_at': base.stamp(), 'argv': argv,
              'mode': 'interactive-tty', 'session_id': manifest['session_id'],
              'before': continuity(state), 'report_command': report})
    os.chdir(manifest['workspace'])
    os.execvpe(claude, argv, base.clean_environment(Path(manifest['workspace'])))


def inspect(folder: Path, output: Path) -> None:
    m = validate(folder)
    session, workspace = m['session_id'], Path(m['workspace'])
    captures = [base.load(p) for p in (folder / 'hooks').glob('*.json')]
    # The host emits stops for auxiliary internal agents without persisted files.
    # Require transcripts for actual registered workers; retain every raw hook.
    started = {c['input'].get('agent_id') for c in captures if c['event'] == 'SubagentStart'}
    selected = [c for c in captures if not c['input'].get('agent_id')
                or c['input']['agent_id'] in started]
    native, _ = base.transcript_evidence(selected, session)
    state = native_state(m)
    rows = list(state.get('workers', {}).values())
    successful = [r for r in rows if r.get('state') == 'accepted']
    count = m.get('worker_count', 2)
    expected = {task['id'] for task in base.load(workspace / '.taskplane/bootstrap/tasks.json')['tasks']}
    first = [r for r in rows if r.get('task_id') == 'CW-LIVE-A' and r.get('attempt') == 1]
    handbacks = [c for c in captures if c['input'].get('tool_name') == 'SubagentHandback']
    startup_handbacks = [c for c in handbacks if len(first) == 1
                        and c['event'] == 'PreToolUse'
                        and c['input'].get('agent_id') == first[0].get('worker_id')
                        and base.admitted(c)]
    denied = [c for c in captures if c['event'] == 'PreToolUse'
              and '2>&1 | head -50' in c['input'].get('tool_input', {}).get('command', '')
              and not base.admitted(c)]
    checks = [
        base.check('actual interactive launch', base.load(folder / 'launch.json')['mode'] == 'interactive-tty'
                   and any(c['input'].get('permission_mode') == 'auto' for c in captures),
                   'Terminal enforced with observed auto permission mode, no --print fallback'),
        base.check('exact native attempt count', len(rows) == count + 1, [(r.get('task_id'), r.get('attempt'), r.get('state')) for r in rows]),
        base.check('startup failure preserved unaccepted', len(first) == 1 and not first[0].get('claimed_at')
                   and first[0].get('state') == 'failed' and first[0].get('terminal_status') == 'failed', first),
        base.check('all independent useful results accepted', {r['task_id'] for r in successful} == expected
                   and len(successful) == count
                   and len({r.get('worker_id') for r in successful}) == count, [r.get('grant_id') for r in successful]),
        base.check('interactive handback actually invoked', bool(handbacks), len(handbacks)),
        base.check('startup handback admitted', len(startup_handbacks) == 1, len(startup_handbacks)),
        base.check('handback schema observed', bool(native['handback_schemas']), len(native['handback_schemas'])),
        base.check('useful native workers overlapped', bool(concurrent_reviews(successful)),
                   {'basis': 'Distinct accepted workers with actual claim and stop observations',
                    'grant_pairs': concurrent_reviews(successful)}),
        base.check('invalid wrapper refused once', len(denied) == 1, len(denied)),
        base.check('source and candidate remain unchanged', all(c.get('runtime_unchanged') for c in captures), 'Prepared hashes revalidated'),
        base.check('native evidence readable', not native['errors'], native['errors']),
    ]
    if count == 10:
        def observed_tool(name):
            pre = [c for c in captures if c['event'] == 'PreToolUse'
                   and c['input'].get('tool_name') == name and base.admitted(c)]
            return any(p['input'].get('tool_use_id') and any(
                c['event'] == 'PostToolUse' and c['input'].get('tool_use_id') == p['input']['tool_use_id']
                and c['input'].get('tool_name') == name and not base.response_failed(c['input'])
                for c in captures) for p in pre)
        stage = state['visits'][state['index']]
        checks.extend([
            base.check('native control discovery exercised', observed_tool('ToolSearch'), 'Automatic admitted pre/post observations'),
            base.check('native read-only worker listing exercised', observed_tool('ListAgents'), 'Listing alone cannot join a worker'),
            base.check('owned worker message exercised', observed_tool('SendMessage'), 'Actual native control, not binary-schema inference'),
            base.check('native decision label dialog exercised', observed_tool('AskUserQuestion'),
                       'UI fixture response only; no checkpoint consent is inferred'),
            base.check('ten-result Engineering checkpoint sealed', stage['phase'] == 'engineering'
                       and stage['decision'] == 'awaiting_human_approval' and bool(stage.get('packet'))
                       and expected <= set(state.get('task_results', {})), stage['decision']),
        ])
        resumed = base.load(folder / 'resume.json') if (folder / 'resume.json').exists() else {}
        after_resume = [c for c in captures if resumed and c['started_at'] >= resumed['started_at']]
        resumed_start = any(c['event'] == 'SessionStart'
                            and c['input'].get('session_id') == session
                            and c['input'].get('source') == 'resume' for c in after_resume)
        def resumed_read(predicate):
            pre = [c for c in after_resume if c['event'] == 'PreToolUse'
                   and c['input'].get('tool_name') == 'Bash' and base.admitted(c)
                   and predicate(c['input'].get('tool_input', {}).get('command', ''))]
            return any(p['input'].get('tool_use_id') and any(
                c['event'] == 'PostToolUse' and c['input'].get('tool_use_id') == p['input']['tool_use_id']
                and not base.response_failed(c['input']) for c in after_resume) for p in pre)
        checks.extend([
            base.check('native same-session resume preserved workflow', resumed_start
                       and resumed.get('before') == continuity(state)
                       and resumed_read(lambda command: command == resumed.get('report_command')),
                       'Actual SessionStart resume plus successful original-run report; no approval inferred'),
            base.check('populated-state registered result read', resumed_read(
                lambda command: ' flow inspect ' in command and '--kind result' in command
                and '--reference ' in command and f"--run {state['run']}" in command),
                       'Actual successful registered-result read after native resume'),
        ])
    report = {'schema': 'taskplane.claude-interactive-verification/v1', 'observed_at': base.stamp(),
              'status': 'pass' if all(c['status'] == 'pass' for c in checks) else 'fail',
              'fixture': m, 'checks': checks, 'native_transcripts': native,
              'workers': rows, 'task_results': state.get('task_results', {}),
              'observations': base.capture_summary(captures, native, []),
              'coverage': 'Real interactive terminal and automatic captured hooks; no synthetic lifecycle events.'}
    base.save(output, report)
    print(json.dumps({'status': report['status'], 'checks': [{k: c[k] for k in ('name', 'status')} for c in checks], 'output': str(output)}))
    if report['status'] != 'pass':
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--launch', action='store_true')
    mode.add_argument('--resume', action='store_true')
    mode.add_argument('--inspect', action='store_true')
    parser.add_argument('--plugin-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--claude', default=shutil.which('claude'))
    parser.add_argument('--worker-count', type=int, choices=(2, 10), default=2,
                        help='Two preserves the recovery fixture; ten adds complete native review coverage')
    args = parser.parse_args()
    if (args.prepare or args.inspect) and args.output is None:
        parser.error('--prepare and --inspect require --output')
    if not args.prepare and args.fixture is None:
        parser.error('--launch, --resume and --inspect require --fixture')
    if (args.launch or args.resume) and not args.claude:
        parser.error('Claude executable unavailable; specify --claude')
    if args.prepare:
        prepare(args.plugin_dir.resolve(), args.output, args.worker_count)
    elif args.launch:
        launch(args.fixture.resolve(), args.claude)
    elif args.resume:
        resume(args.fixture.resolve(), args.claude)
    else:
        inspect(args.fixture.resolve(), args.output)


if __name__ == '__main__':
    main()
