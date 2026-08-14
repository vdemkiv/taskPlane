"""v1.4.0 — Claude Tag support.

Tag's sandbox is ephemeral and hook-less, and approvers are humans in the
Slack thread. Three mechanisms adapt taskplane to that:
  * TASKPLANE_STORE=repo — the store relocates INSIDE the workspace
    (.taskplane-kb/) so it commits and survives sandbox teardown;
  * `loop approve --by` — every human-gate pass records WHO approved,
    making gates attributable without a hook layer;
  * the tp-tag skill — the thread protocol (never self-approve, post the
    dashboard, resume from the branch).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


class _RepoStore(unittest.TestCase):
    """Base: set TASKPLANE_STORE=repo for the test, restore after."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        self._prev = os.environ.get("TASKPLANE_STORE")
        os.environ["TASKPLANE_STORE"] = "repo"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_STORE", None)
        else:
            os.environ["TASKPLANE_STORE"] = self._prev


class TestRepoStoreMode(_RepoStore):
    def test_store_root_is_inside_workspace(self):
        root = tp.store_root(self.ws)
        self.assertEqual(root, os.path.join(self.ws, ".taskplane-kb"))
        self.assertTrue(tp.kb_root(self.ws).startswith(root))

    def test_decisions_land_in_repo_and_are_committable(self):
        kb.record_decision(self.ws, "tag decision", context="c",
                           decision="d", tags=["t"])
        idx = os.path.join(self.ws, ".taskplane-kb", "knowledge",
                           "index.json")
        self.assertTrue(os.path.exists(idx))
        _git(self.ws, "add", ".taskplane-kb")
        _git(self.ws, "commit", "-qm", "kb")
        r = subprocess.run(["git", "ls-files", ".taskplane-kb"],
                           cwd=self.ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertIn("index.json", r.stdout)   # NOT gitignored — by design

    def test_loop_state_survives_home_teardown(self):
        # simulate sandbox recycling: nuke TASKPLANE_HOME; repo store remains
        loop.init(self.ws, "tag goal")
        home_before = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp()  # "new sandbox"
        try:
            st = loop.load(self.ws)
            self.assertIsNotNone(st)
            self.assertEqual(st["goal"], "tag goal")
        finally:
            if home_before is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = home_before

    def test_external_mode_untouched_without_env(self):
        os.environ.pop("TASKPLANE_STORE", None)
        self.assertNotIn(self.ws, tp.store_root(self.ws))


class TestApproveBy(_RepoStore):
    def _park_at_plan_approval(self):
        loop.init(self.ws, "g")
        st = loop.load(self.ws)
        st.update({"step": "plan_approval",
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "pending"}]})
        loop.save(self.ws, st)

    def _trace(self):
        p = os.path.join(self.ws, ".taskplane", "trace.jsonl")
        return [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []

    def test_approve_by_recorded_in_trace_and_kb(self):
        self._park_at_plan_approval()
        out = loop.approve(self.ws, by="Dana R. — 'approved' in #eng")
        self.assertNotIn("error", out)
        ev = [e for e in self._trace() if e.get("event") == "loop_approve"]
        self.assertTrue(ev and ev[-1].get("by") ==
                        "Dana R. — 'approved' in #eng")
        # the KB decision carries the approver too
        ds = kb.list_decisions(self.ws)
        body = open(os.path.join(kb.kb_dir(self.ws),
                                 ds[-1]["file"]), encoding="utf-8").read()
        self.assertIn("Approved by: Dana R.", body)

    def test_approve_without_by_still_works_but_records_none(self):
        self._park_at_plan_approval()
        loop.approve(self.ws)
        ev = [e for e in self._trace() if e.get("event") == "loop_approve"]
        self.assertTrue(ev)
        # v2.2.1 (L5): an anonymous pass is RECORDED as unattributed —
        # still detectable as self-approval, now explicit in the trail.
        self.assertEqual(ev[-1].get("by"), "(unattributed)")

    @unittest.skipUnless(
        "utf" in sys.getfilesystemencoding().lower(),
        "needs a UTF-8 filesystem encoding: this case carries non-ASCII "
        "through argv/paths, which a C-locale host cannot represent at all "
        "(a harness limit, not a product limit — Windows paths are UTF-16)")
    def test_cli_accepts_by_flag(self):
        self._park_at_plan_approval()
        tppy = os.path.join(ROOT, "taskplane", "tp.py")
        r = subprocess.run([sys.executable, tppy, "loop", "approve",
                            "--by", "Leo — 'ship it'",
                            "--workspace", self.ws],
                           capture_output=True, text=True,
                           env={**os.environ}, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = [e for e in self._trace() if e.get("event") == "loop_approve"]
        self.assertEqual(ev[-1].get("by"), "Leo — 'ship it'")


class TestTagSkill(unittest.TestCase):
    def test_skill_exists_with_protocol(self):
        p = os.path.join(ROOT, "skills", "tp-tag", "SKILL.md")
        self.assertTrue(os.path.exists(p))
        body = open(p, encoding="utf-8").read()
        for must in ("TASKPLANE_STORE=repo", "--by",
                     "Do not run `loop approve`",
                     "Never approves a human gate"):
            self.assertIn(must, body)
        # plugin validation: no angle-bracket tags in the description line
        desc = [ln for ln in body.splitlines()
                if ln.startswith("description:")][0]
        self.assertNotIn("<", desc)

    def test_approved_flow_contract_is_exact_and_attributable(self):
        p = os.path.join(ROOT, "skills", "tp-tag", "flow.json")
        with open(p, encoding="utf-8") as stream:
            flow = json.load(stream)
        self.assertEqual(flow["schema"], "taskplane.skill-flow/v1")
        self.assertEqual(
            [node["id"] for node in flow["nodes"]],
            ["thread", "store", "loop", "work", "dashboard", "reply",
             "approve", "persist"])
        self.assertEqual(
            flow["edges"],
            [["thread", "store"], ["store", "loop"], ["loop", "work"],
             ["work", "dashboard"], ["dashboard", "reply"],
             ["reply", "approve"], ["approve", "persist"]])
        self.assertEqual(flow["invariants"]["store"],
                         "TASKPLANE_STORE=repo")
        self.assertEqual(flow["invariants"]["dashboard"],
                         ".taskplane/dashboard.html")
        self.assertEqual(flow["invariants"]["approval_requires"], "--by")
        self.assertFalse(flow["invariants"]["self_approval"])


if __name__ == "__main__":
    unittest.main()
