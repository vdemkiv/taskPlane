"""R-0006: strict, capability-aware evaluator output contracts."""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import evaluation_output as output  # noqa: E402
import eval_drivers  # noqa: E402
import failure_routing  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
from host_capabilities import (  # noqa: E402
    Observation,
    probe_snapshot,
)


def _snapshot(tmp_path, status: str):
    return probe_snapshot(
        str(tmp_path), host="codex", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations={
            "native_structured_output": Observation(
                status, "test:host-receipt", "high", "fixture"),
        },
        session_id="session-1", now="2026-08-15T00:00:00Z")


def _value():
    failure_evidence = {
        "schema": "taskplane.failure-evidence/v1",
        "command": "pytest -q taskplane/tests/test_contract.py::test_schema",
        "returncode": 1,
        "stderr": "schema contract failed",
    }
    failure = {
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
        "id": "F-schema", "source": "pytest:test_schema",
        "stage": "evaluate", "repro": failure_evidence["command"],
        "evidence": failure_evidence,
        "evidence_digest": failure_routing.evidence_digest(failure_evidence),
        "class": "product", "reason": "schema output violates its contract",
        "owner": "product-code", "cluster": "schema-output",
        "route": "fix",
        "candidate": {"id": "candidate-schema",
                      "fingerprint": "a" * 64},
    }
    return {
        "schema": output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": "t1", "requirement": "R-0006", "verdict": "fail",
        "criteria": [{"criterion": "schema output", "status": "not-met",
                      "evidence": "test:1"}],
        "graph": {
            "dispositions": [{"node": "contract:evaluation-output",
                              "status": "contract-verified",
                              "evidence": "test:1"}],
            "requirements_checked": ["req:R-0006"],
            "contracts_checked": ["contract:evaluation-output"],
        },
        "failures": [failure],
    }


def _contract(tmp_path, status="supported"):
    return output.create_output_contract(
        workspace=str(tmp_path), task="t1", stage="evaluate",
        result_path=".eval/verdict.json", write_allow=[".eval/verdict.json"],
        output_schema=output.evaluator_output_schema(),
        capability_snapshot=_snapshot(tmp_path, status),
        slot="t1", lease="lease-1", producer="tp-evaluator",
        canonical_revision=3)


@pytest.mark.parametrize("status,transport", [
    ("supported", "native_schema"),
    ("unsupported", "validated_file"),
    ("unknown", "validated_file"),
    ("contradictory", "validated_file"),
])
def test_capability_selects_native_or_governed_file_deterministically(
        tmp_path, status, transport):
    contract = _contract(tmp_path, status)
    assert contract["schema_transport"] == transport
    assert contract["capability"]["status"] == status
    assert contract["output_schema_id"] == output.EVALUATOR_OUTPUT_SCHEMA_ID
    assert contract["max_attempts"] == 2


def test_corrupt_capability_selects_explicit_file_fallback(tmp_path):
    selection = output.select_schema_transport({"capabilities": {
        "native_structured_output": {"status": "not-a-status"}}})
    assert selection == {
        "transport": "validated_file",
        "status": "contradictory",
        "source": "corrupt-capability",
        "reason": "native structured-output capability is corrupt",
    }


def test_contract_rejects_escape_and_path_not_already_allowed(tmp_path):
    args = dict(
        workspace=str(tmp_path), task="t1", stage="evaluate",
        output_schema=output.evaluator_output_schema(),
        capability_snapshot=_snapshot(tmp_path, "unsupported"))
    with pytest.raises(output.OutputContractError, match="escapes"):
        output.create_output_contract(
            **args, result_path="../verdict.json",
            write_allow=["../verdict.json"])
    with pytest.raises(output.OutputContractError, match="write allowance"):
        output.create_output_contract(
            **args, result_path=".eval/verdict.json", write_allow=[])


@pytest.mark.parametrize("mutation,code", [
    (lambda row: row.pop("criteria"), "missing_field"),
    (lambda row: row.update(schema="taskplane.evaluator-output/v0"),
     "const_mismatch"),
    (lambda row: row.update(extra="not allowed"), "extra_field"),
    (lambda row: row.update(lenses=[]), "extra_field"),
])
def test_schema_invalid_output_never_passes(tmp_path, mutation, code):
    row = _value()
    mutation(row)
    with pytest.raises(output.OutputValidationError) as caught:
        output.validate_output_bytes(
            output.canonical_bytes(row), _contract(tmp_path))
    assert caught.value.code == code


