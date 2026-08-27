"""R-0013 fresh-task bootstrap keeps hook evidence in the governed home."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import host_capabilities
import storage
import taskplane_lite
import tp as cli


ROOT = Path(__file__).resolve().parents[2]


def _manifest_command(path: Path, event: str) -> str:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest["hooks"][event][0]["hooks"][0]["command"]


def _write_locator(checkout: Path, home: Path, run_id: str) -> None:
    identity = storage.resolve_repository_identity(str(checkout))
    layout = storage.resolve_layout(
        identity, run_id=run_id, home=str(home))
    storage.write_workspace_locator(
        str(checkout), identity=identity, layout=layout, run_id=run_id)


def test_new_codex_task_writes_current_compatible_receipt_only_to_locator_bound_home(
        tmp_path):
    """Prove the generated launcher protocol; live-host proof follows merge."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    dedicated_home = tmp_path / "dedicated-home"
    default_user_home = tmp_path / "user-home"
    default_user_home.mkdir()
    _write_locator(checkout, dedicated_home, "run-bootstrap")

    with mock.patch.dict(
            os.environ,
            {"TASKPLANE_MANAGED_HOOK_POLICY": "supported"}), \
            mock.patch.object(cli, "_install_context",
                              return_value="personal"):
        report = cli._install_codex_hooks(str(checkout))
    assert report["ok"] is True
    launcher = checkout / ".taskplane" / "codex-hook.py"
    assert launcher.is_file()
    assert "Generated locally by taskplane onboarding" in \
        launcher.read_text(encoding="utf-8")

    native_manifest = ROOT / "hooks" / "hooks.json"
    bridge_manifest = checkout / ".codex" / "hooks.json"
    commands = {
        (hook_path, event_name): _manifest_command(manifest, event_name)
        for hook_path, manifest in (
            ("native", native_manifest), ("bridge", bridge_manifest))
        for event_name in ("SubagentStart", "SubagentStop")
    }
    assert all('.taskplane/codex-hook.py' in command
               for command in commands.values())

    stable_identity = {
        "session_id": "fresh-codex-task",
        "turn_id": "turn-bootstrap",
        "agent_id": "child-bootstrap",
        "agent_type": "taskplane-worker",
        "cwd": str(checkout),
    }
    environment = {
        **os.environ,
        "HOME": str(default_user_home),
        "PLUGIN_ROOT": str(ROOT),
        "CODEX_THREAD_ID": "fresh-codex-task",
    }
    environment.pop("TASKPLANE_HOME", None)

    for event_name in ("SubagentStart", "SubagentStop"):
        event = {**stable_identity, "hook_event_name": event_name}
        for hook_path in ("native", "bridge"):
            result = subprocess.run(
                commands[(hook_path, event_name)], cwd=checkout, shell=True,
                input=json.dumps(event), text=True, capture_output=True,
                env=environment, encoding="utf-8", errors="replace")
            assert result.returncode == 0, result.stderr

    receipts = {}
    for hook_path in ("native", "bridge"):
        receipt_path = dedicated_home / "host-receipts" / f"{hook_path}.json"
        assert receipt_path.is_file()
        receipts[hook_path] = receipt_path.read_bytes()
        receipt = json.loads(receipts[hook_path])
        assert receipt["schema"] == host_capabilities.RUNTIME_RECEIPT_SCHEMA
        assert receipt["hook_path"] == hook_path
        assert receipt["event_name"] == "SubagentStart"
    observations = host_capabilities.runtime_hook_observations(
        str(dedicated_home), session_id="fresh-codex-task",
        workspace=str(checkout))
    assert observations["native_plugin_hooks_loaded"].status == "supported"
    assert observations["repository_bridge_loaded"].status == "supported"
    assert observations["managed_policy_permission"].status == "supported"
    assert observations["stable_event_identity"].status == "supported"

    journal = json.loads(Path(taskplane_lite.hook_claim_journal_path(
        str(checkout))).read_text(encoding="utf-8"))
    assert len(journal["claims"]) == 2
    assert {row["hook_path"] for row in journal["claims"]} == {"native"}
    assert all(row["status"] == "completed" for row in journal["claims"])
    assert not (default_user_home / ".taskplane" / "host-receipts").exists()

    conflicting = {**environment, "TASKPLANE_HOME": str(
        default_user_home / ".taskplane")}
    rejected = subprocess.run(
        commands[("native", "SubagentStart")], cwd=checkout, shell=True,
        input=json.dumps({**stable_identity,
                          "hook_event_name": "SubagentStart",
                          "session_id": "conflicting-task"}),
        text=True, capture_output=True, env=conflicting,
        encoding="utf-8", errors="replace")
    assert rejected.returncode != 0
    assert {
        hook_path: (dedicated_home / "host-receipts" /
                    f"{hook_path}.json").read_bytes()
        for hook_path in ("native", "bridge")
    } == receipts
    assert not (default_user_home / ".taskplane" / "host-receipts").exists()


def test_missing_locator_fails_closed_for_both_hooks_and_host_native_bootstrap(
        tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    default_user_home = tmp_path / "user-home"
    default_user_home.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "missing-locator",
        "tool_use_id": "missing-locator-call",
        "cwd": str(checkout),
    }
    environment = {
        **os.environ,
        "HOME": str(default_user_home),
        "PLUGIN_ROOT": str(ROOT),
        "TASKPLANE_WORKSPACE": str(checkout),
    }
    environment.pop("TASKPLANE_HOME", None)

    for manifest in (ROOT / "hooks" / "hooks.json",
                     ROOT / ".codex" / "hooks.json"):
        result = subprocess.run(
            _manifest_command(manifest, "PreToolUse"), cwd=checkout,
            shell=True, input=json.dumps(event), text=True,
            capture_output=True, env=environment,
            encoding="utf-8", errors="replace")
        assert result.returncode != 0

    host_native = subprocess.run(
        ["python3", str(ROOT / "hooks" / "host_native_runtime.py"),
         "check", "--host", "codex"], cwd=checkout, text=True,
        capture_output=True, env=environment,
        encoding="utf-8", errors="replace")
    assert host_native.returncode != 0
    assert not Path(taskplane_lite.hook_claim_journal_path(
        str(checkout))).exists()
    assert not (default_user_home / ".taskplane" / "host-receipts").exists()


def test_hook_home_binding_rejects_noncanonical_and_accepts_secure_default_home(
        tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    dedicated_home = tmp_path / "dedicated-home"
    _write_locator(checkout, dedicated_home, "run-binding")

    with pytest.raises(storage.StorageIdentityError, match="not canonical"):
        storage.bind_hook_taskplane_home(str(checkout), {
            "HOME": str(user_home),
            "TASKPLANE_HOME": str(dedicated_home / ".." /
                                  dedicated_home.name),
        })

    default_checkout = tmp_path / "default-checkout"
    default_checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=default_checkout, check=True)
    default_home = user_home / ".taskplane"
    _write_locator(default_checkout, default_home, "run-default")
    environment = {"HOME": str(user_home)}
    assert storage.bind_hook_taskplane_home(
        str(default_checkout), environment) == str(default_home)
    assert environment["TASKPLANE_HOME"] == str(default_home)

    locator_path = Path(storage._locator_path(str(default_checkout)))
    locator = json.loads(locator_path.read_text(encoding="utf-8"))
    locator["home"] = str(default_home / ".." / default_home.name)
    locator_path.write_text(json.dumps(locator) + "\n", encoding="utf-8")
    with pytest.raises(storage.StorageIdentityError, match="not canonical"):
        storage.bind_hook_taskplane_home(
            str(default_checkout), {"HOME": str(user_home)})
