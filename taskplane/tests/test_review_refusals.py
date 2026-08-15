"""Two refusals a review must make BEFORE it reviews anything.

A field run of v2.13.0 against `aws/karpenter-provider-aws#9464` came back
fully governed: contract activated, target pinned and fingerprinted, every
obligation discharged, `steps.target ok: true` from end to end. It had
reviewed the WRONG TREE. An 83-file working tree was scored as a 4-file pull
request and seven deep lenses cited evidence from files the pull request
never touches. Nothing in the payload flagged it — a human noticed because
the file count was implausible.

The binding the harness already had proves the findings came from the tree
the workspace is pinned to. It cannot prove that tree is the one the review
CLAIMS to be about, and it says nothing about the OTHER derived artifact a
review leans on: the dependency graph the blast radius is read out of.

  D1  `review start` refuses a checkout whose head is not the pull
      request's `refs/pull/N/head`. Naming both shas is the point: "wrong
      tree" with no numbers is unactionable.
  D2  a blast radius computed from a graph scanned at ANOTHER revision
      blocks — folded into `binding_problem`, because that is the function
      the gate actually consults. A mechanism the gate never calls is a
      mechanism that does not exist.

Both refusals are deliberately narrow, and the two ways they could be worse
than useless are pinned here:

  * a NON-PR target must not touch the network at all — reviewing a branch
    or a range is legitimate and must stay free;
  * an UNREACHABLE remote is an environment fact, not a wrong tree. Blocking
    there makes the refusal the first thing anyone disables in a sandbox,
    and a disabled refusal catches nothing.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import depgraph                   # noqa: E402
import target as tgt              # noqa: E402
import taskplane_lite as tp       # noqa: E402
import tp as cli                  # noqa: E402


def _run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(args))
        except SystemExit as e:
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8",
                          errors="replace").stdout.strip()


SLUG = "aws/karpenter-provider-aws"
SPEC = f"{SLUG}#42"


class _PRRepo(unittest.TestCase):
    """A checkout plus a local `origin` that really serves
    `refs/pull/42/head` — the same ref shape GitHub serves, so nothing here
    needs the network or a stubbed git."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tp-refusal-")
        self.ws = os.path.join(self.d, "repo")
        os.makedirs(self.ws)
        for a in (["init", "-q"], ["config", "user.email", "e@e"],
                  ["config", "user.name", "t"]):
            _git(self.ws, *a)
        self._commit(self.ws, "pkg/a.py", "def a():\n    return 1\n", "mod a")
        self._commit(self.ws, "pkg/b.py", "from pkg.a import a\n", "mod b")
        self._commit(self.ws, "a.txt", "one\n", "base")
        self._commit(self.ws, "a.txt", "two\n", "change")
        self.head = _git(self.ws, "rev-parse", "HEAD")

        self.upstream = os.path.join(self.d, "upstream")
        shutil.copytree(self.ws, self.upstream)
        _git(self.upstream, "checkout", "-q", "-b", "feature")
        self._commit(self.upstream, "c.txt", "pr\n", "pull request work")
        self.pr_head = _git(self.upstream, "rev-parse", "feature")
        _git(self.upstream, "update-ref", "refs/pull/42/head", "feature")
        _git(self.ws, "remote", "add", "origin", self.upstream)

        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "store")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.d, ignore_errors=True)

    def _commit(self, root, name, body, msg):
        p = os.path.join(root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", msg)

    def _pinned(self, spec=SPEC):
        return tgt.pin(self.ws, target=tgt.parse(spec))

    def _spy_on_git(self):
        """Every `target.git` invocation, so "it never touched the remote"
        is an assertion about calls and not about a return value."""
        calls = []
        real = tgt.git

        def spy(root, *args, **kw):
            calls.append(args)
            return real(root, *args, **kw)

        tgt.git = spy
        self.addCleanup(setattr, tgt, "git", real)
        return calls


# --------------------------------------------------------------------------
# D1 — the tree under review must BE the pull request's head
# --------------------------------------------------------------------------

class TestResolvingThePullRequestsHead(_PRRepo):
    """The only value that settles "is this the right tree" is the one the
    remote serves for `refs/pull/N/head`. It is read, never inferred."""

    def test_it_reads_the_head_the_remote_actually_serves(self):
        res = tgt.resolve_pr_head(self.ws, tgt.parse(SPEC))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["sha"], self.pr_head)
        self.assertNotEqual(res["sha"], self.head)

    def test_it_asks_for_refs_pull_N_head(self):
        """The ref matters: `pull/N/merge` is GitHub's throwaway merge
        commit and would refuse every legitimately checked-out pull request.
        `refs/pull/N/head` is the pull request's own tip."""
        calls = self._spy_on_git()
        tgt.resolve_pr_head(self.ws, tgt.parse(f"{SLUG}#9464"))
        self.assertTrue(calls, "it must ask the remote something")
        self.assertIn(("ls-remote", "origin", "refs/pull/9464/head"), calls)
        for args in calls:
            self.assertNotIn("refs/pull/9464/merge", args)

    def test_an_unreachable_remote_is_reported_not_raised(self):
        _git(self.ws, "remote", "set-url", "origin",
             os.path.join(self.d, "gone"))
        res = tgt.resolve_pr_head(self.ws, tgt.parse(SPEC))
        self.assertFalse(res["ok"])
        self.assertIsNone(res.get("sha"))

    def test_a_non_pr_target_is_not_resolved_and_asks_nothing(self):
        calls = self._spy_on_git()
        res = tgt.resolve_pr_head(self.ws, tgt.parse("feature/widget"))
        self.assertFalse(res["ok"])
        self.assertEqual(calls, [])


