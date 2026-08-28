"""Focused M1-A evidence for architecture scanning and custody decisions."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import depgraph  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _architecture_fixture(root: Path, *, terminal_source: str = "x = 1\n") \
        -> None:
    accepted = json.loads(
        (ROOT / "design/contract.json").read_text(encoding="utf-8"))
    contract = {
        "requirement": accepted["requirement"],
        "contracts": accepted["contracts"],
        "architecture_decomposition": accepted["architecture_decomposition"],
        "graph": {
            "proposed_modules": accepted["graph"]["proposed_modules"],
            "proposed_edges": accepted["graph"]["proposed_edges"],
        },
    }
    fixtures = {
        "taskplane/loop.py": "x = 1\n",
        "taskplane/tp.py": "x = 1\n",
        "taskplane/build_c.py": "x = 1\n",
        "taskplane/plan_topology.py": "x = 1\n",
        "taskplane/command_runtime.py": "x = 1\n",
        "taskplane/native_authority.py": "x = 1\n",
        "taskplane/design_sweep.py": "x = 1\n",
        "taskplane/terminal_truth.py": terminal_source,
        "taskplane/repository.py": "x = 1\n",
        "taskplane/progress.py": "x = 1\n",
        "taskplane/checkpoint.py": "x = 1\n",
        "taskplane/views.py": "x = 1\n",
        "taskplane/release_evidence.py": "x = 1\n",
        "taskplane/tests/test_r0013_contract.py":
            "def test_contract(): pass\n",
        "exports/run/terminal/evidence.json": "{}\n",
        "plan/tasks.json": "{}\n",
        "lenses/catalog.json": "{}\n",
        "docs/guide.md": "# Guide\n",
    }
    for relative, text in fixtures.items():
        _write(root, relative, text)
    _write(root, "design/contract.json", json.dumps(contract))


def _scan(root: Path, graph_path: Path) -> dict:
    with mock.patch.object(depgraph, "_path", lambda _ws: str(graph_path)), \
            mock.patch.object(depgraph.runtime_storage,
                              "load_workspace_locator", return_value=None), \
            mock.patch.object(depgraph.tp, "trace", return_value=None):
        return depgraph.scan(str(root), strict=True)


def test_m02_decomposition_is_scanner_input(tmp_path: Path) -> None:
    graph = _scan(ROOT, tmp_path / "root-graph.json")
    proof = graph["meta"]["architecture_map"]

    assert proof["complete"] is True
    assert proof["source"] == \
        "design/contract.json#/architecture_decomposition"
    assert proof["node_count"] == 14
    assert proof["edge_count"] == 24
    assert proof["required_singleton_sccs"] == [
        "component:design-sweep-validator",
        "component:native-authority-validator",
        "component:terminal-truth-coordinator",
    ]
    assert graph["meta"]["graph_scan_quality"]["producers"] \
        ["architecture-map"]["status"] == "complete"

    drifted = tmp_path / "drifted"
    _architecture_fixture(drifted, terminal_source="import loop\n")
    drift_proof = depgraph.architecture_map_proof(str(drifted))

    assert drift_proof["complete"] is False
    assert any("new owners depend on host transport or transition adapters"
               in error for error in drift_proof["errors"])


def test_m02_authority_floor_is_opt_in_and_configured_drift_fails(
        tmp_path: Path) -> None:
    unconfigured = tmp_path / "unconfigured"
    _write(unconfigured, "src/app.py", "x = 1\n")

    graph = _scan(unconfigured, tmp_path / "unconfigured-graph.json")
    proof = depgraph.architecture_map_proof(str(unconfigured))

    assert proof["configured"] is False
    assert proof["status"] == "not-requested"
    assert proof["errors"] == []
    assert "architecture_map" not in graph["meta"]
    assert graph["meta"]["graph_scan_quality"]["producers"] \
        ["architecture-map"] == {"status": "not-requested", "failures": []}

    configured = tmp_path / "configured"
    _architecture_fixture(configured)
    contract_path = configured / "design" / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["architecture_decomposition"]["semantic_edges"] = [
        row for row in contract["architecture_decomposition"]["semantic_edges"]
        if not (row["from"] == "taskplane" and
                row["to"] == "contract:delivery.codex-native-dispatch")
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    drifted = depgraph.architecture_map_proof(str(configured))
    assert drifted["configured"] is True
    assert drifted["status"] == "incomplete"
    assert any("semantic authority omits required edges" in error
               for error in drifted["errors"])


def test_m26_terminal_capability_records_recoverability_tradeoff(
        tmp_path: Path) -> None:
    proof = depgraph.terminal_capability_custody_proof(str(ROOT))

    assert proof["complete"] is True
    assert proof["selected"] == "durably-protected-issuer"
    assert "authority isolation" in proof["gain"]
    assert "protected secret at rest" in proof["cost"]
    assert {row["id"] for row in proof["alternatives"]} == {
        "process-only-custody",
        "host-authenticated-reissuance",
        "durably-protected-issuer",
    }
    process_only = next(row for row in proof["alternatives"]
                        if row["id"] == "process-only-custody")
    assert "authority isolation" in process_only["gain"]
    assert "restart recoverability" in process_only["cost"]
    assert "first finalizer process replacement" in proof["revisit_when"]
    assert "failed restart canary" in proof["revisit_when"]
    assert all(proof["observed_runtime"].values())

    components = (ROOT / "components.yaml").read_text(encoding="utf-8")
    runtime = (ROOT / "taskplane/terminal_truth.py").read_text(
        encoding="utf-8")
    mutations = [
        (
            components.replace(
                "  - alternative: process-only-custody | gain: maximum "
                "non-serializable authority isolation | cost: restart "
                "recoverability after finalizer process replacement\n", ""),
            runtime,
            "must compare exactly",
        ),
        (
            components.replace(
                "first finalizer process replacement or failed restart canary",
                "manual review"),
            runtime,
            "observable first finalizer replacement",
        ),
        (
            components.replace(
                "  - selected: durably-protected-issuer",
                "  - selected: process-only-custody"),
            runtime,
            "does not match the durable production issuer",
        ),
        (
            components,
            runtime.replace("def _issuer_key_path", "def renamed_key_path"),
            "not wired in production",
        ),
    ]
    for index, (candidate, terminal_source, message) in enumerate(mutations):
        root = tmp_path / str(index)
        _write(root, "components.yaml", candidate)
        _write(root, "taskplane/terminal_truth.py", terminal_source)
        mutated = depgraph.terminal_capability_custody_proof(str(root))
        assert mutated["complete"] is False
        assert any(message in error for error in mutated["errors"])
