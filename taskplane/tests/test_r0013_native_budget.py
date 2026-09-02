"""R-0013 AC6: native usage, hard budgets, and bounded handoffs."""

from __future__ import annotations

import contextlib
import copy

import pytest

from taskplane import brief_projection, dispatch_telemetry, loop, progress, spend
from taskplane.delivery_ports import FakeClock


def _ledger() -> dict:
    return dispatch_telemetry.new_ledger(
        run_id="run-r0013", source_sha="a" * 40,
        design_fingerprint="design-r0013", plan_fingerprint="plan-r0013",
        started_at=5,
    )


def _dispatch(dispatch_id: str, *, correction_count: int = 0) -> dict:
    return {
        "dispatch_id": dispatch_id,
        "thread_id": f"thread-{dispatch_id}",
        "thread_type": "worker",
        "task_id": f"task-{dispatch_id}",
        "dependencies": [],
        "shared_owner": None,
        "started_at": 6,
        "ended_at": 6,
        "wait_duration_seconds": 0,
        "correction_count": correction_count,
        "events": [],
    }


def _usage(*, cached: int = 2, uncached: int = 6,
           output: int = 2, total: int = 10) -> dict:
    return {
        "input_tokens": cached + uncached,
        "cached_input_tokens": cached,
        "uncached_input_tokens": uncached,
        "output_tokens": output,
        "reasoning_tokens": 1,
        "total_tokens": total,
    }


def _reference(kind: str, fingerprint: str, *, size: int = 1) -> dict:
    return {
        "schema": brief_projection.REFERENCE_SCHEMA,
        "kind": kind,
        "fingerprint": fingerprint,
        "bytes": size,
    }


def _screen(ledger: dict, **overrides: int) -> dict:
    return dispatch_telemetry.screen_dispatch(
        ledger, FakeClock(wall_time=10), current_stage="build",
        outstanding_set_fingerprint="b" * 64,
        preserved_context_fingerprint="c" * 64,
        overrides=overrides or None,
    )


def test_missing_or_malformed_host_usage_fails_closed_before_next_dispatch() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(ledger, _dispatch("active"))

    stopped = _screen(ledger)
    assert stopped["status"] == "human_scope_review"
    assert stopped["dispatch_allowed"] is False
    assert stopped["observed_usage"]["status"] == "unavailable"
    assert stopped["checkpoint"]["measured_value"] is None
    assert stopped["checkpoint"]["resume_allowed"] is False
    assert {row["id"] for row in stopped["checkpoint"]["actions"]} == {
        "reduce-scope", "end-wave", "architecture-review",
    }

    malformed = _usage()
    malformed["total_tokens"] = 1
    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="total tokens do not reconcile"):
        dispatch_telemetry.observe_usage(
            ledger, dispatch_id="active", usage=malformed,
            source_fingerprint="d" * 64)

    dispatch_telemetry.observe_usage(
        ledger, dispatch_id="active", usage=_usage(),
        source_fingerprint="d" * 64)
    assert _screen(ledger)["dispatch_allowed"] is True

    with pytest.raises(ValueError, match="observed provider usage"):
        spend.observed_dispatch_usage(
            {"input_tokens": 1, "output_tokens": 1}, provider="codex")
    with pytest.raises(ValueError, match="finite and non-null"):
        progress.require_active_observed_tokens({
            "schema": progress.SNAPSHOT_SCHEMA, "state": "agent-wait",
            "observed_tokens": None,
        })


def test_breach_stops_before_any_next_spawn() -> None:
    for field in dispatch_telemetry.ADMISSION_BUDGET_FIELDS:
        ceiling = dispatch_telemetry.WAVE_BUDGET_CEILINGS[field]
        stopped = _screen(_ledger(), **{field: ceiling})
        assert stopped["dispatch_allowed"] is False
        assert stopped["status"] == "human_scope_review"
        assert stopped["budget"]["triggered"] == [{
            "field": field, "observed": ceiling, "ceiling": ceiling,
        }]
        assert stopped["checkpoint"]["source_sha"] == "a" * 40
        assert stopped["checkpoint"]["outstanding_set_fingerprint"] == \
            "b" * 64
        assert stopped["checkpoint"]["observed_usage_fingerprint"] == \
            stopped["observed_usage_fingerprint"]
        assert stopped["fingerprint"]


def test_aggregate_token_observation_is_preserved_without_program_stop() -> None:
    ledger = _ledger()
    ceiling = dispatch_telemetry.WAVE_BUDGET_CEILINGS["total_tokens"]
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active"),
        usage=_usage(cached=ceiling, uncached=0, output=0, total=ceiling),
        source_fingerprint="d" * 64,
    )

    stopped = _screen(
        ledger, elapsed_seconds=0, sessions=0,
        total_tokens=0, uncached_input_tokens=0)

    assert stopped["dispatch_allowed"] is True
    assert stopped["observed_usage"] == {
        "elapsed_seconds": 5,
        "sessions": 1,
        "total_tokens": ceiling,
        "uncached_input_tokens": 0,
    }
    assert stopped["budget"]["triggered"] == []


