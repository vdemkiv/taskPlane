"""DoR/DoD visibility wiring (v1.0.0):
- DoR is computed every loop step and now traced with blockers/warnings, and the
  dashboard renders a DoR strip from it.
- The mechanical DoD (scope-diff + KB-lint) now runs at the sign-off gate
  (loop._signoff_dod) and is surfaced in the next_action payload + dashboard.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import dashboard  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    open(os.path.join(ws, "README.md"), "w").write("# readme\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _head(ws):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          capture_output=True, text=True).stdout.strip()


class TestSignoffDoD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, base, scope=("src/**",)):
        return {"goal": "g", "step": "signoff", "current_task": 0,
                "max_fix_cycles": 2, "checkpoints": ["plan", "em"],
                "tasks": [{"id": "t1", "scope": list(scope)}],
                "baseline": base}

    def test_in_scope_change_passes(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "src", "a.py"), "w").write("x = 2\n")  # in scope
        d = loop._signoff_dod(ws, self._state(base))
        self.assertTrue(d["passed"], d["errors"])

    def test_out_of_scope_change_fails(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "README.md"), "w").write("# changed\n")  # NOT src
        d = loop._signoff_dod(ws, self._state(base))
        self.assertFalse(d["passed"])
        self.assertTrue(any("diff_scope" in e for e in d["errors"]), d["errors"])

    def test_kb_lint_folds_into_dod(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        ctx = os.path.join(tp.kb_root(ws), "context")   # isolated by conftest
        os.makedirs(ctx, exist_ok=True)
        open(os.path.join(ctx, "product.md"), "w").write("Paid SKU ~15k/yr\n")
        d = loop._signoff_dod(ws, self._state(base))
        self.assertFalse(d["passed"])
        self.assertTrue(any("kb_lint" in e for e in d["errors"]), d["errors"])


class TestPayloadAndTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_signoff_next_action_carries_dod(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        loop.init(ws, "g")
        st = loop.load(ws)
        st.update({"step": "signoff",
                   "tasks": [{"id": "t1", "scope": ["src/**"]}],
                   "baseline": base})
        loop.save(ws, st)
        out = loop.next_action(ws)
        self.assertEqual(out["step"], "signoff")
        self.assertIn("dod", out)
        self.assertIn("passed", out["dod"])

    def test_loop_step_trace_carries_dor_detail(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")          # pm
        loop.gate(ws, "pass")       # pm -> plan
        loop.next_action(ws)        # plan step -> traces loop_step + DoR detail
        tr = [json.loads(ln) for ln in
              open(os.path.join(tp.tp_dir(ws), "trace.jsonl")) if ln.strip()]
        steps = [e for e in tr if e.get("event") == "loop_step"]
        self.assertTrue(steps)
        self.assertIn("dor_ready", steps[-1])
        self.assertIn("dor_blockers", steps[-1])


class TestDashboardSurfaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_widget_shows_dor_strip_and_dod_verdict(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        loop.init(ws, "g")
        loop.gate(ws, "pass")       # -> plan
        loop.next_action(ws)        # emits a loop_step trace (DoR)
        st = loop.load(ws)
        st.update({"step": "signoff",
                   "tasks": [{"id": "t1", "scope": ["src/**"]}],
                   "baseline": base})
        loop.save(ws, st)
        frag = dashboard.widget(ws)
        self.assertIn("DoR", frag)   # entry-gate strip
        self.assertIn("DoD", frag)   # sign-off verdict


if __name__ == "__main__":
    unittest.main()
