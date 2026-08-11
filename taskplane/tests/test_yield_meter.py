"""Yield meter — the properties that would make it LIE or make it COST.

Six cases, and deliberately only six. This module is an instrument, not a
guardrail, so most of its surface is arithmetic that a broken test would
catch no faster than looking at the output. What is worth pinning is the
handful of properties whose failure would be invisible:

  1. fingerprints survive a line-number shift (else every finding looks new
     and recurrence — the whole inference half — measures nothing)
  2. a retried gate records nothing new (else the meter rewards flakiness:
     re-run a gate three times and a lens looks three times as productive)
  3. explicit and inferred verdicts never blend (the honesty property the
     module exists for — inference is weak and must stay labelled weak)
  4. "no later review yet" reports as unknown, not as zero-acted, which
     would slander a lens that has simply not been judged yet
  5. counted-only blockers never reach the acted/dismissed columns, because
     they have no identity to disposition
  6. THE SAFETY PROPERTY: a broken ledger never costs anyone a gate

Every assertion here was observed FAILING before it was kept.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yield_meter as ym  # noqa: E402


class _Ledgered(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-yield-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = tempfile.mkdtemp(prefix="tp-yield-ws-")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _review(self, findings, routed=None, meta_extra=None):
        d = os.path.join(self.ws, ".em-review")
        os.makedirs(d, exist_ok=True)
        meta = {"routed": routed or []}
        meta.update(meta_extra or {})
        with open(os.path.join(d, "findings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"findings": findings, "meta": meta}, f)


F_AUTHZ = {"lens": "security", "file": "src/a.py", "severity": "high",
           "title": "authz gap on the export path at line 12"}
F_I18N = {"lens": "i18n", "file": "src/b.tsx", "severity": "low",
          "title": "hardcoded string"}


class TestFingerprintSurvivesEdits(_Ledgered):
    def test_line_number_shift_does_not_make_a_new_finding(self):
        moved = dict(F_AUTHZ, title="authz gap on the export path at line 480")
        self.assertEqual(ym.fingerprint(F_AUTHZ), ym.fingerprint(moved),
                         "a finding that moved down the file must stay the "
                         "same finding, or recurrence measures nothing")

    def test_meaningful_numbers_are_not_collapsed(self):
        a = dict(F_AUTHZ, title="timeout 30s is too short")
        b = dict(F_AUTHZ, title="timeout 300s is too short")
        self.assertNotEqual(ym.fingerprint(a), ym.fingerprint(b),
                            "stripping every digit would merge two distinct "
                            "claims into one row")

    def test_same_claim_from_a_different_lens_is_a_different_finding(self):
        self.assertNotEqual(ym.fingerprint(F_AUTHZ),
                            ym.fingerprint(dict(F_AUTHZ, lens="backend")))


class TestGateRetryRecordsNothingNew(_Ledgered):
    """Gates get retried. A meter that counted each retry would report a
    lens as more productive the flakier the gate around it was."""

    def test_regating_identical_findings_is_idempotent(self):
        self._review([F_AUTHZ, F_I18N], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        first = len(ym.read_ledger(self.ws))
        self.assertGreater(first, 0, "nothing was recorded at all")
        for _ in range(3):
            ym.gate_snapshot(self.ws, "em", "pass")
        self.assertEqual(len(ym.read_ledger(self.ws)), first)
        self.assertEqual(ym.report(self.ws)["findings"], 2)

    def test_a_changed_review_does_record(self):
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        self._review([F_AUTHZ, F_I18N], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        self.assertEqual(ym.report(self.ws)["reviews"], 2)


class TestExplicitAndInferredNeverBlend(_Ledgered):
    def test_stopped_recurring_is_never_counted_as_acted(self):
        self._review([F_AUTHZ, F_I18N], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        self._review([F_AUTHZ], routed=["security", "i18n"])   # i18n dropped
        ym.gate_snapshot(self.ws, "em", "pass")
        rows = {r["lens"]: r for r in ym.report(self.ws)["lenses"]}
        self.assertEqual(rows["i18n"]["stopped_recurring"], 1)
        self.assertEqual(rows["i18n"]["acted"], 0,
                         "inference must never be promoted to a human "
                         "verdict — that is the whole honesty property")

    def test_an_explicit_verdict_wins_over_inference(self):
        self._review([F_AUTHZ, F_I18N], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        ym.record_disposition(self.ws, ym.fingerprint(F_I18N), "dismissed")
        self._review([F_AUTHZ], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        rows = {r["lens"]: r for r in ym.report(self.ws)["lenses"]}
        self.assertEqual(rows["i18n"]["dismissed"], 1)
        self.assertEqual(rows["i18n"]["stopped_recurring"], 0)

    def test_a_bad_verdict_is_refused(self):
        self.assertIn("error", ym.record_disposition(self.ws, "abc", "maybe"))
        self.assertIn("error", ym.record_disposition(self.ws, "", "acted"))


class TestUnjudgedIsUnknownNotZero(_Ledgered):
    def test_a_finding_with_no_later_review_reports_unknown(self):
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        row = {r["lens"]: r for r in ym.report(self.ws)["lenses"]}["security"]
        self.assertEqual(row["unknown"], 1)
        self.assertEqual((row["acted"], row["dismissed"],
                          row["stopped_recurring"], row["persisted"]),
                         (0, 0, 0, 0),
                         "a finding nobody has judged yet must not land in "
                         "any bucket that reads as a verdict")

    def test_a_lens_that_never_fired_is_not_on_the_deletion_shortlist(self):
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        self.assertEqual(ym.zero_yield(ym.report(self.ws)), [],
                         "one firing is not evidence of anything")


class TestCountedBlockersStayCounts(_Ledgered):
    """The evaluate verdict reports `blockers: N` per lens — a number, not a
    list. Those shape the escape picture but have no identity."""

    def test_verdict_counts_reach_escape_but_never_a_verdict_column(self):
        d = os.path.join(self.ws, ".eval")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as f:
            json.dump({"lenses": [{"lens": "security", "blockers": 2},
                                  {"lens": "qa", "blockers": 0}]}, f)
        ym.gate_snapshot(self.ws, "evaluate", "pass")
        rep = ym.report(self.ws)
        self.assertEqual(rep["escape"]["in_task"], 2)
        self.assertEqual(rep["counted_only"], 2)
        row = {r["lens"]: r for r in rep["lenses"]}["security"]
        self.assertEqual(row["blockers"], 2)
        self.assertEqual((row["acted"], row["dismissed"], row["findings"]),
                         (0, 0, 0),
                         "a count has no identity, so it can never be "
                         "dispositioned and must not look as if it were")
        self.assertEqual(rep["open_blockers"], [],
                         "counts must not appear on the marking worklist — "
                         "there is nothing to mark")


class TestABrokenLedgerNeverCostsAGate(_Ledgered):
    """THE safety property. This module is allowed to be wrong; it is not
    allowed to be expensive. Every write is best-effort, and `gate_snapshot`
    sits on the one line every gate transition passes through."""

    def test_unreadable_store_does_not_raise(self):
        self._review([F_AUTHZ], routed=["security"])
        with mock.patch.object(ym, "ledger_path",
                                        side_effect=OSError("disk gone")):
            ym.gate_snapshot(self.ws, "em", "pass")          # must not raise
            self.assertEqual(ym.read_ledger(self.ws), [])

    def test_corrupt_ledger_lines_are_skipped_not_fatal(self):
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        with open(ym.ledger_path(self.ws), "a", encoding="utf-8") as f:
            f.write("{not json at all\n\n")
        self.assertEqual(ym.report(self.ws)["findings"], 1)

    def test_garbage_findings_artifact_does_not_raise(self):
        d = os.path.join(self.ws, ".em-review")
        os.makedirs(d, exist_ok=True)
        for junk in ("", "[[[", '{"findings": "not a list"}', '{"findings": [1, 2]}'):
            with self.subTest(junk=junk[:20]):
                with open(os.path.join(d, "findings.json"), "w",
                          encoding="utf-8") as f:
                    f.write(junk)
                ym.gate_snapshot(self.ws, "em", "pass")      # must not raise

    def test_an_exception_anywhere_inside_is_swallowed(self):
        """The outer guard itself, not the inner readers.

        `test_garbage_findings_artifact_does_not_raise` passes even with the
        guard removed, because `_load` and `_artifact` are already total —
        so it proves those, not the net. Mutation testing caught that; this
        case forces a raise the inner code cannot absorb.
        """
        self._review([F_AUTHZ], routed=["security"])
        with mock.patch.object(ym, "record_findings",
                               side_effect=RuntimeError("boom")):
            ym.gate_snapshot(self.ws, "em", "pass")          # must not raise

    def test_the_engine_never_reads_the_ledger(self):
        """If the loop ever consumed this, a bad ledger could change a
        verdict — and the module's central promise would be false."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("loop.py", "taskplane_lite.py", "audit.py",
                     "evidence.py", "lens.py"):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            with self.subTest(module=name):
                src = open(path, encoding="utf-8").read()
                for banned in ("yield_meter.report", "yield_meter.read_ledger",
                               "yield_meter.zero_yield"):
                    self.assertNotIn(
                        banned, src,
                        f"{name} READS the yield ledger. The meter records; "
                        "nothing in the engine may depend on it, or a broken "
                        "ledger becomes a broken gate.")


