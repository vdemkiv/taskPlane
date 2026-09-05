"""Focused transport fixtures; no fixture claims real native host execution."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from taskplane import design_host_transport, graph_primitives, lens, lens_route_policy
from taskplane import loop, phase_handoff, phase_review
from taskplane.tests.test_stage_non_build_handoffs import _resume_handoff


def _reference(kind, raw):
    digest = hashlib.sha256(raw).hexdigest()
    return {"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
            "kind": kind, "digest": digest, "bytes": len(raw),
            "media_type": "application/json" if not kind.endswith("-narrative")
                          else "text/markdown",
            "destination": phase_handoff.artifact_destination(digest),
            "locator": "repo-artifact://sha256/" + digest}


def _inputs(phase):
    handoff = _resume_handoff(phase)
    requirement = {"id": handoff["requirement"]["id"],
        "acceptance": [row["criterion"] for row in handoff["acceptance"]],
        "contracts": handoff["contracts"], "depends_on": [], "open_questions": [],
        "context_files": ["taskplane/example.py"]}
    design = {"requirement": requirement["id"],
              "summary": "A small modular phase boundary with explicit recovery.",
              "selected_approach": "One shared validator"}
    plan = {"requirement": requirement["id"], "tasks": [{
        "id": "T-001", "req": requirement["id"],
        "scope": ["taskplane/example.py"], "tests": "pytest exact_selector",
        "criteria": requirement["acceptance"], "deps": []}]}
    candidate = design if phase == "design" else plan
    content = {phase: json.dumps(candidate).encode(),
               phase + "-narrative": b"# Authored fixture candidate\n"}
    return handoff, {"attempt_id": "attempt-focused-fixture",
        "requirement": requirement, "design": design,
        "graph": {"modules": {"taskplane": {}}, "edges": [],
                  "meta": {"content_fingerprint": "a" * 64}},
        "plan": plan if phase == "plan" else None,
        "candidate_artifacts": [_reference(kind, raw) for kind, raw in content.items()],
        "candidate_content": content}


def _prepared(phase):
    handoff, kwargs = _inputs(phase)
    return phase_review.prepare(handoff, **kwargs)


def _results(prepared, *, outcome="pass"):
    results = {}
    for brief in prepared["dispatches"]:
        value = {**brief["result_template"], "outcome": outcome, "findings": [],
                 "evidence": "Synthetic protocol fixture; no native review claim."}
        value["fingerprint"] = lens_route_policy.fingerprint(value)
        results[brief["lens"]] = json.dumps(value).encode()
    return results


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_focused_phase_is_closed_quick_and_stateless(phase, monkeypatch):
    def private_read(*_args, **_kwargs):
        pytest.fail("focused phase accessed source or predecessor state")
    monkeypatch.setattr(graph_primitives, "load_graph", private_read)
    monkeypatch.setattr(graph_primitives.Ctx, "read", private_read)
    monkeypatch.setattr(loop.reqs, "get_requirement", private_read)
    monkeypatch.setattr(loop.tp, "load_json", private_read)
    prepared = _prepared(phase)
    plan = prepared["plan"]
    route = plan["route"]
    assert len(route["dispositions"]) == 26
    assert len(prepared["dispatches"]) == len(route["selected"])
    assert {row["lens"] for row in prepared["dispatches"]} == set(route["selected"])
    assert all(row["disposition"] != "execute_deep" for row in route["dispositions"])
    assert all(row["fork_turns"] == "none" for row in prepared["dispatches"])
    assert all(row["result_template"]["phase"] == phase for row in prepared["dispatches"])
    assert all(row["output_paths"] == [row["result_path"]] for row in prepared["dispatches"])
    if phase == "design":
        assert "solution-design" in route["selected"]
    else:
        assert len(route["selected"]) in {3, 4}
    assert "lease" not in prepared["dispatches"][0]


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_focused_phase_replay_retains_current_lease_and_candidate(phase):
    handoff, kwargs = _inputs(phase)
    first = phase_review.prepare(handoff, **kwargs)
    second = phase_review.prepare(handoff, startup=first["startup"], **kwargs)
    assert first == second
    other = copy.deepcopy(kwargs)
    other["attempt_id"] = "attempt-another"
    with pytest.raises(ValueError, match="startup is stale"):
        phase_review.prepare(handoff, startup=first["startup"], **other)


@pytest.mark.parametrize("case", ["digest", "narrative", "different-candidate", "missing-candidate"])
def test_focused_phase_refuses_candidate_not_matching_actual_bytes(case):
    handoff, kwargs = _inputs("design")
    if case == "digest":
        kwargs["candidate_artifacts"][0]["digest"] = "b" * 64
    elif case == "narrative":
        kwargs["candidate_content"]["design-narrative"] = b"changed after author stopped"
    elif case == "different-candidate":
        kwargs["design"]["summary"] = "A different Design"
    else:
        kwargs["design"] = None
    with pytest.raises(ValueError):
        phase_review.prepare(handoff, **kwargs)


def test_focused_plan_overflow_refuses_without_borrowing_prior_approval():
    handoff, kwargs = _inputs("plan")
    kwargs["plan"]["plan_route"] = {"selected": [
        "architecture", "project-management", "testability", "security", "cost-finops"]}
    raw = json.dumps(kwargs["plan"]).encode()
    kwargs["candidate_content"]["plan"] = raw
    kwargs["candidate_artifacts"][0] = _reference("plan", raw)
    with pytest.raises(ValueError, match="scope split or authenticated expanded approval"):
        phase_review.prepare(handoff, **kwargs)


def test_build_never_prepares_lens_workers():
    handoff, kwargs = _inputs("design")
    handoff["successor"]["phase"] = "build"
    with pytest.raises(ValueError, match="Build has zero lenses"):
        phase_review.prepare(handoff, **kwargs)


def test_plan_child_uses_plan_settings_not_build_tier_defaults(monkeypatch):
    monkeypatch.setattr(phase_review.kernel, "_canonical_operational_settings", lambda **_kwargs:
        SimpleNamespace(stages={"plan": SimpleNamespace(model="fixture-plan-model", reasoning="high")},
                        digest="d" * 64))
    prepared = _prepared("plan")
    assert all(row["model"] == "fixture-plan-model" and row["reasoning_effort"] == "high"
               and row["settings_digest"] == "d" * 64 for row in prepared["dispatches"])


def test_collection_cannot_turn_a_forged_empty_route_into_success():
    prepared = _prepared("plan")
    plan = copy.deepcopy(prepared["plan"])
    plan["selected"] = plan["workers"] = []
    route = plan["route"]
    route["selected"] = route["dispatchable_selected"] = []
    for row in route["dispositions"]:
        row.update({"disposition": "not_applicable", "negative_evidence": ["forged absence"]})
    route["route_fingerprint"] = lens_route_policy.fingerprint({
        key: value for key, value in route.items() if key != "route_fingerprint"})
    plan["fingerprint"] = lens_route_policy.fingerprint({
        key: value for key, value in plan.items() if key != "fingerprint"})
    with pytest.raises(ValueError, match="non-empty focused lens floor"):
        phase_review.collect(plan, {}, verify_observation=lambda *_args: None)


@pytest.mark.parametrize("outcome", ["pass", "changes-required"])
def test_collection_requires_exact_observed_children_and_preserves_judgment(outcome):
    prepared = _prepared("plan")
    results = _results(prepared, outcome=outcome)
    observed = []
    def fixture_observer(worker, raw):
        observed.append((worker["lens"], raw))
    collected = phase_review.collect(
        prepared["plan"], results, verify_observation=fixture_observer)
    assert observed == [(worker["lens"], results[worker["lens"]])
                        for worker in prepared["plan"]["workers"]]
    assert collected["status"] == outcome
    assert collected["human_approval"] is False


@pytest.mark.parametrize("case", ["missing", "extra", "foreign-phase", "foreign-candidate", "unobserved"])
def test_collection_refuses_missing_foreign_or_unobserved_output(case):
    prepared = _prepared("design")
    results = _results(prepared)
    first = next(iter(results))
    if case == "missing":
        results.pop(first)
    elif case == "extra":
        results["foreign-lens"] = b"{}"
    elif case in {"foreign-phase", "foreign-candidate"}:
        result = json.loads(results[first])
        result["phase" if case == "foreign-phase" else "candidate_fingerprint"] = "foreign"
        result["fingerprint"] = lens_route_policy.fingerprint({
            key: value for key, value in result.items() if key != "fingerprint"})
        results[first] = json.dumps(result).encode()
    def fixture_observer(_worker, _raw):
        if case == "unobserved":
            raise ValueError("missing trusted byte observation")
    with pytest.raises(ValueError):
        phase_review.collect(prepared["plan"], results, verify_observation=fixture_observer)


def test_existing_design_result_protocol_remains_unchanged():
    plan = {"fingerprint": "a" * 64, "candidate_fingerprint": "b" * 64}
    worker = {"lens": "solution-design", "task_name": "fixture_design_lens",
              "task_slot": "fixture_design_lens", "output": "design/lenses/solution-design.json"}
    brief = design_host_transport.design_worker_brief(plan, worker)
    assert brief["result_schema"]["$id"] == "taskplane.design-lens-result/v1"
    assert "phase" not in brief["result_template"]
    result = {**brief["result_template"], "outcome": "pass", "findings": []}
    result["fingerprint"] = lens_route_policy.fingerprint(result)
    assert design_host_transport.validate_design_worker_result(plan, worker, result) == result
    assert "fingerprint" in brief["result_schema"]["required"]
    del result["fingerprint"]
    with pytest.raises(ValueError, match="fingerprint"):
        design_host_transport.validate_design_worker_result(plan, worker, result)


@pytest.mark.parametrize("phase,outcome", [("design", "pass"), ("plan", "changes-required")])
def test_phase_digest_derivation_preserves_all_judgment_without_mutating_input(phase, outcome):
    prepared = _prepared(phase)
    worker = prepared["plan"]["workers"][0]
    brief = prepared["dispatches"][0]
    assert "fingerprint" not in brief["result_schema"]["required"]
    assert brief["result_fingerprint"]["worker_may_omit"] is True
    value = {**brief["result_template"], "outcome": outcome, "findings": [],
             "evidence": "Synthetic judgment fixture; engine cannot provide this."}
    before = copy.deepcopy(value)
    checked = design_host_transport.validate_design_worker_result(prepared["plan"], worker, value)
    assert value == before and "fingerprint" not in value
    assert checked == {**before, "fingerprint": lens_route_policy.fingerprint(before)}


@pytest.mark.parametrize("fingerprint", [None, "", "f" * 64, False])
def test_phase_digest_derivation_never_repairs_a_supplied_bad_digest(fingerprint):
    prepared = _prepared("design")
    result = {**prepared["dispatches"][0]["result_template"], "outcome": "pass",
              "findings": [], "fingerprint": fingerprint}
    with pytest.raises(ValueError, match="fingerprint"):
        design_host_transport.validate_design_worker_result(
            prepared["plan"], prepared["plan"]["workers"][0], result)


@pytest.mark.parametrize("missing", ["outcome", "findings", "lens", "worker_identity",
                                    "candidate_fingerprint", "team_plan_fingerprint", "phase"])
def test_phase_digest_derivation_never_fills_missing_judgment_or_identity(missing):
    prepared = _prepared("plan")
    result = {**prepared["dispatches"][0]["result_template"], "outcome": "pass", "findings": []}
    del result[missing]
    with pytest.raises(ValueError, match="contract is invalid"):
        design_host_transport.validate_design_worker_result(
            prepared["plan"], prepared["plan"]["workers"][0], result)


def test_collection_observes_raw_bytes_before_any_phase_digest_derivation(monkeypatch):
    prepared = _prepared("design")
    results = _results(prepared)
    for lens_id, raw in results.items():
        value = json.loads(raw)
        del value["fingerprint"]
        results[lens_id] = json.dumps(value).encode()
    observed = []
    original = design_host_transport.validate_design_worker_result
    def validating(plan, worker, value):
        assert observed[-1] == (worker["lens"], results[worker["lens"]])
        assert "fingerprint" not in value
        return original(plan, worker, value)
    monkeypatch.setattr(design_host_transport, "validate_design_worker_result", validating)
    collection = phase_review.collect(prepared["plan"], results,
        verify_observation=lambda worker, raw: observed.append((worker["lens"], raw)))
    assert collection["status"] == "pass" and collection["human_approval"] is False
    assert all("fingerprint" not in json.loads(raw) for raw in results.values())
    def unobserved(*_args):
        raise ValueError("unobserved raw output")
    with pytest.raises(ValueError, match="unobserved raw output"):
        phase_review.collect(prepared["plan"], results, verify_observation=unobserved)


def test_focused_policy_extraction_matches_existing_workspace_wrapper(tmp_path, monkeypatch):
    catalog = lens.load_catalog()
    incumbent = {"lenses": [{"id": row["id"], "verdict": "n/a", "score": 0,
                              "evidence": [], "negative_evidence": ["fixture absent"]}
                             for row in catalog["lenses"]], "context": {"status": "ready"}}
    monkeypatch.setattr(loop.lens_router, "route", lambda *_a, **_k: incumbent)
    evidence = {"files": [], "approved_design": "One local boundary",
                "task_to_ac_coverage": {"T1": ["A1"]}}
    args = {"stage": "plan", "target": "R-0001", "evidence": evidence}
    workspace = loop._focused_stage_route(str(tmp_path), **args)
    sealed = loop._focused_stage_route_from_incumbent(None, incumbent=incumbent, **args)
    assert workspace == sealed
