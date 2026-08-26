"""Plan topology, atomic ready-set admission, and execution-DAG metrics.

The pair map produced here is the scheduler input, rather than explanatory
metadata.  In particular, a declared test file is a build artifact: when it
does not exist in the admitted source and another task owns that path, the
producer is an implicit predecessor of the consumer even if edit scopes are
otherwise disjoint.
"""

from __future__ import annotations

from copy import deepcopy
import fnmatch
import json
from pathlib import Path
import shlex
import threading
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

try:
    from .delivery_ports import (
        Clock,
        EventWaiter,
        TaskDispatchCapabilityFactory,
        canonical_json,
        content_fingerprint,
    )
except ImportError:  # pragma: no cover - direct module loading
    from delivery_ports import (  # type: ignore
        Clock,
        EventWaiter,
        TaskDispatchCapabilityFactory,
        canonical_json,
        content_fingerprint,
    )


TOPOLOGY_SCHEMA = "taskplane.plan-topology/v1"
ADMISSION_SCHEMA = "taskplane.dispatch-admission/v1"
EXECUTION_DAG_SCHEMA = "taskplane.execution-dag/v1"
EVENT_KINDS = frozenset({
    "progress", "complete", "attention", "failed", "cancelled",
    "partial-host",
})
TERMINAL_EVENT_KINDS = frozenset({
    "complete", "attention", "failed", "cancelled", "partial-host",
})
COMPLETE_STATUSES = frozenset({"complete", "done", "pass", "passed"})
TERMINAL_STATUSES = COMPLETE_STATUSES | frozenset({
    "attention", "failed", "cancelled", "skipped",
})
DEFAULT_EVENT_QUEUE_CAP = 256
MAX_EVENT_BYTES = 64 * 1024


class PlanTopologyError(RuntimeError):
    """The Plan or a scheduler transition is structurally unsafe."""


_ADMISSION_LOCK = threading.RLock()


def _path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").removeprefix("./")


def _task_rows(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(task) for task in tasks]
    ids = [str(task.get("id") or "") for task in rows]
    if any(not task_id for task_id in ids) or len(set(ids)) != len(ids):
        raise PlanTopologyError("task ids must be non-empty and unique")
    known = set(ids)
    for row, task_id in zip(rows, ids):
        row["id"] = task_id
        deps = [str(dep) for dep in row.get("deps") or ()]
        unknown = set(deps).difference(known)
        if unknown:
            raise PlanTopologyError(
                f"task {task_id} has unknown dependencies: {sorted(unknown)}"
            )
        if task_id in deps:
            raise PlanTopologyError(f"task {task_id} depends on itself")
        row["deps"] = sorted(set(deps))
        row["scope"] = sorted({_path(item) for item in row.get("scope") or () if _path(item)})
    return rows


def _scope_matches(scope: str, artifact: str) -> bool:
    if not scope or not artifact:
        return False
    if scope == artifact:
        return True
    if any(mark in scope for mark in "*?["):
        return fnmatch.fnmatchcase(artifact, scope)
    return artifact.startswith(scope.rstrip("/") + "/")


def _scope_overlap(left: Sequence[str], right: Sequence[str]) -> str | None:
    candidates: list[str] = []
    for left_path in left:
        for right_path in right:
            if _scope_matches(left_path, right_path):
                candidates.append(right_path)
            elif _scope_matches(right_path, left_path):
                candidates.append(left_path)
    return min(candidates) if candidates else None


def _declared_test_files(command: object) -> tuple[str, ...]:
    if not isinstance(command, str) or not command.strip():
        return ()
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise PlanTopologyError(f"malformed task test command: {exc}") from exc
    files = set()
    for token in tokens:
        candidate = _path(token.split("::", 1)[0])
        if candidate.endswith(".py") and "/" in candidate:
            files.add(candidate)
    return tuple(sorted(files))


def _repository_inventory(
    test_files: Iterable[str], repository_files: Iterable[str] | None,
) -> set[str]:
    if repository_files is not None:
        return {_path(item) for item in repository_files}
    return {item for item in test_files if Path(item).is_file()}


