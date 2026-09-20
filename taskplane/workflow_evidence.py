"""Phase output validation and fingerprints; evidence never grants approval."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any
import uuid

from . import workflow as w
from .depgraph import GRAPH_SCAN_QUALITY_SCHEMA, scan_quality, source_inputs_current
from .primitives import content_fingerprint

TASK_OBSERVATIONS = {"status", "started_at", "completed_at", "updated_at", "elapsed_seconds"}
TASK_DEFINITIONS = "taskplane.task-definitions/v1"

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


def task_dag(data: dict[str, Any], criteria: list[str]) -> list[dict[str, Any]]:
    tasks = data.get("tasks")
    w.require(isinstance(tasks, list) and tasks and all(isinstance(t, dict) for t in tasks),
              "invalid_evidence", "A shared task decomposition is required.")
    assert isinstance(tasks, list)
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
        covered.update(task_criteria(t))
        index[key] = t
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(key: str) -> None:
        w.require(key in index and key not in visiting, "invalid_evidence", "Task prerequisite is missing or cyclic.")
        if key in visited:
            return
        visiting.add(key)
        for dep in index[key]["dependencies"]:
            walk(dep)
        visiting.remove(key)
        visited.add(key)

    for key in index:
        walk(key)
    w.require(set(criteria) <= covered, "invalid_evidence", "Task plan does not cover every acceptance criterion.")
    return list(index.values())


def build_task_map(state: dict[str, Any], tasks: list[dict[str, Any]], value: Any,
                   criteria: list[str]) -> None:
    plan = w.accepted_plan(state)["packet"]["output"]
    approved = task_dag({"tasks": plan["task_dag"]}, criteria)
    # Only observations may change without renewed Plan acceptance. Unknown fields
    # remain normative, so adding a new scope/verification field cannot evade this check.

    def definitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {t["id"]: {**{k: v for k, v in t.items()
                             if k not in TASK_OBSERVATIONS | {"criteria", "acceptance_criteria"}},
                          "criteria": task_criteria(t)} for t in rows}

    w.require(definitions(tasks) == definitions(approved), "invalid_evidence",
              "Build task definitions must match the human-accepted Plan; only progress observations may change.")
    criterion_map(value, criteria, "Build task/acceptance map")
    for criterion, mapped in value.items():
        allowed = {t["id"] for t in approved if t.get("phase") == "build"
                   and criterion in task_criteria(t)}
        w.require(isinstance(mapped, list) and mapped and all(isinstance(t, str) for t in mapped)
                  and len(set(mapped)) == len(mapped) and set(mapped) <= allowed
                  and set(mapped) <= set(plan["acceptance_coverage"][criterion]),
                  "invalid_evidence", "Build map must name approved Build tasks associated with that criterion.")


def seal(root: Path, state: dict[str, Any], output_path: str, tasks_path: str) -> dict[str, Any]:
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
    tasks = task_dag(object_file(root, tasks_path), criteria)
    files = [output_path]
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
    return {"checkpoint": uuid.uuid4().hex, "phase": phase, "visit": stage["id"],
            "output": output, "manifest": manifest(root, files, task_path=tasks_path),
            "source_manifest": manifest(root, state["scope"]["paths"]["build"] +
                                        state["scope"].get("verification_inputs", []), allow_missing=True, task_path=tasks_path)
                               if phase in ("build", "evaluate", "engineering") else {},
            # Context is sealed in the protected packet; shared views can refresh without
            # invalidating approved normative artifacts merely because telemetry changed.
            "context": {"graph": graph, "graph_receipt": receipt, "tasks": tasks, "tasks_path": tasks_path, "dashboard_digest": content_fingerprint(dashboard)},
            "route_change": change}


def changed(root: Path, state: dict[str, Any], *, skip_current: bool = False) -> tuple[str, str] | None:
    for stage in state["visits"]:
        if stage["superseded"] or stage["decision"] == "stale" or not stage["packet"] or skip_current and stage["id"] == w.current(state)["id"]:
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
