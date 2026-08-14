"""The eval GATE: a baseline is a verdict vector, and the gate is per item.

WHAT IS BEING PROVED. `eval_rubric.evaluate()` turns one scenario plus one
recorded run into a scorecard. This file proves the layer that DECIDES on
that scorecard — the CLI surface (`--skill`, `--set-baseline`, `--gate`), the
stored baseline, the waiver log and the staleness check — and it proves the
four ways that layer could quietly stop deciding anything:

  1. AN UNKNOWN FLAG WAS IGNORED. `main()` hand-parsed argv and dropped what
     it did not recognise, so `--totally-invented-flag` exited 0 and looked
     like a clean run. Every gate added here would have been one typo away
     from silently not running. The control is a test that an invented flag
     exits 2.

  2. THE GATE COULD HAVE BEEN A SCALAR. `eval_rubric.score` is pass over pass
     plus fail; one row improving while another regresses leaves it exactly
     where it was, and a row dropping to `no_evidence` leaves the denominator
     smaller and the average HIGHER. `evals/negative/no-ledger/` pins
     `score: 1.0` beside `instrument: broken` to make that concrete, and it
     is used here as a fixture rather than as an anecdote.

  3. A BAR COULD HAVE BEEN SET BY A RUN NOBODY WATCHED. A subagent-mode run
     shares a transcript and a budget with its parent, and a run whose
     dispatch hook was inactive has UNKNOWN fan-out rather than zero.
     `run.json` already carries `baseline_eligible` and the reason;
     `--set-baseline` has to honour it, or the bar is whatever the least
     observed run happened to score.

  4. NOBODY RE-RECORDS. A baseline graded against a skill that has since
     changed is not a baseline, it is a fossil. The run's
     `inputs_fingerprint` against the baseline's is what makes that loud.

WHAT THE ACCEPTOR CHECK IS NOT. A waiver carries an acceptor, and the
acceptor string is NOT AUTHENTICATED. In this product the committer is
routinely the model, and typing a human's name satisfies the check. The check
rejects the two identities the machine already answers to — an agent name and
the `taskplane-role:` marker — and past that it buys attribution in a diff,
not authorisation. That is tested here as a stated fact, because a control
that overstates itself is worse than no control.

WHY A WAIVER IS BOUNDED. Because the acceptor is not authenticated, the only
honest way to keep that control from becoming a formality is to make an
unauthenticated waiver COST something to keep. An unbounded waiver covers its
step's drops forever, so a sentence written once for a transient evidence gap
absorbs a real regression six months later and nobody re-reads it — it never
asks to be re-read. Sections 8 and 9 pin the two bounds and the three ways the
gate must not go quiet: an unbounded waiver is REFUSED, an expired one BLOCKS
and is named rather than silently ceasing to apply, and one written about a
flow that has since moved stops covering the drop that reappeared under it.
"""
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "taskplane"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ci_evals                                                  # noqa: E402
import eval_rubric as er                                         # noqa: E402
import eval_scenario as es                                       # noqa: E402

SCRIPT = os.path.join(REPO, "scripts", "ci_evals.py")
SKILL = "tp-demo"
SOURCE = "skills/tp-demo/SKILL.md"

# Plain markdown. It mandates no surface, so the manifest below may declare
# none — the scenario validator refuses a declared surface no source file
# mandates, and this file is a fixture for the GATE, not for the extractor.
SKILL_MD = """# tp-demo

The demo flow activates a contract, answers DoR, answers DoD and derives the
impact once.
"""

# A new MANDATED SURFACE, because that is what the flow extract digests: prose
# alone is not a flow change and must not fire a gate people would learn to
# waive. Shared by the staleness section and by the waiver-scope section,
# which are two views of the same event — the skill moved.
NEW_MANDATE = "\nThe review MUST run `tp lens dispatch` before it closes.\n"


def _day(offset):
    """An ISO date `offset` days from today, UTC — the spelling a waiver's
    `expires` field uses. Computed rather than frozen: a bound that only holds
    on the day the test was written is not a bound."""
    return (datetime.datetime.now(datetime.timezone.utc).date()
            + datetime.timedelta(days=offset)).isoformat()


def _steps():
    """Four rows, one per universal tag — the shape the validator requires.

    S1..S3 read the trace and S4 reads the derivation ledger, which is what
    lets a fixture move ONE row at a time: dropping a trace event turns a row
    `fail`, and dropping the ledger's pre-flight probe turns S4 `no_evidence`
    while the other three stay `pass`.
    """
    return [
        {"id": "S1", "claim": "the contract was activated before any work",
         "record": "trace", "check": "exists",
         "select": {"event": "contract_activated"},
         "required": True, "universal": ["contract"]},
        {"id": "S2", "claim": "the DoR was answered", "record": "trace",
         "check": "exists", "select": {"event": "dor"},
         "required": True, "universal": ["dor"]},
        {"id": "S3", "claim": "the DoD was answered", "record": "trace",
         "check": "exists", "select": {"event": "dod"},
         "required": True, "universal": ["dod"]},
        {"id": "S4", "claim": "nothing was derived twice",
         "record": "derivations", "check": "repeats",
         "select": {"event": "derived", "probe": {"absent": True}},
         "distinct_by": ["key", "input_key"], "max": 0,
         "required": True, "universal": ["no_rederive"]},
    ]


TRACE = [
    {"ts": 1, "event": "contract_activated"},
    {"ts": 2, "event": "dor", "ready": True},
    {"ts": 9, "event": "dod", "passed": True},
]
LEDGER = [
    {"ts": 0.5, "event": "derived", "key": "impact", "input_key": "H|abc",
     "probe": True, "id": "p-1"},
    {"ts": 4, "event": "derived", "key": "impact", "input_key": "H|abc"},
]


