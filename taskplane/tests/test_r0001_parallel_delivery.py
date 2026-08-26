import contextlib
import copy
import json
from pathlib import Path
import threading

import pytest

from taskplane import (
    build_c,
    command_runtime,
    dispatch_telemetry,
    loop,
    progress,
    retro,
    tp as cli,
)
from taskplane.delivery_ports import (
    content_fingerprint,
    DeliveryPortError,
    FakeClock,
    RecordedEventWaiter,
    RecordedTaskDispatchCapabilityFactory,
    SandboxEvidenceStore,
)
from taskplane.delivery_policy import validate_plan_mode
from taskplane.plan_topology import (
    ClosedTaskDispatchCapabilityFactory,
    ExecutionDagRevisionStore,
    PlanTopologyError,
    admit_ready_batch,
    append_replan_generation,
    classify_plan,
    execution_metrics,
    new_scheduler_state,
    record_worker_event,
    wait_for_worker_events,
)


def _task(task_id, *, deps=(), scope=(), tests="", long_worker=False):
    return {
        "id": task_id,
        "deps": list(deps),
        "scope": list(scope),
        "tests": tests,
        "long_worker": long_worker,
        "allowed_tools": ["read", "test"],
        "allowed_git_refs": [f"refs/heads/{task_id}"],
    }


def _host_capacity_receipt(*, run_id="run", source_sha="a" * 40,
                           plan_fingerprint="b" * 64, issued_at=0,
                           expires_at=100, concurrency=2, max_in_flight=2):
    material = {
        "schema": "taskplane.scheduler-host-capability/v1",
        "run_id": run_id,
        "source_sha": source_sha,
        "plan_fingerprint": plan_fingerprint,
        "configured_host_concurrency": concurrency,
        "max_in_flight": max_in_flight,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "cryptographic_authenticity_claimed": False,
    }
    return {**material, "fingerprint": content_fingerprint(material)}


def _capacity_loop_state():
    delivery_receipt = validate_plan_mode(
        {
            "requirement": "R-0001", "delivery_mode": "build",
            "automatic_lenses": [], "plan_authority": "human:operator",
        },
        plan_fingerprint="b" * 64, source_sha="a" * 40,
    )
    return {
        "goal": "capacity authority", "parallel": True, "step": "execute",
        "run_id": "run", "baseline": "a" * 40,
        "requirement_id": "R-0001",
        "design_fingerprint": "d" * 64,
        "delivery_mode_receipt": delivery_receipt,
        "tasks": [{**_task("a", scope=("src/a.py",)), "status": "pending"}],
    }


def _trusted_parallel_loop_state(*, overlap=False, evidence_store=None):
    tasks = [
        _task("t17a", scope=("exports/t17/shared" if overlap else
                              "exports/t17/a",)),
        _task("t17b", scope=("exports/t17/shared" if overlap else
                              "exports/t17/b",)),
        _task("t17c", scope=("exports/t17/shared" if overlap else
                              "exports/t17/c",)),
        _task("t18", deps=("t17a", "t17b", "t17c"),
              scope=("exports/t18",)),
    ]
    state = _capacity_loop_state()
    state["design_fingerprint"] = "d" * 64
    state["tasks"] = [
        {**task, "status": "running" if task["id"] == "t17a" else
         "pending"}
        for task in tasks
    ]
    receipt = _host_capacity_receipt(
        concurrency=1, max_in_flight=1, issued_at=0, expires_at=5,
    )
    scheduler = new_scheduler_state(
        tasks, run_id="run", source_sha="a" * 40,
        design_fingerprint="d" * 64, plan_fingerprint="b" * 64,
        stage="Execute", repository_files=set(),
    )
    admitted = admit_ready_batch(
        scheduler, {"configured_host_concurrency": 1},
        {"max_in_flight": 1, "session_limit": 60}, evidence_store,
        FakeClock(wall_time=1),
        capability_factory=RecordedTaskDispatchCapabilityFactory(),
    )
    assert admitted["dispatch_set"]["members"] == ["t17a"]
    scheduler["capacity_binding"] = {
        "schema": "taskplane.scheduler-capacity-binding/v1",
        "authority": "validated-host-receipt",
        "host_capability_fingerprint": receipt["fingerprint"],
        "configured_host_concurrency": 1,
        "max_in_flight": 1,
        "session_limit": 60,
    }
    state["scheduler_host_capability_receipt"] = receipt
    state["performance_scheduler"] = scheduler
    return state


def _rehash_reservation(state, index=0):
    scheduler = state["performance_scheduler"]
    reservation = scheduler["reservations"][index]
    material_fields = {
        "schema", "run_id", "source_sha", "design_fingerprint",
        "plan_fingerprint", "stage", "scheduler_revision",
        "topology_fingerprint", "members",
    }
    old = reservation["reservation_fingerprint"]
    new = content_fingerprint({field: reservation[field]
                               for field in material_fields})
    reservation["reservation_fingerprint"] = new
    for assignment in reservation["assignments"]:
        assignment["reservation_fingerprint"] = new
        capability = assignment["capability"]
        capability["reservation_fingerprint"] = new
        capability["capability_id"] = content_fingerprint({
            field: value for field, value in capability.items()
            if field != "capability_id"
        })
    for task_id, fingerprint in list(scheduler["in_flight"].items()):
        if fingerprint == old:
            scheduler["in_flight"][task_id] = new


def _rehash_operator_assertion(assertion):
    assertion["fingerprint"] = content_fingerprint({
        field: value for field, value in assertion.items()
        if field != "fingerprint"
    })


def _state(tasks, **overrides):
    values = dict(
        run_id="run",
        source_sha="a" * 40,
        design_fingerprint="design",
        plan_fingerprint="plan",
        stage="Execute",
        repository_files=set(),
    )
    values.update(overrides)
    return new_scheduler_state(tasks, **values)


def _admit(state, *, concurrency=8, max_in_flight=8, session_limit=60):
    return admit_ready_batch(
        state,
        {"configured_host_concurrency": concurrency},
        {"max_in_flight": max_in_flight, "session_limit": session_limit},
        None,
        FakeClock(wall_time=100),
        capability_factory=RecordedTaskDispatchCapabilityFactory(),
    )


def _pair(topology, left, right):
    key = tuple(sorted((left, right)))
    return next(
        row for row in topology["pairs"]
        if tuple(sorted((row["left"], row["right"]))) == key
    )


def test_plan_classifies_every_task_pair():
    tasks = [
        _task("a", scope=("src/a.py",)),
        _task("b", scope=("src/b.py",)),
        _task("c", deps=("a",), scope=("src/c.py",)),
        _task("d", scope=("src/d.py",)),
    ]
    topology = classify_plan(tasks, repository_files=set())

    assert len(topology["pairs"]) == 6
    assert {row["disposition"] for row in topology["pairs"]} == {
        "parallel", "serialized"
    }
    assert _pair(topology, "a", "c")["shared_owner"] == "dependency:a"
    assert _pair(topology, "b", "d")["disposition"] == "parallel"


def test_missing_owned_test_assets_serialize_false_ready_consumer():
    tasks = [
        _task(
            "t12", scope=("taskplane/tests/test_r0001_wave_budgets.py",),
        ),
        _task(
            "t13", scope=("taskplane/tests/test_r0001_parallel_delivery.py",),
        ),
        _task(
            "t14", scope=("taskplane/dispatch_telemetry.py",),
            tests=("python3 -m pytest -q "
                   "taskplane/tests/test_r0001_wave_budgets.py "
                   "taskplane/tests/test_r0001_parallel_delivery.py"),
        ),
    ]
    topology = classify_plan(tasks, repository_files=set())

    assert _pair(topology, "t12", "t13")["disposition"] == "parallel"
    assert _pair(topology, "t12", "t14") == {
        "left": "t12", "right": "t14", "disposition": "serialized",
        "shared_owner": "test-artifact:taskplane/tests/test_r0001_wave_budgets.py",
    }
    assert _pair(topology, "t13", "t14")["shared_owner"] == (
        "test-artifact:taskplane/tests/test_r0001_parallel_delivery.py"
    )
    assert topology["effective_dependencies"]["t14"] == ["t12", "t13"]


