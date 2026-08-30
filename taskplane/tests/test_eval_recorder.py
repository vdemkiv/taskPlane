"""The recorder: a frozen pull request, a seam where the model goes, and a
record the pure scorer can grade.

WHY THIS LAYER IS SPLIT IN TWO. The scorer (`taskplane/eval_rubric.py`) is a
pure function of data and can therefore gate CI over recorded runs, forever,
for free. The recorder needs a MODEL and can never run in CI. Keeping them
apart is what makes a model-behaviour eval gateable at all — but it only
works if the recorder is itself testable, or the half that produces every
record is the half nobody checks.

So the model-driving step is a SEAM: `record_run(driver=...)` takes a
callable. In the field it is a real model; here it is a stub, and the whole
recorder — the fixture build, the credential scrub, the pre-flight probe, the
record synthesis, the eligibility rule — is exercised end to end at zero
token cost. That seam is the acceptance criterion, not a convenience.

WHAT IS PINNED HERE

1. Determinism of the fixture. Two builds into different destinations, under
   a deliberately hostile ambient identity, clock, TZ, locale and global git
   config, produce the SHAs `manifest.json` pins. One appended byte in a head
   file is refused — the pin covers tree CONTENT, not just metadata.

2. The fixture is invisible to the repository. Bare `pytest` at the root
   still collects cleanly (two `test_checkout.py` files under one rootdir is
   a collection abort, and CI's `taskplane/tests` scope would never see it),
   and `ci_evals.py --corpus` names the fixture as SKIPPED and exits 0.

3. The run is instrumented or it is nothing. `derivation.probe()` runs before
   the driver and a `None` aborts loudly; an unobserved run (`hook_active`
   false) and any in-session `subagent` run are recorded as ineligible for a
   baseline IN THE RECORD, not in prose.

4. The record is what the scorer reads. Exactly `eval_rubric.RECORD_FILES`'
   names, per-brief rows under the additive `eval_rubric.DISPATCH_ROWS` key,
   and a comparable `eval_scenario.ORDER_KEY` on every synthesized row —
   without which every cross-record ordering check can only return
   `no_evidence`.

5. The end-to-end proof. A stub-driver run is scored by the REAL
   `eval_rubric.evaluate()` against the REAL `evals/scenarios/tp-engineering.json`,
   and a deliberately non-compliant driver produces a DIFFERENT verdict vector
   with the expected steps failing. A recorder that can only produce passes is
   not a recorder.

Every assertion here was observed FAILING before it was kept.
"""
import importlib.util
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import derivation                      # noqa: E402
import eval_record                     # noqa: E402
import eval_rubric                     # noqa: E402
import eval_scenario                   # noqa: E402
import lens as lens_router             # noqa: E402
import loop as loop_mod                # noqa: E402
import obligations                     # noqa: E402
import review                          # noqa: E402
import target as target_mod            # noqa: E402
import taskplane_lite as tp            # noqa: E402

SCENARIO = os.path.join(REPO, "evals", "scenarios", "tp-engineering.json")

# What the ambient environment looks like when it is actively trying to leak
# into the fixture: a different identity, a different clock, a different TZ,
# a locale whose case rules differ, and a global git config that would flip
# line endings underneath the trees.
HOSTILE = {
    "GIT_AUTHOR_NAME": "Ambient Author",
    "GIT_AUTHOR_EMAIL": "ambient@example.invalid",
    "GIT_COMMITTER_NAME": "Ambient Committer",
    "GIT_COMMITTER_EMAIL": "ambient@example.invalid",
    "GIT_AUTHOR_DATE": "2001-02-03T04:05:06+09:00",
    "GIT_COMMITTER_DATE": "2009-08-07T06:05:04-05:00",
    "EMAIL": "ambient@example.invalid",
    "TZ": "Pacific/Kiritimati",
    "LC_ALL": "tr_TR.UTF-8",
    "LANG": "tr_TR.UTF-8",
}

# The three lenses a compliant stub review routes. A strict subset of the
# catalog, which is what the LEGACY (no-stage) router produces — and the one
# shape the routed-set inference could ever read correctly. The signal-driven
# router emits an entry for all 26 (n/a ones carry negative evidence), so
# `ENGINE_ROUTED_EVERYTHING` below is the shape a real routed review has.
ROUTED = ("security", "qa", "architecture")


def _env_patch(case, values):
    """Set env vars for one test and restore them exactly.

    conftest's module-scoped guard requires os.environ to be byte-identical
    after a module runs; a test that sets a variable and forgets changes what
    every LATER module sees.
    """
    for key, value in values.items():
        prior = os.environ.get(key)
        os.environ[key] = value
        case.addCleanup(_restore, key, prior)


def _restore(key, prior):
    if prior is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = prior


