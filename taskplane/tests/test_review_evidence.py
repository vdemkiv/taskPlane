"""R-0005 immutable shared review envelope and deterministic scoped views."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_evidence as evidence  # noqa: E402
import evidence as evaluation_evidence  # noqa: E402
import lens  # noqa: E402
import review  # noqa: E402
import defect_claim  # noqa: E402
import runnability  # noqa: E402
import runtime_eval  # noqa: E402
import target  # noqa: E402


class TestCanonicalViolationNormalization(unittest.TestCase):
    def test_graph_requirement_identity_remains_a_blocking_violation(self):
        finding = {
            "lens": "architecture", "kind": "violation",
            "severity": "blocker", "class": "regression",
            "file": "taskplane/review.py", "line": 142,
            "title": "static review can be overwritten",
            "scenario": "select static and record executed render evidence",
            "fix": "preserve the exact approved review execution mode",
            "declares": "req:R-0006",
        }
        seen = []

        def resolves(_workspace, identity):
            seen.append(identity)
            return identity == "R-0006"

        with mock.patch.object(defect_claim, "declaration_resolves",
                               side_effect=resolves):
            findings, notes = review._adjudicate_findings(
                "/tmp/review-normalization", None, {}, [finding])

        self.assertEqual(seen, ["R-0006"])
        self.assertEqual(notes, [])
        self.assertEqual(
            review.blocking_findings_by_lens(findings), {"architecture": 1})


class TestImmutableEnvelope(unittest.TestCase):
    def test_derived_seams_keep_source_and_coverage_limits(self):
        graph = {"meta":{"scanned_head":"abc1234", "content_fingerprint":"g",
            "source_coverage":{"status":"partial", "complete":False,
                "stopping_conditions":["timed-out", "unsupported", "truncated"]}},
            "edges":[{"from":"src/a", "to":"api", "kind":"imports", "source":"scanner"},
                {"from":"src/a", "to":"outside", "kind":"imports", "source":"scanner"}]}
        impact = {"touched":["src/a"], "impacted":{"1":[{"module":"api"}]},
            "truncated":True, "unknown":["unparsed.py"], "depth_limit":3}
        kwargs = {"target":self.kw["target"], "diff":self.kw["diff"], "graph":graph,
            "impact":impact, "graph_quality":self.kw["graph_quality"]}
        value = evidence.source_derived_review_seams(**kwargs)
        self.assertEqual(value["coverage"]["status"], "partial")
        self.assertEqual(value["coverage"]["stopping_conditions"], ["timed-out", "unsupported", "truncated"])
        self.assertTrue(value["coverage"]["truncated"])
        self.assertEqual(value["coverage"]["unknown"], ["unparsed.py"])
        self.assertEqual([row["to"] for row in value["edges"]], ["api"])
        graph["meta"]["scanned_head"] = "foreign"
        stale = evidence.source_derived_review_seams(**kwargs)
        self.assertFalse(stale["coverage"]["source_current"])
        self.assertEqual(stale["edges"], [])
        graph["meta"].pop("scanned_head")
        self.assertEqual(evidence.source_derived_review_seams(**kwargs)["edges"], [])

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-evidence-")
        self.store = evidence.ArtifactStore(self.ws)
        self.kw = {
            "target": {"fingerprint": "target-1", "head": "abc1234"},
            "diff": {"files": ["src/a.py"], "changed_symbols": ["changed"]},
            "impact": {"touched": ["src/a"], "affected_requirements": ["R-1"]},
            "graph_quality": {"status": "complete", "fingerprint": "gq-1"},
            "runnability": {"fingerprint": "run-1", "checks": []},
            "requirement": {"id": "R-1", "text": "does the thing"},
            "acceptance": ["works"],
            "contracts": ["contract:thing"],
            "change": {"type": "architecture"},
        }

    def test_identical_snapshot_creates_one_content_addressed_envelope(self):
        first = evidence.create_envelope(self.store, **self.kw)
        second = evidence.create_envelope(self.store, **self.kw)
        self.assertEqual(first, second)
        self.assertTrue(self.store.verify(first))
        self.assertEqual(len(self.store.references("envelope")), 1)
        payload = self.store.read(first)
        self.assertEqual(payload["schema"], "taskplane.review-envelope/v2")
        self.assertEqual(payload["context_fingerprint"], first["fingerprint"])

    def test_every_fact_changes_the_context_fingerprint(self):
        first = evidence.create_envelope(self.store, **self.kw)
        changed = dict(self.kw)
        changed["contracts"] = ["contract:other"]
        self.assertNotEqual(first["fingerprint"],
                            evidence.create_envelope(self.store, **changed)["fingerprint"])






class TestCanonicalInputs(unittest.TestCase):
    def test_target_identity_is_one_projection_shape(self):
        rec = {"fingerprint": "target-1", "head": "abc", "base": "def"}
        self.assertEqual(target.canonical_identity(rec), {
            "target_fingerprint": "target-1", "target_head": "abc",
            "target_base": "def"})
        self.assertEqual(target.cited_fingerprint({
            "identity": {"target_fingerprint": "target-1"}}), "target-1")

    def test_cached_runnability_annotation_does_not_change_evidence(self):
        result = {"fingerprint": "run-1", "checks": [], "summary": "ok"}
        cached = dict(result, cached=True)
        self.assertEqual(runnability.evidence_record(result),
                         runnability.evidence_record(cached))


if __name__ == "__main__":
    unittest.main()
