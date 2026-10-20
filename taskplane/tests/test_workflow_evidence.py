"""N06/N08/N09/N10/N12 and P10 exercise artifact and source binding."""
import json
import sys
from pathlib import Path
from copy import deepcopy
import pytest
from taskplane import workflow as w, workflow_evidence as e


@pytest.mark.parametrize('nonce', [None, '../outside', 'A'*32, '0'*31])
def test_fresh_package_projection_requires_a_bounded_controller_nonce(tmp_path, nonce):
    state, _, _ = prepare(tmp_path, 'evaluate')
    check = {'command': [sys.executable, '-B', 'scripts/package_openai.py', '--output-dir', 'old'],
             'evidence_outputs': ['old/candidate.zip', 'old/candidate.zip.json', 'check.log']}
    with pytest.raises(w.Refusal, match='controller-allocated'):
        e.check_execution(tmp_path, state, check, nonce=nonce)
    assert not (tmp_path/'old').exists()


def test_check_projection_refuses_an_unowned_literal_output_and_incomplete_package_pair(tmp_path):
    state, _, _ = prepare(tmp_path, 'evaluate')
    with pytest.raises(w.Refusal, match='outside its current phase'):
        e.check_execution(tmp_path, state, {'command': ['python3', 'writer.py', 'outside.txt'],
                                           'evidence_outputs': ['outside.txt']})
    with pytest.raises(w.Refusal, match='archive and provenance pair'):
        e.check_execution(tmp_path, state,
            {'command': [sys.executable, '-B', 'scripts/package_openai.py', '--output-dir', 'old'],
             'evidence_outputs': ['old/candidate.zip']}, nonce='0'*32)


@pytest.mark.parametrize('relative', ['.taskplane/workflow-root.json', '.taskplane/harness-root.json',
    '.taskplane/workspace-binding.json', '.taskplane/context-v1/objects/object.json'])
def test_controller_metadata_is_readable_but_never_a_task_write(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{}')
    assert e.read(tmp_path, relative) == b'{}'
    with pytest.raises(w.Refusal, match='Controller metadata'):
        e.write_path(tmp_path, relative)


def test_task_write_rejects_hardlink_alias_without_changing_either_file(tmp_path):
    import os
    first, alias = tmp_path / 'owned.txt', tmp_path / 'outside.txt'
    first.write_text('preserved')
    os.link(first, alias)
    with pytest.raises(w.Refusal, match='multiply-linked'):
        e.write_path(tmp_path, 'owned.txt')
    assert first.read_text() == alias.read_text() == 'preserved'


def test_invalid_repair_task_is_refused_before_sealing_or_consuming_consent(tmp_path):
    state, _, _ = prepare(tmp_path, 'build', plan_fixture=True)
    state['visits'][3].update(decision='approved', packet={'output': {}})
    state['index'] = 4
    before = deepcopy(state)
    with pytest.raises(w.Refusal, match='existing approved Build tasks|typed Plan'):
        w.route_eligibility(state, {'kind': 'repair', 'tasks': ['FOREIGN']})
    assert state == before


@pytest.mark.parametrize('fresh_subset', [False, True])
def test_evaluate_never_inherits_an_ineligible_identical_pass(tmp_path, monkeypatch, fresh_subset):
    from taskplane import context_reuse as reuse
    from taskplane.tests.test_farm_evidence import contract, capture, fake_key
    state, plan, check = contract(tmp_path)
    (tmp_path / 'app.py').write_text('value = 2\n')
    def unknown_key(*args, **kwargs):
        return {**fake_key(*args, **kwargs), 'eligible': False}
    monkeypatch.setattr(reuse, 'key', unknown_key)
    secondary = {**deepcopy(check), 'id': 'secondary', 'required': False}
    if fresh_subset:
        plan['verification_strategy']['checks'].append(secondary)
    def run(requirement, history, log):
        provenance = reuse.key(tmp_path, paths=requirement['source_inputs'], tests=requirement['test_inputs'],
            criteria=requirement['criteria'], command=requirement['command'], tool='python', contract='fixture/v1')
        reuse.run_check(tmp_path, provenance, producer='root', state=state, history_path=history,
            log_path=log, run=state['run'], visit=w.current(state)['id'], check_id=requirement['id'],
            kind=requirement['kind'], environment=requirement['environment'])
        row = json.loads((tmp_path / history).read_text())['attempts'][-1]
        return {'check_id': requirement['id'], 'attempt_id': row['id'], 'status': row['status'], 'evidence': log}
    output = {'verification_history': 'history.json', 'build_checks': [run(check, 'history.json', 'check-1.log')]}
    capture(state, output, e.validate_verification(tmp_path, state, output))
    state['index'] = 4
    evaluated = {'criterion_results': {'AC1': {'status': 'unknown'}}}
    if fresh_subset:
        state['scope']['paths']['evaluate'] += ['eval-history.json', 'eval-secondary.log']
        run(secondary, 'eval-history.json', 'eval-secondary.log')
        evaluated['verification_history'] = 'eval-history.json'
    selected = e.validate_verification(tmp_path, state, evaluated)['effective_checks']
    assert next(row for row in selected if row['check_id'] == check['id'])['status'] == 'unknown'
    evaluated['criterion_results']['AC1']['status'] = 'pass'
    with pytest.raises(w.Refusal, match='Criterion pass requires'):
        e.validate_verification(tmp_path, state, evaluated)


def test_repair_uses_pinned_later_unknown_instead_of_copied_build_pass(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from taskplane import context_reuse as reuse, workflow_host
    from taskplane.tests.test_farm_evidence import contract, attempt, capture, fake_key
    state, plan, check = contract(tmp_path)
    other = dict(id='OTHER', phase='build', owner='root', paths=['other.txt'], dependencies=[],
                 read_inputs=[], criteria=['AC1'], verification='Review the unrelated output')
    plan['task_dag'].append(other)
    plan['write_scope'].append('other.txt')
    state['scope']['paths']['build'].append('other.txt')
    (tmp_path / 'other.txt').write_text('unrelated')
    (tmp_path / 'app.py').write_text('value = 2\n')
    output, old_history, _ = attempt(tmp_path, monkeypatch, state, check, 'check-1.log')
    capture(state, output, e.validate_verification(tmp_path, state, output))
    state['index'] = 4
    state['scope']['paths']['evaluate'] += ['eval-history.json', 'eval.log']
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], .01)
    monkeypatch.setattr(reuse, '_execute', timeout)
    provenance = fake_key(tmp_path, paths=check['source_inputs'], tests=check['test_inputs'],
        criteria=check['criteria'], command=check['command'], tool='python', contract='fixture/v1')
    reuse.run_check(tmp_path, provenance, producer='root', state=state, history_path='eval-history.json',
        log_path='eval.log', run=state['run'], visit=w.current(state)['id'], check_id=check['id'],
        kind=check['kind'], environment=check['environment'])
    evaluated = {'verification_history': 'eval-history.json', 'criterion_results': {'AC1': {'status': 'unknown'}}}
    state['visits'][4].update(decision='approved', packet={'output': evaluated,
        'verification': e.validate_verification(tmp_path, state, evaluated)})
    previous = deepcopy(state)
    state = w.repair(state, ['OTHER'], {'human': True, 'automatic': False, 'choice': 'approved'})
    workflow_host.Controller._retain_repair_context(SimpleNamespace(workspace=tmp_path), previous, state)
    history = w.current(state)['artifact_paths'][2]
    (tmp_path / history).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / history).write_text(json.dumps({**old_history, 'visit': w.current(state)['id']}))
    selected = e.validate_verification(tmp_path, state, {'verification_history': history})['effective_checks']
    assert selected[0]['status'] == 'unknown' and selected[0]['evidence'] == 'eval.log'
    with pytest.raises(w.Refusal, match='controller-pinned newest attempt'):
        e.validate_verification(tmp_path, state, {'verification_history': history, 'build_checks': output['build_checks']})


