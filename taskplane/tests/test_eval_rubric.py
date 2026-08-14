"""The rubric scorer: a scenario plus a record becomes a verdict per step.

WHAT IS BEING PROVED. `eval_rubric.evaluate(scenario, record)` answers, for
one recorded run, the rows `evals/scenarios/<skill>.json` declares — each row
`pass`, `fail`, `no_evidence` or `n/a`, with the evidence or the reason. It
does that generically: the vocabulary of checks, records, selectors and
anchors is `eval_scenario`'s, and the scorer never learns that a skill named
`tp-engineering` exists. `TestTheScorerIsGeneric` reads its own source and
fails if it ever does.

THE INVARIANT THAT OUTRANKS EVERY OTHER ASSERTION HERE. An absent producing
record is `no_evidence`, NEVER `pass`. This is not a nicety — it is the exact
defect that failed the last evaluation of this layer. `repeats == 0` is
arithmetically true over zero rows, so a run whose derivation ledger was
never written scored a PERFECT efficiency result: the instrument's own
failure read back as compliance, which is the one thing an instrument may
never do. `TestAnAbsentRecordIsNeverPass` walks EVERY check kind the scorer
implements, over an absent, an empty and an unreadable record, and demands
`no_evidence` from all of them — so the invariant cannot be re-broken by
adding a ninth check that forgets it.

Three further vacuities are closed the same way, because each is a claim
resting on nothing:

  * a `before`/`after` whose ANCHOR has no rows — the run never reached the
    reference point, so "it happened first" is unanswerable, not true;
  * a `repeats` over zero derivation rows — nothing was derived, so nothing
    could have been re-derived;
  * a derivation ledger carrying no pre-flight probe row — with no probe, a
    short ledger cannot be told from a ledger nobody could write, which is
    the same unknown wearing a different hat.

FALSIFIABILITY. A rubric row that can only be green measures nothing, so
every check kind carries a negative fixture under `evals/negative/` that
FAILS it, in the frozen-corpus shape. `TestEveryCheckKindHasANegativeFixture`
is the mechanism: it evaluates the whole negative corpus, collects the
verdict each CONSTRAINT actually got, and fails if any implemented check kind
never produced a `fail` anywhere in it. Adding a check kind without a fixture
fails the suite; deleting a fixture fails the suite.

Every assertion here was observed FAILING before it was kept.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ci_evals                                                # noqa: E402
import derivation                                              # noqa: E402
import eval_rubric as er                                       # noqa: E402
import eval_scenario as es                                     # noqa: E402


# --------------------------------------------------------------- test helpers

def _step(sid="S1", **kw):
    step = {"id": sid, "claim": "a claim a human would recognise"}
    step.update(kw)
    return step


def _scen(*steps):
    return {"schema": es.SCHEMA, "skill": "x", "steps": list(steps)}


def _verdict(scenario, record, sid="S1"):
    return er.evaluate(scenario, record)["verdicts"][sid]


def _one(step, record):
    """The verdict of a one-step scenario."""
    return _verdict(_scen(step), record)


# One evaluable constraint per implemented check kind. The keys are pinned
# against `er.CHECK_KINDS` below, so a ninth check kind cannot be added
# without also being put through the absent-record invariant.
ONE_OF_EACH = {
    "exists": {"record": "trace", "check": "exists",
               "select": {"event": "dod"}},
    "absent": {"record": "trace", "check": "absent",
               "select": {"event": "dod"}},
    "before": {"record": "trace", "check": "before",
               "select": {"event": "dod"}, "before": "completion_claim"},
    "after": {"record": "trace", "check": "after",
              "select": {"event": "dod"}, "after": "first_write"},
    "count": {"record": "trace", "check": "count",
              "select": {"event": "dod"}, "min": 1},
    "repeats": {"record": "derivations", "check": "repeats",
                "select": {"event": "derived", "probe": {"absent": True}},
                "distinct_by": ["key", "input_key"], "max": 0},
    "field_equals": {"record": "trace", "check": "field_equals",
                     "select": {"event": "dod"}, "field": "passed",
                     "value": True},
    "pairs": {"record": "dispatch", "check": "pairs", "select": {},
              "with": {"record": "context",
                       "select": {"kind": "lens_findings"}},
              "key": "lens"},
}


def _compliant():
    """A record that satisfies every row of the REAL tp-engineering scenario.

    Written out in full rather than generated because it doubles as the
    recorder lane's target: this is the row shape, in this order, that a
    governed engineering review has to leave behind.
    """
    return er.record(
        trace=[
            {"ts": 1, "event": "contract_activated"},
            {"ts": 2, "event": "dor", "ready": True},
            {"ts": 5, "event": "review_kernel_started", "target_head": "H",
             "graph_quality_status": "complete", "routing_mode": "selective",
             "routing_complete": True, "dispositions_complete": True,
             "context_fingerprint": "CTX"},
            {"ts": 6, "event": "subagent_start", "lens": "security"},
            {"ts": 7, "event": "subagent_start", "lens": "arch"},
            {"ts": 10, "event": "review_kernel_collected", "revision": 1},
            {"ts": 10.5, "event": "dod", "passed": True},
        ],
        obligations=[],
        dispatch=[
            {"ts": 6, "kind": "review-kernel-slot", "lens": "deep.security",
             "slot_id": "deep.security", "context_fingerprint": "CTX"},
            {"ts": 7, "kind": "review-kernel-slot", "lens": "deep.arch",
             "slot_id": "deep.arch", "context_fingerprint": "CTX"},
        ],
        derivations=[
            {"ts": 0.5, "event": "derived", "key": "impact",
             "input_key": "H|abc", "probe": True, "id": "p-1"},
            {"ts": 4, "event": "derived", "key": "diff", "input_key": "B..H"},
            {"ts": 6, "event": "derived", "key": "impact",
             "input_key": "H|abc"},
        ],
        context=[
            {"ts": 3, "kind": "target", "head": "H", "base": "B"},
            {"ts": 4, "kind": "review_envelope", "fingerprint": "CTX",
             "path": ".em-review/kernel-v2/envelope.json"},
            {"ts": 8, "kind": "slot_result", "slot_id": "deep.security",
             "path": ".em-review/kernel-v2/results/security.json"},
            {"ts": 9, "kind": "slot_result", "slot_id": "deep.arch",
             "path": ".em-review/kernel-v2/results/arch.json"},
        ],
        run={"target_head": "H"},
    )


def _reference():
    return es.load(os.path.join(es.scenario_dir(REPO), "tp-engineering.json"))


def _read(path, name):
    with io.open(os.path.join(path, name), encoding="utf-8") as f:
        return json.load(f)


def _negatives():
    """{name: (scenario, record, expected)} for the whole negative corpus."""
    out = {}
    root = os.path.join(REPO, er.NEGATIVE_DIRNAME)
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        out[name] = (_read(path, "scenario.json"), er.read_record(path),
                     _read(path, "expected.json"))
    return out


# ===================================================== the headline invariant

class TestAnAbsentRecordIsNeverPass(unittest.TestCase):
    """The defect that failed this layer last time, closed for every check.

    `repeats == 0` over an empty ledger is arithmetically true and completely
    meaningless: it says a run that derived nothing repeated nothing. Scored
    as a pass, a missing instrument reads as a perfect result. The rule is
    therefore stated once, over the record, ahead of every check's own logic
    — and asserted here for every check kind rather than for `repeats` alone,
    because the next check to be added will have the same hole.
    """

    def test_the_table_of_sample_constraints_covers_every_check_kind(self):
        """This whole class is only as complete as its sample table."""
        self.assertEqual(set(ONE_OF_EACH), set(er.CHECK_KINDS))

    def test_every_check_kind_over_an_absent_record_is_no_evidence(self):
        blank = er.record()
        for kind, c in sorted(ONE_OF_EACH.items()):
            card = er.evaluate(_scen(_step(**c)), blank)
            self.assertEqual(card["verdicts"]["S1"], "no_evidence",
                             f"{kind} over an absent record")

    def test_every_check_kind_over_an_empty_record_is_no_evidence(self):
        """A record that exists and holds no rows is the SAME unknown."""
        empty = er.record(trace=[], obligations=[], dispatch=[],
                          derivations=[], context=[], run={})
        for kind, c in sorted(ONE_OF_EACH.items()):
            card = er.evaluate(_scen(_step(**c)), empty)
            self.assertEqual(card["verdicts"]["S1"], "no_evidence",
                             f"{kind} over an empty record")

    def test_every_check_kind_over_an_unreadable_record_is_no_evidence(self):
        rec = _compliant()
        rec["unreadable"] = tuple(er.RECORDS)
        for kind, c in sorted(ONE_OF_EACH.items()):
            card = er.evaluate(_scen(_step(**c)), rec)
            self.assertEqual(card["verdicts"]["S1"], "no_evidence",
                             f"{kind} over an unreadable record")

    def test_the_reason_names_the_record_that_was_missing(self):
        """`no_evidence` with no reason is an unknown nobody can act on."""
        card = er.evaluate(_scen(_step(**ONE_OF_EACH["exists"])), er.record())
        self.assertIn("trace", card["steps"][0]["reason"])
        self.assertIn("absent", card["steps"][0]["reason"])

    def test_a_missing_ledger_does_not_score_the_real_efficiency_row(self):
        """The regression, end to end, on the REAL manifest.

        R7 of tp-engineering is the row the complaint was about — "the
        expensive part ran ONCE". Take a fully compliant record, delete only
        the derivation ledger, and R7 must go from `pass` to `no_evidence`.
        A scorer that says `pass` here is the broken instrument reporting
        perfect compliance.
        """
        scenario = _reference()
        good = er.evaluate(scenario, _compliant())
        self.assertEqual(good["verdicts"]["R7"], "pass")

        rec = _compliant()
        rec["rows"]["derivations"] = None
        card = er.evaluate(scenario, rec)
        self.assertEqual(card["verdicts"]["R7"], "no_evidence")
        self.assertEqual(card["derivation_ledger"], "absent")
        self.assertEqual(card["instrument"], "broken")

    def test_an_empty_ledger_is_reported_as_empty_and_not_as_absent(self):
        rec = _compliant()
        rec["rows"]["derivations"] = []
        card = er.evaluate(_reference(), rec)
        self.assertEqual(card["derivation_ledger"], "empty")
        self.assertEqual(card["verdicts"]["R7"], "no_evidence")

    def test_a_ledger_with_no_probe_row_marks_the_instrument_broken(self):
        """The pre-flight probe is what proves the ledger could be written.

        Without it, a ledger holding two rows and a ledger nobody could
        append to are the same picture, so the arithmetic over it is not
        evidence — however clean the arithmetic comes out.
        """
        rec = _compliant()
        rec["rows"]["derivations"] = [
            r for r in rec["rows"]["derivations"] if not r.get("probe")]
        card = er.evaluate(_reference(), rec)
        self.assertEqual(card["instrument"], "broken")
        self.assertEqual(card["derivation_ledger"], "present")
        self.assertEqual(card["verdicts"]["R7"], "no_evidence")
        self.assertIn("probe", card["instrument_reason"])

    def test_a_broken_instrument_does_not_blind_the_other_records(self):
        """The probe certifies the LEDGER, not the trace.

        Marking every row unknown because one record is untrustworthy would
        throw away real evidence — the opposite error, and just as dishonest.
        """
        rec = _compliant()
        rec["rows"]["derivations"] = None
        card = er.evaluate(_reference(), rec)
        self.assertEqual(card["instrument"], "broken")
        self.assertEqual(card["verdicts"]["R8"], "pass")


# ======================================================== the check vocabulary

class TestExists(unittest.TestCase):
    def test_a_matching_row_passes_and_none_fails(self):
        c = ONE_OF_EACH["exists"]
        yes = er.record(trace=[{"ts": 1, "event": "dod"}])
        no = er.record(trace=[{"ts": 1, "event": "dor"}])
        self.assertEqual(_one(_step(**c), yes), "pass")
        self.assertEqual(_one(_step(**c), no), "fail")


class TestAbsent(unittest.TestCase):
    def test_no_matching_row_passes_and_one_fails(self):
        c = ONE_OF_EACH["absent"]
        clean = er.record(trace=[{"ts": 1, "event": "dor"}])
        dirty = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(_step(**c), clean), "pass")
        self.assertEqual(_one(_step(**c), dirty), "fail")


class TestCount(unittest.TestCase):
    def test_the_count_is_checked_against_min_and_max(self):
        rows = [{"ts": 1, "event": "dod"}, {"ts": 2, "event": "dod"}]
        rec = er.record(trace=rows)
        self.assertEqual(_one(_step(record="trace", check="count",
                                    select={"event": "dod"}, min=2), rec),
                         "pass")
        self.assertEqual(_one(_step(record="trace", check="count",
                                    select={"event": "dod"}, min=3), rec),
                         "fail")
        self.assertEqual(_one(_step(record="trace", check="count",
                                    select={"event": "dod"}, max=1), rec),
                         "fail")

    def test_a_count_with_no_bounds_is_no_evidence_not_a_free_pass(self):
        """A row that cannot fail is not a measurement of anything."""
        rec = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(_step(record="trace", check="count",
                                    select={"event": "dod"}), rec),
                         "no_evidence")


class TestBeforeAndAfter(unittest.TestCase):
    """Order, and the two ways an ordering question stops being answerable."""

    def test_order_decides_the_verdict(self):
        c = ONE_OF_EACH["before"]
        early = er.record(trace=[{"ts": 1, "event": "dod"},
                                 {"ts": 2, "event": "loop_submit"}])
        late = er.record(trace=[{"ts": 3, "event": "dod"},
                                {"ts": 2, "event": "loop_submit"}])
        self.assertEqual(_one(_step(**c), early), "pass")
        self.assertEqual(_one(_step(**c), late), "fail")

    def test_after_is_the_mirror(self):
        c = ONE_OF_EACH["after"]
        ok = er.record(trace=[{"ts": 1, "event": "workspace_write"},
                              {"ts": 2, "event": "dod"}])
        bad = er.record(trace=[{"ts": 3, "event": "workspace_write"},
                               {"ts": 2, "event": "dod"}])
        self.assertEqual(_one(_step(**c), ok), "pass")
        self.assertEqual(_one(_step(**c), bad), "fail")

    def test_an_anchor_with_no_rows_is_no_evidence_and_never_pass(self):
        """Lane E's policy call, and it is the same rule as the invariant.

        "the findings file existed before any lens was briefed" is not TRUE
        when no lens was ever briefed — it is unanswerable. Scored as a pass,
        the flow that never reached the control point outranks the flow that
        reached it late.
        """
        rec = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["before"]), rec),
                         "no_evidence")
        self.assertEqual(_one(_step(**ONE_OF_EACH["after"]), rec),
                         "no_evidence")

    def test_a_subject_with_no_rows_fails_because_the_act_is_mandated(self):
        """The asymmetry, stated: the subject is the ACT, the anchor is the
        REFERENCE POINT. A record that works and holds no `dod` row is
        evidence that the gate did not run — not an unknown."""
        rec = er.record(trace=[{"ts": 1, "event": "loop_submit"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["before"]), rec), "fail")

    def test_an_unresolvable_anchor_is_no_evidence(self):
        """An anchor nobody can resolve would otherwise be satisfied by
        absence, which is the shape of every gate that quietly stopped
        gating."""
        rec = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(_step(record="trace", check="before",
                                    select={"event": "dod"},
                                    before="no_such_anchor"), rec),
                         "no_evidence")

    def test_rows_without_a_timestamp_are_unordered_not_ordered(self):
        rec = er.record(trace=[{"event": "dod"},
                               {"ts": 2, "event": "loop_submit"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["before"]), rec),
                         "no_evidence")

    def test_an_inline_anchor_is_resolved_like_a_named_one(self):
        rec = er.record(trace=[{"ts": 1, "event": "dod"},
                               {"ts": 2, "event": "loop_retro"}])
        step = _step(record="trace", check="before", select={"event": "dod"},
                     before={"record": "trace",
                             "select": {"event": "loop_retro"}})
        self.assertEqual(_one(step, rec), "pass")


class TestFieldEquals(unittest.TestCase):
    def test_a_literal_value_is_compared_against_every_matching_row(self):
        c = ONE_OF_EACH["field_equals"]
        ok = er.record(trace=[{"ts": 1, "event": "dod", "passed": True}])
        bad = er.record(trace=[{"ts": 1, "event": "dod", "passed": False}])
        self.assertEqual(_one(_step(**c), ok), "pass")
        self.assertEqual(_one(_step(**c), bad), "fail")

    def test_a_comparand_may_name_another_record(self):
        step = _step(record="trace", check="field_equals",
                     select={"event": "graph_impact"}, field="scanned_head",
                     equals={"record": "context", "select": {"kind": "target"},
                             "field": "head"})
        ok = er.record(trace=[{"ts": 2, "event": "graph_impact",
                               "scanned_head": "H"}],
                       context=[{"ts": 1, "kind": "target", "head": "H"}])
        bad = er.record(trace=[{"ts": 2, "event": "graph_impact",
                                "scanned_head": "OTHER"}],
                        context=[{"ts": 1, "kind": "target", "head": "H"}])
        self.assertEqual(_one(step, ok), "pass")
        self.assertEqual(_one(step, bad), "fail")

    def test_a_comparand_may_name_the_run(self):
        c = dict(_reference()["steps"][0], id="S1")
        good = _compliant()
        self.assertEqual(_one(c, good), "pass")
        wrong = _compliant()
        wrong["run"] = {"target_head": "SOMETHING-ELSE"}
        self.assertEqual(_one(c, wrong), "fail")

    def test_an_absent_run_is_no_evidence_not_a_mismatch(self):
        """No identity for the run is an unknown comparand, not a wrong one."""
        rec = _compliant()
        rec["run"] = None
        c = dict(_reference()["steps"][0], id="S1")
        self.assertEqual(_one(c, rec), "no_evidence")

    def test_zero_matching_rows_is_no_evidence(self):
        rec = er.record(trace=[{"ts": 1, "event": "dor"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["field_equals"]), rec),
                         "no_evidence")

    def test_a_field_no_row_carries_is_no_evidence_not_a_mismatch(self):
        """An unrecorded field is an instrument gap. Scoring it `fail` blames
        the run for what the recorder did not write — the same dishonesty as
        the invariant, pointed the other way."""
        rec = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["field_equals"]), rec),
                         "no_evidence")

    def test_a_field_some_rows_carry_and_one_does_not_fails(self):
        """Once the recorder demonstrably writes the field, a row without it
        is a real gap in the run."""
        rec = er.record(trace=[{"ts": 1, "event": "dod", "passed": True},
                               {"ts": 2, "event": "dod"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["field_equals"]), rec),
                         "fail")

    def test_true_does_not_equal_one(self):
        """JSON booleans and integers are different values; Python's `==`
        disagrees, and a rubric that reads `ready: 1` as `ready: true` grades
        a field the recorder never set."""
        rec = er.record(trace=[{"ts": 1, "event": "dod", "passed": 1}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["field_equals"]), rec),
                         "fail")


class TestPairs(unittest.TestCase):
    def test_every_left_row_needs_its_partner(self):
        c = ONE_OF_EACH["pairs"]
        briefs = [{"ts": 1, "lens": "a"}, {"ts": 2, "lens": "b"}]
        ok = er.record(dispatch=briefs, context=[
            {"ts": 3, "kind": "lens_findings", "lens": "a"},
            {"ts": 4, "kind": "lens_findings", "lens": "b"}])
        bad = er.record(dispatch=briefs, context=[
            {"ts": 3, "kind": "lens_findings", "lens": "a"}])
        self.assertEqual(_one(_step(**c), ok), "pass")
        self.assertEqual(_one(_step(**c), bad), "fail")

    def test_the_key_may_name_a_different_field_on_each_side(self):
        step = _step(record="dispatch", check="pairs", select={},
                     with_={"record": "context",
                            "select": {"kind": "context_file"}})
        step.pop("with_")
        step["with"] = {"record": "context",
                        "select": {"kind": "context_file"}}
        step["key"] = {"left": "context_path", "right": "path"}
        ok = er.record(dispatch=[{"ts": 1, "context_path": "ctx"}],
                       context=[{"ts": 0, "kind": "context_file",
                                 "path": "ctx"}])
        bad = er.record(dispatch=[{"ts": 1, "context_path": "ctx"}],
                        context=[{"ts": 0, "kind": "context_file",
                                  "path": "OTHER"}])
        self.assertEqual(_one(step, ok), "pass")
        self.assertEqual(_one(step, bad), "fail")

    def test_zero_left_rows_is_no_evidence_not_a_vacuous_pass(self):
        """"every brief came back with findings" over zero briefs is the
        vacuous truth again — a review that dispatched nothing is not a
        review that dispatched perfectly."""
        rec = er.record(dispatch=[{"ts": 1, "lens": "a"}],
                        context=[{"ts": 2, "kind": "target"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["pairs"]), rec), "fail")
        none = er.record(dispatch=[], context=[{"ts": 2, "kind": "target"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["pairs"]), none),
                         "no_evidence")

    def test_selective_zero_dispatch_is_proven_not_vacuous(self):
        step = _step(**ONE_OF_EACH["pairs"])
        step["allow_empty_with"] = {
            "record": "trace", "select": {
                "event": "review_kernel_started",
                "routing_complete": True, "slots": []}}
        rec = er.record(
            dispatch=[],
            context=[{"ts": 3, "kind": "target"}],
            trace=[{"ts": 2, "event": "review_kernel_started",
                    "routing_complete": True, "slots": []}])
        self.assertEqual(_one(step, rec), "pass")
        stopped = er.record(
            dispatch=[], context=[{"ts": 3, "kind": "target"}],
            trace=[{"ts": 2, "event": "review_kernel_started",
                    "routing_complete": False, "slots": []}])
        self.assertEqual(_one(step, stopped), "no_evidence")

    def test_a_key_no_left_row_carries_is_no_evidence(self):
        rec = er.record(dispatch=[{"ts": 1}],
                        context=[{"ts": 2, "kind": "lens_findings",
                                  "lens": "a"}])
        self.assertEqual(_one(_step(**ONE_OF_EACH["pairs"]), rec),
                         "no_evidence")


class TestRepeats(unittest.TestCase):
    """The arithmetic belongs to `derivation.repeats()` and stays there."""

    def test_the_scorer_delegates_to_the_derivation_ledger(self):
        """Re-implementing the count is how the probe's false positive comes
        back: probe rows carry the same (key, input_key) as the model's own
        derivation, so a private copy of the arithmetic scores a compliant
        run as one repeat. This asserts the delegation itself — patch
        `derivation.repeats` and the verdict must follow it.
        """
        rec = _compliant()
        step = _step(**ONE_OF_EACH["repeats"])
        self.assertEqual(_one(step, rec), "pass")
        original = derivation.repeats
        try:
            derivation.repeats = lambda ws=None, rows=None: 99
            self.assertEqual(_one(step, rec), "fail")
        finally:
            derivation.repeats = original
        self.assertEqual(_one(step, rec), "pass")

    def test_a_probe_row_is_not_a_repeat(self):
        """The probe derives `impact` at the same head the run does. Counting
        it would fail the very row it exists to protect.

        Asserted with a selector that does NOT exclude probe rows, so the
        exclusion is proved where it actually lives — in
        `derivation.repeats()` — and not in the manifest's own selector. A
        private copy of the arithmetic scores this compliant run as one
        repeat.
        """
        rec = _compliant()
        probes = [r for r in rec["rows"]["derivations"] if r.get("probe")]
        self.assertTrue(probes)
        self.assertEqual(_one(_step(**ONE_OF_EACH["repeats"]), rec), "pass")
        blind = _step(**dict(ONE_OF_EACH["repeats"],
                             select={"event": "derived"}))
        self.assertEqual(_one(blind, rec), "pass")

    def test_a_second_derivation_of_the_same_input_fails(self):
        rec = _compliant()
        rec["rows"]["derivations"].append(
            {"ts": 20, "event": "derived", "key": "diff", "input_key": "B..H"})
        self.assertEqual(_one(_step(**ONE_OF_EACH["repeats"]), rec), "fail")

    def test_a_ledger_of_probe_rows_alone_is_no_evidence(self):
        """Nothing was derived, so nothing could have been re-derived. The
        arithmetic says 0; the honest answer is `no_evidence`."""
        rec = _compliant()
        rec["rows"]["derivations"] = [
            r for r in rec["rows"]["derivations"] if r.get("probe")]
        self.assertEqual(_one(_step(**ONE_OF_EACH["repeats"]), rec),
                         "no_evidence")

    def test_a_distinct_by_the_ledger_cannot_answer_is_no_evidence(self):
        """`derivation.repeats()` distinguishes (key, input_key) and nothing
        else. A row asking for another grouping must say it cannot be
        answered rather than quietly answering a different question."""
        step = _step(**dict(ONE_OF_EACH["repeats"], distinct_by=["host"]))
        self.assertEqual(_one(step, _compliant()), "no_evidence")


# ======================================================= the scorecard itself

class TestTheScorecard(unittest.TestCase):
    def test_the_shape_is_a_vector_of_verdicts_plus_counters(self):
        card = er.evaluate(_reference(), _compliant())
        self.assertEqual(card["schema"], er.SCHEMA)
        self.assertEqual(card["skill"], "tp-engineering")
        self.assertEqual(sorted(card["verdicts"]),
                         ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"])
        self.assertEqual(set(card["counts"]), set(er.VERDICTS))
        self.assertEqual(card["counts"]["pass"], 8)
        self.assertEqual(card["records"]["trace"], "present")
        self.assertEqual(card["derivation_ledger"], "present")
        self.assertEqual(card["instrument"], "ok")

    def test_every_verdict_is_one_of_the_four(self):
        for name, (scenario, rec, _) in _negatives().items():
            card = er.evaluate(scenario, rec)
            for sid, v in card["verdicts"].items():
                self.assertIn(v, er.VERDICTS, f"{name}/{sid}")

    def test_every_step_carries_its_claim_and_a_reason(self):
        card = er.evaluate(_reference(), er.record())
        for step in card["steps"]:
            self.assertTrue(step["claim"])
            self.assertTrue(step["reason"])

    def test_a_step_fails_when_any_of_its_constraints_fails(self):
        """`all` is a conjunction: one CLAIM resting on two facts."""
        rec = _compliant()
        started = next(r for r in rec["rows"]["trace"]
                       if r.get("event") == "review_kernel_started")
        started["routing_complete"] = False
        card = er.evaluate(_reference(), rec)
        self.assertEqual(card["verdicts"]["R5"], "fail")
        kinds = [c["verdict"] for c in card["steps"][4]["constraints"]]
        self.assertEqual(kinds, ["pass", "fail", "pass"])

    def test_a_definite_failure_outranks_an_unknown_within_a_step(self):
        """Evidence of a violation is evidence. An unknown elsewhere in the
        same claim does not launder it into an unknown."""
        step = _step(check="all", record="trace", of=[
            {"check": "exists", "select": {"event": "nothing_like_this"}},
            {"record": "derivations", "check": "repeats",
             "select": {"event": "derived"},
             "distinct_by": ["key", "input_key"], "max": 0},
        ])
        rec = er.record(trace=[{"ts": 1, "event": "dod"}])
        self.assertEqual(_one(step, rec), "fail")

    def test_score_is_pass_over_pass_plus_fail_and_ignores_unknowns(self):
        card = er.evaluate(_reference(), _compliant())
        self.assertEqual(card["score"], 1.0)
        self.assertEqual(er.score(er.evaluate(_reference(), er.record())),
                         None)

    def test_the_scalar_hides_a_regression_that_the_vector_shows(self):
        """WHY the score does not gate: one item improving while another
        regresses leaves the average exactly where it was, and a gate on the
        average would call that no change at all.
        """
        rec = _compliant()
        rec["run"] = {"target_head": "OTHER"}                  # R1 pass->fail
        before = er.evaluate(_reference(), rec)
        rec2 = _compliant()
        next(r for r in rec2["rows"]["context"]
             if r.get("kind") == "review_envelope")["ts"] = 6.5
        after = er.evaluate(_reference(), rec2)                # R3 pass->fail
        self.assertEqual(before["score"], after["score"])
        self.assertNotEqual(before["verdicts"], after["verdicts"])

    def test_the_universal_rubric_is_rolled_up_per_tag(self):
        card = er.evaluate(_reference(), _compliant())
        self.assertEqual(set(card["universal"]), set(es.UNIVERSAL))
        self.assertEqual(card["universal"]["no_rederive"], "pass")
        rec = _compliant()
        rec["rows"]["derivations"] = None
        self.assertEqual(er.evaluate(_reference(), rec)["universal"]
                         ["no_rederive"], "no_evidence")


class TestNotApplicableIsDeclaredNeverInferred(unittest.TestCase):
    """`n/a` is a statement by the SCENARIO, not a verdict the scorer reaches.

    A step the scorer cannot evaluate is `no_evidence`. If it could answer
    `n/a` instead, every unmeasurable row would leave the vector looking
    complete — which is how a control point nobody checked comes to read
    exactly like a control point that passed.
    """

    def test_a_declared_inapplicable_step_is_n_a(self):
        step = _step(record="derivations", check="exists",
                     select={"event": "derived"}, applicable=False,
                     reason="tp-status derives nothing; it is read-only")
        self.assertEqual(_one(step, er.record()), "n/a")

    def test_an_inapplicable_step_with_no_reason_is_not_n_a(self):
        step = _step(record="trace", check="exists", select={"event": "dod"},
                     applicable=False)
        self.assertEqual(_one(step, er.record(trace=[{"ts": 1}])),
                         "no_evidence")

    def test_an_unevaluable_step_is_no_evidence_and_never_n_a(self):
        for step in (_step(record="trace", check="no_such_check", select={}),
                     _step(record="no_such_record", check="exists",
                           select={}),
                     _step(record="trace", check="exists",
                           select={"event": {"no_such_op": 1}}),
                     _step(record="trace", check="all", of=[])):
            self.assertEqual(_one(step, _compliant()), "no_evidence",
                             step["check"])

    def test_the_corpus_never_reaches_n_a_by_accident(self):
        for name, (scenario, rec, _) in _negatives().items():
            declared = {s["id"] for s in scenario["steps"]
                        if s.get("applicable") is False}
            card = er.evaluate(scenario, rec)
            for sid, v in card["verdicts"].items():
                if v == "n/a":
                    self.assertIn(sid, declared, name)


# ================================================== generic, not per-skill

class TestTheScorerIsGeneric(unittest.TestCase):
    def test_no_skill_name_appears_in_the_scorer(self):
        """Adding a skill must be adding a JSON file, not adding a branch.

        The plugin's own name is exempted in exactly one place — it is the
        SCHEMA namespace both modules already publish under — and the
        exemption is checked, not assumed: every line that mentions it must
        be spelling a schema id.
        """
        with io.open(er.__file__, encoding="utf-8") as f:
            src = f.read()
        namespace = es.SCHEMA.split(".")[0]
        for skill in es.GOVERNED_SKILLS:
            if skill == namespace:
                continue
            self.assertNotIn(skill, src)
        for line in src.splitlines():
            if namespace in line:
                self.assertIn('"%s.' % namespace, line)
        self.assertNotIn("if skill", src)
        self.assertIsNone(re.search(r"skill\s*==", src))

    def test_the_scorer_evaluates_a_scenario_it_has_never_seen(self):
        """A manifest for an invented skill, over the same record, scores."""
        scenario = _scen(_step("Z1", record="trace", check="exists",
                               select={"event": "dod"}))
        scenario["skill"] = "tp-invented"
        card = er.evaluate(scenario, _compliant())
        self.assertEqual(card["verdicts"], {"Z1": "pass"})

    def test_the_check_vocabulary_is_eval_scenarios_and_stays_in_step(self):
        """A ninth check kind in the manifest vocabulary with no
        implementation would be scored `no_evidence` forever while reading
        like a shy session."""
        self.assertEqual(set(er.CHECK_KINDS) | {"all"}, set(es.CHECKS))
        self.assertEqual(set(er.RECORDS), set(es.RECORDS))

    def test_all_is_flattened_by_eval_scenario_and_not_re_implemented(self):
        with io.open(er.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("constraints(", src)


# ================================================ the negative corpus is real

class TestTheNegativeCorpus(unittest.TestCase):
    def test_the_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(_negatives()), 8)

    def test_every_fixture_scores_exactly_what_it_pins(self):
        for name, (scenario, rec, expected) in sorted(_negatives().items()):
            card = er.evaluate(scenario, rec)
            pinned = expected["rubric"]
            self.assertIn("verdicts", pinned, name)
            for key, want in sorted(pinned.items()):
                self.assertEqual(card[key], want, f"{name}: {key}")

    def test_every_fixture_says_why_it_exists(self):
        for name, (_, _, expected) in _negatives().items():
            self.assertTrue((expected.get("why") or "").strip(), name)

    def test_every_fixture_uses_only_the_declared_vocabulary(self):
        for name, (scenario, _, _) in _negatives().items():
            for step in scenario["steps"]:
                for c in es.constraints(step):
                    self.assertIn(c["check"], es.CHECKS, name)
                    self.assertIn(c["record"], es.RECORDS, name)

    def test_every_fixture_is_a_loadable_eval_record(self):
        """`evals/negative/**` must not break the corpus run: wave 1's
        discriminator descends to depth 3 and treats a marker-carrying
        directory as a record it will SCORE."""
        root = os.path.join(REPO, er.NEGATIVE_DIRNAME)
        for name in sorted(os.listdir(root)):
            rec = ci_evals.load_record(os.path.join(root, name), name)
            self.assertTrue(rec["is_record"], name)
            self.assertTrue(rec["loadable"], f"{name}: {rec['reason']}")

    def test_no_fixture_pins_a_rate_it_does_not_score(self):
        """`ci_evals` compares `expected["rates"]` against its own areas and
        exits 1 on a mismatch. These fixtures pin a RUBRIC vector, which is a
        different instrument."""
        root = os.path.join(REPO, er.NEGATIVE_DIRNAME)
        for name in sorted(os.listdir(root)):
            rec = ci_evals.load_record(os.path.join(root, name), name)
            res = ci_evals.score(rec["trace"], rec["obligations"],
                                 rec["dispatch"])
            for area, want in ((rec["expected"] or {}).get("rates")
                               or {}).items():
                self.assertIn(area, res, name)
                self.assertEqual(res[area]["rate"], want, f"{name}/{area}")

    def test_the_absent_instrument_fixture_stays_loadable(self):
        """`no-ledger` is the invariant's own fixture. Wave 1 deliberately
        kept `derivations.jsonl` out of `RECORD_FILES` so this record is
        SCORED rather than rejected — a rejected fixture would leave the
        invariant untested while looking tested."""
        self.assertNotIn("derivations.jsonl", ci_evals.RECORD_FILES)
        path = os.path.join(REPO, er.NEGATIVE_DIRNAME, "no-ledger")
        self.assertTrue(ci_evals.load_record(path, "no-ledger")["loadable"])
        scenario, rec, _ = _negatives()["no-ledger"]
        card = er.evaluate(scenario, rec)
        ledger_rows = [s for s in card["steps"]
                       if any(c["record"] == "derivations"
                              for c in s["constraints"])]
        self.assertTrue(ledger_rows)
        for step in ledger_rows:
            self.assertEqual(step["verdict"], "no_evidence")
        self.assertEqual(card["derivation_ledger"], "absent")


class TestEveryCheckKindHasANegativeFixture(unittest.TestCase):
    """The falsifiability mechanism. A paragraph asking for it is not one.

    A rubric item that can only be green measures nothing. This evaluates the
    whole negative corpus, collects the verdict each CONSTRAINT actually got,
    and demands that every implemented check kind was observed FAILING at
    least once. Adding a check kind without a fixture fails here; deleting a
    fixture fails here.
    """

    def _observed(self):
        seen = {}
        for name, (scenario, rec, _) in _negatives().items():
            card = er.evaluate(scenario, rec)
            for step in card["steps"]:
                for c in step["constraints"]:
                    seen.setdefault(c["check"], {}).setdefault(
                        c["verdict"], []).append(name)
        return seen

    def test_every_implemented_check_kind_is_observed_failing(self):
        seen = self._observed()
        for kind in sorted(er.CHECK_KINDS):
            self.assertIn(kind, seen, f"no negative fixture exercises {kind}")
            self.assertIn("fail", seen[kind],
                          f"no negative fixture makes {kind} FAIL")

    def test_the_corpus_also_observes_the_absent_record_verdict(self):
        seen = self._observed()
        self.assertIn("no_evidence", seen.get("repeats", {}))

    def test_the_corpus_is_not_green_only_in_the_other_direction(self):
        """Every fixture must also contain something that PASSES, or a
        fixture could be failing for a reason nobody meant."""
        for name, (scenario, rec, _) in sorted(_negatives().items()):
            card = er.evaluate(scenario, rec)
            self.assertIn("pass", card["verdicts"].values(),
                          f"{name} has no control row that passes")


class TestReadRecord(unittest.TestCase):
    """The loader: which file is which record, and what absence looks like."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tp-rubric-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, name, text):
        with io.open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_a_missing_file_is_an_absent_record(self):
        rec = er.read_record(self.dir)
        for name in er.RECORDS:
            self.assertIsNone(rec["rows"][name])
        self.assertEqual(er.status(rec, "derivations"), "absent")

    def test_an_empty_file_is_an_empty_record_not_an_absent_one(self):
        self._write("derivations.jsonl", "")
        self.assertEqual(er.status(er.read_record(self.dir), "derivations"),
                         "empty")

    def test_a_torn_last_line_does_not_blind_the_whole_record(self):
        self._write("trace.jsonl",
                    '{"ts": 1, "event": "dod"}\n{"ts": 2, "even')
        rec = er.read_record(self.dir)
        self.assertEqual(len(rec["rows"]["trace"]), 1)

    def test_unparseable_json_is_unreadable_and_never_empty(self):
        self._write("dispatch.json", "{not json")
        rec = er.read_record(self.dir)
        self.assertIn("dispatch", rec["unreadable"])
        self.assertEqual(er.status(rec, "dispatch"), "unreadable")

    def test_the_dispatch_record_is_the_briefs_the_engine_composed(self):
        self._write("dispatch.json", json.dumps(
            {"expected": 1, "unobserved": 0, "hook_active": True,
             "briefs": [{"ts": 1, "lens": "a"}]}))
        rec = er.read_record(self.dir)
        self.assertEqual(rec["rows"]["dispatch"], [{"ts": 1, "lens": "a"}])

    def test_a_counts_only_dispatch_report_is_empty_not_present(self):
        """`dispatch.json` as the engine writes it today carries counts, not
        per-brief rows. That is an instrument gap, so it reads as `empty`."""
        self._write("dispatch.json", json.dumps({"expected": 8}))
        self.assertEqual(er.status(er.read_record(self.dir), "dispatch"),
                         "empty")

    def test_the_run_identity_is_read_from_run_json(self):
        self._write("run.json", json.dumps({"target_head": "H"}))
        self.assertEqual(er.read_record(self.dir)["run"], {"target_head": "H"})


if __name__ == "__main__":
    unittest.main()
