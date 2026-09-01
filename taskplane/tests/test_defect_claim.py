"""Defect-claim bar (R-0013) — commentary may not block a gate.

The v3 phase 3 review filed twenty-one findings, of which roughly seven were
not defects at all, while a guardrail that had shipped completely inert sat
in the same pile classed as an observation. Severity and class were both
unreliable, in both directions, and everything rendered identically.

These tests pin the two properties that make the bar honest:

  1. It is STRUCTURAL, not textual. An earlier cut scored prose for words
     like "verified" and "measured"; against the real corpus it kept a
     byte-count measurement and downgraded a reproduced HIGH. There is a
     test below that would fail if anyone reintroduces prose scoring.
  2. It can only make blocking HARDER. Nothing that blocks today stops
     blocking; the frozen finding_blocks rule still decides which findings
     block at all.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import defect_claim as dc  # noqa: E402
import loop  # noqa: E402


CLAIM = {"trigger": "gate a submission whose stamp is absent",
         "outcome": "the gate advances instead of refusing the evidence",
         "repro": "run tp loop gate pass with no submission record"}


def _f(**kw):
    base = {"severity": "high", "class": "regression", "title": "a thing"}
    base.update(kw)
    return base


class TestTheBarIsStructuralNotTextual(unittest.TestCase):
    def test_a_claim_block_is_required_not_inferred_from_prose(self):
        """The load-bearing property. Prose that SOUNDS like evidence —
        'Verified live', 'measured: n=8 -> 56.5px' — must not satisfy the
        bar, because a well-measured observation is still not a defect."""
        prosey = _f(scenario="Verified live: measured byte deltas show the "
                             "skill payload grew 4.1KB net, reproduced on "
                             "every invocation")
        self.assertFalse(dc.is_defect_claim(prosey))

    def test_a_thin_claim_is_rejected_field_by_field(self):
        errors = dc.claim_errors(_f(claim={"trigger": "x", "outcome": "y",
                                           "repro": "z"}))
        self.assertEqual(len(errors), 3)
        for field in dc.REQUIRED:
            self.assertTrue(any("claim." + field in e for e in errors), field)

    def test_a_complete_claim_clears_the_bar(self):
        self.assertTrue(dc.is_defect_claim(_f(claim=CLAIM)))

    def test_a_one_line_repro_is_not_rejected(self):
        """A bar that demanded an essay would just move the noise."""
        self.assertTrue(dc.is_defect_claim(_f(claim=dict(
            CLAIM, repro="run tp loop gate pass in a worktree"))))


class TestItOnlyMakesBlockingHarder(unittest.TestCase):
    def test_a_blocking_finding_without_a_claim_is_refused(self):
        errors = dc.blocking_errors([_f()], lambda f: True)
        self.assertEqual(len(errors), 1)
        self.assertIn("without a defect claim", errors[0])

    def test_a_blocking_finding_with_a_claim_passes(self):
        self.assertEqual(dc.blocking_errors([_f(claim=CLAIM)],
                                            lambda f: True), [])

    def test_a_non_blocking_finding_owes_nothing(self):
        """Commentary is welcome — it just cannot block. An observation with
        no claim must not produce an error."""
        self.assertEqual(dc.blocking_errors([_f(class_="observation")],
                                            lambda f: False), [])

    def test_a_note_that_blocks_is_its_own_refusal(self):
        errors = dc.blocking_errors([_f(kind="note")], lambda f: True)
        self.assertEqual(len(errors), 1)
        self.assertIn("filed as a note but blocks", errors[0])

    def test_it_never_reports_an_error_for_something_that_does_not_block(self):
        rows = [_f(), _f(kind="note"), _f(claim=CLAIM), {"junk": True}]
        self.assertEqual(dc.blocking_errors(rows, lambda f: False), [])

class TestPartitionKeepsEverything(unittest.TestCase):
    def test_nothing_is_dropped(self):
        rows = [_f(claim=CLAIM), _f(), _f(kind="note")]
        out = dc.partition(rows)
        self.assertEqual(
            len(out["findings"]) + len(out["unclaimed"]) + len(out["notes"]),
            len(rows))

    def test_a_note_loses_its_severity(self):
        out = dc.partition([_f(kind="note", severity="high")])
        self.assertNotIn("severity", out["notes"][0])

    def test_an_unclaimed_row_keeps_its_severity_and_is_not_a_note(self):
        """Unclaimed is not the same as commentary — the reviewer may simply
        not have written the block yet, and silently restyling their finding
        as a note would be the engine making their call for them."""
        out = dc.partition([_f(severity="high")])
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["unclaimed"][0]["severity"], "high")


class TestEngineeringGateBehaviorJourney(unittest.TestCase):
    """Exercise the public EM transition, not implementation source text."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "README.md"), "w",
                  encoding="utf-8") as stream:
            stream.write("reviewed product\n")
        for command in (
                ("git", "init", "-q"),
                ("git", "config", "user.email", "test@example.invalid"),
                ("git", "config", "user.name", "TaskPlane Test"),
                ("git", "add", "README.md"),
                ("git", "commit", "-qm", "reviewed revision")):
            subprocess.run(command, cwd=self.ws, check=True)

        state_dir = os.path.join(self.tmp, "state")
        state_patch = mock.patch.object(loop, "_state_dir",
                                        return_value=state_dir)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        loop.save(self.ws, {
            "schema": "taskplane.run/v3",
            "run_id": "defect-claim-gate-journey",
            "goal": "exercise the Engineering claim gate",
            "step": "em",
            "baseline": loop.tp.git_head(self.ws),
            "tasks": [],
            "current_task": 0,
            "max_fix_cycles": 2,
            "checkpoints": ["em"],
        })
        review_dir = os.path.join(self.ws, ".em-review")
        os.makedirs(review_dir)
        with open(os.path.join(review_dir, "report.md"), "w",
                  encoding="utf-8") as stream:
            stream.write("# Engineering review\n\nEvidence checked.\n")

    def _write_findings(self, rows):
        coverage = {
            entry["id"]: "sweep"
            for entry in loop.lens_router.load_catalog()["lenses"]
        }
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  "w", encoding="utf-8") as stream:
            json.dump({
                "meta": {
                    "lens_coverage": coverage,
                    "impact": {"touched": []},
                    "tests": ["focused gate journey: pass"],
                    "gate": {"verdict": "recommend-pass"},
                },
                "findings": rows,
            }, stream)

    def _gate(self):
        binding = {"workspace": self.ws, "run_id": "review-run"}
        with mock.patch.object(loop, "review_kernel_binding",
                               return_value=binding), \
                mock.patch("review._load_state", return_value={
                    "status": "complete", "stage": "review"}), \
                mock.patch("review_evidence._read_current", return_value={}), \
                mock.patch.object(loop.kb, "lint", return_value=[]), \
                mock.patch.object(loop, "record_audit_review", return_value=1), \
                mock.patch.object(loop, "audit_due", return_value=False), \
                mock.patch.object(loop.tp, "trace"), \
                mock.patch.object(loop.tp, "release_worker_contracts_for_gate",
                                  return_value=["em-contract"]), \
                mock.patch.object(loop.yield_meter, "gate_snapshot"):
            return loop.gate(self.ws, "pass")

    def test_blocking_finding_without_complete_claim_refuses_transition(self):
        self._write_findings([_f(severity="low", claim={
            "trigger": CLAIM["trigger"],
            "outcome": CLAIM["outcome"],
        })])

        result = self._gate()

        self.assertIn("engineering review is incomplete", result["error"])
        self.assertTrue(any("claim.repro" in error
                            for error in result["dod"]["errors"]))
        self.assertEqual(loop.load(self.ws)["step"], "em")

    def test_complete_current_claim_advances_to_signoff(self):
        self._write_findings([_f(severity="low", claim=CLAIM)])

        result = self._gate()

        self.assertNotIn("error", result)
        self.assertEqual(result["step"], "signoff")
        self.assertEqual(loop.load(self.ws)["step"], "signoff")

    def test_nonblocking_observation_remains_nonblocking(self):
        self._write_findings([_f(severity="low", **{
            "class": "observation",
        })])

        result = self._gate()

        self.assertNotIn("error", result)
        self.assertEqual(loop.load(self.ws)["step"], "signoff")


