"""R-0013 fresh-task bootstrap keeps hook evidence in the governed home."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import host_capabilities
import storage


ROOT = Path(__file__).resolve().parents[2]


def test_new_codex_task_writes_current_compatible_receipt_only_to_locator_bound_home(
        tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    dedicated_home = tmp_path / "dedicated-home"
    default_user_home = tmp_path / "user-home"
    default_user_home.mkdir()

    identity = storage.resolve_repository_identity(str(checkout))
    layout = storage.resolve_layout(
        identity, run_id="run-bootstrap", home=str(dedicated_home))
    storage.write_workspace_locator(
        str(checkout), identity=identity, layout=layout,
        run_id="run-bootstrap")

    manifests = [
        json.loads((ROOT / relative).read_text(encoding="utf-8"))
        for relative in ("hooks/hooks.json", ".codex/hooks.json")
    ]
    commands = [
        manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        for manifest in manifests
    ]
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "fresh-codex-task",
        "tool_use_id": "bootstrap-read",
        "tool_name": "Read",
        "tool_input": {"path": str(checkout / "README.md")},
        "cwd": str(checkout),
    }
    environment = {
        **os.environ,
        "HOME": str(default_user_home),
        "PLUGIN_ROOT": str(ROOT),
        "CODEX_THREAD_ID": "fresh-codex-task",
    }
    environment.pop("TASKPLANE_HOME", None)

    for command in commands:
        result = subprocess.run(
            command, cwd=checkout, shell=True, input=json.dumps(event),
            text=True, capture_output=True, env=environment)
        assert result.returncode == 0, result.stderr

    receipts = {}
    for hook_path in ("native", "bridge"):
        receipt_path = dedicated_home / "host-receipts" / f"{hook_path}.json"
        assert receipt_path.is_file()
        receipts[hook_path] = receipt_path.read_bytes()
        receipt = json.loads(receipts[hook_path])
        assert receipt["schema"] == host_capabilities.RUNTIME_RECEIPT_SCHEMA
        assert receipt["hook_path"] == hook_path
    observations = host_capabilities.runtime_hook_observations(
        str(dedicated_home), session_id="fresh-codex-task")
    assert observations["native_plugin_hooks_loaded"].status == "supported"
    assert observations["repository_bridge_loaded"].status == "supported"
    assert observations["managed_policy_permission"].status == "supported"
    assert observations["stable_event_identity"].status == "supported"
    assert not (default_user_home / ".taskplane" / "host-receipts").exists()

    conflicting = {**environment, "TASKPLANE_HOME": str(
        default_user_home / ".taskplane")}
    rejected = subprocess.run(
        commands[0], cwd=checkout, shell=True, input=json.dumps({
            **event, "session_id": "conflicting-task"}), text=True,
        capture_output=True, env=conflicting)
    assert rejected.returncode != 0
    assert {
        hook_path: (dedicated_home / "host-receipts" /
                    f"{hook_path}.json").read_bytes()
        for hook_path in ("native", "bridge")
    } == receipts
    assert not (default_user_home / ".taskplane" / "host-receipts").exists()
