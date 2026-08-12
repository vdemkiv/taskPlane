"""The exit path — the second karpenter field report (v2.10.0 in the field).

The review itself completed and produced a defensible verdict. Every
mechanism that exists to stop an agent cutting corners held: read-only
enforcement across six parallel agents, per-agent contract slots, the
once-only runnability probe, the classification gate, and the
render-observation ledger, which caught a fabricated graph the agent itself
had not noticed. Everything that broke was in the CLOSING sequence:

  B-1  `session-verify` demanded `tp ack <id>`; the action budget refused
       `tp ack <id>`. The hook fired ~12 times with no reachable state that
       satisfied it. The budget had been spent on taskplane's OWN mandated
       orchestration, so the review reached a verdict with nothing left to
       record it — and `.em-review/` is git-ignored scratch in an ephemeral
       sandbox, so the blocked step was the one whose entire purpose is
       surviving the session.
  B-2  Governance is keyed on cwd. The shell's working directory reverted
       mid-run and three `tp ack <id>` calls returned "acknowledged" against
       an empty directory with no contract, while the real obligations
       stayed open; `graph html` there emitted 5,684 bytes of valid-looking
       dependency graph for a workspace that had never been scanned.
  B-3  A flat 30-action ceiling truncated a verification agent mid-research.
  I-3  `tp init` appended to the reviewed repo's `.gitignore`, dirtying the
       tree on the exact commit under review and adding a 5th file to
       `git diff <base>` for a 4-file PR.
  I-4  `graph impact` still could not see intra-repo Go — v2.10.0 claimed it
       could.
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
import depgraph as dg              # noqa: E402
import obligations                 # noqa: E402
import taskplane_lite as tp        # noqa: E402
import tp as cli                   # noqa: E402


def _run(ws, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(args))
        except SystemExit as e:
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


class _WS(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ws = os.path.join(self.d, "repo")
        os.makedirs(self.ws)
        for a in (["init", "-q"], ["config", "user.email", "e@e"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.ws, capture_output=True)
        with open(os.path.join(self.ws, "a.txt"), "w") as f:
            f.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=self.ws, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.ws,
                       capture_output=True)
        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "store")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.d, ignore_errors=True)

    def contract(self, max_actions=40):
        _run(self.ws, "new", "--read-only", "--write-allow", ".em-review/**",
             "--owes", "review", "--max-actions", str(max_actions),
             "--workspace", self.ws, "review: probe")
        return tp.load_active(self.ws)

    def spend(self, n):
        c = tp.load_active(self.ws)
        tid = c.get("task_id", "_")
        os.makedirs(tp.tp_dir(self.ws), exist_ok=True)
        with open(os.path.join(tp.tp_dir(self.ws), "meter.json"), "w") as f:
            json.dump({tid: {"actions": n}}, f)

    def screen(self, command):
        ev = json.dumps({"tool_name": "Bash",
                         "tool_input": {"command": command}, "cwd": self.ws})
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


class B1_TheClosingSequenceCanAlwaysRun(_WS):

    def test_ack_is_never_metered(self):
        """The deadlock's proximate cause. The Stop hook's instruction has to
        be one the harness will actually let the agent follow."""
        self.contract()
        for used in (0, 35, 40, 400):
            self.spend(used)
            for cmd in ("tp ack o-1", "tp ack --status"):
                with self.subTest(used=used, cmd=cmd):
                    decision, _ = self.screen(cmd)
                    self.assertEqual(decision, "abstain",
                                     f"{cmd} was metered at {used} actions")

    def test_the_last_actions_are_reserved_for_closing(self):
        self.contract(40)
        self.spend(35)
        work, _ = self.screen("grep -r foo .")
        self.assertEqual(work, "block")
        for cmd in ("tp findings --html", "tp decision 'x'", "tp req debt 'y'"):
            with self.subTest(cmd):
                self.spend(35)
                decision, why = self.screen(cmd)
                self.assertNotEqual(decision, "block", f"{cmd}: {why}")

    def test_the_reserve_does_not_raise_the_ceiling(self):
        """A carve-out, not an increase — at the ceiling itself nothing
        passes but the pure reads that never cost anything."""
        self.contract(40)
        self.spend(40)
        for cmd in ("tp dod", "tp findings", "tp decision 'x'",
                    "grep -r foo ."):
            with self.subTest(cmd):
                decision, _ = self.screen(cmd)
                self.assertEqual(decision, "block", cmd)

    def test_clear_can_still_not_be_run_from_inside(self):
        """The half of the v2.10.0 decision that stands: clearing leaves the
        workspace ungoverned, so an exhausted agent must not reach it."""
        self.contract(40)
        for used in (35, 40):
            self.spend(used)
            decision, _ = self.screen("tp clear")
            self.assertEqual(decision, "block", f"clear passed at {used}")

    def test_a_small_ceiling_does_not_reserve_itself_to_death(self):
        for ceiling, expected in ((0, 0), (4, 1), (8, 2), (30, 5), (40, 5)):
            with self.subTest(ceiling):
                self.assertEqual(tp.closing_reserve(ceiling, 5), expected)

    def test_a_zero_action_ceiling_stays_maximally_strict(self):
        ok, _ = tp.budget_status({"budget": {"max_actions": 0}}, 0,
                                 reserve=5, closing=True)
        self.assertFalse(ok)

    def test_the_stop_hook_names_the_budget_when_that_is_the_blocker(self):
        """It used to repeat one unsatisfiable instruction forever. A hook
        that cannot be satisfied is a hang, not enforcement."""
        self.contract(40)
        self.spend(40)
        obligations.issue(self.ws, "render_dashboard", detail="d",
                          step="review", binding=True)
        seen = []
        for _ in range(3):
            old = sys.stdin
            sys.stdin = io.StringIO("{}")
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    rc = cli.main(["session-verify", "--workspace", self.ws])
            finally:
                sys.stdin = old
            seen.append((rc, err.getvalue()))
        self.assertTrue(all(rc == 2 for rc, _ in seen),
                        "the refusal must still stand")
        self.assertIn("unmetered", seen[0][1])
        self.assertIn("NO PROGRESS", seen[-1][1])
        self.assertIn("budget --grant", seen[-1][1])


class B2_GovernanceMustNotSucceedInTheWrongWorkspace(_WS):

    def test_ack_refuses_an_id_this_workspace_never_issued(self):
        empty = os.path.join(self.d, "elsewhere")
        os.makedirs(empty)
        rc, out, err = _run(empty, "ack", "o-deadbeef01", "--workspace", empty)
        self.assertEqual(rc, 1)
        self.assertNotIn("acknowledged", out)
        self.assertIn("no obligation", err)

    def test_the_refusal_says_the_ledger_is_empty(self):
        empty = os.path.join(self.d, "elsewhere2")
        os.makedirs(empty)
        _, _, err = _run(empty, "ack", "o-x", "--workspace", empty)
        self.assertIn("no obligation ledger at all", err)

    def test_a_real_obligation_still_acks(self):
        self.contract()
        oid = obligations.issue(self.ws, "render_dashboard", detail="d",
                                step="review")
        rc, out, _ = _run(self.ws, "ack", oid, "--workspace", self.ws)
        self.assertEqual(rc, 0)
        self.assertIn("acknowledged", out)

    def test_acking_the_wrong_id_lists_what_this_workspace_does_owe(self):
        self.contract()
        oid = obligations.issue(self.ws, "render_graph", detail="g",
                                step="graph")
        _, _, err = _run(self.ws, "ack", "o-nope", "--workspace", self.ws)
        self.assertIn(oid, err)

    def test_graph_html_refuses_an_unscanned_workspace(self):
        """5,684 bytes of dependency graph for a directory that is not a
        repo, rendered to a human as the review's blast radius."""
        empty = os.path.join(self.d, "nograph")
        os.makedirs(empty)
        rc, out, err = _run(empty, "graph", "--workspace", empty,
                            "html", "--fragment")
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "")
        self.assertIn("nothing has been scanned", err)

    def test_graph_html_still_renders_a_real_graph(self):
        with open(os.path.join(self.ws, "m.py"), "w") as f:
            f.write("import os\n")
        dg.scan(self.ws)
        rc, out, err = _run(self.ws, "graph", "--workspace", self.ws,
                            "html", "--fragment")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip())


