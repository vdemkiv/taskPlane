"""Pure Plan topology classification and trace-derived execution metrics.

A declared test file is a build artifact: when it does not exist in the
admitted source and another task owns that path, the producer is an implicit
predecessor of the consumer even if edit scopes are otherwise disjoint. Host
concurrency and agent lifecycle remain owned by the native Codex runtime.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from pathlib import Path
import shlex
from typing import Any, Iterable, Mapping, Sequence

try:
    from .delivery_ports import content_fingerprint
except ImportError:  # pragma: no cover - direct module loading
    from delivery_ports import content_fingerprint  # type: ignore


TOPOLOGY_SCHEMA = "taskplane.plan-topology/v1"
SEALED_READY_SET_SCHEMA = "taskplane.sealed-ready-set/v1"
PLAN_DASHBOARD_SCHEMA = "taskplane.dashboard-plan-task-dag/v1"
PLAN_WAVES_DASHBOARD_SCHEMA = "taskplane.dashboard-plan-waves/v1"


class PlanTopologyError(RuntimeError):
    """The Plan topology or trace-derived metrics are structurally unsafe."""


def canonical_plan_fingerprint(plan: Mapping[str, Any]) -> str:
    """Return the exact fingerprint used by the Plan approval receipt.

    The loop seals the complete committed ``plan/tasks.json`` object rather
    than a renderer-selected subset.  Keeping that byte-independent canonical
    rule here lets presentation code prove approval without inventing a second
    approval flag.
    """
    if not isinstance(plan, Mapping):
        raise PlanTopologyError("Plan dashboard source must be an object")
    try:
        encoded = json.dumps(
            dict(plan), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanTopologyError("Plan dashboard source is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


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


def _topological_order(
    dependencies: Mapping[str, Sequence[str]],
) -> list[str]:
    """Return one deterministic order or refuse a cyclic Plan."""
    _descendants(dependencies)  # validates the complete graph first
    remaining = {task_id: set(values)
                 for task_id, values in dependencies.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(task_id for task_id, deps in remaining.items()
                       if not deps)
        if not ready:  # defensive; _descendants already rejects this
            raise PlanTopologyError("task dependency graph contains a cycle")
        for task_id in ready:
            order.append(task_id)
            remaining.pop(task_id)
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


def _dashboard_waves(
    raw_waves: object, *, task_ids: Sequence[str],
    dependencies: Mapping[str, Sequence[str]], approval: str,
) -> list[dict[str, Any]]:
    """Validate and normalize the Plan-authored wave partition."""
    if not isinstance(raw_waves, list) or not raw_waves:
        raise PlanTopologyError("Plan dashboard waves must be a non-empty list")
    known = set(task_ids)
    seen_wave_ids: set[str] = set()
    seen_tasks: set[str] = set()
    task_wave: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_waves):
        if not isinstance(raw, Mapping):
            raise PlanTopologyError("every Plan dashboard wave must be an object")
        wave_id = str(raw.get("id") or "").strip()
        if not wave_id or wave_id in seen_wave_ids:
            raise PlanTopologyError("Plan dashboard wave ids must be unique")
        seen_wave_ids.add(wave_id)
        tasks = raw.get("parallel")
        if not isinstance(tasks, list) or not tasks or any(
                not isinstance(task_id, str) or not task_id.strip()
                for task_id in tasks):
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} has invalid task membership")
        members = [task_id.strip() for task_id in tasks]
        if len(set(members)) != len(members):
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} repeats a task")
        unknown = sorted(set(members) - known)
        repeated = sorted(set(members) & seen_tasks)
        if unknown:
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} has unknown tasks: {unknown}")
        if repeated:
            raise PlanTopologyError(
                f"Plan dashboard tasks occur in multiple waves: {repeated}")
        after = raw.get("after") or []
        if not isinstance(after, list) or any(
                not isinstance(task_id, str) or not task_id.strip()
                for task_id in after):
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} has invalid predecessors")
        after_ids = [task_id.strip() for task_id in after]
        if sorted(set(after_ids) - known):
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} has unknown predecessors")
        if any(task_id not in seen_tasks for task_id in after_ids):
            raise PlanTopologyError(
                f"Plan dashboard wave {wave_id} precedes its after-task")
        for task_id in members:
            task_wave[task_id] = index
        seen_tasks.update(members)
        normalized.append({
            "id": wave_id,
            "index": index,
            "tasks": members,
            "after": after_ids,
            "serialization": str(raw.get("serialization") or ""),
            "approval": approval,
        })
    missing = sorted(known - seen_tasks)
    if missing:
        raise PlanTopologyError(
            f"Plan dashboard waves omit tasks: {missing}")
    for task_id, deps in dependencies.items():
        for dependency in deps:
            if task_wave[dependency] >= task_wave[task_id]:
                raise PlanTopologyError(
                    f"Plan dashboard wave order violates {dependency}->{task_id}")
    return normalized


def _exact_plan_approval(
    plan: Mapping[str, Any], receipt: Mapping[str, Any] | None,
    *, plan_fingerprint: str,
) -> tuple[str, str | None]:
    """Return approved only for a valid receipt over this complete Plan."""
    if not isinstance(receipt, Mapping):
        return "planned", None
    try:
        try:
            from . import delivery_policy
        except ImportError:  # pragma: no cover - direct module loading
            import delivery_policy  # type: ignore
        checked = delivery_policy.validate_delivery_mode_receipt(receipt)
    except Exception:
        return "planned", None
    matches = (
        checked.get("plan_fingerprint") == plan_fingerprint
        and checked.get("requirement") == str(plan.get("requirement") or "")
        and checked.get("mode") == plan.get("delivery_mode")
        and checked.get("automatic_lenses") == plan.get("automatic_lenses")
        and checked.get("plan_authority") == plan.get("plan_authority")
    )
    if not matches:
        return "planned", None
    return "approved", str(checked.get("fingerprint") or "") or None


def dashboard_plan_projection(
    plan: Mapping[str, Any], *,
    approval_receipt: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project the current Plan DAG and waves without becoming authority.

    The complete Plan is fingerprinted before any presentation field is
    selected.  Wave membership is checked against the declared DAG, and the
    word ``approved`` appears only when the incumbent closed delivery-mode
    receipt validates and binds that exact full-Plan fingerprint.
    """
    if not isinstance(plan, Mapping) or plan.get("schema") != \
            "taskplane.plan/v1":
        raise PlanTopologyError("Plan dashboard source schema is invalid")
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or any(
            not isinstance(row, Mapping) for row in raw_tasks):
        raise PlanTopologyError("Plan dashboard tasks must be a list of objects")
    rows = _task_rows(raw_tasks)
    dependencies = {row["id"]: list(row["deps"]) for row in rows}
    order = _topological_order(dependencies)
    plan_fingerprint = canonical_plan_fingerprint(plan)
    approval, receipt_fingerprint = _exact_plan_approval(
        plan, approval_receipt, plan_fingerprint=plan_fingerprint)
    edges = [
        {"from": dependency, "to": row["id"], "kind": "depends"}
        for row in rows for dependency in row["deps"]
    ]
    edges.sort(key=lambda row: (row["from"], row["to"]))
    tasks = [{
        "id": row["id"],
        "deps": list(row["deps"]),
        "scope": list(row["scope"]),
        "status": str(row.get("status") or "pending"),
    } for row in rows]
    waves = _dashboard_waves(
        plan.get("waves"), task_ids=[row["id"] for row in rows],
        dependencies=dependencies, approval=approval)
    dag_material = {
        "schema": PLAN_DASHBOARD_SCHEMA,
        "source": "plan/tasks.json#/tasks",
        "plan_fingerprint": plan_fingerprint,
        "tasks": tasks,
        "edges": edges,
        "task_total": len(tasks),
        "edge_total": len(edges),
        "topological_order": order,
    }
    wave_material = {
        "schema": PLAN_WAVES_DASHBOARD_SCHEMA,
        "source": "plan/tasks.json#/waves",
        "plan_fingerprint": plan_fingerprint,
        "waves": waves,
        "wave_total": len(waves),
        "approval": approval,
        "approval_receipt_fingerprint": receipt_fingerprint,
    }
    return {
        "dag": {**dag_material,
                "fingerprint": content_fingerprint(dag_material)},
        "waves": {**wave_material,
                  "fingerprint": content_fingerprint(wave_material)},
    }