def test_direct_assignment_dispatches_all_ready_disjoint_tasks_simultaneously():
    state = _state([
        _task("a", scope=("src/a.py",)),
        _task("b", scope=("src/b.py",)),
        _task("c", scope=("src/c.py",)),
    ])

    result = _admit(state)

    assert result["status"] == "admitted"
    assert result["dispatch_set"] == {
        "schema": "taskplane.direct-assignment-set/v1",
        "concurrent": True,
        "members": ["a", "b", "c"],
        "member_count": 3,
    }
    assert {row["task_id"] for row in result["assignments"]} == {"a", "b", "c"}
    assert result["overflow_ready"] == []


def test_direct_assignment_maximizes_disjoint_ready_tasks_within_capacity():
    state = _state([
        _task("a-center", scope=("src",)),
        _task("b-left", scope=("src/left.py",)),
        _task("c-right", scope=("src/right.py",)),
    ])

    result = _admit(state, concurrency=2, max_in_flight=2)

    assert result["dispatch_set"]["members"] == ["b-left", "c-right"]
    assert result["overflow_ready"] == ["a-center"]


def test_shared_owner_serializes_with_named_reason():
    topology = classify_plan([
        _task("a", scope=("src/shared.py",)),
        _task("b", scope=("src/shared.py",)),
    ], repository_files=set())
    state = _state([
        _task("a", scope=("src/shared.py",)),
        _task("b", scope=("src/shared.py",)),
    ])

    assert _pair(topology, "a", "b")["shared_owner"] == "scope:src/shared.py"
    assert len(_admit(state)["assignments"]) == 1


def test_long_workers_emit_events_and_ready_work_never_idles():
    state = _state([
        _task("long", scope=("src/long.py",), long_worker=True),
        _task("next", deps=("long",), scope=("src/next.py",)),
    ])
    first = _admit(state, concurrency=1, max_in_flight=1)
    assert first["assignments"][0]["event_contract"]["required_for_long_worker"] == [
        "progress", "terminal"
    ]

    progress = record_worker_event(
        state, {"event_id": "long-1", "task_id": "long", "sequence": 1,
                "kind": "progress", "at": 110},
    )
    terminal = record_worker_event(
        state, {"event_id": "long-2", "task_id": "long", "sequence": 2,
                "kind": "complete", "at": 120},
        host_capability={"configured_host_concurrency": 1},
        budget={"max_in_flight": 1, "session_limit": 60},
        clock=FakeClock(wall_time=120),
        capability_factory=RecordedTaskDispatchCapabilityFactory(),
    )

    assert progress["status"] == "recorded"
    assert terminal["admission"]["dispatch_set"]["members"] == ["next"]
    assert state["scheduler_caused_idle_seconds"] == 0


def test_long_worker_complete_without_progress_fails_without_mutation():
    state = _state([_task("long", long_worker=True)])
    _admit(state, concurrency=1, max_in_flight=1)

    with pytest.raises(PlanTopologyError, match="prior progress"):
        record_worker_event(state, {
            "event_id": "long-1", "task_id": "long", "sequence": 1,
            "kind": "complete", "at": 2,
        })

    assert state["events"] == []
    assert state["statuses"]["long"] == "in_flight"


def test_non_positive_worker_sequence_fails_closed_before_valid_reconciliation():
    factory = RecordedTaskDispatchCapabilityFactory()
    state = _state([
        _task("a", scope=("a",)),
        _task("b", deps=("a",), scope=("b",)),
    ])
    _admit(state, concurrency=1, max_in_flight=1)
    before = copy.deepcopy(state)
    admission = {
        "host_capability": {"configured_host_concurrency": 1},
        "budget": {"max_in_flight": 1, "session_limit": 60},
        "clock": FakeClock(wall_time=2),
        "capability_factory": factory,
    }

    for sequence in (0, -1):
        with pytest.raises(PlanTopologyError, match="at least 1"):
            record_worker_event(
                state,
                {"event_id": f"a-{sequence}", "task_id": "a",
                 "sequence": sequence, "kind": "progress", "at": 1},
                **admission,
            )
        assert state == before

    progress = record_worker_event(
        state, {"event_id": "a-1", "task_id": "a", "sequence": 1,
                "kind": "progress", "at": 1},
        **admission,
    )
    complete_event = {
        "event_id": "a-2", "task_id": "a", "sequence": 2,
        "kind": "complete", "at": 2,
    }
    terminal = record_worker_event(state, complete_event, **admission)

    assert progress["terminal"] is False
    assert terminal["terminal"] is True
    assert terminal["admission"]["dispatch_set"]["members"] == ["b"]
    assert state["events"] == [
        {"event_id": "a-1", "task_id": "a", "sequence": 1,
         "kind": "progress", "at": 1},
        complete_event,
    ]
    assert state["statuses"] == {"a": "complete", "b": "in_flight"}
    assert len(state["reservations"]) == 2

    terminalized = copy.deepcopy(state)
    duplicate = record_worker_event(state, complete_event, **admission)
    assert duplicate == {
        "schema": "taskplane.worker-event-result/v1",
        "status": "duplicate",
        "terminal": True,
    }
    assert state == terminalized


@pytest.mark.parametrize(
    "at",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_non_finite_worker_event_time_fails_without_any_state_mutation(at):
    state = _state([
        _task("a", scope=("a",)),
        _task("b", deps=("a",), scope=("b",)),
    ])
    _admit(state, concurrency=1, max_in_flight=1)
    before = copy.deepcopy(state)

    with pytest.raises(PlanTopologyError, match="finite"):
        record_worker_event(
            state,
            {"event_id": "a-1", "task_id": "a", "sequence": 1,
             "kind": "complete", "at": at},
            host_capability={"configured_host_concurrency": 1},
            budget={"max_in_flight": 1, "session_limit": 60},
            clock=FakeClock(wall_time=2),
            capability_factory=RecordedTaskDispatchCapabilityFactory(),
        )

    assert state == before
    assert execution_metrics(state) == {
        "schema": "taskplane.execution-metrics/v1",
        "active_worker_seconds": 0,
        "delivery_wall_seconds": 0,
        "parallelism_factor": 0,
        "longest_serial_chain": {"tasks": [], "seconds": 0},
        "scheduler_caused_idle_seconds": 0.0,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("start", float("nan"), id="start-nan"),
        pytest.param("terminal", float("inf"), id="terminal-positive-infinity"),
        pytest.param("terminal", float("-inf"), id="terminal-negative-infinity"),
    ],
)
def test_execution_metrics_fail_closed_on_non_finite_authoritative_time(
    field, value,
):
    state = _state([_task("a", scope=("a",))])
    state["task_times"] = {"a": {"start": 0, "terminal": 1}}
    state["task_times"]["a"][field] = value

    with pytest.raises(PlanTopologyError, match="finite"):
        execution_metrics(state)
    with pytest.raises(PlanTopologyError, match="finite"):
        retro.performance_projection(state)


def test_retro_reports_parallelism_factor_and_longest_serial_chain():
    state = _state([
        _task("a", scope=("a",)),
        _task("b", scope=("b",)),
        _task("c", deps=("a",), scope=("c",)),
    ])
    state["task_times"] = {
        "a": {"start": 0, "terminal": 10},
        "b": {"start": 0, "terminal": 10},
        "c": {"start": 10, "terminal": 20},
    }

    metrics = execution_metrics(state)

    assert metrics["parallelism_factor"] == 1.5
    assert metrics["delivery_wall_seconds"] == 20
    assert metrics["longest_serial_chain"] == {"tasks": ["a", "c"], "seconds": 20}
    assert retro.performance_projection(state) == metrics


def test_verification_fanout_and_terminal_full_matrix():
    tasks = [
        _task("v-a", deps=("runtime",), scope=("exports/a",)),
        _task("v-b", deps=("runtime",), scope=("exports/b",)),
        _task("v-c", deps=("runtime",), scope=("exports/c",)),
        _task("full", deps=("v-a", "v-b", "v-c"), scope=("exports/full",)),
        _task("runtime", scope=("src/runtime",)),
    ]
    state = _state(tasks, statuses={"runtime": "complete"})
    fanout = _admit(state)
    assert fanout["dispatch_set"]["members"] == ["v-a", "v-b", "v-c"]
    assert "full" in fanout["held"]


