"""Split-stage invariants: deterministic children and all-or-nothing input."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from taskplane import (review_evidence, run_store, stage_entities,
                       stage_handoff, storage)


RUN_ID = "run-split-001"
PARENT_ID = "stage-plan-parent"


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


def _parent(*, root: bool = False) -> dict[str, object]:
    return stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=PARENT_ID,
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="plan",
        parent_stage_ids=[],
        predecessor_stage_ids=([] if root else ["stage-design-predecessor"]),
        input_manifest_ref=_reference("stage-handoff", "f"),
        execution_root_id=f"execution-{PARENT_ID}",
        deliverables=["component-a", "component-b"],
        budget={"tokens": 4000, "seconds": 600},
        dependencies=([] if root else ["stage-design-predecessor"]),
        contracts=["contract:delivery-lineage"],
        authority=_authority(),
        created_at="2026-08-21T14:00:00Z",
        selected_artifacts=[
            _reference("component-a", "a"),
            _reference("component-b", "e"),
        ],
    )


def _store(tmp_path: Path) -> tuple[run_store.RunStore, dict]:
    identity = storage.identity_from_remote(
        "https://github.com/vdemkiv/taskplane.git")
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(tmp_path / "checkout"),
        host={"kind": "codex", "session_id": "thread-1"},
        target={"kind": "workspace"},
    )
    return store, manifest


def _integration_split_fixture(tmp_path: Path):
    artifact_store = review_evidence.ArtifactStore(
        str(tmp_path), root=str(tmp_path / "artifacts"))
    root_input = review_evidence.portable_artifact_reference(
        artifact_store,
        artifact_store.put("root-input", {"requirement": "R-0004"}),
    )
    selected = [
        review_evidence.portable_artifact_reference(
            artifact_store,
            artifact_store.put("component-a", {"component": "a"}),
        ),
        review_evidence.portable_artifact_reference(
            artifact_store,
            artifact_store.put("component-b", {"component": "b"}),
        ),
    ]
    parent = stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=PARENT_ID,
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="plan",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=root_input,
        execution_root_id=f"execution-{PARENT_ID}",
        deliverables=["component-a", "component-b"],
        budget={"tokens": 4000, "seconds": 600},
        dependencies=[],
        contracts=["contract:delivery-lineage"],
        authority=_authority(),
        created_at="2026-08-21T14:00:00Z",
        selected_artifacts=selected,
    )
    specs = [
        {
            "stage_kind": "build",
            "selected_artifacts": [selected[0]],
            "dependencies": [PARENT_ID],
            "budget": {"tokens": 1200, "seconds": 180},
            "deliverables": ["component-a"],
            "contracts": ["contract:delivery-lineage"],
        },
        {
            "stage_kind": "evaluate",
            "selected_artifacts": [selected[1]],
            "dependencies": ["child:0"],
            "budget": {"tokens": 900, "seconds": 120},
            "deliverables": ["component-b"],
            "contracts": ["contract:delivery-lineage"],
        },
    ]
    stored_handoffs: dict[str, dict] = {}
    for ordinal, spec in enumerate(specs):
        evidence = artifact_store.put(
            "completion-evidence", {"child_ordinal": ordinal})
        manifest = stage_handoff.create_manifest(
            artifact_store,
            producer_stage_id=PARENT_ID,
            producer_outcome="closed",
            requirement=parent["requirement"],
            design=parent["design"],
            target=None,
            commit=None,
            contracts={
                "provided": ["contract:delivery-lineage"],
                "consumed": [],
                "changed": [],
            },
            deliverables=spec["deliverables"],
            evidence_references=[evidence],
            selected_artifacts=spec["selected_artifacts"],
            exclusions=list(stage_handoff.REQUIRED_EXCLUSIONS),
            authorization={
                "actor": parent["authority"]["actor"],
                "session_id": parent["authority"]["session_id"],
                "authorized_at": "2026-08-21T14:05:00Z",
                "operation_id": f"split-parent-child-{ordinal}",
                "authority_record": {
                    "schema": "taskplane.authority-record-reference/v1",
                    "authority_schema":
                        "taskplane.consolidated-authorization/v1",
                    "revision": parent["authority"]["authority_revision"],
                    "fingerprint":
                        parent["authority"]["authority_fingerprint"],
                },
            },
            allow_nonconsumable_reuse=True,
        )
        native_ref = stage_handoff.store_manifest(artifact_store, manifest)
        portable_ref = review_evidence.portable_artifact_reference(
            artifact_store, native_ref)
        spec["input_manifest_ref"] = portable_ref
        stored_handoffs[str(portable_ref["fingerprint"])] = native_ref

    def handoff_resolver(reference: dict):
        return artifact_store, stored_handoffs[str(reference["fingerprint"])]

    def artifact_validator(reference: dict) -> None:
        review_evidence.verify_portable_artifact_reference(
            artifact_store, reference)

    return parent, specs, handoff_resolver, artifact_validator


def _validate_exact_authority(expected: dict, current: dict) -> None:
    if expected != current:
        raise stage_entities.StageLifecycleError("authority is stale")


def _successor(parent: dict[str, object], spec: dict[str, object], *,
               input_manifest_ref: dict[str, object] | None = None,
               stage_id: str = "stage-successor") -> dict[str, object]:
    return stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=stage_id,
        requirement=parent["requirement"],
        design=parent["design"],
        stage_kind=str(spec["stage_kind"]),
        parent_stage_ids=[],
        predecessor_stage_ids=[PARENT_ID],
        input_manifest_ref=(input_manifest_ref or
                            spec["input_manifest_ref"]),
        execution_root_id=f"execution-{stage_id}",
        deliverables=spec["deliverables"],
        budget=spec["budget"],
        dependencies=[PARENT_ID],
        contracts=spec["contracts"],
        authority=parent["authority"],
        created_at="2026-08-21T14:05:00Z",
        selected_artifacts=spec["selected_artifacts"],
    )


def _specs() -> list[dict[str, object]]:
    return [
        {
            "stage_kind": "build",
            "input_manifest_ref": _reference("stage-handoff", "1"),
            "selected_artifacts": [_reference("component-a", "a")],
            "dependencies": [PARENT_ID],
            "budget": {"tokens": 1200, "seconds": 180},
            "deliverables": ["component-a"],
            "contracts": ["contract:delivery-lineage"],
        },
        {
            "stage_kind": "evaluate",
            "input_manifest_ref": _reference("stage-handoff", "2"),
            "selected_artifacts": [_reference("component-b", "e")],
            "dependencies": ["child:0"],
            "budget": {"tokens": 900, "seconds": 120},
            "deliverables": ["component-b"],
            "contracts": ["contract:delivery-lineage"],
        },
    ]


def _split(parent: dict[str, object], operation_id: str = "split-op-1") -> dict:
    return stage_entities.create_split(
        parent,
        operation_id=operation_id,
        child_specs=_specs(),
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:05:00Z",
        reason="Separate implementation from independent evaluation.",
    )


def test_split_closes_parent_and_creates_deterministic_isolated_children() -> None:
    parent = _parent()
    original = copy.deepcopy(parent)

    first = _split(parent)
    replay = _split(copy.deepcopy(parent))

    assert parent == original, "the pure split builder mutated its input head"
    assert first == replay
    assert len(first["children"]) == 2
    assert first["active_stage_ids"] == sorted(
        child["stage_id"] for child in first["children"])
    assert [child["stage_id"] for child in first["children"]] == [
        stage_entities.split_child_id(RUN_ID, PARENT_ID, "split-op-1", 0),
        stage_entities.split_child_id(RUN_ID, PARENT_ID, "split-op-1", 1),
    ]

    closed_parent = first["parent"]
    assert closed_parent["state"] == "terminal"
    assert closed_parent["outcome"] == "closed"
    assert closed_parent["default_consumable"] is False
    assert closed_parent["terminal"]["reason_code"] == "split"
    assert closed_parent["terminal"]["reason"] == \
        "Separate implementation from independent evaluation."

    left, right = first["children"]
    assert left["parent_stage_ids"] == [PARENT_ID]
    assert right["parent_stage_ids"] == [PARENT_ID]
    assert left["execution_root_id"] != right["execution_root_id"]
    assert left["selected_artifacts"] == [_reference("component-a", "a")]
    assert right["selected_artifacts"] == [_reference("component-b", "e")]
    assert left["budget"] == {"tokens": 1200, "seconds": 180}
    assert right["budget"] == {"tokens": 900, "seconds": 120}
    assert right["dependencies"] == [left["stage_id"]]
    assert [(row["parent_stage_id"], row["child_stage_id"])
            for row in first["lineage"]] == [
        (PARENT_ID, child_id) for child_id in sorted(
            [left["stage_id"], right["stage_id"]])
    ]
    assert all(row["split_operation_id"] == "split-op-1"
               for row in first["lineage"])
    assert all(len(row["fingerprint"]) == 64 for row in first["lineage"])


def test_split_child_identity_is_bound_to_operation_and_ordinal() -> None:
    first = [
        stage_entities.split_child_id(RUN_ID, PARENT_ID, "split-op-1", index)
        for index in range(4)
    ]
    replay = [
        stage_entities.split_child_id(RUN_ID, PARENT_ID, "split-op-1", index)
        for index in range(4)
    ]
    another_operation = stage_entities.split_child_id(
        RUN_ID, PARENT_ID, "split-op-2", 0)

    assert first == replay
    assert len(set(first)) == 4
    assert another_operation not in first


def test_terminalizing_one_child_cannot_change_parent_or_sibling_history() -> None:
    split = _split(_parent())
    parent_before = copy.deepcopy(split["parent"])
    sibling_before = copy.deepcopy(split["children"][1])

    terminalized = stage_entities.terminalize_stage(
        split["children"][0],
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:10:00Z",
        reason_code="superseded",
        reason="This child is no longer required.",
    )

    assert terminalized["stage_id"] == split["children"][0]["stage_id"]
    assert terminalized["fingerprint"] != split["children"][0]["fingerprint"]
    assert terminalized["outcome"] == "closed"
    assert split["parent"] == parent_before
    assert split["children"][1] == sibling_before


@pytest.mark.parametrize(
    "specs, error",
    [
        (lambda: _specs()[:1], r"requires.*(?:two|2)"),
        (lambda: [_specs()[0], copy.deepcopy(_specs()[0])], "duplicate"),
        (lambda: [{**_specs()[0], "budget": {}}, _specs()[1]], "budget"),
        (
            lambda: [
                {**_specs()[0],
                 "selected_artifacts": [_reference("undeclared", "9")]},
                _specs()[1],
            ],
            r"(?:undeclared.*parent|outside.*parent)",
        ),
        (
            lambda: [
                _specs()[0],
                {**_specs()[1], "dependencies": ["missing-stage"]},
            ],
            "dependency",
        ),
    ],
    ids=["one-child", "duplicate-child", "missing-budget",
         "artifact-outside-parent", "unresolved-dependency"],
)
def test_any_invalid_child_rejects_the_whole_split(specs, error: str) -> None:
    parent = _parent()
    before = copy.deepcopy(parent)

    with pytest.raises(stage_entities.StageValidationError, match=error):
        stage_entities.create_split(
            parent,
            operation_id="split-op-invalid",
            child_specs=specs(),
            actor="human:vdemkiv",
            terminalized_at="2026-08-21T14:05:00Z",
            reason="This must not partially commit.",
        )

    assert parent == before


def test_deterministic_child_id_collision_rejects_the_whole_split(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    before = copy.deepcopy(parent)
    monkeypatch.setattr(
        stage_entities, "split_child_id",
        lambda _run, _parent, _operation, _ordinal: "stage-child-collision")

    with pytest.raises(stage_entities.StageValidationError, match="collision"):
        stage_entities.create_split(
            parent,
            operation_id="split-op-collision",
            child_specs=_specs(),
            actor="human:vdemkiv",
            terminalized_at="2026-08-21T14:05:00Z",
            reason="No partial child may survive a collision.",
        )

    assert parent == before


def test_lifecycle_split_commits_parent_children_lineage_and_projection_once(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    parent, specs, handoff_resolver, artifact_validator = \
        _integration_split_fixture(tmp_path)
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
        handoff_resolver=handoff_resolver,
        artifact_validator=artifact_validator,
    )
    lifecycle.start_stage(
        parent,
        expected_revision=initial["revision"],
        operation_id="start-parent",
    )
    before_split = store.load(RUN_ID)
    old_parent_head = before_split["stage_heads"][PARENT_ID]

    receipt = lifecycle.split_stage(
        RUN_ID,
        stage_id=PARENT_ID,
        expected_head_fingerprint=parent["fingerprint"],
        expected_revision=before_split["revision"],
        operation_id="split-parent",
        child_specs=specs,
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:05:00Z",
        reason="Separate implementation from independent evaluation.",
    )

    persisted = store.load(RUN_ID)
    child_ids = [
        stage_entities.split_child_id(
            RUN_ID, PARENT_ID, "split-parent", ordinal)
        for ordinal in range(2)
    ]
    assert persisted["revision"] == before_split["revision"] + 1
    assert set(persisted["stage_heads"]) == {PARENT_ID, *child_ids}
    assert persisted["stage_heads"][PARENT_ID] != old_parent_head
    assert persisted["stage_heads"][PARENT_ID] == \
        receipt["result"]["parent_head"]
    assert persisted["stage_heads"][PARENT_ID]["summary"]["outcome"] == \
        "closed"
    assert receipt["result"]["child_heads"] == {
        child_id: persisted["stage_heads"][child_id]
        for child_id in child_ids
    }
    assert persisted["lineage"] == receipt["result"]["lineage"]
    assert [(row["parent_stage_id"], row["child_stage_id"])
            for row in persisted["lineage"]] == [
        (PARENT_ID, child_id) for child_id in sorted(child_ids)
    ]
    assert persisted["active_stage_projection"] == \
        receipt["result"]["active_stage_projection"]
    assert persisted["active_stage_projection"]["active_stage_ids"] == \
        sorted(child_ids)
    assert persisted["stage_operations"]["split-parent"] == receipt


@pytest.mark.parametrize("failure", ["authority", "head"])
def test_lifecycle_split_rejects_stale_authority_or_parent_head_atomically(
        tmp_path: Path, failure: str) -> None:
    store, initial = _store(tmp_path)
    parent, specs, handoff_resolver, artifact_validator = \
        _integration_split_fixture(tmp_path)
    current_authority = _authority()
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: current_authority,
        authority_validator=_validate_exact_authority,
        handoff_resolver=handoff_resolver,
        artifact_validator=artifact_validator,
    )
    lifecycle.start_stage(
        parent,
        expected_revision=initial["revision"],
        operation_id="start-parent",
    )
    before = store.load(RUN_ID)
    expected_head = parent["fingerprint"]
    error = "head"
    if failure == "authority":
        current_authority["authority_revision"] = 8
        error = "authority"
    else:
        expected_head = "9" * 64

    with pytest.raises(stage_entities.StageLifecycleError, match=error):
        lifecycle.split_stage(
            RUN_ID,
            stage_id=PARENT_ID,
            expected_head_fingerprint=expected_head,
            expected_revision=before["revision"],
            operation_id=f"invalid-split-{failure}",
            child_specs=specs,
            actor="human:vdemkiv",
            terminalized_at="2026-08-21T14:05:00Z",
            reason="This request must fail before commit.",
        )

    assert store.load(RUN_ID) == before


def test_terminalize_and_start_is_one_replayable_successor_transaction(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    parent, specs, handoff_resolver, artifact_validator = \
        _integration_split_fixture(tmp_path)
    successor = _successor(parent, specs[0])
    authority_checks = 0
    claims: list[dict] = []

    def validate_authority(expected: dict, current: dict) -> None:
        nonlocal authority_checks
        authority_checks += 1
        _validate_exact_authority(expected, current)

    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=validate_authority,
        handoff_resolver=handoff_resolver,
        artifact_validator=artifact_validator,
        execution_root_claimer=lambda claim: claims.append(dict(claim)),
    )
    lifecycle.start_stage(
        parent,
        expected_revision=initial["revision"],
        operation_id="start-parent",
    )
    before = store.load(RUN_ID)
    claims_before = len(claims)

    receipt = lifecycle.terminalize_and_start(
        PARENT_ID,
        successor,
        expected_head_fingerprint=parent["fingerprint"],
        expected_revision=before["revision"],
        operation_id="close-and-start-successor",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:05:00Z",
        reason_code="continued",
        reason="Continue through the bounded successor handoff.",
    )
    committed = store.load(RUN_ID)
    checks_after_commit = authority_checks
    claims_after_commit = len(claims)

    replay = lifecycle.terminalize_and_start(
        PARENT_ID,
        successor,
        expected_head_fingerprint=parent["fingerprint"],
        expected_revision=before["revision"],  # stale after first commit
        operation_id="close-and-start-successor",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:05:00Z",
        reason_code="continued",
        reason="Continue through the bounded successor handoff.",
    )

    assert replay == receipt
    assert authority_checks == checks_after_commit
    assert len(claims) == claims_after_commit == claims_before + 1
    assert store.load(RUN_ID) == committed
    assert committed["revision"] == before["revision"] + 1
    assert receipt["stage_ids"] == sorted([PARENT_ID,
                                            successor["stage_id"]])
    assert committed["stage_heads"][PARENT_ID] == \
        receipt["result"]["predecessor_head"]
    assert committed["stage_heads"][PARENT_ID]["summary"]["state"] == \
        "terminal"
    assert committed["stage_heads"][successor["stage_id"]] == \
        receipt["result"]["successor_head"]
    assert committed["stage_heads"][successor["stage_id"]]["summary"][
        "state"] == "active"
    assert committed["stage_heads"][successor["stage_id"]]["summary"][
        "parent_stage_ids"] == []
    assert committed["stage_heads"][successor["stage_id"]]["summary"][
        "predecessor_stage_ids"] == [PARENT_ID]
    assert committed["lineage"] == receipt["result"]["lineage"]
    assert committed["lineage"][-1]["parent_stage_id"] is None
    assert committed["lineage"][-1]["child_stage_id"] == \
        successor["stage_id"]
    assert committed["lineage"][-1]["predecessor_stage_ids"] == [PARENT_ID]
    assert committed["active_stage_projection"]["active_stage_ids"] == [
        successor["stage_id"]
    ]


def test_terminalize_and_start_invalid_handoff_preserves_active_predecessor(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    parent, specs, handoff_resolver, artifact_validator = \
        _integration_split_fixture(tmp_path)
    invalid_successor = _successor(
        parent, specs[0],
        input_manifest_ref=_reference("stage-handoff", "9"),
        stage_id="stage-invalid-successor",
    )
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
        handoff_resolver=handoff_resolver,
        artifact_validator=artifact_validator,
    )
    lifecycle.start_stage(
        parent,
        expected_revision=initial["revision"],
        operation_id="start-parent",
    )
    path = Path(store._manifest_path(RUN_ID))
    before_bytes = path.read_bytes()
    before = store.load(RUN_ID)

    with pytest.raises((stage_entities.StageLifecycleError, KeyError),
                       match="handoff|fingerprint"):
        lifecycle.terminalize_and_start(
            PARENT_ID,
            invalid_successor,
            expected_head_fingerprint=parent["fingerprint"],
            expected_revision=before["revision"],
            operation_id="invalid-close-and-start",
            outcome="closed",
            actor="human:vdemkiv",
            terminalized_at="2026-08-21T14:05:00Z",
            reason_code="continued",
            reason="This invalid successor must never become active.",
        )

    assert path.read_bytes() == before_bytes
    assert store.load(RUN_ID) == before
    assert before["stage_heads"][PARENT_ID]["summary"]["state"] == "active"
    assert invalid_successor["stage_id"] not in before["stage_heads"]


def test_multi_predecessor_successor_records_one_predecessor_lineage_row(
        tmp_path: Path) -> None:
    store, initial = _store(tmp_path)
    first, specs, handoff_resolver, artifact_validator = \
        _integration_split_fixture(tmp_path)
    second_id = "stage-plan-other"
    second = stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=second_id,
        requirement=first["requirement"],
        design=first["design"],
        stage_kind="plan",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=first["input_manifest_ref"],
        execution_root_id=f"execution-{second_id}",
        deliverables=first["deliverables"],
        budget=first["budget"],
        dependencies=[],
        contracts=first["contracts"],
        authority=first["authority"],
        created_at="2026-08-21T14:00:00Z",
        selected_artifacts=first["selected_artifacts"],
    )
    successor_id = "stage-multi-successor"
    successor = stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=successor_id,
        requirement=first["requirement"],
        design=first["design"],
        stage_kind="build",
        parent_stage_ids=[],
        predecessor_stage_ids=[PARENT_ID, second_id],
        input_manifest_ref=specs[0]["input_manifest_ref"],
        execution_root_id=f"execution-{successor_id}",
        deliverables=specs[0]["deliverables"],
        budget=specs[0]["budget"],
        dependencies=[PARENT_ID, second_id],
        contracts=specs[0]["contracts"],
        authority=first["authority"],
        created_at="2026-08-21T14:10:00Z",
        selected_artifacts=specs[0]["selected_artifacts"],
    )
    lifecycle = stage_entities.StageLifecycle(
        store,
        authority_resolver=lambda _manifest: _authority(),
        authority_validator=_validate_exact_authority,
        handoff_resolver=handoff_resolver,
        artifact_validator=artifact_validator,
    )
    lifecycle.start_stage(
        first,
        expected_revision=initial["revision"],
        operation_id="start-first",
    )
    after_first = store.load(RUN_ID)
    lifecycle.start_stage(
        second,
        expected_revision=after_first["revision"],
        operation_id="start-second",
        foreground=False,
    )
    both_active = store.load(RUN_ID)
    first_terminal = lifecycle.terminalize(
        RUN_ID,
        stage_id=PARENT_ID,
        expected_head_fingerprint=first["fingerprint"],
        expected_revision=both_active["revision"],
        operation_id="terminal-first",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:05:00Z",
        reason_code="continued",
        reason="The bounded successor consumes this producer handoff.",
    )
    after_first_terminal = store.load(RUN_ID)
    second_terminal = lifecycle.terminalize(
        RUN_ID,
        stage_id=second_id,
        expected_head_fingerprint=second["fingerprint"],
        expected_revision=after_first_terminal["revision"],
        operation_id="terminal-second",
        outcome="closed",
        actor="human:vdemkiv",
        terminalized_at="2026-08-21T14:06:00Z",
        reason_code="continued",
        reason="This predecessor contributes lineage but no selected handoff.",
    )
    predecessors_terminal = store.load(RUN_ID)
    lifecycle.start_stage(
        successor,
        expected_revision=predecessors_terminal["revision"],
        operation_id="start-multi-successor",
        expected_predecessor_fingerprints={
            PARENT_ID: first_terminal["result"]["head"]["object"][
                "fingerprint"],
            second_id: second_terminal["result"]["head"]["object"][
                "fingerprint"],
        },
    )

    persisted = store.load(RUN_ID)
    rows = [
        row for row in persisted["lineage"]
        if row["child_stage_id"] == successor_id
    ]
    assert len(rows) == 1
    assert rows[0]["parent_stage_id"] is None
    assert rows[0]["predecessor_stage_ids"] == sorted(
        [PARENT_ID, second_id])
    assert persisted["stage_heads"][successor_id]["summary"][
        "parent_stage_ids"] == []
    assert persisted["stage_heads"][successor_id]["summary"][
        "predecessor_stage_ids"] == sorted([PARENT_ID, second_id])
