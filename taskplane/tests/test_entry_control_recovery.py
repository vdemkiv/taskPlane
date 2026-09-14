"""Exercise the shared host screen, not only individual CLI handlers."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import obligations
import taskplane_lite as kernel
import tp as cli
from taskplane.tests.native_meter_support import attach_native_counter
from taskplane.tests.test_token_budget_recovery import governed  # noqa: F401

ENGINE = str(Path(cli.__file__).resolve())


def screen(workspace, tool, payload, *, tokens=10):
    event = attach_native_counter({"cwd": workspace, "tool_name": tool,
                                   "tool_input": payload}, workspace,
                                  total_tokens=tokens, label="entry-control")
    result = subprocess.run([sys.executable, ENGINE, "screen"], input=json.dumps(event),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


@pytest.mark.parametrize("args", [
    ["review", "option", "dynamic", "--run-id", "current"],
    ["review", "option", "static", "--run-id", "current"],
    ["review", "option", "dynamic-render", "--run-id", "current"],
    ["review", "evidence", "dynamic_validation", "failed", "--run-id", "current"],
    ["review", "sandbox", "--run-id", "current"],
    ["review", "signoff", "changes", "--by", "human", "--run-id", "current"],
    ["onboard", "--json"], ["summary"], ["loop", "resume"],
    ["loop", "next"], ["stage", "read-input", "--request", "input.json"],
    ["stage", "history", "--request", "input.json"],
    ["command", "show", "--request", "input.json"],
    ["repository", "status", "--run-id", "current"],
    ["graph", "impact", "--base", "HEAD~1"], ["req", "list"],
    ["lens", "list"], ["decision", "list"], ["target", "show"],
])
@pytest.mark.parametrize("tool,field", [("Bash", "command"), ("exec_command", "cmd")])
def test_all_entry_controls_reach_their_own_handlers(governed, args, tool, field):
    command = shlex.join([sys.executable, ENGINE, *args])
    assert screen(governed, tool, {field: command}) == {}
    assert kernel.load_active(governed)["read_only"] is True


def test_review_continuation_remains_metered(governed):
    command = shlex.join([sys.executable, ENGINE, "review", "option", "dynamic",
                          "--run-id", "current"])
    assert "TOKEN BUDGET" in screen(governed, "Bash", {"command": command},
                                    tokens=2_000_000)["reason"]


@pytest.mark.parametrize("tail", [
    "review option dynamic --run-id current; touch source.py",
    "review option dynamic --run-id current > source.py",
    "new replacement --read-only", "clear", "budget --grant-tokens 100",
    "onboard --json --out source.py", "onboard --workspace / --json",
    "onboard --workspace=/ --json", "screen", "subagent-start",
    "onboard --json --o source.py", "onboard --work=/ --json",
    "onboard --install-codex-hooks", "target tools --install",
])
def test_control_admission_preserves_the_boundary(governed, tail):
    command = shlex.join([sys.executable, ENGINE]) + " " + tail
    assert screen(governed, "Bash", {"command": command})["decision"] == "block"


def test_fake_engine_and_contract_denies_are_not_controls(governed):
    fake = Path(governed, "tp.py")
    fake.write_text("raise SystemExit('must not run')")
    assert screen(governed, "Bash", {"command": shlex.join([
        sys.executable, str(fake), "review", "option", "static", "--run-id", "x"])
        })["decision"] == "block"
    contract = kernel.load_active(governed)
    contract["coding"]["command_policy"]["deny"].append("review option")
    kernel.activate(governed, contract)
    command = shlex.join([sys.executable, ENGINE, "review", "option", "static", "--run-id", "x"])
    assert screen(governed, "Bash", {"command": command})["decision"] == "block"


def test_control_cannot_hide_a_forged_interpreter_or_exhausted_validation(governed):
    fake = Path(governed, "python3")
    fake.write_text("#!/bin/sh\ntouch source.py\n")
    fake.chmod(0o755)
    command = shlex.join([str(fake), ENGINE, "review", "option", "static", "--run-id", "x"])
    assert screen(governed, "Bash", {"command": command})["decision"] == "block"
    for tail in (["--", "echo", "collect"], ["echo", "--help"]):
        command = shlex.join([sys.executable, ENGINE, "review", "validate", "--run-id", "x", *tail])
        result = screen(governed, "Bash", {"command": command}, tokens=2_000_000)
        assert "TOKEN BUDGET" in result["reason"]


def test_controls_preserve_each_contract_in_a_union(governed):
    import copy
    first = kernel.load_active(governed)
    second = copy.deepcopy(first)
    second["task_id"] = "other"
    second["coding"]["command_policy"]["deny"].append("review option")
    union = {**first, "_union": [first, second]}
    command = shlex.join([sys.executable, ENGINE, "review", "option", "static", "--run-id", "x"])
    allowed, reason = cli._screen_contract_tool(union, "Bash", {"command": command}, governed)
    assert not allowed and "other" in reason


@pytest.mark.parametrize("tool", ["AskUserQuestion", "request_user_input",
                                  "request_user_input_async", "functions.request_user_input_async"])
def test_budget_cannot_block_asking_the_human(governed, tool):
    before = kernel.load_active(governed)
    assert screen(governed, tool, {"questions": [{"title": "Approve additional budget?"}]},
                  tokens=2_000_000) == {}
    assert kernel.load_active(governed) == before


@pytest.mark.parametrize("tool", ["mcp__codex_app__open_in_codex", "SendUserFile",
                                  "mcp__visualize__show_widget"])
def test_owed_delivery_is_reachable_but_is_not_automatic_acknowledgement(governed, tool):
    artifact = Path(governed, "dashboard.html")
    artifact.write_text("<div>Canonical dashboard</div>")
    contract = kernel.load_active(governed)
    obligations.issue(governed, "render_dashboard", detail="Review dashboard",
                      artifact=str(artifact), session=contract["task_id"], binding=True)
    payload = ({"target": {"type": "file", "path": str(artifact)}} if "open_in_codex" in tool
               else {"file_path": str(artifact)} if tool == "SendUserFile"
               else {"title": "Dashboard", "html": artifact.read_text()})
    assert screen(governed, tool, payload, tokens=2_000_000) == {}
    assert obligations.status(governed)["acknowledged"] == 0
    assert obligations.status(governed)["observed"] == 0
    artifact.write_text("Substituted bytes")
    assert screen(governed, tool, payload, tokens=2_000_000)["decision"] == "block"


def test_presentation_cannot_navigate_or_target_another_task(governed):
    for payload in ({"target": {"type": "browser", "url": "https://example.com"}},
                    {"target": {"type": "terminal"}},
                    {"target": {"type": "file", "path": "dashboard.html"}, "threadId": "other"}):
        assert screen(governed, "mcp__codex_app__open_in_codex", payload)["decision"] == "block"
