"""R-0001 delta brief and binding wave-budget behavior."""

from __future__ import annotations

import copy
import contextlib
import json

import pytest

from taskplane import brief_projection, dispatch_telemetry, loop, spend, tp
from taskplane.delivery_ports import FakeClock


def _loop_action(*, marker: str = "old") -> dict:
    return {
        "step": "execute",
        "role": "tp-executor",
        "task": {"id": "t12", "status": "running"},
        "instruction": "execute the current task",
        "contract": {"scope": ["taskplane/brief_projection.py"]},
        "knowledge": {
            "current_state": "unchanged " * 12_000,
            "marker": marker,
        },
        "design": {"fingerprint": "d" * 64, "body": "stable " * 12_000},
        "new_evidence": {"event": marker},
    }


def test_loop_next_delta_projection_is_under_4000_tokens(tmp_path) -> None:
    previous = _loop_action()
    current = copy.deepcopy(previous)
    current["new_evidence"] = {"event": "checkpoint-complete"}

    result = brief_projection.project(current, previous=previous)

    assert result["schema"] == "taskplane.loop-next-delta/v1"
    assert result["status"] == "ready"
    assert result["step"] == "execute"
    assert result["current_action"]["step"] == "execute"
    assert result["new_evidence"] == {
        "new_evidence": {"event": "checkpoint-complete"}
    }
    assert set(result["unchanged_refs"]) == {"design", "knowledge"}
    assert all(
        ref["schema"] == "taskplane.content-reference/v1"
        and len(ref["fingerprint"]) == 64
        for ref in result["unchanged_refs"].values()
    )
    assert "unchanged unchanged" not in brief_projection.canonical_text(result)
    assert result["measurement"] == brief_projection.measure(result)
    assert result["measurement"]["token_upper_bound"] < 4_000

    tampered = copy.deepcopy(previous)
    tampered["design"]["body"] += "tampered"
    changed_ref = brief_projection.project(
        current, previous=tampered
    )["unchanged_refs"]
    assert "design" not in changed_ref

    oversized = copy.deepcopy(current)
    oversized["new_evidence"] = {"event": "new " * 20_000}
    refused = brief_projection.project(oversized, previous=previous)
    assert refused["schema"] == "taskplane.loop-next-delta-refusal/v1"
    assert refused["status"] == "refused"
    assert refused["reason"] == "brief_token_budget_exceeded"
    assert refused["current_action"] == result["current_action"]
    assert refused["artifact_ref"]["fingerprint"]
    assert refused["measurement"] == brief_projection.measure(refused)
    assert refused["measurement"]["token_upper_bound"] < 4_000

    # W18 is a production edge, not only a projection unit: the loop adapter
    # persists the exact source bytes and gives the CLI a resolvable bounded
    # delta.  A second action references unchanged content from that store.
    first_runtime = loop.project_next_action_for_host(
        str(tmp_path), previous, wave_usage={
            "elapsed_seconds": 0,
            "sessions": 0,
            "total_tokens": 0,
            "uncached_input_tokens": 0,
        },
    )
    second_runtime = loop.project_next_action_for_host(
        str(tmp_path), current, wave_usage={
            "elapsed_seconds": 1,
            "sessions": 1,
            "total_tokens": 1,
            "uncached_input_tokens": 1,
        },
    )
    assert first_runtime["measurement"]["token_upper_bound"] < 4_000
    assert second_runtime["measurement"]["token_upper_bound"] < 4_000
    assert set(second_runtime["unchanged_refs"]) == {"design", "knowledge"}
    for reference in second_runtime["unchanged_refs"].values():
        source = tmp_path / reference["artifact"]
        assert source.is_file()
        assert json.loads(source.read_text())[reference["field"]]

    # Production actions can carry an authority-heavy Plan instruction.  The
    # bounded host delta keeps its step inline and resolves the large field
    # through the exact persisted source rather than failing the transition.
    large_runtime = loop.project_next_action_for_host(
        str(tmp_path), {
            "step": "plan",
            "instruction": "plan authority " * 2_000,
            "contract": {"body": "contract authority " * 2_000},
        }, wave_usage={
            "elapsed_seconds": 2,
            "sessions": 1,
            "total_tokens": 1,
            "uncached_input_tokens": 1,
        },
    )
    assert large_runtime["schema"] == "taskplane.loop-next-delta/v1"
    assert large_runtime["status"] == "ready"
    assert large_runtime["measurement"]["token_upper_bound"] < 4_000
    instruction_ref = large_runtime["current_action"]["instruction"]
    assert instruction_ref["schema"] == "taskplane.content-reference/v1"
    assert json.loads(
        (tmp_path / instruction_ref["artifact"]).read_text()
    )[instruction_ref["field"]].startswith("plan authority")


