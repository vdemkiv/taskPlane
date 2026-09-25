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


def test_frozen_native_lens_cannot_seal_with_plausible_root_labels(tmp_path):
    s, out, _ = prepare(tmp_path, 'engineering')
    rows = [dict(id='L1', phase='engineering', owner='root', paths=['engineering.json'],
                 dependencies=[], criteria=['AC1'], verification='Review source',
                 execution='native_required', review_lens='quality')]
    s['task_context'] = {'visit':w.current(s)['id'], 'tasks':e.freeze_tasks(tmp_path, s, {'tasks':rows})}
    (tmp_path/'tasks.json').write_text(json.dumps({'tasks':rows}))
    out['lens_coverage'] = [dict(lens='quality', task_id='L1', reviewer='root',
        status='native_verified', grant='plausible', rationale='Claimed independent inspection')]
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    from taskplane import depgraph
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    with pytest.raises(w.Refusal, match='native|Native|accepted'):
        e.seal(tmp_path, s, 'engineering.json', 'tasks.json')


def test_native_contract_requires_typed_task_execution(tmp_path):
    s, _, tasks = prepare(tmp_path)
    s['scope']['execution_contract'] = 'native-default/v1'
    with pytest.raises(w.Refusal, match='execution'):
        e.freeze_tasks(tmp_path, s, tasks)


def native_lens_fixture(tmp_path, *, serial=False, phase='engineering'):
    from taskplane import workflow_host as h, depgraph
    from taskplane.tests.test_worker_runtime import reserve, launch, consume, complete
    from taskplane.context_handoff import Session, consume_required
    (tmp_path/'input.py').write_text('value = 1\n')
    scope = dict(criteria=['AC'], execution_contract='native-default/v1',
                 paths={p:[p+'.json'] for p in w.PHASES}, verification_inputs=['input.py'])
    scope['paths'][phase] += ['T0.md', 'T1.md']
    rows = [dict(id='T'+str(i), phase=phase, owner='root' if serial else 'native/reviewer',
                 paths=['T'+str(i)+'.md'], dependencies=[], criteria=['AC'], verification='Inspect actual source',
                 execution='root' if serial else 'native_required') for i in range(2)]
    if phase == 'engineering':
        for row, lens in zip(rows, ['backend', 'security']): row['review_lens'] = lens
    if serial:
        for row in rows: row.update(execution_reason='User explicitly requested serial review', execution_reference='user/serial')
    (tmp_path/'tasks.json').write_text(json.dumps({'tasks':rows}))
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    s = c.start(dict(scope=scope, entry=phase, standalone=True, tasks='tasks.json', request_reference='fixture/request'))
    claims = []
    if not serial:
        prepared = [reserve(c, s, row['id']) for row in rows]
        for i, item in enumerate(prepared):
            launch(c, s, item, 'native-'+str(i)); consume(c, s, item['grant'], 'native-'+str(i))
        for i, item in enumerate(prepared):
            complete(c, s, item, 'native-'+str(i))
            if phase == 'engineering':
                claims.append(dict(lens=rows[i]['review_lens'], task_id=rows[i]['id'], reviewer='native-'+str(i),
                    grant=item['grant']['grant_id'], status='native_verified', rationale='Actual fixture review'))
    else:
        claims = [dict(lens=row['review_lens'], task_id=row['id'], reviewer='root', status='serial_scope',
                       execution_reference='user/serial', rationale='Recorded serial source inspection') for row in rows]
    out = dict(schema='taskplane.phase-output/v1', run=s['run'], visit=w.current(s)['id'], phase=phase,
               criteria=['AC'], **{key:'Observed fixture evidence' for key in w.OUTPUT_FIELDS[phase]})
    if phase == 'engineering': out.update(findings=[], lens_coverage=claims, requirements_comparison={'AC':'Reviewed actual fixture source'})
    if phase == 'product': out.update(acceptance_criteria=[dict(id='AC', statement='Observe the fixture behavior')])
    (tmp_path/(phase+'.json')).write_text(json.dumps(out))
    (tmp_path/'.taskplane/dashboard.html').write_text('<html>Fixture dashboard</html>')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    out['context_receipt'], _ = consume_required(Session(tmp_path, c.report()))
    (tmp_path/(phase+'.json')).write_text(json.dumps(out))
    return c, c.report(), out, rows