def test_active_usage_contributes_to_all_four_budget_totals() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active"), usage=_usage(),
        source_fingerprint="d" * 64)
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("final"),
        usage=_usage(cached=3, uncached=7, output=3, total=20),
        source_fingerprint="e" * 64)
    dispatch_telemetry.finalize_usage(
        ledger, dispatch_id="final", ended_at=9,
        clock=FakeClock(wall_time=10),
        events=[{"kind": "complete", "sequence": 1}],
    )

    assert dispatch_telemetry.wave_usage(
        ledger, FakeClock(wall_time=10)) == {
            "elapsed_seconds": 5,
            "sessions": 2,
            "total_tokens": 30,
            "uncached_input_tokens": 13,
        }
    # The finalized binding remains for audit, but its counters are represented
    # by exactly one final receipt rather than counted a second time.
    assert len(ledger["bindings"]) == 2
    assert len(ledger["dispatches"]) == 1
    assert progress.require_active_observed_tokens({
        "schema": progress.SNAPSHOT_SCHEMA, "state": "agent-wait",
        "observed_tokens": 30,
    }) == 30


def test_cut_screen_dispatch_to_telemetry_binding_refuses_dispatch() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active"), usage=_usage(),
        source_fingerprint="d" * 64)
    binding = ledger["bindings"][0]
    assert binding["usage_integrity_fingerprint"]

    # Simulate the Design edge being severed while retaining otherwise valid
    # counters.  The screen must not fall back to those unbound numbers.
    binding["usage_integrity_fingerprint"] = None

    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="active dispatch usage integrity fingerprint mismatched"):
        _screen(ledger)


def test_live_hook_dispatch_populates_active_observed_tokens(
        tmp_path, monkeypatch) -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(ledger, _dispatch("hook"))
    state = {"dispatch_telemetry": ledger}

    @contextlib.contextmanager
    def mutate(_workspace):
        yield state

    monkeypatch.setattr(loop, "mutate", mutate)
    observed = {
        "schema": spend.USAGE_SCHEMA,
        "provider": "codex",
        "available": True,
        "reason": None,
        "uncached_input_tokens": 6,
        "cached_input_tokens": 2,
        "cache_creation_tokens": 0,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "raw_total_tokens": 10,
        "effective_tokens": 12,
    }

    result = loop.record_observed_dispatch_usage(
        str(tmp_path), task_id="task-hook", normalized_usage=observed,
        source=str(tmp_path / "live-hook.jsonl"))

    assert result["usage"] == _usage()
    assert result["usage_source_fingerprint"]
    assert result["usage_integrity_fingerprint"]
    assert result["finalized_receipt_fingerprint"] is None
    active = dispatch_telemetry.wave_usage(
        ledger, FakeClock(wall_time=10))
    assert active == {
        "elapsed_seconds": 5,
        "sessions": 1,
        "total_tokens": 10,
        "uncached_input_tokens": 6,
    }
    assert progress.require_active_observed_tokens({
        "schema": progress.SNAPSHOT_SCHEMA,
        "state": "agent-wait",
        "observed_tokens": active["total_tokens"],
    }) == 10


@pytest.mark.parametrize("mutation", ["usage", "identity", "source"])
def test_active_usage_mutation_with_retained_fingerprint_fails_closed(
        mutation: str) -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active"), usage=_usage(),
        source_fingerprint="d" * 64)
    binding = ledger["bindings"][0]
    retained = binding["usage_integrity_fingerprint"]

    if mutation == "usage":
        binding["usage"]["total_tokens"] += 1
    elif mutation == "identity":
        binding["thread_id"] = "thread-forged"
    else:
        binding["usage_source_fingerprint"] = "e" * 64
    assert binding["usage_integrity_fingerprint"] == retained

    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="active dispatch usage integrity fingerprint mismatched"):
        dispatch_telemetry.validate_ledger(ledger)
    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="active dispatch usage integrity fingerprint mismatched"):
        _screen(ledger)


def test_final_usage_mutation_with_retained_fingerprint_fails_closed() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("final"), usage=_usage(),
        source_fingerprint="d" * 64)
    dispatch_telemetry.finalize_usage(
        ledger, dispatch_id="final", ended_at=9,
        clock=FakeClock(wall_time=10),
        events=[{"kind": "complete", "sequence": 1}],
    )
    receipt = ledger["dispatches"][0]
    retained = receipt["fingerprint"]
    receipt["total_tokens"] += 1
    assert receipt["fingerprint"] == retained

    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="final dispatch usage integrity fingerprint mismatched"):
        dispatch_telemetry.validate_ledger(ledger)
    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="final dispatch usage integrity fingerprint mismatched"):
        _screen(ledger)