def prepare(tmp_path, phase="product", *, plan_fixture=False):
    from taskplane import depgraph

    scope = {"criteria": ["AC1"], "paths": {p: [p+".json"] for p in w.PHASES},
             "verification_inputs": ["app.py"]}
    scope["paths"]["build"].append("app.py")
    scope['paths']['build'] += ['build-check.txt', 'checks.json']
    scope['paths']['evaluate'] += ['evaluate-checks.json', 'evaluate-check.txt']
    s = w.new_state(str(tmp_path), "root", "run", scope)
    # This low-level fixture also exercises readable historical untyped packets.
    # New controller runs normalize their contract to ownership/v1 separately.
    s['scope']['execution_contract'] = None
    s["index"] = w.PHASES.index(phase)
    graph = tmp_path/".taskplane/knowledge"
    graph.mkdir(parents=True, exist_ok=True)
    (tmp_path/".taskplane/dashboard.html").write_text("<html>Shared dashboard</html>")
    if not (tmp_path/"app.py").exists():
        (tmp_path/"app.py").write_text("value = 1\n")
    tasks = {"tasks": [{"id": "T1", "phase": "build", "dependencies": [], "paths": scope['paths']['build'],
                        "owner": "root", "read_inputs": ['app.py'], "verification": "Observe behavior", "criteria": ["AC1"]}]}
    (tmp_path/"tasks.json").write_text(json.dumps(tasks))
    out = {"schema": "taskplane.phase-output/v1", "run": "run", "phase": phase,
           "visit": w.current(s)["id"], "criteria": ["AC1"],
           **{field: "Specific reviewed content" for field in w.OUTPUT_FIELDS[phase]}}
    if phase == "product":
        out["acceptance_criteria"] = [{"id": "AC1", "statement": "Human accepts the declared outcome"}]
    if phase == "design":
        out["acceptance_test_map"] = {"AC1": "Exercise the native decision boundary"}
    if phase == "plan":
        out.update(write_scope=scope['paths']['build'], acceptance_coverage={"AC1": ["T1"]},
                   task_dag=tasks["tasks"], ownership={"T1": "primary"}, integration_order=["T1"])
        out['ownership'] = {'T1': 'root'}
        out['verification_strategy'] = {'schema': e.VERIFICATION_STRATEGY, 'checks': [dict(
            id='fixture', name='Application assertion', task='T1', kind='unit', environment='local',
            required=True, criteria=['AC1'], source_inputs=['app.py'], test_inputs=[],
            command=[sys.executable, '-c', 'exec(open("app.py").read()); assert isinstance(value, int)'],
            evidence_outputs=['build-check.txt'])]}
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
        historical = json.loads((tmp_path/'plan.json').read_text())
        historical['verification_strategy'] = 'Historical untyped verification'
        (tmp_path/'plan.json').write_text(json.dumps(historical))
        plan_state = w.submit(plan_state, e.seal(tmp_path, plan_state, "plan.json", "tasks.json"))
        plan_state = w.decide(plan_state, {"event_id": "fixture-plan", "human": True, "automatic": False,
                                          "choice": "approved", "binding": w.binding(plan_state, w.current(plan_state)["packet"])})
        s["visits"][w.PHASES.index("plan")] = w.current(plan_state)
        s["decisions"] = plan_state["decisions"]
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    return s, out, tasks