def _tmp(case, prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    case.addCleanup(shutil.rmtree, d, True)
    return d


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(body)


def _read_jsonl(path):
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ------------------------------------------------------------- the drivers
#
# What a governed engineering review DOES, minus the model. These live in the
# test and never in the recorder: a recorder that ships its own fake model
# could produce a compliant record with no model at all.

def _open_the_contract(ctx):
    tp.activate(ctx.ws, {
        "task_id": "eval-review",
        "task": "review the frozen pull request",
        "read_only": True,
        "write_allow": [".em-review/**"],
        "coding": {"scope_paths": []},
        "owes": ["review"],
    }, snapshot=None)
    tp.trace(ctx.ws, "dor", ready=True, blockers=[], warnings=[])


def _pin_and_derive_once(ctx):
    """Pin the target, then derive the diff and the blast radius ONCE."""
    rec = target_mod.pin(ctx.ws, base=ctx.base)
    target_mod.save(ctx.ws, rec)
    # ONE `tp review start` derives both `diff` and `impact` — that is why
    # the classification table maps it to two keys.
    derivation.record(ctx.ws, "tp review start", "allow")
    _, diff = target_mod.git(ctx.ws, "diff", ctx.base, ctx.head)
    impact = {"touched": ["pricing/checkout.py", "pricing/discount.py"],
              "total_impacted": 3}
    paths = review.write_context(
        ctx.ws, diff=diff or "(empty)", impact=impact,
        blast_radius="# Blast radius\n\npricing.checkout gains an import.\n")
    _write(os.path.join(ctx.ws, ".em-review", "findings.json"),
           json.dumps({"target_fingerprint": rec.get("fingerprint"),
                       "findings": [], "meta": {"routing_decision": "v2"}},
                      indent=2))
    tp.trace(ctx.ws, "graph_impact", step="em", scanned_head=ctx.head,
             touched=impact["touched"], impacted=impact["total_impacted"])
    return paths


def _brief_and_run(ctx, lenses, breadth="routed", engine_ran=True,
                   stamp=True):
    """Brief each lens, stamping the route with the breadth it was ASKED for.

    `stamp` mirrors `loop.py`: a route that ran under a governed workspace
    records `requested_breadth` / `engine_ran` on its own `lens_route` row.
    `stamp=False` is the fallback shape — a route the loop made with no
    workspace to record into (the pm/plan/design briefs, `prime_scope` at
    execute/fix, and parallel evaluate, whose breadth row lands in the
    worktree while this row lands in `ws`).
    """
    extra = ({"requested_breadth": breadth, "engine_ran": engine_ran}
             if stamp else {})
    tp.trace(ctx.ws, "lens_route", step="em", lenses=list(lenses), **extra)
    target = target_mod.load(ctx.ws)
    kernel = os.path.join(ctx.ws, ".em-review", "kernel-v2")
    envelope_rel = ".em-review/kernel-v2/envelope.json"
    envelope_path = os.path.join(ctx.ws, envelope_rel)
    envelope_body = json.dumps({"schema": "taskplane.review-envelope/v2",
                                "target": target}, sort_keys=True)
    _write(envelope_path, envelope_body)
    slots = []
    for lid in lenses:
        slot_id = "deep." + lid
        brief_rel = f".em-review/kernel-v2/briefs/{lid}.json"
        result_rel = f".em-review/kernel-v2/results/{lid}.json"
        _write(os.path.join(ctx.ws, brief_rel), json.dumps({"slot_id": slot_id}))
        slots.append({"slot_id": slot_id, "lens_ids": [lid],
                      "brief": {"relative_path": brief_rel},
                      "result_path": result_rel})
    run_id = "e" * 32
    state = {
        "schema": "taskplane.review-run-state/v2", "run_id": run_id,
        "status": "ready", "stage": "review", "target": target,
        "envelope": {"relative_path": envelope_rel, "fingerprint": "CTX",
                     "digest": hashlib.sha256(envelope_body.encode()).hexdigest(),
                     "bytes": len(envelope_body.encode())},
        "slots": slots,
    }
    _write(os.path.join(kernel, "runs", run_id, "state.json"),
           json.dumps(state, sort_keys=True))
    started = {
        "stage": "review", "run_id": run_id,
        "target_head": target.get("head"),
        "graph_quality_status": "complete",
        "context_fingerprint": "CTX",
    }
    if stamp:
        started.update({"routing_mode": ("selective" if breadth == "routed"
                                         else breadth),
                        "routing_complete": True,
                        "dispositions_complete": True})
    tp.trace(ctx.ws, "review_kernel_started", **started)
    for lid in lenses:
        tp.record_expected_dispatch(ctx.ws, "lens", "tp-lens", "standard",
                                    None, ref=lid,
                                    task_name=f"tp-lens-{lid}")
        # The PreToolUse Task hook, standing in for itself: without an
        # observation `hook_active` is false and the run may not set a
        # baseline, which is the rule under test elsewhere in this module.
        tp.record_observed_dispatch(ctx.ws, f"tp-lens-{lid}", None, None, True)
        tp.trace(ctx.ws, "subagent_start", agent_id=f"a-{lid}",
                 agent_type=f"tp-lens-{lid}")
        _write(os.path.join(ctx.ws, ".em-review", f"lens-{lid}",
                            "findings.json"),
               json.dumps({"lens": lid, "findings": []}, indent=2))
        _write(os.path.join(ctx.ws, ".em-review", "kernel-v2", "results",
                            f"{lid}.json"),
               json.dumps({"slot_id": "deep." + lid, "findings": []},
                          indent=2))


def _show_the_artifact(ctx):
    """Render the review dashboard and acknowledge the obligation.

    'here we go again no inline dashboard visualisation. no report nothing?'
    is what an UNACKNOWLEDGED render obligation looks like from the outside,
    and a stub run that issued none would leave the obligations ledger empty
    — a record with a whole ledger missing is not a compliant run.
    """
    art = os.path.join(ctx.ws, ".em-review", "dashboard.html")
    _write(art, "<!doctype html><title>review</title><h1>3 lenses</h1>\n")
    oid = obligations.issue(ctx.ws, "render_dashboard",
                            detail="the review dashboard", step="em",
                            artifact=art, key=".em-review/dashboard.html")
    obligations.acknowledge(
        ctx.ws, oid, evidence="rendered inline",
        fingerprint=obligations.artifact_fingerprint(art))


def _close_the_gates(ctx):
    tp.trace(ctx.ws, "review_kernel_collected", stage="review", revision=1)
    tp.trace(ctx.ws, "dod", passed=True, errors=[], notices=[])
    tp.trace(ctx.ws, "loop_submit", step="em", task="eval-review")


def compliant_driver(ctx):
    """A review that does everything `tp-engineering` mandates."""
    _open_the_contract(ctx)
    _pin_and_derive_once(ctx)
    _brief_and_run(ctx, ROUTED)
    _show_the_artifact(ctx)
    _close_the_gates(ctx)


def non_compliant_driver(ctx):
    """The same review with two defects and nothing else changed.

    It re-derives the diff it already had, and it FORCES the whole catalog
    with `--all` — the flag that switches the applicability engine off —
    instead of letting the router pick for this change. Both are invisible in
    prose and both are exactly what the rubric exists to catch.

    The `--all` defect is now stated by the record (`requested_breadth`),
    not guessed from the lens list, because the lens list of a compliant
    signal-routed review looks exactly the same.
    """
    _open_the_contract(ctx)
    _pin_and_derive_once(ctx)
    derivation.record(ctx.ws, "git diff", "allow")      # the same diff, again
    _brief_and_run(ctx, _every_lens(ctx), breadth="all", engine_ran=False)
    _show_the_artifact(ctx)
    _close_the_gates(ctx)


def engine_routed_everything_driver(ctx):
    """COMPLIANT, and indistinguishable from `--all` by the lens list alone.

    This is what a real route v2 review records: the engine ran, and it
    emitted an entry for every catalog lens because the n/a ones carry the
    negative evidence coverage honesty needs. Under the routed-set inference
    this run was scored `--all` — the rubric accused the compliant path.
    """
    _open_the_contract(ctx)
    _pin_and_derive_once(ctx)
    _brief_and_run(ctx, _every_lens(ctx), breadth="routed", engine_ran=True)
    _show_the_artifact(ctx)
    _close_the_gates(ctx)


def unrecorded_full_catalog_driver(ctx):
    """The full catalog with NO breadth on the record — the fallback case.

    A route the loop made with no governed workspace to record into. The
    honest reading is "cannot tell", so the recorder must write no breadth
    at all rather than fabricate the `--all` finding.
    """
    _open_the_contract(ctx)
    _pin_and_derive_once(ctx)
    _brief_and_run(ctx, _every_lens(ctx), stamp=False)
    _show_the_artifact(ctx)
    _close_the_gates(ctx)


def _every_lens(ctx):
    return [l["id"] for l in lens_router.load_catalog(ctx.root)["lenses"]]


def _record(case, driver, **kw):
    out = _tmp(case, "tp-evalrec-out-")
    dest = _tmp(case, "tp-evalrec-run-")
    kw.setdefault("skill", "tp-engineering")
    kw.setdefault("run_id", "t-0001")
    return eval_record.record_run(root=REPO, dest=dest, driver=driver,
                                  out_dir=os.path.join(out, "rec"), **kw)


# ============================================================== the fixture

class TestTheFixtureBuildsToTheShasItsManifestPins(unittest.TestCase):
    """A score drop has to be the model's fault. It cannot be if the tree
    under review is free to move, so the tree is two frozen file sets and a
    builder that pins every input a commit SHA depends on."""

    def test_the_build_reproduces_the_manifests_base_and_head(self):
        built = eval_record.build_fixture(REPO, os.path.join(
            _tmp(self, "tp-fix-"), "checkout"))
        manifest = eval_record.load_manifest(REPO)
        self.assertEqual(built["base"], manifest["base"])
        self.assertEqual(built["head"], manifest["head"])

    def test_two_builds_into_different_destinations_agree(self):
        """Determinism is the claim; one build cannot make it."""
        root = _tmp(self, "tp-fix-")
        a = eval_record.build_fixture(REPO, os.path.join(root, "one"))
        b = eval_record.build_fixture(REPO, os.path.join(root, "two"))
        self.assertEqual(a["shas"], b["shas"])

    def test_a_hostile_ambient_identity_clock_tz_and_locale_never_land(self):
        """The environment the builder runs under is BUILT, never inherited.

        An inherited GIT_AUTHOR_DATE, committer identity or global git config
        would move the SHAs on one developer's machine and nowhere else —
        the least debuggable failure this fixture could have.
        """
        gitconfig = os.path.join(_tmp(self, "tp-hostile-"), "gitconfig")
        _write(gitconfig, "[user]\n\tname = Global User\n\temail = g@x.invalid\n"
                          "[core]\n\tautocrlf = true\n\teol = crlf\n")
        _env_patch(self, dict(HOSTILE, GIT_CONFIG_GLOBAL=gitconfig))
        root = _tmp(self, "tp-fix-")
        a = eval_record.build_fixture(REPO, os.path.join(root, "one"))
        b = eval_record.build_fixture(REPO, os.path.join(root, "two"))
        manifest = eval_record.load_manifest(REPO)
        self.assertEqual(a["shas"], b["shas"])
        self.assertEqual(a["head"], manifest["head"])

    def test_one_appended_byte_in_a_head_file_is_refused(self):
        """The pin covers tree CONTENT, not just commit metadata.

        Pinning only identity and dates would let the reviewed diff change
        while every recorded run kept comparing against a baseline taken from
        a different tree.
        """
        copy = os.path.join(_tmp(self, "tp-fix-"), "fixture-repo")
        shutil.copytree(eval_record.fixture_dir(REPO), copy)
        with io.open(os.path.join(copy, "tree-b", "pricing", "discount.py"),
                     "a", encoding="utf-8") as f:
            f.write("\n")
        with self.assertRaises(eval_record.FixtureMismatch) as caught:
            eval_record.build_fixture(REPO, os.path.join(
                _tmp(self, "tp-fix-"), "checkout"), fixture_root=copy)
        self.assertIn("tree-b", str(caught.exception))

    def test_the_fixture_is_plain_files_and_never_a_committed_gitlink(self):
        """A nested `.git` commits as a mode-160000 gitlink with no objects
        behind it: a fresh clone gets a pointer it cannot resolve, and the
        fixture silently cannot be materialized at all."""
        for dirpath, dirs, _files in os.walk(eval_record.fixture_dir(REPO)):
            self.assertNotIn(".git", dirs,
                             f"{dirpath} carries a nested git directory")
        listed = subprocess.run(
            ["git", "ls-files", "-s", "--", "evals/fixture-repo"],
            cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        gitlinks = [l for l in listed.stdout.splitlines()
                    if l.startswith("160000")]
        self.assertEqual(gitlinks, [])

    def test_the_trees_are_checked_out_with_the_endings_they_were_pinned_with(
            self):
        """A CRLF checkout hashes to different SHAs.

        The repository's own `.gitattributes` normalizes text to LF in the
        working tree on every platform, and these SHAs now DEPEND on that:
        relax it and the fixture reproduces on Linux and is refused on
        Windows, with a mismatch message that points at the trees rather
        than at the checkout. Pinned here so the dependency is visible.
        """
        sample = []
        for dirpath, _dirs, files in os.walk(eval_record.fixture_dir(REPO)):
            if "__pycache__" in dirpath:
                continue
            for name in files:
                path = os.path.join(dirpath, name)
                with io.open(path, "rb") as f:
                    self.assertNotIn(b"\r", f.read(),
                                     f"{path} carries a CR byte")
                sample.append(os.path.relpath(path, REPO))
        attrs = subprocess.run(
            ["git", "check-attr", "eol", "--"] + sorted(sample)[:4],
            cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertTrue(attrs.stdout.strip())
        for line in attrs.stdout.splitlines():
            self.assertTrue(line.endswith("eol: lf"), line)

    def test_a_stray_byte_cache_beside_a_tree_does_not_move_the_shas(self):
        """`__pycache__` is not part of the fixture and must never be
        committed into it. A builder that copied one in would refuse a tree
        that is byte-identical to the pinned one, and the message would send
        the reader looking at the wrong thing entirely."""
        copy = os.path.join(_tmp(self, "tp-fix-"), "fixture-repo")
        shutil.copytree(eval_record.fixture_dir(REPO), copy)
        _write(os.path.join(copy, "tree-b", "pricing", "__pycache__",
                            "discount.cpython-311.pyc"), "not source\n")
        built = eval_record.build_fixture(
            REPO, os.path.join(_tmp(self, "tp-fix-"), "checkout"),
            fixture_root=copy)
        self.assertEqual(built["head"], eval_record.load_manifest(REPO)["head"])

    def test_the_diff_is_shaped_like_the_thing_the_scenario_grades(self):
        """A one-line diff gives the qa lens and the architecture floor
        nothing honest to say, and a review of nothing scores like a review
        that skipped everything."""
        built = eval_record.build_fixture(REPO, os.path.join(
            _tmp(self, "tp-fix-"), "checkout"))
        files = eval_record.changed_files(built["path"], built["base"],
                                          built["head"])
        self.assertEqual(files, eval_record.load_manifest(REPO)["diff"]["files"])
        self.assertGreaterEqual(len(files), 4)
        self.assertTrue([f for f in files
                         if os.path.basename(f).startswith("test_")],
                        "the graded diff carries no test file")


class TestTheFixtureIsInvisibleToTheRepositorysOwnTooling(unittest.TestCase):
    """A fixture that breaks the repo's own commands is a fixture that gets
    deleted. Both of these failures land on a developer and never on CI."""

    def test_bare_pytest_at_the_repo_root_still_collects_cleanly(self):
        """`tree-a/tests/test_checkout.py` and `tree-b/tests/test_checkout.py`
        share a basename under one rootdir, which aborts COLLECTION for the
        whole repository — not one test, all of them. CI scopes pytest to
        `taskplane/tests`, so only a developer at the root ever sees it."""
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0,
                         f"root collection failed:\n{r.stdout[-3000:]}")
        self.assertNotIn("import file mismatch", r.stdout)
        self.assertNotIn("fixture-repo", r.stdout)

    def test_the_corpus_run_names_the_fixture_skipped_and_exits_zero(self):
        """`evals/fixture-repo/` carries neither marker, so it is not an eval
        record. Being SKIPPED BY NAME is the contract: silence is how the old
        walker got away with treating a container as a scored profile."""
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts", "ci_evals.py"),
             "--corpus"], capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped fixture-repo", r.stdout)

    def test_the_corpus_engine_loads_in_legacy_direct_module_mode(self):
        """The scorer adds ``taskplane/`` itself, not its repository parent.

        Keep that shipped direct-module entry point independent of pytest's
        ambient package path: package-only imports inside a new loop module
        otherwise make the real corpus command fail before it can score.
        """
        r = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import loop; "
                "assert loop.STEP_ROLE",
                os.path.join(REPO, "taskplane"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ============================================================= the recorder

class TestTheModelDrivingStepIsASeam(unittest.TestCase):
    """The recorder needs a model; the tests must not. Everything except the
    model is therefore reachable through a stub driver."""

    def test_the_driver_is_handed_the_checkout_it_is_meant_to_review(self):
        seen = {}

        def spy(ctx):
            seen.update(ws=ctx.ws, head=ctx.head, base=ctx.base,
                        calls=seen.get("calls", 0) + 1)
            compliant_driver(ctx)

        out = _record(self, spy)
        manifest = eval_record.load_manifest(REPO)
        self.assertEqual(seen["calls"], 1)
        self.assertEqual(seen["head"], manifest["head"])
        self.assertEqual(seen["base"], manifest["base"])
        self.assertTrue(os.path.isdir(os.path.join(seen["ws"], "pricing")))
        self.assertEqual(out["run"]["target_head"], manifest["head"])

    def test_the_pre_flight_probe_runs_before_the_driver_does(self):
        """A ledger nobody could write scores zero repeats and looks
        compliant. The probe is the receipt that it was writable, and a
        receipt taken afterwards proves nothing about the run."""
        order = []
        real = derivation.probe

        def watched(ws):
            order.append("probe")
            return real(ws)

        derivation.probe = watched
        self.addCleanup(setattr, derivation, "probe", real)
        out = _record(self, lambda ctx: (order.append("driver"),
                                         compliant_driver(ctx)))
        self.assertEqual(order, ["probe", "driver"])
        self.assertTrue(out["probe"])

    def test_setup_finishes_before_measurement_probe_and_model_driver(self):
        order = []
        real = derivation.probe

        def setup(**ctx):
            order.append("setup")
            _write(os.path.join(ctx["ws"], "setup-only.md"), "harness\n")

        def watched(ws):
            order.append("probe")
            return real(ws)

        derivation.probe = watched
        self.addCleanup(setattr, derivation, "probe", real)
        out = _record(self, lambda ctx: order.append("driver"), setup=setup)
        rows = _read_jsonl(os.path.join(out["path"], "trace.jsonl"))
        self.assertEqual(order, ["setup", "probe", "driver"])
        self.assertFalse([r for r in rows if r.get("event") ==
                          "workspace_write"], rows)

    def test_a_ledger_that_cannot_be_probed_aborts_the_whole_run(self):
        """`instrument: broken` is the honest reading of a probe-less ledger,
        and a recorder that produced one anyway would ship records whose every
        derivation row is unusable."""
        real = derivation.probe
        derivation.probe = lambda ws: None
        self.addCleanup(setattr, derivation, "probe", real)
        driven = []
        with self.assertRaises(eval_record.InstrumentBroken):
            _record(self, lambda ctx: driven.append(1))
        self.assertEqual(driven, [], "the driver ran over a broken ledger")


class TestTheRunCarriesNoCredentialsAndNoAmbientGitConfig(unittest.TestCase):
    """A recorded run reviews a LOCAL fixture. A token in its environment
    buys nothing and risks a model reaching the network mid-eval, which is
    the one thing this whole fixture exists to prevent."""

    def test_a_github_token_never_reaches_the_running_driver(self):
        _env_patch(self, {"GH_TOKEN": "ghp_leak", "GITHUB_TOKEN": "ghs_leak"})
        seen = {}

        def spy(ctx):
            seen["env"] = dict(os.environ)
            seen["ctx_env"] = dict(ctx.env)
            compliant_driver(ctx)

        _record(self, spy)
        for key in eval_record.CREDENTIAL_VARS:
            self.assertNotIn(key, seen["env"],
                             f"{key} was visible to the driver")
            self.assertNotIn(key, seen["ctx_env"])
        self.assertEqual(os.environ.get("GH_TOKEN"), "ghp_leak",
                         "the recorder did not restore the caller's env")

    def test_a_credential_in_a_built_environment_is_refused_outright(self):
        with self.assertRaises(eval_record.CredentialLeak):
            eval_record.assert_no_credentials({"GITHUB_TOKEN": "x"})

    def test_git_in_the_checkout_reads_no_global_configuration(self):
        seen = {}

        def spy(ctx):
            seen["global"] = ctx.env.get("GIT_CONFIG_GLOBAL")
            seen["remote"] = target_mod.git(ctx.ws, "remote", "get-url",
                                            "origin")[1]
            compliant_driver(ctx)

        out = _record(self, spy)
        self.assertEqual(seen["global"], os.devnull)
        self.assertTrue(os.path.isdir(seen["remote"]),
                        "origin is not a local bare repository on disk")
        self.assertTrue(seen["remote"].startswith(
            os.path.realpath(out["dest"])) or
            seen["remote"].startswith(out["dest"]))


class TestOnlyACleanOutOfBandRunMaySetABaseline(unittest.TestCase):
    """The eligibility rule has to live in the RECORD. In prose it is a
    convention, and a convention is what gets waived at 2am by whoever needs
    a baseline moved."""

    def test_an_out_of_band_run_that_was_observed_is_eligible(self):
        run = _record(self, compliant_driver, mode="out-of-band")["run"]
        self.assertTrue(run["hook_active"])
        self.assertEqual(run["mode"], "out-of-band")
        self.assertTrue(run["baseline_eligible"])

    def test_an_in_session_subagent_run_is_informational_forever(self):
        run = _record(self, compliant_driver, mode="subagent")["run"]
        self.assertEqual(run["mode"], "subagent")
        self.assertFalse(run["baseline_eligible"])
        self.assertIn("subagent", run["baseline_reason"])

    def test_an_unobserved_run_may_never_set_a_baseline(self):
        """`hook_active` false means the dispatch hook saw nothing, so the
        fan-out is UNKNOWN rather than zero — and a baseline taken from an
        unobserved run pins a number nobody measured."""
        def blind(ctx):
            _open_the_contract(ctx)
            _pin_and_derive_once(ctx)
            tp.trace(ctx.ws, "lens_route", step="em", lenses=list(ROUTED))
            _close_the_gates(ctx)

        run = _record(self, blind, mode="out-of-band")["run"]
        self.assertFalse(run["hook_active"])
        self.assertFalse(run["baseline_eligible"])
        self.assertIn("hook", run["baseline_reason"])

    def test_an_unknown_mode_is_refused_rather_than_recorded(self):
        with self.assertRaises(eval_record.RecorderError):
            _record(self, compliant_driver, mode="whatever")


class TestTheFrozenRecordIsWhatTheScorerReads(unittest.TestCase):
    """Two lanes meet at these names. A record that spells one of them
    differently is a record the scorer reports as `absent` — which is not a
    pass, but is also not the truth."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="tp-evalrec-cls-")
        cls.out = eval_record.record_run(
            root=REPO, dest=os.path.join(cls._tmp, "run"),
            driver=compliant_driver, skill="tp-engineering",
            run_id="t-shape", out_dir=os.path.join(cls._tmp, "rec"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, True)

    def test_every_file_the_rubric_names_is_written_under_that_name(self):
        for name in eval_rubric.RECORD_FILES.values():
            self.assertTrue(
                os.path.isfile(os.path.join(self.out["path"], name)),
                f"the scorer reads {name} and the recorder did not write it")
        self.assertTrue(os.path.isfile(
            os.path.join(self.out["path"], eval_rubric.RUN_FILE)))

    def test_the_record_loads_through_the_scorers_own_reader(self):
        rec = eval_rubric.read_record(self.out["path"])
        self.assertEqual(rec["unreadable"], ())
        for name in eval_rubric.RECORDS:
            self.assertEqual(eval_rubric.status(rec, name), "present", name)

    def test_the_per_brief_rows_live_under_the_additive_dispatch_key(self):
        """The engine already writes counts to dispatch.json. The rubric's
        rows are an ADDITIVE key so one file serves both instruments and
        neither has to learn about the other."""
        with io.open(os.path.join(self.out["path"], "dispatch.json"),
                     encoding="utf-8") as f:
            dispatch = json.load(f)
        for count_key in ("expected", "observed", "hook_active"):
            self.assertIn(count_key, dispatch)
        rows = dispatch[eval_rubric.DISPATCH_ROWS]
        native = [r for r in rows if r.get("kind") == "review-kernel-slot"]
        legacy = [r for r in rows if r.get("kind") != "review-kernel-slot"]
        self.assertEqual(sorted(r["lens"] for r in legacy), sorted(ROUTED))
        self.assertEqual(sorted(r["slot_id"] for r in native),
                         sorted("deep." + lens for lens in ROUTED))
        self.assertTrue(all(r["context_path"] ==
                            ".em-review/kernel-v2/envelope.json"
                            for r in native))
        self.assertTrue(all(r["context_fingerprint"] == "CTX" for r in native))

    def test_every_row_the_recorder_synthesized_carries_the_order_key(self):
        """Ordering is compared on one field. A synthesized row without it
        makes `_sort_key` return the unorderable kind, and every cross-record
        ordering check can then only ever answer `no_evidence` — a rubric
        that looks shy instead of a recorder that is broken."""
        for name in ("context.jsonl", "trace.jsonl", "derivations.jsonl"):
            for row in _read_jsonl(os.path.join(self.out["path"], name)):
                ts = row.get(eval_scenario.ORDER_KEY)
                self.assertIsInstance(ts, float,
                                      f"{name}: {row} carries no comparable "
                                      f"{eval_scenario.ORDER_KEY}")
        with io.open(os.path.join(self.out["path"], "dispatch.json"),
                     encoding="utf-8") as f:
            for row in json.load(f)[eval_rubric.DISPATCH_ROWS]:
                self.assertIsInstance(row.get(eval_scenario.ORDER_KEY), float)

    def test_the_context_record_names_every_artifact_the_run_shared(self):
        """`context.jsonl` is the only record no engine writes. It is the
        difference between "a diff was derived" and "the diff was derived
        ONCE and every lens read that one copy"."""
        kinds = {}
        for row in _read_jsonl(os.path.join(self.out["path"],
                                            "context.jsonl")):
            kinds.setdefault(row["kind"], []).append(row)
        self.assertEqual(sorted(kinds), ["context_file", "findings",
                                         "lens_findings", "review_envelope",
                                         "slot_result", "target"])
        self.assertEqual(kinds["target"][0]["head"],
                         eval_record.load_manifest(REPO)["head"])
        self.assertEqual(sorted(r["lens"] for r in kinds["lens_findings"]),
                         sorted(ROUTED))
        self.assertEqual(len(kinds["review_envelope"]), 1)
        self.assertEqual(kinds["review_envelope"][0]["fingerprint"], "CTX")
        self.assertEqual(sorted(r["slot_id"] for r in kinds["slot_result"]),
                         sorted("deep." + lens for lens in ROUTED))
        for row in kinds["context_file"]:
            self.assertEqual(len(row["sha256"]), 64)

    def test_the_run_record_carries_identity_eligibility_and_cost(self):
        run = self.out["run"]
        for key in ("schema", "skill", "run_id", "mode", "hook_active",
                    "baseline_eligible", "target_head", "target_base",
                    "inputs_fingerprint", "effective_tokens", "host",
                    "recorded_at"):
            self.assertIn(key, run)
        self.assertEqual(run["skill"], "tp-engineering")
        self.assertEqual(
            run["inputs_fingerprint"],
            eval_scenario.load(SCENARIO)["inputs_fingerprint"],
            "the run does not name the flow it was recorded against, so "
            "nothing can tell a stale record from a fresh one")

    def test_the_trace_carries_the_fields_the_engine_does_not_emit(self):
        """`eval_scenario.SYNTHETIC_EVENTS` / `SYNTHETIC_FIELDS` are the
        recorder's specification. A field nothing records selects nothing,
        and a rubric row that selects nothing scores `no_evidence` forever
        while reading like a shy session."""
        rows = _read_jsonl(os.path.join(self.out["path"], "trace.jsonl"))
        routes = [r for r in rows if r["event"] == "lens_route"]
        self.assertTrue(routes)
        self.assertEqual([r["breadth"] for r in routes], ["routed"])
        writes = [r for r in rows if r["event"] == "workspace_write"]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0]["synthesized"])
        activated = [r for r in rows if r["event"] == "contract_activated"]
        self.assertEqual(len(activated), 1,
                         "the engine emits this one; synthesizing a second "
                         "would double-count it")