def test_event_wait_is_1800_seconds_idempotent_and_wakes_on_terminal_event():
    state = _state([_task("a", long_worker=True)])
    _admit(state, concurrency=1, max_in_flight=1)
    clock = FakeClock(wall_time=100)
    waiter = RecordedEventWaiter([[
        {"event_id": "a-1", "task_id": "a", "sequence": 1,
         "kind": "progress", "at": 101},
        {"event_id": "a-2", "task_id": "a", "sequence": 2,
         "kind": "complete", "at": 102},
    ]], clock)

    result = wait_for_worker_events(state, waiter, clock=clock)

    assert waiter.invocations == [(
        {"mode": "event", "timeout_seconds": 1800, "scheduled_polling": False},
        ("a",),
    )]
    assert result["terminal"] is True
    assert state["statuses"]["a"] == "complete"


def test_duplicate_and_out_of_order_events_reconcile_without_double_count():
    state = _state([_task("a")])
    _admit(state, concurrency=1, max_in_flight=1)
    second = {"event_id": "a-2", "task_id": "a", "sequence": 2,
              "kind": "complete", "at": 2}
    first = {"event_id": "a-1", "task_id": "a", "sequence": 1,
             "kind": "progress", "at": 1}

    assert record_worker_event(state, second)["status"] == "recorded"
    assert record_worker_event(state, first)["status"] == "recorded"
    assert record_worker_event(state, second)["status"] == "duplicate"
    assert len(state["events"]) == 2


def test_same_task_sequence_new_id_is_idempotent_and_conflicts_fail_closed():
    state = _state([_task("a")])
    _admit(state, concurrency=1, max_in_flight=1)
    first = {"event_id": "a-1", "task_id": "a", "sequence": 1,
             "kind": "progress", "at": 1}
    replay = {**first, "event_id": "a-stale"}

    assert record_worker_event(state, first)["status"] == "recorded"
    assert record_worker_event(state, replay)["status"] == "duplicate"
    assert state["events"] == [first]

    before_conflict = copy.deepcopy(state)
    with pytest.raises(PlanTopologyError, match="task sequence collision"):
        record_worker_event(state, {**replay, "kind": "attention"})
    assert state == before_conflict

    terminal = record_worker_event(
        state, {"event_id": "a-2", "task_id": "a", "sequence": 2,
                "kind": "complete", "at": 2},
    )
    assert terminal["terminal"] is True
    assert state["statuses"]["a"] == "complete"
    assert len(state["events"]) == 2


def test_gapped_terminal_event_waits_for_contiguous_reconciliation():
    factory = RecordedTaskDispatchCapabilityFactory()
    state = _state([
        _task("a", scope=("a",)),
        _task("b", deps=("a",), scope=("b",)),
    ])
    _admit(state, concurrency=1, max_in_flight=1)
    admission = {
        "host_capability": {"configured_host_concurrency": 1},
        "budget": {"max_in_flight": 1, "session_limit": 60},
        "clock": FakeClock(wall_time=2),
        "capability_factory": factory,
    }

    gapped = record_worker_event(
        state, {"event_id": "a-2", "task_id": "a", "sequence": 2,
                "kind": "complete", "at": 2},
        **admission,
    )

    assert gapped["terminal"] is False
    assert gapped["admission"] is None
    assert state["statuses"] == {"a": "in_flight", "b": "ready"}
    reconciled = record_worker_event(
        state, {"event_id": "a-1", "task_id": "a", "sequence": 1,
                "kind": "progress", "at": 1},
        **admission,
    )
    assert reconciled["terminal"] is True
    assert reconciled["admission"]["dispatch_set"]["members"] == ["b"]
    assert state["statuses"] == {"a": "complete", "b": "in_flight"}


def test_event_queue_cap_and_oversize_event_fail_closed():
    state = _state([_task("a")])
    state["event_queue_cap"] = 1
    record_worker_event(state, {
        "event_id": "a-1", "task_id": "a", "sequence": 1,
        "kind": "progress", "at": 1,
    })
    with pytest.raises(PlanTopologyError, match="event queue cap"):
        record_worker_event(state, {
            "event_id": "a-2", "task_id": "a", "sequence": 2,
            "kind": "progress", "at": 2,
        })
    with pytest.raises(PlanTopologyError, match="64 KiB"):
        record_worker_event(state, {
            "event_id": "huge", "task_id": "a", "sequence": 3,
            "kind": "progress", "at": 3, "detail": "x" * 66000,
        })


def test_partial_host_is_terminal_attention_not_green_or_outage():
    state = _state([_task("a")])
    _admit(state, concurrency=1, max_in_flight=1)
    result = record_worker_event(state, {
        "event_id": "a-1", "task_id": "a", "sequence": 1,
        "kind": "partial-host", "at": 2,
    })
    assert result["terminal"] is True
    assert state["statuses"]["a"] == "attention"
    assert state["statuses"]["a"] not in {"complete", "outage"}


def test_atomic_batch_admission_caps_and_preserves_overflow_ready():
    state = _state([_task(letter, scope=(letter,)) for letter in "abcde"])
    result = _admit(state, concurrency=3, max_in_flight=2)
    assert result["dispatch_set"]["members"] == ["a", "b"]
    assert result["overflow_ready"] == ["c", "d", "e"]
    assert [state["statuses"][item] for item in "cde"] == ["ready"] * 3


def test_session_59_admits_one_and_stops_remaining():
    state = _state([_task("a"), _task("b")], sessions_admitted=59)
    result = _admit(state)
    assert result["dispatch_set"]["members"] == ["a"]
    assert result["overflow_ready"] == ["b"]
    assert state["sessions_admitted"] == 60


def test_capacity_exhaustion_creates_no_reservation_and_human_stop():
    state = _state([_task("a")], sessions_admitted=60)
    result = _admit(state)
    assert result["status"] == "stop_for_human_scope_review"
    assert result["reservation_fingerprint"] is None
    assert state["reservations"] == []


def test_concurrent_reservation_race_has_one_winner():
    state = _state([_task("a")])
    results = []
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        results.append(_admit(state, concurrency=1, max_in_flight=1))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result["status"] == "admitted" for result in results) == 1
    assert sum(len(result.get("assignments", [])) for result in results) == 1


def test_huge_ready_set_is_bounded_and_lossless():
    state = _state([_task(f"t-{number:04d}") for number in range(1000)])
    result = _admit(state, concurrency=4, max_in_flight=4)
    assert len(result["assignments"]) == 4
    assert len(result["overflow_ready"]) == 996
    assert len(set(result["dispatch_set"]["members"] + result["overflow_ready"])) == 1000


def test_execution_dag_remains_edge_complete_across_replans():
    state = _state([_task("a"), _task("b", deps=("a",))])
    first = state["execution_dag"]
    second = append_replan_generation(
        first,
        [_task("a"), _task("b", deps=("a",)), _task("c", deps=("b",))],
        FakeClock(wall_time=5),
    )
    assert first["generations"] == second["generations"][:1]
    assert {tuple(edge) for edge in second["edges"]} >= {
        ("g0:a", "g0:b", "dependency"),
        ("g1:a", "g1:b", "dependency"),
        ("g1:b", "g1:c", "dependency"),
        ("g0:a", "g1:a", "supersession"),
    }


def test_wide_dag_elapsed_is_critical_path_plus_orchestration_allowance_not_sum():
    tasks = [_task("root")] + [
        _task(f"leaf-{number}", deps=("root",)) for number in range(20)
    ]
    state = _state(tasks)
    state["task_times"] = {"root": {"start": 0, "terminal": 10}}
    state["task_times"].update({
        f"leaf-{number}": {"start": 11, "terminal": 21} for number in range(20)
    })
    metrics = execution_metrics(state)
    assert metrics["longest_serial_chain"]["seconds"] == 20
    assert metrics["delivery_wall_seconds"] == 21
    assert metrics["active_worker_seconds"] == 210


def test_scheduler_caused_idle_is_zero_when_ready_capacity_exists():
    state = _state([_task("a"), _task("b")])
    result = _admit(state, concurrency=1, max_in_flight=1)
    assert result["assignments"]
    assert state["scheduler_caused_idle_seconds"] == 0