def test_malformed_duplicate_and_oversized_json_fail_closed(tmp_path):
    contract = _contract(tmp_path)
    with pytest.raises(output.OutputValidationError) as malformed:
        output.validate_output_bytes(b"{", contract)
    assert malformed.value.code == "malformed_json"
    duplicate = output.canonical_bytes(_value()).replace(
        b'{"criteria":', b'{"task":"copied","criteria":', 1)
    with pytest.raises(output.OutputValidationError) as dup:
        output.validate_output_bytes(duplicate, contract)
    assert dup.value.code == "duplicate_field"
    too_large = dict(contract, max_bytes=8)
    with pytest.raises(output.OutputValidationError) as large:
        output.validate_output_bytes(output.canonical_bytes(_value()), too_large)
    assert large.value.code == "output_too_large"


def test_native_and_file_transport_admit_byte_identical_canonical_output(
        tmp_path):
    row = _value()
    raw = output.canonical_bytes(row)
    native = _contract(tmp_path, "supported")
    fallback = _contract(tmp_path, "unsupported")
    result_path = tmp_path / ".eval" / "verdict.json"
    result_path.parent.mkdir()
    result_path.write_bytes(raw)
    receipt = {
        "schema": output.WRITE_OBSERVATION_SCHEMA_ID,
        "host_observed": True,
        "result_path": ".eval/verdict.json",
        "result_sha256": hashlib.sha256(raw).hexdigest(),
        "result_bytes": len(raw),
        "task": "t1", "stage": "evaluate", "slot": "t1",
        "lease": "lease-1", "producer": "tp-evaluator",
        "producer_host": "codex", "producer_session": "session-1",
        "producer_child_id": "child-1",
    }
    native_result = output.validate_output_bytes(raw, native)
    file_result = output.validate_output_file(
        str(tmp_path), fallback, observed_write=receipt)
    assert native_result["canonical_bytes"] == file_result["canonical_bytes"]
    assert native_result["sha256"] == file_result["sha256"]
    assert native_result["value"] == file_result["value"] == row


def test_evaluator_schema_has_no_lens_route_or_slot_surface():
    schema = output.evaluator_output_schema()
    properties = schema["properties"]
    assert "lenses" not in properties
    assert "lens_routes" not in properties
    assert "slots" not in properties
    assert "dispositions" not in properties


@pytest.mark.parametrize("field", [
    "task", "stage", "slot", "lease", "producer", "canonical_revision",
    "output_schema_id", "output_schema_sha256", "result_path",
])
def test_resume_identity_binds_every_semantic_field(tmp_path, field):
    contract = _contract(tmp_path)
    changed = dict(contract)
    changed[field] = "different" if field != "canonical_revision" else 4
    assert output.resume_identity(changed) != output.resume_identity(contract)


def test_retry_is_bounded_to_two_total_attempts():
    assert output.retry_disposition(attempt=1, max_attempts=2) == "retry"
    assert output.retry_disposition(attempt=2, max_attempts=2) == \
        "retry_exhausted"
    assert output.retry_disposition(attempt=99, max_attempts=2) == \
        "retry_exhausted"


def test_native_driver_validates_before_admitting_output(tmp_path):
    raw = output.canonical_bytes(_value())

    def runner(**_kwargs):
        return eval_drivers.ProcessOutcome(
            status="success", returncode=0, stdout=raw, stderr=b"")

    result = eval_drivers.CodexAdapter(
        executable="codex", runner=runner).run(
            {"task": "t1"}, cwd=str(tmp_path), timeout_s=1,
            output_contract=_contract(tmp_path, "supported"))
    assert result["status"] == "success"
    assert result["output_validation"]["status"] == "valid"
    assert result["validated_output"] == _value()


def test_pass_lens_requires_nonempty_source_anchored_checked_evidence():
    schema = output.lens_slot_output_schema()
    lens_schema = schema["properties"]["lens_results"]["items"]
    conditional = lens_schema["allOf"][0]
    assert conditional["if"]["properties"]["verdict"] == {"const": "pass"}
    assert "checked_evidence" in conditional["then"]["required"]
    assert conditional["then"]["properties"]["checked_evidence"][
        "minItems"] == 1

    for checked in (None, []):
        verdict = {"lens": "architecture", "verdict": "pass",
                   "blockers": 0}
        if checked is not None:
            verdict["checked_evidence"] = checked
        with pytest.raises(review_evidence.ProvenanceError,
                           match="pass verdict requires"):
            review._validated_checked_evidence(
                verdict, lens_id="architecture", slot_id="deep.architecture",
                canonical_revision=1)