def test_delta_projection_preserves_existing_stage_task_path() -> None:
    stage_action = {
        "step": "evaluate",
        "instruction": "evaluate the task",
        "task": {"id": "t1", "workspace": "/tmp/t1"},
    }

    assert tp._should_project_loop_next("next", stage_action) is False
    # R-0004 predates structured task identities.  Its fail-open task rail
    # also covers legacy stage payloads that name only the stage and brief;
    # the R-0001 projection must not reshape those exact bytes.
    assert tp._should_project_loop_next(
        "next", {"step": "evaluate", "brief": "unchanged"}
    ) is False
    assert tp._should_project_loop_next(
        "next", {"step": "fix", "brief": "unchanged"}
    ) is False
    assert tp._should_project_loop_next(
        "next", {"step": "plan", "instruction": "prepare the plan"}
    ) is True


@pytest.mark.parametrize(
    ("field", "ceiling"),
    [
        ("elapsed_seconds", 28_800),
        ("sessions", 60),
    ],
)
def test_wave_level_time_or_session_ceiling_stops_for_human_scope_review(
    field: str, ceiling: int
) -> None:
    below = {
        "elapsed_seconds": 0,
        "sessions": 0,
        "total_tokens": 0,
        "uncached_input_tokens": 0,
    }
    below[field] = ceiling - 1
    allowed = brief_projection.project(_loop_action(), wave_usage=below)
    assert allowed["status"] == "ready"
    assert allowed["budget"]["dispatch_allowed"] is True

    at_ceiling = dict(below)
    at_ceiling[field] = ceiling
    stopped = brief_projection.project(_loop_action(), wave_usage=at_ceiling)
    assert stopped["status"] == "human_scope_review"
    assert stopped["current_action"] == {
        "step": "human_scope_review",
        "paused": True,
        "dispatch_allowed": False,
        "reason": "binding_wave_budget_reached",
    }
    assert stopped["budget"]["dispatch_allowed"] is False
    assert stopped["budget"]["triggered"] == [
        {"field": field, "observed": ceiling, "ceiling": ceiling}
    ]
    assert stopped["measurement"]["token_upper_bound"] < 4_000

    with pytest.raises(
        brief_projection.BriefProjectionError,
        match="wave_usage is binding and must contain",
    ):
        brief_projection.project(_loop_action(), wave_usage={field: ceiling})

    ledger = dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=0,
    )
    owner_stop = dispatch_telemetry.budget_projection(
        ledger, FakeClock(wall_time=at_ceiling["elapsed_seconds"]),
        overrides={key: value for key, value in at_ceiling.items()
                   if key != "elapsed_seconds"},
    )
    assert owner_stop["dispatch_allowed"] is False
    assert owner_stop["status"] == "human_scope_review"


@pytest.mark.parametrize(
    ("field", "observed"),
    [("total_tokens", 1_000_000_000),
     ("uncached_input_tokens", 100_000_000)],
)
def test_aggregate_tokens_remain_visible_without_program_level_stop(
        field: str, observed: int) -> None:
    usage = {"elapsed_seconds": 0, "sessions": 10,
             "total_tokens": 0, "uncached_input_tokens": 0}
    usage[field] = observed

    projected = brief_projection.project(_loop_action(), wave_usage=usage)

    assert projected["status"] == "ready"
    assert projected["budget"]["dispatch_allowed"] is True
    assert projected["budget"]["usage"][field] == observed
    assert projected["budget"]["triggered"] == []


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_dispatch_telemetry_rejects_non_finite_time_boundaries(value: float) -> None:
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match="finite"):
        dispatch_telemetry.new_ledger(
            run_id="run", source_sha="a" * 40,
            design_fingerprint="design", plan_fingerprint="plan",
            started_at=value,
        )

    ledger = dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=0,
    )
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match="finite"):
        dispatch_telemetry.budget_projection(
            ledger, FakeClock(wall_time=value),
        )
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match="finite"):
        dispatch_telemetry.dispatch_event(
            dispatch_id="dispatch", thread_id="thread", thread_type="worker",
            task_id="task", sequence=1, kind="complete", at=value,
        )


@pytest.mark.parametrize("field", dispatch_telemetry.WAVE_BUDGET_CEILINGS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_budget_projection_rejects_non_finite_override_matrix(
    field: str, value: float,
) -> None:
    ledger = dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=0,
    )

    with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match="finite"):
        dispatch_telemetry.budget_projection(
            ledger, FakeClock(wall_time=0), overrides={field: value},
        )


