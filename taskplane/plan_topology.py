"""Pure Plan topology classification and trace-derived execution metrics.

A declared test file is a build artifact: when it does not exist in the
admitted source and another task owns that path, the producer is an implicit
predecessor of the consumer even if edit scopes are otherwise disjoint. Host
concurrency and agent lifecycle remain owned by the native Codex runtime.
"""

from __future__ import annotations

import fnmatch
import math
from pathlib import Path
import shlex
from typing import Any, Iterable, Mapping, Sequence

try:
    from .delivery_ports import content_fingerprint
except ImportError:  # pragma: no cover - direct module loading
    from delivery_ports import content_fingerprint  # type: ignore


TOPOLOGY_SCHEMA = "taskplane.plan-topology/v1"


class PlanTopologyError(RuntimeError):
    """The Plan topology or trace-derived metrics are structurally unsafe."""


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanTopologyError(f"{label} must be numeric")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise PlanTopologyError(f"{label} must be finite") from exc
    if not math.isfinite(normalized):
        raise PlanTopologyError(f"{label} must be finite")
    return normalized



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


def execution_metrics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Compute parallelism and the duration-weighted critical path from trace state."""
    times = {}
    for task_id, values in (state.get("task_times") or {}).items():
        if values.get("start") is None or values.get("terminal") is None:
            continue
        times[task_id] = {
            "start": _finite_number(
                values["start"], f"task {task_id} start time",
            ),
            "terminal": _finite_number(
                values["terminal"], f"task {task_id} terminal time",
            ),
        }
    if not times:
        return {
            "schema": "taskplane.execution-metrics/v1",
            "active_worker_seconds": 0,
            "delivery_wall_seconds": 0,
            "parallelism_factor": 0,
            "longest_serial_chain": {"tasks": [], "seconds": 0},
        }
    durations = {
        task_id: max(0.0, values["terminal"] - values["start"])
        for task_id, values in times.items()
    }
    durations = {
        task_id: _finite_number(value, f"task {task_id} active time")
        for task_id, value in durations.items()
    }
    starts = [values["start"] for values in times.values()]
    terminals = [values["terminal"] for values in times.values()]
    wall = _finite_number(
        max(terminals) - min(starts), "delivery wall time",
    )
    active = _finite_number(sum(durations.values()), "active worker time")
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
    longest_seconds = _finite_number(
        longest_seconds, "longest serial chain time",
    )
    parallelism = _finite_number(
        active / wall if wall else 0, "parallelism factor",
    )
    return {
        "schema": "taskplane.execution-metrics/v1",
        "active_worker_seconds": active,
        "delivery_wall_seconds": wall,
        "parallelism_factor": parallelism,
        "longest_serial_chain": {"tasks": longest_tasks, "seconds": longest_seconds},
    }
