"""The eval-record loader: one shape, one walker, nothing opened on faith.

THE DEFECT THIS PINS. `ci_evals._score_corpus()` listed every directory under
`evals/` and opened `<dir>/expected.json` unconditionally:

    exp_path = os.path.join(d, "expected.json")
    with io.open(exp_path, encoding="utf-8") as f:

Every directory under `evals/` was therefore *assumed* to be a scored profile.
The corpus happened to contain exactly four of those, so it happened to work.
The moment the eval layer grows the directories it was designed to grow —
`evals/scenarios/`, `evals/negative/`, `evals/runs/`, `evals/baselines/`,
`evals/fixture-repo/` — `--corpus` raises FileNotFoundError on the first one
and the whole instrument is unavailable. A loader that cannot say "this is not
a record" is not a loader; it is an assumption with a file handle.

`taskplane/tests/test_evals_obligations.py` carried a SECOND copy of the same
walker (`TestTheCorpusProvesTheScorer`), with the same unconditional open. A
hazard fixed in one of its two homes is not fixed, so that test now routes
through `ci_evals._discover` too.

WHAT IS PINNED HERE.

1. Discrimination. A directory is a record because it carries a MARKER —
   `expected.json` (a verdict vector) or `run.json` (identity/eligibility).
   The markers are ADDITIVE: both together is an eligibility fixture, neither
   means not-a-record. Callers branch on `is_record`/`missing`, never on which
   marker was found, so a later kind of record needs no new caller.

2. Absence is never silence. A record missing one of `RECORD_FILES` is
   reported UNLOADABLE, naming the file, and exits non-zero — it is never
   scored as an empty session, because an empty session scores `no evidence`
   and that would launder a broken fixture into an honest unknown.

3. `derivations.jsonl` is deliberately NOT required. `evals/negative/no-ledger/`
   has to be loadable and score `no_evidence`; if a missing derivation ledger
   made the record unloadable, the "an absent record is never a pass"
   invariant would be untestable.

4. Nothing loosened. `score()`, the six-entry `AREAS` tuple and the FACT/CLAIM
   column split are pinned by sha256 of their BASELINE source segments, the 16
   corpus files by sha256 of their bytes, and `--corpus` by a golden capture of
   the baseline's own stdout. `git diff --stat evals/` proves nothing once
   anything commits; these hashes keep proving it.

Every assertion here was observed FAILING before it was kept.
"""
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ci_evals  # noqa: E402

CORPUS = os.path.join(REPO, "evals")

# The five directories the eval layer is designed to grow. Each one is a
# landing hazard for the baseline loader; none of them is a scored profile.
PLANNED_DIRS = ("scenarios", "negative", "runs", "baselines", "fixture-repo")

# --- the pins -------------------------------------------------------------
# Computed from the BASELINE blob (`git show HEAD:scripts/ci_evals.py` at the
# pre-change mirror commit) and from the corpus bytes as they stood there.
# Pinning them from the edited file would have pinned the change itself.
BASELINE_SCORE_SHA = \
    "edd0ed955ca06e3d1b9be5fee93a61894c63a32267189310d7e0f105df41b2bd"
BASELINE_AREAS_SHA = \
    "7dd6d5ad967c5b9cfca6e72c8fe31f69dd8ae11032cd643f366d31b90c5ca059"
# Every rate the instrument prints comes out of `_pct`, and it is one line
# long. Pinning `score()` alone would leave `return 1.0` in a helper as a way
# to make everything pass without touching the function the pin names.
BASELINE_PCT_SHA = \
    "c6bad89336480262916fa484420303d0f3e0e3bbf43389d96fa0869808b07043"
# The record reader `_rows` is deliberately NOT pinned: it is the loader's
# own dependency and the golden capture covers it end to end.
# The four frozen corpora, by name. Anything else under evals/ is the
# eval layer's own growing fixture set and is NOT pinned here.
FROZEN_CORPORA = ("compliant", "skipped-render", "substitute-graph",
                  "no-hook-one-host")

