"""Focused documentation authority checks for MX-DOCS-ARCH.

These tests deliberately validate complete decision statements rather than
isolated keywords so documentation drift cannot silently weaken the runtime
authority contract.
"""

from __future__ import annotations

import json
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
    """M-28: loop ownership is a complete retrievable ACTIVE decision."""

    registry = _section(
        _document(),
        "Decision registry (resolved & locked 2026-07-11)",
        level=2,
    )
    ownership_row = re.search(
        r"^\| `D-LOOP-ENGINE-OWNERSHIP/v1` \| ACTIVE \|(?P<summary>.*?)"
        r"\| Complete versioned record below \|$",
        registry,
        re.MULTILINE,
    )
    assert ownership_row is not None
    assert "host orchestrator owns native worker lifecycle" in \
        ownership_row.group("summary")

    decision_section = _section(
        registry,
        "Decision record: `D-LOOP-ENGINE-OWNERSHIP/v1`",
        level=3,
    )
    match = re.search(r"```json\n(?P<record>.*?)\n```", decision_section,
                      re.DOTALL)
    assert match is not None, "a summary row is not a decision record"
    record = json.loads(match.group("record"))

    assert record["schema"] == "taskplane.decision/v1"
    assert record["id"] == "D-LOOP-ENGINE-OWNERSHIP"
    assert record["version"] == 1
    assert record["status"] == "ACTIVE"
    assert record["owner"] == "taskplane-loop-engine"
    assert record["affected_module_globs"] == [
        "taskplane/loop*.py",
        "taskplane/tp.py",
        "taskplane/native_authority.py",
    ]
    assert record["provenance"] == {
        "requirement_ids": ["R-0002"],
        "finding_ids": ["M-28"],
        "sources": [
            "docs/loop-design.md",
            "design/contract.json#/finding_map/M-28",
        ],
    }

    selected_id = record["selected_alternative"]
    alternatives = {row["id"]: row for row in record["alternatives"]}
    assert len(alternatives) >= 3
    selected = alternatives[selected_id]
    assert selected["disposition"] == "SELECTED"
    assert "host orchestrator owns native worker dispatch" in \
        selected["decision"]
    assert "SubagentStart/SubagentStop lifecycle" in selected["decision"]
    assert len(selected["qualities_gained"]) >= 2
    assert len(selected["qualities_spent"]) >= 2
    rejected = [row for row in alternatives.values()
                if row["disposition"] == "REJECTED"]
    assert len(rejected) >= 2
    assert all(len(row["qualities_gained"]) >= 2 for row in rejected)
    assert all(len(row["qualities_spent"]) >= 2 for row in rejected)

    assert record["authority_owners"] == {
        "governed_state_transitions_gates_and_audit":
            "taskplane-loop-engine",
        "native_worker_dispatch_start_stop_and_wait": "host-orchestrator",
    }
    trigger = record["revisit_trigger"]
    assert trigger["subject"] == "stable host-native lifecycle contract"
    assert trigger["minimum_consecutive_minor_releases"] >= 2
    assert trigger["minimum_governed_dispatches"] >= 100
    assert trigger["required_start_stop_receipt_pairing_percent"] == 100
    assert trigger["required_exact_checkout_run_binding_percent"] == 100
    assert trigger["maximum_orphaned_worker_identities"] == 0
    assert trigger["maximum_poll_based_waits"] == 0
    assert "every supported host meets every threshold" in trigger["action"]

    lineage = record["lineage"]
    assert lineage["supersedes"] == [
        "D-LOOP-ENGINE-OWNERSHIP/prose-2026-07-11"
    ]
    assert lineage["superseded_by"] is None
    assert lineage["narrows"] == \
        "engine authority excludes host-native worker lifecycle"

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
