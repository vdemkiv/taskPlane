"""T20 production migration/fence regressions with local authority inputs.

These isolated tests are not native journey, sign-off, or publication evidence.
The migration and lifecycle producers create the receipts consumed below.
"""
from __future__ import annotations

import base64
from pathlib import Path
import subprocess

import pytest

from taskplane import loop, review_evidence, stage_entities, stage_migration, storage, track
from taskplane.tests.test_stage_migration import (
    NOW, RUN_ID, _authority, _legacy_loop, _requirement, _sources, _store,
)


@pytest.fixture
def legacy_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    store, initial = _store(tmp_path)
    monkeypatch.setenv("TASKPLANE_HOME", store.home)
    monkeypatch.setenv("TASKPLANE_STORE", "repo")
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    identity = storage.identity_from_remote("https://github.com/example/project.git")
    storage.write_workspace_locator(str(workspace), identity=identity,
        layout=storage.resolve_layout(identity, home=store.home, run_id=RUN_ID), run_id=RUN_ID)
    loop.save(str(workspace), _legacy_loop())
    return str(workspace), store, initial


def _migrate(context):
    workspace, store, initial = context
    def authorize(expected, current):
        assert expected == _authority()
        assert current["run_id"] == RUN_ID
    return stage_migration.migrate_singleton(workspace, store=store, run_id=RUN_ID,
        expected_revision=initial["revision"], operation_id="retire-singleton",
        authority=_authority(), requirement=_requirement(),
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"], created_at=NOW,
        legacy_sources=_sources(_legacy_loop()), authority_validator=authorize)


def _terminalize(context, receipt):
    workspace, store, _ = context
    def authorize(expected, current):
        assert expected == current == _authority()
    lifecycle = stage_entities.StageLifecycle(store, workspace=workspace,
        authority_resolver=lambda manifest: _authority(), authority_validator=authorize)
    return lifecycle.terminalize(RUN_ID, stage_id=receipt["stage_ids"][0],
        expected_head_fingerprint=receipt["result"]["head"]["object"]["fingerprint"],
        expected_revision=store.load(RUN_ID)["revision"], operation_id="discard-migrated-stage",
        outcome="discarded", actor="human:vdemkiv", terminalized_at=NOW,
        reason_code="rollback", reason="Retain evidence while stopping new work")


def test_legacy_writer_stops_before_reader_removal(legacy_workspace):
    workspace, store, initial = legacy_workspace
    assert "error" not in track.new(workspace, "legacy", "unmigrated work")
    assert stage_migration.migration_projection(workspace) is None
    assert store.load(RUN_ID) == initial
    receipt = _migrate(legacy_workspace)
    before = Path(loop._loop_path(workspace)).read_bytes()
    assert "read-only" in track.new(workspace, "forbidden", "retired writer")["error"]
    assert track.list_(workspace)["active"] == receipt["stage_ids"][0]
    assert Path(loop._loop_path(workspace)).read_bytes() == before


@pytest.mark.parametrize("mode", ["disabled", "enabled"], ids=["rollback", "enabled"])
@pytest.mark.parametrize("severed", [False, True], ids=["intact", "retained-source-severed"])
def test_superseded_path_permanently_fenced(legacy_workspace, monkeypatch, mode, severed):
    workspace, store, _ = legacy_workspace
    receipt = _migrate(legacy_workspace)
    _terminalize(legacy_workspace, receipt)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)
    before = store.load(RUN_ID)
    source = Path(loop._loop_path(workspace)).read_bytes()
    if severed:
        artifacts = stage_migration._migration_artifact_store(workspace, store, RUN_ID)
        native = artifacts.references("legacy-source")[0]
        Path(native["path"]).write_bytes(b"severed retained producer output")
        # Damage to the actual producer output must fail this same public
        # reader and writer boundary, never fall back to the old singleton.
        for action in (lambda: track.list_(workspace),
                       lambda: track.new(workspace, "old", "must not dispatch")):
            with pytest.raises(track._stage_migration.review_evidence.ArtifactIntegrityError,
                               match="digest mismatch"):
                action()
        assert store.load(RUN_ID) == before
        assert Path(loop._loop_path(workspace)).read_bytes() == source
        return
    for result in (track.new(workspace, "old", "must not dispatch"),
                   track.switch(workspace, "old"), track.close(workspace, "old")):
        assert "read-only" in result["error"]
    assert track.list_(workspace)["active"] is None
    assert store.load(RUN_ID) == before
    assert Path(loop._loop_path(workspace)).read_bytes() == source


@pytest.mark.parametrize("terminal", [False, True], ids=["active", "terminal"])
def test_retired_evidence_remains_readable(legacy_workspace, terminal):
    workspace, store, _ = legacy_workspace
    receipt = _migrate(legacy_workspace)
    if terminal:
        _terminalize(legacy_workspace, receipt)
    before = store.load(RUN_ID)
    projected = stage_migration.migration_projection(workspace)
    assert projected["receipt"] == receipt
    assert projected["stages"][receipt["stage_ids"][0]]["state"] == (
        "terminal" if terminal else "active")
    artifacts = stage_migration._migration_artifact_store(workspace, store, RUN_ID)
    bundle = artifacts.read(receipt["result"]["source_ref"])
    assert {row["name"]: base64.b64decode(row["base64"]) for row in bundle["sources"]} == _sources(_legacy_loop())
    original = store.read_stage_object(RUN_ID, receipt["result"]["head"]["object"])
    assert original["state"] == "active"
    readable = stage_migration.read_compatible_contract(review_evidence.canonical_bytes(original))
    assert readable.progression_authority is False
    assert store.load(RUN_ID) == before


def test_no_second_phase_or_progression_owner(tmp_path, monkeypatch):
    from taskplane.tests.test_r0001_phase_cutover import _normal_phase_workspace
    workspace, store, stage, _, route, authorize = _normal_phase_workspace(tmp_path, monkeypatch)
    before = store.load(stage["run_id"])
    rollback = stage_migration.change_phase_routing(store, stage["run_id"],
        owner="incumbent", configuration=None, expected_previous=route["result_fingerprint"],
        expected_revision=before["revision"], operation_id="rollback-new-attempts",
        validate_authority=authorize)
    after = store.load(stage["run_id"])
    assert stage_migration.phase_routing(after) == rollback
    for key in ("stage_heads", "stage_operations", "lineage", "active_stage_projection"):
        assert after[key] == before[key]
    assert "phase_runtime" not in loop.next_action(workspace)
    current = store.load(stage["run_id"])
    def revoked(_manifest):
        raise ValueError("current authority revoked")
    with pytest.raises(ValueError, match="authority revoked"):
        stage_migration.change_phase_routing(store, stage["run_id"], owner="agent-runtime",
            configuration=route["result"]["configuration"], expected_previous=rollback["result_fingerprint"],
            expected_revision=current["revision"], operation_id="stale-authority",
            validate_authority=revoked)
    assert store.load(stage["run_id"]) == current
