"""Behavioral contract for immutable, bounded stage aggregates."""
from __future__ import annotations

import copy
import json

import pytest

from taskplane import stage_entities


def _portable_ref(kind: str = "stage-handoff", *, marker: str = "a",
                  size: int = 128) -> dict[str, object]:
    fingerprint = marker * 64
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": kind,
        "fingerprint": fingerprint,
        "digest": marker * 64,
        "bytes": size,
        "locator": f"artifact://{kind}/{fingerprint}",
        "transport": "artifact-reference",
    }


def _authority(*, run_id: str = "run-r0004") -> dict[str, object]:
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


def _stage(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "run_id": "run-r0004",
        "stage_id": "stage-build-001",
        "requirement": {
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        "design": {"revision": "2", "fingerprint": "c" * 64},
        "stage_kind": "build",
        "parent_stage_ids": ["stage-plan-002", "stage-plan-001"],
        "predecessor_stage_ids": ["stage-design-002", "stage-product-001"],
        "input_manifest_ref": _portable_ref(),
        "execution_root_id": "execution-stage-build-001",
        "deliverables": ["declared-tests", "commit"],
        "selected_artifacts": [
            _portable_ref("source", marker="f"),
            _portable_ref("design", marker="e"),
        ],
        "budget": {"token_limit": 8_000, "attempt_limit": 3},
        "dependencies": ["t01-bounded-handoff-artifact-boundary"],
        "contracts": [
            "contract:stage-entity-lifecycle",
            "contract:delivery-lineage",
        ],
        "authority": _authority(),
        "created_at": "2026-08-21T12:00:00Z",
    }
    values.update(changes)
    return stage_entities.create_stage(**values)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")


def _split_specs() -> list[dict[str, object]]:
    return [
        {
            "stage_kind": "build",
            "selected_artifacts": [_portable_ref("design", marker="e")],
            "dependencies": ["stage-build-001"],
            "budget": {"token_limit": 4_000},
            "deliverables": ["commit"],
            "contracts": ["contract:delivery-lineage"],
            "input_manifest_ref": _portable_ref(),
        },
        {
            "stage_kind": "evaluate",
            "selected_artifacts": [_portable_ref("source", marker="f")],
            "dependencies": ["child:0"],
            "budget": {"token_limit": 2_000},
            "deliverables": ["declared-tests"],
            "contracts": ["contract:delivery-lineage"],
            "input_manifest_ref": _portable_ref(),
        },
    ]


def test_create_stage_is_stable_canonical_and_bounded() -> None:
    first = _stage()
    second = _stage()

    assert first == second
    assert first["schema"] == "taskplane.stage/v1"
    assert first["stage_id"] == "stage-build-001"
    assert first["run_id"] == "run-r0004"
    assert first["requirement"] == {
        "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
    }
    assert first["design"] == {"revision": "2", "fingerprint": "c" * 64}
    assert first["stage_kind"] == "build"
    assert first["parent_stage_ids"] == ["stage-plan-001", "stage-plan-002"]
    assert first["predecessor_stage_ids"] == [
        "stage-design-002", "stage-product-001",
    ]
    assert first["execution_root_id"] == "execution-stage-build-001"
    assert [row["kind"] for row in first["selected_artifacts"]] == [
        "design", "source",
    ]
    assert first["state"] == "active"
    assert first["outcome"] is None
    assert first["terminal"] is None
    assert first["default_consumable"] is True
    assert first["aggregate_revision"] == 1
    assert stage_entities.validate_stage(copy.deepcopy(first)) == first
    assert stage_entities.stage_fingerprint(first) == first["fingerprint"]
    assert len(first["fingerprint"]) == 64

    oversized_ref = _portable_ref(size=(64 * 1024) + 1)
    with pytest.raises(ValueError):
        _stage(input_manifest_ref=oversized_ref)


def test_stage_and_run_ids_use_storage_safe_path_components() -> None:
    assert _stage()["authority"]["actor"] == "human:vdemkiv"

    with pytest.raises(ValueError):
        _stage(
            stage_id="stage:build-001",
            execution_root_id="execution-stage:build-001")
    unsafe_run = "run:r0004"
    with pytest.raises(ValueError):
        _stage(run_id=unsafe_run, authority=_authority(run_id=unsafe_run))
    with pytest.raises(ValueError):
        _stage(parent_stage_ids=["stage:plan-parent"])
    with pytest.raises(ValueError):
        _stage(predecessor_stage_ids=["stage:design-predecessor"])


@pytest.mark.parametrize(
    "root_id",
    [
        "execution-other-stage",
        "stage-build-001",
        "execution-stage-build-001-attempt-2",
    ],
)
def test_execution_root_identity_is_deterministically_bound_to_stage_id(
        root_id: str) -> None:
    with pytest.raises(ValueError, match="execution root"):
        _stage(execution_root_id=root_id)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unexpected_runtime_context": {}}),
        lambda value: value["requirement"].pop("revision"),
        lambda value: value.update({"execution_root_id": "../predecessor"}),
        lambda value: value.update({"state": "terminal", "outcome": None}),
        lambda value: value.update({"state": "active", "outcome": "done"}),
        lambda value: value["parent_stage_ids"].append(
            value["parent_stage_ids"][0]),
    ],
)
def test_validate_stage_rejects_open_or_ambiguous_entities(mutate) -> None:
    stage = copy.deepcopy(_stage())
    mutate(stage)

    with pytest.raises(ValueError):
        stage_entities.validate_stage(stage)


