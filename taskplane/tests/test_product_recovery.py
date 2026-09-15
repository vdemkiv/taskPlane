"""Product can author and finish without granting source or delivery authority."""

import argparse
import io
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from taskplane import tp as cli
import loop
import taskplane_lite as kernel


@pytest.fixture
def product(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith(("TASKPLANE_", "CLAUDE_", "CODEX_")):
            monkeypatch.delenv(name)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(loop, "_load_raw", lambda ws: None)
    scope = ["docs/**", "specs/**", "knowledge/**"]
    contract = kernel.build_contract("Product recovery fixture", read_only=True,
                                     scope=scope, write_allow=scope)
    contract["standalone_product"] = True
    kernel.activate(str(workspace), contract, snapshot=None)
    return str(workspace), contract


def command(*args):
    return shlex.join([sys.executable, str(Path(cli.__file__).resolve()), *args])


def screen(product, tool, payload):
    workspace, contract = product
    if tool == "exec_command":
        payload = {"shell": "/bin/sh", "login": False, "workdir": workspace, **payload}
    return cli._screen_contract_tool(contract, tool, payload, workspace)


def test_product_allows_documents_and_rejects_source_and_mixed_patch(product):
    workspace, _ = product
    document = "*** Begin Patch\n*** Add File: " + workspace + "/docs/spec.md\n+Draft\n*** End Patch"
    source = "*** Begin Patch\n*** Add File: " + workspace + "/src/app.py\n+change\n*** End Patch"
    assert screen(product, "apply_patch", {"command": document})[0]
    assert not screen(product, "apply_patch", {"command": source})[0]
    assert not screen(product, "apply_patch", {"command": document + "\n" + source})[0]


@pytest.mark.parametrize("args", [
    ("req", "new", "A complete requirement; with punctuation", "--acceptance", "Reject $(unsafe) as data"),
    ("req", "score", "R-0001"), ("req", "show", "R-0001"),
    ("graph", "link", "--req", "R-0001", "--kind", "planned", "--files", "taskplane/tp.py"),
    ("decision", "new", "proposed policy", "--status", "proposed"),
])
def test_required_product_controls_are_admitted(product, args):
    assert screen(product, "exec_command", {"cmd": command(*args)})[0]


@pytest.mark.parametrize("args", [
    ("loop", "approve", "--by", "invented"), ("new", "replacement"),
    ("clear", "--all"), ("budget", "--grant", "100"), ("screen",),
    ("req", "list", "--workspace", "/"), ("req", "list", "--work=/"),
    ("req", "list", "--workspace", ".", "--workspace", "/"),
    ("graph", "link", "--req", "R-0001", "--files", "src/**"),
    ("graph", "link", "--req", "R-0001", "--files", "src/**", "--kind", "realizes"),
    ("graph", "link", "--req", "R-0001", "--files", "src/**", "--kind", "planned", "--kind", "realizes"),
    ("decision", "new", "new policy", "--status", "accepted"),
    ("decision", "new", "new policy", "--supersedes", "D-0001"),
    ("decision", "new", "new policy"),
    ("graph", "impact", "--base=--output=src/app.py"),
    ("graph", "impact", "--ba=--output=src/app.py"),
])
def test_product_control_does_not_widen_authority(product, args):
    assert not screen(product, "exec_command", {"cmd": command(*args)})[0]


def test_product_control_rejects_shell_effects_and_checkout_interpreter(product):
    workspace, _ = product
    for suffix in ["; touch src/app.py", " > src/app.py", " $(touch src/app.py)"]:
        assert not screen(product, "exec_command", {"cmd": command("req", "list") + suffix})[0]
    counterfeit = Path(workspace) / "python3"
    counterfeit.write_text("not executed")
    assert not screen(product, "exec_command", {"cmd": shlex.join(
        [str(counterfeit), str(Path(cli.__file__).resolve()), "req", "list"])})[0]
    outside = Path(workspace).parent / "outside" / "python3"
    outside.parent.mkdir()
    outside.write_text("not the runtime interpreter")
    assert not screen(product, "exec_command", {"cmd": shlex.join(
        [str(outside), str(Path(cli.__file__).resolve()), "req", "list"])})[0]


@pytest.mark.parametrize("overrides", [
    {"shell": "/tmp/custom-shell"}, {"login": True}, {"workdir": "/"},
    {"env": {"BASH_ENV": "source.py"}},
])
def test_control_native_payload_cannot_change_workspace_or_shell(product, overrides):
    assert not screen(product, "exec_command", {"cmd": command("req", "list"), **overrides})[0]


def test_codex_projected_control_requires_matching_pending_native_call(product, monkeypatch):
    workspace, _ = product
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture")
    text = command("req", "list")
    monkeypatch.setattr(cli.host_caps, "pending_codex_tool_call", lambda _: None)
    assert not screen(product, "Bash", {"command": text})[0]
    monkeypatch.setattr(cli.host_caps, "pending_codex_tool_call", lambda _: {
        "cmd": text, "workdir": workspace, "shell": "/bin/sh", "login": False})
    assert screen(product, "Bash", {"command": text})[0]


def test_product_presentation_and_human_input_are_scoped(product):
    workspace, _ = product
    assert screen(product, "request_user_input_async", {"questions": []})[0]
    assert screen(product, "mcp__codex_app__open_in_codex", {
        "target": {"type": "file", "path": workspace + "/docs/spec.md"}})[0]
    assert not screen(product, "mcp__codex_app__open_in_codex", {
        "target": {"type": "browser", "url": "https://example.com"}})[0]
    assert not screen(product, "mcp__codex_app__open_in_codex", {
        "target": {"type": "file", "path": workspace + "/src/app.py"}})[0]


def test_finish_matches_only_own_standalone_contract(product, monkeypatch):
    workspace, contract = product
    request = command("clear", "--task-id", contract["task_id"])
    assert cli._product_finish_command(request, workspace, contract)
    assert not cli._product_finish_command(command("clear", "--task-id", "other"), workspace, contract)
    assert not cli._product_finish_command(request + " --all", workspace, contract)
    assert not cli._product_finish_command(request, workspace, {**contract, "worker_scoped": True})
    assert not cli._product_finish_command(request, workspace, {**contract, "_union": [contract]})
    monkeypatch.setattr(loop, "_load_raw", lambda ws: {"step": "execute"})
    assert not cli._product_finish_command(request, workspace, contract)


def test_finishing_retains_artifacts_and_does_not_record_acceptance(product):
    workspace, contract = product
    artifact = Path(workspace) / "docs" / "spec.md"
    artifact.parent.mkdir()
    artifact.write_text("DoD: pending review")
    args = argparse.Namespace(workspace=workspace, task_id=contract["task_id"],
                              all=False, slot=None, approved_by=None)
    assert cli.cmd_clear(args) == 0
    assert kernel.load_active(workspace) is None
    assert artifact.read_text() == "DoD: pending review"
    assert loop._load_raw(workspace) is None


def test_finish_rechecks_replaced_contract_at_execution(product):
    workspace, contract = product
    replacement = kernel.build_contract("replacement", scope=["src/**"])
    kernel.activate(workspace, replacement, snapshot=None)
    args = argparse.Namespace(workspace=workspace, task_id=contract["task_id"],
                              all=False, slot=None, approved_by=None)
    assert cli.cmd_clear(args) == 1
    assert kernel.load_active(workspace)["task_id"] == replacement["task_id"]


def test_product_entry_uses_default_native_tools_and_document_scope(product, monkeypatch, capsys):
    workspace, _ = product
    kernel.clear(workspace)
    monkeypatch.setattr(cli, "_enforcement_check", lambda *a, **k: ({"mode": "strict"}, None))
    args = argparse.Namespace(workspace=workspace, product=True, goal=["Product draft"],
                              available_tools="Read, apply_patch", scope=None, tools=None,
                              write_allow=None, tests=None, deny=None, budget=None)
    assert cli.cmd_new(args) == 0
    contract = kernel.load_active(workspace)
    assert contract["standalone_product"] is True
    assert "Read" in contract["allowed_tools"] and "apply_patch" in contract["allowed_tools"]
    assert contract["write_allow"] == ["docs/**", "specs/**", "knowledge/**"]
    assert "--task-id " + contract["task_id"] in capsys.readouterr().out


def test_product_entry_detects_missing_writer_before_activation(product, monkeypatch, capsys):
    workspace, _ = product
    kernel.clear(workspace)
    monkeypatch.setattr(cli, "_enforcement_check", lambda *a, **k: ({}, None))
    args = argparse.Namespace(workspace=workspace, product=True, goal=["Product draft"],
                              available_tools="Read", scope=None, tools=None,
                              write_allow=None, tests=None, deny=None, budget=None)
    assert cli.cmd_new(args) == 1
    assert kernel.load_active(workspace) is None
    assert json.loads(capsys.readouterr().out)["write_available"] is False


@pytest.mark.parametrize("flag", ["fetch", "target", "base", "allow_foreign_state"])
def test_product_entry_refuses_source_acquisition_before_effects(product, monkeypatch, flag):
    workspace, _ = product
    kernel.clear(workspace)
    monkeypatch.setattr(cli, "_enforcement_check", lambda *a, **k: pytest.fail("must refuse before effects"))
    args = argparse.Namespace(workspace=workspace, product=True, **{flag: "requested"})
    assert cli.cmd_new(args) == 1
    assert kernel.load_active(workspace) is None


@pytest.mark.parametrize("operation", ["finish", "question", "presentation"])
def test_product_exit_and_delivery_remain_reachable_with_corrupt_meter(product, monkeypatch, capsys, operation):
    workspace, contract = product
    if operation == "finish":
        tool, payload = "Bash", {"command": command("clear", "--task-id", contract["task_id"])}
    elif operation == "question":
        tool, payload = "request_user_input_async", {"questions": []}
    else:
        tool, payload = "mcp__codex_app__open_in_codex", {
            "target": {"type": "file", "path": workspace + "/docs/spec.md"}}
    monkeypatch.setattr(kernel, "orphan_status", lambda *args: (False, ""))
    monkeypatch.setattr(cli, "_meter_load", lambda *args, **kwargs: (_ for _ in ()).throw(cli.MeterCorrupt()))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "cwd": workspace, "tool_name": tool, "tool_input": payload})))
    assert cli.cmd_screen(argparse.Namespace()) == 0
    assert not capsys.readouterr().out.strip()  # abstain; host permission still applies


def test_product_still_refuses_source_when_meter_unreadable(product, monkeypatch, capsys):
    workspace, _ = product
    monkeypatch.setattr(kernel, "orphan_status", lambda *args: (False, ""))
    monkeypatch.setattr(cli, "_meter_load", lambda *args, **kwargs: (_ for _ in ()).throw(cli.MeterCorrupt()))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "cwd": workspace, "tool_name": "Write", "tool_input": {"file_path": workspace + "/src/app.py"}})))
    assert cli.cmd_screen(argparse.Namespace()) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