BASELINE_CORPUS_SHA = {
    "compliant/dispatch.json":
        "cad1b14b74990fe1dfa50068154457822e3adfde2212aef7e4ace39fd2b1c479",
    "compliant/expected.json":
        "24fd73ec6bbe63addbc77891fcf55d10b45583fce710ff87c354ed9c3aec5a73",
    "compliant/obligations.jsonl":
        "10fa4ab396594a1e1c5d0b1a1321f3343c9ed2eb5127d92b6acb60612f679af5",
    "compliant/trace.jsonl":
        "b2f86fa5add7b0f7ba2b048ec5c35ec223c85cfe747dba656e659b76011c157b",
    "no-hook-one-host/dispatch.json":
        "5bd965cfd6130ed238c123643af7a01f8a19a9f97385ad05497891e723d27ee1",
    "no-hook-one-host/expected.json":
        "19e6506de6663d312e0423b8639d7a49779433f5f11a3267b3836e7e545f0ae9",
    "no-hook-one-host/obligations.jsonl":
        "2fa2869627a006f9bb0758f31e79dbead4174e4ba29a81af396bcb92c50cecd9",
    "no-hook-one-host/trace.jsonl":
        "6ab58ca2ca626ca87cd7413275e9d95074e2529e60a99d61a4f4b676500fe160",
    "skipped-render/dispatch.json":
        "610f966176b51045fd15a8b4116116f840b0097e8d8dd4dcf35339394bd11b3a",
    "skipped-render/expected.json":
        "c42031a58de08299ce84a76897e9987f09abe4d65b9f645fa1aaf1f977490397",
    "skipped-render/obligations.jsonl":
        "906e9d20ad5a35eca5d4eaa161e267f137064a509816c4fcd67f7624c38a1e65",
    "skipped-render/trace.jsonl":
        "101a21f6d9b69060fac50299297af6019c430cc91a0b2b23cf9b6db41e7caccf",
    "substitute-graph/dispatch.json":
        "5bb7f9b7434a40df7134e65e4bb864aca5312f9992f60428eecbd4c3897f3822",
    "substitute-graph/expected.json":
        "c23eea4a5020ff429f060e4fcbb87d53a72b65db667fed6dc7aa77df92190687",
    "substitute-graph/obligations.jsonl":
        "a7f89b433b18ea366ccd085f44a5ac69c36e59f60907e083ccda8784ada8b1de",
    "substitute-graph/trace.jsonl":
        "905802877f12d3982055f5ff1944f1a98f391bfa2e6ff74628166430e1c731cd",
}

