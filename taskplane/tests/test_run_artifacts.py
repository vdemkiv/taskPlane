"""Behavioral contract for private, candidate-bound run artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from taskplane import run_artifacts, run_store, storage


def _binding(**changes) -> dict:
    values = {
        "repository_id": "github.com/example/project",
        "run_id": "run-2",
        "stage_id": "build",
        "stage_instance_id": "build-3",
        "candidate": {
            "id": "working-candidate-7",
            "fingerprint": "a" * 64,
            "source": "b" * 64,
            "tests": "c" * 64,
            "working_tree": "d" * 64,
        },
        "settings_digest": "e" * 64,
        "source_fingerprint": "f" * 64,
    }
    values.update(changes)
    return run_artifacts.create_binding(**values)


def _root(tmp_path: Path, *, binding: dict | None = None) -> Path:
    run_root = tmp_path / "home" / "runs" / "run-2"
    run_root.mkdir(parents=True)
    root = run_root / "artifacts"
    run_artifacts.create_manifest(root, binding=binding or _binding())
    return root


def test_manifest_has_seven_private_classes_and_run_store_only_keeps_locator(
        tmp_path):
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    store = run_store.RunStore(home=str(tmp_path / "store"))
    run = store.create(
        identity, run_id="run-2", checkout=str(tmp_path / "checkout"),
        host={"kind": "codex"}, target={"kind": "workspace"})

    assert run["run_artifacts"] == \
        run_artifacts.manifest_locator_reference()
    assert set(run["run_artifacts"]) == {"schema", "locator"}
    assert not any(key in run["run_artifacts"] for key in (
        "status", "outcome", "stage", "revision", "classes"))
    with pytest.raises(run_store.RunStoreError, match="owned fields"):
        store.commit(
            "run-2", expected_revision=1,
            changes={"run_artifacts": {"status": "terminal"}})

    root = Path(run["paths"]["artifacts"])
    run_artifacts.create_manifest(root, binding=_binding())
    manifest = run_artifacts.load_manifest(root)
    assert set(manifest["classes"]) == set(run_artifacts.ARTIFACT_CLASSES)
    assert manifest["binding"] == _binding()
    assert "status" not in manifest and "outcome" not in manifest
    if os.name != "nt":
        for artifact_class in run_artifacts.ARTIFACT_CLASSES:
            info = (root / artifact_class).stat()
            assert stat.S_IMODE(info.st_mode) == 0o700
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(
            (root / run_artifacts.MANIFEST_NAME).stat().st_mode) == 0o600


def test_all_artifacts_and_activity_repeat_full_working_binding_and_verify(
        tmp_path):
    binding = _binding()
    root = _root(tmp_path, binding=binding)
    for artifact_class in (
            "dashboard", "dependency-graphs", "telemetry", "validation",
            "cleanup", "retro"):
        reference = run_artifacts.publish_artifact(
            root, artifact_class, {"producer": artifact_class},
            metadata={"source": artifact_class})
        assert reference["class"] == artifact_class
        assert reference["binding"] == binding
        assert reference["locator"].startswith(artifact_class + "/")

    activity = []
    for event_type in (
            "assignment", "worker-identity", "start", "progress",
            "attention", "usage-reference", "evidence-reference",
            "terminal"):
        activity.append(run_artifacts.append_activity(
            root,
            event_type=event_type,
            agent_attempt_id="attempt-4",
            worker_id="worker-7",
            task_id="RUN-ARTIFACT-BOUNDARY",
            lens="zero-lens-build",
            details={"outcome": "success"} if event_type == "terminal" else
                    {"state": event_type},
            usage_reference={"fingerprint": "1" * 64}
            if event_type == "usage-reference" else None,
            evidence_references=[{"fingerprint": "2" * 64}]
            if event_type == "evidence-reference" else (),
            occurred_at_ns=100 + len(activity)))

    assert [row["sequence"] for row in activity] == list(range(1, 9))
    assert all(row["binding"] == binding for row in activity)
    persisted_event = json.loads(
        (root / activity[-1]["locator"]).read_text(encoding="utf-8"))
    assert persisted_event["schema"] == run_artifacts.ACTIVITY_SCHEMA
    assert persisted_event["binding"] == binding
    manifest = run_artifacts.load_manifest(root)
    assert [row["metadata"]["event_type"] for row in
            manifest["classes"]["agent-activity"]["entries"]] == [
        "assignment", "worker-identity", "start", "progress", "attention",
        "usage-reference", "evidence-reference", "terminal",
    ]
    verified = run_artifacts.verify_manifest(root, expected_binding=binding)
    assert verified["readable"] is True
    assert verified["zero_unindexed_files"] is True
    assert verified["artifact_count"] == 14
    assert all(verified["class_counts"][name] > 0
               for name in run_artifacts.ARTIFACT_CLASSES)


def test_manifest_refuses_foreign_binding_unallowlisted_class_and_bad_events(
        tmp_path):
    root = _root(tmp_path)
    with pytest.raises(run_artifacts.RunArtifactError, match="another binding"):
        run_artifacts.verify_manifest(
            root, expected_binding=_binding(stage_instance_id="build-4"))
    with pytest.raises(run_artifacts.RunArtifactError, match="allowlisted"):
        run_artifacts.publish_artifact(root, "misc", b"foreign")
    with pytest.raises(run_artifacts.RunArtifactError, match="needs an outcome"):
        run_artifacts.append_activity(
            root, event_type="terminal", agent_attempt_id="attempt-4",
            worker_id="worker-7", task_id="task-7", lens="zero-lens",
            details={})
    with pytest.raises(run_artifacts.RunArtifactError,
                       match="candidate fingerprint"):
        _binding(candidate={"id": "partial", "fingerprint": "short"})


@pytest.mark.parametrize("case", [
    "symlink-class", "hardlink-object", "unindexed-object", "changed-object",
])
def test_verification_refuses_aliases_unindexed_files_and_changed_bytes(
        tmp_path, case):
    root = _root(tmp_path)
    reference = run_artifacts.publish_artifact(
        root, "validation", {"result": "green"})
    artifact = root / reference["locator"]
    if case == "symlink-class":
        foreign = tmp_path / "foreign-validation"
        foreign.mkdir()
        artifact.unlink()
        (root / "validation").rmdir()
        (root / "validation").symlink_to(foreign, target_is_directory=True)
    elif case == "hardlink-object":
        os.link(artifact, tmp_path / "foreign-hardlink.json")
    elif case == "unindexed-object":
        (root / "validation" / ("00000002-" + "9" * 64 + ".json")).write_text(
            "foreign\n", encoding="utf-8")
    else:
        artifact.write_text('{"result":"substituted"}\n', encoding="utf-8")

    with pytest.raises(run_artifacts.RunArtifactError):
        run_artifacts.verify_manifest(root)


def test_atomic_bounded_writes_leave_foreign_symlink_target_unchanged(
        tmp_path, monkeypatch):
    root = _root(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    marker = foreign / "marker.json"
    marker.write_text('{"owned":false}\n', encoding="utf-8")
    (root / "telemetry").rmdir()
    (root / "telemetry").symlink_to(foreign, target_is_directory=True)

    with pytest.raises(run_artifacts.RunArtifactError, match="unavailable"):
        run_artifacts.publish_artifact(root, "telemetry", {"owned": True})
    assert json.loads(marker.read_text(encoding="utf-8")) == {"owned": False}

    safe = _root(tmp_path / "bounded")
    monkeypatch.setattr(run_artifacts, "_MAX_ARTIFACT_BYTES", 8)
    with pytest.raises(run_artifacts.RunArtifactError, match="byte bound"):
        run_artifacts.publish_artifact(safe, "telemetry", b"123456789")
    assert run_artifacts.load_manifest(safe)["revision"] == 0


def test_portable_backend_is_semantic_private_atomic_and_alias_refusing(
        tmp_path, monkeypatch):
    monkeypatch.setattr(run_artifacts.os, "supports_dir_fd", set())
    root = _root(tmp_path)
    activity = []
    for event_type, outcome in (
            ("cancel", "cancellation"),
            ("interruption", "interruption"),
            ("handoff", "handoff")):
        activity.append(run_artifacts.append_activity(
            root, event_type=event_type, agent_attempt_id="attempt-portable",
            worker_id="worker-portable", task_id="task-portable",
            lens="zero-lens-build", details={"outcome": outcome},
            occurred_at_ns=10 + len(activity)))
    artifact = run_artifacts.publish_artifact(
        root, "validation", {"backend": "portable", "result": "green"})

    verification = run_artifacts.verify_manifest(root)
    assert verification["readable"] is True
    assert verification["artifact_count"] == 4
    assert [row["metadata"]["event_type"] for row in
            run_artifacts.load_manifest(root)["classes"][
                "agent-activity"]["entries"]] == [
        "cancel", "interruption", "handoff"]
    assert [json.loads((root / row["locator"]).read_text(encoding="utf-8"))[
                "event"]["details"]["outcome"] for row in activity] == [
        "cancellation", "interruption", "handoff"]
    with pytest.raises(run_artifacts.RunArtifactError,
                       match="handoff activity needs outcome handoff"):
        run_artifacts.append_activity(
            root, event_type="handoff", agent_attempt_id="attempt-portable",
            worker_id="worker-portable", task_id="task-portable",
            lens="zero-lens-build", details={"outcome": "interruption"})
    if os.name != "nt":
        assert stat.S_IMODE(
            (root / run_artifacts.MANIFEST_NAME).stat().st_mode) == 0o600

    os.link(root / artifact["locator"], tmp_path / "portable-hardlink.json")
    with pytest.raises(run_artifacts.RunArtifactError, match="exact-owned"):
        run_artifacts.verify_manifest(root)

    alias_root = _root(tmp_path / "alias-portable")
    foreign = tmp_path / "portable-foreign"
    foreign.mkdir()
    (alias_root / "telemetry").rmdir()
    (alias_root / "telemetry").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(run_artifacts.RunArtifactError, match="alias"):
        run_artifacts.publish_artifact(
            alias_root, "telemetry", {"must_not_escape": True})
    assert list(foreign.iterdir()) == []