class TestATreeThatIsNotThePullRequestsHeadIsRefused(_PRRepo):
    """THE defect. The field run reviewed an 83-file working tree as a
    4-file pull request and every gate passed, because nothing compared the
    checkout to the pull request it claimed to be."""

    def test_the_refusal_names_both_shas_the_repo_and_the_number(self):
        why = tgt.wrong_tree(self.ws, self._pinned())
        self.assertTrue(why, "a tree that is not the PR head must refuse")
        self.assertIn(self.head[:12], why)
        self.assertIn(self.pr_head[:12], why)
        self.assertIn(SLUG, why)
        self.assertIn("42", why)

    def test_the_pull_requests_own_head_passes(self):
        _git(self.ws, "fetch", "-q", "origin",
             "refs/pull/42/head:tp-pr-42")
        _git(self.ws, "checkout", "-q", "tp-pr-42")
        rec = self._pinned()
        self.assertEqual(rec["head"], self.pr_head)
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))

    def test_a_short_sha_matches_a_full_one_in_either_direction(self):
        """`git ls-remote` serves 40 characters, but a record may carry an
        abbreviated head (a `--short` rev-parse, a copied payload). Treating
        an abbreviation as a mismatch would refuse a correct tree — the one
        failure mode that gets a refusal switched off."""
        self._fake_resolution(self.pr_head)
        rec = {"head": self.pr_head[:12], "target": tgt.parse(SPEC)}
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))
        self._fake_resolution(self.pr_head[:12])
        rec = {"head": self.pr_head, "target": tgt.parse(SPEC)}
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))
        self._fake_resolution("f" * 40)
        self.assertTrue(tgt.wrong_tree(self.ws, rec))

    def _fake_resolution(self, sha):
        real = tgt.resolve_pr_head
        tgt.resolve_pr_head = lambda *a, **k: {"ok": True, "sha": sha}
        self.addCleanup(setattr, tgt, "resolve_pr_head", real)

    def test_an_unreachable_remote_is_advisory_never_a_refusal(self):
        """An offline remote is an environment fact, not a wrong tree. A
        refusal that fires in every sandbox is the first thing anyone
        disables, and a disabled refusal catches nothing."""
        _git(self.ws, "remote", "set-url", "origin",
             os.path.join(self.d, "gone"))
        rec = self._pinned()
        self.assertNotEqual(rec["head"], self.pr_head)
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))

    def test_a_branch_target_never_touches_the_remote(self):
        """Reviewing a branch or a range is as legitimate as reviewing a
        pull request, and must stay free: no ls-remote, no network, no
        timeout to sit through."""
        rec = self._pinned("feature/widget")
        calls = self._spy_on_git()
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))
        self.assertEqual(calls, [])

    def test_a_record_with_no_target_is_not_a_wrong_tree(self):
        rec = tgt.pin(self.ws)                 # pinned BEFORE the spy: only
        calls = self._spy_on_git()             # wrong_tree's calls count
        self.assertIsNone(tgt.wrong_tree(self.ws, rec))
        self.assertIsNone(tgt.wrong_tree(self.ws, {}))
        self.assertIsNone(tgt.wrong_tree(self.ws, None))
        self.assertEqual(calls, [])