class TestScenarioAwareRecorderBoundaries(unittest.TestCase):
    def test_native_codex_dispatch_closes_the_matching_expected_brief(self):
        expected = [{"task_name": "tp_step_product", "agent": "tp-product",
                     "model": None, "reasoning_effort": "high",
                     "matched": False}]
        trace = [{"event": "subagent_start",
                  "source": "codex_session_store", "host_observed": True,
                  "task_name": "tp_step_product", "model": "gpt-test",
                  "reasoning_effort": "high"}]
        got = eval_record.merge_native_dispatch_report(
            {"observed": 0, "unobserved": 1, "mismatches": [],
             "hook_active": False}, expected, trace)
        self.assertEqual(got["observed"], 1)
        self.assertEqual(got["expected"], 1)
        self.assertEqual(got["unobserved"], 0)
        self.assertEqual(got["mismatches"], [])
        self.assertEqual(got["observation_source"], "codex_session_store")

    def test_native_derivations_fill_silent_hooks_without_double_counting(self):
        hook = [
            {"event": "derived", "key": "impact", "input_key": "H|F",
             "ts": 1},
        ]
        native = [
            {"event": "command", "verb": "tp review start", "ts": 2,
             "source": "codex_session_store"},
            {"event": "derived", "key": "impact", "input_key": "H|F",
             "ts": 2, "source": "codex_session_store"},
            {"event": "derived", "key": "diff", "input_key": "B..H",
             "ts": 2, "source": "codex_session_store"},
        ]
        got = eval_record.merge_native_derivations(hook, native)
        self.assertEqual(sum(row.get("key") == "impact" for row in got), 1)
        self.assertEqual(sum(row.get("key") == "diff" for row in got), 1)
        self.assertEqual(sum(row.get("verb") == "tp review start"
                             for row in got), 1)

    def test_two_native_review_starts_remain_a_repeat(self):
        one = {"event": "derived", "key": "diff", "input_key": "B..H",
               "source": "codex_session_store"}
        got = eval_record.merge_native_derivations([], [one, dict(one)])
        self.assertEqual(len(got), 2)
        self.assertEqual(derivation.repeats(rows=got), 1)

    def test_evaluator_setup_is_not_misreported_as_a_model_workspace_write(self):
        with tempfile.TemporaryDirectory() as ws:
            _write(os.path.join(ws, ".codex", "hooks.json"), "{}")
            _write(os.path.join(ws, ".taskplane-eval", "plugin", "marker"),
                   "staged")
            self.assertIsNone(eval_record.first_write(ws, None, 0))

    def test_loop_readiness_and_human_gate_are_synthesized_from_frozen_state(self):
        rows = eval_record.synthesize_trace(
            [{"ts": 2, "event": "loop_step", "dor_ready": True}],
            started_at=1, loop_state={"step": "design_approval"})
        events = {row["event"]: row for row in rows}
        self.assertEqual(events["evaluation_started"]["source"],
                         "recorder boundary")
        self.assertTrue(events["dor"]["ready"])
        self.assertEqual(events["human_gate_wait"]["step"],
                         "design_approval")

    def test_review_collection_is_the_engineering_dod_receipt(self):
        rows = eval_record.synthesize_trace(
            [{"ts": 2, "event": "review_kernel_started",
              "graph_quality_status": "complete"},
             {"ts": 4, "event": "review_kernel_collected"}],
            started_at=1)
        events = {row["event"]: row for row in rows}
        self.assertTrue(events["dor"]["ready"])
        self.assertTrue(events["dod"]["passed"])
        self.assertEqual(events["dod"]["source"],
                         "review_kernel_collected")

    def test_trace_selects_one_review_run_instead_of_mixing_history(self):
        with tempfile.TemporaryDirectory() as ws:
            for marker in ("a", "b"):
                run_id = marker * 32
                envelope = f".em-review/kernel-v2/{marker}-envelope.json"
                brief = f".em-review/kernel-v2/{marker}-brief.json"
                _write(os.path.join(ws, envelope), "{}")
                _write(os.path.join(ws, brief), json.dumps({"role": {
                    "agent": "tp-lens",
                    "task_name": "tp_lens_" + marker,
                    "reasoning_effort": "medium"}}))
                state = {
                    "run_id": run_id,
                    "target": {"head": marker.upper()},
                    "envelope": {"relative_path": envelope,
                                 "fingerprint": marker + "-context"},
                    "slots": [{"slot_id": "deep." + marker,
                               "lens_ids": [marker],
                               "brief": {"relative_path": brief},
                               "result_path": "missing-result.json"}],
                }
                _write(os.path.join(ws, ".em-review", "kernel-v2", "runs",
                                    run_id, "state.json"),
                       json.dumps(state))
            trace = [{"ts": 1, "event": "review_kernel_started",
                      "run_id": "b" * 32}]
            context = eval_record.synthesize_context(
                ws, trace_rows=trace, fallback_ts=0)
            briefs = eval_record.synthesize_briefs(
                ws, trace_rows=trace, context_path=None)
            self.assertEqual([r["run_id"] for r in context
                              if r["kind"] == "review_envelope"], ["b" * 32])
            self.assertEqual([r["slot_id"] for r in briefs], ["deep.b"])
            self.assertEqual(briefs[0]["task_name"], "tp_lens_b")
            self.assertEqual(briefs[0]["reasoning_effort"], "medium")
            got = eval_record.merge_native_dispatch_report(
                {"observed": 0, "unobserved": 0, "mismatches": [],
                 "hook_active": False}, briefs,
                [{"event": "subagent_start",
                  "source": "codex_session_store", "host_observed": True,
                  "task_name": "tp_lens_b", "model": "gpt-test",
                  "reasoning_effort": "medium"}])
            self.assertEqual(got["expected"], 1)
            self.assertEqual(got["observed"], 1)
            self.assertEqual(got["unobserved"], 0)
            self.assertEqual(got["mismatches"], [])


