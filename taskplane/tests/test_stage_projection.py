"""The active-stage pointer is a rebuildable, non-authoritative projection."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os

import pytest

from taskplane import run_store, stage_entities, storage


def _portable_ref(marker: str = "a") -> dict[str, object]:
    fingerprint = marker * 64
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": "stage-handoff",
        "fingerprint": fingerprint,
        "digest": marker * 64,
        "bytes": 128,
        "locator": f"artifact://stage-handoff/{fingerprint}",
        "transport": "artifact-reference",
    }


def _authority(run_id: str) -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": run_id,
        "repository_id": "github.com/vdemkiv/taskplane",
        "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "t02-worktree",
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


def _stage(stage_id: str, *, run_id: str = "run-projection",
           terminal: bool = False) -> dict[str, object]:
    stage = stage_entities.create_stage(
        run_id=run_id,
        stage_id=stage_id,
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="build",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=_portable_ref(),
        execution_root_id=f"execution-{stage_id}",
        deliverables=["commit"],
        selected_artifacts=[],
        budget={"token_limit": 8_000},
        dependencies=[],
        contracts=["contract:stage-entity-lifecycle"],
        authority=_authority(run_id),
        created_at="2026-08-21T12:00:00Z",
    )
    if not terminal:
        return stage
    return stage_entities.terminalize_stage(
        stage, outcome="closed", actor="human:vdemkiv",
        terminal_at="2026-08-21T13:00:00Z", reason_code="complete",
        reason="This predecessor has no remaining work.")


def _head(stage: dict[str, object], *,
          store: run_store.RunStore | None = None) -> dict[str, object]:
    stage_id = str(stage["stage_id"])
    fingerprint = str(stage["fingerprint"])
    reference = (store.put_stage_object("run-projection", stage)
                 if store is not None else {
                     "schema": "taskplane.stage-object-ref/v1",
                     "stage_id": stage_id,
                     "fingerprint": fingerprint,
                     "digest": "e" * 64,
                     "bytes": 128,
                     "locator": (
                         f"stages/objects/{stage_id}/{fingerprint}.json"),
                 })
    return {
        "object": reference,
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def _heads(*, store: run_store.RunStore | None = None
           ) -> dict[str, dict[str, object]]:
    values = [
        _stage("stage-active-z"),
        _stage("stage-terminal-m", terminal=True),
        _stage("stage-active-a"),
    ]
    return {
        str(stage["stage_id"]): _head(stage, store=store)
        for stage in values
    }


def _seed_v4(store: run_store.RunStore, manifest: dict[str, object],
             heads: dict[str, dict[str, object]]) -> dict[str, object]:
    projection = stage_entities.active_stage_projection(heads)

    def mutate(_current: dict[str, object]) -> dict[str, object]:
        return {
            "changes": {
                "stage_heads": heads,
                "lineage": [],
                "active_stage_projection": projection,
            },
            "receipt": {
                "operation": "seed_projection_fixture",
                "stage_ids": sorted(heads),
                "result": {"projection": projection},
            },
        }

    store.commit_stage_operation(
        "run-projection", expected_revision=int(manifest["revision"]),
        operation_id="seed-projection-fixture",
        request_fingerprint="6" * 64, mutate=mutate,
        validate_authority=lambda _current: None)
    return store.load("run-projection")


def _rewrite_projection_fixture(
        store: run_store.RunStore, manifest: dict[str, object],
        projection: object, *, missing: bool = False) -> dict[str, object]:
    rewritten = copy.deepcopy(manifest)
    if missing:
        rewritten.pop("active_stage_projection", None)
    else:
        rewritten["active_stage_projection"] = copy.deepcopy(projection)
    run_store._atomic_write_json(
        store._manifest_path("run-projection"), rewritten)
    return rewritten


def test_projection_is_derived_only_from_authoritative_heads() -> None:
    heads = _heads()

    projection = stage_entities.active_stage_projection(heads)
    reverse_projection = stage_entities.active_stage_projection(
        dict(reversed(list(heads.items()))))

    assert projection == reverse_projection
    assert projection["schema"] == "taskplane.active-stage-projection/v1"
    assert projection["active_stage_ids"] == [
        "stage-active-a", "stage-active-z",
    ]
    assert projection["foreground_stage_id"] is None
    assert len(projection["fingerprint"]) == 64

    explicit = stage_entities.active_stage_projection(
        heads, foreground_stage_id="stage-active-z")
    assert explicit["foreground_stage_id"] == "stage-active-z"

    terminal_is_not_foreground = stage_entities.active_stage_projection(
        heads, foreground_stage_id="stage-terminal-m")
    assert terminal_is_not_foreground["foreground_stage_id"] is None


def test_projection_never_uses_pointer_state_to_reclassify_history() -> None:
    heads = _heads()
    fingerprints = {
        stage_id: head["object"]["fingerprint"]
        for stage_id, head in heads.items()
    }
    forged_pointer = {
        "schema": "taskplane.active-stage-projection/v1",
        "active_stage_ids": ["stage-terminal-m"],
        "foreground_stage_id": "stage-terminal-m",
        "fingerprint": "0" * 64,
    }

    rebuilt = stage_entities.active_stage_projection(heads)

    assert rebuilt["active_stage_ids"] == [
        "stage-active-a", "stage-active-z",
    ]
    assert forged_pointer["active_stage_ids"] != rebuilt["active_stage_ids"]
    assert {
        stage_id: head["object"]["fingerprint"]
        for stage_id, head in heads.items()
    } == fingerprints
    assert heads["stage-terminal-m"]["summary"]["state"] == "terminal"
    assert heads["stage-terminal-m"]["summary"]["outcome"] == "closed"


def test_projection_rejects_oversized_self_consistent_head_summary() -> None:
    heads = _heads()
    summary = heads["stage-active-a"]["summary"]
    summary["reason"] = "x" * (16 * 1024)
    summary["fingerprint"] = stage_entities.request_fingerprint({
        key: value for key, value in summary.items() if key != "fingerprint"
    })

    with pytest.raises(ValueError, match="summary"):
        stage_entities.active_stage_projection(heads)


def test_projection_rejects_tampered_summary_fingerprint() -> None:
    heads = _heads()
    heads["stage-active-a"]["summary"]["state"] = "terminal"

    with pytest.raises(ValueError, match="fingerprint"):
        stage_entities.active_stage_projection(heads)


def test_projection_rejects_object_and_summary_fingerprint_mismatch() -> None:
    heads = _heads()
    heads["stage-active-a"]["object"]["fingerprint"] = "0" * 64

    with pytest.raises(ValueError, match="fingerprint"):
        stage_entities.active_stage_projection(heads)


def test_run_store_cannot_index_a_syntactic_ref_without_immutable_object(
        tmp_path) -> None:
    checkout = tmp_path / "checkout"
    os.makedirs(checkout)
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git", workspace=str(checkout))
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-projection", checkout=str(checkout),
        host={"kind": "test"}, target={"branch": "main"})
    original = copy.deepcopy(manifest)

    with pytest.raises(run_store.StageStateError):
        _seed_v4(store, manifest, _heads())

    assert store.load("run-projection") == original


def test_run_store_cannot_index_summary_that_disagrees_with_stored_object(
        tmp_path) -> None:
    checkout = tmp_path / "checkout"
    os.makedirs(checkout)
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git", workspace=str(checkout))
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-projection", checkout=str(checkout),
        host={"kind": "test"}, target={"branch": "main"})
    original = copy.deepcopy(manifest)
    heads = _heads(store=store)
    summary = heads["stage-active-a"]["summary"]
    summary["stage_kind"] = "evaluate"
    summary["fingerprint"] = stage_entities.request_fingerprint({
        key: value for key, value in summary.items() if key != "fingerprint"
    })

    with pytest.raises(run_store.StageStateError):
        _seed_v4(store, manifest, heads)

    assert store.load("run-projection") == original


@pytest.mark.parametrize("case", ["missing", "corrupt", "stale", "ambiguous"])
def test_run_store_repairs_bad_projection_under_lock_without_guessing(
        tmp_path, monkeypatch, case: str) -> None:
    checkout = tmp_path / "checkout"
    os.makedirs(checkout)
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git", workspace=str(checkout))
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-projection", checkout=str(checkout),
        host={"kind": "test"}, target={"branch": "main"})
    heads = _heads(store=store)
    expected = stage_entities.active_stage_projection(heads)
    if case == "missing":
        supplied = None
    elif case == "corrupt":
        supplied = {"schema": "corrupt", "active_stage_ids": "not-a-list"}
    elif case == "stale":
        supplied = copy.deepcopy(expected)
        supplied["active_stage_ids"] = ["stage-terminal-m"]
    else:
        supplied = copy.deepcopy(expected)
        supplied["foreground_stage_id"] = "stage-terminal-m"
    manifest = _seed_v4(store, manifest, heads)
    manifest = _rewrite_projection_fixture(
        store, manifest, supplied, missing=supplied is None)

    real_lock = run_store._lock
    lock_observation = {"entries": 0, "held": False}

    @contextmanager
    def observed_lock(path: str):
        with real_lock(path):
            lock_observation["entries"] += 1
            lock_observation["held"] = True
            try:
                yield
            finally:
                lock_observation["held"] = False

    monkeypatch.setattr(run_store, "_lock", observed_lock)
    repaired = store.rebuild_active_stage_projection(
        "run-projection", expected_revision=manifest["revision"])

    assert lock_observation["entries"] >= 1
    assert lock_observation["held"] is False
    assert repaired["revision"] == manifest["revision"] + 1
    assert repaired["active_stage_projection"] == expected
    assert repaired["active_stage_projection"]["foreground_stage_id"] is None
    assert repaired["stage_heads"] == heads

    replay = store.rebuild_active_stage_projection(
        "run-projection", expected_revision=repaired["revision"])
    assert replay == repaired
    assert store.load("run-projection")["revision"] == repaired["revision"]


def test_projection_repair_rejects_stale_run_revision_without_mutation(
        tmp_path) -> None:
    checkout = tmp_path / "checkout"
    os.makedirs(checkout)
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git", workspace=str(checkout))
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-projection", checkout=str(checkout),
        host={"kind": "test"}, target={"branch": "main"})
    heads = _heads(store=store)
    manifest = _seed_v4(store, manifest, heads)
    manifest = _rewrite_projection_fixture(
        store, manifest, None, missing=True)

    with pytest.raises(run_store.RevisionConflict):
        store.rebuild_active_stage_projection(
            "run-projection", expected_revision=manifest["revision"] - 1)

    unchanged = store.load("run-projection")
    assert unchanged["revision"] == manifest["revision"]
    assert "active_stage_projection" not in unchanged


def test_projection_repair_receipt_replays_before_stale_revision_check(
        tmp_path) -> None:
    checkout = tmp_path / "checkout"
    os.makedirs(checkout)
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git", workspace=str(checkout))
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-projection", checkout=str(checkout),
        host={"kind": "test"}, target={"branch": "main"})
    heads = _heads(store=store)
    manifest = _seed_v4(store, manifest, heads)
    manifest = _rewrite_projection_fixture(store, manifest, {
        "schema": "corrupt", "active_stage_ids": "not-a-list",
    })
    original_revision = manifest["revision"]

    repaired = store.rebuild_active_stage_projection(
        "run-projection", expected_revision=original_revision,
        operation_id="repair-projection-op")
    assert repaired["revision"] == original_revision + 1
    assert "repair-projection-op" in repaired["stage_operations"]
    with open(store._journal_path("run-projection"), "rb") as stream:
        journal_after_repair = stream.read()

    replay = store.rebuild_active_stage_projection(
        "run-projection", expected_revision=original_revision,
        operation_id="repair-projection-op")

    assert replay == repaired
    with open(store._journal_path("run-projection"), "rb") as stream:
        assert stream.read() == journal_after_repair

    with pytest.raises(run_store.RunStoreError):
        store.rebuild_active_stage_projection(
            "run-projection", expected_revision=original_revision,
            foreground_stage_id="stage-active-z",
            operation_id="repair-projection-op")
    assert store.load("run-projection") == repaired
    with open(store._journal_path("run-projection"), "rb") as stream:
        assert stream.read() == journal_after_repair
