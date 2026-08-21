"""Atomic stage-operation receipts survive retries, reconnects, and crashes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from taskplane import run_store, stage_entities, storage


RUN_ID = "run-stage-transactions"


def _store(tmp_path: Path) -> tuple[run_store.RunStore, dict]:
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(tmp_path / "checkout"),
        host={"kind": "codex", "session_id": "thread-1"},
        target={"kind": "workspace"},
    )
    return store, manifest


def _empty_stage_index_changes() -> dict[str, object]:
    return {
        "stage_heads": {},
        "lineage": [],
        "active_stage_projection": stage_entities.active_stage_projection({}),
    }


def _mutation(operation: str = "rebuild_active_stage_projection",
              marker: str = "committed"):
    def mutate(_current: dict) -> dict:
        return {
            "changes": _empty_stage_index_changes(),
            "receipt": {
                "operation": operation,
                "stage_ids": [],
                "result": {"marker": marker},
            },
        }

    return mutate


def _reference(kind: str, token: str) -> dict[str, object]:
    fingerprint = token * 64
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": kind,
        "fingerprint": fingerprint,
        "digest": fingerprint,
        "bytes": 128,
        "locator": f"artifact://{kind}/{fingerprint}",
        "transport": "artifact-reference",
    }


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": RUN_ID,
        "repository_id": "github.com/example/project",
        "repository_key": "github.com-example-project",
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


def _root_stage(stage_id: str) -> dict[str, object]:
    return stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=stage_id,
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="build",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=_reference("stage-handoff", "f"),
        execution_root_id=f"execution-{stage_id}",
        deliverables=[f"deliverable-{stage_id}"],
        budget={"tokens": 1000, "seconds": 120},
        dependencies=[],
        contracts=["contract:stage-entity-lifecycle"],
        authority=_authority(),
        created_at="2026-08-21T14:00:00Z",
        selected_artifacts=[],
    )


def _validate_exact_authority(expected: dict, current: dict) -> None:
    if expected != current:
        raise stage_entities.StageLifecycleError("authority is stale")


def _head(store: run_store.RunStore,
          stage: dict[str, object]) -> dict[str, object]:
    return {
        "object": store.put_stage_object(RUN_ID, stage),
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def test_exact_replay_returns_receipt_before_revision_or_authority_checks(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    calls = {"mutate": 0, "authority": 0}

    def authority(_current: dict) -> None:
        calls["authority"] += 1

    def first_mutation(current: dict) -> dict:
        calls["mutate"] += 1
        assert current["revision"] == initial["revision"]
        return _mutation()(current)

    first = store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="start-a",
        request_fingerprint="a" * 64,
        mutate=first_mutation,
        validate_authority=authority,
    )
    committed = store.load(RUN_ID)

    def must_not_run(_current: dict) -> dict:
        raise AssertionError("receipt replay invoked the mutator")

    def stale_authority(_current: dict) -> None:
        raise AssertionError("receipt replay revalidated stale authority")

    replay = store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],  # intentionally stale
        operation_id="start-a",
        request_fingerprint="a" * 64,
        mutate=must_not_run,
        validate_authority=stale_authority,
    )

    assert replay == first
    assert replay["schema"] == "taskplane.stage-operation-receipt/v1"
    assert replay["committed_revision"] == committed["revision"]
    assert calls == {"mutate": 1, "authority": 1}
    assert store.load(RUN_ID) == committed


def test_operation_id_reuse_with_different_request_is_rejected_first(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="terminal-a",
        request_fingerprint="b" * 64,
        mutate=_mutation(),
        validate_authority=lambda _current: None,
    )
    committed = store.load(RUN_ID)

    with pytest.raises(run_store.OperationConflict, match="fingerprint"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],  # also stale
            operation_id="terminal-a",
            request_fingerprint="c" * 64,
            mutate=lambda _current: (_ for _ in ()).throw(
                AssertionError("conflicting replay invoked the mutator")),
        )

    assert store.load(RUN_ID) == committed


def test_ambiguous_singleton_migration_can_commit_receipt_without_stage_head(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)

    receipt = store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="migrate-ambiguous",
        request_fingerprint="9" * 64,
        mutate=_mutation(
            operation="migrate_singleton", marker="legacy-unknown"),
        validate_authority=lambda _current: None,
    )

    committed = store.load(RUN_ID)
    assert receipt["operation"] == "migrate_singleton"
    assert receipt["stage_ids"] == []
    assert committed["stage_heads"] == {}
    assert committed["stage_operations"]["migrate-ambiguous"] == receipt


def test_unrelated_operation_cannot_commit_receipt_without_stage_head(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)

    with pytest.raises(run_store.StageStateError, match="must not be empty"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="empty-split",
            request_fingerprint="8" * 64,
            mutate=_mutation(operation="split", marker="invalid"),
            validate_authority=lambda _current: None,
        )


def test_post_commit_journal_crash_reconnects_through_stored_receipt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, initial = _store(tmp_path)
    original_append = store._append_journal

    def crash_after_manifest(_run_id: str, _event: dict) -> None:
        raise OSError("injected journal crash")

    monkeypatch.setattr(store, "_append_journal", crash_after_manifest)
    with pytest.raises(OSError, match="injected journal crash"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="handoff-a",
            request_fingerprint="d" * 64,
            mutate=_mutation(marker="handoff-committed"),
            validate_authority=lambda _current: None,
        )

    persisted = store.load(RUN_ID)
    stored_receipt = persisted["stage_operations"]["handoff-a"]
    assert persisted["schema"] == "taskplane.run/v4"

    monkeypatch.setattr(store, "_append_journal", original_append)
    replay = store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="handoff-a",
        request_fingerprint="d" * 64,
        mutate=lambda _current: (_ for _ in ()).throw(
            AssertionError("reconnect duplicated the handoff")),
    )

    assert replay == stored_receipt
    assert store.load(RUN_ID) == persisted


@pytest.mark.parametrize(
    "bad_mutation",
    [
        lambda _current: (_ for _ in ()).throw(ValueError("invalid child")),
        lambda _current: {
            "changes": {"revision": 99},
            "receipt": {
                "operation": "split", "stage_ids": ["stage-a"]
            },
        },
        lambda _current: {
            "changes": {"stage_test_marker": "must-not-commit"},
            "receipt": {
                "operation": "split", "stage_ids": ["stage-a"]
            },
        },
        lambda _current: {
            "changes": _empty_stage_index_changes(),
            "receipt": {
                "operation": "split",
                "stage_ids": ["stage-a", "stage-a"],
            },
        },
    ],
    ids=["mutator-rejects", "reserved-key", "custom-top-level-key",
         "invalid-receipt"],
)
def test_failed_operation_preserves_the_entire_manifest(
        tmp_path: Path, bad_mutation) -> None:
    store, initial = _store(tmp_path)
    path = Path(store._manifest_path(RUN_ID))
    before_bytes = path.read_bytes()
    before = json.loads(before_bytes)

    with pytest.raises((ValueError, run_store.RunStoreError)):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="split-a",
            request_fingerprint="e" * 64,
            mutate=bad_mutation,
            validate_authority=lambda _current: None,
        )

    assert path.read_bytes() == before_bytes
    assert store.load(RUN_ID) == before
    assert "split-a" not in store.load(RUN_ID).get("stage_operations", {})


def test_new_operation_requires_an_authoritative_validation_callback(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    before = store.load(RUN_ID)

    with pytest.raises(run_store.RunStoreError, match="authority"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="start-without-authority",
            request_fingerprint="8" * 64,
            mutate=_mutation(),
        )

    assert store.load(RUN_ID) == before


def test_generic_commit_cannot_seed_or_overwrite_stage_authority(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    initial_bytes = Path(store._manifest_path(RUN_ID)).read_bytes()

    with pytest.raises(run_store.RunStoreError, match="stage"):
        store.commit(
            RUN_ID,
            expected_revision=initial["revision"],
            changes={"stage_heads": {}},
        )
    assert Path(store._manifest_path(RUN_ID)).read_bytes() == initial_bytes

    store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="promote-v4",
        request_fingerprint="7" * 64,
        mutate=_mutation(),
        validate_authority=lambda _current: None,
    )
    promoted = store.load(RUN_ID)

    for changes in (
            {"stage_heads": {}},
            {"lineage": []},
            {"stage_operations": {}},
            {"active_stage_projection": {}},
            {"schema": "taskplane.run/v3"}):
        with pytest.raises(run_store.RunStoreError):
            store.commit(
                RUN_ID,
                expected_revision=promoted["revision"],
                changes=changes,
            )
        assert store.load(RUN_ID) == promoted


def test_corrupt_stored_receipt_is_rejected_before_replay(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    store.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="start-a",
        request_fingerprint="6" * 64,
        mutate=_mutation(),
        validate_authority=lambda _current: None,
    )
    path = Path(store._manifest_path(RUN_ID))
    corrupt = json.loads(path.read_bytes())
    corrupt["stage_operations"]["start-a"]["result_fingerprint"] = "bad"
    path.write_text(
        json.dumps(corrupt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    corrupt_bytes = path.read_bytes()

    with pytest.raises(run_store.RunStoreError, match="receipt|fingerprint"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="start-a",
            request_fingerprint="6" * 64,
            mutate=lambda _current: (_ for _ in ()).throw(
                AssertionError("corrupt replay invoked mutator")),
            validate_authority=lambda _current: (_ for _ in ()).throw(
                AssertionError("corrupt replay invoked authority")),
        )

    assert path.read_bytes() == corrupt_bytes


def test_stage_operation_rejects_missing_immutable_head_object(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    stage = _root_stage("stage-a")
    summary = stage_entities.bounded_stage_summary(stage)
    missing_ref = {
        "schema": "taskplane.stage-object-ref/v1",
        "stage_id": stage["stage_id"],
        "fingerprint": stage["fingerprint"],
        "digest": "0" * 64,
        "bytes": 1,
        "locator": (
            f"stages/objects/{stage['stage_id']}/{stage['fingerprint']}.json"
        ),
    }
    path = Path(store._manifest_path(RUN_ID))
    before = path.read_bytes()

    with pytest.raises(run_store.RunStoreError, match="object|unavailable"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="missing-stage-object",
            request_fingerprint="5" * 64,
            mutate=lambda _current: {
                "changes": {
                    "stage_heads": {
                        stage["stage_id"]: {
                            "object": missing_ref, "summary": summary,
                        },
                    },
                    "lineage": [],
                    "active_stage_projection":
                        stage_entities.active_stage_projection({
                            stage["stage_id"]: {
                                "object": missing_ref, "summary": summary,
                            },
                        }),
                },
                "receipt": {
                    "operation": "start_stage",
                    "stage_ids": [stage["stage_id"]],
                },
            },
            validate_authority=lambda _current: None,
        )

    assert path.read_bytes() == before


def test_stage_operation_rejects_object_summary_semantic_mismatch(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    active = _root_stage("stage-a")
    terminal = stage_entities.terminalize_stage(
        active,
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:10:00Z",
        reason_code="complete",
        reason="Create a valid but mismatched summary fixture.",
    )
    mismatched_head = {
        "object": store.put_stage_object(RUN_ID, active),
        "summary": stage_entities.bounded_stage_summary(terminal),
    }
    path = Path(store._manifest_path(RUN_ID))
    before = path.read_bytes()

    with pytest.raises(run_store.RunStoreError, match="summary|fingerprint"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="mismatched-stage-summary",
            request_fingerprint="4" * 64,
            mutate=lambda _current: {
                "changes": {
                    "stage_heads": {active["stage_id"]: mismatched_head},
                    "lineage": [],
                    "active_stage_projection":
                        stage_entities.active_stage_projection({}),
                },
                "receipt": {
                    "operation": "start_stage",
                    "stage_ids": [active["stage_id"]],
                },
            },
            validate_authority=lambda _current: None,
        )

    assert path.read_bytes() == before


def test_stage_operation_receipt_must_name_every_changed_sibling(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    first = _root_stage("stage-a")
    second = _root_stage("stage-b")
    heads = {
        first["stage_id"]: _head(store, first),
        second["stage_id"]: _head(store, second),
    }
    path = Path(store._manifest_path(RUN_ID))
    before = path.read_bytes()

    with pytest.raises(run_store.RunStoreError, match="changed heads"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="underreported-sibling",
            request_fingerprint="3" * 64,
            mutate=lambda _current: {
                "changes": {
                    "stage_heads": heads,
                    "lineage": [],
                    "active_stage_projection":
                        stage_entities.active_stage_projection(heads),
                },
                "receipt": {
                    "operation": "start_stage",
                    "stage_ids": [first["stage_id"]],
                },
            },
            validate_authority=lambda _current: None,
        )

    assert path.read_bytes() == before


def test_lifecycle_start_indexes_root_head_and_projection_in_one_revision(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
    )
    stage = _root_stage("stage-root-a")

    receipt = lifecycle.start_stage(
        stage,
        expected_revision=initial["revision"],
        operation_id="start-root-a",
    )

    persisted = store.load(RUN_ID)
    head = receipt["result"]["head"]
    assert persisted["schema"] == "taskplane.run/v4"
    assert persisted["revision"] == initial["revision"] + 1
    assert persisted["stage_heads"] == {stage["stage_id"]: head}
    assert head["summary"]["aggregate_fingerprint"] == stage["fingerprint"]
    assert head["summary"]["state"] == "active"
    assert persisted["active_stage_projection"] == \
        receipt["result"]["active_stage_projection"]
    assert persisted["active_stage_projection"]["active_stage_ids"] == [
        stage["stage_id"]
    ]
    assert persisted["stage_operations"]["start-root-a"] == receipt


def test_lifecycle_terminalize_replaces_only_addressed_head_and_replays(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    authority_checks = 0

    def authority_validator(expected: dict, current: dict) -> None:
        nonlocal authority_checks
        authority_checks += 1
        if expected != current:
            raise stage_entities.StageLifecycleError("authority is stale")

    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=authority_validator,
    )
    first = _root_stage("stage-root-a")
    second = _root_stage("stage-root-b")
    lifecycle.start_stage(
        first, expected_revision=initial["revision"],
        operation_id="start-root-a")
    after_first = store.load(RUN_ID)
    lifecycle.start_stage(
        second, expected_revision=after_first["revision"],
        operation_id="start-root-b",
        foreground=False)
    before_terminal = store.load(RUN_ID)
    sibling_before = before_terminal["stage_heads"][second["stage_id"]]

    receipt = lifecycle.terminalize(
        RUN_ID,
        stage_id=first["stage_id"],
        expected_head_fingerprint=first["fingerprint"],
        expected_revision=before_terminal["revision"],
        operation_id="terminal-root-a",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:10:00Z",
        reason_code="complete",
        reason="No further work is required in this stage.",
    )
    committed = store.load(RUN_ID)
    checks_after_commit = authority_checks

    replay = lifecycle.terminalize(
        RUN_ID,
        stage_id=first["stage_id"],
        expected_head_fingerprint=first["fingerprint"],
        expected_revision=before_terminal["revision"],  # now stale
        operation_id="terminal-root-a",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:10:00Z",
        reason_code="complete",
        reason="No further work is required in this stage.",
    )

    assert replay == receipt
    assert authority_checks == checks_after_commit
    assert store.load(RUN_ID) == committed
    assert committed["stage_heads"][second["stage_id"]] == sibling_before
    assert committed["stage_heads"][first["stage_id"]] == \
        receipt["result"]["head"]
    assert receipt["result"]["head"]["summary"]["state"] == "terminal"
    assert receipt["result"]["head"]["summary"]["outcome"] == "closed"
    assert committed["active_stage_projection"]["active_stage_ids"] == [
        second["stage_id"]
    ]


@pytest.mark.parametrize("failure", ["authority", "head"])
def test_lifecycle_stale_authority_or_head_leaves_manifest_unchanged(
        tmp_path: Path, failure: str) -> None:
    store, initial = _store(tmp_path)

    current_authority = _authority()
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: current_authority,
        authority_validator=_validate_exact_authority,
    )
    stage = _root_stage("stage-root-a")
    lifecycle.start_stage(
        stage, expected_revision=initial["revision"],
        operation_id="start-root-a")
    before = store.load(RUN_ID)
    expected_head = stage["fingerprint"]
    error = "head"
    if failure == "authority":
        current_authority["authority_revision"] = 8
        error = "authority"
    else:
        expected_head = "9" * 64

    with pytest.raises(stage_entities.StageLifecycleError, match=error):
        lifecycle.terminalize(
            RUN_ID,
            stage_id=stage["stage_id"],
            expected_head_fingerprint=expected_head,
            expected_revision=before["revision"],
            operation_id=f"invalid-{failure}",
            outcome="closed",
            actor="human:vdemkiv",
            terminalized_at="2026-08-21T14:10:00Z",
            reason_code="complete",
            reason="This request must fail before commit.",
        )

    assert store.load(RUN_ID) == before


def test_resume_claims_fresh_attempts_and_exact_replay_claims_nothing(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    stage = _root_stage("stage-root-a")
    claims: list[dict] = []
    authority_checks = 0

    def validate_authority(expected: dict, current: dict) -> None:
        nonlocal authority_checks
        authority_checks += 1
        _validate_exact_authority(expected, current)

    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=validate_authority,
        execution_root_claimer=lambda claim: claims.append(dict(claim)),
    )
    lifecycle.start_stage(
        stage,
        expected_revision=initial["revision"],
        operation_id="start-root-a",
    )
    before_resume = store.load(RUN_ID)
    head_before = before_resume["stage_heads"][stage["stage_id"]]
    lineage_before = before_resume["lineage"]
    projection_before = before_resume["active_stage_projection"]
    claims.clear()

    receipt = lifecycle.resume_stage(
        RUN_ID,
        stage_id=stage["stage_id"],
        expected_head_fingerprint=stage["fingerprint"],
        expected_revision=before_resume["revision"],
        operation_id="resume-root-a-1",
    )
    committed = store.load(RUN_ID)
    checks_after_commit = authority_checks
    claims_after_commit = copy.deepcopy(claims)

    replay = lifecycle.resume_stage(
        RUN_ID,
        stage_id=stage["stage_id"],
        expected_head_fingerprint=stage["fingerprint"],
        expected_revision=before_resume["revision"],  # intentionally stale
        operation_id="resume-root-a-1",
    )

    assert replay == receipt
    assert authority_checks == checks_after_commit
    assert claims == claims_after_commit
    assert store.load(RUN_ID) == committed
    assert committed["stage_heads"][stage["stage_id"]] == head_before
    assert committed["lineage"] == lineage_before
    assert committed["active_stage_projection"] == projection_before
    assert receipt["stage_ids"] == [stage["stage_id"]]
    assert receipt["result"]["stage_id"] == stage["stage_id"]
    assert receipt["result"]["stage_fingerprint"] == stage["fingerprint"]
    assert receipt["result"]["execution_root_id"] == \
        stage["execution_root_id"]
    assert receipt["result"]["claim"] == {
        "schema": "taskplane.stage-execution-attempt-claim/v1",
        "run_id": RUN_ID,
        "stage_id": stage["stage_id"],
        "execution_root_id": stage["execution_root_id"],
        "attempt_id": receipt["result"]["attempt_id"],
    }
    assert "root" not in receipt["result"]["claim"]

    second = lifecycle.resume_stage(
        RUN_ID,
        stage_id=stage["stage_id"],
        expected_head_fingerprint=stage["fingerprint"],
        expected_revision=committed["revision"],
        operation_id="resume-root-a-2",
    )
    assert second["result"]["attempt_id"] != \
        receipt["result"]["attempt_id"]
    assert second["result"]["execution_root_id"] == \
        receipt["result"]["execution_root_id"]


def test_terminal_stage_cannot_resume_and_manifest_stays_unchanged(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    stage = _root_stage("stage-root-a")
    claims: list[dict] = []
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
        execution_root_claimer=lambda claim: claims.append(dict(claim)),
    )
    lifecycle.start_stage(
        stage,
        expected_revision=initial["revision"],
        operation_id="start-root-a",
    )
    active = store.load(RUN_ID)
    terminal_receipt = lifecycle.terminalize(
        RUN_ID,
        stage_id=stage["stage_id"],
        expected_head_fingerprint=stage["fingerprint"],
        expected_revision=active["revision"],
        operation_id="terminal-root-a",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:10:00Z",
        reason_code="complete",
        reason="No further execution attempt is permitted.",
    )
    before = store.load(RUN_ID)
    claims_before = copy.deepcopy(claims)
    terminal_fingerprint = terminal_receipt["result"]["head"]["object"][
        "fingerprint"]

    with pytest.raises(stage_entities.StageLifecycleError,
                       match="active|terminal|resume"):
        lifecycle.resume_stage(
            RUN_ID,
            stage_id=stage["stage_id"],
            expected_head_fingerprint=terminal_fingerprint,
            expected_revision=before["revision"],
            operation_id="resume-terminal-root-a",
        )

    assert store.load(RUN_ID) == before
    assert claims == claims_before


def test_execution_root_rename_substitution_fails_before_start_commit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, initial = _store(tmp_path)
    stage = _root_stage("stage-root-race")
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
    )
    manifest_path = Path(store._manifest_path(RUN_ID))
    before_bytes = manifest_path.read_bytes()
    swapped_paths: list[str] = []

    def substitute_after_claim(path: str) -> None:
        swapped_paths.append(path)
        original = f"{path}.original"
        claim_name = storage.STAGE_EXECUTION_ROOT_CLAIM
        claim_bytes = Path(path, claim_name).read_bytes()
        os.rename(path, original)
        os.mkdir(path, 0o700)
        Path(path, claim_name).write_bytes(claim_bytes)

    monkeypatch.setattr(
        storage, "_before_stage_execution_root_reopen",
        substitute_after_claim)

    with pytest.raises(storage.StorageIdentityError,
                       match="stage execution root changed during claim"):
        lifecycle.start_stage(
            stage,
            expected_revision=initial["revision"],
            operation_id="start-root-race",
        )

    assert len(swapped_paths) == 1
    assert manifest_path.read_bytes() == before_bytes
    assert store.load(RUN_ID)["revision"] == initial["revision"]
    assert "stage_heads" not in store.load(RUN_ID)
