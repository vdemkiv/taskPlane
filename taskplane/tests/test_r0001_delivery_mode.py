import json
from pathlib import Path
import subprocess

import pytest

from taskplane import (
    build_c,
    lens,
    loop,
    review,
    storage as runtime_storage,
    tp as tp_cli,
)
from taskplane.delivery_policy import (
    DeliveryPolicyError,
    automatic_lens_workers_for_dispatch,
    create_empty_lens_collection_receipt,
    validate_plan_mode,
)
from taskplane.evaluation_output import (
    OutputValidationError,
    canonical_bytes,
    validate_evaluator_value,
)
from taskplane.producer_observation import ProducerObservationError


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
        "schema": "taskplane.evaluator-output/v2",
        "task": "task-a",
        "requirement": "R-0001",
        "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": ""},
        "criteria": [],
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


def _complete_review_route():
    return {
        "lenses": [
            {
                "id": row["id"],
                "name": row["name"],
                "mode": "subagent",
                "tier": "deep",
                "verdict": "deep",
                "score": 10,
                "reasons": ["would be selected without delivery authority"],
                "evidence": ["controlled complete mapper result"],
                "checks": row.get("checks") or [],
                "looks_for": row.get("looks_for") or "",
            }
            for row in lens.load_catalog()["lenses"]
        ],
        "context": {"status": "complete", "breadth": "routed"},
    }


def _start_evaluate_kernel(workspace: Path, receipt):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    return review.start_review(
        str(workspace),
        target={
            "fingerprint": "e" * 64,
            "head": head,
            "task": "task-a",
        },
        graph={
            "meta": {
                "scanned_head": head,
                "content_fingerprint": "f" * 64,
            },
            "modules": {"src": {"files": ["src/feature.py"]}},
            "edges": [],
        },
        impact={
            "touched": ["src"],
            "impacted": {},
            "total_impacted": 1,
            "unknown": [],
        },
        diff={
            "files": ["src/feature.py"],
            "changed_symbols": ["VALUE"],
        },
        runnability={"summary": "available", "checks": []},
        requirement={"id": "R-0001", "text": "zero automatic lenses"},
        acceptance=["the feature is complete"],
        contracts=["contract:delivery-mode-receipt"],
        stage=loop.EVALUATE_ROUTE_STAGE,
        task_type="feature",
        router=_complete_review_route,
        delivery_mode_receipt=receipt,
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






def test_cli_refuses_producer_observation_flag(tmp_path, capsys):
    with pytest.raises(SystemExit):
        tp_cli.main([
            "loop",
            "--workspace",
            str(tmp_path),
            "submit",
            "pass",
            "--producer-observation",
            "{}",
        ])

    assert "unrecognized arguments" in capsys.readouterr().err


def test_public_loop_submit_refuses_recorded_producer_double(tmp_path):
    class RecordedProducerDouble(dict):
        pass

    recorded = RecordedProducerDouble(schema="recorded-producer-double")
    with pytest.raises(TypeError, match="producer_observation"):
        loop.submit(
            str(tmp_path),
            "pass",
            producer_observation=recorded,
        )
    with pytest.raises(ProducerObservationError, match="external host"):
        loop.bind_producer_observation(
            {"step": "evaluate", "task": "task-a"},
            recorded,
            output_bytes=b"{}\n",
            output_schema_id="taskplane.evaluator-output/v2",
            output_contract_fingerprint="d" * 64,
        )






def test_malformed_empty_lens_result_is_not_success():
    result = _evaluator_result()
    result.pop("verdict")
    with pytest.raises(OutputValidationError, match="missing verdict"):
        _empty_collection(result)


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