class TestReviewStartRefusesBeforeItDoesAnyWork(_PRRepo):
    """The refusal is worth nothing at the end. `review start` activates a
    contract, seeds obligations, scans the graph and hands out lens briefs —
    every one of those is work spent on the wrong tree."""

    def test_it_refuses_and_names_the_tree_it_expected(self):
        rc, out, _err = _run("review", "start", SPEC, "--base", "HEAD~1",
                             "--workspace", self.ws)
        self.assertEqual(rc, 1, out)
        d = json.loads(out)
        self.assertFalse(d["ok"])
        self.assertEqual(d["status"], "target_not_checked_out")
        self.assertIn("tp review start", d["recovery"])
        self.assertIn("--fetch", d["recovery"])
        step = [s for s in d["steps"] if s["step"] == "target"][0]
        self.assertFalse(step["ok"])
        self.assertIn(self.pr_head[:12], step["reason"])
        self.assertIn(self.head[:12], step["reason"])

    def test_nothing_is_activated_and_no_later_step_runs(self):
        _rc, out, _err = _run("review", "start", SPEC, "--base", "HEAD~1",
                              "--workspace", self.ws)
        d = json.loads(out)
        self.assertEqual([s["step"] for s in d["steps"]], ["tools", "target"])
        self.assertFalse((tp.load_active(self.ws) or {}).get("task_id"))
        self.assertNotIn("dispatch", d)

    def test_a_ref_target_that_is_not_checked_out_is_refused_too(self):
        _git(self.ws, "fetch", "-q", "origin",
             "refs/pull/42/head:tp-pr-42")
        rc, out, _err = _run(
            "review", "start", "tp-pr-42", "--base", "HEAD~1",
            "--workspace", self.ws)
        self.assertEqual(rc, 1, out)
        d = json.loads(out)
        self.assertEqual(d["status"], "target_not_checked_out")
        self.assertIn("git checkout tp-pr-42", d["recovery"])

    def test_a_setup_refusal_creates_no_kernel_cache_and_retry_is_fresh(self):
        rc, out, _err = _run("review", "start", SPEC, "--base", "HEAD~1",
                             "--workspace", self.ws)
        self.assertEqual(rc, 1, out)
        self.assertFalse(os.path.exists(os.path.join(
            self.ws, ".em-review", "kernel-v2")))
        _git(self.ws, "fetch", "-q", "origin",
             "refs/pull/42/head:tp-pr-42")
        _git(self.ws, "checkout", "-q", "tp-pr-42")
        rc, out, _err = _run("review", "start", SPEC, "--base", "HEAD~1",
                             "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["status"], "ready")

    def test_the_pull_requests_head_opens_the_review_normally(self):
        _git(self.ws, "fetch", "-q", "origin", "refs/pull/42/head:tp-pr-42")
        _git(self.ws, "checkout", "-q", "tp-pr-42")
        rc, out, _err = _run("review", "start", SPEC, "--base", "HEAD~1",
                             "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        d = json.loads(out)
        self.assertEqual(d["status"], "ready")
        self.assertTrue(d["target_fingerprint"])
        self.assertEqual(d["preflight"]["identity"]["head"], self.pr_head)
        self.assertEqual(d["preflight"]["identity"]["merge_base"],
                         d["preflight"]["cache_identity"]["merge_base"])
        self.assertTrue(
            d["preflight"]["cache_identity"]["graph_revision"])


class TestReviewPreflightDiagnostics(_PRRepo):
    def test_a_parseable_mismatched_origin_is_wrong_repository(self):
        _git(self.ws, "remote", "set-url", "origin",
             "https://github.com/elsewhere/not-backstage.git")
        rec = tgt.pin(
            self.ws, base="HEAD~1",
            target=tgt.parse("https://github.com/backstage/backstage/pull/42"))
        out = tgt.review_preflight(self.ws, rec)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "wrong_repository")
        self.assertIn("elsewhere/not-backstage", out["reason"])
        self.assertIn("backstage/backstage", out["reason"])

    def test_a_missing_merge_base_is_not_graph_insufficiency(self):
        rec = self._pinned()
        rec.update({"base_ref": "origin/main", "base": "b" * 40,
                    "merge_base": None, "shallow": True,
                    "changed_files": []})
        out = tgt.review_preflight(self.ws, rec)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "merge_base_missing")
        self.assertNotIn("graph", out["reason"].lower())
        self.assertIn("--fetch", out["recovery"])


# --------------------------------------------------------------------------
# D2 — a blast radius read out of a graph scanned somewhere else
# --------------------------------------------------------------------------

def _findings(fingerprint=None, scanned_head=None):
    meta = {"title": "probe"}
    if fingerprint is not None:
        meta["target"] = {"fingerprint": fingerprint}
    if scanned_head is not None:
        meta["impact"] = {"graph": {"scanned_head": scanned_head}}
    return {"meta": meta, "findings": []}