# ============================================ the breadth is READ, not GUESSED
#
# The rubric row this serves (R5) asks: did the review let the applicability
# engine route, or did it force the whole catalog with `--all`, which switches
# that engine off? `eval_record.breadth_of` used to answer by comparing the
# routed set against the catalog — routed-set superset of catalog => "all".
#
# `_route_v2` emits an output entry for EVERY catalog lens, n/a ones included
# with their negative evidence, because the renderer needs the coverage to be
# honest. So a signal-routed review's lens list IS the whole catalog and the
# inference read `--all` on EVERY routed review, not merely at some edge. The
# instrument accused the compliant path and could not have done otherwise.

CATALOG_IDS = [l["id"] for l in lens_router.load_catalog(REPO)["lenses"]]


def _route_row(lenses, **extra):
    row = {"event": "lens_route", "step": "em", "lenses": list(lenses)}
    row.update(extra)
    return row


class TestTheRoutingBreadthComesFromTheRecordNotTheRoutedSet(
        unittest.TestCase):
    """WHY: an instrument that reports its own blind spot as a finding is
    worse than one that reports nothing — a wrong verdict is acted on."""

    def test_a_route_that_states_its_breadth_beats_the_routed_set(self):
        """The whole catalog routed BY THE ENGINE is not `--all`, and the
        record now says so in a field instead of leaving it to arithmetic
        that cannot tell the two apart."""
        row = _route_row(CATALOG_IDS, requested_breadth="routed",
                         engine_ran=True)
        self.assertEqual(eval_record.breadth_of(row, set(CATALOG_IDS)),
                         "routed")

    def test_a_stated_all_stands_even_when_the_routed_set_is_a_subset(self):
        """`--all` on a catalog the router then narrowed (only/skip) is still
        `--all`: the operator threw the switch, whatever came back."""
        row = _route_row(["security", "qa"], requested_breadth="all",
                         engine_ran=False)
        self.assertEqual(eval_record.breadth_of(row, set(CATALOG_IDS)), "all")

    def test_the_engine_on_and_the_engine_off_leave_different_records(self):
        """Driven through the REAL router, both ways, on the same files.
        This is the defect itself: without the recorded field these two
        routings produce the same 26-entry lens list."""
        cat = lens_router.load_catalog(REPO)
        files = ["pricing/checkout.py", "pricing/discount.py"]
        on = lens_router.route(files, catalog=cat, stage="review")
        off = lens_router.route(files, catalog=cat, breadth="all")
        ids_on = [[x["id"], x["mode"]] for x in on["lenses"]]
        ids_off = [[x["id"], x["mode"]] for x in off["lenses"]]
        self.assertEqual(sorted(x[0] for x in ids_on),
                         sorted(x[0] for x in ids_off),
                         "premise: the two lens SETS are identical, which is "
                         "why the routed-set inference cannot work")
        self.assertEqual(
            eval_record.breadth_of(
                _route_row(ids_on,
                           requested_breadth=on["context"]["breadth"],
                           engine_ran="signals" in on["context"]),
                set(CATALOG_IDS)),
            "routed")
        self.assertEqual(
            eval_record.breadth_of(
                _route_row(ids_off,
                           requested_breadth=off["context"]["breadth"],
                           engine_ran="signals" in off["context"]),
                set(CATALOG_IDS)),
            "all")

    def test_a_full_catalog_route_with_nothing_recorded_is_cannot_tell(self):
        """`None`, never `"all"`. The routed set is compatible with both a
        routed review and a forced one, so naming one of them is the
        instrument reporting its own gap as a finding."""
        self.assertIsNone(eval_record.breadth_of(_route_row(CATALOG_IDS),
                                                 set(CATALOG_IDS)))

    def test_a_subset_route_with_nothing_recorded_is_still_routed(self):
        """The one thing the routed set DOES prove: a strict subset cannot
        have come from a full-catalog sweep. The fallback is repaired, not
        deleted — the loop still makes unrecorded routes."""
        self.assertEqual(
            eval_record.breadth_of(_route_row(["security", "qa"]),
                                   set(CATALOG_IDS)),
            "routed")

    def test_the_loops_own_id_and_mode_pairs_are_read_and_do_not_crash(self):
        """`loop.py` traces `lenses=[[id, mode], ...]`, not bare ids. The
        inference indexed that shape as a dict and raised AttributeError, so
        the recorder could not read a real governed run's trace at all —
        only the string-list shape the stub drivers happened to emit."""
        row = _route_row([[i, "subagent"] for i in ["security", "qa"]])
        self.assertEqual(eval_record.breadth_of(row, set(CATALOG_IDS)),
                         "routed")

    def test_an_unreadable_catalog_leaves_the_breadth_undecided(self):
        self.assertIsNone(eval_record.breadth_of(_route_row(CATALOG_IDS),
                                                 set()))

    def test_a_synthesized_breadth_names_which_of_the_two_answered_it(self):
        """`breadth_source` is the audit trail. A field copied off the route's
        own record and a field guessed from the catalog carry very different
        weight, and a reader who cannot tell them apart trusts both."""
        rows = eval_record.synthesize_trace(
            [_route_row(CATALOG_IDS, requested_breadth="routed",
                        engine_ran=True),
             _route_row(["security", "qa"])],
            known_lenses=set(CATALOG_IDS))
        routes = [r for r in rows if r["event"] == "lens_route"]
        self.assertEqual([r["breadth"] for r in routes], ["routed", "routed"])
        self.assertIn("recorded", routes[0]["breadth_source"])
        self.assertIn("derived", routes[1]["breadth_source"])

    def test_a_row_that_cannot_be_told_carries_no_breadth_at_all(self):
        """Silence, not a value. A `breadth` key holding a placeholder would
        be selected by the rubric exactly like a real one."""
        rows = eval_record.synthesize_trace([_route_row(CATALOG_IDS)],
                                            known_lenses=set(CATALOG_IDS))
        self.assertNotIn("breadth", rows[0])
        self.assertNotIn("breadth_source", rows[0])


