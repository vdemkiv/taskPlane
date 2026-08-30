import json
import os

import pytest

import lens
import loop
import review
import review_evidence
import review_progression
import runtime_eval
import storage as runtime_storage


def _policy():
    return review.review_depth_policy({
        "id": "R-0006",
        "review_policy": {"depth": "quick-only"},
    })


def _routing():
    return {
        "lenses": [
            {
                "id": "security", "name": "Security", "tier": "deep",
                "verdict": "deep", "mode": "subagent", "score": 9,
                "reasons": ["authentication boundary changed"],
                "evidence": ["authentication boundary changed"],
                "checks": ["authorization"], "looks_for": "auth defects",
            },
            {
                "id": "qa", "name": "QA", "tier": "light",
                "verdict": "light", "mode": "inline", "score": 4,
                "reasons": ["code changed"], "evidence": ["code changed"],
                "checks": ["regressions"], "looks_for": "test gaps",
            },
            {
                "id": "dba", "name": "Database", "tier": "n/a",
                "verdict": "n/a", "mode": "none", "score": 0,
                "negative_evidence": ["no data surface"],
                "checks": [], "looks_for": "data defects",
            },
        ],
        "context": {
            "changed_files": 1,
            "review_progression": {
                "deep_lenses": ["security"],
                "sweep_lenses": ["qa"],
                "deferred_light": [],
            },
        },
    }


def _start_args(requirement, *, changed_symbols=None):
    return {
        "target": {"fingerprint": "target-r0006", "head": "abc123"},
        "graph": {
            "meta": {
                "scanned_head": "",
                "content_fingerprint": "stale-graph",
                "stale": True,
            },
            "modules": {"src": {"files": ["src/service.py"]}},
            "edges": [],
        },
        "impact": {
            "touched": ["src"], "impacted": {}, "total_impacted": 1,
            "unknown": ["src"],
        },
        "diff": {
            "files": ["src/service.py"],
            "changed_symbols": list(changed_symbols or []),
            "patch_artifact": {"fingerprint": "diff-r0006"},
        },
        "runnability": {"summary": "available"},
        "requirement": requirement,
        "acceptance": ["quick output is sufficient"],
        "contracts": ["contract:review.collection"],
        "stage": "build",
    }


def test_r0006_policy_is_requirement_bound_and_machine_readable():
    explicit = _policy()
    compatibility = review.review_depth_policy({"id": "R-0006"})

    assert explicit["schema"] == "taskplane.review-depth-policy/v1"
    assert explicit["depth"] == "quick-only"
    assert explicit["deep_slots_allowed"] is False
    assert explicit["promotion"] == "correction-required"
    assert compatibility["depth"] == "quick-only"
    assert review.review_depth_policy({"id": "R-0007"})["depth"] == \
        "quick-only"
    with pytest.raises(review.ReviewKernelError, match="cannot weaken"):
        review.review_depth_policy({
            "id": "R-0006", "review_policy": {"depth": "progressive"}})


def test_quick_only_demotes_explicit_deep_routes_into_one_quick_sweep():
    routed = review_progression.apply_depth_policy(_routing(), _policy())
    dispatch = lens.dispatch_briefs(routed)
    wave = review_progression.initial_wave(routed)

    assert dispatch["deep"] == []
    assert dispatch["sweep"]["ids"] == ["qa", "security"]
    assert dispatch["review_depth_policy"]["depth"] == "quick-only"
    assert wave["deep"] == []
    assert wave["sweep"]["lenses"] == ["qa", "security"]
    assert all(row["tier"] != "deep" for row in routed["lenses"])


def test_quick_only_direct_dispatch_demotes_contradictory_deep_signal():
    policy = _policy()
    routing = {
        "lenses": [
            {
                "id": "security", "name": "Security", "tier": "deep",
                "verdict": "n/a", "mode": "none", "score": 0,
                "negative_evidence": ["caller claimed n/a"],
                "checks": [], "looks_for": "auth defects",
            },
            {
                "id": "dba", "name": "Database", "tier": "n/a",
                "verdict": "n/a", "mode": "inline", "score": 0,
                "negative_evidence": ["no data surface"],
                "checks": [], "looks_for": "data defects",
            },
        ],
        "context": {"changed_files": 1, "review_depth_policy": policy},
    }

    dispatch = lens.dispatch_briefs(routing)

    assert dispatch["deep"] == []
    assert dispatch["sweep"]["ids"] == ["security"]
    assert dispatch["routing_decision"]["security"]["verdict"] == "light"
    assert dispatch["routing_decision"]["dba"]["verdict"] == "n/a"