def bound_output(controller, state):
    """Fixture output with actual typed checks at owned Build/Evaluate paths."""
    phase = w.current(state)['phase']
    repairing = w.current(state)['id'] in state.get('repair_scope', {}).get('visits', [])
    renewed = w.current(state).get('artifact_paths') == w.visit_result_paths(state, w.current(state))
    target, history = phase+'.json', 'checks.json'
    if repairing or renewed:
        if renewed:
            target, _, _, history = w.current(state)['artifact_paths'][:4]
        else:
            target, _, history, _ = w.current(state)['artifact_paths'][:4]
        plan = w.accepted_plan(state)['packet']['output']
        output = {'schema': 'taskplane.phase-output/v1', 'phase': phase, 'criteria': state['scope']['criteria'],
                  **{field: 'Fixture phase analysis' for field in w.OUTPUT_FIELDS[phase]}}
        if phase == 'build':
            output.update(change_inventory=['app.py'], task_acceptance_map=plan['acceptance_coverage'],
                          known_gaps=[], build_checks=[])
        elif phase == 'evaluate':
            output['criterion_results'] = {criterion: dict(status='pass', evidence='check.txt', explanation='Actual checks passed')
                                           for criterion in state['scope']['criteria']}
        elif phase == 'engineering':
            output.update(findings=[], lens_coverage=[dict(lens='quality', reviewer='root', rationale='Reviewed affected work')],
                          requirements_comparison={criterion: 'Matches affected behavior' for criterion in state['scope']['criteria']})
        prior = next((v['packet'].get('verification', {}).get('history') for v in reversed(state['visits'])
                      if v.get('packet') and v['decision'] == 'approved' and v['packet'].get('verification', {}).get('history')), None)
        if prior and phase == 'build':
            captured = {**deepcopy(prior), 'visit': w.current(state)['id']}
            (controller.workspace/history).parent.mkdir(parents=True, exist_ok=True)
            (controller.workspace/history).write_text(json.dumps(captured))
    else:
        _, output, _ = prepare(controller.workspace, phase)
        if phase == 'evaluate':
            # Progressive flows allocate this visit's paths at advance. The
            # historical fixed filenames are not authority for a fresh visit.
            target = state['scope']['paths']['evaluate'][0]
    output.update(run=state['run'], visit=w.current(state)['id'])
    (controller.workspace/target).parent.mkdir(parents=True, exist_ok=True)
    (controller.workspace/target).write_text(json.dumps(output))
    if phase in {'build', 'evaluate'}:
        from taskplane import context_reuse as reuse
        from taskplane.context import Store
        plan = w.accepted_plan(state)['packet']['output']
        checks = plan.get('verification_strategy', {}).get('checks', [])
        if checks:
            if phase == 'evaluate':
                history = (w.current(state)['artifact_paths'][3] if renewed else
                           w.current(state)['artifact_paths'][2] if repairing else
                           next(p for p in state['scope']['paths']['evaluate']
                                if Path(p).name in {'evaluate-checks.json', 'checks.json'}))
            rows = []
            for check in checks:
                log = check['evidence_outputs'][0]
                if phase == 'evaluate':
                    log = next((p for p in state['scope']['paths']['evaluate']
                                if Path(p).name in {'evaluate-check.txt', 'report.md'}), 'evaluate-check.txt')
                if renewed:
                    log = w.current(state)['artifact_paths'][2]
                elif repairing:
                    from taskplane.primitives import content_fingerprint
                    log = (Path(target).parent / ('check-'+content_fingerprint(check['id'])[:16]+'.log')).as_posix()
                if not (controller.workspace/history).exists():
                    # Discard only the unverified placeholder emitted by prepare.
                    (controller.workspace/log).unlink(missing_ok=True)
                provenance = reuse.key(controller.workspace, paths=check['source_inputs'], tests=check['test_inputs'],
                    criteria=check['criteria'], command=check['command'], tool='python', contract='fixture/v1',
                    state=state, check_id=check['id'], history_path=history, log_path=log,
                    output_path=target)
                reference = reuse.run_check(controller.workspace, provenance, producer='root', state=state,
                    history_path=history, run=state['run'], visit=w.current(state)['id'],
                    check_id=check['id'], kind=check['kind'], environment=check['environment'], log_path=log)
                record = Store(controller.workspace).resolve(reference)
                attempt = json.loads((controller.workspace/history).read_text())['attempts'][-1]
                rows.append(dict(check_id=check['id'], name=check['name'], attempt_id=attempt['id'],
                                 status=record['status'], evidence=log))
            output.update(verification_history=history, build_checks=rows)
            if phase == 'evaluate':
                for result in output['criterion_results'].values():
                    result['evidence'] = sorted({row['evidence'] for row in rows})
    (controller.workspace/target).write_text(json.dumps(output))
    return target


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


@pytest.mark.parametrize('phase,field', [
    ('product', 'non_goals'), ('product', 'dependencies'), ('product', 'task_outline'),
    ('product', 'finding_references'), ('design', 'alternatives'), ('design', 'interfaces_and_state'),
    ('design', 'authority_boundaries'), ('design', 'failure_and_recovery'), ('design', 'graph_impact'),
    ('evaluate', 'source_test_fingerprints'), ('evaluate', 'regression_evidence'), ('evaluate', 'unknowns_and_failures'),
    ('engineering', 'severity'), ('engineering', 'source_locations'), ('engineering', 'remaining_risk'),
    ('retro', 'deferred_items_and_owners'), ('retro', 'lessons'),
])
def test_removed_obligations_cannot_shrink_with_the_validator_constant(tmp_path, phase, field):
    state, output, _ = prepare(tmp_path, phase)
    output.pop(field, None)
    (tmp_path/(phase+'.json')).write_text(json.dumps(output))
    with pytest.raises(w.Refusal, match=field):
        e.seal(tmp_path, state, phase+'.json', 'tasks.json')


