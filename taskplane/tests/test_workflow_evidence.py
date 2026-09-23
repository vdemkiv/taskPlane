"""N06/N08/N09/N10/N12 and P10 exercise artifact and source binding."""
import json
from copy import deepcopy
import pytest
from taskplane import workflow as w, workflow_evidence as e


def prepare(tmp_path, phase="product", *, plan_fixture=False):
    from taskplane import depgraph

    scope = {"criteria": ["AC1"], "paths": {p: [p+".json"] for p in w.PHASES},
             "verification_inputs": ["app.py"]}
    scope["paths"]["build"].append("app.py")
    s = w.new_state(str(tmp_path), "root", "run", scope)
    s["index"] = w.PHASES.index(phase)
    graph = tmp_path/".taskplane/knowledge"
    graph.mkdir(parents=True, exist_ok=True)
    (tmp_path/".taskplane/dashboard.html").write_text("<html>Shared dashboard</html>")
    if not (tmp_path/"app.py").exists():
        (tmp_path/"app.py").write_text("value = 1\n")
    tasks = {"tasks": [{"id": "T1", "phase": "build", "dependencies": [], "paths": ["build.json", "app.py"],
                        "owner": "primary", "verification": "Observe behavior", "criteria": ["AC1"]}]}
    (tmp_path/"tasks.json").write_text(json.dumps(tasks))
    out = {"schema": "taskplane.phase-output/v1", "run": "run", "phase": phase,
           "visit": w.current(s)["id"], "criteria": ["AC1"],
           **{field: "Specific reviewed content" for field in w.OUTPUT_FIELDS[phase]}}
    if phase == "product":
        out["acceptance_criteria"] = [{"id": "AC1", "statement": "Human accepts the declared outcome"}]
    if phase == "design":
        out["acceptance_test_map"] = {"AC1": "Exercise the native decision boundary"}
    if phase == "plan":
        out.update(write_scope=["build.json", "app.py"], acceptance_coverage={"AC1": ["T1"]},
                   task_dag=tasks["tasks"], ownership={"T1": "primary"}, integration_order=["T1"])
    if phase == "build":
        (tmp_path/"build-check.txt").write_text("Fixture Build check passed")
        out.update(change_inventory=["app.py"], task_acceptance_map={"AC1": ["T1"]},
                   build_checks=[{"name": "Fixture check", "status": "pass", "evidence": "build-check.txt"}])
    if phase == "evaluate":
        (tmp_path/"check.txt").write_text("Observed fixture check result")
        out["criterion_results"] = {"AC1": {"status": "pass", "evidence": "check.txt", "explanation": "Observed check"}}
    if phase == "engineering":
        out.update(findings=[], lens_coverage=[{"lens": "quality", "reviewer": "primary", "rationale": "Boundary checks"}],
                   requirements_comparison={"AC1": "Matches observed checkpoint behavior"})
    (tmp_path/(phase+".json")).write_text(json.dumps(out))
    if phase == "build" and plan_fixture:
        # Unit evidence fixtures also carry a real accepted Plan packet. Controllers
        # use their own stored history; prepare's state is never injected into them.
        plan_state, _, _ = prepare(tmp_path, "plan")
        plan_state = w.submit(plan_state, e.seal(tmp_path, plan_state, "plan.json", "tasks.json"))
        plan_state = w.decide(plan_state, {"event_id": "fixture-plan", "human": True, "automatic": False,
                                          "choice": "approved", "binding": w.binding(plan_state, w.current(plan_state)["packet"])})
        s["visits"][w.PHASES.index("plan")] = w.current(plan_state)
        s["decisions"] = plan_state["decisions"]
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    # Existing native fixture clients now explicitly consume their current inputs.
    # Pure state-machine fixtures remain legacy data and need no retroactive receipt.
    from taskplane import workflow_host
    from taskplane.context_handoff import Session, consume_required
    controller = workflow_host.Controller(tmp_path, "root", workflow_host.installed_adapter("codex"))
    if controller.adapter.state_exists():
        active = controller.report()
        if active.get("context_contract") and w.current(active)["phase"] == phase:
            receipt, returned = consume_required(Session(tmp_path, active))
            assert returned and receipt["consumed_inputs"]
            out["context_receipt"] = receipt
    return s, out, tasks


@pytest.mark.parametrize("phase", w.PHASES)
def test_all_phase_outputs_require_their_own_evidence(tmp_path, phase):
    s, output, _ = prepare(tmp_path, phase, plan_fixture=phase == "build")
    assert e.seal(tmp_path, s, phase+".json", "tasks.json")["phase"] == phase
    for field in w.OUTPUT_FIELDS[phase]:
        broken = deepcopy(output)
        broken.pop(field)
        (tmp_path/(phase+".json")).write_text(json.dumps(broken))
        with pytest.raises(w.Refusal):
            e.seal(tmp_path, s, phase+".json", "tasks.json")
    for field in w.OUTPUT_FIELDS[phase]:
        broken = deepcopy(output)
        broken[field] = "   "
        (tmp_path/(phase+".json")).write_text(json.dumps(broken))
        with pytest.raises(w.Refusal):
            e.seal(tmp_path, s, phase+".json", "tasks.json")


