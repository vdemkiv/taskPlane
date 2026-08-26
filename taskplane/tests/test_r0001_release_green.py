from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from taskplane.delivery_ports import FakeClock, RecordedPlatformCiQuery
from taskplane.delivery_ports import (
    EVIDENCE_FAULT_SEAMS,
    EnumeratingFaultInjector,
    InjectedFault,
    SandboxEvidenceStore,
)
from taskplane.release_evidence import (
    RELEASE_REQUIRED_PROOFS,
    ReleaseEvidenceError,
    authorize_irreversible_action,
    create_feature_green,
    create_release_green,
    create_release_override,
    grants_release_authority,
    publish,
    reconcile,
    validate_release_green,
    validate_release_override,
)


SHA = "a" * 40
DIGEST = "d" * 64
REPOSITORY = "openai/taskplane"
BRANCH = "main"
WORKFLOW = "run-21721"
CHECK_IDS = ("check-linux", "check-macos")
CHECK_NAMES = ("full / linux", "full / macos")


def _platform_response(**overrides):
    response = {
        "schema": "taskplane.platform-ci-proof/v1",
        "provider": "github",
        "repository_id": REPOSITORY,
        "protected_default_branch": BRANCH,
        "pushed_sha": SHA,
        "workflow_run_id": WORKFLOW,
        "check_run_ids": list(CHECK_IDS),
        "required_check_names": list(CHECK_NAMES),
        "conclusions": {name: "success" for name in CHECK_NAMES},
        "queried_at": 100.0,
        "fresh_until": 200.0,
        "platform_response_digest": "e" * 64,
    }
    response.update(overrides)
    return response


def _release_inputs():
    return {
        "source_sha": SHA,
        "version": "2.17.21",
        "wiring_closure_fingerprint": "1" * 64,
        "feature_receipt_digests": ["2" * 64],
        "full_matrix_receipts": ["3" * 64],
        "package_manifest_receipts": ["4" * 64, "5" * 64],
        "compatibility_policy_fingerprint": "6" * 64,
        "schema_bundle_fingerprint": "7" * 64,
        "compatibility_diff_receipt": "8" * 64,
        "mixed_version_matrix_receipt": "9" * 64,
        "live_host_canary_receipt": "b" * 64,
        "recorded_event_replay_receipt": "c" * 64,
        "host_action_capability_refusal_receipt": "d" * 64,
        "task_dispatch_capability_default_deny_receipt": "e" * 64,
        "reviewed_prompt_injection_reference_digest": "f" * 64,
        "repository_id": REPOSITORY,
        "protected_default_branch": BRANCH,
        "workflow_run_id": WORKFLOW,
        "check_run_ids": CHECK_IDS,
        "required_check_names": CHECK_NAMES,
        "outside_model_human_recheck": {
            "actor": "human:release-owner",
            "channel": "outside-model",
            "action": "release-candidate",
            "source_sha": SHA,
            "confirmed": True,
            "cryptographic_authenticity_claimed": False,
        },
    }


def _release(query=None, **overrides):
    values = _release_inputs()
    values.update(overrides)
    return create_release_green(
        **values,
        platform_ci_query=query or RecordedPlatformCiQuery([_platform_response()]),
        clock=FakeClock(wall_time=110.0),
    )


def _human(action):
    return {
        "actor": "human:release-owner",
        "channel": "outside-model",
        "action": action,
        "source_sha": SHA,
        "confirmed": True,
        "cryptographic_authenticity_claimed": False,
    }


def _override(**overrides):
    values = {
        "source_sha": SHA,
        "skipped_proofs": RELEASE_REQUIRED_PROOFS,
        "human_authority_receipt": _human("release-override"),
        "reason": "human accepted an incomplete historical release",
        "recorded_at": 110.0,
    }
    values.update(overrides)
    return create_release_override(**values)


def test_feature_green_cannot_authorize_release():
    receipt = create_feature_green(
        source_sha=SHA,
        design_fingerprint=DIGEST,
        task_id="t05-a2-shared-adapter-integration",
        declared_selectors=["tests/test_a2.py::test_feature"],
        focused_receipt_digests=["1" * 64],
    )

    assert receipt["status"] == "feature-green"
    assert receipt["cryptographic_authenticity_claimed"] is False
    assert grants_release_authority(receipt) is False
    with pytest.raises(ReleaseEvidenceError, match="release-green"):
        authorize_irreversible_action(
            receipt,
            action="tag",
            platform_ci_query=RecordedPlatformCiQuery([_platform_response()]),
            outside_model_human_recheck={"confirmed": True},
            clock=FakeClock(110.0),
        )