def test_quick_only_direct_dispatch_retains_legacy_sweep_tier():
    policy = _policy()
    routing = {
        "lenses": [
            {
                "id": "security", "name": "Security", "tier": "deep",
                "mode": "subagent", "score": 9,
                "evidence": ["authentication boundary changed"],
                "checks": [], "looks_for": "auth defects",
            },
            {
                "id": "qa", "name": "QA", "tier": "sweep",
                "mode": "inline", "score": 2,
                "evidence": ["legacy sweep route"],
                "checks": [], "looks_for": "regressions",
            },
        ],
        "context": {
            "changed_files": 1,
            "review_depth_policy": policy,
            "review_progression": {
                "deep_lenses": ["security"], "sweep_lenses": ["qa"],
            },
        },
    }

    dispatch = lens.dispatch_briefs(routing)

    assert dispatch["deep"] == []
    assert dispatch["sweep"]["ids"] == ["qa", "security"]
    assert dispatch["routing_decision"]["qa"]["evidence"] == [
        "legacy sweep route"]


def test_quick_only_clears_stage_review_deep_slot_metadata():
    policy = _policy()
    routing = lens.route(
        ["src/auth.py"], stage="review", use_signals=True, workspace=".",
        content_by_file={"src/auth.py": "authorize(user)"})
    assert routing["context"]["review_progression"]["deep_slots"] == []
    routing["context"]["review_depth_policy"] = policy

    routed = review_progression.apply_depth_policy(routing, policy)
    dispatch = lens.dispatch_briefs(routed)

    progression = routed["context"]["review_progression"]
    assert progression["deep_slots"] == []
    assert "deep_lenses" not in progression
    assert dispatch["deep"] == []
    assert dispatch["sweep"]["ids"] == progression["sweep_lenses"]


@pytest.mark.parametrize("changed_symbols", [[], [
    f"service.symbol_{index}" for index in range(19)]])
def test_sparse_or_stale_graph_still_produces_zero_lens_evaluate(
        tmp_path, changed_symbols):
    quick = review.start_review(
        str(tmp_path / ("quick-" + str(len(changed_symbols)))),
        **_start_args({"id": "R-0006"}, changed_symbols=changed_symbols))
    ordinary = review.start_review(
        str(tmp_path / "ordinary"), **_start_args({"id": "R-0007"}))

    assert quick["status"] == "ready"
    assert quick["expected_lenses"] == []
    assert quick["slots"] == []
    assert quick["lens_execution_policy"] == "none"
    assert ordinary["status"] == "ready"
    assert ordinary["expected_lenses"] == []
    assert ordinary["slots"] == []


def test_incremental_retry_override_cannot_reacquire_evaluate_lenses(tmp_path):
    workspace = str(tmp_path / "retry")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}),
        retry_lenses=["security"], retry_source_run_id="prior-run")
    state = review._load_state(workspace, opened["run_id"])
    assert opened["expected_lenses"] == []
    assert opened["slots"] == []
    assert state["zero_lens_evaluation"] is True
    assert "routing" not in state


def test_substantive_quick_finding_requests_correction_without_promotion():
    concern = {
        "id": "quick-risk-1",
        "severity": "high",
        "lens": "security",
        "evidence_ref": "diff:src/service.py:4",
        "rationale": "authorization can be bypassed",
        "trigger": "authorization token",
    }
    outcome = review_progression.resolve_sweep_concerns(
        [concern], review_policy=_policy())
    finding = {
        "lens": "security", "severity": "high", "class": "regression",
    }

    assert outcome["promotions"] == []
    assert outcome["outcome"] == "correction_required"
    assert outcome["corrections"][0]["lens"] == "security"
    assert outcome["corrections"][0]["deep_dispatch"] is False
    assert review.blocking_findings_by_lens([finding]) == {"security": 1}


