"""Public journey: one bound run survives every governed cleanup outcome.

The tests deliberately compose only supported production entry points.  They
do not inspect source, compare implementation bytes, or manufacture cleanup
authority after a target exists.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from taskplane import (
    dashboard,
    host_native,
    owned_cleanup,
    retro,
    run_artifacts,
    views,
    wave_metrics,
)
from taskplane import taskplane_lite as tp
from taskplane.tests.test_r0002_cross_host_journey import (
    _artifact_store,
    _plan,
    _repository,
    _worker,
)


OUTCOMES = (
    "success",
    "failure",
    "cancellation",
    "interruption",
    "handoff",
    "timeout",
    "recovery",
)
WORKER_OUTCOMES = (
    ("success", "success", None),
    ("failure", "failed", None),
    ("cancellation", "cancelled", "cancel"),
    ("interruption", "interrupted", "interruption"),
    ("handoff", "handoff", "handoff"),
)
METRICS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "wave-metrics" / "closed-run.json"
)
CANDIDATE = "b" * 64
SETTINGS = "c" * 64


def _binding(run_id: str, *, stage: str = "retro") -> dict:
    return run_artifacts.create_binding(
        repository_id="repo-r0002",
        run_id=run_id,
        stage_id=stage,
        stage_instance_id=f"{stage}-{run_id}",
        candidate={
            "id": "R-0002@current",
            "fingerprint": CANDIDATE,
            "revision": "a" * 40,
        },
        settings_digest=SETTINGS,
        source_fingerprint="d" * 64,
    )


def _cleanup_manifest(case: Path, run_id: str) -> Path:
    path = case / "cleanup-state" / "manifest.json"
    owned_cleanup.create_manifest(
        path,
        repository_id="repo-r0002",
        workspace_fingerprint="e" * 64,
        settings_digest=SETTINGS,
        run_id=run_id,
        task_id="RUN-ARTIFACT-LIFECYCLE",
        attempt=1,
        evidence_root=case / "sealed-evidence",
    )
    return path


def _reserve_file(
    manifest: Path,
    root: Path,
    name: str,
    *,
    nonce: str,
    content: str | None = None,
    dependencies: tuple[str, ...] = (),
) -> tuple[str, Path]:
    resource_id = owned_cleanup.reserve_resource(
        manifest,
        kind="test-artifact",
        containment_root=root,
        relative_name=name,
        creator_nonce=nonce,
        stable_identity={
            "producer": "r0002-run-artifact-journey",
            "version": "1",
            "input": name,
        },
        evidence_refs=("terminal", "publication-replay"),
        dependencies=dependencies,
    )
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or f"owned:{name}\n", encoding="utf-8")
    owned_cleanup.activate_resource(manifest, resource_id)
    return resource_id, target


def _terminal_evidence(
    case: Path,
    manifest: Path,
    outcome: str,
    *,
    terminal: Path | None = None,
) -> dict:
    terminal = terminal or case / "terminal-input.json"
    if not terminal.exists():
        terminal.write_text(
            json.dumps({"outcome": outcome}) + "\n", encoding="utf-8"
        )
    owner = owned_cleanup.load_manifest(manifest)["owner"]
    publication = case / "publication-replay.json"
    owned_cleanup.write_publication_replay(
        publication,
        owner=owner,
        outcome=outcome,
        source_revision=1,
        source_fingerprint="f" * 64,
        trigger="terminal",
    )
    return {"terminal": terminal, "publication-replay": publication}


def _snapshot(run_id: str) -> dict:
    return host_native.HostSurfaceSnapshot.create(
        workflow_id="taskplane-loop",
        run_id=run_id,
        target="stage-retro",
        revision="a" * 40,
        sequence=7,
        stage="retro",
        state="terminal",
        values={
            "generated_at": "2026-09-01T12:00:00Z",
            "candidate_fingerprint": CANDIDATE,
            "design_graph": {
                "status": "current",
                "nodes": [{"id": "design"}],
                "edges": [],
            },
            "plan_task_dag": {
                "status": "current",
                "nodes": [{"id": "RUN-ARTIFACT-LIFECYCLE"}],
                "edges": [],
            },
            "plan_waves": {
                "status": "current",
                "waves": [["RUN-ARTIFACT-LIFECYCLE"]],
            },
            "module_impact": {
                "status": "current",
                "modules": ["taskplane/run_artifacts.py"],
            },
        },
        evidence=("current-run", "current-candidate"),
        safe_actions=(),
    ).to_dict()


def _terminal_report(receipt: dict) -> dict:
    projection = wave_metrics.consumer_projection(receipt, consumer="retro")
    return {
        "tasks": [],
        "hook_denials": 0,
        "parallel_waves": 1,
        "findings": {"total": 0, "by_severity": {}, "by_lens": {}},
        "execution_metrics": {
            "parallelism_factor": 1,
            "longest_serial_chain": {"tasks": [], "seconds": 0},
        },
        "execution_metric_source": "governed-lifecycle",
        "wave_metrics": projection,
        "evaluator_summary": retro.evaluator_summary([]),
        "graph_true_up": {
            "content_fingerprint": "1" * 64,
            "scanned_head": "a" * 40,
            "modules": 1,
            "edges": 0,
            "components": 1,
        },
        "lessons": ["durable artifacts survived exact-owned cleanup"],
    }


def _publish_run(case: Path, run_id: str, outcome: str) -> tuple[Path, Path]:
    artifact_root = case / "private-run" / "artifacts"
    artifact_root.parent.mkdir(parents=True)
    binding = _binding(run_id)
    run_artifacts.create_manifest(artifact_root, binding=binding)
    cleanup_manifest = _cleanup_manifest(case, run_id)

    delivery_resource = owned_cleanup.reserve_resource(
        cleanup_manifest,
        kind="generated-state",
        containment_root=case / "owned",
        relative_name="dashboard-delivery",
        creator_nonce=f"dashboard-{outcome}",
        stable_identity={
            "producer": "taskplane.views.deliver_dashboard",
            "version": "1",
            "input": binding["fingerprint"],
        },
        evidence_refs=("terminal", "publication-replay"),
    )
    model = _snapshot(run_id)
    delivery_path = case / "owned" / "dashboard-delivery"
    delivery = views.deliver_dashboard(
        str(delivery_path),
        model,
        html_renderer=dashboard.render_canonical_dashboard_snapshot,
        html_stylesheet=dashboard.dashboard_document_style(),
    )
    owned_cleanup.activate_resource(cleanup_manifest, delivery_resource)
    preserved = views.preserve_dashboard_run_artifacts(
        str(artifact_root), model, delivery
    )
    assert preserved["dashboard"]["class"] == "dashboard"
    assert preserved["dependency_graphs"]["class"] == "dependency-graphs"

    validation = run_artifacts.publish_artifact(
        artifact_root,
        "validation",
        {
            "schema": "taskplane.public-validation/v1",
            "candidate_fingerprint": CANDIDATE,
            "status": "passed",
            "selectors": [
                "test_separate_dashboard_graph_telemetry_and_"
                "agent_activity_artifacts_survive_all_cleanup_outcomes"
            ],
        },
        metadata={"producer": "public-build-quality", "status": "passed"},
    )
    run_artifacts.append_activity(
        artifact_root,
        event_type="terminal",
        agent_attempt_id=f"attempt-{outcome}",
        worker_id=f"worker-{outcome}",
        task_id="RUN-ARTIFACT-LIFECYCLE",
        lens="zero-lens-build",
        details={"outcome": outcome},
        evidence_references=(validation,),
        occurred_at_ns=1,
    )

    wave_receipt = wave_metrics.seal_wave_receipt(
        json.loads(METRICS_FIXTURE.read_text(encoding="utf-8"))
    )
    terminal = retro.publish_terminal_artifacts(
        str(artifact_root),
        wave_receipt=wave_receipt,
        report=_terminal_report(wave_receipt),
        lifecycle_outcome=outcome,
    )
    assert terminal["telemetry"]["class"] == "telemetry"
    assert terminal["retro"]["class"] == "retro"

    evidence_resource, evidence_path = _reserve_file(
        cleanup_manifest,
        case / "owned",
        "terminal-evidence.json",
        nonce=f"evidence-{outcome}",
        content=json.dumps({
            "outcome": outcome,
            "binding_fingerprint": binding["fingerprint"],
        }) + "\n",
        dependencies=(delivery_resource,),
    )
    assert evidence_resource and evidence_path.is_file()

    return artifact_root, cleanup_manifest


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_separate_dashboard_graph_telemetry_and_agent_activity_artifacts_survive_all_cleanup_outcomes(
    tmp_path: Path,
    outcome: str,
) -> None:
    case = tmp_path / outcome
    case.mkdir()
    artifact_root, manifest = _publish_run(case, f"run-{outcome}", outcome)
    foreign = case / "unowned-sentinel.txt"
    foreign.write_text("must survive\n", encoding="utf-8")
    owned_cleanup.bind_durable_artifacts(manifest, artifact_root)

    receipt = owned_cleanup.seal_and_cleanup(
        manifest,
        outcome=outcome,
        evidence=_terminal_evidence(
            case,
            manifest,
            outcome,
            terminal=case / "owned" / "terminal-evidence.json",
        ),
    )

    assert receipt["original_outcome"] == outcome
    assert receipt["cleanup_status"] == "clean"
    assert receipt["leak_count"] == 0
    assert receipt["artifact_verification"]["before"]["readable"] is True
    assert receipt["artifact_verification"]["after"]["readable"] is True
    assert list((case / "owned").iterdir()) == []
    assert foreign.read_text(encoding="utf-8") == "must survive\n"
    assert all(Path(row["sealed_path"]).is_file()
               for row in receipt["evidence"])

    verification = run_artifacts.verify_manifest(
        artifact_root, expected_binding=_binding(f"run-{outcome}")
    )
    assert set(verification["class_counts"]) == set(
        run_artifacts.ARTIFACT_CLASSES
    )
    assert all(verification["class_counts"][name] > 0
               for name in run_artifacts.ARTIFACT_CLASSES
               if name != "cleanup")
    assert verification["class_counts"]["cleanup"] == 1
    cleanup_entry = run_artifacts.load_manifest(artifact_root)["classes"][
        "cleanup"
    ]["entries"][-1]
    assert {
        key: cleanup_entry["metadata"].get(key)
        for key in (
            "cleanup_status", "leak_count", "original_outcome", "producer"
        )
    } == {
        "cleanup_status": "clean", "leak_count": 0,
        "original_outcome": outcome, "producer": "taskplane.owned_cleanup",
    }
    assert cleanup_entry["binding"] == _binding(f"run-{outcome}")


def test_activity_log_is_bound_append_only_and_complete_for_every_worker_outcome(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path, "activity-repository")
    run_id = "run-activity"
    workers = [
        _worker(
            outcome,
            run_id=run_id,
            stage_id="design-stage-r0002",
            candidate=CANDIDATE,
        )
        for outcome, _raw, _event in WORKER_OUTCOMES
    ]
    plan = _plan(
        workers,
        run_id=run_id,
        stage_id="design-stage-r0002",
        candidate=CANDIDATE,
        settings=SETTINGS,
    )
    artifact_root, binding = _artifact_store(workspace, plan)
    validation = run_artifacts.publish_artifact(
        artifact_root,
        "validation",
        {"schema": "taskplane.public-validation/v1", "status": "passed"},
    )
    authority = tp.register_design_lens_dispatch_plan(
        str(workspace),
        plan,
        artifact_root=str(artifact_root),
        artifact_binding=binding,
        now=10,
    )

    expected_by_lens: dict[str, list[str]] = {}
    for index, (expected_outcome, raw_outcome, semantic_event) in enumerate(
        WORKER_OUTCOMES,
        start=1,
    ):
        worker = workers[index - 1]
        expectation = tp.peek_expectation(
            str(workspace), worker["task_name"], strict=True
        )
        assert expectation is not None
        tp.record_design_dispatch_assignment_activity(
            str(workspace), expectation
        )
        assert tp.commit_dispatch_verification(
            str(workspace),
            worker["task_name"],
            worker["model"],
            expectation,
            True,
            worker["reasoning_effort"],
            strict=True,
        )

        contract = tp.build_contract(
            f"DESIGN LENS: {worker['lens']}",
            read_only=True,
            write_allow=[worker["output"]],
            tools=["Read", "Write"],
        )
        contract["task_id"] = worker["task_slot"]
        contract = tp.prepare_worker_contract(
            str(workspace),
            contract,
            stage="design-lens",
            task=worker["lens"],
            task_name=worker["task_name"],
            role_marker=worker["role_marker"],
            now=20 + index,
        )
        contract = tp.attach_design_lens_host_authority(
            contract,
            authority["workers"][worker["lens"]],
            artifact_root=str(artifact_root),
            artifact_binding=binding,
        )
        tp.activate(
            str(workspace),
            contract,
            snapshot=tp.git_head(str(workspace)),
            task_slot_override=worker["task_slot"],
        )
        event = {
            "cwd": str(workspace),
            "session_id": "session-r0002",
            "agent_id": f"agent-{index}",
            "agent_type": worker["task_name"],
            "task_name": worker["task_name"],
            "turn_id": f"turn-{index}",
        }
        bound = tp.bind_worker_contract_event(
            str(workspace), event, now=30 + index
        )
        tp.record_design_worker_start_activity(
            str(workspace), bound, event, now=30 + index
        )
        tp.record_design_worker_activity(
            str(workspace),
            {**event, "message": "current candidate needs attention"},
            event_type="attention",
        )
        tp.terminalize_worker_contract(
            str(workspace),
            {
                **event,
                "outcome": raw_outcome,
                "usage_reference": {
                    "status": "measured",
                    "total_tokens": index * 10,
                },
                "evidence_references": [validation],
            },
            outcome=raw_outcome,
            submission_status=("valid" if expected_outcome == "success"
                               else "not-required"),
            now=40 + index,
        )
        expected = [
            "assignment",
            "worker-identity",
            "start",
            "progress",
            "attention",
        ]
        if semantic_event is not None:
            expected.append(semantic_event)
        expected.extend(["usage-reference", "evidence-reference", "terminal"])
        expected_by_lens[worker["lens"]] = expected

    manifest = run_artifacts.load_manifest(artifact_root)
    entries = manifest["classes"]["agent-activity"]["entries"]
    observed_by_lens = {
        lens: [entry["metadata"]["event_type"] for entry in entries
               if entry["metadata"]["lens"] == lens]
        for lens in expected_by_lens
    }
    assert observed_by_lens == expected_by_lens
    assert [entry["sequence"] for entry in entries] == list(
        range(1, len(entries) + 1)
    )
    assert all(entry["binding"] == binding for entry in entries)
    terminals = [entry["metadata"] for entry in entries
                 if entry["metadata"]["event_type"] == "terminal"]
    assert [row["details"]["outcome"] for row in terminals] == [
        outcome for outcome, _raw, _event in WORKER_OUTCOMES
    ]
    assert all(row["usage_reference"] is not None for row in terminals)
    assert all(row["evidence_references"] == [validation]
               for row in terminals)
    verification = run_artifacts.verify_manifest(
        artifact_root, expected_binding=binding
    )
    assert verification["class_counts"]["agent-activity"] == len(entries)


@pytest.mark.parametrize(
    "case",
    ("symlink", "hardlink", "foreign", "ambiguous"),
)
def test_unsafe_cleanup_target_refuses_the_entire_owned_set_atomically(
    tmp_path: Path,
    case: str,
) -> None:
    root = tmp_path / case
    root.mkdir()
    artifact_root = root / "run" / "artifacts"
    artifact_root.parent.mkdir(parents=True)
    binding = _binding(f"run-refusal-{case}")
    run_artifacts.create_manifest(artifact_root, binding=binding)
    run_artifacts.publish_artifact(
        artifact_root, "validation", {"status": "must-survive"}
    )
    manifest = _cleanup_manifest(root, f"run-refusal-{case}")
    owned_cleanup.bind_durable_artifacts(manifest, artifact_root)
    _safe_id, safe = _reserve_file(
        manifest, root / "owned", "safe.txt", nonce=f"safe-{case}"
    )

    if case == "ambiguous":
        parent_id = owned_cleanup.reserve_resource(
            manifest,
            kind="generated-state",
            containment_root=root / "owned",
            relative_name="nested",
            creator_nonce="nested-parent",
            stable_identity={
                "producer": "journey-parent", "version": "1", "input": case,
            },
            evidence_refs=("terminal", "publication-replay"),
        )
        child_id = owned_cleanup.reserve_resource(
            manifest,
            kind="test-artifact",
            containment_root=root / "owned",
            relative_name="nested/child.txt",
            creator_nonce="nested-child",
            stable_identity={
                "producer": "journey-child", "version": "1", "input": case,
            },
            evidence_refs=("terminal", "publication-replay"),
        )
        child = root / "owned" / "nested" / "child.txt"
        child.parent.mkdir(parents=True)
        child.write_text("nested\n", encoding="utf-8")
        owned_cleanup.activate_resource(manifest, parent_id)
        owned_cleanup.activate_resource(manifest, child_id)
        unsafe = child.parent
    else:
        unsafe_id = owned_cleanup.reserve_resource(
            manifest,
            kind="test-artifact",
            containment_root=root / "owned",
            relative_name="unsafe.txt",
            creator_nonce=f"unsafe-{case}",
            stable_identity={
                "producer": "journey-unsafe",
                "version": "1",
                "input": case,
            },
            evidence_refs=("terminal", "publication-replay"),
        )
        unsafe = root / "owned" / "unsafe.txt"
        foreign = root / "foreign.txt"
        foreign.write_text("foreign\n", encoding="utf-8")
        if case == "symlink":
            unsafe.symlink_to(foreign)
        else:
            unsafe.write_text("owned:unsafe\n", encoding="utf-8")
            owned_cleanup.activate_resource(manifest, unsafe_id)
            if case == "hardlink":
                os.link(unsafe, root / "foreign-hardlink.txt")
            else:
                unsafe.unlink()
                unsafe.write_text("foreign replacement\n", encoding="utf-8")

    receipt = owned_cleanup.seal_and_cleanup(
        manifest,
        outcome="failure",
        evidence=_terminal_evidence(root, manifest, "failure"),
    )

    assert receipt["cleanup_status"] == "attention"
    assert receipt["leak_count"] >= 1
    assert all(row["status"] in {"refused", "preserved"}
               for row in receipt["resources"])
    assert safe.is_file()
    assert os.path.lexists(unsafe)
    assert run_artifacts.verify_manifest(
        artifact_root, expected_binding=binding
    )["readable"] is True


def test_durable_artifact_ancestor_and_descendant_can_never_be_reserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "durable-overlap"
    artifact_root = root / "run" / "artifacts"
    artifact_root.parent.mkdir(parents=True)
    run_artifacts.create_manifest(
        artifact_root, binding=_binding("run-durable-overlap")
    )
    manifest = _cleanup_manifest(root, "run-durable-overlap")
    owned_cleanup.bind_durable_artifacts(manifest, artifact_root)
    before = owned_cleanup.load_manifest(manifest)["resources"]

    with pytest.raises(
        owned_cleanup.OwnedCleanupError,
        match="durable artifact root overlaps",
    ):
        owned_cleanup.reserve_resource(
            manifest,
            kind="generated-state",
            containment_root=root,
            relative_name="run",
            creator_nonce="durable-ancestor",
            stable_identity={
                "producer": "journey", "version": "1", "input": "ancestor",
            },
            evidence_refs=("terminal", "publication-replay"),
        )
    with pytest.raises(
        owned_cleanup.OwnedCleanupError,
        match="durable artifact root overlaps",
    ):
        owned_cleanup.reserve_resource(
            manifest,
            kind="generated-state",
            containment_root=artifact_root,
            relative_name="dashboard",
            creator_nonce="durable-descendant",
            stable_identity={
                "producer": "journey", "version": "1", "input": "descendant",
            },
            evidence_refs=("terminal", "publication-replay"),
        )

    assert owned_cleanup.load_manifest(manifest)["resources"] == before == {}
    assert run_artifacts.verify_manifest(artifact_root)["readable"] is True
