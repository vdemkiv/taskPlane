"""Onboarding authenticates its declared run without rewriting evidence."""
import json
from pathlib import Path
from unittest import mock

import pytest

from .test_repository_preflight import _review_checkout
import preflight
import run_store
import storage
import taskplane_lite as tp
import tp as cli


@pytest.fixture
def bound_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "checkout"
    _review_checkout(str(ws))
    home = tmp_path / "home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    identity = storage.resolve_repository_identity(str(ws))
    layout = storage.resolve_layout(identity, home=str(home), run_id="startup")
    store = run_store.RunStore(home=str(home))
    store.create(identity, run_id="startup", checkout=str(ws),
                 host={"kind": "codex"}, target={"kind": "repository"})
    storage.write_workspace_locator(str(ws), identity=identity,
                                    layout=layout, run_id="startup")
    Path(tp.kb_root(str(ws)), "context").mkdir(parents=True)
    return str(ws), Path(layout.run_root, "manifest.json")


def test_readiness_checks_only_declared_run_without_mutation(bound_workspace):
    ws, manifest = bound_workspace
    before = manifest.read_bytes()
    with mock.patch.object(run_store.RunStore, "load", side_effect=AssertionError(
            "readiness must not relay journals")):
        result = preflight.workspace_readiness(ws)
    assert result["ready"] is True
    assert result["status"] == "bound"
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("damage,status", [
    ("missing", "missing_manifest"), ("corrupt", "invalid_manifest"),
    ("unsupported_schema", "unsupported_run_schema"),
    ("foreign_run", "invalid_manifest"), ("foreign_repo", "binding_mismatch"),
    ("foreign_checkout", "binding_mismatch"), ("foreign_paths", "binding_mismatch"),
])
def test_onboarding_fails_closed_and_preserves_damaged_binding(
        bound_workspace, damage, status):
    ws, manifest = bound_workspace
    data = json.loads(manifest.read_text())
    if damage == "missing":
        manifest.unlink()
    elif damage == "corrupt":
        manifest.write_text("{broken")
    else:
        if damage == "unsupported_schema":
            data["schema"] = "unsupported"
        elif damage == "foreign_run":
            data["run_id"] = "another-run"
        elif damage == "foreign_repo":
            data["repository"]["repo_id"] = "another-repository"
        elif damage == "foreign_checkout":
            data["repository"]["checkout"] = "/foreign/checkout"
        elif damage == "foreign_paths":
            data["paths"]["artifacts"] = "/foreign/artifacts"
        manifest.write_text(json.dumps(data))
    before = manifest.read_bytes() if manifest.exists() else None
    report = cli._onboard_report(ws)
    assert report["ready"] is False
    assert report["next_action"] == (
        "archive_run" if damage == "unsupported_schema" else "recover_run_binding")
    assert report["run_readiness"]["status"] == status
    assert report["recovery"]["preserve_existing_state"] is True
    assert (manifest.read_bytes() if manifest.exists() else None) == before


def test_permission_failure_is_not_reported_as_missing(bound_workspace):
    ws, _ = bound_workspace
    error = run_store.RunStoreError("run manifest is unavailable: startup")
    error.__cause__ = PermissionError("denied")
    with mock.patch.object(run_store.RunStore, "inspect", side_effect=error):
        result = preflight.workspace_readiness(ws)
    assert result["ready"] is False
    assert result["status"] == "permission_denied"


def test_unbound_workspace_is_not_required_to_have_a_manifest(tmp_path):
    _review_checkout(str(tmp_path))
    assert preflight.workspace_readiness(str(tmp_path)) == {
        "ready": True, "status": "unbound", "run_id": None}


def test_invalid_locator_still_reaches_onboarding_diagnostic(bound_workspace):
    ws, manifest = bound_workspace
    before = manifest.read_bytes()
    locator = Path(storage._locator_path(ws))
    locator.write_text("{corrupt")
    report = cli._onboard_report(ws)
    assert report["ready"] is False
    assert report["next_action"] == "recover_run_binding"
    assert report["run_readiness"]["status"] == "invalid_locator"
    assert locator.read_text() == "{corrupt"
    assert manifest.read_bytes() == before
