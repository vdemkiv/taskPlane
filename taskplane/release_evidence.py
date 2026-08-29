"""Closed forward-release evidence and compatibility authority for R-0001.

Feature evidence may advance Build.  It is deliberately not a release proxy.
Only a validated release-green receipt, followed by a fresh protected-platform
query and an outside-model human recheck, can authorize an irreversible
release action.  The records in this module provide attribution and channel
continuity only; none claims cryptographic actor authenticity.
"""

from __future__ import annotations

import base64
import json
import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from taskplane.delivery_ports import (
    Clock,
    EvidenceStore,
    GitRunner,
    PlatformCiQuery,
    PreparedEvidence,
    canonical_json,
    content_fingerprint,
)


FEATURE_GREEN_SCHEMA = "taskplane.feature-green-receipt/v1"
RELEASE_GREEN_SCHEMA = "taskplane.release-green-receipt/v1"
RELEASE_OVERRIDE_SCHEMA = "taskplane.release-override-receipt/v1"
PLATFORM_CI_PROOF_SCHEMA = "taskplane.platform-ci-proof/v1"
RELEASE_PUBLICATION_SCHEMA = "taskplane.release-evidence-publication/v1"
COMPATIBILITY_DIFF_SCHEMA = "taskplane.compatibility-diff-receipt/v1"
MIXED_VERSION_MATRIX_SCHEMA = "taskplane.mixed-version-matrix-receipt/v1"
PROTECTED_RELEASE_AUTHORIZATION_SCHEMA = (
    "taskplane.protected-release-authorization/v1"
)

CURRENT_VERSION = "2.17.26"
PREVIOUS_VERSION = "2.17.20"
COMPATIBILITY_PREVIOUS_VERSION = "2.17.25"
SUPERSEDED_CANDIDATE_VERSION = "2.17.25"
PREVIOUS_RELEASE_TAG = "v2.17.20"
PREVIOUS_RELEASE_COMMIT = "4a0378e7f080136d27f01d4ab7ecdf9bac8a1ad6"
HISTORICAL_GRAPH_REVISION = "2757822ede49177fc52de8c173302286364d6206"
HISTORICAL_GRAPH_VERIFIER_PATHS = (
    "scripts/ci_graph_accuracy.py",
    "taskplane/depgraph.py",
)
# R-0002 legitimately extended depgraph's architecture evidence after the
# attributed R-0001 revision.  Pin both ends of that reviewed lineage instead
# of requiring the verifier bytes to remain frozen forever.  These are Git
# blob object ids resolved by ``<revision>:<path>``; any unreviewed historical
# rewrite or future verifier edit still fails closed.
HISTORICAL_GRAPH_VERIFIER_BLOBS = {
    "scripts/ci_graph_accuracy.py": {
        "historical": "c34136b3ea6275665e9a95f9fbc87850c161034d",
        "current": "c34136b3ea6275665e9a95f9fbc87850c161034d",
    },
    "taskplane/depgraph.py": {
        "historical": "3a98d31a9dfeea8456a123cef4636cf004e56bee",
        "current": "13aca2b71e907dc8fafa7351786cfefd39075e30",
    },
}
IRREVERSIBLE_RELEASE_ACTIONS = frozenset(
    {"tag", "install", "publication", "publish"}
)
MAX_PLATFORM_PROOF_AGE_SECONDS = 900.0

RELEASE_REQUIRED_PROOFS = (
    "wiring_closure",
    "feature_receipts",
    "terminal_full_matrix",
    "openai_package_manifest",
    "claude_package_manifest",
    "pushed_sha_platform_ci",
    "compatibility_policy",
    "schema_bundle",
    "compatibility_diff",
    "mixed_n_n_minus_1_matrix",
    "live_host_canary",
    "recorded_event_replay",
    "host_action_capability_refusal",
    "task_dispatch_capability_default_deny",
    "outside_model_human_recheck",
    "reviewed_prompt_injection_reference",
)