# ====================================================== the end-to-end proof

class TestAStubDriverRunIsScoredByTheRealScorer(unittest.TestCase):
    """The two halves meet here, over the real scenario file. Anything less
    proves the recorder against the recorder's own idea of a record."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="tp-evalrec-e2e-")
        cls.scenario = eval_scenario.load(SCENARIO)
        cls.cards, cls.paths = {}, {}
        for name, driver in (("compliant", compliant_driver),
                             ("non_compliant", non_compliant_driver),
                             ("engine_routed_all",
                              engine_routed_everything_driver),
                             ("unrecorded_all",
                              unrecorded_full_catalog_driver)):
            out = eval_record.record_run(
                root=REPO, dest=os.path.join(cls._tmp, name + "-run"),
                driver=driver, skill="tp-engineering", run_id="t-" + name,
                out_dir=os.path.join(cls._tmp, name + "-rec"))
            rec = eval_rubric.read_record(out["path"])
            cls.cards[name] = eval_rubric.evaluate(cls.scenario, rec)
            cls.paths[name] = out["path"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, True)

    def test_the_scenario_it_is_scored_against_is_the_committed_one(self):
        self.assertEqual(self.scenario["skill"], "tp-engineering")
        self.assertEqual(len(self.scenario["steps"]), 8)

    def test_the_instrument_reports_itself_ok_on_a_recorded_run(self):
        self.assertEqual(self.cards["compliant"]["instrument"], "ok",
                         self.cards["compliant"]["instrument_reason"])

    def test_a_compliant_stub_run_passes_every_step(self):
        card = self.cards["compliant"]
        self.assertEqual(
            card["verdicts"],
            {"R1": "pass", "R2": "pass", "R3": "pass", "R4": "pass",
             "R5": "pass", "R6": "pass", "R7": "pass", "R8": "pass"},
            json.dumps([(s["id"], s["verdict"], s["reason"])
                        for s in card["steps"]], indent=2))
        self.assertEqual(card["universal"],
                         {"contract": "pass", "dor": "pass", "dod": "pass",
                          "no_rederive": "pass"})

    def test_a_non_compliant_stub_run_produces_a_different_vector(self):
        """A recorder that can only produce passes is not a recorder — it is
        a fixture generator with a scorer attached."""
        self.assertNotEqual(self.cards["non_compliant"]["verdicts"],
                            self.cards["compliant"]["verdicts"])

    def test_the_non_compliant_run_fails_exactly_the_two_defects_it_has(self):
        card = self.cards["non_compliant"]
        failed = sorted(sid for sid, v in card["verdicts"].items()
                        if v != "pass")
        self.assertEqual(
            failed, ["R5", "R7"],
            json.dumps([(s["id"], s["verdict"], s["reason"])
                        for s in card["steps"]], indent=2))
        self.assertEqual(card["verdicts"]["R5"], "fail")
        self.assertEqual(card["verdicts"]["R7"], "fail")
        self.assertEqual(card["universal"]["no_rederive"], "fail")

    def test_the_two_runs_differ_in_score_as_well_as_in_verdicts(self):
        self.assertLess(self.cards["non_compliant"]["score"],
                        self.cards["compliant"]["score"])

    def test_the_two_runs_are_told_apart_by_the_breadth_they_recorded(self):
        """The non-compliant vector must fail for the reason it NAMES. Both
        runs brief lenses, pin a target and close their gates identically;
        the only difference the rubric may act on is the recorded `--all`."""
        def breadths(name):
            rows = _read_jsonl(os.path.join(self.paths[name], "trace.jsonl"))
            return [r.get("breadth") for r in rows
                    if r["event"] == "lens_route"]
        self.assertEqual(breadths("compliant"), ["routed"])
        self.assertEqual(breadths("non_compliant"), ["all"])
        self.assertIn("routing_mode", self.cards["non_compliant"]["steps"][4]
                      ["constraints"][0]["evidence"]["rows"][0])

    def test_a_review_the_engine_routed_over_the_whole_catalog_passes(self):
        """THE DEFECT, end to end. This run is compliant — the engine chose,
        and emitted an entry per catalog lens for coverage honesty. The
        routed-set inference scored it `--all` and failed R5, i.e. the rubric
        accused every correctly-routed review it will ever see."""
        card = self.cards["engine_routed_all"]
        self.assertEqual(
            card["verdicts"],
            {"R1": "pass", "R2": "pass", "R3": "pass", "R4": "pass",
             "R5": "pass", "R6": "pass", "R7": "pass", "R8": "pass"},
            json.dumps([(s["id"], s["verdict"], s["reason"])
                        for s in card["steps"]], indent=2))

    def test_a_full_catalog_route_with_no_recorded_breadth_never_fails(self):
        """`no_evidence`, never `fail`. The recorder writes NO breadth for
        this run, so R5 rests on the routed set alone and may not be turned
        into a finding.

        Residual, and out of this lane: R5's second constraint is `absent`,
        which is vacuously satisfied by a missing field, so the verdict this
        lands on today is `pass` rather than `no_evidence`. Making it
        `no_evidence` needs R5 to gain a `field_equals` constraint on
        `breadth` in `evals/scenarios/tp-engineering.json` — `_field_equals`
        already returns `no_evidence` for an unrecorded field, calling it an
        instrument gap and not a mismatch. What is enforced here is the half
        that is this lane's: the gap is never scored as a violation."""
        card = self.cards["unrecorded_all"]
        rows = _read_jsonl(os.path.join(self.paths["unrecorded_all"],
                                        "trace.jsonl"))
        routes = [r for r in rows if r["event"] == "lens_route"]
        self.assertTrue(routes)
        self.assertEqual([r.get("breadth") for r in routes], [None])
        self.assertNotEqual(card["verdicts"]["R5"], "fail",
                            json.dumps([(s["id"], s["verdict"], s["reason"])
                                        for s in card["steps"]], indent=2))

