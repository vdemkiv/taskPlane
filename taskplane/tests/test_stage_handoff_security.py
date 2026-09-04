"""Handoff validation fails closed at every untrusted artifact boundary."""
from __future__ import annotations

from collections.abc import Iterator
import copy
from pathlib import Path
import subprocess

import pytest

from taskplane import phase_handoff, review_evidence, stage_handoff
from taskplane.tests.test_stage_handoff import _manifest
from taskplane.tests.test_stateless_phase_pickup import _published_checkout


def test_tampered_artifact_digest_and_byte_count_are_rejected(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)

    for field, replacement, message in (
        ("digest", "0" * 64, "digest"),
        ("bytes", 999999, "byte length"),
    ):
        tampered = copy.deepcopy(manifest)
        tampered["selected_artifacts"][0][field] = replacement
        tampered["fingerprint"] = stage_handoff.manifest_fingerprint(tampered)
        with pytest.raises(review_evidence.ArtifactIntegrityError,
                           match=message):
            stage_handoff.validate_manifest(store, tampered)


def test_host_paths_and_undeclared_context_cannot_enter_manifest(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    reference = manifest["selected_artifacts"][0]
    reference["path"] = "/tmp/attacker-controlled.json"
    manifest["fingerprint"] = stage_handoff.manifest_fingerprint(manifest)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="artifact reference.*unknown fields"):
        stage_handoff.validate_manifest(store, manifest)

    for forbidden in ("agents", "conversations", "event_logs", "tools",
                      "secrets", "approvals"):
        manifest = _manifest(store)
        manifest[forbidden] = ["injected"]
        manifest["fingerprint"] = stage_handoff.manifest_fingerprint(manifest)
        with pytest.raises(stage_handoff.HandoffValidationError,
                           match="unknown fields"):
            stage_handoff.validate_manifest(store, manifest)


def test_required_exclusions_are_explicit(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    exclusions = set(stage_handoff.REQUIRED_EXCLUSIONS)
    exclusions.remove("predecessor-conversations")

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="missing required exclusions"):
        _manifest(store, exclusions=sorted(exclusions))

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="evidence references are incomplete"):
        _manifest(store, evidence_references=[])


def test_reference_count_and_manifest_bytes_are_bounded(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    reference = store.put("delivery", {"value": 1})

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="at most 64"):
        _manifest(store, selected_artifacts=[reference] * 65)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="65536 bytes"):
        _manifest(store, deliverables=[f"{index:04d}-" + "x" * 195
                                      for index in range(400)])


def test_reference_generator_stops_at_the_pre_materialization_bound(
        tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    reference = store.put("delivery", {"value": 1})
    pulled = 0

    def unbounded_references() -> Iterator[dict[str, object]]:
        nonlocal pulled
        while True:
            pulled += 1
            if pulled > stage_handoff.MAX_ARTIFACT_REFERENCES + 1:
                raise AssertionError("reference iterator was over-consumed")
            yield reference

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="at most 64"):
        _manifest(store, selected_artifacts=unbounded_references())
    # _manifest supplies one evidence reference, so the 64th selected
    # artifact is the 65th combined entry and triggers rejection immediately.
    assert pulled == stage_handoff.MAX_ARTIFACT_REFERENCES


def test_stale_authority_and_discarded_default_consumption_are_rejected(
        tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)

    with pytest.raises(stage_handoff.StaleAuthorityError,
                       match="authority revision"):
        stage_handoff.validate_manifest(
            store, manifest, expected_authority_revision=8)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="discarded"):
        _manifest(store, producer_outcome="discarded")