_FEATURE_FIELDS = frozenset(
    {
        "schema",
        "source_sha",
        "design_fingerprint",
        "task_id",
        "declared_selectors",
        "focused_receipt_digests",
        "status",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
)
_PLATFORM_FIELDS = frozenset(
    {
        "schema",
        "provider",
        "repository_id",
        "protected_default_branch",
        "pushed_sha",
        "workflow_run_id",
        "check_run_ids",
        "required_check_names",
        "conclusions",
        "queried_at",
        "fresh_until",
        "platform_response_digest",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
)
_RELEASE_FIELDS = frozenset(
    {
        "schema",
        "source_sha",
        "version",
        "wiring_closure_fingerprint",
        "feature_receipt_digests",
        "full_matrix_receipts",
        "package_manifest_receipts",
        "pushed_sha_proof",
        "platform_ci_proof",
        "compatibility_policy_fingerprint",
        "schema_bundle_fingerprint",
        "compatibility_diff_receipt",
        "mixed_version_matrix_receipt",
        "live_host_canary_receipt",
        "recorded_event_replay_receipt",
        "host_action_capability_refusal_receipt",
        "task_dispatch_capability_default_deny_receipt",
        "outside_model_human_recheck",
        "reviewed_prompt_injection_reference_digest",
        "status",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
)
_OVERRIDE_FIELDS = frozenset(
    {
        "schema",
        "source_sha",
        "status",
        "required_proofs",
        "completed_proofs",
        "skipped_proofs",
        "human_authority_receipt",
        "reason",
        "recorded_at",
        "predecessor_digest",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
)
_HUMAN_RECHECK_FIELDS = frozenset(
    {
        "actor",
        "channel",
        "action",
        "source_sha",
        "confirmed",
        "cryptographic_authenticity_claimed",
    }
)


class ReleaseEvidenceError(ValueError):
    """Release evidence is incomplete, stale, ambiguous, or over-authorized."""


def terminal_release_evidence_surface(
    identity: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Prepare release evidence as one non-authoritative terminal surface."""
    from taskplane import terminal_truth
    return terminal_truth.prepare_terminal_surface(
        "release_evidence", identity, dict(evidence)
    )


def validate_terminal_release_claim(
    terminal_receipt: Mapping[str, Any],
    candidate_wiring_receipt: Mapping[str, Any],
    *,
    repository_fingerprint: str,
    full_source_sha: str,
    requirement_id: str,
) -> dict[str, Any]:
    """Reject opaque/foreign wiring before accepting any release claim."""
    from taskplane import terminal_truth, wiring_closure
    try:
        wiring = wiring_closure.validate_candidate_checkout_receipt(
            candidate_wiring_receipt,
            expected_repository_fingerprint=repository_fingerprint,
            expected_head_sha=full_source_sha,
            expected_requirement_id=requirement_id,
        )
    except wiring_closure.WiringClosureError as exc:
        raise ReleaseEvidenceError(str(exc)) from exc
    try:
        terminal = terminal_truth.assert_terminal_authority(
            terminal_receipt,
            expected_sha=full_source_sha,
            expected_requirement_id=requirement_id,
        )
    except terminal_truth.TerminalTruthError as exc:
        raise ReleaseEvidenceError(exc.detail) from exc
    if terminal["bundle"]["identity"]["candidate_wiring_fingerprint"] != \
            wiring["fingerprint"]:
        raise ReleaseEvidenceError("terminal authority names another wiring receipt")
    return terminal


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseEvidenceError(f"{field_name} is required")
    return value.strip()


def _closed(value: Any, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ReleaseEvidenceError(f"{name} fields are not closed")
    return value


def _fingerprint(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    text = _text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ReleaseEvidenceError(
            f"{field_name} must be a lowercase SHA-256 fingerprint"
        )
    return text


def _source_sha(value: Any, field_name: str = "source_sha") -> str:
    text = _text(value, field_name)
    if len(text) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ReleaseEvidenceError(
            f"{field_name} must be an exact lowercase Git SHA"
        )
    return text


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReleaseEvidenceError(f"{field_name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ReleaseEvidenceError(f"{field_name} must be a finite number")
    return result


def _strings(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    exact_count: int | None = None,
) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ReleaseEvidenceError(f"{field_name} must be a collection")
    result = [_text(item, field_name) for item in value]
    if not allow_empty and not result:
        raise ReleaseEvidenceError(f"{field_name} must not be empty")
    if exact_count is not None and len(result) != exact_count:
        raise ReleaseEvidenceError(
            f"{field_name} must contain exactly {exact_count} receipts"
        )
    if len(result) != len(set(result)):
        raise ReleaseEvidenceError(f"{field_name} contains duplicates")
    return result


def _fingerprints(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    exact_count: int | None = None,
) -> list[str]:
    values = _strings(
        value,
        field_name,
        allow_empty=allow_empty,
        exact_count=exact_count,
    )
    return [_fingerprint(item, field_name) for item in values]  # type: ignore[list-item]


def _seal(projection: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(projection)
    sealed["fingerprint"] = content_fingerprint(sealed)
    return sealed


def _verify_seal(receipt: Mapping[str, Any], fields: frozenset[str], name: str) -> None:
    projection = {key: receipt[key] for key in fields - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise ReleaseEvidenceError(f"{name} fingerprint mismatch")


def _no_authenticity_claim(value: Any, name: str) -> None:
    if value is not False:
        raise ReleaseEvidenceError(
            f"{name} must state cryptographic_authenticity_claimed=false"
        )


def create_feature_green(
    *,
    source_sha: str,
    design_fingerprint: str,
    task_id: str,
    declared_selectors: Sequence[str],
    focused_receipt_digests: Sequence[str],
) -> dict[str, Any]:
    """Create focused Build evidence with explicitly zero release authority."""
    receipt = _seal(
        {
            "schema": FEATURE_GREEN_SCHEMA,
            "source_sha": _source_sha(source_sha),
            "design_fingerprint": _fingerprint(
                design_fingerprint, "design_fingerprint"
            ),
            "task_id": _text(task_id, "task_id"),
            "declared_selectors": _strings(
                declared_selectors, "declared_selectors"
            ),
            "focused_receipt_digests": _fingerprints(
                focused_receipt_digests, "focused_receipt_digests"
            ),
            "status": "feature-green",
            "cryptographic_authenticity_claimed": False,
        }
    )
    return validate_feature_green(receipt)


def validate_feature_green(receipt: Mapping[str, Any]) -> dict[str, Any]:
    _closed(receipt, _FEATURE_FIELDS, "feature-green receipt")
    if receipt.get("schema") != FEATURE_GREEN_SCHEMA:
        raise ReleaseEvidenceError("feature-green receipt schema is invalid")
    _source_sha(receipt.get("source_sha"))
    _fingerprint(receipt.get("design_fingerprint"), "design_fingerprint")
    _text(receipt.get("task_id"), "task_id")
    _strings(receipt.get("declared_selectors"), "declared_selectors")
    _fingerprints(
        receipt.get("focused_receipt_digests"), "focused_receipt_digests"
    )
    if receipt.get("status") != "feature-green":
        raise ReleaseEvidenceError("feature-green receipt status is invalid")
    _no_authenticity_claim(
        receipt.get("cryptographic_authenticity_claimed"), "feature-green receipt"
    )
    _verify_seal(receipt, _FEATURE_FIELDS, "feature-green receipt")
    return dict(receipt)


def _human_recheck(
    value: Any,
    *,
    source_sha: str,
    action: str,
) -> dict[str, Any]:
    _closed(value, _HUMAN_RECHECK_FIELDS, "outside-model human recheck")
    actor = _text(value.get("actor"), "outside-model human actor")
    if not actor.startswith("human:") or not actor.removeprefix("human:").strip():
        raise ReleaseEvidenceError(
            "outside-model human actor must be attributed as human:<identity>"
        )
    if value.get("channel") != "outside-model":
        raise ReleaseEvidenceError("human recheck channel must be outside-model")
    if value.get("action") != action:
        raise ReleaseEvidenceError("outside-model human recheck action mismatch")
    if value.get("source_sha") != source_sha:
        raise ReleaseEvidenceError("outside-model human recheck source_sha mismatch")
    if value.get("confirmed") is not True:
        raise ReleaseEvidenceError("outside-model human recheck is not confirmed")
    _no_authenticity_claim(
        value.get("cryptographic_authenticity_claimed"),
        "outside-model human recheck",
    )
    return dict(value)


def _validate_platform_response(
    response: Any,
    *,
    repository_id: str,
    protected_default_branch: str,
    pushed_sha: str,
    workflow_run_id: str,
    check_run_ids: Sequence[str],
    required_check_names: Sequence[str],
    now: float,
) -> dict[str, Any]:
    raw_fields = _PLATFORM_FIELDS - {
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
    if not isinstance(response, Mapping) or set(response) not in {
        raw_fields,
        _PLATFORM_FIELDS,
    }:
        raise ReleaseEvidenceError("platform CI response fields are not closed")
    proof = dict(response)
    if proof.get("schema") != PLATFORM_CI_PROOF_SCHEMA:
        raise ReleaseEvidenceError("platform CI proof schema is invalid")
    _text(proof.get("provider"), "provider")
    expected_scalars = {
        "repository_id": repository_id,
        "protected_default_branch": protected_default_branch,
        "pushed_sha": pushed_sha,
        "workflow_run_id": workflow_run_id,
    }
    for field_name, expected in expected_scalars.items():
        if proof.get(field_name) != expected:
            raise ReleaseEvidenceError(f"platform CI {field_name} mismatch")
    _source_sha(proof.get("pushed_sha"), "pushed_sha")
    expected_check_ids = _strings(check_run_ids, "check_run_ids")
    expected_check_names = _strings(
        required_check_names, "required_check_names"
    )
    actual_check_ids = _strings(proof.get("check_run_ids"), "check_run_ids")
    actual_check_names = _strings(
        proof.get("required_check_names"), "required_check_names"
    )
    if actual_check_ids != expected_check_ids:
        raise ReleaseEvidenceError("platform CI check_run_ids mismatch")
    if actual_check_names != expected_check_names:
        raise ReleaseEvidenceError("platform CI required_check_names mismatch")
    conclusions = proof.get("conclusions")
    if not isinstance(conclusions, Mapping) or set(conclusions) != set(
        expected_check_names
    ):
        raise ReleaseEvidenceError("platform CI conclusions identities mismatch")
    if any(value != "success" for value in conclusions.values()):
        raise ReleaseEvidenceError("platform CI required check was not successful")
    queried_at = _number(proof.get("queried_at"), "queried_at")
    fresh_until = _number(proof.get("fresh_until"), "fresh_until")
    if queried_at > now or fresh_until <= now:
        raise ReleaseEvidenceError("platform CI proof is stale or future-dated")
    if fresh_until - queried_at > MAX_PLATFORM_PROOF_AGE_SECONDS:
        raise ReleaseEvidenceError("platform CI proof freshness window is too wide")
    _fingerprint(
        proof.get("platform_response_digest"), "platform_response_digest"
    )
    if "cryptographic_authenticity_claimed" in proof:
        _no_authenticity_claim(
            proof.get("cryptographic_authenticity_claimed"), "platform CI proof"
        )
    proof["cryptographic_authenticity_claimed"] = False
    claimed_fingerprint = proof.pop("fingerprint", None)
    sealed = _seal(proof)
    if claimed_fingerprint is not None and claimed_fingerprint != sealed["fingerprint"]:
        raise ReleaseEvidenceError("platform CI proof fingerprint mismatch")
    return sealed


def _query_platform(
    query: PlatformCiQuery,
    *,
    repository_id: str,
    protected_default_branch: str,
    pushed_sha: str,
    workflow_run_id: str,
    check_run_ids: Sequence[str],
    required_check_names: Sequence[str],
    clock: Clock,
) -> dict[str, Any]:
    if not isinstance(query, PlatformCiQuery):
        raise ReleaseEvidenceError("an injected PlatformCiQuery is required")
    try:
        response = query.query(repository_id=repository_id, pushed_sha=pushed_sha)
    except Exception as exc:
        raise ReleaseEvidenceError("independent platform CI query failed") from exc
    return _validate_platform_response(
        response,
        repository_id=repository_id,
        protected_default_branch=protected_default_branch,
        pushed_sha=pushed_sha,
        workflow_run_id=workflow_run_id,
        check_run_ids=check_run_ids,
        required_check_names=required_check_names,
        now=_number(clock.wall_time(), "clock.wall_time"),
    )


def validate_platform_ci_proof(receipt: Mapping[str, Any], *, now: float) -> dict[str, Any]:
    _closed(receipt, _PLATFORM_FIELDS, "platform CI proof")
    return _validate_platform_response(
        receipt,
        repository_id=receipt.get("repository_id"),
        protected_default_branch=receipt.get("protected_default_branch"),
        pushed_sha=receipt.get("pushed_sha"),
        workflow_run_id=receipt.get("workflow_run_id"),
        check_run_ids=receipt.get("check_run_ids"),
        required_check_names=receipt.get("required_check_names"),
        now=now,
    )


def create_release_green(
    *,
    source_sha: str,
    version: str,
    wiring_closure_fingerprint: str,
    feature_receipt_digests: Sequence[str],
    full_matrix_receipts: Sequence[str],
    package_manifest_receipts: Sequence[str],
    compatibility_policy_fingerprint: str,
    schema_bundle_fingerprint: str,
    compatibility_diff_receipt: str,
    mixed_version_matrix_receipt: str,
    live_host_canary_receipt: str,
    recorded_event_replay_receipt: str,
    host_action_capability_refusal_receipt: str,
    task_dispatch_capability_default_deny_receipt: str,
    reviewed_prompt_injection_reference_digest: str,
    repository_id: str,
    protected_default_branch: str,
    workflow_run_id: str,
    check_run_ids: Sequence[str],
    required_check_names: Sequence[str],
    outside_model_human_recheck: Mapping[str, Any],
    platform_ci_query: PlatformCiQuery,
    clock: Clock,
) -> dict[str, Any]:
    """Create final-SHA authority only after an independent platform query."""
    sha = _source_sha(source_sha)
    if version != CURRENT_VERSION:
        raise ReleaseEvidenceError(
            f"forward release version must be exactly {CURRENT_VERSION}"
        )
    repository = _text(repository_id, "repository_id")
    branch = _text(protected_default_branch, "protected_default_branch")
    workflow = _text(workflow_run_id, "workflow_run_id")
    check_ids = _strings(check_run_ids, "check_run_ids")
    check_names = _strings(required_check_names, "required_check_names")
    human_recheck = _human_recheck(
        outside_model_human_recheck,
        source_sha=sha,
        action="release-candidate",
    )
    platform_proof = _query_platform(
        platform_ci_query,
        repository_id=repository,
        protected_default_branch=branch,
        pushed_sha=sha,
        workflow_run_id=workflow,
        check_run_ids=check_ids,
        required_check_names=check_names,
        clock=clock,
    )
    receipt = _seal(
        {
            "schema": RELEASE_GREEN_SCHEMA,
            "source_sha": sha,
            "version": version,
            "wiring_closure_fingerprint": _fingerprint(
                wiring_closure_fingerprint, "wiring_closure_fingerprint"
            ),
            "feature_receipt_digests": _fingerprints(
                feature_receipt_digests, "feature_receipt_digests"
            ),
            "full_matrix_receipts": _fingerprints(
                full_matrix_receipts, "full_matrix_receipts", exact_count=1
            ),
            "package_manifest_receipts": _fingerprints(
                package_manifest_receipts,
                "package_manifest_receipts",
                exact_count=2,
            ),
            "pushed_sha_proof": platform_proof["fingerprint"],
            "platform_ci_proof": platform_proof,
            "compatibility_policy_fingerprint": _fingerprint(
                compatibility_policy_fingerprint,
                "compatibility_policy_fingerprint",
            ),
            "schema_bundle_fingerprint": _fingerprint(
                schema_bundle_fingerprint, "schema_bundle_fingerprint"
            ),
            "compatibility_diff_receipt": _fingerprint(
                compatibility_diff_receipt, "compatibility_diff_receipt"
            ),
            "mixed_version_matrix_receipt": _fingerprint(
                mixed_version_matrix_receipt, "mixed_version_matrix_receipt"
            ),
            "live_host_canary_receipt": _fingerprint(
                live_host_canary_receipt, "live_host_canary_receipt"
            ),
            "recorded_event_replay_receipt": _fingerprint(
                recorded_event_replay_receipt, "recorded_event_replay_receipt"
            ),
            "host_action_capability_refusal_receipt": _fingerprint(
                host_action_capability_refusal_receipt,
                "host_action_capability_refusal_receipt",
            ),
            "task_dispatch_capability_default_deny_receipt": _fingerprint(
                task_dispatch_capability_default_deny_receipt,
                "task_dispatch_capability_default_deny_receipt",
            ),
            "outside_model_human_recheck": human_recheck,
            "reviewed_prompt_injection_reference_digest": _fingerprint(
                reviewed_prompt_injection_reference_digest,
                "reviewed_prompt_injection_reference_digest",
            ),
            "status": "release-green",
            "cryptographic_authenticity_claimed": False,
        }
    )
    return validate_release_green(receipt, now=clock.wall_time())


def validate_release_green(
    receipt: Mapping[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    _closed(receipt, _RELEASE_FIELDS, "release-green receipt")
    if receipt.get("schema") != RELEASE_GREEN_SCHEMA:
        raise ReleaseEvidenceError("release-green receipt schema is invalid")
    sha = _source_sha(receipt.get("source_sha"))
    if receipt.get("version") != CURRENT_VERSION:
        raise ReleaseEvidenceError(
            f"release-green version must be exactly {CURRENT_VERSION}"
        )
    for field_name in (
        "wiring_closure_fingerprint",
        "pushed_sha_proof",
        "compatibility_policy_fingerprint",
        "schema_bundle_fingerprint",
        "compatibility_diff_receipt",
        "mixed_version_matrix_receipt",
        "live_host_canary_receipt",
        "recorded_event_replay_receipt",
        "host_action_capability_refusal_receipt",
        "task_dispatch_capability_default_deny_receipt",
        "reviewed_prompt_injection_reference_digest",
    ):
        _fingerprint(receipt.get(field_name), field_name)
    _fingerprints(
        receipt.get("feature_receipt_digests"), "feature_receipt_digests"
    )
    _fingerprints(
        receipt.get("full_matrix_receipts"),
        "full_matrix_receipts",
        exact_count=1,
    )
    _fingerprints(
        receipt.get("package_manifest_receipts"),
        "package_manifest_receipts",
        exact_count=2,
    )
    proof = receipt.get("platform_ci_proof")
    if not isinstance(proof, Mapping):
        raise ReleaseEvidenceError("platform_ci_proof is required")
    checked_proof = validate_platform_ci_proof(
        proof,
        now=_number(now if now is not None else proof.get("queried_at"), "now"),
    )
    if checked_proof["pushed_sha"] != sha:
        raise ReleaseEvidenceError("platform CI pushed_sha does not bind source_sha")
    if checked_proof["fingerprint"] != receipt.get("pushed_sha_proof"):
        raise ReleaseEvidenceError("pushed_sha_proof fingerprint mismatch")
    _human_recheck(
        receipt.get("outside_model_human_recheck"),
        source_sha=sha,
        action="release-candidate",
    )
    if receipt.get("status") != "release-green":
        raise ReleaseEvidenceError("release-green receipt status is invalid")
    _no_authenticity_claim(
        receipt.get("cryptographic_authenticity_claimed"), "release-green receipt"
    )
    _verify_seal(receipt, _RELEASE_FIELDS, "release-green receipt")
    return dict(receipt)


def create_release_override(
    *,
    source_sha: str,
    skipped_proofs: Sequence[str],
    human_authority_receipt: Mapping[str, Any],
    reason: str,
    recorded_at: float,
    predecessor_digest: str | None = None,
    required_proofs: Sequence[str] = RELEASE_REQUIRED_PROOFS,
    completed_proofs: Sequence[str] = (),
) -> dict[str, Any]:
    """Record an auditable exception that intentionally grants no authority."""
    required = _strings(required_proofs, "required_proofs")
    completed = _strings(completed_proofs, "completed_proofs", allow_empty=True)
    skipped = _strings(skipped_proofs, "skipped_proofs")
    if set(required) != set(RELEASE_REQUIRED_PROOFS):
        raise ReleaseEvidenceError("required_proofs must name the closed release proof set")
    if set(completed).intersection(skipped):
        raise ReleaseEvidenceError("completed and skipped proofs overlap")
    if set(completed).union(skipped) != set(required):
        raise ReleaseEvidenceError("skipped_proofs must list every incomplete proof")
    sha = _source_sha(source_sha)
    authority = _human_recheck(
        human_authority_receipt,
        source_sha=sha,
        action="release-override",
    )
    receipt = _seal(
        {
            "schema": RELEASE_OVERRIDE_SCHEMA,
            "source_sha": sha,
            "status": "released-unverified",
            "required_proofs": required,
            "completed_proofs": completed,
            "skipped_proofs": skipped,
            "human_authority_receipt": authority,
            "reason": _text(reason, "reason"),
            "recorded_at": _number(recorded_at, "recorded_at"),
            "predecessor_digest": _fingerprint(
                predecessor_digest, "predecessor_digest", optional=True
            ),
            "cryptographic_authenticity_claimed": False,
        }
    )
    return validate_release_override(receipt)


def validate_release_override(receipt: Mapping[str, Any]) -> dict[str, Any]:
    _closed(receipt, _OVERRIDE_FIELDS, "release override receipt")
    if receipt.get("schema") != RELEASE_OVERRIDE_SCHEMA:
        raise ReleaseEvidenceError("release override receipt schema is invalid")
    sha = _source_sha(receipt.get("source_sha"))
    if receipt.get("status") != "released-unverified":
        raise ReleaseEvidenceError("release override must be released-unverified")
    required = _strings(receipt.get("required_proofs"), "required_proofs")
    completed = _strings(
        receipt.get("completed_proofs"), "completed_proofs", allow_empty=True
    )
    skipped = _strings(receipt.get("skipped_proofs"), "skipped_proofs")
    if set(required) != set(RELEASE_REQUIRED_PROOFS):
        raise ReleaseEvidenceError("release override required_proofs are invalid")
    if set(completed).intersection(skipped) or set(completed).union(skipped) != set(
        required
    ):
        raise ReleaseEvidenceError("release override does not list every skipped proof")
    _human_recheck(
        receipt.get("human_authority_receipt"),
        source_sha=sha,
        action="release-override",
    )
    _text(receipt.get("reason"), "reason")
    _number(receipt.get("recorded_at"), "recorded_at")
    _fingerprint(
        receipt.get("predecessor_digest"), "predecessor_digest", optional=True
    )
    _no_authenticity_claim(
        receipt.get("cryptographic_authenticity_claimed"), "release override receipt"
    )
    _verify_seal(receipt, _OVERRIDE_FIELDS, "release override receipt")
    return dict(receipt)


def validate_receipt(receipt: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
    schema = receipt.get("schema") if isinstance(receipt, Mapping) else None
    if schema == FEATURE_GREEN_SCHEMA:
        return validate_feature_green(receipt)
    if schema == RELEASE_GREEN_SCHEMA:
        return validate_release_green(receipt, now=now)
    if schema == RELEASE_OVERRIDE_SCHEMA:
        return validate_release_override(receipt)
    raise ReleaseEvidenceError("unknown release evidence schema")


def grants_release_authority(receipt: Mapping[str, Any]) -> bool:
    """Return true only for a complete, closed release-green receipt."""
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RELEASE_GREEN_SCHEMA:
        return False
    try:
        validate_release_green(receipt)
    except ReleaseEvidenceError:
        return False
    return True


def authorize_irreversible_action(
    receipt: Mapping[str, Any],
    *,
    action: str,
    platform_ci_query: PlatformCiQuery,
    outside_model_human_recheck: Mapping[str, Any],
    clock: Clock,
) -> dict[str, Any]:
    """Protected consumer: re-query CI and recheck human authority immediately."""
    normalized_action = _text(action, "action")
    if normalized_action not in IRREVERSIBLE_RELEASE_ACTIONS:
        raise ReleaseEvidenceError("action is not a protected release action")
    if receipt.get("schema") != RELEASE_GREEN_SCHEMA:
        raise ReleaseEvidenceError("a release-green receipt is required")
    release = validate_release_green(receipt, now=clock.wall_time())
    prior_proof = release["platform_ci_proof"]
    fresh_proof = _query_platform(
        platform_ci_query,
        repository_id=prior_proof["repository_id"],
        protected_default_branch=prior_proof["protected_default_branch"],
        pushed_sha=release["source_sha"],
        workflow_run_id=prior_proof["workflow_run_id"],
        check_run_ids=prior_proof["check_run_ids"],
        required_check_names=prior_proof["required_check_names"],
        clock=clock,
    )
    human = _human_recheck(
        outside_model_human_recheck,
        source_sha=release["source_sha"],
        action=normalized_action,
    )
    return _seal(
        {
            "schema": PROTECTED_RELEASE_AUTHORIZATION_SCHEMA,
            "action": normalized_action,
            "source_sha": release["source_sha"],
            "release_green_fingerprint": release["fingerprint"],
            "independently_requeried_platform_ci_proof": fresh_proof,
            "outside_model_human_recheck": human,
            "authorized": True,
            "cryptographic_authenticity_claimed": False,
        }
    )


def forward_history_receipt() -> dict[str, Any]:
    """Return the immutable historical disposition and forward-only target."""
    return _seal(
        {
            "schema": "taskplane.forward-release-history/v1",
            "released_generation": {
                "version": PREVIOUS_VERSION,
                "tag": PREVIOUS_RELEASE_TAG,
                "commit": PREVIOUS_RELEASE_COMMIT,
                "status": "released-incomplete",
                "re_release": False,
            },
            "forward_generation": {
                "version": CURRENT_VERSION,
                "repair_of": SUPERSEDED_CANDIDATE_VERSION,
                "history_rewrite": False,
            },
            "historical_graph": {
                "revision": HISTORICAL_GRAPH_REVISION,
                "classification": "attributed-inherited-limitation",
                "history_rewrite": False,
                "re_release": False,
                "verifier_weakened": False,
            },
            "cryptographic_authenticity_claimed": False,
        }
    )


def validate_forward_history(
    receipt: Mapping[str, Any] | None = None,
    *,
    git_runner: GitRunner | None = None,
    repository: str | Path | None = None,
) -> dict[str, Any]:
    expected = forward_history_receipt()
    value = dict(receipt or expected)
    if value != expected:
        raise ReleaseEvidenceError("forward release history disposition changed")
    if git_runner is not None:
        cwd = str(repository) if repository is not None else None

        def resolve(revision: str) -> str:
            result = git_runner.run(("rev-parse", revision), cwd=cwd)
            if result.returncode != 0:
                raise ReleaseEvidenceError(f"release history cannot resolve {revision}")
            return result.stdout.strip()

        if resolve(f"{PREVIOUS_RELEASE_TAG}^{{}}") != PREVIOUS_RELEASE_COMMIT:
            raise ReleaseEvidenceError("v2.17.20 tag history was rewritten or re-released")
        if resolve(HISTORICAL_GRAPH_REVISION) != HISTORICAL_GRAPH_REVISION:
            raise ReleaseEvidenceError("historical graph revision attribution changed")
        if tuple(HISTORICAL_GRAPH_VERIFIER_BLOBS) != \
                HISTORICAL_GRAPH_VERIFIER_PATHS:
            raise ReleaseEvidenceError(
                "historical graph verifier lineage is not closed")
        for path in HISTORICAL_GRAPH_VERIFIER_PATHS:
            historical_blob = resolve(f"{HISTORICAL_GRAPH_REVISION}:{path}")
            current_blob = resolve(f"HEAD:{path}")
            expected_blobs = HISTORICAL_GRAPH_VERIFIER_BLOBS[path]
            if historical_blob != expected_blobs["historical"]:
                raise ReleaseEvidenceError(
                    f"historical graph verifier history changed: {path}")
            if current_blob != expected_blobs["current"]:
                raise ReleaseEvidenceError(
                    f"historical graph verifier changed outside reviewed lineage: {path}")
    return expected


def load_compatibility_policy(path: str | Path | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path is not None else (
        Path(__file__).resolve().parents[1] / "design" / "compatibility.json"
    )
    try:
        value = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError("compatibility policy is unavailable") from exc
    return validate_compatibility_policy(value)


def validate_compatibility_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        raise ReleaseEvidenceError("compatibility policy must be a mapping")
    if policy.get("schema") != "taskplane.compatibility-policy/v1":
        raise ReleaseEvidenceError("compatibility policy schema is invalid")
    window = policy.get("window")
    if not isinstance(window, Mapping) or window.get("current") != CURRENT_VERSION or window.get(
        "previous"
    ) != COMPATIBILITY_PREVIOUS_VERSION:
        raise ReleaseEvidenceError(
            "compatibility window must be "
            f"N={CURRENT_VERSION}/N-1={COMPATIBILITY_PREVIOUS_VERSION}"
        )
    evolution = policy.get("json_evolution")
    if not isinstance(evolution, Mapping) or evolution.get("authority_objects") != "closed" or evolution.get(
        "additionalProperties"
    ) is not False or evolution.get("unknown_schema") != "refuse":
        raise ReleaseEvidenceError("compatibility JSON evolution must fail closed")
    cutover = policy.get("cutover")
    if not isinstance(cutover, Mapping) or cutover.get("mode") != "emit-before-require":
        raise ReleaseEvidenceError("compatibility cutover must be emit-before-require")
    states = cutover.get("states")
    if not isinstance(states, list) or [row.get("id") for row in states if isinstance(row, Mapping)] != [
        "emit",
        "observe",
        "require",
    ]:
        raise ReleaseEvidenceError("compatibility cutover states are invalid")
    matrix = policy.get("matrix")
    expected_pairs = {
        (CURRENT_VERSION, CURRENT_VERSION),
        (CURRENT_VERSION, COMPATIBILITY_PREVIOUS_VERSION),
        (COMPATIBILITY_PREVIOUS_VERSION, CURRENT_VERSION),
        (COMPATIBILITY_PREVIOUS_VERSION, COMPATIBILITY_PREVIOUS_VERSION),
    }
    if not isinstance(matrix, list) or len(matrix) != 4 or {
        (row.get("plugin"), row.get("host"))
        for row in matrix
        if isinstance(row, Mapping)
    } != expected_pairs:
        raise ReleaseEvidenceError("compatibility matrix must contain all four N/N-1 cells")
    if policy.get("authority_limitations", {}).get(
        "cryptographic_authenticity_claimed"
    ) is not False:
        raise ReleaseEvidenceError("compatibility policy must not claim authenticity")
    try:
        canonical_json(policy)
    except (TypeError, ValueError) as exc:
        raise ReleaseEvidenceError("compatibility policy must be canonical JSON") from exc
    return json.loads(canonical_json(policy))


def compatibility_policy_fingerprint(policy: Mapping[str, Any]) -> str:
    return content_fingerprint(validate_compatibility_policy(policy))


def compatibility_cell(
    plugin_version: str,
    host_version: str,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validated = validate_compatibility_policy(policy or load_compatibility_policy())
    for row in validated["matrix"]:
        if row["plugin"] == plugin_version and row["host"] == host_version:
            return dict(row)
    raise ReleaseEvidenceError("unknown plugin/host generation")


def create_mixed_version_matrix_receipt(
    *,
    source_sha: str,
    observations: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validated_policy = validate_compatibility_policy(
        policy or load_compatibility_policy()
    )
    sha = _source_sha(source_sha)
    if isinstance(observations, (str, bytes, bytearray)) or not isinstance(
        observations, Sequence
    ):
        raise ReleaseEvidenceError("matrix observations must be a collection")
    expected = {
        (row["plugin"], row["host"]): row for row in validated_policy["matrix"]
    }
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    fields = {"plugin", "host", "feature", "release", "source_sha", "observed"}
    for observation in observations:
        if not isinstance(observation, Mapping) or set(observation) != fields:
            raise ReleaseEvidenceError("matrix observation fields are not closed")
        key = (observation.get("plugin"), observation.get("host"))
        if key in seen or key not in expected:
            raise ReleaseEvidenceError("matrix observation cell is duplicate or unknown")
        if observation.get("source_sha") != sha:
            raise ReleaseEvidenceError("matrix observations must bind one exact source_sha")
        if observation.get("observed") is not True:
            raise ReleaseEvidenceError("matrix observation is incomplete")
        if observation.get("feature") != expected[key]["feature"] or observation.get(
            "release"
        ) != expected[key]["release"]:
            raise ReleaseEvidenceError("matrix observation result differs from policy")
        normalized.append(dict(observation))
        seen.add(key)
    if seen != set(expected):
        raise ReleaseEvidenceError("mixed-version matrix is missing an N/N-1 cell")
    normalized.sort(key=lambda row: (row["plugin"], row["host"]), reverse=True)
    return _seal(
        {
            "schema": MIXED_VERSION_MATRIX_SCHEMA,
            "source_sha": sha,
            "compatibility_policy_fingerprint": content_fingerprint(
                validated_policy
            ),
            "cells": normalized,
            "status": "complete",
            "cryptographic_authenticity_claimed": False,
        }
    )


def validate_mixed_version_matrix_receipt(
    receipt: Mapping[str, Any], *, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    fields = {
        "schema",
        "source_sha",
        "compatibility_policy_fingerprint",
        "cells",
        "status",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != fields:
        raise ReleaseEvidenceError("mixed-version matrix receipt fields are not closed")
    if receipt.get("schema") != MIXED_VERSION_MATRIX_SCHEMA:
        raise ReleaseEvidenceError("mixed-version matrix receipt schema is invalid")
    rebuilt = create_mixed_version_matrix_receipt(
        source_sha=receipt.get("source_sha"),
        observations=receipt.get("cells"),
        policy=policy,
    )
    if receipt != rebuilt:
        raise ReleaseEvidenceError("mixed-version matrix receipt fingerprint mismatch")
    return dict(receipt)


def cutover_capabilities_required(
    state: str,
    *,
    matrix_receipt: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    validated = validate_compatibility_policy(policy or load_compatibility_policy())
    states = {row["id"]: row for row in validated["cutover"]["states"]}
    if state not in states:
        raise ReleaseEvidenceError("unknown compatibility cutover state")
    if state == "require":
        if matrix_receipt is None:
            raise ReleaseEvidenceError("require cutover needs a complete mixed-version matrix")
        validate_mixed_version_matrix_receipt(matrix_receipt, policy=validated)
    return bool(states[state]["require_capabilities"])


def classify_schema_changes(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify every structural JSON difference by exact JSON-pointer path."""
    if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
        raise ReleaseEvidenceError("schema versions must be mappings")
    changes: list[dict[str, Any]] = []

    def visit(old: Any, new: Any, path: str) -> None:
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for key in sorted(set(old).union(new)):
                child = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
                if key not in old:
                    changes.append({"path": child, "classification": "added"})
                elif key not in new:
                    changes.append({"path": child, "classification": "removed"})
                else:
                    visit(old[key], new[key], child)
        elif isinstance(old, list) and isinstance(new, list):
            limit = max(len(old), len(new))
            for index in range(limit):
                child = f"{path}/{index}"
                if index >= len(old):
                    changes.append({"path": child, "classification": "added"})
                elif index >= len(new):
                    changes.append({"path": child, "classification": "removed"})
                else:
                    visit(old[index], new[index], child)
        elif old != new:
            changes.append({"path": path or "/", "classification": "changed"})

    visit(previous, current, "")
    return _seal(
        {
            "schema": COMPATIBILITY_DIFF_SCHEMA,
            "previous_schema_fingerprint": content_fingerprint(previous),
            "current_schema_fingerprint": content_fingerprint(current),
            "changes": changes,
            "classified_change_count": len(changes),
            "cryptographic_authenticity_claimed": False,
        }
    )


def legacy_evidence_authority(
    evidence_id: str, *, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    validated = validate_compatibility_policy(policy or load_compatibility_policy())
    for row in validated.get("legacy", []):
        if row.get("id") == evidence_id:
            return {
                "id": evidence_id,
                "read_as": row["read_as"],
                "authority": list(row["authority"]),
                "release_green": False,
                "cryptographic_authenticity_claimed": False,
            }
    raise ReleaseEvidenceError("unknown legacy evidence id")


@dataclass
class _PendingPublication:
    prepared: PreparedEvidence
    publication: dict[str, Any]


@dataclass
class _PublicationState:
    publications: list[dict[str, Any]] = field(default_factory=list)
    envelope_heads: list[str] = field(default_factory=list)
    operation_ids: set[str] = field(default_factory=set)
    pending: _PendingPublication | None = None


_STATE_GUARD = threading.Lock()
_PUBLICATION_STATES: dict[tuple[Any, ...], _PublicationState] = {}
_PUBLICATION_LOCKS: dict[tuple[Any, ...], threading.RLock] = {}


def _store_key(store: EvidenceStore) -> tuple[Any, ...]:
    identity = tuple(
        getattr(store, field_name, None)
        for field_name in ("caller_root", "repository_fingerprint", "run_namespace")
    )
    if all(value is not None for value in identity):
        return ("release-evidence-store", *(str(value) for value in identity))
    return ("release-evidence-store-object", id(store))


def _state_and_lock(
    store: EvidenceStore,
) -> tuple[_PublicationState, threading.RLock]:
    key = _store_key(store)
    with _STATE_GUARD:
        return _PUBLICATION_STATES.setdefault(
            key, _PublicationState()
        ), _PUBLICATION_LOCKS.setdefault(key, threading.RLock())


def _decode_store_envelope(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        envelope = json.loads(raw)
        payload = base64.b64decode(envelope["payload"], validate=True)
        publication = json.loads(payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError("release evidence envelope is invalid") from exc
    envelope_projection = {
        key: value for key, value in envelope.items() if key != "fingerprint"
    }
    if content_fingerprint(payload) != envelope.get("payload_fingerprint") or content_fingerprint(
        envelope_projection
    ) != envelope.get("fingerprint"):
        raise ReleaseEvidenceError("release evidence envelope fingerprint mismatch")
    fields = {
        "schema",
        "operation_id",
        "source_sha",
        "predecessor_fingerprint",
        "receipt",
        "receipt_fingerprint",
        "cryptographic_authenticity_claimed",
        "fingerprint",
    }
    if not isinstance(publication, Mapping) or set(publication) != fields:
        raise ReleaseEvidenceError("release publication fields are not closed")
    if publication.get("schema") != RELEASE_PUBLICATION_SCHEMA:
        raise ReleaseEvidenceError("release publication schema is invalid")
    receipt = validate_receipt(publication.get("receipt"))
    if publication.get("receipt_fingerprint") != receipt["fingerprint"]:
        raise ReleaseEvidenceError("release publication receipt fingerprint mismatch")
    if publication.get("source_sha") != receipt["source_sha"]:
        raise ReleaseEvidenceError("release publication contains mixed source SHA")
    _no_authenticity_claim(
        publication.get("cryptographic_authenticity_claimed"),
        "release publication",
    )
    projection = {key: publication[key] for key in fields - {"fingerprint"}}
    if publication.get("fingerprint") != content_fingerprint(projection):
        raise ReleaseEvidenceError("release publication fingerprint mismatch")
    return dict(publication), envelope["fingerprint"]


def _append_publication(
    state: _PublicationState,
    publication: dict[str, Any],
    envelope_head: str,
) -> None:
    if publication["fingerprint"] in {
        row["fingerprint"] for row in state.publications
    }:
        return
    expected = (
        state.publications[-1]["receipt_fingerprint"]
        if state.publications
        else None
    )
    if publication["predecessor_fingerprint"] != expected:
        raise ReleaseEvidenceError("release publication chain has a fork or gap")
    if state.publications and publication["source_sha"] != state.publications[-1][
        "source_sha"
    ]:
        raise ReleaseEvidenceError("release publication chain contains mixed SHA")
    if publication["operation_id"] in state.operation_ids:
        raise ReleaseEvidenceError("release publication operation collision")
    state.publications.append(publication)
    state.envelope_heads.append(envelope_head)
    state.operation_ids.add(publication["operation_id"])


def _recover_publications_locked(
    store: EvidenceStore, state: _PublicationState
) -> None:
    if state.pending is not None:
        raw = store.commit(state.pending.prepared)
        publication, envelope_head = _decode_store_envelope(raw)
        if publication != state.pending.publication:
            raise ReleaseEvidenceError("committed release evidence bytes changed")
        _append_publication(state, publication, envelope_head)
        state.pending = None
        return
    if state.publications:
        return
    for raw in store.reconcile("release_evidence"):
        publication, envelope_head = _decode_store_envelope(raw)
        _append_publication(state, publication, envelope_head)


def reconcile(evidence_store: EvidenceStore) -> tuple[dict[str, Any], ...]:
    """Recover interrupted publications and return one validated linear chain."""
    state, lock = _state_and_lock(evidence_store)
    with lock:
        _recover_publications_locked(evidence_store, state)
        return tuple(dict(row["receipt"]) for row in state.publications)


def publish(
    receipt: Mapping[str, Any],
    *,
    evidence_store: EvidenceStore,
    operation_id: str,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Atomically append one receipt without granting new authority."""
    normalized = validate_receipt(receipt)
    operation = _text(operation_id, "operation_id")
    expected_predecessor = _fingerprint(
        predecessor_fingerprint, "predecessor_fingerprint", optional=True
    )
    state, lock = _state_and_lock(evidence_store)
    with lock:
        _recover_publications_locked(evidence_store, state)
        actual_predecessor = (
            state.publications[-1]["receipt_fingerprint"]
            if state.publications
            else None
        )
        if operation in state.operation_ids:
            raise ReleaseEvidenceError("release publication operation collision")
        if expected_predecessor != actual_predecessor:
            raise ReleaseEvidenceError("release publication predecessor CAS mismatch")
        if state.publications and state.publications[-1]["source_sha"] != normalized[
            "source_sha"
        ]:
            raise ReleaseEvidenceError("release publication refuses mixed source SHA")
        publication = _seal(
            {
                "schema": RELEASE_PUBLICATION_SCHEMA,
                "operation_id": operation,
                "source_sha": normalized["source_sha"],
                "predecessor_fingerprint": actual_predecessor,
                "receipt": normalized,
                "receipt_fingerprint": normalized["fingerprint"],
                "cryptographic_authenticity_claimed": False,
            }
        )
        prepared = evidence_store.prepare(
            "release_evidence",
            operation,
            publication,
            expected_head=state.envelope_heads[-1] if state.envelope_heads else None,
        )
        state.pending = _PendingPublication(prepared, publication)
        raw = evidence_store.commit(prepared)
        committed, envelope_head = _decode_store_envelope(raw)
        if committed != publication:
            raise ReleaseEvidenceError("committed release evidence bytes changed")
        _append_publication(state, committed, envelope_head)
        state.pending = None
        return dict(committed["receipt"])
