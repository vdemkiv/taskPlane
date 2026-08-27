"""The release-tag gate: run it on this repo, and prove it CATCHES drift.

Five releases (v2.5.0, v2.5.1, v2.6.0, v2.8.1, v2.8.2) shipped with no tag
and nobody noticed for months, because tagging lived in a human's memory of
the release routine. Writing the routine down again was never going to work
— every previous "resolved" of this class was written down and skipped. So
the routine is now a check that fails.

Running the gate on this repo (below) proves the CURRENT state is clean. It
does not prove the gate would notice if it stopped being clean — a check that
always passes is worse than no check, because it reads as evidence. So most
of this file builds throwaway git repos with a specific defect planted in
each, and asserts the gate names that defect.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ci_release_tags as gate     # noqa: E402


def _git(root, *args, check=True):
    p = subprocess.run(["git"] + list(args), cwd=root,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stdout}")
    return p.stdout.strip()


class _Repo:
    """A tiny repo whose only content is a plugin manifest and a CHANGELOG."""

    def __init__(self, path):
        self.path = path
        _git(path, "init", "-q", "-b", "main")
        _git(path, "config", "user.email", "t@example.com")
        _git(path, "config", "user.name", "t")
        os.makedirs(os.path.join(path, ".codex-plugin"))
        self.versions = []

    def release(self, version):
        with open(os.path.join(self.path, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": version}, f)
        self.versions.append(version)
        self._changelog()
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", f"v{version}")
        return _git(self.path, "rev-parse", "HEAD")

    def _changelog(self, extra=()):
        rows = ["| Version | Highlights |", "| --- | --- |"]
        for v in reversed(list(self.versions) + list(extra)):
            rows.append(f"| **v{v}** | notes |")
        with open(os.path.join(self.path, "CHANGELOG.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    def changelog_claims(self, version):
        """Add a CHANGELOG row for a version that was never shipped."""
        self._changelog(extra=[version])
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", "changelog")

    def tag(self, version, commit="HEAD"):
        _git(self.path, "tag", "-a", f"v{version}", commit, "-m", f"v{version}")

    def audit(self):
        return gate.audit(self.path)

    def checks(self):
        return sorted(p["check"] for p in self.audit()["problems"])


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = _Repo(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestThisRepoIsClean(unittest.TestCase):
    def test_every_shipped_version_here_is_tagged(self):
        res = gate.audit()
        if res.get("unavailable"):
            self.skipTest(f"release history unavailable: {res['unavailable']}")
        self.assertEqual(
            res["problems"], [],
            "release-tag drift: " + "; ".join(
                f"{p['check']} v{p['version']}: {p['detail']}"
                for p in res["problems"]))

    def test_this_repo_passes_the_gate_when_the_gate_can_see(self):
        """Runs the real script against this checkout — but only where the
        clone HAS the history and tags to check.

        The first version of this asserted exit 0 unconditionally. The main
        test job checks out shallow and tagless (only the dedicated
        release-tags job sets fetch-depth 0 and fetch-tags), so the gate
        correctly reported CANNOT VERIFY, exited 1, and the test failed on
        the gate being RIGHT. An environment-dependent assertion in a test
        is the same defect class the gate exists to catch: a claim nobody
        checked against the thing that settles it."""
        if gate.audit().get("unavailable"):
            self.skipTest("shallow/tagless clone — see the release-tags job")
        p = subprocess.run([sys.executable, "scripts/ci_release_tags.py"],
                           cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout)


class TestTheExitCodes(_RepoCase):
    """A gate that reports problems on stdout and exits 0 is not a gate.
    Proven on synthetic repos, so these assertions hold in EVERY CI job
    regardless of what that job fetched."""

    def _run(self, root):
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts",
                                          "ci_release_tags.py"), root],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8")
        return p.returncode, p.stdout

    def test_the_source_returns_one_when_not_ok(self):
        src = open(os.path.join(ROOT, "scripts", "ci_release_tags.py"),
                   encoding="utf-8").read()
        self.assertIn("return 0 if res[\"ok\"] else 1", src)

    def test_a_clean_repo_exits_zero(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 0, out)
        self.assertIn("ok:", out)

    def test_a_repo_with_drift_exits_one(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 1, out)
        self.assertIn("C1 v1.0.0", out)
        self.assertIn("FAIL", out)

    def test_a_tagless_clone_exits_one_and_says_it_cannot_verify(self):
        """The exact shape that made this file red in CI. It must FAIL
        closed and name the checkout setting that fixes it."""
        self.repo.release("1.0.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 1, out)
        self.assertIn("CANNOT VERIFY", out)
        self.assertIn("fetch-tags", out)

    def test_a_repo_with_no_mainline_exits_one(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            rc, out = self._run(d)
            self.assertEqual(rc, 1, out)
            self.assertIn("CANNOT VERIFY", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_json_mode_carries_the_same_exit_code(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts",
                                          "ci_release_tags.py"),
             self.dir, "--json"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 1)
        self.assertFalse(json.loads(p.stdout)["ok"])


class TestItCatchesAMissingTag(_RepoCase):
    def test_an_untagged_older_release_is_C1(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        self.assertEqual(self.repo.checks(), ["C1"])
        self.assertIn("has no v1.0.0 tag",
                      self.repo.audit()["problems"][0]["detail"])

    def test_the_newest_release_may_be_untagged(self):
        """Between merging the bump and tagging after CI, main is legitimately
        one release ahead. At most ONE — which is what stops five from piling
        up."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.assertEqual(self.repo.checks(), [])

    def test_two_untagged_releases_is_never_ok(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.release("1.2.0")
        self.assertEqual(self.repo.checks(), ["C1"])

    def test_five_untagged_releases_reports_all_five(self):
        """The exact shape that actually happened."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        for v in ("1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0"):
            self.repo.release(v)
        self.assertEqual(self.repo.checks(), ["C1"] * 5)


class TestTheReleaseInFlightIsExempt(_RepoCase):
    """A release is prepared by bumping the manifest and writing its
    CHANGELOG row BEFORE the commit reaches the mainline. C1 already
    tolerates the newest mainline version being untagged for that reason;
    C4 has to tolerate its CHANGELOG row for the same one."""

    def test_a_changelog_row_for_the_version_on_disk_is_fine(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        # bump the working tree only: no commit, so the mainline has not
        # seen 1.2.0 yet, and the CHANGELOG already names it.
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.2.0"}, f)
        self.repo.changelog_claims("1.2.0")
        self.assertEqual(self.repo.checks(), [])

    def test_a_stack_of_unpushed_release_commits_is_fine(self):
        """Two releases can be prepared locally before either is pushed —
        which is exactly what happened here: v2.11.0 was committed and
        v2.12.0 was in the working tree, and a one-version exemption called
        the older one fictional."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        # 1.1.0 and 1.2.0 committed locally; the mainline is `main` and has
        # them, so simulate "not yet on the mainline" by pointing the gate
        # at a mainline that stops earlier.
        self.repo.release("1.1.0")
        self.repo.release("1.2.0")
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.3.0"}, f)
        self.repo.changelog_claims("1.3.0")
        checks = self.repo.checks()
        self.assertNotIn("C4", checks, f"unexpected: {self.repo.audit()['problems']}")

    def test_it_exempts_exactly_one_version_not_any_unshipped_row(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.2.0"}, f)
        self.repo.changelog_claims("9.9.9")
        self.assertIn("C4", self.repo.checks())


class TestItCatchesAMisplacedTag(_RepoCase):
    def test_a_tag_on_the_wrong_commit_is_C3(self):
        self.repo.release("1.0.0")
        c11 = self.repo.release("1.1.0")
        c12 = self.repo.release("1.2.0")
        self.repo.tag("1.0.0", c11)      # points at the 1.1.0 tree
        self.repo.tag("1.1.0", c11)
        self.repo.tag("1.2.0", c12)
        self.assertEqual(self.repo.checks(), ["C3"])
        self.assertIn("declares '1.1.0', not '1.0.0'",
                      self.repo.audit()["problems"][0]["detail"])

    def test_a_tag_off_the_mainline_is_C2(self):
        """A tag on an abandoned branch looks fine in `git tag -l` and points
        at a tree nobody can check out from main."""
        c10 = self.repo.release("1.0.0")
        self.repo.tag("1.0.0", c10)
        _git(self.dir, "checkout", "-q", "-b", "side")
        with open(os.path.join(self.dir, "SIDE"), "w") as f:
            f.write("abandoned\n")      # or git dedupes it into one commit
        side = self.repo.release("1.1.0")
        self.repo.tag("1.1.0", side)
        _git(self.dir, "checkout", "-q", "-f", "main")
        self.repo.versions = ["1.0.0"]
        self.repo.release("1.1.0")       # 1.1.0 also shipped ON main
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        self.assertIn("C2", self.repo.checks())

    def test_an_annotated_tag_is_not_mistaken_for_a_dangling_commit(self):
        """The 'dangling v2.8.0' that cost an hour was an annotated tag OBJECT
        sha read as a commit sha. The gate must dereference."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        tags = gate.release_tags(self.dir)
        obj = _git(self.dir, "rev-parse", "v1.0.0")
        commit = _git(self.dir, "rev-parse", "v1.0.0^{}")
        self.assertNotEqual(obj, commit, "expected an annotated tag")
        self.assertEqual(tags["v1.0.0"], commit)

    def test_a_tagged_release_on_a_merged_side_parent_is_real(self):
        """v2.17.17's exact shape: its tagged manifest commit is reachable
        through a no-ff merge, but the merge's first-parent tree had already
        advanced. It is a release, not a fictional CHANGELOG row or tag."""
        c10 = self.repo.release("1.0.0")
        self.repo.tag("1.0.0", c10)
        _git(self.dir, "checkout", "-q", "-b", "release-side")
        side = self.repo.release("1.0.5")
        self.repo.tag("1.0.5", side)

        _git(self.dir, "checkout", "-q", "main")
        self.repo.versions = ["1.0.0"]
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        _git(self.dir, "merge", "-q", "--no-ff", "-s", "ours",
             "release-side", "-m", "merge released side parent")
        self.repo.versions = ["1.0.0", "1.1.0"]
        self.repo.changelog_claims("1.0.5")

        result = self.repo.audit()
        self.assertEqual(result["problems"], [])
        self.assertIn("1.0.5", result["shipped"])
        self.assertEqual(result["tagged_side_releases"], ["1.0.5"])


class TestItCatchesAFictionalRelease(_RepoCase):
    def test_a_changelog_row_no_tree_ever_declared_is_C4(self):
        """v2.4.0's row was ADDED by the commit that bumped to 2.5.0."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.changelog_claims("1.0.5")
        self.assertEqual(self.repo.checks(), ["C4"])
        self.assertIn("no tree ever declared",
                      self.repo.audit()["problems"][0]["detail"])

    def test_a_tag_for_a_version_that_never_shipped_is_C6(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.tag("9.9.9")
        self.assertIn("C6", self.repo.checks())


class TestTheExemptionListCannotRot(unittest.TestCase):
    """NOT_SHIPPED is the one soft spot: a list of versions the gate agrees
    not to demand tags for. C5 re-derives whether each entry is still true, so
    it cannot quietly become a way to un-tag a real release."""

    def test_every_exemption_carries_a_reason_and_a_destination(self):
        self.assertTrue(gate.NOT_SHIPPED)
        for v, info in gate.NOT_SHIPPED.items():
            with self.subTest(v):
                self.assertGreater(len(info.get("reason", "")), 60,
                                   "an exemption needs a real explanation")
                self.assertIn("co_released_with", info)

    def test_an_exemption_for_a_version_that_did_ship_is_C5(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            repo.tag("1.0.0")
            repo.release("1.1.0")
            repo.tag("1.1.0")
            orig = gate.NOT_SHIPPED
            gate.NOT_SHIPPED = dict(orig)
            gate.NOT_SHIPPED["1.0.0"] = {"reason": "x" * 70,
                                         "co_released_with": "1.1.0"}
            try:
                checks = sorted(p["check"] for p in gate.audit(d)["problems"])
            finally:
                gate.NOT_SHIPPED = orig
            self.assertIn("C5", checks)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_two_real_exemptions_are_still_not_shipped(self):
        res = gate.audit()
        if res.get("unavailable"):
            self.skipTest(res["unavailable"])
        for v in gate.NOT_SHIPPED:
            self.assertNotIn(v, res["shipped"],
                             f"v{v} is exempted but IS in the manifest "
                             f"history — tag it and drop the exemption")


class TestItFailsClosedWhenItCannotSee(unittest.TestCase):
    """A gate that skips when the clone is shallow or tagless is a gate that
    passes on every misconfigured runner."""

    def test_a_repo_with_no_main_is_unavailable_not_ok(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            res = gate.audit(d)
            self.assertFalse(res["ok"])
            self.assertIn("unavailable", res)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_repo_with_no_tags_is_unavailable_not_ok(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            res = gate.audit(d)
            self.assertFalse(res["ok"])
            self.assertIn("fetch-tags", res["unavailable"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unavailable_exits_nonzero(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            src = open(os.path.join(ROOT, "scripts", "ci_release_tags.py"),
                       encoding="utf-8").read()
            self.assertIn("CANNOT VERIFY", src)
            self.assertIn("        return 1", src)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCiWiring(unittest.TestCase):
    """The gate has to actually run, with enough history to be able to see."""

    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"),
                  encoding="utf-8") as f:
            self.ci = f.read()

    def test_ci_runs_the_gate(self):
        self.assertIn("scripts/ci_release_tags.py", self.ci)

    def test_the_leg_that_runs_it_fetches_full_history_and_tags(self):
        idx = self.ci.index("scripts/ci_release_tags.py")
        job = self.ci[:idx]
        tail = job[job.rindex("runs-on:"):]
        self.assertIn("fetch-depth: 0", tail,
                      "the release-tag gate needs full history")
        self.assertIn("fetch-tags: true", tail,
                      "the release-tag gate needs tags")


if __name__ == "__main__":
    unittest.main()
