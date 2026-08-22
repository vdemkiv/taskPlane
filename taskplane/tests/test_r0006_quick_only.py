import json
import os

import pytest

import lens
import review
import review_evidence
import review_progression


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