# `python3 scripts/ci_evals.py --corpus`, captured from the BASELINE blob
# before a line of this change existed. The four profiles at the rates their
# expected.json files declare, word for word.
BASELINE_CORPUS_STDOUT = """evals — the scorer against sessions whose answer is known

  compliant
    artifact_surfacing   claim 100%   (1/1 shown, 0 skipped, 0 substituted)
    product_graph        claim 100%   (1/1 shown, 0 skipped, 0 substituted)
    agent_fanout         fact  100%   (8/8 dispatched)
    skill_flow           fact  100%   (4 steps, 4 distinct)
    gate_discipline      fact  100%   (1 approvals, 0 unattributed)
    cross_host           fact  no evidence   (claude)
      note: one host in this record — parity is UNKNOWN until the same scenario runs on another

    why: Every artifact the engine built was shown, the fan-out landed all eight lens subagents, the approval carries a human's name, and every step is one the engine's own machine declares.

  no-hook-one-host
    artifact_surfacing   claim 100%   (1/1 shown, 0 skipped, 0 substituted)
    product_graph        claim no evidence   (0/0 shown, 0 skipped, 0 substituted)
    agent_fanout         fact  no evidence   (0/5 dispatched)
      note: no dispatches observed — the PreToolUse Task hook was not active, so fan-out is UNKNOWN for this session, not zero
    skill_flow           fact  100%   (1 steps, 1 distinct)
    gate_discipline      fact  no evidence   (0 approvals, 0 unattributed)
    cross_host           fact  no evidence   (codex)
      note: one host in this record — parity is UNKNOWN until the same scenario runs on another

    why: The instrument must not slander a session for what it could not see. With no PreToolUse Task hook the engine observes zero dispatches, which is indistinguishable from dispatching none — so fan-out reports UNKNOWN, not 0%. Same for parity on one host, and for gate discipline with no approval yet.

  skipped-render
    artifact_surfacing   claim   0%   (0/2 shown, 2 skipped, 0 substituted)
    product_graph        claim   0%   (0/1 shown, 1 skipped, 0 substituted)
    agent_fanout         fact   25%   (2/8 dispatched)
    skill_flow           fact  100%   (2 steps, 2 distinct)
    gate_discipline      fact  no evidence   (0 approvals, 0 unattributed)
    cross_host           fact  no evidence   (claude)
      note: one host in this record — parity is UNKNOWN until the same scenario runs on another

    why: 'here we go again no inline dashboard visualisation. no report nothing?' — the engine issued three render obligations and none was acknowledged, and six of eight lens subagents never dispatched. A green engine; this is what the user saw.

  substitute-graph
    artifact_surfacing   claim 100%   (1/1 shown, 0 skipped, 0 substituted)
    product_graph        claim   0%   (0/1 shown, 0 skipped, 1 substituted)
    agent_fanout         fact  100%   (4/4 dispatched)
    skill_flow           fact  100%   (1 steps, 1 distinct)
    gate_discipline      fact    0%   (1 approvals, 1 unattributed)
    cross_host           fact  no evidence   (claude)
      note: one host in this record — parity is UNKNOWN until the same scenario runs on another

    why: 'this is not the graph and dependency visualisation we designed' — the graph obligation WAS acknowledged, citing a fingerprint that is not the artifact the engine built. A substitute is a different failure from a skip and is counted as one. The self-approved gate is the second defect in this session.

  The corpus proves the SCORER. Real sessions are what it is for.
"""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _segment(name):
    """The exact source text of a top-level `def name` or `NAME = ...`."""
    with io.open(os.path.join(REPO, "scripts", "ci_evals.py"),
                 encoding="utf-8") as f:
        src = f.read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} is gone from scripts/ci_evals.py")


