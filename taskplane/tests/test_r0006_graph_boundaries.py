"""R-0006 S1: cut graph/decomposition cycles behind stable facades."""
from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import subprocess
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
        elif isinstance(node, ast.Call) and node.args:
            target = None
            if (isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"):
                target = node.args[0]
            elif (isinstance(node.func, ast.Name)
                  and node.func.id == "__import__"):
                target = node.args[0]
            if isinstance(target, ast.Constant) and isinstance(
                    target.value, str):
                found.add(target.value.split(".")[0])
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


def _fresh_python(source: str, *args: str) -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(TASKPLANE)
    env["TASKPLANE_STORE"] = "repo"
    completed = subprocess.run(
        [sys.executable, "-c", source, *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_boundary_imports_follow_the_approved_non_circular_contract() -> None:
    imports = {name: _imports(TASKPLANE / f"{name}.py") for name in (
        "depgraph", "decompose", "graph_decomposition", "graph_primitives",
        "lens_signals", "tp")}

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
                & imports["graph_primitives"])
    assert "review_progression" not in imports["graph_primitives"]
    assert "lens_signals" not in imports["tp"]
    assert not any(hasattr(graph_primitives, name) for name in (
        "_LENS_ROUTER", "register_lens_router", "lens_router_registered"))


def test_forbidden_import_check_includes_dynamic_forms(tmp_path: Path) -> None:
    probe = tmp_path / "dynamic_imports.py"
    probe.write_text(
        "import importlib\n"
        "importlib.import_module('lens_signals.routing')\n"
        "__import__('depgraph.store')\n",
        encoding="utf-8",
    )
    assert {"importlib", "lens_signals", "depgraph"} <= _imports(probe)


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


def test_registered_graph_loader_resolves_the_live_depgraph_seam(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import lens_signals

    fake = {
        "meta": {},
        "edges": [{"from": "api", "to": "auth", "kind": "imports"}],
    }
    monkeypatch.setattr(depgraph, "load", lambda _workspace: fake)

    assert lens_signals._graph_payload("/unused", ["src/auth/a.py"])[
        "module_dependents"] == {"auth": 1}


def test_fresh_direct_depgraph_scan_owns_successful_lens_maps(
        tmp_path: Path) -> None:
    workspace = tmp_path / "direct-depgraph"
    _write_fixture(workspace)
    direct = _fresh_python(
        "import json,sys,depgraph\n"
        "before='lens_signals' in sys.modules\n"
        "graph=depgraph.scan(sys.argv[1],decompose=True)\n"
        "quality=depgraph.scan_quality(graph)\n"
        "print(json.dumps({'before':before,"
        "'after':'lens_signals' in sys.modules,"
        "'maps':[c.get('lens_map') for c in graph.get('components') or []],"
        "'degraded':quality.get('degraded')},sort_keys=True))\n",
        str(workspace),
    )
    assert direct["before"] is False
    assert direct["after"] is False
    assert direct["maps"] and all(direct["maps"])
    assert direct["degraded"] is False

    composed_workspace = tmp_path / "facade-depgraph"
    _write_fixture(composed_workspace)
    composed = _fresh_python(
        "import json,sys,lens_signals,depgraph\n"
        "graph=depgraph.scan(sys.argv[1],decompose=True)\n"
        "print(json.dumps({'maps':[c.get('lens_map') "
        "for c in graph.get('components') or []],"
        "'degraded':depgraph.scan_quality(graph).get('degraded')},"
        "sort_keys=True))\n",
        str(composed_workspace),
    )
    assert composed == {"maps": direct["maps"], "degraded": False}


def test_fresh_direct_decompose_owns_successful_lens_maps(
        tmp_path: Path) -> None:
    workspace = tmp_path / "direct-decompose"
    _write_fixture(workspace)
    source = (
        "import json,sys,decompose\n"
        "before='lens_signals' in sys.modules\n"
        "graph={'files':{'src/auth/session.py':{'hash':'a'},"
        "'src/payments/charge.py':{'hash':'b'}},"
        "'edges':[{'from':'auth','to':'payments','kind':'imports'}],"
        "'meta':{}}\n"
        "components,stats=decompose.derive(sys.argv[1],graph)\n"
        "print(json.dumps({'before':before,"
        "'after':'lens_signals' in sys.modules,"
        "'maps':[c.get('lens_map') for c in components],"
        "'degraded':stats.get('degraded')},sort_keys=True))\n"
    )
    direct = _fresh_python(source, str(workspace))
    assert direct["before"] is False
    assert direct["after"] is False
    assert direct["maps"] and all(direct["maps"])
    assert direct["degraded"] == []

    composed = _fresh_python(
        "import lens_signals\n" + source,
        str(workspace),
    )
    assert composed["maps"] == direct["maps"]
    assert composed["degraded"] == []


def test_boundary_cut_shrinks_to_the_measured_residual_sccs() -> None:
    inventory = import_cycles.build_inventory(
        ROOT, source_revision="t09-focused-boundary")
    rows = {frozenset(row["members"]): row for row in inventory["sccs"]}
    orchestration = frozenset({
        "taskplane.audit", "taskplane.dashboard", "taskplane.evidence",
        "taskplane.loop", "taskplane.loop_status", "taskplane.retro",
        "taskplane.review", "taskplane.review_repair",
        "taskplane.review_retry", "taskplane.runtime_eval", "taskplane.views",
    })
    kernel = frozenset({
        "taskplane.collision", "taskplane.regression",
        "taskplane.review_evidence", "taskplane.stage_entities",
        "taskplane.stage_handoff", "taskplane.taskplane_lite",
    })
    lens_family = frozenset({
        "taskplane.lens", "taskplane.lens_signals",
        "taskplane.review_progression",
    })

    assert set(rows) == {orchestration, kernel, lens_family}
    assert rows[orchestration]["edge_count"] == 29
    assert rows[kernel]["edge_count"] == 11
    assert rows[lens_family]["edge_count"] == 5
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
