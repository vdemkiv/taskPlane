"""Stage-adapter proofs for focused-routing/v1 (R-0001, LR-06)."""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lens  # noqa: E402
import lens_route_policy as policy  # noqa: E402
import loop  # noqa: E402
import review  # noqa: E402
import taskplane_lite as tp  # noqa: E402


CATALOG_IDS = [row["id"] for row in lens.load_catalog()["lenses"]]


def _route(tmp_path, stage, evidence, *, mandatory_lenses=None):
    workspace = tmp_path / "repo"
    workspace.mkdir(exist_ok=True)
    return loop._focused_stage_route(
        str(workspace), stage=stage, target="R-0001", evidence=evidence,
        mandatory_lenses=mandatory_lenses)


def _assert_quick_complete(decision, projected):
    assert [row["lens"] for row in decision["dispositions"]] == CATALOG_IDS
    assert len(projected["lenses"]) == 26
    dispatch = [row for row in projected["lenses"] if row["mode"] != "none"]
    assert {row["id"] for row in dispatch} == \
        set(decision["dispatchable_selected"])
    assert dispatch
    assert {row["tier"] for row in dispatch} == {"sweep"}
    assert {row["mode"] for row in dispatch} == {"subagent"}
    assert projected["context"]["execution_mode"] == "quick-only"


def test_product_route_is_deterministic_complete_and_risk_sensitive(tmp_path):
    evidence = {
        "goal": "Make account onboarding clearer",
        "requirement": {"title": "Account onboarding"},
        "acceptance": ["A new user completes onboarding"],
        "domain": ["accounts"],
        "constraints": ["preserve the existing flow"],
        "product_risk": ["adoption"],
        "files": [],
    }
    first, first_projected, first_request = _route(
        tmp_path, "product", evidence)
    second, second_projected, second_request = _route(
        tmp_path, "product", copy.deepcopy(evidence))

    assert first == second
    assert first_projected == second_projected
    assert first_request is second_request is None
    assert "product" in first["selected"]
    _assert_quick_complete(first, first_projected)

    security_evidence = copy.deepcopy(evidence)
    security_evidence["product_risk"] = [
        "account takeover at the authentication trust boundary"]
    changed, changed_projected, _ = _route(
        tmp_path, "product", security_evidence)
    assert changed["route_fingerprint"] != first["route_fingerprint"]
    assert "security" in changed["selected"]
    _assert_quick_complete(changed, changed_projected)


@pytest.mark.parametrize("risk,lens_id", [
    ("A new external API interface and integration contract", "integrability"),
    ("A credential trust boundary and authorization handoff", "security"),
    ("A rollback and crash recovery path", "sre"),
])
def test_design_route_covers_solution_design_and_independent_risk_mutations(
        tmp_path, risk, lens_id):
    baseline, _, _ = _route(tmp_path, "design", {
        "approved_requirement": "Change the routing policy",
        "acceptance": ["The route is deterministic"],
        "proposed_solution": "One local pure adapter",
        "files": ["taskplane/loop.py"],
    })
    changed, projected, _ = _route(tmp_path, "design", {
        "approved_requirement": "Change the routing policy",
        "acceptance": ["The route is deterministic"],
        "proposed_solution": "One local pure adapter",
        "design_risk": risk,
        "files": ["taskplane/loop.py"],
    })

    assert "solution-design" in changed["selected"]
    assert lens_id in changed["selected"]
    assert changed["route_fingerprint"] != baseline["route_fingerprint"]
    _assert_quick_complete(changed, projected)


@pytest.mark.parametrize("count", [3, 4])
def test_nontrivial_plan_accepts_exactly_three_or_four_with_ac_coverage(
        tmp_path, count):
    lenses = ["architecture", "project-management", "testability",
              "security"][:count]
    decision, projected, request = _route(tmp_path, "plan", {
        "approved_product": "R-0001",
        "approved_design": "focused routing",
        "dependency_graph": {"edges": 3},
        "task_scopes": {"LR-06": ["taskplane/loop.py"]},
        "ownership": {"LR-06": "stage adapters"},
        "selectors": {"LR-06": "pytest focused"},
        "validation_strategy": "targeted then affected radius",
        "task_to_ac_coverage": {"LR-06": ["AC-LR1", "AC-LR3"]},
        "files": ["taskplane/loop.py"],
    }, mandatory_lenses=lenses)

    assert decision["status"] == "ready"
    assert len(decision["selected"]) == count
    assert request is None
    assert projected["context"]["task_to_ac_coverage"] == {
        "LR-06": ["AC-LR1", "AC-LR3"]}
    _assert_quick_complete(decision, projected)


def test_nontrivial_plan_refuses_two_lenses(tmp_path):
    with pytest.raises(policy.LensRoutePolicyError, match="at least 3"):
        _route(tmp_path, "plan", {
            "approved_product": "R-0001",
            "approved_design": "focused routing",
            "task_to_ac_coverage": {"LR-06": ["AC-LR3"]},
            "files": [],
        }, mandatory_lenses=["architecture", "testability"])


