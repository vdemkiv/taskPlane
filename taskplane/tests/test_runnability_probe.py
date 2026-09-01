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
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lens                     # noqa: E402
import loop                     # noqa: E402
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

    def test_typescript_is_a_first_class_toolchain_not_only_generic_node(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "tsconfig.json"), "w").close()
            open(os.path.join(d, "package.json"), "w").close()
            self.assertEqual(runnability.detect(d), ["typescript", "node"])

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

    def test_typescript_requires_the_checkout_local_compiler(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "tsconfig.json"), "w").close()
            os.makedirs(os.path.join(d, "node_modules"))
            orig = runnability.shutil.which
            runnability.shutil.which = lambda t: "/usr/bin/" + t
            try:
                res = runnability.probe(d)
            finally:
                runnability.shutil.which = orig
            self.assertEqual(res["checks"][0]["id"], "typescript")
            self.assertEqual(res["checks"][0]["verdict"],
                             runnability.BROKEN)
            self.assertIn("TypeScript compiler", res["checks"][0]["detail"])

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

    def test_an_authorized_off_switch_skips_the_probe_entirely(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "go.mod"), "w").close()
            calls = []
            orig, runnability.probe = runnability.probe, self._stub(d, calls)
            os.environ["TASKPLANE_RUNNABILITY"] = "off"
            authority = {
                "schema": "taskplane.human-decision/v1",
                "authorized": True,
                "authority_requested": "gate_weakening",
                "actor": "human:test",
                "thread": "runnability-probe",
                "revision": "1",
            }
            try:
                res = runnability.probe_once(
                    d, settings_authority=authority)
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

    def test_pm_gate_is_invariant_under_absent_green_and_broken_probes(self):
        verdicts = {
            "absent": None,
            "green": runnability.RUNS,
            "broken": runnability.BROKEN,
        }
        for label, verdict in verdicts.items():
            with self.subTest(probe=label), tempfile.TemporaryDirectory() as ws:
                os.makedirs(os.path.join(ws, "specs"))
                with open(os.path.join(ws, "specs", "spec.md"), "w",
                          encoding="utf-8") as f:
                    f.write("A current product requirement.\n")
                subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
                subprocess.run(["git", "config", "user.email", "t@t"],
                               cwd=ws, check=True)
                subprocess.run(["git", "config", "user.name", "t"],
                               cwd=ws, check=True)
                subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
                subprocess.run(["git", "commit", "-qm", "base"], cwd=ws,
                               check=True)

                with mock.patch.dict(
                        os.environ, {"TASKPLANE_STAGE_NATIVE": "disabled"}):
                    self.assertEqual(loop.init(ws, "goal")["step"], "pm")
                    if verdict is not None:
                        runnability.store(ws, {
                            "fingerprint": runnability.fingerprint(ws),
                            "checks": [{
                                "id": "python", "tool": "python3",
                                "command": "pytest", "verdict": verdict,
                                "detail": "ok" if verdict == runnability.RUNS
                                else "dependency failure",
                            }],
                            "summary": "pytest runs" if verdict ==
                            runnability.RUNS else
                            "pytest could not run — dependency failure",
                        })
                    result = loop.gate(ws, "pass")

                self.assertNotIn("error", result, result)
                self.assertEqual(result["step"], "plan")


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
    def test_hostile_locale_preserves_non_ascii_probe_output(self):
        """The verdict remains usable when a C-locale child emits UTF-8."""
        spec = {
            "id": "unicode", "markers": ("tool.marker",),
            "tool": "python3", "test_cmd": "unicode tests",
            "probe": (
                sys.executable, "-c",
                "import sys; sys.stdout.buffer.write("
                "'café — ✓\\n'.encode('utf-8')); raise SystemExit(1)",
            ),
        }
        with tempfile.TemporaryDirectory() as ws:
            open(os.path.join(ws, "tool.marker"), "w").close()
            with mock.patch.object(runnability, "SPECS", (spec,)), \
                    mock.patch.object(runnability, "_BY_ID", {"unicode": spec}), \
                    mock.patch.object(runnability.shutil, "which",
                                      return_value=sys.executable), \
                    mock.patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}):
                result = runnability.probe(ws, only=["unicode"])

        self.assertEqual(result["checks"][0]["verdict"], runnability.BROKEN)
        self.assertEqual(result["checks"][0]["detail"], "café — ✓")