@pytest.mark.parametrize("field", dispatch_telemetry.WAVE_BUDGET_CEILINGS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_brief_projection_rejects_non_finite_wave_usage_matrix(
    field: str, value: float,
) -> None:
    usage = {
        "elapsed_seconds": 0,
        "sessions": 0,
        "total_tokens": 0,
        "uncached_input_tokens": 0,
    }
    usage[field] = value

    with pytest.raises(brief_projection.BriefProjectionError, match="finite"):
        brief_projection.project(_loop_action(), wave_usage=usage)


def test_dispatch_telemetry_records_all_required_fields_and_thread_types() -> None:
    ledger = dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=10,
    )
    clock = FakeClock(wall_time=20)
    for number, thread_type in enumerate(
        ("main", "worker", "lens", "evaluator", "guardian"), start=1
    ):
        result = dispatch_telemetry.admit(
            ledger,
            {
                "dispatch_id": f"dispatch-{number}",
                "thread_id": f"thread-{number}",
                "thread_type": thread_type,
                "task_id": f"task-{number}",
                "dependencies": [],
                "shared_owner": None,
                "started_at": 10 + number,
                "ended_at": 11 + number,
                "wait_duration_seconds": number,
                "correction_count": number - 1,
                "events": [{"kind": "complete", "sequence": 1}],
            },
            {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "uncached_input_tokens": 60,
                "output_tokens": 20,
                "reasoning_tokens": 5,
                "total_tokens": 120,
            },
            clock,
        )
        assert result["status"] == "admitted"

    required = {
        "dispatch_id", "thread_id", "thread_type", "task_id",
        "dependencies", "shared_owner", "input_tokens",
        "cached_input_tokens", "uncached_input_tokens", "output_tokens",
        "reasoning_tokens", "total_tokens", "started_at", "ended_at",
        "duration_seconds", "wait_duration_seconds", "correction_count",
        "events", "fingerprint",
    }
    assert {row["thread_type"] for row in ledger["dispatches"]} == {
        "main", "worker", "lens", "evaluator", "guardian"
    }
    assert all(required <= set(row) for row in ledger["dispatches"])
    assert dispatch_telemetry.wave_usage(ledger, clock) == {
        "elapsed_seconds": 10,
        "sessions": 5,
        "total_tokens": 600,
        "uncached_input_tokens": 300,
    }

    # The existing spend adapter supplies the owner with normalized provider
    # usage rather than making the telemetry owner parse host transcripts.
    assert spend.dispatch_usage({
        "schema": spend.USAGE_SCHEMA,
        "provider": "codex", "available": True, "reason": None,
        "uncached_input_tokens": 60, "cached_input_tokens": 40,
        "cache_creation_tokens": 0, "output_tokens": 20,
        "raw_total_tokens": 120, "effective_tokens": 164,
        "effective": 164,
    }) == {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "uncached_input_tokens": 60,
        "output_tokens": 20,
        "reasoning_tokens": 0,
        "total_tokens": 120,
    }


@pytest.mark.parametrize(
    ("cached", "uncached", "total", "trigger"),
    [
        (150_000_000, 0, 150_000_000, "total_tokens"),
        (0, 25_000_000, 25_000_000, "uncached_input_tokens"),
    ],
)
def test_observed_aggregate_tokens_do_not_stop_the_next_live_wave(
    tmp_path, monkeypatch, cached: int, uncached: int, total: int,
    trigger: str,
) -> None:
    ledger = dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=0,
    )
    dispatch_telemetry.bind_dispatch(
        ledger,
        {
            "dispatch_id": "dispatch-a", "thread_id": "thread-a",
            "thread_type": "worker", "task_id": "a",
            "dependencies": [], "shared_owner": None,
            "started_at": 1, "ended_at": 1,
            "wait_duration_seconds": 0, "correction_count": 0,
            "events": [],
        },
    )
    state = {
        "run_id": "run",
        "parallel": True, "step": "execute", "goal": "budget",
        "tasks": [{"id": "a", "status": "pending", "deps": [],
                   "scope": ["src/a.py"], "tests": "true"}],
        "dispatch_telemetry": ledger,
    }

    @contextlib.contextmanager
    def mutate(_ws):
        # Real load/mutate decode separate persisted snapshots. Keep the
        # telemetry fixture's store distinct from a caller's working copy.
        fresh = copy.deepcopy(state)
        yield fresh
        state.clear()
        state.update(fresh)

    monkeypatch.setattr(loop, "mutate", mutate)
    monkeypatch.setattr(loop, "load", lambda _ws: copy.deepcopy(state))
    monkeypatch.setattr(loop, "_stage_loop_mutation_refusal", lambda _ws: None)
    monkeypatch.setattr(loop, "_validated_delivery_mode", lambda _state: None)
    monkeypatch.setattr(loop, "SystemClock", lambda: FakeClock(wall_time=2))

    observed = {
        "schema": spend.USAGE_SCHEMA, "provider": "codex",
        "available": True, "reason": None,
        "uncached_input_tokens": uncached,
        "cached_input_tokens": cached,
        "cache_creation_tokens": 0, "output_tokens": 0,
        "raw_total_tokens": total, "effective_tokens": total,
    }
    loop.record_observed_dispatch_usage(
        str(tmp_path), task_id="a", normalized_usage=observed,
        source=str(tmp_path / "worker.jsonl"),
    )
    admitted = loop.finalize_observed_dispatch_usage(
        str(tmp_path), task_id="a", ended_at=2)
    continued = loop.wave(str(tmp_path))

    assert admitted["status"] == "admitted"
    assert continued["step"] == "execute"
    assert continued.get("paused") is not True
    assert dispatch_telemetry.wave_usage(
        state["dispatch_telemetry"], FakeClock(wall_time=2)
    )[trigger] == dispatch_telemetry.WAVE_BUDGET_CEILINGS[trigger]
