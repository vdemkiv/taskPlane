import pytest

from taskplane import build_c, command_runtime, loop, retro
from taskplane.plan_topology import (
    PlanTopologyError,
    classify_plan,
    execution_metrics,
)


def _task(task_id, *, deps=(), scope=(), tests="", status="pending"):
    return {
        "id": task_id,
        "deps": list(deps),
        "scope": list(scope),
        "tests": tests,
        "status": status,
    }


def _pair(topology, left, right):
    key = tuple(sorted((left, right)))
    return next(
        row for row in topology["pairs"]
        if tuple(sorted((row["left"], row["right"]))) == key
    )


def test_plan_classifies_every_pair_without_host_capacity():
    topology = classify_plan([
        _task("a", scope=("src/a.py",)),
        _task("b", scope=("src/b.py",)),
        _task("c", deps=("a",), scope=("src/c.py",)),
    ], repository_files=set())

    assert len(topology["pairs"]) == 3
    assert _pair(topology, "a", "c")["shared_owner"] == "dependency:a"
    assert _pair(topology, "a", "b")["disposition"] == "parallel"


def test_native_ready_set_emits_all_disjoint_work_and_holds_overlap():
    tasks = [
        _task("a", scope=("src/a.py",)),
        _task("b", scope=("src/b.py",)),
        _task("held", scope=("src/a.py",)),
    ]

    ready, held, _ = loop.select_ready_tasks(
        tasks, passed=set(), repository_files=set())

    assert [row["id"] for row in ready] == ["a", "b"]
    assert held == [{
        "task": "held",
        "reason": "serialized by scope:src/a.py",
        "shared_owner": "scope:src/a.py",
    }]


def test_missing_owned_tests_become_dependency_edges():
    tasks = [
        _task("producer", scope=("tests/new_test.py",)),
        _task(
            "consumer", scope=("src/runtime.py",),
            tests="python3 -m pytest -q tests/new_test.py",
        ),
    ]

    ready, held, topology = loop.select_ready_tasks(
        tasks, passed=set(), repository_files=set())

    assert [row["id"] for row in ready] == ["producer"]
    assert held[0]["task"] == "consumer"
    assert topology["effective_dependencies"]["consumer"] == ["producer"]


def test_retro_uses_native_wave_claim_and_gate_trace():
    tasks = [_task("a"), _task("b"), _task("c", deps=("a",))]
    events = [
        {"event": "loop_wave", "ready": ["a", "b"], "ts": 0.0},
        {"event": "loop_gate", "step": "evaluate", "task": "a", "ts": 3.0},
        {"event": "loop_gate", "step": "evaluate", "task": "b", "ts": 4.0},
        {"event": "loop_claim", "task": "c", "ts": 4.0},
        {"event": "loop_gate", "step": "evaluate", "task": "c", "ts": 6.0},
    ]

    state, source = retro._authoritative_execution_state({}, tasks, events)
    metrics = retro.performance_projection(state)

    assert source == "native-dispatch-and-loop-trace"
    assert metrics["parallelism_factor"] == 1.5
    assert metrics["longest_serial_chain"] == {
        "tasks": ["a", "c"], "seconds": 5,
    }
    assert "scheduler_caused_idle_seconds" not in metrics


def test_non_finite_native_trace_time_fails_closed():
    with pytest.raises(PlanTopologyError, match="finite"):
        execution_metrics({
            "topology": {"effective_dependencies": {"a": []}},
            "task_times": {
                "a": {"start": 0, "terminal": float("inf")},
            },
        })


def test_command_completion_remains_a_native_dispatch_event():
    event = command_runtime.dispatch_event({
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

    assert event["schema"] == "taskplane.dispatch-event/v1"
    assert event["kind"] == "complete"
    assert event["task_id"] == "a"


def test_build_c_receipt_is_native_dispatch_set_without_scheduler_fields(
        tmp_path, monkeypatch):
    tasks = [
        _task("a", scope=("src/a.py",)),
        _task("b", scope=("src/b.py",)),
    ]
    graph = {
        "modules": {
            "a": {"files": ["src/a.py"]},
            "b": {"files": ["src/b.py"]},
        },
        "edges": [], "files": {}, "meta": {},
    }
    monkeypatch.setattr(
        build_c.depgraph, "scope_modules",
        lambda _ws, scope: ["a"] if scope == ["src/a.py"] else ["b"])
    workers = {task_id: tmp_path / task_id for task_id in ("a", "b")}
    for worker in workers.values():
        worker.mkdir()

    def wait_policy(_name, count):
        return {
            "schema": "taskplane.wait-policy/v1", "mode": "event",
            "scheduled_polling": False, "timeout_seconds": 1800,
            "reissue_after": ["completion", "attention"],
            "outstanding_count": count, "outstanding_set": "build-c",
        }

    receipt = build_c.assign_scopes(
        str(tmp_path), {"tasks": tasks}, graph=graph,
        revision="a" * 40,
        create_worktree=lambda _ws, task_id, _rev: str(workers[task_id]),
        register_worktree=lambda _ws, worker, task_id: {
            "schema": "taskplane.managed-task-worktree/v1",
            "task_id": task_id, "path": worker, "branch_tip": "a" * 40,
        },
        wait_policy_factory=wait_policy,
        wait_invocation_factory=lambda _policy, members: {
            "schema": "taskplane.event-wait-invocation/v1",
            "operation": "wait_for_events", "scheduled": False,
            "reissue": False, "outstanding_members": members,
        },
        repository_files=set(),
    )

    assert receipt["dispatch_set"]["members"] == ["a", "b"]
    assert receipt["wait_invocation"]["outstanding_members"] == ["a", "b"]
    forbidden = {
        "reservation_fingerprint", "scheduler_revision",
        "execution_dag_head", "capability", "event_contract",
    }
    assert not forbidden.intersection(receipt)
    assert all(not forbidden.intersection(row)
               for row in receipt["assignments"])


def test_retired_scheduler_is_not_an_active_api():
    from taskplane import plan_topology

    retired = {
        "admit_ready_batch", "new_scheduler_state", "record_worker_event",
        "wait_for_worker_events", "ExecutionDagRevisionStore",
        "ClosedTaskDispatchCapabilityFactory",
    }
    assert retired.isdisjoint(set(dir(plan_topology)))
    assert not hasattr(loop, "_admit_scheduler_wave")
    assert not hasattr(loop, "_record_scheduler_terminal")
