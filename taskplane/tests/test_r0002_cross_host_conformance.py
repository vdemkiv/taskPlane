"""R-0003 t09: conservative host/mode conformance and rollback."""
from __future__ import annotations

from argparse import Namespace
import io
import json
import os
import sys

import collision
import taskplane_lite as kernel
import tp as cli


def _manifest(root, relative):
    with open(os.path.join(root, relative), encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


def test_codex_and_claude_hook_surfaces_have_equivalent_authority():
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    native = _manifest(root, "hooks/hooks.json")
    bridge = _manifest(root, ".codex/hooks.json")
    assert set(native) == set(bridge)
    for event in native:
        assert [row.get("matcher") for row in native[event]] == [
            row.get("matcher") for row in bridge[event]]
    for manifest in (native, bridge):
        skill = next(row for row in manifest["PreToolUse"]
                     if row.get("matcher") == "Skill")
        hook = skill["hooks"][0]
        assert "screen-skill" in hook["command"]
        assert "python3" in hook["command"]
        assert "screen-skill" in hook["commandWindows"]
        assert "py -3" in hook["commandWindows"]


def test_explicit_collision_rollback_observes_without_forging_denial(
        tmp_path, monkeypatch, capsys):
    contract = kernel.build_contract("governed", scope=["**"])
    contract["enforcement"] = {"status": "live", "evidence_id": "e"}
    kernel.activate(str(tmp_path), contract, snapshot=None)
    monkeypatch.setenv("TASKPLANE_COLLISION_SCREEN", "off")
    event = {"cwd": str(tmp_path), "tool_input": {
        "skill": "orchestrator-supaconductor:go"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_screen_skill(Namespace()) == 0
    output = json.loads(capsys.readouterr().out)
    assert "systemMessage" in output
    assert "would deny" in output["systemMessage"]
    assert "permissionDecision" not in json.dumps(output)
    ledger = collision.load_ledger(str(tmp_path))
    assert ledger["counts"]["observed_invocations"] == 1


def test_rollback_never_weakens_no_force_cleanup_contract():
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "worktree_cleanup.py"), encoding="utf-8").read()
    assert '"worktree", "remove", "--", candidate' in source
    assert '"worktree", "remove", "--force"' not in source
    assert '"branch", "-D"' not in source
