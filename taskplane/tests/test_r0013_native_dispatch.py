from __future__ import annotations

import hashlib
import inspect
import json

import pytest

from taskplane import build_c, command_runtime, loop, plan_topology


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def _task(task_id: str, scope: list[str], *, deps: list[str] | None = None,
          status: str = "pending") -> dict:
    return {
        "id": task_id,
        "scope": scope,
        "deps": list(deps or []),
        "status": status,
        "tests": "python3 -m pytest -q",
        "req": "R-0013",
        "contracts": ["contract:delivery.codex-native-dispatch"],
    }


def _wait_policy(_name: str, count: int) -> dict:
    return {
        "schema": "taskplane.wait-policy/v1",
        "mode": "event",
        "scheduled_polling": False,
        "timeout_seconds": 1800,
        "reissue_after": ["completion", "attention"],
        "outstanding_count": count,
        "outstanding_set": "r0013-native-set",
        "deadline_at": 200.0,
        "wave_deadline_at": 300.0,
        "reconciliation_reserve_seconds": 50.0,
    }


def _wait_invocation(policy: dict, members: list[str]) -> dict:
    return {
        "schema": "taskplane.event-wait-invocation/v1",
        "operation": "wait_for_events",
        "scheduled": False,
        "reissue": False,
        "outstanding_members": list(members),
        "deadline_at": policy["deadline_at"],
    }


def _native_result(status: str, request: dict, *,
                   observed_at: float = 40.0) -> dict:
    material = {
        "schema": command_runtime.NATIVE_ADAPTER_SCHEMA,
        "operation": "wait_for_events",
        "status": status,
        "observed_at": observed_at,
        "native_agent_id": "agent-fresh-r0013",
        **{key: request[key] for key in (
            "run_id", "task_name", "outstanding_set_fingerprint",
            "intent_fingerprint", "source_sha", "idempotency_key",
            "request_fingerprint",
        )},
        **({"attention_kind": "input_required"}
           if status == "attention" else {}),
    }
    return {**material, "result_fingerprint": _fingerprint(material)}


def test_every_ready_disjoint_unit_is_emitted_once_in_one_native_set():
    tasks = [
        _task("a", ["src/a/main.py"]),
        _task("b", ["src/b.py"]),
        _task("c-overlap", ["src/a"]),
        _task("d-dependent", ["src/d.py"], deps=["a"]),
    ]

    receipt = plan_topology.seal_ready_set(
        tasks, passed=set(), repository_files=set())

    assert receipt["schema"] == "taskplane.sealed-ready-set/v1"
    assert [row["task_id"] for row in receipt["members"]] == ["a", "b"]
    assert [row["task_id"] for row in receipt["held"]] == [
        "c-overlap", "d-dependent"]
    assert receipt["held"][0]["shared_owner"] == "scope:src/a/main.py"
    assert receipt["held"][1]["shared_owner"] == "dependency:a"
    assert len({row["task_id"] for row in receipt["members"]}) == 2
    assert plan_topology.validate_ready_set(receipt, tasks) == receipt

    corrupted = json.loads(json.dumps(receipt))
    corrupted["members"].append(dict(corrupted["members"][0]))
    with pytest.raises(plan_topology.PlanTopologyError, match="fingerprint"):
        plan_topology.validate_ready_set(corrupted, tasks)


@pytest.mark.parametrize(("field", "invented"), [
    ("reason", "waiting on deps: invented"),
    ("shared_owner", "dependency:invented"),
])
def test_sealed_ready_set_refuses_rehashed_invented_hold(field, invented):
    tasks = [
        _task("a", ["src/a.py"]),
        _task("b", ["src/b.py"], deps=["a"]),
    ]
    sealed = plan_topology.seal_ready_set(
        tasks, passed=set(), repository_files=set())
    tampered = json.loads(json.dumps(sealed))
    tampered["held"][0][field] = invented
    material = {key: value for key, value in tampered.items()
                if key != "fingerprint"}
    tampered["fingerprint"] = plan_topology.content_fingerprint(material)

    with pytest.raises(plan_topology.PlanTopologyError,
                       match="approved topology"):
        plan_topology.validate_ready_set(tampered, tasks)


