"""Product layer of the dependency graph: req nodes, planned/realizes
links, product depends-edges, product_impact — and the loop wiring that
maintains them (plan-gate annotation, EM true-up)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402
import requirements  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True, check=False)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-prodgraph-")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    os.makedirs(os.path.join(ws, "src/api"))
    os.makedirs(os.path.join(ws, "src/web"))
    open(os.path.join(ws, "src/api/orders.py"), "w", encoding="utf-8").write("import db\n")
    open(os.path.join(ws, "src/api/db.py"), "w", encoding="utf-8").write("x = 1\n")
    open(os.path.join(ws, "src/web/home.js"), "w", encoding="utf-8").write("const a = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    depgraph.scan(ws)
    return ws


class TestProductLayer(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()
        self.addCleanup(shutil.rmtree, self.ws)

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
        requirement = requirements.record_requirement(
            self.ws, "API change", acceptance=["API remains correct"])
        state = {"tasks": [{"id": "t1", "req": requirement["id"],
                            "scope": ["src/api/**"]}]}
        loop._annotate_plan_graph(self.ws, state)
        blast = state["tasks"][0].get("blast")
        self.assertIsNotNone(blast)
        self.assertIn("req:R-0009", blast["shared_with"])
        # and the planned link for the task's own requirement exists
        g = depgraph.load(self.ws)
        self.assertTrue(any(e["from"] == "req:" + requirement["id"]
                            and e["kind"] == "planned"
                            for e in g["edges"]))


@pytest.mark.parametrize("parallel", [False, True], ids=["serial", "parallel"])
def test_graph_evidence_requires_dependents_and_requirements_in_each_mode(tmp_path, parallel):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    for folder, text in (("provider", "VALUE = 1\n"), ("consumer", "from provider import value\n"),
                         ("src/design", "from provider import value\n")):
        target = workspace / folder / "value.py"
        target.parent.mkdir(parents=True)
        target.write_text(text)
    for path in ("design/contract.json", "plan/tasks.json", "waves/execute/T1.json"):
        target = workspace / path
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"source": "provider/value.py"}))
    ws = str(workspace)
    _git(ws, "init", "-q")
    _git(ws, "add", ".")
    _git(ws, "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", "commit", "-qm", "source")
    baseline = loop.tp.git_head(ws)
    depgraph.scan(ws)
    depgraph.link_requirement(ws, "R-OTHER", ["provider/value.py"], kind="realizes")
    (workspace / "provider/value.py").write_text("VALUE = 2\n")
    depgraph.scan(ws)
    task = {"id":"T1", "scope":["provider/value.py"], "contracts":["contract:value"]}
    state = {"parallel":parallel, "baseline":baseline, "graph_governance":True, "requirement_id":"R-OWN"}
    errors = loop._task_graph_evidence_errors(ws, state, task, {})
    assert "graph impact has no evidenced disposition: consumer" in errors
    # src/design shares the module ID with design/contract.json; mixed
    # modules still require product evidence, regardless of their name.
    assert "graph impact has no evidenced disposition: design" in errors
    impacted = loop._task_graph_dod(ws, state, task)["impact"]["impacted"][1]
    assert {"design", "plan", "waves/execute"} <= {row["module"] for row in impacted}
    assert "affected requirement was not re-checked: req:R-OTHER" in errors
    assert "declared contract was not verified: contract:value" in errors
    graph = {"dispositions":[{"node":node, "status":"tested", "evidence":"Consumer regression passed"}
                            for node in ("consumer", "design")],
        "requirements_checked":["req:R-OTHER"], "contracts_checked":["contract:value"]}
    assert loop._task_graph_evidence_errors(ws, state, task, {"graph":graph}) == []
    graph["dispositions"][0]["status"] = "requires-replan"
    assert "graph impact requires replanning: consumer" in loop._task_graph_evidence_errors(ws, state, task, {"graph":graph})


if __name__ == "__main__":
    unittest.main()