def test_new_ownership_plan_requires_typed_mandatory_evidence(tmp_path):
    state, output, _ = prepare(tmp_path, 'plan')
    state['scope']['execution_contract'] = 'ownership/v1'
    output['verification_strategy'] = 'We ran everything successfully'
    (tmp_path/'plan.json').write_text(json.dumps(output))
    with pytest.raises(w.Refusal, match='typed verification'):
        e.seal(tmp_path, state, 'plan.json', 'tasks.json')
    state, output, _ = prepare(tmp_path, 'plan')
    output['verification_strategy']['checks'][0]['required'] = False
    (tmp_path/'plan.json').write_text(json.dumps(output))
    with pytest.raises(w.Refusal, match='AC1'):
        e.seal(tmp_path, state, 'plan.json', 'tasks.json')


def test_phase_content_accepts_equivalent_wording_but_needs_decomposition(tmp_path):
    state, output, _ = prepare(tmp_path, 'design')
    output['interfaces_and_state'] = 'The same API and existing durable workflow state remain authoritative.'
    (tmp_path/'design.json').write_text(json.dumps(output))
    assert e.seal(tmp_path, state, 'design.json', 'tasks.json')['phase'] == 'design'
    with pytest.raises(w.Refusal, match='task decomposition'):
        e.seal(tmp_path, state, 'design.json', '')


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
    assert e.changed(tmp_path, s)  # Declared relevant source changed before Build approval.
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










def test_source_freshness_ignores_runtime_and_task_observations(tmp_path):
    from taskplane import depgraph

    s, _, tasks = prepare(tmp_path, 'build', plan_fixture=True)
    packet = e.seal(tmp_path, s, 'build.json', 'tasks.json')
    (tmp_path/'.taskplane/dashboard.html').write_text('<html>Refreshed counters</html>')
    (tmp_path/'.taskplane/flow-events.jsonl').write_text('{"kind":"usage"}\n')
    tasks['tasks'][0].update(status='complete', elapsed_seconds=1)
    (tmp_path/'tasks.json').write_text(json.dumps(tasks))
    assert packet['context']['graph'] == {}  # Optional graph is not checkpoint authority.
    assert e.seal(tmp_path, s, 'build.json', 'tasks.json')['context']['tasks'][0]['status'] == 'complete'




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




def native_lens_fixture(tmp_path, *, serial=False, phase='engineering'):
    from taskplane import workflow_host as h, depgraph
    from taskplane.tests.test_worker_runtime import reserve, launch, consume, complete
    from taskplane.context_handoff import Brief
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
    from taskplane.context_handoff import Brief
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
    else:
        claims[0][{'reviewer':'reviewer','task':'task_id','grant':'grant','status':'status'}[defect]] = {
            'reviewer':'plausible label', 'task':'T1', 'grant':claims[1]['grant'], 'status':'serial_scope'}[defect]
    (tmp_path/'engineering.json').write_text(json.dumps(out))
    with pytest.raises(w.Refusal, match='lens|Lens|definitions|identities|execution metadata'):
        e.seal(tmp_path, s, 'engineering.json', 'tasks.json')


@pytest.mark.parametrize('defect', ['root-result','unjoined','source','same-reviewer'])
def test_native_lens_actual_result_integrity_refuses(tmp_path, defect):
    from taskplane.context_handoff import Brief
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
    else:
        # Preserve a valid consumer receipt for the reassigned identity so the
        # independent-reviewer rule, rather than receipt mismatch, is exercised.
        from taskplane import worker_runtime as wr
        other = s['task_results']['T1']; second = s['workers'][other['grant']]
        result['worker_id'] = row['worker_id'] = second['worker_id']
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


@pytest.mark.parametrize('change', ['unknown-mode','root-owner','duplicate-lens','wrong-phase'])
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
    from taskplane.context_handoff import Brief
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
    from taskplane.tests.test_worker_runtime import parent_pair
    from taskplane.context_handoff import Brief
    c, s, out, rows = native_lens_fixture(tmp_path, serial=True)
    for row in rows:
        row.update(execution='native_required', owner='native/reviewer')
        row.pop('execution_reason'); row.pop('execution_reference')
    task_path = '.taskplane/native-tasks.json'
    (tmp_path/task_path).write_text(json.dumps({'tasks':rows}))
    s = c.update_tasks(s['run'], s['revision'], task_path)
    parent_pair(c)
    status = c.worker(s['run'], 'capacity', revision=s['revision'], request={'capacity':dict(
        host_slots=0, includes_root=False, status='unavailable', reason='Native host adapter unavailable', reference='host/observed-error')})
    assert {row['status'] for row in status['lens_coverage']} == {'unavailable'}
    with pytest.raises(w.Refusal, match='No available native worker slot'):
        c.worker(s['run'], 'prepare', revision=s['revision'], task='T0', request={'capacity':dict(
            host_slots=0, includes_root=False, status='unavailable', reason='Native host adapter unavailable', reference='host/observed-error')})
    s = c.report()
    out['lens_coverage'] = [dict(lens=row['review_lens'],task_id=row['id'],reviewer='root',
        status='unavailable',rationale='Observed host refusal') for row in rows]
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
        from taskplane.tests.test_worker_runtime import parent_pair
        parent_pair(c)
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