def test_build_c_consumes_one_sealed_ready_set_without_reclassification(
        tmp_path, monkeypatch):
    tasks = [_task("a", ["src/a.py"]), _task("b", ["src/b.py"])]
    sealed = plan_topology.seal_ready_set(
        tasks, passed=set(), repository_files=set())
    graph = {
        "modules": {
            "module-a": {"files": ["src/a.py"]},
            "module-b": {"files": ["src/b.py"]},
        }
    }
    monkeypatch.setattr(
        build_c, "executable_topology",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("BUILD-C reclassified the sealed ready set")))
    monkeypatch.setattr(
        build_c.depgraph, "scope_modules",
        lambda _ws, scope: ["module-a" if scope == ["src/a.py"]
                            else "module-b"])
    worktrees = {task_id: tmp_path / task_id for task_id in ("a", "b")}
    for path in worktrees.values():
        path.mkdir()

    receipt = build_c.assign_scopes(
        str(tmp_path), {"tasks": tasks, "ready_set": sealed},
        graph=graph, revision="a" * 40,
        create_worktree=lambda _ws, task_id, _revision:
            str(worktrees[task_id]),
        register_worktree=lambda _ws, path, task_id: {
            "schema": "taskplane.managed-task-worktree/v1",
            "task_id": task_id,
            "path": path,
            "branch_tip": "a" * 40,
        },
        wait_policy_factory=_wait_policy,
        wait_invocation_factory=_wait_invocation,
    )

    assert receipt["ready_set_fingerprint"] == sealed["fingerprint"]
    assert receipt["dispatch_set"]["members"] == ["a", "b"]
    assert receipt["dispatch_set"]["member_count"] == 2
    assert [row["task_id"] for row in receipt["assignments"]] == ["a", "b"]
    assert receipt["serialized"] == []
    encoded = json.dumps(receipt, sort_keys=True).lower()
    for forbidden in (
            "capacity", "reservation", "queue", "stage_runtime_dispatch",
            "execution_root", "_stage_bindings"):
        assert forbidden not in encoded