def test_cycle_missing_prerequisite_uncovered_criterion(tmp_path):
    _, _, tasks = prepare(tmp_path)
    for bad in ("cycle", "missing", "coverage"):
        data = deepcopy(tasks)
        data["tasks"][0]["dependencies"] = ["T1"] if bad == "cycle" else ["absent"] if bad == "missing" else []
        if bad == "coverage":
            data["tasks"][0]["criteria"] = []
        with pytest.raises(w.Refusal):
            e.task_dag(data, ["AC1"])


def test_normative_and_source_changes_have_different_effects(tmp_path):
    s, _, _ = prepare(tmp_path)
    s = w.submit(s, e.seal(tmp_path, s, "product.json", "tasks.json"))
    (tmp_path/"app.py").write_text("value = 2\n")
    (tmp_path/".taskplane/dashboard.html").write_text("<html>New telemetry</html>")
    assert e.changed(tmp_path, s) is None
    (tmp_path/"product.json").write_text("{}")
    assert e.changed(tmp_path, s)[0] == w.current(s)["id"]
    s, _, _ = prepare(tmp_path, "evaluate")
    s = w.submit(s, e.seal(tmp_path, s, "evaluate.json", "tasks.json"))
    (tmp_path/"app.py").write_text("changed after verification\n")
    assert e.changed(tmp_path, s)


def test_scope_and_symlink_escapes_are_rejected(tmp_path):
    s, _, _ = prepare(tmp_path)
    (tmp_path/"link").symlink_to(tmp_path.parent, target_is_directory=True)
    for p in ("../elsewhere", "/absolute", ".git/config", "link/file"):
        with pytest.raises(w.Refusal):
            e.path(tmp_path, p)
    (tmp_path/"product.json").unlink()
    (tmp_path/"product.json").symlink_to(tmp_path/"tasks.json")
    with pytest.raises(w.Refusal):
        e.seal(tmp_path, s, "product.json", "tasks.json")


def test_criterion_evidence_must_exist_and_remain_bound(tmp_path):
    s, _, _ = prepare(tmp_path, "evaluate")
    s = w.submit(s, e.seal(tmp_path, s, "evaluate.json", "tasks.json"))
    (tmp_path/"check.txt").unlink()
    assert e.changed(tmp_path, s)
    from taskplane import depgraph
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    with pytest.raises(w.Refusal, match="Evidence unavailable"):
        e.seal(tmp_path, s, "evaluate.json", "tasks.json")


@pytest.mark.parametrize("mapping", [{}, {"AC1": []}, {"AC1": "T1"}, {"AC1": [1]},
                                     {"AC1": ["absent"]}, {"AC1": ["T1", "T1"]}])
def test_build_rejects_missing_or_invalid_approved_task_ids(tmp_path, mapping):
    s, out, _ = prepare(tmp_path, "build", plan_fixture=True)
    out["task_acceptance_map"] = mapping
    (tmp_path/"build.json").write_text(json.dumps(out))
    with pytest.raises(w.Refusal):
        e.seal(tmp_path, s, "build.json", "tasks.json")


@pytest.mark.parametrize("field, value", [("id", "NEW"), ("phase", "engineering"), ("owner", "other"),
                                        ("paths", ["app.py"]), ("verification", "Different verification"),
                                        ("title", "New material instruction"), ("extra_scope", ["other.py"])])
def test_build_rejects_replaced_plan_task_definitions(tmp_path, field, value):
    s, out, tasks = prepare(tmp_path, "build", plan_fixture=True)
    tasks["tasks"][0][field] = value
    if field == "id":
        out["task_acceptance_map"] = {"AC1": [value]}
        (tmp_path/"build.json").write_text(json.dumps(out))
    (tmp_path/"tasks.json").write_text(json.dumps(tasks))
    with pytest.raises(w.Refusal, match="definitions"):
        e.seal(tmp_path, s, "build.json", "tasks.json")


def test_build_allows_observations_but_requires_current_plan(tmp_path):
    s, _, tasks = prepare(tmp_path, "build", plan_fixture=True)
    tasks["tasks"][0].update(status="complete", started_at="2026-09-16T12:00:00Z",
                             completed_at="2026-09-16T12:01:00Z", elapsed_seconds=60)
    (tmp_path/"tasks.json").write_text(json.dumps(tasks))
    packet = e.seal(tmp_path, s, "build.json", "tasks.json")
    assert packet["context"]["tasks"][0]["status"] == "complete"
    s["visits"][w.PHASES.index("plan")]["decision"] = "stale"
    with pytest.raises(w.Refusal, match="accepted Plan"):
        e.seal(tmp_path, s, "build.json", "tasks.json")


