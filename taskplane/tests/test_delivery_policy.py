from __future__ import annotations

import pytest

from taskplane.delivery_policy import (
    DeliveryPolicyError,
    create_execution_stage_origin_receipt,
    validate_stage_lens_execution,
    validate_stage_lens_execution_receipt,
)


TERMINAL_EVENTS: dict[str, tuple[str, str]] = {
    "passed": ("SubagentStop", "completed"),
    "failed": ("SubagentFailed", "failed"),
    "cancelled": ("SubagentCancelled", "cancelled"),
    "interrupted": ("SubagentInterrupted", "interrupted"),
    "handed_off": ("SubagentHandedOff", "handed-off"),
}


def _origin(stage: str) -> dict[str, str]:
    return {
        "run_id": "run-focused-routing",
        "session_id": "session-focused-routing",
        "task_name": f"tp_step_{stage}_deadbeef",
        "agent_id": f"{stage}-agent",
    }


def _expected_origin(stage: str) -> dict[str, object]:
    return create_execution_stage_origin_receipt(
        stage=stage,
        dispatch_identity_fingerprint="d" * 64,
        **_origin(stage),
    )


def _attempt(
    stage: str, outcome: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    native_terminal, ledger_terminal = TERMINAL_EVENTS[outcome]
    origin = _origin(stage)
    native_trace = [
        {
            "hook_event_name": "SubagentStart",
            "stage": stage,
            "role_marker": f"taskplane-role:tp-{stage}",
            **origin,
        },
        {
            "hook_event_name": native_terminal,
            "stage": stage,
            "role_marker": f"taskplane-role:tp-{stage}",
            **origin,
        },
    ]
    session_ledger = [
        {
            "event": "started",
            "stage": stage,
            "role_marker": f"taskplane-role:tp-{stage}",
            **origin,
        },
        {
            "event": ledger_terminal,
            "stage": stage,
            "role_marker": f"taskplane-role:tp-{stage}",
            **origin,
        },
    ]
    return native_trace, session_ledger


@pytest.mark.parametrize("stage", ["build", "fix"])
@pytest.mark.parametrize("outcome", list(TERMINAL_EVENTS))
def test_build_and_fix_all_terminal_paths_prove_zero_lens_starts(
    stage: str, outcome: str
) -> None:
    native_trace, session_ledger = _attempt(stage, outcome)

    receipt = validate_stage_lens_execution(
        stage=stage,
        native_trace=native_trace,
        session_ledger=session_ledger,
        expected_origin_receipt=_expected_origin(stage),
    )

    assert validate_stage_lens_execution_receipt(receipt) == receipt
    assert receipt["terminal_outcome"] == outcome
    assert receipt["lens_worker_start_count"] == 0
    assert receipt["status"] == "observed"


@pytest.mark.parametrize("stage", ["build", "fix", "em"])
@pytest.mark.parametrize("outcome", list(TERMINAL_EVENTS))
def test_zero_lens_stages_refuse_lens_workers_for_every_terminal_path(
    stage: str, outcome: str
) -> None:
    native_trace, session_ledger = _attempt(stage, outcome)
    lens_origin = {
        "run_id": "run-focused-routing",
        "session_id": "session-focused-routing",
        "task_name": "tp_lens_security_deadbeef",
        "agent_id": "security-lens-agent",
    }
    native_trace.insert(1, {
        "hook_event_name": "SubagentStart",
        "stage": stage,
        **lens_origin,
    })
    session_ledger.insert(1, {
        "event": "started",
        "stage": stage,
        **lens_origin,
    })

    with pytest.raises(DeliveryPolicyError, match="lens worker start"):
        validate_stage_lens_execution(
            stage=stage,
            native_trace=native_trace,
            session_ledger=session_ledger,
            expected_origin_receipt=_expected_origin(stage),
        )


@pytest.mark.parametrize("stage", ["product", "design", "plan", "evaluate"])
def test_routed_stages_allow_focused_lens_workers(stage: str) -> None:
    native_trace, session_ledger = _attempt(stage, "passed")
    lens_origin = {
        "run_id": "run-focused-routing",
        "session_id": "session-focused-routing",
        "task_name": "tp_lens_architecture_deadbeef",
        "agent_id": "architecture-lens-agent",
    }
    native_trace.insert(1, {
        "hook_event_name": "SubagentStart",
        "stage": stage,
        **lens_origin,
    })
    session_ledger.insert(1, {
        "event": "started",
        "stage": stage,
        **lens_origin,
    })

    receipt = validate_stage_lens_execution(
        stage=stage,
        native_trace=native_trace,
        session_ledger=session_ledger,
        expected_origin_receipt=_expected_origin(stage),
    )

    assert receipt["lens_worker_start_count"] == 1


def test_routed_stage_preserves_duplicate_lens_worker_start_count() -> None:
    native_trace, session_ledger = _attempt("evaluate", "passed")
    lens_origin = {
        "run_id": "run-focused-routing",
        "session_id": "session-focused-routing",
        "task_name": "tp_lens_architecture_deadbeef",
        "agent_id": "architecture-lens-agent",
    }
    native_lens_start = {
        "hook_event_name": "SubagentStart",
        "stage": "evaluate",
        **lens_origin,
    }
    ledger_lens_start = {
        "event": "started",
        "stage": "evaluate",
        **lens_origin,
    }
    native_trace[1:1] = [native_lens_start, native_lens_start.copy()]
    session_ledger[1:1] = [ledger_lens_start, ledger_lens_start.copy()]

    receipt = validate_stage_lens_execution(
        stage="evaluate",
        native_trace=native_trace,
        session_ledger=session_ledger,
        expected_origin_receipt=_expected_origin("evaluate"),
    )

    assert receipt["lens_worker_start_count"] == 2

    session_ledger.pop(2)
    with pytest.raises(
        DeliveryPolicyError,
        match="lens worker starts do not match",
    ):
        validate_stage_lens_execution(
            stage="evaluate",
            native_trace=native_trace,
            session_ledger=session_ledger,
            expected_origin_receipt=_expected_origin("evaluate"),
        )


def test_terminal_outcome_must_match_across_native_trace_and_ledger() -> None:
    native_trace, session_ledger = _attempt("fix", "failed")
    session_ledger[-1]["event"] = "cancelled"

    with pytest.raises(DeliveryPolicyError, match="terminal outcomes do not match"):
        validate_stage_lens_execution(
            stage="fix",
            native_trace=native_trace,
            session_ledger=session_ledger,
            expected_origin_receipt=_expected_origin("fix"),
        )


def test_zero_lens_stage_refuses_terminal_only_lens_observation() -> None:
    native_trace, session_ledger = _attempt("build", "passed")
    lens_terminal = {
        "stage": "build",
        "run_id": "run-focused-routing",
        "session_id": "session-focused-routing",
        "task_name": "tp_lens_security_deadbeef",
        "agent_id": "security-lens-agent",
    }
    native_trace.insert(1, {
        "hook_event_name": "SubagentStop",
        **lens_terminal,
    })
    session_ledger.insert(1, {
        "event": "completed",
        **lens_terminal,
    })

    with pytest.raises(DeliveryPolicyError, match="lens worker observation"):
        validate_stage_lens_execution(
            stage="build",
            native_trace=native_trace,
            session_ledger=session_ledger,
            expected_origin_receipt=_expected_origin("build"),
        )
