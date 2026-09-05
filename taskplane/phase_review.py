"""Focused lens transport over explicit repository-phase inputs.

The incumbent applicability engine, focused policy, native result protocol,
and stateless worker startup remain the owners of their respective rules.
This adapter performs no repository or predecessor-state reads. The caller
owns current-attempt contracts and must supply trusted byte observations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import delivery_ports, design_host_transport, graph_primitives, lens, lens_route_policy
    from . import lens_signals, loop, phase_handoff, taskplane_lite as kernel
else:
    import delivery_ports
    import design_host_transport
    import graph_primitives
    import lens
    import lens_route_policy
    import lens_signals
    import loop
    import phase_handoff
    import taskplane_lite as kernel

Json = dict[str, Any]
PLAN_SCHEMA = "taskplane.phase-review-plan/v1"


def _phase(handoff: Json) -> str:
    phase = str(handoff["successor"]["phase"])
    if phase not in {"design", "plan"}:
        raise ValueError("focused phase review requires Design or Plan; Build has zero lenses")
    return phase


def _candidate(phase: str, artifact: Json, references: list[Json],
               contents: Mapping[str, bytes]) -> list[Json]:
    kinds = [phase, phase + "-narrative"]
    visual = artifact.get("visualization")
    if phase == "design" and isinstance(visual, dict) and visual.get("required") is True:
        if visual.get("path") != "design/visual.html":
            raise ValueError("focused Design review visual must use its canonical path")
        kinds.append("design-visual")
    checked = [phase_handoff._validate_artifact_shape(row) for row in references]
    if [row["kind"] for row in checked] != kinds or set(contents) != set(kinds):
        raise ValueError("focused phase review requires exact candidate machine, narrative and required visual bytes")
    hydrated = []
    for reference in checked:
        raw = contents[str(reference["kind"])]
        if not isinstance(raw, bytes) or len(raw) != reference["bytes"] or \
                hashlib.sha256(raw).hexdigest() != reference["digest"]:
            raise ValueError("focused phase review candidate bytes are stale")
        text = raw.decode("utf-8")
        if reference["kind"] == phase and json.loads(text) != artifact:
            raise ValueError("focused phase review candidate differs from authored artifact")
        hydrated.append({"reference": reference, "text": text})
    return hydrated


def _stage_evidence(phase: str, requirement: Json, graph: Json,
                    design: Json, plan: Json | None) -> Json:
    if phase == "design":
        return {
            "approved_requirement": copy.deepcopy(requirement),
            "acceptance": copy.deepcopy(requirement["acceptance"]),
            "proposed_solution": copy.deepcopy(design),
            "dependency_graph": copy.deepcopy(graph),
            "files": list(requirement.get("context_files") or []),
        }
    if plan is None or not isinstance(plan.get("tasks"), list):
        raise ValueError("focused Plan review requires an authored task array")
    tasks = plan["tasks"]
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
           for row in tasks):
        raise ValueError("focused Plan review tasks are invalid")
    scopes = {row["id"]: list(row.get("scope") or []) for row in tasks}
    return {
        "approved_product": copy.deepcopy(requirement),
        "approved_design": copy.deepcopy(design),
        "dependency_graph": copy.deepcopy(graph),
        "plan": copy.deepcopy(plan),
        "task_scopes": scopes,
        "ownership": {row["id"]: str(row.get("owner") or "") for row in tasks},
        "selectors": {row["id"]: str(row.get("tests") or "") for row in tasks},
        "validation_strategy": {row["id"]: row.get("tests") for row in tasks},
        "task_to_ac_coverage": {row["id"]: copy.deepcopy(
            row.get("acceptance_refs") or row.get("criteria") or []) for row in tasks},
        "files": sorted({path for paths in scopes.values() for path in paths}),
    }


def prepare(handoff: Json, *, attempt_id: str, requirement: Json, graph: Json,
            design: Json, candidate_artifacts: list[Json],
            candidate_content: Mapping[str, bytes], plan: Json | None = None,
            content_by_file: Mapping[str, str] | None = None,
            startup: Json | None = None) -> Json:
    """Project the exact focused route and fresh child contracts for a candidate.

    On replay the caller supplies the saved *current* lens startup. No lease,
    review run, or execution state is imported from the predecessor phase.
    Source text, when selected, must be explicitly supplied and already verified
    by the repository artifact loader. An empty mapping never triggers disk reads.
    """
    phase = _phase(handoff)
    if not isinstance(requirement, dict) or \
            requirement.get("id") != handoff["requirement"]["id"] or \
            requirement.get("acceptance") != [row["criterion"] for row in handoff["acceptance"]]:
        raise ValueError("focused phase review requirement is missing or foreign")
    if not isinstance(graph, dict) or not isinstance(graph.get("modules"), dict) or \
            not isinstance(graph.get("edges"), list) or not isinstance(graph.get("meta"), dict):
        raise ValueError("focused phase review requires an explicit full graph")
    if not isinstance(design, dict) or design.get("requirement") != requirement["id"]:
        raise ValueError("focused phase review requires the exact Design candidate or authority")
    artifact = design if phase == "design" else plan
    if not isinstance(artifact, dict):
        raise ValueError("focused phase review candidate is missing")
    hydrated = _candidate(phase, artifact, candidate_artifacts, candidate_content)
    evidence = _stage_evidence(phase, requirement, graph, design, plan)
    contents = dict(content_by_file or {})
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in contents.items()):
        raise ValueError("focused phase review selected source content is invalid")
    # The content map is an explicit closed input, including when empty.
    # graph_payload and the lower signal engine do not open a workspace here.
    module_ids = graph_primitives.declared_module_ids(graph)
    modules = [graph_primitives.module_of(path, module_ids) for path in evidence["files"]]
    graph_context = graph_primitives.graph_payload(
        graph, modules, fixture_module_predicate=graph_primitives.is_fixture_module)
    signals = lens_signals.route_verdicts(
        "", evidence["files"], stage=phase, graph=graph_context,
        requirement_text=json.dumps(evidence, sort_keys=True, ensure_ascii=False),
        content_by_file=contents)
    incumbent = {"lenses": [{"id": lens_id, **row} for lens_id, row in signals.items()],
                 "context": {"status": "ready"}}
    declared = plan.get("plan_route") if phase == "plan" and plan else None
    mandatory = declared.get("selected") if isinstance(declared, dict) else None
    route, _, _ = loop._focused_stage_route_from_incumbent(
        None, stage=phase, target=handoff["requirement"]["id"],
        evidence=evidence, incumbent=incumbent, mandatory_lenses=mandatory)
    if route["status"] != "ready":
        raise ValueError("focused Plan route requires scope split or authenticated expanded approval")
    selected_value = route["dispatchable_selected"]
    if not isinstance(selected_value, list):
        raise ValueError("focused phase review selected set is invalid")
    selected = list(selected_value)
    if len(selected) > 16:
        raise ValueError("focused phase review exceeds the native child bound")
    specs = [{"worker_id": lens_id, "lens": lens_id,
              "output": f"{phase}/lenses/{lens_id}.json"} for lens_id in selected]
    if startup is None:
        startup = kernel.stateless_phase_startup(handoff, workers=specs, attempt_id=attempt_id)
    else:
        kernel.validate_stateless_phase_startup(startup, handoff)
    if startup["attempt_id"] != attempt_id or \
            [{key: worker[key] for key in ("worker_id", "lens", "output")}
             for worker in startup["workers"]] != specs:
        raise ValueError("focused phase review startup is stale or belongs to another route")
    catalog = lens.load_catalog()
    settings = kernel._canonical_operational_settings(legacy_environment=True)
    workers = []
    for worker in startup["workers"]:
        # tp-lens has no implicit stage. Resolve the actual phase settings,
        # then use the same pure dispatch assembler; Plan must not inherit
        # Build defaults merely because the role is shared across stages.
        fields = delivery_ports.dispatch_envelope(
            "lens", "tp-lens", worker["worker_id"], "deep",
            role_instructions="agents/tp-lens.md",
            requested_model=settings.stages[phase].model,
            requested_effort=settings.stages[phase].reasoning,
            settings_digest=settings.digest, namespace=attempt_id)
        fields.pop("role_instructions", None)
        if fields["task_name"] != worker["task_name"]:
            raise ValueError("focused phase review native child identity differs from its contract")
        workers.append({**fields, "lens": worker["lens"],
            "task_slot": worker["task_slot"], "output": worker["output"],
            "role_reference": kernel.portable_role_reference("tp-lens"),
            "producer_contract": copy.deepcopy(worker["producer_contract"]),
            "scoped_view": copy.deepcopy(worker["scoped_view"]),
            "full_envelope_reference": copy.deepcopy(worker["full_envelope_reference"]),
            "brief": lens.lens_brief(worker["lens"], catalog)})
    material: Json = {
        "schema": PLAN_SCHEMA, "phase": phase, "attempt_id": attempt_id,
        "handoff_fingerprint": handoff["fingerprint"],
        "candidate_fingerprint": lens_route_policy.fingerprint({
            "handoff": handoff["fingerprint"], "artifacts": candidate_artifacts}),
        "candidate_artifacts": copy.deepcopy(candidate_artifacts),
        "candidate_content": hydrated, "stage_evidence": evidence,
        "selected_source_content": contents,
        "route": route, "selected": selected, "workers": workers,
    }
    material["fingerprint"] = lens_route_policy.fingerprint(material)
    dispatches = []
    for worker in workers:
        brief = design_host_transport.design_worker_brief(material, worker)
        brief.update({"phase": phase, "protocol": "repository-phase-review",
            "fork_turns": "none", "inherited_turns": 0,
            "candidate_artifacts": copy.deepcopy(candidate_artifacts),
            "candidate_content": copy.deepcopy(hydrated),
            "selected_source_content": copy.deepcopy(contents),
            "focused_route": copy.deepcopy(route),
            "environment": {"TASKPLANE_TASK": worker["task_slot"]},
            "output_paths": [worker["output"]]})
        if len(kernel.canonical_json_bytes(brief)) > kernel.MAX_STAGE_STARTUP_BYTES:
            raise ValueError("focused phase review brief exceeds its bounded input")
        dispatches.append(brief)
    return {"plan": material, "startup": startup, "dispatches": dispatches,
        "wait_policy": {"schema": "taskplane.wait-policy/v1", "mode": "event",
            "outstanding_set": material["fingerprint"], "outstanding_count": len(workers),
            "timeout_seconds": 1800, "minimum_timeout_seconds": 300,
            "reissue_after": ["completion", "attention"], "scheduled_polling": False}}


def collect(plan: Json, results: Mapping[str, bytes], *,
            verify_observation: Callable[[Json, bytes], None]) -> Json:
    """Conserve the selected set and validate each actual byte-bound result.

    verify_observation is the mandatory current-attempt native lifecycle seam;
    no caller-authored provenance flag or default successful receipt is accepted.
    This returns mechanical collection, never human approval or an embedded
    rewrite of the phase owner's artifact.
    """
    if plan.get("schema") != PLAN_SCHEMA or plan.get("phase") not in {"design", "plan"} or \
            plan.get("fingerprint") != lens_route_policy.fingerprint({
                key: value for key, value in plan.items() if key != "fingerprint"}):
        raise ValueError("focused phase review plan is stale or malformed")
    route = lens_route_policy.validate_route(plan["route"], lens.load_catalog()["lenses"])
    selected = plan["selected"]
    if not isinstance(selected, list) or \
            (plan["phase"] == "design" and
             (not 1 <= len(selected) <= 16 or "solution-design" not in selected)) or \
            (plan["phase"] == "plan" and len(selected) not in {3, 4}):
        raise ValueError("focused phase review requires its non-empty focused lens floor")
    if route["status"] != "ready" or route["stage"] != plan["phase"] or \
            selected != route["dispatchable_selected"] or \
            [worker["lens"] for worker in plan["workers"]] != selected or \
            set(results) != set(selected) or not callable(verify_observation):
        raise ValueError("focused phase review selected, dispatched and collected sets differ")
    collected = []
    for worker in plan["workers"]:
        raw = results[worker["lens"]]
        if not isinstance(raw, bytes):
            raise ValueError("focused phase review collection requires actual result bytes")
        verify_observation(copy.deepcopy(worker), raw)
        result = design_host_transport.validate_design_worker_result(
            plan, worker, json.loads(raw.decode("utf-8")))
        collected.append({"lens": worker["lens"], "outcome": result["outcome"],
            "result_fingerprint": result["fingerprint"],
            "output_digest": hashlib.sha256(raw).hexdigest(), "result": result})
    material = {"schema": "taskplane.phase-review-collection/v1",
        "plan_fingerprint": plan["fingerprint"], "candidate_fingerprint": plan["candidate_fingerprint"],
        "route_fingerprint": route["route_fingerprint"], "phase": plan["phase"],
        "route": copy.deepcopy(route),
        "status": "pass" if all(row["outcome"] == "pass" for row in collected)
                  else "changes-required", "results": collected,
        "human_approval": False}
    return {**material, "fingerprint": lens_route_policy.fingerprint(material)}
