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
        "progressive"
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
    assert routing["context"]["review_progression"]["deep_slots"]
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
def test_sparse_or_stale_graph_still_dispatches_governed_quick_only(
        tmp_path, changed_symbols):
    quick = review.start_review(
        str(tmp_path / ("quick-" + str(len(changed_symbols)))),
        **_start_args({"id": "R-0006"}, changed_symbols=changed_symbols))
    ordinary = review.start_review(
        str(tmp_path / "ordinary"), **_start_args({"id": "R-0007"}))

    assert quick["status"] == "ready"
    assert quick["graph_degraded"] is True
    assert quick["review_depth_policy"]["depth"] == "quick-only"
    assert [slot["slot_id"] for slot in quick["slots"]] == ["light-sweep"]
    assert quick["routing_counts"]["deep"] == 0
    assert ordinary["status"] == "impact_incomplete"
    assert ordinary["slots"] == []


def test_incremental_retry_override_is_finally_demoted_to_quick(tmp_path):
    workspace = str(tmp_path / "retry")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}),
        retry_lenses=["security"], retry_source_run_id="prior-run")
    state = review._load_state(workspace, opened["run_id"])
    security = next(row for row in state["routing"]["lenses"]
                    if row["id"] == "security")

    assert opened["routing_counts"]["deep"] == 0
    assert [slot["slot_id"] for slot in opened["slots"]] == ["light-sweep"]
    assert security["initial_verdict"] == "n/a"
    assert security["verdict"] == security["tier"] == "light"
    assert security["mode"] == "inline"
    routed_ids = {row["id"] for row in state["routing"]["lenses"]
                  if row["mode"] != "none"}
    leased_ids = {lens_id for slot in opened["slots"]
                  for lens_id in slot["lens_ids"]}
    assert routed_ids == leased_ids == {"security"}


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
    with pytest.raises(review.ReviewKernelError, match="promotion"):
        review._assert_review_depth_manifest(
            _policy(), [{"slot_id": "light-sweep", "lens_ids": ["qa"]}],
            promotions={"qa": [{"id": "risk"}]})


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

    outcome = review._light_sweep_promotions(store, state, [ref])

    assert outcome["promotions"] == {}
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

    outcome = review._light_sweep_promotions(store, state, [ref])

    assert outcome["promotions"] == {}
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

    outcome = review._light_sweep_promotions(store, state, [ref])

    assert review.blocking_findings_by_lens([finding]) == {"code-quality": 1}
    assert outcome["promotions"] == {}
    assert outcome["outcome"] == "correction_required"
    assert outcome["corrections"][0]["lens"] == "code-quality"
    assert outcome["corrections"][0]["deep_dispatch"] is False


def _write_quick_output(workspace, run_id, findings):
    state = review._load_state(workspace, run_id)
    assert [slot["slot_id"] for slot in state["slots"]] == ["light-sweep"]
    store = review_evidence.ArtifactStore(workspace)
    slot = state["slots"][0]
    lease = store.read(slot["lease"])
    brief = store.read(slot["brief"])
    blocking = review.blocking_findings_by_lens(findings)
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
        } for lens_id in lease["lens_ids"]],
        "findings": findings,
    }
    if brief.get("language_references"):
        row["references_applied"] = list(brief["language_references"])
    content = json.dumps(row, sort_keys=True, separators=(",", ":"))
    event = {
        "session_id": "quick-sweep-session",
        "agent_id": "quick-sweep-child",
        "tool_name": "Write",
        "tool_input": {"file_path": slot["result_path"], "content": content},
    }
    contract = {
        "task": brief["producer_contract"]["task"],
        "task_id": "quick-sweep-contract", "read_only": True,
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
        "schema": "taskplane.evaluator-output/v1",
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
        "lenses": [{
            "lens": lens_id, "verdict": "pass", "blockers": 0,
        } for lens_id in state["slots"][0]["lens_ids"]],
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


def test_clean_quick_output_completes_without_any_deep_artifact(tmp_path):
    workspace = str(tmp_path / "clean")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    _write_quick_output(workspace, opened["run_id"], [])

    collected = review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])

    assert collected["status"] == "complete", json.dumps(
        collected, indent=2, sort_keys=True)
    assert collected["review_depth_policy"]["outcome"] == \
        "quick_output_sufficient"
    assert collected["review_depth_policy"]["deep_slots"] == []
    assert "adaptive_wave" not in state
    assert [slot["slot_id"] for slot in state["slots"]] == ["light-sweep"]


def test_clean_quick_output_satisfies_legacy_runtime_receipts(tmp_path):
    workspace = str(tmp_path / "runtime-receipts")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    _write_quick_output(workspace, opened["run_id"], [])
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])
    quality = review_evidence.ArtifactStore(workspace).read(state["quality"])
    assert quality["status"] == "impact_incomplete"
    _write_green_evaluator_output(workspace, state)

    facts = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])

    assert facts == {key: True for key in runtime_eval.REVIEW_FACTS}
    assert runtime_eval.assess("evaluate", facts)["status"] == "on_path"


