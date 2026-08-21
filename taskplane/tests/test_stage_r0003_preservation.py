"""R-0004 stages preserve the R-0003 authority and cleanup boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import collision
import loop
from run_store import RunStore
import stage_migration
import storage
import taskplane_lite
import worktree_cleanup


RUN_ID = "run-r0003-preservation"
NOW = "2026-08-21T14:00:00Z"


def _store(tmp_path: Path) -> tuple[RunStore, dict, Path]:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    store = RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(workspace),
        host={"kind": "codex", "session_id": "thread-1"},
        target={"kind": "workspace", "revision": "1" * 40},
    )
    return store, manifest, workspace


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": RUN_ID,
        "repository_id": "github.com/example/project",
        "repository_key": "github.com-example-project",
        "worktree_id": "legacy-worktree",
        "target_revision": "1" * 40,
        "worktree_revision": "2" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-1",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _legacy_sources() -> dict[str, bytes]:
    loop = {
        "governance_revision": 2,
        "goal": "Preserve the R-0003 delivery floor",
        "requirement_id": "R-0004",
        "step": "execute",
        "current_task": 0,
        "tasks": [{"id": "t01", "status": "running"}],
        "decisions": [],
        "audit_history": [],
    }
    return {
        "loop.json": (json.dumps(loop, sort_keys=True) + "\n").encode(),
    }


def _merge_receipt(workspace: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": worktree_cleanup.MERGE_SCHEMA,
        "run_id": RUN_ID,
        "task_id": "legacy-task",
        "repository": {
            "repo_id": "github.com/example/project",
            "repository_key": "github.com-example-project",
        },
        "managed_path": str(workspace / "legacy-worker"),
        "branch_ref": "refs/heads/tp/legacy-task",
        "branch_tip": "a" * 40,
        "primary_checkout": str(workspace),
        "primary_ref": "refs/heads/main",
        "primary_tip": "a" * 40,
        "merged_at": 1,
    }
    identity = {key: value for key, value in payload.items()
                if key != "merged_at"}
    payload["receipt_id"] = (
        "merge-" + worktree_cleanup._fingerprint(identity)[:24])
    return worktree_cleanup.validate_merge_receipt(payload)


def _seed_r0003_receipts(
        store: RunStore, manifest: dict, workspace: Path,
        ) -> tuple[dict, dict, dict, dict]:
    decision = collision.classify(
        "agent",
        "orchestrator-supaconductor:worker",
        governed=True,
        run_id=RUN_ID,
        step="execute",
    )
    ledger = collision.record(None, decision, observed_at=1)
    manifest = store.record_foreign_interference(
        RUN_ID,
        expected_revision=manifest["revision"],
        interference=ledger,
    )
    merge = _merge_receipt(workspace)
    manifest = store.record_task_merge(
        RUN_ID,
        expected_revision=manifest["revision"],
        receipt=merge,
    )
    cleanup = worktree_cleanup._result(
        merge,
        "preserved",
        reason="review evidence is still required",
        checks={"evidence_needed": True},
    )
    manifest = store.record_worktree_cleanup(
        RUN_ID,
        expected_revision=manifest["revision"],
        cleanup=cleanup,
    )
    return manifest, ledger, merge, cleanup


def _assert_migration_authority(expected: dict, manifest: dict) -> None:
    assert expected == _authority()
    assert manifest["run_id"] == RUN_ID
    assert manifest["schema"] == "taskplane.run/v4"


@pytest.mark.parametrize("outcome", ["closed", "discarded"])
def test_migration_and_terminalization_preserve_r0003_receipts_without_cleanup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        outcome: str) -> None:
    store, manifest, workspace = _store(tmp_path)
    worker = workspace / "legacy-worker"
    worker.mkdir()
    retained = worker / "evidence.txt"
    retained.write_text("retain me\n", encoding="utf-8")
    manifest, ledger, merge, cleanup = _seed_r0003_receipts(
        store, manifest, workspace)
    r0003 = {
        "foreign_interference": copy.deepcopy(ledger),
        "task_merges": {"legacy-task": copy.deepcopy(merge)},
        "worktree_cleanups": {"legacy-task": copy.deepcopy(cleanup)},
    }

    cleanup_calls: list[str] = []

    def forbidden_cleanup(*_args, **_kwargs):
        cleanup_calls.append("called")
        raise AssertionError(
            "stage migration or terminalization invoked R-0003 cleanup")

    monkeypatch.setattr(worktree_cleanup, "record_merge_receipt",
                        forbidden_cleanup)
    monkeypatch.setattr(worktree_cleanup, "eligibility", forbidden_cleanup)
    monkeypatch.setattr(worktree_cleanup, "cleanup", forbidden_cleanup)

    migration = stage_migration.migrate_singleton(
        str(workspace),
        store=store,
        run_id=RUN_ID,
        expected_revision=manifest["revision"],
        operation_id=f"migrate-before-{outcome}",
        authority=_authority(),
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"],
        created_at=NOW,
        legacy_sources=_legacy_sources(),
        authority_validator=_assert_migration_authority,
    )
    migrated = store.load(RUN_ID)
    stage_id = migration["stage_ids"][0]
    head = migrated["stage_heads"][stage_id]
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: store)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "enabled")
    terminal = loop.stage_command(str(workspace), "terminalize", {
        "schema": "taskplane.stage-command/v1",
        "run_id": RUN_ID,
        "stage_id": stage_id,
        "expected_head_fingerprint": head["object"]["fingerprint"],
        "expected_revision": migrated["revision"],
        "operation_id": f"terminal-{outcome}",
        "outcome": outcome,
        "actor": "human:vdemkiv",
        "terminalized_at": "2026-08-21T14:10:00Z",
        "reason_code": "bounded-stage-complete",
        "reason": "The stage has no remaining authorized work.",
        "authority": _authority(),
    })
    assert "error" not in terminal, terminal
    committed = store.load(RUN_ID)

    assert terminal["receipt"]["result"]["head"]["summary"]["outcome"] == \
        outcome
    history = loop.stage_history(str(workspace), RUN_ID, limit=100)
    assert [(row["stage_id"], row["outcome"])
            for row in history["stages"]] == [(stage_id, outcome)]
    assert set(committed["stage_heads"]) == {stage_id}
    assert committed["active_stage_projection"]["active_stage_ids"] == []
    assert {key: committed[key] for key in r0003} == r0003
    assert cleanup_calls == []
    assert retained.read_text(encoding="utf-8") == "retain me\n"

    # Promotion to v4 must not lock out the existing R-0003 evidence writer.
    updated_ledger = collision.record(
        ledger,
        collision.classify(
            "skill", "unknown:skill", governed=True,
            run_id=RUN_ID, step="execute"),
        observed_at=2,
    )
    stage_heads = copy.deepcopy(committed["stage_heads"])
    updated = store.record_foreign_interference(
        RUN_ID,
        expected_revision=committed["revision"],
        interference=updated_ledger,
    )
    assert updated["schema"] == "taskplane.run/v4"
    assert updated["stage_heads"] == stage_heads
    assert updated["foreign_interference"] == updated_ledger
    assert updated["task_merges"] == r0003["task_merges"]
    assert updated["worktree_cleanups"] == r0003["worktree_cleanups"]
