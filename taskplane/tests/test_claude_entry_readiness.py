"""Real startup/CLI handoff and shared readiness, isolated from installed plugins."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import host_capabilities as caps
import taskplane_lite as kernel
import tp as cli
from taskplane.tests.test_entry_initialization import project  # noqa: F401

ROOT = Path(cli.__file__).resolve().parents[1]


@pytest.fixture
def host(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAUDE_", "CODEX_", "TASKPLANE_HOST_", "TASKPLANE_NATIVE_")):
            if name != "TASKPLANE_HOST_HOME":
                monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("host_name", ["claude", "codex"])
def test_setup_ready_is_not_hook_readiness(project, host, monkeypatch, host_name):
    key = "CODEX_THREAD_ID" if host_name == "codex" else "CLAUDE_SESSION_ID"
    monkeypatch.setenv(key, "entry-a")
    first = cli._initialize_entry(project)
    assert first["setup_ready"] is True
    assert first["ready"] is False
    assert first["host_capabilities"]["loaded_session"]["status"] != "supported"
    assert cli._enforcement_check(project)[1] is not None
    assert kernel.load_active(project) is None
    context = Path(kernel.kb_root(project), "context/product.md")
    before = context.read_bytes()

    # Run in the initialized project, as the host does. The repository running
    # pytest may have no TaskPlane setup, so its global hooks are inert.
    event = {"cwd": project, "session_id": "entry-a", "hook_event_name": "PreToolUse",
             "tool_use_id": "read-a", "tool_name": "Read",
             "tool_input": {"file_path": str(context)}}
    result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"), "screen"],
        input=json.dumps(event), text=True, capture_output=True, cwd=project,
        env={**os.environ, "TASKPLANE_HOOK_PATH": "native"})
    assert result.returncode == 0, result.stderr
    fresh = cli._initialize_entry(project)
    assert fresh["ready"] is True, fresh["host_capabilities"]
    assert fresh["initialization"]["repairs"] == []
    assert cli._enforcement_check(project)[1] is None
    assert context.read_bytes() == before
    assert fresh["engine"]["path"] == str(ROOT / "taskplane/tp.py")

    # A receipt from an old installed engine cannot vouch for updated code.
    caps.record_runtime_hook_receipt(kernel.store_home(project), hook_path="native",
        event=event, native_home=os.environ["TASKPLANE_HOST_HOME"],
        engine_fingerprint="old-installed-engine")
    changed = cli._onboard_report(project)
    assert changed["ready"] is False
    native = changed["host_capabilities"]["loaded_session"]["native"]
    assert native["status"] == "changed"
    assert "loaded hook engine differs" in native["reason"]
    assert cli._enforcement_check(project)[1] is not None

    monkeypatch.setenv(key, "entry-b")
    other = cli._onboard_report(project)
    assert other["ready"] is False
    assert cli._enforcement_check(project)[1] is not None


@pytest.mark.parametrize("code_version", [None, "2.1.0"])
def test_claude_recovery_distinguishes_code_from_session_identity(
        project, host, monkeypatch, code_version):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "cloud-or-code")
    if code_version:
        monkeypatch.setenv("CLAUDE_CODE_VERSION", code_version)
    _, refusal = cli._enforcement_check(project)
    assert refusal["diagnostics"]["session_identity_available"] is True
    assert refusal["diagnostics"]["host"] == "claude"
    recovery = " ".join(refusal["recovery"])
    if code_version:
        assert "In Claude Code" in recovery and "run /reload-plugins" in recovery
    else:
        assert "Claude/Cowork" in recovery and "if available" in recovery
        assert "run /reload-plugins" not in recovery
    assert "same run and pinned scope" in recovery


def test_claude_startup_exports_identity_to_subsequent_shell(project, host, tmp_path):
    envfile = tmp_path / "claude-session.env"
    envfile.write_text("export PRESERVE_ME=yes\n")
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_ENV_FILE": str(envfile)}
    event = {"cwd": project, "session_id": "claude-startup-a", "source": "startup",
             "hook_event_name": "SessionStart"}
    manifest = json.loads((ROOT / "hooks/hooks.json").read_text())
    # Both installed Claude startup entry points execute the same handoff.
    for row in manifest["hooks"]["SessionStart"]:
        for hook in row["hooks"]:
            for _ in range(2):
                result = subprocess.run(hook["command"], shell=True, cwd=project, env=env,
                    input=json.dumps(event), text=True, capture_output=True)
                assert result.returncode == 0, result.stderr
    saved = envfile.read_text()
    assert saved.count("export CLAUDE_SESSION_ID=") == 1
    assert "export PRESERVE_ME=yes" in saved
    # Emulate the host sourcing its env file for the subsequent Bash command.
    command = ". " + shlex.quote(str(envfile)) + "\n" + shlex.join([
        sys.executable, str(ROOT / "taskplane/tp.py"), "onboard", "--initialize",
        "--json", "--available-tools", "Read,Write,Grep,Glob", "--workspace", project])
    result = subprocess.run(command, shell=True, cwd=project, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 2, result.stderr + result.stdout
    first = json.loads(result.stdout)
    assert first["setup_ready"] is True and first["ready"] is False
    # Once setup exists, the next host tool event establishes live enforcement.
    screen_event = {"cwd": project, "session_id": event["session_id"],
        "hook_event_name": "PreToolUse", "tool_use_id": "onboard-recheck",
        "tool_name": "Bash", "tool_input": {"command": command}}
    hook = manifest["hooks"]["PreToolUse"][0]["hooks"][0]
    observed = subprocess.run(hook["command"], shell=True, cwd=project, env=env,
        input=json.dumps(screen_event), text=True, capture_output=True)
    assert observed.returncode == 0, observed.stderr
    result = subprocess.run(command, shell=True, cwd=project, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["ready"] is True
    assert report["host"] == "claude"
    assert report["host_capabilities"]["effective_path"]["value"] == "native_effective"


def test_direct_startup_call_does_not_export_identity_or_receipt(project, host, tmp_path):
    envfile = tmp_path / "claude-session.env"
    event = {"cwd": project, "session_id": "untrusted", "source": "startup",
             "hook_event_name": "SessionStart"}
    result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"), "context"],
        input=json.dumps(event), text=True, capture_output=True, cwd=project,
        env={**os.environ, "CLAUDE_ENV_FILE": str(envfile)})
    assert result.returncode == 0, result.stderr
    assert not envfile.exists()
    assert not caps.runtime_hook_observations(kernel.store_home(project),
        session_id="untrusted", workspace=project,
        native_home=os.environ["TASKPLANE_HOST_HOME"])


def test_session_export_shell_quotes_host_identity(host, tmp_path, monkeypatch):
    envfile = tmp_path / "claude-session.env"
    marker = tmp_path / "must-not-exist"
    session = f"x'; touch {marker}; #"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(envfile))
    cli._persist_claude_hook_session({"hook_event_name": "SessionStart", "session_id": session})
    result = subprocess.run(". " + shlex.quote(str(envfile)), shell=True)
    assert result.returncode == 0
    assert not marker.exists()


def test_stale_launcher_refreshes_with_live_native_hooks(project, host, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    launcher = Path(project, ".taskplane/codex-hook.py")
    launcher.parent.mkdir(exist_ok=True)
    current = cli._codex_runner_body(str(ROOT))
    launcher.write_text(current + "\n# previous generator\n")
    before = cli._codex_hooks_report(project)
    assert before["launcher_present"] and not before["launcher_ready"]
    report = cli._initialize_entry(project)
    assert "install_launcher" in report["initialization"]["repairs"]
    assert launcher.read_text() == current
    assert report["launcher"]["launcher_ready"]
    assert cli._initialize_entry(project)["initialization"]["repairs"] == []
