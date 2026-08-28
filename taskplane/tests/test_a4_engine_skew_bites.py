"""A4 repair (EM, v3 phase 3) — the guardrail shipped inert.

A4 exists to refuse a gate whose evidence was produced by a different engine
build than the one validating it. It stamped `tp.engine_fingerprint()`, which
hashes the engine THIS PROCESS loaded — so `submit` attested the SUBMITTING
process, not the engine that produced the evidence. Every skill resolves the
CLI through one installed plugin root, so on every documented path producer
and validator were the same build and the refusal could never fire.

The original A4 tests all passed. They asserted the comparison behaved
correctly GIVEN two fingerprints; none asserted that the two fingerprints
could ever actually differ on a real topology. That is the gap these tests
close: the load-bearing case here is a wave worktree carrying its own edited
engine, which is the exact Phase 2 skew (t7) A4 was built for.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402


ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _EngineWorkspaces(unittest.TestCase):
    """Two checkouts that each carry their own copy of the engine — the
    dogfooding / wave-worktree topology."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.primary = self._checkout("primary")
        self.worktree = self._checkout("worktree")

    def _checkout(self, name):
        ws = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(ws, "taskplane"))
        for mod in tp.VALIDATOR_SURFACE:
            src = os.path.join(ENGINE_DIR, mod + ".py")
            if os.path.exists(src):
                shutil.copy(src, os.path.join(ws, "taskplane", mod + ".py"))
        subprocess.run(["git", "init", "-q"], cwd=ws, capture_output=True)
        return ws

    def _edit_engine(self, ws, module="loop"):
        path = os.path.join(ws, "taskplane", module + ".py")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n# a wave worker changed the engine\n")


class TestTheFingerprintCanActuallyDiffer(_EngineWorkspaces):
    def test_two_identical_checkouts_fingerprint_the_same(self):
        """One build checked out twice is the SAME build — bytes, not paths."""
        self.assertEqual(tp.workspace_engine_fingerprint(self.primary),
                         tp.workspace_engine_fingerprint(self.worktree))

    def test_an_edited_engine_fingerprints_differently(self):
        """The property the old stamp could never express."""
        self._edit_engine(self.worktree)
        self.assertNotEqual(tp.workspace_engine_fingerprint(self.primary),
                            tp.workspace_engine_fingerprint(self.worktree))

    def test_a_workspace_with_no_engine_copy_returns_none(self):
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        self.assertIsNone(tp.workspace_engine_fingerprint(bare))

    def test_a_directory_without_the_surface_returns_none(self):
        """A `taskplane/` directory holding none of the surface is not an
        engine — returning a hash for it would fabricate an identity."""
        odd = os.path.join(self.tmp, "odd")
        os.makedirs(os.path.join(odd, "taskplane"))
        open(os.path.join(odd, "taskplane", "readme.txt"), "w", encoding="utf-8").write("x")
        self.assertIsNone(tp.workspace_engine_fingerprint(odd))

    def test_a_truncated_engine_is_not_equal_to_a_complete_one(self):
        os.remove(os.path.join(self.worktree, "taskplane", "audit.py"))
        self.assertNotEqual(tp.workspace_engine_fingerprint(self.primary),
                            tp.workspace_engine_fingerprint(self.worktree))


class TestTheRefusalNowFires(_EngineWorkspaces):
    def _submission(self, produced_in):
        return {"task": "t7", "outcome": "pass",
                "engine_fingerprint": tp.engine_fingerprint(),
                "evidence_engine_fingerprint":
                    tp.workspace_engine_fingerprint(produced_in)}

    def test_the_phase2_skew_is_refused(self):
        """THE case A4 was built for: a worktree edited engine files, its
        evidence is gated by the primary. Before this repair the refusal
        returned None here — both stamps came from one installed plugin."""
        self._edit_engine(self.worktree)
        out = tp.engine_skew_refusal(
            self.primary, self._submission(self.worktree))
        self.assertIsNotNone(out, "the guardrail must fire on real skew")
        self.assertEqual(out["engine_skew"]["reason"], "engine_skew_workspace")

    def test_the_refusal_names_two_different_hashes(self):
        """A message quoting one hash twice reads like a bug in the gate."""
        self._edit_engine(self.worktree)
        out = tp.engine_skew_refusal(
            self.primary, self._submission(self.worktree))
        skew = out["engine_skew"]
        self.assertNotEqual(skew["submitted"], skew["validator"])
        self.assertIn(str(skew["submitted"])[:12], out["error"])
        self.assertIn(str(skew["validator"])[:12], out["error"])

    def test_matching_engines_still_pass_cleanly(self):
        """No new refusal for the ordinary case — the flow stays unchanged
        where nothing actually diverged."""
        self.assertIsNone(tp.engine_skew_refusal(
            self.primary, self._submission(self.worktree)))

    def test_a_workspace_without_an_engine_copy_does_not_refuse(self):
        """A repo that merely USES taskplane has no second engine to differ
        from — absent must not be treated as skew."""
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        self.assertIsNone(tp.engine_skew_refusal(bare, self._submission(bare)))

    def test_the_running_engine_check_still_refuses_an_absent_stamp(self):
        """The pre-existing fail-closed behavior is untouched."""
        out = tp.engine_skew_refusal(self.primary, {"task": "t1"})
        self.assertIsNotNone(out)
        self.assertEqual(out["engine_skew"]["reason"], "engine_skew")

    def test_the_refusal_is_traced_with_the_reason(self):
        self._edit_engine(self.worktree)
        tp.engine_skew_refusal(self.primary, self._submission(self.worktree))
        with open(os.path.join(tp.tp_dir(self.primary), "trace.jsonl"), encoding="utf-8") as f:
            events = [json.loads(x) for x in f if x.strip()]
        blocked = [e for e in events if e.get("event") == "loop_gate_blocked"]
        self.assertTrue(blocked)
        self.assertEqual(
            blocked[-1]["reason"], tp._audit_minimized(
                "engine_skew_workspace"))


class TestSubmitStampsTheEvidenceEngine(unittest.TestCase):
    def test_submit_selects_and_records_the_evidence_producer_engine(self):
        """The stamp the gate needs must be in the record, or the repair is
        a function nobody calls."""
        import loop
        src = open(loop.__file__, encoding="utf-8").read()
        self.assertIn("evidence_engine_fingerprint", src)
        self.assertIn("_submission_evidence_engine_workspace(", src)
        self.assertIn(
            "tp.workspace_engine_fingerprint(evidence_engine_ws)", src)


if __name__ == "__main__":
    unittest.main()
