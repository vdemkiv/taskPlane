from __future__ import annotations

from copy import deepcopy

import pytest

from taskplane.delivery_ports import FakeClock, RecordedPlatformCiQuery, content_fingerprint
from taskplane.release_evidence import (
    COMPATIBILITY_PREVIOUS_VERSION,
    CURRENT_VERSION,
    ReleaseEvidenceError,
    classify_schema_changes,
    compatibility_cell,
    create_feature_green,
    create_mixed_version_matrix_receipt,
    create_release_green,
    cutover_capabilities_required,
    grants_release_authority,
    legacy_evidence_authority,
    load_compatibility_policy,
    validate_feature_green,
    validate_mixed_version_matrix_receipt,
    validate_release_green,
)


SHA = "a" * 40


def _observations(policy):
    return [
        {
            **{key: row[key] for key in ("plugin", "host", "feature", "release")},
            "source_sha": SHA,
            "observed": True,
        }
        for row in policy["matrix"]
    ]


def _matrix(policy=None):
    policy = policy or load_compatibility_policy()
    return create_mixed_version_matrix_receipt(
        source_sha=SHA,
        observations=_observations(policy),
        policy=policy,
    )


def _platform_response():
    return {
        "schema": "taskplane.platform-ci-proof/v1",
        "provider": "github",
        "repository_id": "openai/taskplane",
        "protected_default_branch": "main",
        "pushed_sha": SHA,
        "workflow_run_id": "run-1",
        "check_run_ids": ["check-1"],
        "required_check_names": ["full / linux"],
        "conclusions": {"full / linux": "success"},
        "queried_at": 100.0,
        "fresh_until": 200.0,
        "platform_response_digest": "f" * 64,
    }


def _release(matrix_fingerprint):
    return create_release_green(
        source_sha=SHA,
        version=CURRENT_VERSION,
        wiring_closure_fingerprint="1" * 64,
        feature_receipt_digests=["2" * 64],
        full_matrix_receipts=["3" * 64],
        package_manifest_receipts=["4" * 64, "5" * 64],
        compatibility_policy_fingerprint="6" * 64,
        schema_bundle_fingerprint="7" * 64,
        compatibility_diff_receipt="8" * 64,
        mixed_version_matrix_receipt=matrix_fingerprint,
        live_host_canary_receipt="b" * 64,
        recorded_event_replay_receipt="c" * 64,
        host_action_capability_refusal_receipt="d" * 64,
        task_dispatch_capability_default_deny_receipt="e" * 64,
        reviewed_prompt_injection_reference_digest="f" * 64,
        repository_id="openai/taskplane",
        protected_default_branch="main",
        workflow_run_id="run-1",
        check_run_ids=["check-1"],
        required_check_names=["full / linux"],
        outside_model_human_recheck={
            "actor": "human:release-owner",
            "channel": "outside-model",
            "action": "release-candidate",
            "source_sha": SHA,
            "confirmed": True,
            "cryptographic_authenticity_claimed": False,
        },
        platform_ci_query=RecordedPlatformCiQuery([_platform_response()]),
        clock=FakeClock(110.0),
    )


def test_emit_before_require_capability_handshake():
    policy = load_compatibility_policy()
    matrix = _matrix(policy)

    assert cutover_capabilities_required("emit", policy=policy) is False
    assert cutover_capabilities_required("observe", policy=policy) is False
    with pytest.raises(ReleaseEvidenceError, match="complete mixed-version matrix"):
        cutover_capabilities_required("require", policy=policy)
    assert cutover_capabilities_required(
        "require", matrix_receipt=matrix, policy=policy
    ) is True


def test_mixed_plugin_host_n_n_minus_1_matrix():
    policy = load_compatibility_policy()
    matrix = _matrix(policy)

    assert validate_mixed_version_matrix_receipt(matrix, policy=policy) == matrix
    assert {(row["plugin"], row["host"]) for row in matrix["cells"]} == {
        (CURRENT_VERSION, CURRENT_VERSION),
        (CURRENT_VERSION, COMPATIBILITY_PREVIOUS_VERSION),
        (COMPATIBILITY_PREVIOUS_VERSION, CURRENT_VERSION),
        (COMPATIBILITY_PREVIOUS_VERSION, COMPATIBILITY_PREVIOUS_VERSION),
    }
    assert compatibility_cell(
        CURRENT_VERSION, COMPATIBILITY_PREVIOUS_VERSION, policy=policy)[
        "release"
    ] == "refuse-missing-N-capability"
    assert matrix["cryptographic_authenticity_claimed"] is False


def test_unknown_fields_require_new_schema_version():
    receipt = create_feature_green(
        source_sha=SHA,
        design_fingerprint="1" * 64,
        task_id="t05",
        declared_selectors=["test_a2.py::test_green"],
        focused_receipt_digests=["2" * 64],
    )
    unknown = deepcopy(receipt)
    unknown["release_authority"] = True
    projection = {key: value for key, value in unknown.items() if key != "fingerprint"}
    unknown["fingerprint"] = content_fingerprint(projection)

    with pytest.raises(ReleaseEvidenceError, match="fields are not closed"):
        validate_feature_green(unknown)


def test_schema_compatibility_diff_classifies_every_change():
    previous = {"type": "object", "properties": {"old": {"type": "string"}}}
    current = {
        "type": "object",
        "properties": {"new": {"type": "number"}, "old": {"type": "integer"}},
    }
    receipt = classify_schema_changes(previous, current)

    assert receipt["classified_change_count"] == len(receipt["changes"]) == 2
    assert receipt["changes"] == [
        {"path": "/properties/new", "classification": "added"},
        {"path": "/properties/old/type", "classification": "changed"},
    ]
    assert receipt["cryptographic_authenticity_claimed"] is False


def test_legacy_feature_receipt_never_grants_release():
    legacy = legacy_evidence_authority("v2.17.20-focused-checkpoint")

    assert legacy["release_green"] is False
    assert "history" in legacy["authority"]
    assert grants_release_authority(legacy) is False


def test_release_green_requires_compatibility_matrix_receipt():
    matrix = _matrix()
    release = _release(matrix["fingerprint"])

    assert release["mixed_version_matrix_receipt"] == matrix["fingerprint"]
    severed = deepcopy(release)
    severed.pop("mixed_version_matrix_receipt")
    with pytest.raises(ReleaseEvidenceError, match="fields are not closed"):
        validate_release_green(severed, now=110.0)
