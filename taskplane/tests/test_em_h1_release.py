from __future__ import annotations

import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane.delivery_ports import (
    FakeClock,
    RecordedPlatformCiQuery,
    content_fingerprint,
)
from taskplane.release_evidence import CURRENT_VERSION, create_release_green


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
NOW = 110.0
RUN_ID = "4242"
TAG_OBJECT_SHA = "d" * 40
SIGNER_KEY_FINGERPRINT = "0123456789ABCDEF0123456789ABCDEF01234567"


def _openpgp_signature(fingerprint: str) -> str:
    fingerprint_bytes = bytes.fromhex(fingerprint)
    issuer_fingerprint = (
        bytes((len(fingerprint_bytes) + 2, 33, 4)) + fingerprint_bytes
    )
    body = (
        b"\x04\x00\x01\x08"
        + len(issuer_fingerprint).to_bytes(2, "big")
        + issuer_fingerprint
        + b"\x00\x00\x00\x00"
    )
    packet = bytes((0xC2, len(body))) + body
    encoded = base64.b64encode(packet).decode("ascii")
    return (
        "-----BEGIN PGP SIGNATURE-----\n\n"
        f"{encoded}\n"
        "-----END PGP SIGNATURE-----"
    )


def _packager():
    path = ROOT / "scripts" / "package_openai.py"
    spec = importlib.util.spec_from_file_location("_em_package_openai", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy() -> dict:
    policy = json.loads(
        (ROOT / "design" / "compatibility.json").read_text(encoding="utf-8")
    )
    policy["release_authority"]["publication_decision"][
        "allowed_signer_key_fingerprints"
    ] = [SIGNER_KEY_FINGERPRINT]
    return policy


def _seal(value: dict) -> dict:
    result = deepcopy(value)
    result.pop("fingerprint", None)
    result["fingerprint"] = content_fingerprint(result)
    return result


def _compatibility_receipt(policy: dict, *, source_sha: str = SHA) -> dict:
    producer = policy["release_observation_producer"]
    receipt = {
        "schema": "taskplane.release-compatibility-matrix/v2",
        "source_sha": source_sha,
        "compatibility_policy_fingerprint": content_fingerprint(policy),
        "producer": producer["entrypoint"],
        "cells": [
            {
                "plugin": row["plugin"],
                "host": row["host"],
                "candidate_sha": source_sha,
                "test_name": "openai-package-archive-roundtrip",
                "test_outcome": "passed",
                "artifact_sha256": str(index + 1) * 64,
                "host_validator_sha256": str(index + 5) * 64,
                "check_identity": (
                    f'{producer["check_identity_prefix"]}'
                    f'{row["plugin"]}-on-{row["host"]}'
                ),
                "platform": producer["platform"],
            }
            for index, row in enumerate(policy["release_matrix"])
        ],
        "status": "release-compatible",
        "cryptographic_authenticity_claimed": False,
    }
    return _seal(receipt)


def _ci_snapshot(policy: dict) -> dict:
    authority = policy["release_authority"]
    return {
        "schema": "taskplane.github-release-ci-snapshot/v1",
        "repository": authority["repository"],
        "protected_ref": authority["protected_ref"],
        "source_sha": SHA,
        "workflow": {
            "id": RUN_ID,
            "name": authority["workflow"]["name"],
            "path": authority["workflow"]["path"],
            "event": authority["workflow"]["event"],
            "head_branch": "main",
            "conclusion": "success",
        },
        "checks": [
            {
                "id": str(1000 + index),
                "name": name,
                "conclusion": "success",
                "app": "github-actions",
                "details_url": (
                    f"https://github.com/vdemkiv/taskPlane/actions/runs/"
                    f"{RUN_ID}/job/{2000 + index}"
                ),
            }
            for index, name in enumerate(authority["required_checks"])
        ],
    }


def _platform_response(policy: dict) -> dict:
    authority = policy["release_authority"]
    snapshot = _ci_snapshot(policy)
    return {
        "schema": "taskplane.platform-ci-proof/v1",
        "provider": "github",
        "repository_id": authority["repository"],
        "protected_default_branch": "main",
        "pushed_sha": SHA,
        "workflow_run_id": RUN_ID,
        "check_run_ids": [row["id"] for row in snapshot["checks"]],
        "required_check_names": list(authority["required_checks"]),
        "conclusions": {
            name: "success" for name in authority["required_checks"]
        },
        "queried_at": 100.0,
        "fresh_until": 200.0,
        "platform_response_digest": content_fingerprint(snapshot),
    }


def _human(action: str) -> dict:
    return {
        "actor": "human:release-owner",
        "channel": "outside-model",
        "action": action,
        "source_sha": SHA,
        "confirmed": True,
        "cryptographic_authenticity_claimed": False,
    }


def _release(policy: dict, compatibility: dict) -> dict:
    response = _platform_response(policy)
    return create_release_green(
        source_sha=SHA,
        version=CURRENT_VERSION,
        wiring_closure_fingerprint="1" * 64,
        feature_receipt_digests=["2" * 64],
        full_matrix_receipts=["3" * 64],
        package_manifest_receipts=["4" * 64, "5" * 64],
        compatibility_policy_fingerprint=content_fingerprint(policy),
        schema_bundle_fingerprint="7" * 64,
        compatibility_diff_receipt="8" * 64,
        mixed_version_matrix_receipt=compatibility["fingerprint"],
        live_host_canary_receipt="b" * 64,
        recorded_event_replay_receipt="c" * 64,
        host_action_capability_refusal_receipt="d" * 64,
        task_dispatch_capability_default_deny_receipt="e" * 64,
        reviewed_prompt_injection_reference_digest="f" * 64,
        repository_id=policy["release_authority"]["repository"],
        protected_default_branch="main",
        workflow_run_id=RUN_ID,
        check_run_ids=response["check_run_ids"],
        required_check_names=policy["release_authority"]["required_checks"],
        outside_model_human_recheck=_human("release-candidate"),
        platform_ci_query=RecordedPlatformCiQuery([response]),
        clock=FakeClock(NOW),
    )


class FakeGitHubApi:
    def __init__(self, policy: dict, release: dict):
        authority = policy["release_authority"]
        repository = authority["repository"]
        tag_name = authority["publication_decision"]["tag_prefix"] + SHA
        self.calls: list[str] = []
        self.responses = {
            f"/repos/{repository}/git/ref/heads/main": {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": SHA},
            },
            f"/repos/{repository}/actions/runs/{RUN_ID}": {
                "id": int(RUN_ID),
                "name": authority["workflow"]["name"],
                "path": authority["workflow"]["path"],
                "event": authority["workflow"]["event"],
                "head_branch": "main",
                "head_sha": SHA,
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": repository},
                "head_repository": {"full_name": repository},
            },
            f"/repos/{repository}/commits/{SHA}/check-runs": {
                "check_runs": [
                    {
                        "id": int(row["id"]),
                        "name": row["name"],
                        "head_sha": SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "app": {"slug": "github-actions"},
                        "details_url": row["details_url"],
                    }
                    for row in _ci_snapshot(policy)["checks"]
                ]
            },
            f"/repos/{repository}/git/ref/tags/{tag_name}": {
                "ref": "refs/tags/" + tag_name,
                "object": {"type": "tag", "sha": TAG_OBJECT_SHA},
            },
            f"/repos/{repository}/git/tags/{TAG_OBJECT_SHA}": {
                "tag": tag_name,
                "message": (
                    "taskplane.openai-publication-approval/v1\n"
                    "decision=approve\n"
                    f"repository={repository}\n"
                    f"source_sha={SHA}\n"
                    f"release_green_fingerprint={release['fingerprint']}"
                ),
                "tagger": {
                    "name": "Volodymyr Demkiv",
                    "email": "vdemkiv@gmail.com",
                },
                "object": {"type": "commit", "sha": SHA},
                "verification": {
                    "verified": True,
                    "reason": "valid",
                    "signature": _openpgp_signature(SIGNER_KEY_FINGERPRINT),
                    "payload": (
                        f"object {SHA}\n"
                        "type commit\n"
                        f"tag {tag_name}\n"
                        "tagger Volodymyr Demkiv <vdemkiv@gmail.com> "
                        "1787947200 -0400\n\n"
                        "taskplane.openai-publication-approval/v1\n"
                        "decision=approve\n"
                        f"repository={repository}\n"
                        f"source_sha={SHA}\n"
                        f"release_green_fingerprint={release['fingerprint']}"
                    ),
                },
            },
        }

    def get(self, path: str):
        self.calls.append(path)
        return deepcopy(self.responses[path])


def test_h19_packaging_requires_release_authority(monkeypatch):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    github = FakeGitHubApi(policy, release)
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="release-green"):
        packager.validate_release_package_authority(
            release_green=None,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )

    checked = packager.validate_release_package_authority(
        release_green=release,
        expected_source_sha=SHA,
        now=NOW,
        policy=policy,
        github_api=github,
    )
    assert checked["source_sha"] == SHA
    assert any("/actions/runs/" in path for path in github.calls)
    assert any("/check-runs" in path for path in github.calls)
    assert any("/git/tags/" in path for path in github.calls)