def _descendants(dependencies: Mapping[str, Sequence[str]]) -> dict[str, set[str]]:
    descendants = {task_id: set() for task_id in dependencies}
    visited: set[str] = set()

    # First validate with a conventional dependency walk.  The second pass
    # below computes transitive descendants without depending on input order.
    def validate(task_id: str, stack: set[str]) -> None:
        if task_id in stack:
            raise PlanTopologyError("task dependency graph contains a cycle")
        if task_id in visited:
            return
        stack.add(task_id)
        for dependency in dependencies[task_id]:
            validate(dependency, stack)
        stack.remove(task_id)
        visited.add(task_id)

    visited.clear()
    for task_id in dependencies:
        validate(task_id, set())
    for task_id in dependencies:
        pending = list(dependencies[task_id])
        while pending:
            dependency = pending.pop()
            descendants[dependency].add(task_id)
            pending.extend(dependencies[dependency])
    return descendants


def classify_plan(
    tasks: Sequence[Mapping[str, Any]], *,
    repository_files: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return an exhaustive pair map and dependency closure for ``tasks``.

    Missing test artifacts are classified against the admitted repository
    inventory, not the executor's later working tree.  A uniquely scoped
    producer becomes an implicit predecessor.  Ambiguous ownership fails
    closed; a missing unowned test is reported and keeps its consumer held.
    """
    rows = _task_rows(tasks)
    by_id = {row["id"]: row for row in rows}
    all_test_files = {
        path for row in rows for path in _declared_test_files(row.get("tests"))
    }
    present = _repository_inventory(all_test_files, repository_files)
    dependencies = {row["id"]: set(row["deps"]) for row in rows}
    test_edges: dict[tuple[str, str], str] = {}
    missing_by_consumer: dict[str, list[str]] = {row["id"]: [] for row in rows}

    for consumer in rows:
        for artifact in _declared_test_files(consumer.get("tests")):
            if artifact in present:
                continue
            owners = sorted(
                row["id"] for row in rows
                if row["id"] != consumer["id"]
                and any(_scope_matches(scope, artifact) for scope in row["scope"])
            )
            if len(owners) > 1:
                raise PlanTopologyError(
                    f"missing test artifact has ambiguous owners: {artifact}: {owners}"
                )
            if owners:
                owner = owners[0]
                dependencies[consumer["id"]].add(owner)
                test_edges[(owner, consumer["id"])] = artifact
            elif not any(_scope_matches(scope, artifact) for scope in consumer["scope"]):
                missing_by_consumer[consumer["id"]].append(artifact)

    effective = {task_id: sorted(values) for task_id, values in dependencies.items()}
    descendants = _descendants(effective)
    pairs: list[dict[str, Any]] = []
    ids = sorted(by_id)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            shared_owner: str | None = None
            artifact = test_edges.get((left_id, right_id)) or test_edges.get((right_id, left_id))
            if artifact:
                shared_owner = f"test-artifact:{artifact}"
            elif right_id in descendants[left_id]:
                shared_owner = f"dependency:{left_id}"
            elif left_id in descendants[right_id]:
                shared_owner = f"dependency:{right_id}"
            else:
                overlap = _scope_overlap(by_id[left_id]["scope"], by_id[right_id]["scope"])
                if overlap:
                    shared_owner = f"scope:{overlap}"
            pairs.append({
                "left": left_id,
                "right": right_id,
                "disposition": "serialized" if shared_owner else "parallel",
                "shared_owner": shared_owner,
            })

    material = {
        "schema": TOPOLOGY_SCHEMA,
        "task_ids": ids,
        "pairs": pairs,
        "effective_dependencies": effective,
        "missing_test_assets": {
            task_id: sorted(paths) for task_id, paths in missing_by_consumer.items() if paths
        },
        "test_artifact_edges": [
            {"producer": producer, "consumer": consumer, "artifact": artifact}
            for (producer, consumer), artifact in sorted(test_edges.items())
        ],
    }
    material["fingerprint"] = content_fingerprint(material)
    return material


def _initial_execution_dag(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = _task_rows(tasks)
    nodes = [
        {"id": f"g0:{row['id']}", "task_id": row["id"], "generation": 0}
        for row in sorted(rows, key=lambda row: row["id"])
    ]
    edges = [
        [f"g0:{dep}", f"g0:{row['id']}", "dependency"]
        for row in sorted(rows, key=lambda row: row["id"])
        for dep in row["deps"]
    ]
    material = {
        "schema": EXECUTION_DAG_SCHEMA,
        "generations": [{"generation": 0, "at": None, "task_ids": sorted(row["id"] for row in rows)}],
        "nodes": nodes,
        "edges": edges,
    }
    material["fingerprint"] = content_fingerprint(material)
    return material


def new_scheduler_state(
    tasks: Sequence[Mapping[str, Any]], *, run_id: str, source_sha: str,
    design_fingerprint: str, plan_fingerprint: str, stage: str,
    repository_files: Iterable[str] | None = None,
    statuses: Mapping[str, str] | None = None, sessions_admitted: int = 0,
) -> dict[str, Any]:
    rows = _task_rows(tasks)
    topology = classify_plan(rows, repository_files=repository_files)
    supplied_statuses = dict(statuses or {})
    unknown = set(supplied_statuses).difference(row["id"] for row in rows)
    if unknown:
        raise PlanTopologyError(f"statuses name unknown tasks: {sorted(unknown)}")
    state = {
        "schema": "taskplane.scheduler-state/v1",
        "run_id": str(run_id),
        "source_sha": str(source_sha),
        "design_fingerprint": str(design_fingerprint),
        "plan_fingerprint": str(plan_fingerprint),
        "stage": str(stage),
        "tasks": rows,
        "repository_files": sorted({_path(item) for item in repository_files or ()}),
        "topology": topology,
        "statuses": {
            row["id"]: supplied_statuses.get(row["id"], "ready") for row in rows
        },
        "in_flight": {},
        "reservations": [],
        "sessions_admitted": int(sessions_admitted),
        "revision": 0,
        "events": [],
        "event_queue_cap": DEFAULT_EVENT_QUEUE_CAP,
        "task_times": {},
        "scheduler_caused_idle_seconds": 0,
        "execution_dag": _initial_execution_dag(rows),
        "evidence_head": None,
    }
    return state


def _pair_lookup(topology: Mapping[str, Any]) -> dict[frozenset[str], Mapping[str, Any]]:
    return {
        frozenset((str(row["left"]), str(row["right"]))): row
        for row in topology.get("pairs") or ()
    }


def _ready_projection(state: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    topology = state["topology"]
    statuses = state["statuses"]
    missing = topology.get("missing_test_assets") or {}
    ready: list[str] = []
    held: list[str] = []
    for task_id in topology["task_ids"]:
        status = statuses.get(task_id)
        if status in TERMINAL_STATUSES or status == "in_flight":
            continue
        dependencies = topology["effective_dependencies"][task_id]
        if not missing.get(task_id) and all(
            statuses.get(dependency) in COMPLETE_STATUSES for dependency in dependencies
        ):
            ready.append(task_id)
        else:
            held.append(task_id)
    return ready, held


def _maximal_disjoint(ready: Sequence[str], topology: Mapping[str, Any]) -> list[str]:
    lookup = _pair_lookup(topology)
    selected: list[str] = []
    for candidate in sorted(ready):
        if all(
            lookup[frozenset((candidate, member))]["disposition"] == "parallel"
            for member in selected
        ):
            selected.append(candidate)
    return selected


def _empty_admission(status: str, *, held: Sequence[str], ready: Sequence[str], reason: str) -> dict[str, Any]:
    return {
        "schema": ADMISSION_SCHEMA,
        "status": status,
        "reason": reason,
        "reservation_fingerprint": None,
        "dispatch_set": None,
        "assignments": [],
        "overflow_ready": list(ready),
        "held": list(held),
    }


def _admit_ready_batch_locked(
    state: MutableMapping[str, Any], host_capability: Mapping[str, Any],
    budget: Mapping[str, Any], evidence_store: Any, clock: Clock, *,
    capability_factory: TaskDispatchCapabilityFactory,
) -> dict[str, Any]:
    ready, held = _ready_projection(state)
    if not ready:
        return _empty_admission("idle", held=held, ready=(), reason="no dependency-ready work")
    if "configured_host_concurrency" not in host_capability:
        return _empty_admission(
            "stop_for_human_scope_review", held=held, ready=ready,
            reason="host concurrency capability is absent",
        )
    host_concurrency = int(host_capability["configured_host_concurrency"])
    max_in_flight = int(budget.get("max_in_flight", host_concurrency))
    session_limit = int(budget.get("session_limit", 60))
    if host_concurrency < 0 or max_in_flight <= 0 or session_limit <= 0:
        raise PlanTopologyError("dispatch capacities must be positive (host may be zero)")
    sessions_remaining = session_limit - int(state.get("sessions_admitted") or 0)
    if sessions_remaining <= 0:
        return _empty_admission(
            "stop_for_human_scope_review", held=held, ready=ready,
            reason="session budget exhausted",
        )
    current_in_flight = len(state.get("in_flight") or {})
    capacity = min(
        host_concurrency - current_in_flight,
        max_in_flight - current_in_flight,
        sessions_remaining,
    )
    if capacity <= 0:
        return _empty_admission(
            "waiting_for_event", held=held, ready=ready,
            reason="in-flight capacity exhausted",
        )

    disjoint = _maximal_disjoint(ready, state["topology"])
    selected = disjoint[:capacity]
    if not selected:
        return _empty_admission(
            "idle", held=held, ready=ready,
            reason="no pairwise-disjoint ready task",
        )
    revision = int(state.get("revision") or 0)
    reservation_material = {
        "schema": "taskplane.dispatch-reservation/v1",
        "run_id": state["run_id"],
        "source_sha": state["source_sha"],
        "design_fingerprint": state["design_fingerprint"],
        "plan_fingerprint": state["plan_fingerprint"],
        "stage": state["stage"],
        "scheduler_revision": revision,
        "topology_fingerprint": state["topology"]["fingerprint"],
        "members": selected,
    }
    reservation_fingerprint = content_fingerprint(reservation_material)
    task_by_id = {row["id"]: row for row in state["tasks"]}
    predecessor = (
        state["reservations"][-1]["reservation_fingerprint"]
        if state.get("reservations") else None
    )
    assignments = []
    for task_id in selected:
        task = task_by_id[task_id]
        scope = tuple(task.get("scope") or ())
        capability = capability_factory.create(
            run_id=state["run_id"],
            source_sha=state["source_sha"],
            design_fingerprint=state["design_fingerprint"],
            plan_fingerprint=state["plan_fingerprint"],
            task_id=task_id,
            stage=state["stage"],
            reservation_fingerprint=reservation_fingerprint,
            predecessor_fingerprint=predecessor,
            allowed_tools=tuple(task.get("allowed_tools") or ("read", "test")),
            read_paths=tuple(task.get("read_paths") or scope),
            write_paths=tuple(task.get("write_paths") or scope),
            allowed_git_refs=tuple(task.get("allowed_git_refs") or ()),
            allowed_network_endpoints=tuple(task.get("allowed_network_endpoints") or ()),
            credential_handles=tuple(task.get("credential_handles") or ()),
        )
        assignments.append({
            "task_id": task_id,
            "reservation_fingerprint": reservation_fingerprint,
            "capability": capability.projection,
            "assignment_mode": "direct",
            "event_contract": {
                "schema": "taskplane.worker-event-contract/v1",
                "wait_mode": "event",
                "timeout_seconds": 1800,
                "scheduled_polling": False,
                "required_for_long_worker": ["progress", "terminal"]
                if task.get("long_worker") else ["terminal"],
            },
        })
    dispatch_set = {
        "schema": "taskplane.direct-assignment-set/v1",
        "concurrent": True,
        "members": selected,
        "member_count": len(selected),
    }
    receipt_payload = {
        **reservation_material,
        "reservation_fingerprint": reservation_fingerprint,
        "dispatch_set": dispatch_set,
        "assignments": assignments,
        "reserved_at": float(clock.wall_time()),
    }
    evidence_fingerprint = None
    if evidence_store is not None:
        prepared = evidence_store.prepare(
            "telemetry", f"dispatch-{reservation_fingerprint}", receipt_payload,
            expected_head=state.get("evidence_head"),
        )
        committed = evidence_store.commit(prepared)
        evidence_receipt = json.loads(committed)
        evidence_fingerprint = evidence_receipt["fingerprint"]

    reservation = {
        **receipt_payload,
        "evidence_fingerprint": evidence_fingerprint,
    }
    for task_id in selected:
        state["statuses"][task_id] = "in_flight"
        state["in_flight"][task_id] = reservation_fingerprint
        state["task_times"].setdefault(task_id, {})["start"] = float(clock.wall_time())
    state["reservations"].append(reservation)
    state["sessions_admitted"] = int(state.get("sessions_admitted") or 0) + len(selected)
    state["revision"] = revision + 1
    if evidence_fingerprint:
        state["evidence_head"] = evidence_fingerprint
    state["scheduler_caused_idle_seconds"] = 0
    selected_set = set(selected)
    return {
        "schema": ADMISSION_SCHEMA,
        "status": "admitted",
        "reason": "atomic pairwise-disjoint ready-set reservation",
        "reservation_fingerprint": reservation_fingerprint,
        "dispatch_set": dispatch_set,
        "assignments": assignments,
        "overflow_ready": [task_id for task_id in ready if task_id not in selected_set],
        "held": held,
        "evidence_fingerprint": evidence_fingerprint,
    }


def admit_ready_batch(
    state: MutableMapping[str, Any], host_capability: Mapping[str, Any],
    budget: Mapping[str, Any], evidence_store: Any, clock: Clock, *,
    capability_factory: TaskDispatchCapabilityFactory,
) -> dict[str, Any]:
    """Atomically reserve and directly assign the maximal ready tranche."""
    with _ADMISSION_LOCK:
        return _admit_ready_batch_locked(
            state, host_capability, budget, evidence_store, clock,
            capability_factory=capability_factory,
        )


def _event_bytes(event: Mapping[str, Any]) -> bytes:
    try:
        return canonical_json(dict(event))
    except (TypeError, ValueError) as exc:
        raise PlanTopologyError(f"worker event is not canonical JSON: {exc}") from exc


def record_worker_event(
    state: MutableMapping[str, Any], event: Mapping[str, Any], *,
    host_capability: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    evidence_store: Any = None,
    clock: Clock | None = None,
    capability_factory: TaskDispatchCapabilityFactory | None = None,
) -> dict[str, Any]:
    """Idempotently record one bounded event and admit on terminal wake."""
    with _ADMISSION_LOCK:
        raw = _event_bytes(event)
        if len(raw) > MAX_EVENT_BYTES:
            raise PlanTopologyError("worker event exceeds 64 KiB")
        required = {"event_id", "task_id", "sequence", "kind", "at"}
        missing = required.difference(event)
        if missing:
            raise PlanTopologyError(f"worker event missing fields: {sorted(missing)}")
        event_id = str(event["event_id"])
        task_id = str(event["task_id"])
        kind = str(event["kind"])
        if not event_id or task_id not in state["statuses"]:
            raise PlanTopologyError("worker event identity is invalid")
        if kind not in EVENT_KINDS:
            raise PlanTopologyError(f"unknown worker event kind: {kind}")
        if int(event["sequence"]) < 0:
            raise PlanTopologyError("worker event sequence cannot be negative")
        terminal = kind in TERMINAL_EVENT_KINDS
        task = next(row for row in state["tasks"] if row["id"] == task_id)
        if task.get("long_worker") and kind == "complete" and not any(
            row["task_id"] == task_id and row["kind"] == "progress"
            for row in state["events"]
        ):
            raise PlanTopologyError(
                "long worker terminal event requires prior progress event"
            )
        existing = next(
            (row for row in state["events"] if row["event_id"] == event_id), None
        )
        normalized = dict(event)
        if existing is not None:
            if existing != normalized:
                raise PlanTopologyError("worker event id collision")
            return {"schema": "taskplane.worker-event-result/v1", "status": "duplicate", "terminal": kind in TERMINAL_EVENT_KINDS}
        if len(state["events"]) >= int(state.get("event_queue_cap") or DEFAULT_EVENT_QUEUE_CAP):
            raise PlanTopologyError("worker event queue cap reached")
        state["events"].append(normalized)
        state["events"].sort(key=lambda row: (str(row["task_id"]), int(row["sequence"]), str(row["event_id"])))
        admission = None
        if terminal:
            status = {
                "complete": "complete",
                "attention": "attention",
                "partial-host": "attention",
                "failed": "failed",
                "cancelled": "cancelled",
            }[kind]
            state["statuses"][task_id] = status
            state["in_flight"].pop(task_id, None)
            state["task_times"].setdefault(task_id, {})["terminal"] = float(event["at"])
            if all(value is not None for value in (
                host_capability, budget, clock, capability_factory,
            )):
                admission = _admit_ready_batch_locked(
                    state, host_capability or {}, budget or {}, evidence_store,
                    clock, capability_factory=capability_factory,
                )
        return {
            "schema": "taskplane.worker-event-result/v1",
            "status": "recorded",
            "terminal": terminal,
            "attention": kind == "partial-host",
            "admission": admission,
        }


def wait_for_worker_events(
    state: MutableMapping[str, Any], waiter: EventWaiter, *, clock: Clock,
    host_capability: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    evidence_store: Any = None,
    capability_factory: TaskDispatchCapabilityFactory | None = None,
) -> dict[str, Any]:
    """Perform the incumbent one-shot 1800-second event wait."""
    outstanding = sorted(state.get("in_flight") or {})
    policy = {"mode": "event", "timeout_seconds": 1800, "scheduled_polling": False}
    events = waiter.wait(policy, outstanding)
    results = [
        record_worker_event(
            state, event,
            host_capability=host_capability, budget=budget,
            evidence_store=evidence_store, clock=clock,
            capability_factory=capability_factory,
        )
        for event in events
    ]
    return {
        "schema": "taskplane.worker-event-wake/v1",
        "event_count": len(results),
        "terminal": any(result["terminal"] for result in results),
        "results": results,
    }


def append_replan_generation(
    execution_dag: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]], clock: Clock,
) -> dict[str, Any]:
    """Append one immutable generation and explicit supersession edges."""
    if execution_dag.get("schema") != EXECUTION_DAG_SCHEMA:
        raise PlanTopologyError("invalid execution DAG schema")
    result = deepcopy(dict(execution_dag))
    rows = _task_rows(tasks)
    generation = len(result["generations"])
    prefix = f"g{generation}"
    previous = generation - 1
    result["generations"].append({
        "generation": generation,
        "at": float(clock.wall_time()),
        "task_ids": sorted(row["id"] for row in rows),
    })
    existing_previous = {
        str(node["task_id"]) for node in result["nodes"]
        if int(node["generation"]) == previous
    }
    for row in sorted(rows, key=lambda item: item["id"]):
        result["nodes"].append({
            "id": f"{prefix}:{row['id']}",
            "task_id": row["id"],
            "generation": generation,
        })
        for dependency in row["deps"]:
            result["edges"].append([
                f"{prefix}:{dependency}", f"{prefix}:{row['id']}", "dependency"
            ])
        if row["id"] in existing_previous:
            result["edges"].append([
                f"g{previous}:{row['id']}", f"{prefix}:{row['id']}", "supersession"
            ])
    result["fingerprint"] = content_fingerprint({
        key: value for key, value in result.items() if key != "fingerprint"
    })
    return result


def execution_metrics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the Retro parallelism and duration-weighted critical path."""
    times = {
        task_id: values for task_id, values in (state.get("task_times") or {}).items()
        if values.get("start") is not None and values.get("terminal") is not None
    }
    if not times:
        return {
            "schema": "taskplane.execution-metrics/v1",
            "active_worker_seconds": 0,
            "delivery_wall_seconds": 0,
            "parallelism_factor": 0,
            "longest_serial_chain": {"tasks": [], "seconds": 0},
            "scheduler_caused_idle_seconds": float(state.get("scheduler_caused_idle_seconds") or 0),
        }
    durations = {
        task_id: max(0.0, float(values["terminal"]) - float(values["start"]))
        for task_id, values in times.items()
    }
    starts = [float(values["start"]) for values in times.values()]
    terminals = [float(values["terminal"]) for values in times.values()]
    wall = max(terminals) - min(starts)
    active = sum(durations.values())
    dependencies = state["topology"]["effective_dependencies"]
    cache: dict[str, tuple[float, list[str]]] = {}

    def critical(task_id: str) -> tuple[float, list[str]]:
        if task_id in cache:
            return cache[task_id]
        candidates = [
            critical(dependency) for dependency in dependencies.get(task_id, ())
            if dependency in durations
        ]
        predecessor_seconds, predecessor_tasks = max(
            candidates, key=lambda item: (item[0], [-ord(ch) for ch in "/".join(item[1])]),
            default=(0.0, []),
        )
        cache[task_id] = (
            predecessor_seconds + durations[task_id],
            [*predecessor_tasks, task_id],
        )
        return cache[task_id]

    longest_seconds, longest_tasks = max(
        (critical(task_id) for task_id in sorted(durations)),
        key=lambda item: (item[0], [-ord(ch) for ch in "/".join(item[1])]),
    )
    return {
        "schema": "taskplane.execution-metrics/v1",
        "active_worker_seconds": active,
        "delivery_wall_seconds": wall,
        "parallelism_factor": active / wall if wall else 0,
        "longest_serial_chain": {"tasks": longest_tasks, "seconds": longest_seconds},
        "scheduler_caused_idle_seconds": float(state.get("scheduler_caused_idle_seconds") or 0),
    }
