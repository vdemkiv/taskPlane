"""Release-matrix proof for the R-0004 stage-native rollout."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(
    encoding="utf-8"))["version"]
SUPPORTED_HOOK_ROOT_FIELDS = {"description", "hooks"}
STAGE_MATRIX_TESTS = (
    "taskplane/tests/test_stage_non_build_handoffs.py",
    "taskplane/tests/test_stage_cross_host.py",
    "taskplane/tests/test_stage_rollout.py",
    "taskplane/tests/test_stage_r0003_preservation.py",
    "taskplane/tests/test_stage_release_matrix.py",
    "taskplane/tests/test_stage_loop_integration.py",
    "taskplane/tests/test_stage_cli.py",
)
SHARED_RUNTIME_MEMBERS = (
    "hooks/hooks.json",
    "hooks/host-native.json",
    "hooks/host_native_runtime.py",
    "taskplane/taskplane_lite.py",
    "taskplane/loop.py",
    "taskplane/tp.py",
    "taskplane/stage_entities.py",
    "taskplane/stage_handoff.py",
    "taskplane/stage_migration.py",
    "taskplane/loop_status.py",
    "taskplane/dashboard.py",
    "taskplane/runtime_eval.py",
    "taskplane/operational-settings.json",
    "taskplane/settings_inventory.json",
    "taskplane/test_portfolio.json",
    "docs/cli-reference.md",
    "skills/taskplane/SKILL.md",
    "skills/taskplane/flow.json",
    "skills/tp-build/SKILL.md",
    "skills/tp-design/SKILL.md",
    "skills/tp-design/flow.json",
    "skills/tp-engineering/SKILL.md",
    "skills/tp-engineering/flow.json",
    "skills/tp-go/SKILL.md",
    "skills/tp-go/flow.json",
    "skills/tp-go/references/parallel.md",
    "skills/tp-go/references/retro.md",
    "skills/tp-product/SKILL.md",
    "skills/tp-product/flow.json",
    "skills/tp-status/SKILL.md",
    "skills/tp-status/flow.json",
)


def _load_packager(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        "_stage_release_" + name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_archives(tmp_path: Path) -> dict[str, Path]:
    openai = _load_packager("package_openai.py")
    claude = _load_packager("package_claude.py")
    paths = {
        "openai": tmp_path / "taskplane-openai.zip",
        "claude": tmp_path / "taskplane-claude.zip",
    }
    openai.write_zip(openai.package_files(openai.load_manifest()),
                     paths["openai"])
    claude.write_zip(claude.package_files(), paths["claude"])
    openai.validate_archive(paths["openai"])
    claude.validate_archive(paths["claude"], VERSION)
    return paths


def _replace_hook_manifest(
        source: Path, destination: Path, value: dict[str, object]) -> None:
    member = "taskplane/hooks/hooks.json"
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info.filename))
                   for info in archive.infolist()]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for info, body in entries:
            archive.writestr(
                info, json.dumps(value).encode("utf-8")
                if info.filename == member else body)


def _python_matrix_entries(workflow: str) -> dict[str, tuple[str, ...]]:
    """Return the bounded entries from the primary compatibility matrix."""
    lines = workflow.splitlines()
    versions = ("3.10", "3.11", "3.12", "3.13")
    headers = {
        version: f'          - python: "{version}"'
        for version in versions
    }
    indexes: dict[str, int] = {}
    for version, header in headers.items():
        matches = [index for index, line in enumerate(lines)
                   if line == header]
        assert len(matches) == 1, \
            f"expected one exact Python {version} test-matrix entry"
        indexes[version] = matches[0]
    assert [indexes[version] for version in versions] == sorted(indexes.values())
    step_boundaries = [index for index, line in enumerate(lines)
                       if index > indexes[versions[-1]] and line == "    steps:"]
    assert step_boundaries, "last Python matrix entry has no bounded end"
    boundaries = [indexes[version] for version in versions] + [step_boundaries[0]]
    return {version: tuple(lines[boundaries[offset]:boundaries[offset + 1]])
            for offset, version in enumerate(versions)}


def test_ci_runs_the_stage_release_contract_on_python_310_through_313() \
        -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8")
    entries = _python_matrix_entries(workflow)

    for version in ("3.10", "3.11", "3.12", "3.13"):
        for test_file in STAGE_MATRIX_TESTS:
            selector = "              " + test_file
            assert entries[version].count(selector) == 1, \
                f"Python {version} must run exact selector {test_file}"


def test_ci_builds_and_provenances_the_deterministic_claude_plugin() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8")

    assert workflow.count(
        "python scripts/package_claude.py --ext plugin") == 1
    for output_dir in ("/tmp/tp-claude-plugin-a",
                       "/tmp/tp-claude-plugin-b"):
        command = ("python3 scripts/package_claude.py --ext plugin "
                   f"--output-dir {output_dir}")
        assert workflow.count(command) == 1
    assert workflow.count(
        '"/tmp/tp-claude-plugin-a/*.provenance.json"') == 1
    assert "if len(found) != 3:" in workflow


def test_post_21713_manifests_keep_parser_safe_hooks_and_supported_metadata() \
        -> None:
    codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(
        encoding="utf-8"))
    claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(
        encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))

    assert {codex["version"], claude["version"], marketplace["version"],
            marketplace["plugins"][0]["version"]} == {VERSION}
    assert tuple(int(part) for part in VERSION.split(".")[:3]) >= (2, 17, 13)
    assert set(hooks) <= SUPPORTED_HOOK_ROOT_FIELDS
    assert "hostNative" not in hooks
    assert "hostNative" not in codex
    assert claude["hostNative"] == "../hooks/host-native.json"


def test_codex_and_claude_archives_ship_identical_stage_runtime_bytes(
        tmp_path: Path) -> None:
    paths = _build_archives(tmp_path)
    payloads: dict[str, dict[str, bytes]] = {}
    for host, path in paths.items():
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            payloads[host] = {
                relative: archive.read("taskplane/" + relative)
                for relative in SHARED_RUNTIME_MEMBERS
            }
            assert {"taskplane/" + relative
                    for relative in SHARED_RUNTIME_MEMBERS} <= names
            hooks = json.loads(payloads[host]["hooks/hooks.json"])
            assert set(hooks) <= SUPPORTED_HOOK_ROOT_FIELDS
            assert "hostNative" not in hooks

    assert payloads["openai"] == payloads["claude"]
    guidance = payloads["openai"]["skills/tp-go/SKILL.md"].decode("utf-8")
    for rail in ("Codex", "Claude", "managed", "Slack-capable",
                 "accessible text"):
        assert rail in guidance


@pytest.mark.parametrize("host", ("openai", "claude"))
def test_each_archive_validator_rejects_the_21712_hostnative_hook_shape(
        tmp_path: Path, host: str) -> None:
    paths = _build_archives(tmp_path)
    unsafe = tmp_path / f"unsafe-{host}.zip"
    _replace_hook_manifest(paths[host], unsafe, {
        "hostNative": "./host-native.json", "hooks": {}})
    packager = _load_packager(
        "package_openai.py" if host == "openai" else "package_claude.py")

    with pytest.raises(packager.PackageError, match="hook manifest"):
        if host == "openai":
            packager.validate_archive(unsafe)
        else:
            packager.validate_archive(unsafe, VERSION)


@pytest.mark.parametrize("host", ("openai", "claude"))
def test_package_bytes_are_deterministic_for_the_same_release_tree(
        tmp_path: Path, host: str) -> None:
    packager = _load_packager(
        "package_openai.py" if host == "openai" else "package_claude.py")
    files = (packager.package_files(packager.load_manifest())
             if host == "openai" else packager.package_files())
    first = tmp_path / f"{host}-first.zip"
    second = tmp_path / f"{host}-second.zip"

    packager.write_zip(files, first)
    packager.write_zip(files, second)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == \
        hashlib.sha256(second.read_bytes()).hexdigest()


def test_claude_plugin_upload_is_deterministic_and_provenanced(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    output_dirs = (tmp_path / "first", tmp_path / "second")
    runs = ((output_dirs[0], True), (output_dirs[1], True),
            (tmp_path / "zip", False))
    for output_dir, plugin in runs:
        command = [sys.executable, "scripts/package_claude.py", "--output-dir",
                   str(output_dir), "--allow-dirty"]
        command += ["--ext", "plugin"] if plugin else []
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert (tmp_path / "zip" / f"taskplane-{VERSION}-claude.zip").is_file()
    filename = f"taskplane-{VERSION}.plugin"
    artifacts = tuple(path / filename for path in output_dirs)
    assert artifacts[0].read_bytes() == artifacts[1].read_bytes()
    for artifact in artifacts:
        assert artifact.with_suffix(".plugin.sha256").is_file()
        provenance = json.loads(artifact.with_suffix(
            ".plugin.provenance.json").read_text(encoding="utf-8"))
        assert provenance["archive"] == filename
        assert provenance["sha256"] == hashlib.sha256(
            artifact.read_bytes()).hexdigest()