@pytest.mark.parametrize('relative,valid', [
    ('app.py', True), ('build.json', True), ('undeclared.py', True),
    ('../outside.py', False), ('linked.py', False),
])
def test_initial_and_published_read_boundaries_match(tmp_path, relative, valid):
    from taskplane import workflow_host as h
    state, _, tasks = prepare(tmp_path)
    row = tasks['tasks'][0]
    row['read_inputs'] = [relative]
    if relative == 'linked.py':
        state['scope']['verification_inputs'].append(relative)
        (tmp_path/relative).symlink_to(tmp_path/'app.py')
    elif relative == '../outside.py':
        # Even a mistaken declaration cannot authorize traversal.
        state['scope']['verification_inputs'].append(relative)
    (tmp_path/'tasks.json').write_text(json.dumps(tasks))
    controller = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    for invoke in (lambda: controller._initial_tasks({'tasks':'tasks.json'}, state['scope']),
                   lambda: e.freeze_tasks(tmp_path, state, tasks)):
        if valid:
            assert invoke()[0]['read_inputs'] == [relative]
        else:
            with pytest.raises(w.Refusal) as failure:
                invoke()
            assert failure.value.reason == 'scope_violation'




def serial_plan(tmp_path):
    from taskplane.tests.test_workflow import implementation_scope
    scope = implementation_scope()
    scope['paths']['build'] += ['src/database', 'src/api']
    state = w.new_state(str(tmp_path), 'root', 'run', scope)
    state['index'] = w.PHASES.index('plan')
    rows = [dict(id=name, phase='build', execution='native_required', owner='native', dependencies=[],
                 paths=[relative], read_inputs=[], criteria=['AC1'], verification='Exercise the changed behavior')
            for name, relative in [('RULES', 'src/rules.md'), ('DATABASE', 'src/database'), ('API', 'src/api')]]
    return state, rows


def root_exception(row, basis, **details):
    row.update(execution='root', owner='root', execution_reason='Integrate the declared work in its current dependency order.',
               execution_reference='plan/current', execution_exception=dict(
                   schema='taskplane.execution-exception/v1', task=row['id'], basis=basis, **details))
















def test_feasible_plan_can_narrow_optional_paths_and_preserve_root_integration(tmp_path):
    state, rows = serial_plan(tmp_path)
    rows[0]['paths'].append('tests/check_rules')
    inputs = [path for row in rows for path in row['paths']]
    for relative in inputs:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture source or executable check\n')
    intent = state['scope']['implementation_intent']
    root = dict(id='INTEGRATE', phase='build', dependencies=[row['id'] for row in rows],
                paths=list(intent['build_outputs'].values()), read_inputs=inputs,
                criteria=['AC1'], verification='Run service regression and assemble the evidence')
    root_exception(root, 'integration', related_tasks=root['dependencies'], paths=inputs)
    rows.append(root)
    output = dict(write_scope=[path for row in rows for path in row['paths']],
                  build_outputs=[dict(kind=kind, path=path, task='INTEGRATE') for kind, path in intent['build_outputs'].items()],
                  integration_order=[row['id'] for row in rows], verification_strategy=dict(
                      schema=e.VERIFICATION_STRATEGY, checks=[dict(id='CHECK', name='service regression', task='INTEGRATE',
                          kind='unit', environment='local', required=True, criteria=['AC1'], command=['check-service'],
                          source_inputs=['src/rules.md', 'src/database', 'src/api'], test_inputs=['tests/check_rules'],
                          evidence_outputs=['build/report.md'])]))
    assert e.plan_preflight(tmp_path, state, output, rows)['status'] == 'validated'
    assert 'optional.txt' not in output['write_scope']


def test_documentation_only_plan_has_real_deliverable_and_review_without_fabricated_tests(tmp_path):
    from taskplane.tests.test_workflow import implementation_scope
    scope = implementation_scope('documentation')
    state = w.new_state(str(tmp_path), 'root', 'run', scope)
    state['index'] = w.PHASES.index('plan')
    intent = scope['implementation_intent']
    row = dict(id='GUIDE', phase='build', dependencies=[], paths=['guide', *intent['build_outputs'].values()],
               read_inputs=[], criteria=['AC1'], verification='Review every documented step')
    root_exception(row, 'trivial')
    output = dict(write_scope=row['paths'], integration_order=['GUIDE'],
                  build_outputs=[dict(kind=kind, path=path, task='GUIDE') for kind, path in intent['build_outputs'].items()],
                  verification_strategy=dict(schema=e.VERIFICATION_STRATEGY, checks=[dict(
                      id='DOC-REVIEW', name='Review all guide steps', task='GUIDE', kind='static', environment='local',
                      required=True, criteria=['AC1'], command=['review-guide'], source_inputs=['guide'], test_inputs=[],
                      evidence_outputs=['build/report.md'])]))
    assert e.plan_preflight(tmp_path, state, output, [row])['status'] == 'validated'


def test_stronger_contract_still_requires_unavailable_native_results(tmp_path):
    state, rows = serial_plan(tmp_path)
    state['index'] = w.PHASES.index('build')
    state['task_context'] = dict(visit=w.current(state)['id'], tasks=rows)
    state['worker_capacity'] = dict(status='unavailable', effective_limit=0, reason='Current host lacks a native worker adapter')
    with pytest.raises(w.Refusal, match='RULES.*fresh accepted native result.*unavailable'):
        e.execution_evidence(tmp_path, state, rows, {})
    assert all(row['execution'] == 'native_required' for row in rows)


def test_legacy_checkpoint_reports_unknown_feasibility(tmp_path):
    state, _, _ = prepare(tmp_path)
    assert e.prevalidate(tmp_path, state, 'product.json', 'tasks.json')['scope_preflight']['status'] == 'progressive'


@pytest.mark.parametrize('change', ['root', 'rename', 'remove', 'phase', 'capability', 'criteria',
                                     'inputs', 'owner', 'empty', 'placeholder', 'duplicate', 'split_root',
                                     'split_criteria', 'split_inputs', 'split_capability', 'all_work_removed'])
