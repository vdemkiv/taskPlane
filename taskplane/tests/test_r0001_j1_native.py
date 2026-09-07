"""J1 remains unverified; no simulated or skipped native journey is registered.

The available production route is loop._phase_bridge_prepare followed by real
SubagentStart/SubagentStop hooks through observe_phase_runtime_hook and
design_host_transport.observe_phase_hook. Taskplane-owned nonce receipts are
eligible; the older prepare_native_entry diagnostic is not this route.

Execution requires an orchestrator-prepared current candidate phase contract,
real host dispatch under that slot, and actual terminal/output collection.
T19 received no such J1 workflow. Authentication is available, not a blocker.
Existing simulated lifecycle helpers cannot supply genuine host authority.
The two approved J1 selectors remain outstanding, not renamed or substituted.
"""

import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts import package_openai


REGISTRY = "agents/spec-phase-definitions.json"
REGISTRY_MEMBER = f"{package_openai.ARCHIVE_ROOT}/{REGISTRY}"


@pytest.fixture(scope="module")
def supporting_package_archive(tmp_path_factory):
    """Actual package producer output; this supplies no native J1 evidence."""
    archive = tmp_path_factory.mktemp("j1-package-support") / "taskplane.zip"
    with pytest.MonkeyPatch.context() as patch:
        # Sparse checkouts can omit this tracked producer input. Read its exact
        # committed bytes without restoring or changing active host hooks.
        hook_path = package_openai.ROOT / ".codex" / "hooks.json"
        if not hook_path.is_file():
            hook_text = subprocess.check_output(
                ["git", "show", "HEAD:.codex/hooks.json"],
                cwd=package_openai.ROOT, text=True, encoding="utf-8",
            )
            read_text = Path.read_text

            def read_source(path, *args, **kwargs):
                return hook_text if path == hook_path else read_text(path, *args, **kwargs)

            patch.setattr(Path, "read_text", read_source)
        manifest = package_openai.load_manifest()
        package_openai.write_zip(package_openai.package_files(manifest), archive)
        yield archive


def _rewrite_supporting_archive(source, target, *, registry=None, version=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as changed:
        for member in original.infolist():
            if member.filename == REGISTRY_MEMBER:
                continue
            payload = original.read(member)
            if version and member.filename.endswith("/.codex-plugin/plugin.json"):
                manifest = json.loads(payload)
                manifest["version"] = version
                payload = json.dumps(manifest).encode()
            changed.writestr(member, payload)
        if registry is not None:
            changed.writestr(REGISTRY_MEMBER, registry)


def test_supporting_package_includes_exact_phase_registry(supporting_package_archive):
    with zipfile.ZipFile(supporting_package_archive) as archive:
        assert REGISTRY_MEMBER in archive.namelist()
        assert archive.read(REGISTRY_MEMBER) == (package_openai.ROOT / REGISTRY).read_bytes()
    package_openai.validate_archive(supporting_package_archive)


@pytest.mark.parametrize("case", ["missing", "changed", "corrupt"])
def test_supporting_package_rejects_invalid_phase_registry(
    supporting_package_archive, tmp_path, case
):
    registry = (package_openai.ROOT / REGISTRY).read_bytes()
    replacement = {"missing": None, "changed": registry + b"\n", "corrupt": b"{broken"}[case]
    archive = tmp_path / f"{case}.zip"
    _rewrite_supporting_archive(supporting_package_archive, archive, registry=replacement)
    with pytest.raises(package_openai.PackageError, match=r"agents/spec-phase-definitions\.json"):
        package_openai.validate_archive(archive)


def test_supporting_package_preserves_explicit_historical_surface_override(
    supporting_package_archive, tmp_path
):
    """Synthetic older declared surface exercises the existing override API."""
    archive = tmp_path / "historical-surface.zip"
    _rewrite_supporting_archive(supporting_package_archive, archive, version="2.18.0")
    package_openai.validate_archive(
        archive,
        expected_version="2.18.0",
        release_surface_root=package_openai.ROOT,
        stage_runtime_files=tuple(
            member for member in package_openai.STAGE_RUNTIME_FILES if member != REGISTRY
        ),
        release_surface_files=(),
        canonical_authority_files=(),
    )