def test_done_requires_all_deliverables_and_completion_evidence() -> None:
    active = _stage()

    with pytest.raises(ValueError):
        stage_entities.terminalize_stage(
            active, outcome="done", actor="human:vdemkiv",
            terminal_at="2026-08-21T13:00:00Z",
            completed_deliverables=["commit"],
            completion_evidence=[_portable_ref("test-evidence", marker="9")])
    with pytest.raises(ValueError):
        stage_entities.terminalize_stage(
            active, outcome="done", actor="human:vdemkiv",
            terminal_at="2026-08-21T13:00:00Z",
            completed_deliverables=["commit", "declared-tests"],
            completion_evidence=[])

    terminal = stage_entities.terminalize_stage(
        active, outcome="done", actor="human:vdemkiv",
        terminal_at="2026-08-21T13:00:00Z",
        completed_deliverables=["declared-tests", "commit"],
        completion_evidence=[_portable_ref("test-evidence", marker="9")])

    assert terminal["state"] == "terminal"
    assert terminal["outcome"] == "done"
    assert terminal["default_consumable"] is True
    assert terminal["terminal"]["completed_deliverables"] == [
        "commit", "declared-tests",
    ]
    assert terminal["terminal"]["completion_evidence"]
    assert terminal["aggregate_revision"] == active["aggregate_revision"] + 1
    assert stage_entities.validate_stage(terminal) == terminal


@pytest.mark.parametrize("outcome", ["closed", "discarded"])
def test_non_done_outcomes_require_attributable_reasons(outcome: str) -> None:
    active = _stage()
    base = {
        "outcome": outcome,
        "actor": "human:vdemkiv",
        "terminal_at": "2026-08-21T13:00:00Z",
        "reason_code": "superseded",
        "reason": "A successor will replace this bounded delivery.",
    }
    for missing in ("actor", "terminal_at", "reason_code", "reason"):
        invalid = dict(base)
        invalid[missing] = ""
        with pytest.raises(ValueError):
            stage_entities.terminalize_stage(active, **invalid)

    terminal = stage_entities.terminalize_stage(active, **base)

    assert terminal["outcome"] == outcome
    assert terminal["terminal"]["actor"] == "human:vdemkiv"
    assert terminal["terminal"]["reason_code"] == "superseded"
    assert terminal["terminal"]["reason"] == base["reason"]
    assert terminal["default_consumable"] is False


def test_terminal_stage_cannot_reopen_and_lineage_is_immutable() -> None:
    active = _stage()
    before = copy.deepcopy(active)
    before_fingerprint = stage_entities.stage_fingerprint(active)
    terminal = stage_entities.terminalize_stage(
        active, outcome="closed", actor="human:vdemkiv",
        terminal_at="2026-08-21T13:00:00Z", reason_code="complete",
        reason="No further work is required in this stage.")

    assert active == before
    assert stage_entities.stage_fingerprint(active) == before_fingerprint
    assert terminal["parent_stage_ids"] == before["parent_stage_ids"]
    assert terminal["predecessor_stage_ids"] == before["predecessor_stage_ids"]
    assert terminal["input_manifest_ref"] == before["input_manifest_ref"]
    assert terminal["execution_root_id"] == before["execution_root_id"]

    with pytest.raises(ValueError):
        stage_entities.terminalize_stage(
            terminal, outcome="discarded", actor="human:vdemkiv",
            terminal_at="2026-08-21T14:00:00Z", reason_code="invalid",
            reason="A terminal stage cannot change outcomes.")
    forged_reopen = copy.deepcopy(terminal)
    forged_reopen.update({"state": "active", "outcome": None,
                          "terminal": None})
    with pytest.raises(ValueError):
        stage_entities.validate_stage(forged_reopen)

    terminal["parent_stage_ids"].append("stage-forged")
    assert active["parent_stage_ids"] == before["parent_stage_ids"]


def test_terminal_and_split_actor_must_match_stage_authority() -> None:
    active = _stage()
    original = copy.deepcopy(active)

    with pytest.raises(ValueError, match="actor.*authority"):
        stage_entities.terminalize_stage(
            active, outcome="closed", actor="human:mallory",
            terminal_at="2026-08-21T13:00:00Z", reason_code="complete",
            reason="An unrelated actor cannot close this stage.")
    with pytest.raises(ValueError, match="actor.*authority"):
        stage_entities.create_split(
            active, operation_id="split-op-security",
            child_specs=_split_specs(), actor="human:mallory",
            terminalized_at="2026-08-21T13:00:00Z",
            reason="An unrelated actor cannot split this stage.")
    assert active == original


def test_split_children_receive_roots_bound_to_their_stable_ids() -> None:
    split = stage_entities.create_split(
        _stage(), operation_id="split-op-roots", child_specs=_split_specs(),
        actor="human:vdemkiv", terminalized_at="2026-08-21T13:00:00Z",
        reason="Create independently addressable child stages.")

    assert all(
        child["execution_root_id"] == f"execution-{child['stage_id']}"
        for child in split["children"])


def test_bounded_summary_omits_manifest_and_artifact_payloads() -> None:
    terminal = stage_entities.terminalize_stage(
        _stage(), outcome="done", actor="human:vdemkiv",
        terminal_at="2026-08-21T13:00:00Z",
        completed_deliverables=["commit", "declared-tests"],
        completion_evidence=[_portable_ref("test-evidence", marker="9")])

    summary = stage_entities.bounded_stage_summary(terminal)

    assert summary["schema"] == "taskplane.stage-summary/v1"
    assert summary["stage_id"] == terminal["stage_id"]
    assert summary["state"] == "terminal"
    assert summary["outcome"] == "done"
    assert summary["aggregate_fingerprint"] == terminal["fingerprint"]
    assert summary["stage_fingerprint"] == terminal["fingerprint"]
    assert len(_canonical_bytes(summary)) <= 16 * 1024
    assert "input_manifest_ref" not in summary
    assert "selected_artifacts" not in summary
