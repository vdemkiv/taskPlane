"""T01 same-slice contract fixtures; no native-host or journey claim.

Every positive crosses a real value producer, signer and consuming validator.
Historical inputs are retained byte-for-byte. Negative parameters sever one
produced value or signature at the declared boundary, never replace success.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import re
import subprocess
import sys

import pytest

from taskplane import review_evidence, stage_entities as entities, stage_handoff as handoff
from taskplane.tests.test_stage_entities import _stage
from taskplane.tests.test_stage_handoff import _manifest


FRESHNESS = {
    "candidate_sha": "1" * 40,
    "source_tree": "2" * 40,
    "impact_manifest_fingerprint": "3" * 64,
}


def test_contract_apis_strict_typing(tmp_path: Path) -> None:
    """Check actual implementation bodies without the legacy module exclusions.

    Source locations only bound this T01 check; mypy judges the types. Older
    lifecycle diagnostics remain visible outside the two new contract sections.
    """
    config = tmp_path / "mypy.ini"
    config.write_text("[mypy]\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    sections = (
        (entities._strict_json, entities.canonical_contract_bytes),
        (handoff.validate_v2_manifest, handoff.verify_contract),
    )
    spans = {}
    for first, last in sections:
        filename = inspect.getsourcefile(first)
        assert filename is not None
        _, start = inspect.getsourcelines(first)
        last_lines, last_start = inspect.getsourcelines(last)
        spans[Path(filename).resolve()] = (start, last_start + len(last_lines))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--config-file",
            str(config),
            "--follow-imports=silent",
            "--explicit-package-bases",
            "--ignore-missing-imports",
            "--no-incremental",
            "--no-error-summary",
            "--show-error-codes",
            *(str(path) for path in spans),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    errors = re.findall(r"^(.+?):(\d+): error: (.+)$", result.stdout, re.MULTILINE)
    assert result.returncode == 0 or errors, result.stdout + result.stderr
    scoped_errors = []
    for filename, line, message in errors:
        path = (root / filename).resolve()
        assert path in spans, f"unexpected diagnostic target: {filename}: {message}"
        start, end = spans[path]
        if start <= int(line) < end:
            scoped_errors.append(f"{filename}:{line}: {message}")
    assert not scoped_errors, "\n".join(scoped_errors)


def _definition():
    return {
        "schema": "taskplane.phase-definition/v1",
        "id": "build",
        "role": "tp-executor",
        "skill_ref": "skill:build",
        "skill_content_fingerprint": "a" * 64,
        "consumes": [],
        "produces": [],
        "domain_validator_refs": ["validator:build"],
        "validator_inventory_fingerprint": "b" * 64,
        "capability_requirements": [],
        "working_lenses": [],
        "evaluation_lenses": [],
        "budget": {"tokens": 100, "wall_ms": 1000, "attempts": 1, "corrections": 0},
        "model_tier": "verified",
        "gate": "mechanical",
        "predecessors": [],
        "successors": [],
        "edge_conditions": [],
        "entry": True,
        "terminal": True,
        "telemetry_scope": ["build"],
    }


def _runtime():
    bindings = {
        key: "a" * 64
        for key in (
            "candidate_fingerprint",
            "definition_set_fingerprint",
            "phase_definition_fingerprint",
            "skill_content_fingerprint",
            "validator_inventory_fingerprint",
            "capability_set_fingerprint",
            "sealed_package_fingerprint",
            "knowledge_fingerprint",
            "authority_fingerprint",
            "nonce_digest",
        )
    }
    return {
        "schema": "taskplane.agent-runtime/v1",
        **bindings,
        "run_id": "run-1",
        "phase_id": "build",
        "attempt_id": "attempt-1",
        "operation_id": "operation-1",
        "validator_identities": ["validator:build"],
        "consumed_artifact_schema_versions": [],
        "produced_artifact_schema_versions": [],
        "host_kind_version": "codex:test",
        "lease_id": "lease-1",
        "fencing_token": 1,
        "deadline": "2026-09-06T12:00:00Z",
        "budget": _definition()["budget"],
        "start_identity": "start-1",
        "progress_identity": [],
        "terminal_identity": "terminal-1",
        "effect_state": "effect_free",
        "collected_output_references": [],
        "produces_conformance": True,
        "knowledge_proposals": [],
        "evaluator_dispatch_eligibility": True,
        "retry_class": "none",
        "status": "accepted",
        "reason_code": None,
        "continuation": {"kind": "evaluate", "phase_id": "build"},
    }


def _proposal():
    return {
        "schema": "taskplane.knowledge-update/v1",
        "base_knowledge_fingerprint": "a" * 64,
        "run_id": "run-1",
        "phase_id": "build",
        "attempt_id": "attempt-1",
        "operation_id": "operation-1",
        "candidate_fingerprint": "b" * 64,
        "finding_or_observation_reference": "artifact://finding/" + "c" * 64,
        "scope": ["taskplane"],
        "content_class": "fact",
        "content": "A bounded fact.",
        "supersedes": [],
    }


def _apply_receipt():
    return {
        "schema": "taskplane.knowledge-apply-receipt/v1",
        **{
            key: "a" * 64
            for key in (
                "base_fingerprint",
                "current_fingerprint",
                "new_fingerprint",
                "proposal_fingerprint",
                "gate_receipt_fingerprint",
            )
        },
        "operation_id": "operation-1",
        "writer_id": "knowledge-owner",
        "fencing_token": 1,
        "retention_class": "minimized",
        "applied_at": "2026-09-06T10:00:00Z",
        "outcome": "applied",
        "continuation": {"kind": "continue", "phase_id": "build"},
    }


def _produce(kind, store):
    if kind == "stage":
        return _stage()
    if kind == "handoff-v1":
        return _manifest(store)
    if kind == "handoff-v2":
        original = _manifest(store)
        result = {key: value for key, value in original.items() if key != "fingerprint"}
        result.update(
            schema="taskplane.stage-handoff/v2",
            phase_result=entities.create_contract(_runtime()),
            produced_artifacts=[
                {
                    "artifact_class": "delivery",
                    "artifact_schema_version": "v1",
                    "reference": original["selected_artifacts"][0],
                }
            ],
            inherited_artifacts=[],
            knowledge_apply_receipts=[],
            unresolved_issues=[],
        )
        return entities.create_contract(result, store=store)
    return entities.create_contract(
        {
            "definition": _definition,
            "runtime": _runtime,
            "proposal": _proposal,
            "apply": _apply_receipt,
        }[kind]()
    )


def _key():
    # In-memory consumer-unit key, never a host credential or approval receipt.
    return handoff.SigningKey("key-1", b"k" * 32, not_before=10, not_after=100)


def _signed(value, store, key=None):
    return handoff.sign_contract(
        value, key=key or _key(), issued_at=20, expires_at=60, freshness=FRESHNESS, store=store
    )


def _consume(value, signed, store, **kwargs):
    return handoff.verify_contract(
        signed,
        trusted_keys={"key-1": _key()},
        expected_schema=value["schema"],
        expected_freshness=FRESHNESS,
        now=30,
        store=store,
        **kwargs,
    )


@pytest.mark.parametrize(
    "kind", ["stage", "handoff-v1", "handoff-v2", "definition", "runtime", "proposal", "apply"]
)
def test_contract_matrix_mixed_versions(tmp_path, kind):
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce(kind, store)
    original = review_evidence.canonical_bytes(value)
    signed = _signed(value, store)
    reference = store.put("signed-contract", signed)
    result = _consume(value, store.read(reference), store)
    assert result["payload"] == value
    assert result["signature_valid"] is True
    assert result["authority_valid"] is True
    assert review_evidence.canonical_bytes(value) == original
    assert (
        entities.canonical_contract_bytes(dict(reversed(list(value.items()))), store=store)
        == original
    )
    if kind == "handoff-v1":
        ref = handoff.store_manifest(store, value)
        assert (
            handoff.read_manifest(
                store, ref, expected_authority_revision=7, expected_authority_fingerprint="a" * 64
            )
            == value
        )
    if kind == "handoff-v2":
        # Reader/writer cutover belongs to T01R; the incumbent cannot reinterpret v2.
        with pytest.raises(handoff.HandoffValidationError, match="unsupported"):
            handoff.store_manifest(store, value)


@pytest.mark.parametrize(
    "case",
    [
        "active",
        "rotation",
        "revoked",
        "compromised",
        "expired",
        "unknown_key",
        "wrong_key",
        "not_yet_valid",
        "invalid_algorithm",
        "severed_entity_output",
        "candidate_sha",
        "source_tree",
        "impact_manifest_fingerprint",
        "canonical_unicode",
    ],
)
def test_signature_lifecycle(tmp_path, case):
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce("definition", store)
    signed = _signed(value, store)
    original = review_evidence.canonical_bytes(signed)
    keys, now = {"key-1": _key()}, 30
    expected = dict(FRESHNESS)
    reason = None
    if case == "rotation":
        keys = handoff.rotate_signing_key(
            keys,
            "key-1",
            handoff.SigningKey("key-2", b"n" * 32, not_before=25, not_after=120),
            at=25,
        )
        newer = handoff.sign_contract(
            value, key=keys["key-2"], issued_at=30, expires_at=60, freshness=FRESHNESS, store=store
        )
        assert handoff.verify_contract(
            newer,
            trusted_keys=keys,
            expected_schema=value["schema"],
            expected_freshness=FRESHNESS,
            now=30,
            store=store,
        )["authority_valid"]
    elif case in {"revoked", "compromised"}:
        keys = handoff.disable_signing_key(keys, "key-1", at=25, compromised=case == "compromised")
        reason = case
    elif case == "expired":
        now, reason = 60, "expired"
    elif case == "unknown_key":
        keys, reason = {}, "untrusted"
    elif case == "wrong_key":
        keys, reason = (
            {"key-1": handoff.SigningKey("key-1", b"x" * 32, not_before=10, not_after=100)},
            "signature",
        )
    elif case == "not_yet_valid":
        now, reason = 19, "not yet valid"
    elif case == "invalid_algorithm":
        signed["algorithm"], reason = "none", "algorithm"
    elif case == "severed_entity_output":
        signed["payload"]["role"], reason = "foreign-role", "fingerprint"
    elif case in FRESHNESS:
        expected[case], reason = "f" * len(expected[case]), "freshness"
    elif case == "canonical_unicode":
        value["skill_ref"] = "skill:é"
        value = entities.create_contract({k: v for k, v in value.items() if k != "fingerprint"})
        signed = _signed(value, store)
    args = dict(
        trusted_keys=keys,
        expected_schema=value["schema"],
        expected_freshness=expected,
        now=now,
        store=store,
    )
    if reason:
        with pytest.raises(
            (entities.StageValidationError, handoff.HandoffValidationError), match=reason
        ):
            handoff.verify_contract(signed, **args)
    else:
        assert handoff.verify_contract(signed, **args)["authority_valid"]
    if case in {"revoked", "compromised", "expired"}:
        historical = handoff.verify_contract(signed, historical=True, **args)
        assert historical["signature_valid"] and not historical["authority_valid"]
        assert historical["key_status"] == (case if case != "expired" else "active")
        assert review_evidence.canonical_bytes(signed) == original
    if case in {"rotation", "revoked", "compromised"}:
        with pytest.raises(handoff.HandoffValidationError, match="issuance"):
            handoff.sign_contract(
                value,
                key=keys["key-1"],
                issued_at=30,
                expires_at=60,
                freshness=FRESHNESS,
                store=store,
            )


@pytest.mark.parametrize(
    "case",
    [
        "unknown_authority",
        "nested_authority",
        "second_graph",
        "second_seam",
        "second_evidence",
        "unknown_major",
        "unsigned",
        "algorithm_downgrade",
        "schema_downgrade",
        "removed_signature",
        "severed_handoff_output",
        "duplicate_json",
        "nonfinite_json",
        "nonstring_key",
    ],
)
def test_unknown_authority_field_and_signature_downgrade_refused(tmp_path, case):
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce("handoff-v1", store)
    signed = _signed(value, store)
    if case in {"unknown_authority", "second_graph", "second_seam", "second_evidence"}:
        field = {
            "unknown_authority": "approval",
            "second_graph": "graph_authority",
            "second_seam": "seam_authority",
            "second_evidence": "evidence_authority",
        }[case]
        value[field] = {"approved": True}
    elif case == "nested_authority":
        value["authorization"]["approve"] = True
        # Recompute integrity so the nested allowlist, not a stale hash, must refuse.
        value["fingerprint"] = handoff.manifest_fingerprint(value)
    elif case == "unknown_major":
        value["schema"] = "taskplane.stage-handoff/v99"
    elif case == "nonfinite_json":
        value["authorization"]["authority_record"]["revision"] = float("nan")
    elif case == "nonstring_key":
        value[1] = "invalid"
    elif case == "duplicate_json":
        data = '{"schema":"taskplane.stage-handoff/v1","schema":"taskplane.stage-handoff/v2"}'
        with pytest.raises(entities.StageValidationError, match="duplicate"):
            entities.read_contract_json(data, store=store)
        return
    else:
        if case == "unsigned":
            signed = value
        elif case == "algorithm_downgrade":
            signed["algorithm"] = "sha256"
        elif case == "schema_downgrade":
            signed["payload_schema"] = "taskplane.stage-handoff/v0"
        elif case == "removed_signature":
            del signed["signature"]
        elif case == "severed_handoff_output":
            signed["signature"] = "0" * 64
        with pytest.raises(handoff.HandoffValidationError):
            _consume(value, signed, store)
        return
    with pytest.raises((entities.StageValidationError, handoff.HandoffValidationError)):
        _signed(value, store)
