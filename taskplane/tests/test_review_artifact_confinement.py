import json
import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review_artifacts  # noqa: E402


def _model(revision="revision-1"):
    return {
        "schema": review_artifacts.ARTIFACT_MODEL_SCHEMA,
        "revision": {
            "id": revision,
            "fingerprint": "a" * 64,
            "disposition": "canonical",
            "status": "complete",
            "supersedes": None,
        },
        "dor": {"status": "ready"},
        "criteria": [],
        "slots": [],
        "findings": [],
        "validation": {"status": "executed"},
        "collection": {"status": "complete"},
        "provenance": {"target_fingerprint": "b" * 64},
        "gate": {"status": "awaiting-human", "approval_enabled": True},
    }


@pytest.mark.parametrize("root", ["", ".", "relative/artifacts"])
def test_publication_requires_an_explicit_absolute_root(root):
    with pytest.raises(review_artifacts.ArtifactPublicationError,
                       match="absolute"):
        review_artifacts.publish_revision_artifacts(root, _model())


def test_nonexistent_root_beneath_symlinked_ancestor_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    with pytest.raises(review_artifacts.ArtifactPublicationError,
                       match="symlink"):
        review_artifacts.publish_revision_artifacts(
            str(alias / "new" / "artifacts"), _model())

    assert list(outside.iterdir()) == []


def test_objects_symlink_cannot_publish_outside_root(tmp_path):
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "objects").symlink_to(outside, target_is_directory=True)

    result = review_artifacts.publish_revision_artifacts(str(root), _model())

    assert result["status"] == "unavailable"
    assert result["approval_enabled"] is False
    assert "symlink" in result["reason"]
    assert list(outside.iterdir()) == []
    assert not (root / "artifact-set.json").exists()


def test_manifest_symlink_is_rejected_without_touching_target(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("do not replace")
    (root / "artifact-set.json").symlink_to(outside)

    result = review_artifacts.publish_revision_artifacts(str(root), _model())

    assert result["status"] == "unavailable"
    assert result["approval_enabled"] is False
    assert "manifest" in result["reason"]
    assert outside.read_text() == "do not replace"
    assert (root / "artifact-set.json").is_symlink()


def test_failed_republication_keeps_previous_lossless_set(tmp_path):
    root = tmp_path / "artifacts"
    first = review_artifacts.publish_revision_artifacts(str(root), _model())
    manifest_before = (root / "artifact-set.json").read_bytes()
    manifest = json.loads(manifest_before)
    artifacts_before = {
        kind: (root / ref["relative_path"]).read_bytes()
        for kind, ref in manifest["artifacts"].items()
    }

    failed = review_artifacts.publish_revision_artifacts(
        str(root), _model("revision-2"), fault="before-manifest")

    assert first["status"] == "published"
    assert failed["status"] == "unavailable"
    assert (root / "artifact-set.json").read_bytes() == manifest_before
    for kind, ref in manifest["artifacts"].items():
        assert (root / ref["relative_path"]).read_bytes() == artifacts_before[kind]