class _Tree(unittest.TestCase):
    """A whole eval root on disk: one skill, one manifest, real fingerprints.

    Built rather than mocked because the fingerprint IS the staleness check —
    a stubbed digest would prove the comparison and not the thing compared.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tp-evalgate-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write_source(SKILL_MD)
        self.write_scenario()

    # --- building the tree -------------------------------------------------

    def _abs(self, rel):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def _text(self, rel, body):
        with io.open(self._abs(rel), "w", encoding="utf-8") as f:
            f.write(body)

    def _json(self, rel, value):
        self._text(rel, json.dumps(value, indent=2, sort_keys=True))

    def _jsonl(self, rel, rows):
        self._text(rel, "".join(json.dumps(r, sort_keys=True) + "\n"
                                for r in rows))

    def write_source(self, body):
        self._text(SOURCE, body)

    def fingerprint(self):
        return es.fingerprint(self.root, [SOURCE])

    def write_scenario(self, steps=None, fingerprint=None):
        self._json(f"evals/scenarios/{SKILL}.json", {
            "schema": es.SCHEMA, "skill": SKILL,
            "title": "the demo flow, graded",
            "source_files": [SOURCE],
            "inputs_fingerprint": fingerprint or self.fingerprint(),
            "expects_derivations": ["impact"],
            "declared_surfaces": [],
            "steps": steps or _steps(),
        })

    def write_run(self, run_id="20260101T000000Z-aaaaaa", *, trace=None,
                  ledger=None, mode="out-of-band", hook_active=True,
                  fingerprint=None, eligible=None, frozen_at=None):
        rel = f"evals/runs/{SKILL}/{run_id}"
        self._jsonl(rel + "/trace.jsonl",
                    TRACE if trace is None else trace)
        self._jsonl(rel + "/derivations.jsonl",
                    LEDGER if ledger is None else ledger)
        self._jsonl(rel + "/obligations.jsonl", [])
        self._jsonl(rel + "/context.jsonl", [])
        self._json(rel + "/dispatch.json",
                   {"expected": 0, "unobserved": 0, "hook_active": hook_active,
                    er.DISPATCH_ROWS: []})
        ok = eligible
        if ok is None:
            ok = mode == "out-of-band" and hook_active
        self._json(rel + "/run.json", {
            "schema": "taskplane.eval-run/v1", "skill": SKILL,
            "run_id": run_id, "mode": mode, "host": "claude",
            "recorded_at": 1.0, "frozen_at": frozen_at or 2.0,
            "hook_active": hook_active, "baseline_eligible": bool(ok),
            "baseline_reason": "fixture", "target_head": "H",
            "inputs_fingerprint": (fingerprint if fingerprint is not None
                                   else self.fingerprint()),
            "effective_tokens": None,
        })
        return os.path.join(self.root, rel.replace("/", os.sep))

    def write_baseline(self, value):
        self._json(f"evals/baselines/{SKILL}.json", value)

    def write_waivers(self, rows):
        self._jsonl(f"evals/baselines/{SKILL}.waivers.jsonl", rows)

    def read_baseline(self):
        with io.open(self._abs(f"evals/baselines/{SKILL}.json"),
                     encoding="utf-8") as f:
            return json.load(f)

    # --- driving the CLI ---------------------------------------------------

    def cli(self, *args):
        """The REAL script against this tree. Exit code and streams."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.join(REPO, "taskplane"), env.get("PYTHONPATH", "")])
        return subprocess.run(
            [sys.executable, SCRIPT, "--root", self.root, *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env)

    def out(self, r):
        return r.stdout + r.stderr


# ================================================== 1: the argv defect itself

class TestAnUnknownFlagIsRefusedInsteadOfIgnored(unittest.TestCase):
    """`main()` hand-parsed argv: anything it did not recognise fell through
    to the workspace scorer and exited 0. Every gate in this file would have
    been one typo away from silently not running."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=REPO)

    def test_an_invented_flag_exits_2_instead_of_scoring_something_else(self):
        r = self._run("--totally-invented-flag")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_the_refusal_names_the_flag_it_could_not_honour(self):
        r = self._run("--totally-invented-flag")
        self.assertIn("--totally-invented-flag", r.stdout + r.stderr)

    def test_a_misspelled_known_flag_is_refused_not_silently_dropped(self):
        """`--corpuss` used to run the WORKSPACE scorer and exit 0, which
        reads in CI as the corpus having passed."""
        r = self._run("--corpuss")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_the_flags_that_do_exist_still_work(self):
        self.assertEqual(self._run("--corpus").returncode, 0)


# ============================================== 2: the scorecard render

class TestAnUnknownSkillIsNamedNotGuessed(_Tree):

    def test_an_unknown_skill_exits_2(self):
        r = self.cli("--skill", "tp-nonesuch")
        self.assertEqual(r.returncode, 2, self.out(r))

    def test_the_refusal_names_the_skill_and_where_it_looked(self):
        r = self.cli("--skill", "tp-nonesuch")
        said = self.out(r)
        self.assertIn("tp-nonesuch", said)
        self.assertIn("evals", said)
        self.assertIn("scenarios", said)

    def test_the_refusal_lists_the_skills_that_do_exist(self):
        self.assertIn(SKILL, self.out(self.cli("--skill", "tp-nonesuch")))


class TestAKnownSkillWithNoRecordedRunSaysSoInsteadOfScoringNothing(_Tree):
    """An empty record scores `no_evidence` in every row, so a missing run
    that fell through to the scorer would print a full card of honest
    unknowns for a run that never happened."""

    def test_a_skill_with_no_run_record_exits_2(self):
        self.assertEqual(self.cli("--skill", SKILL).returncode, 2)

    def test_the_refusal_names_the_directory_it_searched(self):
        r = self.cli("--skill", SKILL)
        self.assertIn(os.path.join("evals", "runs", SKILL).replace("\\", "/"),
                      self.out(r).replace("\\", "/"))


class TestTheScorecardRendersEveryRubricRow(_Tree):

    def setUp(self):
        super().setUp()
        self.run_dir = self.write_run()

    def test_every_step_id_appears_in_the_table(self):
        said = self.out(self.cli("--skill", SKILL))
        for sid in ("S1", "S2", "S3", "S4"):
            self.assertIn(sid, said)

    def test_a_failing_row_prints_its_verdict_and_its_human_claim(self):
        self.write_run(trace=[r for r in TRACE if r["event"] != "dod"])
        said = self.out(self.cli("--skill", SKILL))
        self.assertIn("fail", said)
        self.assertIn("the DoD was answered", said)

    def test_the_counters_are_printed(self):
        said = self.out(self.cli("--skill", SKILL))
        self.assertIn("pass", said)
        self.assertIn("no_evidence", said)

    def test_the_scalar_is_printed_and_labelled_as_gating_nothing(self):
        """It is reported for humans. Printing it beside the vector without
        saying so is how a scalar becomes a gate by habit."""
        said = self.out(self.cli("--skill", SKILL)).lower()
        self.assertIn("per item", said)

    def test_json_carries_the_verdict_vector(self):
        r = self.cli("--skill", SKILL, "--json")
        self.assertEqual(r.returncode, 0, self.out(r))
        card = json.loads(r.stdout)
        self.assertEqual(card["verdicts"],
                         {"S1": "pass", "S2": "pass", "S3": "pass",
                          "S4": "pass"})

    def test_scoring_a_run_is_read_only_and_exits_0_without_a_baseline(self):
        self.assertEqual(self.cli("--skill", SKILL).returncode, 0)
        self.assertFalse(os.path.isdir(
            os.path.join(self.root, "evals", "baselines")))

    def test_all_skills_scores_every_manifest_that_has_a_run(self):
        said = self.out(self.cli("--all-skills"))
        self.assertIn(SKILL, said)

    def test_the_newest_run_is_the_one_scored(self):
        """Two runs, one older and green, one newer and red. Scoring the
        older one would let a regression sit in the tree unseen."""
        self.write_run("20260101T000000Z-aaaaaa", frozen_at=2.0)
        self.write_run("20260202T000000Z-bbbbbb", frozen_at=99.0,
                       trace=[r for r in TRACE if r["event"] != "dod"])
        card = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.assertEqual(card["verdicts"]["S3"], "fail")
        self.assertIn("bbbbbb", json.dumps(card))


# ================================================ 3: setting a baseline

class TestANamedRunCanBeGradedInsteadOfTheNewest(_Tree):
    """`--run` exists because "the newest" is not always the one you mean:
    a baseline is set from a run someone LOOKED at, which by then may not be
    the latest thing on disk."""

    def test_the_named_run_is_the_one_scored(self):
        old = self.write_run("20260101T000000Z-aaaaaa", frozen_at=2.0)
        self.write_run("20260202T000000Z-bbbbbb", frozen_at=99.0,
                       trace=[r for r in TRACE if r["event"] != "dod"])
        card = json.loads(self.cli("--skill", SKILL, "--run", old,
                                   "--json").stdout)
        self.assertEqual(card["verdicts"]["S3"], "pass")

    def test_a_directory_that_is_not_a_run_record_exits_2(self):
        self.write_run()
        r = self.cli("--skill", SKILL, "--run", self.root)
        self.assertEqual(r.returncode, 2, self.out(r))
        self.assertIn("run.json", self.out(r))

    def test_a_named_run_is_still_checked_for_eligibility(self):
        path = self.write_run("20260303T000000Z-cccccc", mode="subagent")
        r = self.cli("--set-baseline", SKILL, "--run", path)
        self.assertNotEqual(r.returncode, 0)


class TestABaselineIsAVerdictVectorNotAnAverage(_Tree):

    def setUp(self):
        super().setUp()
        self.write_run()

    def test_setting_a_baseline_exits_0_and_writes_the_file(self):
        r = self.cli("--set-baseline", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "evals", "baselines", SKILL + ".json")))

    def test_the_baseline_stores_the_whole_verdict_vector(self):
        self.cli("--set-baseline", SKILL)
        self.assertEqual(self.read_baseline()["verdicts"],
                         {"S1": "pass", "S2": "pass", "S3": "pass",
                          "S4": "pass"})

    def test_the_baseline_stores_the_inputs_fingerprint_it_was_graded_at(self):
        self.cli("--set-baseline", SKILL)
        self.assertEqual(self.read_baseline()["inputs_fingerprint"],
                         self.fingerprint())

    def test_the_baseline_names_the_run_it_came_from(self):
        """A bar with no provenance cannot be re-derived or argued with."""
        self.cli("--set-baseline", SKILL)
        run = self.read_baseline()["run"]
        self.assertEqual(run["run_id"], "20260101T000000Z-aaaaaa")
        self.assertEqual(run["mode"], "out-of-band")
        self.assertTrue(run["hook_active"])
        self.assertIn("runs", run["path"])

    def test_the_stored_scalar_is_named_so_it_cannot_be_gated_by_accident(self):
        self.cli("--set-baseline", SKILL)
        base = self.read_baseline()
        self.assertNotIn("score", base)
        self.assertIn("score_for_humans", base)

    def test_the_baseline_records_a_digest_per_source_file(self):
        """So a STALE verdict can name WHICH input moved instead of printing
        two hexes and leaving the reader to guess."""
        self.cli("--set-baseline", SKILL)
        self.assertIn(SOURCE, self.read_baseline()["source_files"])

    def test_setting_the_same_baseline_twice_produces_an_identical_file(self):
        """No wall clock in the artifact. A re-run that changes nothing must
        show as an empty diff, or `git diff evals/baselines/` stops being
        the place a lowering is visible."""
        self.cli("--set-baseline", SKILL)
        first = self.read_baseline()
        self.cli("--set-baseline", SKILL)
        self.assertEqual(first, self.read_baseline())


class TestAnUnobservedRunMayNeverSetABar(_Tree):
    """`run.json` already decides this and says why. The CLI honours it or
    the bar becomes whatever the least observed run happened to score."""

    def test_a_subagent_mode_run_is_refused(self):
        self.write_run(mode="subagent", hook_active=True)
        r = self.cli("--set-baseline", SKILL)
        self.assertNotEqual(r.returncode, 0)

    def test_a_run_whose_dispatch_hook_saw_nothing_is_refused(self):
        self.write_run(mode="out-of-band", hook_active=False)
        r = self.cli("--set-baseline", SKILL)
        self.assertNotEqual(r.returncode, 0)

    def test_the_refusal_repeats_the_records_own_reason(self):
        self.write_run(mode="subagent")
        self.assertIn("fixture", self.out(self.cli("--set-baseline", SKILL)))

    def test_no_baseline_file_is_written_by_a_refused_run(self):
        self.write_run(mode="subagent")
        self.cli("--set-baseline", SKILL)
        self.assertFalse(os.path.isfile(
            os.path.join(self.root, "evals", "baselines", SKILL + ".json")))

    def test_the_flag_in_the_record_is_what_decides_not_the_mode_alone(self):
        """`baseline_eligible` is the recorder's verdict. A CLI that
        re-derived it from `mode` would be a second implementation free to
        disagree with the record it is reading."""
        self.write_run(mode="out-of-band", hook_active=True, eligible=False)
        self.assertNotEqual(self.cli("--set-baseline", SKILL).returncode, 0)

    def test_an_eligible_run_is_accepted(self):
        self.write_run()
        self.assertEqual(self.cli("--set-baseline", SKILL).returncode, 0)


# ====================================================== 4: the gate itself

class _Gated(_Tree):
    """A green run, its baseline, and helpers to move one row at a time."""

    def setUp(self):
        super().setUp()
        self.write_run()
        self.assertEqual(self.cli("--set-baseline", SKILL).returncode, 0)

    def regress(self, event="dod"):
        """Drop one trace event: that row goes pass -> fail."""
        self.write_run(trace=[r for r in TRACE if r["event"] != event])

    def blind(self):
        """Drop the ledger's pre-flight probe: S4 goes pass -> no_evidence
        while every trace row stays exactly where it was."""
        self.write_run(ledger=[r for r in LEDGER if not r.get("probe")])

    def waiver(self, **kw):
        """A waiver that is well formed in every respect, so a test that
        moves ONE field proves that field and nothing else.

        Both bounds are filled in: the flow this waiver was written about,
        and the date by which somebody has to read it again.
        """
        row = {"step": "S3",
               "reason": "the DoD event moved to the host and the recorder "
                         "has not caught up",
               "acceptor": "Vlad Demkiv",
               "inputs_fingerprint": self.fingerprint(),
               "expires": _day(30)}
        row.update(kw)
        return row


class TestTheGateBlocksPerItemNotOnTheAverage(_Gated):

    def test_an_unchanged_vector_exits_0(self):
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_pass_to_fail_exits_non_zero(self):
        self.regress()
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_pass_to_fail_names_the_rubric_item_and_calls_it_a_regression(self):
        self.regress()
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("S3", said)
        self.assertIn("REGRESSION", said)

    def test_pass_to_no_evidence_exits_non_zero(self):
        self.blind()
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_pass_to_no_evidence_is_called_evidence_lost_not_a_regression(self):
        """The instrument stopped seeing the row. Reported as its own
        transition because the fix is different: re-instrument, not re-code."""
        self.blind()
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("S4", said)
        self.assertIn("EVIDENCE LOST", said)

    def test_a_row_dropping_to_no_evidence_RAISES_the_scalar(self):
        """The concrete form of the argument. `score` is pass over pass plus
        fail, so an unknown leaves the denominator smaller — a scalar bar of
        'not worse than before' is PASSED by an instrument going blind."""
        before = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.blind()
        after = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.assertEqual(before["score"], 1.0)
        self.assertEqual(after["score"], 1.0)
        self.assertGreaterEqual(after["score"], before["score"])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_one_row_improving_while_another_regresses_still_blocks(self):
        """The average is the same number and one control point is gone."""
        self.write_run(trace=[r for r in TRACE if r["event"] != "dor"])
        self.cli("--set-baseline", SKILL)         # baseline: S2 fail
        base = self.read_baseline()["verdicts"]
        self.assertEqual(base["S2"], "fail")
        self.write_run(trace=[r for r in TRACE if r["event"] != "dod"])
        card = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.assertEqual(card["verdicts"]["S2"], "pass")   # improved
        self.assertEqual(card["verdicts"]["S3"], "fail")   # regressed
        self.assertEqual(card["score"], 0.75)              # flat
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("S3", self.out(r))

    def test_fail_to_pass_is_not_blocked(self):
        self.regress()
        self.cli("--set-baseline", SKILL)
        self.write_run()
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_an_improvement_is_reported_rather_than_passed_over(self):
        self.regress()
        self.cli("--set-baseline", SKILL)
        self.write_run()
        self.assertIn("IMPROVED", self.out(self.cli("--gate", "--skill",
                                                    SKILL)))

    def test_no_evidence_to_fail_is_not_a_drop_from_a_pass(self):
        """Only a row that once PASSED can be lowered. A row that was never
        evidence gaining a definite failure is the instrument working."""
        self.blind()
        self.cli("--set-baseline", SKILL)
        self.write_run(ledger=[
            {"ts": 0.5, "event": "derived", "key": "impact",
             "input_key": "H|abc", "probe": True, "id": "p-1"},
            {"ts": 4, "event": "derived", "key": "impact", "input_key": "K"},
            {"ts": 5, "event": "derived", "key": "impact", "input_key": "K"},
        ])
        card = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.assertEqual(card["verdicts"]["S4"], "fail")
        self.assertEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)


class TestARowCannotBeRetiredOutOfTheVectorToPassTheGate(_Gated):
    """Beyond the two named transitions, and stated as its own class because
    it is an EXTENSION: `inputs_fingerprint` digests the SKILL's source
    files, not the scenario manifest, so editing the manifest to declare a
    failing row `applicable: false` — or deleting the row — moves no
    fingerprint and fires no staleness. Both are drops from a pass and both
    block."""

    def test_a_row_turned_inapplicable_blocks(self):
        steps = _steps()
        steps[2].update(applicable=False, reason="we decided not to")
        self.write_scenario(steps=steps)
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))
        self.assertIn("S3", self.out(r))

    def test_a_row_deleted_from_the_manifest_blocks_and_is_named(self):
        self.write_scenario(steps=[s for s in _steps() if s["id"] != "S3"])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))
        self.assertIn("S3", self.out(r))

    def test_a_brand_new_row_does_not_block(self):
        """A row the baseline never carried cannot have regressed."""
        steps = _steps()
        steps.append({"id": "S5", "claim": "a newly added control point",
                      "record": "trace", "check": "exists",
                      "select": {"event": "nothing_writes_this"},
                      "required": True})
        self.write_scenario(steps=steps)
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertIn("S5", self.out(r))


class TestTheNoLedgerFixtureIsWhyTheGateIsNotAScalar(unittest.TestCase):
    """`evals/negative/no-ledger/` pins `score: 1.0` beside
    `instrument: broken` for exactly this argument. Read from the frozen
    corpus rather than restated, so the claim cannot drift from the fixture
    that makes it."""

    def _fixture(self):
        path = os.path.join(REPO, er.NEGATIVE_DIRNAME, "no-ledger")
        with io.open(os.path.join(path, "expected.json"),
                     encoding="utf-8") as f:
            return json.load(f)["rubric"]

    def test_the_fixture_still_pins_a_perfect_scalar(self):
        self.assertEqual(self._fixture()["score"], 1.0)

    def test_the_fixture_still_pins_a_broken_instrument(self):
        self.assertEqual(self._fixture()["instrument"], "broken")

    def test_a_scalar_bar_would_pass_that_run_and_the_per_item_gate_blocks(
            self):
        rubric = self._fixture()
        base = {k: "pass" for k in rubric["verdicts"]}
        blocking = [t for t in ci_evals.compare(base, rubric["verdicts"])
                    if t["blocking"]]
        self.assertGreaterEqual(rubric["score"], 1.0)
        self.assertEqual([t["step"] for t in blocking], ["N1"])
        self.assertEqual(blocking[0]["kind"], ci_evals.EVIDENCE_LOST)


# ========================================================== 5: waivers

class TestOnlyARecordedWaiverLetsADropPass(_Gated):

    def test_an_unwaived_drop_blocks(self):
        self.regress()
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_a_waiver_naming_the_step_lets_that_drop_pass(self):
        self.regress()
        self.write_waivers([self.waiver()])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_a_waived_drop_is_still_printed_named_and_attributed(self):
        """The whole control is that a lowering is VISIBLE. A waiver that
        silenced the line would delete the only evidence it exists to leave."""
        self.regress()
        self.write_waivers([self.waiver()])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("S3", said)
        self.assertIn("WAIVED", said)
        self.assertIn("Vlad Demkiv", said)
        self.assertIn("the recorder has not caught up", said)

    def test_a_waiver_for_a_different_step_does_not_cover_this_one(self):
        self.regress()
        self.write_waivers([self.waiver(step="S1")])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_a_waiver_may_be_pinned_to_one_transition(self):
        """`from`/`to` are optional and NARROW the waiver. A waiver written
        for an evidence gap must not silently absorb a later real failure."""
        self.blind()
        self.write_waivers([self.waiver(step="S4",
                                        **{"from": "pass",
                                           "to": "no_evidence"})])
        self.assertEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)
        self.regress()
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_a_waiver_with_no_reason_is_rejected(self):
        self.regress()
        row = self.waiver()
        row.pop("reason")
        self.write_waivers([row])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("reason", self.out(r))

    def test_a_waiver_with_no_acceptor_is_rejected(self):
        self.regress()
        row = self.waiver()
        row.pop("acceptor")
        self.write_waivers([row])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acceptor", self.out(r))

    def test_a_malformed_waiver_row_is_named_and_does_not_waive(self):
        self.regress()
        self._text(f"evals/baselines/{SKILL}.waivers.jsonl", "{not json\n")
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("waiver", self.out(r).lower())

    def test_the_log_is_append_only_in_shape_so_every_waiver_stays_in_diff(
            self):
        """JSONL, one waiver per line: a lowering is added, never edited over
        the top of the last one."""
        self.assertTrue(ci_evals.waiver_path(self.root, SKILL)
                        .endswith(".waivers.jsonl"))


class TestTheAcceptorCheckRejectsTheMachineAndNothingMore(_Gated):

    def _waive(self, acceptor):
        """Bounded and reasoned, so the ACCEPTOR is the only thing left that
        can decide the outcome."""
        self.regress()
        self.write_waivers([self.waiver(reason="a stated reason",
                                        acceptor=acceptor)])
        return self.cli("--gate", "--skill", SKILL)

    def test_an_agent_name_is_rejected_as_an_acceptor(self):
        r = self._waive("tp-engineering")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acceptor", self.out(r))

    def test_every_agent_in_the_repo_is_rejected_not_just_a_hardcoded_few(
            self):
        """Read from `agents/`, so a new agent is covered the day it lands."""
        names = ci_evals.agent_names(REPO)
        self.assertIn("tp-executor", names)
        self.assertIn("tp-lens", names)
        for name in names:
            self.assertIsNotNone(
                ci_evals.acceptor_problem(name, REPO), name)

    def test_the_role_marker_prefix_is_rejected(self):
        r = self._waive("taskplane-role:tp-lens")
        self.assertNotEqual(r.returncode, 0)

    def test_the_check_is_case_and_whitespace_insensitive(self):
        self.assertIsNotNone(
            ci_evals.acceptor_problem("  TP-Engineering  ", REPO))

    def test_an_empty_acceptor_is_rejected(self):
        self.assertIsNotNone(ci_evals.acceptor_problem("   ", REPO))

    def test_a_human_name_is_accepted(self):
        self.assertIsNone(ci_evals.acceptor_problem("Vlad Demkiv", REPO))

    def test_the_code_states_plainly_that_the_acceptor_is_not_authenticated(
            self):
        """Not a comment anyone can skip. The statement is a constant, it
        names WHO routinely commits in this product, and it says what typing
        a human's name does and does not achieve."""
        said = ci_evals.ACCEPTOR_IS_NOT_AUTHENTICATED.lower()
        self.assertIn("not authenticated", said)
        self.assertIn("model", said)
        self.assertIn("attribution", said)

    def test_the_disclaimer_is_printed_whenever_a_waiver_is_applied(self):
        """Stated where the lowering happens, not only in a docstring."""
        self.regress()
        self.write_waivers([self.waiver(reason="a stated reason")])
        self.assertIn("not authenticated",
                      self.out(self.cli("--gate", "--skill", SKILL)).lower())


# ======================================================= 6: staleness

class TestABaselineGradedAgainstAChangedSkillIsNotABaseline(_Gated):

    # `NEW_MANDATE` lives at module scope: the waiver-scope section moves the
    # skill's flow the same way, and two spellings of "the flow moved" would
    # be free to drift apart.

    def _change_the_skill(self):
        """The skill's FLOW moves and the manifest is re-fingerprinted — the
        real shape of the problem. The recorded run is the one nobody
        re-recorded."""
        self.write_source(SKILL_MD + NEW_MANDATE)
        self.write_scenario()

    def test_prose_that_changes_no_mandate_is_not_stale(self):
        """The fingerprint digests the FLOW the source files mandate, not
        their bytes — a typo may not fire a gate."""
        self.write_source(SKILL_MD + "\nThis paragraph mandates nothing.\n")
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_a_run_fingerprint_that_differs_from_the_baselines_blocks(self):
        self._change_the_skill()
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))

    def test_the_verdict_is_called_stale(self):
        self._change_the_skill()
        self.assertIn("STALE", self.out(self.cli("--gate", "--skill", SKILL)))

    def test_staleness_blocks_even_when_every_single_verdict_held(self):
        """This is the whole point: 'nobody re-recorded' passes a per-item
        gate perfectly, because the vector did not move."""
        self._change_the_skill()
        card = json.loads(self.cli("--skill", SKILL, "--json").stdout)
        self.assertEqual(set(card["verdicts"].values()), {"pass"})
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_the_stale_report_names_the_input_that_moved(self):
        self._change_the_skill()
        self.assertIn(SOURCE, self.out(self.cli("--gate", "--skill", SKILL)))

    def test_a_matching_fingerprint_is_not_stale(self):
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertNotIn("STALE", self.out(r))

    def test_a_manifest_that_no_longer_describes_its_own_sources_is_stale(
            self):
        """The other direction: the skill changed and NOBODY re-fingerprinted
        the manifest, so the run and the baseline agree on a digest that
        describes neither."""
        self.write_source(SKILL_MD + NEW_MANDATE)
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))
        self.assertIn("STALE", self.out(r))

    def test_a_step_waiver_does_not_launder_staleness(self):
        """A waiver is per rubric item. Staleness is a property of the whole
        comparison, and there is no step id it could be filed against."""
        self._change_the_skill()
        self.write_waivers([self.waiver(step="S1", reason="r"),
                            self.waiver(step="STALE", reason="r")])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)


