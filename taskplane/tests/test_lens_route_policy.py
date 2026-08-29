"""Closed-contract tests for focused-routing/v1 (R-0001, LR-01)."""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graph_primitives  # noqa: E402
import lens  # noqa: E402
import lens_route_policy as policy  # noqa: E402
import lens_signals  # noqa: E402


CATALOG = lens.load_catalog()
CATALOG_IDS = [row["id"] for row in CATALOG["lenses"]]


def context(stage="product", **updates):
    value = {
        "schema": policy.CONTEXT_SCHEMA,
        "stage": stage,
        "target": "R-0001",
        "policy_version": policy.POLICY_VERSION,
        "catalog_fingerprint": policy.catalog_fingerprint(CATALOG["lenses"]),
        "requirement_fingerprint": "a" * 64,
        "acceptance_fingerprints": ["b" * 64],
        "constraint_fingerprints": ["c" * 64],
        "evidence": {"risk": "d" * 64},
    }
    value.update(updates)
    return value


def rows(positive=("product", "security"), *, duplicate=False):
    out = []
    for index, lens_id in enumerate(CATALOG_IDS):
        hit = lens_id in positive
        out.append({
            "id": lens_id,
            "score": 0.9 - index / 1000 if hit else 0.0,
            "verdict": "deep" if hit else "n/a",
            "evidence": [f"signal:{lens_id}"] if hit else [],
            "negative_evidence": [] if hit else [f"absent:{lens_id}"],
            "risk_group": "authority" if duplicate and hit else lens_id,
            "mandatory": lens_id == "security" and hit,
        })
    return out


def build(stage="product", positive=("product", "security"), **context_updates):
    return policy.build_route(
        context(stage, **context_updates), rows(positive), CATALOG["lenses"])


def test_complete_catalog_disposition_and_selected_conservation():
    route = build()
    assert route["schema"] == policy.DECISION_SCHEMA
    assert [row["lens"] for row in route["dispositions"]] == CATALOG_IDS
    assert route["selected"] == ["product", "security"]
    assert {row["disposition"] for row in route["dispositions"]} <= {
        "execute_deep", "execute_light", "covered_by", "not_applicable"
    }
    policy.validate_route(route, CATALOG["lenses"])


@pytest.mark.parametrize("mutation,match", [
    (lambda route: route["dispositions"].pop(), "catalog order"),
    (lambda route: route["dispositions"].append(copy.deepcopy(
        route["dispositions"][0])), "catalog order"),
    (lambda route: route["dispositions"][0].update(lens="unknown"),
     "catalog order"),
    (lambda route: route["dispositions"][0].update(disposition="maybe"),
     "unsupported disposition"),
    (lambda route: route["dispositions"][0].update(evidence=[]),
     "evidence"),
    (lambda route: route["dispositions"][0].update(reason=""),
     "reason"),
    (lambda route: route.update(selected=["security"]),
     "selected set"),
])
def test_closed_ledger_mutations_fail_closed(mutation, match):
    route = build()
    mutation(route)
    with pytest.raises(policy.LensRoutePolicyError, match=match):
        policy.validate_route(route, CATALOG["lenses"])


def test_covered_by_requires_selected_target_and_rejects_cycles():
    route = policy.build_route(
        context(), rows(("product", "security"), duplicate=True),
        CATALOG["lenses"])
    covered = next(row for row in route["dispositions"]
                   if row["disposition"] == "covered_by")
    assert covered["covered_by"] in route["selected"]

    selected = next(row for row in route["dispositions"]
                    if row["lens"] == covered["covered_by"])
    selected["disposition"] = "covered_by"
    selected["covered_by"] = covered["lens"]
    route["selected"] = []
    with pytest.raises(policy.LensRoutePolicyError, match="cycle|selected"):
        policy.validate_route(route, CATALOG["lenses"])


def test_not_applicable_requires_machine_readable_negative_evidence():
    bad = rows()
    next(row for row in bad if row["id"] == "i18n")["negative_evidence"] = []
    with pytest.raises(policy.LensRoutePolicyError,
                       match="negative evidence"):
        policy.build_route(context(), bad, CATALOG["lenses"])


def test_plan_and_evaluate_enforce_three_or_four_nontrivial_lenses():
    selected = ("security", "testability", "architecture")
    assert len(build("plan", selected)["selected"]) == 3
    assert len(build("evaluate", selected + ("cost-finops",))["selected"]) == 4
    with pytest.raises(policy.LensRoutePolicyError, match="at least 3"):
        build("plan", selected[:2])


