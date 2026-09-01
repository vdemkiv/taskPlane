"""Strict, host-neutral output contracts for governed model work.

Hosts may transport structured output differently.  This module owns the
schema and validation boundary so native schema support and a governed file
fallback admit the same canonical value and fail closed in the same way.
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy

import storage as runtime_storage

if __package__:
    from . import failure_routing
else:  # pragma: no cover - direct CLI module loading
    import failure_routing


EVALUATOR_OUTPUT_SCHEMA_ID = "taskplane.evaluator-output/v2"
EVALUATOR_READ_SCHEMA_ID = "taskplane.evaluator-read/v1"
LENS_SLOT_OUTPUT_SCHEMA_ID = "taskplane.lens-slot-output/v2"
WRITE_OBSERVATION_SCHEMA_ID = "taskplane.output-write-observation/v1"
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ATTEMPTS = 2


def validate_submission_observation(
        submission: dict, *, output_bytes: bytes, output_schema_id: str,
        output_contract_fingerprint: str) -> dict:
    """Validate the producer receipt consumed by evaluator and EM gates."""
    if __package__:
        from . import producer_observation
    else:  # pragma: no cover - direct CLI module loading
        import producer_observation

    if not isinstance(submission, dict):
        raise producer_observation.ProducerObservationError(
            "submission must be a mapping")
    receipt = submission.get("producer_observation")
    if not isinstance(receipt, dict):
        raise producer_observation.ProducerObservationError(
            "producer observation is required")
    receipt = producer_observation.validate_producer_observation(receipt)
    if not isinstance(output_bytes, bytes):
        raise producer_observation.ProducerObservationError(
            "output_bytes must be exact bytes")
    expected_stage = str(submission.get("step") or "").lower()
    expected_task = str(submission.get("task") or "")
    if expected_stage not in {"evaluate", "em"} or \
            receipt["stage"] != expected_stage:
        raise producer_observation.ProducerObservationError(
            "producer observation stage does not match submission")
    if expected_task and receipt["task_id"] != expected_task:
        raise producer_observation.ProducerObservationError(
            "producer observation task does not match submission")
    if receipt["output_bytes"] != len(output_bytes) or \
            receipt["output_sha256"] != hashlib.sha256(output_bytes).hexdigest():
        raise producer_observation.ProducerObservationError(
            "producer observation does not match exact output bytes")
    if receipt["output_schema_id"] != output_schema_id:
        raise producer_observation.ProducerObservationError(
            "producer observation output schema does not match submission")
    if receipt["output_contract_fingerprint"] != \
            output_contract_fingerprint:
        raise producer_observation.ProducerObservationError(
            "producer observation contract does not match submission")
    return receipt


class OutputContractError(ValueError):
    """The dispatch contract itself is unsafe or contradictory."""


class OutputValidationError(ValueError):
    """Model output cannot enter a governed record or gate."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def canonical_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode(
                           "utf-8")


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "required": required,
            "additionalProperties": False, "properties": properties}


def evaluator_output_schema() -> dict:
    string = {"type": "string"}
    evaluation = _object({
        "status": {"enum": ["complete", "unavailable"]},
        "reason_code": {"enum": [
            "none", "host_unavailable", "agent_timeout",
            "transport_unavailable", "producer_receipt_unavailable",
            "orchestration_unavailable",
        ]},
        "detail": string,
    }, ["status", "reason_code", "detail"])
    criterion = _object({
        "criterion": string,
        "status": {"enum": ["met", "not-met", "cannot-verify"]},
        "evidence": string,
    }, ["criterion", "status", "evidence"])
    disposition = _object({
        "node": string, "status": string, "evidence": string,
    }, ["node", "status", "evidence"])
    graph = _object({
        "dispositions": {"type": "array", "items": disposition},
        "requirements_checked": {"type": "array", "items": string},
        "contracts_checked": {"type": "array", "items": string},
    }, ["dispositions", "requirements_checked", "contracts_checked"])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": EVALUATOR_OUTPUT_SCHEMA_ID,
        **_object({
            "schema": {"const": EVALUATOR_OUTPUT_SCHEMA_ID},
            "task": string, "requirement": {"type": "string"},
            "verdict": {"enum": ["pass", "fail"]},
            # Optional for byte compatibility with completed v1 records. It
            # is mandatory at the loop boundary for ``unavailable``.
            "evaluation": evaluation,
            "criteria": {"type": "array", "items": criterion},
            "graph": graph,
            "failures": {
                "type": "array",
                "items": failure_routing.failure_record_schema(),
            },
        }, ["schema", "task", "requirement", "verdict", "criteria",
            "graph", "failures"]),
    }


