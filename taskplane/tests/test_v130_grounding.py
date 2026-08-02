"""v1.3.0 coverage — current-state grounding (R-0004).

Design work must be judged as a DELTA against the as-built inventory
(context/current-state.md), never in a vacuum: onboarding seeds the
scaffold, kb.current_state() returns it only when filled, every brief
carries it, the dashboard surfaces it, and the design-family lens prompts
carry the grounding instruction with reinvention/drift as blocker-class.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import kb  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")

FILLED = """# Current state — as-built inventory

- **Built & running (components, who owns them):** controller agent (rule-
  based, live), hardware intercept over Rotem
- **Data & integrations that exist:** edge -> IoT Hub -> blob -> medallion
"""


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _write_state(ws, body):
    ctx = os.path.join(tp.kb_root(ws), "context")
    os.makedirs(ctx, exist_ok=True)
    open(os.path.join(ctx, "current-state.md"), "w").write(body)


class TestCurrentState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)

    def test_missing_file_returns_none(self):
        self.assertIsNone(kb.current_state(self.ws))

    def test_unfilled_scaffold_returns_none(self):
        # the exact scaffold onboarding writes: headings + empty bullets only
        from tp import CURRENT_STATE_MD
        _write_state(self.ws, CURRENT_STATE_MD)
        self.assertIsNone(kb.current_state(self.ws))

    def test_filled_inventory_returned_with_path(self):
        _write_state(self.ws, FILLED)
        cs = kb.current_state(self.ws)
        self.assertIsNotNone(cs)
        self.assertIn("controller agent", cs["text"])
        self.assertIn("current-state.md", cs["path"])

    def test_long_inventory_truncated_at_cap(self):
        _write_state(self.ws, "# Current state\n\nreal line\n" + "x" * 9000)
        cs = kb.current_state(self.ws)
        self.assertLess(len(cs["text"]), kb.CURRENT_STATE_CAP + 200)
        self.assertIn("truncated", cs["text"])

    def test_onboarding_seeds_scaffold(self):
        subprocess.run([sys.executable, TPPY, "init",
                        "--workspace", self.ws],
                       capture_output=True, text=True)
        p = os.path.join(tp.kb_root(self.ws), "context", "current-state.md")
        self.assertTrue(os.path.exists(p))
        self.assertIn("as-built inventory", open(p).read())


class TestBriefAndDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        _write_state(self.ws, FILLED)

    def test_brief_carries_current_state(self):
        loop.init(self.ws, "g")
        st = loop.load(self.ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "pending"}]})
        loop.save(self.ws, st)
        out = loop.next_action(self.ws)
        cs = out["knowledge"]["current_state"]
        self.assertIsNotNone(cs)
        self.assertIn("hardware intercept", cs["text"])

    def test_brief_omits_when_unfilled(self):
        from tp import CURRENT_STATE_MD
        _write_state(self.ws, CURRENT_STATE_MD)
        loop.init(self.ws, "g")
        st = loop.load(self.ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "pending"}]})
        loop.save(self.ws, st)
        out = loop.next_action(self.ws)
        self.assertIsNone(out["knowledge"]["current_state"])

    def test_dashboard_shows_grounding_panel(self):
        loop.init(self.ws, "g")
        st = loop.load(self.ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "pending"}]})
        loop.save(self.ws, st)
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-current-state", frag)
        self.assertIn("as-built inventory", frag)


class TestLensGrounding(unittest.TestCase):
    def test_design_lens_prompts_carry_grounding(self):
        for lid in ("tradeoffs", "architecture", "services-selection",
                    "time-to-market"):
            body = open(os.path.join(ROOT, "lenses", f"{lid}.md")).read()
            self.assertIn("GROUND IN THE CURRENT STATE FIRST", body, lid)
            self.assertIn("REINVENTION", body, lid)

    def test_reinvention_is_blocker_class(self):
        for lid in ("tradeoffs", "architecture"):
            body = open(os.path.join(ROOT, "lenses", f"{lid}.md")).read()
            self.assertIn("as-built inventory", body.lower(), lid)


if __name__ == "__main__":
    unittest.main()
