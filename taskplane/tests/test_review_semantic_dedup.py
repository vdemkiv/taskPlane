import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402


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