# ============================== the loop stamps the breadth it asked the router
#
# The fact lives on the `lens_route` row rather than being joined from
# `lens.py`'s `lens_breadth` row, because:
#
#   * `eval_scenario.SYNTHETIC_FIELDS` names the fact `trace.lens_route.
#     breadth` and R5 selects it PER ROW — a joined value has to end up on
#     this row anyway.
#   * there is no join key. `lens_breadth` carries `stage`; `lens_route`
#     carries `step`, and they are deliberately different (em routes with
#     stage=None, evaluate with the build stage). `lens_count` collides
#     across steps of one run.
#   * for a PARALLEL evaluate the two rows are not even in the same trace:
#     the loop routes against the worktree, so `lens_breadth` lands in the
#     worktree's `.taskplane/` and `lens_route` lands in `ws`. A join that
#     is impossible in a known live case is not a join.
#
# The value written is the caller's `breadth` argument verbatim — the same
# thing `_record_breadth` calls `requested_breadth` — so the two records
# agree even on the fail-open path, where the engine broke and widened a
# ROUTED request to the full catalog. Reading the widened breadth back would
# blame the operator for the engine's failure, which is the same fabrication
# this change removes, pointed the other way.

FROZEN_CLOCK = 1600000000.0


def _loop_ws(tmp, name):
    """A governed git workspace with one scoped task, committed under a
    FIXED identity and date so two of them have byte-identical heads — the
    differential compares payloads that embed the head sha."""
    ws = os.path.join(tmp, name)
    shutil.rmtree(ws, True)
    os.makedirs(os.path.join(ws, "src", "todo"))
    _write(os.path.join(ws, "src", "todo", "a.py"), "x = 1\n")
    _write(os.path.join(ws, "plan", "tasks.json"), json.dumps({"tasks": [
        {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
         "criteria": ["complete() marks done"]}]}))
    # The pm DoD demands an authored requirement; without one the loop parks
    # at pm and the transcript below exercises exactly one routing branch.
    _write(os.path.join(ws, "specs", "spec.md"),
           "# complete()\n\n## Acceptance criteria\n\n- complete() marks "
           "the item done\n")
    env = dict(os.environ, GIT_AUTHOR_DATE="2020-01-01T00:00:00+0000",
               GIT_COMMITTER_DATE="2020-01-01T00:00:00+0000",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="e@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="e@e",
               GIT_CONFIG_GLOBAL=os.path.join(tmp, "nonexistent-gitconfig"),
               GIT_CONFIG_SYSTEM=os.path.join(tmp, "nonexistent-gitconfig"))
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=ws, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return ws


