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
