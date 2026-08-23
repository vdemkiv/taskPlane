"""R-0007: findings are structured, leased, and provenance-bound."""
import pytest

import review_evidence as evidence


LEASE = {
    "schema": "taskplane.slot-lease/v1", "slot_id": "sweep.security",
    "lens_ids": ["security"], "target_fingerprint": "a" * 64,
    "context_fingerprint": "b" * 64, "view_fingerprint": "c" * 64,
    "canonical_revision": 1, "lease_fingerprint": "d" * 64,
}
FINDING = {
    "lens": "security", "kind": "defect", "severity": "high",
    "class": "regression", "file": "taskplane/review.py", "line": 1,
    "title": "Unleased evidence", "scenario": "A forged producer writes it",
    "fix": "Require provenance", "claim": {
        "trigger": "write", "outcome": "accepted", "repro": "replay"},
}


def test_receipt_binds_finding_to_lease_and_settled_inventory():
    row = dict(FINDING)
    row["provenance_receipt"] = evidence.finding_provenance_receipt(
        lease=LEASE, finding=row, source="result.json", settled_count=3)
    receipt = evidence.validate_finding_provenance(lease=LEASE, finding=row)
    assert receipt["lease_fingerprint"] == LEASE["lease_fingerprint"]
    assert receipt["settled_count"] == 3


def test_prose_or_tampered_finding_is_rejected():
    with pytest.raises(evidence.ProvenanceError, match="structured finding"):
        evidence.finding_provenance_receipt(
            lease=LEASE, finding="high severity prose", source="result.json")
    row = dict(FINDING)
    row["provenance_receipt"] = evidence.finding_provenance_receipt(
        lease=LEASE, finding=row, source="result.json")
    row["title"] = "changed after receipt"
    with pytest.raises(evidence.ProvenanceError, match="contradicts"):
        evidence.validate_finding_provenance(lease=LEASE, finding=row)