def test_compiled_native_refinement_rejects_lost_obligations(tmp_path, change):
    from taskplane.tests.test_blueprint_harness import delivery_plan
    state, compiled, rows, output = delivery_plan(tmp_path)
    native = next(row for row in rows if row['id'] == 'implementation')
    checkpoint = next(row for row in rows if row['id'] == 'workflow-build-checkpoint')
    if change == 'root':
        native.update(execution='root', owner='root', execution_reason='Integration', execution_reference='fixture')
    elif change in {'rename', 'remove'}:
        old = native['id']
        if change == 'rename':
            native['id'] = 'replacement'
        else:
            rows.remove(native)
        for row in rows:
            row['dependencies'] = [d if d != old else 'replacement' for d in row['dependencies']
                                   if change != 'remove' or d != old]
    elif change == 'phase':
        native['phase'] = 'design'
    elif change == 'capability':
        native['capability'] = 'taskplane.phase.synthesis'
    elif change == 'criteria':
        native['criteria'] = []
    elif change == 'inputs':
        native['read_inputs'] = []
    elif change == 'owner':
        native['owner'] = 'root'
    elif change == 'empty':
        checkpoint['paths'] += native['paths']
        native['paths'] = []
    elif change == 'placeholder':
        checkpoint['paths'] += native['paths']
        native['paths'] = [checkpoint['paths'].pop(0)]
    elif change == 'duplicate':
        checkpoint['paths'].append('main.py')
    elif change.startswith('split_'):
        sibling = deepcopy(native)
        sibling.update(id='implementation-more', paths=['new.py'])
        native['paths'] = ['main.py']
        if change == 'split_root':
            sibling.update(execution='root', owner='root', execution_reason='Integration', execution_reference='fixture')
        elif change == 'split_criteria':
            sibling['criteria'] = []
        elif change == 'split_inputs':
            sibling['read_inputs'] = []
        else:
            sibling['capability'] = 'taskplane.phase.synthesis'
        rows.append(sibling)
    else:
        output['write_scope'] = [p for p in output['write_scope'] if p not in native['paths']]
        native['paths'] = [checkpoint['paths'].pop(0)]
    before = deepcopy(state)
    with pytest.raises(w.Refusal, match='native obligation'):
        e.native_refinement(compiled['compilation'], rows, 'plan', build_paths=output['write_scope'])
    assert state == before


def test_compiled_native_split_narrowing_and_progress_are_valid(tmp_path):
    from taskplane.tests.test_blueprint_harness import delivery_plan
    state, compiled, rows, output = delivery_plan(tmp_path)
    native = next(row for row in rows if row['id'] == 'implementation')
    sibling = deepcopy(native)
    sibling.update(id='implementation-more', paths=['new.py'], status='running')
    native.update(paths=['main.py'], status='complete')
    rows.append(sibling)
    checkpoint = next(row for row in rows if row['id'] == 'workflow-build-checkpoint')
    checkpoint['dependencies'].append(sibling['id'])
    output['integration_order'].insert(-1, sibling['id'])
    assert e.plan_preflight(tmp_path, state, output, rows)['status'] == 'validated'
    assert len(e.freeze_tasks(tmp_path, state, {'tasks': rows})) == len(rows)
    rows.remove(sibling)
    checkpoint['dependencies'].remove(sibling['id'])
    output['integration_order'].remove(sibling['id'])
    output['write_scope'].remove('new.py')
    assert e.plan_preflight(tmp_path, state, output, rows)['status'] == 'validated'


@pytest.mark.parametrize('strict', [False, True])
def test_root_exception_cannot_discharge_compiled_native_obligation(tmp_path, strict):
    from taskplane.tests.test_blueprint_harness import delivery_plan
    state, compiled, rows, _ = delivery_plan(tmp_path)
    native = next(row for row in rows if row['id'] == 'implementation')
    sibling = deepcopy(native)
    sibling.update(id='implementation-more', paths=['new.py'])
    rows.append(sibling)
    native.update(paths=['main.py'], read_inputs=['main.py', 'dependency.py', 'new.py'],
                  dependencies=[*native['dependencies'], sibling['id']])
    root_exception(native, 'integration', related_tasks=[sibling['id']], paths=['new.py'])
    # The exception is structurally valid; other generated root tasks get their
    # own valid proof when exercising the strict implementation contract.
    if strict:
        state['scope']['planning_contract'] = w.PLANNING_CONTRACT
        for row in rows:
            if row['execution'] == 'root' and row['id'] != 'implementation':
                peers = [p for p in rows if p['phase'] == row['phase'] and p['id'] != row['id']]
                if peers:
                    row['dependencies'] = [p['id'] for p in peers]
                    row['read_inputs'] = [p for peer in peers for p in peer['paths']]
                    root_exception(row, 'integration', related_tasks=row['dependencies'], paths=row['read_inputs'])
                else:
                    root_exception(row, 'trivial')
    e.execution_preflight(state, rows)
    with pytest.raises(w.Refusal, match='native obligation'):
        e.native_refinement(compiled['compilation'], rows, 'plan')


@pytest.mark.parametrize('operation', ['preflight', 'prevalidate', 'seal'])
def test_plan_boundaries_authenticate_and_reject_native_downgrade_before_writes(tmp_path, operation):
    from taskplane.tests.test_blueprint_harness import delivery_plan
    state, compiled, rows, output = delivery_plan(tmp_path)
    native = next(row for row in rows if row['id'] == 'implementation')
    native.update(execution='root', owner='root', execution_reason='Integration', execution_reference='fixture')
    output['ownership'][native['id']] = 'root'
    files = compiled['compilation']['phase_files']['plan']
    (tmp_path / files['packet']).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / files['packet']).write_text(json.dumps(output))
    (tmp_path / files['tasks']).write_text(json.dumps({'tasks': rows}))
    before_state = deepcopy(state)
    before_files = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(w.Refusal, match='native obligation'):
        if operation == 'preflight':
            e.plan_preflight(tmp_path, state, output, rows)
        else:
            getattr(e, operation)(tmp_path, state, files['packet'], files['tasks'])
    assert state == before_state
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before_files