def test_r0013_native_build_refuses_missing_ready_set_before_classification(
        tmp_path, monkeypatch):
    tasks = [_task("a", ["src/a.py"])]
    created: list[str] = []
    monkeypatch.setattr(
        build_c, "executable_topology",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing sealed input reached classification")))

    with pytest.raises(build_c.ScopeAssignmentError,
                       match="requires a sealed ready_set"):
        build_c.assign_scopes(
            str(tmp_path), {"tasks": tasks},
            graph={"modules": {"a": {"files": ["src/a.py"]}}},
            revision="a" * 40,
            create_worktree=lambda *_args:
                created.append("unexpected") or str(tmp_path / "unexpected"),
            register_worktree=lambda *_args: {},
            wait_policy_factory=_wait_policy,
            wait_invocation_factory=_wait_invocation,
        )
    assert created == []


@pytest.mark.parametrize("status", ["completion", "attention"])
def test_one_native_wait_wakes_on_completion_or_attention(status):
    members = ["a", "b"]
    request = command_runtime.native_wait_request(
        run_id="r0013-run",
        outstanding_set="r0013-native-set",
        members=members,
        intent_fingerprint="1" * 64,
        source_sha="a" * 40,
        deadline_at=90.0,
        idempotency_key="r0013-wait-1",
    )
    result = _native_result(status, request)

    first = command_runtime.consume_native_wait(
        request, result, members=members, now=40.0,
        elapsed_seconds=10.0, usage_identity={"observed_tokens": 21})
    replay = command_runtime.consume_native_wait(
        request, result, members=members, now=40.0,
        elapsed_seconds=10.0, usage_identity={"observed_tokens": 21})

    assert first == replay
    assert first["kind"] == status
    assert first["scheduled_polling"] is False
    assert first["reissue"] is False
    assert first["replacement"] is False
    assert first["outstanding_members"] == members
    assert first["stop_required"] is (status == "attention")

    altered = dict(result, observed_at=41.0)
    with pytest.raises(command_runtime.CommandRuntimeError,
                       match="fingerprint"):
        command_runtime.consume_native_wait(
            request, altered, members=members, now=41.0,
            elapsed_seconds=11.0,
            usage_identity={"observed_tokens": 22})


@pytest.mark.parametrize(("field", "foreign"), [
    ("run_id", "foreign-run"),
    ("task_name", "foreign-set"),
    ("outstanding_set_fingerprint", "f" * 64),
    ("intent_fingerprint", "2" * 64),
    ("source_sha", "b" * 40),
    ("idempotency_key", "foreign-idempotency"),
    ("request_fingerprint", "e" * 64),
])
def test_native_wait_refuses_foreign_rehashed_result(field, foreign):
    members = ["a", "b"]
    request = command_runtime.native_wait_request(
        run_id="r0013-run",
        outstanding_set="r0013-native-set",
        members=members,
        intent_fingerprint="1" * 64,
        source_sha="a" * 40,
        deadline_at=90.0,
        idempotency_key="r0013-wait-1",
    )
    result = _native_result("completion", request)
    result[field] = foreign
    material = {key: value for key, value in result.items()
                if key != "result_fingerprint"}
    result["result_fingerprint"] = _fingerprint(material)

    with pytest.raises(command_runtime.CommandRuntimeError,
                       match="foreign to its request"):
        command_runtime.consume_native_wait(
            request, result, members=members, now=40.0,
            elapsed_seconds=10.0,
            usage_identity={"observed_tokens": 21})


def test_silent_transport_deadline_returns_one_attention_before_wave_ceiling():
    members = ["a", "b"]
    request = command_runtime.native_wait_request(
        run_id="r0013-run",
        outstanding_set="r0013-native-set",
        members=members,
        intent_fingerprint="1" * 64,
        source_sha="a" * 40,
        deadline_at=90.0,
        idempotency_key="r0013-wait-deadline",
    )

    with pytest.raises(command_runtime.NativeObservationUnavailable):
        command_runtime.consume_native_wait(
            request, None, members=members, now=89.0,
            elapsed_seconds=20.0,
            usage_identity={"observed_tokens": 34})

    attention = command_runtime.consume_native_wait(
        request, None, members=members, now=90.0,
        elapsed_seconds=21.0,
        usage_identity={"observed_tokens": 34})
    replay = command_runtime.consume_native_wait(
        request, None, members=members, now=300.0,
        elapsed_seconds=21.0,
        usage_identity={"observed_tokens": 34})

    assert attention == replay
    assert attention["kind"] == "attention"
    assert attention["attention_kind"] == "NATIVE_WAIT_DEADLINE"
    assert attention["observed_at"] == 90.0
    assert attention["stop_required"] is True
    assert attention["human_actions"] == [
        "reduce-scope", "end-wave", "architecture-review"]
    assert attention["scheduled_polling"] is False
    assert attention["reissue"] is False
    assert attention["replacement"] is False


def test_severed_readiness_dispatch_completion_and_wait_fail_without_fallback(
        tmp_path, monkeypatch):
    tasks = [_task("a", ["src/a.py"])]
    sealed = plan_topology.seal_ready_set(
        tasks, passed=set(), repository_files=set())
    broken = json.loads(json.dumps(sealed))
    broken["topology_fingerprint"] = "severed"
    created: list[str] = []
    with pytest.raises(build_c.ScopeAssignmentError, match="fingerprint"):
        build_c.assign_scopes(
            str(tmp_path), {"tasks": tasks, "ready_set": broken},
            graph={"modules": {"a": {"files": ["src/a.py"]}}},
            revision="a" * 40,
            create_worktree=lambda *_args: created.append("unexpected") or
            str(tmp_path / "unexpected"),
            register_worktree=lambda *_args: {},
            wait_policy_factory=_wait_policy,
            wait_invocation_factory=_wait_invocation,
        )
    assert created == []


def test_stage_journal_has_no_agent_dispatch_or_execution_root_projection():
    wave_source = inspect.getsource(loop.wave)
    for forbidden in (
            "_stage_loop_wave_dispatches", "_stage_bindings",
            "stage_runtime_dispatch", "execution_root"):
        assert forbidden not in wave_source
