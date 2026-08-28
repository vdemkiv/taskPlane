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
import os
import sys
import unittest

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

    def test_the_frozen_block_rule_still_owns_which_findings_block(self):
        """The bar takes the predicate as an argument — it must never grow
        its own opinion about what blocks."""
        src = open(dc.__file__, encoding="utf-8").read()
        self.assertNotIn("regression", src.split('"""', 2)[-1],
                         "defect_claim must not reimplement the class rule")


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


class TestWiredIntoTheEmGate(unittest.TestCase):
    def test_the_gate_consults_the_bar(self):
        import audit
        src = open(audit.__file__, encoding="utf-8").read()
        self.assertIn("defect_claim.blocking_errors", src)

    def test_the_gate_passes_the_frozen_predicate(self):
        """The bar must never grow its own opinion about what blocks — the
        em gate hands it loop.finding_blocks."""
        import audit
        src = open(audit.__file__, encoding="utf-8").read()
        self.assertIn("loop.finding_blocks(f, changed)", src)

    def test_loop_calls_it_at_the_review_gate(self):
        src = open(loop.__file__, encoding="utf-8").read()
        self.assertIn("_blocking_claim_errors(ws, state, rows)", src)


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

    def test_both_runners_opt_into_the_scoped_guard(self):
        """Both runners request isolation without making package import
        mutate process-global state.  Unittest enters through ``load_tests``;
        pytest requests the lazy compatibility bootstrap from conftest."""
        import taskplane.tests as pkg
        src = open(pkg.__file__, encoding="utf-8").read()
        conftest = open(os.path.join(os.path.dirname(pkg.__file__),
                                    "conftest.py"), encoding="utf-8").read()
        self.assertIn("def isolated_test_runtime", src)
        self.assertIn("class _RunnerScopedSuite", src)
        self.assertIn("def load_tests", src)
        self.assertIn("tempfile.tempdir = tmp_root", src)
        self.assertIn("tempfile.tempdir = saved_tempdir", src)
        self.assertIn("atexit.register", src)
        self.assertIn("from taskplane.tests import _SESSION_HOME", conftest)
        self.assertNotIn("tempfile.tempdir = _TMP_ROOT", src)


if __name__ == "__main__":
    unittest.main()