@pytest.mark.parametrize("defect", ["criterion", "dependency", "unapproved-coverage", "non-build"])
def test_build_uses_accepted_criterion_associations_and_dependencies(tmp_path, defect):
    s, _, tasks = prepare(tmp_path, "build", plan_fixture=True)
    approved = w.accepted_plan(s)["packet"]["output"]
    second = {**deepcopy(tasks["tasks"][0]), "id": "T2", "criteria": ["AC2"]}
    tasks["tasks"].append(second)
    approved["task_dag"] = deepcopy(tasks["tasks"])
    approved["acceptance_coverage"] = {"AC1": ["T1"], "AC2": ["T2"]}
    mapping = {"AC1": ["T1"], "AC2": ["T2"]}
    if defect == "criterion":
        mapping = {"AC1": ["T2"], "AC2": ["T1"]}
    elif defect == "dependency":
        tasks["tasks"][1]["dependencies"] = ["T1"]
    elif defect == "non-build":
        tasks["tasks"][1]["phase"] = approved["task_dag"][1]["phase"] = "evaluate"
    else:
        approved["acceptance_coverage"]["AC2"] = ["T1"]
    with pytest.raises(w.Refusal):
        e.build_task_map(s, tasks["tasks"], mapping, ["AC1", "AC2"])


@pytest.mark.parametrize("defect", ["degraded", "missing", "bad-meta", "bad-schema", "bad-type",
                                   "incomplete", "fingerprint", "producer-failure", "component-failure"])
def test_required_graph_quality_cannot_default_to_healthy(tmp_path, defect):
    from taskplane import depgraph

    s, _, _ = prepare(tmp_path)
    target = tmp_path/".taskplane/knowledge/graph.json"
    graph = json.loads(target.read_text())
    quality = graph["meta"]["graph_scan_quality"]
    if defect == "degraded":
        quality["degraded"] = True
        # A healthy top-level decoy cannot conceal the real persisted record.
        graph["graph_scan_quality"] = {"degraded": False}
        assert depgraph.scan_quality(graph)["degraded"]
    elif defect == "missing":
        graph["meta"].pop("graph_scan_quality")
    elif defect == "bad-meta":
        graph["meta"] = "invalid"
    elif defect == "bad-schema":
        quality["schema"] = "unsupported"
    elif defect == "bad-type":
        quality["degraded"] = 0
    elif defect == "incomplete":
        quality["producers"]["decomposition"]["status"] = "not-requested"
    elif defect == "producer-failure":
        quality["producers"]["base-scanner"]["failures"] = [{"reason": "parse failed"}]
    elif defect == "component-failure":
        graph["components"][0]["degraded"] = True
    else:
        quality["fingerprint"] = "corrupt"
    target.write_text(json.dumps(graph))
    with pytest.raises(w.Refusal, match="graph|decomposition"):
        e.seal(tmp_path, s, "product.json", "tasks.json")


def test_missing_or_corrupt_required_graph_refuses(tmp_path):
    s, _, _ = prepare(tmp_path)
    target = tmp_path/".taskplane/knowledge/graph.json"
    target.unlink()
    with pytest.raises(w.Refusal):
        e.seal(tmp_path, s, "product.json", "tasks.json")
    target.write_text("{broken")
    with pytest.raises(w.Refusal):
        e.seal(tmp_path, s, "product.json", "tasks.json")


def test_real_scanner_quality_is_accepted_then_degradation_refuses(tmp_path):
    from taskplane import depgraph

    s, _, _ = prepare(tmp_path)
    graph = depgraph.scan(str(tmp_path), decompose=True, strict=True)
    packet = e.seal(tmp_path, s, "product.json", "tasks.json")
    assert packet["context"]["graph"]["meta"]["graph_scan_quality"] == depgraph.scan_quality(graph)
    (tmp_path/"app.py").write_text("def broken(:\n")
    graph = depgraph.scan(str(tmp_path), decompose=True)
    assert depgraph.scan_quality(graph)["degraded"]
    with pytest.raises(w.Refusal, match="graph|decomposition"):
        e.seal(tmp_path, s, "product.json", "tasks.json")


