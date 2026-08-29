from __future__ import annotations

from pathlib import Path
import json
import re


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    raw = (ROOT / relative).read_text(encoding="utf-8").lower()
    return re.sub(r"\s+", " ", raw)


def _assert_contract(relative: str, *clauses: str) -> None:
    text = _text(relative)
    for clause in clauses:
        assert clause.lower() in text, f"{relative} is missing contract clause: {clause}"


def test_stage_agent_contracts_define_focused_routes() -> None:
    _assert_contract(
        "agents/tp-product.md",
        "minimum-sufficient focused route",
        "goal, requirement, acceptance criteria, domain, declared constraints, and product-risk evidence",
        "all 26 dispositions",
        "never launch a normal full-catalog run",
    )
    _assert_contract(
        "agents/tp-designer.md",
        "minimum-sufficient focused route",
        "approved requirement and proposed solution evidence",
        "solution-design coverage",
        "all 26 dispositions",
    )
    _assert_contract(
        "agents/tp-planner.md",
        "exactly three or four quick lenses",
        "task-to-acceptance-criterion coverage",
        "all 26 dispositions",
        "split the scope",
        "authenticated expanded-route approval",
    )
    _assert_contract(
        "agents/tp-evaluator.md",
        "exactly three or four quick lenses",
        "actual diff, changed files, dependency impact, test evidence, and unresolved findings",
        "all 26 dispositions",
        "authenticated expanded-route approval",
        "fingerprint inputs changed",
    )


def test_build_and_fix_agents_never_launch_lens_workers() -> None:
    terminal_paths = "success, failure, cancellation, interruption, and handoff"
    for relative in ("agents/tp-executor.md", "agents/tp-fixer.md"):
        _assert_contract(
            relative,
            "zero lens workers",
            terminal_paths,
            "product, design, plan, and evaluate",
        )
    assert "primed lenses" not in _text("agents/tp-executor.md")
    _assert_contract(
        "agents/tp-lens.md",
        "quick lens worker",
        "product, design, plan, or evaluate",
        "refuse any build or fix brief",
    )
    _assert_contract(
        "agents/tp-orchestrator.md",
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
        _assert_contract(
            relative,
            "product/design minimum-sufficient focused routes",
            "plan/evaluate exactly three or four quick lenses",
            "build/fix zero lens workers",
            "all 26 dispositions",
        )

    _assert_contract(
        "skills/tp-product/SKILL.md",
        "minimum-sufficient focused route",
        "all 26 dispositions",
        "normal full-catalog run",
    )
    _assert_contract(
        "skills/tp-design/SKILL.md",
        "minimum-sufficient focused route",
        "solution-design coverage",
        "all 26 dispositions",
    )
    _assert_contract(
        "skills/tp-engineering/SKILL.md",
        "exactly three or four quick lenses",
        "all 26 dispositions",
        "fingerprint inputs changed",
    )


def test_governed_contract_records_overflow_reuse_and_zero_lens_terminal_paths() -> None:
    combined = "\n".join(
        _text(relative)
        for relative in (
            "skills/taskplane/references/harness-rules.md",
            "skills/tp-go/SKILL.md",
            "skills/tp-build/SKILL.md",
            "skills/tp-engineering/SKILL.md",
        )
    )
    assert "authenticated expanded-route approval" in combined
    assert "split the scope" in combined
    assert "fingerprint inputs changed" in combined
    assert "success, failure, cancellation, interruption, and handoff" in combined


def test_legacy_broad_review_routing_is_not_still_authoritative() -> None:
    governed = "\n".join(
        _text(relative)
        for relative in (
            "agents/tp-engineering.md",
            "skills/tp-go/SKILL.md",
            "skills/tp-build/SKILL.md",
            "skills/tp-engineering/SKILL.md",
            "skills/tp-engineering/references/em-session.md",
        )
    )
    for stale in (
        "cap-8 budget",
        "full-catalog audit sweep",
        "plus at most one light sweep",
        "deep slots plus the optional single light-sweep slot",
    ):
        assert stale not in governed
    _assert_contract(
        "agents/tp-engineering.md",
        "exactly three or four quick lenses",
        "does not launch a second lens sweep",
        "all 26 dispositions",
    )


def test_skill_flow_graphs_expose_focused_stage_boundaries() -> None:
    def labels(relative: str) -> str:
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        return " ".join(node["label"].lower() for node in payload["nodes"])

    assert "minimum-sufficient quick route + 26 dispositions" in labels(
        "skills/tp-product/flow.json"
    )
    design = labels("skills/tp-design/flow.json")
    assert "minimum-sufficient quick route" in design
    assert "solution-design" in design
    for relative in ("skills/tp-go/flow.json", "skills/tp-build/flow.json"):
        flow = labels(relative)
        assert "plan · exactly 3–4 quick" in flow
        assert "evaluate · exactly 3–4 quick" in flow
        assert "zero lenses" in flow
        assert "consume evaluate evidence" in flow
    engineering = labels("skills/tp-engineering/flow.json")
    assert "consume 3–4 quick evaluate results" in engineering
    assert "deep" not in engineering