def validate_evaluator_value(
        value: dict, *, expected_lenses: list[str] | None = None) -> dict:
    """Validate evaluator output for admission to a governed decision.

    Historical failure rows are intentionally rejected here.  They remain
    readable through :func:`read_evaluator_value`, but cannot authorize a
    correction route.
    """
    if expected_lenses is not None:
        if not isinstance(expected_lenses, list):
            raise OutputValidationError(
                "expected_lenses_type", "expected_lenses must be a list")
        if expected_lenses:
            raise OutputValidationError(
                "nonempty_expected_lenses",
                "zero-lens evaluation requires expected_lenses=[]",
            )
        availability = value.get("evaluation") if isinstance(value, dict) \
            else None
        if not isinstance(availability, dict) or \
                availability.get("status") != "complete" or \
                availability.get("reason_code") != "none":
            raise OutputValidationError(
                "zero_lens_completion_required",
                "zero-lens evaluation requires an exact evaluation "
                "completion block with status=complete and "
                "reason_code=none; omission or outage fallback is forbidden",
            )
    _validate(value, evaluator_output_schema())
    failures = value["failures"]
    if value["verdict"] == "pass":
        if failures:
            raise OutputValidationError(
                "pass_has_failures",
                "a passing evaluator result must not carry failures",
            )
        return value
    try:
        failure_routing.validate_failure_records(failures)
    except failure_routing.FailureRoutingError as exc:
        raise OutputValidationError(
            "failure_admission", str(exc)) from None
    return value


def _validate_legacy_failure(value: dict) -> dict:
    fields = {"what", "repro", "where"}
    if not isinstance(value, dict) or set(value) != fields:
        raise OutputValidationError(
            "legacy_failure_shape",
            "legacy failure must contain exactly what, repro, and where",
        )
    for field in sorted(fields):
        text = value.get(field)
        if not isinstance(text, str) or not text.strip():
            raise OutputValidationError(
                "legacy_failure_shape",
                f"legacy failure {field} must be a non-empty string",
            )
    return deepcopy(value)


def read_evaluator_value(value: dict) -> dict:
    """Read current or v1-compatible output without granting correction.

    This is a display/audit compatibility boundary.  Callers seeking fix or
    recovery authority must use :func:`validate_evaluator_value` and then the
    failure router.
    """
    if not isinstance(value, dict):
        raise OutputValidationError(
            "type_mismatch", "evaluator output must be a mapping")
    failures = value.get("failures")
    if not isinstance(failures, list):
        raise OutputValidationError(
            "type_mismatch", "evaluator failures must be a list")

    # Validate the common envelope using the live contract while validating
    # failure rows below against their explicit current-or-legacy boundary.
    envelope = deepcopy(value)
    envelope["failures"] = []
    _validate(envelope, evaluator_output_schema())

    current: list[dict] = []
    legacy: list[dict] = []
    for failure in failures:
        try:
            current.append(failure_routing.validate_failure_record(failure))
        except failure_routing.FailureRoutingError:
            legacy.append(_validate_legacy_failure(failure))

    routing = None
    correction_authority = False
    if value.get("verdict") == "fail" and current and not legacy:
        try:
            routing = failure_routing.route_failure_records(current)
        except failure_routing.FailureRoutingError as exc:
            raise OutputValidationError(
                "failure_admission", str(exc)) from None
        correction_authority = bool(routing["admitted"])
    return {
        "schema": EVALUATOR_READ_SCHEMA_ID,
        "value": deepcopy(value),
        "failure_records": current,
        "legacy_failures": legacy,
        "routing": routing,
        "correction_authority": correction_authority,
    }


