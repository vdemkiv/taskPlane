"""R-0004 non-build stage handoffs remain bounded and explicit."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLOW_PATHS = {
    name: ROOT / "skills" / name / "flow.json"
    for name in ("taskplane", "tp-product", "tp-design")
}
SKILL_PATHS = {
    name: ROOT / "skills" / name / "SKILL.md"
    for name in ("taskplane", "tp-product", "tp-design")
}
STAGE_RUNTIME = {
    "dispatch_schema": "taskplane.stage-dispatch/v1",
    "startup_schema": "taskplane.stage-startup/v1",
    "handoff_schema": "taskplane.stage-handoff/v1",
    "bounded_inputs": [
        "authority", "input_handoff", "selected_artifacts", "budget",
        "declared_scope", "execution_claim",
    ],
    "forbidden_inheritance": [
        "agents", "conversations", "event_logs", "tool_transcripts",
        "leases", "runtime_state", "predecessor_roots",
    ],
    "non_build_terminal_outcomes": ["closed", "discarded"],
    "implicit_build": False,
    "rollout_modes": ["new-run", "enabled"],
    "rollback": "retain-v4-read-only-no-reverse-migration",
}


def _flow(name: str) -> dict[str, object]:
    return json.loads(FLOW_PATHS[name].read_text(encoding="utf-8"))


def test_facade_product_and_design_offer_explicit_non_build_terminal_path() \
        -> None:
    flow = _flow("taskplane")
    nodes = {row["id"]: row for row in flow["nodes"]}
    edges = {tuple(row) for row in flow["edges"]}

    assert nodes["terminal_handoff"] == {
        "id": "terminal_handoff",
        "label": "done / closed / discarded (no implicit Build)",
        "kind": "stage",
    }
    assert {("product", "terminal_handoff"),
            ("design", "terminal_handoff")} <= edges
    assert ("terminal_handoff", "build") not in edges


def test_facade_product_and_design_share_the_canonical_stage_runtime() -> None:
    for name in FLOW_PATHS:
        assert _flow(name)["stage_runtime"] == STAGE_RUNTIME


def test_product_and_design_keep_build_and_non_build_outcomes_distinct() \
        -> None:
    product = _flow("tp-product")
    product_edges = {tuple(row) for row in product["edges"]}
    product_nodes = {row["id"]: row for row in product["nodes"]}
    assert product_nodes["terminal_handoff"]["label"] == \
        "done / closed / discarded (no implicit Build)"
    assert ("approval", "build") in product_edges
    assert ("approval", "terminal_handoff") in product_edges
    assert ("terminal_handoff", "build") not in product_edges

    design = _flow("tp-design")
    design_edges = {tuple(row) for row in design["edges"]}
    design_nodes = {row["id"]: row for row in design["nodes"]}
    assert design_nodes["terminal_handoff"]["label"] == \
        "done / closed / discarded (no implicit Build)"
    assert ("approval", "terminal_handoff") in design_edges
    assert ("terminal_handoff", "build") not in design_edges


def test_non_build_skill_contracts_are_bounded_auditable_and_reusable_only_by_new_authority() \
        -> None:
    required = (
        "taskplane.stage-handoff/v1",
        "versioned bounded manifest",
        "content-addressed artifacts",
        "stage authority, budget, and scope",
        "predecessor agents",
        "conversations",
        "event logs",
        "tool transcripts",
        "leases",
        "runtime roots",
        "`done`",
        "`closed`",
        "`discarded`",
        "no implicit Build",
        "explicit new authority",
        "audit",
    )
    for name, path in SKILL_PATHS.items():
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in required:
            assert phrase in text, f"{name} omits {phrase!r}"