def test_distinct_real_native_lenses_seal_and_pin_result_artifacts(tmp_path):
    from taskplane import worker_runtime as wr
    c, s, out, rows = native_lens_fixture(tmp_path)
    packet = e.seal(tmp_path, s, 'engineering.json', 'tasks.json')
    assert {'T0.md','T1.md'} <= set(packet['manifest'])
    assert all(wr.result_valid(tmp_path, s, row['id']) for row in rows)
    accepted = c.apply('submit', s['run'], expected_revision=s['revision'], output='engineering.json', tasks='tasks.json')
    assert {row['status'] for row in c.report()['worker_status']['lens_coverage']} == {'native_verified'}
    (tmp_path/'T0.md').write_text('changed after seal')
    assert e.changed(tmp_path, accepted)


@pytest.mark.parametrize('defect', ['missing','extra','duplicate','reviewer','task','grant','status','downgrade'])
def test_native_lens_false_claims_and_frozen_downgrade_refuse(tmp_path, defect):
    from taskplane.context_handoff import Session, consume_required
    from taskplane import depgraph
    c, s, out, rows = native_lens_fixture(tmp_path)
    claims = out['lens_coverage']
    if defect == 'missing': claims.pop()
    elif defect == 'extra': claims.append({**claims[0], 'lens':'extra'})
    elif defect == 'duplicate': claims[1] = dict(claims[0])
    elif defect == 'downgrade':
        for row in rows: row.pop('execution'); row.pop('review_lens')
        (tmp_path/'tasks.json').write_text(json.dumps({'tasks':rows}))
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
        out['context_receipt'], _ = consume_required(Session(tmp_path, c.report()))
    else:
        claims[0][{'reviewer':'reviewer','task':'task_id','grant':'grant','status':'status'}[defect]] = {
            'reviewer':'plausible label', 'task':'T1', 'grant':claims[1]['grant'], 'status':'serial_scope'}[defect]
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='lens|Lens|definitions|identities|execution metadata'):
        e.seal(tmp_path, s, 'engineering.json', 'tasks.json')


@pytest.mark.parametrize('defect', ['root-result','unjoined','receipt','source','stale-attempt','same-reviewer'])
def test_native_lens_actual_result_integrity_refuses(tmp_path, defect):
    from taskplane.context_handoff import Session, consume_required
    from taskplane import depgraph
    c, s, out, rows = native_lens_fixture(tmp_path)
    result = s['task_results']['T0']; row = s['workers'][result['grant']]
    if defect == 'root-result': result.update(grant=None, worker_id=None)
    elif defect == 'unjoined': row['state'] = 'running'
    elif defect == 'receipt': row['context_receipt'] = None
    elif defect == 'stale-attempt': row['attempt'] += 1
    elif defect == 'source':
        (tmp_path/'input.py').write_text('value = 2\n')
        depgraph.scan(str(tmp_path), decompose=True, strict=True)
        out['context_receipt'], _ = consume_required(Session(tmp_path, s))
    else:
        # Preserve a valid consumer receipt for the reassigned identity so the
        # independent-reviewer rule, rather than receipt mismatch, is exercised.
        from taskplane import worker_runtime as wr
        other = s['task_results']['T1']; second = s['workers'][other['grant']]
        result['worker_id'] = row['worker_id'] = second['worker_id']
        row['context_receipt'], _ = consume_required(wr.worker_session(tmp_path, s, row))
        out['lens_coverage'][0]['reviewer'] = second['worker_id']
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='native result|identities'):
        e.seal(tmp_path, s, 'engineering.json', 'tasks.json')


def test_serial_scope_is_honest_and_unavailable_native_work_blocks(tmp_path):
    c, s, out, rows = native_lens_fixture(tmp_path, serial=True)
    assert e.seal(tmp_path, s, 'engineering.json', 'tasks.json')['output']['lens_coverage'][0]['status'] == 'serial_scope'
    out['lens_coverage'][0]['status'] = 'native_verified'
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='Serial'): e.seal(tmp_path, s, 'engineering.json', 'tasks.json')
    from taskplane.tests.test_worker_runtime import reserve
    with pytest.raises(w.Refusal, match='Root execution'): reserve(c, s)


