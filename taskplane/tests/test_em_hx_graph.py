"""Focused evidence for HX-GRAPH (H-02, H-31, and L-02)."""
from __future__ import annotations

import ast
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
                  b: str = "import a\n",
                  owners=("a.py", "b.py"),
                  edges=("b.py -> a.py",)) -> None:
    _write(root, "a.py", a)
    _write(root, "b.py", b)
    text = "owners:\n" + "".join(f"  - {node}\n" for node in owners)
    text += "owner_edges:\n" + "".join(
        f"  - {edge}\n" for edge in edges)
    _write(root, "components.yaml", text)


def _scan_without_external_store(root: Path, *, strict: bool = False) -> dict:
    graph_path = str(root / ".graph.json")
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
    _architecture(tmp_path)

    graph = _scan_without_external_store(tmp_path, strict=True)
    proof = graph["meta"]["architecture_map"]

    assert proof["schema"] == depgraph.ARCHITECTURE_MAP_SCHEMA
    assert proof["status"] == "complete"
    assert proof["complete"] is True
    assert proof["truncated"] is False
    assert proof["declared_edges"] == proof["observed_edges"] == [{
        "from": "b.py", "to": "a.py", "kind": "imports"}]
    assert proof["sccs"] == [["a.py"], ["b.py"]]
    assert proof["cyclic_sccs"] == []
    assert graph["modules"]["a.py"]["declared_by"] == \
        "components.yaml:owners"
    assert any(edge["from"] == "b.py" and edge["to"] == "a.py"
               and edge["source"] == "components.yaml:owner_edges"
               for edge in graph["edges"])
    assert depgraph.quality_errors(graph) == []


def test_h31_unknown_owner_fails_strict_scan(tmp_path: Path) -> None:
    _architecture(tmp_path, owners=("a.py", "missing.py"), edges=())

    with pytest.raises(depgraph.GraphQualityDegraded, match="unknown owner nodes"):
        _scan_without_external_store(tmp_path, strict=True)


@pytest.mark.parametrize(
    ("b_source", "edges", "message"),
    [
        ("x = 2\n", ("b.py -> a.py",),
         "declared owner edges are not observed"),
        ("import a\n", (), "observed owner edges are undeclared"),
    ],
)
def test_h31_incomplete_edge_proof_never_passes(
        tmp_path: Path, b_source: str, edges: tuple[str, ...],
        message: str) -> None:
    _architecture(tmp_path, b=b_source, edges=edges)

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
    assert proof["node_count"] == 2
    assert proof["declared_nodes"] == ["a.py"]
    assert proof["sccs"] == [], "a bounded prefix must not mint SCC proof"
    assert any("exceeds bound" in error for error in proof["errors"])


def test_h31_production_scan_refuses_over_bound_architecture_map(
        tmp_path: Path) -> None:
    owners = tuple(f"owner_{index}.py"
                   for index in range(depgraph.ARCHITECTURE_MAX_NODES + 1))
    _architecture(tmp_path, owners=owners, edges=())

    with pytest.raises(depgraph.GraphQualityDegraded, match="exceeds bound"):
        _scan_without_external_store(tmp_path, strict=True)


def test_h31_cycle_fails_complete_scc_proof(tmp_path: Path) -> None:
    _architecture(tmp_path, a="import b\n", b="import a\n",
                  edges=("a.py -> b.py", "b.py -> a.py"))

    proof = depgraph.architecture_map_proof(str(tmp_path))

    assert proof["complete"] is False
    assert proof["truncated"] is False
    assert proof["cyclic_sccs"] == [["a.py", "b.py"]]
    assert any("dependency cycles" in error for error in proof["errors"])


def test_h31_unknown_edge_endpoint_cannot_expand_declared_nodes(
        tmp_path: Path) -> None:
    _architecture(tmp_path, edges=("b.py -> ghost.py",))

    proof = depgraph.architecture_map_proof(str(tmp_path))

    assert proof["complete"] is False
    assert any("undeclared nodes" in error for error in proof["errors"])
    assert proof["sccs"] == []


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
