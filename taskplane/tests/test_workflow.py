"""N01/N02/N05/N07/N16/N17 and route/decision behavior P01–P05."""
from copy import deepcopy
import pytest
from taskplane import workflow as w


def scope():
    return {"criteria": ["AC1"], "paths": {p: [] for p in w.PHASES}, "verification_inputs": []}


def implementation_scope(kind="source"):
    result = scope()
    result.update(planning_contract="implementation/v1", execution_contract="native-default/v1")
    outputs = dict(packet="build/output.json", report="build/report.md", verification_history="build/checks.json")
    # The source input can itself be a Markdown template; extension heuristics
    # cannot determine whether changing it implements behavior or documents it.
    targets = ["src/rules.md"] if kind == "source" else ["guide"]
    tests = ["tests/check_rules"] if kind == "source" else []
    result["paths"]["build"] = [*outputs.values(), *targets, *tests, "optional.txt"]
    result["implementation_intent"] = dict(kind=kind, implementation_paths=targets, test_paths=tests,
        build_outputs=outputs, criteria={"AC1": dict(paths=targets, verification="Render rules and compare the expected behavior")})
    return result


def test_new_run_can_start_before_future_implementation_paths_are_known():
    value = {"criteria": ["AC1"], "paths": {"product": ["scope.json"]}}
    result = w.new_state("/workspace", "root", "run", value)
    assert result["schema"] == "taskplane.workflow/v2"
    assert result["scope"]["paths"]["build"] == []
    assert result["scope"]["execution_contract"] == "ownership/v1"
    assert w.scope_preflight(result["scope"])["status"] == "progressive"


def state(entry="product", standalone=False):
    return w.new_state("/workspace", "root", "run", scope(), entry=entry, standalone=standalone)


def ready(s, **extra):
    v = w.current(s)
    return w.submit(s, {"phase": v["phase"], "visit": v["id"], "checkpoint": v["id"] + str(s["revision"]),
                        "output": {"write_scope": []}, **extra})


def answer(s, choice="approved", **values):
    v = w.current(s)
    return {"event_id": "human-" + str(s["revision"]), "human": True, "automatic": False,
            "choice": choice, "binding": w.binding(s, v["packet"]), **values}


def accept(s, **extra):
    s = ready(s, **extra)
    return w.decide(s, answer(s))


def test_every_phase_requires_its_own_human_decision():
    s = state()
    for i, phase in enumerate(w.PHASES):
        s = ready(s)
        untouched = deepcopy(s)
        with pytest.raises(w.Refusal, match="human acceptance"):
            w.finish(s)
        if i + 1 < len(w.PHASES):
            with pytest.raises(w.Refusal, match="Human"):
                w.advance(s, w.PHASES[i+1])
        assert s == untouched
        s = w.decide(s, answer(s))
        if i + 1 < len(w.PHASES):
            s = w.advance(s, w.PHASES[i+1])
    assert w.finish(s)["finished"]
    assert len(s["decisions"]) == 7


@pytest.mark.parametrize("entry", w.ENTRY_PHASES)
def test_standalone_acceptance_does_not_fabricate_predecessors(entry):
    s = accept(state(entry, True))
    assert [v["phase"] for v in s["visits"]] == [entry]
    assert w.finish(s)["finished"]
    with pytest.raises(w.Refusal):
        w.advance(s, "build")


def test_skip_direct_build_wrong_binding_and_conflicting_replay():
    with pytest.raises(w.Refusal):
        state("build")
    s = accept(state())
    with pytest.raises(w.Refusal):
        w.advance(s, "build")
    s = ready(w.advance(s, "design"))
    for key in w.BINDING_FIELDS:
        a = answer(s)
        a["binding"][key] = "different"
        with pytest.raises(w.Refusal):
            w.decide(s, a)
    a = answer(s)
    accepted = w.decide(s, a)
    assert w.decide(accepted, a) == accepted
    with pytest.raises(w.Refusal):
        w.decide(accepted, {**a, "choice": "rejected"})


@pytest.mark.parametrize("choice", ["changes_requested", "rejected"])
def test_changes_need_new_checkpoint_and_decision(choice):
    s = ready(state())
    a = answer(s, choice)
    s = w.decide(s, a)
    with pytest.raises(w.Refusal):
        w.advance(s, "design")
    s = ready(s)
    assert w.current(s)["packet"]["checkpoint"] != a["binding"]["checkpoint"]
    assert w.advance(w.decide(s, answer(s)), "design")