def _drive(mod, ws):
    """One scripted pass over EVERY step that routes lenses, returning what
    the loop handed back at each. `pm` is reached by a goal with no spec;
    plan / execute / evaluate / fix / em by the fail-and-escalate path, which
    needs no verdict fixture. The clock and the contract's uuid are frozen:
    `submit` embeds `int(time.time())` and `tp.activate` a `uuid4` task id,
    so an unfrozen control would differ from itself and prove nothing."""
    out = []

    def step(label, fn):
        try:
            out.append((label, fn()))
        except Exception as exc:               # exception paths are compared
            out.append((label, f"{type(exc).__name__}: {exc}"))

    with mock.patch("time.time", lambda: FROZEN_CLOCK), \
            mock.patch("uuid.uuid4",
                       lambda: __import__("uuid").UUID(int=0x5eed)):
        step("init", lambda: mod.init(ws, "add complete()",
                                      checkpoints=["em"], max_fix_cycles=2))
        step("pm", lambda: mod.next_action(ws))
        step("pm-gate", lambda: mod.gate(ws, "pass"))
        step("plan", lambda: mod.next_action(ws))
        step("plan-gate", lambda: mod.gate(ws, "pass"))
        for cycle in range(3):
            step(f"execute-{cycle}", lambda: mod.next_action(ws))
            step(f"submit-{cycle}", lambda: mod.submit(ws, "pass"))
            step(f"gate-{cycle}", lambda: mod.gate(ws, "pass"))
            step(f"evaluate-{cycle}", lambda: mod.next_action(ws))
            step(f"esubmit-{cycle}", lambda: mod.submit(ws, "fail"))
            step(f"egate-{cycle}", lambda: mod.gate(ws, "fail"))
        step("resolve", lambda: mod.resolve(ws, "skip"))
        step("em", lambda: mod.next_action(ws))
    return out


def _canon(transcript, ws):
    blob = json.dumps(transcript, indent=1, sort_keys=True, default=str)
    for path in (os.path.realpath(ws), ws):
        blob = blob.replace(path, "<WS>")
    return blob


def _routes(ws):
    rows = _read_jsonl(os.path.join(ws, ".taskplane", "trace.jsonl"))
    return [r for r in rows if r.get("event") == "lens_route"]


def _private_store(cls, tmp):
    """Point the external KB store inside this test's own tmp dir.

    `tp.store_home()` defaults to ~/.taskplane and lives OUTSIDE the
    workspace, so a run that only wipes the workspace inherits the previous
    run's requirements and artifacts — and a second control that starts from
    the first control's leftovers is not a control.
    """
    home = os.path.join(tmp, "home")
    prior = os.environ.get("TASKPLANE_HOME")
    os.environ["TASKPLANE_HOME"] = home
    cls.addClassCleanup(_restore, "TASKPLANE_HOME", prior)
    return home


