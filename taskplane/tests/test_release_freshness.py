"""Current release-surface contracts with direct product value."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest

from taskplane import release_evidence


ROOT = Path(__file__).resolve().parents[2]


def _packager(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"_release_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _members(name: str, tmp_path: Path) -> set[str]:
    module = _packager(name)
    archive = tmp_path / name.replace(".py", ".zip")
    if name == "package_openai.py":
        module.write_zip(module.package_files(module.load_manifest()), archive)
        module.validate_archive(archive)
    else:
        module.write_zip(module.package_files(), archive)
        module.validate_archive(archive, release_evidence.CURRENT_VERSION)
    with zipfile.ZipFile(archive) as handle:
        return set(handle.namelist())


def test_current_version_authority_agrees_across_runtime_and_manifests() -> None:
    current = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(
        encoding="utf-8"))["version"]
    claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(
        encoding="utf-8"))
    marketplace = json.loads((
        ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    compatibility = json.loads((ROOT / "design/compatibility.json").read_text(
        encoding="utf-8"))

    assert re.fullmatch(r"\d+\.\d+\.\d+", current)
    assert {
        release_evidence.CURRENT_VERSION,
        claude["version"],
        marketplace["version"],
        marketplace["plugins"][0]["version"],
        compatibility["window"]["current"],
        compatibility["baseline_rebind"]["next_generation"],
    } == {current}
    assert release_evidence.PREVIOUS_VERSION == compatibility["window"][
        "last_released"]
    assert release_evidence.COMPATIBILITY_PREVIOUS_VERSION == compatibility[
        "window"]["previous"]

    for relative in ("README.md", "CHANGELOG.md"):
        body = (ROOT / relative).read_text(encoding="utf-8")
        assert f"| **v{current}** |" in body


def test_live_cli_reference_generator_rejects_undocumented_flags() -> None:
    """The parser, rather than a copied flag inventory, owns CLI truth."""
    sys.path.insert(0, str(ROOT / "taskplane"))
    try:
        import argparse
        import tp

        parser = argparse.ArgumentParser(prog="fake")
        command = parser.add_subparsers(dest="cmd", required=True).add_parser(
            "one", help="documented command")
        command.add_argument("--bare")
        with pytest.raises(tp.CliReferenceError, match="bare"):
            tp.cli_reference_markdown(parser)
    finally:
        sys.path.remove(str(ROOT / "taskplane"))


def test_marketplace_archives_ship_runtime_hooks_and_all_host_skills(
    tmp_path: Path,
) -> None:
    openai = _members("package_openai.py", tmp_path)
    claude = _members("package_claude.py", tmp_path)
    required_runtime = {
        "taskplane/taskplane/design_host_transport.py",
        "taskplane/taskplane/lens_catalog.py",
        "taskplane/taskplane/phase_admission.py",
        "taskplane/taskplane/phase_build.py",
        "taskplane/taskplane/phase_dispatch.py",
        "taskplane/taskplane/phase_entry.py",
        "taskplane/taskplane/phase_handoff.py",
        "taskplane/taskplane/phase_inputs.py",
        "taskplane/taskplane/phase_output.py",
        "taskplane/taskplane/phase_pickup.py",
        "taskplane/taskplane/phase_plan.py",
        "taskplane/taskplane/phase_producer.py",
        "taskplane/taskplane/phase_review.py",
        "taskplane/taskplane/phase_review_host.py",
        "taskplane/taskplane/build_quality.py",
        "taskplane/taskplane/failure_routing.py",
        "taskplane/taskplane/run_artifacts.py",
        "taskplane/taskplane/operational-settings.json",
        "taskplane/hooks/hooks.json",
        "taskplane/hooks/host-native.json",
        "taskplane/hooks/host_native_runtime.py",
    }
    assert required_runtime <= openai
    assert required_runtime <= claude
    assert "taskplane/taskplane/test_portfolio.json" not in openai
    assert "taskplane/taskplane/test_portfolio.json" not in claude

    source_skills = {
        "taskplane/" + path.relative_to(ROOT).as_posix()
        for path in (ROOT / "skills").rglob("*") if path.is_file()
    }
    codex_skills = {
        path for path in source_skills
        if not path.startswith("taskplane/skills/tp-tag/")
    }
    assert codex_skills <= openai
    assert source_skills <= claude


def test_version_cli_reports_the_current_candidate() -> None:
    result = subprocess.run(
        [sys.executable, "taskplane/tp.py", "version"], cwd=ROOT,
        text=True, encoding="utf-8", capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == release_evidence.CURRENT_VERSION
