from __future__ import annotations

import subprocess

import pytest

from taskplane import lens, loop, review
from taskplane.delivery_policy import (
    DeliveryPolicyError,
    authorize_execution_stage,
    create_execution_stage_origin_receipt,
    validate_execution_stage_authorization,
    validate_plan_mode,
)
from taskplane.evaluation_output import (
    OutputValidationError,
    validate_evaluator_value,
)


def _workspace(tmp_path):
    ws = tmp_path / "repo"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"],
                   cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws,
                   check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=ws,
                   check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          check=True, capture_output=True, text=True).stdout.strip()
    return ws, head


def _route():
    return {
        "lenses": [{
            "id": row["id"], "name": row["name"], "mode": "subagent",
            "tier": "deep", "verdict": "deep", "score": 10,
            "reasons": ["legacy route"], "evidence": ["fixture"],
            "checks": row.get("checks") or [],
            "looks_for": row.get("looks_for") or "",
        } for row in lens.load_catalog()["lenses"]],
        "context": {"status": "complete", "breadth": "routed"},
    }


def _receipt():
    return validate_plan_mode(
        {"requirement": "R-0013", "delivery_mode": "build",
         "automatic_lenses": [], "plan_authority": "human:operator"},
        plan_fingerprint="a" * 64, source_sha="b" * 40)


def _expected_origin(stage):
    return create_execution_stage_origin_receipt(
        stage=stage, run_id="run-r0013", session_id="session-r0013",
        task_name=f"tp_step_{stage}_deadbeef",
        agent_id=f"{stage}-agent", dispatch_identity_fingerprint="d" * 64,
    )


def _evaluator_result(**overrides):
    result = {
        "schema": "taskplane.evaluator-output/v1",
        "task": "task-a",
        "requirement": "R-0013",
        "verdict": "pass",
        "evaluation": {
            "status": "complete", "reason_code": "none", "detail": "",
        },
        "criteria": [],
        "lenses": [],
        "graph": {
            "dispositions": [],
            "requirements_checked": ["R-0013"],
            "contracts_checked": [
                "contract:delivery.execution-zero-lens"
            ],
        },
        "failures": [],
    }
    result.update(overrides)
    return result


def _native_evidence():
    trace, ledger = [], []
    roles = {
        "build": "taskplane-role:tp-execute",
        "fix": "taskplane-role:tp-fix",
        "evaluate": "taskplane-role:tp-evaluator",
        "em": "taskplane-role:tp-engineering",
    }
    for stage, role in roles.items():
        origin = {
            "run_id": "run-r0013",
            "session_id": "session-r0013",
            "task_name": f"tp_step_{stage}_deadbeef",
            "agent_id": f"{stage}-agent",
        }
        trace.extend([
            {"hook_event_name": "SubagentStart", "stage": stage,
             "role_marker": role, **origin},
            {"hook_event_name": "SubagentStop", "stage": stage,
             "role_marker": role, **origin},
        ])
        ledger.extend([
            {"event": "started", "stage": stage, "role_marker": role,
             **origin},
            {"event": "completed", "stage": stage, "role_marker": role,
             **origin},
        ])
    return trace, ledger


