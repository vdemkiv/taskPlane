"""Behavior contracts for classified failures and evaluator admission."""
from __future__ import annotations

import hashlib
import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import evaluation_output  # noqa: E402
import failure_routing  # noqa: E402


ROUTES = {
    "product": "fix",
    "test": "test-correction",
    "infrastructure": "infrastructure-recovery",
    "environment": "environment-recovery",
    "mixed": "hold",
    "unknown": "hold",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate(name: str = "candidate-7") -> dict:
    return {"id": name, "fingerprint": _digest(name)}


def _failure(failure_class: str = "product", *, failure_id: str = "F-1",
             candidate: dict | None = None, **changes) -> dict:
    evidence = {
        "schema": "taskplane.failure-evidence/v1",
        "command": "pytest -q taskplane/tests/test_widget.py::test_contract",
        "returncode": 1,
        "stderr": "assertion failed",
    }
    record = {
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
        "id": failure_id,
        "source": "pytest:taskplane/tests/test_widget.py::test_contract",
        "stage": "evaluate",
        "repro": "pytest -q taskplane/tests/test_widget.py::test_contract",
        "evidence": evidence,
        "evidence_digest": failure_routing.evidence_digest(evidence),
        "class": failure_class,
        "reason": "the observed behavior violates the acceptance contract",
        "owner": "product-code" if failure_class == "product" else
                 f"{failure_class}-owner",
        "cluster": "widget-contract",
        "route": ROUTES[failure_class],
        "candidate": candidate or _candidate(),
    }
    record.update(changes)
    return record


def _evaluation(verdict: str, failures: list[dict]) -> dict:
    return {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": "task-7",
        "requirement": "R-0007",
        "verdict": verdict,
        "evaluation": {
            "status": "complete",
            "reason_code": "none",
            "detail": "",
        },
        "criteria": [{
            "criterion": "classified correction",
            "status": "met" if verdict == "pass" else "not-met",
            "evidence": "pytest report",
        }],
        "graph": {
            "dispositions": [],
            "requirements_checked": ["R-0007"],
            "contracts_checked": ["failure-routing"],
        },
        "failures": failures,
    }


@pytest.mark.parametrize("failure_class,route", ROUTES.items())
def test_each_class_has_one_owned_route(failure_class, route):
    record = failure_routing.validate_failure_record(
        _failure(failure_class))
    assert record["route"] == route

    decision = failure_routing.route_failure_records([record])
    assert decision["next"] == route
    assert decision["routes"][route] == ["F-1"]
    assert decision["hold_required"] is (failure_class in {"mixed", "unknown"})
    assert decision["product_fix_allowed"] is (failure_class == "product")
    assert decision["test_correction_allowed"] is (failure_class == "test")
    assert decision["infrastructure_recovery_required"] is (
        failure_class == "infrastructure")
    assert decision["environment_recovery_required"] is (
        failure_class == "environment")


def test_known_classes_split_without_cross_authorizing_product_fixes():
    test_failure = _failure("test", failure_id="F-test")
    environment_failure = _failure("environment", failure_id="F-env")
    decision = failure_routing.route_failure_records(
        [test_failure, environment_failure])

    assert decision["admitted"] is True
    assert decision["next"] == "split"
    assert decision["test_correction_allowed"] is True
    assert decision["environment_recovery_required"] is True
    assert decision["product_fix_allowed"] is False


def test_product_fix_requires_an_exclusively_product_inventory():
    decision = failure_routing.route_failure_records([
        _failure("product", failure_id="F-product"),
        _failure("test", failure_id="F-test"),
    ])

    assert decision["next"] == "split"
    assert decision["product_fix_allowed"] is False
    assert decision["test_correction_allowed"] is True


def test_one_unknown_record_holds_the_entire_inventory():
    decision = failure_routing.route_failure_records([
        _failure("product", failure_id="F-product"),
        _failure("unknown", failure_id="F-unknown"),
    ])

    assert decision["admitted"] is False
    assert decision["next"] == "hold"
    assert decision["hold_required"] is True
    assert decision["product_fix_allowed"] is False


@pytest.mark.parametrize("field", sorted({
    "schema", "id", "source", "stage", "repro", "evidence",
    "evidence_digest",
    "class", "reason", "owner", "cluster", "route", "candidate",
}))
def test_every_failure_field_is_required(field):
    record = _failure()
    record.pop(field)
    with pytest.raises(failure_routing.FailureRoutingError):
        failure_routing.validate_failure_record(record)


@pytest.mark.parametrize("mutation,code", [
    (lambda row: row.update(route="test-correction"), "failure_route"),
    (lambda row: row.update(evidence_digest="not-a-digest"),
     "failure_digest"),
    (lambda row: row.update(reason=""), "failure_field"),
    (lambda row: row.update(stage="invented-stage"), "failure_stage"),
    (lambda row: row.update(extra="not allowed"), "failure_record_shape"),
])
def test_malformed_or_contradictory_records_fail_closed(mutation, code):
    record = _failure()
    mutation(record)
    with pytest.raises(failure_routing.FailureRoutingError) as caught:
        failure_routing.validate_failure_record(record)
    assert caught.value.code == code


def test_evidence_is_recomputed_instead_of_trusting_a_claimed_digest():
    record = _failure()
    record["evidence"]["stderr"] = "different bytes"

    with pytest.raises(failure_routing.FailureRoutingError) as caught:
        failure_routing.validate_failure_record(record)
    assert caught.value.code == "failure_evidence_mismatch"


@pytest.mark.parametrize("stage", ["build", "evaluate", "ci"])
def test_build_evaluate_and_ci_use_the_same_failure_contract(stage):
    record = failure_routing.validate_failure_record(_failure(stage=stage))

    assert record["schema"] == failure_routing.FAILURE_RECORD_SCHEMA_ID
    assert record["stage"] == stage


def test_inventory_rejects_duplicate_ids_and_mixed_candidate_identity():
    duplicate = _failure(failure_id="F-same")
    with pytest.raises(failure_routing.FailureRoutingError) as caught:
        failure_routing.validate_failure_records([duplicate, duplicate])
    assert caught.value.code == "duplicate_failure"

    with pytest.raises(failure_routing.FailureRoutingError) as caught:
        failure_routing.validate_failure_records([
            _failure(failure_id="F-1"),
            _failure(failure_id="F-2", candidate=_candidate("candidate-8")),
        ])
    assert caught.value.code == "candidate_mismatch"


def test_real_evaluator_validator_requires_failures_only_on_fail():
    with pytest.raises(evaluation_output.OutputValidationError) as caught:
        evaluation_output.validate_evaluator_value(_evaluation("pass", []))
    assert caught.value.code == "child_evidence_required"

    with pytest.raises(evaluation_output.OutputValidationError) as caught:
        evaluation_output.validate_evaluator_value(
            _evaluation("pass", [_failure()]))
    assert caught.value.code == "pass_has_failures"

    with pytest.raises(evaluation_output.OutputValidationError) as caught:
        evaluation_output.validate_evaluator_value(_evaluation("fail", []))
    assert caught.value.code == "failure_admission"


def test_real_evaluator_validator_admits_complete_records_and_checks_routes():
    value = _evaluation("fail", [_failure("product")])
    assert evaluation_output.validate_evaluator_value(value) is value

    value["failures"][0]["route"] = "test-correction"
    with pytest.raises(evaluation_output.OutputValidationError) as caught:
        evaluation_output.validate_evaluator_value(value)
    assert caught.value.code == "failure_admission"


def test_evaluator_byte_admission_runs_failure_semantics():
    value = _evaluation("fail", [_failure("product")])
    value["failures"][0]["reason"] = ""
    contract = {
        "max_bytes": evaluation_output.MAX_OUTPUT_BYTES,
        "output_schema": evaluation_output.evaluator_output_schema(),
    }
    with pytest.raises(evaluation_output.OutputValidationError) as caught:
        evaluation_output.validate_output_bytes(
            evaluation_output.canonical_bytes(value), contract)
    assert caught.value.code == "failure_admission"


def test_legacy_failures_are_readable_but_never_correction_authority():
    legacy = {
        "what": "widget is wrong",
        "repro": "pytest -q test_widget.py",
        "where": "test_widget.py:10",
    }
    value = _evaluation("fail", [legacy])

    with pytest.raises(evaluation_output.OutputValidationError):
        evaluation_output.validate_evaluator_value(value)
    read = evaluation_output.read_evaluator_value(value)
    assert read["value"] == value
    assert read["legacy_failures"] == [legacy]
    assert read["failure_records"] == []
    assert read["routing"] is None
    assert read["correction_authority"] is False


def test_complete_unknown_failure_is_recorded_but_held():
    value = _evaluation("fail", [_failure("unknown")])
    evaluation_output.validate_evaluator_value(value)

    read = evaluation_output.read_evaluator_value(value)
    assert read["routing"]["next"] == "hold"
    assert read["routing"]["admitted"] is False
    assert read["correction_authority"] is False
