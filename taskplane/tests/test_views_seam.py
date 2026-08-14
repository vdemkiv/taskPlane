"""The view is not the engine's job, and it must not fail in silence (D-0011).

`loop._with_dashboard` wrapped every state transition, rendered the
dashboard, published the gate snapshot, and put all of it under
`except Exception: pass`.

That is not fail-open. It is fail-SILENT, and the difference is the whole
finding. The transition payload TELLS the human the dashboard was "refreshed
for this transition"; when rendering threw, the key was simply absent. No
error, no trace, no warning — a transition that looked completely healthy
while the artifact the human was told to govern through was stale or
missing. It is the exact shape of the most-repeated complaint against this
product ("no inline dashboard visualisation, no report, nothing"), and by
construction it left nothing behind to diagnose.

The layering half was debt this codebase had been recording against itself
since v2.3.0, in a comment inside the function: "rendering/publishing belongs
in the CLI/driver layer". It does. `views.py` is that extraction, and the
import cycle it was working around (`dashboard` -> `loop`) is now genuinely
broken rather than smuggled into function bodies.

Every assertion here was observed FAILING before it was kept.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import views  # noqa: E402


class _Ws(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-view-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = tempfile.mkdtemp(prefix="tp-view-ws-")
        open(os.path.join(self.ws, "a.py"), "w", encoding="utf-8").write("x=1\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=self.ws, capture_output=True)
        loop.init(self.ws, "view goal")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


class TestAFailedRenderIsReported(_Ws):
    def _break_renderer(self):
        import dashboard
        original = dashboard.report_widget

        def boom(*a, **k):
            raise RuntimeError("renderer exploded")
        dashboard.report_widget = boom
        self.addCleanup(setattr, dashboard, "report_widget", original)

    def test_the_payload_carries_the_error(self):
        self._break_renderer()
        out: dict = {}
        views.refresh_views(self.ws, out)
        self.assertIn("error", out["dashboard"])
        self.assertIn("renderer exploded", out["dashboard"]["error"])

    def test_the_payload_stops_claiming_the_view_is_current(self):
        """The dangerous half. A caller that reads `render` and relays it is
        telling the human to look at something that was never refreshed."""
        self._break_renderer()
        out: dict = {}
        views.refresh_views(self.ws, out)
        self.assertIn("STALE", out["dashboard"]["render"])
        self.assertNotIn("refreshed for this transition",
                         out["dashboard"]["render"])

    def test_the_failure_reaches_the_audit_trace(self):
        self._break_renderer()
        views.refresh_views(self.ws, {})
        import json
        events = []
        for p in tp.trace_paths(self.ws):
            with open(p, encoding="utf-8") as f:
                events += [json.loads(l) for l in f if l.strip().startswith("{")]
        self.assertTrue(
            any(e.get("event") == "dashboard_render_failed" for e in events))

    def test_a_broken_renderer_still_cannot_break_the_transition(self):
        """The property the bare `except` was protecting is kept: the state
        machine advances, the payload is returned, nothing raises."""
        self._break_renderer()
        views._VIEW_FAILED_WARNED = True     # keep the stderr warning quiet
        out = loop.next_action(self.ws)
        self.assertIsInstance(out, dict)
        self.assertNotIn("error", out)
        self.assertIn("error", out["dashboard"])

    def test_a_healthy_render_says_so(self):
        out: dict = {}
        views.refresh_views(self.ws, out)
        self.assertNotIn("error", out["dashboard"])
        self.assertIn("refreshed for this transition",
                      out["dashboard"]["render"])
        self.assertTrue(os.path.isfile(
            os.path.join(tp.tp_dir(self.ws), "dashboard.html")))
        with open(os.path.join(tp.tp_dir(self.ws), "dashboard.html"),
                  encoding="utf-8") as f:
            doc = f.read()
        self.assertTrue(doc.startswith("<!DOCTYPE html>"))
        self.assertIn("--changed-bg", doc)
        self.assertIn('id="tp-workflow-flow"', doc)


class TestTheSeamIsRealNotCosmetic(unittest.TestCase):
    def test_views_does_not_import_loop_at_module_scope(self):
        """`dashboard` imports `loop` at its top. If `views` did too, the
        cycle would only have moved rather than closed — and the old code's
        every-import-inside-a-function-body style is what that costs."""
        import ast
        tree = ast.parse(open(views.__file__, encoding="utf-8").read())
        # AST, not a line scan: this module's own prose mentions `loop.load`
        # and a text match would read a docstring as an import.
        names = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        self.assertNotIn("loop", names,
                         f"views imports loop at module scope: {names}")

    def test_the_engine_no_longer_carries_the_renderer(self):
        src = open(loop.__file__, encoding="utf-8").read()
        self.assertNotIn("_dash.widget(ws)", src)
        self.assertNotIn("except Exception:\n            pass\n        return out",
                         src)

    def test_the_transition_and_its_presentation_are_separable(self):
        """`gate`/`next_action` are wrapped, so the raw engine function is
        reachable and testable without a renderer at all."""
        for name in ("gate", "submit", "next_action", "approve", "retro"):
            fn = getattr(loop, name)
            self.assertTrue(hasattr(fn, "__wrapped__"), name)


if __name__ == "__main__":
    unittest.main()
