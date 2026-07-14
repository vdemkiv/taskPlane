"""Product layer of the dependency graph: req nodes, planned/realizes
links, product depends-edges, product_impact — and the loop wiring that
maintains them (plan-gate annotation, EM true-up)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True, check=False)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-prodgraph-")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    os.makedirs(os.path.join(ws, "src/api"))
    os.makedirs(os.path.join(ws, "src/web"))
    open(os.path.join(ws, "src/api/orders.py"), "w").write("import db\n")
    open(os.path.join(ws, "src/api/db.py"), "w").write("x = 1\n")
    open(os.path.join(ws, "src/web/home.js"), "w").write("const a = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    depgraph.scan(ws)
    return ws


class TestProductLayer(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()

    def test_link_requirement_and_replace(self):
        r = depgraph.link_requirement(
            self.ws, "R-0001", ["src/api/orders.py"], kind="planned")
        self.assertEqual(r["requirement"], "req:R-0001")
        self.assertTrue(r["modules"])
        # true-up replaces the planned view of the SAME kind only
        depgraph.link_requirement(
            self.ws, "R-0001", ["src/web/home.js"], kind="planned")
        g = depgraph.load(self.ws)
        planned = [e for e in g["edges"] if e["from"] == "req:R-0001"
                   and e["kind"] == "planned"]
        self.assertEqual(len(planned), 1)
        self.assertIn("web", planned[0]["to"])

    def test_scope_globs_map_to_modules(self):
        mods = depgraph.modules_for_scope(["src/api/**", "src/web/home.js"])
        self.assertTrue(any("api" in m for m in mods))
        self.assertTrue(any("web" in m for m in mods))

    def test_links_survive_rescan(self):
        depgraph.link_requirement(
            self.ws, "R-0001", ["src/api/orders.py"], kind="realizes")
        depgraph.scan(self.ws)
        g = depgraph.load(self.ws)
        self.assertTrue(any(e["from"] == "req:R-0001" for e in g["edges"]))

    def test_product_impact_direct_and_dependent(self):
        depgraph.link_requirement(
            self.ws, "R-0001", ["src/api/orders.py"], kind="realizes")
        depgraph.link_requirement_dep(self.ws, "R-0002", "R-0001")
        p = depgraph.product_impact(self.ws, ["src/api/orders.py"])
        self.assertIn("req:R-0001", p["affected_requirements"])
        self.assertIn("req:R-0002", p["dependent_requirements"])
        # an unrelated change touches no requirement surface
        p2 = depgraph.product_impact(self.ws, ["src/web/home.js"])
        self.assertEqual(p2["affected_requirements"], [])

    def test_plan_gate_annotates_blast_and_shared_surface(self):
        # another requirement already realizes the api surface
        depgraph.link_requirement(
            self.ws, "R-0009", ["src/api/**"], kind="realizes")
        loop.init(self.ws, "goal")
        state = loop.load(self.ws)
        state["step"] = "plan"
        loop.save(self.ws, state)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        json.dump({"tasks": [{"id": "t1", "req": "R-0010",
                              "scope": ["src/api/**"], "tests": "true"}]},
                  open(os.path.join(self.ws, "plan", "tasks.json"), "w"))
        loop.gate(self.ws, "pass")
        state = loop.load(self.ws)
        blast = state["tasks"][0].get("blast")
        self.assertIsNotNone(blast)
        self.assertIn("req:R-0009", blast["shared_with"])
        # and the planned link for the task's own requirement exists
        g = depgraph.load(self.ws)
        self.assertTrue(any(e["from"] == "req:R-0010"
                            and e["kind"] == "planned"
                            for e in g["edges"]))


if __name__ == "__main__":
    unittest.main()