def test_findings_can_extend_to_product_and_standalone_design_cannot_skip_it():
    for phase in ("engineering", "design"):
        s = accept(state(phase, True), route_change={"kind": "delivery", "scope": scope()})
        assert w.current(w.advance(s, "product"))["phase"] == "product"
        with pytest.raises(w.Refusal):
            w.advance(s, "build")


@pytest.mark.parametrize("entry", ["design", "engineering"])
@pytest.mark.parametrize("build_paths", [None, ["app.py"]])
def test_progressive_delivery_extension_uses_new_run_scope_normalization(entry, build_paths):
    requested = {"criteria": ["AC1"], "paths": {"product": ["product.json"]},
                 "verification_inputs": ["app.py"]}
    if build_paths is not None:
        requested["paths"]["build"] = build_paths
    original = deepcopy(requested)
    s = accept(state(entry, True), route_change={"kind": "delivery", "scope": requested})
    expected = w.new_state("/workspace", "root", "new-run", requested)["scope"]
    assert s["scope"] == expected
    assert requested == original
    assert s["scope"]["paths"]["build"] == (build_paths or [])
    assert all(not paths for phase, paths in s["scope"]["paths"].items()
               if phase not in {"product", "build"})
    assert w.current(s)["packet"]["route_change"]["scope"] == original
    assert w.current(s)["superseded"]
    assert len(s["decisions"]) == 1
    s = w.advance(s, "product")
    with pytest.raises(w.Refusal):
        w.advance(s, "design")


def test_approved_repair_has_fresh_visits_and_preserves_decision_history():
    s = state()
    for phase in w.PHASES[:5]:
        s = w.advance(accept(s), w.PHASES[w.PHASES.index(phase)+1])
    old_ids = {v["id"] for v in s["visits"]}
    s = accept(s, route_change={"kind": "repair"})
    s = w.advance(s, "build")
    assert w.current(s)["id"] not in old_ids
    assert len(s["decisions"]) == 6
    for phase in w.PHASES[3:]:
        s = accept(s)
        if phase != "retro":
            s = w.advance(s, w.PHASES[w.PHASES.index(phase)+1])
    assert w.finish(s)["finished"]


def test_standalone_repair_cannot_grant_build_or_record_acceptance():
    s = state("engineering", True)
    before = deepcopy(s)
    with pytest.raises(w.Refusal, match="accepted Plan"):
        ready(s, route_change={"kind": "repair"})
    assert s == before
    assert s["decisions"] == {} and len(s["visits"]) == 1


@pytest.mark.parametrize("phase, defect", [("plan", "stale"), ("plan", "superseded"),
                                         ("build", "stale"), ("build", "superseded")])
def test_repair_requires_current_accepted_plan_and_build(phase, defect):
    s = state()
    for p in w.PHASES[:4]:
        s = w.advance(accept(s), w.PHASES[w.PHASES.index(p)+1])
    stage = next(v for v in s["visits"] if v["phase"] == phase)
    stage["superseded" if defect == "superseded" else "decision"] = True if defect == "superseded" else "stale"
    before = deepcopy(s)
    with pytest.raises(w.Refusal, match="Plan|Build"):
        ready(s, route_change={"kind": "repair"})
    assert s == before


def test_repeated_repairs_reuse_accepted_plan_scope_and_fresh_build_lineage():
    s = state()
    for phase in w.PHASES[:3]:
        s = w.advance(accept(s), w.PHASES[w.PHASES.index(phase)+1])
    plan_id = w.accepted_plan(s)["id"]
    for _ in range(2):
        s = w.advance(accept(s), "evaluate")
        s = accept(s, route_change={"kind": "repair", "scope": {"paths": {"build": ["unapproved.py"]}}})
        s = w.advance(s, "build")
        assert w.accepted_plan(s)["id"] == plan_id
        assert s["scope"]["paths"]["build"] == []
        assert not w.current(s)["superseded"]