def test_more_than_four_mandatory_risks_returns_non_dispatchable_overflow():
    mandatory = ("security", "testability", "data-safety", "architecture",
                 "privacy-compliance")
    signal_rows = rows(mandatory)
    for row in signal_rows:
        row["mandatory"] = row["id"] in mandatory
    route = policy.build_route(
        context("evaluate"), signal_rows, CATALOG["lenses"])
    assert route["status"] == "expanded_approval_required"
    assert route["dispatchable_selected"] == []
    assert route["overflow"]["mandatory_lenses"] == list(mandatory)


def test_design_applies_solution_design_floor_before_deduplication():
    signal_rows = rows(("architecture", "solution-design"), duplicate=True)
    route = policy.build_route(
        context("design"), signal_rows, CATALOG["lenses"])
    assert "solution-design" in route["selected"]


def test_equivalent_mapping_key_order_is_byte_identical():
    a = build(evidence={"risk": "d" * 64, "graph": {"a": 1, "b": 2}})
    b = build(evidence={"graph": {"b": 2, "a": 1}, "risk": "d" * 64})
    assert policy.canonical_bytes(a) == policy.canonical_bytes(b)
    assert a["route_fingerprint"] == b["route_fingerprint"]
    # Canonical JSON sorts mapping keys. Mapping insertion order is not part
    # of the trust-boundary contract after persistence and reload.
    policy.validate_route(
        json.loads(policy.canonical_bytes(a)), CATALOG["lenses"])


def test_relevant_input_and_policy_version_change_fingerprints():
    a = build()
    b = build(evidence={"risk": "e" * 64})
    cctx = context(policy_version="focused-routing/v2")
    c = policy.build_route(cctx, rows(), CATALOG["lenses"],
                           policy_version="focused-routing/v2")
    assert a["route_fingerprint"] != b["route_fingerprint"]
    assert a["route_fingerprint"] != c["route_fingerprint"]


def test_catalog_definition_change_invalidates_catalog_fingerprint():
    mutated = copy.deepcopy(CATALOG["lenses"])
    mutated[0]["charter"] += " changed"
    assert policy.catalog_fingerprint(CATALOG["lenses"]) != \
        policy.catalog_fingerprint(mutated)


def test_per_lens_fingerprint_can_be_scoped_to_relevant_inputs():
    signal_rows = rows()
    for row in signal_rows:
        row["fingerprint_inputs"] = {"lens": row["id"], "value": 1}
    a = policy.build_route(context(evidence={"unrelated": 1}), signal_rows,
                           CATALOG["lenses"])
    b = policy.build_route(context(evidence={"unrelated": 2}), signal_rows,
                           CATALOG["lenses"])
    assert a["route_fingerprint"] != b["route_fingerprint"]
    assert a["lens_input_fingerprints"] == b["lens_input_fingerprints"]


def test_graph_signal_projection_and_lens_adapter_are_dependency_neutral():
    verdict_map = {}
    for lens_id in CATALOG_IDS:
        verdict_map[lens_id] = {
            "verdict": "light" if lens_id == "architecture" else "n/a",
            "score": 0.4 if lens_id == "architecture" else 0.0,
            "evidence": ["graph:hub"] if lens_id == "architecture" else [],
            "negative_evidence": ([] if lens_id == "architecture" else
                                  [f"absent:{lens_id}"]),
        }
    projected = graph_primitives.focused_signal_rows(verdict_map, CATALOG_IDS)
    assert lens_signals.focused_signal_rows is graph_primitives.focused_signal_rows
    route = lens.focused_route(context(), verdict_map, catalog=CATALOG)
    assert route["selected"] == ["architecture"]
    assert projected[CATALOG_IDS.index("architecture")]["id"] == "architecture"


def test_canonicalizer_rejects_non_finite_and_non_json_values():
    with pytest.raises(policy.LensRoutePolicyError, match="finite"):
        policy.canonical_bytes({"bad": float("nan")})
    with pytest.raises(policy.LensRoutePolicyError, match="JSON"):
        policy.canonical_bytes({"bad": object()})


def test_signal_boolean_fields_are_closed_not_truthy_coerced():
    bad = rows()
    bad[0]["mandatory"] = "false"
    with pytest.raises(policy.LensRoutePolicyError, match="mandatory"):
        policy.build_route(context(), bad, CATALOG["lenses"])