class _Tree(unittest.TestCase):
    """Builds eval trees on disk. Nothing here touches the real corpus."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tp-evalrec-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _dir(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def _write(self, d, name, payload):
        with io.open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(payload if isinstance(payload, str)
                    else json.dumps(payload))

    def _record(self, *parts, **kw):
        """A directory with the three record files plus chosen markers."""
        d = self._dir(*parts)
        for name in kw.get("files", ci_evals.RECORD_FILES):
            self._write(d, name, "{}" if name.endswith(".json") else "")
        if kw.get("expected") is not None:
            self._write(d, "expected.json", kw["expected"])
        if kw.get("run") is not None:
            self._write(d, "run.json", kw["run"])
        return d


class TestAMarkerIsWhatMakesADirectoryARecord(_Tree):
    """The discrimination the baseline did not have: it treated every
    directory under evals/ as a scored profile and opened expected.json on
    faith."""

    def test_a_directory_carrying_neither_marker_is_not_a_record(self):
        d = self._dir("fixture-repo")
        self._write(d, "README.md", "a repo to run scenarios against")
        rec = ci_evals.load_record(d, "fixture-repo")
        self.assertFalse(rec["is_record"])
        self.assertIn("expected.json", rec["reason"])
        self.assertIn("run.json", rec["reason"])

    def test_a_non_record_is_reported_by_name_and_never_opened(self):
        """The baseline's failure was an OPEN, not a verdict. Proving the
        reason is stated is not enough — the file handle must never be
        asked for, or the same FileNotFoundError comes back."""
        self._dir("fixture-repo", "src")
        self._record("compliant", expected={"rates": {}})
        opened = []
        real = ci_evals.io

        class _Spy(object):
            def open(self, path, *a, **kw):
                opened.append(path)
                return real.open(path, *a, **kw)

        ci_evals.io = _Spy()
        self.addCleanup(setattr, ci_evals, "io", real)
        records, skipped = ci_evals._discover(self.root)
        self.assertEqual([r["name"] for r in records], ["compliant"])
        self.assertEqual([r["name"] for r in skipped], ["fixture-repo"])
        self.assertTrue(skipped[0]["reason"])
        for path in opened:
            self.assertNotIn("fixture-repo", path)

    def test_expected_and_run_are_additive_so_both_together_is_a_record(self):
        """A fixture that pins a verdict AND carries run identity is an
        eligibility fixture. Exclusive markers would force the loader to
        pick one meaning and drop the other."""
        d = self._record("elig", expected={"rates": {}},
                         run={"skill": "tp-go", "eligible": True})
        rec = ci_evals.load_record(d, "elig")
        self.assertTrue(rec["is_record"])
        self.assertEqual(rec["expected"], {"rates": {}})
        self.assertEqual(rec["run"]["skill"], "tp-go")

    def test_run_alone_is_a_record_with_no_verdict_vector(self):
        d = self._record("r1", run={"skill": "tp-go"})
        rec = ci_evals.load_record(d, "r1")
        self.assertTrue(rec["is_record"])
        self.assertIsNone(rec["expected"])

    def test_every_record_kind_returns_one_identical_shape(self):
        """ONE loader, one dict. A caller that has to know which marker was
        found is a caller that breaks when the next kind lands."""
        kinds = [
            ci_evals.load_record(self._record("a", expected={}), "a"),
            ci_evals.load_record(self._record("b", run={}), "b"),
            ci_evals.load_record(self._record("c", expected={}, run={}), "c"),
            ci_evals.load_record(self._dir("d"), "d"),
        ]
        first = sorted(kinds[0])
        for rec in kinds[1:]:
            self.assertEqual(sorted(rec), first)


class TestAnAbsentRecordFileIsNamedNotScoredAsEmpty(_Tree):
    """An empty session scores `no evidence` in every area. Scoring a broken
    fixture that way would launder it into an honest unknown — the one thing
    this instrument's whole design forbids."""

    def test_a_missing_record_file_makes_the_record_unloadable_by_name(self):
        d = self._record("half", expected={"rates": {}},
                         files=("obligations.jsonl", "dispatch.json"))
        rec = ci_evals.load_record(d, "half")
        self.assertTrue(rec["is_record"])
        self.assertFalse(rec["loadable"])
        self.assertEqual(rec["missing"], ("trace.jsonl",))
        self.assertIn("trace.jsonl", rec["reason"])

    def test_unparseable_json_is_reported_rather_than_raised(self):
        d = self._record("bad", expected={"rates": {}})
        self._write(d, "dispatch.json", "{not json")
        rec = ci_evals.load_record(d, "bad")
        self.assertFalse(rec["loadable"])
        self.assertIn("dispatch.json", rec["reason"])

    def test_a_derivation_ledger_is_not_required_of_a_record(self):
        """evals/negative/no-ledger/ must LOAD and score `no evidence`. If an
        absent derivations.jsonl made it unloadable, the invariant it exists
        to prove — an absent record is never a pass — could not be tested."""
        self.assertNotIn("derivations.jsonl", ci_evals.RECORD_FILES)
        self.assertEqual(ci_evals.RECORD_FILES,
                         ("trace.jsonl", "obligations.jsonl", "dispatch.json"))
        d = self._record("no-ledger", expected={"rates": {}})
        rec = ci_evals.load_record(d, "no-ledger")
        self.assertTrue(rec["loadable"], rec["reason"])
        res = ci_evals.score(rec["trace"], rec["obligations"], rec["dispatch"])
        self.assertIsNone(res["artifact_surfacing"]["rate"])


