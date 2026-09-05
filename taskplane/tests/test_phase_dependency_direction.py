"""Native dispatch composes lower phase adapters without reverse imports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskplane import import_cycles, phase_dispatch, phase_handoff


def test_phase_adapter_dependencies_are_directional_at_every_import_depth():
    root = Path(__file__).resolve().parents[2]
    modules = ("phase_admission", "phase_dispatch", "phase_review_host", "phase_handoff")
    sources = {"taskplane." + name: (
        "taskplane/" + name + ".py",
        (root / "taskplane" / (name + ".py")).read_text(encoding="utf-8")) for name in modules}
    graph = import_cycles._scan_graph(sources)
    assert graph["taskplane.phase_admission"].isdisjoint({
        "taskplane.phase_dispatch", "taskplane.phase_review_host"})
    assert "taskplane.phase_dispatch" not in graph["taskplane.phase_review_host"]
    assert "taskplane.phase_admission" in graph["taskplane.phase_review_host"]
    assert {"taskplane.phase_admission", "taskplane.phase_review_host"} <= graph["taskplane.phase_dispatch"]
    assert import_cycles._tarjan(graph) == []


def test_native_observer_and_catalog_do_not_reimport_their_callers():
    root = Path(__file__).resolve().parents[2]
    graph = import_cycles._scan_graph(import_cycles._working_sources(root))
    for start in ("taskplane.phase_output", "taskplane.lens_catalog", "taskplane.phase_handoff"):
        pending, reached = [start], set()
        while pending:
            name = pending.pop()
            if name not in reached:
                reached.add(name)
                pending.extend(graph[name])
        assert reached.isdisjoint({"taskplane.taskplane_lite", "taskplane.phase_producer",
                                   "taskplane.lens", "taskplane.loop"})


def test_empty_catalog_root_still_means_the_shipped_catalog(tmp_path, monkeypatch):
    from taskplane import lens_catalog

    expected = lens_catalog.load_catalog()
    monkeypatch.chdir(tmp_path)
    assert lens_catalog.load_catalog("") == expected


@pytest.mark.parametrize("phase,visual", [("design", False), ("design", True), ("plan", False)])
def test_owner_and_review_use_one_lower_exact_output_selector(monkeypatch, phase, visual):
    monkeypatch.setattr(phase_handoff, "_safe_regular_file", lambda *_args, **_kwargs:
        ("design/contract.json", json.dumps({"visualization": {
            "required": visual, "path": "design/visual.html"}}).encode()))
    monkeypatch.setattr(phase_handoff, "create_repository_artifact_reference",
        lambda workspace, path, **kwargs: {"workspace": workspace, "source": path, **kwargs})
    lower = phase_handoff.phase_output_references("fixture-workspace", phase, publish=False)
    owner = phase_dispatch.output_references("fixture-workspace", phase, publish=False)
    assert lower == owner
    assert [row["source"] for row in lower] == phase_handoff.phase_output_paths(phase)[:2] + (
        ["design/visual.html"] if visual else [])
    assert all(row["publish"] is False for row in lower)
