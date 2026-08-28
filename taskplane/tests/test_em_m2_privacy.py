"""Focused adversarial evidence for M2 privacy defaults and disclosure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

import taskplane_lite as tp  # noqa: E402
import audit_projection  # noqa: E402


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _privacy_notice() -> str:
    return (ROOT / "PRIVACY.md").read_text(encoding="utf-8").lower()


def test_m10_privacy_notice_matches_actual_collection_and_network_paths() \
        -> None:
    notice = _privacy_notice()

    # The former categorical denials hid data Taskplane necessarily handles.
    for obsolete_claim in (
            "collects nothing", "no personal information collected",
            "shares data with no one", "no network requests initiated"):
        assert obsolete_claim not in notice

    # The published inventory covers the authority, repository, and command
    # records the runtime actually persists, as well as purpose and lifecycle.
    for disclosed_category in (
            "repository urls", "source/history", "diffs", "file paths",
            "commands", "requirements", "decisions", "debt",
            "actor or approval", "24-hour retention", "delete"):
        assert disclosed_category in notice
    assert "volodymyr demkiv" in notice
    assert "accountable" in notice

    runtime_source = (ROOT / "taskplane" / "taskplane_lite.py").read_text(
        encoding="utf-8")
    projection_source = (
        ROOT / "taskplane" / "audit_projection.py"
    ).read_text(encoding="utf-8")
    assert "_AUDIT_IDENTITY_FIELDS" in projection_source
    assert "_TRACE_ARCHIVE_RETENTION_SECONDS" in runtime_source
    assert tp.audit_record is audit_projection.audit_record


def test_m11_new_user_storage_defaults_private_despite_repository_setting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _repository(tmp_path / "repo-controlled-workspace")
    shared = workspace / ".taskplane-kb"
    shared.mkdir()
    # A repository can propose sharing, but cannot manufacture this local
    # user's consent by committing additional truthy fields.
    (shared / "config.json").write_text(json.dumps({
        "plan": "team", "store": "repo", "private": False,
        "sharing_confirmed": True,
    }), encoding="utf-8")
    private_home = tmp_path / "new-user-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(private_home))
    monkeypatch.delenv("TASKPLANE_STORE", raising=False)

    mode = tp.get_mode(str(workspace))
    assert mode == {
        "plan": "team",
        "store": "external",
        "private": True,
        "source": "shared-config-unconfirmed",
        "notice": mode["notice"],
    }
    assert "tp share set shared" in mode["notice"]
    assert Path(tp.store_root(str(workspace))).is_relative_to(private_home)
    assert not Path(tp.store_root(str(workspace))).is_relative_to(workspace)

    personal = tp.set_mode(str(workspace), plan="personal")
    assert (personal["store"], personal["private"]) == ("external", True)

    # Only a durable local action changes the destination to the repo store.
    confirmed = tp.set_mode(str(workspace), private=False)
    assert (confirmed["store"], confirmed["private"], confirmed["source"]) == (
        "repo", False, "shared-config")
    assert Path(tp.store_root(str(workspace))) == shared

    # Managed ephemeral hosts remain able to make their explicit environment
    # contract authoritative; this is not repository-controlled cold start.
    forced_workspace = _repository(tmp_path / "managed-workspace")
    monkeypatch.setenv("TASKPLANE_STORE", "repo")
    forced = tp.get_mode(str(forced_workspace))
    assert (forced["store"], forced["source"]) == ("repo", "env")


def test_m20_remote_acquisition_network_disclosure_is_accurate() -> None:
    notice = _privacy_notice()
    repository_source = (ROOT / "taskplane" / "repository.py").read_text(
        encoding="utf-8")

    # Bind the disclosure to the concrete remote acquisition implementation,
    # including both repository and pull-request paths.
    assert "def acquire_pr" in repository_source
    assert "def acquire_repository" in repository_source
    assert '"fetch"' in repository_source
    for disclosure in (
            "remote repository", "pull request", "local `git`",
            "github", "credentials", "request and connection metadata",
            "repository/pr/ref", "marketplace",
            "repository host's procedures"):
        assert disclosure in notice
    assert "these transfers are initiated by the action you request" in notice
    assert "no telemetry" in notice