def test_h19_freshly_resealed_semantic_forgery_cannot_reuse_human_decision(
    monkeypatch,
):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    github = FakeGitHubApi(policy, release)
    forged = deepcopy(release)
    forged["feature_receipt_digests"] = ["9" * 64]
    forged = _seal(forged)
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="signed publication decision"):
        packager.validate_release_package_authority(
            release_green=forged,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )


def test_h19_verified_tag_from_non_allowlisted_signer_is_refused(monkeypatch):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    github = FakeGitHubApi(policy, release)
    repository = policy["release_authority"]["repository"]
    tag_path = f"/repos/{repository}/git/tags/{TAG_OBJECT_SHA}"
    github.responses[tag_path]["verification"]["signature"] = _openpgp_signature(
        "89ABCDEF0123456789ABCDEF0123456789ABCDEF"
    )
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="signing key fingerprint"):
        packager.validate_release_package_authority(
            release_green=release,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )


def test_h19_stored_policy_has_no_implicit_signer_authority():
    packager = _packager()
    stored_policy = json.loads(
        (ROOT / "design" / "compatibility.json").read_text(encoding="utf-8")
    )

    with pytest.raises(packager.PackageError, match="separately authorized release"):
        packager._release_authority(stored_policy)


def test_h19_required_check_url_from_fork_repository_is_refused(monkeypatch):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    github = FakeGitHubApi(policy, release)
    checks_path = (
        f"/repos/{policy['release_authority']['repository']}/commits/{SHA}/check-runs"
    )
    github.responses[checks_path]["check_runs"][0]["details_url"] = (
        f"https://github.com/attacker/taskPlane/actions/runs/{RUN_ID}/job/2000"
    )
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="required check"):
        packager.validate_release_package_authority(
            release_green=release,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )


def test_h19_resealed_ci_proof_cannot_change_repository_or_check_identity(
    monkeypatch,
):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    forged = deepcopy(release)
    proof = forged["platform_ci_proof"]
    proof["repository_id"] = "attacker/fork"
    forged["platform_ci_proof"] = _seal(proof)
    forged["pushed_sha_proof"] = forged["platform_ci_proof"]["fingerprint"]
    forged = _seal(forged)
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="repository"):
        packager.validate_release_package_authority(
            release_green=forged,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=FakeGitHubApi(policy, release),
        )


def test_h22_compatibility_matrix_includes_last_released_generation():
    policy = _policy()

    assert policy["window"]["current"] == "2.18.0"
    assert policy["window"]["last_released"] == "2.17.20"
    assert policy["window"]["candidate_previous"] == "2.17.26"
    assert {(row["plugin"], row["host"]) for row in policy["release_matrix"]} == {
        ("2.18.0", "2.18.0"),
        ("2.18.0", "2.17.20"),
        ("2.17.20", "2.18.0"),
        ("2.17.20", "2.17.20"),
    }
    producer = policy["release_observation_producer"]
    assert producer["last_released_tag"] == "v2.17.20"
    assert producer["last_released_commit"] == (
        "4a0378e7f080136d27f01d4ab7ecdf9bac8a1ad6"
    )
    assert producer["entrypoint"].startswith("scripts/package_openai.py ")