def test_runtime_wave_and_build_c_consume_executable_topology():
    tasks = [
        _task("producer", scope=("tests/generated.py",)),
        _task(
            "consumer", scope=("src/consumer.py",),
            tests="python3 -m pytest -q tests/generated.py",
        ),
        _task("parallel", scope=("src/parallel.py",)),
    ]

    selected, held, topology = loop.select_ready_tasks(
        tasks, passed=set(), repository_files=set()
    )

    assert [row["id"] for row in selected] == ["parallel", "producer"]
    assert held == [{
        "task": "consumer",
        "reason": "waiting on deps: producer",
        "shared_owner": "test-artifact:tests/generated.py",
    }]
    assert topology["effective_dependencies"]["consumer"] == ["producer"]
    build_projection = build_c.executable_topology(
        tasks, repository_files=set()
    )
    assert build_projection["fingerprint"] == topology["fingerprint"]


def test_runtime_event_progress_and_command_adapters_consume_telemetry():
    state = _state([_task("a", scope=("a",))])
    state["task_times"] = {"a": {"start": 1, "terminal": 3}}
    state["statuses"]["a"] = "complete"
    state["events"] = [{
        "event_id": "a-1", "task_id": "a", "sequence": 1,
        "kind": "complete", "at": 3,
    }]

    projected = progress.scheduler_projection(state)
    assert projected["ready"] == []
    assert projected["running"] == []
    assert projected["attention"] == []
    assert projected["events"][0]["kind"] == "complete"

    command_event = command_runtime.dispatch_event({
        "schema": command_runtime.SCHEMA,
        "handle": "a" * 32,
        "revision": 2,
        "state": "succeeded",
        "created_at": 1,
        "updated_at": 3,
        "identity": {
            "schema": "taskplane.governed-command-identity/v1",
            "run_id": "run", "task_id": "a",
        },
        "wave_id": "wave",
    })
    assert command_event["schema"] == "taskplane.dispatch-event/v1"
    assert command_event["kind"] == "complete"
    assert command_event["task_id"] == "a"


@pytest.mark.parametrize("receipt_kind", [
    "absent", "malformed", "stale", "cross-run",
])
def test_production_admission_requires_current_exact_host_capacity_receipt(
    tmp_path, monkeypatch, receipt_kind,
):
    state = {
        "goal": "capacity authority", "parallel": True, "step": "execute",
        "run_id": "run", "baseline": "a" * 40,
        "design_fingerprint": "d" * 64,
        "plan_fingerprint": "b" * 64,
        "tasks": [{**_task("a", scope=("src/a.py",)), "status": "pending"}],
    }
    if receipt_kind != "absent":
        receipt = _host_capacity_receipt()
        if receipt_kind == "malformed":
            receipt = {**receipt, "configured_host_concurrency": "two"}
        elif receipt_kind == "stale":
            receipt = _host_capacity_receipt(issued_at=0, expires_at=1)
        elif receipt_kind == "cross-run":
            receipt = _host_capacity_receipt(run_id="other")
        state["scheduler_host_capability_receipt"] = receipt

    @contextlib.contextmanager
    def mutate(_ws):
        yield state

    monkeypatch.setattr(loop, "mutate", mutate)
    result = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][0]], repository_files=set(),
        clock=FakeClock(wall_time=10),
    )

    assert result["status"] == "stop_for_human_scope_review"
    assert result["reservation_fingerprint"] is None
    assert state.get("performance_scheduler", {}).get("reservations", []) == []


def _capacity_runtime(monkeypatch, *, run_id="run"):
    monkeypatch.setattr(loop, "_managed_scheduler_run_id", lambda _ws: run_id)


def test_scheduler_capacity_from_plan_mints_fixed_receipt_and_admits(
    tmp_path, monkeypatch,
):
    state = _capacity_loop_state()
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda _ws, event, **data:
                        traces.append((event, data)))

    minted = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )

    assert "error" not in minted, minted
    receipt = minted["receipt"]
    sealed = state["delivery_mode_receipt"]
    assert set(receipt) == {
        "schema", "run_id", "source_sha", "plan_fingerprint",
        "configured_host_concurrency", "max_in_flight", "issued_at",
        "expires_at", "cryptographic_authenticity_claimed", "fingerprint",
    }
    assert receipt["configured_host_concurrency"] == 1
    assert receipt["max_in_flight"] == 1
    assert receipt["issued_at"] == 10.0
    assert receipt["expires_at"] == 910.0
    assert receipt["run_id"] == "run"
    assert receipt["source_sha"] == state["baseline"]
    assert receipt["plan_fingerprint"] == sealed["plan_fingerprint"]
    assert receipt["cryptographic_authenticity_claimed"] is False
    assert loop.load(str(tmp_path))[
        "scheduler_host_capability_receipt"] == receipt
    assert "scheduler_capacity_attribution" not in loop.load(str(tmp_path))
    assert traces == [("scheduler_capacity_from_plan", {
        "source": "sealed-plan-single-worker-liveness",
        "delivery_mode_receipt_fingerprint": sealed["fingerprint"],
        "scheduler_host_capability_fingerprint": receipt["fingerprint"],
        "issued_at": 10.0, "expires_at": 910.0,
    })]

    evidence = SandboxEvidenceStore(tmp_path, "repo", "scheduler-capacity")
    dag_store = ExecutionDagRevisionStore(tmp_path)
    monkeypatch.setattr(
        loop, "_production_scheduler_evidence",
        lambda _ws, _state: (evidence, dag_store),
    )
    admitted = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][0]], repository_files=set(),
        clock=FakeClock(wall_time=20),
    )
    assert admitted["status"] == "admitted"
    assert admitted["dispatch_set"]["members"] == ["a"]


def test_scheduler_identity_prefers_sealed_plan_and_rejects_tasks_only_receipt(
    tmp_path, monkeypatch,
):
    state = _capacity_loop_state()
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    identity = loop._dispatch_telemetry_identity(str(tmp_path), state)
    assert identity["plan_fingerprint"] == \
        state["delivery_mode_receipt"]["plan_fingerprint"]

    tasks_only_state = copy.deepcopy(state)
    tasks_only_state.pop("delivery_mode_receipt")
    tasks_only = loop._dispatch_telemetry_identity(
        str(tmp_path), tasks_only_state)["plan_fingerprint"]
    assert tasks_only != identity["plan_fingerprint"]
    state["scheduler_host_capability_receipt"] = _host_capacity_receipt(
        run_id="run", source_sha=state["baseline"],
        plan_fingerprint=tasks_only, concurrency=1, max_in_flight=1,
        issued_at=0, expires_at=100,
    )
    loop.save(str(tmp_path), state)
    refused = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][0]], repository_files=set(),
        clock=FakeClock(wall_time=10),
    )
    assert refused["status"] == "stop_for_human_scope_review"
    assert "cross-run bindings" in refused["reason"]


@pytest.mark.parametrize(("mutate_state", "match"), [
    (lambda state: state.update(step="plan"), "Execute"),
    (lambda state: state.update(baseline=""), "source"),
    (lambda state: state.pop("delivery_mode_receipt"), "sealed"),
    (lambda state: state["delivery_mode_receipt"].update(mode="review"),
     "delivery-mode"),
    (lambda state: state["delivery_mode_receipt"].update(automatic_lenses=["qa"]),
     "delivery-mode"),
    (lambda state: state.update(requirement_id="R-other"), "requirement"),
])
def test_scheduler_capacity_from_plan_rejects_unsealed_or_stale_authority(
    tmp_path, monkeypatch, mutate_state, match,
):
    state = _capacity_loop_state()
    mutate_state(state)
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )

    assert match in result["error"]
    assert "scheduler_host_capability_receipt" not in loop.load(str(tmp_path))


def test_scheduler_capacity_from_plan_requires_matching_managed_run(
    tmp_path, monkeypatch,
):
    loop.save(str(tmp_path), _capacity_loop_state())
    _capacity_runtime(monkeypatch, run_id="other")
    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )
    assert "run" in result["error"]


def test_scheduler_capacity_from_plan_accepts_live_shape_without_state_run_id(
    tmp_path, monkeypatch,
):
    state = _capacity_loop_state()
    state.pop("run_id")
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )

    assert "error" not in result, result
    assert result["receipt"]["run_id"] == "run"


