"""v1.2.0 coverage extension (R-0002 decision registry + R-0003 lenses).

R-0002: structured ADR registry — lifecycle, alternatives w/ trade-offs,
        links, supersede chains; governing decisions ALWAYS injected into
        briefs whose task scope overlaps the decision's modules; dashboard.
R-0003: three design lenses — tradeoffs, services-selection, time-to-market —
        catalog 26, routed correctly, prompts carry the D-record instruction.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import kb  # noqa: E402
import lens  # noqa: E402
import loop  # noqa: E402

TPPY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tp.py")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


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


def _tp(ws, *args):
    return subprocess.run([sys.executable, TPPY, *args, "--workspace", ws],
                          capture_output=True, text=True)


class TestRegistry(unittest.TestCase):            # R-0002 AC1
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)

    def test_new_with_alternatives_links_and_show(self):
        r = _tp(self.ws, "decision", "new", "Use SQLite for MVP",
                "--status", "proposed",
                "--decision", "SQLite until multi-writer load appears",
                "--alternative", "SQLite | zero ops, fast start | "
                "single-writer ceiling",
                "--alternative", "Postgres | scale headroom | ops load now",
                "--req", "R-0002", "--modules", "src/db/**,src/models/**")
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "proposed")
        self.assertEqual(out["alternatives"], 2)
        did = out["recorded"]
        d = kb.get_decision(self.ws, did)
        self.assertEqual(d["links"]["requirement"], "R-0002")
        self.assertIn("src/db/**", d["links"]["modules"])
        body = open(os.path.join(kb.kb_dir(self.ws), d["file"])).read()
        self.assertIn("given up: single-writer ceiling", body)

    def test_accept_lifecycle_and_supersede_chain(self):
        a = json.loads(_tp(self.ws, "decision", "new", "old way").stdout)
        _tp(self.ws, "decision", "accept", a["recorded"])
        self.assertEqual(kb.get_decision(self.ws, a["recorded"])["status"],
                         "accepted")
        b = json.loads(_tp(self.ws, "decision", "new", "new way",
                           "--supersedes", a["recorded"]).stdout)
        old = kb.get_decision(self.ws, a["recorded"])
        self.assertIn("superseded-by-" + b["recorded"], old["status"])
        # append-only: the old file still exists
        self.assertTrue(os.path.exists(
            os.path.join(kb.kb_dir(self.ws), old["file"])))

    def test_list_filters_by_status(self):
        _tp(self.ws, "decision", "new", "p1", "--status", "proposed")
        _tp(self.ws, "decision", "new", "a1")
        ls = json.loads(_tp(self.ws, "decision", "list",
                            "--status", "proposed").stdout)
        self.assertEqual([d["title"] for d in ls], ["p1"])


class TestGoverningInjection(unittest.TestCase):  # R-0002 AC2+AC3
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        _tp(self.ws, "decision", "new", "src is sacred",
            "--modules", "src/**")            # accepted by default
        _tp(self.ws, "decision", "new", "docs rule",
            "--modules", "docs/**")           # non-overlapping
        _tp(self.ws, "decision", "new", "proposed only",
            "--modules", "src/**", "--status", "proposed")

    def test_governing_matches_scope_accepted_only(self):
        gov = kb.governing(self.ws, ["src/a/**"])
        self.assertEqual([d["title"] for d in gov], ["src is sacred"])

    def test_brief_carries_governing_decisions(self):
        loop.init(self.ws, "g")
        loop.gate(self.ws, "pass")            # pm -> plan
        st = loop.load(self.ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "tests": "true",
                              "criteria": ["governing decisions injected"],
                              "status": "pending"}]})
        loop.save(self.ws, st)
        out = loop.next_action(self.ws)
        gov = out["knowledge"]["governing_decisions"]
        self.assertEqual([d["title"] for d in gov], ["src is sacred"])

    def test_dashboard_shows_governing(self):
        loop.init(self.ws, "g")
        st = loop.load(self.ws)
        st.update({"step": "execute", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "pending"}]})
        loop.save(self.ws, st)
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-governing", frag)
        self.assertIn("src is sacred", frag)


class TestNewLenses(unittest.TestCase):           # R-0003
    def test_catalog_has_26_with_solution_design(self):
        cat = json.load(open(os.path.join(ROOT, "lenses", "catalog.json")))
        lenses = cat["lenses"] if isinstance(cat, dict) else cat
        ids = {x["id"] for x in lenses}
        self.assertEqual(len(lenses), 26)
        self.assertTrue({"tradeoffs", "services-selection",
                         "time-to-market"} <= ids)
        self.assertIn("solution-design", ids)
        self.assertNotEqual(
            next(x for x in lenses if x["id"] == "solution-design")["charter"],
            next(x for x in lenses if x["id"] == "design")["charter"],
        )

    def test_router_fires_services_selection_on_manifest(self):
        r = lens.route(["package.json"])
        self.assertIn("services-selection", [x["id"] for x in r["lenses"]])

    def test_router_fires_time_to_market_on_plan(self):
        r = lens.route(["plan/tasks.json"])
        self.assertIn("time-to-market", [x["id"] for x in r["lenses"]])

    def test_router_fires_tradeoffs_deep_on_design(self):
        r = lens.route(["design/checkout.arch.md",
                        "architecture/flow.md"])
        hit = next(x for x in r["lenses"] if x["id"] == "tradeoffs")
        self.assertEqual(hit.get("mode", "inline"), "subagent")

    def test_prompts_exist_with_registry_instruction(self):
        for lid in ("tradeoffs", "services-selection", "time-to-market"):
            p = os.path.join(ROOT, "lenses", f"{lid}.md")
            self.assertTrue(os.path.exists(p), lid)
        t = open(os.path.join(ROOT, "lenses", "tradeoffs.md")).read()
        self.assertIn("tp decision new", t)
        ttm = open(os.path.join(ROOT, "lenses", "time-to-market.md")).read()
        self.assertIn("cut SCOPE, not floors", ttm)


if __name__ == "__main__":
    unittest.main()