def test_delta_handoff_is_below_4000_tokens_and_contains_only_required_fields() -> None:
    handoff = brief_projection.stage_delta_handoff(
        source_sha="a" * 40,
        requirement_id="R-0013",
        active_contracts=["contract:delivery.bounded-stage-handoff"],
        acceptance_outcomes=["AC6"],
        new_evidence=[_reference("evidence", "d" * 64, size=250)],
        unresolved_decisions=[],
        outstanding_native_set=_reference(
            "native-set", "b" * 64, size=180),
        observed_usage={"elapsed_seconds": 5, "sessions": 2,
                        "total_tokens": 30, "uncached_input_tokens": 13},
        predecessor_fingerprint="c" * 64,
    )

    assert set(handoff) == brief_projection.STAGE_DELTA_FIELDS
    assert handoff["schema"] == "taskplane.stage-delta-handoff/v1"
    assert handoff["observed_usage"]["unique_sessions"] == 2
    assert "sessions" not in handoff["observed_usage"]
    assert len(brief_projection.canonical_text(handoff).encode("utf-8")) < 4_000
    assert brief_projection.validate_stage_delta_handoff(handoff) == handoff

    tampered = copy.deepcopy(handoff)
    tampered["acceptance_outcomes"] = ["AC7"]
    with pytest.raises(brief_projection.BriefProjectionError,
                       match="fingerprint or content mismatched"):
        brief_projection.validate_stage_delta_handoff(tampered)
    with pytest.raises(brief_projection.BriefProjectionError,
                       match="strictly below 4000"):
        brief_projection.stage_delta_handoff(
            source_sha="a" * 40, requirement_id="R-0013",
            active_contracts=["contract:x"], acceptance_outcomes=["AC6"],
            new_evidence=[
                _reference("evidence", f"{index:064x}", size=1)
                for index in range(40)
            ],
            unresolved_decisions=[], outstanding_native_set=None,
            observed_usage={"elapsed_seconds": 0, "sessions": 0,
                            "total_tokens": 0, "uncached_input_tokens": 0},
            predecessor_fingerprint=None,
        )


@pytest.mark.parametrize(
    "inline_key",
    ["prompt", "messages", "model_output", "transcript"],
)
def test_delta_handoff_rejects_nested_inline_content_by_closed_schema(
        inline_key: str) -> None:
    forged = _reference("evidence", "d" * 64)
    forged["metadata"] = {inline_key: ["copied inline body"]}

    with pytest.raises(
            brief_projection.BriefProjectionError,
            match="closed content-addressed reference"):
        brief_projection.stage_delta_handoff(
            source_sha="a" * 40, requirement_id="R-0013",
            active_contracts=["contract:x"], acceptance_outcomes=["AC6"],
            new_evidence=[forged], unresolved_decisions=[],
            outstanding_native_set=None,
            observed_usage={"elapsed_seconds": 0, "sessions": 0,
                            "total_tokens": 0, "uncached_input_tokens": 0},
            predecessor_fingerprint=None,
        )


def test_delta_handoff_rejects_untyped_or_wrong_kind_references() -> None:
    for evidence in (
            {"evidence_id": "native-usage", "fingerprint": "d" * 64},
            _reference("decision", "d" * 64),
            _reference("evidence", "d" * 63),
            {**_reference("evidence", "d" * 64), "bytes": {"prompt": "x"}},
    ):
        with pytest.raises(brief_projection.BriefProjectionError):
            brief_projection.stage_delta_handoff(
                source_sha="a" * 40, requirement_id="R-0013",
                active_contracts=["contract:x"],
                acceptance_outcomes=["AC6"],
                new_evidence=[evidence], unresolved_decisions=[],
                outstanding_native_set=None,
                observed_usage={"elapsed_seconds": 0, "sessions": 0,
                                "total_tokens": 0,
                                "uncached_input_tokens": 0},
                predecessor_fingerprint=None,
            )


def test_second_fix_evaluate_failure_requires_human_decision() -> None:
    first = dispatch_telemetry.fix_evaluate_cycle_decision(
        1, source_sha="a" * 40, task_id="T06", current_stage="evaluate")
    second = dispatch_telemetry.fix_evaluate_cycle_decision(
        2, source_sha="a" * 40, task_id="T06", current_stage="evaluate")

    assert first["dispatch_allowed"] is True
    assert first["decision_required"] is None
    assert second["dispatch_allowed"] is False
    assert second["status"] == "human_scope_review"
    assert second["decision_required"] == "human architecture or scope decision"
    assert set(second["actions"]) == {
        "architecture-review", "reduce-scope", "end-wave",
    }
    assert second["fingerprint"]
