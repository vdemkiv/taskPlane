import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402
import review_evidence  # noqa: E402


def _finding(lens, title, severity="low", line=94):
    return {
        "lens": lens, "kind": "defect", "severity": severity,
        "class": "observation", "file": "src/Header.tsx", "line": line,
        "title": title, "scenario": "margin-only change leaves stale state",
        "fix": "observe the actual invalidation source",
    }


def test_semantic_dedup_clusters_near_duplicates_and_retains_every_producer():
    rows = review.semantic_deduplicate_findings([
        _finding("architecture", "Margin-only changes are not observed"),
        _finding("frontend", "margin only change is never observed", "med"),
    ])
    assert len(rows) == 1
    assert rows[0]["severity"] == "med"
    assert {p["lens"] for p in rows[0]["provenance"]} == {
        "architecture", "frontend"}
    assert {p["severity"] for p in rows[0]["provenance"]} == {"low", "med"}


def test_semantic_dedup_does_not_merge_different_source_anchors():
    rows = review.semantic_deduplicate_findings([
        _finding("frontend", "margin only change is never observed", line=94),
        _finding("testability", "margin only change is never observed", line=212),
    ])
    assert len(rows) == 2


def test_revision_provenance_retains_engine_bound_result_source():
    ws = tempfile.mkdtemp(prefix="tp-review-dedup-")
    store = review_evidence.ArtifactStore(ws)
    envelope_ref = store.put("envelope", {
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
    })
    revision, _ = review._revision_record(store, envelope_ref, {
        "canonical_revision": 1,
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "result_fingerprints": ["result-1"],
        "results": [{
            "slot_id": "lens-architecture",
            "result_fingerprint": "result-1",
            "canonical_revision": 1,
            "source": ".taskplane/runs/run-1/lenses/results/architecture.json",
            "findings": [_finding(
                "architecture", "Margin-only changes are not observed")],
        }],
    })

    assert revision["findings"][0]["provenance"] == [{
        "lens": "architecture",
        "slot_id": "lens-architecture",
        "source": ".taskplane/runs/run-1/lenses/results/architecture.json",
        "result_fingerprint": "result-1",
        "canonical_revision": 1,
        "severity": "low",
    }]
