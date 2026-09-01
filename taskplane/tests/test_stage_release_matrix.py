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


def test_release_manifests_keep_parser_safe_hooks_and_supported_metadata() \
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
    assert set(hooks) <= SUPPORTED_HOOK_ROOT_FIELDS
    assert "hostNative" not in hooks
    assert "hostNative" not in codex
    assert claude["hostNative"] == "../hooks/host-native.json"


def test_codex_and_claude_archives_execute_the_installed_stage_runtime(
        tmp_path: Path) -> None:
    paths = _build_archives(tmp_path)
    for host, path in paths.items():
        install = tmp_path / f"installed-{host}"
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            hooks = json.loads(archive.read("taskplane/hooks/hooks.json"))
            assert set(hooks) <= SUPPORTED_HOOK_ROOT_FIELDS
            assert "hostNative" not in hooks
            assert "taskplane/taskplane/test_portfolio.json" not in names
            archive.extractall(install)

        result = subprocess.run(
            [sys.executable, str(install / "taskplane/taskplane/tp.py"),
             "version"],
            cwd=install, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == VERSION


@pytest.mark.parametrize("host", ("openai", "claude"))
def test_each_archive_validator_rejects_unsupported_hostnative_hook_shape(
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


def test_claude_plugin_and_zip_uploads_are_versioned_and_provenanced(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    runs = ((tmp_path / "plugin", True), (tmp_path / "zip", False))
    for output_dir, plugin in runs:
        command = [sys.executable, "scripts/package_claude.py", "--output-dir",
                   str(output_dir), "--allow-dirty"]
        command += ["--ext", "plugin"] if plugin else []
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, result.stderr
    artifacts = (
        tmp_path / "plugin" / f"taskplane-{VERSION}.plugin",
        tmp_path / "zip" / f"taskplane-{VERSION}-claude.zip",
    )
    provenance_validator = _load_packager("release_provenance.py")
    source_sha = provenance_validator.source_state(ROOT)["commit"]
    for artifact in artifacts:
        assert artifact.is_file()
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        checksum = artifact.with_suffix(artifact.suffix + ".sha256")
        assert checksum.read_text(encoding="utf-8") == \
            f"{digest}  {artifact.name}\n"
        provenance_path = artifact.with_suffix(
            artifact.suffix + ".provenance.json")
        provenance = provenance_validator.validate(
            json.loads(provenance_path.read_text(encoding="utf-8")),
            expected_source_sha=source_sha,
            require_release_inputs=True,
        )
        assert provenance["kind"] == "claude"
        assert provenance["archive"] == artifact.name
        assert provenance["sha256"] == digest
