"""Controller task authority survives absent telemetry and phase decisions."""
import json

from taskplane import flow, depgraph, flow_dashboard
from taskplane.tests.test_workflow_local import setup, decide, submit
from taskplane.tests.test_workflow_evidence import bound_output


def test_accepted_native_result_is_completed_without_inventing_timestamps(tmp_path):
    from taskplane.tests.test_native_acceptance_persistence import accepted_fixture
    controller, state, _ = accepted_fixture(tmp_path)
    view = flow.report(tmp_path, state['run'], governor=controller)
    task = next(row for row in view['tasks'] if row['id'] == 'T0')
    assert task['status'] == 'completed'
    html = flow_dashboard.render(str(tmp_path), view)
    assert 'Unknown start time' in html or task.get('started_at')
    assert task.get('phase_decision') != 'approved'


def test_unfinished_task_not_completed_by_phase_approval(tmp_path):
    c, state = setup(tmp_path)
    target = bound_output(c, state)
    rows = json.loads((tmp_path / "tasks.json").read_text())
    for row in rows["tasks"]:
        row["phase"] = "product"
        row["paths"] = ["product.json"]
        row["status"] = "blocked"
    (tmp_path / "tasks.json").write_text(json.dumps(rows))
    state = c.apply("submit", state["run"], expected_revision=state["revision"],
                    output=target, tasks="tasks.json")
    state = decide(c, state)
    view = flow.report(tmp_path, state["run"], governor=c)
    product = [t for t in view["tasks"] if t.get("phase") == "product"]
    assert product and all(t.get("status") != "complete" for t in product), (
        "Phase approval cannot manufacture completion of unfinished tasks")


def test_phase_links_owners_and_missing_artifact_are_explicit(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    model = flow.report(tmp_path, state["run"], governor=c)
    links = {row["path"]: row for row in model["artifact_links"]}
    assert links["product.json"]["status"] == "available"
    assert links["product.json"]["href"] == (tmp_path / "product.json").as_uri()
    html = flow_dashboard.render(str(tmp_path), model)
    assert "Owner / phase" in html and "Phase decision:" in html
    assert "product.json</a>" in html
    (tmp_path / "product.json").unlink()
    missing = flow.phase_artifacts(tmp_path, state, {})
    row = next(item for item in missing if item["path"] == "product.json")
    assert row["status"] == "missing" and "href" not in row


def test_lifecycle_scan_reuses_unchanged_inputs_and_exposes_failure(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("value = 1\n")
    original = depgraph.scan
    calls = []
    def scan(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(depgraph, "scan", scan)
    flow.refresh_scan(tmp_path)
    first = depgraph.load(str(tmp_path))
    flow.refresh_scan(tmp_path)
    assert len(calls) == 1 and depgraph.load(str(tmp_path)) == first
    (tmp_path / "app.py").write_text("value = 2\n")
    flow.refresh_scan(tmp_path)
    assert len(calls) == 2
    def fail(*args, **kwargs):
        raise OSError("fixture scanner unavailable")
    monkeypatch.setattr(depgraph, "scan", fail)
    (tmp_path / "app.py").write_text("value = 3\n")
    flow.refresh_scan(tmp_path)
    view = flow.graph_view(tmp_path, {}, False)
    assert view["status"] == "failed" and "fixture scanner unavailable" in view["detail"]
