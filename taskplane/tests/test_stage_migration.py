"""Conservative, non-destructive singleton-to-stage migration."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest

from taskplane import run_store, stage_migration, storage


RUN_ID = "run-legacy-migration"
NOW = "2026-08-21T14:00:00Z"


def _store(tmp_path: Path) -> tuple[run_store.RunStore, dict]:
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(tmp_path / "checkout"),
        host={"kind": "codex", "session_id": "thread-1"},
        target={"kind": "workspace", "revision": "1" * 40},
    )
    return store, manifest


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": RUN_ID,
        "repository_id": "github.com/example/project",
        "repository_key": "github.com-example-project",
        "worktree_id": "legacy-workspace",
        "target_revision": "1" * 40,
        "worktree_revision": "1" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-1",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _requirement() -> dict[str, object]:
    return {"id": "R-0004", "revision": "4", "fingerprint": "b" * 64}


def _legacy_loop(step: str = "execute") -> dict[str, object]:
    return {
        "governance_revision": 2,
        "goal": "Retain every legacy fact",
        "requirement_id": "R-0004",
        "step": step,
        "current_task": 0,
        "tasks": [
            {"id": "t01", "status": "passed", "commit": "a" * 40,
             "reviews": [{"lens": "qa", "verdict": "pass"}],
             "evidence": [{"kind": "suite", "fingerprint": "e" * 64}]},
            {"id": "t02", "status": "running", "deps": ["t01"]},
        ],
        "decisions": [{"id": "D-1", "decision": "approved"}],
        "audit_history": [{"event": "gate", "actor": "human:vdemkiv"}],
    }


def _sources(loop: dict[str, object]) -> dict[str, bytes]:
    # Deliberately retain non-canonical whitespace and the final newline.
    return {
        "loop.json": (json.dumps(loop, indent=3, sort_keys=False) + "\n").encode(),
        "tracks.json": b'{\n  "active": "main", "tracks": {}\n}\n',
        "requirements/R-0004.json": (
            b'{"id":"R-0004","acceptance":["no loss"]}\n'),
    }


def test_source_bundle_retains_exact_bytes_and_conserves_named_records() -> None:
    sources = _sources(_legacy_loop())
    bundle = stage_migration.retain_legacy_sources(sources)

    assert bundle["schema"] == "taskplane.legacy-source-bundle/v1"
    assert [row["name"] for row in bundle["sources"]] == sorted(sources)
    for row in bundle["sources"]:
        raw = base64.b64decode(row["base64"], validate=True)
        assert raw == sources[row["name"]]
        assert row["bytes"] == len(raw)

    report = bundle["conservation"]
    assert report["schema"] == "taskplane.legacy-conservation/v1"
    assert report["requirements"]["count"] >= 1
    assert report["tasks"]["count"] == 2
    assert report["decisions"]["count"] == 1
    assert report["evidence"]["count"] == 1
    assert report["commits"]["count"] == 1
    assert report["reviews"]["count"] == 1
    assert report["audit_history"]["count"] == 1
    assert stage_migration.verify_retained_sources(bundle, sources)


def test_ambiguous_state_produces_immutable_unknown_not_a_lifecycle_guess() -> None:
    sources = _sources(_legacy_loop("mystery-state"))
    bundle = stage_migration.retain_legacy_sources(sources)
    sentinel = stage_migration.legacy_unknown(
        bundle, unknown_reason="unrecognized_loop_step:mystery-state")

    assert sentinel["schema"] == "taskplane.legacy-unknown/v1"
    assert sentinel["unknown_reason"] == \
        "unrecognized_loop_step:mystery-state"
    assert "state" not in sentinel
    assert "outcome" not in sentinel
    assert stage_migration.verify_legacy_unknown(sentinel, bundle)

    changed = copy.deepcopy(sentinel)
    changed["unknown_reason"] = "pending"
    with pytest.raises(stage_migration.MigrationIntegrityError):
        stage_migration.verify_legacy_unknown(changed, bundle)


def test_migration_requires_exact_authority_validator_without_mutation(
        tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)

    with pytest.raises(stage_migration.MigrationIntegrityError,
                       match="authority validator"):
        stage_migration.migrate_singleton(
            str(workspace), store=store, run_id=RUN_ID,
            expected_revision=initial["revision"],
            operation_id="migrate-without-validator",
            authority=_authority(), requirement=_requirement(),
            design={"revision": "2", "fingerprint": "c" * 64},
            contracts=["contract:stage-entity-lifecycle"], created_at=NOW,
            legacy_sources=_sources(_legacy_loop()),
        )
    assert store.load(RUN_ID) == initial


def test_unambiguous_migration_is_atomic_idempotent_and_conservative(
        tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)
    sources = _sources(_legacy_loop("execute"))

    first = stage_migration.migrate_singleton(
        str(workspace),
        store=store,
        run_id=RUN_ID,
        expected_revision=initial["revision"],
        operation_id="migrate-legacy-main",
        authority=_authority(),
        requirement=_requirement(),
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"],
        created_at=NOW,
        legacy_sources=sources,
        authority_validator=lambda _expected, _current: None,
    )
    committed = store.load(RUN_ID)

    assert first["operation"] == "migrate_singleton"
    assert committed["schema"] == "taskplane.run/v4"
    assert len(first["stage_ids"]) == 1
    stage_id = first["stage_ids"][0]
    assert committed["stage_heads"][stage_id]["summary"]["state"] == "active"
    assert committed["active_stage_projection"]["foreground_stage_id"] == stage_id
    assert first["result"]["classification"] == "stage"
    assert first["result"]["conservation"] == \
        stage_migration.retain_legacy_sources(sources)["conservation"]
    projection = stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID)
    assert projection["foreground_stage_id"] == stage_id
    assert projection["foreground"]["state"] == "active"

    replay = stage_migration.migrate_singleton(
        str(workspace),
        store=store,
        run_id=RUN_ID,
        expected_revision=initial["revision"],  # stale by design
        operation_id="migrate-legacy-main",
        authority=_authority(),
        requirement=_requirement(),
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"],
        created_at=NOW,
        legacy_sources=sources,
        authority_validator=lambda *_args: (_ for _ in ()).throw(
            AssertionError("idempotent replay revalidated authority")),
    )
    assert replay == first
    assert store.load(RUN_ID) == committed


def test_ambiguous_migration_commits_unknown_receipt_without_lifecycle_guess(
        tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)

    arguments = dict(
        workspace=str(workspace), store=store, run_id=RUN_ID,
        expected_revision=initial["revision"],
        operation_id="migrate-ambiguous", authority=_authority(),
        requirement=_requirement(),
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"], created_at=NOW,
        legacy_sources=_sources(_legacy_loop("mystery-state")),
        authority_validator=lambda _expected, _current: None,
    )
    receipt = stage_migration.migrate_singleton(**arguments)
    committed = store.load(RUN_ID)

    assert receipt["stage_ids"] == []
    assert receipt["result"]["classification"] == "legacy-unknown"
    assert receipt["result"]["unknown"]["schema"] == \
        "taskplane.legacy-unknown/v1"
    assert committed["stage_heads"] == {}
    assert committed["active_stage_projection"]["active_stage_ids"] == []
    projection = stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID)
    assert projection["foreground"] is None
    assert projection["stages"] == {}

    replay = stage_migration.migrate_singleton(**{
        **arguments,
        "authority_validator": lambda *_args: (_ for _ in ()).throw(
            AssertionError("replay revalidated authority")),
    })
    assert replay == receipt
    assert store.load(RUN_ID) == committed
