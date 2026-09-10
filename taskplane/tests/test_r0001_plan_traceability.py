from __future__ import annotations

from collections import Counter
import copy

import pytest

from taskplane import plan_topology


_SOURCE_LIMITS = {
    "local_depth": 3,
    "max_fanout": 2,
    "max_elapsed_ms": 10,
    "parsers": ["declarative", "python-ast", "runtime-probe"],
    "languages": ["data", "python", "runtime"],
    "policy": "approved",
}
_BOUND_SOURCE = [
    {"id": "file:taskplane/depgraph.py", "kind": "file"},
    {"id": "module:taskplane", "kind": "module"},
    {"id": "symbol:depgraph.scan", "kind": "symbol"},
    {"id": "configuration:components.yaml", "kind": "configuration"},
    {
        "id": "contract:taskplane-source-touchpoint-coverage-v1",
        "kind": "contract",
    },
    {"id": "runtime:tp-graph-scan-strict", "kind": "runtime"},
]
_SOURCE_STOP_CASES = [
    ("verified", False, "unverified"),
    ("state", "missing", "missing"),
    ("state", "ambiguous", "ambiguous"),
    ("state", "unsupported", "unsupported"),
    ("state", "truncated", "truncated"),
    ("state", "rejected", "rejected"),
    ("parser", "unknown-parser", "parser"),
    ("language", "unknown-language", "language"),
    ("policy", "denied", "policy"),
    ("depth", 4, "depth"),
    ("fanout", 3, "fan-out"),
    ("elapsed_ms", 11, "time"),
]
_SOURCE_STOP_IDS = (
    "unverified",
    "missing",
    "ambiguous",
    "unsupported",
    "truncated",
    "rejected",
    "parser",
    "language",
    "policy",
    "depth",
    "fan-out",
    "time",
)


def _source_observations() -> dict[str, dict]:
    observations = {}
    for row in _BOUND_SOURCE:
        kind = row["kind"]
        observations[row["id"]] = {
            "verified": True,
            "state": "present",
            "source_fingerprint": f"sha256:{kind}",
            "parser": "python-ast" if kind in {"file", "module", "symbol"}
            else "runtime-probe" if kind == "runtime"
            else "declarative",
            "language": "python" if kind in {"file", "module", "symbol"}
            else "runtime" if kind == "runtime"
            else "data",
            "policy": "approved",
            "depth": 1,
            "fanout": 1,
            "elapsed_ms": 1,
        }
    return observations


def _assert_source_stop_case(monkeypatch, field, value, stop_reason):
    depgraph = plan_topology._depgraph
    observations = _source_observations()
    target = _BOUND_SOURCE[0]["id"]
    observations[target][field] = value
    calls = Counter()

    def verify(bound_input):
        calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    receipt = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=verify,
    )
    assert receipt["status"] == "partial"
    assert receipt["complete"] is False
    assert stop_reason in receipt["touchpoints"][target]["stop_reasons"]
    assert {row["reason"] for row in receipt["stopping_conditions"]} >= {
        stop_reason
    }
    assert calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})
    with pytest.raises(ValueError, match="source coverage is partial"):
        depgraph.require_complete_source_coverage(receipt)
    with monkeypatch.context() as patch:
        patch.setattr(
            depgraph.graph_decomposition,
            "derive",
            lambda *_args, **_kwargs: pytest.fail(
                "partial coverage reached decomposition"
            ),
        )
        with pytest.raises(ValueError, match="source coverage is partial"):
            depgraph.derive_verified_source(
                "unused",
                {"meta": {"scanned_head": "source-tree-a"}},
                receipt,
            )