class TestTheSuiteDoesNotLeakTempDirs(unittest.TestCase):
    """The leak that filled this project's container: test modules call
    tempfile.mkdtemp() in setUp and mostly never remove the result. It
    reached 185,541 directories and about 30 GB before anything failed —
    and every review passed over it, because a single run looks fine.

    The guard is one session root that everything lands in and that is
    removed at exit."""

    def test_every_mkdtemp_lands_under_the_session_root(self):
        import shutil
        import tempfile as tf
        import taskplane.tests as pkg
        made = tf.mkdtemp()
        self.addCleanup(shutil.rmtree, made, True)
        self.assertTrue(made.startswith(pkg._TMP_ROOT), made)

    def test_named_temp_files_land_there_too(self):
        import tempfile as tf
        import taskplane.tests as pkg
        with tf.NamedTemporaryFile() as fh:
            self.assertTrue(fh.name.startswith(pkg._TMP_ROOT), fh.name)

    def test_the_root_exists_and_is_ours_to_remove(self):
        import taskplane.tests as pkg
        self.assertTrue(os.path.isdir(pkg._TMP_ROOT))
        self.assertIn("tp-tests-", pkg._TMP_ROOT)

    def test_the_root_still_sits_under_the_system_temp_dir(self):
        """A test asserting a /tmp prefix must keep passing."""
        import tempfile as tf
        import taskplane.tests as pkg
        self.assertTrue(
            pkg._TMP_ROOT.startswith(os.path.realpath(tf.gettempdir()))
            or pkg._TMP_ROOT.startswith("/tmp"))

if __name__ == "__main__":
    unittest.main()
