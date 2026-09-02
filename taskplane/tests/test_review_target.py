"""A review must name the tree it reviewed, and git/gh must be present.

Two field reviews of `aws/karpenter-provider-aws#9464` both cloned the
repository and neither could PROVE it. `tp new` took the target as free text
and the contract recorded `task`, `read_only`, `write_allow`, `budget` —
no origin, no base, no head, no record of how the code arrived. So both
reports stated the workspace and the diff base in prose, by hand, and a
review conducted entirely from a rendered web diff would have produced
identical artifacts and an identical gate.

Same conversion as the obligations before it: not "please record the
target" — the run may not DECLARE ITSELF FINISHED until it has.

The tool half is the other lesson from those runs. `gh` was missing, and
the PR's title, body, linked issue and review conversation — none of which
live in the git objects — arrived over unauthenticated web reads that
nothing recorded. A clone gives you the code and none of the intent.
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import target as tgt              # noqa: E402
import taskplane_lite as tp       # noqa: E402
import tp as cli                  # noqa: E402
from taskplane.tests.native_meter_support import attach_native_counter  # noqa: E402


def _run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(args))
        except SystemExit as e:
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


class TestParsingATarget(unittest.TestCase):
    def test_it_reads_every_shape_a_human_types(self):
        for spec in ("https://github.com/aws/karpenter-provider-aws/pull/9464",
                     "http://github.com/aws/karpenter-provider-aws/pull/9464",
                     "github.com/aws/karpenter-provider-aws/pull/9464"):
            with self.subTest(spec):
                t = tgt.parse(spec)
                self.assertEqual(
                    (t["kind"], t["owner"], t["repo"], t["number"]),
                    ("pr", "aws", "karpenter-provider-aws", 9464))

    def test_short_and_bare_forms(self):
        t = tgt.parse("aws/karpenter-provider-aws#9464")
        self.assertEqual((t["kind"], t["number"]), ("pr", 9464))
        self.assertEqual(tgt.parse("#12")["number"], 12)
        self.assertEqual(tgt.parse("12")["number"], 12)

    def test_a_git_ref_is_a_target_not_an_error(self):
        """Reviewing a branch or a range is as legitimate as reviewing a
        PR — refusing it would push people back to unpinned reviews."""
        self.assertEqual(tgt.parse("release/2.x")["kind"], "ref")
        self.assertEqual(tgt.parse("")["kind"], "local")

    def test_a_dot_git_suffix_does_not_become_part_of_the_repo_name(self):
        t = tgt.parse("https://github.com/o/r.git/pull/7")
        self.assertEqual(t["repo"], "r")


class _Repo(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ws = os.path.join(self.d, "repo")
        os.makedirs(self.ws)
        for a in (["init", "-q"], ["config", "user.email", "e@e"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.ws, capture_output=True)
        self._commit("a.txt", "one\n", "base")
        self.base = self._head()
        self._commit("a.txt", "two\n", "change")
        self.head = self._head()
        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "store")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.d, ignore_errors=True)

    def _commit(self, name, body, msg):
        with open(os.path.join(self.ws, name), "w") as f:
            f.write(body)
        subprocess.run(["git", "add", "-A"], cwd=self.ws, capture_output=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=self.ws,
                       capture_output=True)

    def _head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.ws,
                              capture_output=True, text=True,
                              encoding="utf-8",
                              errors="replace").stdout.strip()

    def _findings(self, fingerprint=None):
        d = os.path.join(self.ws, ".em-review")
        os.makedirs(d, exist_ok=True)
        meta = {"title": "probe"}
        if fingerprint is not None:
            meta["target"] = {"fingerprint": fingerprint}
        with open(os.path.join(d, "findings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"meta": meta, "findings": []}, f)


class TestPinningTheCheckout(_Repo):
    def test_it_records_what_the_tree_actually_is(self):
        rec = tgt.pin(self.ws, base=self.base)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["head"], self.head)
        self.assertEqual(rec["base"], self.base)
        self.assertEqual(rec["changed_files"], ["a.txt"])
        self.assertTrue(rec["fingerprint"])

    def test_a_non_repo_cannot_be_pinned(self):
        empty = os.path.join(self.d, "empty")
        os.makedirs(empty)
        rec = tgt.pin(empty)
        self.assertFalse(rec["ok"])
        self.assertIn("not a git checkout", rec["reason"])

    def test_the_fingerprint_changes_with_the_tree(self):
        a = tgt.pin(self.ws, base=self.base)["fingerprint"]
        self._commit("b.txt", "x\n", "more")
        b = tgt.pin(self.ws, base=self.base)["fingerprint"]
        self.assertNotEqual(a, b)

    def test_the_fingerprint_is_stable_for_the_same_tree(self):
        self.assertEqual(tgt.pin(self.ws, base=self.base)["fingerprint"],
                         tgt.pin(self.ws, base=self.base)["fingerprint"])

    def test_review_scratch_does_not_invalidate_the_binding(self):
        """A reviewer writes `.em-review/**` under its own contract. If that
        broke the pin, the binding would fail exactly when it is used."""
        before = tgt.pin(self.ws, base=self.base)["fingerprint"]
        self._findings()
        after = tgt.pin(self.ws, base=self.base)
        self.assertEqual(after["fingerprint"], before)
        self.assertTrue(after["dirty"], "dirt is still RECORDED, just not "
                                        "folded into the fingerprint")

    def test_a_different_origin_is_a_different_target(self):
        a = tgt.fingerprint({"origin": "git@github.com:a/b.git",
                             "base": "1" * 40, "head": "2" * 40})
        b = tgt.fingerprint({"origin": "git@github.com:evil/b.git",
                             "base": "1" * 40, "head": "2" * 40})
        self.assertNotEqual(a, b)

    def test_history_shape_is_part_of_the_target_fingerprint(self):
        base = {"origin": "git@github.com:a/b.git", "base": "1" * 40,
                "head": "2" * 40}
        shallow = tgt.fingerprint({**base, "merge_base": None,
                                   "shallow": True})
        complete = tgt.fingerprint({**base, "merge_base": "3" * 40,
                                    "shallow": False})
        self.assertNotEqual(shallow, complete)
        unknown = tgt.fingerprint({**base, "merge_base": "3" * 40,
                                   "shallow": None})
        self.assertNotEqual(complete, unknown)

    def test_review_cache_identity_includes_the_graph_revision(self):
        rec = tgt.pin(self.ws, base=self.base)
        first = tgt.review_cache_identity(
            rec, {"meta": {"content_fingerprint": "graph-a"}})
        second = tgt.review_cache_identity(
            rec, {"meta": {"content_fingerprint": "graph-b"}})
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["head"], rec["head"])
        self.assertEqual(first["base"], rec["base"])
        self.assertEqual(first["merge_base"], rec["merge_base"])
        self.assertIn("shallow", first)


class TestTheCompletionGate(_Repo):
    """The conversion: recording the target is not requested, it is the
    price of declaring the work finished."""

    def _contract(self, *extra):
        _run("new", "--read-only", "--write-allow", ".em-review/**",
             "--tools", "Read,Grep,Glob,Write,Edit",
             "--workspace", self.ws, *extra, "review: probe")

    def _screen(self, command):
        event = attach_native_counter(
            {"tool_name": "Bash", "tool_input": {"command": command},
             "cwd": self.ws}, self.ws, label="review-target-screen")
        ev = json.dumps(event)
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(ev)
        try:
            with contextlib.redirect_stdout(out):
                cli.main(["screen"])
        finally:
            sys.stdin = old
        text = out.getvalue().strip()
        if not text:
            return "abstain", ""
        d = json.loads(text)
        return d.get("decision", "allow"), d.get("reason", "")

    def test_readonly_review_uses_native_tools_not_shell(self):
        """Target binding stays narrow; H1 independently denies all shell."""
        self._contract()
        for cmd in ("grep -rn foo .", "cat a.txt", "git diff HEAD~1",
                    "tp findings", "tp graph scan", "tp target pin",
                    "tp lens dispatch", "echo hi > .em-review/x"):
            with self.subTest(cmd):
                decision, why = self._screen(cmd)
                self.assertEqual(decision, "block", cmd)
                self.assertIn("every shell command tool is blocked", why)
        contract = tp.load_active(self.ws)
        for tool, tool_input in (
                ("Read", {"file_path": os.path.join(self.ws, "a.txt")}),
                ("Grep", {"pattern": "x", "path": self.ws}),
                ("Glob", {"pattern": "*.txt", "path": self.ws})):
            with self.subTest(tool=tool):
                allowed, why = tp.screen_tool(
                    contract, tool, tool_input, self.ws)
                self.assertTrue(allowed, why)

    def test_pinning_the_target_unblocks_the_gate(self):
        self._contract()
        self.assertIsNotNone(tgt.binding_problem(self.ws))
        self.assertEqual(self._screen("tp dod")[0], "block")
        _run("target", "--workspace", self.ws, "pin", "--base", self.base)
        self.assertIsNone(tgt.binding_problem(self.ws))
        decision, why = self._screen("tp dod")
        self.assertEqual(decision, "block")
        self.assertIn("every shell command tool is blocked", why)

    def test_a_build_contract_is_not_subject_to_this(self):
        """A build contract already carries its snapshot; the hole was
        specific to read-only reviews."""
        _run("new", "--workspace", self.ws, "--scope", "**", "build: probe")
        self.assertNotEqual(self._screen("tp dod")[0], "block")

    def test_tp_new_target_binds_at_activation(self):
        _run("new", "--read-only", "--write-allow", ".em-review/**",
             "--workspace", self.ws, "--target", "aws/karpenter#9464",
             "--base", self.base, "review: probe")
        rec = tgt.load(self.ws)
        self.assertTrue(rec and rec["ok"])
        self.assertEqual(rec["target"]["number"], 9464)
        contract = tp.load_active(self.ws)
        self.assertEqual(contract["target"]["fingerprint"], rec["fingerprint"])
        self.assertIsNone(tgt.binding_problem(self.ws))
        decision, why = self._screen("tp dod")
        self.assertEqual(decision, "block")
        self.assertIn("every shell command tool is blocked", why)


class TestFindingsMustCiteTheTree(_Repo):
    def test_uncited_findings_are_reported_as_unbound(self):
        tgt.save(self.ws, tgt.pin(self.ws, base=self.base))
        self._findings()
        rc, out, err = _run("findings", "--workspace", self.ws)
        self.assertIn("UNBOUND", err)
        self.assertIn("do not cite", err)

    def test_findings_from_a_different_checkout_are_named_as_such(self):
        tgt.save(self.ws, tgt.pin(self.ws, base=self.base))
        self._findings(fingerprint="deadbeefdeadbeef")
        rc, out, err = _run("findings", "--workspace", self.ws)
        self.assertIn("different checkout", err)

    def test_correctly_cited_findings_are_bound(self):
        rec = tgt.save(self.ws, tgt.pin(self.ws, base=self.base))
        self._findings(fingerprint=rec["fingerprint"])
        rc, out, err = _run("findings", "--workspace", self.ws)
        self.assertNotIn("UNBOUND", err)

    def test_the_findings_are_still_shown_when_unbound(self):
        """Reported, never withheld. A human reading a review is better
        served by 'here are the findings AND they cite no tree' than by a
        refusal; it is the SIGN-OFF that is gated."""
        tgt.save(self.ws, tgt.pin(self.ws, base=self.base))
        self._findings()
        rc, out, err = _run("findings", "--workspace", self.ws)
        self.assertEqual(rc, 0)
        self.assertIn("HEADLINE:", out)

    def test_a_string_citation_is_accepted_too(self):
        rec = tgt.save(self.ws, tgt.pin(self.ws, base=self.base))
        self.assertEqual(
            tgt.cited_fingerprint({"meta": {"target": rec["fingerprint"]}}),
            rec["fingerprint"])
        self.assertIsNone(tgt.cited_fingerprint({"meta": {}}))
        self.assertIsNone(tgt.cited_fingerprint({}))


class TestAcquisition(_Repo):
    def test_a_non_pr_target_is_refused_by_acquire(self):
        rec = tgt.acquire(self.ws, "release/2.x")
        self.assertFalse(rec["ok"])
        self.assertIn("not a pull-request target", rec["reason"])

    def test_acquiring_into_a_non_repo_says_to_clone_first(self):
        empty = os.path.join(self.d, "empty2")
        os.makedirs(empty)
        rec = tgt.acquire(empty, "o/r#1")
        self.assertFalse(rec["ok"])
        self.assertIn("clone the repository first", rec["reason"])

    def test_a_failed_fetch_records_the_exact_commands_it_ran(self):
        """'How did the code get here' must have an answer that is not a
        reviewer's recollection — including when it did not get here."""
        rec = tgt.acquire(self.ws, "o/r#1", base=self.base)
        self.assertFalse(rec["ok"])
        self.assertTrue(rec["steps"])
        self.assertIn("git fetch origin pull/1/head", rec["steps"][0]["cmd"])

    def test_a_real_pull_ref_is_fetched_and_pinned(self):
        """End to end against a local 'remote' carrying a pull/N/head ref —
        the same shape GitHub serves."""
        upstream = os.path.join(self.d, "upstream")
        shutil.copytree(self.ws, upstream)
        subprocess.run(["git", "checkout", "-q", "-b", "feature"],
                       cwd=upstream, capture_output=True)
        with open(os.path.join(upstream, "c.txt"), "w") as f:
            f.write("pr\n")
        subprocess.run(["git", "add", "-A"], cwd=upstream,
                       capture_output=True)
        subprocess.run(["git", "commit", "-qm", "pr work"], cwd=upstream,
                       capture_output=True)
        subprocess.run(["git", "update-ref", "refs/pull/42/head", "feature"],
                       cwd=upstream, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", upstream],
                       cwd=self.ws, capture_output=True)
        rec = tgt.acquire(self.ws, "o/r#42", base=self.base)
        self.assertTrue(rec.get("ok"), rec.get("reason"))
        self.assertEqual(rec["branch"], "tp-pr-42")
        self.assertIn("c.txt", rec["changed_files"])
        self.assertEqual(rec["target"]["number"], 42)

    def test_fetch_deepens_a_shallow_base_once_before_preflight(self):
        calls = []
        records = [
            {"ok": True, "head": "h" * 40, "base": "b" * 40,
             "base_ref": "origin/main", "merge_base": None,
             "shallow": True, "changed_files": [], "fingerprint": "old"},
            {"ok": True, "head": "h" * 40, "base": "b" * 40,
             "base_ref": "origin/main", "merge_base": "m" * 40,
             "shallow": True, "changed_files": ["a.txt"],
             "fingerprint": "new"},
        ]
        real_git, real_pin = tgt.git, tgt.pin

        def fake_git(_root, *args, **_kwargs):
            calls.append(args)
            if args[:2] == ("rev-parse", "--git-dir"):
                return 0, ".git"
            if args[0] in {"fetch", "checkout"}:
                return 0, ""
            return 1, ""

        def fake_pin(*_args, **_kwargs):
            return records.pop(0)

        tgt.git, tgt.pin = fake_git, fake_pin
        self.addCleanup(setattr, tgt, "git", real_git)
        self.addCleanup(setattr, tgt, "pin", real_pin)
        rec = tgt.acquire(self.ws, "o/r#42", base="origin/main")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["merge_base"], "m" * 40)
        self.assertIn(("fetch", "--deepen=256", "origin", "main"), calls)