class I3_InitMustNotDirtyAReviewedRepo(_WS):

    def test_init_leaves_the_working_tree_clean(self):
        _run(self.ws, "init", "--workspace", self.ws)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=self.ws,
                               capture_output=True, text=True,
                               encoding="utf-8").stdout.strip()
        self.assertEqual(dirty, "", f"tp init dirtied the tree: {dirty}")

    def test_it_ignores_via_git_info_exclude(self):
        _run(self.ws, "init", "--workspace", self.ws)
        with open(os.path.join(self.ws, ".git", "info", "exclude"),
                  encoding="utf-8") as f:
            body = f.read()
        for entry in (".taskplane/", ".em-review/", ".eval/"):
            self.assertIn(entry, body)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".gitignore")))

    def test_the_runtime_paths_are_actually_ignored(self):
        _run(self.ws, "init", "--workspace", self.ws)
        os.makedirs(os.path.join(self.ws, ".em-review"), exist_ok=True)
        with open(os.path.join(self.ws, ".em-review", "f.json"), "w") as f:
            f.write("{}")
        untracked = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.ws,
            capture_output=True, text=True, encoding="utf-8").stdout
        self.assertNotIn(".em-review", untracked)

    def test_it_falls_back_to_gitignore_with_no_git_dir(self):
        plain = os.path.join(self.d, "plain")
        os.makedirs(plain)
        added = cli._ensure_excluded(plain, [".taskplane/"], "h")
        self.assertEqual(added, [".taskplane/"])
        self.assertTrue(os.path.exists(os.path.join(plain, ".gitignore")))