def lens_slot_output_schema(references: list[dict] | None = None) -> dict:
    string = {"type": "string"}
    checked_evidence = _object({
        "file": string,
        "line": {"type": "integer", "minimum": 1},
        "claim": string,
    }, ["file", "line", "claim"])
    lens_result = _object({
        "lens": string, "verdict": {"enum": ["pass", "fail"]},
        "blockers": {"type": "integer", "minimum": 0},
        "checked_evidence": {"type": "array", "items": checked_evidence},
    }, ["lens", "verdict", "blockers"])
    lens_result["allOf"] = [{
        "if": {"properties": {"verdict": {"const": "pass"}}},
        "then": {
            "required": ["checked_evidence"],
            "properties": {"checked_evidence": {
                "type": "array", "minItems": 1,
                "items": checked_evidence,
            }},
        },
    }]
    finding = _object({
        "lens": string, "kind": {"enum": ["defect", "violation", "note"]},
        "severity": string, "class": string, "file": string,
        "line": {"type": "integer", "minimum": 1}, "title": string,
        "scenario": string, "fix": string, "claim": {"type": "object"},
        "declares": string, "recurrence": string,
    }, ["lens", "kind", "severity", "class", "file", "line", "title",
        "scenario", "fix"])
    required = [
        "schema", "lease_fingerprint", "slot_id", "lens_ids",
        "target_fingerprint", "context_fingerprint", "view_fingerprint",
        "canonical_revision", "authored_by", "lens_results", "findings",
    ]
    properties = {
        "schema": {"const": LENS_SLOT_OUTPUT_SCHEMA_ID},
        "lease_fingerprint": string, "slot_id": string,
        "lens_ids": {"type": "array", "items": string},
        "target_fingerprint": string, "context_fingerprint": string,
        "view_fingerprint": string,
        "canonical_revision": {"type": "integer", "minimum": 1},
        "authored_by": {"const": "lens-slot"},
        "lens_results": {"type": "array", "items": lens_result},
        "findings": {"type": "array", "items": finding},
        "references_applied": {"type": "array", "items": {"type": "object"}},
    }
    if references:
        required.append("references_applied")
        properties["references_applied"] = {"const": deepcopy(references)}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": LENS_SLOT_OUTPUT_SCHEMA_ID,
        **_object(properties, required),
    }
    # These descriptive compatibility fields are consumed by existing brief
    # renderers.  Validation authority remains the JSON Schema above.
    schema.update({
        "schema": LENS_SLOT_OUTPUT_SCHEMA_ID,
        "authored_by": "lens-slot",
        "lens_result": {
            "type": "object",
            "required": ["lens", "verdict", "blockers"],
            "verdict": ["pass", "fail"],
            "blockers": {"type": "integer", "minimum": 0},
            "checked_evidence": {
                "required_for": "pass",
                "items": {"required": ["file", "line", "claim"]},
                "meaning": "compact source-anchored evidence actually checked",
            },
        },
        "findings": {"type": "array", "items": "finding"},
        "finding": {
            "type": "object",
            "required": ["lens", "kind", "severity", "class", "file",
                         "line", "title", "scenario", "fix"],
        },
        "finding_kinds": {
            "defect": "requires claim.trigger/outcome/repro",
            "violation": "requires resolvable declares identity",
            "note": "recorded outside the findings surface",
        },
        "codex_completion_receipt": {
            "advisory_lines": ["taskplane-result-path:<result_path>",
                               "taskplane-result-sha256:<sha256>"],
            "authority": "the sealed, validated lease artifact",
        },
    })
    if references:
        schema["references_applied"] = {
            "type": "array", "exact": deepcopy(references)}
    return schema