class TestOneWalkerFindsRecordsWhereTheLayerPutsThem(_Tree):
    """Records live at evals/negative/<name>/ (two levels) and at
    evals/runs/<skill>/<run_id>/ (three). A one-level walker misses the
    second; an unbounded one walks a fixture repo's whole source tree."""

    def test_records_are_found_two_and_three_levels_down(self):
        self._record("negative", "no-ledger", expected={"rates": {}})
        self._record("runs", "tp-go", "2026-08-13T00", run={"skill": "tp-go"})
        records, _ = ci_evals._discover(self.root)
        self.assertEqual(sorted(r["name"] for r in records),
                         ["negative/no-ledger", "runs/tp-go/2026-08-13T00"])

    def test_a_record_stops_the_descent(self):
        """A record's own subdirectories are its payload, not more records."""
        d = self._record("compliant", expected={"rates": {}})
        self._record(os.path.basename(d), "artifacts", expected={"rates": {}})
        records, _ = ci_evals._discover(self.root)
        self.assertEqual([r["name"] for r in records], ["compliant"])

    def test_recursion_stops_at_max_depth_and_says_so(self):
        """MAX_DEPTH is what keeps evals/fixture-repo/ from being walked as
        if it were a corpus."""
        self.assertEqual(ci_evals.MAX_DEPTH, 3)
        self._record("a", "b", "c", "d", expected={"rates": {}})
        records, skipped = ci_evals._discover(self.root)
        self.assertEqual(records, [])
        self.assertEqual([r["name"] for r in skipped], ["a"])
        self.assertIn(f"within {ci_evals.MAX_DEPTH} levels",
                      skipped[0]["reason"])

    def test_git_metadata_is_never_walked(self):
        self._record(".git", "modules", expected={"rates": {}})
        records, skipped = ci_evals._discover(self.root)
        self.assertEqual(records, [])
        self.assertIn(".git", [r["name"] for r in skipped])

    def test_a_dot_directory_that_is_not_git_is_still_walked(self):
        """Skipping every dot-directory blindly would silently drop a record
        someone deliberately hid from a packager."""
        self._record(".staged", "run-1", expected={"rates": {}})
        records, _ = ci_evals._discover(self.root)
        self.assertEqual([r["name"] for r in records], [".staged/run-1"])

    def test_a_container_that_holds_records_is_not_itself_reported_skipped(self):
        self._record("runs", "tp-go", run={})
        records, skipped = ci_evals._discover(self.root)
        self.assertEqual([r["name"] for r in records], ["runs/tp-go"])
        self.assertEqual(skipped, [])