def test_scheduler_capacity_from_plan_rejects_sealed_source_mismatch(
    tmp_path, monkeypatch,
):
    state = _capacity_loop_state()
    state["delivery_mode_receipt"] = validate_plan_mode(
        {
            "requirement": "R-0001", "delivery_mode": "build",
            "automatic_lenses": [], "plan_authority": "human:operator",
        },
        plan_fingerprint="b" * 64, source_sha="9" * 40,
    )
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )

    assert "source" in result["error"]
    assert "scheduler_host_capability_receipt" not in loop.load(str(tmp_path))


@pytest.mark.parametrize("status", ["running", "built", "submitted"])
def test_scheduler_capacity_from_plan_refuses_in_flight_task(
    tmp_path, monkeypatch, status,
):
    state = _capacity_loop_state()
    state["tasks"][0]["status"] = status
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )
    assert "in flight" in result["error"]


def test_scheduler_capacity_from_plan_idempotency_and_idle_expired_renewal(
    tmp_path, monkeypatch,
):
    loop.save(str(tmp_path), _capacity_loop_state())
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    first = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )
    same = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=20),
    )
    assert same == {"receipt": first["receipt"], "idempotent": True}

    renewed = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=911),
    )
    assert renewed["receipt"]["issued_at"] == 911.0
    assert renewed["receipt"]["fingerprint"] != first["receipt"]["fingerprint"]


@pytest.mark.parametrize("existing", [
    _host_capacity_receipt(
        run_id="other", source_sha="a" * 40, concurrency=1,
        max_in_flight=1, issued_at=0, expires_at=100),
    _host_capacity_receipt(
        run_id="run", source_sha="a" * 40, concurrency=2,
        max_in_flight=2, issued_at=0, expires_at=1),
    _host_capacity_receipt(
        run_id="run", source_sha="a" * 40, concurrency=1,
        max_in_flight=1, issued_at=20, expires_at=100),
])
def test_scheduler_capacity_from_plan_refuses_existing_invalid_authority(
    tmp_path, monkeypatch, existing,
):
    state = _capacity_loop_state()
    state["scheduler_host_capability_receipt"] = existing
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), clock=FakeClock(wall_time=10),
    )
    assert "existing scheduler capacity authority" in result["error"]


def test_scheduler_capacity_from_plan_cli_calls_production_without_inputs(
    tmp_path, monkeypatch, capsys,
):
    import loop as loop_cli_module

    observed = []
    expected = {"receipt": {"fingerprint": "f" * 64}}
    monkeypatch.setattr(
        loop_cli_module, "scheduler_capacity_from_plan",
        lambda ws: (observed.append(ws) or expected),
    )

    rc = cli.main([
        "loop", "--workspace", str(tmp_path),
        "scheduler-capacity-from-plan",
    ])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert observed == [str(tmp_path)]


def test_scheduler_capacity_from_plan_trusted_parallel_requires_flag_and_actor(
    tmp_path, monkeypatch,
):
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    for arguments, match in [
        ({"trust_parallel": True}, "attributed"),
        ({"by": "Volodymyr Demkiv"}, "trust-parallel"),
    ]:
        state = _trusted_parallel_loop_state()
        loop.save(str(tmp_path), state)
        before = loop.load(str(tmp_path))

        result = loop.scheduler_capacity_from_plan(
            str(tmp_path), clock=FakeClock(wall_time=10), **arguments,
        )

        assert match in result["error"]
        assert loop.load(str(tmp_path)) == before


def test_scheduler_capacity_from_plan_cli_rejects_numeric_capacity(
    tmp_path,
):
    with pytest.raises(SystemExit):
        cli.main([
            "loop", "--workspace", str(tmp_path),
            "scheduler-capacity-from-plan", "--trust-parallel",
            "--by", "human:operator", "--capacity", "3",
        ])


def test_scheduler_capacity_from_plan_cli_refuses_actor_without_mode(
    tmp_path, monkeypatch, capsys,
):
    import loop as loop_cli_module

    monkeypatch.setattr(
        loop_cli_module, "scheduler_capacity_from_plan",
        lambda _ws: pytest.fail("capacity producer must not run"),
    )
    rc = cli.main([
        "loop", "--workspace", str(tmp_path),
        "scheduler-capacity-from-plan", "--by", "human:operator",
    ])

    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "--by requires --trust-parallel",
    }


def test_scheduler_capacity_from_plan_trusted_parallel_rebinds_and_admits_two(
    tmp_path, monkeypatch,
):
    state = _trusted_parallel_loop_state()
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda _ws, event, **data:
                        traces.append((event, data)))
    before_scheduler = copy.deepcopy(
        loop.load(str(tmp_path))["performance_scheduler"])

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True,
        by="Volodymyr Demkiv — explicit parallel authority",
        clock=FakeClock(wall_time=10),
    )

    assert "error" not in result, result
    updated = loop.load(str(tmp_path))
    receipt = result["receipt"]
    assert receipt["configured_host_concurrency"] == 3
    assert receipt["max_in_flight"] == 3
    assert receipt["cryptographic_authenticity_claimed"] is False
    assert updated["scheduler_host_capability_receipt"] == receipt
    scheduler = updated["performance_scheduler"]
    assert scheduler["capacity_binding"] == {
        **before_scheduler["capacity_binding"],
        "host_capability_fingerprint": receipt["fingerprint"],
        "configured_host_concurrency": 3,
        "max_in_flight": 3,
    }
    for field in ("tasks", "statuses", "in_flight", "reservations",
                  "events", "revision", "sessions_admitted"):
        assert scheduler[field] == before_scheduler[field]

    assertion = result["operator_assertion"]
    assert assertion == updated["scheduler_capacity_operator_assertions"][-1]
    material = {key: value for key, value in assertion.items()
                if key != "fingerprint"}
    assert assertion["fingerprint"] == content_fingerprint(material)
    assert assertion["actor"] == \
        "Volodymyr Demkiv — explicit parallel authority"
    assert assertion["source"] == "human-attributed-sealed-plan-parallel"
    assert assertion["run_id"] == "run"
    assert assertion["source_sha"] == "a" * 40
    assert assertion["plan_fingerprint"] == "b" * 64
    assert assertion["delivery_mode_receipt_fingerprint"] == \
        state["delivery_mode_receipt"]["fingerprint"]
    assert assertion["cryptographic_authenticity_claimed"] is False
    assert assertion["host_observation_claimed"] is False
    assert assertion["derived_tranche_members"] == ["t17a", "t17b", "t17c"]
    assert assertion["current_reservations"] == [{
        "task_id": "t17a",
        "reservation_fingerprint":
            before_scheduler["in_flight"]["t17a"],
        "capability_id": before_scheduler["reservations"][0]
            ["assignments"][0]["capability"]["capability_id"],
    }]
    assert traces[0][0] == "scheduler_capacity_from_plan"
    assert traces[0][1]["source"] == \
        "human-attributed-sealed-plan-parallel"

    evidence = SandboxEvidenceStore(tmp_path, "repo", "trusted-parallel")
    dag_store = ExecutionDagRevisionStore(tmp_path)
    monkeypatch.setattr(
        loop, "_production_scheduler_evidence",
        lambda _ws, _state: (evidence, dag_store),
    )
    admitted = loop._admit_scheduler_wave(
        str(tmp_path), [updated["tasks"][1], updated["tasks"][2]],
        repository_files=set(), clock=FakeClock(wall_time=20),
        capability_factory=RecordedTaskDispatchCapabilityFactory(),
    )
    assert admitted["status"] == "admitted"
    assert admitted["dispatch_set"]["members"] == ["t17b", "t17c"]
    assert admitted["overflow_ready"] == []


def test_scheduler_capacity_from_plan_trusted_parallel_replay_is_idempotent(
    tmp_path, monkeypatch,
):
    loop.save(str(tmp_path), _trusted_parallel_loop_state())
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    first = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )
    before = loop.load(str(tmp_path))

    replay = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=20),
    )

    assert replay == {
        "receipt": first["receipt"],
        "operator_assertion": first["operator_assertion"],
        "idempotent": True,
    }
    assert loop.load(str(tmp_path)) == before
    assert len(before["scheduler_capacity_operator_assertions"]) == 1


