"""Validate the native Plan artifact before projecting portable task contracts."""
from __future__ import annotations

import copy
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import loop, phase_handoff, phase_pickup, taskplane_lite as kernel
else:
    import loop
    import phase_handoff
    import phase_pickup
    import taskplane_lite as kernel


def selected_json(workspace: str, handoff: dict[str, Any], kind: str) -> dict[str, Any]:
    refs = [row for row in handoff["selected_artifacts"] if row["kind"] == kind]
    if len(refs) != 1 or refs[0]["media_type"] != "application/json":
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", f"phase requires one sealed {kind} JSON artifact")
    phase_handoff.validate_repository_artifact_reference(workspace, refs[0])
    _, data = phase_handoff._safe_regular_file(
        workspace, refs[0]["destination"], code="artifact-integrity")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise phase_pickup.PhasePickupError("authoring-invalid", f"sealed {kind} must be an object")
    return value


def _acceptance_ids(task: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
    refs = task.get("acceptance_refs", task.get("criteria"))
    declared = {row["criterion"]: row["id"] for row in handoff["acceptance"]}
    if (not isinstance(refs, list) or not refs or
            any(not isinstance(ref, str) or ref not in declared for ref in refs) or
            len(set(refs)) != len(refs)):
        raise phase_pickup.PhasePickupError(
            "scope-widened", "portable Plan requires exact, unambiguous acceptance ownership")
    return [row["id"] for row in handoff["acceptance"] if row["criterion"] in refs]


def validate_identity(handoff: dict[str, Any], plan: dict[str, Any]) -> None:
    expected = loop._plan_output_contract({
        "requirement_id": handoff["requirement"]["id"],
        "design_fingerprint": handoff["design"]["fingerprint"],
    })["template"]
    if (any(plan.get(key) != value for key, value in expected.items() if key != "tasks") or
            not isinstance(plan.get("tasks"), list) or
            not isinstance(plan.get("replan_history"), list)):
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "Plan must retain its native task schema and exact Design authority")
    validate_native_rows(plan["tasks"])


def validate_native_rows(tasks: object) -> None:
    """Refuse malformed native values before the incumbent semantic checks."""
    if not isinstance(tasks, list) or any(not isinstance(row, dict) for row in tasks):
        raise phase_pickup.PhasePickupError("authoring-invalid", "native Plan tasks must be object rows")
    errors = kernel.plan_task_id_errors(tasks)
    for row in tasks:
        for field in ("scope", "criteria", "deps", "acceptance_refs", "new_modules",
                      "modules"):
            value = row.get(field)
            if field not in row and field not in {"scope", "criteria"}:
                continue
            if (not isinstance(value, list) or
                    (field in {"scope", "criteria"} and not value) or
                    any(not isinstance(item, str) or not item.strip() for item in value)):
                errors.append(f"{field} must be a string list")
        for field in ("test_contract", "test_strategy_authority", "impact_policy", "impact"):
            if field in row and not isinstance(row[field], dict):
                errors.append(f"{field} must be an object")
        for field in ("tests", "title", "req", "type", "model"):
            if field in row and (not isinstance(row[field], str) or not row[field].strip()):
                errors.append(f"{field} must be nonempty text")
        contracts = row.get("contracts", [])
        if not isinstance(contracts, list) or any(
                not isinstance(item.get("id") if isinstance(item, dict) else item, str)
                for item in contracts):
            errors.append("contracts must contain text or contract objects with text ids")
    if errors:
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "native Plan task is malformed: " + "; ".join(errors))


