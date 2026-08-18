import json
import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review_artifacts  # noqa: E402


def _model(*, findings=3, disposition="canonical"):
    rows = []
    for index in range(findings):
        rows.append({
            "id": f"F-{index:03d}", "lens": "security",
            "severity": "high" if index % 2 else "medium",
            "title": f"Finding {index}", "file": "src/server.py",
            "line": index + 1,
            "scenario": "x" * 1100,
            "evidence": [{"kind": "source", "reference": f"ev-{index}"}],
            "provenance": [{"slot_id": "security", "result": f"r-{index}"}],
        })
    complete = disposition == "canonical"
    return {
        "schema": "taskplane.review-artifact-model/v1",
        "revision": {
            "id": "revision-2", "fingerprint": "a" * 64,
            "disposition": disposition, "status": (
                "complete" if complete else "incomplete"),
            "supersedes": "revision-1" if complete else None,
        },
        "dor": {
            "status": "ready", "sources": [{
                "kind": "pr-body", "identity": "PR-1",
                "revision": "abc123", "status": "available",
                "provenance": "source-1",
            }],
            "objectives": ["Ship the requested review change"],
        },
        "criteria": [{
            "id": "AC1", "text": "The review is lossless",
            "verdict": "pass", "rationale": "all formats round trip",
            "evidence": ["artifact-set"], "verification": "round-trip",
            "responsible": "artifact-publisher",
        }],
        "slots": [{
            "slot_id": "security", "lens_ids": ["security"],
            "status": "valid", "result_fingerprint": "b" * 64,
        }],
        "findings": rows,
        "validation": {
            "status": "executed", "submitted_pr": "failed",
            "sandbox": "passed", "evidence": ["command-1"],
        },
        "collection": {
            "status": "complete" if complete else "incomplete",
            "expected": 1, "collected": 1, "gaps": [],
        },
        "provenance": {
            "target_fingerprint": "c" * 64,
            "context_fingerprint": "d" * 64,
            "run_id": "run-1",
        },
        "gate": {
            "status": "awaiting-human" if complete else "blocked",
            "approval_enabled": complete,
            "reason": "human disposition" if complete else "incomplete review",
        },
    }


@pytest.mark.parametrize("disposition", ["provisional", "canonical"])
def test_publish_writes_three_lossless_semantically_equal_formats(
        tmp_path, disposition):
    model = _model(findings=120, disposition=disposition)

    result = review_artifacts.publish_revision_artifacts(
        str(tmp_path), model)

    assert result["status"] == "published"
    assert result["completed"] is (disposition == "canonical")
    assert result["finding_count"] == 120
    manifest = json.loads((tmp_path / "artifact-set.json").read_text())
    decoded = {}
    for kind, ref in manifest["artifacts"].items():
        path = tmp_path / ref["relative_path"]
        assert path.is_file()
        decoded[kind] = review_artifacts.parse_artifact(kind, path.read_bytes())
        assert ref["bytes"] == path.stat().st_size
    assert decoded["json"] == decoded["markdown"] == decoded["html"]
    assert decoded["json"] == review_artifacts.sanitize_model(model)["model"]
    assert len(decoded["json"]["findings"]) == 120
    assert (tmp_path / manifest["artifacts"]["markdown"]["relative_path"]).stat().st_size >= 126 * 1024
    assert (tmp_path / manifest["artifacts"]["html"]["relative_path"]).stat().st_size >= 342 * 1024


def test_multimegabyte_evidence_is_preserved_without_semantic_truncation(tmp_path):
    model = _model()
    model["dor"]["sources"][0]["content"] = "e" * (2 * 1024 * 1024)

    result = review_artifacts.publish_revision_artifacts(str(tmp_path), model)
    manifest = json.loads((tmp_path / "artifact-set.json").read_text())
    for kind, ref in manifest["artifacts"].items():
        decoded = review_artifacts.parse_artifact(
            kind, (tmp_path / ref["relative_path"]).read_bytes())
        assert decoded["dor"]["sources"][0]["content"] == "e" * (2 * 1024 * 1024)
    assert result["semantic_bytes"] > 2 * 1024 * 1024


def test_publication_redacts_secrets_and_personal_absolute_paths(tmp_path):
    model = _model()
    model["validation"]["log"] = (
        "Authorization: Bearer abc.def.ghi from /Users/alice/private/repo "
        "token=supersecretvalue")

    review_artifacts.publish_revision_artifacts(str(tmp_path), model)
    manifest = json.loads((tmp_path / "artifact-set.json").read_text())
    for ref in manifest["artifacts"].values():
        raw = (tmp_path / ref["relative_path"]).read_text()
        assert "abc.def.ghi" not in raw
        assert "supersecretvalue" not in raw
        assert "/Users/alice/private/repo" not in raw
    decoded = review_artifacts.parse_artifact(
        "json", (tmp_path / manifest["artifacts"]["json"]["relative_path"]).read_bytes())
    assert decoded["validation"]["log"].count("[REDACTED]") == 3


def test_output_root_and_published_paths_reject_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(review_artifacts.ArtifactPublicationError, match="symlink"):
        review_artifacts.publish_revision_artifacts(str(linked), _model())


def test_failure_does_not_advertise_partial_set_or_replace_stable_manifest(
        tmp_path):
    first = review_artifacts.publish_revision_artifacts(str(tmp_path), _model())
    stable = (tmp_path / "artifact-set.json").read_bytes()
    changed = _model(disposition="provisional")
    changed["revision"]["id"] = "revision-3"

    failed = review_artifacts.publish_revision_artifacts(
        str(tmp_path), changed, fault="before-manifest")

    assert failed["status"] == "unavailable"
    assert failed["completed"] is False
    assert failed["approval_enabled"] is False
    assert failed["action"] == "retry artifact publication"
    assert (tmp_path / "artifact-set.json").read_bytes() == stable
    assert first["manifest_fingerprint"] == json.loads(stable)["fingerprint"]


@pytest.mark.parametrize("missing", [
    "dor", "criteria", "slots", "findings", "validation", "collection",
    "provenance", "gate",
])
def test_missing_governance_section_is_a_stable_non_success(tmp_path, missing):
    model = _model()
    del model[missing]

    result = review_artifacts.publish_revision_artifacts(str(tmp_path), model)

    assert result["status"] == "unavailable"
    assert result["completed"] is False
    assert result["approval_enabled"] is False
    assert missing in result["reason"]
    assert not (tmp_path / "artifact-set.json").exists()


def test_small_review_is_deterministic_and_existing_manifest_is_idempotent(
        tmp_path):
    model = _model(findings=1)
    first = review_artifacts.publish_revision_artifacts(str(tmp_path), model)
    before = (tmp_path / "artifact-set.json").read_bytes()
    second = review_artifacts.publish_revision_artifacts(str(tmp_path), model)

    assert second == first
    assert (tmp_path / "artifact-set.json").read_bytes() == before
