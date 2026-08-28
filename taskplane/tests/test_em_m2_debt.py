"""Focused evidence for M-25 priced deferred-work governance."""

from __future__ import annotations

import copy

import pytest

import remediation_trace


DEFERRED_ITEMS = [
    {
        "item_id": "R0013-P1-W31-cold-start",
        "source_reference": "specs/spec.md:218#W31-live-host-cold-start",
        "debt_id": "D-1301",
    },
    {
        "item_id": "R0013-P1-release-repair",
        "source_reference": "specs/spec.md:218#historical-release-repair",
        "debt_id": "D-1302",
    },
    {
        "item_id": "R0013-P2-pushed-sha-closure",
        "source_reference": "specs/spec.md:218#pushed-sha-release-closure",
        "debt_id": "D-1303",
    },
]


def _cost(total: int, basis: str) -> dict:
    return {
        "unit": "relative-work-units",
        "backfill": total - 4,
        "migration": 1,
        "compatibility": 1,
        "operator_reteaching": 1,
        "other": 1,
        "total": total,
        "basis": basis,
    }


def _record(reference: dict, index: int) -> dict:
    return remediation_trace.priced_debt_record(
        debt_id=reference["debt_id"],
        deferred_item=reference["item_id"],
        owner=f"team:delivery-{index}",
        reentry_trigger=f"Re-enter when release checkpoint {index} is scheduled",
        follow_up=f"Complete deferred R-0013 follow-up {index} with migration proof",
        now_cost=_cost(5 + index, "Current bounded implementation estimate"),
        later_cost=_cost(9 + index, "Includes accumulated compatibility work"),
        references=[reference["source_reference"], f"decision:D-13{index:02d}"],
    )


def _approved_references(records: list[dict]) -> list[dict]:
    return [
        {**reference, "record_fingerprint": record["content_fingerprint"]}
        for reference, record in zip(DEFERRED_ITEMS, records)
    ]


def test_m25_deferred_items_link_to_priced_governed_debt() -> None:
    records = [
        _record(reference, index)
        for index, reference in enumerate(DEFERRED_ITEMS, 1)
    ]
    approved = _approved_references(records)
    trace = remediation_trace.build_priced_debt_trace(
        deferred_references=approved,
        records=list(reversed(records)),
    )

    assert remediation_trace.verify_priced_debt_trace(
        trace, expected_deferred_references=approved
    ) == trace
    assert trace["record_count"] == len(approved)
    assert trace["required_debt_ids"] == [row["debt_id"] for row in approved]
    for record, reference in zip(trace["records"], approved):
        assert record["owner"].startswith("team:")
        assert record["reentry_trigger"]
        assert record["now_cost"]["unit"] == record["later_cost"]["unit"]
        assert record["now_cost"]["total"] == sum(
            record["now_cost"][field]
            for field in remediation_trace._DEBT_COST_COMPONENTS
        )
        assert record["later_cost"]["total"] == sum(
            record["later_cost"][field]
            for field in remediation_trace._DEBT_COST_COMPONENTS
        )
        assert reference["source_reference"] in record["references"]
        assert len(record["content_fingerprint"]) == 64


def test_m25_missing_unpriced_or_tampered_debt_is_refused() -> None:
    records = [
        _record(reference, index)
        for index, reference in enumerate(DEFERRED_ITEMS, 1)
    ]
    approved = _approved_references(records)

    with pytest.raises(remediation_trace.RemediationTraceError, match="every deferred item"):
        remediation_trace.build_priced_debt_trace(
            deferred_references=approved, records=records[:-1]
        )

    unpriced = copy.deepcopy(records[0])
    unpriced["now_cost"]["total"] = 0
    with pytest.raises(remediation_trace.RemediationTraceError, match="total"):
        remediation_trace.build_priced_debt_trace(
            deferred_references=approved, records=[unpriced, *records[1:]]
        )

    trace = remediation_trace.build_priced_debt_trace(
        deferred_references=approved, records=records
    )
    tampered = copy.deepcopy(trace)
    tampered["records"][0]["owner"] = "team:unreviewed-owner"
    with pytest.raises(remediation_trace.RemediationTraceError, match="tampered"):
        remediation_trace.verify_priced_debt_trace(
            tampered, expected_deferred_references=approved
        )

    # A fully re-minted record is still refused when its changed owner is not
    # the exact Product-approved fingerprint.  This is stronger than merely
    # detecting edits made without recomputing public hashes.
    forged_record = remediation_trace.priced_debt_record(
        debt_id=records[0]["debt_id"],
        deferred_item=records[0]["deferred_item"],
        owner="team:unreviewed-owner",
        reentry_trigger=records[0]["reentry_trigger"],
        follow_up=records[0]["follow_up"],
        now_cost=records[0]["now_cost"],
        later_cost=records[0]["later_cost"],
        references=records[0]["references"],
    )
    with pytest.raises(remediation_trace.RemediationTraceError, match="does not match"):
        remediation_trace.build_priced_debt_trace(
            deferred_references=approved,
            records=[forged_record, *records[1:]],
        )

    replayed_reference = copy.deepcopy(approved)
    replayed_reference[-1]["debt_id"] = replayed_reference[0]["debt_id"]
    with pytest.raises(remediation_trace.RemediationTraceError, match="reuses"):
        remediation_trace.verify_priced_debt_trace(
            trace, expected_deferred_references=replayed_reference
        )