if __name__ == "__main__":
    unittest.main()


class TestResumeAnInterruptedWave(unittest.TestCase):
    """The most expensive accident this product has: a fan-out spawned in a
    turn that dies before the agents report, and the whole wave paid for
    twice. Four of ten sub-agent transcripts in one measured session were
    exactly that — ~16% of its effective tokens."""

    def _lane(self, ws, lid, findings=None):
        d = os.path.join(ws, ".em-review", f"lens-{lid}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "findings.json"), "w", encoding="utf-8") as f:
            json.dump({"lens": lid, "findings": findings or []}, f)

    def _briefs(self):
        return {"deep": [{"id": "security", "name": "S"},
                         {"id": "perf", "name": "P"},
                         {"id": "qa", "name": "Q"}],
                "sweep": {"ids": ["x"]},
                "instruction": "Dispatch ONE tp-lens agent per DEEP brief."}

    def test_landed_lanes_are_not_re_dispatched(self):
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            self._lane(ws, "security")
            self._lane(ws, "sweep")
            out = tpcli._resume_filter(ws, self._briefs())
            self.assertEqual([b["id"] for b in out["deep"]], ["perf", "qa"])
            self.assertIsNone(out["sweep"])
            self.assertEqual(sorted(out["resumed"]["skipped"]),
                             ["security", "sweep"])
            self.assertIn("RESUMING an interrupted wave", out["instruction"])

    def test_a_fully_landed_wave_dispatches_nothing(self):
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            for lid in ("security", "perf", "qa", "sweep"):
                self._lane(ws, lid)
            out = tpcli._resume_filter(ws, self._briefs())
            self.assertEqual(out["deep"], [])
            self.assertTrue(out["nothing_to_review"])
            self.assertIn("dispatch NOTHING", out["instruction"])

    def test_a_corrupt_findings_file_is_re_run_not_accepted(self):
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            d = os.path.join(ws, ".em-review", "lens-security")
            os.makedirs(d)
            with open(os.path.join(d, "findings.json"), "w",
                      encoding="utf-8") as f:
                f.write("{ truncated")
            out = tpcli._resume_filter(ws, self._briefs())
            self.assertIn("security", [b["id"] for b in out["deep"]])

    def test_a_findings_file_without_a_findings_list_is_not_landed(self):
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            d = os.path.join(ws, ".em-review", "lens-perf")
            os.makedirs(d)
            with open(os.path.join(d, "findings.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"lens": "perf"}, f)
            out = tpcli._resume_filter(ws, self._briefs())
            self.assertIn("perf", [b["id"] for b in out["deep"]])

    def test_nothing_is_skipped_when_no_lane_landed(self):
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            out = tpcli._resume_filter(ws, self._briefs())
            self.assertEqual(len(out["deep"]), 3)
            self.assertIsNotNone(out["sweep"])
            self.assertEqual(out["resumed"]["skipped"], [])
            self.assertNotIn("RESUMING", out["instruction"])

    def test_resume_and_the_wave_board_read_the_same_source(self):
        """If they ever diverge, the board shows a lane DONE while dispatch
        re-runs it — the bug that resume exists to prevent."""
        import tp as tpcli
        with tempfile.TemporaryDirectory() as ws:
            self._lane(ws, "security", [{"severity": "high"}])
            self.assertTrue(tpcli._lane_landed(ws, "security"))
            self.assertFalse(tpcli._lane_landed(ws, "perf"))