class TestTheFourRegressionsStayFixed(_Ledgered):
    """Four defects the whole-codebase review found in this module, all of
    which shipped the same day it did. Each is pinned by the case the
    original tests never drove."""

    def test_a_review_is_not_re_recorded_at_later_gates(self):
        """THE one that mattered: `_artifact` took a `step` and ignored it,
        so every later gate re-read .em-review/findings.json — inflating
        the count AND landing the copies in the CHEAP in-task bucket, i.e.
        moving the headline metric in the flattering direction."""
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        before = ym.report(self.ws)
        for step in ("fix", "evaluate", "execute"):
            ym.gate_snapshot(self.ws, step, "pass")
        after = ym.report(self.ws)
        self.assertEqual(after["findings"], before["findings"])
        self.assertEqual(after["escape"], before["escape"])
        self.assertEqual(after["escape"]["in_task"], 0)

    def test_the_engine_step_ids_are_the_ones_in_buckets(self):
        """`em` is the loop's review step; `review` is not a step it emits.
        Listing the wrong name made at_review a FALLTHROUGH result, so the
        headline depended on the default rather than on the data."""
        self.assertIn("em", ym.BUCKETS["at_review"])
        self.assertIn("fix", ym.BUCKETS["in_task"])
        self.assertNotIn("review", ym.KNOWN_STEPS,
                         "'review' is not a loop step — listing it is what "
                         "made the bucketing accidental")

    def test_em_buckets_by_declaration_not_by_default(self):
        """Proves it is not the fallthrough doing the work: an unknown step
        and `em` must not be indistinguishable."""
        self.assertEqual(ym._bucket("em"), "at_review")
        self.assertEqual(ym._bucket("fix"), "in_task")
        self.assertNotIn("totally-unknown-step", ym.KNOWN_STEPS)

    def test_recurrence_is_judged_per_lens_not_globally(self):
        """A security finding must not be marked stopped_recurring because
        some LATER review that routed no security lens happened to land."""
        self._review([F_AUTHZ], routed=["security"])
        ym.gate_snapshot(self.ws, "em", "pass")
        # a later review in which security did NOT fire
        self._review([F_I18N], routed=["i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        rows = {r["lens"]: r for r in ym.report(self.ws)["lenses"]}
        self.assertEqual(rows["security"]["stopped_recurring"], 0,
                         "a review that never ran this lens says nothing "
                         "about whether its finding recurred")
        self.assertEqual(rows["security"]["unknown"], 1)

    def test_recurrence_still_fires_when_the_lens_did_run_again(self):
        """The complement — otherwise the fix above could be 'never infer'."""
        self._review([F_AUTHZ, F_I18N], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        self._review([F_AUTHZ], routed=["security", "i18n"])
        ym.gate_snapshot(self.ws, "em", "pass")
        rows = {r["lens"]: r for r in ym.report(self.ws)["lenses"]}
        self.assertEqual(rows["i18n"]["stopped_recurring"], 1)

    def test_the_dead_ordering_constant_is_gone(self):
        """CAUGHT_ORDER was defined, documented as authoritative, read by
        nothing, and named a step the engine does not emit."""
        self.assertFalse(hasattr(ym, "CAUGHT_ORDER"))



if __name__ == "__main__":
    unittest.main()