class B3_ActionCeilingsScaleWithTheWork(unittest.TestCase):

    def test_a_deep_lens_gets_more_room_than_the_sweep(self):
        import lens
        self.assertGreater(lens.DEEP_ACTIONS, lens.SWEEP_ACTIONS)
        self.assertEqual(lens.actions_for("deep"), lens.DEEP_ACTIONS)
        self.assertEqual(lens.actions_for("sweep"), lens.SWEEP_ACTIONS)

    def test_an_explicit_ceiling_still_wins_everywhere(self):
        import lens
        self.assertEqual(lens.actions_for("deep", 12), 12)
        self.assertEqual(lens.actions_for("sweep", 12), 12)

    def test_the_briefs_carry_the_tiered_ceilings(self):
        import lens
        routing = {"context": {"changed_files": 2},
                   "lenses": [{"id": "security", "name": "S", "tier": "deep",
                               "mode": "subagent", "reasons": ["r"],
                               "checks": ["c"]},
                              {"id": "perf", "name": "P", "tier": "sweep",
                               "mode": "inline", "reasons": ["r"],
                               "checks": ["c"]}]}
        out = lens.dispatch_briefs(routing)
        self.assertEqual(out["deep"][0]["contract"]["max_actions"],
                         lens.DEEP_ACTIONS)
        self.assertEqual(out["sweep"]["contract"]["max_actions"],
                         lens.SWEEP_ACTIONS)

    def test_a_bigger_ceiling_does_not_widen_what_a_lens_may_write(self):
        import lens
        routing = {"context": {"changed_files": 2},
                   "lenses": [{"id": "security", "name": "S", "tier": "deep",
                               "mode": "subagent", "reasons": ["r"],
                               "checks": ["c"]}]}
        c = lens.dispatch_briefs(routing)["deep"][0]["contract"]
        self.assertTrue(c["read_only"])
        self.assertEqual(c["write_allow"], [".em-review/lens-security/**"])


