"""v2.0.0 — the graph is CONSUMED, not just produced.

Review findings: the dependency graph informed the
judges (evaluate/em) but not the actors. Fixed and pinned here:
  F1 architecture routing escalates on graph hubness (a hub edit is an
     architecture event whatever its path looks like);
  F2 execute/fix briefs carry the blast radius — side effects prevented
     at build time, not detected a loop-step later;
  F3 dispatched lens briefs embed the impact context;
  F4 loop init + onboarding surface prior artifact snapshots (the cache);
  F5 evaluate/em briefs nudge recording runtime edges the import scanner
     cannot see (SQL/migrations, HTTP, messaging).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import lens  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], cwd=ws, check=True)


def _hub_repo():
    """core/hub.py imported by three feature modules -> hub has 3 dependents."""
    ws = tempfile.mkdtemp(prefix="tp-hub-")
    os.makedirs(os.path.join(ws, "src", "core"))
    open(os.path.join(ws, "src", "core", "hub.py"), "w", encoding="utf-8").write("X = 1\n")
    for feat in ("alpha", "beta", "gamma"):
        os.makedirs(os.path.join(ws, "src", feat))
        open(os.path.join(ws, "src", feat, "m.py"), "w", encoding="utf-8").write(
            "from core import hub\n")
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    depgraph.scan(ws)
    return ws


class _Env(unittest.TestCase):
    def setUp(self):
        # t9 (R-0011 E2): save-and-restore both vars — the old tearDown
        # popped HOME and never restored STORE, so an exported
        # TASKPLANE_STORE vanished for every LATER test module.
        self._env0 = {k: os.environ.get(k)
                      for k in ("TASKPLANE_HOME", "TASKPLANE_STORE")}
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-h-")
        os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        for k, v in self._env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestF1HubEscalation(_Env):
    def test_effort_escalates_with_dependents(self):
        f = ["core/billing.py"]
        self.assertEqual(lens.architecture_effort(f, "fix", False), "skip")
        self.assertEqual(
            lens.architecture_effort(f, "fix", False, hub_dependents=3),
            "light")
        self.assertEqual(
            lens.architecture_effort(f, "fix", False, hub_dependents=8),
            "full")

    def test_route_names_the_hub_reason(self):
        r = lens.route(["core/billing.py"], task_type="fix",
                       hub_dependents=9)
        arch = [x for x in r["lenses"] if x["id"] == "architecture"][0]
        self.assertTrue(any("dependents" in reason
                            for reason in arch["reasons"]))

    def test_route_git_diff_reads_the_real_graph(self):
        ws = _hub_repo()
        # a one-line edit to the hub — no arch-ish path anywhere
        open(os.path.join(ws, "src", "core", "hub.py"), "a", encoding="utf-8").write("Y = 2\n")
        r = lens.route_git_diff(ws, base="HEAD", task_type="fix")
        self.assertGreaterEqual(r["context"]["hub_dependents"], 3)
        arch = [x for x in r["lenses"] if x["id"] == "architecture"][0]
        self.assertTrue(any("dependents" in reason
                            for reason in arch["reasons"]))


class TestF2BuilderImpact(_Env):
    def test_execute_brief_carries_blast_radius(self):
        ws = _hub_repo()
        loop.init(ws, "g")
        st = loop.load(ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/core/**"],
                              "tests": "true", "criteria": ["works"],
                              "status": "pending", "fix_cycles": 0}]})
        loop.save(ws, st)
        out = loop.next_action(ws)
        self.assertNotIn("error", out)
        self.assertIsNotNone(out.get("impact"))
        self.assertGreaterEqual(out["impact"]["total_impacted"], 3)


class TestF3BriefImpact(_Env):
    def test_dispatch_briefs_embed_impact_context(self):
        ws = _hub_repo()
        open(os.path.join(ws, "src", "core", "hub.py"), "a", encoding="utf-8").write("Z = 3\n")
        routing = lens.route_git_diff(ws, base="HEAD", breadth="all")
        self.assertIn("files", routing["context"])
        briefs = lens.dispatch_briefs(
            routing, base="HEAD",
            impact_context="touches core; depth 1: alpha, beta, gamma")
        arch = [b for b in briefs["deep"] if b["id"] == "architecture"]
        target = arch[0] if arch else briefs["deep"][0]
        self.assertIn("BLAST RADIUS", target["prompt"])
        self.assertIn("alpha, beta, gamma", target["prompt"])


class TestF4CacheDiscoverability(_Env):
    def test_loop_init_points_at_prior_snapshots(self):
        ws = _hub_repo()
        art = os.path.join(tp.store_root(ws), "artifacts", "old-track")
        os.makedirs(art)
        open(os.path.join(art, "HEADLINES.md"), "w", encoding="utf-8").write("# log\n")
        out = loop.init(ws, "new goal")
        self.assertIn("prior_artifacts", out)
        self.assertIn("old-track", out["prior_artifacts"]["tracks"])

    def test_onboard_report_lists_the_cache(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        import tp as cli
        ws = _hub_repo()
        art = os.path.join(tp.store_root(ws), "artifacts", "t1")
        os.makedirs(art)
        open(os.path.join(art, "dashboard.html"), "w", encoding="utf-8").write("<div/>")
        r = cli._onboard_report(ws)
        self.assertIsNotNone(r["artifacts"])
        self.assertIn("t1", r["artifacts"]["tracks"])


class TestF5EdgeNudges(_Env):
    def test_sql_and_http_diffs_produce_nudges(self):
        ws = _hub_repo()
        os.makedirs(os.path.join(ws, "migrations"))
        open(os.path.join(ws, "migrations", "001_add.sql"), "w", encoding="utf-8").write(
            "ALTER TABLE x ADD y INT;\n")
        open(os.path.join(ws, "src", "alpha", "client.py"), "w", encoding="utf-8").write(
            "import requests\nrequests.get('https://api.example.com/v1')\n")
        _git(ws, "add", "-A")
        changed = ["migrations/001_add.sql", "src/alpha/client.py"]
        nudges = loop._edge_nudges(ws, changed, "HEAD")
        joined = " ".join(nudges)
        self.assertIn("SQL/migrations", joined)
        self.assertIn("HTTP", joined)
        self.assertTrue(all("tp graph edge" in n for n in nudges))

    def test_plain_code_diff_produces_no_nudges(self):
        ws = _hub_repo()
        open(os.path.join(ws, "src", "beta", "m.py"), "a", encoding="utf-8").write("A = 1\n")
        self.assertEqual(
            loop._edge_nudges(ws, ["src/beta/m.py"], "HEAD"), [])


if __name__ == "__main__":
    unittest.main()
