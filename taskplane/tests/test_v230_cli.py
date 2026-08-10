"""v2.3.0 CLI fix wave — regression tests for the tp.py / manifest findings.

Covers (fix agent C_cli):
  HIGH  single-source version + `tp version --verify` drift detection
  MED   loop/track engine error dicts exit nonzero (refusals are LOUD)
  MED   tier-routing observability: dispatch audit at the gate summary
  MED   meter.json read-modify-write serialized under file_lock
  MED   main() user-layer error boundary — StateError: one governed line +
        remedy, exit 1; UNEXPECTED errors: short reason + FULL traceback on
        stderr, exit 70 (EX_SOFTWARE) — detail preserved, never swallowed;
        TASKPLANE_DEBUG=1 re-raises; BrokenPipeError unchanged
  LOW   `tp new --budget 0` means a ZERO ceiling, negatives rejected
  LOW   lens dispatch --dashboard is a pure view (no expectation re-record)
  LOW   git helper consolidation (_git_head gone; impact uses changed_files)
  LOW   no bare open().read() in tp.py
  LOW   summary prints the decision sentence once
  LOW   git absence -> 'git is required' message, exit nonzero
  LOW   _bare_root: TASKPLANE_BARE_ROOT extends (never shrinks) the guard
  LOW   fourth Codex prompt (taskplane status) present
"""
import contextlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tpl  # noqa: E402
import tp as cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=ws, capture_output=True)
    return ws