class TestToolReadiness(unittest.TestCase):
    """git and gh are dependencies, not conveniences."""

    def test_it_reports_both_tools(self):
        t = tgt.tools()
        self.assertIn("git", t)
        self.assertIn("gh", t)
        for k in ("present", "version", "path"):
            self.assertIn(k, t["gh"])

    def test_gh_carries_an_authentication_state(self):
        self.assertIn("authenticated", tgt.tools()["gh"])

    def test_the_install_hint_is_a_real_command_for_this_host(self):
        hint = tgt.install_hint()
        self.assertTrue(hint)
        self.assertTrue(hint.startswith(("brew", "sudo", "winget", "see ")))

    def test_ensure_gh_is_a_no_op_when_gh_is_present(self):
        if not shutil.which("gh"):
            self.skipTest("gh not installed here")
        res = tgt.ensure_gh()
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "already-present")

    def test_ensure_gh_never_runs_anything_when_asked_not_to(self):
        calls = []
        orig_which, orig_run = tgt.shutil.which, tgt.subprocess.run
        tgt.shutil.which = lambda p: None

        def boom(*a, **k):
            calls.append(a)
            raise AssertionError("must not shell out with run=False")

        tgt.subprocess.run = boom
        try:
            res = tgt.ensure_gh(run=False)
        finally:
            tgt.shutil.which, tgt.subprocess.run = orig_which, orig_run
        self.assertEqual(calls, [])
        self.assertEqual(res["action"], "manual")
        self.assertFalse(res["ok"])

    def test_it_never_downloads_and_executes_a_binary(self):
        """Deliberate: taskplane delegates to the package manager already
        trusted on this host rather than fetching a release tarball it
        cannot verify offline. A hardcoded checksum nobody maintains is a
        worse guarantee than the user's own package source."""
        src = open(os.path.join(ROOT, "taskplane", "target.py"),
                   encoding="utf-8").read()
        for bad in ("curl", "wget", "urlopen", "urlretrieve", "requests.",
                    "tarfile", "zipfile"):
            self.assertNotIn(bad, src, f"target.py reaches for {bad}")

    def test_onboarding_reports_the_tools(self):
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
            rc, out, _ = _run("onboard", "--workspace", d, "--json")
            report = json.loads(out)
            self.assertIn("tools", report)
            self.assertIn("install_hint", report["tools"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_cli_fails_when_gh_is_missing(self):
        orig = tgt.shutil.which
        tgt.shutil.which = lambda p: None if p == "gh" else orig(p)
        try:
            rc, out, err = _run("target", "tools")
        finally:
            tgt.shutil.which = orig
        self.assertEqual(rc, 1)
        self.assertIn("REQUIRED", err)
        self.assertIn("not in the git objects", err)


if __name__ == "__main__":
    unittest.main()