def select_schema_transport(snapshot) -> dict:
    capabilities = (snapshot.get("capabilities") if isinstance(snapshot, dict)
                    else getattr(snapshot, "capabilities", None))
    row = capabilities.get("native_structured_output") \
        if hasattr(capabilities, "get") else None
    status = (row.get("status") if isinstance(row, dict)
              else getattr(row, "status", None))
    source = (row.get("source") if isinstance(row, dict)
              else getattr(row, "source", None))
    if status not in {"supported", "unsupported", "unknown", "contradictory"}:
        return {"transport": "validated_file", "status": "contradictory",
                "source": "corrupt-capability",
                "reason": "native structured-output capability is corrupt"}
    if status == "supported":
        return {"transport": "native_schema", "status": status,
                "source": source or "unknown",
                "reason": "host reports native structured output"}
    return {"transport": "validated_file", "status": status,
            "source": source or "unknown",
            "reason": f"native structured output is {status}"}


def _safe_result_path(workspace: str, path: str) -> str:
    if os.path.isabs(str(path or "")):
        resolved = os.path.realpath(str(path))
        if not runtime_storage.managed_path_allowed(workspace, resolved):
            raise OutputContractError(
                "result path escapes the governed run store")
        return resolved
    raw = str(path or "").replace("\\", "/")
    normalized = os.path.normpath(raw).replace("\\", "/")
    if not raw or os.path.isabs(raw) or normalized == ".." or \
            normalized.startswith("../"):
        raise OutputContractError("result path escapes the governed workspace")
    root = os.path.realpath(workspace)
    resolved = os.path.realpath(os.path.join(root, normalized))
    if os.path.commonpath((root, resolved)) != root:
        raise OutputContractError("result path escapes the governed workspace")
    return normalized


def create_output_contract(*, workspace: str, task: str, stage: str,
                           result_path: str, write_allow: list[str],
                           output_schema: dict, capability_snapshot,
                           slot: str | None = None, lease: str | None = None,
                           producer: str | None = None,
                           canonical_revision: int | None = None,
                           max_bytes: int = MAX_OUTPUT_BYTES,
                           max_attempts: int = MAX_ATTEMPTS) -> dict:
    path = _safe_result_path(workspace, result_path)
    allowed = [_safe_result_path(workspace, item) for item in write_allow]
    if path not in allowed:
        raise OutputContractError(
            "result path is not present in the existing write allowance")
    if not isinstance(output_schema, dict) or not output_schema.get("$id"):
        raise OutputContractError("output schema must have a versioned $id")
    selection = select_schema_transport(
        capability_snapshot.to_dict() if hasattr(capability_snapshot, "to_dict")
        else capability_snapshot)
    schema_bytes = canonical_bytes(output_schema)
    return {
        "schema": "taskplane.evaluation-output-contract/v1",
        "task": str(task), "stage": str(stage), "slot": slot,
        "lease": lease, "producer": producer,
        "canonical_revision": canonical_revision,
        "result_path": path, "write_allow": allowed,
        "output_schema": deepcopy(output_schema),
        "output_schema_id": output_schema["$id"],
        "output_schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "schema_transport": selection["transport"],
        "transport": selection["transport"],
        "capability": {key: selection[key] for key in
                       ("status", "source", "reason")},
        "fallback_reason": (selection["reason"]
                            if selection["transport"] == "validated_file"
                            else None),
        "write_observation_required": True,
        "max_bytes": int(max_bytes), "max_attempts": int(max_attempts),
    }


def create_evaluator_contract(*, workspace: str, task: str,
                              capability_snapshot: object,
                              slot: str | None = None) -> dict:
    """Build the one live evaluator contract used by every host rail."""
    result_path = runtime_storage.evaluator_contract_path(workspace)
    return create_output_contract(
        workspace=workspace, task=task, stage="evaluate",
        slot=slot or task, producer="tp-evaluator",
        result_path=result_path, write_allow=[result_path],
        output_schema=evaluator_output_schema(),
        capability_snapshot=capability_snapshot)