def test_scheduler_capacity_from_plan_trusted_parallel_replay_binds_actor(
    tmp_path, monkeypatch,
):
    loop.save(str(tmp_path), _trusted_parallel_loop_state())
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:first",
        clock=FakeClock(wall_time=10),
    )
    before = loop.load(str(tmp_path))
    trace_count = len(traces)

    replay = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:second",
        clock=FakeClock(wall_time=20),
    )

    assert "actor" in replay["error"]
    assert loop.load(str(tmp_path)) == before
    assert len(traces) == trace_count


def test_scheduler_capacity_from_plan_trusted_parallel_refuses_tampered_assertion(
    tmp_path, monkeypatch,
):
    loop.save(str(tmp_path), _trusted_parallel_loop_state())
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )
    state = loop.load(str(tmp_path))
    state["scheduler_capacity_operator_assertions"][0]["actor"] = \
        "tampered"
    loop.save(str(tmp_path), state)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=20),
    )

    assert "assertion ledger is malformed" in result["error"]
    assert loop.load(str(tmp_path)) == before


def test_scheduler_capacity_from_plan_trusted_parallel_binds_exact_loop_plan(
    tmp_path, monkeypatch,
):
    state = _trusted_parallel_loop_state()
    for task in state["tasks"]:
        if task["id"] in {"t17a", "t17b"}:
            task["scope"] = ["exports/t17/severed-shared-owner"]
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "sealed Plan" in result["error"]
    assert loop.load(str(tmp_path)) == before


def test_scheduler_capacity_from_plan_trusted_parallel_overlap_does_not_widen(
    tmp_path, monkeypatch,
):
    state = _trusted_parallel_loop_state(overlap=True)
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "disjoint concurrency" in result["error"]
    assert loop.load(str(tmp_path)) == before


def test_scheduler_capacity_from_plan_trusted_parallel_refuses_non_t17_ready(
    tmp_path, monkeypatch,
):
    state = _trusted_parallel_loop_state()
    scheduler = state["performance_scheduler"]
    for task in scheduler["tasks"]:
        if task["id"] == "t17c":
            task["id"] = "t16"
        task["deps"] = ["t16" if dep == "t17c" else dep
                        for dep in task.get("deps") or []]
    scheduler["statuses"]["t16"] = scheduler["statuses"].pop("t17c")
    scheduler["topology"] = classify_plan(
        scheduler["tasks"], repository_files=set())
    for task in state["tasks"]:
        if task["id"] == "t17c":
            task["id"] = "t16"
        task["deps"] = ["t16" if dep == "t17c" else dep
                        for dep in task.get("deps") or []]
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "T17-only" in result["error"]
    assert loop.load(str(tmp_path)) == before


@pytest.mark.parametrize("field", [
    "release_credentials_available", "irreversible_actions_allowed",
])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_capability_flags(
    tmp_path, monkeypatch, field,
):
    state = _trusted_parallel_loop_state()
    capability = state["performance_scheduler"]["reservations"][0] \
        ["assignments"][0]["capability"]
    capability[field] = True
    capability["capability_id"] = content_fingerprint({
        key: value for key, value in capability.items()
        if key != "capability_id"
    })
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "capability" in result["error"]
    assert loop.load(str(tmp_path)) == before


@pytest.mark.parametrize("fault", [
    "extra", "missing", "duplicate", "malformed-extra",
])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_assignment_set(
    tmp_path, monkeypatch, fault,
):
    state = _trusted_parallel_loop_state()
    assignments = state["performance_scheduler"]["reservations"][0] \
        ["assignments"]
    if fault == "extra":
        extra = copy.deepcopy(assignments[0])
        extra["task_id"] = "t17b"
        assignments.append(extra)
    elif fault == "missing":
        assignments.clear()
    elif fault == "duplicate":
        assignments.append(copy.deepcopy(assignments[0]))
    else:
        assignments.append({"task_id": "ghost"})
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "assignment" in result["error"]
    assert state_path.read_bytes() == before_bytes
    assert traces == []


@pytest.mark.parametrize("prior", [None, {}, "malformed", {
    "reservation_fingerprint": 7,
}])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_prior_reservations(
    tmp_path, monkeypatch, prior,
):
    state = _trusted_parallel_loop_state()
    state["performance_scheduler"]["reservations"].insert(0, prior)
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "reservation" in result["error"]
    assert state_path.read_bytes() == before_bytes
    assert traces == []


@pytest.mark.parametrize("fault", ["scheduler-head", "reservation-ordinal"])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_revision_chain(
    tmp_path, monkeypatch, fault,
):
    state = _trusted_parallel_loop_state()
    scheduler = state["performance_scheduler"]
    if fault == "scheduler-head":
        scheduler["revision"] = 99
    else:
        scheduler["reservations"][0]["scheduler_revision"] = 99
        scheduler["revision"] = 100
        _rehash_reservation(state)
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "revision" in result["error"]
    assert state_path.read_bytes() == before_bytes
    assert traces == []


@pytest.mark.parametrize("status", [{}, [], None, 7])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_status_types(
    tmp_path, monkeypatch, status,
):
    state = _trusted_parallel_loop_state()
    state["performance_scheduler"]["statuses"]["t17b"] = status
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "status" in result["error"]
    assert state_path.read_bytes() == before_bytes
    assert traces == []


def test_scheduler_capacity_from_plan_trusted_parallel_binds_evidence_store(
    tmp_path, monkeypatch,
):
    store = SandboxEvidenceStore(tmp_path, "repo", "trusted-integrity")
    state = _trusted_parallel_loop_state(evidence_store=store)
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop, "_production_scheduler_evidence",
                        lambda *_a, **_k: (store, None))
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "error" not in result, result


@pytest.mark.parametrize("fault", [
    "stale-head", "forged-receipt", "omitted-state",
])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_evidence_chain(
    tmp_path, monkeypatch, fault,
):
    store = SandboxEvidenceStore(tmp_path, "repo", "trusted-forgery")
    state = _trusted_parallel_loop_state(evidence_store=store)
    scheduler = state["performance_scheduler"]
    if fault == "stale-head":
        scheduler["evidence_head"] = "e" * 64
    elif fault == "forged-receipt":
        receipt_path = next(
            (store.path / "telemetry" / "receipts").glob("*.json"))
        receipt = json.loads(receipt_path.read_text())
        receipt["operation_id"] = "dispatch-forged"
        receipt["fingerprint"] = content_fingerprint({
            field: value for field, value in receipt.items()
            if field != "fingerprint"
        })
        forged_path = receipt_path.with_name(receipt["fingerprint"] + ".json")
        forged_path.write_text(json.dumps(receipt))
        (store.path / "telemetry" / "HEAD").write_text(
            receipt["fingerprint"] + "\n")
        (store.path / "telemetry" / "STATE").write_text(
            receipt["fingerprint"] + "\n")
        scheduler["reservations"][0]["evidence_fingerprint"] = \
            receipt["fingerprint"]
        scheduler["evidence_head"] = receipt["fingerprint"]
    else:
        scheduler["reservations"][0]["evidence_fingerprint"] = None
        scheduler["evidence_head"] = None
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    monkeypatch.setattr(loop, "_production_scheduler_evidence",
                        lambda *_a, **_k: (store, None))
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "evidence" in result["error"]
    assert state_path.read_bytes() == before_bytes
    assert traces == []


