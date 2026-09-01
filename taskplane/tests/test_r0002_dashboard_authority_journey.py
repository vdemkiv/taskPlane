"""Public journey: dashboard actions need exact current host authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from taskplane import dashboard, host_native, views


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _model() -> dict:
    material = {
        "schema": host_native.SNAPSHOT_SCHEMA,
        "workflow_id": "taskplane-loop", "run_id": "run-current",
        "target": "stage-design", "revision": "revision-7", "sequence": 7,
        "stage": "design", "state": "design",
        "values": {
            "generated_at": "2026-09-01T00:00:07Z",
            "loop": {"step": "design", "goal": "current dashboard"},
        },
        "evidence": ["current-run"], "safe_actions": ["approve"],
    }
    return {**material, "fingerprint": _digest(material)}


def _render(raw: str) -> str:
    return dashboard.render_canonical_dashboard_snapshot(json.loads(raw))


def test_absent_or_wrong_host_acknowledgement_keeps_dashboard_actions_inert(
        tmp_path: Path) -> None:
    model = _model()
    absent = views.deliver_dashboard(
        str(tmp_path / "absent"), model, html_renderer=_render,
        html_stylesheet=dashboard.dashboard_document_style())
    document = Path(absent["artifacts"]["html"]["path"]).read_text(
        encoding="utf-8")

    assert absent["publication_receipt"]["host_acknowledgement"][
        "status"] == "static-limitation"
    assert absent["publication_receipt"]["dom_freshness"][
        "actions_enabled"] is False
    assert 'disabled aria-disabled="true"' in document

    head = absent["current_head"]
    wrong_material = {
        "schema": "taskplane.host-native-acknowledgement/v1",
        "snapshot_fingerprint": "0" * 64, "sequence": head["sequence"],
        "identity": {key: head[key] for key in (
            "workflow_id", "run_id", "target", "revision")},
    }
    wrong = {**wrong_material, "fingerprint": _digest(wrong_material)}
    rejected = views.deliver_dashboard(
        str(tmp_path / "wrong"), model, html_renderer=_render,
        host_acknowledgement=wrong)
    assert rejected["publication_receipt"]["host_acknowledgement"][
        "status"] == "rejected"
    assert rejected["publication_receipt"]["dom_freshness"][
        "actions_enabled"] is False


def test_exact_host_acknowledgement_enables_only_its_bound_snapshot(
        tmp_path: Path) -> None:
    model = _model()
    first = views.deliver_dashboard(
        str(tmp_path / "probe"), model, html_renderer=_render)
    head = first["current_head"]
    acknowledgement_material = {
        "schema": "taskplane.host-native-acknowledgement/v1",
        "snapshot_fingerprint": head["snapshot_fingerprint"],
        "sequence": head["sequence"],
        "identity": {key: head[key] for key in (
            "workflow_id", "run_id", "target", "revision")},
    }
    acknowledgement = {
        **acknowledgement_material,
        "fingerprint": _digest(acknowledgement_material),
    }

    exact = views.deliver_dashboard(
        str(tmp_path / "exact"), model, html_renderer=_render,
        host_acknowledgement=acknowledgement)

    assert exact["publication_receipt"]["host_acknowledgement"][
        "status"] == "acknowledged"
    assert exact["publication_receipt"]["dom_freshness"][
        "actions_enabled"] is True
