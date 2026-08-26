"""Closed delivery-mode and empty automatic-lens receipt contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from taskplane.delivery_ports import content_fingerprint


DELIVERY_MODE_RECEIPT_SCHEMA = "taskplane.delivery-mode-receipt/v1"
EMPTY_LENS_COLLECTION_SCHEMA = "taskplane.empty-lens-collection/v1"
DELIVERY_MODES = frozenset({"build", "review", "design"})
AUTOMATIC_LENS_MODES = frozenset({"design"})


class DeliveryPolicyError(ValueError):
    """A Plan or collection value violates the closed delivery policy."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryPolicyError(f"{field} is required")
    return value


def _fingerprint(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    text = _required_text(value, field)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DeliveryPolicyError(f"{field} must be a lowercase SHA-256 fingerprint")
    return text


def _source_sha(value: Any) -> str:
    text = _required_text(value, "source_sha")
    if len(text) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DeliveryPolicyError("source_sha must be an exact lowercase Git SHA")
    return text


def _lens_ids(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DeliveryPolicyError(f"{field} must be a collection")
    lenses = tuple(_required_text(item, field) for item in value)
    if len(lenses) != len(set(lenses)):
        raise DeliveryPolicyError(f"{field} contains duplicate lens ids")
    return lenses


def _seal(projection: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(projection)
    sealed["fingerprint"] = content_fingerprint(sealed)
    return sealed


def validate_plan_mode(
    plan: Mapping[str, Any],
    *,
    plan_fingerprint: str,
    source_sha: str,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Normalize a Plan declaration into a closed delivery-mode receipt."""
    if not isinstance(plan, Mapping):
        raise DeliveryPolicyError("Plan must be a mapping")
    mode = plan.get("delivery_mode")
    if mode not in DELIVERY_MODES:
        raise DeliveryPolicyError("delivery mode must be build, review, or design")
    lenses = _lens_ids(plan.get("automatic_lenses"), "automatic_lenses")
    if mode not in AUTOMATIC_LENS_MODES and lenses:
        raise DeliveryPolicyError(f"{mode} delivery mode forbids automatic lenses")
    return _seal(
        {
            "schema": DELIVERY_MODE_RECEIPT_SCHEMA,
            "requirement": _required_text(plan.get("requirement"), "requirement"),
            "plan_fingerprint": _fingerprint(plan_fingerprint, "plan_fingerprint"),
            "mode": mode,
            "automatic_lenses": list(lenses),
            "plan_authority": _required_text(
                plan.get("plan_authority"), "plan_authority"
            ),
            "source_sha": _source_sha(source_sha),
            "predecessor_fingerprint": _fingerprint(
                predecessor_fingerprint, "predecessor_fingerprint", optional=True
            ),
        }
    )


def validate_delivery_mode_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact v1 receipt shape and its content fingerprint."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError("delivery-mode receipt must be a mapping")
    fields = {
        "schema",
        "requirement",
        "plan_fingerprint",
        "mode",
        "automatic_lenses",
        "plan_authority",
        "source_sha",
        "predecessor_fingerprint",
        "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError("delivery-mode receipt fields are not closed")
    if receipt.get("schema") != DELIVERY_MODE_RECEIPT_SCHEMA:
        raise DeliveryPolicyError("delivery-mode receipt schema is invalid")
    normalized = validate_plan_mode(
        {
            "requirement": receipt.get("requirement"),
            "delivery_mode": receipt.get("mode"),
            "automatic_lenses": receipt.get("automatic_lenses"),
            "plan_authority": receipt.get("plan_authority"),
        },
        plan_fingerprint=receipt.get("plan_fingerprint"),
        source_sha=receipt.get("source_sha"),
        predecessor_fingerprint=receipt.get("predecessor_fingerprint"),
    )
    if receipt.get("fingerprint") != normalized["fingerprint"]:
        raise DeliveryPolicyError("delivery-mode receipt fingerprint mismatch")
    return normalized


def automatic_lens_workers_for_dispatch(
    receipt: Mapping[str, Any], lens_worker_factory: Callable[[str], Any]
) -> tuple[Any, ...]:
    """Validate policy before constructing any automatic lens worker."""
    normalized = validate_delivery_mode_receipt(receipt)
    lenses = tuple(normalized["automatic_lenses"])
    if normalized["mode"] == "build" and lenses:
        raise DeliveryPolicyError("build delivery mode forbids automatic lenses")
    if lenses and not callable(lens_worker_factory):
        raise DeliveryPolicyError("lens_worker_factory must be callable")
    return tuple(lens_worker_factory(lens) for lens in lenses)


def create_empty_lens_collection_receipt(
    *,
    run_id: str,
    task_id: str,
    stage: str,
    expected_lenses: Sequence[str],
    collected_lenses: Sequence[str],
    result: Mapping[str, Any],
    result_validator: Callable[[dict[str, Any]], Any],
    producer_observation_fingerprint: str,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Record normal success for a validated result with no expected lenses."""
    expected = _lens_ids(expected_lenses, "expected_lenses")
    collected = _lens_ids(collected_lenses, "collected_lenses")
    if expected or collected:
        raise DeliveryPolicyError(
            "empty collection requires empty expected and collected lenses"
        )
    if not isinstance(result, Mapping):
        raise DeliveryPolicyError("result must be a mapping")
    if not callable(result_validator):
        raise DeliveryPolicyError("result_validator must be callable")
    validation_input = dict(result)
    validated_result = result_validator(validation_input)
    if validated_result is None:
        validated_result = validation_input
    if not isinstance(validated_result, Mapping):
        raise DeliveryPolicyError("result validator must return a mapping or None")
    return _seal(
        {
            "schema": EMPTY_LENS_COLLECTION_SCHEMA,
            "run_id": _required_text(run_id, "run_id"),
            "task_id": _required_text(task_id, "task_id"),
            "stage": _required_text(stage, "stage"),
            "expected_lenses": [],
            "collected_lenses": [],
            "result_fingerprint": content_fingerprint(dict(validated_result)),
            "producer_observation_fingerprint": _fingerprint(
                producer_observation_fingerprint,
                "producer_observation_fingerprint",
            ),
            "status": "complete",
            "predecessor_fingerprint": _fingerprint(
                predecessor_fingerprint, "predecessor_fingerprint", optional=True
            ),
        }
    )


def validate_empty_lens_collection_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed successful empty-collection receipt."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError("empty-collection receipt must be a mapping")
    fields = {
        "schema",
        "run_id",
        "task_id",
        "stage",
        "expected_lenses",
        "collected_lenses",
        "result_fingerprint",
        "producer_observation_fingerprint",
        "status",
        "predecessor_fingerprint",
        "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError("empty-collection receipt fields are not closed")
    if receipt.get("schema") != EMPTY_LENS_COLLECTION_SCHEMA:
        raise DeliveryPolicyError("empty-collection receipt schema is invalid")
    _required_text(receipt.get("run_id"), "run_id")
    _required_text(receipt.get("task_id"), "task_id")
    _required_text(receipt.get("stage"), "stage")
    if _lens_ids(receipt.get("expected_lenses"), "expected_lenses") or _lens_ids(
        receipt.get("collected_lenses"), "collected_lenses"
    ):
        raise DeliveryPolicyError("empty-collection receipt contains lenses")
    _fingerprint(receipt.get("result_fingerprint"), "result_fingerprint")
    _fingerprint(
        receipt.get("producer_observation_fingerprint"),
        "producer_observation_fingerprint",
    )
    _fingerprint(
        receipt.get("predecessor_fingerprint"),
        "predecessor_fingerprint",
        optional=True,
    )
    if receipt.get("status") != "complete":
        raise DeliveryPolicyError("empty-collection receipt status must be complete")
    projection = {key: receipt[key] for key in fields - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise DeliveryPolicyError("empty-collection receipt fingerprint mismatch")
    return dict(receipt)
