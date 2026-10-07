"""Phase output validation and fingerprints; evidence never grants approval."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
import stat
from typing import Any
import uuid

from . import workflow as w
from .depgraph import GRAPH_SCAN_QUALITY_SCHEMA, scan_quality, source_inputs_current
from .primitives import content_fingerprint

TASK_OBSERVATIONS = {"status", "started_at", "completed_at", "updated_at", "elapsed_seconds"}
TASK_DEFINITIONS = "taskplane.task-definitions/v1"
VERIFICATION_STRATEGY = "taskplane.verification-strategy/v1"
VERIFICATION_HISTORY = "taskplane.verification-history/v1"
CHECK_KINDS = {"static", "unit", "roundtrip", "browser", "integration", "independent"}
CHECK_ENVIRONMENTS = {"fixture", "local", "deployed"}

EMPTY_LIST_FIELDS = {"non_goals", "dependencies", "finding_references", "known_gaps",
                     "findings", "source_locations", "remaining_risk", "unknowns_and_failures",
                     "deferred_items_and_owners"}


def substantive(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and any(substantive(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and any(substantive(v) for v in value)
    return False


def criterion_map(value: Any, criteria: list[str], label: str) -> None:
    w.require(isinstance(value, dict) and set(value) == set(criteria)
              and all(substantive(v) for v in value.values()),
              "invalid_evidence", f"{label} must map every criterion to substantive evidence.")


def path(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    w.require(bool(parts) and not Path(relative).is_absolute() and
              not any(p in ("..", ".git", ".codex", ".agents") or any(c in p for c in "*?[]")
                      for p in parts), "scope_violation", "Use a literal workspace path outside protected metadata.")
    target = root.resolve()
    for part in parts:
        target = target / part
        w.require(not target.is_symlink(), "scope_violation", f"Symlink path is not permitted: {relative}")
    w.require(target.is_relative_to(root.resolve()), "scope_violation", "Path escapes workspace.")
    return target


def read(root: Path, relative: str) -> bytes:
    target = path(root, relative)
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid_evidence", "Evidence must be a regular file.")
            return stream.read()
    except OSError as exc:
        raise w.Refusal("invalid_evidence", f"Evidence unavailable: {relative}: {exc}") from None


def object_file(root: Path, relative: str) -> dict[str, Any]:
    try:
        data = json.loads(read(root, relative))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, w.Refusal):
            raise
        raise w.Refusal("invalid_evidence", f"Invalid JSON evidence: {relative}") from None
    w.require(isinstance(data, dict), "invalid_evidence", f"Expected an object: {relative}")
    return dict(data)


def manifest(root: Path, paths: list[str], *, allow_missing: bool = False,
             task_path: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for relative in sorted(set(paths)):
        target = path(root, relative)
        if allow_missing and not target.exists():
            result[relative] = None
        elif relative == task_path:
            data = object_file(root, relative)
            rows = task_dag(data, [])
            normalized = {**data, "tasks": [{k:v for k,v in row.items() if k not in TASK_OBSERVATIONS} for row in rows]}
            result[relative] = {"schema": TASK_DEFINITIONS, "digest": content_fingerprint(normalized)}
        else:
            result[relative] = content_fingerprint(read(root, relative))
    return result


def valid_scope(root: Path, scope: dict[str, Any]) -> None:
    w.validate_scope(scope)
    for paths in [*scope["paths"].values(), scope.get("verification_inputs", [])]:
        for value in paths:
            w.require(isinstance(value, str), "invalid_evidence", "Scope paths must be strings.")
            path(root, value)


def task_criteria(task: dict[str, Any]) -> list[str]:
    """Resolve the two supported spellings without accepting contradictory scope."""
    ids = task.get("criteria", task.get("acceptance_criteria", []))
    w.require(isinstance(ids, list) and all(isinstance(c, str) for c in ids),
              "invalid_evidence", "Task has invalid criterion IDs.")
    w.require("criteria" not in task or "acceptance_criteria" not in task
              or task["criteria"] == task["acceptance_criteria"],
              "invalid_evidence", "Task criterion aliases conflict.")
    return list(ids)


def execution_fields(rows: list[dict[str, Any]], *, required: bool = False,
                     phase: str | None = None, root: str | None = None) -> None:
    lenses: set[str] = set()
    for row in rows:
        mode = row.get("execution")
        w.require(mode in ("native_required", "root") or not required and "execution" not in row,
                  "invalid_evidence", f"Task {row.get('id')} needs valid execution metadata.")
        if mode == "root":
            w.require(all(isinstance(row.get(key), str) and row[key].strip()
                          for key in ("execution_reason", "execution_reference")),
                      "invalid_evidence", "Root execution needs its reason and exception reference.")
            if root is not None:
                w.require(row.get("owner") in {"root", root}, "invalid_evidence",
                          "Root execution conflicts with native ownership.")
        else:
            w.require(not any(key in row for key in ("execution_reason", "execution_reference", "execution_exception")),
                      "invalid_evidence", "Root execution exception conflicts with native or untyped execution.")
        if "review_lens" in row:
            lens = row["review_lens"]
            w.require(isinstance(lens, str) and lens.strip() and lens not in lenses
                      and mode in {"native_required", "root"}
                      and (row.get("phase", phase) in (None, "engineering")), "invalid_evidence",
                      "Each review_lens needs one typed Engineering task and unique lens ID.")
            lenses.add(lens)


def execution_preflight(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Validate specific serial exceptions against this task graph and observations.

    Free text is retained for review, never classified using a blacklist of words.
    Legacy native-default/v1 scopes remain readable under their original contract.
    """
    strict = state["scope"].get("planning_contract") == w.PLANNING_CONTRACT
    phase = w.current(state)["phase"]
    index = {row["id"]: row for row in rows}

    def ancestors(key: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        w.require(key not in seen, "invalid_evidence", "Serial exception has cyclic dependencies.")
        seen = seen | {key}
        result: set[str] = set()
        for dependency in index[key].get("dependencies", []):
            w.require(dependency in index, "invalid_evidence", "Serial exception names a missing prerequisite.")
            result.add(dependency)
            result.update(ancestors(dependency, seen))
        return result

    for row in rows:
        exception = row.get("execution_exception")
        if row.get("execution") != "root" or not (strict or exception is not None):
            continue
        key = row["id"]
        task_phase = row.get("phase", phase)
        w.require(isinstance(exception, dict) and exception.get("schema") == "taskplane.execution-exception/v1"
                  and exception.get("task") == key, "invalid_evidence",
                  f"Root task {key} needs a task-specific execution_exception; historical failures alone do not justify serial work.")
        assert isinstance(exception, dict)
        basis = exception.get("basis")
        if basis == "trivial":
            w.require(sum(other.get("phase", phase) == task_phase for other in rows) == 1,
                      "invalid_evidence", "A trivial serial exception requires one integrated task in its phase.")
        elif basis in ("integration", "task_conflict"):
            related = _strings(exception.get("related_tasks"), "Serial exception related tasks")
            w.require(all(other in index and other != key
                          and index[other].get("phase", phase) == task_phase for other in related),
                      "invalid_evidence", "Serial exception must identify other tasks in the same phase.")
            cited = set(_strings(exception.get("paths"), "Serial exception paths"))
            actual: set[str] = set()
            for other in related:
                peer = index[other]
                reads, peer_reads = set(read_inputs(state, row)), set(read_inputs(state, peer))
                writes, peer_writes = set(row["paths"]), set(peer["paths"])
                if basis == "integration":
                    conflict = reads & peer_writes
                    ordered = other in ancestors(key)
                else:
                    conflict = (writes & (peer_writes | peer_reads)) | (reads & peer_writes)
                    ordered = other in ancestors(key) or key in ancestors(other)
                w.require(conflict & cited and ordered, "invalid_evidence",
                          f"Serial {basis} for {key} needs a current read/write relationship and dependency with {other}.")
                actual.update(conflict)
            w.require(cited <= actual, "invalid_evidence", "Serial exception cites paths outside its actual task relationships.")
        elif basis == "user_serial":
            request = state["scope"].get("serial_execution")
            w.require(isinstance(request, dict) and isinstance(request.get("reference"), str)
                      and request["reference"] == row["execution_reference"]
                      and isinstance(request.get("excerpt"), str) and request["excerpt"].strip()
                      and exception.get("request") == request, "invalid_evidence",
                      "User serial execution needs the exact request reference and excerpt declared in the scope.")
        elif basis == "capability":
            observation = exception.get("observation")
            capacity = state.get("worker_capacity", {})
            visits = {w.current(state)["id"]}
            visits.update(stage["id"] for stage in state["visits"][:state["index"]]
                          if stage["phase"] == "plan" and stage["decision"] == "approved" and not stage["superseded"])
            w.require(isinstance(observation, dict) and observation.get("run") == state["run"]
                      and isinstance(observation.get("visit"), str) and observation["visit"] in visits
                      and capacity.get("binding") == {"run": state["run"], "visit": observation["visit"]}
                      and capacity.get("status") == "unavailable"
                      and capacity.get("effective_limit") == 0 and substantive(capacity.get("reason"))
                      and substantive(capacity.get("observed_at"))
                      and observation.get("observed_at") == capacity.get("observed_at")
                      and observation.get("reference") == capacity.get("reference") == row["execution_reference"],
                      "invalid_evidence", "Capability exception needs the current run's recorded unavailable observation.")
        else:
            raise w.Refusal("invalid_evidence", "Serial exception basis must be integration, task_conflict, trivial, user_serial or capability.")


def task_dag(data: dict[str, Any], criteria: list[str]) -> list[dict[str, Any]]:
    tasks = data.get("tasks")
    w.require(isinstance(tasks, list) and tasks and all(isinstance(t, dict) for t in tasks),
              "invalid_evidence", "A shared task decomposition is required.")
    assert isinstance(tasks, list)
    execution_fields(tasks)
    index: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for t in tasks:
        key = t.get("id")
        w.require(isinstance(key, str) and key and key not in index, "invalid_evidence", "Task IDs must be unique.")
        w.require(t.get("owner") and t.get("verification"), "invalid_evidence", f"Task {key} needs ownership and verification.")
        w.require(isinstance(t.get("dependencies"), list) and all(isinstance(d, str) for d in t["dependencies"]),
                  "invalid_evidence", f"Task {key} needs prerequisite IDs.")
        w.require(isinstance(t.get("paths"), list) and all(isinstance(p, str) for p in t["paths"]),
                  "invalid_evidence", f"Task {key} needs declared paths.")
        for field in ("read_inputs", "readiness_after"):
            if field in t:
                value = t[field]
                w.require(isinstance(value, list) and all(isinstance(p, str) and p for p in value)
                          and len(value) == len(set(value)), "invalid_evidence", f"Task {key} has invalid {field}.")
        if "context_budget_bytes" in t:
            w.require(type(t["context_budget_bytes"]) is int and 16384 <= t["context_budget_bytes"] <= 1048576,
                      "invalid_evidence", "Task context budget must be between 16 KiB and 1 MiB.")
        if "purpose" in t:
            w.require(isinstance(t["purpose"], str) and 0 < len(t["purpose"].strip()) <= 512,
                      "invalid_evidence", "Task purpose must be a short nonempty description.")
        covered.update(task_criteria(t))
        index[key] = t
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(key: str) -> None:
        w.require(key in index and key not in visiting, "invalid_evidence", "Task prerequisite is missing or cyclic.")
        if key in visited:
            return
        visiting.add(key)
        for dep in [*index[key]["dependencies"], *index[key].get("readiness_after", [])]:
            if dep in index[key].get("readiness_after", []):
                w.require(dep in index and index[dep].get("phase") == index[key].get("phase")
                          and index[dep].get("execution") != "root", "invalid_evidence",
                          "Readiness must name a native task in the same phase.")
            walk(dep)
        visiting.remove(key)
        visited.add(key)

    for key in index:
        walk(key)
    w.require(set(criteria) <= covered, "invalid_evidence", "Task plan does not cover every acceptance criterion.")
    return list(index.values())


def task_definitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Canonical task authority; progress never changes an execution grant."""
    return {t["id"]: {**{k: v for k, v in t.items()
                         if k not in TASK_OBSERVATIONS | {"criteria", "acceptance_criteria"}},
                      "criteria": task_criteria(t)} for t in rows}


def context_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("initial_context_tasks", [])
    for stage in state["visits"][:state["index"]]:
        if stage["decision"] == "approved" and not stage.get("superseded") and stage.get("packet"):
            rows = stage["packet"].get("context", {}).get("tasks", rows)
    update = state.get("task_context", {})
    if update.get("visit") == w.current(state)["id"]:
        rows = update["tasks"]
    return list(rows)


def context_task_phase(state: dict[str, Any]) -> str:
    """Phase of the published context, including the gap before a new publication."""
    phase: str = state["visits"][0]["phase"]
    for stage in state["visits"][:state["index"]]:
        if (stage["decision"] == "approved" and not stage.get("superseded")
                and stage.get("packet", {}).get("context", {}).get("tasks") is not None):
            phase = stage["phase"]
    if state.get("task_context", {}).get("visit") == w.current(state)["id"]:
        phase = w.current(state)["phase"]
    return phase


def native_refinement(compilation: dict[str, Any], rows: list[dict[str, Any]], phase: str,
                      *, build_paths: list[str] | None = None, root_id: str = "root") -> None:
    """Preserve native obligations from an already authenticated compilation.

    This pure check neither authenticates a package nor changes state. Build may
    narrow optional paths and split work among compatible native tasks, retaining
    the original nonempty producer. Earlier cumulative obligations remain intact.
    """
    rows = task_dag({"tasks": rows}, [])
    w.require(phase in w.PHASES and all(row.get("phase", phase) in w.PHASES for row in rows),
              "binding_mismatch", "Compiled native obligation refinement has an unknown phase.")
    patterns = compilation["task_patterns"]
    last = max(patterns, key=w.PHASES.index)
    originals = patterns[last]["tasks"]
    phases = set(w.PHASES[:w.PHASES.index(phase) + 1])
    phases.update(row.get("phase", phase) for row in rows)
    if phase == "plan":
        phases.add("build")
    index = {row["id"]: row for row in rows}
    outer = compilation["semantic_scope"]
    planned = set(_strings(build_paths, "Plan write scope", nonempty=False) if build_paths is not None else
                  [p for row in rows if row.get("phase", phase) == "build" for p in row["paths"]])
    w.require(planned <= set(outer["paths"]["build"]), "binding_mismatch",
              "Compiled native obligation refinement exceeds the outer Build scope.")

    def compatible(original: dict[str, Any], candidate: dict[str, Any]) -> bool:
        return (candidate.get("phase") == original["phase"]
                and candidate.get("capability") == original["capability"]
                and candidate.get("review_lens") == original.get("review_lens")
                and candidate.get("execution") == "native_required"
                and candidate.get("owner") not in ("root", root_id)
                and not any(key in candidate for key in
                            ("execution_reason", "execution_reference", "execution_exception"))
                and set(task_criteria(original)) <= set(task_criteria(candidate))
                and set(original["read_inputs"]) <= set(candidate.get(
                    "read_inputs", outer.get("verification_inputs", []))))

    for original in originals:
        if original.get("execution") != "native_required" or original["phase"] not in phases:
            continue
        key = original["id"]
        anchor = index.get(key)
        w.require(anchor is not None and compatible(original, anchor), "binding_mismatch",
                  f"Compiled native obligation {key} must retain its ID, phase, capability, lens, execution, criteria and inputs.")
        assert anchor is not None
        retained = set(original["paths"])
        if original["phase"] == "build":
            retained &= planned
        w.require(retained and retained & set(anchor["paths"]), "binding_mismatch",
                  f"Compiled native obligation {key} needs a nonempty retained producer; placeholders cannot replace it.")
        for relative in sorted(retained):
            owners = [row for row in rows if relative in row["paths"]]
            w.require(len(owners) == 1 and compatible(original, owners[0])
                      and (original["phase"] == "build" or owners[0]["id"] == key),
                      "binding_mismatch", f"Compiled native obligation {key} path {relative} needs exactly one compatible native owner.")


def read_inputs(state: dict[str, Any], definition: dict[str, Any]) -> list[str]:
    """One source-selection contract for delivery, freshness and scheduling.

    Absence retains conservative legacy coverage; an explicit list is frozen
    task authority. Owned paths are added by the consumer, never read dependencies.
    """
    return list(definition.get("read_inputs", state["scope"].get("verification_inputs", [])))


def validate_read_inputs(root: Path, scope: dict[str, Any], row: dict[str, Any]) -> None:
    """Apply the same declared read boundary before any task is frozen."""
    if "read_inputs" not in row:
        return
    allowed = set(scope.get("verification_inputs", [])) | set(scope["paths"].get("build", []))
    w.require(set(row["read_inputs"]) <= allowed, "scope_violation",
              "Task read inputs must be declared run verification or Build paths.")
    for relative in row["read_inputs"]:
        path(root, relative)


def freeze_tasks(root: Path, state: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate an explicit run-bound publication without consulting global files."""
    from copy import deepcopy
    rows = task_dag(data, state["scope"]["criteria"])
    from .workflow_local import verify_workflow
    verify_workflow(root, state, tasks=rows)
    execution_fields(rows, required=state["scope"].get("execution_contract") == "native-default/v1"
                     or state["scope"].get("planning_contract") == w.PLANNING_CONTRACT,
                     phase=w.current(state)["phase"], root=state["root"])
    execution_preflight(state, rows)
    for row in rows:
        phase = row.get("phase", w.current(state)["phase"])
        w.require(phase in state["scope"]["paths"], "invalid_evidence", "Unknown task phase.")
        w.require(set(row["paths"]) <= set(state["scope"]["paths"][phase])
                  and set(task_criteria(row)) <= set(state["scope"]["criteria"]),
                  "scope_violation", "Task publication exceeds accepted paths or criteria.")
        for relative in row["paths"]:
            path(root, relative)
        validate_read_inputs(root, state["scope"], row)
    if w.current(state)["phase"] == "build":
        approved = w.accepted_plan(state)["packet"]["output"]["task_dag"]
        w.require(task_definitions(rows) == task_definitions(approved), "invalid_evidence",
                  "Build task definitions must match the human-accepted Plan.")
    frozen = list(deepcopy(task_definitions(rows)).values())
    w.require(len(json.dumps(frozen).encode()) <= 65536, "invalid_evidence", "Task snapshot exceeds 64 KiB.")
    return frozen


def build_task_map(state: dict[str, Any], tasks: list[dict[str, Any]], value: Any,
                   criteria: list[str]) -> None:
    plan = w.accepted_plan(state)["packet"]["output"]
    approved = task_dag({"tasks": plan["task_dag"]}, criteria)
    # Only observations may change without renewed Plan acceptance. Unknown fields
    # remain normative, so adding a new scope/verification field cannot evade this check.

    w.require(task_definitions(tasks) == task_definitions(approved), "invalid_evidence",
              "Build task definitions must match the human-accepted Plan; only progress observations may change.")
    criterion_map(value, criteria, "Build task/acceptance map")
    for criterion, mapped in value.items():
        allowed = {t["id"] for t in approved if t.get("phase") == "build"
                   and criterion in task_criteria(t)}
        w.require(isinstance(mapped, list) and mapped and all(isinstance(t, str) for t in mapped)
                  and len(set(mapped)) == len(mapped) and set(mapped) <= allowed
                  and set(mapped) <= set(plan["acceptance_coverage"][criterion]),
                  "invalid_evidence", "Build map must name approved Build tasks associated with that criterion.")


def native_lens_conflicts(tasks: list[dict[str, Any]], results: dict[str, Any]) -> set[str]:
    """Find all selected lens tasks sharing one actual accepted reviewer."""
    reviewers: dict[str, list[str]] = {}
    for row in tasks:
        if row.get("execution") == "native_required" and row.get("review_lens"):
            reviewer = results.get(row["id"], {}).get("worker_id")
            if reviewer:
                reviewers.setdefault(reviewer, []).append(row["id"])
    return {task_id for group in reviewers.values() if len(group) > 1 for task_id in group}


def _strings(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    w.require(isinstance(value, list) and (bool(value) or not nonempty)
              and all(isinstance(item, str) and item.strip() for item in value)
              and len(value) == len(set(value)), "invalid_evidence", f"{label} needs unique strings.")
    return list(value)


def typed_plan(output: dict[str, Any]) -> bool:
    return ("build_outputs" in output or isinstance(output.get("verification_strategy"), dict)
            and output["verification_strategy"].get("schema") == VERIFICATION_STRATEGY)


def _plan(state: dict[str, Any]) -> dict[str, Any]:
    plans = [visit for visit in state.get("visits", [])[:state.get("index", 0) + 1]
             if visit.get("phase") == "plan" and visit.get("decision") == "approved"
             and not visit.get("superseded") and visit.get("packet")]
    return plans[-1]["packet"]["output"] if plans else {}


def plan_preflight(root: Path, state: dict[str, Any], output: dict[str, Any],
                   tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Check declared production/read dependencies, never expand their authority."""
    from .workflow_local import verify_workflow
    verify_workflow(root, state, tasks=tasks, build_paths=output.get("write_scope"))
    return _plan_preflight(root, state, output, tasks)


def _plan_preflight(root: Path, state: dict[str, Any], output: dict[str, Any],
                    tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan checks after the public caller authenticated its workflow binding."""
    strict = (state["scope"].get("execution_contract") == "native-default/v1"
              or state["scope"].get("planning_contract") == w.PLANNING_CONTRACT or typed_plan(output))
    if not strict:
        return {"status": "legacy_untyped"}
    planned = set(_strings(output.get("write_scope"), "Plan write scope"))
    outer = set(state["scope"]["paths"]["build"])
    w.require(planned <= outer, "scope_violation", "Plan writes exceed the authorized Build scope.")
    execution_preflight(state, tasks)
    intent = state["scope"].get("implementation_intent")
    if intent is not None:
        w.scope_preflight(state["scope"])
        required = set(intent["implementation_paths"]) | set(intent["build_outputs"].values())
        required.update(set(intent["test_paths"]) & outer)
        w.require(required <= planned, "scope_violation",
                  "Plan cannot remove the declared implementation, owned tests or required Build evidence.")
    index = {row["id"]: row for row in tasks}
    build = {key: row for key, row in index.items() if row.get("phase") == "build"}
    owners: dict[str, str] = {}
    for key, row in build.items():
        validate_read_inputs(root, state["scope"], row)
        for relative in _strings(row.get("paths"), f"Task {key} paths"):
            path(root, relative)
            w.require(relative in planned and relative not in owners, "invalid_evidence",
                      "Every Build path needs exactly one declared task owner.")
            owners[relative] = key
    w.require(set(owners) == planned, "invalid_evidence", "Plan write scope needs exact Build ownership.")
    outputs = output.get("build_outputs")
    w.require(isinstance(outputs, list) and outputs, "invalid_evidence",
              "Plan needs declared Build packet, report and verification history outputs.")
    assert isinstance(outputs, list)
    kinds: list[str] = []
    paths: set[str] = set()
    for row in outputs:
        w.require(isinstance(row, dict) and row.get("kind") in {"packet", "report", "verification_history"}
                  and isinstance(row.get("path"), str) and row["path"] not in paths
                  and row.get("task") in build and owners.get(row["path"]) == row["task"],
                  "invalid_evidence", "Build output needs a unique declared path and matching Build task owner.")
        paths.add(row["path"])
        kinds.append(row["kind"])
    w.require(kinds.count("packet") == 1 and "report" in kinds and "verification_history" in kinds,
              "invalid_evidence", "Plan requires one Build packet and at least one report and verification history.")
    if intent is not None:
        w.require(all(any(row["kind"] == kind and row["path"] == relative for row in outputs)
                      for kind, relative in intent["build_outputs"].items()), "invalid_evidence",
                  "Plan Build outputs must retain the implementation intent's declared evidence paths.")
    strategy = output.get("verification_strategy")
    w.require(isinstance(strategy, dict) and strategy.get("schema") == VERIFICATION_STRATEGY
              and isinstance(strategy.get("checks"), list) and strategy["checks"],
              "invalid_evidence", "Plan needs a typed verification strategy.")
    assert isinstance(strategy, dict)
    allowed_reads = set(state["scope"].get("verification_inputs", [])) | outer
    ids: set[str] = set()
    order = output.get("integration_order", [])
    w.require(isinstance(order, list) and set(order) == set(index), "invalid_evidence",
              "Plan integration order must identify its tasks.")

    def ancestors(key: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else set(seen)
        seen.add(key)
        result: set[str] = set()
        for dependency in index[key].get("dependencies", []):
            w.require(dependency in index and dependency not in seen, "invalid_evidence",
                      "Verification prerequisite is missing or cyclic.")
            result.add(dependency)
            result.update(ancestors(dependency, seen))
        return result

    for check in strategy["checks"]:
        w.require(isinstance(check, dict) and isinstance(check.get("id"), str) and check["id"]
                  and check["id"] not in ids and isinstance(check.get("name"), str) and check["name"].strip()
                  and check.get("task") in build and check.get("kind") in CHECK_KINDS
                  and check.get("environment") in CHECK_ENVIRONMENTS and type(check.get("required")) is bool,
                  "invalid_evidence", "Verification checks need unique IDs, Build task, kind, environment and required flag.")
        ids.add(check["id"])
        task = build[check["task"]]
        criteria = _strings(check.get("criteria"), "Check criteria")
        w.require(set(criteria) <= set(task_criteria(task)) & set(state["scope"]["criteria"]),
                  "invalid_evidence", "Check criteria must belong to its declared task.")
        command = check.get("command")
        w.require(isinstance(command, list) and command and all(isinstance(arg, str) and arg for arg in command),
                  "invalid_evidence", "Check command must be an explicit argument vector.")
        inputs = _strings(check.get("source_inputs"), "Check source inputs")
        inputs += _strings(check.get("test_inputs"), "Check test inputs",
                           nonempty=not intent or intent["kind"] != "documentation")
        task_reads = set(read_inputs(state, task)) | set(task["paths"])
        deps = ancestors(task["id"])
        for relative in inputs:
            target = path(root, relative)
            w.require(relative in allowed_reads and relative in task_reads, "scope_violation",
                      f"Verification input is undeclared for task {task['id']}: {relative}")
            producer = owners.get(relative)
            if producer and producer != task["id"]:
                w.require(producer in deps and order.index(producer) < order.index(task["id"]),
                          "invalid_evidence", f"Verification input needs its preceding producer dependency: {relative}")
            if target.exists():
                read(root, relative)  # Includes regular-file/no-symlink checks.
            else:
                w.require(producer is not None, "invalid_evidence",
                          f"Missing verification input has no declared producer: {relative}")
        for relative in _strings(check.get("evidence_outputs"), "Check evidence outputs"):
            path(root, relative)
            w.require(owners.get(relative) == task["id"], "invalid_evidence",
                      "Every verification log/output must be owned by its checking task.")
    if intent is not None:
        required_checks = [check for check in strategy["checks"] if check["required"]]
        for criterion, declaration in intent["criteria"].items():
            relevant = [check for check in required_checks if criterion in check["criteria"]]
            w.require(relevant and set(declaration["paths"]) <= {
                relative for check in relevant for relative in check["source_inputs"]}, "invalid_evidence",
                "Plan required checks must verify each criterion's declared implementation paths.")
        w.require(set(intent["test_paths"]) <= {
            relative for check in required_checks for relative in check["test_inputs"]}, "invalid_evidence",
            "Plan required checks must retain the declared test inputs.")
    return {"status": "validated", "checks": sorted(ids), "outputs": deepcopy(outputs)}


def validate_build_outputs(root: Path, state: dict[str, Any], output: dict[str, Any], output_path: str) -> None:
    plan = _plan(state)
    if not typed_plan(plan):
        return
    rows = plan["build_outputs"]
    w.require(next(row["path"] for row in rows if row["kind"] == "packet") == output_path,
              "invalid_evidence", "Build must submit the exact planned packet path.")
    for row in rows:
        read(root, row["path"])
        if row["kind"] == "report":
            w.require(any(ref.get("path") == row["path"] and ref.get("kind") == "report"
                          and row["task"] in ref.get("tasks", []) for ref in output.get("artifacts", [])),
                      "invalid_evidence", "Build needs its planned report with matching task ownership.")


def finding_paths(output: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for field in ("finding_references", "findings", "finding_updates"):
        for row in output.get(field, []) if isinstance(output.get(field, []), list) else []:
            if not isinstance(row, dict):
                continue
            values = row.get("evidence", [])
            for relative in ([values] if isinstance(values, str) else values if isinstance(values, list) else []):
                if isinstance(relative, str) and relative:
                    paths.add(relative)
    return sorted(paths)


def _packets(state: dict[str, Any]) -> list[dict[str, Any]]:
    # Rejected/superseded declarations still describe obligations; only a current
    # approved decision can apply a closing update.
    return [row for row in state.get("history", []) if row.get("packet")] + [
        row for row in state.get("visits", []) if row.get("packet")]


def _finding_packets(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Replay each accepted packet once, in approval order when recorded.

    Legacy packets without a decision binding retain visit/history order. Real
    decision revisions order resubmissions even when an earlier visit is repaired
    after a later one. A packet retained in both history and visits is one event.
    """
    visits = {row["id"]: (index, row) for index, row in enumerate(state.get("visits", []))}
    packets: dict[str, dict[str, Any]] = {}
    for index, stage in enumerate(_packets(state)):
        packet = stage["packet"]
        digest = content_fingerprint(packet)
        visit_index, current = visits.get(packet.get("visit"), (len(visits), {}))
        decisions = [decision for decision in state.get("decisions", {}).values()
                     if decision.get("choice") == "approved"
                     and decision.get("binding", {}).get("manifest_digest") == digest]
        revisions = [decision["binding"]["revision"] for decision in decisions
                     if type(decision.get("binding", {}).get("revision")) is int]
        entry = packets.setdefault(digest, {"packet": packet, "digest": digest,
            "order": (visit_index, index), "revision": min(revisions) if revisions else None,
            "previously_accepted": False, "accepted": False})
        entry["previously_accepted"] |= stage.get("decision") == "approved" or bool(decisions)
        entry["accepted"] |= (current.get("decision") == "approved" and not current.get("superseded")
                              and current.get("packet") == packet)
    ordered = sorted(packets.values(), key=lambda entry: entry["order"])
    # Keep unknown legacy positions stable rather than inventing a revision.
    recorded = iter(sorted((entry for entry in ordered if entry["revision"] is not None),
                           key=lambda entry: entry["revision"]))
    return [next(recorded) if entry["revision"] is not None else entry for entry in ordered]


def finding_register(state: dict[str, Any]) -> list[dict[str, Any]]:
    inherited = state.get("inherited_findings", {})
    rows = inherited.get("findings", []) if isinstance(inherited, dict) else inherited
    registry = {(row["origin_run"], row["id"]): deepcopy(row) for row in rows or []}
    for row in registry.values():
        if row.get("disposition") == "resolved":
            row.update(disposition="open", verification="unverified", resolution_visit=None,
                       reason="Inherited resolution requires current runtime verification.")
    packets = _finding_packets(state)
    # Declarations must all exist before history can hand off their ownership or
    # add obligations. History is commonly stored before the current Product visit.
    for entry in packets:
        packet = entry["packet"]
        output = packet.get("output", {})
        refs = packet.get("finding_evidence", {})
        for declaration in [*output.get("finding_references", []), *output.get("findings", [])]:
            if not isinstance(declaration, dict) or not isinstance(declaration.get("id"), str):
                continue
            key = (declaration.get("origin_run", state["run"]), declaration["id"])
            if key not in registry:
                criteria = declaration.get("criteria", [declaration["criterion"]] if declaration.get("criterion") else [])
                registry[key] = {"origin_run": key[0], "id": key[1],
                    "owner": declaration.get("owner", "unknown"),
                    "required_evidence": deepcopy(declaration.get("required_evidence", [])),
                    "evidence_requirements": deepcopy(declaration.get("evidence_requirements", [])),
                    "criteria": deepcopy(criteria), "disposition": "open", "verification": "unverified",
                    "origin": {"run": key[0], "visit": packet.get("visit"), "packet_digest": entry["digest"]},
                    "evidence": deepcopy(declaration.get("evidence", [])), "evidence_refs": [],
                    "ownership_history": [], "update_history": [], "metadata": "typed" if declaration.get("owner")
                        and declaration.get("required_evidence") else "legacy_untyped"}
                if declaration.get("disposition") == "deferred":
                    registry[key]["disposition"] = "deferred"
                    registry[key]["reason"] = declaration.get("reason", "Historical deferral; metadata may be incomplete.")
            row = registry[key]
            for relative in finding_paths({"findings": [declaration]}):
                if relative in refs and refs[relative] not in row["evidence_refs"]:
                    row["evidence_refs"].append(deepcopy(refs[relative]))
    for entry in packets:
        if not entry["previously_accepted"]:
            continue
        packet = entry["packet"]
        refs = packet.get("finding_evidence", {})
        for update in packet.get("output", {}).get("finding_updates", []):
            if not isinstance(update, dict):
                continue
            key = (update.get("origin_run", state["run"]), update.get("id"))
            if key not in registry:
                continue
            row = registry[key]
            historical = {"visit": packet["visit"], "packet_digest": entry["digest"], "update": deepcopy(update)}
            if historical not in row.setdefault("update_history", []):
                row["update_history"].append(historical)
            if update.get("owner", row["owner"]) != row["owner"]:
                row["ownership_history"].append({"from": row["owner"], "to": update["owner"],
                                                "visit": packet["visit"], "reason": update.get("reason")})
            for field in ("required_evidence", "evidence_requirements"):
                if field in update:
                    before, after = row.get(field, []), update[field]
                    row[field] = (deepcopy(before) + [deepcopy(item) for item in after if item not in before]
                                  if isinstance(before, list) and isinstance(after, list) else deepcopy(after))
            for field in ("owner", "disposition", "verification", "evidence", "reason"):
                if field in update:
                    row[field] = deepcopy(update[field])
            if not entry["accepted"] and update.get("disposition") == "resolved":
                row.update(disposition="open", verification="unverified",
                           reason="Historical resolution is stale or superseded; runtime verification is required.")
            row["resolution_visit"] = packet["visit"] if row["disposition"] == "resolved" else None
            for relative in finding_paths({"finding_updates": [update]}):
                if relative in refs and refs[relative] not in row["evidence_refs"]:
                    row["evidence_refs"].append(deepcopy(refs[relative]))
    checks = effective_checks(state)
    verification = _verification_packet(state).get("packet", {}).get("verification", {})
    for row in registry.values():
        if row.get("disposition") == "resolved" and not _runtime_resolution(row, row, checks, verification):
            row.update(disposition="open", verification="unverified", resolution_visit=None,
                       reason="Current runtime verification is failed, missing, incompatible or stale.")
    return sorted(registry.values(), key=lambda row: (row["origin_run"], row["id"]))


def carry_findings(previous: dict[str, Any]) -> dict[str, Any]:
    """Carry observations only. No approval, policy, grant or receipt crosses runs."""
    inherited = previous.get("inherited_findings", {})
    histories = deepcopy(inherited.get("verification_histories", [])) if isinstance(inherited, dict) else []
    for stage in _packets(previous):
        verification = stage["packet"].get("verification", {})
        if verification.get("history") and verification["history"] not in histories:
            histories.append(deepcopy(verification["history"]))
    return {"schema": "taskplane.inherited-findings/v1",
            "predecessor": {"run": previous["run"], "revision": previous["revision"],
                            "digest": content_fingerprint(previous)},
            "findings": finding_register(previous), "verification_histories": histories}


def _valid_runtime_requirement(requirement: Any, criteria: Any) -> bool:
    """Require an explicit runtime contract within the finding's criteria."""
    if not isinstance(requirement, dict):
        return False
    required_criteria = requirement.get("criteria")
    return (all(isinstance(requirement.get(field), str) and requirement[field].strip()
                for field in ("obligation", "check_id"))
            and isinstance(requirement.get("kind"), str)
            and requirement["kind"] in {"unit", "roundtrip", "browser", "integration"}
            and isinstance(requirement.get("environment"), str)
            and requirement["environment"] in CHECK_ENVIRONMENTS
            and isinstance(criteria, list) and bool(criteria)
            and all(isinstance(criterion, str) and criterion.strip() for criterion in criteria)
            and isinstance(required_criteria, list) and bool(required_criteria)
            and all(isinstance(criterion, str) and criterion.strip() for criterion in required_criteria)
            and set(required_criteria) <= set(criteria))


def _runtime_resolution(prior: dict[str, Any], update: dict[str, Any],
                        checks: list[dict[str, Any]], verification: dict[str, Any]) -> bool:
    """Match every obligation to fresh typed execution, never to free prose.

    A typed required_evidence item supplies obligation/check_id/kind/environment/
    criteria. Every accepted evidence_requirements entry adds coverage for its
    named obligation, including typed obligations. Legacy strings need at least
    one such mapping. Orphan or malformed historical mappings prevent closure.
    Adding a mapping and closing in one update is not permitted.
    """
    obligations = prior.get("required_evidence", [])
    mappings = prior.get("evidence_requirements", [])
    criteria = prior.get("criteria", [])
    if (verification.get("status") != "validated"
            or not isinstance(obligations, list) or not obligations or not isinstance(mappings, list)):
        return False
    names = [item.get("obligation") if isinstance(item, dict) else item for item in obligations]
    if not all(isinstance(name, str) and name.strip() for name in names):
        return False
    requirements = [item for item in obligations if isinstance(item, dict)] + mappings
    if any(not _valid_runtime_requirement(item, criteria) or item["obligation"] not in names
           for item in requirements):
        return False
    # Check every accepted mapping, not just mappings selected by legacy strings.
    # A mapping cannot replace the original typed requirement or invent an obligation.
    if any(isinstance(item, str) and not any(mapping["obligation"] == item for mapping in mappings)
           for item in obligations):
        return False
    evidence = set(finding_paths({"finding_updates": [update]}))
    covered: set[str] = set()
    for requirement in requirements:
        required_criteria = requirement["criteria"]
        matched = [check for check in checks if check.get("check_id") == requirement["check_id"]
                   and check.get("kind") == requirement["kind"]
                   and check.get("environment") == requirement["environment"]
                   and set(required_criteria) <= set(check.get("criteria", []))]
        if (not matched or any(check.get("status") != "pass" or not check.get("id")
                              or not check.get("record_ref") or not check.get("log_ref")
                              or check.get("evidence") not in evidence for check in matched)):
            return False
        covered.update(required_criteria)
    associated = [check for check in checks if set(criteria) & set(check.get("criteria", []))]
    return set(criteria) <= covered and all(check.get("status") == "pass" for check in associated)


def validate_finding_updates(root: Path, state: dict[str, Any], output: dict[str, Any], *,
                             verification: dict[str, Any] | None = None) -> list[str]:
    registered = {(row["origin_run"], row["id"]): row for row in finding_register(state)}
    updates = output.get("finding_updates", [])
    w.require(isinstance(updates, list), "invalid_evidence", "Finding updates must be a list.")
    seen: set[tuple[str, str]] = set()
    for update in updates:
        w.require(isinstance(update, dict) and isinstance(update.get("origin_run"), str)
                  and isinstance(update.get("id"), str), "invalid_evidence", "Finding update needs its original run and ID.")
        key = (update["origin_run"], update["id"])
        w.require(key in registered and key not in seen, "invalid_evidence", "Finding update is unknown, renamed or duplicated.")
        seen.add(key)
        prior = registered[key]
        merged = {**prior, **update}
        w.require(merged.get("disposition") in {"open", "deferred", "resolved"}
                  and merged.get("verification") in {"unverified", "implemented", "static_checked", "runtime_verified"},
                  "invalid_evidence", "Finding disposition or verification level is invalid.")
        w.require("criteria" not in update and "origin" not in update and "evidence_refs" not in update,
                  "invalid_evidence", "Finding origin, criteria and immutable evidence cannot be replaced.")
        for field in ("required_evidence", "evidence_requirements"):
            before, after = prior.get(field, []), merged.get(field, [])
            w.require(isinstance(before, list) and isinstance(after, list) and all(item in after for item in before)
                      or before == after, "invalid_evidence", "Original evidence obligations cannot be removed.")
        if merged["disposition"] in {"deferred", "resolved"} or merged["owner"] != prior["owner"]:
            w.require(isinstance(merged.get("owner"), str) and merged["owner"] not in {"", "unknown"}
                      and substantive(merged.get("required_evidence")) and substantive(update.get("reason")),
                      "invalid_evidence", "Deferral, resolution and ownership changes need owner, evidence obligations and reason.")
        if merged["disposition"] == "resolved":
            w.require(all(merged.get(field, []) == prior.get(field, [])
                          for field in ("required_evidence", "evidence_requirements")), "invalid_evidence",
                      "Resolution requires runtime checks matching previously accepted obligations; accept additions first.")
            w.require(finding_paths({"finding_updates": [update]}) and merged["verification"] == "runtime_verified",
                      "invalid_evidence", "Resolution requires current runtime evidence.")
            validated = verification if verification is not None else _verification_packet(state, output).get(
                "packet", {}).get("verification", {})
            checks = validated.get("effective_checks", []) if verification is not None else effective_checks(state, output)
            w.require(_runtime_resolution(prior, update, checks, validated), "invalid_evidence",
                      "Resolution requires validated current runtime checks matching every original obligation and environment.")
        before, after = prior.get("evidence_requirements", []), merged.get("evidence_requirements", [])
        if after != before:
            obligations = merged.get("required_evidence", [])
            obligations = obligations if isinstance(obligations, list) else []
            names = [item.get("obligation") if isinstance(item, dict) else item for item in obligations]
            for item in after:
                if item in before:
                    continue
                w.require(_valid_runtime_requirement(item, prior.get("criteria", [])) and item["obligation"] in names,
                          "invalid_evidence", "Each new evidence mapping must specify valid runtime coverage for an existing obligation.")
                w.require(all(not isinstance(other, dict) or other.get("check_id") != item["check_id"]
                              or all(other.get(field) == item[field] for field in ("kind", "environment"))
                              for other in obligations + after), "invalid_evidence",
                          "Evidence mappings for the same check must agree on runtime kind and environment.")
    files = finding_paths(output)
    for relative in files:
        read(root, relative)
    return files


def outcome_summary(state: dict[str, Any]) -> dict[str, Any]:
    findings = finding_register(state)
    checks = effective_checks(state)
    return {"current_checks": checks, "findings": findings,
            "unresolved_findings": [row for row in findings if row["disposition"] != "resolved"],
            "verification": "typed" if typed_plan(_plan(state)) else "legacy_untyped"}


def _verification_packet(state: dict[str, Any], output: dict[str, Any] | None = None) -> dict[str, Any]:
    packets: list[dict[str, Any]] = [stage for stage in state.get("visits", [])[:state.get("index", 0) + 1]
                                    if stage.get("packet") and not stage.get("superseded")]
    if output is not None:
        # Only Build/Evaluate packets validate check snapshots. Later phase
        # outputs must not hide the current Build evidence during policy lookup.
        matches = [stage for stage in packets if stage.get("phase") in {"build", "evaluate"}
                   and stage["packet"].get("output") == output]
        if matches:
            return matches[-1]
    matches = [stage for stage in packets if stage.get("phase") == "build"]
    return matches[-1] if matches else {}


def effective_checks(state: dict[str, Any], output: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One current selector: a later fail/unknown always supersedes an older pass.

    Typed selections come from validated packet snapshots, not mutable log files.
    The Controller invalidates changed packet/source manifests before using this.
    """
    stage = _verification_packet(state, output)
    packet = stage.get("packet", {})
    source = output if output is not None else packet.get("output", {})
    plan = _plan(state)
    if not typed_plan(plan):
        rows: dict[str, dict[str, Any]] = {}
        for check in source.get("build_checks", []):
            if isinstance(check, dict):
                rows[str(check.get("check_id", check.get("name")))] = deepcopy(check)
        return list(rows.values())
    verification = packet.get("verification", {})
    selected = {row["check_id"]: row for row in verification.get("effective_checks", [])}
    result = []
    for requirement in plan["verification_strategy"]["checks"]:
        row = deepcopy(selected.get(requirement["id"], {}))
        invalid = stage.get("decision") in {"stale", "rejected", "changes_requested", "cancelled"}
        if not row or invalid:
            row.update(status="unknown", reason="No current validated verification attempt.")
        row.update(check_id=requirement["id"], name=requirement["name"],
                   kind=requirement["kind"], environment=requirement["environment"],
                   required=requirement["required"], criteria=deepcopy(requirement["criteria"]))
        result.append(row)
    return result


def validate_verification(root: Path, state: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Bind declared requirements, immutable attempts, current provenance and logs."""
    plan = _plan(state)
    phase = w.current(state)["phase"]
    if phase not in {"build", "evaluate"} or not typed_plan(plan):
        return {"status": "legacy_untyped", "files": []}
    from .context import Store
    from . import context_reuse
    import hashlib
    store = Store(root)
    requirements = {check["id"]: check for check in plan["verification_strategy"]["checks"]}
    history_path = output.get("verification_history")
    if phase == "evaluate" and history_path is None:
        predecessor = _verification_packet(state)
        packet = predecessor.get("packet", {})
        w.require(predecessor.get("decision") == "approved" and packet.get("verification", {}).get("history"),
                  "invalid_evidence", "Evaluate needs a current accepted typed Build verification history.")
        history_path = packet["verification"]["history_path"]
    declared_histories = {row["path"] for row in plan["build_outputs"] if row["kind"] == "verification_history"}
    w.require(isinstance(history_path, str) and history_path in declared_histories,
              "invalid_evidence", "Verification history must name a planned output.")
    assert isinstance(history_path, str)
    history = object_file(root, history_path)
    w.require(history.get("schema") == VERIFICATION_HISTORY and history.get("run") == state["run"]
              and history.get("coverage") == "declared_producer" and isinstance(history.get("attempts"), list),
              "invalid_evidence", "Verification history has foreign binding, schema or coverage.")
    visits = {visit["id"] for visit in state["visits"] if visit["phase"] == "build"}
    current_visit = w.current(state)["id"] if phase == "build" else _verification_packet(state).get("id")
    w.require(history.get("visit") == current_visit, "invalid_evidence", "Verification history belongs to a different Build visit.")
    attempts = history["attempts"]
    # Every previously captured attempt remains an exact ordered prefix through
    # resubmission and repair. Replacement keeps predecessor histories separately.
    for stage in _packets(state):
        previous = stage["packet"].get("verification", {}).get("history", {})
        if previous.get("run") == state["run"]:
            old = previous.get("attempts", [])
            w.require(attempts[:len(old)] == old, "invalid_evidence",
                      "Verification attempts cannot be removed, reordered or rewritten.")
    seen: set[str] = set()
    latest: dict[str, dict[str, Any]] = {}
    files = [history_path]
    refs: list[dict[str, Any]] = []
    for sequence, attempt in enumerate(attempts, 1):
        w.require(isinstance(attempt, dict) and isinstance(attempt.get("id"), str) and attempt["id"]
                  and attempt["id"] not in seen and attempt.get("sequence") == sequence
                  and attempt.get("check_id") in requirements and attempt.get("run") == state["run"]
                  and attempt.get("visit") in visits and attempt.get("status") in {"pass", "fail", "unknown"},
                  "invalid_evidence", "Verification attempt identity, sequence or binding is invalid.")
        seen.add(attempt["id"])
        requirement = requirements[attempt["check_id"]]
        w.require(attempt.get("kind") == requirement["kind"]
                  and attempt.get("environment") == requirement["environment"]
                  and isinstance(attempt.get("record_ref"), dict), "invalid_evidence",
                  "Attempt kind/environment must exactly match its required coverage.")
        record = store.resolve(attempt["record_ref"])
        bound = {key: attempt[key] for key in ("id", "check_id", "run", "visit", "kind", "environment")}
        w.require(isinstance(record, dict) and record.get("schema") == "taskplane.verification-record/v1"
                  and record.get("status") == attempt["status"] and record.get("verification") == bound,
                  "invalid_evidence", "Attempt does not match its immutable verification record.")
        key = record.get("key", {})
        w.require(key.get("command") == requirement["command"]
                  and set(key.get("paths", [])) == set(requirement["source_inputs"])
                  and set(key.get("tests", [])) == set(requirement["test_inputs"])
                  and set(key.get("criteria", [])) == set(requirement["criteria"]),
                  "invalid_evidence", "Attempt command or input contract differs from its Plan requirement.")
        log_path = attempt.get("evidence")
        w.require(log_path in requirement["evidence_outputs"] and attempt.get("log_ref") == record.get("log_ref")
                  and record.get("log_path") == log_path, "invalid_evidence", "Attempt log is not its declared immutable evidence.")
        log = store.resolve(attempt["log_ref"])
        w.require(isinstance(log, dict) and log.get("path") == log_path and isinstance(log.get("text"), str)
                  and log.get("sha256") == hashlib.sha256(log["text"].encode()).hexdigest(),
                  "invalid_evidence", "Immutable attempt log is invalid.")
        # Missing historical files can be recovered from the pinned immutable
        # body. An existing edited path must not masquerade as the old log.
        if path(root, log_path).exists():
            w.require(read(root, log_path) == log["text"].encode(), "invalid_evidence", "A captured verification log was edited.")
            files.append(log_path)
        refs.extend([attempt["record_ref"], attempt["log_ref"]])
        if attempt["visit"] == current_visit:
            latest[attempt["check_id"]] = {**deepcopy(attempt), "_record": record}
    selected: list[dict[str, Any]] = []
    final_rows = output.get("build_checks", []) if phase == "build" else _verification_packet(state).get("packet", {}).get("output", {}).get("build_checks", [])
    w.require(isinstance(final_rows, list) and all(isinstance(row, dict) for row in final_rows),
              "invalid_evidence", "Final Build checks must be a list.")
    final = {row.get("check_id"): row for row in final_rows}
    w.require(len(final) == len(final_rows) and set(final) <= set(requirements),
              "invalid_evidence", "Final checks must identify unique declared check IDs.")
    for check_id, requirement in requirements.items():
        attempt = latest.get(check_id)
        if attempt is None:
            w.require(check_id not in final, "invalid_evidence", "Final check has no current attempt.")
            selected.append({"check_id": check_id, "status": "unknown", "reason": "Required check has no current attempt."})
            continue
        claim = final.get(check_id)
        w.require(claim is not None and claim.get("attempt_id") == attempt["id"]
                  and claim.get("status") == attempt["status"] and claim.get("evidence") == attempt["evidence"],
                  "invalid_evidence", "Final check must select its latest attempt with the same observed status and log.")
        record = attempt.pop("_record")
        saved = record["key"]
        current_key = context_reuse.key(root, paths=saved["paths"], tests=saved["tests"],
            criteria=saved["criteria"], command=saved["command"], tool=saved["tool"], contract=saved["contract"])
        if current_key != saved:
            attempt.update(status="unknown", reason="Verification source/runtime/environment fingerprint changed.")
        if attempt["status"] == "pass":
            w.require(record.get("result", {}).get("returncode") == 0, "invalid_evidence", "Pass lacks successful command evidence.")
            if requirement["kind"] == "browser":
                details = record.get("result", {}).get("coverage_details", {})
                w.require(all(substantive(details.get(field)) for field in ("engine", "version", "interactions")),
                          "invalid_evidence", "Browser coverage needs engine, version and exercised interactions.")
            if requirement["environment"] == "deployed":
                details = record.get("result", {}).get("coverage_details", {})
                w.require(all(substantive(details.get(field)) for field in ("target", "service_result")),
                          "invalid_evidence", "Deployed coverage needs actual target and service outcome.")
            if requirement["kind"] == "independent":
                from . import worker_runtime as workers
                task = requirement["task"]
                result = state.get("task_results", {}).get(task, {})
                details = record.get("result", {}).get("coverage_details", {})
                w.require(workers.native_result_valid(root, state, task, result) and workers.result_valid(root, state, task)
                          and details.get("grant") == result.get("grant")
                          and details.get("reviewer") == result.get("worker_id")
                          and result.get("worker_id") != state.get("root")
                          and task not in native_lens_conflicts(plan["task_dag"], state.get("task_results", {})),
                          "invalid_evidence", "Independent checks need current native result and actual distinct reviewer.")
        attempt.update(check_id=check_id, name=requirement["name"], criteria=deepcopy(requirement["criteria"]),
                       required=requirement["required"], kind=requirement["kind"], environment=requirement["environment"])
        selected.append(attempt)
    if phase == "evaluate":
        for criterion, result in output.get("criterion_results", {}).items():
            if result.get("status") == "pass":
                checks = [row for row in selected if requirements[row["check_id"]]["required"]
                          and criterion in requirements[row["check_id"]]["criteria"]]
                w.require(checks and all(row["status"] == "pass" for row in checks), "invalid_evidence",
                          "Criterion pass requires every mandatory check kind/environment to be current and passing.")
    return {"status": "validated", "files": sorted(set(files)), "history_path": history_path,
            "history": deepcopy(history), "refs": refs, "effective_checks": selected}


def execution_evidence(root: Path, state: dict[str, Any], tasks: list[dict[str, Any]],
                       output: dict[str, Any]) -> list[str]:
    """Frozen execution requirements must be discharged by actual current results."""
    from . import worker_runtime as workers
    frozen = context_tasks(state)
    contracted = (state["scope"].get("execution_contract") == "native-default/v1"
                  or state["scope"].get("planning_contract") == w.PLANNING_CONTRACT)
    typed = any("execution" in row or "review_lens" in row for row in [*tasks, *frozen])
    if not contracted and not typed:
        return []  # Historical untyped evidence has no invented native requirement.
    execution_fields(tasks, required=contracted, phase=w.current(state)["phase"], root=state["root"])
    execution_preflight(state, tasks)
    w.require(task_definitions(tasks) == task_definitions(frozen), "invalid_evidence",
              "Execution task definitions must match the published frozen context.")
    phase = w.current(state)["phase"]
    selected = [row for row in frozen if row.get("phase", phase) == phase]
    files: list[str] = []
    for row in selected:
        if row.get("execution") == "native_required":
            capacity = state.get("worker_capacity", {})
            reason = " Native unavailable: " + str(capacity.get("reason")) if capacity.get("status") == "unavailable" else ""
            result = state.get("task_results", {}).get(row["id"], {})
            w.require(workers.native_result_valid(root, state, row["id"], result)
                      and workers.result_valid(root, state, row["id"]), "invalid_evidence",
                      f"Task {row['id']} needs a fresh accepted native result." + reason)
            files.extend(state["task_results"][row["id"]]["manifest"])
    if phase != "engineering":
        return files
    lenses = {row["review_lens"]: row for row in selected if "review_lens" in row}
    coverage = output.get("lens_coverage")
    w.require(isinstance(coverage, list) and all(isinstance(row, dict) and isinstance(row.get("lens"), str) for row in coverage),
              "invalid_evidence", "Engineering needs typed lens coverage.")
    assert isinstance(coverage, list)
    w.require(len(coverage) == len(lenses) and {row.get("lens") for row in coverage} == set(lenses)
              and (bool(lenses) or not contracted), "invalid_evidence",
              "Lens coverage must identify every frozen Engineering lens exactly once.")
    conflicts = native_lens_conflicts(selected, state.get("task_results", {}))
    for claim in coverage:
        row = lenses[claim["lens"]]
        w.require(claim.get("task_id") == row["id"] and substantive(claim.get("rationale")),
                  "invalid_evidence", "Lens coverage needs its frozen task and rationale.")
        if row["execution"] == "native_required":
            result = state["task_results"][row["id"]]
            w.require(claim.get("status") == "native_verified"
                      and claim.get("grant") == result["grant"] and claim.get("reviewer") == result["worker_id"]
                      and row["id"] not in conflicts, "invalid_evidence",
                      "Native lens coverage requires the accepted grant and distinct actual worker identities.")
        else:
            w.require(claim.get("status") == "serial_scope" and claim.get("reviewer") == state["root"]
                      and claim.get("execution_reference") == row["execution_reference"] and not claim.get("grant"),
                      "invalid_evidence", "Serial lens coverage must identify the root and frozen exception reference.")
    return files


def prevalidate(root: Path, state: dict[str, Any], output_path: str, tasks_path: str) -> dict[str, Any]:
    """Validate the exact prospective packet without allocating or writing anything."""
    valid_scope(root, state["scope"])
    stage = w.current(state)
    phase = stage["phase"]
    w.require(output_path in state["scope"]["paths"][phase], "scope_violation", "Output is outside the declared phase scope.")
    output = object_file(root, output_path)
    w.require(output.get("schema") == "taskplane.phase-output/v1" and
              all(output.get(key) == expected for key, expected in
                  (("phase", phase), ("visit", stage["id"]), ("run", state["run"]))),
              "invalid_evidence", "Output schema/run/phase/visit does not match this checkpoint.")
    for field in w.OUTPUT_FIELDS[phase]:
        value = output.get(field)
        w.require(field in output and (substantive(value) or field in EMPTY_LIST_FIELDS and value == []),
                  "invalid_evidence", f"{phase} output needs substantive {field}.")
    criteria = state["scope"]["criteria"]
    w.require(output.get("criteria") == criteria, "invalid_evidence", "Output must identify the accepted criteria.")
    if state.get("context_contract"):
        from .context_handoff import Session
        Session(root, state, persist=False).validate(output.get("context_receipt"))
    tasks = task_dag(object_file(root, tasks_path), criteria)
    from .workflow_local import verify_workflow
    verify_workflow(root, state, tasks=tasks,
                    build_paths=output.get("write_scope") if phase == "plan" else None)
    files = [output_path, *execution_evidence(root, state, tasks, output)]
    for t in tasks:
        for p in t["paths"]:
            path(root, p)
    if phase == "product":
        entries = output["acceptance_criteria"]
        w.require(isinstance(entries, list) and all(isinstance(c, dict) and c.get("statement") for c in entries)
                  and {c.get("id") for c in entries} == set(criteria),
                  "invalid_evidence", "Product criteria need stable IDs and statements.")
    if phase == "design":
        criterion_map(output["acceptance_test_map"], criteria, "Design test map")
    if phase == "plan":
        planned = output["write_scope"]
        w.require(isinstance(planned, list) and all(isinstance(p, str) for p in planned)
                  and set(planned) <= set(state["scope"]["paths"]["build"]),
                  "scope_violation", "Plan cannot widen the human-authorized Build scope.")
        criterion_map(output["acceptance_coverage"], criteria, "Plan acceptance coverage")
        ids = {t["id"] for t in tasks}
        w.require(output["task_dag"] == tasks, "invalid_evidence", "Plan must include the actual shared task DAG.")
        order = output["integration_order"]
        w.require(isinstance(order, list) and all(isinstance(i, str) for i in order)
                  and len(order) == len(ids) and set(order) == ids
                  and all(order.index(d) < order.index(t["id"]) for t in tasks for d in t["dependencies"]),
                  "invalid_evidence", "Plan integration order must respect every prerequisite.")
        w.require(output["ownership"] == {t["id"]: t["owner"] for t in tasks},
                  "invalid_evidence", "Plan ownership must match the shared tasks.")
        for criterion, covered in output["acceptance_coverage"].items():
            w.require(isinstance(covered, list) and all(isinstance(t, str) for t in covered)
                      and set(covered) <= {t["id"] for t in tasks if criterion in task_criteria(t)},
                      "invalid_evidence", "Plan coverage names an unrelated or unknown task.")
        build_tasks = [t for t in tasks if t.get("phase") == "build"]
        w.require(build_tasks and set(planned) == {p for t in build_tasks for p in t["paths"]},
                  "invalid_evidence", "Plan write scope must exactly match its Build task paths.")
        _plan_preflight(root, state, output, tasks)
    if phase == "build":
        build_task_map(state, tasks, output["task_acceptance_map"], criteria)
        inventory = output["change_inventory"]
        w.require(isinstance(inventory, list) and all(isinstance(p, str) for p in inventory)
                  and set(inventory) <= set(state["scope"]["paths"]["build"]),
                  "scope_violation", "Build inventory must identify exact accepted write paths.")
        checks = output["build_checks"]
        w.require(isinstance(checks, list) and checks, "invalid_evidence", "Build needs actual check records.")
        for check in checks:
            w.require(isinstance(check, dict) and substantive(check.get("name"))
                      and check.get("status") in ("pass", "fail", "unknown")
                      and isinstance(check.get("evidence"), str) and check["evidence"],
                      "invalid_evidence", "Build checks need name, observed status and evidence file.")
            files.append(check["evidence"])
        validate_build_outputs(root, state, output, output_path)
    if phase == "evaluate":
        results = output["criterion_results"]
        w.require(isinstance(results, dict) and set(results) == set(criteria),
                  "invalid_evidence", "Evaluate must report each criterion.")
        for result in results.values():
            w.require(isinstance(result, dict) and result.get("status") in ("pass", "fail", "unknown")
                      and result.get("evidence") and result.get("explanation"),
                      "invalid_evidence", "Each criterion needs an observed status, evidence and explanation.")
            refs = result["evidence"]
            refs = [refs] if isinstance(refs, str) else refs
            w.require(isinstance(refs, list) and refs and all(isinstance(p, str) and p for p in refs),
                      "invalid_evidence", "Criterion evidence must name existing workspace files.")
            files.extend(refs)
    if phase == "engineering":
        criterion_map(output["requirements_comparison"], criteria, "Engineering requirements comparison")
        w.require(isinstance(output["findings"], list) and isinstance(output["lens_coverage"], list)
                  and all(isinstance(lens, dict) and substantive(lens.get("lens"))
                          and substantive(lens.get("rationale")) and substantive(lens.get("reviewer"))
                          for lens in output["lens_coverage"]),
                  "invalid_evidence", "Engineering needs findings and attributed lens coverage.")
        for finding in output["findings"]:
            w.require(isinstance(finding, dict) and all(substantive(finding.get(k)) for k in
                      ("id", "severity", "source", "evidence")) and isinstance(finding["evidence"], str),
                      "invalid_evidence", "Findings need ID, severity, source location and evidence.")
            files.append(finding["evidence"])
    verification = validate_verification(root, state, output)
    files.extend(verification.get("files", []))
    files.extend(validate_finding_updates(root, state, output, verification=verification))
    graph = object_file(root, ".taskplane/knowledge/graph.json")
    w.require(isinstance(graph.get("modules"), (dict, list)) and isinstance(graph.get("components"), list)
              and (not graph["modules"] or graph["components"])
              and all(isinstance(c, dict) and not c.get("degraded") for c in graph["components"]),
              "invalid_evidence", "Shared source graph and component decomposition are required.")
    meta = graph.get("meta")
    quality = meta.get("graph_scan_quality") if isinstance(meta, dict) else None
    # scan_quality supports legacy module-only graphs with defaults. Checkpoints
    # require an actual completed scan and decomposition, not those defaults.
    w.require(isinstance(quality, dict) and quality.get("schema") == GRAPH_SCAN_QUALITY_SCHEMA,
              "invalid_evidence", "Stored source graph quality is missing or corrupt.")
    assert isinstance(quality, dict)
    producers = quality.get("producers")
    w.require(quality.get("degraded") is False and quality.get("mode") == "components"
              and quality.get("failures") == [] and quality.get("affected_modules") == []
              and isinstance(quality.get("scanned_revision"), str) and isinstance(producers, dict)
              and all(isinstance(producers.get(p), dict) and producers[p].get("status") == "complete"
                      and producers[p].get("failures") == [] for p in ("base-scanner", "decomposition"))
              and quality.get("fingerprint") == scan_quality(graph)["fingerprint"],
              "invalid_evidence", "Source graph quality is degraded, incomplete or corrupt.")
    w.require(source_inputs_current(str(root), graph), "invalid_evidence",
              "Source graph is stale or lacks source input evidence; rerun tp graph scan --decompose --strict.")
    dashboard = read(root, ".taskplane/dashboard.html")
    w.require(bool(dashboard.strip()), "invalid_evidence", "Shared dashboard is missing.")
    artifacts = output.get("artifacts", [])
    w.require(isinstance(artifacts, list), "invalid_evidence", "Artifact references must be a list.")
    for ref in artifacts:
        w.require(isinstance(ref, dict) and all(isinstance(ref.get(k), str) and ref[k].strip() for k in ("path", "kind", "schema"))
                  and ref.get("phase") == phase and ref.get("visit") == stage["id"]
                  and isinstance(ref.get("criteria"), list) and isinstance(ref.get("tasks"), list)
                  and set(ref["criteria"]) <= set(criteria)
                  and set(ref["tasks"]) <= {t["id"] for t in tasks},
                  "invalid_evidence", "Artifact reference lacks phase/visit/kind/schema/task/criterion provenance.")
        required_for = ref.get("required_for", [])
        w.require(isinstance(required_for, list) and all(p in w.PHASES for p in required_for),
                  "invalid_evidence", "Artifact required_for must name downstream phases.")
        files.append(ref["path"])
    change = output.get("route_change")
    if change:
        w.require(isinstance(change, dict) and change.get("kind") in ("delivery", "repair"),
                  "invalid_evidence", "Invalid route amendment.")
        if change["kind"] == "delivery":
            valid_scope(root, change.get("scope", {}))
    receipt_path = root / ".taskplane/graph-receipt.json"
    receipt = object_file(root, ".taskplane/graph-receipt.json") if receipt_path.exists() else None
    if receipt and (receipt.get("workspace") != str(root.resolve())
                    or receipt.get("graph_digest") != content_fingerprint(graph)):
        receipt = None
    return {"phase": phase, "visit": stage["id"], "scope_preflight": w.scope_preflight(state["scope"]),
            "output": output, "manifest": manifest(root, files, task_path=tasks_path),
            "execution_evidence": "native-results/v1" if any("execution" in row for row in tasks) else None,
            "source_manifest": manifest(root, state["scope"]["paths"]["build"] +
                                        state["scope"].get("verification_inputs", []), allow_missing=True, task_path=tasks_path)
                               if phase in ("build", "evaluate", "engineering") else {},
            # Context is sealed in the protected packet; shared views can refresh without
            # invalidating approved normative artifacts merely because telemetry changed.
            "context": {"graph": graph, "graph_receipt": receipt, "tasks": tasks, "tasks_path": tasks_path, "dashboard_digest": content_fingerprint(dashboard)},
            "verification": verification, "route_change": change}


def seal(root: Path, state: dict[str, Any], output_path: str, tasks_path: str) -> dict[str, Any]:
    packet = prevalidate(root, state, output_path, tasks_path)
    # Only sealing persists immutable finding bodies. Previewing the identical
    # validation path neither allocates a checkpoint nor touches the store.
    from .context import Store
    from .context_handoff import text_body
    store = Store(root)
    refs = {}
    for relative in finding_paths(packet["output"]):
        refs[relative] = store.put("finding-evidence", {"path": relative, **text_body(read(root, relative))})
    packet.update(checkpoint=uuid.uuid4().hex, finding_evidence=refs)
    return packet


def changed(root: Path, state: dict[str, Any], *, skip_current: bool = False) -> tuple[str, str] | None:
    for stage in state["visits"]:
        # A native negative decision reopens this visit for correction. Its old
        # packet remains history; protected-host revocation semantics stay intact.
        editable = stage["id"] == w.current(state)["id"] and (
            skip_current or state.get("profile") == "native_workflow"
            and stage["decision"] in ("changes_requested", "rejected"))
        if stage["superseded"] or stage["decision"] == "stale" or not stage["packet"] or editable:
            continue
        packet = stage["packet"]
        for field in ("manifest", "source_manifest"):
            before = packet[field]
            try:
                after = {}
                for relative, expected in before.items():
                    normalized = isinstance(expected, dict)
                    w.require(not normalized or (expected.get("schema") == TASK_DEFINITIONS
                              and set(expected) == {"schema", "digest"}
                              and relative == packet.get("context", {}).get("tasks_path")),
                              "invalid_evidence", "Unknown or misplaced task fingerprint.")
                    # Legacy byte fingerprints retain their exact original contract.
                    # Only a fresh, explicitly sealed packet can use normalization.
                    after.update(manifest(root, [relative], allow_missing=field == "source_manifest",
                                          task_path=relative if normalized else None))
            except w.Refusal:
                return stage["id"], "Approved or submitted evidence is missing or outside its safe path."
            if before != after:
                return stage["id"], "Normative artifacts or verified source changed."
    return None
