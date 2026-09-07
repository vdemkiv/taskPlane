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
from taskplane import loop, requirements, review_evidence, stage_migration


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


def _supporting_pristine_phase_run(tmp_path, monkeypatch):
    """Public producers with simulated source/authority; no stage or host event seed."""
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement,
    )

    workspace, store, initial = _real_pristine_run(tmp_path)
    ws = str(workspace)
    requirement = _record_bootstrap_requirement(workspace)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "supporting first-dispatch regression", requirement_id=requirement["id"],
        by="human:simulated",
    )
    assert "error" not in initialized, initialized
    authority = initialized["_stage_native_root_authority"]
    artifacts = review_evidence.ArtifactStore(ws)
    configuration = {
        "definition_source": REGISTRY,
        "definition_set_fingerprint": _registry().definition_set_fingerprint,
        "knowledge_reference": artifacts.put("phase-knowledge", {"facts": []}),
        "candidate_fingerprint": review_evidence.content_fingerprint(
            {"revision": authority["target_revision"], "evidence_mode": "simulated"}
        ),
        "target_revision": authority["target_revision"],
        "host_kind": "simulated", "host_version": "supporting-local-test",
        "output_paths": {"product": {"requirement": "specs/requirement.json"}},
    }

    def authorize(current):
        assert current["run_id"] == authority["run_id"]
        assert loop._stage_native_init_authority(
            ws, requirement["id"], "human:simulated"
        ) == authority

    stage_migration.change_phase_routing(
        store, initial["run_id"], owner="agent-runtime", configuration=configuration,
        expected_previous=None, expected_revision=initial["revision"],
        operation_id="supporting-enable-phase-runtime", validate_authority=authorize,
    )
    before = store.load(initial["run_id"])
    assert before["schema"] == "taskplane.run/v3"
    assert not before.get("stage_heads")
    assert not before.get("stage_operations")
    return ws, store, initial["run_id"], requirement


def test_supporting_pristine_init_next_prepares_once_and_picks_up_pending(
    tmp_path, monkeypatch, record_property
):
    """Actual init/next and preparation; no native launch/terminal is simulated."""
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    first = loop.next_action(ws)
    assert "error" not in first, first
    assert first["phase_runtime"]["status"] == "pending"
    manifest = store.load(run_id)
    root_id = loop.load(ws)["_stage_run_binding"]["root_stage_id"]
    assert manifest["active_stage_projection"]["active_stage_ids"] == [root_id]
    assert sorted(row["operation"] for row in manifest["stage_operations"].values()) == [
        "resume_stage", "start_stage",
    ]
    preparations = [row for row in stage_migration.phase_records(manifest).values()
                    if row["operation"] == "phase_prepare"]
    assert len(preparations) == 1
    assert first["phase_runtime"]["reference"] == preparations[0]["result"]["reference"]

    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
    assert pending["phase_runtime"]["operation_id"] == first["phase_runtime"]["operation_id"]
    assert pending["phase_runtime"]["status"] == "pending"
    assert "task_name" not in pending  # One dispatch request, no second launch request.
    assert loop.next_action(ws)["phase_runtime"] == pending["phase_runtime"]
    # Pickup keeps precedence over a new requirement attachment.
    assert loop.next_action(ws, rid="R-9999")["phase_runtime"] == pending["phase_runtime"]
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")
    record_property("native_host_execution", "not-performed")


@pytest.mark.parametrize("change", ["session", "requirement"])
def test_supporting_pristine_next_rejects_changed_authority_before_effects(
    tmp_path, monkeypatch, record_property, change
):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    before = store.load(run_id)
    if change == "session":
        monkeypatch.setenv("TASKPLANE_SESSION_ID", "foreign-simulated-session")
    else:
        requirements.amend_requirement(ws, requirement["id"],
            acceptance=["changed supporting requirement must refuse bootstrap"])
    refused = loop.next_action(ws)
    assert "stage-native root bootstrap failed closed" in refused["error"]
    assert f"{change} changed" in refused["error"]
    assert "task_name" not in refused
    assert "phase_runtime" not in refused
    assert store.load(run_id) == before
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")


def test_supporting_pristine_next_recovers_committed_root_before_pending_lookup(
    tmp_path, monkeypatch, record_property
):
    """Sever the singleton binding write after the actual lifecycle commit."""
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    persist = loop._persist_stage_run_binding

    def interrupted(*args, **kwargs):
        raise OSError("supporting interruption after root commit")

    with monkeypatch.context() as patch:
        patch.setattr(loop, "_persist_stage_run_binding", interrupted)
        refused = loop.next_action(ws)
    assert "supporting interruption after root commit" in refused["error"]
    committed = store.load(run_id)
    assert len(committed["stage_operations"]) == 1
    assert not any(row["operation"] == "phase_prepare"
                   for row in stage_migration.phase_records(committed).values())
    assert loop._persist_stage_run_binding is persist

    recovered = loop.next_action(ws)
    assert "error" not in recovered, recovered
    assert recovered["phase_runtime"]["status"] == "pending"
    manifest = store.load(run_id)
    assert {key: manifest["stage_operations"][key]
            for key in committed["stage_operations"]} == committed["stage_operations"]
    assert sorted(row["operation"] for row in manifest["stage_operations"].values()) == [
        "resume_stage", "start_stage",
    ]
    assert manifest["stage_heads"] == committed["stage_heads"]
    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == recovered["phase_runtime"]["reference"]
    assert "task_name" not in pending
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")
