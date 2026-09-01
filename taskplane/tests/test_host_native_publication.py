"""Behavioral contracts for current, provenance-bound dashboard publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import dashboard, host_native, run_artifacts, views


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _design_graph(node: str = "dashboard") -> dict:
    graph = {
        "schema": "taskplane.dashboard-design-graph/v1",
        "source": "artifact://run/design/decomposition",
        "design_graph_fingerprint": _digest([node]),
        "modules": [node, "views"],
        "edges": [{"from": node, "to": "views", "kind": "publishes"}],
        "module_total": 2,
        "edge_total": 1,
        "depth_policy": {"max": 3},
    }
    return {**graph, "fingerprint": _digest(graph)}


def _model(*, run: str, sequence: int, node: str = "dashboard") -> dict:
    graph = _design_graph(node)
    provenance = {
        "schema": "taskplane.dashboard-provenance/v1",
        "run_id": run,
        "requirement_id": "R-CURRENT",
        "stage": "design",
        "revision": f"revision-{sequence}",
        "settings_digest": "a" * 64,
        "authority_receipt": "b" * 64,
        "graph_receipt": graph["fingerprint"],
        "publication_epoch": sequence,
    }
    material = {
        "schema": host_native.SNAPSHOT_SCHEMA,
        "workflow_id": "taskplane-loop",
        "run_id": run,
        "target": "stage-design",
        "revision": f"revision-{sequence}",
        "sequence": sequence,
        "stage": "design",
        "state": "design",
        "values": {
            "generated_at": f"2026-09-01T00:00:{sequence:02d}Z",
            "settings_digest": "a" * 64,
            "loop": {"goal": "current graph", "step": "design",
                     "requirement_id": "R-CURRENT", "tasks": []},
            "design_graph": graph,
            "provenance": provenance,
        },
        "evidence": ["current-run-state"],
        "safe_actions": ["approve"],
    }
    return {**material, "fingerprint": _digest(material)}


def test_complete_binding_is_visible_and_identical_in_native_projection() -> None:
    model = _model(run="run-current", sequence=12)
    snapshot = host_native.HostSurfaceSnapshot.from_dict(model)
    fragment = dashboard.render_canonical_dashboard_snapshot(model)
    native = host_native.native_dashboard_projection(snapshot, host="codex")
    native_binding = next(
        row["value"] for row in native["components"]
        if row["id"] == "provenance")

    assert native_binding == model["values"]["provenance"]
    for value in (
        "run-current", "R-CURRENT", "design", "revision-12",
        "a" * 64, "b" * 64, model["fingerprint"],
        model["values"]["design_graph"]["fingerprint"], "12",
    ):
        assert value in fragment


def test_delayed_prior_run_and_wrong_expected_head_cannot_replace_current(
        tmp_path: Path) -> None:
    root = tmp_path / "delivery"
    current = views.deliver_dashboard(
        str(root), _model(run="run-current", sequence=12),
        html_renderer=lambda raw: dashboard.render_canonical_dashboard_snapshot(
            json.loads(raw)),
        html_stylesheet=dashboard.dashboard_document_style())

    with pytest.raises(ValueError, match="stale sequence"):
        views.deliver_dashboard(
            str(root), _model(run="run-prior", sequence=11),
            html_renderer=lambda raw:
                dashboard.render_canonical_dashboard_snapshot(json.loads(raw)),
            expected_head=current["current_head"]["receipt_fingerprint"])
    with pytest.raises(ValueError, match="expected head changed"):
        views.deliver_dashboard(
            str(root), _model(run="run-current", sequence=13),
            html_renderer=lambda raw:
                dashboard.render_canonical_dashboard_snapshot(json.loads(raw)),
            expected_head="0" * 64)

    head = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert head == current["current_head"]


def test_static_degraded_dashboard_is_visibly_read_only(tmp_path: Path) -> None:
    model = _model(run="run-degraded", sequence=4)
    model["values"]["phase_graph_error"] = "graph receipt is unavailable"
    material = {key: value for key, value in model.items()
                if key != "fingerprint"}
    model["fingerprint"] = _digest(material)
    delivery = views.deliver_dashboard(
        str(tmp_path), model,
        html_renderer=lambda raw: dashboard.render_canonical_dashboard_snapshot(
            json.loads(raw)),
        html_stylesheet=dashboard.dashboard_document_style())
    document = Path(delivery["artifacts"]["html"]["path"]).read_text(
        encoding="utf-8")

    assert 'id="tp-dashboard-freshness-status"' in document
    assert "graph receipt is unavailable" in document
    assert 'disabled aria-disabled="true"' in document
    assert delivery["publication_receipt"]["dom_freshness"][
        "actions_enabled"] is False


def test_dashboard_and_graph_are_preserved_in_separate_run_artifact_classes(
        tmp_path: Path) -> None:
    root = tmp_path / "run-artifacts"
    candidate = {"kind": "git", "sha": "c" * 40}
    candidate["fingerprint"] = _digest(candidate)
    binding = run_artifacts.create_binding(
        repository_id="repository-1", run_id="run-current",
        stage_id="design", stage_instance_id="design-1",
        candidate=candidate, settings_digest="a" * 64,
        source_fingerprint="d" * 64)
    run_artifacts.create_manifest(root, binding=binding)
    model = _model(run="run-current", sequence=12)
    delivery = views.deliver_dashboard(
        str(tmp_path / "delivery"), model,
        html_renderer=lambda raw: dashboard.render_canonical_dashboard_snapshot(
            json.loads(raw)))

    refs = views.preserve_dashboard_run_artifacts(str(root), model, delivery)
    verified = run_artifacts.verify_manifest(root, expected_binding=binding)

    assert refs["status"] == "preserved"
    assert verified["class_counts"]["dashboard"] == 1
    assert verified["class_counts"]["dependency-graphs"] == 1
