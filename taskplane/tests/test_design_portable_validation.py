"""The repository adapter and loop share the existing substantive Design gate."""
from __future__ import annotations

import copy
import json

import pytest

from taskplane import design_contract as dc


@pytest.fixture
def design_inputs(tmp_path):
    requirement = {
        "id": "R-TEST", "acceptance": ["approved before Build"],
        "contracts": [{"id": "contract:design", "relation": "provides"}],
    }
    graph = {"meta": {"content_fingerprint": "a" * 64},
             "modules": {"core": {}}}
    contract = {
        "schema": "taskplane.design/v1", "requirement": requirement["id"],
        "title": "Shared Design validation", "summary": "Reuse the Design gate.",
        "decision": "Keep one validator with explicit inputs.",
        "current_state": {"summary": "Validation currently loads loop state.",
                          "sources": ["taskplane/design_contract.py"]},
        "alternatives": [
            {"id": name, "name": name, "description": description,
             "tradeoffs": {"gains": [gain], "costs": [cost],
                           "revisit_when": "The existing contract changes."}}
            for name, description, gain, cost in (
                ("shared", "Extract the existing validator.", "One policy", "Adapter inputs"),
                ("duplicate", "Copy the existing checks.", "No extraction", "Policy drift"))],
        "selected_approach": "shared",
        "modules": {"existing": ["core"], "new": []},
        "contracts": [{"relation": "provides", "id": "contract:design",
                       "description": "One substantive Design validation boundary."}],
        "graph": {
            "baseline_fingerprint": graph["meta"]["content_fingerprint"],
            "proposed_modules": ["core"],
            "proposed_edges": [{"from": "core", "to": "contract:design",
                                "kind": "provides", "reason": "Owns validation"}],
            "depth_policy": {"local_depth": 1, "boundary_mode": "contract-only",
                             "contract_depth": 1, "requirement_depth": 0},
            "dor": [{"check": "Baseline is current", "evidence": "Pinned graph"}],
            "dod": [{"check": "Graph remains an overlay", "evidence": "Graph comparison"}],
        },
        "acceptance_map": [{"criterion": "approved before Build",
                            "design_element": "Shared gate", "validation": "Parity regression",
                            "tests": ["taskplane/tests/test_design_portable_validation.py::"
                                      "test_three_field_design_is_not_substantive_evidence"]}],
        "risks": [{"risk": "Policy drift", "mitigation": "One shared body", "owner": "engine"}],
        "failure_modes": [{"mode": "Incomplete artifact", "detection": "Existing gate",
                           "recovery": "Return exact missing evidence"}],
        "observability": {"signals": ["Validation errors"],
                          "alerts_none_rationale": "Synchronous validation reports its refusal."},
        "rollout": {"strategy": "Wire both callers", "rollback": "Keep the old wrapper"},
        "visualization": {"required": False, "reason": "One shared function needs no diagram."},
        "lens_evidence": [{"lens": "solution-design", "verdict": "pass", "blockers": 0,
                           "evidence": "Isolated validator fixture, not host review evidence.",
                           "produced_by": "fixture", "self_attested": True}],
        "open_questions": [],
    }
    (tmp_path / "design").mkdir()
    (tmp_path / dc.DESIGN_NARRATIVE).write_text(
        "# Shared validation\nUse the same explicit checks.\n", encoding="utf-8")
    contract["lens_evidence"][0]["content_fingerprint"] = \
        dc.design_content_fingerprint(str(tmp_path), contract)
    return str(tmp_path), contract, requirement, graph


def _portable(inputs, *, contract=None, baseline=None, current=None):
    workspace, original, requirement, graph = inputs
    return dc.design_artifact_errors(
        workspace, original if contract is None else contract,
        requirement=requirement,
        baseline_graph=graph if baseline is None else baseline,
        current_graph=graph if current is None else current)


def test_three_field_design_is_not_substantive_evidence(design_inputs):
    _, _, requirement, _ = design_inputs
    incomplete = {"schema": "taskplane.design/v1", "requirement": requirement["id"],
                  "summary": "A header does not prove the Design."}
    errors = _portable(design_inputs, contract=incomplete)
    assert "design must compare at least two approaches" in errors
    assert "design graph has no proposed_modules" in errors
    assert "solution-design lens must pass with evidence and no blockers" in errors
    assert any("acceptance criterion lacks one complete design mapping" in row for row in errors)


@pytest.mark.parametrize("case", ["valid", "incomplete", "wrong-requirement",
                                 "missing-baseline", "changed-graph", "missing-visual",
                                 "stale-lens", "missing-narrative"])
def test_portable_and_existing_loop_wrapper_have_identical_errors(
        design_inputs, monkeypatch, case):
    workspace, contract, requirement, graph = design_inputs
    baseline = copy.deepcopy(graph)
    current = copy.deepcopy(graph)
    if case == "incomplete":
        contract = {"schema": "taskplane.design/v1", "requirement": requirement["id"],
                    "summary": "Incomplete fixture"}
    elif case == "wrong-requirement":
        contract["requirement"] = "R-FOREIGN"
    elif case == "missing-baseline":
        baseline["meta"] = {}
    elif case == "changed-graph":
        current["meta"]["content_fingerprint"] = "b" * 64
    elif case == "missing-visual":
        contract["visualization"] = {"required": True, "kind": "sequence",
                                     "path": "design/visual.html"}
    elif case == "stale-lens":
        contract["lens_evidence"][0]["content_fingerprint"] = "b" * 64
    elif case == "missing-narrative":
        from pathlib import Path
        Path(workspace, dc.DESIGN_NARRATIVE).unlink()
    from pathlib import Path
    Path(workspace, dc.DESIGN_CONTRACT).write_text(json.dumps(contract), encoding="utf-8")
    state = {"requirement_id": requirement["id"],
             "design_graph_fingerprint": baseline["meta"].get("content_fingerprint")}
    monkeypatch.setattr(dc.reqs, "get_requirement", lambda *_: copy.deepcopy(requirement))
    monkeypatch.setattr(dc.depgraph, "load", lambda *_: copy.deepcopy(current))
    existing = dc.design_dod_errors(workspace, state)
    assert bool(existing) is (case != "valid"), existing
    inputs_before = copy.deepcopy((contract, requirement, baseline, current))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("portable Design must not load predecessor requirement/graph state")

    monkeypatch.setattr(dc.reqs, "get_requirement", forbidden)
    monkeypatch.setattr(dc.depgraph, "load", forbidden)
    portable = _portable(design_inputs, contract=contract, baseline=baseline, current=current)
    assert portable == existing
    assert (contract, requirement, baseline, current) == inputs_before
    assert bool(portable) is (case != "valid")