def resume_identity(contract: dict) -> str:
    fields = {key: contract.get(key) for key in (
        "task", "stage", "slot", "lease", "producer",
        "canonical_revision", "output_schema_id", "output_schema_sha256",
        "result_path")}
    return hashlib.sha256(canonical_bytes(fields)).hexdigest()


def retry_disposition(*, attempt: int, max_attempts: int) -> str:
    return "retry" if int(attempt) < int(max_attempts) else "retry_exhausted"


def _type_ok(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _validate(value, schema: dict, path: str = "$") -> None:
    if "const" in schema and value != schema["const"]:
        raise OutputValidationError("const_mismatch", f"{path} has wrong value")
    if "enum" in schema and value not in schema["enum"]:
        raise OutputValidationError("enum_mismatch", f"{path} is not allowed")
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        raise OutputValidationError("type_mismatch", f"{path} has wrong type")
    if expected == "object":
        properties = schema.get("properties") or {}
        for field in schema.get("required") or []:
            if field not in value:
                raise OutputValidationError(
                    "missing_field", f"{path} is missing {field}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise OutputValidationError(
                    "extra_field", f"{path} contains {sorted(extra)[0]}")
        for field, child in properties.items():
            if field in value:
                _validate(value[field], child, f"{path}.{field}")
    elif expected == "array":
        child = schema.get("items")
        if isinstance(child, dict):
            for index, item in enumerate(value):
                _validate(item, child, f"{path}[{index}]")
    if "minimum" in schema and value < schema["minimum"]:
        raise OutputValidationError("minimum", f"{path} is below minimum")


def _decode(raw: bytes):
    duplicate = None

    def pairs(rows):
        nonlocal duplicate
        out = {}
        for key, value in rows:
            if key in out and duplicate is None:
                duplicate = key
            out[key] = value
        return out

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutputValidationError("malformed_json", str(exc)) from None
    if duplicate is not None:
        raise OutputValidationError("duplicate_field",
                                    f"duplicate JSON field {duplicate}")
    return value


def validate_output_bytes(raw: bytes, contract: dict) -> dict:
    if not isinstance(raw, bytes):
        raise OutputValidationError("type_mismatch", "output must be bytes")
    if len(raw) > int(contract.get("max_bytes") or MAX_OUTPUT_BYTES):
        raise OutputValidationError("output_too_large", "output exceeds limit")
    value = _decode(raw)
    output_schema = contract.get("output_schema") or {}
    _validate(value, output_schema)
    if output_schema.get("$id") == EVALUATOR_OUTPUT_SCHEMA_ID:
        validate_evaluator_value(value)
    body = canonical_bytes(value)
    return {"status": "valid", "value": value,
            "canonical_bytes": body,
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def validate_output_file(workspace: str, contract: dict, *,
                         observed_write: dict | None) -> dict:
    path = _safe_result_path(workspace, str(contract.get("result_path") or ""))
    absolute = os.path.join(workspace, path)
    try:
        with open(absolute, "rb") as stream:
            raw = stream.read(int(contract.get("max_bytes") or
                                  MAX_OUTPUT_BYTES) + 1)
    except OSError as exc:
        raise OutputValidationError("missing_output", str(exc)) from None
    receipt = observed_write if isinstance(observed_write, dict) else {}
    expected = {
        "result_path": path, "result_sha256": hashlib.sha256(raw).hexdigest(),
        "result_bytes": len(raw), "task": contract.get("task"),
        "stage": contract.get("stage"), "slot": contract.get("slot"),
        "lease": contract.get("lease"), "producer": contract.get("producer"),
    }
    if receipt.get("schema") != WRITE_OBSERVATION_SCHEMA_ID or \
            receipt.get("host_observed") is not True or any(
                receipt.get(key) != value for key, value in expected.items()):
        raise OutputValidationError(
            "unobserved_write", "output lacks its exact host-observed write")
    return validate_output_bytes(raw, contract)
