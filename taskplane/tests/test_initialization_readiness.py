"""Disposable hook fixtures verify contracts, never live host initialization."""
import os
from pathlib import Path
import shlex
import sys

import pytest

from taskplane import flow, runtime_command, workflow_local as local
from taskplane.tests.test_workflow_local import setup


@pytest.mark.skipif(os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"),
                    reason="Restoration requires no-follow directory descriptors.")
def test_restoration_completion_preserves_controller_without_accepting_phases(tmp_path, monkeypatch):
    from taskplane import workspace_binding as binding
    from taskplane.tests.test_workflow_local import displaced_database
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path)
    write = binding._write
    def interrupt(path, value, **kwargs):
        if value.get("schema") == "taskplane.initialization-recovery/v1" and value["status"] == "restored":
            raise OSError("fixture receipt completion stopped")
        return write(path, value, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(binding, "_write", interrupt)
        with pytest.raises(OSError, match="completion stopped"):
            c.recover_initialization(candidate.name, checksum, state["run"], state["revision"], "fixture/exact-retry")
    assert target.read_bytes() == raw
    assert c.recover_initialization(candidate.name, checksum, state["run"], state["revision"], "fixture/exact-retry")["status"] == "restored"
    restored = c.report()
    before = target.read_bytes()
    readiness = local.Harness(c.workspace, c.root).readiness(restored)
    assert readiness["status"] == "active"
    assert not restored["decisions"]
    assert restored["revision"] == state["revision"] and restored["decisions"] == state["decisions"]
    assert target.read_bytes() == before == candidate.read_bytes() == raw






@pytest.mark.parametrize('terminal', ['finished', 'retired', 'cancellation'])
def test_closed_runs_never_report_active_or_pending(tmp_path, terminal):
    controller, _ = setup(tmp_path)
    state = controller.report()
    state[terminal] = True if terminal == 'finished' else {'fixture': True}
    ready = local.Harness(tmp_path, 'root').readiness(state)
    assert ready['status'] == 'inactive'






def launcher_event(workspace, executable=None, script=None, flags=('-B',)):
    argv = [executable or sys.executable, *flags,
            str(script or Path(flow.__file__).with_name('tp.py')),
            'flow', 'report', '--workspace', str(workspace)]
    return {'hook_event_name': 'PreToolUse', 'cwd': str(workspace), 'session_id': 'root',
            'call_id': 'fixture-no-bytecode', 'tool_name': 'exec_command',
            'tool_input': {'cmd': shlex.join(argv)}}


def test_no_bytecode_launcher_keeps_real_runtime_identity(tmp_path):
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
