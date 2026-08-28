"""Focused evidence for HX-GRAPH (H-02, H-31, and L-02)."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import depgraph  # noqa: E402
import glob_match  # noqa: E402
import graph_primitives  # noqa: E402
import lens  # noqa: E402


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _architecture(root: Path, *, a: str = "x = 1\n",
                  b: str = "import a\n", mutate=None) -> dict:
    _write(root, "a.py", a)
    _write(root, "b.py", b)
    _write(root, "taskplane/loop.py", "x = 1\n")
    _write(root, "taskplane/terminal_truth.py", "x = 1\n")
    _write(root, "docs/guide.md", "# Guide\n")
    semantic_edges = [
        {"from": "taskplane",
         "to": "contract:delivery.codex-native-dispatch",
         "kind": "intent", "reason": "governed intent"},
        {"from": "contract:delivery.codex-native-dispatch",
         "to": "ext:codex-native-orchestration",
         "kind": "transported-by", "reason": "native host"},
        {"from": "taskplane",
         "to": "contract:delivery.exact-sha-terminal-truth",
         "kind": "changes", "reason": "terminal aggregation"},
        {"from": "contract:delivery.exact-sha-terminal-truth",
         "to": "taskplane/terminal_truth.py",
         "kind": "coordinated-by", "reason": "one coordinator"},
    ]
    contract = {
        "contracts": [{"id": "contract:current"}],
        "architecture_decomposition": {
            "schema": depgraph.DESIGN_ARCHITECTURE_SCHEMA,
            "decision_record": "D-test",
            "scanner_input": (
                "design/contract.json#/architecture_decomposition"),
            "scanner_rule": "strict",
            "nodes": [
                {"id": "ext:codex-native-orchestration",
                 "kind": "external-host", "path_globs": []},
                {"id": "component:taskplane-governance-adapters",
                 "kind": "existing", "path_globs": ["taskplane/loop.py"]},
                {"id": "component:native-authority-validator",
                 "kind": "new", "path_globs": ["a.py"]},
                {"id": "component:design-sweep-validator",
                 "kind": "new", "path_globs": ["b.py"]},
                {"id": "component:terminal-truth-coordinator",
                 "kind": "new",
                 "path_globs": ["taskplane/terminal_truth.py"]},
                {"id": "surface:documentation", "kind": "producer",
                 "path_globs": ["docs/*.md"]},
            ],
            "required_properties": sorted(
                depgraph._ARCHITECTURE_REQUIRED_PROPERTIES),
            "required_singleton_sccs": [
                "component:native-authority-validator",
                "component:design-sweep-validator",
                "component:terminal-truth-coordinator",
            ],
            "semantic_edges": semantic_edges,
        },
        "graph": {
            "proposed_modules": ["taskplane"],
            "proposed_edges": [{
                "from": "taskplane", "to": "contract:current",
                "kind": "provides", "reason": "current design authority",
            }],
        },
    }
    architecture = contract["architecture_decomposition"]
    architecture["content_fingerprint"] = hashlib.sha256(
        json.dumps(architecture, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    if mutate:
        mutate(contract)
    _write(root, "design/contract.json", json.dumps(contract))
    return contract


def _scan_without_external_store(root: Path, *, strict: bool = False,
                                 graph_path: Path | None = None) -> dict:
    graph_path = str(graph_path or (root / ".graph.json"))
    with mock.patch.object(depgraph, "_path", lambda _ws: graph_path), \
            mock.patch.object(depgraph.runtime_storage,
                              "load_workspace_locator", return_value=None), \
            mock.patch.object(depgraph.tp, "trace", return_value=None):
        return depgraph.scan(str(root), strict=strict)


def test_h02_nodes_activate_with_enter_and_space() -> None:
    html = depgraph._HTML
    activation = "ev.key==='Enter'||ev.key===' '||ev.key==='Spacebar'"
    assert html.count(activation) == 2, (
        "both module and decomposed-component buttons need keyboard activation")
    assert html.count("ev.preventDefault()") >= 2
    assert "showTip(n,n.x,n.y)" in html
    assert "show(c.x,c.y)" in html
    assert html.count("role:'button'") == 2
    assert html.count("tabindex:'0'") == 2


def test_h31_scanner_consumes_accepted_architecture_map(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    graph = _scan_without_external_store(
        root, strict=True, graph_path=tmp_path / "graph.json")
    proof = graph["meta"]["architecture_map"]

    assert proof["schema"] == depgraph.ARCHITECTURE_MAP_SCHEMA
    assert proof["status"] == "complete"
    assert proof["complete"] is True
    assert proof["truncated"] is False
    assert proof["node_count"] == 14
    assert set(proof["declared_nodes"]) == {
        "ext:codex-native-orchestration",
        "component:taskplane-governance-adapters",
        "component:native-authority-validator",
        "component:design-sweep-validator",
        "component:terminal-truth-coordinator",
        "surface:git-head", "surface:governed-progress",
        "surface:run-journal", "surface:tasks-and-gates",
        "surface:public-report", "surface:repository-verification-report",
        "surface:release-evidence", "surface:exports-terminal-evidence",
        "component:r0013-contract-tests",
    }
    assert proof["edge_count"] == 24
    assert proof["current_design_edge_count"] == 23
    export_node = next(row for row in proof["node_details"]
                       if row["id"] == "surface:exports-terminal-evidence")
    assert export_node["matched_files"], "non-Python export glob is consumed"
    assert all((
        ["component:native-authority-validator"] in proof["sccs"],
        ["component:design-sweep-validator"] in proof["sccs"],
        ["component:terminal-truth-coordinator"] in proof["sccs"],
    ))
    assert graph["modules"]["surface:exports-terminal-evidence"]["kind"] == \
        "surface"
    assert graph["modules"]["resource:review.finding-traceability"]["kind"] == \
        "resource"
    assert any(edge["kind"] == "transported-by" and
               edge["source"].endswith("semantic_edges")
               for edge in graph["edges"])
    assert any(edge["to"] == "contract:runtime.durable-state-and-authority"
               and edge["source"].endswith("graph/proposed_edges")
               for edge in graph["edges"])
    assert depgraph.quality_errors(graph) == []


def test_h31_file_external_contract_resource_and_semantic_edges_coexist(
        tmp_path: Path) -> None:
    _architecture(tmp_path)

    graph = _scan_without_external_store(tmp_path, strict=True)
    proof = graph["meta"]["architecture_map"]

    assert proof["complete"] is True
    assert any(row["id"] == "surface:documentation"
               and row["matched_files"] == ["docs/guide.md"]
               for row in proof["node_details"])
    assert graph["modules"]["ext:codex-native-orchestration"]["kind"] == \
        "external"
    assert graph["modules"]["contract:current"]["kind"] == "contract"
    assert len(proof["declared_edges"]) == 4
    assert len(proof["current_design_edges"]) == 1


def test_h31_missing_accepted_map_fails_strict_scan(tmp_path: Path) -> None:
    _architecture(tmp_path, mutate=lambda contract:
                  contract.pop("architecture_decomposition"))

    with pytest.raises(depgraph.GraphQualityDegraded,
                       match="missing architecture_decomposition"):
        _scan_without_external_store(tmp_path, strict=True)


def test_h31_malformed_design_is_degraded_not_ignored(tmp_path: Path) -> None:
    _architecture(tmp_path)
    _write(tmp_path, "design/contract.json", "{not-json")

    with pytest.raises(depgraph.GraphQualityDegraded, match="not valid UTF-8 JSON"):
        _scan_without_external_store(tmp_path, strict=True)


def test_h31_current_edges_cannot_substitute_for_accepted_edges(
        tmp_path: Path) -> None:
    def substitute(contract: dict) -> None:
        architecture = contract["architecture_decomposition"]
        moved = architecture["semantic_edges"].pop(0)
        contract["graph"]["proposed_edges"].append(moved)

    _architecture(tmp_path, mutate=substitute)

    with pytest.raises(depgraph.GraphQualityDegraded,
                       match="content_fingerprint"):
        _scan_without_external_store(tmp_path, strict=True)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda contract: contract["architecture_decomposition"].__setitem__(
            "future_section", {}), "unknown sections"),
        (lambda contract: contract["architecture_decomposition"].__setitem__(
            "schema", "taskplane.design-architecture-map/v999"),
         "unknown schema"),
        (lambda contract: contract["architecture_decomposition"]
         ["semantic_edges"].pop(), "omits required edges"),
        (lambda contract: contract["architecture_decomposition"]
         ["semantic_edges"].append({
             "from": "taskplane", "to": "contract:ghost",
             "kind": "teleports", "reason": "invalid"}),
         "unknown semantic kind"),
        (lambda contract: contract["architecture_decomposition"]
         ["semantic_edges"].append({
             "from": "missing/module", "to": "contract:ghost",
             "kind": "uses", "reason": "invalid endpoint"}),
         "unknown endpoint"),
        (lambda contract: contract["architecture_decomposition"]
         ["nodes"].append({"id": "component:ghost", "kind": "new",
                            "path_globs": ["missing/*.rs"]}),
         "has no candidate files"),
        (lambda contract: contract["graph"].pop("proposed_edges"),
         "graph.proposed_edges must be a list"),
    ],
)
def test_h31_unknown_missing_extra_or_degraded_map_never_passes(
        tmp_path: Path, mutate, message: str) -> None:
    _architecture(tmp_path, mutate=mutate)

    proof = depgraph.architecture_map_proof(str(tmp_path))

    assert proof["status"] == "incomplete"
    assert proof["complete"] is False
    assert any(message in error for error in proof["errors"])


def test_h31_truncated_proof_is_explicitly_incomplete(tmp_path: Path) -> None:
    _architecture(tmp_path)

    proof = depgraph.architecture_map_proof(
        str(tmp_path), max_nodes=1, max_edges=1)

    assert proof["truncated"] is True
    assert proof["complete"] is False
    assert proof["status"] == "incomplete"
    assert proof["node_count"] == 6
    assert proof["declared_nodes"] == ["ext:codex-native-orchestration"]
    assert proof["sccs"] == [], "a bounded prefix must not mint SCC proof"
    assert any("exceeds bound" in error for error in proof["errors"])


def test_h31_production_scan_refuses_over_bound_architecture_map(
        tmp_path: Path) -> None:
    def over_bound(contract: dict) -> None:
        nodes = contract["architecture_decomposition"]["nodes"]
        while len(nodes) <= depgraph.ARCHITECTURE_MAX_NODES:
            nodes.append({"id": f"component:extra-{len(nodes)}",
                          "kind": "file", "path_globs": ["a.py"]})

    _architecture(tmp_path, mutate=over_bound)

    with pytest.raises(depgraph.GraphQualityDegraded, match="exceeds bound"):
        _scan_without_external_store(tmp_path, strict=True)


def test_h31_cycle_fails_complete_scc_proof(tmp_path: Path) -> None:
    _architecture(tmp_path, a="import b\n", b="import a\n")

    proof = depgraph.architecture_map_proof(str(tmp_path))

    assert proof["complete"] is False
    assert proof["truncated"] is False
    assert ["component:design-sweep-validator",
            "component:native-authority-validator"] in proof["cyclic_sccs"]
    assert any("required singleton SCCs are cyclic" in error
               for error in proof["errors"])


def test_h31_ignored_architecture_glob_fails_strict_scan(tmp_path: Path) -> None:
    def ignored(contract: dict) -> None:
        contract["architecture_decomposition"]["nodes"][2]["path_globs"] = [
            "generated/**"]

    _architecture(tmp_path, mutate=ignored)
    _write(tmp_path, "generated/owner.py", "x = 1\n")
    _write(tmp_path, "components.yaml", "exclude:\n  - generated\n")

    with pytest.raises(depgraph.GraphQualityDegraded,
                       match="ignored or excluded"):
        _scan_without_external_store(tmp_path, strict=True)


def test_l02_both_routing_layers_consume_one_neutral_glob_matcher_with_parity(
        monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = [
        ("src/auth/login.py", "**/auth/**", True),
        ("web/components/Btn.tsx", "**/*.tsx", True),
        ("README.md", "**/README.md", True),
        ("src/todo/core.py", "**/auth/**", False),
        ("api/schema.json", "api/*.json", True),
        ("nested/api/schema.json", "api/*.json", False),
    ]
    for path, pattern, expected in corpus:
        assert glob_match.path_matches(path, pattern) is expected
        assert lens._match(path, pattern) is expected
        assert graph_primitives._match(path, pattern) is expected

    calls = []
    original = glob_match.path_matches

    def observed(path: str, pattern: str) -> bool:
        calls.append((path, pattern))
        return original(path, pattern)

    monkeypatch.setattr(glob_match, "path_matches", observed)
    assert lens._match("src/a.py", "**/*.py")
    assert graph_primitives._match("src/a.py", "**/*.py")
    assert calls == [("src/a.py", "**/*.py"),
                     ("src/a.py", "**/*.py")]

    tree = ast.parse(Path(glob_match.__file__).read_text(encoding="utf-8"))
    imports = {
        node.names[0].name if isinstance(node, ast.Import) else node.module
        for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    assert imports <= {"__future__", "fnmatch", "os"}


def test_l02_ordered_match_helpers_preserve_router_contracts() -> None:
    files = ["src/a.py", "tests/a.py", "README.md"]
    patterns = ["**/*.py", "**/*.md"]

    assert graph_primitives._glob_hit(files, patterns) == \
        glob_match.first_match(files, patterns) == ("src/a.py", "**/*.py")
    assert lens._any_match(files, patterns) == \
        glob_match.matches_by_pattern(files, patterns) == [
            ("src/a.py", "**/*.py"), ("README.md", "**/*.md")]