def _assert_live_scan_coverage_gate(monkeypatch, tmp_path):
    depgraph = plan_topology._depgraph
    decomposition = depgraph.graph_decomposition
    (tmp_path / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    original_coverage = decomposition.build_source_touchpoint_coverage

    def scan_with(coverage_builder):
        events = []

        def observe_coverage(*args, **kwargs):
            events.append("coverage")
            return coverage_builder(*args, **kwargs)

        def observe_derive(_workspace, _graph, _previous):
            events.append("derive")
            return ([{"id": "sample::core"}], {
                "components": 1,
                "recomputed": 1,
                "cache_hits": 0,
                "floor_folded": 0,
                "floors_hash": "floors",
                "failures": [],
                "degraded": [],
                "error": None,
            })

        with monkeypatch.context() as patch:
            patch.setattr(depgraph, "load", lambda _workspace: depgraph._empty())
            patch.setattr(
                depgraph, "_git_candidates", lambda _workspace: ["sample.py"]
            )
            patch.setattr(
                depgraph, "load_excludes", lambda _workspace: ([], None)
            )
            patch.setattr(
                depgraph,
                "architecture_map_proof",
                lambda _workspace, **_kwargs: {
                    "status": "not-requested",
                    "complete": False,
                },
            )
            patch.setattr(depgraph, "_git_head", lambda _workspace: "source-tree-a")
            patch.setattr(depgraph.audit_projection, "trace", lambda *_args, **_kwargs: None)
            patch.setattr(
                decomposition,
                "build_source_touchpoint_coverage",
                observe_coverage,
            )
            patch.setattr(decomposition, "derive", observe_derive)
            graph = depgraph._scan_locked(
                str(tmp_path), into={}, decompose=True
            )
        return events, graph

    complete_events, complete_graph = scan_with(original_coverage)
    assert complete_events == ["coverage", "derive"]
    assert complete_graph["components"] == [{"id": "sample::core"}]
    assert complete_graph["meta"]["source_coverage"]["status"] == "complete"

    def partial_coverage(source_tree, bound_inputs, *, limits, verifier):
        first = bound_inputs[0]["id"]

        def partial_verifier(bound_input):
            observed = verifier(bound_input)
            if bound_input["id"] == first:
                observed["state"] = "missing"
            return observed

        return original_coverage(
            source_tree,
            bound_inputs,
            limits=limits,
            verifier=partial_verifier,
        )

    partial_events, partial_graph = scan_with(partial_coverage)
    assert partial_events == ["coverage"]
    assert "components" not in partial_graph
    assert partial_graph["meta"]["source_coverage"]["status"] == "partial"
    assert partial_graph["meta"]["graph_scan_quality"]["degraded"] is True


@pytest.mark.parametrize(
    ("coverage_field", "coverage_value", "coverage_stop_reason"),
    _SOURCE_STOP_CASES,
    ids=_SOURCE_STOP_IDS,
)
def test_source_coverage_refuses_incomplete_inputs(
    monkeypatch, tmp_path, coverage_field, coverage_value, coverage_stop_reason,
):
    _assert_source_stop_case(
        monkeypatch, coverage_field, coverage_value, coverage_stop_reason
    )
    if coverage_stop_reason == "unverified":
        _assert_live_scan_coverage_gate(monkeypatch, tmp_path)


def test_bound_source_coverage_is_deterministic_complete_and_verified_once(
    monkeypatch,
):
    depgraph = plan_topology._depgraph
    observations = _source_observations()
    calls = Counter()

    def verify(bound_input):
        calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    receipt = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=verify,
    )

    assert receipt["schema"] == "taskplane.source-touchpoint-coverage/v1"
    assert receipt["status"] == "complete"
    assert receipt["complete"] is True
    assert receipt["stopping_conditions"] == []
    assert receipt["limits"] == _SOURCE_LIMITS
    assert set(receipt["touchpoints"]) == {
        row["id"] for row in _BOUND_SOURCE
    }
    assert set(receipt["kinds"]) == {
        "file",
        "module",
        "symbol",
        "configuration",
        "contract",
        "runtime",
    }
    assert calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})

    replay_calls = Counter()

    def replay_verify(bound_input):
        replay_calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    replay = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        list(reversed(_BOUND_SOURCE)),
        limits=dict(reversed(list(_SOURCE_LIMITS.items()))),
        verifier=replay_verify,
    )
    assert replay == receipt
    assert replay_calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})

    changed_observations = copy.deepcopy(observations)
    changed_observations[_BOUND_SOURCE[0]["id"]]["source_fingerprint"] = (
        "sha256:changed"
    )
    changed = depgraph.build_source_touchpoint_coverage(
        "source-tree-b",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=lambda row: copy.deepcopy(changed_observations[row["id"]]),
    )
    assert changed["fingerprint"] != receipt["fingerprint"]

    with pytest.raises(ValueError, match="source tree"):
        depgraph.derive_verified_source(
            "unused", {"meta": {"scanned_head": "source-tree-b"}}, receipt
        )

    expected = ([{"id": "taskplane::core"}], {"components": 1})
    with monkeypatch.context() as patch:
        patch.setattr(
            depgraph.graph_decomposition,
            "derive",
            lambda workspace, graph, previous: expected,
        )
        assert depgraph.derive_verified_source(
            "unused",
            {"meta": {"scanned_head": "source-tree-a"}},
            receipt,
        ) == expected
