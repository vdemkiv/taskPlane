"""Exercise the explicit CLI and real immutable publisher in temporary stores."""
from copy import deepcopy
import json
from types import SimpleNamespace
import time
import pytest
from taskplane import flow, tp, workflow, snapshot_retention as snapshots
from taskplane import dashboard, flow_dashboard


def model(root, decision="approved", checkpoint="checkpoint-1", revision=22):
    return {"run": "fixture-run", "historical": False,
        "workflow": {"root": "fixture-root", "run": "fixture-run", "index": 0,
            "revision": revision, "status": decision,
            "visits": [{"id": "visit-1", "phase": "engineering", "decision": decision,
                        "packet": {"checkpoint": checkpoint}}]},
        "graph": {"status": "current", "data": {}},
        "snapshot": {"workspace": str(root), "root": "fixture-root", "run": "fixture-run",
                     "revision": revision, "generated_at": "2026-10-17T16:00:00Z"}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    state = model(tmp_path)
    monkeypatch.setattr(flow, "report", lambda *a, **k: deepcopy(state))
    monkeypatch.setattr(flow, "_controller", lambda *a, **k:
                        SimpleNamespace(report=lambda *a: deepcopy(state["workflow"])))
    monkeypatch.setattr(flow.depgraph, "source_inputs_current", lambda *a: True)
    monkeypatch.setattr(flow_dashboard, "render", lambda *a: "<main>Current checkpoint</main>")
    monkeypatch.setattr(dashboard, "standalone_document", lambda parts, **k: "".join(parts))
    monkeypatch.setattr(tp.workspace_binding, "resolve_workspace", lambda *a, **k: tmp_path)
    monkeypatch.setattr(tp.workspace_binding, "ensure", lambda *a, **k: None)
    monkeypatch.setattr(snapshots, "MAX_BYTES", 1)
    return tmp_path, state


def catalog(root):
    return json.loads((root / ".taskplane" / snapshots.CATALOG).read_text())


@pytest.mark.parametrize("decision", ["approved", "awaiting_human_approval"])
def test_cli_required_checkpoint_survives_optional_pressure(fixture, decision):
    root, state = fixture
    state["workflow"]["visits"][0]["decision"] = decision
    before = deepcopy(state)
    assert tp.main(["dashboard", "--workspace", str(root), "--run", "fixture-run"]) == 0
    pairs = catalog(root)["pairs"]
    assert len(pairs) == 1
    assert next(iter(pairs.values()))["pins"] == ["checkpoint/checkpoint-1"]
    assert (root / ".taskplane/dashboard.html").exists()
    assert state == before, "Publication cannot rewrite approval or workflow state"


@pytest.mark.parametrize("case", ["unsealed", "no-checkpoint", "finished", "retired",
    "superseded_by", "cancellation", "historical", "final_observation"])
def test_noncurrent_or_unsealed_stays_optional(fixture, case):
    root, state = fixture
    if case == "unsealed":
        state["workflow"]["visits"][0]["decision"] = "pending"
    elif case == "no-checkpoint":
        state["workflow"]["visits"][0]["packet"] = None
    elif case in {"historical", "final_observation"}:
        state[case] = True
    else:
        state["workflow"][case] = True
    with pytest.raises(snapshots.SnapshotCapacityError):
        flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    assert not (root / ".taskplane/dashboard.html").exists()


def test_background_refresh_stays_optional(fixture):
    root, _ = fixture
    with pytest.raises(snapshots.SnapshotCapacityError):
        flow.publish_dashboard(root, "fixture-run")


def test_current_phase_view_survives_pressure_without_claiming_approval(fixture):
    root, state = fixture
    state['workflow']['visits'][0].update(decision='not_requested', packet=None)
    before = deepcopy(state)
    flow.publish_dashboard(root, 'fixture-run', phase_required=True)
    assert next(iter(catalog(root)['pairs'].values()))['pins'] == ['phase/fixture-run/visit-1']
    assert state == before


def test_phase_context_refresh_moves_only_its_working_pin(fixture):
    root, state = fixture
    state['workflow']['visits'][0].update(decision='not_requested', packet=None)
    flow.publish_dashboard(root, 'fixture-run', phase_required=True)
    first = next(iter(catalog(root)['pairs']))
    state['workflow']['revision'] += 1
    state['snapshot']['revision'] += 1
    flow.publish_dashboard(root, 'fixture-run', phase_required=True)
    pairs = catalog(root)['pairs']
    assert not pairs[first]['pins']
    assert sum('phase/fixture-run/visit-1' in pair['pins'] for pair in pairs.values()) == 1
    selected = json.loads((root/'.taskplane/dashboard.selection.json').read_text())
    assert selected['revision'] == state['workflow']['revision']


def test_revision_drift_is_refused(fixture, monkeypatch):
    root, state = fixture
    monkeypatch.setattr(flow, "_controller", lambda *a, **k: SimpleNamespace(
        report=lambda *a: dict(state["workflow"], revision=23)))
    with pytest.raises(workflow.Refusal, match="changed during publication"):
        flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    assert not (root / ".taskplane/dashboard.html").exists()


def test_source_drift_is_refused(fixture, monkeypatch):
    root, _ = fixture
    monkeypatch.setattr(flow.depgraph, "source_inputs_current", lambda *a: False)
    with pytest.raises(workflow.Refusal, match="changed during publication"):
        flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)