class TestTheGateWillNotJudgeARunNobodyObserved(_Gated):
    """`run.json`'s own eligibility text says an in-session run 'may never set
    or satisfy a baseline'. Grading one anyway manufactures regressions people
    would learn to waive, which corrodes the waiver log."""

    def test_gating_an_ineligible_run_exits_2_rather_than_reporting_a_drop(
            self):
        self.write_run(mode="subagent")
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 2, self.out(r))

    def test_the_refusal_says_it_is_the_run_that_cannot_answer(self):
        self.write_run(mode="subagent")
        self.assertIn("subagent", self.out(self.cli("--gate", "--skill",
                                                    SKILL)))


class TestABaselineIsRequiredBeforeTheGateCanAnswer(_Tree):

    def test_a_skill_with_no_baseline_exits_2(self):
        self.write_run()
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 2, self.out(r))

    def test_the_refusal_says_how_to_set_one(self):
        self.write_run()
        self.assertIn("--set-baseline",
                      self.out(self.cli("--gate", "--skill", SKILL)))

    def test_an_unreadable_baseline_is_an_error_not_an_empty_vector(self):
        """An empty baseline vector has no `pass` in it, so every drop
        becomes a non-transition and the gate goes green on a corrupt file."""
        self.write_run()
        self._text(f"evals/baselines/{SKILL}.json", "{not json")
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)


