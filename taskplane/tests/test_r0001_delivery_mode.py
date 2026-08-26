import pytest

from taskplane import build_c, loop, review
from taskplane.delivery_policy import (
    DeliveryPolicyError,
    automatic_lens_workers_for_dispatch,
    create_empty_lens_collection_receipt,
    validate_plan_mode,
)
from taskplane.evaluation_output import (
    OutputValidationError,
    validate_evaluator_value,
)


SOURCE_SHA = "a" * 40
PLAN_FINGERPRINT = "b" * 64
OBSERVATION_FINGERPRINT = "c" * 64


def _build_receipt(**plan_overrides):
    plan = {
        "requirement": "R-0001",
        "delivery_mode": "build",
        "automatic_lenses": [],
        "plan_authority": "human:operator",
    }
    plan.update(plan_overrides)
    return validate_plan_mode(
        plan,
        plan_fingerprint=PLAN_FINGERPRINT,
        source_sha=SOURCE_SHA,
    )


def _evaluator_result(**overrides):
    result = {
        "schema": "taskplane.evaluator-output/v1",
        "task": "task-a",
        "requirement": "R-0001",
        "verdict": "pass",
        "criteria": [],
        "lenses": [],
        "graph": {
            "dispositions": [],
            "requirements_checked": ["R-0001"],
            "contracts_checked": ["contract:delivery-mode-receipt"],
        },
        "failures": [],
    }
    result.update(overrides)
    return result


def _empty_collection(result):
    return create_empty_lens_collection_receipt(
        run_id="run-a",
        task_id="task-a",
        stage="Evaluate",
        expected_lenses=[],
        collected_lenses=[],
        result=result,
        result_validator=validate_evaluator_value,
        producer_observation_fingerprint=OBSERVATION_FINGERPRINT,
    )


def test_plan_gate_requires_and_stamps_delivery_mode():
    receipt = _build_receipt()

    assert receipt["schema"] == "taskplane.delivery-mode-receipt/v1"
    assert receipt["requirement"] == "R-0001"
    assert receipt["plan_fingerprint"] == PLAN_FINGERPRINT
    assert receipt["mode"] == "build"
    assert receipt["automatic_lenses"] == []
    assert receipt["plan_authority"] == "human:operator"
    assert receipt["source_sha"] == SOURCE_SHA
    assert len(receipt["fingerprint"]) == 64

    with pytest.raises(DeliveryPolicyError, match="delivery mode"):
        _build_receipt(delivery_mode=None)


def test_build_mode_dispatch_creates_zero_automatic_lens_workers():
    created = []

    workers = automatic_lens_workers_for_dispatch(
        _build_receipt(), lambda lens: created.append(lens)
    )

    assert workers == ()
    assert created == []


def test_sever_delivery_mode_receipt_to_dispatch_fails_closed():
    receipt = _build_receipt()
    receipt["source_sha"] = "d" * 40
    created = []

    with pytest.raises(DeliveryPolicyError, match="fingerprint"):
        automatic_lens_workers_for_dispatch(
            receipt, lambda lens: created.append(lens)
        )
    assert created == []


def test_empty_expected_lenses_emits_successful_collection_receipt():
    receipt = _empty_collection(_evaluator_result())

    assert receipt["schema"] == "taskplane.empty-lens-collection/v1"
    assert receipt["expected_lenses"] == []
    assert receipt["collected_lenses"] == []
    assert receipt["status"] == "complete"
    assert receipt["producer_observation_fingerprint"] == OBSERVATION_FINGERPRINT
    assert len(receipt["result_fingerprint"]) == 64
    assert len(receipt["fingerprint"]) == 64


def test_malformed_empty_lens_result_is_not_success():
    result = _evaluator_result()
    result.pop("verdict")
    with pytest.raises(OutputValidationError, match="missing verdict"):
        _empty_collection(result)


def test_empty_lens_path_never_enters_outage_resolution():
    receipt = _empty_collection(_evaluator_result())

    assert receipt["status"] == "complete"
    assert not any("outage" in key or "resolution" in key for key in receipt)


def test_plan_gate_receipt_is_the_build_dispatch_authority():
    state = {"requirement_id": "R-0001"}
    receipt = loop.stamp_plan_delivery_mode(
        state,
        {
            "requirement": "R-0001",
            "delivery_mode": "build",
            "automatic_lenses": [],
            "plan_authority": "human:operator",
        },
        plan_fingerprint=PLAN_FINGERPRINT,
        source_sha=SOURCE_SHA,
    )
    created = []

    dispatch = build_c.authorize_delivery_dispatch(
        receipt, lens_worker_factory=lambda lens: created.append(lens)
    )

    assert state["delivery_mode_receipt"] == receipt
    assert dispatch["delivery_mode_receipt"] == receipt
    assert dispatch["automatic_lens_workers"] == ()
    assert created == []


def test_review_empty_expected_set_uses_normal_collection_not_outage():
    outage_calls = []

    receipt = review.collect_expected_set(
        run_id="run-a",
        task_id="task-a",
        stage="Evaluate",
        expected_lenses=[],
        collected_lenses=[],
        result=_evaluator_result(),
        result_validator=validate_evaluator_value,
        producer_observation_fingerprint=OBSERVATION_FINGERPRINT,
        outage_resolver=lambda *_args, **_kwargs: outage_calls.append(True),
    )

    assert receipt["schema"] == "taskplane.empty-lens-collection/v1"
    assert receipt["status"] == "complete"
    assert outage_calls == []


def test_design_governed_missing_delivery_mode_never_uses_legacy_lens_fallback(
    monkeypatch,
):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("lens routing or worker construction was invoked")

    monkeypatch.setattr(loop.lens_router, "prime_scope", forbidden)
    monkeypatch.setattr(build_c, "authorize_delivery_dispatch", forbidden)

    with pytest.raises(DeliveryPolicyError, match="delivery-mode receipt"):
        loop.build_dispatch_lens_routing(
            {
                "requirement_id": "R-0001",
                "design_fingerprint": "d" * 64,
            },
            {"id": "t05", "scope": ["taskplane/**"], "type": "integration"},
            workspace="/workspace",
        )

    assert calls == []
