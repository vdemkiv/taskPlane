"""Regression tests for the v0.8.6 self-review fixes — each asserts the
finding's failure mode is gone. Reproduce-then-pass."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tl  # noqa: E402
import loop  # noqa: E402
import depgraph  # noqa: E402
import dashboard  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True, check=False)


def _repo(prefix="tp-fix-"):
    ws = tempfile.mkdtemp(prefix=prefix)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    open(os.path.join(ws, "a.py"), "w").write("x = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    return ws


class TestKernel(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()
        os.makedirs(os.path.join(self.ws, "server"))
        open(os.path.join(self.ws, "server", "ok.py"), "w").close()

    def test_rm_screened_as_write_readonly(self):
        c = {"read_only": True, "write_allow": [".em-review/**"], "coding": {}}
        allow, _ = tl.screen_tool(c, "Bash", {"command": "rm -rf server/"}, self.ws)
        self.assertFalse(allow)

    def test_chmod_screened_out_of_scope(self):
        c = {"coding": {"scope_paths": ["server/**"]}}
        allow, _ = tl.screen_tool(c, "Bash", {"command": "chmod 777 ../x"}, self.ws)
        self.assertFalse(allow)

    def test_rm_in_scope_allowed(self):
        c = {"coding": {"scope_paths": ["server/**"]}}
        allow, _ = tl.screen_tool(c, "Bash", {"command": "rm server/ok.py"}, self.ws)
        self.assertTrue(allow)

    def test_symlink_escape_blocked(self):
        os.symlink("/etc", os.path.join(self.ws, "server", "link"))
        c = {"coding": {"scope_paths": ["server/**"]}}
        allow, _ = tl.screen_tool(
            c, "Write", {"file_path": "server/link/passwd", "content": "x"}, self.ws)
        self.assertFalse(allow)

    def test_normal_write_still_allowed(self):
        c = {"coding": {"scope_paths": ["server/**"]}}
        allow, _ = tl.screen_tool(
            c, "Write", {"file_path": "server/new.py", "content": "x"}, self.ws)
        self.assertTrue(allow)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()

    def _ab_to_selection(self, ids=("va", "vb")):
        loop.init(self.ws, "g", parallel=True)
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"mode": "ab-selection", "tasks": [
            {"id": ids[0], "variant": "A", "scope": ["src/**"], "tests": "t"},
            {"id": ids[1], "variant": "B", "scope": ["src/**"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass"); loop.approve(self.ws)
        s = loop.load(self.ws)
        for t in s["tasks"]:
            t["status"] = "passed"
        s["step"] = "selection"; loop.save(self.ws, s)

    def test_nested_ab_after_hybrid_pauses_at_selection(self):
        self._ab_to_selection()
        loop.select(self.ws, "hybrid")
        json.dump({"mode": "ab-selection", "tasks": [
            {"id": "ga", "variant": "A", "scope": ["src/**"], "tests": "t"},
            {"id": "gb", "variant": "B", "scope": ["src/**"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        loop.gate(self.ws, "pass"); loop.approve(self.ws)
        s = loop.load(self.ws)
        for t in s["tasks"]:
            t["status"] = "passed"
        s["step"] = "evaluate"; s["current_task"] = len(s["tasks"]) - 1
        loop.save(self.ws, s)
        r = loop.gate(self.ws, "pass")
        self.assertEqual(r["step"], "selection")

    def test_skip_cascades_to_dependents_no_deadlock(self):
        loop.init(self.ws, "g", parallel=True)
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"tasks": [
            {"id": "t1", "scope": ["a/**"], "tests": "t"},
            {"id": "t2", "scope": ["b/**"], "deps": ["t1"], "tests": "t"},
            {"id": "t3", "scope": ["c/**"], "deps": ["t2"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass"); loop.approve(self.ws)
        s = loop.load(self.ws); s["step"] = "escalated"; s["current_task"] = 0
        loop.save(self.ws, s)
        loop.resolve(self.ws, "skip")
        s = loop.load(self.ws)
        self.assertTrue(all(t["status"] == "skipped" for t in s["tasks"]))
        self.assertEqual(s["step"], "em")

    def test_wave_deadlock_surfaced(self):
        loop.init(self.ws, "g", parallel=True)
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"tasks": [
            {"id": "t1", "scope": ["a/**"], "tests": "t"},
            {"id": "t2", "scope": ["b/**"], "deps": ["t1"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass"); loop.approve(self.ws)
        s = loop.load(self.ws)
        # t1 skipped directly (not via cascade), t2 left pending → deadlock
        for t in s["tasks"]:
            if t["id"] == "t1":
                t["status"] = "skipped"
        loop.save(self.ws, s)
        w = loop.wave(self.ws)
        self.assertIn("deadlock", w)

    def test_scopes_overlap_segment_aware(self):
        self.assertFalse(loop._scopes_overlap(["src/a/**"], ["src/ab/**"]))
        self.assertFalse(loop._scopes_overlap(["**/x.py"], ["foo/**"]))
        self.assertTrue(loop._scopes_overlap(["src/a/**"], ["src/**"]))

    def test_ab_without_parallel_forced(self):
        loop.init(self.ws, "g")  # no parallel
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"mode": "ab-selection", "tasks": [
            {"id": "va", "variant": "A", "scope": ["src/**"], "tests": "t"},
            {"id": "vb", "variant": "B", "scope": ["src/**"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass")
        self.assertTrue(loop.load(self.ws)["parallel"])

    def test_shared_requirement_keeps_all_edges(self):
        os.makedirs(os.path.join(self.ws, "src", "auth"))
        os.makedirs(os.path.join(self.ws, "src", "pay"))
        open(os.path.join(self.ws, "src", "auth", "a.py"), "w").write("x=1\n")
        open(os.path.join(self.ws, "src", "pay", "p.py"), "w").write("y=1\n")
        depgraph.scan(self.ws)
        loop.init(self.ws, "g", parallel=True)
        s = loop.load(self.ws); s["step"] = "plan"; loop.save(self.ws, s)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"tasks": [
            {"id": "t1", "req": "R-0001", "scope": ["src/auth/**"], "tests": "t"},
            {"id": "t2", "req": "R-0001", "scope": ["src/pay/**"], "tests": "t"}]},
            open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass")
        g = depgraph.load(self.ws)
        planned = {e["to"] for e in g["edges"]
                   if e["from"] == "req:R-0001" and e["kind"] == "planned"}
        self.assertIn("auth", planned)
        self.assertIn("pay", planned)


class TestKernelFailClosed(unittest.TestCase):
    def test_corrupt_contract_blocks(self):
        ws = _repo()
        os.makedirs(os.path.join(ws, ".taskplane"))
        open(os.path.join(ws, ".taskplane", "active_contract.json"), "w").write("{bad")
        r = subprocess.run(
            [sys.executable, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "tp.py"), "screen"],
            input=json.dumps({"cwd": ws, "tool_name": "Write",
                              "tool_input": {"file_path": "x"}}),
            capture_output=True, text=True)
        self.assertIn('"decision": "block"', r.stdout)

    def test_no_contract_abstains(self):
        # Ungoverned workspace: the screener ABSTAINS (emits no decision) so
        # Claude Code's normal permission flow applies — it must NOT force
        # "approve", which would auto-approve every tool in any ungoverned
        # repo where taskplane is installed, bypassing the user's own
        # permission prompts.
        ws = _repo()
        r = subprocess.run(
            [sys.executable, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "tp.py"), "screen"],
            input=json.dumps({"cwd": ws, "tool_name": "Write"}),
            capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")          # no forced decision
        self.assertNotIn('"decision"', r.stdout)


class TestSurface(unittest.TestCase):
    def test_render_escapes_goal_and_deny_reason(self):
        ws = _repo()
        loop.init(ws, "add <script>alert(1)</script>", parallel=False)
        import taskplane_lite as tp
        tp.trace(ws, "hook_deny", tool="Bash",
                 reason="<img src=x onerror=alert(1)>")
        out = dashboard.render(ws, out=os.path.join(ws, "d.html"))
        html = open(out).read()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()


class TestFindingsDashboard(unittest.TestCase):
    def test_render_all_severities_and_filter_wiring(self):
        findings = [
            {"severity": "high", "domain": "kernel", "file": "a.py", "line": 1,
             "title": "H", "scenario": "boom", "fix": "patch", "status": "fixed"},
            {"severity": "med", "title": "M", "scenario": "meh"},
            {"severity": "low", "title": "L"},
        ]
        html = dashboard.render_findings(
            findings, {"title": "t", "tests": "3/3", "clean": ["ok"],
                       "gate": True,
                       "gate_buttons": [{"label": "accept", "prompt": "go",
                                         "primary": True}]})
        # all three severities present as cards
        self.assertEqual(html.count('class="tpf-card"'), 3)
        # filter + toggle JS wired
        self.assertIn("tpFilter", html)
        self.assertIn("tpToggle", html)
        # counts in chips
        self.assertIn(">1</span> high", html)
        self.assertIn(">1</span> medium", html)
        self.assertIn(">1</span> low", html)
        # clean section + gate button
        self.assertIn("CLEAN", html)
        self.assertIn("accept", html)

    def test_render_findings_escapes_and_sorts(self):
        html = dashboard.render_findings(
            [{"severity": "low", "title": "<script>x</script>"},
             {"severity": "high", "title": "first"}])
        # high sorts before low
        self.assertLess(html.index("first"), html.index("&lt;script&gt;"))
        self.assertNotIn("<script>x</script>", html)


class TestOnboarding(unittest.TestCase):
    def _report(self, ws):
        import tp as tpcli
        return tpcli._onboard_report(ws)

    def test_bare_dir_prompts_attach_folder(self):
        ws = tempfile.mkdtemp(prefix="tp-ob-bare-")
        r = self._report(ws)
        # empty dir → not a project yet
        self.assertFalse(r["looks_like_project"])
        self.assertEqual(r["next_action"], "attach_folder")
        self.assertFalse(r["ready"])

    def test_files_no_git_prompts_init_git(self):
        ws = tempfile.mkdtemp(prefix="tp-ob-files-")
        open(os.path.join(ws, "app.py"), "w").write("x=1\n")
        r = self._report(ws)
        self.assertTrue(r["looks_like_project"])
        self.assertEqual(r["next_action"], "init_git")

    def test_git_no_context_prompts_tp_init(self):
        ws = _repo(prefix="tp-ob-git-")
        r = self._report(ws)
        self.assertTrue(r["is_git"])
        self.assertTrue(r["has_commit"])
        self.assertEqual(r["next_action"], "tp_init")

    def test_ready_when_all_present(self):
        ws = _repo(prefix="tp-ob-ready-")
        os.makedirs(os.path.join(ws, "knowledge", "context"))
        r = self._report(ws)
        self.assertTrue(r["ready"])
        self.assertEqual(r["next_action"], "ready")

    def test_render_onboarding_has_buttons_and_escapes(self):
        r = {"workspace": "/x", "looks_like_project": False, "is_git": False,
             "has_commit": False, "has_context": False, "ready": False,
             "next_action": "attach_folder",
             "checks": [{"id": "workspace", "label": "A folder <b>",
                         "ok": False, "detail": "d", "hint": "h"}]}
        html = dashboard.render_onboarding(r)
        self.assertIn("sendPrompt", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertNotIn("A folder <b>", html)


class TestLensAgents(unittest.TestCase):
    def test_catalog_list_and_show(self):
        import lens
        cat = lens.catalog_summary()
        self.assertGreaterEqual(len(cat), 20)
        self.assertTrue(all("id" in l and "looks_for" in l for l in cat))
        b = lens.lens_brief("security")
        self.assertEqual(b["id"], "security")
        self.assertIn("checks", b)
        self.assertIsNone(lens.lens_brief("nope-not-a-lens"))

    def test_dispatch_briefs_are_readonly_and_per_lens(self):
        import lens
        routing = lens.route(["server/api/users.py"], catalog=None)
        d = lens.dispatch_briefs(routing, base="main", max_actions=25)
        self.assertTrue(d["deep"])
        for b in d["deep"]:
            self.assertEqual(b["agent"], "tp-lens")
            self.assertTrue(b["contract"]["read_only"])
            self.assertEqual(b["contract"]["write_allow"],
                             [f".em-review/lens-{b['id']}/**"])
            self.assertEqual(b["contract"]["max_actions"], 25)
            self.assertIn(b["id"], b["prompt"].lower() or b["prompt"])
            self.assertTrue(b["output"].startswith(".em-review/lens-"))

    def test_dispatch_sweep_batched_when_breadth_all(self):
        import lens
        routing = lens.route(["server/api/users.py"], catalog=None,
                             breadth="all")
        d = lens.dispatch_briefs(routing)
        # full catalog → some lenses are sweep tier, batched into one agent
        self.assertIsNotNone(d["sweep"])
        self.assertTrue(d["sweep"]["ids"])
        self.assertTrue(d["sweep"]["contract"]["read_only"])


class TestLensWaveProgress(unittest.TestCase):
    def test_wave_board_shows_statuses_and_progress(self):
        html = dashboard.render_lens_wave([
            {"id": "security", "name": "Security", "status": "done", "findings": 2},
            {"id": "a11y", "name": "Accessibility", "status": "done", "findings": 0},
            {"id": "perf", "name": "Performance", "status": "running", "findings": None},
            {"id": "dba", "name": "DBA", "status": "queued", "findings": None},
        ])
        self.assertIn("2 findings", html)   # done + count
        self.assertIn("clean", html)        # done + zero
        self.assertIn("running", html)
        self.assertIn("queued", html)
        # phase string: 1 running · 2/4 reported (was a dead `if False` assert)
        self.assertIn("2/4 reported", html)
        self.assertIn("1 running", html)
        # progress: 2 of 4 done → 50%
        self.assertIn("width:50%", html)

    def test_wave_board_blocked_lane(self):
        html = dashboard.render_lens_wave([
            {"id": "security", "name": "Security", "status": "done", "findings": 1},
            {"id": "perf", "name": "Perf", "status": "blocked", "findings": None},
        ])
        self.assertIn("blocked", html)

    def test_wave_board_all_reported_full_bar(self):
        html = dashboard.render_lens_wave([
            {"id": "a", "name": "A", "status": "done", "findings": 0},
            {"id": "b", "name": "B", "status": "done", "findings": 3},
        ])
        self.assertIn("all lenses reported", html)
        self.assertIn("width:100%", html)

    def test_wave_board_escapes(self):
        html = dashboard.render_lens_wave(
            [{"id": "<x>", "name": "n", "status": "queued", "findings": None}])
        self.assertIn("&lt;x&gt;", html)
        self.assertNotIn("<x>", html)


# =====================================================================
# v0.9.1 full-lens-review fixes — one class per finding cluster.
# =====================================================================

_TP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tp.py")


def _screen_once(ws, tool_name, tool_input):
    r = subprocess.run(
        [sys.executable, _TP_PY, "screen"],
        input=json.dumps({"cwd": ws, "tool_name": tool_name,
                          "tool_input": tool_input}),
        capture_output=True, text=True)
    return r.stdout


class TestScreenerBypassClosed(unittest.TestCase):
    """SECURITY (2 HIGH + 1 LOW): wrappers, interpreters, find -delete, VCS
    reverts, and quoted redirects no longer slip past the write screen."""
    def setUp(self):
        self.ro = {"read_only": True, "write_allow": [".em-review/**"],
                   "coding": {}}
        self.scoped = {"coding": {"scope_paths": ["server/**"]}}

    def test_wrappers_and_interpreters_blocked_readonly(self):
        for cmd in ['sh -c "rm -rf src"', 'bash -c "rm -rf src"',
                    "env X=1 rm -rf src", "nohup rm -rf src", "time rm -rf src",
                    "echo src | xargs rm -rf", "find src -name '*.py' -delete",
                    "find . -exec rm {} +",
                    'python3 -c "import shutil;shutil.rmtree(1)" src',
                    "perl -e 'unlink 1'", "git checkout -- src/x.py",
                    "$(rm -rf src)"]:
            allow, _ = tl.screen_tool(self.ro, "Bash", {"command": cmd}, None)
            self.assertFalse(allow, f"read-only leaked: {cmd}")

    def test_readonly_still_allows_read_commands(self):
        for cmd in ["find src -name '*.py'", "grep -r x src", "cat src/x.py",
                    "ls -la"]:
            allow, _ = tl.screen_tool(self.ro, "Bash", {"command": cmd}, None)
            self.assertTrue(allow, f"over-blocked read cmd: {cmd}")

    def test_scoped_blocks_wrapped_escape_and_destructive(self):
        for cmd in ["env rm -rf ../other", "find . -delete",
                    "git reset --hard", "echo x > ../evil.txt"]:
            v = tl.screen_command(cmd, self.scoped["coding"], None)
            self.assertIsNotNone(v, f"scoped contract allowed: {cmd}")

    def test_scoped_allows_in_scope_and_bare_interpreter(self):
        # in-scope rm and a bare interpreter (documented gap) stay allowed
        self.assertIsNone(
            tl.screen_command("rm server/ok.py", self.scoped["coding"], None))
        self.assertIsNone(
            tl.screen_command('python3 -c "print(1)"',
                              self.scoped["coding"], None))

    def test_quoted_redirect_target_resolved(self):
        self.assertEqual(tl.write_targets('cat e > "my file.txt"'),
                         ["my file.txt"])


class TestKernelCorrectness(unittest.TestCase):
    """CODE-QUALITY (MED): is_dirty porcelain prefix; trace ts ordering."""
    def test_is_dirty_ignores_runtime_owned(self):
        ws = _repo()
        os.makedirs(os.path.join(ws, "knowledge"), exist_ok=True)
        open(os.path.join(ws, "knowledge", "index.json"), "w").write("{}")
        # only a runtime-owned file is uncommitted → NOT dirty
        self.assertEqual(tl.is_dirty(ws), [])
        # a real source change IS dirty
        open(os.path.join(ws, "b.py"), "w").write("y=2\n")
        self.assertTrue(tl.is_dirty(ws))

    def test_trace_records_carry_timestamp(self):
        ws = _repo()
        tl.trace(ws, "unit_event", k="v")
        line = open(os.path.join(ws, ".taskplane", "trace.jsonl")).readlines()[-1]
        rec = json.loads(line)
        self.assertIn("ts", rec)
        self.assertIsInstance(rec["ts"], (int, float))


class TestContractSchemaUnified(unittest.TestCase):
    """ARCHITECTURE (MED) + CLI LOWs: one contract schema; status/budget no
    longer crash on a loop-created (kernel) contract; --nfr, SHA-256, home."""
    def test_status_and_budget_survive_kernel_contract(self):
        ws = _repo()
        c = tl.build_contract("loop step", scope=["src/**"])  # no max_cost_usd
        tl.activate(ws, c)
        for verb in (["status"], ["budget", "--spent", "1"]):
            r = subprocess.run([sys.executable, _TP_PY, *verb],
                               cwd=ws, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("KeyError", r.stderr)

    def test_nfr_without_equals_errors_cleanly(self):
        ws = _repo()
        r = subprocess.run([sys.executable, _TP_PY, "req", "new", "t",
                            "--nfr", "security"], cwd=ws,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("LENS=STATEMENT", r.stderr)

    def test_sha256_commit_recognized(self):
        import tp as tpcli
        self.assertTrue(tpcli._is_commit_sha("a" * 40))
        self.assertTrue(tpcli._is_commit_sha("b" * 64))
        self.assertFalse(tpcli._is_commit_sha("HEAD"))
        self.assertFalse(tpcli._is_commit_sha("a" * 41))

    def test_populated_home_is_bare_without_commit(self):
        import tp as tpcli
        # a populated dir that is NOT a committed git tree, treated as $HOME
        ws = tempfile.mkdtemp(prefix="tp-home-")
        open(os.path.join(ws, "app.py"), "w").write("x=1\n")
        orig = os.path.expanduser
        try:
            os.path.expanduser = lambda p: ws if p == "~" else orig(p)
            rep = tpcli._onboard_report(ws)
        finally:
            os.path.expanduser = orig
        self.assertFalse(rep["looks_like_project"])
        self.assertEqual(rep["next_action"], "attach_folder")


class TestActionBudgetEnforced(unittest.TestCase):
    """TESTABILITY (HIGH): the action-budget ceiling is enforced end-to-end
    through the CLI hook — previously ZERO tests covered it."""
    def test_budget_blocks_second_call_at_ceiling(self):
        ws = _repo()
        c = tl.build_contract("t", scope=["**"], max_actions=1,
                              tools=["Write"])
        tl.activate(ws, c)
        inp = {"file_path": "in_scope.py", "content": "x"}
        first = _screen_once(ws, "Write", inp)
        self.assertIn('"approve"', first)          # used 0 < 1 → approve
        second = _screen_once(ws, "Write", inp)
        self.assertIn('"block"', second)           # used 1 >= 1 → block
        self.assertIn("ACTION BUDGET", second)

    def test_budget_rule_boundary(self):
        c = tl.build_contract("t", max_actions=2)
        self.assertTrue(tl.budget_status(c, 1)[0])   # 1 < 2 ok
        self.assertFalse(tl.budget_status(c, 2)[0])  # 2 >= 2 block

    def test_cli_approve_meters_and_block_echoes_reason(self):
        ws = _repo()
        os.makedirs(os.path.join(ws, "server"))
        c = tl.build_contract("t", scope=["server/**"], tools=["Write"])
        tl.activate(ws, c)
        ok = _screen_once(ws, "Write", {"file_path": "server/a.py",
                                        "content": "x"})
        self.assertIn('"approve"', ok)
        bad = _screen_once(ws, "Write", {"file_path": "other/a.py",
                                         "content": "x"})
        self.assertIn('"block"', bad)
        self.assertIn("outside scope", bad)


class TestLoopSerialSkipAndSelection(unittest.TestCase):
    """CODE-QUALITY (MED) + ARCH (LOW): serial advance skips SETTLED tasks;
    the 'neither' selection has a real transition; the dead ternary is gone."""
    def _serial_two_dep(self):
        ws = _repo()
        loop.init(ws, "g", parallel=False)
        s = loop.load(ws)
        s["step"] = "escalated"
        s["current_task"] = 0
        s["tasks"] = [
            {"id": "t1", "scope": ["a/**"], "tests": "t", "status": "running",
             "fix_cycles": 2},
            {"id": "t2", "scope": ["b/**"], "tests": "t", "status": "pending",
             "deps": ["t1"]}]
        loop.save(ws, s)
        return ws

    def test_serial_skip_does_not_reexecute_cascaded(self):
        ws = self._serial_two_dep()
        loop.resolve(ws, "skip")
        s = loop.load(ws)
        # t1 skipped, t2 cascade-skipped → nothing left to build → em
        self.assertEqual(s["tasks"][0]["status"], "skipped")
        self.assertEqual(s["tasks"][1]["status"], "skipped")
        self.assertEqual(s["step"], "em")

    def test_neither_selection_has_transition(self):
        ws = _repo()
        loop.init(ws, "g", parallel=True)
        s = loop.load(ws)
        s["step"] = "selection"; s["ab"] = True
        s["tasks"] = [
            {"id": "va", "variant": "A", "scope": ["src/**"], "status": "passed"},
            {"id": "vb", "variant": "B", "scope": ["src/**"], "status": "passed"}]
        loop.save(ws, s)
        out = loop.select(ws, "neither", note="both wrong")
        self.assertNotIn("error", out)
        s2 = loop.load(ws)
        self.assertEqual(s2["step"], "plan")
        self.assertTrue(all(t["status"] == "not_selected" for t in s2["tasks"]))

    def test_display_pipeline_splices_selection_for_ab(self):
        steps = [s for s, _, _ in loop.display_pipeline({"ab": True})]
        self.assertIn("selection", steps)
        self.assertLess(steps.index("selection"), steps.index("em"))
        # not spliced once selection is recorded
        done = [s for s, _, _ in loop.display_pipeline(
            {"ab": True, "selection": {"choice": "va"}})]
        self.assertNotIn("selection", done)

    def test_retro_tolerates_a_bad_trace_line(self):
        ws = _repo()
        loop.init(ws, "g", parallel=False)
        d = os.path.join(ws, ".taskplane")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "trace.jsonl"), "a") as f:
            f.write('{"event":"loop_init","ts":1}\n')
            f.write("{truncated partial line\n")   # must not crash retro
        out = loop.retro(ws)
        self.assertNotIn("error", out)


class TestDashboardDecoupled(unittest.TestCase):
    """ARCHITECTURE (MED) + A11Y (LOW): the view derives from the engine's
    read models; empty findings render; active state has non-color cues."""
    def test_role_labels_sourced_from_engine(self):
        self.assertIs(dashboard.STEP_ROLE_LABEL, loop.STEP_ROLE)

    def test_counts_via_public_accessors(self):
        ws = _repo()
        c = dashboard._counts(ws)
        self.assertEqual(set(c), {"decisions", "requirements", "debt",
                                  "modules", "edges"})

    def test_empty_findings_render_is_clean(self):
        html = dashboard.render_findings([], {"title": "clean", "gate": True})
        self.assertIn("no findings", html)
        self.assertIn("aria-pressed", html)   # a11y cue present

    def test_findings_chips_have_aria(self):
        html = dashboard.render_findings(
            [{"severity": "high", "domain": "x", "file": "a.py", "line": 1,
              "title": "t", "scenario": "s", "fix": "f"}], {"title": "r"})
        self.assertIn("aria-pressed", html)
        self.assertIn("setAttribute", html)   # JS updates the pressed state


class TestLensEmptyRouting(unittest.TestCase):
    """TESTABILITY (MED): a no-op diff signals nothing_to_review instead of
    instructing a dispatch of zero agents."""
    def test_empty_routing_signals_nothing(self):
        import lens
        routing = lens.route(["notes.txt"], catalog=None)
        d = lens.dispatch_briefs(routing)
        if not d["deep"] and not d["sweep"]:
            self.assertTrue(d.get("nothing_to_review"))
            self.assertIn("nothing to review", d["instruction"].lower())


class TestDepgraphIncremental(unittest.TestCase):
    """SCALABILITY (MED): a rescan reuses unchanged files by mtime/size and
    yields an identical graph."""
    def test_rescan_is_identical_and_caches_stat(self):
        import depgraph as dg
        ws = tempfile.mkdtemp(prefix="tp-dg-")
        os.makedirs(os.path.join(ws, "src"))
        open(os.path.join(ws, "src", "x.py"), "w").write("import os\n")
        g1 = dg.scan(ws); dg.save(ws, g1)
        g2 = dg.scan(ws)
        self.assertEqual(sorted(map(tuple, g1["edges"])),
                         sorted(map(tuple, g2["edges"])))
        # cached entries now carry size + mtime for the short-circuit
        entry = next(iter(g2["files"].values()))
        self.assertIn("size", entry)
        self.assertIn("mtime", entry)


class TestKBSensitiveGuard(unittest.TestCase):
    """DATA-SAFETY (MED): the committed-store lint flags pricing/
    commercialization strategy so it can't ship in the public repo."""
    def test_pricing_marker_flagged(self):
        import kb
        ws = tempfile.mkdtemp(prefix="tp-kb-")
        os.makedirs(os.path.join(ws, "knowledge", "decisions"))
        open(os.path.join(ws, "knowledge", "decisions", "0001-x.md"),
             "w").write("Paid SKU ~15-25k/yr, ACV 25-75k. Monetize later.")
        problems = kb.lint(ws)
        self.assertTrue(any("commercial" in p["problem"] for p in problems))


if __name__ == "__main__":
    unittest.main()