def _start(ws, head, *, receipt=review._DELIVERY_MODE_AUTHORITY_UNSET):
    return review.start_review(
        str(ws), target={"fingerprint": "e" * 64, "head": head,
                         "task": "task-a"},
        graph={"meta": {"scanned_head": head,
                         "content_fingerprint": "f" * 64},
               "modules": {"src": {"files": ["src/feature.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {}, "total_impacted": 1,
                "unknown": []},
        diff={"files": ["src/feature.py"], "changed_symbols": ["VALUE"]},
        runnability={"summary": "available", "checks": []},
        requirement={"id": "R-0013", "text": "final EM"},
        acceptance=["delivery is complete"], contracts=[], stage="review",
        task_type="feature", router=_route,
        delivery_mode_receipt=receipt)


def test_build_fix_evaluate_and_em_start_zero_taskplane_lens_workers():
    trace, ledger = _native_evidence()
    worker_calls = []

    for stage in ("build", "fix", "evaluate", "em"):
        authorization = authorize_execution_stage(
            stage=stage,
            delivery_mode_receipt=_receipt(),
            expected_lenses=[],
            native_trace=trace,
            session_ledger=ledger,
            lens_worker_factory=lambda lens_id: worker_calls.append(lens_id),
            expected_origin_receipt=_expected_origin(stage),
        )

        assert validate_execution_stage_authorization(authorization) == \
            authorization
        assert authorization["stage"] == stage
        assert authorization["automatic_lens_workers"] == []
        assert authorization["automatic_lens_worker_count"] == 0

    assert worker_calls == []

    for stage in ("build", "fix", "evaluate", "em"):
        forbidden = trace + [{
            "hook_event_name": "SubagentStart", "stage": stage,
            "run_id": "run-r0013", "session_id": "session-r0013",
            "task_name": f"tp_lens_security_{stage}",
            "agent_id": f"lens-{stage}",
        }]
        with pytest.raises(DeliveryPolicyError, match="lens worker start"):
            authorize_execution_stage(
                stage=stage,
                delivery_mode_receipt=_receipt(),
                expected_lenses=[],
                native_trace=forbidden,
                session_ledger=ledger,
                lens_worker_factory=lambda lens_id: worker_calls.append(
                    lens_id),
                expected_origin_receipt=_expected_origin(stage),
            )

    assert worker_calls == []


@pytest.mark.parametrize("source,mutation", [
    ("native_trace", lambda rows, stage: []),
    ("session_ledger", lambda rows, stage: []),
    ("native_trace", lambda rows, stage: [
        row for row in rows
        if row.get("stage") != stage or
        row.get("hook_event_name") != "SubagentStop"
    ]),
    ("session_ledger", lambda rows, stage: [
        row for row in rows
        if row.get("stage") != stage or row.get("event") != "completed"
    ]),
])
def test_execution_authority_refuses_empty_or_partial_current_stage_evidence(
        source, mutation):
    trace, ledger = _native_evidence()
    arguments = {
        "stage": "evaluate",
        "delivery_mode_receipt": _receipt(),
        "expected_lenses": [],
        "native_trace": trace,
        "session_ledger": ledger,
        "lens_worker_factory": lambda _lens_id: pytest.fail(
            "lens worker factory must not run"),
        "expected_origin_receipt": _expected_origin("evaluate"),
    }
    arguments[source] = mutation(arguments[source], "evaluate")

    with pytest.raises(DeliveryPolicyError, match="current-stage|complete"):
        authorize_execution_stage(**arguments)


@pytest.mark.parametrize("field", [
    "run_id", "session_id", "task_name", "agent_id",
])
def test_execution_authority_refuses_foreign_or_unbound_session_ledger(field):
    trace, ledger = _native_evidence()
    for row in ledger:
        if row["stage"] == "evaluate":
            row[field] = "foreign-origin"

    with pytest.raises(DeliveryPolicyError, match="origins do not match"):
        authorize_execution_stage(
            stage="evaluate", delivery_mode_receipt=_receipt(),
            expected_lenses=[], native_trace=trace, session_ledger=ledger,
            lens_worker_factory=lambda _lens_id: pytest.fail(
                "lens worker factory must not run"),
            expected_origin_receipt=_expected_origin("evaluate"),
        )


@pytest.mark.parametrize("field", [
    "run_id", "session_id", "task_name", "agent_id",
])
def test_matching_foreign_trace_and_ledger_cannot_replace_expected_origin(field):
    trace, ledger = _native_evidence()
    for rows in (trace, ledger):
        for row in rows:
            if row["stage"] == "evaluate":
                row[field] = "jointly-forged-origin"

    with pytest.raises(DeliveryPolicyError, match="sealed expected origin"):
        authorize_execution_stage(
            stage="evaluate", delivery_mode_receipt=_receipt(),
            expected_lenses=[], native_trace=trace, session_ledger=ledger,
            lens_worker_factory=lambda _lens_id: pytest.fail(
                "lens worker factory must not run"),
            expected_origin_receipt=_expected_origin("evaluate"),
        )


def test_missing_tampered_or_wrong_stage_expected_origin_fails_closed():
    trace, ledger = _native_evidence()
    arguments = {
        "stage": "evaluate", "delivery_mode_receipt": _receipt(),
        "expected_lenses": [], "native_trace": trace,
        "session_ledger": ledger,
        "lens_worker_factory": lambda _lens_id: pytest.fail(
            "lens worker factory must not run"),
    }

    with pytest.raises(DeliveryPolicyError, match="sealed expected"):
        authorize_execution_stage(**arguments)

    tampered = _expected_origin("evaluate")
    tampered["agent_id"] = "tampered-agent"
    with pytest.raises(DeliveryPolicyError, match="fingerprint mismatch"):
        authorize_execution_stage(
            **arguments, expected_origin_receipt=tampered
        )

    with pytest.raises(DeliveryPolicyError, match="current stage"):
        authorize_execution_stage(
            **arguments, expected_origin_receipt=_expected_origin("build")
        )


def test_native_tp_lens_task_name_is_refused_without_synthetic_role_marker():
    trace, ledger = _native_evidence()
    lens_origin = {
        "run_id": "run-r0013", "session_id": "session-r0013",
        "task_name": "tp_lens_security_deadbeef", "agent_id": "lens-agent",
    }
    trace.extend([
        {"hook_event_name": "SubagentStart", "stage": "evaluate",
         **lens_origin},
        {"hook_event_name": "SubagentStop", "stage": "evaluate",
         **lens_origin},
    ])
    ledger.extend([
        {"event": "started", "stage": "evaluate", **lens_origin},
        {"event": "completed", "stage": "evaluate", **lens_origin},
    ])

    with pytest.raises(DeliveryPolicyError, match="lens worker start"):
        authorize_execution_stage(
            stage="evaluate", delivery_mode_receipt=_receipt(),
            expected_lenses=[], native_trace=trace, session_ledger=ledger,
            lens_worker_factory=lambda _lens_id: pytest.fail(
                "lens worker factory must not run"),
            expected_origin_receipt=_expected_origin("evaluate"),
        )


def test_empty_expected_collection_is_valid_success():
    result = _evaluator_result()
    validated = validate_evaluator_value(result, expected_lenses=[])
    outage_calls = []

    receipt = review.collect_expected_set(
        run_id="run-a",
        task_id="task-a",
        stage="Evaluate",
        expected_lenses=[],
        collected_lenses=validated["lenses"],
        result=validated,
        result_validator=lambda value: validate_evaluator_value(
            value, expected_lenses=[]),
        producer_observation_fingerprint="c" * 64,
        outage_resolver=lambda *_args, **_kwargs: outage_calls.append(True),
    )

    assert receipt["status"] == "complete"
    assert receipt["expected_lenses"] == []
    assert receipt["collected_lenses"] == []
    assert outage_calls == []


def test_nonempty_malformed_or_outage_fallback_refuses_before_dispatch_or_gate():
    trace, ledger = _native_evidence()
    worker_calls, gate_calls, validator_calls = [], [], []

    def attempt_dispatch(**overrides):
        arguments = {
            "stage": "evaluate",
            "delivery_mode_receipt": _receipt(),
            "expected_lenses": [],
            "native_trace": trace,
            "session_ledger": ledger,
            "lens_worker_factory": (
                lambda lens_id: worker_calls.append(lens_id)
            ),
            "expected_origin_receipt": _expected_origin("evaluate"),
        }
        arguments.update(overrides)
        authorization = authorize_execution_stage(**arguments)
        gate_calls.append(authorization)

    with pytest.raises(DeliveryPolicyError, match=r"expected_lenses=\[\]"):
        attempt_dispatch(expected_lenses=["security"])
    with pytest.raises(DeliveryPolicyError, match="outage fallback"):
        attempt_dispatch(outage_fallback=True)

    malformed = _evaluator_result(lenses=[{
        "lens": "security", "verdict": "pass", "blockers": 0,
    }])
    with pytest.raises(OutputValidationError, match=r"requires lenses=\[\]"):
        validate_evaluator_value(malformed, expected_lenses=[])

    outage = _evaluator_result(evaluation={
        "status": "unavailable",
        "reason_code": "transport_unavailable",
        "detail": "host unavailable",
    }, verdict="fail")
    with pytest.raises(OutputValidationError, match="outage fallback"):
        validate_evaluator_value(outage, expected_lenses=[])

    for ambiguous in (
        _evaluator_result(evaluation=None),
        _evaluator_result(evaluation={}),
        _evaluator_result(evaluation={
            "status": "complete", "reason_code": "host_unavailable",
            "detail": "contradictory completion",
        }),
    ):
        if ambiguous.get("evaluation") is None:
            ambiguous.pop("evaluation")
        with pytest.raises(OutputValidationError, match="completion block"):
            validate_evaluator_value(ambiguous, expected_lenses=[])

    with pytest.raises(DeliveryPolicyError, match="outage fallback"):
        review.collect_expected_set(
            run_id="run-a",
            task_id="task-a",
            stage="Evaluate",
            expected_lenses=[],
            collected_lenses=[],
            result=_evaluator_result(),
            result_validator=lambda value: validator_calls.append(value),
            producer_observation_fingerprint="c" * 64,
            outage_fallback=True,
        )

    assert worker_calls == []
    assert gate_calls == []
    assert validator_calls == []


def test_execution_time_em_uses_sealed_authority_and_zero_slots(tmp_path):
    ws, head = _workspace(tmp_path)
    receipt = _receipt()

    opened = _start(ws, head, receipt=receipt)
    state = review._load_state(str(ws), opened["run_id"])

    assert opened["slots"] == []
    assert opened["expected_lenses"] == []
    assert state["delivery_mode_receipt"] == receipt
    assert all(row["tier"] == "n/a" and row["mode"] == "none"
               for row in state["routing"]["lenses"])

    collected = loop.collect_review_bridge(
        str(ws), publish=False, run_id=opened["run_id"],
        evaluator_result={"findings": {}, "report_sha256": "c" * 64},
        producer_observation_fingerprint="d" * 64,
        collection_stage="EM", result_validator=lambda value: value)
    assert collected["status"] == "complete"
    assert collected["empty_lens_collection"]["stage"] == "EM"


def test_legacy_em_keeps_normal_lens_slots(tmp_path):
    ws, head = _workspace(tmp_path)
    opened = _start(ws, head)

    assert opened["slots"]
    assert "delivery_mode_receipt" not in opened
