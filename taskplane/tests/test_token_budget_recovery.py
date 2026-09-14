"""Approved recovery through the real hook and CLI, using fixture native usage."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import taskplane_lite as kernel
import tp as cli
from taskplane.tests.native_meter_support import attach_native_counter

ENGINE = str(Path(cli.__file__).resolve())


@pytest.fixture
def governed(tmp_path, monkeypatch):
    for name in ("CODEX_THREAD_ID", "CLAUDE_SESSION_ID", "TASKPLANE_HOME", "TASKPLANE_TASK"):
        monkeypatch.delenv(name, raising=False)
    ws = str(tmp_path)
    contract = kernel.build_contract("review", read_only=True, max_actions=100)
    contract["budget"].update(max_tokens=1_000_000, token_usage_required=True)
    kernel.activate(ws, contract)
    return ws


def screen(ws, command, total=49_000_000, *, counter=True):
    event = {"cwd": ws, "tool_name": "Bash", "tool_input": {"command": command}}
    if counter:
        event = attach_native_counter(event, ws, total_tokens=total, label="approved-recovery")
    result = subprocess.run([sys.executable, ENGINE, "screen"], input=json.dumps(event),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def grant_command(ws, extra=10_000_000):
    return [sys.executable, ENGINE, "budget", "--grant-tokens", str(extra),
            "--approved-by", "user approval in current chat", "--workspace", ws]


def test_approved_tokens_restore_headroom_and_preserve_read_only_contract(governed):
    ws = governed
    before = kernel.load_active(ws)
    argv = grant_command(ws)
    # Screening records the host source but must not mutate the budget before
    # the native permission flow has allowed execution of the CLI command.
    assert screen(ws, shlex.join(argv)) == {}
    assert kernel.load_active(ws) == before
    result = subprocess.run(argv, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    after = kernel.load_active(ws)
    assert after["budget"]["max_tokens"] == 59_000_000
    after["budget"]["max_tokens"] = before["budget"]["max_tokens"]
    assert after == before
    # Scope remains enforced after approval. Allowed native reads can resume.
    assert not kernel.screen_tool(kernel.load_active(ws), "Write",
                                  {"file_path": str(Path(ws, "source.py"))}, ws)[0]
    event = attach_native_counter({"cwd": ws, "tool_name": "Read",
        "tool_input": {"file_path": str(Path(ws, "README.md"))}}, ws,
        total_tokens=49_000_001, label="resumed-read")
    result = subprocess.run([sys.executable, ENGINE, "screen"], input=json.dumps(event),
                            text=True, capture_output=True, check=True)
    assert not result.stdout.strip(), result.stdout
    trace = Path(kernel.tp_dir(ws), "trace.jsonl").read_text()
    approval = next(row for row in map(json.loads, trace.splitlines())
                    if row["event"] == "token_budget_granted")
    # Audit privacy intentionally hashes human attribution; the event persists.
    assert approval["approved_by"].startswith("anon:")


@pytest.mark.parametrize("args", [["--help"], ["budget", "--help"], ["clear", "--help"]])
def test_recovery_help_is_available_when_exhausted(governed, args):
    assert screen(governed, shlex.join([sys.executable, ENGINE, *args])) == {}


@pytest.mark.parametrize("mutation", ["bare", "compound", "zero", "fake", "empty", "spent"])
def test_recovery_exception_is_narrow(governed, mutation):
    args = grant_command(governed)
    if mutation == "bare":
        del args[5:7]
    elif mutation == "zero":
        args[4] = "0"
    elif mutation == "empty":
        args[6] = ""
    elif mutation == "fake":
        args[1] = str(Path(governed, "tp.py"))
    elif mutation == "spent":
        args += ["--spent", "1"]
    command = shlex.join(args) + (" && touch source.py" if mutation == "compound" else "")
    assert screen(governed, command)["decision"] == "block"
    assert kernel.load_active(governed)["budget"]["max_tokens"] == 1_000_000


def test_missing_current_counter_does_not_reuse_old_observation(governed):
    args = grant_command(governed)
    assert screen(governed, shlex.join(args)) == {}
    assert screen(governed, shlex.join(args), counter=False) == {}
    result = subprocess.run(args, text=True, capture_output=True)
    assert result.returncode == 1
    assert "counter unavailable" in result.stderr
    assert kernel.load_active(governed)["budget"]["max_tokens"] == 1_000_000


def test_grant_from_optional_generated_launcher(governed):
    path = Path(governed, ".taskplane/codex-hook.py")
    path.parent.mkdir(exist_ok=True)
    path.write_text(cli._codex_runner_body(str(Path(ENGINE).parents[1])))
    args = grant_command(governed)
    args[1] = str(path)
    assert screen(governed, shlex.join(args)) == {}
    assert subprocess.run(args, capture_output=True).returncode == 0


def test_existing_headroom_is_not_discarded(governed):
    args = grant_command(governed, extra=100)
    assert screen(governed, shlex.join(args), total=50) == {}
    assert subprocess.run(args, capture_output=True).returncode == 0
    assert kernel.load_active(governed)["budget"]["max_tokens"] == 1_000_100


@pytest.mark.parametrize("prefix", ["python", "python3.13", "python.exe", "py", "py -3", "py.exe -3"])
def test_platform_python_launchers_reach_approved_recovery(prefix):
    command = prefix + " " + shlex.join([ENGINE, "budget", "--grant-tokens", "100",
                                         "--approved-by", "user"])
    assert cli._is_release_command(command)
