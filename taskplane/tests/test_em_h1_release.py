from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from taskplane.delivery_ports import (
    FakeClock,
    RecordedPlatformCiQuery,
    content_fingerprint,
)
from taskplane.release_evidence import (
    CURRENT_VERSION,
    authorize_irreversible_action,
    create_release_green,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
NOW = 110.0


def _packager():
    path = ROOT / "scripts" / "package_openai.py"
    spec = importlib.util.spec_from_file_location("_em_package_openai", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _platform_response():
    return {
        "schema": "taskplane.platform-ci-proof/v1",
        "provider": "github",
        "repository_id": "openai/taskplane",
        "protected_default_branch": "main",
        "pushed_sha": SHA,
        "workflow_run_id": "run-h1-d",
        "check_run_ids": ["release-linux"],
        "required_check_names": ["release / linux"],
        "conclusions": {"release / linux": "success"},
        "queried_at": 100.0,
        "fresh_until": 200.0,
        "platform_response_digest": "f" * 64,
    }


def _human(action: str):
    return {
        "actor": "human:release-owner",
        "channel": "outside-model",
        "action": action,
        "source_sha": SHA,
        "confirmed": True,
        "cryptographic_authenticity_claimed": False,
    }


def _compatibility_receipt(policy: dict, *, omit_last_released: bool = False):
    rows = policy["release_matrix"]
    if omit_last_released:
        rows = policy["matrix"]
    receipt = {
        "schema": "taskplane.release-compatibility-matrix/v1",
        "source_sha": SHA,
        "compatibility_policy_fingerprint": content_fingerprint(policy),
        "cells": [
            {
                "plugin": row["plugin"],
                "host": row["host"],
                "source_sha": SHA,
                "observed": True,
            }
            for row in rows
        ],
        "status": "release-compatible",
        "cryptographic_authenticity_claimed": False,
    }
    receipt["fingerprint"] = content_fingerprint(receipt)
    return receipt


def _authority(policy: dict, *, omit_last_released: bool = False):
    compatibility = _compatibility_receipt(
        policy, omit_last_released=omit_last_released
    )
    query = RecordedPlatformCiQuery([_platform_response()])
    release = create_release_green(
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
        repository_id="openai/taskplane",
        protected_default_branch="main",
        workflow_run_id="run-h1-d",
        check_run_ids=["release-linux"],
        required_check_names=["release / linux"],
        outside_model_human_recheck=_human("release-candidate"),
        platform_ci_query=query,
        clock=FakeClock(NOW),
    )
    authorization = authorize_irreversible_action(
        release,
        action="publication",
        platform_ci_query=RecordedPlatformCiQuery([_platform_response()]),
        outside_model_human_recheck=_human("publication"),
        clock=FakeClock(NOW),
    )
    return release, authorization, compatibility


def test_h19_packaging_requires_release_authority(monkeypatch):
    packager = _packager()
    policy = json.loads((ROOT / "design" / "compatibility.json").read_text())
    release, authorization, compatibility = _authority(policy)

    monkeypatch.setattr(sys, "argv", ["package_openai.py"])
    with pytest.raises(SystemExit) as missing_cli_authority:
        packager.main()
    assert missing_cli_authority.value.code == 2

    with pytest.raises(packager.PackageError, match="release-green"):
        packager.validate_release_package_authority(
            release_green=None,
            publication_authorization=authorization,
            compatibility_receipt=compatibility,
            expected_source_sha=SHA,
            now=NOW,
        )

    checked = packager.validate_release_package_authority(
        release_green=release,
        publication_authorization=authorization,
        compatibility_receipt=compatibility,
        expected_source_sha=SHA,
        now=NOW,
        policy=policy,
    )
    assert checked["source_sha"] == SHA

    forged = deepcopy(authorization)
    forged["source_sha"] = "b" * 40
    with pytest.raises(packager.PackageError, match="authorization"):
        packager.validate_release_package_authority(
            release_green=release,
            publication_authorization=forged,
            compatibility_receipt=compatibility,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
        )


def test_h22_compatibility_matrix_includes_last_released_generation():
    policy = json.loads((ROOT / "design" / "compatibility.json").read_text())

    assert policy["window"]["current"] == "2.17.25"
    assert policy["window"]["last_released"] == "2.17.20"
    assert policy["window"]["candidate_previous"] == "2.17.24"
    assert {(row["plugin"], row["host"]) for row in policy["release_matrix"]} == {
        ("2.17.25", "2.17.25"),
        ("2.17.25", "2.17.20"),
        ("2.17.20", "2.17.25"),
        ("2.17.20", "2.17.20"),
    }


def test_h26_release_gate_refuses_without_N_minus_1_evidence():
    packager = _packager()
    policy = json.loads((ROOT / "design" / "compatibility.json").read_text())
    release, authorization, candidate_only = _authority(
        policy, omit_last_released=True
    )

    with pytest.raises(packager.PackageError, match="last released generation"):
        packager.validate_release_package_authority(
            release_green=release,
            publication_authorization=authorization,
            compatibility_receipt=candidate_only,
            expected_source_sha=SHA,
            now=NOW,
            policy=policy,
        )