@pytest.mark.parametrize('phase', ['product', 'design', 'engineering', 'build'])
@pytest.mark.parametrize('change', ['source', 'add', 'remove', 'manifest', 'exclusions', 'artifact'])
def test_checkpoint_rejects_source_and_resolution_drift_until_rescanned(tmp_path, phase, change):
    import os
    from taskplane import depgraph

    (tmp_path/'lib').mkdir()
    (tmp_path/'lib/util.py').write_text('value = 1\n')
    (tmp_path/'lib/package.json').write_text('{"name":"first"}')
    (tmp_path/'lib/guide.md').write_text('lib/util.py\n')
    s, _, _ = prepare(tmp_path, phase, plan_fixture=phase == 'build')
    packet = e.seal(tmp_path, s, phase+'.json', 'tasks.json')
    if change == 'source':
        path = tmp_path/'app.py'
        before = path.stat()
        path.write_text('value = 2\n')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == 'add':
        (tmp_path/'lib/new.py').write_text('from lib import util\n')
    elif change == 'remove':
        (tmp_path/'lib/util.py').unlink()
    elif change == 'manifest':
        (tmp_path/'lib/package.json').write_text('{"name":"second"}')
    elif change == 'exclusions':
        (tmp_path/'components.yaml').write_text('exclude:\n  - lib\n')
    else:
        (tmp_path/'lib/guide.md').write_text('app.py\n')
    with pytest.raises(w.Refusal, match='stale.*scan'):
        e.seal(tmp_path, s, phase+'.json', 'tasks.json')
    graph = depgraph.scan(str(tmp_path), decompose=True, strict=True)
    fresh = e.seal(tmp_path, s, phase+'.json', 'tasks.json')
    assert fresh['context']['graph']['meta']['source_inputs'] == graph['meta']['source_inputs']
    assert packet['context']['graph']['meta']['source_inputs'] != graph['meta']['source_inputs']
    if change == 'source':
        assert packet['context']['graph']['files']['app.py']['hash'] != graph['files']['app.py']['hash']


def test_source_freshness_ignores_runtime_and_task_observations(tmp_path):
    from taskplane import depgraph

    s, _, tasks = prepare(tmp_path, 'build', plan_fixture=True)
    packet = e.seal(tmp_path, s, 'build.json', 'tasks.json')
    (tmp_path/'.taskplane/dashboard.html').write_text('<html>Refreshed counters</html>')
    (tmp_path/'.taskplane/flow-events.jsonl').write_text('{"kind":"usage"}\n')
    tasks['tasks'][0].update(status='complete', elapsed_seconds=1)
    (tmp_path/'tasks.json').write_text(json.dumps(tasks))
    assert depgraph.source_inputs_current(str(tmp_path), packet['context']['graph'])
    assert e.seal(tmp_path, s, 'build.json', 'tasks.json')['context']['tasks'][0]['status'] == 'complete'


def test_legacy_graph_without_source_identity_requires_rescan(tmp_path):
    s, _, _ = prepare(tmp_path)
    path = tmp_path/'.taskplane/knowledge/graph.json'
    graph = json.loads(path.read_text())
    del graph['meta']['source_inputs']
    path.write_text(json.dumps(graph))
    with pytest.raises(w.Refusal, match='source input evidence'):
        e.seal(tmp_path, s, 'product.json', 'tasks.json')


@pytest.mark.parametrize('spelling', ['criteria', 'acceptance_criteria', 'both'])
def test_plan_accepts_equivalent_criterion_spellings_and_build_uses_same_binding(tmp_path, spelling):
    s, out, tasks = prepare(tmp_path, 'plan')
    task = tasks['tasks'][0]
    if spelling != 'criteria':
        task['acceptance_criteria'] = ['AC1']
        if spelling != 'both':
            del task['criteria']
    (tmp_path/'tasks.json').write_text(json.dumps(tasks))
    out['task_dag'] = tasks['tasks']
    (tmp_path/'plan.json').write_text(json.dumps(out))
    s = w.submit(s, e.seal(tmp_path, s, 'plan.json', 'tasks.json'))
    s = w.decide(s, {'event_id':'alias-plan', 'human':True, 'automatic':False,
                    'choice':'approved', 'binding':w.binding(s, w.current(s)['packet'])})
    s = w.advance(s, "build")
    # Criterion spelling can normalize without accepting new task definitions.
    current = deepcopy(tasks['tasks'])
    current[0].pop('criteria', None)
    current[0]['acceptance_criteria'] = ['AC1']
    e.build_task_map(s, current, {'AC1':['T1']}, ['AC1'])
    current[0]['acceptance_criteria'] = ['AC1', 'AC2']
    with pytest.raises(w.Refusal, match='definitions'):
        e.build_task_map(s, current, {'AC1':['T1']}, ['AC1'])


@pytest.mark.parametrize('alias', [[], ['AC2'], 'AC1', None])
def test_conflicting_or_invalid_task_aliases_refuse(tmp_path, alias):
    _, _, tasks = prepare(tmp_path)
    tasks['tasks'][0]['acceptance_criteria'] = alias
    with pytest.raises(w.Refusal, match='aliases conflict'):
        e.task_dag(tasks, ['AC1'])