class TestTheCorpusRunSurvivesTheDirectoriesTheLayerWillGrow(_Tree):
    """The landing hazard, end to end through the real CLI."""

    def _tree(self):
        scripts = self._dir("scripts")
        shutil.copyfile(os.path.join(REPO, "scripts", "ci_evals.py"),
                        os.path.join(scripts, "ci_evals.py"))
        evals = self._dir("evals")
        for name in sorted(os.listdir(CORPUS)):
            shutil.copytree(os.path.join(CORPUS, name),
                            os.path.join(evals, name))
        return os.path.join(scripts, "ci_evals.py"), evals

    def _run(self, script):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.join(REPO, "taskplane"), env.get("PYTHONPATH", "")])
        return subprocess.run([sys.executable, script, "--corpus"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    def test_the_five_planned_directories_do_not_break_the_corpus_run(self):
        """scenarios/, negative/, runs/, baselines/ and fixture-repo/ are the
        eval layer's own roadmap. The baseline loader raised FileNotFoundError
        on the first of them to be created, taking `--corpus` down with it."""
        script, evals = self._tree()
        for name in PLANNED_DIRS:
            os.makedirs(os.path.join(evals, name), exist_ok=True)
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in PLANNED_DIRS:
            self.assertIn(name, r.stdout + r.stderr,
                          "an ignored directory must be named, not silent")
        self.assertIn("compliant", r.stdout)

    def test_a_fixture_naming_an_area_that_does_not_exist_is_told_so(self):
        """The same defect one layer up: a fixture is DATA. `expected.json`
        was indexed straight into the result (`res[area]`), so one typo in a
        future fixture is a KeyError traceback — which reads as the
        instrument being broken rather than the fixture being wrong."""
        script, evals = self._tree()
        p = os.path.join(evals, "compliant", "expected.json")
        with io.open(p, encoding="utf-8") as f:
            expected = json.load(f)
        expected["rates"]["artefact_surfacing"] = 1.0   # British-spelled typo
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(expected, f)
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("artefact_surfacing", r.stderr)
        self.assertNotIn("KeyError", r.stderr)

    def test_an_unloadable_record_fails_the_run_and_names_the_file(self):
        script, evals = self._tree()
        os.remove(os.path.join(evals, "compliant", "trace.jsonl"))
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("trace.jsonl", r.stderr)
        self.assertIn("compliant", r.stderr)


class TestNothingInTheScorerWasLoosenedToMakeThisLand(unittest.TestCase):
    """A loader change that quietly relaxed a rate, dropped an area or edited
    a fixture would look exactly like this one in a diff stat."""

    def test_the_scoring_function_is_byte_identical_to_the_baseline(self):
        self.assertEqual(_sha(_segment("score")), BASELINE_SCORE_SHA,
                         "score() changed; the corpus no longer proves the "
                         "same scorer it proved before")

    def test_the_arithmetic_behind_every_rate_is_byte_identical(self):
        self.assertEqual(_sha(_segment("_pct")), BASELINE_PCT_SHA)

    def test_the_six_areas_are_still_the_six_the_layer_was_designed_around(self):
        self.assertEqual(_sha(_segment("AREAS")), BASELINE_AREAS_SHA)
        self.assertEqual(ci_evals.AREAS,
                         ("artifact_surfacing", "product_graph",
                          "agent_fanout", "skill_flow", "gate_discipline",
                          "cross_host"))

    def test_claims_and_facts_still_never_share_a_column(self):
        res = ci_evals.score([], [], {"expected": 0, "unobserved": 0,
                                      "hook_active": True})
        self.assertEqual([res[a]["source"] for a in ci_evals.AREAS],
                         ["claim", "claim", "fact", "fact", "fact", "fact"])

    def test_every_corpus_fixture_is_byte_unchanged(self):
        """The four FROZEN corpora must not move. Deliberately scoped to
        them by name rather than to a walk of all of `evals/`: this test's
        own sibling blesses growing `evals/negative/` and `evals/runs/` as
        the roadmap this loader exists to unblock, so a whole-tree dict
        made the two tests contradict each other and turned every new
        fixture into a red build. Scoping keeps what it was for — a
        changed or vanished corpus file still fails, and so does a NEW
        file inside one of the four."""
        found = {}
        for dirpath, _dirs, files in os.walk(CORPUS):
            for name in files:
                p = os.path.join(dirpath, name)
                rel = os.path.relpath(p, CORPUS).replace(os.sep, "/")
                if rel.split("/", 1)[0] not in FROZEN_CORPORA:
                    continue
                with io.open(p, "rb") as f:
                    found[rel] = hashlib.sha256(f.read()).hexdigest()
        want = {k: v for k, v in BASELINE_CORPUS_SHA.items()
                if k.split("/", 1)[0] in FROZEN_CORPORA}
        self.assertEqual(found, want)

    def test_the_corpus_run_still_prints_what_the_baseline_printed(self):
        """`git diff --stat evals/` is empty the moment anything commits; a
        golden captured from the baseline blob keeps proving it.

        Every line the baseline printed must still be printed, in the same
        order — the four profiles, their rates, their `why`, their honest
        `no evidence` notes. ADDED lines are allowed and only added lines
        are: growing evals/negative/ and evals/runs/ is the roadmap this
        loader exists to unblock, and a byte-equality pin would be the same
        landmine in a new place. Nothing may be dropped, reordered or
        reworded, so no rate can quietly move. (At the commit that landed
        this, stdout is byte-identical to the capture.)
        """
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts", "ci_evals.py"),
             "--corpus"], capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = r.stdout.splitlines()
        at = 0
        for line in BASELINE_CORPUS_STDOUT.splitlines():
            while at < len(got) and got[at] != line:
                at += 1
            self.assertLess(at, len(got),
                            f"the baseline printed this line and the corpus "
                            f"run no longer does, or prints it out of "
                            f"order:\n  {line!r}")
            at += 1


if __name__ == "__main__":
    unittest.main()