class TestTheLoopRecordsTheBreadthOnTheRouteItTraced(unittest.TestCase):
    """WHY the loop and not the recorder: the breadth is the loop's own
    argument. Anything downstream can only guess at it, and the guess is
    what was wrong."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="tp-evalrec-loopstamp-")
        _private_store(cls, cls._tmp)
        cls.ws = _loop_ws(cls._tmp, "ws")
        _drive(loop_mod, cls.ws)
        cls.routes = _routes(cls.ws)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, True)

    def test_every_traced_route_states_the_breadth_it_asked_for(self):
        self.assertTrue(self.routes)
        for row in self.routes:
            with self.subTest(step=row.get("step")):
                self.assertIn(row.get("requested_breadth"),
                              ("routed", "all"))

    def test_every_traced_route_says_whether_the_engine_produced_it(self):
        """`engine_ran` is the other half: "the engine ran and selected
        everything" and "the engine was switched off" are the same lens list
        and must never be the same record."""
        for row in self.routes:
            with self.subTest(step=row.get("step")):
                self.assertIsInstance(row.get("engine_ran"), bool)

    def test_the_working_steps_record_a_routed_breadth(self):
        working = [r for r in self.routes if r.get("step") != "em"]
        self.assertTrue(working)
        self.assertEqual({r["requested_breadth"] for r in working},
                         {"routed"})

    def test_the_final_review_records_selective_breadth(self):
        """R-0005: final EM maps the catalog once but dispatches selectively.

        The route record describes the request, not the number of mapping
        entries; a complete 26-lens decision is therefore still ``routed``.
        """
        em = [r for r in self.routes if r.get("step") == "em"]
        self.assertEqual([r["requested_breadth"] for r in em], ["routed"])
        self.assertEqual([r["engine_ran"] for r in em], [True])
        self.assertEqual([r["kernel_status"] for r in em], ["ready"])

    def test_the_evaluate_route_records_that_the_engine_chose(self):
        """Evaluate records that D-0014 bypassed the lens engine."""
        ev = [r for r in self.routes if r.get("step") == "evaluate"]
        self.assertTrue(ev)
        self.assertEqual({r["engine_ran"] for r in ev}, {False})
        self.assertEqual({r["requested_breadth"] for r in ev}, {"routed"})
        self.assertTrue(all(r.get("lenses") == [] for r in ev))

    def test_the_recorder_reads_those_routes_without_inferring(self):
        """End of the wire: the loop's own rows, through the real recorder,
        with the real catalog. The evaluate row names all 26 lenses."""
        biggest = max(self.routes, key=lambda r: len(r.get("lenses") or []))
        self.assertEqual(len(biggest["lenses"]), len(CATALOG_IDS),
                         "premise: a routed step names the whole catalog")
        rows = eval_record.synthesize_trace([dict(biggest)],
                                            known_lenses=set(CATALOG_IDS))
        self.assertEqual(rows[0]["breadth"], "routed")
        self.assertIn("recorded", rows[0]["breadth_source"])


PRE_INSTRUMENTATION_LOOP_BLOB = "dfb95b871361ed097d156e4785158cb7c86505a4"


def _loop_at_pre_instrumentation_baseline():
    """Load the exact loop.py blob immediately before breadth recording.

    A moving ``HEAD`` is not a baseline: after the instrumentation commit it
    contains the two fields the differential is meant to prove additive.
    The content-addressed blob keeps the real previous implementation (and
    its real routing path) stable across later commits without manufacturing
    a baseline by deleting fields from current source.
    """
    src = subprocess.run(
        ["git", "cat-file", "blob", PRE_INSTRUMENTATION_LOOP_BLOB], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", check=False)
    if src.returncode != 0 or not src.stdout:
        raise unittest.SkipTest(
            f"cannot read pinned pre-instrumentation loop.py: {src.stderr}")
    if "requested_breadth" in src.stdout or "engine_ran" in src.stdout:
        raise AssertionError("pinned baseline unexpectedly contains breadth "
                             "instrumentation")
    scratch = tempfile.mkdtemp(prefix="loop-baseline-")
    path = os.path.join(scratch, "loop_pre_instrumentation.py")
    _write(path, src.stdout)
    spec = importlib.util.spec_from_file_location(
        "loop_pre_instrumentation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)              # not registered in sys.modules
    mod._scratch_dir = scratch
    return mod


class TestStampingTheBreadthChangedNothingTheLoopDECIDES(unittest.TestCase):
    """The historical control proves what R-0005 deliberately superseded.

    The old loop remains pinned as a deterministic witness, while the current
    loop must expose the selective kernel in payloads and trace rather than
    remain byte-identical to the pre-kernel implementation.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="tp-evalrec-diff-")
        home = _private_store(cls, cls._tmp)
        cls.head = _loop_at_pre_instrumentation_baseline()
        cls.runs = {}
        for name, mod in (("control_a", cls.head), ("control_b", cls.head),
                          ("current", loop_mod)):
            # The SAME path each time, torn down between runs. Workspace-
            # derived slugs and hashes reach the payloads, so two runs at two
            # paths differ for a reason that is not the change under test.
            ws = _loop_ws(cls._tmp, "ws")
            cls.runs[name] = {"transcript": _canon(_drive(mod, ws), ws),
                              "routes": _routes(ws)}
            shutil.rmtree(ws, True)
            shutil.rmtree(home, True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, True)
        shutil.rmtree(getattr(cls.head, "_scratch_dir", ""), True)

    def test_the_control_repeats_itself_before_anything_is_compared(self):
        self.assertEqual(self.runs["control_a"]["transcript"],
                         self.runs["control_b"]["transcript"])

    def test_the_control_actually_exercised_the_routing_steps(self):
        """A differential over a transcript of nothing is green for free."""
        self.assertTrue(self.runs["control_a"]["routes"])
        self.assertIn("evaluate", {r.get("step") for r in
                                   self.runs["control_a"]["routes"]})

    def test_current_payload_exposes_the_selective_kernel(self):
        self.assertNotEqual(self.runs["current"]["transcript"],
                            self.runs["control_a"]["transcript"])
        self.assertIn('"review_kernel"', self.runs["current"]["transcript"])
        self.assertNotIn('"review_kernel"',
                         self.runs["control_a"]["transcript"])

    def test_the_trace_adds_kernel_status_to_the_recorded_fields(self):
        """The current trace states request, mapper execution, and kernel
        readiness; the pinned pre-kernel trace carries none of them."""
        added = {"requested_breadth", "engine_ran", "kernel_status"}
        for name in ("control_a", "current"):
            self.assertTrue(self.runs[name]["routes"])
        base = self.runs["control_a"]["routes"]
        now = self.runs["current"]["routes"]
        self.assertEqual(len(base), len(now))
        for was, is_ in zip(base, now):
            expected = {"requested_breadth", "engine_ran"}
            if is_.get("step") in ("evaluate", "em"):
                expected.add("kernel_status")
            self.assertEqual(set(was) & added, set())
            self.assertEqual(set(is_) - set(was), expected)
            # R-0005 deliberately changes the routed dispositions while
            # leaving the surrounding event identity stable.
            ignored = added | {"lenses", "ts"}
            self.assertEqual({k: v for k, v in is_.items()
                              if k not in ignored},
                             {k: v for k, v in was.items()
                              if k not in ignored})
            if is_.get("step") in ("evaluate", "em"):
                self.assertEqual(is_["lenses"], [])

    def test_the_baseline_could_not_tell_the_routed_review_from_all(self):
        """The regression this locks: over the PREVIOUS revision's own trace,
        the old inference called the compliant evaluate route `--all`."""
        ev = [r for r in self.runs["control_a"]["routes"]
              if r.get("step") == "evaluate"]
        self.assertTrue(ev)
        self.assertEqual(len(ev[0]["lenses"]), len(CATALOG_IDS))
        self.assertIsNone(eval_record.breadth_of(dict(ev[0]),
                                                 set(CATALOG_IDS)),
                          "an unrecorded full-catalog route is 'cannot "
                          "tell'; the old rule fabricated 'all' here")


if __name__ == "__main__":
    unittest.main()