def test_all_current_phase_required_native_tasks_need_results(tmp_path):
    c, s, out, rows = native_lens_fixture(tmp_path, phase='product')
    assert e.seal(tmp_path, s, 'product.json', 'tasks.json')
    del s['task_results']['T1']
    s['worker_capacity'] = dict(effective_limit=0, status='unavailable', reason='Observed adapter refused native dispatch', reference='host/refusal')
    with pytest.raises(w.Refusal, match='T1.*unavailable'):
        e.seal(tmp_path, s, 'product.json', 'tasks.json')
    from taskplane import worker_runtime as wr
    status = wr.summary(s, tmp_path)
    assert 'native unavailable' in next(row['reason'] for row in status['scheduling'] if row['task'] == 'T1')


@pytest.mark.parametrize('change', ['unknown-mode','missing-reason','missing-reference','root-owner','native-exception','duplicate-lens','wrong-phase'])
def test_typed_execution_metadata_validation(tmp_path, change):
    s, _, _ = prepare(tmp_path, 'engineering')
    rows = [dict(id='L', phase='engineering', owner='root', paths=['engineering.json'], dependencies=[],
                 criteria=['AC1'], verification='Inspect source', execution='root', review_lens='quality',
                 execution_reason='Explicit user serial request', execution_reference='user/request')]
    if change == 'unknown-mode': rows[0]['execution'] = 'maybe'
    elif change == 'missing-reason': rows[0].pop('execution_reason')
    elif change == 'missing-reference': rows[0].pop('execution_reference')
    elif change == 'root-owner': rows[0]['owner'] = 'native/reviewer'
    elif change == 'native-exception': rows[0]['execution'] = 'native_required'
    elif change == 'duplicate-lens': rows.append({**rows[0], 'id':'L2'})
    else: rows[0]['phase'] = 'product'
    with pytest.raises(w.Refusal): e.freeze_tasks(tmp_path, s, {'tasks':rows})


@pytest.mark.parametrize('projection', ['summary', 'dashboard'])
def test_actual_sequential_reuse_of_one_native_worker_cannot_cover_two_lenses(tmp_path, projection):
    from taskplane.tests.test_worker_runtime import reserve, consume
    from taskplane import worker_runtime as wr, depgraph
    from taskplane.context_handoff import Session, consume_required
    c, s, out, rows = native_lens_fixture(tmp_path)
    # Replace T1's completed result with a fresh actual follow-up to T0's worker.
    item = reserve(c, s, 'T1', worker_id='native-0'); grant = item['grant']['grant_id']
    event = dict(tool_name='followup_task', call_id='reuse-for-second-lens',
                 tool_input=dict(target='native-0', message=item['message']))
    c.guard(event, s['run'])
    c.observe({**event, 'hook_event_name':'PostToolUse', 'tool_response':{'agent_id':'native-0'}}, s['run'])
    consume(c, s, item['grant'], 'native-0')
    c.observe(dict(hook_event_name='SubagentStop', call_id=event['call_id'], agent_id='native-0'), s['run'])
    c.worker(s['run'], 'accept-result', revision=s['revision'], task='T1', grant=grant,
             request=dict(outputs=['T1.md'],checks=[dict(name='Follow-up inspection',status='pass',evidence='T1.md')]))
    s = c.report()
    assert all(wr.result_valid(tmp_path, s, row['id']) for row in rows)
    if projection == 'summary':
        coverage = s['worker_status']['lens_coverage']
        assert {row['task_id'] for row in coverage} == {'T0', 'T1'}
        assert {row['status'] for row in coverage} == {'native_pending'}
        assert all('distinct actual worker' in row['rationale'] and 'native-0' in row['rationale']
                   for row in coverage)
        assert all(row['reviewer'] is None and row['grant'] is None for row in coverage)
    else:
        from taskplane import flow, flow_dashboard
        rendered = flow_dashboard.render(str(tmp_path), flow.report(tmp_path, governor=c))
        assert 'Independent review verified' not in rendered
        assert rendered.count('Independent review pending') == 2
        assert rendered.count('distinct actual worker') == 2
    out['lens_coverage'][1].update(reviewer='native-0', grant=grant)
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    out['context_receipt'], _ = consume_required(Session(tmp_path, s))
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='distinct actual worker'):
        e.seal(tmp_path, s, 'engineering.json', 'tasks.json')