@pytest.mark.parametrize("fault", [
    "wrong-tranche", "duplicate-reservation", "missing-reservation",
    "wrong-reservation-fingerprint", "malformed-tranche-member",
])
def test_scheduler_capacity_from_plan_trusted_parallel_closes_replay_evidence(
    tmp_path, monkeypatch, fault,
):
    loop.save(str(tmp_path), _trusted_parallel_loop_state())
    _capacity_runtime(monkeypatch)
    traces = []
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k:
                        traces.append((_a, _k)))
    loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )
    state = loop.load(str(tmp_path))
    assertion = state["scheduler_capacity_operator_assertions"][0]
    if fault == "wrong-tranche":
        assertion["derived_tranche_members"][-1] = "t17x"
    elif fault == "duplicate-reservation":
        assertion["current_reservations"].append(copy.deepcopy(
            assertion["current_reservations"][0]))
    elif fault == "missing-reservation":
        assertion["current_reservations"] = []
    elif fault == "wrong-reservation-fingerprint":
        assertion["current_reservations"][0]["reservation_fingerprint"] = \
            "e" * 64
    else:
        assertion["derived_tranche_members"][0] = {}
    _rehash_operator_assertion(assertion)
    loop.save(str(tmp_path), state)
    traces.clear()
    state_path = Path(loop._loop_path(str(tmp_path)))
    before_bytes = state_path.read_bytes()

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=20),
    )

    assert "error" in result
    assert state_path.read_bytes() == before_bytes
    assert traces == []


@pytest.mark.parametrize("fault", ["malformed", "stale", "cross-run"])
def test_scheduler_capacity_from_plan_trusted_parallel_rejects_bad_reservation(
    tmp_path, monkeypatch, fault,
):
    state = _trusted_parallel_loop_state()
    scheduler = state["performance_scheduler"]
    if fault == "malformed":
        scheduler["reservations"][0]["assignments"][0]["capability"] \
            ["capability_id"] = "malformed"
    elif fault == "stale":
        scheduler["in_flight"]["t17a"] = "e" * 64
    else:
        scheduler["reservations"][0]["run_id"] = "other-run"
    loop.save(str(tmp_path), state)
    _capacity_runtime(monkeypatch)
    before = loop.load(str(tmp_path))

    result = loop.scheduler_capacity_from_plan(
        str(tmp_path), trust_parallel=True, by="human:operator",
        clock=FakeClock(wall_time=10),
    )

    assert "reservation" in result["error"]
    assert loop.load(str(tmp_path)) == before


def test_scheduler_capacity_from_plan_cli_passes_attributed_parallel_assertion(
    tmp_path, monkeypatch, capsys,
):
    import loop as loop_cli_module

    observed = []
    expected = {"receipt": {"fingerprint": "f" * 64}}

    def invoke(ws, *, trust_parallel=False, by=None):
        observed.append((ws, trust_parallel, by))
        return expected

    monkeypatch.setattr(loop_cli_module, "scheduler_capacity_from_plan", invoke)
    rc = cli.main([
        "loop", "--workspace", str(tmp_path),
        "scheduler-capacity-from-plan", "--trust-parallel",
        "--by", "Volodymyr Demkiv",
    ])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert observed == [(str(tmp_path), True, "Volodymyr Demkiv")]


def test_live_wave_with_no_host_receipt_stops_without_ready_count_inference(
    tmp_path, monkeypatch,
):
    state = {
        "goal": "no fabricated capacity", "parallel": True,
        "step": "execute", "run_id": "run", "baseline": "a" * 40,
        "design_fingerprint": "d" * 64,
        "plan_fingerprint": "b" * 64,
        "tasks": [
            {**_task("a", scope=("src/a.py",)), "status": "pending"},
            {**_task("b", scope=("src/b.py",)), "status": "pending"},
        ],
    }

    @contextlib.contextmanager
    def mutate(_ws):
        yield state

    monkeypatch.setattr(loop, "mutate", mutate)
    monkeypatch.setattr(loop, "load", lambda _ws: state)
    monkeypatch.setattr(loop, "_stage_loop_mutation_refusal", lambda _ws: None)
    monkeypatch.setattr(loop, "_validated_delivery_mode", lambda _state: None)
    monkeypatch.setattr(loop, "SystemClock", lambda: FakeClock(wall_time=10))
    result = loop.wave(str(tmp_path))

    assert result["step"] == "human_scope_review"
    assert result["wave"] == []
    assert result["scheduler_admission"]["reservation_fingerprint"] is None
    assert state["performance_scheduler"]["reservations"] == []


def test_production_default_uses_closed_factory_and_persists_dag(
    tmp_path, monkeypatch,
):
    state = {
        "goal": "production", "parallel": True, "step": "execute",
        "run_id": "run", "baseline": "a" * 40,
        "design_fingerprint": "d" * 64,
        "plan_fingerprint": "b" * 64,
        "tasks": [{**_task("a", scope=("src/a.py",)), "status": "pending"}],
        "scheduler_host_capability_receipt": _host_capacity_receipt(
            concurrency=1, max_in_flight=1),
    }

    @contextlib.contextmanager
    def mutate(_ws):
        yield state

    monkeypatch.setattr(loop, "mutate", mutate)
    evidence = SandboxEvidenceStore(tmp_path, "repo", "scheduler")
    dag_store = ExecutionDagRevisionStore(tmp_path)
    monkeypatch.setattr(
        loop, "_production_scheduler_evidence",
        lambda _ws, _state: (evidence, dag_store),
    )
    created = []
    original = ClosedTaskDispatchCapabilityFactory.create

    def observed(self, **bindings):
        created.append(dict(bindings))
        return original(self, **bindings)

    monkeypatch.setattr(ClosedTaskDispatchCapabilityFactory, "create", observed)
    result = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][0]], repository_files=set(),
        clock=FakeClock(wall_time=10),
    )

    assert result["status"] == "admitted"
    assert len(created) == 1
    head = dag_store.read_head()
    assert head["fingerprint"] == \
        state["performance_scheduler"]["execution_dag"]["fingerprint"]
    assert state["performance_scheduler"]["execution_dag_head"] == head


def test_execution_dag_revision_bytes_cas_fork_and_crash_recovery(tmp_path):
    store = ExecutionDagRevisionStore(tmp_path)
    first = _state([_task("a")])["execution_dag"]
    first_head = store.persist(first, expected_head=None)
    first_path = (tmp_path / "execution-dag" / "revisions" /
                  f"0-{first['fingerprint']}.json")
    immutable = first_path.read_bytes()
    assert first_head["revision_path"] == \
        f"execution-dag/revisions/0-{first['fingerprint']}.json"
    assert store.persist(first, expected_head=first["fingerprint"]) == first_head
    assert first_path.read_bytes() == immutable

    second = append_replan_generation(
        first, [_task("a"), _task("b", deps=("a",))],
        FakeClock(wall_time=2))
    with pytest.raises(PlanTopologyError, match="CAS"):
        store.persist(second, expected_head="f" * 64)

    crash_root = tmp_path / "crash"
    crash_store = ExecutionDagRevisionStore(crash_root)
    revision_dir = crash_root / "execution-dag" / "revisions"
    revision_dir.mkdir(parents=True, exist_ok=True)
    crash_path = revision_dir / f"0-{first['fingerprint']}.json"
    crash_path.write_bytes((json.dumps(
        first, sort_keys=True, separators=(",", ":")) + "\n").encode())
    recovered = crash_store.persist(first, expected_head=None)
    assert recovered == crash_store.read_head()
    assert crash_store.persist(first, expected_head=first["fingerprint"]) == \
        recovered

    fork_path = revision_dir / ("0-" + "e" * 64 + ".json")
    fork_path.write_bytes(b"{}\n")
    with pytest.raises(PlanTopologyError, match="fork"):
        crash_store.read_head()


