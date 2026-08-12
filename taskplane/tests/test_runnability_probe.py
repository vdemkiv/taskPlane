"""B9 — probe build/test runnability ONCE, state it in every brief.

The karpenter field report (aws/karpenter-provider-aws#9464) measured the
waste directly: six lens agents were dispatched in parallel and all six
independently discovered that `go test ./...` could not run in that sandbox.
Six cold starts, six failed commands, six chains of reasoning about whether
the failure belonged to the PR — for one fact about the CHECKOUT.

These tests pin the three properties that make that not happen again:

  * the probe never runs the suite (it runs a bounded, cheap subcommand),
  * the answer is computed ONCE per tree state and shared by the whole wave,
  * every dispatched brief carries the verdict as a stated fact.

And one property that keeps it honest: it is INFORMATION, not enforcement.
Nothing here may block anything — a lens that cannot run a dynamic check
reports that in its finding; it does not get denied.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lens                     # noqa: E402
import runnability              # noqa: E402
import taskplane_lite as tp     # noqa: E402


def _routing(n=2):
    return {"context": {"changed_files": n, "files": ["a.go", "b.go"]},
            "lenses": [
                {"id": "security", "name": "Security", "tier": "deep",
                 "mode": "subagent", "reasons": ["r"], "checks": ["c"]},
                {"id": "perf", "name": "Performance", "tier": "sweep",
                 "mode": "inline", "reasons": ["r"], "checks": ["c"]}]}


class TestDetection(unittest.TestCase):
    def test_it_detects_the_toolchains_the_repo_declares(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            open(os.path.join(d, "package.json"), "w").close()
            self.assertEqual(runnability.detect(d), ["go", "node"])

    def test_a_repo_with_no_manifests_detects_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(runnability.detect(d), [])
            self.assertEqual(runnability.probe(d)["checks"], [])
            self.assertIn("no build/test toolchain",
                          runnability.probe(d)["summary"])


class TestTheProbeIsNotTheSuite(unittest.TestCase):
    """The cheapest way to get this wrong is to 'probe' by running the tests.
    Every declared probe must be a bounded subcommand, never a test verb."""

    FORBIDDEN = {"test", "tests", "check", "verify"}

    def test_no_spec_probes_by_running_tests(self):
        for spec in runnability.SPECS:
            with self.subTest(spec["id"]):
                self.assertFalse(
                    self.FORBIDDEN & set(spec["probe"]),
                    f"{spec['id']} probes by running its test verb: "
                    f"{spec['probe']}")

    def test_every_probe_is_bounded_by_a_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            seen = {}

            def fake_run(cmd, **kw):
                seen.update(kw)
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

            orig, runnability.subprocess.run = runnability.subprocess.run, fake_run
            try:
                if not runnability.shutil.which("go"):
                    self.skipTest("no go toolchain to reach the probe")
                res = runnability.probe(d, timeout=3)
            finally:
                runnability.subprocess.run = orig
            self.assertEqual(seen.get("timeout"), 3)
            self.assertEqual(res["checks"][0]["verdict"], runnability.UNKNOWN)

    def test_a_missing_toolchain_is_answered_without_any_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Cargo.toml"), "w").close()
            calls = []

            def boom(cmd, **kw):
                calls.append(cmd)
                raise AssertionError("must not spawn for a missing toolchain")

            orig_which = runnability.shutil.which
            orig_run = runnability.subprocess.run
            runnability.shutil.which = lambda t: None
            runnability.subprocess.run = boom
            try:
                res = runnability.probe(d)
            finally:
                runnability.shutil.which = orig_which
                runnability.subprocess.run = orig_run
            self.assertEqual(calls, [])
            self.assertEqual(res["checks"][0]["verdict"],
                             runnability.UNAVAILABLE)
            self.assertIn("cargo", res["checks"][0]["detail"])

    def test_node_without_installed_deps_is_broken_not_runnable(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "package.json"), "w").close()
            orig = runnability.shutil.which
            runnability.shutil.which = lambda t: "/usr/bin/" + t
            try:
                res = runnability.probe(d)
            finally:
                runnability.shutil.which = orig
            self.assertEqual(res["checks"][0]["verdict"], runnability.BROKEN)
            self.assertIn("node_modules", res["checks"][0]["detail"])

    def test_the_probe_never_raises_even_when_the_child_cannot_start(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            orig_which = runnability.shutil.which
            orig_run = runnability.subprocess.run
            runnability.shutil.which = lambda t: "/usr/bin/" + t

            def oserr(cmd, **kw):
                raise OSError("nope")

            runnability.subprocess.run = oserr
            try:
                res = runnability.probe(d)
            finally:
                runnability.shutil.which = orig_which
                runnability.subprocess.run = orig_run
            self.assertEqual(res["checks"][0]["verdict"], runnability.UNKNOWN)


class TestProbedOnce(unittest.TestCase):
    """The whole point: one answer per tree state, shared by the wave."""

    def _stub(self, d, calls):
        def fake_probe(root, timeout=None, only=None):
            calls.append(root)
            return {"fingerprint": runnability.fingerprint(root),
                    "checks": [{"id": "go", "tool": "go",
                                "command": "go test ./...",
                                "verdict": runnability.UNAVAILABLE,
                                "detail": "`go` is not on PATH"}],
                    "summary": "go test ./... could not run — "
                               "`go` is not on PATH"}
        return fake_probe

    def test_six_agents_in_one_wave_probe_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            calls = []
            orig, runnability.probe = runnability.probe, self._stub(d, calls)
            try:
                for _ in range(6):
                    res = runnability.probe_once(d)
            finally:
                runnability.probe = orig
            self.assertEqual(len(calls), 1,
                             "the wave paid for the same fact more than once")
            self.assertTrue(res.get("cached"))

    def test_editing_the_manifest_re_probes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "go.mod")
            with open(p, "w") as f:
                f.write("module x\n")
            calls = []
            orig, runnability.probe = runnability.probe, self._stub(d, calls)
            try:
                runnability.probe_once(d)
                os.utime(p, (1, 1))
                runnability.probe_once(d)
            finally:
                runnability.probe = orig
            self.assertEqual(len(calls), 2)

    def test_a_changed_PATH_re_probes(self):
        """Installing the missing toolchain mid-review must not be masked by
        a cache that says it is still missing."""
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            calls = []
            orig, runnability.probe = runnability.probe, self._stub(d, calls)
            old_path = os.environ.get("PATH", "")
            try:
                runnability.probe_once(d)
                os.environ["PATH"] = old_path + os.pathsep + "/opt/go/bin"
                runnability.probe_once(d)
            finally:
                runnability.probe = orig
                os.environ["PATH"] = old_path
            self.assertEqual(len(calls), 2)

    def test_the_off_switch_skips_the_probe_entirely(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            calls = []
            orig, runnability.probe = runnability.probe, self._stub(d, calls)
            os.environ["TASKPLANE_RUNNABILITY"] = "off"
            try:
                res = runnability.probe_once(d)
            finally:
                runnability.probe = orig
                os.environ.pop("TASKPLANE_RUNNABILITY", None)
            self.assertEqual(calls, [])
            self.assertEqual(res["checks"], [])
            self.assertEqual(res["skipped"], "TASKPLANE_RUNNABILITY=off")

    def test_an_unwritable_cache_does_not_fail_the_review(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            orig = runnability.os.makedirs

            def boom(*a, **k):
                raise OSError("read-only")

            runnability.os.makedirs = boom
            try:
                res = runnability.store(d, {"checks": [], "summary": "x"})
            finally:
                runnability.os.makedirs = orig
            self.assertEqual(res["summary"], "x")


class TestEveryBriefCarriesTheVerdict(unittest.TestCase):
    VERDICT = {"fingerprint": "abc",
               "checks": [{"id": "go", "tool": "go",
                           "command": "go test ./...",
                           "verdict": runnability.UNAVAILABLE,
                           "detail": "`go` is not on PATH"}],
               "summary": "go test ./... could not run — `go` is not on PATH"}

    def test_deep_and_sweep_briefs_both_state_it(self):
        out = lens.dispatch_briefs(_routing(), runnability=self.VERDICT)
        for b in out["deep"]:
            self.assertIn("BUILD/TEST RUNNABILITY", b["prompt"])
            self.assertIn("go test ./...", b["prompt"])
            self.assertIn("CANNOT RUN", b["prompt"])
        self.assertIn("BUILD/TEST RUNNABILITY", out["sweep"]["prompt"])

    def test_the_brief_tells_the_agent_not_to_re_probe(self):
        out = lens.dispatch_briefs(_routing(), runnability=self.VERDICT)
        prompt = out["deep"][0]["prompt"]
        self.assertIn("do NOT re-probe", prompt)
        self.assertIn("do not retry the command", prompt)

    def test_a_runnable_toolchain_states_it_without_the_fallback_advice(self):
        ok = {"fingerprint": "a", "checks": [
            {"id": "go", "tool": "go", "command": "go test ./...",
             "verdict": runnability.RUNS, "detail": "`go list ./...` succeeded"}],
            "summary": "go test ./... runs"}
        prompt = lens.dispatch_briefs(_routing(), runnability=ok)["deep"][0]["prompt"]
        self.assertIn("CAN RUN", prompt)
        self.assertNotIn("do not retry the command", prompt)

    def test_the_dispatch_payload_carries_the_verdict_for_the_headline(self):
        out = lens.dispatch_briefs(_routing(), runnability=self.VERDICT)
        self.assertEqual(out["runnability"]["summary"], self.VERDICT["summary"])
        self.assertIn("meta.tests", out["instruction"])

    def test_without_a_probe_the_payload_is_byte_identical_to_before(self):
        """Codex dispatch parity: the probe is ADDITIVE. No probe, no change."""
        a = lens.dispatch_briefs(_routing())
        b = lens.dispatch_briefs(_routing(), runnability=None)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))
        self.assertNotIn("runnability", a)
        self.assertNotIn("BUILD/TEST RUNNABILITY", a["deep"][0]["prompt"])

    def test_a_skipped_probe_adds_no_note(self):
        skipped = {"fingerprint": "a", "checks": [],
                   "skipped": "TASKPLANE_RUNNABILITY=off",
                   "summary": "runnability probe disabled"}
        out = lens.dispatch_briefs(_routing(), runnability=skipped)
        self.assertNotIn("BUILD/TEST RUNNABILITY", out["deep"][0]["prompt"])


class TestItIsInformationNotEnforcement(unittest.TestCase):
    """The deletability contract in spirit: runnability may never gate."""

    def test_no_gate_or_screener_consults_the_probe(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for mod in ("loop.py", "taskplane_lite.py", "obligations.py"):
            with open(os.path.join(root, mod), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("runnability", src,
                             f"{mod} consults the runnability probe — it is "
                             f"information, not a gate")

    def test_the_probe_module_denies_nothing(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "runnability.py"), encoding="utf-8") as f:
            src = f.read()
        for word in ("def deny", "blocked_reason", "sys.exit(2)"):
            self.assertNotIn(word, src)


class TestSummaryLine(unittest.TestCase):
    def test_it_names_the_command_and_the_reason(self):
        s = runnability.summary([{"command": "go test ./...",
                                  "verdict": runnability.UNAVAILABLE,
                                  "detail": "`go` is not on PATH"}])
        self.assertEqual(s, "go test ./... could not run — `go` is not on PATH")

    def test_it_counts_the_other_broken_toolchains(self):
        s = runnability.summary([
            {"command": "go test ./...", "verdict": runnability.UNAVAILABLE,
             "detail": "`go` is not on PATH"},
            {"command": "npm test", "verdict": runnability.BROKEN,
             "detail": "deps missing"},
            {"command": "cargo test", "verdict": runnability.BROKEN,
             "detail": "deps missing"}])
        self.assertIn("(+2 more toolchains)", s)

    def test_all_green_reports_what_runs(self):
        s = runnability.summary([{"command": "pytest",
                                  "verdict": runnability.RUNS, "detail": "ok"}])
        self.assertEqual(s, "pytest runs")
        self.assertTrue(runnability.can_run_tests(
            {"checks": [{"verdict": runnability.RUNS}]}))
        self.assertFalse(runnability.can_run_tests({"checks": []}))


class TestNoLocaleDecoding(unittest.TestCase):
    def test_the_probe_pins_its_encoding(self):
        """v2.9.0's CI red: text=True without encoding= decodes with the
        ambient locale, which is ascii on a bare runner."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "runnability.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('encoding="utf-8"', src)


if __name__ == "__main__":
    unittest.main()
