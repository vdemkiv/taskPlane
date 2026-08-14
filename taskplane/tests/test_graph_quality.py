"""R-0005 graph-quality gate and single bounded caller expansion."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graph_quality as quality  # noqa: E402
import depgraph  # noqa: E402
import runnability


def graph(**meta):
    base = {
        "content_fingerprint": "graph-1",
        "scanned_head": "abc1234",
        "scanners": {"python": {"coverage": "complete", "files": 2}},
    }
    base.update(meta)
    return {
        "files": {"src/a.py": {"hash": "a"}, "src/b.py": {"hash": "b"}},
        "modules": {"src/a": {"files": 1}, "src/b": {"files": 1}},
        "edges": [],
        "meta": base,
    }


class TestGraphQualityRecord(unittest.TestCase):
    def test_policy_limited_impact_is_complete_when_depth_is_exhausted(self):
        record = quality.assess(
            graph(), target_head="abc1234", changed_files=["src/a.py"],
            changed_symbols=["src/a.py::changed"],
            impact={
                "touched": ["src/a"], "unknown": [], "truncated": True,
                "depth_truncated": False,
                "policy_blocked": [{
                    "module": "req:R-0002", "via": "req:R-0001",
                    "kind": "depends", "reason": "requirement-depth",
                }],
            })

        self.assertEqual(record["status"], "complete")
        self.assertFalse(record["truncated"])
        self.assertTrue(record["policy_limited"])
        self.assertEqual(record["impact"]["policy_blocked"][0]["reason"],
                         "requirement-depth")

    def test_python_coverage_shape_emitted_by_scanner_is_supported(self):
        produced = graph(scanners={})
        record = quality.assess(
            produced, target_head="abc1234", changed_files=["src/a.py"],
            changed_symbols=["src/a.py::changed"],
            impact={"touched": ["src/a"], "unknown": [],
                    "truncated": False})
        self.assertEqual(record["scanner_coverage"], [
            {"language": "python", "coverage": "complete", "relevant": True}
        ])
        self.assertEqual(record["status"], "complete")

    def test_complete_input_records_every_pre_routing_fact(self):
        record = quality.assess(
            graph(), target_head="abc1234", changed_files=["src/a.py"],
            changed_symbols=["src/a.py::changed"],
            impact={"touched": ["src/a"], "unknown": [], "truncated": False})
        self.assertEqual(record["status"], "complete")
        self.assertTrue(record["sufficient"])
        self.assertEqual(record["scanner_coverage"][0]["language"], "python")
        self.assertEqual(record["unresolved_internal_edges"], [])
        self.assertFalse(record["stale"])
        self.assertFalse(record["truncated"])
        self.assertEqual(record["module_confidence"], "high")
        self.assertIn("changed_symbol_caller_coverage", record)
        self.assertIn("fingerprint", record)

    def test_sparse_module_graph_runs_one_bounded_expansion_and_merges_it(self):
        calls = []

        def expand(*, snapshot, changed_symbols, bounds):
            calls.append((snapshot, tuple(changed_symbols), dict(bounds)))
            return {"callers": ["src/b.py::caller"],
                    "contracts": ["contract:userdata"],
                    "unresolved": [], "complete": True, "edges_examined": 1}

        record = quality.assess(
            graph(), target_head="abc1234", changed_files=["src/a.py"],
            changed_symbols=["src/a.py::changed"],
            impact={"touched": ["src/a"], "unknown": [], "truncated": False,
                    "module_confidence": "low"},
            caller_expander=expand, snapshot={"head": "abc1234"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(record["expansion"]["count"], 1)
        self.assertEqual(record["expansion"]["bounds"], quality.DEFAULT_CALLER_BOUNDS)
        self.assertEqual(record["changed_symbol_caller_coverage"]["callers"],
                         ["src/b.py::caller"])
        self.assertEqual(record["contracts"], ["contract:userdata"])
        self.assertEqual(record["status"], "complete")

    def test_unresolved_expansion_fails_closed_with_zero_dispatch(self):
        record = quality.assess(
            graph(), target_head="abc1234", changed_files=["src/a.py"],
            changed_symbols=["src/a.py::changed"],
            impact={"touched": ["src/a"], "unknown": ["src/missing"],
                    "truncated": False, "module_confidence": "low"},
            caller_expander=lambda **_: {
                "callers": [], "contracts": [], "unresolved": ["changed"],
                "complete": False, "truncated": True})
        self.assertEqual(record["status"], "impact_incomplete")
        self.assertFalse(record["sufficient"])
        self.assertEqual(quality.dispatch_manifest(record),
                         {"status": "impact_incomplete", "slots": [],
                          "briefs": [], "agents": [], "breadth": None})

    def test_stale_graph_cannot_be_rescued_by_expansion(self):
        called = []
        record = quality.assess(
            graph(scanned_head="old"), target_head="abc1234",
            changed_files=["src/a.py"], changed_symbols=["changed"],
            impact={"touched": ["src/a"], "unknown": [], "truncated": False},
            caller_expander=lambda **_: called.append(True) or {"complete": True})
        self.assertEqual(record["status"], "impact_incomplete")
        self.assertEqual(called, [], "stale snapshots must be repaired, not expanded")

    def test_same_inputs_have_the_same_fingerprint(self):
        kw = dict(target_head="abc1234", changed_files=["src/a.py"],
                  changed_symbols=["changed"],
                  impact={"touched": ["src/a"], "unknown": [],
                          "truncated": False})
        self.assertEqual(quality.assess(graph(), **kw)["fingerprint"],
                         quality.assess(graph(), **kw)["fingerprint"])


class TestBoundedChangedSymbolCallers(unittest.TestCase):
    def test_walk_is_deterministic_and_collects_contracts(self):
        snapshot = {
            "symbol_edges": [
                {"caller": "provision", "callee": "serialize",
                 "contract": "contract:userdata"},
                {"caller": "validate", "callee": "serialize"},
                {"caller": "reconcile", "callee": "validate"},
            ]}
        result = depgraph.bounded_changed_symbol_callers(
            snapshot=snapshot, changed_symbols=["serialize"],
            bounds={"max_symbols": 128, "max_hops": 6,
                    "max_edges": 512, "timeout_seconds": 10})
        self.assertEqual(result["callers"],
                         ["provision", "reconcile", "validate"])
        self.assertEqual(result["contracts"], ["contract:userdata"])
        self.assertTrue(result["complete"])

    def test_edge_bound_is_explicit_and_fails_incomplete(self):
        snapshot = {"symbol_edges": [
            {"caller": f"caller-{i}", "callee": "serialize"}
            for i in range(4)]}
        result = depgraph.bounded_changed_symbol_callers(
            snapshot=snapshot, changed_symbols=["serialize"],
            bounds={"max_symbols": 2, "max_hops": 2,
                    "max_edges": 2, "timeout_seconds": 10})
        self.assertFalse(result["complete"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["edges_examined"], 2)

    def test_exact_edge_bound_reports_unvisited_frontier_as_incomplete(self):
        result = depgraph.bounded_changed_symbol_callers(
            snapshot={"symbol_edges": [
                {"caller": "ca", "callee": "a"},
                {"caller": "cb", "callee": "b"},
            ]},
            changed_symbols=["a", "b"],
            bounds={"max_symbols": 2, "max_hops": 2,
                    "max_edges": 1, "timeout_seconds": 10})
        self.assertEqual(result["callers"], ["ca"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["truncated"])

    def test_timeout_uses_injected_monotonic_clock(self):
        ticks = iter([0.0, 2.0])
        result = depgraph.bounded_changed_symbol_callers(
            snapshot={"symbol_edges": [{"caller": "ca", "callee": "a"}]},
            changed_symbols=["a"],
            bounds={"max_symbols": 1, "max_hops": 1,
                    "max_edges": 1, "timeout_seconds": 1},
            clock=lambda: next(ticks))
        self.assertEqual(result["edges_examined"], 0)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["complete"])


class TestBoundedRunnabilityEvidence(unittest.TestCase):
    def test_cache_observation_never_forks_the_shared_envelope_fact(self):
        probed = {"fingerprint": "run-1", "checks": [], "summary": "ready"}
        cached = dict(probed, cached=True, cache_hit=True)
        self.assertEqual(runnability.evidence_record(probed),
                         runnability.evidence_record(cached))


if __name__ == "__main__":
    unittest.main()
