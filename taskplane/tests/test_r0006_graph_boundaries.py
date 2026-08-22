"""R-0006 S1: cut graph/decomposition cycles behind stable facades."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TASKPLANE = ROOT / "taskplane"
sys.path.insert(0, str(TASKPLANE))

import decompose  # noqa: E402
import depgraph  # noqa: E402
import graph_decomposition  # noqa: E402
import graph_primitives  # noqa: E402
import import_cycles  # noqa: E402


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _write_fixture(workspace: Path, *, broken: bool = False) -> None:
    auth = workspace / "src" / "auth"
    payments = workspace / "src" / "payments"
    auth.mkdir(parents=True)
    payments.mkdir(parents=True)
    (auth / "session.py").write_text(
        "from src.payments import charge\n", encoding="utf-8")
    (payments / "charge.py").write_text(
        "def charge():\n    return 1\n" if not broken
        else ("VALUE = 1\n" * 600 + "def charge(:\n    return 1\n"),
        encoding="utf-8",
    )


def test_boundary_imports_follow_the_approved_non_circular_contract() -> None:
    imports = {name: _imports(TASKPLANE / f"{name}.py") for name in (
        "depgraph", "decompose", "graph_decomposition", "lens_signals")}

    assert "decompose" not in imports["depgraph"]
    assert "depgraph" not in imports["decompose"]
    assert "lens_signals" not in imports["decompose"]
    assert {"graph_primitives", "graph_decomposition"} <= imports["depgraph"]
    assert {"graph_primitives", "graph_decomposition"} <= imports["decompose"]
    assert "graph_primitives" in imports["graph_decomposition"]
    assert "graph_primitives" in imports["lens_signals"]
    assert not ({"depgraph", "decompose", "lens_signals"}
                & imports["graph_decomposition"])
    assert not ({"depgraph", "decompose", "lens_signals"}
                & _imports(TASKPLANE / "graph_primitives.py"))


def test_decompose_remains_a_compatibility_facade() -> None:
    assert inspect.signature(decompose.derive) == inspect.signature(
        graph_decomposition.derive)
    assert decompose.load_floors is graph_decomposition.load_floors
    assert decompose.floors_hash is graph_decomposition.floors_hash
    assert decompose.CANDIDATE_MIN_FILES == 8
    assert decompose.BIG_FILE_LINES == 600
    assert depgraph.module_of is graph_primitives.module_of
    assert depgraph.declared_module_ids is graph_primitives.declared_module_ids
    assert depgraph.is_dependency_edge is graph_primitives.is_dependency_edge


def test_shared_graph_payload_preserves_dependency_and_boundary_meaning() -> None:
    graph = {
        "meta": {"module_ids": {"packages/ui": "@acme/ui"}},
        "edges": [
            {"from": "api", "to": "@acme/ui", "kind": "imports"},
            {"from": "fixtures/client", "to": "@acme/ui",
             "kind": "imports"},
            {"from": "svc:ui", "to": "@acme/ui", "kind": "defined_in"},
            {"from": "@acme/ui", "to": "contract:web", "kind": "consumes"},
        ],
    }

    payload = graph_primitives.graph_payload(
        graph, ["@acme/ui"],
        fixture_module_predicate=lambda module: module.startswith("fixtures/"),
    )

    assert payload == {
        "hub_dependents": 3,
        "boundary_contracts": ["contract:web"],
        "modules": ["@acme/ui"],
        "module_ids": {"packages/ui": "@acme/ui"},
        "module_dependents": {"@acme/ui": 1},
    }


def test_boundary_cut_shrinks_to_the_measured_residual_sccs() -> None:
    inventory = import_cycles.build_inventory(
        ROOT, source_revision="t09-focused-boundary")
    rows = {frozenset(row["members"]): row for row in inventory["sccs"]}
    orchestration = frozenset({
        "taskplane.audit", "taskplane.dashboard", "taskplane.defect_claim",
        "taskplane.design_contract", "taskplane.evidence", "taskplane.lens",
        "taskplane.lens_signals", "taskplane.loop", "taskplane.loop_status",
        "taskplane.requirements", "taskplane.retro", "taskplane.review",
        "taskplane.review_progression", "taskplane.review_repair",
        "taskplane.review_retry", "taskplane.runtime_eval", "taskplane.views",
    })
    kernel = frozenset({
        "taskplane.collision", "taskplane.depgraph", "taskplane.regression",
        "taskplane.review_evidence", "taskplane.stage_entities",
        "taskplane.stage_handoff", "taskplane.taskplane_lite",
    })

    assert set(rows) == {orchestration, kernel}
    assert rows[orchestration]["edge_count"] == 48
    assert rows[kernel]["edge_count"] == 13
    cyclic = set().union(*rows)
    assert not ({"taskplane.decompose", "taskplane.graph_decomposition",
                 "taskplane.graph_primitives"} & cyclic)


def test_scan_signature_plain_shape_and_decomposition_fail_open(
        tmp_path: Path) -> None:
    signature = inspect.signature(depgraph.scan)
    assert list(signature.parameters) == ["ws", "decompose", "strict"]
    assert signature.parameters["decompose"].default is False
    assert signature.parameters["strict"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["strict"].default is False

    workspace = tmp_path / "repo"
    _write_fixture(workspace)
    plain = depgraph.scan(str(workspace))
    assert "components" not in plain
    assert [(row["from"], row["to"], row["kind"])
            for row in plain["edges"]] == [
                ("auth", "payments", "imports")]
    plain_fingerprint = plain["meta"]["content_fingerprint"]

    decomposed = depgraph.scan(str(workspace), decompose=True)
    assert decomposed["components"]
    assert decomposed["meta"]["content_fingerprint"] == plain_fingerprint
    assert depgraph.scan(str(workspace))["components"] == decomposed["components"]

    broken = tmp_path / "broken"
    _write_fixture(broken, broken=True)
    degraded = depgraph.scan(str(broken), decompose=True)
    quality = depgraph.scan_quality(degraded)
    assert quality["degraded"] is True
    assert quality["producers"]["decomposition"]["status"] == "degraded"
    assert any(row["file"] == "src/payments/charge.py"
               for row in quality["failures"])
    with pytest.raises(depgraph.GraphQualityDegraded):
        depgraph.scan(str(broken), decompose=True, strict=True)