def test_quick_only_manifest_accepts_complete_quick_output_without_deep_artifacts():
    receipt = review._assert_review_depth_manifest(
        _policy(), [{"slot_id": "light-sweep", "lens_ids": ["qa"]}])

    assert receipt["status"] == "satisfied"
    assert receipt["quick_slots"] == ["light-sweep"]
    assert receipt["deep_slots"] == []
    with pytest.raises(review.ReviewKernelError, match="quick-only"):
        review._assert_review_depth_manifest(
            _policy(), [{"slot_id": "deep.qa", "lens_ids": ["qa"]}])


def test_quick_only_collection_progression_never_builds_a_deep_followup(tmp_path):
    store = review_evidence.ArtifactStore(str(tmp_path))
    ref = store.put("slot-result", {
        "slot_id": "light-sweep",
        "findings": [{
            "lens": "security", "severity": "high", "class": "regression",
            "file": "src/service.py", "line": 4,
            "title": "Authorization can be bypassed",
            "scenario": "An untrusted token reaches the protected action.",
            "fix": "Reject the token before dispatch.",
            "claim": {"trigger": "authorization token"},
        }],
    })
    decision = store.put("routing-decision", {
        "schema": "taskplane.routing-decision/v2",
        "dispositions": {"security": {"verdict": "light"}},
    })
    state = {
        "routing_decision": decision,
        "review_depth_policy": _policy(),
    }

    outcome = review._resolve_sweep_corrections(store, state, [ref])

    assert outcome["corrections"][0]["lens"] == "security"
    assert outcome["outcome"] == "correction_required"


@pytest.mark.parametrize("finding_class", ["pre-existing", "observation"])
def test_quick_only_high_nonblocking_finding_does_not_request_correction(
        tmp_path, finding_class):
    store = review_evidence.ArtifactStore(str(tmp_path))
    ref = store.put("slot-result", {
        "slot_id": "light-sweep",
        "findings": [{
            "lens": "security", "severity": "high", "class": finding_class,
            "file": "src/service.py", "line": 4,
            "title": "Visible but not introduced by this change",
            "scenario": "The quick sweep retained contextual evidence.",
            "claim": {"trigger": "inspect the changed authorization path"},
        }],
    })
    decision = store.put("routing-decision", {
        "schema": "taskplane.routing-decision/v2",
        "dispositions": {"security": {"verdict": "light"}},
    })
    state = {
        "routing_decision": decision,
        "review_depth_policy": _policy(),
    }

    outcome = review._resolve_sweep_corrections(store, state, [ref])

    assert outcome["corrections"] == []
    assert outcome["outcome"] == "continue"


def test_quick_only_cross_domain_regression_bypasses_promotion_charter(
        tmp_path):
    store = review_evidence.ArtifactStore(str(tmp_path))
    finding = {
        "lens": "code-quality", "severity": "low", "class": "regression",
        "file": "src/service.py", "line": 9,
        "title": "Authorization token bypass",
        "scenario": "An authorization token bypass reaches the handler.",
        "claim": {"trigger": "authorization token bypass"},
    }
    ref = store.put("slot-result", {
        "slot_id": "light-sweep", "findings": [finding]})
    decision = store.put("routing-decision", {
        "schema": "taskplane.routing-decision/v2",
        "dispositions": {"code-quality": {"verdict": "light"}},
    })
    state = {
        "routing_decision": decision,
        "review_depth_policy": _policy(),
    }

    outcome = review._resolve_sweep_corrections(store, state, [ref])

    assert review.blocking_findings_by_lens([finding]) == {"code-quality": 1}
    assert outcome["outcome"] == "correction_required"
    assert outcome["corrections"][0]["lens"] == "code-quality"
    assert outcome["corrections"][0]["deep_dispatch"] is False


