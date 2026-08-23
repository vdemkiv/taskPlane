"""R-0007 T11: DoD consumes one explicit graph input from both callers."""
import ast
import copy
import hashlib
import json
from pathlib import Path

import depgraph
import taskplane_lite as tp


ROOT = Path(__file__).resolve().parents[2]


def _contract():
    return {"coding": {"scope_paths": [], "dod": {
        "require_graph_input": True, "require_clean_scope_diff": False,
        "test_command": None}}}


def _record(tmp_path):
    graph = {"modules": {"taskplane.review": {"files": 1}}, "edges": [],
             "files": {"taskplane/review.py": {}}, "recorded": [],
             "meta": {}}
    impact = {"touched": ["taskplane.review"], "impacted": {},
              "total_impacted": 0}
    return depgraph.dod_graph_input(
        str(tmp_path), ["taskplane/review.py"], graph=graph,
        impact_record=impact)


def test_lite_gate_has_no_depgraph_import_and_accepts_explicit_record(tmp_path):
    tree = ast.parse((ROOT / "taskplane" / "taskplane_lite.py").read_text())
    assert not any((isinstance(node, ast.Import) and any(
        alias.name == "depgraph" for alias in node.names)) or
        (isinstance(node, ast.ImportFrom) and node.module == "depgraph")
        for node in ast.walk(tree))
    record = _record(tmp_path)
    assert tp.dod_check(_contract(), str(tmp_path), None,
                        dod_graph_input=record) == []


def test_missing_or_non_sparse_impact_fails_closed(tmp_path):
    errors = tp.dod_check(_contract(), str(tmp_path), None)
    assert any("explicit DoD graph input is missing" in row for row in errors)
    record = _record(tmp_path)
    record["impact"] = None
    material = {key: record[key] for key in record if key != "fingerprint"}
    record["fingerprint"] = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    errors = tp.dod_check(_contract(), str(tmp_path), None,
                          dod_graph_input=record)
    assert any("non-sparse graph omitted impact" in row for row in errors)

