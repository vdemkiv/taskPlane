"""Stage-aware Design, Plan, and repository-impact dashboard graphs."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys


TASKPLANE = Path(__file__).resolve().parents[1]
ROOT = TASKPLANE.parent
FIXTURES = Path(__file__).with_name("fixtures") / "dashboard-phase"
sys.path.insert(0, str(TASKPLANE))

import dashboard  # noqa: E402


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "design").mkdir(parents=True)
    (ws / "plan").mkdir()
    shutil.copyfile(FIXTURES / "design-contract.json", ws / "design" / "contract.json")
    shutil.copyfile(FIXTURES / "plan-tasks.json", ws / "plan" / "tasks.json")
    return ws


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _state(step: str, *, exact_receipt: bool = False) -> dict:
    state = {"step": step, "design_required": True}
    if exact_receipt:
        state["delivery_mode_receipt"] = _json("approved-plan-receipt.json")
    return state


def test_design_graph_is_visible_from_design_onward(tmp_path):
    ws = _workspace(tmp_path)
    impact = _json("module-impact.json")

    define = dashboard.phase_graph_projection(str(ws), _state("pm"), impact=impact)
    assert "design_graph" not in define
    for step in ("design", "design_approval", "plan", "plan_approval",
                 "execute", "evaluate", "fix", "em", "signoff", "done",
                 "failed"):
        projection = dashboard.phase_graph_projection(
            str(ws), _state(step), impact=impact)
        graph = projection["design_graph"]
        assert graph["schema"] == "taskplane.dashboard-design-graph/v1"
        assert graph["source"] == "design/contract.json#/graph"
        assert graph["module_total"] == 5
        assert graph["edge_total"] == 4


def test_plan_task_dag_and_waves_are_visible_from_plan_onward(tmp_path):
    ws = _workspace(tmp_path)
    impact = _json("module-impact.json")

    design = dashboard.phase_graph_projection(
        str(ws), _state("design_approval"), impact=impact)
    assert "plan_task_dag" not in design
    assert "plan_waves" not in design
    for step in ("plan", "plan_approval", "execute", "em", "done",
                 "failed"):
        projection = dashboard.phase_graph_projection(
            str(ws), _state(step), impact=impact)
        dag = projection["plan_task_dag"]
        waves = projection["plan_waves"]
        assert dag["schema"] == "taskplane.dashboard-plan-task-dag/v1"
        assert dag["task_total"] == 4
        assert dag["edge_total"] == 4
        assert dag["topological_order"] == ["foundation", "api", "ui", "proof"]
        assert waves["schema"] == "taskplane.dashboard-plan-waves/v1"
        assert [wave["id"] for wave in waves["waves"]] == ["W0", "W1", "W2"]


def test_plan_graph_uses_live_governed_task_status_and_wave_execution(tmp_path):
    ws = _workspace(tmp_path)
    state = _state("done")
    state["tasks"] = [
        {"id": task_id, "status": "passed"}
        for task_id in ("foundation", "api", "ui", "proof")
    ]

    projection = dashboard.phase_graph_projection(
        str(ws), state, impact=_json("module-impact.json"))
    dag = projection["plan_task_dag"]
    waves = projection["plan_waves"]

    assert dag["status_source"] == "governed-loop"
    assert dag["status_counts"] == {"passed": 4}
    assert {task["status"] for task in dag["tasks"]} == {"passed"}
    assert waves["execution"] == "passed"
    assert all(wave["execution"] == "passed" for wave in waves["waves"])
    rendered = dashboard.render_phase_dependency_graphs(projection)
    assert "foundation · passed" in rendered
    assert 'data-wave-execution="passed"' in rendered
    assert "approval planned · execution passed" in rendered


def test_plan_graph_refuses_stale_status_when_loop_task_edge_is_severed(tmp_path):
    ws = _workspace(tmp_path)
    state = _state("done")
    state["tasks"] = [{"id": "foundation", "status": "passed"}]

    projection = dashboard.phase_graph_projection(
        str(ws), state, impact=_json("module-impact.json"))
    dag = projection["plan_task_dag"]
    waves = projection["plan_waves"]

    assert dag["status_source"] == "unavailable"
    assert dag["status_counts"] == {"unknown": 4}
    assert {task["status"] for task in dag["tasks"]} == {"unknown"}
    assert waves["execution"] == "unavailable"
    assert "do not match the Plan" in dag["status_error"]


def test_only_exact_plan_receipt_labels_waves_approved(tmp_path):
    ws = _workspace(tmp_path)
    impact = _json("module-impact.json")

    exact = dashboard.phase_graph_projection(
        str(ws), _state("plan_approval", exact_receipt=True), impact=impact)
    assert exact["plan_waves"]["approval"] == "approved"
    assert all(wave["approval"] == "approved"
               for wave in exact["plan_waves"]["waves"])

    bad_state = _state("plan_approval", exact_receipt=True)
    bad_state["delivery_mode_receipt"]["fingerprint"] = "0" * 64
    invalid = dashboard.phase_graph_projection(str(ws), bad_state, impact=impact)
    assert invalid["plan_waves"]["approval"] == "planned"

    plan_path = ws / "plan" / "tasks.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["waves"][1]["serialization"] = "changed after approval"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    stale = dashboard.phase_graph_projection(
        str(ws), _state("plan_approval", exact_receipt=True), impact=impact)
    assert stale["plan_waves"]["approval"] == "planned"


def test_module_impact_reports_source_visible_omitted_and_truncation_totals(tmp_path):
    ws = _workspace(tmp_path)
    projection = dashboard.phase_graph_projection(
        str(ws), _state("execute"), impact=_json("module-impact.json"),
        module_impact_limit=2)
    impact = projection["module_impact"]

    assert impact["schema"] == "taskplane.dashboard-module-impact/v1"
    assert impact["source"] == "taskplane.depgraph.impact"
    assert impact["source_total"] == 5
    assert impact["visible_total"] == 2
    assert impact["omitted_total"] == 3
    assert impact["unknown_total"] == 1
    assert impact["policy_blocked_total"] == 1
    assert impact["source_truncated"] is True
    assert impact["depth_truncated"] is True
    assert impact["render_truncated"] is True

    rendered = dashboard.render_phase_dependency_graphs(projection)
    for text in ("source 5", "visible 2", "omitted 3",
                 "source truncated yes", "depth truncated yes"):
        assert text in rendered


def test_design_graph_plan_dag_waves_and_module_impact_are_distinct(tmp_path):
    ws = _workspace(tmp_path)
    projection = dashboard.phase_graph_projection(
        str(ws), _state("execute", exact_receipt=True),
        impact=_json("module-impact.json"), module_impact_limit=2)

    component_keys = [key for key in projection if key in {
        "design_graph", "plan_task_dag", "plan_waves", "module_impact"}]
    assert component_keys == [
        "design_graph", "plan_task_dag", "plan_waves", "module_impact"]
    assert len({component["schema"]
                for key, component in projection.items()
                if key in component_keys}) == 4
    rendered = dashboard.render_phase_dependency_graphs(projection)
    for component_id in (
        "tp-design-graph", "tp-plan-task-dag", "tp-plan-waves",
        "tp-repository-module-impact",
    ):
        assert f'id="{component_id}"' in rendered
    assert "Design proposed module &amp; edge graph" in rendered
    assert "Plan task dependency DAG" in rendered
    assert "Plan waves" in rendered
    assert "Repository module impact" in rendered

    # One frozen HostSurfaceSnapshot value set wins over later workspace
    # changes; renderers must not re-read and mix revisions.
    (ws / "design" / "contract.json").write_text("{}", encoding="utf-8")
    (ws / "plan" / "tasks.json").write_text("{}", encoding="utf-8")
    frozen = dashboard.phase_graph_projection(
        str(ws), _state("execute"),
        snapshot_values={key: projection[key] for key in component_keys},
        impact={"total_impacted": 999, "impacted": {}})
    assert {key: frozen[key] for key in component_keys} == {
        key: projection[key] for key in component_keys}
