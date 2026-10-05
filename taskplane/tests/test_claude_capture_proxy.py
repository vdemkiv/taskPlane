"""The live capture proxy delegates known hooks without interpreting shell text."""
import io
import json
import sys
from pathlib import Path

import pytest

from scripts import verify_claude_workers as capture

ROOT = Path(__file__).resolve().parents[2]


def shipped_command(event):
    return json.loads((ROOT / 'hooks/hooks.json').read_text())['hooks'][event][0]['hooks'][0]['command']


@pytest.mark.parametrize('event,action', [
    ('SessionStart', 'context'), ('PreToolUse', 'screen'),
    ('PostToolUse', 'tool-observe'), ('SubagentStart', 'subagent-start'),
    ('SubagentStop', 'subagent-stop'), ('Stop', 'session-verify'),
    ('UserPromptSubmit', 'human-input'),
])
def test_proxy_recognizes_each_shipped_hook(event, action):
    assert capture.hook_action(event, shipped_command(event)) == action


@pytest.mark.parametrize('event,command', [
    ('SessionStart', shipped_command('SessionStart') + '; echo extra'),
    ('SessionStart', shipped_command('PreToolUse')),
    ('UnknownEvent', shipped_command('SessionStart')),
])
def test_proxy_refuses_unrecognized_commands(event, command):
    with pytest.raises(ValueError, match='Unsupported hook command'):
        capture.hook_action(event, command)


@pytest.mark.parametrize('root_variable', ['CLAUDE_PLUGIN_ROOT', 'PLUGIN_ROOT'])
def test_proxy_preserves_input_output_and_exit_status(tmp_path, monkeypatch, capsysbinary, root_variable):
    root = tmp_path.resolve() / 'plugin with spaces $HOME;echo'
    (root / 'taskplane').mkdir(parents=True)
    (root / 'taskplane/tp.py').write_text(
        'import sys\n'
        'assert sys.argv[1:] == ["context"]\n'
        'sys.stdout.buffer.write(sys.stdin.buffer.read())\n'
        'sys.stderr.buffer.write(b"delegate diagnostic\\n")\n'
        'sys.exit(7)\n')
    capture.save(root / '.claude-plugin/plugin.json', {'version': '0.0.0'})
    capture.save(root / 'hooks/hooks.json', {'hooks': {}})
    captures = tmp_path / 'captures'
    config = tmp_path.resolve() / 'proxy.json'
    original = shipped_command('SessionStart')
    capture.save(config, {'capture': str(captures), 'commands': [
        {'event': 'SessionStart', 'command': original}]})
    monkeypatch.setenv('CLAUDE_PLUGIN_ROOT', '')
    monkeypatch.delenv('PLUGIN_ROOT', raising=False)
    monkeypatch.setenv(root_variable, str(root))
    monkeypatch.setattr(capture.shutil, 'which', lambda name: sys.executable if name == 'python3' else None)
    raw = b'{"hook_event_name":"SessionStart","literal":"$(echo untouched)"}\n'
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(raw)))

    assert capture.proxy(config, 0) == 7
    output = capsysbinary.readouterr()
    assert output.out == raw
    assert output.err == b'delegate diagnostic\n'
    record, = [json.loads(path.read_text()) for path in captures.glob('*.json')]
    assert record['delegate_command'] == original
    assert record['delegate_argv'] == [sys.executable, str(root / 'taskplane/tp.py'), 'context']
    assert record['delegate_exit_code'] == 7
    assert record['runtime_unchanged'] is True
    assert record['input'] == json.loads(raw)


def test_proxy_blocks_missing_plugin_root(tmp_path, monkeypatch, capsys):
    config = tmp_path.resolve() / 'proxy.json'
    capture.save(config, {'commands': [
        {'event': 'SessionStart', 'command': shipped_command('SessionStart')}]})
    monkeypatch.delenv('CLAUDE_PLUGIN_ROOT', raising=False)
    monkeypatch.delenv('PLUGIN_ROOT', raising=False)
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(b'{}')))

    assert capture.proxy(config, 0) == 2
    assert json.loads(capsys.readouterr().out) == {
        'decision': 'block', 'reason': 'Taskplane plugin root unavailable'}
