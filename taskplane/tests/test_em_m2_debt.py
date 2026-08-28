"""Focused evidence for M-25 repository-authoritative priced debt."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import remediation_trace


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "specs" / "spec.md"


def test_m25_deferred_items_link_to_priced_governed_debt() -> None:
    authority = remediation_trace.priced_debt_authority()
    records = authority["records"]
    trace = remediation_trace.build_priced_debt_trace(
        records=list(reversed(records))
    )

    assert remediation_trace.verify_priced_debt_trace(trace) == trace
    assert authority["path"] == "specs/spec.md"
    assert trace["authority"] == {
        "path": authority["path"],
        "content_sha256": authority["content_sha256"],
    }
    assert trace["required_debt_ids"] == ["D-1301", "D-1302", "D-1303"]
    assert trace["record_count"] == 3
    for record in trace["records"]:
        assert record["owner"].startswith("owner:")
        assert set(record["reentry_trigger"]) == {"signal", "threshold", "action"}
        assert record["now_cost"]["unit"] == record["later_cost"]["unit"]
        assert record["now_cost"]["total"] == sum(
            record["now_cost"][field]
            for field in remediation_trace._DEBT_COST_COMPONENTS
        )
        assert record["later_cost"]["total"] == sum(
            record["later_cost"][field]
            for field in remediation_trace._DEBT_COST_COMPONENTS
        )
        assert all(reference.startswith("specs/spec.md#")
                   for reference in record["references"])
        assert len(record["content_fingerprint"]) == 64


def test_m25_caller_recomputed_authority_and_invalid_provenance_are_refused() -> None:
    records = remediation_trace.priced_debt_authority()["records"]

    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="every deferred item"):
        remediation_trace.build_priced_debt_trace(records=records[:-1])

    changed = records[0]
    reminted = remediation_trace.priced_debt_record(
        debt_id=changed["debt_id"],
        deferred_item=changed["deferred_item"],
        owner="owner:caller-reminted",
        reentry_trigger=changed["reentry_trigger"],
        follow_up=changed["follow_up"],
        now_cost=changed["now_cost"],
        later_cost=changed["later_cost"],
        references=changed["references"],
    )
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="differs from repository Product authority"):
        remediation_trace.build_priced_debt_trace(
            records=[reminted, *records[1:]]
        )

    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="anchor does not resolve"):
        remediation_trace.priced_debt_record(
            debt_id=changed["debt_id"],
            deferred_item=changed["deferred_item"],
            owner=changed["owner"],
            reentry_trigger=changed["reentry_trigger"],
            follow_up=changed["follow_up"],
            now_cost=changed["now_cost"],
            later_cost=changed["later_cost"],
            references=["specs/spec.md#invented-provenance-anchor"],
        )

    vague_trigger = copy.deepcopy(changed["reentry_trigger"])
    vague_trigger["signal"] = "someday"
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="not actionable"):
        remediation_trace.priced_debt_record(
            debt_id=changed["debt_id"],
            deferred_item=changed["deferred_item"],
            owner=changed["owner"],
            reentry_trigger=vague_trigger,
            follow_up=changed["follow_up"],
            now_cost=changed["now_cost"],
            later_cost=changed["later_cost"],
            references=changed["references"],
        )


def test_m25_missing_out_of_scope_link_invalidates_spec_authority() -> None:
    source = SPEC.read_text(encoding="utf-8")
    assert "[D-1302](#debt-d-1302)" in source
    missing_link = source.replace("[D-1302](#debt-d-1302)", "D-1302", 1)

    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="every deferred item must link"):
        remediation_trace._parse_priced_debt_authority(missing_link)
