from __future__ import annotations

from pathlib import Path
import json
import re

import pytest

from taskplane.delivery_policy import ROUTED_LENS_STAGES, ZERO_LENS_STAGES


ROOT = Path(__file__).resolve().parents[2]


def _raw(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _section_from_text(raw: str, heading: str) -> str:
    heading_pattern = re.compile(
        rf"^(?P<marks>#{{1,6}})\s+{re.escape(heading)}\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(heading_pattern.finditer(raw))
    assert len(matches) == 1, f"expected exactly one section: {heading}"
    match = matches[0]
    level = len(match.group("marks"))
    next_heading = re.compile(
        rf"^#{{1,{level}}}\s+.+$", re.MULTILINE
    ).search(raw, match.end())
    end = next_heading.start() if next_heading else len(raw)
    return raw[match.end():end]


def _section(relative: str, heading: str) -> str:
    return _section_from_text(_raw(relative), heading)


def _assert_section_contract(
    relative: str, heading: str, *clauses: str
) -> None:
    text = _normalized(_section(relative, heading))
    for clause in clauses:
        assert _normalized(clause) in text, (
            f"{relative}#{heading} is missing contract clause: {clause}"
        )


def _assert_owned_clause(raw: str, heading: str, clause: str) -> None:
    normalized_clause = _normalized(clause)
    section = _normalized(_section_from_text(raw, heading))
    document = _normalized(raw)
    assert section.count(normalized_clause) == 1, (
        f"{heading} must own exactly one contract clause: {clause}"
    )
    assert document.count(normalized_clause) == 1, (
        f"contract clause must not be duplicated or misplaced: {clause}"
    )


def test_stage_agent_contracts_define_focused_routes() -> None:
    _assert_section_contract(
        "agents/tp-product.md",
        "Focused routing contract",
        "minimum-sufficient focused route",
        "goal, requirement, acceptance criteria, domain, declared constraints, and product-risk evidence",
        "all 26 dispositions",
        "never launch a normal full-catalog run",
    )
    _assert_section_contract(
        "agents/tp-designer.md",
        "Focused routing contract",
        "minimum-sufficient focused route",
        "approved requirement and proposed solution evidence",
        "solution-design coverage",
        "all 26 dispositions",
    )
    _assert_section_contract(
        "agents/tp-planner.md",
        "Focused routing contract",
        "exactly three or four quick lenses",
        "task-to-acceptance-criterion coverage",
        "all 26 dispositions",
        "split the scope",
        "authenticated expanded-route approval",
    )
    _assert_section_contract(
        "agents/tp-evaluator.md",
        "Focused routing contract",
        "exactly three or four quick lenses",
        "actual diff, changed files, dependency impact, test evidence, and unresolved findings",
        "all 26 dispositions",
        "authenticated expanded-route approval",
        "fingerprint inputs changed",
    )


def test_build_and_fix_agents_never_launch_lens_workers() -> None:
    terminal_paths = "success, failure, cancellation, interruption, and handoff"
    for relative, heading in (
        ("agents/tp-executor.md", "Zero-lens Build invariant"),
        ("agents/tp-fixer.md", "Zero-lens Fix invariant"),
    ):
        _assert_section_contract(
            relative, heading,
            "zero lens workers",
            terminal_paths,
            "product, design, plan, and evaluate",
        )
    assert "primed lenses" not in _normalized(
        _section("agents/tp-executor.md", "Zero-lens Build invariant")
    )
    _assert_section_contract(
        "agents/tp-lens.md",
        "Focused-stage boundary",
        "quick lens worker",
        "product, design, plan, or evaluate",
        "refuse any build or fix brief",
    )
    _assert_section_contract(
        "agents/tp-orchestrator.md",
        "Focused routing invariant",
        "plan and evaluate dispatch exactly three or four quick lens workers",
        "build and fix dispatch zero",
        "all 26 dispositions",
    )


def test_skill_contracts_carry_the_same_stage_policy() -> None:
    for relative in (
        "skills/taskplane/SKILL.md",
        "skills/taskplane/references/harness-rules.md",
        "skills/tp-go/SKILL.md",
        "skills/tp-build/SKILL.md",
    ):
        _assert_section_contract(
            relative, "Focused routing invariant",
            "product/design minimum-sufficient focused routes",
            "plan/evaluate exactly three or four quick lenses",
            "build/fix zero lens workers",
            "all 26 dispositions",
        )

    _assert_section_contract(
        "skills/tp-product/SKILL.md",
        "Focused routing contract",
        "minimum-sufficient focused route",
        "all 26 dispositions",
        "normal full-catalog run",
    )
    _assert_section_contract(
        "skills/tp-design/SKILL.md",
        "Focused routing contract",
        "minimum-sufficient focused route",
        "solution-design coverage",
        "all 26 dispositions",
    )
    _assert_section_contract(
        "skills/tp-engineering/SKILL.md",
        "Focused routing contract",
        "exactly three or four quick lenses",
        "all 26 dispositions",
        "fingerprint inputs changed",
    )


def test_governed_contract_records_overflow_reuse_and_zero_lens_terminal_paths() -> None:
    for relative in (
        "skills/taskplane/references/harness-rules.md",
        "skills/tp-go/SKILL.md",
        "skills/tp-build/SKILL.md",
    ):
        _assert_section_contract(
            relative,
            "Focused routing invariant",
            "authenticated expanded-route approval",
            "split the scope",
            "fingerprint inputs changed",
            "success, failure, cancellation, interruption, and handoff",
        )
    _assert_section_contract(
        "skills/tp-engineering/SKILL.md",
        "Focused routing contract",
        "authenticated expanded-route approval",
        "split the scope",
        "fingerprint inputs changed",
    )


def test_legacy_broad_review_routing_is_not_still_authoritative() -> None:
    authoritative_sections = (
        ("agents/tp-engineering.md", "Focused routing contract"),
        ("skills/tp-go/SKILL.md", "Focused routing invariant"),
        ("skills/tp-build/SKILL.md", "Focused routing invariant"),
        ("skills/tp-engineering/SKILL.md", "Stage-native review boundary"),
        (
            "skills/tp-engineering/references/em-session.md",
            "This is an interactive session, not a report",
        ),
        ("skills/tp-engineering/references/em-session.md", "Step 0 — Open once"),
        ("skills/tp-engineering/references/em-session.md", "Invocation"),
    )
    stale_clauses = (
        "cap-8 budget",
        "full-catalog audit sweep",
        "plus at most one light sweep",
        "deep slots plus the optional single light-sweep slot",
        "dispatch only mapped lenses",
        "bounded promoted deep wave",
        "dispatch a general subagent",
        "{id: deep|sweep}",
    )
    for relative, heading in authoritative_sections:
        governed = _normalized(_section(relative, heading))
        for stale in stale_clauses:
            assert stale not in governed, f"stale clause in {relative}#{heading}"
    _assert_section_contract(
        "agents/tp-engineering.md",
        "Focused routing contract",
        "exactly three or four quick lenses",
        "does not launch a second lens sweep",
        "all 26 dispositions",
    )


def test_skill_flow_graphs_expose_focused_stage_boundaries() -> None:
    def flow(relative: str) -> dict:
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))

    def labels(relative: str) -> str:
        payload = flow(relative)
        return " ".join(node["label"].lower() for node in payload["nodes"])

    assert "minimum-sufficient quick route + 26 dispositions" in labels(
        "skills/tp-product/flow.json"
    )
    design = labels("skills/tp-design/flow.json")
    assert "minimum-sufficient quick route" in design
    assert "solution-design" in design
    for relative in ("skills/tp-go/flow.json", "skills/tp-build/flow.json"):
        flow_labels = labels(relative)
        assert "plan · exactly 3–4 quick" in flow_labels
        assert "evaluate · exactly 3–4 quick" in flow_labels
        assert "zero lenses" in flow_labels
        assert "consume evaluate evidence" in flow_labels
    engineering = labels("skills/tp-engineering/flow.json")
    assert "consume 3–4 quick evaluate results" in engineering
    assert "deep" not in engineering
    engineering_flow = flow("skills/tp-engineering/flow.json")
    node_by_id = {node["id"]: node for node in engineering_flow["nodes"]}
    assert node_by_id["route"] == {
        "id": "route", "label": "26 lens dispositions", "kind": "stage"
    }
    assert node_by_id["wave"] == {
        "id": "wave",
        "label": "consume 3–4 quick Evaluate results",
        "kind": "stage",
    }
    assert ["route", "wave"] in engineering_flow["edges"]
    assert ["wave", "collect"] in engineering_flow["edges"]
    assert "evaluate" in ROUTED_LENS_STAGES
    assert "em" in ZERO_LENS_STAGES