# ============================== 7: the baseline store and the corpus scorer

class TestTheBaselineStoreDoesNotBreakTheCorpusScorer(unittest.TestCase):
    """`evals/baselines/` is a store of loose JSON files under the tree the
    corpus walker descends. The walker's discriminator has to skip it, and
    the four frozen corpora have to keep printing exactly what they printed."""

    PINNED = {
        "compliant": ("artifact_surfacing", "100%"),
        "skipped-render": ("artifact_surfacing", "  0%"),
        "substitute-graph": ("product_graph", "  0%"),
        "no-hook-one-host": ("agent_fanout", "no evidence"),
    }

    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(
            [sys.executable, SCRIPT, "--corpus"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", cwd=REPO)

    def test_the_corpus_run_still_exits_0(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_the_baselines_directory_exists_in_the_repo(self):
        self.assertTrue(os.path.isdir(
            os.path.join(REPO, ci_evals.BASELINE_DIRNAME)))

    def test_the_baselines_directory_is_named_as_skipped_never_silent(self):
        self.assertIn("baselines", self.result.stdout + self.result.stderr)

    def test_the_four_frozen_corpora_still_print_their_pinned_rates(self):
        for name, (area, rate) in self.PINNED.items():
            block = self.result.stdout.split("  " + name + "\n")
            self.assertEqual(len(block), 2, f"{name} is not in the report")
            line = [ln for ln in block[1].splitlines() if area in ln][0]
            self.assertIn(rate, line, f"{name}/{area}: {line!r}")

    def test_a_baseline_file_in_the_store_is_not_walked_as_a_record(self):
        """A baseline is a loose FILE, and the walker only ever considers
        directories — proved rather than assumed, because a store that grew a
        per-skill DIRECTORY would land straight in the corpus."""
        records, skipped = ci_evals._discover(os.path.join(REPO, "evals"))
        self.assertNotIn("baselines", [r["name"] for r in records])
        self.assertIn("baselines", [r["name"] for r in skipped])


class TestNoFlagIsAcceptedAndThenIgnored(_Tree):
    """The argv defect one level in. Refusing `--totally-invented-flag` and
    then accepting `--json` or `--run` in a mode that drops them would be the
    same silence with a smaller blast radius."""

    def setUp(self):
        super().setUp()
        self.write_run()

    def test_the_gate_honours_json(self):
        self.cli("--set-baseline", SKILL)
        r = self.cli("--gate", "--skill", SKILL, "--json")
        report = json.loads(r.stdout)
        self.assertFalse(report["blocked"])
        self.assertEqual([t["kind"] for t in report["transitions"]],
                         ["held"] * 4)

    def test_the_json_gate_report_carries_the_acceptor_disclaimer(self):
        self.cli("--set-baseline", SKILL)
        report = json.loads(
            self.cli("--gate", "--skill", SKILL, "--json").stdout)
        self.assertIn("not authenticated",
                      report["acceptor_disclaimer"].lower())

    def test_set_baseline_honours_json(self):
        r = self.cli("--set-baseline", SKILL, "--json")
        self.assertEqual(json.loads(r.stdout)["schema"],
                         ci_evals.BASELINE_SCHEMA)

    def test_a_run_named_without_a_skill_is_refused(self):
        """One record cannot be graded against every rubric at once, and
        pretending otherwise turns every other skill's card red."""
        r = self.cli("--gate", "--run", self.root)
        self.assertEqual(r.returncode, 2, self.out(r))
        self.assertIn("--run", self.out(r))

    def test_corpus_refuses_to_be_combined_rather_than_winning_silently(self):
        r = self.cli("--corpus", "--skill", SKILL)
        self.assertEqual(r.returncode, 2, self.out(r))
        self.assertIn("--skill", self.out(r))


class TestABaselineIsCommittedSoItCarriesNothingMachineLocal(_Tree):

    def test_the_stored_run_path_is_relative_to_the_repository(self):
        """An absolute `/home/…` prefix in a committed baseline is noise in
        every other checkout's diff, which is the one place a lowering is
        supposed to be legible."""
        self.write_run()
        self.cli("--set-baseline", SKILL)
        path = self.read_baseline()["run"]["path"]
        self.assertFalse(os.path.isabs(path), path)
        self.assertTrue(path.startswith("evals/runs/"), path)


class TestAFanOutReportsTheBlockRatherThanTheSetupProblem(unittest.TestCase):

    def test_a_real_block_outranks_a_cannot_answer(self):
        """One skill regressed and another has no baseline yet. Plain max()
        would exit 2 and read as a setup message, hiding the regression."""
        self.assertEqual(ci_evals._worst([ci_evals.EXIT_USAGE,
                                          ci_evals.EXIT_BLOCKED]),
                         ci_evals.EXIT_BLOCKED)

    def test_a_cannot_answer_still_surfaces_when_nothing_blocked(self):
        self.assertEqual(ci_evals._worst([ci_evals.EXIT_OK,
                                          ci_evals.EXIT_USAGE]),
                         ci_evals.EXIT_USAGE)

    def test_all_green_is_green(self):
        self.assertEqual(ci_evals._worst([ci_evals.EXIT_OK]),
                         ci_evals.EXIT_OK)

    def test_an_empty_fan_out_is_green(self):
        self.assertEqual(ci_evals._worst([]), ci_evals.EXIT_OK)


class TestTheGateLogicIsPureAndTestableWithoutADisk(unittest.TestCase):
    """`compare()` takes two vectors and returns transitions. Kept pure so
    the table below is the specification, not a description of it."""

    TABLE = [
        ("pass", "pass", None),
        ("pass", "fail", ci_evals.REGRESSION),
        ("pass", "no_evidence", ci_evals.EVIDENCE_LOST),
        ("pass", "n/a", ci_evals.RETIRED),
        ("pass", None, ci_evals.DROPPED),
        ("fail", "pass", None),
        ("fail", "fail", None),
        ("fail", "no_evidence", None),
        ("no_evidence", "pass", None),
        ("no_evidence", "fail", None),
        ("no_evidence", "no_evidence", None),
        ("n/a", "fail", None),
        (None, "fail", None),
    ]

    def test_only_a_drop_from_a_pass_blocks(self):
        for was, now, kind in self.TABLE:
            base = {"X": was} if was else {}
            cur = {"X": now} if now else {}
            got = [t for t in ci_evals.compare(base, cur) if t["blocking"]]
            if kind is None:
                self.assertEqual(got, [], f"{was} -> {now}")
            else:
                self.assertEqual([t["kind"] for t in got], [kind],
                                 f"{was} -> {now}")

    def test_the_two_named_transitions_are_distinguishable_in_the_report(self):
        """A regression and a lost instrument have different fixes; a gate
        that printed one word for both would send people to the wrong one."""
        self.assertNotEqual(ci_evals.REGRESSION, ci_evals.EVIDENCE_LOST)

    def test_a_row_that_held_at_pass_is_reported_as_held(self):
        got = ci_evals.compare({"X": "pass"}, {"X": "pass"})
        self.assertEqual([t["kind"] for t in got], [ci_evals.HELD])

    def test_an_improvement_is_reported_and_does_not_block(self):
        got = ci_evals.compare({"X": "fail"}, {"X": "pass"})
        self.assertEqual([t["kind"] for t in got], [ci_evals.IMPROVED])
        self.assertFalse(got[0]["blocking"])


# ============================================ 8: a waiver must declare bounds

class TestAWaiverWithNoBoundIsRefusedRatherThanKeptForever(_Gated):
    """The hole this closes: `from`/`to` narrow WHICH transition a waiver
    covers, and nothing narrowed it in time or in relevance. A sentence
    written once for a transient evidence gap covered that step's drops
    forever, and never asked to be re-read.

    Both bounds are required and there is no grandfathering: the tree carries
    no waiver yet, so strict is free today and impossible later.
    """

    def test_a_waiver_with_neither_bound_does_not_cover_the_drop_it_names(
            self):
        self.regress()
        row = self.waiver()
        row.pop("inputs_fingerprint")
        row.pop("expires")
        self.write_waivers([row])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_the_refusal_names_the_line_and_the_bound_it_is_missing(self):
        """"Something is wrong with your waivers" sends nobody anywhere. The
        row number and the missing field are what make it fixable."""
        self.regress()
        row = self.waiver()
        row.pop("inputs_fingerprint")
        self.write_waivers([row])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("inputs_fingerprint", said)
        self.assertIn("line 1", said)

    def test_a_waiver_with_no_expiry_is_refused_and_the_field_is_named(self):
        self.regress()
        row = self.waiver()
        row.pop("expires")
        self.write_waivers([row])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("expires", self.out(r))

    def test_an_unbounded_waiver_blocks_even_when_no_verdict_dropped(self):
        """REFUSED, not ignored. A malformed row is the one someone could not
        write correctly, and skipping it is how a broken control reads green
        right up until the drop it was meant to cover arrives."""
        self.write_waivers([{"step": "S3", "reason": "r",
                             "acceptor": "Vlad Demkiv"}])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))

    def test_a_fingerprint_bound_that_is_not_a_digest_is_refused(self):
        """A bound nobody can compute is not a bound. `soon`, `the next
        release` and `abc` all read as diligence and check nothing."""
        self.regress()
        self.write_waivers([self.waiver(inputs_fingerprint="soon")])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("inputs_fingerprint", self.out(r))

    def test_an_expiry_that_is_not_a_date_is_refused(self):
        self.regress()
        self.write_waivers([self.waiver(expires="when #412 lands")])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("expires", self.out(r))

    def test_an_expiry_past_the_horizon_is_refused_as_forever_in_a_costume(
            self):
        """`expires: 2999-01-01` satisfies a required-field check perfectly
        and bounds nothing. The horizon is what makes the field cost
        something: an unauthenticated waiver has to be re-read on a schedule
        or it stops working."""
        self.regress()
        self.write_waivers([self.waiver(expires=_day(3650))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(ci_evals.WAIVER_MAX_HORIZON_DAYS), self.out(r))

    def test_a_waiver_bounded_on_both_axes_covers_its_drop(self):
        """The control case: strictness that refused everything would just be
        a broken gate."""
        self.regress()
        self.write_waivers([self.waiver()])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_an_expiry_at_the_horizon_exactly_is_accepted(self):
        self.regress()
        self.write_waivers([self.waiver(
            expires=_day(ci_evals.WAIVER_MAX_HORIZON_DAYS))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_the_fingerprint_bound_may_be_written_as_a_readable_prefix(self):
        """A 64-character hex copied by hand is a field people get wrong. A
        prefix long enough to name one flow is still tied to that flow."""
        self.regress()
        self.write_waivers([self.waiver(
            inputs_fingerprint=self.fingerprint()[
                :ci_evals.FINGERPRINT_BOUND_MIN_CHARS])])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_a_prefix_too_short_to_name_one_flow_is_refused(self):
        self.regress()
        self.write_waivers([self.waiver(inputs_fingerprint="ab")])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)


# =================================== 9: a waiver stops applying, out loud

class TestAWaiverStopsCoveringADropWhenTheFlowItNamedHasMoved(_Gated):
    """The bound that is tied to the thing that changed rather than to the
    calendar. A drop that reappears after the skill's flow moved is a
    DIFFERENT drop — the sentence someone wrote about the old flow is not an
    argument about the new one, and it deserves a fresh human sentence."""

    def _move_the_flow(self):
        """The skill changes, and the run and the baseline are both
        re-recorded against it — so nothing is stale and the ONLY thing left
        deciding the gate is whether the old waiver still applies."""
        self.write_source(SKILL_MD + NEW_MANDATE)
        self.write_scenario()
        self.write_run()
        self.assertEqual(self.cli("--set-baseline", SKILL).returncode, 0)

    def test_a_waiver_written_at_other_inputs_does_not_cover_this_drop(self):
        self.regress()
        self.write_waivers([self.waiver(inputs_fingerprint="0" * 64)])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_the_gate_names_the_waiver_and_both_fingerprints(self):
        """Silence here is the failure mode: the drop would surface as if it
        were new, and the waiver that stopped applying would be invisible."""
        self.regress()
        self.write_waivers([self.waiver(inputs_fingerprint="0" * 64)])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("OUT OF SCOPE", said)
        self.assertIn("line 1", said)
        self.assertIn("0000", said)
        self.assertIn(self.fingerprint()[:12], said)

    def test_the_same_waiver_stops_covering_once_the_skill_actually_changes(
            self):
        """The real sequence, not a doctored digest: the waiver covers the
        drop today, the flow moves, everything is re-recorded, the same row
        drops again — and the old sentence no longer answers for it."""
        self.regress()
        self.write_waivers([self.waiver()])
        self.assertEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)
        self._move_the_flow()
        self.regress()
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))
        self.assertIn("S3", self.out(r))
        self.assertIn("OUT OF SCOPE", self.out(r))

    def test_a_fresh_waiver_written_against_the_new_flow_covers_it_again(self):
        self._move_the_flow()
        self.regress()
        self.write_waivers([self.waiver(
            inputs_fingerprint=self.fingerprint(),
            reason="the same gap, re-argued against the flow that moved")])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_a_waiver_out_of_scope_is_reported_but_does_not_block_by_itself(
            self):
        """The log is append-only, so a spent waiver can never be deleted. It
        has to be able to retire quietly-but-visibly, or the first waiver
        anyone writes reddens the build forever."""
        self.write_waivers([self.waiver(inputs_fingerprint="0" * 64)])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertIn("OUT OF SCOPE", self.out(r))


