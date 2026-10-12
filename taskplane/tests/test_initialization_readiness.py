"""Disposable hook fixtures verify contracts, never live host initialization."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import flow, runtime_command, workflow_local as local
from taskplane.context import Store, encode
from taskplane.context_views import summary
from taskplane.tests.test_context_cli import command, create
from taskplane.tests.test_workflow_local import setup
from taskplane.tests.test_worker_runtime import parent_pair


def test_saved_run_and_single_hook_do_not_prove_initialization(tmp_path):
    controller, state = setup(tmp_path)
    harness = local.Harness(tmp_path, 'root')
    before = controller._path().read_bytes()
    first = harness.readiness(state)
    assert first['status'] == 'initialization_pending' and first['run_created']
    assert first['parent_hook']['status'] == 'missing'
    assert datetime.fromisoformat(first['observed_at']).tzinfo is not None
    harness.update(hook_observed=True)
    assert harness.readiness(state)['status'] == 'initialization_pending'
    assert 'saved, but native initialization is missing' in harness.guidance(state)
    assert controller._path().read_bytes() == before
    assert state['decisions'] == {} and state['revision'] == 0
    parent_pair(controller)
    ready = harness.readiness(controller.report())
    assert ready['status'] == 'active'
    assert ready['parent_hook']['status'] == 'ready'
    assert ready['parent_hook']['reference']


@pytest.mark.parametrize('defect', ['revision', 'workspace', 'runtime'])
def test_current_pair_required_without_changing_saved_state(tmp_path, defect):
    controller, _ = setup(tmp_path)
    parent_pair(controller)
    state = controller.report()
    if defect == 'revision':
        state['revision'] += 1
    elif defect == 'workspace':
        state['parent_hook_readiness']['workspace'] = str(tmp_path / 'foreign')
    else:
        state['parent_hook_readiness']['runtime']['root'] = '/foreign-runtime'
    before = deepcopy(state)
    result = local.Harness(tmp_path, 'root').readiness(state)
    assert result['status'] == 'initialization_stale'
    assert result['parent_hook']['missing']
    assert state == before


@pytest.mark.parametrize('terminal', ['finished', 'retired', 'cancellation'])
def test_closed_runs_never_report_active_or_pending(tmp_path, terminal):
    controller, _ = setup(tmp_path)
    parent_pair(controller)
    state = controller.report()
    state[terminal] = True if terminal == 'finished' else {'fixture': True}
    ready = local.Harness(tmp_path, 'root').readiness(state)
    assert ready['status'] == 'inactive' and ready['parent_hook'] is None


def test_actual_cli_start_and_reopen_show_pending_without_hooks(tmp_path):
    create(tmp_path)
    start, size = command(tmp_path, 'start', '--standalone', '--phase', 'product',
                          '--scope', '.taskplane/scope.json', '--request-reference', 'fixture/initialization')
    assert size < 16384
    assert start['initialization']['status'] == 'initialization_pending'
    assert start['errors']['blocking'] and 'do not dispatch' in start['next_action']
    reopened, _ = command(tmp_path, 'report')
    full, _ = command(tmp_path, 'report', '--full')
    assert reopened['run'] == start['run'] == full['workflow']['run']
    assert reopened['initialization']['status'] == full['harness']['status'] == 'initialization_pending'
    assert reopened['approval']['status'] == 'not_requested'
    assert full['workflow']['decisions'] == {} and full['workflow']['revision'] == 0


def test_compact_readiness_preserves_approval_and_bounds_presentation(tmp_path):
    _, state = setup(tmp_path)
    ready = local.Harness(tmp_path, 'root').readiness(state)
    ready['presentation'] = {'note': 'x' * 50000}
    payload = {'workflow': state, 'harness': ready}
    result = summary(Store(tmp_path), payload, 'report')
    assert len(encode(result)) < 16384
    assert result['initialization']['parent_hook']['status'] == 'missing'
    assert result['approval']['status'] == 'not_requested'
    assert Store(tmp_path).resolve(result['details']) == payload


def launcher_event(workspace, executable=None, script=None, flags=('-B',)):
    argv = [executable or sys.executable, *flags,
            str(script or Path(flow.__file__).with_name('tp.py')),
            'flow', 'report', '--workspace', str(workspace)]
    return {'hook_event_name': 'PreToolUse', 'cwd': str(workspace), 'session_id': 'root',
            'call_id': 'fixture-no-bytecode', 'tool_name': 'exec_command',
            'tool_input': {'cmd': shlex.join(argv)}}


def test_no_bytecode_launcher_admits_exact_pair(tmp_path):
    controller, _ = setup(tmp_path)
    event = launcher_event(tmp_path)
    assert runtime_command.installed(local.runtime_words(event), tmp_path)
    flow._hook(event, governor=controller)
    flow._hook({**event, 'hook_event_name': 'PostToolUse'}, governor=controller)
    assert local.Harness(tmp_path, 'root').readiness(controller.report())['status'] == 'active'


@pytest.mark.parametrize('defect', ['interpreter', 'script', 'extra_flag', 'duplicate_flag'])
def test_no_bytecode_launcher_keeps_exact_identity_checks(tmp_path, defect):
    args = {'executable': str(tmp_path / 'foreign-python')} if defect == 'interpreter' else (
        {'script': tmp_path / 'tp.py'} if defect == 'script' else
        {'flags': ('-B', '-I') if defect == 'extra_flag' else ('-B', '-B')})
    event = launcher_event(tmp_path, **args)
    words = local.runtime_words(event)
    assert not runtime_command.installed(words, tmp_path)
    if defect in {'interpreter', 'script'}:
        assert runtime_command.collision(words, tmp_path)