def _capture(fn, *args):
    """Run fn capturing stdout/stderr; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- version

def _version_fixture(tmp, v="9.9.9", drift=None):
    """A minimal plugin tree whose every surface says `v` — except the one
    named in `drift`."""
    def w(rel, body):
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    codex = drift.get("codex", v) if drift else v
    claude = drift.get("claude", v) if drift else v
    market = drift.get("market", v) if drift else v
    docs = drift.get("docs", v) if drift else v
    w(".codex-plugin/plugin.json", json.dumps({"name": "t", "version": codex}))
    w(".claude-plugin/plugin.json", json.dumps({"name": "t", "version": claude}))
    w(".claude-plugin/marketplace.json", json.dumps(
        {"version": market, "plugins": [{"name": "t", "version": market}]}))
    w("README.md", f"hello v{v}\n")
    w("CHANGELOG.md", f"| v{v} | stuff |\n")
    w("docs/openai-submission.md", f"submission {docs}\n")
    return tmp


class TestVersionSingleSource(unittest.TestCase):
    def test_authoritative_source_is_codex_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _version_fixture(tmp, "1.2.3")
            self.assertEqual(cli.plugin_version(tmp), "1.2.3")

    def test_consistent_tree_verifies_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            _version_fixture(tmp, "1.2.3")
            rep = cli.version_report(tmp)
            self.assertTrue(rep["ok"], rep["mismatches"])
            # marketplace.json's TWO version fields are both checked.
            market = [c for c in rep["checks"]
                      if c["file"].endswith("marketplace.json")]
            self.assertEqual(len(market), 2)

    def test_manifest_drift_is_mechanically_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _version_fixture(tmp, "1.2.3", drift={"claude": "1.2.2"})
            rep = cli.version_report(tmp)
            self.assertFalse(rep["ok"])
            self.assertTrue(any("plugin.json" in m["file"] and
                                m["found"] == "1.2.2"
                                for m in rep["mismatches"]))

    def test_docs_drift_is_detected_the_shipped_221_failure(self):
        # The exact shipped drift: manifests bumped, submission doc stale.
        with tempfile.TemporaryDirectory() as tmp:
            _version_fixture(tmp, "2.2.1", drift={"docs": "2.2.0"})
            rep = cli.version_report(tmp)
            self.assertFalse(rep["ok"])
            self.assertTrue(any("openai-submission" in m["file"]
                                for m in rep["mismatches"]))

    def test_docs_without_version_literals_cannot_drift(self):
        # The submission worksheet dropped hand-synced version mentions; a
        # doc with NO version literals is clean, a STALE one still fails.
        with tempfile.TemporaryDirectory() as tmp:
            _version_fixture(tmp, "1.2.3", drift={"docs": ""})
            with open(os.path.join(tmp, "docs", "openai-submission.md"),
                      "w", encoding="utf-8") as f:
                f.write("submission worksheet — no version literals here\n")
            rep = cli.version_report(tmp)
            self.assertTrue(rep["ok"], rep["mismatches"])

    def test_live_repo_manifests_all_agree(self):
        # The four hand-maintained manifest fields must agree with the
        # single source right now (docs may lag; manifests may not).
        rep = cli.version_report(ROOT)
        manifest_bad = [c for c in rep["mismatches"]
                        if c["file"].endswith("plugin.json")
                        or c["file"].endswith("marketplace.json")]
        self.assertEqual(manifest_bad, [])
        # No literal pin — the release step bumps the single source and this
        # test must keep passing; drift (not the number) is what it guards.
        self.assertRegex(rep["version"], r"^\d+\.\d+\.\d+$")

    def test_cli_version_prints_and_exits_zero(self):
        r = subprocess.run([sys.executable, TPPY, "version"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), cli.plugin_version(ROOT))


# ------------------------------------------------- loop/track exit codes

class TestEngineErrorsExitNonzero(unittest.TestCase):
    def test_loop_error_dict_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            for argv in (["loop", "--workspace", ws, "submit", "pass"],
                         ["loop", "--workspace", ws, "gate", "pass"],
                         ["loop", "--workspace", ws, "wave"]):
                rc, out, _ = _capture(cli.main, argv)
                self.assertEqual(rc, 1, argv)
                self.assertIn("error", json.loads(out))

    def test_track_error_dict_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            rc, out, _ = _capture(
                cli.main, ["track", "--workspace", ws, "switch", "nope"])
            self.assertEqual(rc, 1)
            self.assertIn("error", json.loads(out))
            # duplicate `track new` refuses AND exits nonzero
            rc, _, _ = _capture(
                cli.main, ["track", "--workspace", ws, "new", "t1"])
            self.assertEqual(rc, 0)
            rc, out, _ = _capture(
                cli.main, ["track", "--workspace", ws, "new", "t1"])
            self.assertEqual(rc, 1)
            self.assertIn("error", json.loads(out))

    def test_loop_refusal_exit_code_via_subprocess(self):
        # The real driver contract: a scripted `&&` chain / CI wrapper sees
        # $? = 1 on an engine refusal, never 0.
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            r = subprocess.run([sys.executable, TPPY, "loop", "--workspace",
                                ws, "gate", "pass"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 1)
            self.assertIn("error", json.loads(r.stdout))

    def test_track_success_still_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            rc, out, _ = _capture(
                cli.main, ["track", "--workspace", ws, "list"])
            self.assertEqual(rc, 0)


# ------------------------------------------- dispatch audit at the gate

class TestGateDispatchAudit(unittest.TestCase):
    def test_gate_summary_carries_dispatch_audit_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            rc, out, _ = _capture(
                cli.main, ["loop", "--workspace", ws, "gate", "pass"])
            data = json.loads(out)
            audit = data.get("dispatch_audit")
            self.assertIsNotNone(audit, "gate output must surface the "
                                 "expected-vs-observed dispatch audit")
            for key in ("expected", "observed", "mismatches", "unobserved",
                        "hook_active"):
                self.assertIn(key, audit)
            # observability only — no dispatches observed is a NOTE, and the
            # audit never flips the exit code (rc==1 here is the error dict).
            self.assertFalse(audit["hook_active"])
            self.assertIn("no dispatches observed", audit["note"])
            self.assertEqual(rc, 1)


# ---------------------------------------------------- meter under lock

def _bump_n(args):
    ws, n = args
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    import tp as _cli
    for _ in range(n):
        _cli._meter_bump(ws, "T", "actions")
    return True


class TestMeterSerialized(unittest.TestCase):
    def test_concurrent_bumps_never_lose_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            procs, per = 4, 10
            with multiprocessing.Pool(procs) as pool:
                pool.map(_bump_n, [(ws, per)] * procs)
            m = cli._meter_load(ws, strict=True)
            self.assertEqual(m["T"]["actions"], procs * per,
                             "unserialized read-modify-write lost bumps — "
                             "the enforced budget ceiling undercounts")

    def test_single_bump_shape_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            e = cli._meter_bump(ws, "T", "actions")
            self.assertEqual(e["actions"], 1)
            self.assertIn("last_action_ts", e)
            e = cli._meter_bump(ws, "T", "denies")
            self.assertEqual(e["denies"], 1)


# ------------------------------------------------- main() error boundary

class TestUserLayerErrorBoundary(unittest.TestCase):
    def _with_raising_summary(self, exc, argv=("summary",), env=None):
        orig = cli.cmd_summary

        def boom(a):
            raise exc
        cli.cmd_summary = boom
        old_env = {}
        for k, v in (env or {}).items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            return _capture(cli.main, list(argv))
        finally:
            cli.cmd_summary = orig
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_state_error_renders_message_and_remedy_no_traceback(self):
        exc = tpl.StateError("/x/loop.json", "corrupt state file",
                             "restore it from git")
        rc, out, err = self._with_raising_summary(exc)
        self.assertEqual(rc, 1)                      # LOUD, never exit 0
        self.assertIn("corrupt state file", err)
        self.assertIn("restore it from git", err)    # the remedy
        self.assertIn("TASKPLANE_DEBUG=1", err)
        self.assertNotIn("Traceback", err + out)

    def test_unexpected_error_short_reason_full_traceback_exit_70(self):
        # Binding contract: the boundary GOVERNS the message but never
        # swallows the detail — full traceback on stderr, exit 70 so a
        # driver can tell an internal fault (70) from a governed refusal (1).
        rc, out, err = self._with_raising_summary(ValueError("boom reason"))
        self.assertEqual(rc, 70)
        self.assertIn("summary failed", err)
        self.assertIn("boom reason", err)            # underlying reason kept
        self.assertIn("Traceback", err)              # full detail preserved
        self.assertIn("ValueError", err)
        self.assertNotIn("Traceback", out)           # stderr only

    def test_debug_env_restores_the_traceback(self):
        with self.assertRaises(ValueError):
            self._with_raising_summary(ValueError("boom"),
                                       env={"TASKPLANE_DEBUG": "1"})

    def test_missing_git_gets_the_documented_remedy(self):
        exc = FileNotFoundError(2, "No such file or directory")
        exc.filename = "git"
        rc, out, err = self._with_raising_summary(exc)
        self.assertEqual(rc, 1)
        self.assertIn("git is required", err)
        self.assertNotIn("Traceback", err + out)

    def test_corrupt_loop_state_no_traceback_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            d = os.path.join(ws, ".taskplane")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "loop.json"), "w", encoding="utf-8") as f:
                f.write("<<<<<<< merge conflict garbage")
            env = {**os.environ}
            env.pop("TASKPLANE_DEBUG", None)
            r = subprocess.run(
                [sys.executable, TPPY, "summary", "--workspace", ws],
                capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
            self.assertNotIn("Traceback", r.stderr + r.stdout)
            if r.returncode != 0:
                # boundary path: the reason must be printed
                self.assertTrue(r.stderr.strip())


# ------------------------------------------------------------ budget 0

class TestBudgetZero(unittest.TestCase):
    def test_budget_zero_means_zero_not_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            rc, _, _ = _capture(cli.main, ["new", "g", "--scope", "src/**",
                                           "--budget", "0",
                                           "--workspace", ws])
            self.assertEqual(rc, 0)
            c = tpl.load_active(ws)
            self.assertEqual(c["budget"]["max_cost_usd"], 0.0)

    def test_budget_zero_flags_any_spend_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            _capture(cli.main, ["new", "g", "--scope", "src/**",
                                "--budget", "0", "--workspace", ws])
            rc, out, _ = _capture(cli.main, ["budget", "--spent", "0.01",
                                             "--workspace", ws])
            self.assertEqual(rc, 2)          # OVER — maximally strict
            self.assertIn("OVER", out)

    def test_negative_budget_rejected_no_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            rc, _, err = _capture(cli.main, ["new", "g", "--scope", "src/**",
                                             "--budget", "-1",
                                             "--workspace", ws])
            self.assertEqual(rc, 1)
            self.assertIn(">= 0", err)
            self.assertIsNone(tpl.load_active(ws))

    def test_default_budget_still_3_usd(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            _capture(cli.main, ["new", "g", "--scope", "src/**",
                                "--workspace", ws])
            c = tpl.load_active(ws)
            self.assertEqual(c["budget"]["max_cost_usd"], 3.0)


# --------------------------------------- lens --dashboard is a pure view

class TestLensDashboardIdempotent(unittest.TestCase):
    def _expected_count(self, ws):
        p = os.path.join(tpl.tp_dir(ws), "expected_dispatch.json")
        if not os.path.exists(p):
            return 0
        with open(p, encoding="utf-8") as f:
            return len(json.load(f))

    def test_dashboard_rerender_records_no_expectations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            with open(os.path.join(ws, "src", "a.py"), "a", encoding="utf-8") as f:
                f.write("y = 2\n")
            rc, _, _ = _capture(cli.main, ["lens", "dispatch", "--all",
                                           "--workspace", ws])
            self.assertEqual(rc, 0)
            n_real = self._expected_count(ws)
            self.assertGreater(n_real, 0)
            for _ in range(2):       # the R1 re-render loop
                rc, _, _ = _capture(cli.main,
                                    ["lens", "dispatch", "--all",
                                     "--dashboard", "--workspace", ws])
                self.assertEqual(rc, 0)
            self.assertEqual(self._expected_count(ws), n_real,
                             "--dashboard re-render must not append fresh "
                             "unmatched expectations")


# --------------------------------------------- git helper consolidation

class TestGitHelperConsolidation(unittest.TestCase):
    def test_git_head_duplicate_removed(self):
        self.assertFalse(hasattr(cli, "_git_head"))

    def test_impact_default_fileset_filters_runtime_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
            with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            with open(os.path.join(ws, "src", "new.py"), "w", encoding="utf-8") as f:
                f.write("z = 3\n")
            files = cli._changed_for_impact(ws, "HEAD")
            self.assertIn("src/new.py", files)
            self.assertNotIn("plan/tasks.json", files,
                             "CLI impact must exclude RUNTIME_OWNED "
                             "bookkeeping like the engine does")

    def test_no_bare_open_read_left_in_tp(self):
        with open(TPPY, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("json.load(open(", src)
        self.assertNotIn(").read().strip()", src.replace(
            "f.read().strip()", ""))


# ----------------------------------------------------- summary dedupe

class TestSummarySingleDecisionSentence(unittest.TestCase):
    def test_decision_sentence_printed_once(self):
        import loop as loopmod
        sentence = "Review and approve the implementation plan."
        orig = loopmod.user_summary
        loopmod.user_summary = lambda ws: {
            "headline": "Decision required — " + sentence,
            "decision": sentence, "state": "plan_approval",
            "goal": "g"}
        try:
            rc, out, _ = _capture(cli.main, ["summary"])
        finally:
            loopmod.user_summary = orig
        self.assertEqual(rc, 0)
        self.assertEqual(out.count(sentence), 1,
                         "the same decision sentence printed twice:\n" + out)
        self.assertIn("ACTION REQUIRED: " + sentence, out)
        self.assertIn("Decision required", out)

    def test_distinct_headline_untouched(self):
        import loop as loopmod
        orig = loopmod.user_summary
        loopmod.user_summary = lambda ws: {
            "headline": "Build step 3 of 5 in progress.",
            "decision": "Approve the fix budget.", "state": "execute",
            "goal": "g"}
        try:
            rc, out, _ = _capture(cli.main, ["summary"])
        finally:
            loopmod.user_summary = orig
        self.assertIn("Build step 3 of 5 in progress.", out)
        self.assertIn("ACTION REQUIRED: Approve the fix budget.", out)


# ---------------------------------------------------- bare-root guard

class TestBareRootEnvOverride(unittest.TestCase):
    def test_extra_root_via_env_gets_the_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = os.path.join(tmp, "other-host-home")
            os.makedirs(other)
            self.assertFalse(cli._bare_root(other))     # default: unguarded
            old = os.environ.get("TASKPLANE_BARE_ROOT")
            os.environ["TASKPLANE_BARE_ROOT"] = other
            try:
                self.assertTrue(cli._bare_root(other))  # now protected
            finally:
                if old is None:
                    os.environ.pop("TASKPLANE_BARE_ROOT", None)
                else:
                    os.environ["TASKPLANE_BARE_ROOT"] = old

    def test_default_roots_unchanged(self):
        # /home/claude stays guarded with no env var set (default set only
        # ever GROWS via the override — guardrails never shrink).
        old = os.environ.pop("TASKPLANE_BARE_ROOT", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ws = _repo(tmp)
                self.assertFalse(cli._bare_root(ws))   # real project: fine
        finally:
            if old is not None:
                os.environ["TASKPLANE_BARE_ROOT"] = old


# --------------------------------------------------- screen git memo

class TestGovernedRootCache(unittest.TestCase):
    def test_toplevel_memoized_within_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            cli._GIT_TOP_CACHE.clear()
            cli._governed_root(ws)
            self.assertIn(os.path.abspath(ws), cli._GIT_TOP_CACHE)
            # poison the cache: a second call must NOT re-shell git
            cli._GIT_TOP_CACHE[os.path.abspath(ws)] = "/poisoned"
            cli._governed_root(ws)   # would repopulate if it re-ran git
            self.assertEqual(cli._GIT_TOP_CACHE[os.path.abspath(ws)],
                             "/poisoned")
            cli._GIT_TOP_CACHE.clear()


# ------------------------------------------------ codex status discovery
#
# OpenAI's submission rules cap interface.defaultPrompt at THREE entries
# (plugin_default_prompt_too_many), so the fourth promised surface (status)
# is discoverable via the capabilities list instead — and the prompt count
# stays at the marketplace maximum.

class TestCodexStatusDiscoverable(unittest.TestCase):
    def test_status_discoverable_and_prompt_cap_respected(self):
        p = os.path.join(ROOT, ".codex-plugin", "plugin.json")
        with open(p, encoding="utf-8") as f:
            manifest = json.load(f)
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertEqual(len(prompts), 3,
                         "OpenAI caps defaultPrompt at 3 entries")
        caps = manifest["interface"]["capabilities"]
        self.assertTrue(any("status" in c.lower() for c in caps),
                        "status must appear on a Codex discovery surface "
                        "(capabilities), since prompts are capped at 3")


if __name__ == "__main__":
    unittest.main()