def test_schema_validated_evaluator_is_the_quick_output_without_slot_result(
        tmp_path):
    workspace = str(tmp_path / "evaluator-is-quick-output")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))

    # Match the live workflow: the legacy collection probe records its one
    # absent lens-slot producer, while the evaluator authors the complete,
    # schema-validated quick judgment itself.
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])
    assert state["status"] == "ready"
    assert state["provisional_revision"]["completeness"]["missing"] == 1
    assert "revision" not in state
    assert "lens_results" not in state
    _write_green_evaluator_output(workspace, state)

    facts = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])

    assert facts == {key: True for key in runtime_eval.REVIEW_FACTS}
    assert runtime_eval.assess("evaluate", facts)["status"] == "on_path"


def test_schema_validated_quick_output_also_satisfies_evaluate_gate(
        tmp_path, monkeypatch):
    workspace = str(tmp_path / "evaluator-quick-gate")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    kernel = review._load_state(workspace, opened["run_id"])
    _write_green_evaluator_output(workspace, kernel)
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

    assert loop._evaluation_errors(workspace, state, task) == []


def test_evaluate_gate_still_rejects_substantive_quick_output(
        tmp_path, monkeypatch):
    workspace = str(tmp_path / "evaluator-quick-gate-blocker")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    kernel = review._load_state(workspace, opened["run_id"])
    _write_green_evaluator_output(workspace, kernel)
    path = runtime_storage.evaluation_path(workspace)
    with open(path, encoding="utf-8") as stream:
        verdict = json.load(stream)
    verdict["lenses"][0].update({"verdict": "fail", "blockers": 1})
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

    assert "evaluation selective review kernel is missing or incomplete" in \
        errors
    assert any("routed lens lacks a leased slot result" in error
               for error in errors), errors


def test_evaluator_quick_output_fails_closed_on_lens_or_provisional_blocker(
        tmp_path):
    workspace = str(tmp_path / "evaluator-quick-negative")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])
    _write_green_evaluator_output(workspace, state)
    path = runtime_storage.evaluation_path(workspace)
    with open(path, encoding="utf-8") as stream:
        verdict = json.load(stream)
    verdict["lenses"][0].update({"verdict": "fail", "blockers": 1})
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(verdict, stream, sort_keys=True)

    failing_lens = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])
    assert runtime_eval.assess("evaluate", failing_lens)["status"] != \
        "on_path"

    verdict["lenses"][0].update({"verdict": "pass", "blockers": 0})
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(verdict, stream, sort_keys=True)
    state["provisional_revision"]["findings"] = [{
        "lens": verdict["lenses"][0]["lens"], "severity": "high",
        "class": "regression", "file": "src/service.py",
    }]
    review._save_state(workspace, state)

    substantive_finding = runtime_eval.review_facts(
        workspace, "evaluate", run_id=opened["run_id"])
    assert runtime_eval.assess(
        "evaluate", substantive_finding)["status"] != "on_path"


def test_quick_runtime_receipt_override_rejects_blockers_and_other_requirements(
        tmp_path):
    workspace = str(tmp_path / "runtime-negative")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    finding = {
        "lens": "code-quality", "kind": "defect", "severity": "high",
        "class": "regression", "file": "src/service.py", "line": 1,
        "title": "Changed service bypasses its guard",
        "scenario": "The changed path reaches the handler without a guard.",
        "fix": "Restore the guard.",
        "claim": {
            "trigger": "invoke the changed path",
            "outcome": "the handler runs without its guard",
            "repro": "call the changed service and inspect the guard",
        },
    }
    _write_quick_output(workspace, opened["run_id"], [finding])
    review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])
    quality = review_evidence.ArtifactStore(workspace).read(state["quality"])
    _write_green_evaluator_output(workspace, state)
    with open(runtime_storage.evaluation_path(workspace), encoding="utf-8") \
            as stream:
        verdict = json.load(stream)

    assert not runtime_eval._complete_quick_only_evaluation(
        state, quality, verdict, review)
    ordinary = dict(state)
    ordinary["review_depth_policy"] = review.review_depth_policy({
        "id": "R-0007"})
    assert not runtime_eval._complete_quick_only_evaluation(
        ordinary, quality, verdict, review)


def test_quick_regression_collects_canonically_then_remains_gate_blocking(tmp_path):
    workspace = str(tmp_path / "blocking")
    opened = review.start_review(
        workspace, **_start_args({"id": "R-0006"}))
    finding = {
        "lens": "code-quality", "kind": "defect", "severity": "low",
        "class": "regression", "file": "src/service.py", "line": 1,
        "title": "Changed service returns the wrong value",
        "scenario": "Calling changed() returns an incompatible value.",
        "fix": "Restore the promised value and cover the regression.",
        "claim": {
            "trigger": "call changed() with the supported input",
            "outcome": "the service returns an incompatible value",
            "repro": "invoke changed() and compare the returned value",
        },
    }
    _write_quick_output(workspace, opened["run_id"], [finding])

    collected = review.collect_review(
        workspace, publish=False, run_id=opened["run_id"])
    state = review._load_state(workspace, opened["run_id"])

    assert collected["status"] == "complete", json.dumps(
        collected, indent=2, sort_keys=True)
    assert collected["review_depth_policy"]["outcome"] == \
        "correction_required"
    assert collected["quick_corrections"][0]["lens"] == "code-quality"
    assert review.blocking_findings_by_lens(state["revision"]["findings"]) == {
        "code-quality": 1}
    assert "adaptive_wave" not in state
    assert [slot["slot_id"] for slot in state["slots"]] == ["light-sweep"]
