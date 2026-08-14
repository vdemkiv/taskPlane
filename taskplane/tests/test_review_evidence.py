"""R-0005 immutable shared review envelope and deterministic scoped views."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_evidence as evidence  # noqa: E402
import runnability  # noqa: E402
import target  # noqa: E402


class TestImmutableEnvelope(unittest.TestCase):
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

    def test_scoped_view_is_stable_bounded_and_keeps_mandatory_facts(self):
        envelope = evidence.create_envelope(self.store, **self.kw)
        first = evidence.create_scoped_view(
            self.store, envelope, slot_id="deep.architecture",
            lens_ids=["architecture"], relevant_files=["src/a.py"])
        second = evidence.create_scoped_view(
            self.store, envelope, slot_id="deep.architecture",
            lens_ids=["architecture"], relevant_files=["src/a.py"])
        self.assertEqual(first, second)
        view = self.store.read(first)
        self.assertEqual(view["context_fingerprint"], envelope["fingerprint"])
        self.assertEqual(view["target"]["fingerprint"], "target-1")
        self.assertEqual(view["requirements"]["requirement"]["id"], "R-1")
        self.assertEqual(view["contracts"], ["contract:thing"])
        self.assertLess(len(json.dumps(view)), evidence.MAX_SCOPED_VIEW_BYTES)

    def test_repeated_requirement_and_impact_facts_are_stored_once(self):
        acceptance = [
            f"Criterion {index}: the governed review records complete canonical "
            "evidence, preserves provenance across host adapters, and refuses "
            "dispatch when bounded impact confidence is incomplete."
            for index in range(16)
        ]
        impacted = [
            {
                "module": f"services/review/component-{index:02d}",
                "via": "taskplane/review_evidence.py",
                "kind": "uses",
            }
            for index in range(44)
        ]
        impact = {
            "touched": ["taskplane", "taskplane/tests"],
            "impacted": {"1": impacted},
            "total_impacted": len(impacted),
            "unknown": [],
            "depth_limit": 6,
            "truncated": False,
            "context": "Change blast radius from the canonical dependency graph.",
        }
        values = dict(self.kw)
        values.update({
            "requirement": {
                "id": "R-0005",
                "title": "Make governed reviews provably complete and cheaper",
                "acceptance": acceptance,
                "open_questions": [],
            },
            "acceptance": acceptance,
            "impact": impact,
            "graph_quality": {
                "status": "complete",
                "fingerprint": "gq-large",
                "coverage": {"scanner": "complete", "callers": "bounded"},
                "impact": impact,
            },
        })

        envelope_ref = evidence.create_envelope(self.store, **values)
        envelope = self.store.read(envelope_ref)
        self.assertEqual(envelope["requirements"]["acceptance"], acceptance)
        self.assertNotIn("acceptance", envelope["requirements"]["requirement"])
        self.assertEqual(envelope["impact"], impact)
        self.assertNotIn("impact", envelope["graph_quality"])

        view_ref = evidence.create_scoped_view(
            self.store, envelope_ref, slot_id="deep.architecture-security",
            lens_ids=["architecture", "security"])
        view = self.store.read(view_ref)
        self.assertLess(
            len(evidence.canonical_bytes(view)), evidence.MAX_SCOPED_VIEW_BYTES)


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