def test_h19_dirty_checkout_cannot_claim_exact_sha_release_authority(monkeypatch):
    packager = _packager()
    policy = _policy()
    compatibility = _compatibility_receipt(policy)
    release = _release(policy, compatibility)
    monkeypatch.setattr(packager, "git_is_clean", lambda: False)
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: deepcopy(compatibility),
    )

    with pytest.raises(packager.PackageError, match="clean exact source"):
        packager.validate_release_package_authority(
            release_green=release,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=FakeGitHubApi(policy, release),
        )


def test_h19_compatibility_receipt_producer_refuses_dirty_checkout(monkeypatch):
    packager = _packager()
    monkeypatch.setattr(packager, "git_is_clean", lambda: False)

    with pytest.raises(packager.PackageError, match="clean exact source"):
        packager.produce_release_compatibility_receipt(
            expected_source_sha=SHA, policy=_policy()
        )


def test_h22_package_workflow_produces_executable_cell_evidence():
    packager = _packager()
    policy = _policy()

    receipt = packager.produce_release_compatibility_receipt(
        expected_source_sha=packager.git_head(), policy=policy
    )

    assert receipt["status"] == "release-compatible"
    assert receipt["producer"] == policy["release_observation_producer"][
        "entrypoint"
    ]
    assert len(receipt["cells"]) == 4
    for cell in receipt["cells"]:
        assert set(cell) == {
            "plugin", "host", "candidate_sha", "test_name", "test_outcome",
            "artifact_sha256", "host_validator_sha256", "check_identity",
            "platform",
        }
        assert cell["candidate_sha"] == packager.git_head()
        assert cell["test_name"] == "openai-package-archive-roundtrip"
        assert cell["test_outcome"] == "passed"
        assert len(cell["artifact_sha256"]) == 64
        assert len(cell["host_validator_sha256"]) == 64
        assert cell["check_identity"].startswith("package-openai/release-matrix/")
        assert cell["platform"] == "openai-marketplace-zip"
        assert "observed" not in cell


def test_h22_package_cli_executes_the_production_observation_path(tmp_path):
    receipt_path = Path("/tmp") / f"taskplane-{tmp_path.name}-compatibility.json"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "package_openai.py"),
                "--write-compatibility-receipt",
                str(receipt_path),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["schema"] == "taskplane.release-compatibility-matrix/v2"
        assert receipt["status"] == "release-compatible"
    finally:
        receipt_path.unlink(missing_ok=True)


def test_h26_release_gate_refuses_without_N_minus_1_evidence(monkeypatch):
    packager = _packager()
    policy = _policy()
    claimed = _compatibility_receipt(policy)
    release = _release(policy, claimed)
    github = FakeGitHubApi(policy, release)
    current = policy["window"]["current"]
    no_last_released = deepcopy(claimed)
    no_last_released["cells"] = [
        cell for cell in no_last_released["cells"]
        if cell["plugin"] == current and cell["host"] == current
    ]
    no_last_released = _seal(no_last_released)
    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt",
        lambda **_kwargs: no_last_released,
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)

    with pytest.raises(packager.PackageError, match="last released generation"):
        packager.validate_release_package_authority(
            release_green=release,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )


def test_h26_observed_true_json_without_real_execution_has_no_authority(
    monkeypatch,
):
    packager = _packager()
    policy = _policy()
    claimed = _compatibility_receipt(policy)
    release = _release(policy, claimed)
    github = FakeGitHubApi(policy, release)

    def no_execution(**_kwargs):
        raise packager.PackageError(
            "release compatibility has no production-produced observations"
        )

    monkeypatch.setattr(
        packager, "produce_release_compatibility_receipt", no_execution
    )
    monkeypatch.setattr(packager, "git_is_clean", lambda: True)
    forged_old_cells = deepcopy(claimed)
    for cell in forged_old_cells["cells"]:
        cell.clear()
        cell.update({
            "plugin": "2.18.0", "host": "2.18.0",
            "source_sha": SHA, "observed": True,
        })
    forged_old_cells = _seal(forged_old_cells)
    assert forged_old_cells["fingerprint"]

    with pytest.raises(packager.PackageError, match="production-produced"):
        packager.validate_release_package_authority(
            release_green=release,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
            github_api=github,
        )
