"""T01R same-slice compatibility fixtures, not native/journey authority.

T01 producers create every positive value and signature. Their stored output
crosses the migration reader and then the incumbent contract consumer unchanged.
R-0001/R-0003, P14 native canary and P23 adjudication remain separate obligations.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from pathlib import Path
from typing import Final

import pytest

from taskplane import review_evidence, stage_entities as entities, stage_handoff as handoff
from taskplane import stage_migration as migration
from taskplane.tests.test_stage_entities import _stage
from taskplane.tests.test_stage_handoff import _manifest


FRESHNESS: Final[dict[str, object]] = {
    "candidate_sha": "1" * 40,
    "source_tree": "2" * 40,
    "impact_manifest_fingerprint": "3" * 64,
}
SCHEMAS: Final[tuple[str, ...]] = (
    entities.SCHEMA,
    handoff.SCHEMA,
    entities.HANDOFF_V2_SCHEMA,
    entities.PHASE_DEFINITION_SCHEMA,
    entities.AGENT_RUNTIME_SCHEMA,
    entities.KNOWLEDGE_UPDATE_SCHEMA,
    entities.KNOWLEDGE_APPLY_SCHEMA,
)


def _key() -> handoff.SigningKey:
    # Explicit local consumer-unit trust; no host credential or gate receipt.
    return handoff.SigningKey("fixture-key", b"k" * 32, not_before=10, not_after=100)


def _runtime() -> dict[str, object]:
    return entities.create_contract(
        {
            "schema": entities.AGENT_RUNTIME_SCHEMA,
            **{
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
            },
            "run_id": "fixture-run",
            "phase_id": "build",
            "attempt_id": "attempt-1",
            "operation_id": "operation-1",
            "validator_identities": ["validator:build"],
            "consumed_artifact_schema_versions": [],
            "produced_artifact_schema_versions": [],
            "host_kind_version": "consumer-unit:v1",
            "lease_id": "lease-1",
            "fencing_token": 1,
            "deadline": "2026-09-06T12:00:00Z",
            "budget": {"tokens": 100, "wall_ms": 1000, "attempts": 1, "corrections": 0},
            "start_identity": "fixture-start",
            "progress_identity": [],
            "terminal_identity": "fixture-terminal",
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
    )


def _produce(schema: str, store: review_evidence.ArtifactStore) -> dict[str, object]:
    if schema == entities.SCHEMA:
        return _stage()
    if schema == handoff.SCHEMA:
        return entities.validate_contract(_manifest(store), store=store)
    if schema == entities.AGENT_RUNTIME_SCHEMA:
        return _runtime()
    if schema == entities.HANDOFF_V2_SCHEMA:
        original = entities.validate_contract(_manifest(store), store=store)
        selected = original["selected_artifacts"]
        assert isinstance(selected, list)
        fields = {key: value for key, value in original.items() if key != "fingerprint"}
        fields.update(
            schema=schema,
            phase_result=_runtime(),
            produced_artifacts=[
                {
                    "artifact_class": "delivery",
                    "artifact_schema_version": "v1",
                    "reference": selected[0],
                }
            ],
            inherited_artifacts=[],
            knowledge_apply_receipts=[],
            unresolved_issues=[],
        )
        return entities.create_contract(fields, store=store)
    if schema == entities.PHASE_DEFINITION_SCHEMA:
        return entities.create_contract(
            {
                "schema": schema,
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
        )
    if schema == entities.KNOWLEDGE_UPDATE_SCHEMA:
        return entities.create_contract(
            {
                "schema": schema,
                "base_knowledge_fingerprint": "a" * 64,
                "run_id": "fixture-run",
                "phase_id": "build",
                "attempt_id": "attempt-1",
                "operation_id": "operation-1",
                "candidate_fingerprint": "b" * 64,
                "finding_or_observation_reference": "artifact://finding/" + "c" * 64,
                "scope": ["taskplane"],
                "content_class": "fact",
                "content": "A consumer-unit compatibility fact.",
                "supersedes": [],
            }
        )
    assert schema == entities.KNOWLEDGE_APPLY_SCHEMA
    return entities.create_contract(
        {
            "schema": schema,
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
    )


def _consume_signed(
    signed: Mapping[str, object],
    schema: str,
    store: review_evidence.ArtifactStore,
    *,
    historical: bool = False,
    freshness: Mapping[str, object] = FRESHNESS,
    now: int = 30,
) -> migration.CompatibleContract:
    return migration.read_authenticated_contract(
        signed,
        trusted_keys={"fixture-key": _key()},
        expected_schema=schema,
        expected_freshness=freshness,
        now=now,
        historical=historical,
        store=store,
    )


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMAS)
@pytest.mark.parametrize("mode", ("retained", "authenticated", "expired-history"))
def test_dual_readers_accept_declared_versions(tmp_path: Path, schema: str, mode: str) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce(schema, store)
    original = entities.canonical_contract_bytes(value, store=store) + b"\n"
    source = tmp_path / "retained.json"
    source.write_bytes(original)
    if mode == "retained":
        result = migration.read_compatible_contract(source.read_bytes(), store=store)
        assert not result.signature_valid
        assert not result.current_authentication
        assert result.source_bytes == original
    else:
        signed = handoff.sign_contract(
            value,
            key=_key(),
            issued_at=20,
            expires_at=60,
            freshness=FRESHNESS,
            store=store,
        )
        reference = store.put("signed-contract", signed)
        stored: object = store.read(reference)
        assert isinstance(stored, dict)
        result = _consume_signed(
            stored,
            schema,
            store,
            historical=mode == "expired-history",
            now=70 if mode == "expired-history" else 30,
        )
        assert result.signature_valid
        assert result.current_authentication == (mode == "authenticated")
    assert result.payload == value
    assert not result.progression_authority
    # Migration output reaches the real consuming validator with its version intact.
    assert (
        entities.read_contract_json(
            entities.canonical_contract_bytes(result.payload, store=store),
            store=store,
        )
        == value
    )
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "case",
    (
        "unknown-v0",
        "unknown-v3",
        "unknown-family",
        "severed-output",
        "missing-signature",
        "schema-downgrade",
        "candidate_sha",
        "source_tree",
        "impact_manifest_fingerprint",
        "expired-current",
        "predecessor-conversation",
        "predecessor-runtime",
        "old-approval",
    ),
    ids=str,
)
def test_unknown_version_refused(tmp_path: Path, case: str) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce(entities.HANDOFF_V2_SCHEMA, store)
    signed = handoff.sign_contract(
        value,
        key=_key(),
        issued_at=20,
        expires_at=60,
        freshness=FRESHNESS,
        store=store,
    )
    freshness = copy.deepcopy(FRESHNESS)
    now = 30
    if case.startswith("unknown-"):
        value["schema"] = {
            "unknown-v0": "taskplane.stage-handoff/v0",
            "unknown-v3": "taskplane.stage-handoff/v3",
            "unknown-family": "taskplane.foreign/v1",
        }[case]
        with pytest.raises(entities.StageValidationError, match="unsupported"):
            migration.read_compatible_contract(review_evidence.canonical_bytes(value), store=store)
        return
    if case == "severed-output":
        signed["signature"] = "0" * 64
    elif case == "missing-signature":
        del signed["signature"]
    elif case == "schema-downgrade":
        signed["payload_schema"] = handoff.SCHEMA
    elif case in FRESHNESS:
        freshness[case] = "f" * len(str(freshness[case]))
    elif case == "expired-current":
        now = 60
    else:
        signed[case] = {"approved": True}
    with pytest.raises(handoff.HandoffValidationError):
        _consume_signed(signed, entities.HANDOFF_V2_SCHEMA, store, freshness=freshness, now=now)


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMAS)
def test_new_producer_inactive_before_cutover(tmp_path: Path, schema: str) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    value = _produce(schema, store)
    migration.read_compatible_contract(
        entities.canonical_contract_bytes(value, store=store), store=store
    )
    assert migration.active_producer_schema("taskplane.stage-handoff") == handoff.SCHEMA
    assert migration.active_producer_schema("taskplane.stage") == entities.SCHEMA
    for family in (
        "taskplane.phase-definition",
        "taskplane.agent-runtime",
        "taskplane.knowledge-update",
        "taskplane.knowledge-apply-receipt",
    ):
        assert migration.active_producer_schema(family) is None
    incumbent = _produce(handoff.SCHEMA, store)
    reference = handoff.store_manifest(store, incumbent)
    assert (
        handoff.read_manifest(
            store,
            reference,
            expected_authority_revision=7,
            expected_authority_fingerprint="a" * 64,
        )
        == incumbent
    )
    with pytest.raises(handoff.HandoffValidationError, match="unsupported"):
        handoff.store_manifest(store, _produce(entities.HANDOFF_V2_SCHEMA, store))