def test_release_green_requeries_platform_run_identity_for_exact_pushed_sha():
    query = RecordedPlatformCiQuery([_platform_response()])
    receipt = _release(query)

    assert query.queries == [(REPOSITORY, SHA)]
    assert receipt["platform_ci_proof"]["pushed_sha"] == SHA
    assert receipt["platform_ci_proof"]["workflow_run_id"] == WORKFLOW
    assert receipt["platform_ci_proof"]["check_run_ids"] == list(CHECK_IDS)
    assert receipt["pushed_sha_proof"] == receipt["platform_ci_proof"]["fingerprint"]
    assert grants_release_authority(receipt) is True


def test_release_green_requires_wiring_matrix_full_matrix_and_pushed_sha():
    receipt = _release()
    required = (
        "wiring_closure_fingerprint",
        "full_matrix_receipts",
        "package_manifest_receipts",
        "pushed_sha_proof",
        "platform_ci_proof",
        "compatibility_diff_receipt",
        "mixed_version_matrix_receipt",
    )
    for field in required:
        severed = deepcopy(receipt)
        severed.pop(field)
        with pytest.raises(ReleaseEvidenceError, match="fields are not closed"):
            validate_release_green(severed, now=110.0)

    for field in ("wiring_closure_fingerprint", "pushed_sha_proof"):
        severed = deepcopy(receipt)
        severed[field] = ""
        with pytest.raises(ReleaseEvidenceError, match=field):
            validate_release_green(severed, now=110.0)


def test_ci_matrix_and_terminal_full_matrix_are_closed():
    assert _release()["full_matrix_receipts"] == ["3" * 64]
    with pytest.raises(ReleaseEvidenceError, match="exactly 1"):
        _release(full_matrix_receipts=["3" * 64, "4" * 64])
    with pytest.raises(ReleaseEvidenceError, match="exactly 2"):
        _release(package_manifest_receipts=["4" * 64])


def test_release_override_records_released_unverified_and_every_skipped_proof():
    receipt = _override()

    assert validate_release_override(receipt) == receipt
    assert receipt["status"] == "released-unverified"
    assert set(receipt["skipped_proofs"]) == set(RELEASE_REQUIRED_PROOFS)
    assert receipt["cryptographic_authenticity_claimed"] is False
    assert grants_release_authority(receipt) is False

    with pytest.raises(ReleaseEvidenceError, match="every incomplete proof"):
        _override(skipped_proofs=RELEASE_REQUIRED_PROOFS[:-1])


@pytest.mark.parametrize("action", ["tag", "install", "publication"])
def test_tag_install_publication_refuse_without_release_green(action):
    query = RecordedPlatformCiQuery([_platform_response()])
    for receipt in (
        create_feature_green(
            source_sha=SHA,
            design_fingerprint=DIGEST,
            task_id="t05",
            declared_selectors=["test_a2.py::test_green"],
            focused_receipt_digests=["1" * 64],
        ),
        _override(),
    ):
        with pytest.raises(ReleaseEvidenceError, match="release-green"):
            authorize_irreversible_action(
                receipt,
                action=action,
                platform_ci_query=query,
                outside_model_human_recheck=_human(action),
                clock=FakeClock(110.0),
            )
    assert query.queries == []


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("repository_id", "other/repository"),
        ("protected_default_branch", "topic"),
        ("pushed_sha", "b" * 40),
        ("workflow_run_id", "other-run"),
        ("check_run_ids", ["other-check"]),
        ("required_check_names", ["other / check"]),
    ],
)
def test_release_green_rejects_wrong_platform_identity(field, wrong):
    response = _platform_response()
    response[field] = wrong
    query = RecordedPlatformCiQuery([response])

    with pytest.raises(ReleaseEvidenceError, match=field):
        _release(query)
    assert query.queries == [(REPOSITORY, SHA)]


def test_local_receipts_alone_never_prove_platform_or_actor_authenticity():
    values = _release_inputs()
    local = deepcopy(_platform_response())
    values["platform_ci_proof"] = local

    with pytest.raises(TypeError):
        create_release_green(**values, clock=FakeClock(110.0))

    receipt = _release()
    assert receipt["cryptographic_authenticity_claimed"] is False
    assert receipt["outside_model_human_recheck"][
        "cryptographic_authenticity_claimed"
    ] is False