def _write_quick_output(workspace, run_id, findings):
    state = review._load_state(workspace, run_id)
    if state.get("zero_lens_evaluation") is True:
        assert state["slots"] == []
        assert findings == []
        return
    assert 4 <= len(state["slots"]) <= 5
    assert all(slot["slot_id"].startswith("sweep.")
               for slot in state["slots"])
    store = review_evidence.ArtifactStore(workspace)
    blocking = review.blocking_findings_by_lens(findings)
    for index, slot in enumerate(state["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        lens_ids = list(lease["lens_ids"])
        slot_findings = [row for row in findings
                         if row.get("lens") in lens_ids]
        row = {
            **lease,
            "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot",
            "lens_results": [{
                "lens": lens_id,
                "verdict": "fail" if blocking.get(lens_id) else "pass",
                "blockers": blocking.get(lens_id, 0),
                **({"checked_evidence": [{
                    "file": "src/service.py", "line": 1,
                    "claim": "quick sweep checked the changed service",
                }]} if not blocking.get(lens_id) else {}),
            } for lens_id in lens_ids],
            "findings": slot_findings,
        }
        if brief.get("language_references"):
            row["references_applied"] = list(brief["language_references"])
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        event = {
            "session_id": f"quick-sweep-session-{index}",
            "agent_id": f"quick-sweep-child-{index}",
            "tool_name": "Write",
            "tool_input": {"file_path": slot["result_path"],
                           "content": content},
        }
        contract = {
            "task": brief["producer_contract"]["task"],
            "task_id": f"quick-sweep-contract-{index}", "read_only": True,
            "write_allow": [slot["result_path"]],
        }
        review.register_slot_producer(
            workspace, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        review.record_slot_write_observation(
            workspace, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        path = slot["result_path"]
        if not os.path.isabs(path):
            path = os.path.join(workspace, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)


def _write_green_evaluator_output(workspace, state):
    verdict = {
        "schema": "taskplane.evaluator-output/v2",
        "task": "t02-wave1-quick-only-review-policy",
        "requirement": "R-0006",
        "verdict": "pass",
        "evaluation": {
            "status": "complete", "reason_code": "none", "detail": "",
        },
        "criteria": [{
            "criterion": "quick output is sufficient", "status": "met",
            "evidence": "canonical light-sweep result is complete",
        }],
        "graph": {
            "dispositions": [], "requirements_checked": ["R-0006"],
            "contracts_checked": ["contract:review.collection"],
        },
        "failures": [],
    }
    path = runtime_storage.evaluation_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(verdict, stream, sort_keys=True)
    return verdict


def test_clean_quick_output_completes_without_any_deep_artifact(tmp_path):
    workspace = str(tmp_path / "clean")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    _write_quick_output(workspace, opened["run_id"], [])
    verdict = _write_green_evaluator_output(
        workspace, review._load_state(workspace, opened["run_id"]))

    collected = loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"],
        evaluator_result=verdict,
        producer_observation_fingerprint="a" * 64)
    state = review._load_state(workspace, opened["run_id"])

    assert collected["status"] == "complete", json.dumps(
        collected, indent=2, sort_keys=True)
    assert collected["review_depth_policy"]["outcome"] == \
        "quick_output_sufficient"
    assert collected["review_depth_policy"]["deep_slots"] == []
    assert "adaptive_wave" not in state
    assert state["slots"] == []


def test_clean_quick_output_satisfies_legacy_runtime_receipts(tmp_path):
    workspace = str(tmp_path / "runtime-receipts")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    _write_quick_output(workspace, opened["run_id"], [])
    verdict = _write_green_evaluator_output(
        workspace, review._load_state(workspace, opened["run_id"]))
    loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"],
        evaluator_result=verdict,
        producer_observation_fingerprint="b" * 64)
    state = review._load_state(workspace, opened["run_id"])
    quality = review_evidence.ArtifactStore(workspace).read(state["quality"])
    assert quality["status"] == "impact_incomplete"
    facts = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])

    assert facts["graph_before_route"] is False
    assert all(facts[key] for key in runtime_eval.REVIEW_FACTS
               if key != "graph_before_route")
    assert runtime_eval.assess("evaluate", facts)["status"] != "on_path"


def test_schema_validated_evaluator_is_the_quick_output_without_slot_result(
        tmp_path):
    workspace = str(tmp_path / "evaluator-is-quick-output")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))

    state = review._load_state(workspace, opened["run_id"])
    verdict = _write_green_evaluator_output(workspace, state)
    collected = loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"],
        evaluator_result=verdict,
        producer_observation_fingerprint="c" * 64)
    state = review._load_state(workspace, opened["run_id"])
    assert collected["status"] == "complete"
    assert state["slots"] == []
    assert state["expected_lenses"] == []
    assert state["zero_lens_evaluation"] is True
    assert state["revision"]["findings"] == []

    facts = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])

    assert facts["output_schema_declared"] is True
    assert facts["output_schema_validated"] is True
    assert facts["lens_results_collected"] is True
    assert facts["output_producer_observed"] is True


