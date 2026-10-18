"""Verify shipped bytes and extracted CLI behavior in disposable host stores.

These are package fixtures. They do not install, reload, publish, certify native
hooks, or close historical source/acceptance incidents.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

import pytest

from scripts import package_plugin
from scripts.ci_local import isolated_environment

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = {"CHANGELOG.md", "docs/claude-interactive-recovery.md",
             "docs/state-import-and-release.md", "docs/host-compatibility-watch.md",
             "docs/test-candidate.md"}


def _selected_names(host):
    """Independent inventory of the documented package envelope."""
    manifest = ".codex-plugin" if host == "openai" else ".claude-plugin"
    names = {"README.md", "CHANGELOG.md", "LICENSE", "PRIVACY.md", "TERMS.md"}
    for folder in (manifest, "hooks", "assets", "skills", "agents"):
        names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "taskplane").glob("*.py"))
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "lenses").rglob("*")
                 if path.suffix == ".md" or path.name == "catalog.json")
    names.update("docs/" + name for name in (
        "ci-performance.md", "cli-reference.md", "lens-catalog.md", "onboarding.md",
        "workflow-builder.md", "claude-worker-recovery.md", "claude-interactive-recovery.md",
        "test-candidate.md", "host-compatibility-watch.md", "state-import-and-release.md",
        "state-spec.md", "routing-and-flows.md", "context-optimization.md",
        "host-native-ux.md", "configuration.md", "security.md", "authority-matrix.md",
        "requirements-core.md", "storage-and-repositories.md", "claude-tag.md",
        "regression-gate-design.md", "specialist-routes.md", "lenses-and-knowledge.md"))
    names.add("taskplane/README.md")
    names.update({"docs/assets/taskplane-cowork-flow.gif", "docs/assets/taskplane-flow-source.html"})
    names.update("workflows/" + name for name in ("change-risk-review.workflow.json",
                 "design-brief.workflow.json", "feature-delivery.workflow.json"))
    return names


def _archive(host, tmp_path, request):
    supplied = request.config.getoption("--taskplane-archive-dir")
    if not supplied:
        manifest = package_plugin.package(host, tmp_path / "archives")
        return Path(manifest["archive"]), manifest
    directory = Path(supplied).resolve()
    records = []
    for path in directory.rglob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("host") == host and "member_sha256" in value:
            archive = path.with_suffix("")
            assert archive.is_file(), "Package sidecar has no adjacent archive: " + str(path)
            records.append((archive, value))
    assert len(records) == 1, "Require exactly one digest-bearing archive per host in --taskplane-archive-dir"
    return records[0]


@pytest.mark.taskplane_packages
@pytest.mark.parametrize("host", ["openai", "claude"])
def test_generated_archives_match_verified_source(tmp_path, request, host, record_property):
    expected = _selected_names(host)
    source = {name: package_plugin.read_member(ROOT / name) for name in sorted(expected)}
    before = {name: hashlib.sha256(data).hexdigest() for name, data in source.items()}
    archive, manifest = _archive(host, tmp_path, request)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive_hash == manifest["sha256"]
    assert manifest["host"] == host
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        names = [info.filename for info in infos]
        assert len(names) == len(set(names)), "Duplicate archive member"
        assert set(names) == expected and DOCUMENTS <= set(names)
        assert manifest["members"] == len(names)
        assert manifest["member_sha256"] == before
        for info in infos:
            path = PurePosixPath(info.filename)
            assert not path.is_absolute() and ".." not in path.parts and "\\" not in info.filename
            assert path.as_posix() == info.filename and not info.is_dir()
            assert stat.S_ISREG(info.external_attr >> 16), "Nonregular archive member"
            data = package.read(info)
            assert data == source[info.filename], "Archive does not match final source: " + info.filename
            destination = extracted.joinpath(*path.parts)
            assert destination.resolve().is_relative_to(extracted.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    selected = ".codex-plugin" if host == "openai" else ".claude-plugin"
    other = ".claude-plugin" if host == "openai" else ".codex-plugin"
    assert not (extracted / other).exists()
    plugin = json.loads((extracted / selected / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "taskplane" and plugin["version"] == manifest["version"]
    identity = package_plugin.source_identity(source)
    for key in ("source_commit", "source_dirty", "matches_source_commit", "source_member_differences"):
        assert manifest[key] == identity[key], "Package lineage differs from actual source identity"
    readiness = manifest["release_readiness"]
    assert readiness["publish_ready"] is False
    assert readiness["installed_runtime"] == readiness["platform_ci"] == "not_observed"

    # Execute the extracted entry point with isolated host/config/cache homes.
    # Help is a smoke check only; no fixture output is called a native lifecycle.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = isolated_environment(tmp_path / "host-environment")
    executions = []
    for arguments in (["--help"], ["flow", "worker", "--help"]):
        command = [sys.executable, str(extracted / "taskplane/tp.py"), *arguments]
        result = subprocess.run(command, cwd=workspace, env=environment, capture_output=True,
                                text=True, timeout=60, check=False)
        executions.append({"command": command, "returncode": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "usage:" in result.stdout.lower()
    assert {name: hashlib.sha256(package_plugin.read_member(ROOT / name)).hexdigest()
            for name in expected} == before, "Package checks changed candidate source"
    assert {name: hashlib.sha256((extracted / name).read_bytes()).hexdigest()
            for name in expected} == before, "Extracted CLI changed shipped members"
    record_property("package_identity_evidence", json.dumps({
        "kind": "package_identity", "host": host, "archive_sha256": archive_hash,
        "member_sha256": before, "source_identity": identity, "executions": executions,
        "installed_runtime_verified": False, "native_lifecycle_verified": False,
        "publication_performed": False}))
