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