def test_retry_pins_the_new_checkpoint(fixture, monkeypatch):
    root, state = fixture
    next_state = model(root, checkpoint="checkpoint-2", revision=23)
    seen = []
    def report(*a, **k):
        seen.append(True)
        return deepcopy(state if len(seen) == 1 else next_state)
    monkeypatch.setattr(flow, "report", report)
    monkeypatch.setattr(flow, "_controller", lambda *a, **k: SimpleNamespace(
        report=lambda *a: deepcopy(next_state["workflow"])))
    flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    assert next(iter(catalog(root)["pairs"].values()))["pins"] == ["checkpoint/checkpoint-2"]
    assert len(seen) == 2


def test_identity_still_refused(fixture):
    root, state = fixture
    state["snapshot"]["workspace"] = "/wrong/workspace"
    with pytest.raises(ValueError, match="identity disagrees"):
        flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)


def test_required_publication_preserves_durability(fixture, monkeypatch):
    root, _ = fixture
    def fail(*a):
        raise OSError("durability unavailable")
    monkeypatch.setattr(snapshots.os, "fsync", fail)
    with pytest.raises(OSError, match="durability unavailable"):
        flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    assert not (root / ".taskplane/dashboard.selection.json").exists()


def test_existing_pin_is_preserved(fixture):
    root, _ = fixture
    flow.publish_dashboard(root, "fixture-run", checkpoint_required=True, pin_reason="explicit-evidence")
    assert next(iter(catalog(root)["pairs"].values()))["pins"] == ["explicit-evidence"]


def test_repeat_reuses_immutable_bytes(fixture):
    root, _ = fixture
    flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    paths = list((root / ".taskplane").glob("snapshot-*.html"))
    assert len(paths) == 1
    before = paths[0].read_bytes(), paths[0].stat().st_mtime_ns
    flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    assert before == (paths[0].read_bytes(), paths[0].stat().st_mtime_ns)
    assert len(catalog(root)["pairs"]) == 1


def test_collection_preserves_required_pair_bytes(fixture):
    root, _ = fixture
    flow.publish_dashboard(root, "fixture-run", checkpoint_required=True)
    pairs = catalog(root)["pairs"]
    paths = [root / ".taskplane" / name for pair in pairs.values() for name in pair["files"]]
    before = {str(p): p.read_bytes() for p in paths}
    result = snapshots.collect(root, max_pairs=0, now=time.time() + 120)
    assert result["removed_pairs"] == 0
    assert before == {str(p): p.read_bytes() for p in paths}


def test_flow_start_and_submit_publish_real_dashboard_but_progress_does_not(tmp_path, capsys):
    from taskplane import workflow_host as h
    from taskplane.tests.test_workflow_evidence import prepare
    initial, _, _ = prepare(tmp_path)
    (tmp_path/'scope.json').write_text(json.dumps(initial['scope']))
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    assert flow.main(['start', '--workspace', str(tmp_path), '--scope', 'scope.json',
                      '--request-reference', 'fixture/dashboard-start'], governor=c) == 0
    capsys.readouterr()
    page = tmp_path/'.taskplane/dashboard.html'
    state = c.report()
    assert page.exists() and state['run'] in page.read_text()
    from taskplane.tests.test_worker_runtime import parent_pair
    parent_pair(c)
    output = json.loads((tmp_path/'product.json').read_text())
    output.update(run=state['run'], visit=workflow.current(state)['id'])
    (tmp_path/'product.json').write_text(json.dumps(output))
    assert flow.main(['submit', '--workspace', str(tmp_path), '--run', state['run'],
                      '--expected-revision', str(state['revision']), '--output', 'product.json',
                      '--tasks', 'tasks.json'], governor=c) == 0
    capsys.readouterr()
    checkpoint = workflow.current(c.report())['packet']['checkpoint']
    assert any('checkpoint/'+checkpoint in pair['pins'] for pair in catalog(tmp_path)['pairs'].values())
    modified = page.stat().st_mtime_ns
    assert flow.main(['progress', '--workspace', str(tmp_path), '--note', 'Still working'], governor=c) == 0
    assert page.stat().st_mtime_ns == modified


def test_dashboard_failure_preserves_committed_phase_result(tmp_path, capsys, monkeypatch):
    from taskplane.tests.test_workflow_local import setup
    c, state = setup(tmp_path)
    output = json.loads((tmp_path/'product.json').read_text())
    output.update(run=state['run'], visit=workflow.current(state)['id'])
    (tmp_path/'product.json').write_text(json.dumps(output))
    def unavailable(*args, **kwargs):
        raise OSError('Fixture dashboard unavailable')
    monkeypatch.setattr(flow, 'publish_dashboard', unavailable)
    assert flow.main(['submit', '--workspace', str(tmp_path), '--run', state['run'],
                      '--expected-revision', str(state['revision']), '--output', 'product.json',
                      '--tasks', 'tasks.json'], governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['workflow']['observation_errors']
    assert workflow.current(c.report())['decision'] == 'awaiting_human_approval'
