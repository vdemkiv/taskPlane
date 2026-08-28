"""Focused documentation authority checks for MX-DOCS-ARCH.

These tests deliberately validate complete decision statements rather than
isolated keywords so documentation drift cannot silently weaken the runtime
authority contract.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOOP_DESIGN = ROOT / "docs" / "loop-design.md"


def _document() -> str:
    return LOOP_DESIGN.read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """Normalize Markdown wrapping while preserving every semantic token."""

    return " ".join(text.split())


def _section(text: str, heading: str, *, level: int) -> str:
    marker = f"{'#' * level} {heading}"
    start = text.index(marker) + len(marker)
    following = re.search(rf"^#{{1,{level}}} .+$", text[start:], re.MULTILINE)
    end = start + following.start() if following else len(text)
    return text[start:end]


def test_m22_loop_synopsis_includes_unavailable_outcome() -> None:
    """M-22: the command and its evaluator-only semantics stay together."""

    engine = _section(_document(), "The loop engine (proposed: taskplane owns it)", level=2)
    synopsis = re.search(r"```\n(?P<body>.*?)\n```", engine, re.DOTALL)
    assert synopsis is not None
    assert "tp.py loop submit [pass|fail|unavailable]" in synopsis.group("body")

    prose = _prose(engine)
    required_semantics = (
        "Only an EVALUATE worker may submit it",
        "bounded host/model outage",
        "one visible warning",
        "without consuming a product FIX cycle",
        "product, contract, test, or lens failure must be submitted as `fail`",
        "`unavailable` cannot turn such a failure into progress",
    )
    for statement in required_semantics:
        assert statement in prose


def test_m27_stage_migration_records_alternatives_and_revisit_trigger() -> None:
    """M-27: the one-way door records real alternatives and a hard trigger."""

    migration = _section(
        _document(),
        "Accepted decision `D-LOOP-STAGE-MIGRATION`",
        level=4,
    )
    prose = _prose(migration)

    assert "**Status / owner:** ACCEPTED" in prose
    assert "Before that commit the legacy singleton is authoritative" in prose
    assert "after it the verified stage-manifest revision is authoritative" in prose
    assert "There is no dual-authority window" in prose

    selected = "A. Retained-source, receipt-verified one-way conversion (selected)"
    rejected = (
        "B. Bidirectional dual-write with reverse migration",
        "C. In-place singleton schema upgrade",
    )
    assert selected in migration
    for alternative in rejected:
        assert alternative in migration
    assert "split-brain authority" in migration
    assert "destroys byte-exact source evidence" in migration

    assert "at least two supported production consumers require reverse export *and* a conformance suite proves a lossless round trip" in prose
    assert "every `taskplane.legacy-unknown/v1` sentinel" in prose
    assert "authority-epoch protocol that prevents split brain" in prose
    assert "Both conditions are required" in prose
    assert "a single legacy client is not a revisit trigger" in prose


def test_m28_loop_engine_ownership_is_in_decision_registry() -> None:
    """M-28: production loop ownership is an accepted registry entry."""

    registry = _section(
        _document(),
        "Decision registry (resolved & locked 2026-07-11)",
        level=2,
    )
    ownership_row = re.search(
        r"^\| `D-LOOP-ENGINE-OWNERSHIP` \| ACCEPTED \|(?P<decision>.*?)"
        r"\|(?P<implementation>.*?)\|$",
        registry,
        re.MULTILINE,
    )
    assert ownership_row is not None
    decision = ownership_row.group("decision")
    assert "taskplane owns the loop state machine" in decision
    assert "transitions, DoR/DoD gates, and single audit trace" in decision
    assert "`taskplane/loop.py`" in ownership_row.group("implementation")
    assert "`taskplane/tp.py loop`" in ownership_row.group("implementation")

    prose = _prose(registry)
    authority_rules = (
        "changing state",
        "deciding a transition",
        "evaluating a DoR/DoD gate",
        "appending the authoritative audit event belongs to the engine",
        "cannot replace them with prose state or self-approve their result",
        "requires a superseding accepted decision",
    )
    for statement in authority_rules:
        assert statement in prose
