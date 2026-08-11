"""The render is OBSERVABLE — the premise that said otherwise was wrong.

`obligations.py` was built on a stated premise: the engine "CANNOT see
whether a rendered artifact was actually put in front of a human", because
`mcp__visualize__show_widget` "happens in the host, outside every process
taskplane runs". Therefore showing an artifact could only ever be a CLAIM.

The premise was false. A PreToolUse matcher is a regex over TOOL NAMES, and
an MCP tool is named `mcp__<server>__<tool>`, so it matches at the same seam
that already screens writes and Task dispatches. These tests pin the three
consequences, because they are the three failures this project has actually
suffered and they were previously indistinguishable from each other:

    SKIPPED       demanded, never rendered
    SUBSTITUTED   rendered — but not the artifact the engine built
    CLAIMED ONLY  acknowledged, with no observation behind the claim

and one property that matters more than any of them: the observer NEVER
denies. A hook that could block a render would be the instrument stopping
the thing it exists to encourage.
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
        self.ws = tempfile.mkdtemp(prefix="tp-render-ws-")
        self.home = tempfile.mkdtemp(prefix="tp-render-home-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def artifact(self, name, body):
        p = os.path.join(self.ws, name)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def render(self, body, title="board", tool="mcp__visualize__show_widget"):
        """Drive the real hook entrypoint over a real subprocess."""
        ev = {"tool_name": tool, "cwd": self.ws, "session_id": "s1",
              "tool_input": {"title": title, "widget_code": body}}
        return subprocess.run(
            [sys.executable, TP, "screen-render"], input=json.dumps(ev),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=dict(os.environ))


class TheObserverNeverDenies(_Ws):
    """The one property that must never regress."""

    def test_a_normal_render_is_allowed(self):
        r = self.render("<div>hi</div>")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")   # no permissionDecision

    def test_malformed_hook_input_is_allowed(self):
        r = subprocess.run([sys.executable, TP, "screen-render"],
                           input="{not json", capture_output=True, text=True,
                           encoding="utf-8", env=dict(os.environ))
        self.assertEqual(r.returncode, 0)

    def test_an_unwritable_ledger_still_allows_the_render(self):
        """Best effort, always — the instrument may not cost a render."""
        blocker = os.path.join(self.home, "iam-a-file")
        with io.open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        os.environ["TASKPLANE_HOME"] = os.path.join(blocker, "store")
        try:
            r = self.render("<div>hi</div>")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "")
        finally:
            os.environ["TASKPLANE_HOME"] = self.home

    def test_a_render_with_no_string_payload_is_allowed(self):
        ev = {"tool_name": "mcp__visualize__show_widget", "cwd": self.ws,
              "tool_input": {"loading_messages": ["a"], "n": 3}}
        r = subprocess.run([sys.executable, TP, "screen-render"],
                           input=json.dumps(ev), capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ))
        self.assertEqual(r.returncode, 0)


class TheRenderIsRecordedAsAFact(_Ws):

    def test_the_hook_records_the_content_fingerprint(self):
        self.render("<div>hello</div>")
        rows = [r for r in obligations.read(self.ws)
                if r.get("event") == "observed"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fingerprint"],
                         obligations.content_fingerprint("<div>hello</div>"))
        self.assertTrue(rows[0]["observed"])
        self.assertEqual(rows[0]["tool"], "mcp__visualize__show_widget")

    def test_a_string_fingerprint_equals_the_files_fingerprint(self):
        """The whole comparison rests on this. If it drifts, nothing works."""
        body = "<div>ENGINE BUILT THIS</div>"
        path = self.artifact("a.html", body)
        self.assertEqual(obligations.content_fingerprint(body),
                         obligations.artifact_fingerprint(path))

    def test_the_longest_string_argument_is_fingerprinted(self):
        """Payload keys differ by tool; pinning one name would silently stop
        recording the day it is renamed."""
        ev = {"tool_name": "mcp__visualize__show_widget", "cwd": self.ws,
              "tool_input": {"title": "t", "some_future_name": "X" * 50}}
        subprocess.run([sys.executable, TP, "screen-render"],
                       input=json.dumps(ev), capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ))
        row = [r for r in obligations.read(self.ws)
               if r.get("event") == "observed"][0]
        self.assertEqual(row["fingerprint"],
                         obligations.content_fingerprint("X" * 50))
        self.assertEqual(row["bytes"], 50)

    def test_the_observation_is_recorded_with_no_obligation_at_all(self):
        """A render nobody demanded is still a fact worth having."""
        self.render("<div>unprompted</div>")
        self.assertEqual(obligations.status(self.ws)["observed"], 1)


class TheThreeFailuresSeparate(_Ws):

    def setUp(self):
        super().setUp()
        self.body = "<div>ENGINE BUILT THIS</div>"
        self.art = self.artifact("dash.html", self.body)
        self.oid = obligations.issue(
            self.ws, "render_dashboard", detail="show the board", step="em",
            artifact=self.art, key=".taskplane/dash.html")

    def test_skipped_is_an_open_obligation_with_no_observation(self):
        s = obligations.status(self.ws)
        self.assertEqual(len(s["open"]), 1)
        self.assertEqual(s["observed"], 0)
        self.assertEqual(len(s["substituted"]), 0)

    def test_a_substitute_is_observed_and_named_as_one(self):
        """'this is not the graph we designed', made checkable."""
        self.render("<div>MY OWN HAND-DRAWN CHART</div>")
        s = obligations.status(self.ws)
        self.assertEqual(len(s["substituted"]), 1)
        self.assertEqual(len(s["open"]), 1)      # still not shown
        self.assertEqual(len(s["corroborated"]), 0)

    def test_the_engines_own_bytes_discharge_the_obligation(self):
        self.render(self.body)
        s = obligations.status(self.ws)
        self.assertEqual(len(s["open"]), 0)
        self.assertEqual(len(s["corroborated"]), 1)
        self.assertEqual(len(s["substituted"]), 0)

    def test_an_edited_render_does_not_count_as_shown(self):
        """The render contract says byte-for-byte. One extra space is not."""
        self.render(self.body + " ")
        s = obligations.status(self.ws)
        self.assertEqual(len(s["open"]), 1)
        self.assertEqual(len(s["substituted"]), 1)

    def test_an_ack_with_no_observation_behind_it_is_flagged(self):
        obligations.acknowledge(
            self.ws, self.oid, evidence="I showed it",
            fingerprint=obligations.artifact_fingerprint(self.art))
        self.render("<div>something else entirely</div>")
        s = obligations.status(self.ws)
        self.assertEqual([r["id"] for r in s["claimed_only"]], [self.oid])

    def test_a_corroborated_ack_is_not_flagged(self):
        obligations.acknowledge(
            self.ws, self.oid, evidence="shown",
            fingerprint=obligations.artifact_fingerprint(self.art))
        self.render(self.body)
        s = obligations.status(self.ws)
        self.assertEqual(s["claimed_only"], [])
        self.assertEqual(len(s["corroborated"]), 1)

    def test_no_observations_at_all_never_accuses_an_ack(self):
        """A host that does not screen renders must not make every honest
        ack look unsupported."""
        obligations.acknowledge(
            self.ws, self.oid, evidence="shown",
            fingerprint=obligations.artifact_fingerprint(self.art))
        s = obligations.status(self.ws)
        self.assertEqual(s["claimed_only"], [])
        self.assertEqual(s["observed"], 0)


class TheEngineStaysBlind(_Ws):
    """The deletability contract, restated for the new writer."""

    def test_the_observer_lives_outside_the_engine(self):
        engine = ("loop.py", "taskplane_lite.py", "lens.py", "evidence.py",
                  "audit.py", "dashboard.py", "depgraph.py")
        for name in engine:
            path = os.path.join(ROOT, "taskplane", name)
            with io.open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("obligations.observe", src, name)
            self.assertNotIn("obligations.status", src, name)

    def test_an_observation_blocks_no_gate(self):
        import loop
        self.assertTrue(hasattr(loop, "gate"))
        for _ in range(3):
            self.render("<div>whatever</div>")
        self.assertEqual(obligations.status(self.ws)["observed"], 3)
        # nothing above raised, refused, or wrote engine state
        self.assertFalse(os.path.exists(
            os.path.join(self.ws, ".taskplane", "active_contract.json")))


class TheHookIsWired(unittest.TestCase):

    def test_the_matcher_reaches_the_render_tool(self):
        import re
        with io.open(os.path.join(ROOT, "hooks", "hooks.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        entries = cfg["hooks"]["PreToolUse"]
        hit = [e for e in entries
               if re.match(e["matcher"], "mcp__visualize__show_widget")]
        self.assertTrue(hit, "no PreToolUse matcher reaches the render tool")
        self.assertTrue(any("screen-render" in h.get("command", "")
                            for e in hit for h in e["hooks"]))

    def test_the_matcher_does_not_swallow_unrelated_tools(self):
        import re
        with io.open(os.path.join(ROOT, "hooks", "hooks.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        render = [e for e in cfg["hooks"]["PreToolUse"]
                  if "screen-render" in json.dumps(e)]
        for tool in ("Write", "Bash", "Task", "mcp__other__thing"):
            for e in render:
                self.assertIsNone(re.match(e["matcher"], tool), tool)

    def test_the_windows_form_fails_OPEN(self):
        """The dispatch hook fails closed with exit 2 when the plugin root is
        unset. Copying that here would let a missing env var DENY a render."""
        with io.open(os.path.join(ROOT, "hooks", "hooks.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        for e in cfg["hooks"]["PreToolUse"]:
            if "screen-render" not in json.dumps(e):
                continue
            win = e["hooks"][0].get("commandWindows", "")
            self.assertIn("screen-render", win)
            self.assertIn("exit /b 0", win)
            self.assertNotIn("exit /b 2", win)


if __name__ == "__main__":
    unittest.main()