def test_plan_prevalidation_and_sealing_keep_valid_compiled_native_work(tmp_path):
    from taskplane import depgraph
    from taskplane.tests.test_blueprint_harness import delivery_plan
    state, compiled, rows, output = delivery_plan(tmp_path)
    files = compiled['compilation']['phase_files']['plan']
    (tmp_path / files['packet']).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / files['packet']).write_text(json.dumps(output))
    (tmp_path / files['tasks']).write_text(json.dumps({'tasks': rows}))
    (tmp_path / '.taskplane/dashboard.html').write_text('<html>Fixture dashboard</html>')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    before_state = deepcopy(state)
    assert e.prevalidate(tmp_path, state, files['packet'], files['tasks'])['phase'] == 'plan'
    assert e.seal(tmp_path, state, files['packet'], files['tasks'])['phase'] == 'plan'
    assert state == before_state


@pytest.mark.parametrize('status', ['fail', 'unknown'])
def test_evaluate_records_own_attempt_without_rewriting_accepted_build(tmp_path, monkeypatch, status):
    import subprocess
    from taskplane import context_reuse as reuse
    from taskplane.tests.test_farm_evidence import contract, attempt, capture, fake_key
    state, _, check = contract(tmp_path)
    (tmp_path / 'app.py').write_text('value = 2\n')
    output, _, _ = attempt(tmp_path, monkeypatch, state, check, 'check-1.log')
    capture(state, output, e.validate_verification(tmp_path, state, output))
    originals = {p: (tmp_path / p).read_bytes() for p in ('history.json', 'check-1.log')}
    state['index'] = w.PHASES.index('evaluate')
    state['scope']['paths']['evaluate'] += ['evaluate-history.json', 'evaluate-check.log']
    def observed_failure(*args, **kwargs):
        if status == 'unknown':
            raise subprocess.TimeoutExpired(args[0], 1)
        return subprocess.CompletedProcess(args[0], 1, '', 'Observed failing fixture command')
    monkeypatch.setattr(reuse, '_execute', observed_failure)
    key = fake_key(tmp_path, paths=check['source_inputs'], tests=check['test_inputs'],
                   criteria=check['criteria'], command=check['command'], tool='python', contract='assertion/v1')
    reuse.run_check(tmp_path, key, producer='root', history_path='evaluate-history.json',
        run=state['run'], visit=w.current(state)['id'], check_id=check['id'], kind=check['kind'],
        environment=check['environment'], log_path='evaluate-check.log', state=state)
    evaluated = {'verification_history': 'evaluate-history.json', 'criterion_results': {'AC1': {'status': status}}}
    validation = e.validate_verification(tmp_path, state, evaluated)
    assert validation['effective_checks'][0]['status'] == status
    stage = state['visits'][state['index']]
    stage.update(decision='awaiting_human_approval', packet={'output': evaluated, 'verification': validation})
    for phase in ('evaluate', 'engineering', 'retro'):
        state['index'] = w.PHASES.index(phase)
        assert e.effective_checks(state)[0]['status'] == status
        assert e.effective_checks(state, output)[0]['status'] == status
        assert e.outcome_summary(state)['current_checks'][0]['status'] == status
    assert {p: (tmp_path / p).read_bytes() for p in originals} == originals