def test_engineering_focused_clause_has_one_authoritative_owner() -> None:
    clause = (
        "Engineering consumes the sealed three-or-four quick\n"
        "Evaluate results "
        "and the complete all-26 disposition ledger; it never launches\n"
        "lens workers or a promotion wave."
    )
    _assert_owned_clause(
        _raw("skills/tp-engineering/SKILL.md"),
        "Stage-native review boundary",
        clause,
    )


def test_section_binding_rejects_removal_duplication_and_relocation() -> None:
    raw = _raw("skills/tp-engineering/SKILL.md")
    clause = (
        "Engineering consumes the sealed three-or-four quick\n"
        "Evaluate results "
        "and the complete all-26 disposition ledger; it never launches\n"
        "lens workers or a promotion wave."
    )
    assert raw.count(clause) == 1

    with_removed = raw.replace(clause, "Engineering consumes sealed evidence.")
    with pytest.raises(AssertionError):
        _assert_owned_clause(with_removed, "Stage-native review boundary", clause)

    with_duplicate = raw.replace(clause, f"{clause}\n\n{clause}")
    with pytest.raises(AssertionError):
        _assert_owned_clause(with_duplicate, "Stage-native review boundary", clause)

    without_owner = raw.replace(clause, "")
    relocated = without_owner.replace(
        "## Engineering actions (judgments, never code)",
        f"## Engineering actions (judgments, never code)\n\n{clause}",
    )
    with pytest.raises(AssertionError):
        _assert_owned_clause(relocated, "Stage-native review boundary", clause)
