"""Integrated retained-finding regressions using disposable canonical workspaces."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import flow, primitives, runtime_command, workflow as w
from taskplane import workflow_host as h, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decision, decide

PYTHON_NAME = 'python.exe' if os.name == 'nt' else 'python3'


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


def command(root, *words, name='PreToolUse', tool='exec_command', **extra):
    return {'hook_event_name': name, 'cwd': str(root), 'session_id': 'root',
            'tool_name': tool, 'tool_input': {'command' if tool == 'Bash' else 'cmd': shlex.join(words)}, **extra}


def runtime(root, *words, **extra):
    return command(root, sys.executable, str(Path(flow.__file__).with_name('tp.py')), *words, **extra)


def stdin(root, handle, chars='', *, name='PreToolUse', **extra):
    return {'hook_event_name': name, 'cwd': str(root), 'session_id': 'root', 'tool_name': 'write_stdin',
            'tool_input': {'session_id': handle, 'chars': chars}, **extra}


def shell_hook(event, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(event)))
    assert flow.run_hook('screen' if event['hook_event_name'] == 'PreToolUse' else 'observe') == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('text', ['Okay. Hold for my approval.', 'LGTM. Wait for my sign-off.'])
def test_controller_refuses_original_deferred_human_responses(root, text):
    controller, state = setup(root)
    state = submit(controller, state)
    envelope = decision(state, text=text)
    envelope['choice'] = 'approved'  # An affirmative label cannot override the full response.
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal, match='unclear'):
        decide(controller, state, envelope)
    assert controller._path().read_bytes() == before
    assert not controller.report()['decisions']
    assert decide(controller, state)['decisions']


@pytest.mark.parametrize('selector', ['duplicate', 'equals', 'mixed', 'abbreviation', 'abbrev_equals'])
@pytest.mark.parametrize('route', ['flow', 'graph', 'review'])
def test_bootstrap_rejects_conflicting_workspace_before_foreign_writes(root, selector, route):
    foreign = root / 'foreign'
    foreign.mkdir()
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    local.Harness(root, 'root').select('tp-go', 'user/request', controller.report())
    bad = {'duplicate': ['--workspace', str(root), '--workspace', str(foreign)],
           'equals': ['--workspace=' + str(root), '--workspace=' + str(foreign)],
           'mixed': ['--workspace', str(root), '--workspace=' + str(foreign)],
           'abbreviation': ['--workspace', str(root), '--work', str(foreign)],
           'abbrev_equals': ['--work=' + str(foreign)]}[selector]
    args = ['flow', 'activate', *bad] if route == 'flow' else ['graph', *bad, 'scan'] if route == 'graph' else ['review', 'start', *bad]
    with pytest.raises(w.Refusal):
        flow.hook(runtime(root, *args), governor=controller)
    assert list(foreign.iterdir()) == []
    if route == 'flow':
        with pytest.raises(SystemExit) as error:
            flow.main(args[1:], governor=controller)
        assert error.value.code == 2
        assert list(foreign.iterdir()) == []


@pytest.mark.parametrize('selector', [lambda root: ['--workspace', str(root)], lambda root: ['--workspace=' + str(root)]])
def test_single_exact_selector_matches_cli_and_hook(root, selector, monkeypatch, capsys):
    monkeypatch.setattr(flow, 'observed_parent', lambda *args: None)
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    local.Harness(root, 'root').select('tp-go', 'user/request', controller.report())
    args = ['activate', *selector(root), '--phase', 'tp-go', '--request-reference', 'user/request']
    flow.hook(runtime(root, 'flow', *args), governor=controller)
    assert flow.main(args, governor=controller) == 0
    capsys.readouterr()
    assert local.Harness(root, 'root').read()['selected']


@pytest.mark.parametrize('option', ['--work', '--request-ref', '--phas'])
def test_flow_cli_refuses_long_option_abbreviations(root, option):
    with pytest.raises(SystemExit) as error:
        flow.main(['activate', option, str(root)])
    assert error.value.code == 2
    assert not (root / '.taskplane').exists()


@pytest.mark.parametrize('tool', ['exec_command', 'Bash'])
def test_runtime_collision_reports_both_paths_and_keeps_foreign_denial(root, tool, monkeypatch, capsys):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    local.Harness(root, 'root').select('tp-go', 'user/request', controller.report())
    foreign = root / 'other-plugin' / 'taskplane' / 'tp.py'
    event = command(root, sys.executable, str(foreign), 'flow', 'activate', '--workspace', str(root), tool=tool)
    result = shell_hook(event, monkeypatch, capsys)['hookSpecificOutput']
    assert result['permissionDecision'] == 'deny'
    reason = result['permissionDecisionReason']
    assert str(foreign) in reason and str(Path(flow.__file__).with_name('tp.py')) in reason
    assert 'plugin settings' in reason and 'reload' in reason
    assert not foreign.parent.exists()
    flow.hook(runtime(root, 'flow', 'activate', '--workspace', str(root)), governor=controller)


@pytest.mark.parametrize('tool', ['exec_command', 'Bash'])
@pytest.mark.parametrize('tail', [('version', '--verify'), ('help', '--md')])
@pytest.mark.parametrize('directory_support', [False, True])
def test_required_binding_allows_only_exact_installed_diagnostics(root, tool, tail, monkeypatch, capsys, directory_support):
    if not directory_support:
        monkeypatch.setattr(os, 'supports_dir_fd', set())
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(root))
    event = runtime(root, *tail, tool=tool)
    result = shell_hook(event, monkeypatch, capsys)
    assert result['hookSpecificOutput'].get('permissionDecision') != 'deny'
    assert 'binding' in result['hookSpecificOutput']['additionalContext']
    assert not (root / '.taskplane').exists()
    for bad in [runtime(root, *tail, '--extra'), runtime(root, *tail, parent_session_id='parent'),
                runtime(root, tail[0], tail[1][:3]), command(root, sys.executable, '-c', 'print(1)'),
                command(root, 'sh', '-c', 'touch file'),
                {'hook_event_name': 'PreToolUse', 'cwd': str(root), 'session_id': 'root',
                 'tool_name': 'Write', 'tool_input': {'path': str(root / 'request.json'), 'content': '{}'}}]:
        assert shell_hook(bad, monkeypatch, capsys)['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert not (root / '.taskplane').exists()


def request():
    return {'scope': {'criteria': ['INIT'], 'paths': {p: [] for p in w.PHASES}, 'verification_inputs': []},
            'request_reference': 'user/first-start'}


@pytest.mark.parametrize('write_number', range(1, 7))
@pytest.mark.parametrize('after', [False, True])
def test_first_creation_recovers_each_write_interruption_without_read_repair(root, monkeypatch, write_number, after):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    original = primitives.atomic_json
    count = 0
    def interrupted(path, value, **kwargs):
        nonlocal count
        count += 1
        if count == write_number and not after:
            raise OSError('injected before publication')
        result = original(path, value, **kwargs)
        if count == write_number and after:
            raise OSError('injected after publication/directory sync')
        return result
    monkeypatch.setattr(primitives, 'atomic_json', interrupted)
    if write_number == 6:  # The run is durable before the advisory harness binding.
        committed = controller.start(request())
        assert committed['start_observation_errors']
        assert controller.report()['run'] == committed['run']
    else:
        with pytest.raises((OSError, w.Refusal), match='injected'):
            controller.start(request())
    monkeypatch.setattr(primitives, 'atomic_json', original)
    before = {p.name: p.read_bytes() for p in (root / '.taskplane').glob('*.json')}
    try:
        controller.report()
    except w.Refusal as exc:
        assert 'pending' in exc.detail
    assert {p.name: p.read_bytes() for p in (root / '.taskplane').glob('*.json')} == before
    state = controller.start(request())
    db = json.loads(controller._path().read_text())
    assert list(db['runs']) == [state['run']] and state['revision'] == 0 and not state['decisions']
    proof = json.loads(next((root / '.taskplane').glob('initialization-*.json')).read_text())
    assert proof['status'] == 'committed'
    assert proof['empty_sha256'] == primitives.content_fingerprint(controller.adapter.empty_database())


@pytest.mark.parametrize('call_id', [None, 'pending-start-call'])
def test_pending_initialization_resumes_through_hook_and_flow_cli(root, monkeypatch, capsys, call_id):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    original = primitives.atomic_json
    def interrupted(path, value, **kwargs):
        if Path(path).name == controller.adapter.filename:
            raise OSError('first database publication')
        return original(path, value, **kwargs)
    monkeypatch.setattr(primitives, 'atomic_json', interrupted)
    with pytest.raises(OSError):
        controller.start(request())
    monkeypatch.setattr(primitives, 'atomic_json', original)
    monkeypatch.setattr(flow, 'observed_parent', lambda *args: None)
    local.Harness(root, 'root').select('tp-go', 'user/first-start', {})
    (root / 'scope.json').write_text(json.dumps(request()['scope']))
    args = ['start', '--workspace', str(root), '--scope', 'scope.json', '--request-reference', 'user/first-start']
    flow.hook(runtime(root, 'flow', *args, call_id=call_id), governor=controller)
    flow.hook(runtime(root, 'flow', *args, call_id=call_id, name='PostToolUse', tool_response={'session_id': 6000}),
              governor=controller)
    for chars in ('', '\x03'):
        flow.hook(stdin(root, 6000, chars), governor=controller)
    for handle, chars in [(6001, ''), (6000, 'print(1)\n')]:
        with pytest.raises(w.Refusal):
            flow.hook(stdin(root, handle, chars), governor=controller)
    assert flow.main(args, governor=controller) == 0
    capsys.readouterr()
    assert controller.report()['run']
    assert controller.report()['observed_handles']['6000']['state'] == 'running'


@pytest.mark.parametrize('kind', ['legacy_marker', 'established_missing', 'corrupt_database', 'foreign_proof', 'nonempty_pending'])
def test_missing_or_corrupt_history_never_qualifies_as_fresh(root, kind):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    if kind == 'legacy_marker':
        marker = root / '.taskplane' / controller.adapter.markername
        primitives.atomic_json(marker, controller.adapter.marker())
    else:
        controller.start(request())
        target = controller._path()
        proof = next((root / '.taskplane').glob('initialization-*.json'))
        if kind == 'established_missing':
            target.unlink()
        elif kind == 'corrupt_database':
            target.write_text('{')
        else:
            data = json.loads(proof.read_text())
            data['root' if kind == 'foreign_proof' else 'status'] = 'other' if kind == 'foreign_proof' else 'pending'
            proof.write_text(json.dumps(data))
    before = {p.name: p.read_bytes() for p in (root / '.taskplane').glob('*.json')}
    for action in [controller.report, lambda: controller.start(request())]:
        with pytest.raises((w.Refusal, ValueError)):
            action()
    assert {p.name: p.read_bytes() for p in (root / '.taskplane').glob('*.json')} == before


def test_setup_handle_survives_first_start_and_only_accepts_safe_input(root):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    harness = local.Harness(root, 'root')
    harness.select('tp-go', 'user/first-start', controller.report())
    event = runtime(root, 'flow', 'activate', '--workspace', str(root), name='PostToolUse',
                    tool_response={'session_id': 4100})
    flow.hook(event, governor=controller)
    for chars in ('', '\x03'):
        flow.hook(stdin(root, 4100, chars), governor=controller)
    for bad in [stdin(root, 999), stdin(root, 4100, 'touch app.py\n'),
                {**stdin(root, 4100), 'session_id': 'foreign'}]:
        with pytest.raises(w.Refusal):
            flow.hook(bad, governor=controller)
    state = controller.start(request())
    assert state['observed_handles']['4100']['control'] is True
    assert harness.read()['setup_handles'] == {}
    assert not controller.adapter.can_seal(state)
    for chars in ('', '\x03'):
        flow.hook(stdin(root, 4100, chars), governor=controller)
    with pytest.raises(w.Refusal):
        flow.hook(stdin(root, 4100, 'print(1)\n'), governor=controller)
    before = deepcopy(state['observed_handles']['4100'])
    flow.hook(stdin(root, 4100, name='PostToolUse', tool_response={'exit_code': 0}), governor=controller)
    record = controller.report()['observed_handles']['4100']
    assert record == {**before, 'state': 'completed'}
    assert controller.adapter.can_seal(controller.report())
    with pytest.raises(w.Refusal):
        flow.hook(stdin(root, 4100), governor=controller)
    with pytest.raises(w.Refusal):
        flow.hook(event, governor=controller)
    # After retiring the run, a new selection cannot revive the pre-run handle.
    controller.apply('retire', state['run'], expected_revision=state['revision'], native_reference=json.dumps({
        'request_reference': 'user/retire', 'reason': 'Fixture complete'}))
    harness.select('tp-go', 'user/new-request', controller.report())
    with pytest.raises(w.Refusal):
        flow.hook(stdin(root, 4100), governor=controller)
    fresh = controller.start({**request(), 'request_reference': 'user/new-request'})
    assert fresh['observed_handles'] == {}


def test_active_control_handle_is_observed_at_sealed_checkpoint(root):
    controller, state = setup(root)
    state = submit(controller, state)
    event = runtime(root, 'flow', 'report', '--workspace', str(root), name='PostToolUse',
                    tool_response={'session_id': 4102})
    flow.hook(event, governor=controller)
    before = controller.report()['observed_handles']['4102']
    assert before['control'] and not controller.adapter.can_seal(controller.report())
    for chars in ('', '\x03'):
        flow.hook(stdin(root, 4102, chars), governor=controller)
    with pytest.raises(w.Refusal):
        flow.hook(stdin(root, 4102, 'touch app.py\n'), governor=controller)
    flow.hook(stdin(root, 4102, name='PostToolUse', tool_response={'exit_code': 1}), governor=controller)
    assert controller.report()['observed_handles']['4102'] == {**before, 'state': 'failed'}
    with pytest.raises(w.Refusal):
        flow.hook(event, governor=controller)


def test_worker_can_drain_own_claim_before_context_but_not_foreign_control(root):
    from taskplane.tests.test_worker_runtime import setup as worker_setup, reserve, launch
    controller, state = worker_setup(root, count=2)
    prepared = reserve(controller, state)
    launch(controller, state, prepared, child='native-0')
    worker = h.Controller(root, 'root', h.installed_adapter('codex'), principal='native-0')
    worker.worker(state['run'], 'claim', grant=prepared['grant']['grant_id'])
    event = runtime(root, 'flow', 'worker', '--operation', 'claim', '--workspace', str(root),
                    name='PostToolUse', tool_response={'session_id': 5200})
    event['session_id'] = 'native-0'
    worker.observe(event, state['run'])
    before = controller.report()['observed_handles']['5200']
    assert before['control'] and before['worker_id'] == 'native-0'
    for chars in ('', '\x03'):
        worker.guard({**stdin(root, 5200, chars), 'session_id': 'native-0'}, state['run'])
    for target, actor, chars in [(controller, 'root', ''), (worker, 'native-0', 'print(1)\n'),
                                 (worker, 'foreign', '')]:
        with pytest.raises(w.Refusal):
            target.guard({**stdin(root, 5200, chars), 'session_id': actor}, state['run'])
    worker.observe({**stdin(root, 5200, name='PostToolUse', tool_response={'exit_code': 0}),
                    'session_id': 'native-0'}, state['run'])
    assert controller.report()['observed_handles']['5200'] == {**before, 'state': 'completed'}
    with pytest.raises(w.Refusal):
        worker.guard({**stdin(root, 5200), 'session_id': 'native-0'}, state['run'])


def test_start_retry_finishes_handle_transfer_after_harness_write_interruption(root, monkeypatch):
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    harness = local.Harness(root, 'root')
    harness.select('tp-go', 'user/first-start', {})
    flow.hook(runtime(root, 'flow', 'activate', '--workspace', str(root), name='PostToolUse',
                      tool_response={'session_id': 7000}), governor=controller)
    original = local.Harness.bind
    monkeypatch.setattr(local.Harness, 'bind', lambda *args: (_ for _ in ()).throw(OSError('handoff interrupted')))
    committed = controller.start(request())
    assert committed['start_observation_errors']
    state = controller.report()
    assert state['run'] == committed['run']
    assert state['observed_handles']['7000']['state'] == 'running'
    assert harness.setup_handles()['7000']['state'] == 'running'
    monkeypatch.setattr(local.Harness, 'bind', original)
    assert controller.start(request())['run'] == state['run']
    assert harness.read()['setup_handles'] == {} and harness.read()['run'] == state['run']


@pytest.mark.parametrize('bad', ['old_visit', 'future_revision', 'foreign_owner'])
def test_control_handle_stale_or_foreign_binding_refuses(root, bad):
    controller, state = setup(root)
    event = runtime(root, 'flow', 'report', '--workspace', str(root), name='PostToolUse',
                    tool_response={'session_id': 8000})
    controller.observe(event, state['run'])
    db = json.loads(controller._path().read_text())
    record = db['runs'][state['run']]['observed_handles']['8000']
    record.update({'visit': 'old-visit'} if bad == 'old_visit' else
                  {'revision': state['revision'] + 1} if bad == 'future_revision' else {'worker_id': 'foreign'})
    controller._write(controller._path(), db)
    with pytest.raises(w.Refusal):
        controller.guard(stdin(root, 8000), state['run'])


@pytest.mark.parametrize('sealed', [False, True])
@pytest.mark.parametrize('variant', ['omitted', 'relative', 'relative_launcher', 'absolute_foreign'])
def test_different_exec_workdir_cannot_redirect_admitted_workspace(root, sealed, variant, monkeypatch):
    selected, foreign = root / 'selected', root / 'foreign'
    selected.mkdir(); foreign.mkdir()
    controller, state = setup(selected)
    if sealed:
        state = submit(controller, state)
    monkeypatch.chdir(selected)
    flags = [] if variant == 'omitted' else ['--workspace', '.' if variant == 'relative' else
                                            str(foreign if variant == 'absolute_foreign' else selected)]
    event = runtime(selected, 'flow', 'activate', *flags)
    event['tool_input']['workdir'] = str(foreign)
    if variant == 'relative_launcher':
        event['tool_input']['cmd'] = shlex.join([sys.executable, 'tp.py', 'flow', 'activate', *flags])
    if variant in {'omitted', 'relative'}:
        value = runtime_command.workspace_selector(flags)
        assert runtime_command.resolve_selection(value, event, selected) == foreign
    with pytest.raises(w.Refusal):
        flow.hook(event, governor=controller)
    assert list(foreign.iterdir()) == []


def test_different_workdir_keeps_absolute_matching_workspace_executable(root, monkeypatch, capsys):
    selected, foreign = root / 'selected', root / 'foreign'
    selected.mkdir(); foreign.mkdir()
    controller, state = setup(selected)
    submit(controller, state)
    args = ['activate', '--workspace', str(selected), '--phase', 'tp-go', '--request-reference', 'user/explicit']
    event = runtime(selected, 'flow', *args)
    event['tool_input']['workdir'] = str(foreign)
    flow.hook(event, governor=controller)
    monkeypatch.chdir(foreign)
    monkeypatch.setattr(flow, 'observed_parent', lambda *args: None)
    assert flow.main(args, governor=controller) == 0
    capsys.readouterr()
    assert list(foreign.iterdir()) == []
    # An explicitly relative dashboard output follows command cwd too.
    board = runtime(selected, 'dashboard', '--workspace', str(selected), '--out', '.taskplane/dashboard.html')
    board['tool_input']['workdir'] = str(foreign)
    with pytest.raises(w.Refusal):
        flow.hook(board, governor=controller)


def test_same_workdir_relative_workspace_uses_execution_cwd(root, monkeypatch):
    controller, state = setup(root)
    submit(controller, state)
    monkeypatch.chdir(root)
    event = runtime(root, 'flow', 'report', '--workspace', '.')
    event['tool_input']['workdir'] = str(root)
    flow.hook(event, governor=controller)


@pytest.mark.parametrize('text', [
    'Okay. I am withholding approval.', "Okay. I'll sign it off tomorrow.",
    'LGTM. My approval is still to come.', 'Okay. Approval is to follow.',
    'Okay. I still need to sign this off.', 'Okay. Approval is forthcoming.',
    'Okay. Approval remains my responsibility.',
    'Okay. I will give the go-ahead tomorrow.', 'Okay. My green light will come later.',
    "Okay. I'll give my OK tomorrow.", 'Okay. I need more time.',
    'Okay. Let me think about it.', 'Okay. I will decide tomorrow.',
    'LGTM; "Allow us to consider this".',
    'Okay. I need more time to think about it.',
    'Okay. We require additional time to deliberate.',
    'Okay. I need time to think about it.',
    'Okay. We need some more time to deliberate.',
    'Okay. I need more time to really think this through.',
    'Okay. We require additional time for consideration.',
    'Okay. We need more time to finalize.',
    'Okay. "I need more time to think about it."',
    'Okay. “We require additional time to deliberate.”',
    "Okay. 'I need time to think about it.'",
    'Okay. `We need some more time to deliberate.`',
    'Okay.\n> I need more time to really think this through.',
    'Okay.\n```\nWe require additional time for consideration.\n```',
    'Approved. "We need more time to finalize." Approved.',
    'I need more time to think about it. Okay.',
    'We require additional time to deliberate. Okay.',
    'Go ahead. For further consideration, we need some more time. Approved.',
    'Approved. I need. More time to really think this through.',
    'Okay. We require additional; time for consideration.',
    'Approved. I need more time to think. We need more time to implement the plan.',
    'Approved. We need more time to implement the plan. I need more time to think.',
    'Approved. To deliberate, we need more time to implement the plan.',
    'Approved. We need more time to implement the plan; to think about it, I need time.',
    'Approved. I need. We need more time to implement the plan. More time to think.',
    # Shared purposes must remain part of the complete time request.
    'Okay. We need more time to implement the plan\nand to deliberate.',
    'Okay. We need more time to implement the plan; and to deliberate.',
    'Okay. We need more time to implement the plan and to deliberate.',
    'Okay. We need more time to implement the plan. And to deliberate.',
    'Okay. We need more time to implement the plan, and to deliberate.',
    'Okay. We need more time to implement the plan: and to deliberate.',
    'Okay. We need more time to implement the plan — and to deliberate.',
    'Approved. "We need more time to implement the plan; and to deliberate." Approved.',
    'Okay. We need more time to implement the plan; “and to deliberate”.',
    'Okay. We need more time to implement the plan; `and to deliberate`.',
    'Okay. We need more time to implement the plan\n> and to deliberate.',
    'Okay. We need more time to implement the plan.\n```\nand to deliberate\n```',
    'Okay. We need more time to deliberate\nand to implement the plan.',
    'Go ahead. For contemplation; we need more time to implement the plan. Approved.',
    'We need more time to implement the plan; and to deliberate. Okay.',
    'Okay. We need more time to implement the plan; for further contemplation.',
    'Okay. We need more time to implement the plan\nand for carefully considering the plan.',
    'Okay. We need more time to implement the plan; and to finalize.',
    'Okay. We need more time to implement the plan; for the next step.',
    'Okay. We need more time to implement the plan; to quuxify the decision process.',
    'Okay. We need more time to implement the plan; this remains undecided.',
    'Approved. We need more time to implement the plan. Approved. quux.',
    'Approved. We need more time to implement the plan; and to publish the release.',
    'Approved. We need more time to implement the plan\nplease and thank you.',
    # Broad time-request detection must not grant unknown quantity modifiers.
    'Approved. We need deliberation time to implement the plan.',
    'Okay. We need thinking time to build the feature. I approve this phase now.',
    'Approved. We need further consideration time to implement the plan.',
    'Go ahead. “I require my approval time for implementation of these changes”.',
    'Approved. We need permission time to implement the plan.',
    'Okay. We need quux time to build the feature. I approve this phase now.',
])
def test_controller_retained_consent_refusal_preserves_pending_store(root, text):
    controller, state = setup(root)
    state = submit(controller, state)
    envelope = decision(state, text=text)
    assert envelope['choice'] is None
    envelope['choice'] = 'approved'
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal, match='unclear') as refusal:
        decide(controller, state, envelope)
    assert refusal.value.reason == 'invalid_evidence'
    assert controller._path().read_bytes() == before
    with pytest.raises(w.Refusal) as refusal:
        controller.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    assert refusal.value.reason == 'approval_required'
    assert controller._path().read_bytes() == before
    pending = controller.report()
    assert w.current(pending)['decision'] == 'awaiting_human_approval'
    assert pending['revision'] == state['revision'] and not pending['decisions']


@pytest.mark.parametrize('text', [
    'Approved.', 'Looks good, proceed.', 'Okay. I sign this off now.',
    'Okay. I give my green light now.', 'Okay. I grant the go-ahead.',
    'Approved. We will publish tomorrow.',
    'Approved. We need more time to implement the plan.',
    'Product approved. Results clearly show over 50% reduction.',
    'Approved. We need time to implement the plan and to build the feature.',
    'Approved. We need time to build the feature and publish the release.',
    'Okay. I require additional time for implementation of these changes.',
    'LGTM. We need time to ship the code.\nWe need time to publish the documentation.',
    'Go ahead. "We need more time to implement the plan". Approved.',
    'Approved. Our team needs a little extra time to publish the release tomorrow.',
    'Okay. We need more time to implement the plan. I approve this phase now.',
    'Okay. My approval is granted. We need time to build the feature. I sign this off now.',
])
def test_controller_clear_consent_advances_and_retains_exact_excerpt(root, text):
    controller, state = setup(root)
    state = submit(controller, state)
    envelope = decision(state, text=text)
    assert envelope['choice'] == 'approved'
    accepted = decide(controller, state, envelope)
    assert w.current(accepted)['decision'] == 'approved'
    assert accepted['decisions'][envelope['event_id']]['provenance']['excerpt'] == text
    advanced = controller.apply('advance', state['run'], expected_revision=accepted['revision'], phase='design')
    assert w.current(advanced)['phase'] == 'design'
    assert advanced['decisions'][envelope['event_id']]['provenance']['excerpt'] == text
    assert controller.report()['decisions'][envelope['event_id']]['provenance']['excerpt'] == text


@pytest.fixture
def interpreters(root, monkeypatch):
    import os
    selected, execution = root / 'selected', root / 'execution'
    selected.mkdir(); execution.mkdir()
    for suffix in [PYTHON_NAME, 'bin/' + PYTHON_NAME]:
        trusted, foreign = selected / suffix, execution / suffix
        trusted.parent.mkdir(exist_ok=True); foreign.parent.mkdir(exist_ok=True)
        trusted.symlink_to(sys.executable)
        foreign.write_text("#!/bin/sh\nprintf 'foreign-interpreter\\n'\n")
        foreign.chmod(0o700)
    monkeypatch.chdir(selected)
    return selected, execution, os.environ['PATH']


def interpreter_event(interpreters, mode, tail, monkeypatch):
    import os
    selected, execution, search = interpreters
    executable = './' + PYTHON_NAME if mode == 'relative-executable' else PYTHON_NAME
    if mode != 'relative-executable':
        monkeypatch.setenv('PATH', ('bin' if mode == 'relative-PATH' else '') + os.pathsep + search)
    event = command(selected, executable, str(Path(flow.__file__).with_name('tp.py')), *tail)
    event['tool_input']['workdir'] = str(execution)
    return event


@pytest.mark.skipif(os.name != 'nt', reason='Windows executable search policy')
@pytest.mark.parametrize('exclude_current_directory', [False, True])
def test_windows_interpreter_search_respects_current_directory_policy(interpreters, monkeypatch, exclude_current_directory):
    selected, execution, _ = interpreters
    monkeypatch.setenv('PATH', str(selected / 'bin'))
    if exclude_current_directory:
        monkeypatch.setenv('NoDefaultCurrentDirectoryInExePath', '1')
    else:
        monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)
    expected = Path(sys.executable).resolve() if exclude_current_directory else execution / PYTHON_NAME
    assert runtime_command.interpreter(PYTHON_NAME, execution) == expected


@pytest.mark.parametrize('mode', ['relative-executable', 'relative-PATH', 'empty-PATH'])
@pytest.mark.parametrize('route', ['activate', 'diagnostic'])
@pytest.mark.parametrize('lifecycle', ['bootstrap', 'active', 'sealed'])
def test_exact_argv_foreign_interpreter_refused_in_execution_directory(interpreters, mode, route, lifecycle, monkeypatch):
    import subprocess
    selected, execution, _ = interpreters
    if lifecycle == 'bootstrap':
        controller = h.Controller(selected, 'root', h.installed_adapter('codex'))
        local.Harness(selected, 'root').select('tp-go', 'user/request', controller.report())
    else:
        controller, state = setup(selected)
        if lifecycle == 'sealed':
            submit(controller, state)
    tail = ['flow', 'activate', '--workspace', str(selected)] if route == 'activate' else ['version', '--verify']
    event = interpreter_event(interpreters, mode, tail, monkeypatch)
    words = local.runtime_words(event)
    assert runtime_command.installed(words, selected)
    assert not runtime_command.installed(words, execution)
    if os.name != 'nt':  # The foreign executable fixture is a POSIX shell script.
        actual = subprocess.run(words, cwd=execution, capture_output=True, text=True, check=True)
        assert actual.stdout.strip() == 'foreign-interpreter'
    with pytest.raises(w.Refusal, match='interpreter mismatch'):
        flow.hook(event, governor=controller)
    if lifecycle != 'bootstrap':
        # Direct Controller admission must not fall back to opaque shell permission.
        with pytest.raises(w.Refusal, match='interpreter mismatch'):
            controller.guard(event, state['run'])
    assert not controller.adapter.control_action(event, controller.report())
    assert not local.Harness(selected, 'root').bootstrap_command(event)


@pytest.mark.parametrize('mode', ['relative-executable', 'relative-PATH', 'empty-PATH'])
@pytest.mark.parametrize('tail', [('version', '--verify'), ('help', '--md'),
                                  ('workspace', 'inspect'), ('workspace', 'bind'),
                                  ('workspace', 'recover'), ('flow', 'report'), ('flow', 'diagnose')])
def test_missing_binding_exemptions_resolve_actual_interpreter(interpreters, mode, tail, monkeypatch):
    selected, _, _ = interpreters
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(selected))
    args = list(tail)
    if tail[0] not in {'version', 'help'}:
        args += ['--workspace', str(selected)]
    if tail[0] == 'workspace' and tail[1] in {'bind', 'recover'}:
        args += ['--request-json', '{}']
    event = interpreter_event(interpreters, mode, args, monkeypatch)
    assert not flow._workspace_admin(event)
    assert not flow._onboarding_read(event)
    assert not flow._state_admin(event, selected)
    with pytest.raises(w.Refusal, match='interpreter mismatch'):
        flow.hook(event)
    assert not (selected / '.taskplane').exists()


@pytest.mark.parametrize('mode', ['relative-executable', 'relative-PATH', 'empty-PATH', 'absolute'])
@pytest.mark.parametrize('different_cwd', [False, True])
def test_valid_interpreter_identity_uses_execution_cwd(interpreters, mode, different_cwd, monkeypatch):
    selected, execution, _ = interpreters
    controller, state = setup(selected)
    submit(controller, state)
    if mode == 'absolute':
        event = runtime(selected, 'version', '--verify')
        event['tool_input']['workdir'] = str(execution if different_cwd else selected)
    else:
        event = interpreter_event(interpreters, mode, ['version', '--verify'], monkeypatch)
        if different_cwd:
            # A valid relative interpreter in the actual directory is supported too.
            for suffix in [PYTHON_NAME, 'bin/' + PYTHON_NAME]:
                (execution / suffix).unlink()
                (execution / suffix).symlink_to(sys.executable)
        else:
            event['tool_input']['workdir'] = str(selected)
    assert runtime_command.installed(local.runtime_words(event), execution if different_cwd else selected)
    flow.hook(event, governor=controller)
    controller.guard(event, state['run'])
    assert flow._onboarding_read(event)


def test_setup_handle_updates_hold_lock_across_concurrent_read_and_merge(root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, local as thread_local
    harness = local.Harness(root, 'root')
    harness.select('tp-go', 'user/first-start', {})
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    first_read, release_first, second_attempt, second_read = Event(), Event(), Event(), Event()
    thread = thread_local()
    original_read, original_lock = local.Harness.setup_handles, primitives.file_lock

    def snapshot(self):
        handles = original_read(self)
        if getattr(thread, 'label', None) == 'first':
            first_read.set()
            assert release_first.wait(10)
        elif getattr(thread, 'label', None) == 'second':
            second_read.set()
        return handles

    @contextmanager
    def observed_lock(path, **kwargs):
        if path == str(harness.path) and getattr(thread, 'label', None) == 'second':
            second_attempt.set()
        with original_lock(path, **kwargs):
            yield

    def register(label, handle):
        thread.label = label
        harness.observe_setup(runtime(root, 'version', '--verify', name='PostToolUse',
                                      tool_response={'session_id': handle}))

    monkeypatch.setattr(local.Harness, 'setup_handles', snapshot)
    monkeypatch.setattr(primitives, 'file_lock', observed_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register, 'first', 901)
        assert first_read.wait(10)
        second = pool.submit(register, 'second', 902)
        try:
            assert second_attempt.wait(10)
            assert not second_read.is_set(), 'Second snapshot escaped the first observation lock'
        finally:
            release_first.set()
        first.result(timeout=10); second.result(timeout=10)
    assert set(harness.setup_handles()) == {'901', '902'}
    for handle in [901, 902]:
        flow.hook(stdin(root, handle), governor=controller)
    controller.start(request())
    flow.hook(stdin(root, 901, name='PostToolUse', tool_response={'exit_code': 0}), governor=controller)
    assert not controller.adapter.can_seal(controller.report())
    flow.hook(stdin(root, 902, name='PostToolUse', tool_response={'exit_code': 0}), governor=controller)
    assert controller.adapter.can_seal(controller.report())


@pytest.mark.parametrize('terminal', [False, True])
def test_stale_pre_run_observation_rechecks_first_start_under_store_lock(root, monkeypatch, terminal):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    harness = local.Harness(root, 'root')
    harness.select('tp-go', 'user/first-start', {})
    if terminal:
        flow.hook(runtime(root, 'version', '--verify', name='PostToolUse',
                          tool_response={'session_id': 903}), governor=controller)
        event = stdin(root, 903, name='PostToolUse', tool_response={'exit_code': 0})
    else:
        event = runtime(root, 'help', '--md', name='PostToolUse', tool_response={'session_id': 903})
    reported, resume = Event(), Event()
    original = flow.select_controller

    def stale_report(*args, **kwargs):
        result = original(*args, **kwargs)
        assert not result[1].get('run')
        reported.set()
        assert resume.wait(10)
        return result

    monkeypatch.setattr(flow, 'select_controller', stale_report)
    with ThreadPoolExecutor(max_workers=1) as pool:
        observation = pool.submit(flow.hook, event, governor=controller)
        try:
            assert reported.wait(10)
            state = controller.start(request())
        finally:
            resume.set()
        observation.result(timeout=10)
    record = controller.report()['observed_handles']['903']
    assert record['state'] == ('completed' if terminal else 'running')
    assert record['visit'] == w.current(state)['id'] and record['revision'] == state['revision']
    assert record['control'] and record['worker_id'] is None
    assert harness.read()['setup_handles'] == {}
    assert controller.adapter.can_seal(controller.report()) is terminal


def test_concurrent_terminal_setup_observations_preserve_each_completion(root):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    controller = h.Controller(root, 'root', h.installed_adapter('codex'))
    harness = local.Harness(root, 'root')
    harness.select('tp-go', 'user/first-start', {})
    for handle in [904, 905]:
        flow.hook(runtime(root, 'version', '--verify', name='PostToolUse',
                          tool_response={'session_id': handle}), governor=controller)
    ready = Barrier(2)

    def complete(handle):
        ready.wait(timeout=10)
        flow.hook(stdin(root, handle, name='PostToolUse', tool_response={'exit_code': 0}), governor=controller)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(complete, [904, 905]))
    assert {key: record['state'] for key, record in harness.setup_handles().items()} == {
        '904': 'completed', '905': 'completed'}
    state = controller.start(request())
    assert controller.adapter.can_seal(state)
    for handle in [904, 905]:
        with pytest.raises(w.Refusal):
            flow.hook(stdin(root, handle), governor=controller)


def _assert_unclassified_response_preserves_pending_checkpoint(root, text):
    controller, state = setup(root)
    state = submit(controller, state)
    before = controller._path().read_bytes()
    envelope = decision(state, text=text)
    assert envelope['choice'] is None
    envelope['choice'] = 'approved'  # An asserted label cannot supply missing consent.
    with pytest.raises(w.Refusal, match='unclear') as refusal:
        decide(controller, state, envelope)
    assert refusal.value.reason == 'invalid_evidence'
    assert controller._path().read_bytes() == before
    pending = controller.report()
    assert pending['revision'] == state['revision']
    assert pending['decisions'] == {}
    assert w.current(pending)['decision'] == 'awaiting_human_approval'
    with pytest.raises(w.Refusal) as refusal:
        controller.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    assert refusal.value.reason == 'approval_required'
    assert controller._path().read_bytes() == before
    reloaded = h.Controller(root, 'root', h.installed_adapter('codex')).report()
    assert reloaded['revision'] == state['revision']
    assert reloaded['decisions'] == {}
    assert w.current(reloaded)['phase'] == 'product'
    assert w.current(reloaded)['decision'] == 'awaiting_human_approval'
    assert controller._path().read_bytes() == before


@pytest.mark.parametrize('clause', [
    "I'll need more time to deliberate", 'I’ll need more time to deliberate',
    'I need 5 minutes to think it over', 'We need a day to think it over',
    'The reviewers need more time to deliberate',
    'I will need more time to deliberate', 'I need five minutes to think it over',
    'The reviewer needs more time to deliberate',
    'The release council needs another week for contemplation',
])
@pytest.mark.parametrize('template', [
    'Okay. {}.', 'LGTM; “{}”. Approved.', '{}.\nOkay.',
])
def test_controller_request_form_never_bypasses_complete_response(root, clause, template):
    _assert_unclassified_response_preserves_pending_checkpoint(root, template.format(clause))


@pytest.mark.parametrize('residue', [
    'The amber lantern must glow', 'Quux', 'Zeta owns the final call', '△',
])
@pytest.mark.parametrize('template', [
    'Approved, {}!', 'Okay. `{}`.', 'Approved.\n> {}.',
    'Approved.\n```\n{}\n```', '{}. Looks good, proceed.',
    'Okay. We will publish tomorrow. {}. I approve this phase now.',
])
def test_controller_unknown_residue_requires_no_negative_keyword(root, residue, template):
    _assert_unclassified_response_preserves_pending_checkpoint(root, template.format(residue))


@pytest.mark.parametrize('request_text', [
    "I'll need 5 minutes", 'We need a day', 'The reviewers need more time',
    'The release council needs another week',
])
@pytest.mark.parametrize('template', [
    'Okay. {} to implement the plan; and to deliberate.',
    'Go ahead. "{} to deliberate\nand to implement the plan". Approved.',
    'Approved. We need time to implement the plan. {} to think it over.',
])
def test_controller_mixed_work_cannot_exempt_unknown_request(root, request_text, template):
    _assert_unclassified_response_preserves_pending_checkpoint(root, template.format(request_text))


@pytest.mark.parametrize('clause', [
    'All required checks passed', 'The results are ready',
    'Results clearly show over 50% reduction', 'We will implement the plan',
    'We need time to implement the plan and publish the release',
    'I sign this off now',
])
@pytest.mark.parametrize('template', [
    'Looks good, proceed. {}.', 'Okay; “{}”. I approve this phase now.',
])
def test_controller_complete_clear_work_and_explanation_retain_provenance(root, clause, template):
    text = template.format(clause)
    controller, state = setup(root)
    state = submit(controller, state)
    envelope = decision(state, text=text)
    assert envelope['choice'] == 'approved'
    accepted = decide(controller, state, envelope)
    assert w.current(accepted)['decision'] == 'approved'
    assert accepted['decisions'][envelope['event_id']]['provenance']['excerpt'] == text
    advanced = controller.apply('advance', state['run'], expected_revision=accepted['revision'], phase='design')
    assert w.current(advanced)['phase'] == 'design'
    assert advanced['decisions'][envelope['event_id']]['provenance']['excerpt'] == text
    reloaded = h.Controller(root, 'root', h.installed_adapter('codex')).report()
    assert w.current(reloaded)['phase'] == 'design'
    assert reloaded['decisions'][envelope['event_id']]['provenance']['excerpt'] == text
