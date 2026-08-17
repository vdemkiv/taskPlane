"""An obligation converted into a prohibition.

A hook can DENY an action. It cannot COMPEL one. That asymmetry is the whole
reason this product enforces its prohibitions at 100% — the screener refuses
an out-of-scope write, refuses `rm -rf .`, refuses an interpreter escape —
while every OBLIGATION it defines ("render the wave board", "show the
graph") sat at 0% and was left to the assistant's diligence. Five structural
attempts to close that by instruction shipped between v1.5.3 and v2.8.2, and
the complaint arrived again after each one, because an instruction is not a
mechanism.

The conversion IS the mechanism: not "you must show the graph" but "you may
not declare the work finished until the graph has been shown". A conclusion
is a command, a command is a tool call, and a tool call can be denied.

These tests pin the conversion and — just as important — its limits. A
governance mechanism that blocked the WORK, or that had no way out, would be
routed around by uninstalling it.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import obligations                                            # noqa: E402
TP = os.path.join(ROOT, "taskplane", "tp.py")


class _Ws(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-bind-ws-")
        self.home = tempfile.mkdtemp(prefix="tp-bind-home-")
        self._env = {k: os.environ.get(k)
                     for k in ("TASKPLANE_HOME", "TASKPLANE_OBLIGATIONS")}
        os.environ["TASKPLANE_HOME"] = self.home
        os.environ.pop("TASKPLANE_OBLIGATIONS", None)
        self.art = os.path.join(self.ws, "graph.html")
        with io.open(self.art, "w", encoding="utf-8") as f:
            f.write("<div>THE ENGINE'S GRAPH</div>")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def owe(self, binding=True):
        return obligations.issue(
            self.ws, "render_graph", detail="show the product's own view",
            step="review", artifact=self.art, key="review:render_graph",
            binding=binding)


class TheBlockIsNarrow(_Ws):
    """It may refuse the conclusion. It may never refuse the work."""

    def setUp(self):
        super().setUp()
        self.oid = self.owe()

    def test_the_conclusion_is_blocked(self):
        for cmd in ("tp dod", "python3 tp.py dod", "tp loop submit pass",
                    "tp loop approve plan", "./tp loop retro"):
            self.assertIsNotNone(obligations.blocked_reason(self.ws, cmd), cmd)

    def test_doing_the_work_is_never_blocked(self):
        for cmd in ("python3 -m pytest", "grep -rn TODO .", "npm test",
                    "tp graph impact --files a.py", "tp findings --paged",
                    "tp lens dispatch --all", "tp graph html --out g.html",
                    "tp ack o-1", "tp ack --status", "git commit -m x"):
            self.assertIsNone(obligations.blocked_reason(self.ws, cmd), cmd)

    def test_a_lookalike_command_is_not_taskplane(self):
        """`git commit -m "dod done"` contains the word and is not a gate."""
        for cmd in ('git commit -m "dod done"', "echo loop submit",
                    "make dod", "./scripts/dod.sh"):
            self.assertIsNone(obligations.blocked_reason(self.ws, cmd), cmd)

    def test_an_empty_command_is_not_blocked(self):
        self.assertIsNone(obligations.blocked_reason(self.ws, ""))
        self.assertIsNone(obligations.blocked_reason(self.ws, None))


class TheBlockIsEscapable(_Ws):

    def test_advisory_obligations_still_block_nothing(self):
        """The default stays a pure instrument — everything the module
        docstring promises about deletability depends on this."""
        self.owe(binding=False)
        self.assertIsNone(obligations.blocked_reason(self.ws, "tp dod"))
        self.assertEqual(obligations.blocking(self.ws), [])

    def test_the_kill_switch_disables_blocking_but_not_recording(self):
        oid = self.owe()
        os.environ["TASKPLANE_OBLIGATIONS"] = "off"
        self.assertIsNone(obligations.blocked_reason(self.ws, "tp dod"))
        # still recorded — the instrument keeps working
        self.assertEqual([o["id"] for o in obligations.status(self.ws)["open"]],
                         [oid])

    def test_the_switch_accepts_the_obvious_spellings(self):
        self.owe()
        for v in ("off", "0", "false", "advisory", "OFF"):
            os.environ["TASKPLANE_OBLIGATIONS"] = v
            self.assertFalse(obligations.blocking_enabled(), v)
        os.environ["TASKPLANE_OBLIGATIONS"] = "on"
        self.assertTrue(obligations.blocking_enabled())


class TheHonestPathIsTheWayOut(_Ws):

    def test_acknowledging_unblocks(self):
        oid = self.owe()
        self.assertIsNotNone(obligations.blocked_reason(self.ws, "tp dod"))
        obligations.acknowledge(
            self.ws, oid, evidence="shown",
            fingerprint=obligations.artifact_fingerprint(self.art))
        self.assertIsNone(obligations.blocked_reason(self.ws, "tp dod"))

    def test_an_observed_render_of_the_engines_bytes_unblocks(self):
        """The strongest discharge needs no claim at all."""
        self.owe()
        with io.open(self.art, encoding="utf-8") as f:
            body = f.read()
        obligations.observe(self.ws, tool="mcp__visualize__show_widget",
                            fingerprint=obligations.content_fingerprint(body),
                            title="graph", bytes_len=len(body))
        self.assertIsNone(obligations.blocked_reason(self.ws, "tp dod"))

    def test_rendering_a_SUBSTITUTE_does_not_unblock(self):
        """'this is not the graph we designed' — the block survives it."""
        self.owe()
        obligations.observe(
            self.ws, tool="mcp__visualize__show_widget",
            fingerprint=obligations.content_fingerprint("<div>my chart</div>"),
            title="mine", bytes_len=20)
        self.assertIsNotNone(obligations.blocked_reason(self.ws, "tp dod"))

    def test_the_refusal_says_how_to_proceed(self):
        oid = self.owe()
        msg = obligations.blocked_reason(self.ws, "tp dod")
        self.assertIn(oid, msg)
        self.assertIn(f"tp ack {oid}", msg)
        self.assertIn("TASKPLANE_OBLIGATIONS=off", msg)

    def test_each_owed_artifact_is_counted_down_separately(self):
        a = self.owe()
        b = obligations.issue(self.ws, "render_dashboard", detail="the board",
                              step="review", key="review:render_dashboard",
                              binding=True)
        self.assertIn("2 artifacts", obligations.blocked_reason(self.ws, "tp dod"))
        obligations.acknowledge(self.ws, a, evidence="x")
        self.assertIn("1 artifact ", obligations.blocked_reason(self.ws, "tp dod"))
        obligations.acknowledge(self.ws, b, evidence="x")
        self.assertIsNone(obligations.blocked_reason(self.ws, "tp dod"))


class TheRunDeclaresWhatItOwesUpFront(unittest.TestCase):
    """Seeded at contract activation — before the work, not after."""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-owes-")
        self.home = tempfile.mkdtemp(prefix="tp-owes-home-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        for cmd in (["init", "-q", "."], ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.ws, capture_output=True)
        with io.open(os.path.join(self.ws, "a.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.ws, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=self.ws,
                       capture_output=True)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def new(self, *extra):
        return subprocess.run(
            [sys.executable, TP, "new", "--read-only", *extra, "goal",
             "--workspace", self.ws], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=dict(os.environ))

    def test_a_review_owes_the_dashboard_with_its_embedded_graph(self):
        r = self.new("--owes", "review")
        self.assertEqual(r.returncode, 0, r.stderr)
        kinds = sorted(o["kind"] for o in obligations.blocking(self.ws))
        self.assertEqual(kinds, ["render_dashboard"])
        self.assertIn("owes", r.stdout)

    def test_without_owes_nothing_is_seeded(self):
        self.assertEqual(self.new().returncode, 0)
        self.assertEqual(obligations.blocking(self.ws), [])

    def test_an_unknown_run_type_owes_nothing_rather_than_guessing(self):
        self.assertEqual(self.new("--owes", "nonsense").returncode, 0)
        self.assertEqual(obligations.blocking(self.ws), [])

    def test_seeding_is_idempotent_across_reactivation(self):
        """Deterministic ids: re-activating must not inflate the debt."""
        self.new("--owes", "review")
        self.new("--owes", "review")
        self.assertEqual(len(obligations.blocking(self.ws)), 1)

    def test_standalone_graph_render_remains_a_graph_obligation(self):
        """An explicitly rendered graph remains independently accountable."""
        self.assertEqual(self.new("--owes", "review").returncode, 0)
        with io.open(os.path.join(self.ws, "a.py"), "w", encoding="utf-8") as f:
            f.write("value = 1\n")
        env = dict(os.environ)
        scan = subprocess.run(
            [sys.executable, TP, "graph", "--workspace", self.ws, "scan"],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace")
        self.assertEqual(scan.returncode, 0, scan.stderr)
        out = os.path.join(self.ws, "graph-view.html")
        render = subprocess.run(
            [sys.executable, TP, "graph", "--workspace", self.ws, "html",
             "--files", "a.py", "--out", out],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace")
        self.assertEqual(render.returncode, 0, render.stderr)
        issued = {row["id"]: row for row in obligations.read(self.ws)
                  if row.get("event") == "issued"}
        self.assertEqual(len(issued), 2)
        graphs = [row for row in issued.values()
                  if row.get("kind") == "render_graph"]
        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0].get("step"), "graph")
        self.assertEqual(graphs[0].get("artifact"), out)
        self.assertEqual(graphs[0].get("fingerprint"),
                         obligations.artifact_fingerprint(out))

    def test_the_screener_refuses_the_conclusion_end_to_end(self):
        """Through the real hook, not the helper."""
        self.new("--owes", "review")
        ev = {"tool_name": "Bash", "cwd": self.ws,
              "tool_input": {"command": "tp dod"}}
        r = subprocess.run([sys.executable, TP, "screen"],
                           input=json.dumps(ev), capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ))
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("cannot be declared finished", out["reason"])
        self.assertIn("tp ack", out["reason"])

    def test_the_screener_still_allows_the_work_end_to_end(self):
        self.new("--owes", "review")
        ev = {"tool_name": "Bash", "cwd": self.ws,
              "tool_input": {"command": "tp graph impact --files a.py"}}
        r = subprocess.run([sys.executable, TP, "screen"],
                           input=json.dumps(ev), capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ))
        self.assertNotEqual(json.loads(r.stdout).get("decision"), "block")


class TheStopHookReportsWhatWasNeverShown(_Ws):

    def run_verify(self):
        return subprocess.run(
            [sys.executable, TP, "session-verify", "--workspace", self.ws],
            input="{}", capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=dict(os.environ))

    def test_it_is_silent_and_zero_when_nothing_is_owed(self):
        r = self.run_verify()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), "")

    def test_it_exits_two_and_names_the_artifact(self):
        oid = self.owe()
        r = self.run_verify()
        self.assertEqual(r.returncode, 2)
        self.assertIn(oid, r.stderr)
        self.assertIn("tp ack", r.stderr)

    def test_it_is_quiet_again_once_discharged(self):
        oid = self.owe()
        obligations.acknowledge(self.ws, oid, evidence="shown")
        self.assertEqual(self.run_verify().returncode, 0)


class TheEngineStillCannotSeeAnyOfThis(_Ws):
    """The deletability contract, unchanged. The block lives in the SHELL."""

    def test_the_engine_does_not_read_the_ledger(self):
        for name in ("loop.py", "taskplane_lite.py", "lens.py", "evidence.py",
                     "audit.py", "dashboard.py", "depgraph.py"):
            with io.open(os.path.join(ROOT, "taskplane", name),
                         encoding="utf-8") as f:
                src = f.read()
            for forbidden in ("obligations.status", "obligations.blocking",
                              "obligations.blocked_reason"):
                self.assertNotIn(forbidden, src, f"{name}: {forbidden}")

    def test_an_open_binding_obligation_never_reaches_a_loop_gate(self):
        """The prohibition is on the COMMAND, never on the state machine.

        Asserting the gate simply PASSES would be the wrong test — it can
        fail for its own reasons (an unauthored spec, here) and then this
        would go green for a reason unrelated to obligations. What must
        hold is that whatever the gate decides, obligations played no part
        in it.
        """
        import loop
        loop.init(self.ws, "goal")
        loop.next_action(self.ws)
        self.owe()
        self.assertTrue(obligations.blocking(self.ws))
        verdict = json.dumps(loop.gate(self.ws, "pass"), default=str).lower()
        for word in ("obligation", "render_graph", "not been shown",
                     "tp ack"):
            self.assertNotIn(word, verdict)


class TheHooksAreWired(unittest.TestCase):

    def cfg(self):
        with io.open(os.path.join(ROOT, "hooks", "hooks.json"),
                     encoding="utf-8") as f:
            return json.load(f)["hooks"]

    def test_a_stop_hook_runs_the_verifier(self):
        self.assertIn("Stop", self.cfg())
        blob = json.dumps(self.cfg()["Stop"])
        self.assertIn("session-verify", blob)

    def test_the_stop_hook_has_a_windows_form(self):
        for e in self.cfg()["Stop"]:
            for h in e["hooks"]:
                self.assertIn("session-verify", h.get("commandWindows", ""))


if __name__ == "__main__":
    unittest.main()