def test_protected_release_consumer_rechecks_sha_ci_and_human_authority():
    receipt = _release()
    query = RecordedPlatformCiQuery([_platform_response(queried_at=109.0)])

    authorization = authorize_irreversible_action(
        receipt,
        action="tag",
        platform_ci_query=query,
        outside_model_human_recheck=_human("tag"),
        clock=FakeClock(110.0),
    )

    assert query.queries == [(REPOSITORY, SHA)]
    assert authorization["authorized"] is True
    assert authorization["source_sha"] == SHA
    assert authorization["cryptographic_authenticity_claimed"] is False

    with pytest.raises(ReleaseEvidenceError, match="action mismatch"):
        authorize_irreversible_action(
            receipt,
            action="publication",
            platform_ci_query=RecordedPlatformCiQuery([_platform_response()]),
            outside_model_human_recheck=_human("tag"),
            clock=FakeClock(110.0),
        )


@pytest.mark.parametrize("seam", EVIDENCE_FAULT_SEAMS)
def test_release_evidence_atomic_publication_recovers_each_fault(tmp_path, seam):
    store = SandboxEvidenceStore(
        tmp_path,
        "repository",
        f"fault-{EVIDENCE_FAULT_SEAMS.index(seam)}",
        fault_injector=EnumeratingFaultInjector(seam),
    )
    receipt = create_feature_green(
        source_sha=SHA,
        design_fingerprint=DIGEST,
        task_id="t05",
        declared_selectors=["test_a2.py::test_green"],
        focused_receipt_digests=["1" * 64],
    )

    try:
        published = publish(
            receipt,
            evidence_store=store,
            operation_id=f"feature-{seam}",
        )
    except InjectedFault:
        try:
            recovered = reconcile(store)
        except InjectedFault:
            recovered = reconcile(store)
        if recovered:
            published = recovered[-1]
        else:
            published = publish(
                receipt,
                evidence_store=store,
                operation_id=f"feature-{seam}",
            )

    assert reconcile(store) == (published,)
    assert published == receipt
    assert len(list((store.path / "release_evidence" / "receipts").glob("*.json"))) == 1


def test_release_evidence_reconciliation_rejects_fork_gap_collision(tmp_path):
    store = SandboxEvidenceStore(tmp_path, "repository", "chain")
    first = create_feature_green(
        source_sha=SHA,
        design_fingerprint=DIGEST,
        task_id="t05",
        declared_selectors=["test_a2.py::test_green"],
        focused_receipt_digests=["1" * 64],
    )
    publish(first, evidence_store=store, operation_id="feature")

    with pytest.raises(ReleaseEvidenceError, match="predecessor CAS mismatch"):
        publish(
            _override(),
            evidence_store=store,
            operation_id="gap",
            predecessor_fingerprint="0" * 64,
        )
    with pytest.raises(ReleaseEvidenceError, match="operation collision"):
        publish(
            _override(),
            evidence_store=store,
            operation_id="feature",
            predecessor_fingerprint=reconcile(store)[-1]["fingerprint"],
        )
    mixed_sha = create_feature_green(
        source_sha="b" * 40,
        design_fingerprint=DIGEST,
        task_id="other",
        declared_selectors=["test_other.py::test_green"],
        focused_receipt_digests=["2" * 64],
    )
    with pytest.raises(ReleaseEvidenceError, match="mixed source SHA"):
        publish(
            mixed_sha,
            evidence_store=store,
            operation_id="mixed",
            predecessor_fingerprint=reconcile(store)[-1]["fingerprint"],
        )


def test_runtime_and_public_surfaces_are_in_both_installable_archives():
    root = Path(__file__).resolve().parents[2]
    for script_name in ("package_openai.py", "package_claude.py"):
        path = root / "scripts" / script_name
        spec = importlib.util.spec_from_file_location(
            f"_r0001_{script_name.replace('.', '_')}", path
        )
        assert spec is not None and spec.loader is not None
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        files = (
            packager.package_files(packager.load_manifest())
            if script_name == "package_openai.py"
            else packager.package_files()
        )
        members = {
            candidate.relative_to(packager.ROOT).as_posix() for candidate in files
        }
        assert "taskplane/release_evidence.py" in members
