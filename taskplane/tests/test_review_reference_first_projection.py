"""R-0008 bounded reference-first review projection."""
import copy
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_evidence as evidence  # noqa: E402


class ReferenceFirstProjectionTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-reference-first-")
        self.store = evidence.ArtifactStore(self.ws)

    def envelope(self, size=4 * 1024 * 1024):
        files = [f"src/component-{index:05d}.py" for index in range(1000)]
        requirement = {
            "id": "R-LARGE", "title": "Review very large changes",
            "narrative": "requirement " * (size // 12),
        }
        return evidence.create_envelope(
            self.store,
            target={"fingerprint": "target-1", "head": "abc", "base": "def"},
            diff={"files": files, "changed_symbols": list(reversed(files)),
                  "patch": "x" * size},
            impact={"touched": files, "total_impacted": len(files),
                    "details": "impact " * (size // 7)},
            graph_quality={"status": "complete", "details": "g" * size},
            runnability={"fingerprint": "run-1", "details": "r" * size},
            requirement=requirement,
            acceptance=["acceptance " * (size // 11)],
            contracts=[f"contract:{index:05d}" for index in range(1000)],
            change={"type": "architecture", "details": "c" * size},
        )

    def project(self, envelope, **overrides):
        values = {
            "slot_id": "deep.architecture-security",
            "lens_ids": ["security", "architecture"],
            "relevant_files": ["src/component-00001.py"],
            "canonical_revision": 7,
            "routing_fingerprint": "route-1",
            "producer": "lens-slot",
        }
        values.update(overrides)
        return evidence.create_scoped_view(self.store, envelope, **values)

    def test_multimegabyte_view_is_bounded_and_has_complete_inline_spine(self):
        envelope = self.envelope()
        ref = self.project(envelope)
        view = self.store.read(ref)

        self.assertLessEqual(len(evidence.canonical_bytes(view)), 16 * 1024)
        self.assertEqual(view["schema"], "taskplane.scoped-review-view/v3")
        self.assertEqual(view["target_fingerprint"], "target-1")
        self.assertEqual(view["canonical_revision"], 7)
        self.assertEqual(view["routing_fingerprint"], "route-1")
        self.assertEqual(view["slot_id"], "deep.architecture-security")
        self.assertEqual(view["lens_ids"], ["architecture", "security"])
        self.assertEqual(view["producer"], "lens-slot")
        self.assertEqual(view["envelope_digest"], envelope["digest"])
        self.assertEqual(view["reference_manifest_fingerprint"],
                         evidence.content_fingerprint(view["reference_manifest"]))
        self.assertEqual(view["integrity"]["algorithm"], "sha256")
        self.assertTrue(view["reference_manifest"])
        self.assertEqual(view["relevance"]["files"],
                         ["src/component-00001.py"])
        self.assertEqual({row["section"] for row in view["omissions"]},
                         {row["section"] for row in view["reference_manifest"]})

    def test_portable_references_omit_host_paths_and_resolve_by_identity(self):
        view = self.store.read(self.project(self.envelope(64 * 1024)))
        reference = view["reference_manifest"][0]["reference"]
        artifact = reference["artifact"]
        self.assertNotIn("path", artifact)
        self.assertNotIn("relative_path", artifact)
        self.assertFalse(any(os.path.isabs(str(value))
                             for value in artifact.values()))
        self.assertIsNotNone(evidence.resolve_evidence_reference(
            self.store, reference, target_fingerprint="target-1",
            canonical_revision=7, allowed_sections={reference["section"]}))

    def test_relevant_summaries_and_fitting_exact_sections_stay_inline(self):
        view = self.store.read(self.project(self.envelope(2048)))
        summaries = {row["section"] for row in view["relevant_summaries"]}
        self.assertIn("diff", summaries)
        self.assertIn("requirements", summaries)
        self.assertTrue(view["inline_sections"])
        self.assertTrue(set(view["inline_sections"]).isdisjoint(
            row["section"] for row in view["reference_manifest"]))
        self.assertLessEqual(len(evidence.canonical_bytes(view)), 16 * 1024)

    def test_exact_budget_boundary_is_accepted_and_impossible_spine_fails(self):
        envelope = self.envelope(2048)
        original = evidence.MAX_SCOPED_VIEW_BYTES
        try:
            evidence.MAX_SCOPED_VIEW_BYTES = 16 * 1024
            view = self.store.read(self.project(envelope))
            exact = len(evidence.canonical_bytes(view))
            evidence.MAX_SCOPED_VIEW_BYTES = exact
            self.project(envelope)
            # A one-byte reduction may legitimately externalize another exact
            # candidate.  A budget below the mandatory provenance spine must
            # still fail closed rather than truncate it.
            evidence.MAX_SCOPED_VIEW_BYTES = 1
            with self.assertRaisesRegex(evidence.ArtifactIntegrityError,
                                        "mandatory scoped view spine"):
                self.project(envelope)
        finally:
            evidence.MAX_SCOPED_VIEW_BYTES = original

    def test_projection_is_deterministic_across_input_ordering(self):
        envelope = self.envelope(8192)
        first = self.project(envelope, lens_ids=["security", "architecture"],
                             relevant_files=["src/component-00002.py",
                                             "src/component-00001.py"])
        second = self.project(envelope, lens_ids=["architecture", "security"],
                              relevant_files=["src/component-00001.py",
                                              "src/component-00002.py"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(self.store.read(first), self.store.read(second))

    def test_overflow_is_deduplicated_and_target_revision_bound(self):
        envelope = self.envelope(64 * 1024)
        first = self.store.read(self.project(envelope))
        second = self.store.read(self.project(
            envelope, slot_id="deep.security", lens_ids=["security"]))
        first_refs = {r["section"]: r["reference"]
                      for r in first["reference_manifest"]}
        second_refs = {r["section"]: r["reference"]
                       for r in second["reference_manifest"]}
        self.assertEqual(first_refs, second_refs)
        self.assertEqual(len(self.store.references("review-section")),
                         len(first_refs))
        sample = next(iter(first_refs.values()))
        resolved = evidence.resolve_evidence_reference(
            self.store, sample, target_fingerprint="target-1",
            canonical_revision=7, allowed_sections={sample["section"]})
        self.assertIsNotNone(resolved)

    def test_reference_resolver_rejects_all_untrusted_forms(self):
        envelope = self.envelope(32 * 1024)
        view = self.store.read(self.project(envelope))
        ref = view["reference_manifest"][0]["reference"]

        cases = [
            ({"target_fingerprint": "other"}, "another target"),
            ({"canonical_revision": 8}, "stale"),
            ({"allowed_sections": {"forbidden"}}, "unauthorized"),
        ]
        for kwargs, message in cases:
            args = {"target_fingerprint": "target-1",
                    "canonical_revision": 7,
                    "allowed_sections": {ref["section"]}}
            args.update(kwargs)
            with self.subTest(message=message), self.assertRaises(
                    evidence.ProvenanceError):
                evidence.resolve_evidence_reference(self.store, ref, **args)

        for key, value in (("section", "../escape"),
                           ("digest", "0" * 64),
                           ("fingerprint", "0" * 64)):
            changed = copy.deepcopy(ref)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(
                    (evidence.ProvenanceError,
                     evidence.ArtifactIntegrityError)):
                evidence.resolve_evidence_reference(
                    self.store, changed, target_fingerprint="target-1",
                    canonical_revision=7, allowed_sections={ref["section"]})

        os.unlink(self.store._path(ref["artifact"]["kind"],
                                   ref["artifact"]["fingerprint"]))
        with self.assertRaises(evidence.ArtifactIntegrityError):
            evidence.resolve_evidence_reference(
                self.store, ref, target_fingerprint="target-1",
                canonical_revision=7, allowed_sections={ref["section"]})

    def test_mutated_and_symlink_escape_artifacts_fail_closed(self):
        def reference():
            envelope = self.envelope(32 * 1024)
            view = self.store.read(self.project(envelope))
            return view["reference_manifest"][0]["reference"]

        mutated = reference()
        mutated_path = self.store._path(mutated["artifact"]["kind"],
                                        mutated["artifact"]["fingerprint"])
        with open(mutated_path, "ab") as stream:
            stream.write(b"altered")
        with self.assertRaisesRegex(evidence.ArtifactIntegrityError,
                                    "digest mismatch"):
            evidence.resolve_evidence_reference(
                self.store, mutated, target_fingerprint="target-1",
                canonical_revision=7,
                allowed_sections={mutated["section"]})

        # Use another revision so content-addressed deduplication selects a
        # fresh immutable path after the deliberately corrupted fixture.
        envelope = self.envelope(32 * 1024)
        view = self.store.read(self.project(envelope, canonical_revision=8))
        escaped = view["reference_manifest"][0]["reference"]
        path = self.store._path(escaped["artifact"]["kind"],
                                escaped["artifact"]["fingerprint"])
        os.unlink(path)
        external = os.path.join(self.ws, "outside.json")
        with open(external, "w", encoding="utf-8") as stream:
            json.dump({}, stream)
        os.symlink(external, path)
        with self.assertRaisesRegex(evidence.ArtifactIntegrityError,
                                    "symlink"):
            evidence.resolve_evidence_reference(
                self.store, escaped, target_fingerprint="target-1",
                canonical_revision=8,
                allowed_sections={escaped["section"]})


if __name__ == "__main__":
    unittest.main()
