"""Behavioral proof that dashboard graphs belong to the active run snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from taskplane import dashboard, host_native, storage, views


def _write_prior_artifacts(workspace: Path) -> None:
    (workspace / "design").mkdir(parents=True)
    (workspace / "plan").mkdir()
    (workspace / "design" / "contract.json").write_text(json.dumps({
        "schema": "taskplane.design/v1",
        "requirement": "R-PRIOR",
        "graph": {
            "proposed_modules": ["prior-design-node"],
            "proposed_edges": [],
        },
    }), encoding="utf-8")
    (workspace / "plan" / "tasks.json").write_text(json.dumps({
        "schema": "taskplane.plan/v1",
        "requirement": "R-PRIOR",
        "delivery_mode": "serial",
        "automatic_lenses": [],
        "plan_authority": "prior run",
        "tasks": [{"id": "PRIOR", "deps": [], "scope": ["prior.py"]}],
        "waves": [{"id": "W0", "tasks": ["PRIOR"], "after": [],
                   "serialization": "prior"}],
    }), encoding="utf-8")


def _publish(workspace: Path, *, step: str, target: str, revision: str) -> dict:
    state = {
        "goal": "fresh run", "step": step, "requirement_id": "R-CURRENT",
        "tasks": [],
    }
    return host_native.refresh_dashboard_snapshot(
        str(workspace), event_type="next_action", outcome="success",
        committed_at=f"2026-09-01T00:00:0{revision}Z",
        settings_digest="settings-current",
        source_loader=lambda _ws: {
            "mode": "legacy", "status": "ready", "run_id": "run-current",
            "revision": revision, "target": target, "state": state,
            "evidence": ["current-run-state"],
        },
        graph_projector=dashboard.phase_graph_projection,
        metrics_projector=lambda value, **_kwargs: value,
        publication_loader=storage.load_dashboard_publication,
        snapshot_committer=storage.commit_dashboard_snapshot,
        event_committer=storage.commit_dashboard_event,
        error_formatter=str,
    )


def test_fresh_design_and_plan_do_not_reuse_prior_run_graph_artifacts(
        tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_prior_artifacts(workspace)

    design = _publish(
        workspace, step="design", target="stage-design", revision="1")
    plan = _publish(
        workspace, step="plan", target="stage-plan", revision="2")

    for publication in (design, plan):
        values = publication["snapshot"]["values"]
        assert "design_graph" not in values
        assert "plan_task_dag" not in values
        assert "plan_waves" not in values


def test_durable_html_graph_comes_from_snapshot_after_workspace_mutation(
        tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_prior_artifacts(workspace)
    graph = {
        "schema": "taskplane.dashboard-design-graph/v1",
        "source": "artifact://design/current",
        "design_graph_fingerprint": "current-design",
        "modules": ["snapshot-node"],
        "edges": [],
        "module_total": 1,
        "edge_total": 0,
        "depth_policy": {},
        "fingerprint": "snapshot-graph-fingerprint",
    }
    snapshot = host_native.HostSurfaceSnapshot.create(
        workflow_id="taskplane-loop", run_id="run-current",
        target="stage-design", revision="revision-current", sequence=1,
        stage="design", state="design",
        values={
            "generated_at": "2026-09-01T00:00:00Z",
            "loop": {"goal": "current design", "step": "design", "tasks": []},
            "design_graph": graph,
        },
        evidence=("current-run-state",), safe_actions=(),
    )

    # The mutable workspace now names another graph.  Publication must still
    # render the graph sealed in ``snapshot``.
    contract_path = workspace / "design" / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["graph"]["proposed_modules"] = ["mutated-workspace-node"]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    outcome = {
        "step": "design",
        "dashboard_snapshot": {"snapshot": snapshot.to_dict()},
    }
    views.refresh_views(str(workspace), outcome)
    html_path = Path(outcome["dashboard"]["delivery"]["artifacts"]["html"][
        "path"])
    document = html_path.read_text(encoding="utf-8")

    assert 'data-dashboard-source="canonical"' in document
    assert "snapshot-node" in document
    assert "mutated-workspace-node" not in document
    assert views.decode_dashboard_artifact(
        "html", document.encode("utf-8")) == snapshot.to_dict()
