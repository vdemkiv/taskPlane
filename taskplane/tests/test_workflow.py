"""N01/N02/N05/N07/N16/N17 and route/decision behavior P01–P05."""
from copy import deepcopy
import pytest
from taskplane import workflow as w


def scope():
    return {"criteria": ["AC1"], "paths": {p: [] for p in w.PHASES}, "verification_inputs": []}


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


@pytest.mark.parametrize("choice", ["changes_requested", "rejected", "cancelled"])
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
    s = ready(state("engineering", True), route_change={"kind": "repair"})
    before = deepcopy(s)
    with pytest.raises(w.Refusal, match="accepted Plan"):
        w.decide(s, answer(s))
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
    s = ready(s, route_change={"kind": "repair"})
    before = deepcopy(s)
    with pytest.raises(w.Refusal, match="Plan|Build"):
        w.decide(s, answer(s))
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