class TestAnExpiredWaiverBlocksInsteadOfQuietlyCeasingToApply(_Gated):

    def test_an_expired_waiver_does_not_cover_the_drop_it_names(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(-1))])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_the_gate_names_the_expired_waiver_its_date_and_its_acceptor(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(-1))])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("EXPIRED", said)
        self.assertIn(_day(-1), said)
        self.assertIn("Vlad Demkiv", said)

    def test_an_expired_waiver_blocks_even_when_every_verdict_held(self):
        """The failure mode being closed: a waiver quietly ceasing to apply,
        and the regression it used to cover arriving later looking new. An
        expiry that lapses in silence is the same silence one step earlier."""
        self.write_waivers([self.waiver(expires=_day(-1))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertNotEqual(r.returncode, 0, self.out(r))
        self.assertIn("EXPIRED", self.out(r))

    def test_expiry_is_end_of_the_named_day_not_the_start_of_it(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(0))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_a_renewal_appended_below_it_supersedes_an_expired_row(self):
        """The only way to answer an expired waiver in an append-only log is
        to write a new one. Re-reading the reason and signing it again is the
        cost the bound exists to impose."""
        self.regress()
        self.write_waivers([self.waiver(expires=_day(-1)),
                            self.waiver(expires=_day(30),
                                        reason="re-read on the retro, still "
                                               "the same recorder gap")])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))

    def test_the_superseded_row_is_still_reported_never_erased(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(-1)),
                            self.waiver(expires=_day(30), reason="re-read")])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("EXPIRED", said)
        self.assertIn("superseded", said.lower())

    def test_a_renewal_of_a_different_transition_does_not_answer_for_it(self):
        """Supersession is per (step, from, to), the same narrowing the
        waiver itself carries. A renewal filed against another row is not a
        re-reading of this one."""
        self.regress()
        self.write_waivers([self.waiver(expires=_day(-1)),
                            self.waiver(step="S1", expires=_day(30))])
        self.assertNotEqual(self.cli("--gate", "--skill", SKILL).returncode, 0)

    def test_an_expired_waiver_about_a_flow_that_moved_retires_quietly(self):
        """Scope is checked before the clock. A waiver that no longer speaks
        about this flow has nothing left to re-read, and demanding a renewal
        of it would be busywork with a red build attached."""
        self.write_waivers([self.waiver(inputs_fingerprint="0" * 64,
                                        expires=_day(-1))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertIn("OUT OF SCOPE", self.out(r))


class TestAWaiverApproachingItsBoundIsReportedBeforeItBites(_Gated):
    """Expiry that first shows up as a broken build teaches people to renew
    without reading. An ordinary green gate run is where it should surface."""

    def test_a_green_gate_still_names_a_waiver_that_is_about_to_expire(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(1))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertIn("EXPIRING", self.out(r))

    def test_the_warning_names_the_step_the_date_and_the_days_left(self):
        self.regress()
        self.write_waivers([self.waiver(expires=_day(2))])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertIn("S3", said)
        self.assertIn(_day(2), said)
        self.assertIn("2 day", said)

    def test_a_waiver_far_from_its_bound_is_not_shouted_about(self):
        """A warning that is always on is a warning nobody reads."""
        self.regress()
        self.write_waivers([self.waiver(
            expires=_day(ci_evals.WAIVER_WARN_DAYS + 5))])
        said = self.out(self.cli("--gate", "--skill", SKILL))
        self.assertNotIn("EXPIRING", said)

    def test_a_waiver_expiring_soon_is_reported_even_with_nothing_to_waive(
            self):
        """It has to be reported by an ORDINARY run — the one nobody is
        debugging — or the tool never prompts the re-reading."""
        self.write_waivers([self.waiver(expires=_day(1))])
        r = self.cli("--gate", "--skill", SKILL)
        self.assertEqual(r.returncode, 0, self.out(r))
        self.assertIn("EXPIRING", self.out(r))

    def test_the_json_report_carries_the_same_notices(self):
        self.write_waivers([self.waiver(expires=_day(1))])
        report = json.loads(
            self.cli("--gate", "--skill", SKILL, "--json").stdout)
        self.assertFalse(report["blocked"])
        states = [n["state"] for n in report["waiver_notices"]]
        self.assertEqual(states, [ci_evals.EXPIRING])

    def test_the_json_report_marks_an_expired_waiver_as_blocking(self):
        self.write_waivers([self.waiver(expires=_day(-1))])
        r = self.cli("--gate", "--skill", SKILL, "--json")
        report = json.loads(r.stdout)
        self.assertTrue(report["blocked"])
        self.assertEqual([n["state"] for n in report["waiver_notices"]],
                         [ci_evals.EXPIRED])
        self.assertEqual(r.returncode, 1)


class TestTheWaiverBoundLogicIsPureAndTestableWithoutADisk(unittest.TestCase):
    """`waiver_status()` takes a row, the fingerprint of the run being gated
    and a date, and returns what that waiver is worth today. Kept pure so the
    table below is the specification rather than a description of it."""

    FP = "a" * 64
    TODAY = datetime.date(2026, 8, 13)

    def _row(self, **kw):
        row = {"step": "S1", "reason": "r", "acceptor": "A Person",
               "inputs_fingerprint": self.FP, "expires": "2026-09-01",
               "_line": 1}
        row.update(kw)
        return row

    def test_a_row_inside_both_bounds_is_in_force(self):
        st = ci_evals.waiver_status(self._row(), self.FP, self.TODAY)
        self.assertEqual(st["state"], ci_evals.IN_FORCE)
        self.assertTrue(st["covers"])

    def test_a_row_past_its_date_covers_nothing_and_blocks(self):
        st = ci_evals.waiver_status(self._row(expires="2026-08-12"), self.FP,
                                    self.TODAY)
        self.assertEqual(st["state"], ci_evals.EXPIRED)
        self.assertFalse(st["covers"])
        self.assertTrue(st["blocking"])

    def test_a_row_about_another_flow_covers_nothing_and_does_not_block(self):
        st = ci_evals.waiver_status(self._row(), "b" * 64, self.TODAY)
        self.assertEqual(st["state"], ci_evals.OUT_OF_SCOPE)
        self.assertFalse(st["covers"])
        self.assertFalse(st["blocking"])

    def test_a_row_near_its_date_still_covers_and_is_flagged(self):
        st = ci_evals.waiver_status(self._row(expires="2026-08-14"), self.FP,
                                    self.TODAY)
        self.assertEqual(st["state"], ci_evals.EXPIRING)
        self.assertTrue(st["covers"])
        self.assertFalse(st["blocking"])

    def test_a_run_with_no_fingerprint_puts_every_waiver_out_of_scope(self):
        """A run that records no `inputs_fingerprint` is already stale by
        `stale_reasons()`. Nothing may be waived against a flow nobody can
        name."""
        st = ci_evals.waiver_status(self._row(), None, self.TODAY)
        self.assertFalse(st["covers"])

    def test_a_row_whose_bounds_were_never_checked_waives_nothing(self):
        """Fail CLOSED. The bounds are evaluated against the run being gated;
        a caller that skipped that step must get a loud gate full of unwaived
        drops, never the silent forever-waiver this section removes."""
        got = ci_evals.apply_waivers(
            ci_evals.compare({"S1": "pass"}, {"S1": "fail"}), [self._row()])
        self.assertTrue(got[0]["blocking"])
        self.assertIsNone(got[0]["waiver"])

    def test_every_state_explains_itself_in_words(self):
        for run_fp, expires in ((self.FP, "2026-09-01"),
                                (self.FP, "2026-08-12"),
                                ("b" * 64, "2026-09-01"),
                                (self.FP, "2026-08-14")):
            st = ci_evals.waiver_status(self._row(expires=expires), run_fp,
                                        self.TODAY)
            self.assertTrue(st["why"].strip(), st)


if __name__ == "__main__":
    unittest.main()
