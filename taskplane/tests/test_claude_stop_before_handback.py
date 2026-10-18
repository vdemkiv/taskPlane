"""Claude 2.1.296 incident ordering, redacted report; no live-host certification."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from taskplane import claude_worker_observations as observations, flow, workflow as w, worker_runtime as workers
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_claude_worker_lifecycle import notification_record

requires_native_reader = pytest.mark.skipif(not observations.supported_reader(), reason='Requires native no-follow reads')


@pytest.mark.parametrize("reentry", [False, True])
@pytest.mark.parametrize("root_variable", ["CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"])
@pytest.mark.parametrize("defect", ["unset", "stale", "missing_entrypoint", "entrypoint_directory",
                                    "unreadable_entrypoint", "inaccessible_root",
                                    "missing_interpreter", "failed_interpreter", "failed_runtime"])
def test_literal_unavailable_runtime_keeps_stop_diagnostic_and_pretool_denied(
        tmp_path, reentry, root_variable, defect):
    hooks = json.loads((Path(__file__).resolve().parents[2] / "hooks/hooks.json").read_text())["hooks"]
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"}}
    root = tmp_path / "runtime with spaces"
    entry = root / "taskplane/tp.py"
    entry.parent.mkdir(parents=True)
    # A real file which cannot initialize. Interpreter failures are separately
    # injected below; no candidate runtime module is imported by these fixtures.
    entry.write_text("raise SystemExit(2)\n")
    env[root_variable] = str(root)
    if defect == "unset":
        env.pop(root_variable)
    elif defect == "stale":
        env[root_variable] = str(tmp_path / "nonexistent installed cache")
    elif defect == "missing_entrypoint":
        entry.unlink()
    elif defect == "entrypoint_directory":
        entry.unlink(); entry.mkdir()
    elif defect == "unreadable_entrypoint":
        entry.chmod(0)
    elif defect == "inaccessible_root":
        root.chmod(0)
    elif defect in ("missing_interpreter", "failed_interpreter"):
        binary = tmp_path / "bin"
        binary.mkdir()
        env["PATH"] = str(binary)
        if defect == "failed_interpreter":
            interpreter = binary / ("py.cmd" if os.name == "nt" else "python3")
            interpreter.write_text("@exit /b 2\n" if os.name == "nt" else "#!/bin/sh\nexit 2\n")
            interpreter.chmod(0o755)
    key = "commandWindows" if os.name == "nt" else "command"
    try:
        for event in ("Stop", "PreToolUse"):
            completed = subprocess.run(hooks[event][0]["hooks"][0][key], shell=True, env=env,
                input=json.dumps({"stop_hook_active": reentry}), capture_output=True, text=True, timeout=10)
            assert completed.returncode == 0, completed.stderr
            result = json.loads(completed.stdout)
            if event == "Stop":
                assert result.get("decision") != "block" and "diagnostic" in result["systemMessage"]
            else:
                assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
                assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        root.chmod(0o755)
        if entry.exists():
            entry.chmod(0o755 if entry.is_dir() else 0o644)


@pytest.mark.parametrize("root_variable", ["CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"])
def test_literal_available_runtime_preserves_its_structured_response(tmp_path, root_variable):
    hooks = json.loads((Path(__file__).resolve().parents[2] / "hooks/hooks.json").read_text())["hooks"]
    root = tmp_path / "runtime with spaces"
    entry = root / "taskplane/tp.py"
    entry.parent.mkdir(parents=True)
    entry.write_text('import json, sys\nprint(json.dumps({"runtime_action": sys.argv[1]}))\n')
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"}}
    env[root_variable] = str(root)
    # Keep a stale lower-priority installation from overriding the current one.
    if root_variable == "CLAUDE_PLUGIN_ROOT":
        env["PLUGIN_ROOT"] = str(tmp_path / "stale fallback")
    key = "commandWindows" if os.name == "nt" else "command"
    for event, action in (("Stop", "session-verify"), ("PreToolUse", "screen")):
        completed = subprocess.run(hooks[event][0]["hooks"][0][key], shell=True, env=env,
            input="{}", capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == {"runtime_action": action}


def test_archived_capture_does_not_silently_migrate_its_binding():
    fixture = json.loads((Path(__file__).parent / 'fixtures/claude/stop-before-handback-2.1.296.json').read_text())
    s, row, completed, peer, stop = (fixture[k] for k in ('state', 'row', 'notification', 'peer_handback', 'stop'))
    original = deepcopy(row)
    # Original capture uses revision-bound v1 proof. New execution cannot adopt
    # it; current ordinary-native chronology is exercised by delivered() below.
    assert workers._redirect_delivery(s, row, completed, peer, stop) is None
    assert row == original


def delivered(host, *, defect=None):
    # Native incident: ordinary final -> automatic stop -> handback admission ->
    # peer -> acknowledgment -> redirect. There is no post-ack textless stop.
    host.stop('Ordinary final text, distinct from the redacted handback report.')
    event = host.event('SubagentHandback', {'message': 'Redacted independently checked worker report.'})
    flow.hook(event)
    peer = host.peer(event['tool_input']['message'], variant='attachment')
    peer['version'] = '2.1.296'
    host.append(peer)
    flow.hook({**event, 'hook_event_name': 'PostToolUse',
               'tool_response': {'success': True, 'message': 'Report delivered to your caller.'}})
    note = host.redirect()
    note['version'] = '2.1.296' if defect != 'version' else '2.1.297'
    host.append(note)
    return event, peer, note


@requires_native_reader
def test_pre_handback_stop_reconciles_original_call_and_preserves_other_work(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready()
    event, peer, note = delivered(host)
    before = deepcopy(host.controller.report())
    result = host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    row = host.row()
    assert result['state'] == 'result_pending' and not result['result_accepted']
    assert row['handback_call'] == before['workers'][host.grant]['handback_call']
    assert row['handback']['stop_event'] == before['workers'][host.grant]['stop_observation']['event_id']
    assert 'textless' not in row['stop_observation']
    assert row['handback']['notification']['record_sha256'] == workers.digest(note)
    assert row['handback_reconciliation']['completion_sequence'] == 'claude.stop-before-handback/v1'
    assert any(item['kind'] == 'stop_before_handback_joined' for item in row['reconciliation_events'].values())
    after = host.controller.report()
    for key in ('decisions', 'visits', 'scope', 'task_results'):
        assert after.get(key) == before.get(key)
    # Later no-report after a resumed call is a different launch, not permission
    # to replace the proven original completion or release the original result.
    later = notification_record(row, host.result, observations.NO_REPORT_RESULT)
    later['sessionId'] = 'root'
    later['message']['content'] = later['message']['content'].replace(row['call_id'], 'different-resume-call')
    host.append(later)
    host.controller.worker(host.state['run'], 'reconcile-handback',
        revision=host.state['revision'], grant=host.grant)
    assert host.row()['handback']['notification']['record_sha256'] == workers.digest(note)
    host.accept()


@pytest.mark.parametrize('defect', ['version', 'missing_ack', 'runtime', 'foreign_stop', 'stop_path',
    'stop_after_ack', 'ack_after_completion', 'bad_digest', 'body', 'call', 'future', 'tampered_span'])
@requires_native_reader
def test_pre_handback_sequence_does_not_relax_identity_or_delivery(tmp_path, monkeypatch, defect):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready(); delivered(host, defect=defect)
    target = host.controller._path(); db = json.loads(target.read_text()); s = db['runs'][host.state['run']]
    row = s['workers'][host.grant]; report = row['handback_call']
    stops = [*row['stop_observations'], row['stop_observation']]
    stops += [v for v in s['unbound_worker_events'].values() if v.get('event') == 'SubagentStop']
    if defect == 'missing_ack': report.pop('acknowledgment')
    elif defect == 'runtime': report['runtime']['root'] = '/other-runtime'
    elif defect == 'foreign_stop':
        for stop in stops: stop['worker_id'] = 'foreign'
    elif defect == 'stop_path':
        for stop in stops: stop['agent_transcript_path'] = '/other-child.jsonl'
    elif defect == 'stop_after_ack':
        for stop in stops: stop['observed_at'] = workers.now()
    elif defect == 'ack_after_completion':
        report['observed_at'] = report['acknowledgment']['observed_at'] = workers.now()
    elif defect == 'bad_digest':
        for stop in stops: stop['body_digest'] = 'not-a-digest'
    elif defect == 'body': report['envelope']['body_digest'] = '0' * 64
    elif defect == 'call': report['envelope']['call_id'] = 'competing-call'
    elif defect == 'future': report['admitted_at'] = '2999-01-01T00:00:00+00:00'
    elif defect == 'tampered_span':
        host.root_hook()
        # Re-read the current cursor before changing its pinned native span.
        db = json.loads(target.read_text())
        host.parent.write_bytes(host.parent.read_bytes().replace(b'Redacted independently', b'Mutated independently'))
    host.controller._write(target, db); before = target.read_bytes()
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert target.read_bytes() == before


@requires_native_reader
def test_same_launch_competing_no_report_remains_conflict(tmp_path, monkeypatch):
    host = Interactive(tmp_path, monkeypatch)
    host.launch(); host.ready(); delivered(host)
    note = notification_record(host.row(), host.result, observations.NO_REPORT_RESULT)
    note['sessionId'] = 'root'; host.append(note)
    with pytest.raises(w.Refusal):
        host.controller.worker(host.state['run'], 'reconcile-handback',
            revision=host.state['revision'], grant=host.grant)
    assert not host.controller.report().get('task_results')
