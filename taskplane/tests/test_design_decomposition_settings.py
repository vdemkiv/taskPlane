from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from taskplane import depgraph, plan_topology, run_artifacts
from taskplane.settings import DEFAULT_SETTINGS_PATH, SettingsError, load_settings


def _write_settings(tmp_path: Path, *, mandatory: list[str], maximum: int
                    ) -> Path:
    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["lenses"]["routing"]["design"] = mandatory
    value["lenses"]["counts"]["design"] = maximum
    path = tmp_path / "operational-settings.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _repository(tmp_path: Path) -> Path:
    workspace = tmp_path / "repository"
    for directory in ("renderer", "store", "gateway", "core"):
        root = workspace / "src" / "app" / directory
        root.mkdir(parents=True)
        for index in range(2):
            (root / f"part_{index}.py").write_text(
                f"def {directory}_{index}():\n    return {index}\n",
                encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=workspace,
                   check=True)
    return workspace


def _design_binding(receipt: dict, settings_digest: str, *,
                    run_id: str = "run-design-1") -> dict:
    material = {
        "schema": "taskplane.design-control-plane-binding/v1",
        "run_id": run_id,
        "stage_instance_id": "design-stage-1",
        "requirement": "R-DESIGN",
        "requirement_fingerprint": "1" * 64,
        "goal_fingerprint": "2" * 64,
        "candidate_fingerprint": "3" * 64,
        "catalog_fingerprint": "4" * 64,
        "settings_digest": settings_digest,
        "decomposition_fingerprint": receipt["fingerprint"],
        "lens_policy": {
            "stage": "design", "mandatory": ["solution-design"],
            "max_count": 16, "dynamic": True,
        },
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return {**material, "fingerprint": hashlib.sha256(encoded).hexdigest()}


def _artifact_root(tmp_path: Path, receipt: dict, settings_digest: str,
                   *, stage_id: str = "design") -> Path:
    parent = tmp_path / "private-run"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / "artifacts"
    binding = run_artifacts.create_binding(
        repository_id="github.com/example/design",
        run_id="run-design-1",
        stage_id=stage_id,
        stage_instance_id="design-stage-1",
        candidate={"fingerprint": "3" * 64},
        settings_digest=settings_digest,
        source_fingerprint=receipt["graph_fingerprint"],
    )
    run_artifacts.create_manifest(root, binding=binding)
    return root


def test_design_policy_is_dynamic_and_configured_by_the_canonical_values(
        tmp_path):
    defaults = load_settings()
    catalog = ["solution-design", "security", "qa"]
    default_policy = defaults.lenses.policy_for(
        "design", catalog_ids=catalog)
    assert default_policy.mandatory == ("solution-design",)
    assert default_policy.max_count == 16
    assert default_policy.dynamic is True

    configured = load_settings(_write_settings(
        tmp_path, mandatory=["solution-design", "security"], maximum=7))
    policy = configured.lenses.policy_for("design", catalog_ids=catalog)
    assert policy.to_dict() == {
        "stage": "design",
        "mandatory": ["solution-design", "security"],
        "max_count": 7,
        "dynamic": True,
    }
    assert policy != default_policy

    invalid_catalog = load_settings(_write_settings(
        tmp_path, mandatory=["solution-design", "invented"], maximum=7))
    with pytest.raises(SettingsError, match="unknown catalog ids"):
        invalid_catalog.lenses.policy_for("design", catalog_ids=catalog)

    for stage in ("build", "evaluate", "fix"):
        zero = configured.lenses.policy_for(stage)
        assert zero.mandatory == ()
        assert zero.max_count == 0
        assert zero.dynamic is False


def test_design_policy_refuses_overflow_and_zero_lens_stage_weakening(tmp_path):
    with pytest.raises(SettingsError, match="cannot exceed 16"):
        load_settings(_write_settings(
            tmp_path, mandatory=["solution-design"], maximum=17))

    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["lenses"]["routing"]["evaluate"] = ["qa"]
    value["lenses"]["counts"]["evaluate"] = 1
    path = tmp_path / "invalid-zero-lens.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SettingsError, match="evaluate.*zero lens"):
        load_settings(path)


def test_design_decomposition_expands_graph_paths_and_refreshes_fingerprints(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    workspace = _repository(tmp_path)
    settings = load_settings()

    first = depgraph.prepare_design_decomposition(
        str(workspace), ["src/app/renderer/**"],
        settings_digest=settings.digest)

    assert first["status"] == "ready"
    assert first["head"] == first["scanned_head"]
    assert first["context"] == {
        "mode": "declared",
        "patterns": ["src/app/renderer/**"],
        "expanded_files": [
            "src/app/renderer/part_0.py",
            "src/app/renderer/part_1.py",
        ],
        "unmatched_patterns": [],
    }
    assert first["component_count"] >= 4
    assert first["selected_component_count"] == 1
    assert first["components"][0]["id"] == "renderer::core"
    assert first["components"][0]["files"] == \
        first["context"]["expanded_files"]
    for field in (
            "graph_fingerprint", "component_fingerprint",
            "floors_fingerprint", "settings_digest", "quality_fingerprint",
            "fingerprint"):
        assert len(first[field]) == 64

    artifacts = _artifact_root(tmp_path, first, settings.digest)
    reference = depgraph.publish_design_decomposition(
        str(workspace), artifacts, first)
    assert reference["class"] == "dependency-graphs"
    assert reference["binding"]["run_id"] == "run-design-1"
    assert reference["binding"]["stage_id"] == "design"
    assert reference["metadata"]["receipt_fingerprint"] == first[
        "fingerprint"]
    persisted = json.loads(
        (artifacts / reference["locator"]).read_text(encoding="utf-8"))
    assert persisted == first
    verification = run_artifacts.verify_manifest(artifacts)
    assert verification["class_counts"]["dependency-graphs"] == 1

    binding = _design_binding(first, settings.digest)
    projection = plan_topology.phase_graph_projection(
        str(workspace), {
            "step": "design",
            "run_id": "run-design-1",
            "requirement_id": "R-DESIGN",
            "settings_digest": settings.digest,
            "design_decomposition_receipt": first,
            "design_control_plane_binding": binding,
        }, require_bound=True)
    visible = projection["design_graph"]
    assert visible["graph_state"] == "ready"
    assert visible["run_id"] == "run-design-1"
    assert visible["stage_instance_id"] == "design-stage-1"
    assert visible["requirement"] == "R-DESIGN"
    assert visible["settings_digest"] == settings.digest
    assert visible["candidate_fingerprint"] == "3" * 64
    assert visible["binding_fingerprint"] == binding["fingerprint"]
    rendered = plan_topology.render_phase_dependency_graphs(projection)
    assert "Design component dependency graph" in rendered
    assert "state ready" in rendered

    changed_file = workspace / "src" / "app" / "renderer" / "part_0.py"
    changed_file.write_text(
        "def renderer_0():\n    return 'changed'\n", encoding="utf-8")
    changed = depgraph.prepare_design_decomposition(
        str(workspace), ["src/app/renderer/**"],
        settings_digest=settings.digest)

    assert changed["head"] == first["head"]
    assert changed["graph_fingerprint"] != first["graph_fingerprint"]
    assert changed["component_fingerprint"] != first["component_fingerprint"]
    assert changed["fingerprint"] != first["fingerprint"]

    configured = load_settings(_write_settings(
        tmp_path, mandatory=["solution-design", "security"], maximum=7))
    rebound = depgraph.prepare_design_decomposition(
        str(workspace), ["src/app/renderer/**"],
        settings_digest=configured.digest)
    assert rebound["component_fingerprint"] == changed[
        "component_fingerprint"]
    assert rebound["settings_digest"] != changed["settings_digest"]
    assert rebound["fingerprint"] != changed["fingerprint"]


def test_design_decomposition_reports_degradation_and_refuses_unsafe_scope(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    workspace = _repository(tmp_path)
    settings = load_settings()
    (workspace / "components.yaml").write_text(
        "floors:\n  - unsupported-list\n", encoding="utf-8")

    degraded = depgraph.prepare_design_decomposition(
        str(workspace), ["src/app/**"], settings_digest=settings.digest)

    assert degraded["status"] == "degraded"
    assert degraded["degraded"] is True
    assert "graph-scan-quality" in degraded["degraded_reasons"]
    assert degraded["component_count"] >= 4
    assert degraded["quality_fingerprint"]

    binding = _design_binding(degraded, settings.digest)
    projection = plan_topology.phase_graph_projection(
        str(workspace), {
            "step": "design",
            "run_id": "run-design-1",
            "requirement_id": "R-DESIGN",
            "settings_digest": settings.digest,
            "design_decomposition_receipt": degraded,
            "design_control_plane_binding": binding,
        }, require_bound=True)
    assert projection["design_graph"]["graph_state"] == "degraded"
    assert "graph-scan-quality" in projection[
        "design_graph"]["degraded_reasons"]
    assert "state degraded" in \
        plan_topology.render_phase_dependency_graphs(projection)

    stale = dict(degraded)
    stale["settings_digest"] = "f" * 64
    with pytest.raises(ValueError, match="fingerprint is stale"):
        depgraph.validate_design_decomposition_receipt(stale)

    foreign_root = _artifact_root(
        tmp_path / "foreign", degraded, "e" * 64)
    with pytest.raises(ValueError, match="settings do not match"):
        depgraph.publish_design_decomposition(
            str(workspace), foreign_root, degraded)

    with pytest.raises(ValueError, match="safe relative paths"):
        depgraph.prepare_design_decomposition(
            str(workspace), ["../outside/**"], settings_digest=settings.digest)


def test_reused_requirement_id_does_not_activate_an_unrelated_architecture_map(
        tmp_path, monkeypatch):
    """A private knowledge store may legitimately reuse a local R-id.

    Architecture authority is explicitly opted into by its typed Design
    section; a bare requirement identifier from an older store is not proof
    that the current design accepted that unrelated immutable map.
    """
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    workspace = _repository(tmp_path)
    design = workspace / "design"
    design.mkdir()
    (design / "contract.json").write_text(json.dumps({
        "schema": "taskplane.design/v1",
        "requirement": "R-0002",
        "graph": {"proposed_modules": [], "proposed_edges": []},
    }), encoding="utf-8")

    proof = depgraph.architecture_map_proof(str(workspace))

    assert proof["configured"] is False
    assert proof["status"] == "not-requested"
    assert proof["errors"] == []
