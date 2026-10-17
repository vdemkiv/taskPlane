"""Graph queries use the supplied snapshot and actual dependency edges."""
import pytest

from taskplane import graph_primitives as graph


def test_graph_change_recomputes_components_without_changing_prior_snapshot():
    nodes = ["a", "b", "c"]
    before = [("a", "b")]
    assert graph.strongly_connected_components(nodes, before) == [["a"], ["b"], ["c"]]
    assert graph.strongly_connected_components(nodes, before + [("b", "a")]) == [["a", "b"], ["c"]]
    assert graph.strongly_connected_components(nodes, before) == [["a"], ["b"], ["c"]]
    with pytest.raises(ValueError, match="unknown node"):
        graph.strongly_connected_components(nodes, [("a", "foreign")])


def test_annotations_and_test_fixtures_do_not_inflate_dependency_count():
    snapshot = {"edges": [
        {"from": "consumer", "to": "producer", "kind": "imports"},
        {"from": "testdata/consumer", "to": "producer", "kind": "imports"},
        {"from": "annotation", "to": "producer", "kind": "contains"}]}
    result = graph.graph_payload(snapshot, ["producer"],
        fixture_module_predicate=graph.is_fixture_module)
    assert result["module_dependents"] == {"producer": 1}
    assert result["hub_dependents"] == 3


def test_manifest_identity_is_selected_from_nearest_owned_package():
    sources = {"package.json": '{"name":"repository"}',
        "packages/api/package.json": '{"name":"@project/api"}',
        "packages/api/nested/package.json": '{"name":"@project/nested"}',
        "unrelated.json": '{"name":"foreign"}'}
    manifests = graph.manifest_modules(sources, sources.get)
    assert graph.module_of("packages/api/service.py", manifests) == "@project/api"
    assert graph.module_of("packages/api/nested/service.py", manifests) == "@project/nested"
    assert "foreign" not in manifests.values()


@pytest.mark.parametrize("shape", ["chain", "cycle"])
def test_iterative_scc_beyond_recursion_depth(shape):
    import sys
    before = sys.getrecursionlimit()
    nodes = [f"n{i:08d}" for i in range(before + 1)]
    edges = list(zip(nodes, nodes[1:]))
    if shape == "cycle":
        edges.append((nodes[-1], nodes[0]))
    actual = graph.strongly_connected_components(nodes, edges)
    assert actual == ([nodes] if shape == "cycle" else [[node] for node in nodes])
    assert sys.getrecursionlimit() == before


@pytest.mark.parametrize("nodes,edges,expected", [
    (["a"], [("a", "a")], [["a"]]),
    (["a", "b", "c", "d"], [("a", "b"), ("b", "a")], [["a", "b"], ["c"], ["d"]]),
    (["a", "b", "c"], [("a", "b"), ("a", "b"), ("b", "a"), ("b", "c")], [["a", "b"], ["c"]]),
], ids=["self-loop", "disconnected", "duplicate-edges"])
def test_iterative_scc_boundary_shapes(nodes, edges, expected):
    assert graph.strongly_connected_components(nodes, edges) == expected


def test_iterative_scc_order_and_resource_refusal():
    import sys
    from copy import deepcopy
    nodes = ["c", "b", "a", "d"]
    edges = [("c", "a"), ("a", "b"), ("b", "c")]
    original = deepcopy((nodes, edges))
    metrics = {}
    before = sys.getrecursionlimit()
    first = graph.strongly_connected_components(nodes, edges, metrics=metrics)
    second = graph.strongly_connected_components(list(reversed(nodes)), list(reversed(edges)))
    assert first == second == [["a", "b", "c"], ["d"]]
    for ceiling in ({"max_nodes": 3}, {"max_edges": 2}):
        with pytest.raises(ValueError, match="resource limit") as caught:
            graph.strongly_connected_components(nodes, edges, metrics=metrics, **ceiling)
        assert (nodes, edges) == original
        assert metrics["refusal_reason"] == str(caught.value)
        assert sys.getrecursionlimit() == before


def test_iterative_scc_matches_reachability_oracle():
    # Exhaust every directed graph on three vertices, independent of Tarjan.
    from itertools import product
    nodes = ["a", "b", "c"]
    possible = list(product(nodes, repeat=2))
    for mask in range(1 << len(possible)):
        edges = [edge for index, edge in enumerate(possible) if mask & (1 << index)]
        reachable = {}
        for start in nodes:
            seen, pending = {start}, [start]
            while pending:
                current = pending.pop()
                for source, target in edges:
                    if source == current and target not in seen:
                        seen.add(target)
                        pending.append(target)
            reachable[start] = seen
        expected = sorted({tuple(node for node in nodes if node in reachable[start]
                                 and start in reachable[node]) for start in nodes})
        assert graph.strongly_connected_components(nodes, edges) == [list(row) for row in expected]



def test_iterative_scc_explicit_node_limit():
    import sys
    nodes = ["a", "b", "c"]
    original = list(nodes)
    before = sys.getrecursionlimit()
    metrics = {}
    assert graph.strongly_connected_components(nodes, [], max_nodes=3) == [["a"], ["b"], ["c"]]
    with pytest.raises(ValueError, match="node resource limit"):
        graph.strongly_connected_components(nodes, [], max_nodes=2, metrics=metrics)
    assert nodes == original and sys.getrecursionlimit() == before
