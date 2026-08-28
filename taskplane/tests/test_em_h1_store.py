"""Focused adversarial proofs for H1-C durable state and read-only safety."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest import mock

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
        try:
            tp.atomic_write_json(str(target), {"generation": 3})
        except OSError as exc:
            assert "disk lost" in str(exc)
        else:  # pragma: no cover - the durability boundary must fail closed
            raise AssertionError("write acknowledged before the data fsync")
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
        try:
            tp.atomic_write_json(str(target), {"generation": 3})
        except OSError as exc:
            assert "metadata lost" in str(exc)
        else:  # pragma: no cover - replacement is not durable without this
            raise AssertionError("write acknowledged before directory fsync")
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 3}


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
        try:
            tp.migrate_store(str(workspace))
        except OSError as exc:
            assert "interrupted" in str(exc)
        else:  # pragma: no cover - partial migration must not be acknowledged
            raise AssertionError("partial migration was acknowledged")

    external = Path(tp.external_store_root(str(workspace))) / "knowledge"
    assert not external.exists()
    assert tp.kb_root(str(workspace)) == str(legacy)
    assert (legacy / "history.json").read_text(encoding="utf-8") == "history"

    # A partial final directory left by an older cross-filesystem move is not
    # authoritative. Retry quarantines it and publishes one verified tree.
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


def test_h30_readonly_contract_refuses_opaque_mutating_launchers(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    launchers = (
        "make test",
        "pytest -q",
        "tox -e py",
        "npm test",
        "./scripts/repository-check --review",
    )
    for command in launchers:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "can't be screened" in reason

    for command in ("rg -n TODO src", "git diff --stat", "cat README.md"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is True, (command, reason)