class I4_IntraRepoGoIsActuallyResolved(unittest.TestCase):
    """v2.10.0 SHIPPED the claim that this worked. It did not: the prefix
    stripping went into the JavaScript resolver, the Go branch was never
    touched, and `_strip_root_module` could not have worked from the set the
    scanner holds even if it had been called."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "pkg", "providers", "amifamily",
                                 "bootstrap"))
        os.makedirs(os.path.join(self.d, "pkg", "providers", "launchtemplate"))
        with open(os.path.join(self.d, "go.mod"), "w") as f:
            f.write("module github.com/aws/karpenter-provider-aws\n\ngo 1.24\n")
        with open(os.path.join(self.d, "pkg", "providers", "amifamily",
                               "bootstrap", "bottlerocket.go"), "w") as f:
            f.write("package bootstrap\n\ntype B struct{}\n")
        with open(os.path.join(self.d, "pkg", "providers", "launchtemplate",
                               "lt.go"), "w") as f:
            f.write('package launchtemplate\n\nimport "github.com/aws/'
                    'karpenter-provider-aws/pkg/providers/amifamily/'
                    'bootstrap"\n\nvar _ = bootstrap.B{}\n')
        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "_store")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.d, ignore_errors=True)

    def test_an_intra_repo_import_becomes_an_EDGE(self):
        g = dg.scan(self.d)
        edges = {(e["from"], e["to"]) for e in (g.get("edges") or [])}
        self.assertIn(("providers/launchtemplate", "providers/amifamily"),
                      edges, f"no intra-repo Go edge; got {sorted(edges)}")

    def test_impact_reports_the_importer(self):
        dg.scan(self.d)
        imp = dg.impact(
            self.d, ["pkg/providers/amifamily/bootstrap/bottlerocket.go"])
        modules = {row["module"]
                   for rows in imp["impacted"].values() for row in rows}
        self.assertIn("providers/launchtemplate", modules)
        self.assertGreaterEqual(imp["total_impacted"], 1)

    def test_nothing_lands_as_an_external_dependency(self):
        g = dg.scan(self.d)
        ext = [e["to"] for e in (g.get("edges") or [])
               if str(e["to"]).startswith("ext:")]
        self.assertEqual(ext, [], f"intra-repo imports still external: {ext}")

    def test_the_repo_is_not_collapsed_into_one_module(self):
        """The root module path is a PREFIX. If it ever becomes a module id,
        every file in the repo buckets into one node and impact says
        'everything' — which is the same as saying nothing."""
        g = dg.scan(self.d)
        self.assertNotIn("github.com/aws/karpenter-provider-aws",
                         set(g.get("modules") or {}))
        self.assertEqual(sorted(g.get("modules") or {}),
                         ["providers/amifamily", "providers/launchtemplate"])

    def test_coverage_is_reported_as_declared_not_external_only(self):
        g = dg.scan(self.d)
        go = ((g.get("meta") or {}).get("scanners") or {}).get("go") or {}
        self.assertEqual(go.get("coverage"), "declared-modules")

    def test_the_root_module_cannot_be_recovered_from_a_set(self):
        """The precise v2.10.0 defect, pinned so the API cannot regress to
        it: the scanner holds `set(manifests.values())`, and a set has no key
        to find the root path by. Anything that needs the root takes it
        explicitly."""
        self.assertIsNone(dg.root_module({"a"}))
        self.assertIsNone(dg.root_module(None))
        self.assertEqual(
            dg.root_module({dg.ROOT_MODULE_KEY: "example.com/m"}),
            "example.com/m")
        self.assertEqual(dg.strip_root_prefix("example.com/m/pkg/x",
                                              "example.com/m"), "pkg/x")
        self.assertIsNone(dg.strip_root_prefix("example.com/m", "example.com/m"))
        self.assertIsNone(dg.strip_root_prefix("example.com/mx/pkg",
                                               "example.com/m"))


class I1_TheContractCanCreateItsOwnDirectory(unittest.TestCase):
    RO = {"read_only": True, "write_allow": [".em-review/**"],
          "task_id": "t", "scope": ["**"]}

    def test_mkdir_of_the_allowed_root_is_allowed(self):
        ok, why = tp.screen_tool(self.RO, "Bash",
                                 {"command": "mkdir -p .em-review"}, None)
        self.assertTrue(ok, why)

    def test_the_widening_is_exactly_one_path(self):
        for cmd in ("mkdir -p .em-review-scratch", "mkdir -p .em-reviewX",
                    "mkdir -p .", "touch ../escape", "mkdir -p src"):
            with self.subTest(cmd):
                ok, _ = tp.screen_tool(self.RO, "Bash", {"command": cmd}, None)
                self.assertFalse(ok, f"LOOSENED: {cmd}")

    def test_writable_is_stem_equality_never_prefix(self):
        self.assertTrue(tp.writable(".em-review", [".em-review/**"]))
        self.assertTrue(tp.writable(".em-review/x", [".em-review/**"]))
        self.assertFalse(tp.writable(".em-review-x", [".em-review/**"]))
        self.assertFalse(tp.writable("", [".em-review/**"]))
        self.assertFalse(tp.writable(".em-review", []))


if __name__ == "__main__":
    unittest.main()