def _ready_task_fingerprint(row: Mapping[str, Any], *,
                            effective_dependencies: Sequence[str]) -> str:
    """Bind a ready-set member to the immutable execution-facing Plan row."""
    return content_fingerprint({
        "id": str(row.get("id") or ""),
        "scope": sorted({_path(value) for value in row.get("scope") or ()
                         if _path(value)}),
        "deps": sorted(str(value) for value in effective_dependencies),
        "tests": row.get("tests"),
        "req": row.get("req"),
        "contracts": sorted(str(value) for value in
                            row.get("contracts") or ()),
    })


def seal_ready_set(
    tasks: Sequence[Mapping[str, Any]], *, passed: Iterable[str],
    repository_files: Iterable[str] | None = None,
    allow_isolated_variants: bool = False,
) -> dict[str, Any]:
    """Classify readiness once and seal one deterministic native set.

    The receipt is intent, not host admission.  Every pending member is
    represented exactly once as either ready or held, and every held row
    names the dependency or ownership fact that serialized it.  Consumers
    validate the fingerprint and Plan-row bindings; they never classify the
    tasks a second time or truncate the ready set by host capacity.
    """
    rows = _task_rows(tasks)
    by_id = {row["id"]: row for row in rows}
    declared_test_files = {
        path for row in rows
        for path in _declared_test_files(row.get("tests"))
    }
    repository_test_files = sorted(_repository_inventory(
        declared_test_files, repository_files))
    topology = classify_plan(
        rows, repository_files=repository_test_files)
    passed_ids = sorted({str(value) for value in passed})
    unknown_passed = sorted(set(passed_ids) - set(by_id))
    if unknown_passed:
        raise PlanTopologyError(
            f"ready-set passed ids are unknown: {unknown_passed}")
    pair_map = {
        frozenset((str(row["left"]), str(row["right"]))): row
        for row in topology["pairs"]
    }
    members: list[dict[str, Any]] = []
    held: list[dict[str, str]] = []
    for task_id in topology["task_ids"]:
        task = by_id[task_id]
        if task.get("status", "pending") != "pending":
            continue
        dependencies = list(topology["effective_dependencies"][task_id])
        unmet = sorted(set(dependencies) - set(passed_ids))
        if unmet:
            pair = next((
                pair_map[frozenset((task_id, dependency))]
                for dependency in unmet
                if frozenset((task_id, dependency)) in pair_map
            ), None)
            held.append({
                "task_id": task_id,
                "reason": "waiting on deps: " + ",".join(unmet),
                "shared_owner": str((pair or {}).get("shared_owner") or
                                    f"dependency:{unmet[0]}"),
            })
            continue
        missing = list(
            (topology.get("missing_test_assets") or {}).get(task_id) or [])
        if missing:
            held.append({
                "task_id": task_id,
                "reason": "missing test assets: " + ",".join(missing),
                "shared_owner": "test-artifact:" + missing[0],
            })
            continue
        blocking_pair = next((
            pair_map[frozenset((task_id, str(member["task_id"])))]
            for member in members
            if pair_map[frozenset((task_id, str(member["task_id"])))]
            ["disposition"] == "serialized"
            and not (
                allow_isolated_variants
                and task.get("variant")
                and by_id[str(member["task_id"])].get("variant")
                and task.get("variant") !=
                by_id[str(member["task_id"])].get("variant")
            )
        ), None)
        if blocking_pair is not None:
            held.append({
                "task_id": task_id,
                "reason": "serialized by " +
                          str(blocking_pair["shared_owner"]),
                "shared_owner": str(blocking_pair["shared_owner"]),
            })
            continue
        scope = list(task["scope"])
        members.append({
            "task_id": task_id,
            "scope": scope,
            "effective_dependencies": dependencies,
            "task_fingerprint": _ready_task_fingerprint(
                task, effective_dependencies=dependencies),
        })

    material = {
        "schema": SEALED_READY_SET_SCHEMA,
        "topology_fingerprint": topology["fingerprint"],
        "source_tasks_fingerprint": content_fingerprint(rows),
        # Preserve the exact admitted test-artifact view so validation can
        # reconstruct the approved topology without consulting a later
        # working tree.  Only declared test files are relevant here.
        "repository_test_files": repository_test_files,
        "allow_isolated_variants": bool(allow_isolated_variants),
        "passed_ids": passed_ids,
        "members": members,
        "held": held,
    }
    return {**material, "fingerprint": content_fingerprint(material)}


