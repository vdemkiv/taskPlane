"""Executed archive compatibility, not live host or publication authority."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _packager():
    spec = importlib.util.spec_from_file_location(
        "_hook_generation_packager", ROOT / "scripts/package_openai.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_uses_each_archives_own_hook_authority(monkeypatch):
    packager = _packager()
    # Tests can run in an in-progress checkout; the real producer separately
    # requires a clean candidate. No matrix result here is release evidence.
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)
    receipt = packager.produce_release_compatibility_receipt(
        expected_source_sha=packager.git_head())
    assert receipt["status"] == "release-compatible"
    assert len(receipt["cells"]) == 4
    assert {row["test_outcome"] for row in receipt["cells"]} == {"passed"}


@pytest.mark.parametrize("mutation", ["archive-command", "authority-root", "wrong-generation"])
def test_legacy_archive_still_requires_exact_valid_hook_authority(tmp_path, mutation):
    packager = _packager()
    policy = packager.load_json_object(ROOT / "design/compatibility.json", "policy")
    producer = policy["release_observation_producer"]
    historical_root = tmp_path / "last-released"
    historical_root.mkdir()
    packager._materialize_git_revision(producer["last_released_commit"], historical_root)
    historical = packager._load_packager(
        historical_root / "scripts/package_openai.py", "_pinned_hook_packager")
    archive = tmp_path / "historical.zip"
    historical.write_zip(historical.package_files(historical.load_manifest()), archive)
    authority = historical.load_hook_manifest()
    if mutation == "archive-command":
        changed = tmp_path / "changed.zip"
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(changed, "w") as target:
            for info in original.infolist():
                body = original.read(info.filename)
                if info.filename == "taskplane/hooks/hooks.json":
                    hooks = json.loads(body)
                    hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "echo changed"
                    body = json.dumps(hooks).encode("utf-8")
                target.writestr(info, body)
        archive = changed
    elif mutation == "authority-root":
        authority["hostNative"] = {}
    else:
        authority = packager.load_hook_manifest()
    with pytest.raises(packager.PackageError, match="(wiring does not match|root fields)"):
        packager.validate_archive(
            archive, expected_version=policy["window"]["last_released"],
            release_surface_root=historical_root,
            stage_runtime_files=historical.STAGE_RUNTIME_FILES,
            release_surface_files=(), canonical_authority_files=(),
            expected_hook_manifest=authority)