def test_zero_lens_collection_refuses_unobserved_evaluator(tmp_path):
    workspace = str(tmp_path / "unobserved-evaluator")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    state = review._load_state(workspace, opened["run_id"])
    verdict = _write_green_evaluator_output(workspace, state)
    with pytest.raises(
        review.ReviewKernelError,
        match="schema-valid producer result and validated observation",
    ):
        loop.collect_review_bridge(
            workspace, publish=False, run_id=opened["run_id"],
            evaluator_result=verdict)
    assert state["status"] == "ready"
    assert "revision" not in state
    assert "lens_results" not in state


def test_schema_validated_quick_output_also_satisfies_evaluate_gate(
        tmp_path, monkeypatch):
    workspace = str(tmp_path / "evaluator-quick-gate")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    kernel = review._load_state(workspace, opened["run_id"])
    verdict = _write_green_evaluator_output(workspace, kernel)
    loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"],
        evaluator_result=verdict,
        producer_observation_fingerprint="d" * 64)
    task = {
        "id": "t02-wave1-quick-only-review-policy",
        "req": "R-0006",
        "criteria": ["quick output is sufficient"],
        "contracts": ["contract:review.collection"],
    }
    state = {"step": "evaluate", "graph_governance": False}
    binding = {"workspace": workspace, "run_id": opened["run_id"]}
    monkeypatch.setattr(
        loop, "review_kernel_binding", lambda *_args: binding)
    monkeypatch.setattr(loop, "_design_current_errors", lambda *_args: [])

    errors = loop._evaluation_errors(workspace, state, task)
    assert any("producer observation" in error or
               "leased slot collection" in error for error in errors), errors


def test_evaluate_gate_still_rejects_substantive_quick_output(
        tmp_path, monkeypatch):
    workspace = str(tmp_path / "evaluator-quick-gate-blocker")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    kernel = review._load_state(workspace, opened["run_id"])
    _write_green_evaluator_output(workspace, kernel)
    path = runtime_storage.evaluation_path(workspace)
    with open(path, encoding="utf-8") as stream:
        verdict = json.load(stream)
    verdict["lenses"] = [{"lens": "security", "verdict": "fail",
                          "blockers": 1}]
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(verdict, stream, sort_keys=True)
    task = {
        "id": "t02-wave1-quick-only-review-policy",
        "req": "R-0006",
        "criteria": ["quick output is sufficient"],
        "contracts": ["contract:review.collection"],
    }
    state = {"step": "evaluate", "graph_governance": False}
    binding = {"workspace": workspace, "run_id": opened["run_id"]}
    monkeypatch.setattr(
        loop, "review_kernel_binding", lambda *_args: binding)
    monkeypatch.setattr(loop, "_design_current_errors", lambda *_args: [])

    errors = loop._evaluation_errors(workspace, state, task)

    assert any("contains lenses" in error for error in errors), errors


def test_evaluator_quick_output_fails_closed_on_lens_or_provisional_blocker(
        tmp_path):
    workspace = str(tmp_path / "evaluator-quick-negative")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    state = review._load_state(workspace, opened["run_id"])
    _write_green_evaluator_output(workspace, state)
    path = runtime_storage.evaluation_path(workspace)
    with open(path, encoding="utf-8") as stream:
        verdict = json.load(stream)
    verdict["lenses"] = [{"lens": "security", "verdict": "fail",
                          "blockers": 1}]
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(verdict, stream, sort_keys=True)

    with pytest.raises(loop.evaluation_output.OutputValidationError,
                       match="contains lenses"):
        loop.evaluation_output.validate_evaluator_value(verdict)


def test_zero_lens_evaluate_has_no_runtime_finding_override(
        tmp_path):
    workspace = str(tmp_path / "runtime-negative")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    state = review._load_state(workspace, opened["run_id"])
    assert state["slots"] == []
    assert state["expected_lenses"] == []
    assert state["zero_lens_evaluation"] is True


def test_evaluate_collection_cannot_synthesize_quick_regression(tmp_path):
    workspace = str(tmp_path / "blocking")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    state = review._load_state(workspace, opened["run_id"])
    verdict = _write_green_evaluator_output(workspace, state)
    collected = loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"],
        evaluator_result=verdict,
        producer_observation_fingerprint="e" * 64)
    state = review._load_state(workspace, opened["run_id"])

    assert collected["status"] == "complete"
    assert state["revision"]["findings"] == []
    assert state["slots"] == []