def validate_ready_set(
    receipt: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate a sealed set against its Plan-bound admitted topology."""
    if not isinstance(receipt, Mapping):
        raise PlanTopologyError("sealed ready set is missing")
    required = {
        "schema", "topology_fingerprint", "source_tasks_fingerprint",
        "repository_test_files", "allow_isolated_variants",
        "passed_ids", "members", "held", "fingerprint",
    }
    if set(receipt) != required or \
            receipt.get("schema") != SEALED_READY_SET_SCHEMA:
        raise PlanTopologyError("sealed ready set schema is invalid")
    material = {key: receipt[key] for key in required - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(material):
        raise PlanTopologyError("sealed ready set fingerprint is invalid")
    rows = _task_rows(tasks)
    if receipt.get("source_tasks_fingerprint") != content_fingerprint(rows):
        raise PlanTopologyError("sealed ready set does not bind the Plan")
    if not isinstance(receipt.get("topology_fingerprint"), str) or \
            not receipt["topology_fingerprint"].strip():
        raise PlanTopologyError("sealed ready set topology is missing")
    passed_ids = receipt.get("passed_ids")
    if not isinstance(passed_ids, list) or \
            any(not isinstance(task_id, str) or not task_id
                for task_id in passed_ids) or \
            passed_ids != sorted(set(passed_ids)):
        raise PlanTopologyError("sealed ready set passed ids are invalid")
    repository_test_files = receipt.get("repository_test_files")
    if not isinstance(repository_test_files, list) or \
            any(not isinstance(path, str) or not _path(path)
                for path in repository_test_files) or \
            repository_test_files != sorted(set(repository_test_files)):
        raise PlanTopologyError(
            "sealed ready set repository test files are invalid")
    if not isinstance(receipt.get("allow_isolated_variants"), bool):
        raise PlanTopologyError(
            "sealed ready set variant policy is invalid")
    by_id = {row["id"]: row for row in rows}
    pending_ids = {
        row["id"] for row in rows
        if row.get("status", "pending") == "pending"
    }
    members = receipt.get("members")
    held = receipt.get("held")
    if not isinstance(members, list) or not isinstance(held, list):
        raise PlanTopologyError("sealed ready set rows are invalid")
    member_ids: list[str] = []
    for member in members:
        if not isinstance(member, Mapping) or set(member) != {
                "task_id", "scope", "effective_dependencies",
                "task_fingerprint"}:
            raise PlanTopologyError("sealed ready member is invalid")
        task_id = str(member.get("task_id") or "")
        row = by_id.get(task_id)
        dependencies = member.get("effective_dependencies")
        if row is None or not isinstance(dependencies, list) or \
                any(not isinstance(dependency, str) or not dependency
                    for dependency in dependencies) or \
                dependencies != sorted(set(dependencies)) or \
                member.get("scope") != row["scope"] or \
                member.get("task_fingerprint") != _ready_task_fingerprint(
                    row, effective_dependencies=dependencies):
            raise PlanTopologyError(
                f"sealed ready member does not bind the Plan: {task_id}")
        member_ids.append(task_id)
    held_ids: list[str] = []
    for row in held:
        if not isinstance(row, Mapping) or set(row) != {
                "task_id", "reason", "shared_owner"} or \
                not str(row.get("reason") or "").strip() or \
                not str(row.get("shared_owner") or "").strip():
            raise PlanTopologyError("sealed held member is invalid")
        held_ids.append(str(row.get("task_id") or ""))
    all_ids = member_ids + held_ids
    if len(set(all_ids)) != len(all_ids) or set(all_ids) != pending_ids:
        raise PlanTopologyError(
            "sealed ready set must cover each pending task exactly once")
    # Rebuild the canonical partition from the Plan and the admitted
    # test-artifact view.  This proves every held dependency/reason/owner and
    # every ready member came from the sealed topology; rehashing an invented
    # held row is insufficient.
    canonical = seal_ready_set(
        rows, passed=passed_ids,
        repository_files=repository_test_files,
        allow_isolated_variants=receipt["allow_isolated_variants"],
    )
    if canonical != dict(receipt):
        raise PlanTopologyError(
            "sealed ready set does not match its approved topology")
    return dict(receipt)


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