def test_five_plan_risks_refuse_dispatch_and_emit_closed_authority_request(
        tmp_path):
    mandatory = ["architecture", "project-management", "testability",
                 "security", "cost-finops"]
    decision, projected, request = _route(tmp_path, "plan", {
        "approved_product": "R-0001",
        "approved_design": "focused routing",
        "task_to_ac_coverage": {"LR-06": ["AC-LR5"]},
        "files": [],
    }, mandatory_lenses=mandatory)

    assert decision["status"] == "expanded_approval_required"
    assert decision["dispatchable_selected"] == []
    assert not [row for row in projected["lenses"] if row["mode"] != "none"]
    assert request["stage"] == "plan"
    assert request["exact_ordered_lens_ids"] == \
        decision["overflow"]["additional_lenses"]
    assert request["estimated_cost"] > 0
    assert tp.expanded_lens_route_provider_request_fingerprint(request)

    tampered = dict(request, estimated_cost=request["estimated_cost"] + 1)
    # A changed request is a different exact authority subject, never a way
    # to reuse approval for the original route.
    assert tp.expanded_lens_route_provider_request_fingerprint(tampered) != \
        tp.expanded_lens_route_provider_request_fingerprint(request)


def test_authenticated_provider_receipt_unblocks_only_its_exact_plan_route(
        tmp_path, monkeypatch):
    mandatory = ["architecture", "project-management", "testability",
                 "security", "cost-finops"]
    evidence = {
        "approved_product": "R-0001",
        "approved_design": "focused routing",
        "task_to_ac_coverage": {"LR-06": ["AC-LR5"]},
        "files": [],
    }
    refused, _, expected_request = _route(
        tmp_path, "plan", evidence, mandatory_lenses=mandatory)
    calls = []

    class ProviderReceipt(dict):
        pass

    class ProviderClient:
        def assert_authenticated(self, receipt, request):
            if request != expected_request:
                raise review.terminal_truth_runtime.TerminalTruthError(
                    "provider-authentication", "request binding changed")
            calls.append((receipt, request))

    monkeypatch.setattr(
        review.terminal_truth_runtime, "ExpandedRouteProviderClient",
        ProviderClient)
    monkeypatch.setattr(
        review.terminal_truth_runtime, "ExpandedRouteProviderReceipt",
        ProviderReceipt)
    client = ProviderClient()
    receipt = ProviderReceipt({
        "provider_protocol_version": "provider/v1",
        "action_fingerprint": "a" * 64,
    })
    workspace = tmp_path / "repo"
    approved, projected, request = loop._focused_stage_route(
        str(workspace), stage="plan", target="R-0001", evidence=evidence,
        mandatory_lenses=mandatory,
        expanded_route_provider_client=client,
        expanded_route_provider_receipt=receipt)

    assert calls == [(receipt, expected_request)]
    assert request == expected_request
    assert approved["status"] == "ready"
    assert approved["dispatchable_selected"] == approved["selected"]
    assert len(approved["selected"]) == 5
    authority = approved["expanded_route_authority"]
    assert authority["requested_route_fingerprint"] == \
        refused["route_fingerprint"]
    assert authority["request_fingerprint"] == \
        tp.expanded_lens_route_provider_request_fingerprint(expected_request)
    _assert_quick_complete(approved, projected)

    with pytest.raises(
            policy.LensRoutePolicyError, match="live expanded-route"):
        loop._focused_stage_route(
            str(workspace), stage="plan", target="R-0001", evidence=evidence,
            mandatory_lenses=mandatory,
            expanded_route_provider_client=client,
            expanded_route_provider_receipt=dict(receipt))

    changed = copy.deepcopy(evidence)
    changed["validation_strategy"] = "different exact route evidence"
    with pytest.raises(
            policy.LensRoutePolicyError, match="not authenticated"):
        loop._focused_stage_route(
            str(workspace), stage="plan", target="R-0001", evidence=changed,
            mandatory_lenses=mandatory,
            expanded_route_provider_client=client,
            expanded_route_provider_receipt=receipt)


def test_review_kernel_evaluate_bypasses_router_provider_and_lens_leases(
        tmp_path, monkeypatch):
    workspace = tmp_path / "evaluate-repo"
    source = workspace / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("def changed():\n    return 2\n", encoding="utf-8")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Evaluate must not route or consult authority")

    monkeypatch.setattr(review, "_focused_evaluate_route", forbidden)
    monkeypatch.setattr(review, "apply_expanded_route_authority", forbidden)
    started = review.start_review(
        str(workspace),
        target={"fingerprint": "a" * 64, "head": "abc123"},
        graph={"meta": {"scanned_head": "abc123",
                        "content_fingerprint": "graph-v1"},
               "modules": {"src": {"files": ["src/service.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["src/service.py"], "changed_symbols": ["changed"]},
        runnability={"summary": "available"},
        requirement={"id": "R-0001", "text": "focused review"},
        acceptance=["the approved expanded route executes"],
        contracts=["contract:authority.expanded-lens-route"], stage="build",
        task_type="reliability", router=forbidden,
        routing_content={"src/service.py": source.read_text(encoding="utf-8")},
        design_contract={
            "schema": "taskplane.design/v1",
            "stage_policy": {"evaluate": {"selection": "focused"}}})

    assert started["status"] == "ready"
    assert started["slots"] == []
    assert started["expected_lenses"] == []
    assert started["lens_execution_policy"] == "none"
    assert "focused_route" not in started
    assert "routing_decision" not in started


def test_build_and_fix_stage_adapter_is_closed(tmp_path):
    for stage in ("build", "fix"):
        with pytest.raises(policy.LensRoutePolicyError, match="routed stage"):
            _route(tmp_path, stage, {"files": []})
