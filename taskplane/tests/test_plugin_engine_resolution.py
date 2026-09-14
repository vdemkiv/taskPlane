"""Installed Claude and Codex layouts share the contained engine resolver."""
import json
import subprocess
import sys

import pytest

import host_capabilities
import repository
import tp as cli


def installed(root, manifests):
    for kind, version in manifests.items():
        manifest = root / kind / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "taskplane", "version": version}))
    engine = root / "taskplane/tp.py"
    engine.parent.mkdir()
    engine.write_text("print('installed engine')\n")
    return engine


@pytest.mark.parametrize("kind", [".codex-plugin", ".claude-plugin"])
def test_installed_manifest_resolves_through_both_entry_points(tmp_path, kind):
    family = tmp_path / "cache"
    root = family / "2.23.7"
    engine = installed(root, {kind: "2.23.7"})
    assert host_capabilities.resolve_plugin_engine(str(family)) == str(engine)
    assert repository._valid_engines(str(family)) == [((2, 23, 7), str(engine))]
    launcher = tmp_path / "launcher.py"
    launcher.write_text(cli._codex_runner_body(str(family)))
    result = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "installed engine"


def test_conflicting_manifests_fail_closed(tmp_path):
    root = tmp_path / "cache/2.23.7"
    installed(root, {".codex-plugin": "2.23.7", ".claude-plugin": "2.23.6"})
    assert host_capabilities.resolve_plugin_engine(str(root.parent)) is None
    assert repository._valid_engines(str(root.parent)) == []


def test_invalid_codex_manifest_cannot_fall_through_to_claude(tmp_path):
    root = tmp_path / "cache/2.23.7"
    installed(root, {".codex-plugin": "2.23.7", ".claude-plugin": "2.23.7"})
    (root / ".codex-plugin/plugin.json").write_text("broken")
    assert host_capabilities.resolve_plugin_engine(str(root.parent)) is None
    assert repository._valid_engines(str(root.parent)) == []
