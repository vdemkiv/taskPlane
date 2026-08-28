"""Focused adversarial proofs for H1-C durable state and read-only safety."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

from taskplane import taskplane_lite as tp


def test_h14_critical_write_fsyncs_before_acknowledgement(tmp_path: Path):
    target = tmp_path / "loop.json"
    target.write_text('{"generation": 1}\n', encoding="utf-8")
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(source: str, destination: str) -> None:
        events.append("replace")
        real_replace(source, destination)

    with mock.patch.object(tp.os, "fsync", side_effect=recording_fsync), \
            mock.patch.object(tp.os, "replace", side_effect=recording_replace):
        tp.atomic_write_json(str(target), {"generation": 2})

    assert events == ["fsync", "replace", "fsync"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}

    replace = mock.Mock()
    with mock.patch.object(tp.os, "fsync", side_effect=OSError("disk lost")), \
            mock.patch.object(tp.os, "replace", replace):
        with pytest.raises(OSError, match="disk lost"):
            tp.atomic_write_json(str(target), {"generation": 3})
    replace.assert_not_called()
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}

    fsync_calls = 0

    def fail_parent_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory metadata lost")
        real_fsync(fd)

    with mock.patch.object(tp.os, "fsync", side_effect=fail_parent_fsync):
        with pytest.raises(OSError, match="metadata lost"):
            tp.atomic_write_json(str(target), {"generation": 3})
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 3}


def test_h14_first_nested_write_durably_publishes_every_parent(tmp_path: Path):
    target = tmp_path / "runs" / "run-1" / "state" / "loop.json"
    synced: list[Path] = []
    replaced: list[Path] = []
    real_sync = tp._fsync_directory
    real_replace = os.replace

    def record_sync(path: str) -> None:
        synced.append(Path(path))
        real_sync(path)

    def record_replace(source: str, destination: str) -> None:
        replaced.append(Path(destination))
        real_replace(source, destination)

    with mock.patch.object(tp, "_fsync_directory", side_effect=record_sync), \
            mock.patch.object(tp.os, "replace", side_effect=record_replace):
        tp.atomic_write_json(str(target), {"phase": "plan"})

    runs = tmp_path / "runs"
    run = runs / "run-1"
    state = run / "state"
    assert synced == [runs, tmp_path, run, runs, state, run, state]
    assert replaced == [target]
    assert json.loads(target.read_text(encoding="utf-8")) == {"phase": "plan"}


def test_h14_nested_parent_durability_failure_is_not_acknowledged(
        tmp_path: Path):
    target = tmp_path / "runs" / "run-1" / "state" / "loop.json"
    failing_parent = tmp_path / "runs"
    calls: list[Path] = []
    real_sync = tp._fsync_directory

    def fail_second_parent(path: str) -> None:
        value = Path(path)
        calls.append(value)
        if value == failing_parent and calls.count(value) == 2:
            raise OSError("ancestor entry was not durable")
        real_sync(path)

    with mock.patch.object(tp, "_fsync_directory", side_effect=fail_second_parent):
        with pytest.raises(OSError, match="ancestor entry was not durable"):
            tp.atomic_write_json(str(target), {"phase": "plan"})

    assert not target.exists()
    assert not (tmp_path / "runs" / "run-1" / "state").exists()


def test_h14_nested_parent_symlink_fails_closed(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "runs"
    link.symlink_to(real, target_is_directory=True)
    target = link / "run-1" / "loop.json"

    with pytest.raises(tp.StateError, match="symlink"):
        tp.atomic_write_json(str(target), {"phase": "plan"})

    assert not target.exists()


def test_h15_interrupted_migration_keeps_legacy_authoritative(tmp_path: Path,
                                                              monkeypatch):
    workspace = tmp_path / "repo"
    legacy = workspace / "knowledge"
    legacy.mkdir(parents=True)
    (legacy / "decisions.json").write_text("complete", encoding="utf-8")
    (legacy / "history.json").write_text("history", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_STORE", "external")
    real_copytree = shutil.copytree

    def interrupted_copy(source: str, destination: str, **kwargs):
        Path(destination).mkdir(parents=True)
        shutil.copy2(Path(source) / "decisions.json",
                     Path(destination) / "decisions.json")
        raise OSError("cross-device copy interrupted")

    with mock.patch("shutil.copytree", side_effect=interrupted_copy):
        with pytest.raises(OSError, match="interrupted"):
            tp.migrate_store(str(workspace))

    external = Path(tp.external_store_root(str(workspace))) / "knowledge"
    assert not external.exists()
    assert tp.kb_root(str(workspace)) == str(legacy)
    assert (legacy / "history.json").read_text(encoding="utf-8") == "history"

    external.mkdir(parents=True)
    (external / "decisions.json").write_text("partial", encoding="utf-8")
    assert tp.kb_root(str(workspace)) == str(legacy)

    with mock.patch("shutil.copytree", wraps=real_copytree):
        result = tp.migrate_store(str(workspace))
    assert result["moved"] is True
    assert not legacy.exists()
    assert tp.kb_root(str(workspace)) == str(external)
    assert (external / "history.json").read_text(encoding="utf-8") == "history"
    assert list(external.parent.glob("knowledge.partial.*"))


@pytest.mark.parametrize("tool_name,input_key", [
    ("Bash", "command"),
    ("BashOutput", "command"),
    ("exec_command", "cmd"),
    ("functions.exec_command", "cmd"),
])
def test_h30_readonly_denies_every_command_tool(tool_name: str,
                                                input_key: str,
                                                tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    for command in (
        "cat README.md",
        "git --version",
        f"{tp._TP_CLI_PATH} loop status",
        f"{tp._TP_CLI_PATH} loop retro",
    ):
        allowed, reason = tp.screen_tool(
            contract, tool_name, {input_key: command}, str(tmp_path))
        assert allowed is False, (tool_name, command)
        assert "every shell command tool is blocked" in reason
        assert "host-native Read/Grep/Glob" in reason


def test_h30_caller_structured_fields_or_receipts_cannot_bypass_shell_denial(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    forged = {
        "cmd": "cat README.md",
        "argv": ["/bin/cat", "README.md"],
        "shell": False,
        "environment": {"PATH": "/usr/bin:/bin"},
        "executable_sha256": "0" * 64,
        "receipt": {"approved": True, "producer": "caller"},
    }

    allowed, reason = tp.screen_tool(
        contract, "exec_command", forged, str(tmp_path))

    assert allowed is False
    assert "every shell command tool is blocked" in reason


def test_h30_exported_builtin_impersonation_has_no_admitted_shell_surface(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract("review", read_only=True)
    monkeypatch.setenv(
        "BASH_FUNC_echo%%", "() { touch reviewed-source; }")

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "echo harmless"}, str(tmp_path))

    assert allowed is False
    assert "every shell command tool is blocked" in reason


def test_h30_path_reuse_and_content_mutation_never_become_command_authority(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract("review", read_only=True)
    tools = tmp_path.parent / "user-tools"
    tools.mkdir(exist_ok=True)
    reader = tools / "cat"
    reader.write_text("#!/bin/sh\nprintf safe\n", encoding="utf-8")
    reader.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")

    first = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))
    reader.write_text("#!/bin/sh\ntouch reviewed-source\n", encoding="utf-8")
    replacement = tools / "replacement"
    replacement.write_text("#!/bin/sh\ntouch another-file\n", encoding="utf-8")
    replacement.chmod(0o755)
    os.replace(replacement, reader)
    second = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))

    assert first[0] is False and second[0] is False
    assert first[1] == second[1]
    assert "every shell command tool is blocked" in first[1]


def test_h30_readonly_contract_has_explicit_native_tool_allowlist(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    assert set(tp.READONLY_NATIVE_READ_TOOLS) <= set(contract["allowed_tools"])
    assert set(tp.WRITE_TOOLS) <= set(contract["allowed_tools"])

    for tool_name, tool_input in (
        ("Read", {"file_path": "README.md"}),
        ("Grep", {"pattern": "contract", "path": "taskplane"}),
        ("Glob", {"pattern": "taskplane/*.py"}),
    ):
        allowed, reason = tp.screen_tool(
            contract, tool_name, tool_input, str(tmp_path))
        assert allowed is True, (tool_name, reason)


def test_h30_readonly_refuses_implicit_or_non_native_tool_admission(
        tmp_path: Path):
    implicit = tp.build_contract("review", read_only=True, tools=[])
    allowed, reason = tp.screen_tool(
        implicit, "Read", {"file_path": "README.md"}, str(tmp_path))
    assert allowed is False
    assert "no explicit allowed_tools" in reason

    broadened = tp.build_contract(
        "review", read_only=True, tools=["Read", "WebFetch"])
    allowed, reason = tp.screen_tool(
        broadened, "WebFetch", {"url": "https://example.invalid"},
        str(tmp_path))
    assert allowed is False
    assert "not an exact host-native" in reason


def test_h30_readonly_artifact_edits_are_tightly_scoped(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    artifact = tmp_path / ".em-review"
    artifact.mkdir()

    allowed, reason = tp.screen_tool(
        contract, "Write", {"file_path": ".em-review/result.json"},
        str(tmp_path))
    assert allowed is True, reason

    for path in ("taskplane/taskplane_lite.py", "../outside.json"):
        allowed, reason = tp.screen_tool(
            contract, "Edit", {"file_path": path}, str(tmp_path))
        assert allowed is False
        assert "reviewed source is protected" in reason


def test_h30_build_command_compatibility_is_unchanged(tmp_path: Path):
    contract = tp.build_contract("builder", scope=["src/**"])
    for command in (
        "FEATURE_FLAG=1 cat README.md",
        "python3 -c 'print(1)'",
        "env cat README.md",
        "rg --pre helper needle src",
    ):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is True, (command, reason)

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cp src/input ../outside"},
        str(tmp_path))
    assert allowed is False
    assert "escapes the workspace" in reason


def test_h30_loop_retro_is_not_even_defense_in_depth_readonly():
    assert tp._tp_readonly_argv_violation(["loop", "status"]) is None
    reason = tp._tp_readonly_argv_violation(["loop", "retro"])
    assert reason is not None
    assert "not on the read-only verb allowlist" in reason