def test_verified_lens_dashboard_preserves_token_coverage_and_plain_labels(tmp_path):
    from taskplane import flow, flow_dashboard
    c, s, _, _ = native_lens_fixture(tmp_path)
    model = flow.report(tmp_path, governor=c)
    model['token_coverage'] = dict(basis='fixture native counters', measured_sessions=7, unmeasured_sessions=2)
    rendered = flow_dashboard.render(str(tmp_path), model)
    assert 'Independent review verified' in rendered and 'native-0' in rendered
    assert 'fixture native counters' in rendered and '7 measured' in rendered
    assert 'native_verified' not in rendered


def test_observed_unavailable_capacity_never_completes_required_lenses(tmp_path):
    from taskplane.tests.test_worker_runtime import reserve
    from taskplane.context_handoff import Session, consume_required
    c, s, out, rows = native_lens_fixture(tmp_path, serial=True)
    for row in rows:
        row.update(execution='native_required', owner='native/reviewer')
        row.pop('execution_reason'); row.pop('execution_reference')
    task_path = '.taskplane/native-tasks.json'
    (tmp_path/task_path).write_text(json.dumps({'tasks':rows}))
    s = c.update_tasks(s['run'], s['revision'], task_path)
    status = c.worker(s['run'], 'capacity', revision=s['revision'], request={'capacity':dict(
        host_slots=0, includes_root=False, status='unavailable', reason='Native host adapter unavailable', reference='host/observed-error')})
    assert {row['status'] for row in status['lens_coverage']} == {'unavailable'}
    with pytest.raises(w.Refusal, match='No available native worker slot'):
        c.worker(s['run'], 'prepare', revision=s['revision'], task='T0', request={'capacity':dict(
            host_slots=0, includes_root=False, status='unavailable', reason='Native host adapter unavailable', reference='host/observed-error')})
    s = c.report()
    out['lens_coverage'] = [dict(lens=row['review_lens'],task_id=row['id'],reviewer='root',
        status='unavailable',rationale='Observed host refusal') for row in rows]
    out['context_receipt'], _ = consume_required(Session(tmp_path, s))
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='T0.*unavailable'):
        e.seal(tmp_path, s, 'engineering.json', task_path)
    assert not s.get('task_results')


@pytest.mark.parametrize('mode', ['native', 'serial', 'legacy'])
def test_engineering_coverage_survives_retro_transition_with_sealed_freshness(tmp_path, mode):
    from taskplane import worker_runtime as wr
    if mode == 'legacy':
        s, out, _ = prepare(tmp_path, 'engineering')
        s = w.submit(s, e.seal(tmp_path, s, 'engineering.json', 'tasks.json'))
        s = w.decide(s, dict(event_id='fixture-review', human=True, automatic=False,
            choice='approved', binding=w.binding(s, w.current(s)['packet'])))
    else:
        from taskplane.tests.test_workflow_local import decide
        c, s, out, rows = native_lens_fixture(tmp_path, serial=mode == 'serial')
        s = c.apply('submit', s['run'], expected_revision=s['revision'], output='engineering.json', tasks='tasks.json')
        s = decide(c, s)
        # Give the pure transition fixture its declared next visit; no controller
        # control store is injected or altered by this unit transition test.
        s['visits'].append(w.visit('retro'))
    before = wr.lens_summary(s, tmp_path)
    s = w.advance(s, 'retro')
    assert wr.lens_summary(s, tmp_path) == before
    assert {row['status'] for row in before} == {dict(native='native_verified', serial='serial_scope', legacy='legacy_unverified')[mode]}
    (tmp_path/'input.py' if mode != 'legacy' else tmp_path/'app.py').write_text('changed after approval\n')
    assert not wr.lens_summary(s, tmp_path)