def test_live_scheduler_admits_waits_wakes_and_persists_replan_dag(
    tmp_path, monkeypatch,
):
    tasks = [
        _task("a", scope=("src/a.py",)),
        _task("b", deps=("a",), scope=("src/b.py",)),
    ]
    state = {
        "goal": "runtime", "parallel": True, "step": "execute",
        "tasks": [{**row, "status": "pending"} for row in tasks],
    }

    @contextlib.contextmanager
    def mutate(_ws):
        yield state

    monkeypatch.setattr(loop, "mutate", mutate)
    monkeypatch.setattr(loop, "load", lambda _ws: state)
    monkeypatch.setattr(loop, "_stage_loop_mutation_refusal", lambda _ws: None)
    evidence = SandboxEvidenceStore(tmp_path, "repo", "live-scheduler")
    dag_store = ExecutionDagRevisionStore(tmp_path)
    factory = RecordedTaskDispatchCapabilityFactory()
    first = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][0]], repository_files=set(),
        capacity={"configured_host_concurrency": 1,
                  "max_in_flight": 1, "session_limit": 60},
        clock=FakeClock(wall_time=1),
        capability_factory=factory, evidence_store=evidence,
        execution_dag_store=dag_store,
    )
    assignment = first["assignments"][0]
    scheduler = state["performance_scheduler"]
    state["dispatch_telemetry"] = dispatch_telemetry.new_ledger(
        run_id=scheduler["run_id"], source_sha=scheduler["source_sha"],
        design_fingerprint=scheduler["design_fingerprint"],
        plan_fingerprint=scheduler["plan_fingerprint"], started_at=0,
    )
    dispatch_telemetry.bind_dispatch(
        state["dispatch_telemetry"],
        {
            "dispatch_id": "dispatch-a", "thread_id": "thread-a",
            "thread_type": "worker", "task_id": "a",
            "dependencies": [], "shared_owner": None,
            "started_at": 1, "ended_at": 1,
            "wait_duration_seconds": 0, "correction_count": 0,
            "events": [],
        },
        reservation_fingerprint=assignment["reservation_fingerprint"],
        capability_id=assignment["capability"]["capability_id"],
    )
    initial_event = command_runtime.dispatch_event({
        "schema": command_runtime.SCHEMA,
        "handle": "a" * 32, "revision": 1, "state": "created",
        "created_at": 1, "updated_at": 1,
        "identity": {
            "schema": "taskplane.governed-command-identity/v1",
            "run_id": scheduler["run_id"], "task_id": "a",
        },
        "wave_id": "execute-wave",
    })
    initial_event["dispatch_id"] = "dispatch-a"
    initial_event["thread_id"] = "thread-a"
    initial_event["fingerprint"] = "event-a-1"
    initial_wake = loop.handle_host_input(
        str(tmp_path), {"type": "worker_event",
                        "dispatch_event": initial_event})
    assert initial_wake["accepted"] is True
    command_event = command_runtime.dispatch_event({
        "schema": command_runtime.SCHEMA,
        "handle": "a" * 32, "revision": 2, "state": "succeeded",
        "created_at": 1, "updated_at": 2,
        "identity": {
            "schema": "taskplane.governed-command-identity/v1",
            "run_id": scheduler["run_id"], "task_id": "a",
        },
        "wave_id": "execute-wave",
    })
    command_event["dispatch_id"] = "dispatch-a"
    command_event["thread_id"] = "thread-a"
    command_event["fingerprint"] = "event-a"
    wake = loop.handle_host_input(
        str(tmp_path), {"type": "worker_event",
                        "dispatch_event": command_event})

    assert wake["accepted"] is True
    assert wake["wake"]["event_count"] == 1
    assert wake["wake"]["terminal"] is False
    assert state["performance_scheduler"]["events"][-1]["kind"] == "progress"

    state["tasks"][0]["status"] = "passed"
    terminal = loop._record_scheduler_terminal(
        str(tmp_path), task_id="a", kind="complete", at=3,
        capability_factory=factory, evidence_store=evidence)
    assert terminal["terminal"] is True
    assert terminal["admission"]["dispatch_set"]["members"] == ["b"]
    second = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][1]], repository_files=set(),
        capacity={"configured_host_concurrency": 1,
                  "max_in_flight": 1, "session_limit": 60},
        clock=FakeClock(wall_time=4),
        capability_factory=factory, evidence_store=evidence,
        execution_dag_store=dag_store,
    )
    assert second["dispatch_set"]["members"] == ["b"]

    state["tasks"][1]["status"] = "passed"
    loop._record_scheduler_terminal(
        str(tmp_path), task_id="b", kind="complete", at=5,
        capability_factory=factory, evidence_store=evidence)
    state["tasks"].append({
        **_task("c", deps=("b",), scope=("src/c.py",)),
        "status": "pending",
    })
    third = loop._admit_scheduler_wave(
        str(tmp_path), [state["tasks"][2]], repository_files=set(),
        capacity={"configured_host_concurrency": 1,
                  "max_in_flight": 1, "session_limit": 60},
        clock=FakeClock(wall_time=6),
        capability_factory=factory, evidence_store=evidence,
        execution_dag_store=dag_store,
    )
    dag = state["performance_scheduler"]["execution_dag"]
    assert third["dispatch_set"]["members"] == ["c"]
    assert len(dag["generations"]) == 2
    assert ["g0:a", "g1:a", "supersession"] in dag["edges"]
    assert retro._authoritative_execution_state(
        state, state["tasks"], [{"event": "loop_wave", "ts": 999}],
        execution_dag_store=dag_store,
    )[1] == "managed-run-execution-dag-head"


def test_build_c_direct_receipt_carries_reservation_capability_and_capacity(
    tmp_path, monkeypatch,
):
    tasks = [{**_task("a", scope=("src/a.py",)), "status": "pending"}]
    graph = {"modules": {"a": {"files": ["src/a.py"]}},
             "edges": [], "files": {}, "meta": {}}
    monkeypatch.setattr(build_c.depgraph, "scope_modules",
                        lambda _ws, _scope: ["a"])

    def register(_ws, worker, task_id):
        return {"schema": "taskplane.managed-task-worktree/v1",
                "task_id": task_id, "path": worker,
                "branch_tip": "a" * 40}

    def wait_policy(_name, count):
        return {"schema": "taskplane.wait-policy/v1", "mode": "event",
                "scheduled_polling": False, "timeout_seconds": 1800,
                "reissue_after": ["completion", "attention"],
                "outstanding_count": count, "outstanding_set": "build-c"}

    def wait_invocation(_policy, members):
        return {"schema": "taskplane.event-wait-invocation/v1",
                "operation": "wait_for_events", "scheduled": False,
                "reissue": False, "outstanding_members": members}

    state = {
        "tasks": tasks, "run_id": "run", "baseline": "a" * 40,
        "design_fingerprint": "d" * 64,
        "plan_fingerprint": "b" * 64,
    }
    host_receipt = _host_capacity_receipt(
        concurrency=1, max_in_flight=1)
    evidence = SandboxEvidenceStore(tmp_path, "repo", "build-c")
    dag_store = ExecutionDagRevisionStore(tmp_path)
    receipt = build_c.assign_scopes(
        str(tmp_path), state, graph=graph,
        revision="a" * 40,
        create_worktree=lambda *_args: str(tmp_path / "worker"),
        register_worktree=register,
        wait_policy_factory=wait_policy,
        wait_invocation_factory=wait_invocation,
        repository_files=set(),
        host_capability_receipt=host_receipt,
        evidence_store=evidence, execution_dag_store=dag_store,
        clock=FakeClock(wall_time=10),
    )
    assignment = receipt["assignments"][0]
    assert receipt["reservation_fingerprint"] == \
        assignment["reservation_fingerprint"]
    assert assignment["capability"]["reservation_fingerprint"] == \
        receipt["reservation_fingerprint"]
    assert assignment["capability"]["write_paths"] == ("src/a.py",)
    assert set(assignment["capability"]) == {
        "schema", "capability_id", "run_id", "source_sha",
        "design_fingerprint", "plan_fingerprint", "task_id", "stage",
        "reservation_fingerprint", "predecessor_fingerprint",
        "allowed_tools", "read_paths", "write_paths", "allowed_git_refs",
        "allowed_network_endpoints", "credential_handles",
        "release_credentials_available", "irreversible_actions_allowed",
        "cryptographic_authenticity_claimed",
    }
    assert assignment["capability"]["cryptographic_authenticity_claimed"] \
        is False

    with pytest.raises(build_c.ScopeAssignmentError, match="event wait"):
        build_c.assign_scopes(
            str(tmp_path), state, graph=graph,
            revision="a" * 40,
            create_worktree=lambda *_args: str(tmp_path / "worker"),
            register_worktree=register,
            wait_policy_factory=lambda *_args: {
                "schema": "taskplane.wait-policy/v1", "mode": "event",
                "scheduled_polling": False, "timeout_seconds": 1800,
                "reissue_after": ["completion", "attention"],
                "outstanding_count": 0,
            },
            wait_invocation_factory=wait_invocation,
            repository_files=set(),
            host_capability_receipt=host_receipt,
            evidence_store=evidence, execution_dag_store=dag_store,
            clock=FakeClock(wall_time=10),
        )
