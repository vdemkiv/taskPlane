from __future__ import annotations

from pathlib import Path
import json
import re
from typing import cast, TypedDict

import pytest


ROOT = Path(__file__).resolve().parents[2]


class _FlowNode(TypedDict):
    id: str
    label: str
    kind: str


class _SkillFlow(TypedDict):
    nodes: list[_FlowNode]
    edges: list[list[str]]


def _raw(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _frontmatter_from_text(raw: str) -> str:
    match = re.match(r"\A---\n(?P<body>.*?)\n---(?:\n|\Z)", raw, re.DOTALL)
    assert match is not None, "expected one leading YAML frontmatter block"
    return match.group("body")


def _frontmatter(relative: str) -> str:
    return _frontmatter_from_text(_raw(relative))


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


def _assert_frontmatter_contract(relative: str, *clauses: str) -> None:
    text = _normalized(_frontmatter(relative))
    for clause in clauses:
        assert _normalized(clause) in text, (
            f"{relative} frontmatter is missing contract clause: {clause}"
        )


def _assert_owned_frontmatter_clause(raw: str, clause: str) -> None:
    normalized_clause = _normalized(clause)
    frontmatter = _normalized(_frontmatter_from_text(raw))
    document = _normalized(raw)
    assert frontmatter.count(normalized_clause) == 1, (
        f"frontmatter must own exactly one contract clause: {clause}"
    )
    assert document.count(normalized_clause) == 1, (
        f"frontmatter clause must not be duplicated or misplaced: {clause}"
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
        "Zero-lens Evaluate invariant",
        "zero Taskplane lens workers",
        "direct evidence judgment only",
        "exact diff, bound tests, acceptance criteria, dependency graph and impact",
        "affected requirements and contracts, approved Design conformance, and provenance",
        "no lens route, lens slots, disposition ledger, lens verdicts, retry or invalidation, or expanded-route authority",
    )


def test_build_fix_evaluate_and_engineering_agents_never_launch_lens_workers() -> None:
    terminal_paths = "success, failure, cancellation, interruption, and handoff"
    for relative, heading in (
        ("agents/tp-executor.md", "Zero-lens Build invariant"),
        ("agents/tp-fixer.md", "Zero-lens Fix invariant"),
    ):
        _assert_section_contract(
            relative, heading,
            "zero lens workers",
            terminal_paths,
            "product, design, and plan",
        )
    assert "primed lenses" not in _normalized(
        _section("agents/tp-executor.md", "Zero-lens Build invariant")
    )
    _assert_section_contract(
        "agents/tp-lens.md",
        "Focused-stage boundary",
        "quick lens worker",
        "product, design, or plan",
        "refuse any build, fix, evaluate, or engineering brief",
    )
    _assert_frontmatter_contract(
        "agents/tp-lens.md",
        "governed read-only quick lens worker for Product, Design, or Plan",
        "one-per-selected execution disposition",
        "Build, Fix, Evaluate, and Engineering never dispatch it",
    )
    _assert_section_contract(
        "agents/tp-orchestrator.md",
        "Focused routing invariant",
        "Plan dispatches exactly three or four quick lens workers",
        "Product, Design, and Plan record all 26 dispositions",
        "Build, Fix, Evaluate, and Engineering dispatch zero",
        "Evaluate performs direct evidence judgment only",
    )
    _assert_section_contract(
        "agents/tp-engineering.md",
        "Focused routing contract",
        "Evaluate launches zero Taskplane lens workers",
        "direct evidence judgment",
        "Engineering launches zero lens workers",
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
            "Plan exactly three or four quick lenses",
            "Product, Design, and Plan record all 26 dispositions",
            "Build/Fix/Evaluate/EM zero lens workers",
            "Evaluate performs direct evidence judgment only",
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
        "Evaluate launches zero Taskplane lens workers",
        "direct evidence judgment",
        "Engineering launches zero lens workers",
    )
    _assert_frontmatter_contract(
        "skills/tp-engineering/SKILL.md",
        "sealed direct evidence",
        "launches no lens workers",
        "fresh zero-lens Evaluate judgment",
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
            "success, failure, cancellation, interruption, and handoff",
        )
    _assert_section_contract(
        "skills/tp-engineering/SKILL.md",
        "Focused routing contract",
        "no lens route, slots, ledger, lens verdicts, retry or invalidation, or expanded-route authority",
    )


def test_no_role_or_skill_reauthorizes_evaluate_lens_work() -> None:
    documents = list((ROOT / "agents").glob("*.md"))
    for directory in (
        "skills/taskplane", "skills/tp-product", "skills/tp-design",
        "skills/tp-go", "skills/tp-build", "skills/tp-engineering",
    ):
        documents.extend((ROOT / directory).rglob("*.md"))

    stale_claims = (
        "plan/evaluate exactly",
        "plan and evaluate dispatch",
        "product, design, plan, and evaluate",
        "product, design, plan, or evaluate",
        "sealed three-or-four quick",
        "all-26 disposition ledger",
        "newly authorized focused evaluate route",
        "post-fix evaluate",
        "evaluate records exactly one evidenced row for all 26",
        "evaluate executes exactly three or four",
        "evaluate runs exactly three or four",
        "evaluate dispatches exactly three or four",
        "evaluate's exactly three or four",
        "consume 3–4 quick evaluate results",
    )
    for path in documents:
        governed = _normalized(path.read_text(encoding="utf-8"))
        for stale in stale_claims:
            assert stale not in governed, f"stale Evaluate claim in {path}: {stale}"


def test_skill_flow_graphs_expose_focused_stage_boundaries() -> None:
    def flow(relative: str) -> _SkillFlow:
        payload: object = json.loads(
            (ROOT / relative).read_text(encoding="utf-8")
        )
        assert isinstance(payload, dict)
        return cast(_SkillFlow, payload)

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
        assert "evaluate · direct evidence · zero lenses" in flow_labels
        assert "zero lenses" in flow_labels
        assert "consume direct evaluate evidence" in flow_labels
    engineering = labels("skills/tp-engineering/flow.json")
    assert "sealed direct evaluate evidence" in engineering
    assert "zero-lens engineering judgment" in engineering
    assert "deep" not in engineering
    engineering_flow = flow("skills/tp-engineering/flow.json")
    node_by_id = {node["id"]: node for node in engineering_flow["nodes"]}
    assert node_by_id["route"] == {
        "id": "route", "label": "sealed direct Evaluate evidence", "kind": "stage"
    }
    assert node_by_id["wave"] == {
        "id": "wave",
        "label": "zero-lens Engineering judgment",
        "kind": "stage",
    }
    assert ["route", "wave"] in engineering_flow["edges"]
    assert ["wave", "collect"] in engineering_flow["edges"]


def test_engineering_frontmatter_has_one_authoritative_zero_lens_clause() -> None:
    clause = (
        "Engineering consumes Evaluate's sealed direct evidence, launches no "
        "lens workers, and returns missing or insufficient substantive "
        "evidence to a fresh zero-lens Evaluate judgment."
    )
    _assert_owned_frontmatter_clause(
        _raw("skills/tp-engineering/SKILL.md"), clause
    )


def test_frontmatter_binding_rejects_removal_duplication_and_relocation() -> None:
    raw = _raw("skills/tp-engineering/SKILL.md")
    clause = (
        "Engineering consumes Evaluate's sealed direct evidence, launches no "
        "lens workers, and returns missing or insufficient substantive "
        "evidence to a fresh zero-lens Evaluate judgment."
    )
    assert raw.count(clause) == 1

    with_removed = raw.replace(clause, "Engineering consumes sealed evidence.")
    with pytest.raises(AssertionError):
        _assert_owned_frontmatter_clause(with_removed, clause)

    with_duplicate = raw.replace(clause, f"{clause} {clause}")
    with pytest.raises(AssertionError):
        _assert_owned_frontmatter_clause(with_duplicate, clause)

    without_owner = raw.replace(clause, "")
    relocated = without_owner.replace(
        "# /tp-engineering",
        f"{clause}\n\n# /tp-engineering",
    )
    with pytest.raises(AssertionError):
        _assert_owned_frontmatter_clause(relocated, clause)


def test_engineering_zero_lens_clause_has_one_authoritative_owner() -> None:
    clause = (
        "Engineering consumes the sealed direct Evaluate evidence; it never "
        "launches\nlens workers or a promotion wave."
    )
    _assert_owned_clause(
        _raw("skills/tp-engineering/SKILL.md"),
        "Stage-native review boundary",
        clause,
    )


def test_section_binding_rejects_removal_duplication_and_relocation() -> None:
    raw = _raw("skills/tp-engineering/SKILL.md")
    clause = (
        "Engineering consumes the sealed direct Evaluate evidence; it never "
        "launches\nlens workers or a promotion wave."
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