def test_rejected_evaluate_snapshot_does_not_reveal_old_build_pass(tmp_path):
    from taskplane.tests.test_farm_evidence import contract, capture
    state, _, check = contract(tmp_path)
    capture(state, {}, {'effective_checks': [{'check_id': check['id'], 'status': 'pass'}]})
    state['index'] = w.PHASES.index('evaluate')
    state['visits'][state['index']].update(decision='rejected', packet={
        'output': {}, 'verification': {'effective_checks': [{'check_id': check['id'], 'status': 'fail'}]}})
    state['index'] = w.PHASES.index('retro')
    assert e.effective_checks(state)[0]['status'] == 'unknown'


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('status', ['fail', 'unknown'])
@pytest.mark.parametrize('decision', ['awaiting_human_approval', 'changes_requested', 'rejected'])
def test_evaluate_history_alias_preserves_captured_attempts(tmp_path, monkeypatch, host, status, decision):
    import subprocess
    import sys
    from taskplane import context_reuse as reuse, workflow_host as h
    from taskplane.tests.test_farm_evidence import fake_key
    from taskplane.tests.test_workflow_local import decision as human_decision

    monkeypatch.setattr(reuse, 'key', fake_key)
    scope = {'criteria': ['AC1'], 'paths': {phase: [phase + '.json'] for phase in w.PHASES}}
    scope['paths']['build'] += ['app.py', 'history.json', 'build.log']
    scope['paths']['evaluate'] += ['eval-history.json', 'replacement.json', 'alias.json',
                                  'eval-pass.log', 'eval-negative.log', 'eval-recovery.log']
    (tmp_path / 'app.py').write_text('value = 1\n')
    (tmp_path / 'test_app.py').write_text('assert True\n')
    scope['verification_inputs'] = ['app.py', 'test_app.py']
    controller = h.Controller(tmp_path, 'root', h.installed_adapter(host))
    state = controller.start({'scope': scope, 'request_reference': 'fixture/history-alias'})
    from taskplane.tests.test_worker_runtime import parent_pair
    parent_pair(controller)
    task = {'id': 'BUILD', 'phase': 'build', 'owner': 'root', 'dependencies': [],
            'paths': scope['paths']['build'], 'criteria': ['AC1'],
            'read_inputs': ['app.py', 'test_app.py'], 'verification': 'Run assertion'}
    check = {'id': 'check', 'name': 'Check', 'task': 'BUILD', 'criteria': ['AC1'],
             'kind': 'unit', 'environment': 'local', 'required': True,
             'source_inputs': ['app.py'], 'test_inputs': ['test_app.py'],
             'command': [sys.executable, '-c', 'assert True'], 'evidence_outputs': ['build.log']}

    def submit(extra, filename):
        output = {'schema': 'taskplane.phase-output/v1', 'run': state['run'],
                  'visit': w.current(state)['id'], 'phase': w.current(state)['phase'],
                  'criteria': ['AC1'],
                  **{field: 'Fixture phase analysis' for field in w.OUTPUT_FIELDS[w.current(state)['phase']]}, **extra}
        (tmp_path / filename).write_text(json.dumps(output))
        (tmp_path/'tasks.json').write_text(json.dumps({'tasks': [task]}))
        return controller.apply('submit', state['run'], expected_revision=state['revision'], output=filename, tasks='tasks.json')

    def decide(choice='approved'):
        observed = human_decision(state, candidate=choice, event='fixture/' + str(state['revision']))
        return controller.apply('decide', state['run'], expected_revision=state['revision'],
                                native_reference=json.dumps(observed))

    for phase, extra in [
        ('product', {'scope': 'One assertion', 'acceptance_criteria': [{'id': 'AC1', 'statement': 'Run assertion'}]}),
        ('design', {'approach': 'Run check', 'acceptance_test_map': {'AC1': 'Check'}}),
        ('plan', {'task_dag': [task], 'ownership': {'BUILD': 'root'}, 'write_scope': task['paths'],
                  'acceptance_coverage': {'AC1': ['BUILD']}, 'integration_order': ['BUILD'],
                  'verification_strategy': {'schema': e.VERIFICATION_STRATEGY, 'checks': [check]}}),
    ]:
        state = submit(extra, phase + '.json')
        state = decide()
        if phase == 'plan':
            from taskplane.tests.test_worker_runtime import parent_pair
            parent_pair(controller)
        state = controller.apply('advance', state['run'], expected_revision=state['revision'],
                                 phase=w.PHASES[w.PHASES.index(phase) + 1])

    def attempt(history, log, observed_status):
        provenance = fake_key(tmp_path, paths=check['source_inputs'], tests=check['test_inputs'],
                              criteria=check['criteria'], command=check['command'], tool='python', contract='fixture/v1')
        def observed_command(*args, **kwargs):
            if observed_status == 'unknown':
                raise subprocess.TimeoutExpired(args[0], 1)
            return subprocess.CompletedProcess(args[0], 0 if observed_status == 'pass' else 1, 'Fixture outcome', '')
        with monkeypatch.context() as observation:
            observation.setattr(reuse, '_execute', observed_command)
            reuse.run_check(tmp_path, provenance, producer='root', history_path=history,
                run=state['run'], visit=w.current(state)['id'], check_id=check['id'],
                kind=check['kind'], environment=check['environment'], log_path=log, state=state)

    attempt('history.json', 'build.log', 'pass')
    row = json.loads((tmp_path / 'history.json').read_text())['attempts'][-1]
    state = submit({'change_inventory': ['app.py'], 'task_acceptance_map': {'AC1': ['BUILD']},
        'known_gaps': [], 'verification_history': 'history.json', 'build_checks': [
            {'check_id': 'check', 'name': 'Check', 'attempt_id': row['id'], 'status': 'pass', 'evidence': 'build.log'}]},
        'build.json')
    state = decide()
    state = controller.apply('advance', state['run'], expected_revision=state['revision'], phase='evaluate')
    attempt('eval-history.json', 'eval-pass.log', 'pass')
    attempt('eval-history.json', 'eval-negative.log', status)
    state = submit({'verification_history': 'eval-history.json', 'criterion_results': {
        'AC1': {'status': status, 'evidence': 'eval-negative.log', 'explanation': 'Latest observation'}}}, 'evaluate.json')
    if decision != 'awaiting_human_approval':
        state = decide(decision)
    before = controller._path().read_bytes()
    original = (tmp_path / 'eval-history.json').read_bytes()
    history = json.loads(original)
    (tmp_path / 'alias.json').write_text(json.dumps({**history, 'attempts': history['attempts'][:1]}))
    with pytest.raises(w.Refusal, match='cannot be removed'):
        submit({'verification_history': 'alias.json', 'criterion_results': {
            'AC1': {'status': 'pass', 'evidence': 'eval-pass.log', 'explanation': 'Old pass'}}}, 'replacement.json')
    assert controller._path().read_bytes() == before
    assert (tmp_path / 'eval-history.json').read_bytes() == original

    # A renamed complete history remains valid; a fresh attempt can recover.
    (tmp_path / 'alias.json').write_bytes(original)
    state = submit({'verification_history': 'alias.json', 'criterion_results': {
        'AC1': {'status': status, 'evidence': 'eval-negative.log', 'explanation': 'Retained latest observation'}}}, 'replacement.json')
    assert e.effective_checks(state)[0]['status'] == status
    attempt('alias.json', 'eval-recovery.log', 'pass')
    state = submit({'verification_history': 'alias.json', 'criterion_results': {
        'AC1': {'status': 'pass', 'evidence': 'eval-recovery.log', 'explanation': 'Fresh passing attempt'}}}, 'replacement.json')
    assert e.effective_checks(state)[0]['status'] == 'pass'
    assert [row['status'] for row in json.loads((tmp_path / 'alias.json').read_text())['attempts']] == ['pass', status, 'pass']
    assert (tmp_path / 'eval-history.json').read_bytes() == original