class TestAGraphScannedAtAnotherRevisionBlocks(unittest.TestCase):
    """The blast radius is the one input a reviewer is told NOT to
    re-derive, so a graph scanned at another revision quietly names files as
    they were somewhere else — and the review that reads it looks exactly
    like a review that read the right one."""

    def test_the_refusal_names_both_revisions_and_the_remedy(self):
        why = tgt.graph_problem({"head": "h" * 40},
                                _findings(scanned_head="s" * 40))
        self.assertTrue(why)
        self.assertIn("s" * 12, why)
        self.assertIn("h" * 12, why)
        self.assertIn("tp graph scan", why)

    def test_the_same_revision_passes(self):
        head = "abc123def4567890" + "0" * 24
        self.assertIsNone(tgt.graph_problem(
            {"head": head}, _findings(scanned_head=head)))

    def test_a_twelve_character_prefix_is_the_comparison(self):
        """`scanned_head` and the pinned head are recorded by different
        writers; comparing full strings would refuse an abbreviation that
        names the same commit."""
        head = "abc123def456" + "9" * 28
        self.assertIsNone(tgt.graph_problem(
            {"head": head}, _findings(scanned_head=head[:12])))

    def test_findings_without_a_scanned_head_do_not_block(self):
        """FAIL OPEN on absence. An older findings file carries no
        `scanned_head` at all, and inventing a mismatch there would block
        reviews that are fine — which is how a refusal gets deleted."""
        for name, doc in (("empty", {}),
                          ("no meta", {"findings": []}),
                          ("no impact", _findings("fp")),
                          ("no graph", {"meta": {"impact": {}}}),
                          ("no scanned_head", {"meta": {"impact":
                                                        {"graph": {}}}}),
                          ("empty scanned_head",
                           _findings(scanned_head="")),
                          ("null scanned_head",
                           {"meta": {"impact": {"graph":
                                                {"scanned_head": None}}}})):
            with self.subTest(shape=name):
                self.assertIsNone(tgt.graph_problem({"head": "h" * 40}, doc))

    def test_an_unpinned_record_does_not_block(self):
        self.assertIsNone(tgt.graph_problem(
            {}, _findings(scanned_head="s" * 40)))
        self.assertIsNone(tgt.graph_problem(
            None, _findings(scanned_head="s" * 40)))

    def test_a_malformed_impact_block_does_not_raise(self):
        for bad in ({"meta": {"impact": "none"}},
                    {"meta": {"impact": {"graph": "none"}}},
                    {"meta": "none"}):
            with self.subTest(doc=bad):
                self.assertIsNone(tgt.graph_problem({"head": "h" * 40}, bad))


class TestTheGateReallyCarriesTheGraphCheck(_PRRepo):
    """`binding_problem` is what the screener and the sign-off gate consult.
    A graph check that lives beside it and is never called from it is a
    mechanism that does not exist — so the WIRING is tested, not just the
    logic."""

    def test_binding_problem_reports_a_graph_from_another_revision(self):
        rec = tgt.save(self.ws, tgt.pin(self.ws))
        why = tgt.binding_problem(
            self.ws, _findings(fingerprint=rec["fingerprint"],
                               scanned_head="s" * 40))
        self.assertTrue(why, "the gate must carry the graph check")
        self.assertIn("s" * 12, why)
        self.assertIn("tp graph scan", why)

    def test_a_graph_at_the_reviewed_head_is_bound(self):
        rec = tgt.save(self.ws, tgt.pin(self.ws))
        self.assertIsNone(tgt.binding_problem(
            self.ws, _findings(fingerprint=rec["fingerprint"],
                               scanned_head=rec["head"])))

    def test_the_older_binding_failures_still_come_first(self):
        """The graph check is the LAST statement, not a replacement: an
        uncited or wrongly-cited tree is the more basic failure and must
        keep its own message."""
        rec = tgt.save(self.ws, tgt.pin(self.ws))
        why = tgt.binding_problem(self.ws,
                                  _findings(scanned_head="s" * 40))
        self.assertIn("do not cite the reviewed tree", why)
        why = tgt.binding_problem(
            self.ws, _findings(fingerprint="deadbeefdeadbeef",
                               scanned_head=rec["head"]))
        self.assertIn("different checkout", why)

    def test_findings_none_is_still_only_about_the_pin(self):
        tgt.save(self.ws, tgt.pin(self.ws))
        self.assertIsNone(tgt.binding_problem(self.ws))


class TestReviewStartRescansAGraphFromAnotherRevision(_PRRepo):
    """`review start` hands the blast radius to every lens. Loading a stored
    graph without checking WHICH tree it was scanned at is how the wrong
    revision reaches seven deep lenses at once."""

    def test_a_graph_scanned_elsewhere_is_rescanned(self):
        depgraph.scan(self.ws)
        g = depgraph.load(self.ws)
        g["meta"]["scanned_head"] = "s" * 40
        depgraph.save(self.ws, g)
        _rc, out, _err = _run("review", "start", "--base", "HEAD~1",
                              "--workspace", self.ws)
        d = json.loads(out)
        quality_path = os.path.join(
            self.ws, d["graph_quality"]["relative_path"])
        with open(quality_path, encoding="utf-8") as stream:
            quality = json.load(stream)
        self.assertEqual(quality["scanned_head"], self.head)
        self.assertEqual(
            (depgraph.load(self.ws)["meta"] or {}).get("scanned_head"),
            self.head)


if __name__ == "__main__":
    unittest.main()