def repair_fixture():
    s = state()
    for phase in ('design', 'plan'):
        s = w.advance(accept(s), phase)
    tasks = [dict(id=key, phase='build', owner='root', dependencies=[], paths=[source, log],
                  read_inputs=[source], criteria=['AC1'])
             for key, source, log in [('A', 'a.py', 'a.log'), ('B', 'b.py', 'b.log')]]
    checks = [dict(id='CHECK-'+row['id'], task=row['id'], source_inputs=[row['paths'][0]], test_inputs=[])
              for row in tasks]
    plan = dict(write_scope=[p for row in tasks for p in row['paths']], task_dag=tasks,
                verification_strategy={'schema': 'taskplane.verification-strategy/v1', 'checks': checks})
    s = ready(s, output=plan)
    s = w.decide(s, answer(s))
    s = w.advance(s, 'build')
    s['task_results'] = {'A': {'accepted': 'a-result'}, 'B': {'accepted': 'b-result'}}
    return s


def test_partial_repair_only_reopens_affected_tasks_and_keeps_accepted_work():
    s = repair_fixture()
    s = ready(s)
    earlier = deepcopy(s['visits'][:3])
    decisions = deepcopy(s['decisions'])
    result = w.repair(s, ['A'], dict(event_id='human-repair', human=True, automatic=False, choice='changes_requested'))
    assert result['visits'][:3] == earlier and result['decisions'] == decisions
    assert result['repair_scope']['tasks'] == ['A']
    assert result['repair_scope']['checks'] == ['CHECK-A']
    assert result['task_results'] == {'B': s['task_results']['B']}
    assert 'a.py' in w.phase_write_paths(result) and 'b.py' not in w.phase_write_paths(result)
    assert w.current(result)['phase'] == 'build' and w.current(result)['decision'] == 'not_requested'


def test_post_completion_repair_preserves_approvals_and_requires_new_affected_acceptance():
    s = repair_fixture()
    for phase in w.PHASES[4:]:
        s = w.advance(accept(s), phase)
    s = w.finish(accept(s))
    accepted = deepcopy(s['visits'])
    repaired = w.repair(s, ['A'], dict(event_id='human-fix', human=True, automatic=False, choice='approved'))
    assert repaired['visits'][:3] == accepted[:3]
    for old, retained in zip(accepted[3:], repaired['visits'][3:len(accepted)], strict=True):
        assert retained['packet'] == old['packet'] and retained['decision'] == old['decision']
        assert retained['superseded']
    assert not repaired['finished'] and w.current(repaired)['phase'] == 'build'
    assert repaired['task_results']['B'] == s['task_results']['B']
    with pytest.raises(w.Refusal, match='human acceptance'):
        w.finish(repaired)
    with pytest.raises(w.Refusal, match='approved Build tasks'):
        w.repair(s, ['unowned'], dict(human=True, automatic=False, choice='approved'))


def test_superseded_run_cannot_enter_pure_repair():
    state = ready(repair_fixture())
    state['superseded_by'] = 'replacement-run'
    before = deepcopy(state)
    with pytest.raises(w.Refusal, match='This run was replaced'):
        w.repair(state, ['A'], dict(human=True, automatic=False, choice='approved'))
    assert state == before


def test_completion_returns_only_unmet_required_evidence_without_consuming_approval():
    s = ready(state(), verification={'effective_checks': [
        dict(check_id='accepted', required=True, status='pass'),
        dict(check_id='missing', status='unknown'),
        dict(check_id='failed', required=True, status='fail'),
        dict(check_id='advisory', required=False, status='fail'),
    ]})
    before = deepcopy(s)
    assert w.unmet_evidence(w.current(s)['packet']) == ['missing', 'failed']
    with pytest.raises(w.Refusal, match='missing, failed'):
        w.decide(s, answer(s))
    assert s == before


def test_bound_human_correction_reopens_only_the_approved_current_phase():
    s = state()
    for phase in ('design', 'plan'):
        s = w.advance(accept(s), phase)
    s = accept(s)
    earlier = deepcopy(s['visits'][:2])
    decisions = deepcopy(s['decisions'])
    corrected = w.decide(s, answer(s, choice='changes_requested', event_id='human-plan-correction'))
    assert corrected['visits'][:2] == earlier
    assert all(corrected['decisions'][key] == value for key, value in decisions.items())
    assert w.current(corrected)['phase'] == 'plan' and w.current(corrected)['decision'] == 'changes_requested'
    with pytest.raises(w.Refusal, match='approval'):
        w.advance(corrected, 'build')
