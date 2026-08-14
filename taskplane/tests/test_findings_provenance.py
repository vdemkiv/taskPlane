"""R-0005 slot-authored result provenance and canonical revisions."""
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_evidence as evidence  # noqa: E402
import dashboard  # noqa: E402
import views  # noqa: E402


class ProvenanceCase(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-provenance-")
        self.store = evidence.ArtifactStore(self.ws)
        self.envelope = evidence.create_envelope(
            self.store, target={"fingerprint": "target-1", "head": "abc"},
            diff={"files": ["a.py"]}, impact={"touched": ["a"]},
            graph_quality={"status": "complete", "fingerprint": "gq"},
            runnability={"checks": []}, requirement={"id": "R-1"},
            acceptance=["works"], contracts=["contract:a"],
            change={"type": "code"})

    def lease(self, slot, lens, revision=None):
        view = evidence.create_scoped_view(
            self.store, self.envelope, slot_id=slot, lens_ids=[lens])
        return evidence.create_slot_lease(
            self.store, self.envelope, view, slot_id=slot,
            lens_ids=[lens], canonical_revision=revision)


class TestSlotAuthorship(ProvenanceCase):
    def test_distinct_slots_author_fingerprint_bound_results(self):
        a = self.lease("deep.architecture", "architecture")
        b = self.lease("light.sweep", "security")
        ar = evidence.write_slot_result(
            self.store, a, authored_slot="deep.architecture",
            lens_ids=["architecture"], findings=[{"id": "A", "severity": "high"}])
        br = evidence.write_slot_result(
            self.store, b, authored_slot="light.sweep",
            lens_ids=["security"], findings=[])
        collected = evidence.collect_slot_results(self.store, [a, b], [ar, br])
        self.assertEqual(collected["status"], "complete")
        self.assertEqual(collected["slot_ids"], ["deep.architecture", "light.sweep"])
        self.assertNotEqual(ar["fingerprint"], br["fingerprint"])

    def test_missing_result_fails_completion(self):
        a = self.lease("deep.architecture", "architecture")
        with self.assertRaisesRegex(evidence.ProvenanceError, "missing"):
            evidence.collect_slot_results(self.store, [a], [])

    def test_wrong_slot_cannot_write_a_result(self):
        lease = self.lease("deep.architecture", "architecture")
        with self.assertRaisesRegex(evidence.ProvenanceError, "slot"):
            evidence.write_slot_result(
                self.store, lease, authored_slot="deep.security",
                lens_ids=["architecture"], findings=[])

    def test_orchestrator_reconstruction_is_rejected(self):
        lease = self.lease("deep.architecture", "architecture")
        with self.assertRaisesRegex(evidence.ProvenanceError, "authored"):
            evidence.write_slot_result(
                self.store, lease, authored_slot="deep.architecture",
                lens_ids=["architecture"], findings=[], authored_by="orchestrator")

    def test_one_result_reference_cannot_satisfy_two_slots(self):
        a = self.lease("deep.architecture", "architecture")
        b = self.lease("deep.security", "security")
        result = evidence.write_slot_result(
            self.store, a, authored_slot="deep.architecture",
            lens_ids=["architecture"], findings=[])
        with self.assertRaisesRegex(evidence.ProvenanceError,
                                    "missing|lease|duplicate|copied"):
            evidence.collect_slot_results(self.store, [a, b], [result, result])

    def test_generator_result_references_are_materialized_once(self):
        lease = self.lease("deep.architecture", "architecture")
        result = evidence.write_slot_result(
            self.store, lease, authored_slot="deep.architecture",
            lens_ids=["architecture"], findings=[])
        collected = evidence.collect_slot_results(
            self.store, iter([lease]), (ref for ref in [result]))
        self.assertEqual(collected["result_fingerprints"],
                         [result["fingerprint"]])

    def test_slot_lease_rejects_view_from_another_envelope(self):
        other = evidence.create_envelope(
            self.store, target={"fingerprint": "target-2", "head": "def"},
            diff={"files": ["b.py"]}, impact={"touched": ["b"]},
            graph_quality={"status": "complete", "fingerprint": "other-gq"},
            runnability={"checks": []}, requirement={"id": "R-2"},
            acceptance=["other"], contracts=["contract:b"],
            change={"type": "code"})
        foreign_view = evidence.create_scoped_view(
            self.store, other, slot_id="deep.architecture",
            lens_ids=["architecture"])
        with self.assertRaisesRegex(evidence.ProvenanceError, "envelope"):
            evidence.create_slot_lease(
                self.store, self.envelope, foreign_view,
                slot_id="deep.architecture", lens_ids=["architecture"])


class TestCanonicalRevision(ProvenanceCase):
    def _collected(self):
        lease = self.lease("deep.architecture", "architecture")
        result = evidence.write_slot_result(
            self.store, lease, authored_slot="deep.architecture",
            lens_ids=["architecture"], findings=[{"id": "A"}])
        return evidence.collect_slot_results(self.store, [lease], [result])

    def test_revisions_are_monotonic_and_all_projections_share_identity(self):
        first = evidence.commit_revision(self.store, self.envelope,
                                         self._collected())
        second = evidence.commit_revision(self.store, self.envelope,
                                          self._collected())
        self.assertEqual(first["canonical_revision"], 1)
        self.assertEqual(second["canonical_revision"], 2)
        projections = [evidence.create_projection(
            self.store, second, kind=k, body={"kind": k})
            for k in ("report", "dashboard", "gate")]
        self.assertTrue(evidence.verify_projection_set(
            self.store, second, projections))

    def test_concurrent_commits_compare_and_swap_the_current_revision(self):
        first_collected = self._collected()
        second_collected = self._collected()
        original_read = evidence._read_current
        barrier = threading.Barrier(2)

        def synchronized_read(store):
            current = original_read(store)
            barrier.wait(timeout=5)
            return current

        with mock.patch.object(evidence, "_read_current",
                               side_effect=synchronized_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(evidence.commit_revision, self.store,
                                self.envelope, collected)
                    for collected in (first_collected, second_collected)
                ]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=10))
                    except evidence.RevisionError as exc:
                        outcomes.append(exc)

        committed = [row for row in outcomes if isinstance(row, dict)]
        rejected = [row for row in outcomes
                    if isinstance(row, evidence.RevisionError)]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["canonical_revision"], 1)
        self.assertEqual(len(rejected), 1)

    def test_corrupt_current_revision_state_fails_closed(self):
        path = evidence._current_path(self.store)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("{not-json")
        with self.assertRaisesRegex(evidence.RevisionError, "corrupt"):
            evidence.next_revision(self.store)

    def test_mixed_revision_projection_blocks(self):
        first = evidence.commit_revision(self.store, self.envelope,
                                         self._collected())
        second = evidence.commit_revision(self.store, self.envelope,
                                          self._collected())
        stale = evidence.create_projection(self.store, first, kind="report",
                                           body={"kind": "report"})
        with self.assertRaisesRegex(evidence.RevisionError, "identity"):
            evidence.verify_projection_set(self.store, second, [stale])

    def test_report_and_dashboard_render_the_exact_canonical_tuple(self):
        revision = evidence.commit_revision(self.store, self.envelope,
                                            self._collected())
        identity = evidence.revision_identity(revision)
        report = views.canonical_report_projection("# report", identity)
        self.assertEqual(report["identity"], identity)
        html = dashboard.render_findings([], {"revision_identity": identity})
        for value in identity.values():
            self.assertIn(str(value), html)

    def test_incomplete_dashboard_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "revision identity"):
            dashboard.render_findings([], {"revision_identity": {
                "target_fingerprint": "target-1"}})


if __name__ == "__main__":
    unittest.main()
