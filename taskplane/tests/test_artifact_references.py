"""R-0005 large-artifact references are content-addressed and tamper evident."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_evidence as evidence  # noqa: E402


class TestArtifactReferences(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-artifacts-")
        self.store = evidence.ArtifactStore(self.ws)

    def test_identical_large_artifact_is_written_once_and_returned_by_reference(self):
        payload = {"html": "x" * 20000}
        first = self.store.put("dashboard", payload)
        second = self.store.put("dashboard", payload)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.references("dashboard")), 1)
        self.assertNotIn("html", first)
        self.assertLess(len(json.dumps(first)), 1024)

    def test_altered_artifact_fails_digest_verification(self):
        ref = self.store.put("findings", {"findings": [{"id": "A"}]})
        with open(ref["path"], "w", encoding="utf-8") as f:
            f.write("{}")
        with self.assertRaisesRegex(evidence.ArtifactIntegrityError, "digest"):
            self.store.verify(ref)

    def test_reference_outside_store_is_rejected(self):
        outside = os.path.join(self.ws, "outside.json")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("{}")
        ref = {"kind": "findings", "path": outside,
               "fingerprint": "0" * 64, "bytes": 2,
               "transport": "artifact-reference"}
        with self.assertRaisesRegex(evidence.ArtifactIntegrityError, "outside"):
            self.store.verify(ref)

    def test_interrupted_publish_never_exposes_partial_final_artifact(self):
        payload = {"findings": [{"id": "A"}]}
        identity = evidence.content_fingerprint(payload)
        final_path = self.store._path("findings", identity)
        with mock.patch.object(evidence.os, "replace",
                               side_effect=OSError("simulated crash")):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                self.store.put("findings", payload)
        self.assertFalse(os.path.exists(final_path))

        recovered = self.store.put("findings", payload)
        self.assertEqual(recovered["path"], final_path)
        self.assertTrue(self.store.verify(recovered))
        leftovers = [name for name in os.listdir(os.path.dirname(final_path))
                     if name.startswith(f".{identity}.") and name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