def validate_obligation_ownership(handoff: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
    """The existing obligation receipt schema supports exactly one task owner."""
    for obligation in handoff["obligations"]:
        acceptance = set(obligation["acceptance"])
        owners = [task for task in tasks if task["id"] == obligation["id"] or
                  acceptance.intersection(task["acceptance"])]
        if len(owners) != 1:
            raise phase_pickup.PhasePickupError(
                "scope-widened", "each sealed obligation requires exactly one task owner; return to Plan")
        if not acceptance <= set(owners[0]["acceptance"]):
            raise phase_pickup.PhasePickupError(
                "scope-widened", "sealed task does not cover every acceptance criterion in a matched obligation; return to Plan")


def project_tasks(plan: dict[str, Any], handoff: dict[str, Any]) -> list[dict[str, Any]]:
    """No task-local criterion or proof may become whole-requirement authority."""
    native = plan["tasks"]
    validate_native_rows(native)
    if len({row["id"] for row in native}) != len(native):
        raise phase_pickup.PhasePickupError("authoring-invalid", "Plan task identities overlap")
    pending = list(native)
    projected: list[dict[str, Any]] = []
    while pending:
        done = {row["id"] for row in projected}
        ready = next((row for row in pending if set(row.get("deps") or []) <= done), None)
        if ready is None:
            raise phase_pickup.PhasePickupError("dependency-unmet", "Plan tasks have missing or cyclic dependencies")
        acceptance = _acceptance_ids(ready, handoff)
        proofs = sorted({proof for row in handoff["acceptance"] if row["id"] in acceptance
                         for proof in row["proofs"]})
        # Exact strings are the portable execution contract. Equivalence of
        # an aggregated/native shell command is not guessed or broadened.
        if proofs != [ready.get("tests")]:
            raise phase_pickup.PhasePickupError(
                "proof-invalid", "native Plan tests do not exactly match its sealed acceptance-proof closure")
        contracts = [row.get("id") if isinstance(row, dict) else row
                     for row in ready.get("contracts") or []]
        projected.append({
            "id": ready["id"], "ordinal": len(projected) + 1,
            "scope": copy.deepcopy(ready["scope"]),
            "dependencies": copy.deepcopy(ready.get("deps") or []),
            "contracts": contracts, "acceptance": acceptance, "proofs": proofs,
        })
        pending.remove(ready)
    validate_obligation_ownership(handoff, projected)
    return projected


def selected_inputs(workspace: str, handoff: dict[str, Any]) -> dict[str, Any]:
    """Refuse incompatible old exports before preparing a native worker."""
    requirement = selected_json(workspace, handoff, "requirement")
    graph = selected_json(workspace, handoff, "graph")
    design = selected_json(workspace, handoff, "design")
    rid = handoff["requirement"]["id"]
    if (requirement.get("id") != rid or requirement.get("acceptance") !=
            [row["criterion"] for row in handoff["acceptance"]] or
            design.get("requirement") != rid):
        raise phase_pickup.PhasePickupError("scope-widened", "sealed Plan inputs disagree on the requirement")
    required_contracts = {row.get("id") if isinstance(row, dict) else row
                          for row in requirement.get("contracts") or []}
    if required_contracts != {row["id"] for row in handoff["contracts"]}:
        raise phase_pickup.PhasePickupError("scope-widened", "sealed requirement contracts differ")
    # Existing strategy validation reads these canonical repository files.
    # Bind them to the selected artifacts before calling the shared checks.
    actual = phase_handoff.create_repository_artifact_reference(
        workspace, "design/contract.json", kind="design", publish=False)
    if actual != handoff["design"]["artifact"]:
        raise phase_pickup.PhasePickupError("authoring-invalid", "canonical Design differs from selected authority")
    strategy = (design.get("test_strategy") or {}).get("authority")
    if strategy is not None:
        if not isinstance(strategy, dict) or not isinstance(strategy.get("path"), str):
            raise phase_pickup.PhasePickupError("authoring-invalid", "Design strategy authority is malformed")
        actual_strategy = phase_handoff.create_repository_artifact_reference(
            workspace, strategy["path"], kind="test-strategy", publish=False)
        if actual_strategy not in handoff["selected_artifacts"]:
            raise phase_pickup.PhasePickupError("authoring-invalid", "Design strategy bytes are not sealed inputs")
    requirements = {rid: requirement}
    for dependency in requirement.get("depends_on") or []:
        record = selected_json(workspace, handoff, "requirement-" + dependency)
        if record.get("id") != dependency:
            raise phase_pickup.PhasePickupError("dependency-unmet", "selected requirement dependency is foreign")
        requirements[dependency] = record
    prior = selected_json(workspace, handoff, "plan") if handoff["plan"] is not None else None
    history = prior.get("replan_history") if prior is not None else []
    if not isinstance(history, list):
        raise phase_pickup.PhasePickupError("receipt-lineage", "selected Plan is missing sealed replan history")
    if any(not isinstance(record.get(field), list) for record in requirements.values()
           for field in ("acceptance", "contracts", "depends_on", "open_questions")) or (
            not isinstance(graph.get("modules"), dict) or
            not isinstance(graph.get("edges"), list) or not isinstance(graph.get("meta"), dict)):
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "Plan input requires complete requirement and graph snapshots; export fresh inputs")
    return {"requirements_by_id": requirements, "graph": graph,
            "approved_design": design, "replan_history": history, "prior": prior}


def validate_output(workspace: str, handoff: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Use existing Plan policy over explicit immutable phase inputs."""
    validate_identity(handoff, plan)
    inputs = selected_inputs(workspace, handoff)
    prior = inputs.pop("prior")
    if plan.get("replan_history") != inputs["replan_history"]:
        raise phase_pickup.PhasePickupError("receipt-lineage", "Plan must preserve sealed replan history")
    _, plan_bytes = phase_handoff._safe_regular_file(workspace, "plan/tasks.json", code="artifact-integrity")
    if json.loads(plan_bytes.decode("utf-8")) != plan:
        raise phase_pickup.PhasePickupError("artifact-integrity", "Plan argument differs from canonical output")
    state = {"requirement_id": handoff["requirement"]["id"], "design_required": True,
             "design_fingerprint": handoff["design"]["fingerprint"], "tasks": plan.get("tasks")}
    errors = loop._plan_dor_errors(workspace, state, sealed_inputs=inputs)
    if errors:
        raise phase_pickup.PhasePickupError("authoring-invalid", "Plan completion refused: " + "; ".join(errors))
    tasks = project_tasks(plan, handoff)
    if prior is not None:
        completed_acceptance = {ref for row in handoff["obligations"]
                                if row["id"] in handoff["progress"]["completed"]
                                for ref in row["acceptance"]}
        current = {row["id"]: row for row in plan["tasks"]}
        for old in prior["tasks"]:
            if completed_acceptance.intersection(_acceptance_ids(old, handoff)) and (
                    old["id"] not in current or loop._build_task_brief(old) !=
                    loop._build_task_brief(current[old["id"]])):
                raise phase_pickup.PhasePickupError("receipt-lineage", "Plan rewrites an already completed task contract")
    return tasks