def test_read_requires_matching_trusted_authority_fingerprint(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    reference = stage_handoff.store_manifest(store, manifest)

    with pytest.raises(TypeError, match="expected_authority_fingerprint"):
        stage_handoff.read_manifest(
            store, reference, expected_authority_revision=7)
    with pytest.raises(stage_handoff.StaleAuthorityError,
                       match="authority fingerprint"):
        stage_handoff.read_manifest(
            store, reference, expected_authority_revision=7,
            expected_authority_fingerprint="f" * 64)
    assert stage_handoff.read_manifest(
        store, reference, expected_authority_revision=7,
        expected_authority_fingerprint="a" * 64) == manifest


def test_manifest_fingerprint_tampering_is_rejected_before_use(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    manifest["authorization"]["actor"] = "attacker"

    with pytest.raises(stage_handoff.HandoffIntegrityError,
                       match="fingerprint mismatch"):
        stage_handoff.validate_manifest(store, manifest)


def _commit_change(root: Path, relative: str) -> None:
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "\n# scoped change\n",
                    encoding="utf-8")
    subprocess.run(["git", "add", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", f"change {relative}"],
                   cwd=root, check=True)


@pytest.mark.parametrize(
    ("changed", "accepted"),
    [
        ("taskplane/phase_handoff.py", True),
        ("taskplane/repository.py", False),
    ],
)
def test_build_submit_validation_allows_only_the_sealed_task_scope(
        tmp_path, changed: str, accepted: bool) -> None:
    root, handoff = _published_checkout(tmp_path, "build")
    _commit_change(root, changed)
    relative = phase_handoff.handoff_path(str(handoff["handoff_id"]))

    if accepted:
        with pytest.raises(phase_handoff.PhaseHandoffError,
                           match="source-to-export lineage"):
            phase_handoff.load_phase_handoff(root, relative)
        assert phase_handoff.load_phase_handoff(
            root, relative, allowed_task_id="T-001") == handoff
    else:
        with pytest.raises(phase_handoff.PhaseHandoffError,
                           match="source-to-export lineage"):
            phase_handoff.load_phase_handoff(
                root, relative, allowed_task_id="T-001")


def _build_complete_handoff(root: Path, plan: dict) -> tuple[dict, str]:
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    source_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    receipt = phase_handoff.create_progress_receipt(
        producer="engine:taskplane.phase-pickup/v1", sequence=1,
        phase="build", obligation_id="AC1", task_id="T-001",
        status="green", predecessor_receipt_fingerprint=None,
        checkpoint_receipt_digest="d" * 64,
        integration_receipt_fingerprint="e" * 64)
    carried = {
        key: copy.deepcopy(plan[key]) for key in (
            "repository", "requirement", "design", "plan", "obligations",
            "tasks", "contracts", "acceptance", "selected_artifacts",
            "authority_receipts", "exclusions")
    }
    handoff = phase_handoff.create_phase_handoff(
        **carried, source={"commit": source_commit, "tree": source_tree},
        producer={"phase": "build", "outcome": "done"},
        successor={"phase": "terminal", "mode": "terminal-evidence"},
        progress={"completed": ["AC1"], "remaining": []},
        progress_receipts=[receipt],
        lineage={
            "predecessor_handoff_fingerprint": plan["fingerprint"],
            "predecessor_receipt_head": receipt["fingerprint"],
        })
    return handoff, source_commit


def _publish_completion(root: Path, handoff: dict) -> None:
    phase_handoff.publish_phase_handoff(root, handoff)
    subprocess.run(["git", "add", "-f", "exports/pickup"],
                   cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "publish Build completion"],
                   cwd=root, check=True)


def test_build_complete_handoff_retains_truthful_ancestor_authority(
        tmp_path) -> None:
    root, plan = _published_checkout(tmp_path, "build")
    _commit_change(root, "taskplane/phase_handoff.py")

    completed, source_commit = _build_complete_handoff(root, plan)
    _publish_completion(root, completed)

    assert all(receipt["source_commit"] != source_commit
               for receipt in completed["authority_receipts"])
    for receipt in completed["authority_receipts"]:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor",
             receipt["source_commit"], source_commit],
            cwd=root, check=True)
    assert phase_handoff.validate_repository_manifest(root, completed) == completed


def test_later_export_reuses_tracked_digest_verified_artifacts(tmp_path) -> None:
    root, plan = _published_checkout(tmp_path, "build")
    _commit_change(root, "taskplane/phase_handoff.py")
    completed, source_commit = _build_complete_handoff(root, plan)
    _publish_completion(root, completed)

    changed = set(subprocess.check_output(
        ["git", "diff", "--name-only", source_commit, "HEAD"],
        cwd=root, text=True).splitlines())
    assert not changed.intersection(
        reference["destination"]
        for reference in completed["selected_artifacts"])
    assert phase_handoff.validate_repository_manifest(root, completed) == completed
