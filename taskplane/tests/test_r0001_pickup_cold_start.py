from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import pickup


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _git(checkout: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=checkout, text=True, encoding="utf-8"
    ).strip()


def _commit(checkout: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "commit", "-qm", message], cwd=checkout, check=True
    )
    return _git(checkout, "rev-parse", "HEAD")


def _fresh_pickup_checkout(tmp_path: Path) -> tuple[Path, str, str]:
    source = tmp_path / "source"
    proof = source / "tests" / "test_proof.py"
    proof.parent.mkdir(parents=True)
    proof.write_text("def test_proof():\n    assert True\n", encoding="utf-8")
    (source / ".gitignore").write_text(
        ".taskplane/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "cold-start@example.test"],
        cwd=source, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Cold Start Test"],
        cwd=source, check=True,
    )
    source_sha = _commit(source, "source")
    design = {
        "schema": "taskplane.pickup-design/v1",
        "source_sha": source_sha,
        "element": {
            "id": "cold-start",
            "scope": ["tests/test_proof.py"],
            "acceptance": [{
                "id": "AC2",
                "proof": {
                    "path": "tests/test_proof.py",
                    "argv": [
                        "python3", "-m", "pytest", "-q",
                        "-p", "no:cacheprovider", "tests/test_proof.py",
                    ],
                },
            }],
        },
    }
    design_fingerprint = hashlib.sha256(_canonical(design)).hexdigest()
    key_id = "1" * 64
    authority = {
        "schema": "taskplane.approved-pickup-contract/v1",
        "design": design,
        "approval": {
            "schema": "taskplane.pickup-design-approval/v1",
            "actor": "human:cold-start-operator",
            "design_fingerprint": design_fingerprint,
            "key_id": key_id,
            "signature": "2" * 64,
        },
        "engine_receipt": {
            "schema": "taskplane.pickup-engine-receipt/v1",
            "producer": "taskplane.design-approval-engine/v1",
            "source_sha": source_sha,
            "design_fingerprint": design_fingerprint,
            "key_id": key_id,
            "signature": "3" * 64,
        },
    }
    authority_rel = "design/shelf.json"
    authority_path = source / authority_rel
    authority_path.parent.mkdir()
    authority_path.write_text(
        json.dumps(authority, indent=2) + "\n", encoding="utf-8"
    )
    candidate_sha = _commit(source, "approved authority")

    checkout = tmp_path / "fresh-checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(source), str(checkout)],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", candidate_sha],
        cwd=checkout, check=True,
    )
    return checkout, authority_rel, source_sha


def test_fresh_checkout_same_sha_reaches_first_executing_checkpoint_under_120_seconds(
        tmp_path: Path) -> None:
    checkout, authority_rel, source_sha = _fresh_pickup_checkout(tmp_path)
    candidate_sha = _git(checkout, "rev-parse", "HEAD")
    private_home = tmp_path / "empty-taskplane-home"

    result = pickup.measure_first_checkpoint(
        str(checkout), authority_rel, expected_sha=candidate_sha,
        taskplane_home=str(private_home), trust_source=source_sha,
    )

    receipt = result["cold_start_receipt"]
    assert receipt["status"] == "passing"
    assert 0.0 <= receipt["first_checkpoint_seconds"] < 120.0
    assert result["trace"][-2:] == [
        "pickup.build_c.assigned", "pickup.checkpoint.started",
    ]


def test_cold_start_receipt_proves_empty_home_and_exact_sha(
        tmp_path: Path) -> None:
    checkout, authority_rel, source_sha = _fresh_pickup_checkout(tmp_path)
    candidate_sha = _git(checkout, "rev-parse", "HEAD")
    private_home = tmp_path / "empty-taskplane-home"

    result = pickup.measure_first_checkpoint(
        str(checkout), authority_rel, expected_sha=candidate_sha,
        taskplane_home=str(private_home), trust_source=source_sha,
    )
    receipt = pickup.require_r0013_cold_start(
        result["cold_start_receipt"], expected_sha=candidate_sha
    )

    assert receipt["schema"] == "taskplane.pickup-cold-start/v1"
    assert receipt["expected_sha"] == candidate_sha
    assert receipt["checkout_sha"] == candidate_sha
    assert receipt["taskplane_home_empty"] is True
    assert not private_home.exists()


def test_r0013_resume_refuses_without_passing_cold_start_receipt() -> None:
    with pytest.raises(pickup.PickupRefusal, match="cold-start"):
        pickup.require_r0013_cold_start(None, expected_sha="a" * 40)

    failing = {
        "schema": "taskplane.pickup-cold-start/v1",
        "producer": "taskplane.pickup/v1",
        "expected_sha": "a" * 40,
        "checkout_sha": "a" * 40,
        "taskplane_home_empty": True,
        "first_checkpoint_seconds": 120.0,
        "limit_seconds": 120.0,
        "status": "failed",
    }
    failing["receipt_digest"] = hashlib.sha256(_canonical(failing)).hexdigest()

    with pytest.raises(pickup.PickupRefusal, match="cold-start"):
        pickup.require_r0013_cold_start(failing, expected_sha="a" * 40)
