from taskplane.phase_records import resource_policy

"""Phase-attempt decisions through the existing run journal and lifecycle ports.

No session owns an attempt. Reads preserve preparation and uncertainty;
explicit retry uses the same journal and never fabricates host completion.
"""

from taskplane import phase_records
import copy
from collections.abc import Mapping
import os
import re
import time
from typing import Any, cast


def initialize(runtime: Any, workspace: str, state: dict[str, Any]) -> None:
    """Select the phase runtime for a newly initialized, attributable run."""
    from taskplane import review_evidence

    authority = state["_stage_native_root_authority"]
    store = runtime._stage_store(workspace, state["run_id"])
    manifest = store.load(state["run_id"])
    if phase_records.phase_routing(manifest) is not None:
        raise ValueError("a new run cannot inherit an earlier phase route")
    registry, _ = runtime._phase_bridge_registry(
        {"definition_source": "agents/spec-phase-definitions.json"}
    )
    artifacts = review_evidence.ArtifactStore(workspace)
    configuration = {
        "definition_source": "agents/spec-phase-definitions.json",
        "definition_set_fingerprint": registry.definition_set_fingerprint,
        "knowledge_reference": artifacts.put(
            "phase-knowledge",
            {
                "schema": "taskplane.scoped-knowledge/v1",
                "facts": [],
                "requirement_id": state["requirement_id"],
            },
        ),
        "candidate_fingerprint": review_evidence.content_fingerprint(
            {"revision": authority["target_revision"]}
        ),
        "target_revision": authority["target_revision"],
        "host_kind": manifest["host"]["kind"],
        "host_version": os.environ.get("TASKPLANE_HOST_VERSION")
        or os.environ.get("CLAUDE_CODE_VERSION")
        or "unknown",
        "output_paths": {
            "product": {"requirement": "specs/requirement.json"},
            "design": {
                "design": "design/contract.json",
                "test-strategy": "design/test-strategy.json",
            },
            "plan": {"plan-task": "plan/tasks.json"},
            "build": {},
            "evaluate": {"judgment": ".eval/phase-judgment.json"},
            "engineering": {"judgment": ".em-review/phase-judgment.json"},
            "retro": {"stage": "reports/phase-retro.json"},
        },
    }

    def authorize(current: Any) -> None:
        if current["run_id"] != state["run_id"] or current["repository"] != manifest["repository"]:
            raise ValueError("new phase route belongs to a different run")
        if (
            runtime._stage_native_init_authority(
                workspace, state["requirement_id"], authority["actor"]
            )
            != authority
        ):
            raise ValueError("new phase route authority changed")

    phase_records.change_phase_routing(
        store,
        state["run_id"],
        owner="agent-runtime",
        configuration=configuration,
        expected_previous=None,
        expected_revision=manifest["revision"],
        operation_id="initialize-phase-runtime",
        validate_authority=authorize,
    )


def compile_brief(
    context: dict[str, Any],
    action: dict[str, Any],
    requirement: object,
    workspace: str | None = None,
) -> None:
    """Compile one role's obligations from the admitted phase definition.

    Domain evidence/submission checks stay with their existing owners. This
    function does not approve, collect, invent a lens route, or author output.
    """
    import copy

    definition = context["definition"]
    phase = definition["id"]
    evaluator_result, review_root = ".eval/result.json", ".em-review"
    action["execution_mode"] = "stateless-phase"
    action["phase_definition"] = copy.deepcopy(definition)
    action["requirement"] = copy.deepcopy(requirement)
    policy = resource_policy(context.get("manifest", {}), str(context.get("run_id") or ""))
    if policy is not None:
        action["resource_policy"] = policy
    action["phase_outputs"] = [
        {
            **row,
            "path": action["phase_runtime"]["outputs"].get(row["artifact_class"]),
            "owner": "worker"
            if row["artifact_class"] in action["phase_runtime"]["outputs"]
            else "runtime",
        }
        for row in definition["produces"]
    ]
    instruction = (
        "Execute the selected stateless phase using only this action's saved inputs, "
        "stage startup and sealed package; do not recover predecessor conversations or workspaces. "
        "The phase_definition owns role, working/evaluation lenses, budget and output schemas. "
        "Write each worker-owned phase_outputs candidate at its exact declared path. "
        "Runtime-owned outputs are produced by the collector; do not fabricate them. "
        "An empty lens set authorizes no lens workers or legacy focused routing. "
        "Use contract_bootstrap.environment for scoped operations; an actual host Start must bind "
        "the slot before authorship. Preserve requirement identity, scope and acceptance criteria. "
        "Do not change run scope or approval, clear contracts or fabricate lifecycle observations. "
    )
    if phase == "product":
        instruction += (
            "Author a requirement candidate with schema taskplane.requirement/v1, the supplied id "
            "and title, and nonempty acceptance_criteria preserving the supplied acceptance criteria. "
            "Do not call req new or loop submit, or substitute specs/spec.md for the declared JSON. "
            "Return after writing; host Stop collects it and the orchestrator owns the gate. "
            "Do not claim native acceptance in the requirement artifact."
        )
    else:
        # Explicit domain ports retain substantive acceptance without importing
        # legacy routing, role changes or predecessor-session instructions.
        obligations = {
            "design": (
                "Remain read-only toward product code. Compare alternatives against the sealed "
                "requirement and baseline graph; define modules, contracts, graph DoR/DoD, depth "
                "policy, acceptance selectors, risks and rollout. Apply solution-design judgment. "
                "The design candidate uses taskplane.design/v1 and includes its selected "
                "test_strategy; author the declared test-strategy candidate too. Preserve the "
                "contract-required design/design.md narrative and design/contract.json projection "
                "for the existing substantive Design validator. Do not mutate the as-built graph. "
                "Return without loop submit; the orchestrator validates and presents human approval."
            ),
            "plan": (
                "Plan from the sealed requirement, approved Design and test strategy. At the "
                "declared plan-task path write a raw plan with a nonempty tasks array (or one raw "
                "task), not an already-sealed taskplane.plan-task/v1 envelope. Each task needs "
                "scope, tests as ONE command string, criteria, dependencies, exact contract ids, "
                "design_edges and impact policy. Cover approved modules, edges, depth policy and "
                "acceptance without drift. Derive dependency impact once, not per worker. Preserve "
                "plan/tasks.json and plan/plan.md required by the existing substantive Plan validator. "
                "The collector seals Design quality authority and dependency outputs; never invent "
                "their receipts. Return without loop submit; only the orchestrator requests a gate."
            ),
            "build": (
                "Implement only the supplied task under its contract and approved Design, using "
                "TDD and the declared phase lenses. For a fix, repair the listed failures and add "
                "a regression test. Preserve the bound test command, test-strategy authority and "
                "real evidence. The runtime produces realized-conformance. Report the actual "
                "outcome with loop submit pass or fail; submission alone is not a gate."
            ),
            "evaluate": (
                "Remain read-only. Start with tp loop evidence --write once for the exact diff, "
                "bound tests, acceptance, impact, contracts, Design conformance and provenance. "
                "Do not dispatch lens workers. Prove each criterion against real behavior; record "
                f"findings and an overall judgment in {evaluator_result} as required by the "
                "output contract as well as the declared phase candidate. Then loop submit pass "
                "or fail. Use structured unavailability and loop submit unavailable only when "
                "the existing bounded-attempt, green-test and no-product-defect conditions hold."
            ),
            "engineering": (
                "Remain read-only. Consume Evaluate's sealed direct evidence and judge requirement "
                "conformance; do not launch a legacy review sweep. Missing substantive evidence "
                "must return to Evaluate. Include the exact accepted_evaluations references in "
                "the phase judgment with task=engineering-signoff. Preserve the report.md and "
                f"findings.json output contract under {review_root}, including lens coverage, "
                "impact, approved Design conformance, tests and verdict. Read graph_conformance "
                "for the runtime's merged Plan proof. Include meta.graph with evidenced dispositions "
                "for directly affected dependencies, requirements_checked and contracts_checked "
                "across all tasks, using the same graph evidence shape as Evaluate. Record the verdict through "
                "the existing knowledge owner and loop submit the actual outcome. Only the "
                "orchestrator validates before presenting human sign-off."
            ),
            "retro": (
                "Consume only the sealed terminal package and author the declared retrospective. "
                "Do not seal the loop, admit knowledge, mint publication authority or publish. "
                "Return through the existing host completion hook."
            ),
        }
        instruction += obligations[phase] + (
            " Host Stop collects the exact attempt. Only the orchestrator may request a gate; "
            "collection is not human approval."
        )
    if policy is not None:
        instruction += (
            " The human's saved resource_policy makes execution resource limits advisory "
            "for this run. Preserve observed usage and declared limits; exceeding a limit is not "
            "a scope change, a retry grant or permission to bypass evidence or human gates."
        )
    action["instruction"] = instruction


def store_input(context: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """Seal the declared input before the phase preparation is committed."""
    from taskplane import review_evidence

    store = context["artifacts"]
    inputs = {
        "schema": "taskplane.phase-input/v1",
        "run_id": context["run_id"],
        "stage_id": context["stage"]["stage_id"],
        "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"],
        "phase_definition": action["phase_definition"],
        "requirement": action["requirement"],
        "outputs": action["phase_outputs"],
        "instruction": action["instruction"],
        **({"resource_policy": action["resource_policy"]} if "resource_policy" in action else {}),
        **({"task": action["task"]} if "task" in action else {}),
        **(
            {"accepted_evaluations": action["accepted_evaluations"]}
            if "accepted_evaluations" in action
            else {}
        ),
        **(
            {"graph_conformance": action["graph_conformance"]}
            if "graph_conformance" in action
            else {}
        ),
        **({"lens_plan": action["lens_plan"]} if "lens_plan" in action else {}),
        **({"graph_baseline": action["graph_baseline"]} if "graph_baseline" in action else {}),
        "artifacts": [
            {
                **row,
                "reference": review_evidence.portable_artifact_reference(store, row["reference"]),
            }
            for row in action["phase_runtime"]["package"]
        ],
    }
    # Only output locations are paths; scope and explicit requirement text
    # remain domain input. Host roots stay in the launch environment.
    for output in inputs["outputs"]:
        path = output["path"]
        if path is not None and (
            os.path.isabs(path)
            or re.match(r"^[A-Za-z]:", path)
            or ".." in path.replace("\\", "/").split("/")
        ):
            raise ValueError("phase output paths must be workspace-relative")
    return review_evidence.portable_artifact_reference(store, store.put("phase-input", inputs))


def dispatch_action(
    runtime: Any, workspace: str, context: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    """Emit only a sealed worker input and the host's launch obligations."""
    envelope = runtime.tp.attach_phase_input(
        action["stage_runtime_dispatch"], action["phase_runtime"]["input"]
    )
    host_keys = {
        "step",
        "role",
        "role_marker",
        "task_name",
        "model_tier",
        "model",
        "reasoning_effort",
        "dispatch_route",
        "settings_digest",
        "dispatch_intent",
        "wait_policy",
        "contract_bootstrap",
    }
    obligations = {key: action[key] for key in host_keys if key in action}
    obligations["phase_operation"] = action["phase_runtime"]["operation_id"]
    obligations["dispatch_allowed"] = True
    if action.get("evidence_children"):
        obligations["children"] = [
            {
                "schema": "taskplane.stage-dispatch/v1",
                "stage_runtime_dispatch": runtime.tp.attach_phase_input(
                    action["stage_runtime_dispatch"], child["evidence_input"]
                ),
                "obligations": {
                    **{key: child[key] for key in host_keys if key in child},
                    "dispatch_allowed": True,
                },
            }
            for child in action["evidence_children"]
        ]
    return {
        "schema": "taskplane.stage-dispatch/v1",
        "stage_runtime_dispatch": envelope,
        "obligations": obligations,
    }


def read_input(runtime: Any, workspace: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Resolve only the startup's exact input, under current stage authority."""
    from taskplane import review_evidence

    runtime.tp.stage_startup_bytes(envelope)
    startup = envelope["startup"]
    context = runtime._stage_loop_context(workspace, stage_id=startup["stage_id"])
    if context is None or context.get("stage") is None:
        raise ValueError("phase input has no current stage")
    runtime._phase_bridge_authorize(workspace, context, context["store"].load(context["run_id"]))
    if startup["authority"] != runtime.tp._stage_authority_reference(context["stage"]["authority"]):
        raise ValueError("phase input startup authority changed")
    row = phase_records.phase_records(context["manifest"]).get(operation_id(context))
    if row is None:
        raise ValueError("phase input has no committed preparation")
    store = review_evidence.ArtifactStore(workspace)
    material = store.read(row["result"]["reference"])
    slot = runtime.tp.task_slot()
    if slot is not None:
        contract = runtime.tp.load_json(
            runtime.tp.active_contract_path(workspace, slot), default=None
        )
        if isinstance(contract, dict) and contract.get("evidence_input") == startup["phase_input"]:
            value = store.read(startup["phase_input"])
            if value.get("schema") != "taskplane.evaluate-child-input/v1" or any(
                value.get(key) != expected
                for key, expected in {
                    "stage_id": context["stage"]["stage_id"],
                    "run_id": context["run_id"],
                    "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"],
                }.items()
            ):
                raise ValueError("evidence input belongs to different authority")
            return cast(dict[str, Any], value)
        if (
            not isinstance(contract, dict)
            or (contract.get("phase_runtime") or {}).get("reference") != row["result"]["reference"]
        ):
            raise ValueError("worker cannot read another phase's input")
    if material.get("worker_input_reference") != startup["phase_input"]:
        raise ValueError("phase input differs from its committed preparation")
    value = store.read(startup["phase_input"])
    if value.get("schema") != "taskplane.phase-input/v1" or any(
        value.get(key) != expected
        for key, expected in {
            "stage_id": context["stage"]["stage_id"],
            "run_id": context["run_id"],
            "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"],
        }.items()
    ):
        raise ValueError("phase input belongs to different authority")
    return cast(dict[str, Any], value)


def accepted_evaluations(
    runtime: Any, context: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Join only collected, accepted task judgments from this run's aggregate."""
    from taskplane import review_evidence

    store = context["artifacts"]
    rows = phase_records.phase_records(context["store"].load(context["run_id"]))
    committed = [row["result"] for row in rows.values() if row["operation"] == "phase_collect"]
    accepted = []
    for task in state.get("tasks") or []:
        if task.get("status") != "passed":
            continue
        completion = task.get("evaluation_phase")
        if not isinstance(completion, dict) or completion not in committed:
            raise ValueError("Engineering task lacks its committed Evaluate collection")
        result = store.read(completion["runtime_result"])
        if (
            result["run_id"] != context["run_id"]
            or result["phase_id"] != "evaluate"
            or result["status"] != "accepted"
        ):
            raise ValueError("Engineering task collection is foreign or unaccepted")
        references = [
            ref for ref in result["collected_output_references"] if ref["kind"] == "judgment"
        ]
        if len(references) != 1:
            raise ValueError("Engineering task judgment selection is ambiguous")
        judgment = runtime.validate_spec_phase_artifact(store.read(references[0]))
        if judgment["task"] != task["id"] or judgment["verdict"] != "pass":
            raise ValueError("Engineering task judgment does not match its accepted task")
        accepted.append(
            {
                "task_id": task["id"],
                "candidate_sha": task["target_commit"],
                **{
                    key: review_evidence.portable_artifact_reference(store, reference)
                    for key, reference in {
                        "judgment": references[0],
                        "handoff": completion["handoff"],
                        "runtime_receipt": completion["runtime_receipt"],
                    }.items()
                },
            }
        )
        lens_refs = [
            ref for ref in result["collected_output_references"] if ref["kind"] == "lens-evidence"
        ]
        if len(lens_refs) != 1:
            raise ValueError("Engineering task lacks its complete lens evidence package")
        accepted[-1]["lens_evidence"] = review_evidence.portable_artifact_reference(
            store, lens_refs[0]
        )
    if not accepted:
        raise ValueError("Engineering requires accepted task evaluations")
    return accepted


def admit_lens_plan(context: dict[str, Any]) -> None:
    """Validate the declared floors; only the saved attempt may dispatch them."""
    from taskplane import lens

    definition = context["definition"]
    selected = definition["working_lenses"] + definition["evaluation_lenses"]
    known = {row["id"] for row in lens.load_catalog()["lenses"]}
    if len(selected) != len(set(selected)) or not set(selected) <= known:
        raise ValueError("phase lenses must be distinct catalog identities")
    if selected and definition["id"] not in {"product", "design", "plan"}:
        raise ValueError("this phase consumes lens evidence and cannot dispatch workers")


def _prepared_plan(
    runtime: Any, context: Any, material: dict[str, Any], planned: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Keep a fixed Plan candidate's reviewed topology, never its verdict.

    The saved graph is the admitted source snapshot. Reading it does not scan
    the repair/runtime checkout again or turn elapsed scan time into new scope.
    """
    from taskplane import review_evidence, stage_artifacts

    store = context["artifacts"]
    binding = {
        key: material["bindings"][key]
        for key in (
            "run_id",
            "phase_id",
            "operation_id",
            "candidate_fingerprint",
            "authority_fingerprint",
        )
    }
    graph = runtime.depgraph.load(context.get("workspace", store.workspace))
    expected = {key: value for key, value in planned.items() if key != "dependency_outputs"}
    matches = []
    for reference in store.references("lens-plan"):
        plan = store.read(reference)
        if plan.get("phase") != "plan" or plan.get("binding") != binding:
            continue
        envelope = review_evidence._load_complete_envelope(store, plan["envelope"])
        candidates = envelope["diff"]["phase_inputs"]["candidate"]
        previous = candidates["plan-task"]
        if {
            key: value for key, value in previous.items() if key != "dependency_outputs"
        } != expected:
            continue
        # This incumbent comparison removes only validated coverage elapsed_ms
        # and graph timestamps. Source hashes, edge/content fingerprints,
        # scanner quality, policy, completeness and limits remain exact.
        previous_graph = envelope["impact"]["graph"]
        if runtime.depgraph._scan_volatile_stripped(
            {"meta": previous_graph}
        ) != runtime.depgraph._scan_volatile_stripped({"meta": graph["meta"]}):
            continue
        for name, value in candidates.items():
            stage_artifacts.validate(name, value)
        topology = previous["dependency_outputs"]
        if (
            topology
            != {
                name: candidates[name]
                for name in ("source-coverage", "decomposition", "seam-manifest")
            }
            or topology["source-coverage"] != previous_graph["source_coverage"]
            or topology["decomposition"]["source_tree"] != previous_graph["source_tree"]
        ):
            raise ValueError("prepared Plan topology differs from its reviewed source snapshot")
        matches.append((reference, plan, envelope))
    if len(matches) > 1:
        # Older runtimes could prepare duplicate contexts from timing alone.
        # File presence identifies the submitted lease, irrespective of pass,
        # fail, malformed output or signature. Normal ingestion verifies all
        # provenance after selection; it never copies results across leases.
        submitted = [
            row
            for row in matches
            if any(
                os.path.isfile(
                    slot["result_path"]
                    if os.path.isabs(slot["result_path"])
                    else os.path.join(store.workspace, slot["result_path"])
                )
                for slot in row[1]["slots"]
            )
        ]
        if len(submitted) != 1:
            raise ValueError(
                "multiple prepared Plan snapshots need an exact submitted lease selection"
            )
        matches = submitted
    return matches[0] if matches else None


def prepare_lenses(
    runtime: Any,
    context: Any,
    material: dict[str, Any],
    worker_input: dict[str, Any],
    candidates: dict[str, Any],
) -> dict[str, Any]:
    """Select only this attempt's immutable input, then use the shared kernel."""
    from taskplane import review, review_evidence, lens

    definition, store = context["definition"], context["artifacts"]
    requirement = worker_input["requirement"]
    inputs = {
        row["artifact_class"]: store.read(row["reference"])
        for row in material["package"]
        if row["artifact_class"] != "lens-evidence"
    }
    if "graph_baseline" in worker_input:
        inputs["graph_baseline"] = store.read(worker_input["graph_baseline"])
    inputs["candidate"] = candidates
    files = sorted(
        set(requirement.get("context_files") or []) | set(material["output_paths"].values())
    )
    impact, graph_quality = {}, {}
    prepared = None
    if definition["id"] == "plan":
        # The shared candidate reader has already run the Plan producer. These
        # exact bytes are reviewed and subsequently sealed, with no second
        # topology producer after the lenses finish.
        for name in ("source-coverage", "decomposition", "seam-manifest"):
            inputs[name] = candidates[name]
        workspace = context.get("workspace", store.workspace)
        graph = runtime.depgraph.load(workspace)
        if graph["meta"]["source_tree"] != candidates["decomposition"]["source_tree"]:
            raise ValueError("Plan lens graph differs from its produced source topology")
        planned = candidates["plan-task"]
        tasks = planned["plan"]["tasks"] if "plan" in planned else [planned["task"]]
        prepared = _prepared_plan(runtime, context, material, planned)
        if prepared is None:
            impact = runtime.depgraph.impact(
                workspace,
                [path for task in tasks for path in task["scope"]],
                policy=runtime.depgraph.aggregate_impact_policy(tasks),
            )
            graph_quality = runtime.depgraph.scan_quality(graph)
        else:
            impact = prepared[2]["impact"]
            graph_quality = prepared[2]["graph_quality"]
        files = sorted(
            set(files)
            | {
                path
                for component in candidates["decomposition"]["components"]
                for path in component["files"]
            }
        )
    selected = definition["working_lenses"] + definition["evaluation_lenses"]
    binding = {
        key: material["bindings"][key]
        for key in (
            "run_id",
            "phase_id",
            "operation_id",
            "candidate_fingerprint",
            "authority_fingerprint",
        )
    }
    if selected:
        policy = runtime.operational_settings.load_settings(
            environment=runtime.os.environ
        ).lenses.policy_for(
            definition["id"], catalog_ids={row["id"] for row in lens.load_catalog()["lenses"]}
        )
        _, routing, request = runtime._focused_stage_route(
            store.workspace,
            stage=definition["id"],
            target=binding["candidate_fingerprint"],
            evidence={"requirement": requirement, "artifacts": inputs, "files": files},
            mandatory_lenses=list(dict.fromkeys([*selected, *policy.mandatory])),
            maximum_lenses=policy.max_count,
        )
        if request is not None:
            raise ValueError("phase lens selection requires its explicit expanded-route decision")
    else:
        routing = {
            "lenses": [
                {
                    **row,
                    "tier": "n/a",
                    "negative_evidence": [
                        definition["id"] + " consumes prior lens results; no workers selected"
                    ],
                }
                for row in lens.load_catalog()["lenses"]
            ]
        }
    envelope = review_evidence.create_envelope(
        store,
        target={"fingerprint": review_evidence.content_fingerprint(binding), **binding},
        diff={"files": files, "phase_inputs": inputs},
        impact=impact,
        graph_quality=graph_quality,
        runnability={},
        requirement=requirement,
        acceptance=requirement.get("acceptance_criteria") or requirement.get("acceptance") or [],
        contracts=[],
    )
    if (
        prepared is not None
        and envelope == prepared[1]["envelope"]
        and review._routing_decision(routing, lens.load_catalog()) == prepared[1]["decision"]
    ):
        return prepared[0]
    return review.prepare_lens_plan(
        store, envelope, routing, phase=definition["id"], binding=binding
    )


def phase_candidates(runtime: Any, workspace: str, material: dict[str, Any]) -> dict[str, Any]:
    """Read selected drafts and derive Plan outputs before review or collection."""
    authored = {}
    for artifact_class, path in material["output_paths"].items():
        output_root, relative = runtime._phase_bridge_output_location(workspace, path)
        raw = runtime._stage_loop_read_output_no_follow(
            output_root,
            relative,
            required=True,
            remaining_bytes=os.stat(os.path.join(output_root, relative)).st_size,
        )
        authored[artifact_class] = runtime.json.loads(raw)
    if material["bindings"]["phase_id"] == "plan":
        state = runtime.load(workspace)
        context = runtime._phase_bridge_context(workspace, state)
        package = input_package(runtime, context)
        planned = runtime.seal_phase_plan_task(
            context["artifacts"], package, state, authored["plan-task"]
        )
        prepared = _prepared_plan(runtime, context, material, planned)
        if prepared is not None:
            return cast(
                dict[str, Any], copy.deepcopy(prepared[2]["diff"]["phase_inputs"]["candidate"])
            )
        authored = runtime.produce_spec_phase_candidates(
            context["artifacts"],
            context["definition"],
            authored,
            package=package,
            state=state,
            workspace=workspace,
        )
    return authored


def collect_lenses(
    runtime: Any, workspace: str, envelope: dict[str, Any], *, prepare: bool = False
) -> dict[str, Any]:
    """Bind the exact current candidate, then dispatch or collect its shared plan."""
    from taskplane import review

    inputs = read_input(runtime, workspace, envelope)
    context = runtime._phase_bridge_context(workspace, runtime.load(workspace))
    if context["stage"]["stage_id"] != inputs["stage_id"]:
        raise ValueError("lens request belongs to another phase")
    row = phase_records.phase_records(context["store"].load(context["run_id"]))[
        operation_id(context)
    ]
    material = context["artifacts"].read(row["result"]["reference"])
    candidates = phase_candidates(runtime, workspace, material)
    plan = prepare_lenses(runtime, context, material, inputs, candidates)
    if prepare:
        collected = review.collect_lens_plan(context["artifacts"], plan)
        if collected["status"] == "complete":
            return {
                "plan": plan,
                "dispatch": [],
                "wait_invocation": None,
                "report": context["artifacts"].read(collected["collection"]),
            }
        bound = runtime._bind_stateless_review_contract_actions(
            workspace,
            {
                "status": "ready",
                "run_id": context["run_id"],
                "slots": context["artifacts"].read(plan)["dispatch"],
            },
            task_id=material["bindings"]["operation_id"],
        )
        return {
            "plan": plan,
            "dispatch": bound["slots"],
            "wait_invocation": bound.get("wait_invocation"),
        }
    collected = review.collect_lens_plan(context["artifacts"], plan)
    # The phase needs these findings now. Returning only an opaque reference
    # forced another model/tool round trip to discover and read the same result.
    return {**collected, "report": context["artifacts"].read(collected["collection"])}


def lens_evidence(store: Any, material: dict[str, Any]) -> dict[str, Any]:
    """Retain full collections across transformations, including zero-lens phases."""
    from taskplane import review, review_evidence, stage_artifacts

    by_plan: dict[str, dict[str, Any]] = {}
    human_amendments: dict[str, dict[str, Any]] = {}
    references = [
        artifact["reference"]
        for artifact in material["package"]
        if artifact["artifact_class"] == "lens-evidence"
    ]
    if material["bindings"]["phase_id"] == "engineering" and "worker_input_reference" in material:
        inputs = store.read(material["worker_input_reference"])
        references.extend(row["lens_evidence"] for row in inputs["accepted_evaluations"])
    for reference in references:
        packet = stage_artifacts.read(store, reference, "lens-evidence")
        from taskplane import phase_amendment

        phase_amendment.verify_human_evidence(
            store, packet.get("human_amendments", []), material["bindings"]["run_id"]
        )
        for decision in packet.get("human_amendments", []):
            human_amendments[decision["fingerprint"]] = decision
        for entry in packet["entries"]:
            plan = store.read(entry["plan"])
            collection = store.read(entry["collection"])
            if (
                plan["binding"]["run_id"] != material["bindings"]["run_id"]
                or collection["status"] != "complete"
            ):
                raise ValueError("inherited lens evidence is foreign or incomplete")
            identity = entry["plan"]["fingerprint"]
            if identity in by_plan and by_plan[identity] != entry:
                raise ValueError("parallel branches disagree on the same lens collection")
            by_plan[identity] = entry
    collected = review.collect_lens_plan(store, material["lens_plan"])
    if collected["status"] != "complete":
        raise ValueError("phase lens results are incomplete: " + str(collected["collection"]))
    entries = list(by_plan.values()) + [collected]
    value = {"schema": "taskplane.lens-evidence/v1", "entries": entries}
    if human_amendments:
        value["human_amendments"] = list(human_amendments.values())
    return {**value, "fingerprint": review_evidence.content_fingerprint(value)}


def lens_gate(runtime: Any, workspace: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Consume canonical verdicts without reclassifying or losing their evidence."""
    from taskplane import phase_amendment

    amendment = phase_amendment.current(runtime, workspace, state)
    if amendment is not None and amendment["phase"] == "design":
        return None  # Explicit human amendment, never an automated lens pass.
    pending = runtime._phase_bridge_pending(workspace, state)
    completion = ((pending or {}).get("phase_runtime") or {}).get("completion")
    if completion is None:
        return None  # The existing runtime gate owns missing completion.
    from taskplane import review_evidence

    store = review_evidence.ArtifactStore(workspace)
    result = store.read(completion["runtime_result"])
    for reference in result["collected_output_references"]:
        if reference["kind"] != "lens-evidence":
            continue
        for entry in store.read(reference)["entries"]:
            collection = store.read(entry["collection"])
            failed = [
                verdict["lens"]
                for row in collection["results"]
                for verdict in row["lens_results"]
                if verdict["verdict"] == "fail"
            ]
            if failed:
                return {
                    "error": "selected lens findings require changes: " + ", ".join(failed),
                    "lens_evidence": review_evidence.portable_artifact_reference(store, reference),
                    "collection": entry["collection"],
                }
    return None


def _initial_operation(context: dict[str, Any]) -> str:
    prepared = sorted(
        (
            row
            for row in phase_records.phase_records(context["manifest"]).values()
            if row["operation"] == "phase_prepare"
            and row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]
        ),
        key=lambda row: row["committed_revision"],
    )
    return cast(
        str,
        prepared[0]["operation_id"]
        if prepared
        else "phase-attempt-" + context["stage"]["fingerprint"][:32],
    )


def retry_chain(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Follow explicit retry grants in the existing run journal, never time."""
    operation = _initial_operation(context)
    rows = sorted(
        (
            row
            for row in phase_records.phase_records(context["manifest"]).values()
            if row["operation"] == "phase_retry"
            and row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]
        ),
        key=lambda row: row["committed_revision"],
    )
    for row in rows:
        result = row["result"]
        request_keys = {
            "run_id",
            "stage_fingerprint",
            "previous_operation",
            "candidate_fingerprint",
            "actor",
            "worker_stopped",
            "additional_attempts",
        }
        from taskplane import review_evidence

        if (
            set(result)
            != request_keys
            | {
                "schema",
                "next_operation",
                "previous_preparation",
                "contract_slot",
                "contract_reference",
                "routing",
                "old_result",
                "approved_in_session",
            }
            or result.get("schema") != "taskplane.phase-retry/v1"
            or review_evidence.content_fingerprint({key: result[key] for key in request_keys})
            != row["request_fingerprint"]
            or result.get("run_id") != context["run_id"]
            or result.get("previous_operation") != operation
            or result.get("additional_attempts") != 1
            or result.get("worker_stopped") is not True
            or result.get("next_operation") != "phase-attempt-" + row["request_fingerprint"][:32]
        ):
            raise ValueError("phase retry chain does not verify")
        operation = result["next_operation"]
    allowed = {_initial_operation(context)} | {row["result"]["next_operation"] for row in rows}
    if any(
        row["operation"] == "phase_prepare"
        and row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]
        and row["operation_id"] not in allowed
        for row in phase_records.phase_records(context["manifest"]).values()
    ):
        raise ValueError("multiple ungranted phase preparations")
    return rows


def operation_id(context: dict[str, Any]) -> str:
    retries = retry_chain(context)
    return str(retries[-1]["result"]["next_operation"]) if retries else _initial_operation(context)


def usage_evidence(
    workspace: str, material: Mapping[str, Any], terminal: dict[str, Any]
) -> dict[str, Any]:
    """An authenticated terminal's inline count or its exact native counter."""
    result = {
        "tokens": terminal["tokens"],
        "source": "host-stop" if terminal["tokens"] is not None else "unavailable",
    }
    if result["tokens"] is None and str(material["bindings"]["host_kind_version"]).startswith(
        "codex:"
    ):
        from taskplane import codex_identity

        try:
            snapshot = codex_identity.observed_usage(workspace, terminal)
            result = {
                "tokens": snapshot["usage"]["total_tokens"],
                "source": "native-counter",
                "snapshot_fingerprint": snapshot["fingerprint"],
            }
        except (ValueError, OSError):
            pass  # Unknown is never zero or a waiver.
    return result


def advise_resource_limits(runtime: Any, ws: str, state: dict[str, Any], by: str) -> dict[str, Any]:
    """Explicit human policy, not a settings rewrite, retry or scope approval."""
    from taskplane import review_evidence

    if runtime.tp.task_slot() is not None:
        raise ValueError("resource policy is orchestrator-only")
    context = runtime._phase_bridge_context(ws, state)
    if (
        context is None
        or not by
        or by != (state.get("_stage_native_root_authority") or {}).get("actor")
    ):
        raise ValueError("resource policy requires the existing run's human --by")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    prior = resource_policy(context["manifest"], context["run_id"])
    if prior is not None:
        return {"resource_policy": prior, "replay": True, "dispatch_allowed": False}
    value = {
        "schema": "taskplane.resource-policy/v1",
        "run_id": context["run_id"],
        "mode": "advisory",
        "actor": by,
        "decided_at": int(time.time()),
        "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"],
    }
    phase_records.commit_phase_record(
        context["store"],
        context["run_id"],
        expected_revision=context["manifest"]["revision"],
        operation_id="run-resource-limits",
        operation="resource_policy",
        request_fingerprint=review_evidence.content_fingerprint(value),
        result=value,
        validate_authority=lambda current: runtime._phase_bridge_authorize(ws, context, current),
    )
    return {
        "resource_policy": resource_policy(
            context["store"].load(context["run_id"]), context["run_id"]
        ),
        "replay": False,
        "dispatch_allowed": False,
    }


def reconcile(runtime: Any, ws: str, state: dict[str, Any], operation: str) -> dict[str, Any]:
    """Validate current candidates using genuine completion, never a replayed hook."""
    from taskplane import codex_identity

    if runtime.tp.task_slot() is not None:
        raise ValueError("phase reconciliation is orchestrator-only")
    context = runtime._phase_bridge_context(ws, state)
    if context is None or operation != operation_id(context):
        raise ValueError("reconcile must name the exact current phase operation")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    row = phase_records.phase_records(context["manifest"]).get(operation)
    if row is None or row["operation"] != "phase_prepare":
        raise ValueError("reconcile requires an existing prepared attempt")
    material = context["artifacts"].read(row["result"]["reference"])
    slot = material["contract_slot"]
    path = runtime.tp.active_contract_path(ws, slot)
    contract = runtime.tp.load_json(path, default=None, what="phase worker contract")
    if contract is None:
        contract = runtime.tp.released_worker_contract(ws, slot)
    attempt = runtime._phase_bridge_attempt(ws, contract)
    if attempt is None or attempt[0]["operation_id"] != operation:
        raise ValueError("phase contract is foreign")
    source, dispatch = attempt[4].nonce, attempt[5]
    source.validate(
        dispatch.issued,
        dispatch.nonce_bindings,
        enforce_deadline=not attempt[4].resource_limits_advisory,
    )
    hooks = source.terminal_hooks(dispatch.issued, dispatch.nonce_bindings)
    if hooks is None:
        if context["stage"]["stage_kind"] not in {"product", "design", "plan"}:
            raise ValueError("provider completion reconciliation is pre-build only")
        start = source._read_phase_hook(dispatch.issued, dispatch.nonce_bindings, "start")
        terminal = codex_identity.completed_child(ws, start)
    else:
        start, terminal = hooks
    owner = (contract.get("worker_lifecycle") or {}).get("owner")
    if not isinstance(owner, dict) or any(
        owner != {key: observed["owner"][key] for key in ("session_id", "agent_id", "task_name")}
        for observed in (start, terminal)
    ):
        raise ValueError("phase terminal owner differs from the bound worker")
    record_dispatch(runtime, ws, contract, material, start)
    result = runtime._collect_phase_attempt(
        ws, attempt, completed_worker=terminal if hooks is None else None
    )
    if result["status"] != "collected":
        return {**result, "dispatch_allowed": False}
    _reconcile_usage(runtime, ws, contract, material, terminal)
    runtime.collect_phase_runtime_telemetry(ws, contract)
    if hooks is None:
        # Current validation is not a historical Stop. Keep the slot intact;
        # the ordinary successful gate owns its retirement.
        return {
            **result,
            "worker_released": False,
            "dispatch_allowed": False,
            "validation": "current-semantic",
            "completion_source": "codex-task-complete",
        }
    lifecycle = contract["worker_lifecycle"]
    with runtime.tp.file_lock(path):
        current = runtime.tp.load_json(path, default=None, what="phase worker contract")
        if current is not None:
            terminal_receipt = (current.get("worker_lifecycle") or {}).get("terminal")
            if terminal_receipt is None:
                terminal_receipt = runtime.tp.record_worker_terminal(
                    ws,
                    slot,
                    event=None,
                    outcome=terminal["outcome"],
                    submission_status="phase-collected:" + terminal["claim"],
                    authority="phase-observation",
                )
            runtime.tp.release_worker_contract(
                ws, slot, action=lifecycle["release_action"], terminal_receipt=terminal_receipt
            )
    return {**result, "worker_released": True, "dispatch_allowed": False}


def record_dispatch(
    runtime: Any, ws: str, contract: dict[str, Any], material: dict[str, Any], start: dict[str, Any]
) -> None:
    """Project an authenticated phase Start, even if the tool hook was absent.

    Called only with the nonce owner's freshly verified or persisted receipt.
    This records an existing host fact, never grants a launch or invents one.
    """
    lifecycle = contract["worker_lifecycle"]
    name = material["envelope"]["task_name"]
    if (
        start["kind"] != "start"
        or start["owner"]["task_name"] != name
        or lifecycle["dispatch_intent_id"] != material["bindings"]["attempt_id"]
        or lifecycle["owner"]
        != {key: start["owner"][key] for key in ("session_id", "agent_id", "task_name")}
    ):
        raise ValueError("phase Start does not match its prepared dispatch")
    runtime.record_native_dispatch_observation(
        ws,
        expected={
            "intent_id": material["bindings"]["attempt_id"],
            "intent_run_id": material["bindings"]["run_id"],
            "ref": lifecycle["task"],
            "kind": "step",
            "agent": material["envelope"]["role"],
            "task_name": name,
        },
        native_task_name=name,
        observed_at=start["observed_at"],
    )
    # An active sibling can keep running while another phase is admitted.
    # Retain its actual initial host counter so dispatch accounting does not
    # require that sibling to finish first.
    from taskplane import codex_identity

    try:
        snapshot = codex_identity.observed_usage(ws, start)
    except ValueError:
        return  # Missing counters stay unavailable, never an invented zero.
    _record_usage_snapshot(runtime, ws, lifecycle, snapshot)


def _record_usage_snapshot(runtime: Any, ws: Any, lifecycle: Any, snapshot: Any) -> None:
    task_id, dispatch_id = lifecycle["task"], lifecycle["dispatch_intent_id"]
    recorded = runtime.record_native_session_snapshot(
        ws, task_id=task_id, dispatch_id=dispatch_id, snapshot=snapshot
    )
    usage = recorded["dispatch_usage"]
    runtime.record_observed_dispatch_usage(
        ws,
        task_id=task_id,
        dispatch_id=dispatch_id,
        native_task_name=lifecycle["expected_task_name"],
        source_fingerprint=snapshot["source"]["path_fingerprint"],
        normalized_usage={
            **usage,
            "schema": "taskplane.token-usage/v2",
            "available": True,
            "raw_total_tokens": usage["total_tokens"],
        },
    )


def _reconcile_usage(
    runtime: Any,
    ws: str,
    contract: dict[str, Any],
    material: dict[str, Any],
    terminal: dict[str, Any],
) -> None:
    """Project the verified terminal and exact counter through existing owners."""
    from taskplane import codex_identity

    lifecycle = contract["worker_lifecycle"]
    task_id, dispatch_id = lifecycle["task"], lifecycle["dispatch_intent_id"]
    name = lifecycle["expected_task_name"]
    unavailable = False
    try:
        snapshot = codex_identity.observed_usage(ws, terminal)
    except (ValueError, OSError):
        snapshot = None
    if snapshot is not None:
        _record_usage_snapshot(runtime, ws, lifecycle, snapshot)
    else:
        from taskplane import dispatch_telemetry

        ledger = runtime.load(ws).get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = next(row for row in ledger["bindings"] if row["dispatch_id"] == dispatch_id)
        unavailable = binding.get("usage") is None
    runtime.finalize_observed_dispatch_usage(
        ws,
        task_id=task_id,
        dispatch_id=dispatch_id,
        native_task_name=name,
        outcome=terminal["outcome"],
        ended_at=terminal["observed_at"],
        usage_unavailable=unavailable,
        phase_runtime=True,
    )


def release_expired(runtime: Any, ws: str, result: dict[str, Any]) -> None:
    """Retire only the exact unbound slot; this is not native completion."""
    from taskplane import review_evidence

    slot = result["contract_slot"]
    path = runtime.tp.active_contract_path(ws, slot)
    with runtime.tp.file_lock(path):
        contract = runtime.tp.load_json(path, default=None, what="expired phase contract")
        if contract is None:
            return  # Authenticated release already quarantined it.
        original = review_evidence.ArtifactStore(ws).read(result["contract_reference"])
        lifecycle = contract.get("worker_lifecycle") or {}
        submission = "human-authorized-phase-retry:" + result["previous_operation"]
        if lifecycle.get("status") == "terminal":
            terminal = lifecycle.get("terminal") or {}
            if (
                terminal.get("authority") != "orphan-recovery"
                or terminal.get("submission_status") != submission
            ):
                raise ValueError("expired worker has a different terminal result")
        else:
            if (
                contract != original
                or lifecycle.get("status") != "pending"
                or lifecycle.get("owner") is not None
            ):
                raise ValueError(
                    "expired worker changed; reconcile its actual effects before retry"
                )
            terminal = runtime.tp.record_worker_terminal(
                ws,
                slot,
                event=None,
                outcome="interruption",
                submission_status=submission,
                authority="orphan-recovery",
            )
        runtime.tp.release_worker_contract(
            ws, slot, action=lifecycle["release_action"], terminal_receipt=terminal
        )


def resolve_retry(
    runtime: Any,
    ws: str,
    state: dict[str, Any],
    *,
    operation: str,
    candidate: str,
    by: str,
    worker_stopped: bool,
) -> dict[str, Any]:
    """One human-authorized successor attempt, retaining the failed evidence.

    Expired unbound workers require an explicit stop attestation. Active or
    effect-owning Build workers need reconciliation, not this recovery path.
    The journal grant precedes cleanup; a crash is replayable and next cannot
    dispatch while cleanup is incomplete. Routing still has its single owner.
    """
    from datetime import datetime
    from taskplane import review_evidence

    if runtime.tp.task_slot() is not None:
        raise ValueError("phase retry is orchestrator-only")
    root = state.get("_stage_native_root_authority") or {}
    if not by or by != root.get("actor") or not worker_stopped:
        raise ValueError(
            "phase retry requires the run's human --by and --worker-stopped attestation"
        )
    session = str(
        os.environ.get("TASKPLANE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()
    if not session or len(session.encode()) > 256 or any(ord(char) < 32 for char in session):
        raise ValueError("phase retry requires an attributable current host session")
    if not re.fullmatch(r"[a-f0-9]{64}", candidate or ""):
        raise ValueError("phase retry requires the exact --candidate-fingerprint")
    context = runtime._phase_bridge_context(ws, state)
    if context is None or context["stage"]["stage_kind"] == "build":
        raise ValueError("phase retry requires a non-Build phase with no active effect owner")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    request = {
        "run_id": context["run_id"],
        "stage_fingerprint": context["stage"]["fingerprint"],
        "previous_operation": operation,
        "candidate_fingerprint": candidate,
        "actor": by,
        "worker_stopped": True,
        "additional_attempts": 1,
    }
    fingerprint = review_evidence.content_fingerprint(request)
    grant_id = operation + "-retry"
    rows = phase_records.phase_records(context["manifest"])
    prior = rows.get(grant_id)
    if prior is not None:
        original_request = {key: prior["result"].get(key) for key in request}
        if prior["operation"] != "phase_retry" or any(
            original_request[key] != value
            for key, value in request.items()
            if key != "candidate_fingerprint"
        ):
            raise ValueError("phase retry approval replay changed")
        config = context["configuration"]
        if candidate != config["candidate_fingerprint"]:
            if prior["result"]["next_operation"] in rows:
                raise ValueError("phase retry approval replay changed after preparation")
            # The existing routing owner may select a corrected candidate
            # before this ONE granted attempt is prepared. Never mint another
            # grant, nonce, deadline, or stage attempt for that selection.
            phase_records.change_phase_routing(
                context["store"],
                context["run_id"],
                owner="agent-runtime",
                configuration=dict(config, candidate_fingerprint=candidate),
                expected_previous=context["route"]["result_fingerprint"],
                expected_revision=context["manifest"]["revision"],
                operation_id=grant_id + "-candidate-" + candidate[:32],
                validate_authority=lambda fresh: runtime._phase_bridge_authorize(
                    ws, context, fresh
                ),
            )
        runtime._phase_retry_release(ws, prior["result"])
        return {
            "resolved": "retry",
            "run_id": context["run_id"],
            "phase_retry": prior,
            "replay": True,
        }
    if operation != operation_id(context):
        raise ValueError("phase retry must name the exact current --phase-operation")
    prepared = rows.get(operation)
    if (
        prepared is None
        or prepared["operation"] != "phase_prepare"
        or operation + "-complete" in rows
    ):
        raise ValueError("phase retry requires an uncollected prepared attempt")
    material = context["artifacts"].read(prepared["result"]["reference"])
    if prepared["request_fingerprint"] != review_evidence.content_fingerprint(material):
        raise ValueError("phase preparation changed")
    deadline = datetime.fromisoformat(material["bindings"]["deadline"])
    if deadline.tzinfo is None or time.time() < deadline.timestamp():
        raise ValueError("phase retry requires an expired attempt")
    slot = material["contract_slot"]
    contract = runtime.tp.load_json(
        runtime.tp.active_contract_path(ws, slot), what="expired phase contract"
    )
    lifecycle = contract.get("worker_lifecycle") or {}
    if (
        lifecycle.get("status") != "pending"
        or lifecycle.get("owner") is not None
        or (contract.get("phase_runtime") or {}).get("operation_id") != operation
    ):
        raise ValueError("phase worker is not unbound; reconcile its actual terminal/effects first")
    # Preserve the exact pre-recovery object, not just its terminal projection.
    snapshot = context["artifacts"].put("phase-retry-contract", contract)
    config = dict(context["configuration"], candidate_fingerprint=candidate)
    route = phase_records.change_phase_routing(
        context["store"],
        context["run_id"],
        owner="agent-runtime",
        configuration=config,
        expected_previous=context["route"]["result"]["previous"]
        if context["route"]["operation_id"] == grant_id + "-routing"
        else context["route"]["result_fingerprint"],
        expected_revision=context["manifest"]["revision"],
        operation_id=grant_id + "-routing",
        validate_authority=lambda fresh: runtime._phase_bridge_authorize(ws, context, fresh),
    )
    result = {
        "schema": "taskplane.phase-retry/v1",
        **request,
        "next_operation": "phase-attempt-" + fingerprint[:32],
        "previous_preparation": prepared["result"]["reference"],
        "contract_slot": slot,
        "contract_reference": snapshot,
        "routing": route["result_fingerprint"],
        "old_result": "uncollected-not-passed",
        "approved_in_session": session,
    }
    current = context["store"].load(context["run_id"])
    receipt = phase_records.commit_phase_record(
        context["store"],
        context["run_id"],
        expected_revision=current["revision"],
        operation_id=grant_id,
        operation="phase_retry",
        request_fingerprint=fingerprint,
        result=result,
        validate_authority=lambda fresh: runtime._phase_bridge_authorize(ws, context, fresh),
    )
    runtime._phase_retry_release(ws, result)
    return {
        "resolved": "retry",
        "run_id": context["run_id"],
        "phase_retry": receipt,
        "replay": False,
    }


def pending(runtime: Any, ws: str, state: Mapping[str, Any]) -> dict[str, Any] | None:
    # Even after rollback, a prepared current stage remains pinned to v2.
    from taskplane import phase_amendment

    amended = phase_amendment.current(runtime, ws, dict(state), verify_candidate=False)
    if amended is not None:
        phase_amendment.require_cleanup(runtime, ws, amended)
    context = runtime._stage_loop_context(ws, state)
    if context is None or not isinstance(context.get("stage"), dict):
        return None
    rows = phase_records.phase_records(context["manifest"])
    retries = retry_chain(context)
    if retries and os.path.exists(
        runtime.tp.active_contract_path(ws, retries[-1]["result"]["contract_slot"])
    ):
        raise ValueError(
            "phase retry cleanup incomplete; replay the exact authorized resolve command"
        )
    row = rows.get(operation_id(context))
    if row is None:
        return None
    from taskplane import review_evidence

    material = review_evidence.ArtifactStore(ws).read(row["result"]["reference"])
    if not {"signing_scope", "freshness", "impact_reference"} <= set(material):
        raise ValueError(
            "persisted phase attempt has no compatible runtime-signing admission; retain its original identity"
        )
    if material["stage_fingerprint"] != context["stage"]["fingerprint"] or row[
        "request_fingerprint"
    ] != review_evidence.content_fingerprint(material):
        raise ValueError("phase preparation changed")
    completed = rows.get(operation_id(context) + "-complete")
    if completed is None:
        nonce = runtime.design_host_transport.phase_nonce_source(
            runtime.tp, ws, context["run_id"], existing_only=True
        )
        issued = nonce.recover(material["nonce_bindings"])
        hooks = nonce.terminal_hooks(issued, material["nonce_bindings"])
        if hooks is not None:
            terminal = hooks[1]
            measured = usage_evidence(ws, material, terminal)
            limit = material["bindings"]["budget"]["tokens"]
            advisory = resource_policy(context["manifest"], context["run_id"]) is not None
            reason = (
                "terminal_not_successful"
                if terminal["outcome"] != "success"
                else "phase_collection_requires_reconciliation"
                if advisory
                else "phase_usage_unavailable"
                if measured["tokens"] is None
                else "phase_token_budget_exhausted"
                if measured["tokens"] >= limit
                else "phase_collection_requires_reconciliation"
            )
            return {
                "step": state.get("step"),
                "paused": True,
                "error": "host terminal is recorded; " + reason,
                "phase_runtime": {
                    "status": "recovery_required",
                    "reason_code": reason,
                    "operation_id": operation_id(context),
                    "reference": row["result"]["reference"],
                    "completion": None,
                    "terminal_claim": terminal["claim"],
                    "usage": {**measured, "token_limit": limit, "advisory": advisory},
                },
                "awaiting": "reconcile the existing terminal; no new worker is authorized",
                "dispatch_allowed": False,
            }
        from datetime import datetime

        deadline = datetime.fromisoformat(
            str(material["bindings"]["deadline"]).replace("Z", "+00:00")
        )
        if deadline.tzinfo is None:
            raise ValueError("persisted phase deadline lacks a timezone")
        if (
            time.time() >= deadline.timestamp()
            and resource_policy(context["manifest"], context["run_id"]) is None
        ):
            # Waiting cannot make an expired attempt admissible. Preserve
            # its nonce, scope and evidence; do not silently launch again.
            return {
                "step": state.get("step"),
                "paused": True,
                "error": "phase attempt deadline exhausted; explicit recovery authority is required",
                "phase_runtime": {
                    "status": "recovery_required",
                    "reason_code": "phase_deadline_exhausted",
                    "operation_id": operation_id(context),
                    "reference": row["result"]["reference"],
                    "completion": None,
                    "deadline": material["bindings"]["deadline"],
                },
                "awaiting": "bounded recovery authorization for the existing run",
                "dispatch_allowed": False,
            }
    return {
        "step": state.get("step"),
        "paused": True,
        "phase_runtime": {
            "status": "collected" if completed else "pending",
            "operation_id": operation_id(context),
            "reference": row["result"]["reference"],
            "completion": None if completed is None else completed["result"],
        },
        "awaiting": "current gate" if completed else "matching host terminal and output collection",
        "wait_policy": runtime.event_wait_policy(operation_id(context), 1),
    }


def _phase_bridge_context(_ports: Any, ws: str, state: Mapping[str, Any]) -> dict[str, Any] | None:
    from taskplane import review_evidence

    context = _ports._stage_loop_context(ws, state)
    if context is None or not isinstance(context.get("stage"), dict):
        return None
    route = _ports.phase_records.phase_routing(context["manifest"])
    if route is None or route["result"]["owner"] != "agent-runtime":
        return None
    config = route["result"]["configuration"]
    if not isinstance(config, dict) or set(config) != {
        "definition_source",
        "definition_set_fingerprint",
        "knowledge_reference",
        "candidate_fingerprint",
        "target_revision",
        "host_kind",
        "host_version",
        "output_paths",
    }:
        raise ValueError("phase routing configuration is not closed")
    stage = context["stage"]
    if config["target_revision"] != stage["authority"]["target_revision"]:
        raise ValueError("phase routing candidate changed")
    registry, validators = _ports._phase_bridge_registry(config)
    definition = registry.admit(stage["stage_kind"], ()).to_dict()
    return {
        **context,
        "route": route,
        "configuration": config,
        "registry": registry,
        "validators": validators,
        "definition": definition,
        "artifacts": review_evidence.ArtifactStore(ws),
    }


def _phase_bridge_registry(_ports: Any, config: Mapping[str, Any]) -> Any:
    from taskplane import stage_entities

    root = _ports.os.path.dirname(_ports.os.path.dirname(_ports.os.path.abspath(_ports.__file__)))
    source = config["definition_source"]
    if source != "agents/spec-phase-definitions.json":
        raise ValueError("unregistered phase definition source")
    raw = _ports._stage_loop_read_output_no_follow(
        root, source, required=True, remaining_bytes=1024 * 1024
    )
    data = _ports.json.loads(raw)
    rows = data
    registered = {
        "taskplane.loop.validate_spec_phase_artifact": (
            "spec-phase/v1",
            _ports.validate_spec_phase_artifact,
        ),
        "taskplane.stage_entities.validate_stage": (
            "taskplane.stage/v1",
            stage_entities.validate_stage,
        ),
    }
    ids = {name for row in rows for name in row["domain_validator_refs"]}
    if not ids <= set(registered):
        raise ValueError("unregistered phase validator")
    inventory = {name: registered[name][0] for name in ids}
    schemas = {
        artifact["artifact_class"]: artifact["artifact_schema_version"]
        for row in rows
        for relation in ("consumes", "produces")
        for artifact in row[relation]
    }
    skills = {
        row["skill_ref"]: _ports._stage_loop_read_output_no_follow(
            root, row["skill_ref"], required=True, remaining_bytes=1024 * 1024
        )
        for row in rows
    }
    registry = _ports.operational_settings.load_phase_registry(
        rows, skills=skills, validator_inventory=inventory, artifact_schemas=schemas
    )
    if (
        config.get("definition_set_fingerprint") is not None
        and registry.definition_set_fingerprint != config["definition_set_fingerprint"]
    ):
        raise ValueError("phase definition set changed")
    return registry, {name: registered[name][1] for name in ids}


def _phase_bridge_authorize(
    _ports: Any, ws: str, context: dict[str, Any], current: Mapping[str, Any]
) -> None:
    authority = context["stage"]["authority"]
    lifecycle = context["lifecycle"]
    lifecycle.authority_validator(
        authority, _ports._current_stage_authority(ws, current, authority)
    )
    indexed = _ports._indexed_stage(
        context["store"], current, context["run_id"], context["stage"]["stage_id"]
    )
    if indexed != context["stage"]:
        raise ValueError("phase stage changed before effect")


def _phase_bridge_retries(_ports: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    return retry_chain(context)


def _phase_bridge_operation(_ports: Any, context: dict[str, Any]) -> str:
    return operation_id(context)


def _phase_retry_release(_ports: Any, ws: str, result: dict[str, Any]) -> None:
    _ports.phase_harness.release_expired(_ports, ws, result)


def _resolve_phase_retry(
    _ports: Any, ws: str, state: dict[str, Any], **request: Any
) -> dict[str, Any]:
    return resolve_retry(_ports, ws, state, **request)


def _phase_bridge_freshness(
    _ports: Any, ws: str, scopes: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the incumbent bounded impact producer, preserving its limits.

    This binds the exact impact output; it does not assert that partial,
    unknown or stale graph coverage is complete and launches no review.
    """
    from taskplane import review_evidence

    impact = _ports.depgraph.impact(ws, scopes, policy=_ports.depgraph.impact_policy({}))
    return {
        "candidate_sha": str(_ports.tp.git_head(ws) or ""),
        "source_tree": _ports.tp._run(["git", "rev-parse", "HEAD^{tree}"], cwd=ws).stdout.strip(),
        "impact_manifest_fingerprint": review_evidence.content_fingerprint(impact),
    }, impact


def _phase_bridge_signing(
    _ports: Any, ws: Any, material: Any, *, admit: Any = False, authorize: Any = None
) -> Any:
    from taskplane import review_evidence

    freshness, current_impact = _ports._phase_bridge_freshness(ws, material["signing_scope"])
    recorded_impact = review_evidence.ArtifactStore(ws).read(material["impact_reference"])
    recorded_fingerprint = review_evidence.content_fingerprint(recorded_impact)
    if recorded_fingerprint != material["freshness"]["impact_manifest_fingerprint"]:
        raise ValueError("runtime signing recorded impact changed")
    original = None
    build_collection = (
        material["bindings"]["phase_id"] == "build" and "original_preparation" in material
    )
    if "original_preparation" in material:
        original = review_evidence.ArtifactStore(ws).read(material["original_preparation"])

        def stable(row: Mapping[str, Any]) -> dict[str, Any]:
            return {
                key: value
                for key, value in row.items()
                if key not in {"original_preparation", "freshness", "impact_reference"}
            }

        if (
            stable(material) != stable(original)
            or recorded_fingerprint != material["freshness"]["impact_manifest_fingerprint"]
        ):
            raise ValueError("current validation changed original preparation")
        if any(
            freshness[key] != material["freshness"][key] for key in ("candidate_sha", "source_tree")
        ):
            raise ValueError("current validation source changed")
        if build_collection:
            manifest = _ports._stage_store(ws, material["bindings"]["run_id"]).load(
                material["bindings"]["run_id"]
            )
            if not _ports.stage_loop.authorized_run_revision(
                _ports,
                ws,
                manifest,
                original["freshness"]["candidate_sha"],
                freshness["candidate_sha"],
            ):
                raise ValueError("Build output changed outside its approved scope or ancestry")
        elif any(
            freshness[key] != original["freshness"][key] for key in ("candidate_sha", "source_tree")
        ):
            raise ValueError("current validation changed original source authority")
    # Impact is a phase input snapshot. The graph is a living asset: Product
    # adds requirement edges, Plan decomposes them, and Build realizes them.
    # Verify the saved snapshot, source authority and policy, without making
    # later graph revisions invalidate an earlier phase's signed evidence.
    if original is None and (
        {**freshness, "impact_manifest_fingerprint": recorded_fingerprint} != material["freshness"]
        or current_impact.get("policy") != recorded_impact.get("policy")
    ):
        raise ValueError("runtime signing current freshness changed")
    run_id = material["bindings"]["run_id"]
    policy = _ports.phase_harness.resource_policy(
        _ports._stage_store(ws, run_id).load(run_id), run_id
    )
    if original is not None and policy is None and not build_collection:
        raise ValueError("current validation requires the saved human collection policy")
    return _ports.design_host_transport.runtime_receipt_authority(
        _ports.tp,
        ws,
        bindings=material["bindings"],
        freshness=material["freshness"],
        now=int(_ports.SystemClock().wall_time()),
        admit=admit,
        authorize=authorize,
        collection_policy=(
            review_evidence.content_fingerprint(
                {
                    "kind": "approved-build-output",
                    "preparation": material["original_preparation"],
                    "freshness": material["freshness"],
                    "scope": material["signing_scope"],
                    **({"resource_policy": policy["fingerprint"]} if policy is not None else {}),
                }
            )
            if build_collection
            else None
            if policy is None
            else policy["fingerprint"]
            if original is None
            else review_evidence.content_fingerprint(
                {
                    "policy": policy["fingerprint"],
                    "validation": material["freshness"],
                    "preparation": material["original_preparation"],
                }
            )
        ),
        resource_policy_fingerprint=(
            policy["fingerprint"] if build_collection and policy is not None else None
        ),
        original_freshness=None if original is None else original["freshness"],
    )


def _phase_bridge_telemetry(
    _ports: Any, ws: str, completion: Mapping[str, Any], *, terminal_snapshot: Any = None
) -> Any:
    """Assemble existing authentic owners' inputs; no missing-fact repair.

    Knowledge proposals require the incumbent knowledge owner's signed apply
    receipts and purpose-specific trust. The runtime signer cannot supply
    that authority; until supplied such proposals remain a precise gap.
    """
    import math

    # These ports exchange typed signing keys. Keep their canonical package
    # identity even when tp.py loads the legacy loop module as a flat script.
    from taskplane import review_evidence, dispatch_telemetry

    artifacts = review_evidence.ArtifactStore(ws)
    material = artifacts.read(
        (completion.get("validation") or {}).get("signing_material", completion["preparation"])
    )
    policy = _ports._phase_bridge_signing(ws, material)
    signed = artifacts.read(completion["runtime_receipt"])
    result = policy.verify(signed, store=artifacts)["payload"]
    if artifacts.read(completion["runtime_result"]) != result:
        raise ValueError("telemetry accepted output differs from signed runtime")
    if (
        review_evidence.content_fingerprint(artifacts.read(material["impact_reference"]))
        != policy.freshness["impact_manifest_fingerprint"]
    ):
        raise ValueError("telemetry impact producer output changed")
    knowledge = review_evidence.canonical_bytes(artifacts.read(material["knowledge_reference"]))
    if _ports.hashlib.sha256(knowledge).hexdigest() != result["knowledge_fingerprint"]:
        raise ValueError("telemetry consumed knowledge changed")
    for output in result["collected_output_references"]:
        artifacts.read(output)
    source = _ports.design_host_transport.phase_nonce_source(_ports.tp, ws, str(result["run_id"]))
    nonce = source.recover(material["nonce_bindings"])
    start = source._read_phase_hook(nonce, material["nonce_bindings"], "start")
    if (completion.get("validation") or {}).get("completion_source") == "codex-task-complete":
        from taskplane import codex_identity

        terminal = codex_identity.completed_child(ws, start)
    else:
        _, terminal = source.phase_hooks(nonce, material["nonce_bindings"])
    if (
        start["claim"] != result["start_identity"]
        or terminal["claim"] != result["terminal_identity"]
        or terminal["outcome"] not in {"success", "complete"}
    ):
        raise ValueError("telemetry accepted output or terminal hook is severed")
    state = _ports.load(ws)
    ledger = dispatch_telemetry.validate_ledger((state or {}).get("dispatch_telemetry") or {})
    if terminal_snapshot is not None:
        snapshot = dispatch_telemetry.validate_ledger(artifacts.read(terminal_snapshot))
        # Preserve every prior producer row; only later attempts may append.
        for field in (
            "schema",
            "run_id",
            "source_sha",
            "design_fingerprint",
            "plan_fingerprint",
            "started_at",
        ):
            if ledger[field] != snapshot[field]:
                raise ValueError("Retro terminal ledger identity changed")
        for field in ("bindings", "dispatches", "usage_baselines"):
            if any(row not in ledger[field] for row in snapshot[field]):
                raise ValueError("Retro terminal ledger evidence changed")
        ledger = snapshot
    binding = next(
        (row for row in ledger["bindings"] if row["dispatch_id"] == result["attempt_id"]), None
    )
    if binding is None or binding["thread_id"] != start["owner"]["task_name"]:
        raise ValueError("telemetry native ledger attempt missing or foreign")
    if result["knowledge_proposals"]:
        raise ValueError(
            "telemetry requires incumbent knowledge apply receipts and knowledge signing authority"
        )
    inputs = dispatch_telemetry.AttemptTelemetryInputs(
        ledger=ledger,
        runtime_receipt=signed,
        nonce_source=source,
        nonce=nonce,
        nonce_bindings=material["nonce_bindings"],
        knowledge_proposals=(),
        knowledge_receipts=(),
        trusted_keys=policy.keys,
        freshness=policy.freshness,
        # Signature verification uses integer seconds; telemetry events retain
        # the host's fractional clock. Round the verification boundary up, not
        # the authentic event down (expiry may conservatively refuse early).
        now=math.ceil(_ports.SystemClock().wall_time()),
        event_deliveries=tuple(binding["events"]),
        resource_limits_advisory=_ports.phase_harness.resource_policy(
            _ports._stage_store(ws, result["run_id"]).load(result["run_id"]), result["run_id"]
        )
        is not None,
    )
    telemetry = dispatch_telemetry.produce_attempt_telemetry(inputs)
    return inputs, artifacts.put("attempt-telemetry", telemetry)


def _phase_bridge_retro_inputs(_ports: Any, ws: Any, context: Any, domain: Any = None) -> Any:
    """Connect the exact predecessor and incumbent terminal seals to Retro.

    A snapshot preserves pre-Retro accounting without hiding later ledger
    changes. This adapter never fills missing usage, evaluator or knowledge
    evidence and never grants publication authority.
    """
    artifacts = context["artifacts"]
    rows = _ports.phase_records.phase_records(context["store"].load(context["run_id"]))
    matches = [
        row["result"]
        for row in rows.values()
        if row["operation"] == "phase_collect"
        and row["result"].get("handoff", {}).get("fingerprint")
        == context["stage"]["input_manifest_ref"].get("fingerprint")
    ]
    if len(matches) != 1:
        raise ValueError(
            "Retro requires signed runtime and sealed terminal telemetry from its exact predecessor"
        )
    completion = matches[0]
    if domain is None:
        state = _ports.load(ws) or {}
        if not isinstance(state.get("wave_metrics_evidence"), dict) or not isinstance(
            state.get("wave_metrics_receipt"), dict
        ):
            raise ValueError(
                "Retro requires sealed terminal telemetry from the incumbent terminal producer"
            )
        snapshot = state.get("wave_metrics_ledger")
        if not isinstance(snapshot, dict):
            raise ValueError("Retro requires the terminal producer's immutable ledger snapshot")
        inputs, telemetry_ref = _ports._phase_bridge_telemetry(
            ws, completion, terminal_snapshot=snapshot
        )
        domain = {
            "predecessor_completion": completion,
            "terminal_snapshot": artifacts.put("terminal-ledger", inputs.ledger),
            "telemetry_ref": telemetry_ref,
            "terminal_evidence_ref": artifacts.put(
                "terminal-evidence", state["wave_metrics_evidence"]
            ),
            "terminal_metrics_ref": artifacts.put(
                "terminal-metrics", state["wave_metrics_receipt"]
            ),
        }
    else:
        if domain["predecessor_completion"] != completion:
            raise ValueError("Retro predecessor completion changed")
        inputs, telemetry_ref = _ports._phase_bridge_telemetry(
            ws, completion, terminal_snapshot=domain["terminal_snapshot"]
        )
        if telemetry_ref != domain["telemetry_ref"]:
            raise ValueError("Retro predecessor telemetry changed")
        # Authentic later usage invalidates the mutable latest-wave cache.
        # The prepared domain retains the original producer seals, bound into
        # Retro's package. Verify those against their immutable ledger below;
        # _phase_bridge_telemetry also requires every predecessor row to remain
        # byte-identical in the current ledger. Never substitute a newer seal.
    kwargs = {
        key: domain[key]
        for key in ("telemetry_ref", "terminal_evidence_ref", "terminal_metrics_ref")
    }
    kwargs["telemetry_inputs"] = inputs
    _ports.retro_engine._phase_telemetry(artifacts, **kwargs)
    return domain, kwargs


def collect_phase_runtime_telemetry(_ports: Any, ws: Any, contract: Any) -> Any:
    requested = contract["phase_runtime"]
    store = _ports._stage_store(ws, requested["run_id"])
    rows = _ports.phase_records.phase_records(store.load(requested["run_id"]))
    completion = rows[requested["operation_id"] + "-complete"]["result"]
    _, reference = _ports._phase_bridge_telemetry(ws, completion)
    return reference


def _phase_bridge_runtime(
    _ports: Any, ws: str, context: dict[str, Any], material: Mapping[str, Any]
) -> Any:
    from taskplane import review_evidence, delivery_ports, producer_observation

    nonce = _ports.design_host_transport.phase_nonce_source(_ports.tp, ws, context["run_id"])
    binding = material["nonce_bindings"]
    issued = nonce.recover(binding)
    knowledge = review_evidence.canonical_bytes(
        context["artifacts"].read(material["knowledge_reference"])
    )
    package = tuple(
        _ports.agent_runtime.Artifact(
            row["artifact_class"], row["artifact_schema_version"], row["reference"]
        )
        for row in material["package"]
    )
    dispatch = _ports.agent_runtime.Dispatch(
        material["bindings"], package, knowledge, issued, binding, material["envelope"]
    )

    def usage() -> Any:
        # Before reservation no worker has consumed anything. After dispatch,
        # only this attempt's authenticated terminal usage can fill the count.
        tokens = 0 if nonce.effect_state(binding) == "issued" else None
        try:
            _, terminal = nonce.phase_hooks(issued, binding)
            tokens = _ports.phase_harness.usage_evidence(ws, material, terminal)["tokens"]
        # The nonce owner is package-imported even when the CLI loads loop flat.
        except producer_observation.ProducerObservationError:
            pass
        return {
            "tokens": tokens,
            "wall_ms": max(0, int((nonce.clock.wall_time() - material["prepared_at"]) * 1000)),
            "attempts": 0,
            "corrections": 0,
        }

    def unavailable(*args: Any) -> None:
        raise ValueError("external host dispatch requires matching hook observation")

    runtime = _ports.agent_runtime.AgentRuntime(
        context["registry"],
        context["artifacts"],
        nonce,
        delivery_ports.SystemClock(),
        unavailable,
        unavailable,
        context["validators"],
        usage,
        lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": binding["phase_id"]},
        resource_limits_advisory=_ports.phase_harness.resource_policy(
            context["store"].load(context["run_id"]), context["run_id"]
        )
        is not None,
    )
    return runtime, dispatch


def _phase_bridge_build_owner(
    _ports: Any, ws: Any, context: Any, runtime: Any, material: Any
) -> Any:
    from taskplane import delivery_ports

    raw = material["domain"]["lease"]
    lease = delivery_ports.AttemptLease(**{**raw, "effect_scope": tuple(raw["effect_scope"])})

    def authorized(bound: Any) -> Any:
        if bound != lease:
            return False
        _ports._phase_bridge_authorize(ws, context, context["store"].load(context["run_id"]))
        return True

    limits = {
        key: value for key, value in material["bindings"]["budget"].items() if key != "corrections"
    }
    owner = _ports.loop_recovery.LeaseRecovery(
        ws,
        mutate_state=_ports.mutate,
        clock=runtime.clock,
        authorize=authorized,
        usage=lambda: {key: runtime.usage()[key] for key in limits},
        limits=limits,
        stage_id=context["stage"]["stage_id"],
    )
    return owner, lease


def _phase_bridge_output_location(_ports: Any, ws: Any, path: Any) -> Any:
    """Resolve the current phase's declared relative output only."""
    if (
        not isinstance(path, str)
        or not path
        or _ports.os.path.isabs(path)
        or re.match(r"^[A-Za-z]:", path)
        or ".." in path.replace("\\", "/").split("/")
    ):
        raise ValueError("phase output path must be workspace-relative")
    return ws, path


def _phase_bridge_preparation_operation(
    _ports: Any, ws: Any, context: Any, source: Any, binding: Any
) -> Any:
    """Keep canceled, never-prepared Build issuance distinct from new work."""
    if context["stage"]["stage_kind"] != "build":
        return binding["operation_id"]
    receipts = source.unreserved_issuances(binding["operation_id"])
    if not receipts:
        return binding["operation_id"]
    current = context["store"].load(context["run_id"])
    _ports._phase_bridge_authorize(ws, context, current)
    if (
        any(
            row["operation"] == "phase_prepare"
            and row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]
            for row in phase_records.phase_records(current).values()
        )
        or ((_ports.load(ws) or {}).get("attempt_leases") or {}).get(context["stage"]["stage_id"])
        or _ports.tp._active_worker_contracts(ws)
    ):
        raise ValueError("prior preparation has an active preparation or effect owner")
    path = _ports.tp._dispatch_path(ws, "expected_dispatch.json")
    with _ports.tp._file_lock(path):
        queue = _ports.tp._load_queue_strict(path)
        for receipt in receipts:
            if any(
                receipt[key] != binding[key]
                for key in (
                    "run_id",
                    "phase_id",
                    "candidate_fingerprint",
                    "definition_set_fingerprint",
                    "phase_definition_fingerprint",
                    "knowledge_fingerprint",
                    "authority_fingerprint",
                    "host_kind",
                    "host_version",
                )
            ):
                raise ValueError("prior preparation has foreign phase bindings")
            matches = [row for row in queue if row.get("intent_id") == receipt["attempt_id"]]
            if (
                len(matches) != 1
                or matches[0].get("intent_run_id") != context["run_id"]
                or matches[0].get("cancelled") is not True
                or matches[0].get("matched") is not True
                or matches[0].get("cancellation_reason") != "worker-contract-activation-failed"
            ):
                raise ValueError(
                    "prior preparation lacks exact activation-failed dispatch cancellation"
                )
    return (
        binding["operation_id"]
        + "-"
        + _ports.hashlib.sha256(binding["attempt_id"].encode()).hexdigest()[:32]
    )


def _phase_predecessor(_ports: Any, context: Any) -> Any:
    stage = context["stage"]
    if context["definition"]["entry"]:
        return None
    parents = stage.get("parent_stage_ids") or []
    if not parents:
        return stage["input_manifest_ref"]
    if stage["stage_kind"] != "build" or len(parents) != 1:
        raise ValueError("phase split must name one Build parent")
    parent = _ports._indexed_stage(
        context["store"], context["manifest"], context["run_id"], parents[0]
    )
    if (
        parent["stage_kind"] != "build"
        or parent["outcome"] != "closed"
        or parent["authority"] != stage["authority"]
    ):
        raise ValueError("Build split parent authority changed")
    context["lifecycle"]._read_handoff(stage["input_manifest_ref"], producer=parent, consumer=stage)
    return parent["input_manifest_ref"]


def input_package(runtime: Any, context: Any) -> Any:
    """Consume only this phase's declared, immutable predecessor package."""
    predecessor = _phase_predecessor(runtime, context)
    if predecessor is None:
        return None
    authority = context["stage"]["authority"]
    return runtime.consume_phase_handoff(
        context["artifacts"],
        predecessor,
        registry=context["registry"],
        phase_id=context["stage"]["stage_kind"],
        expected_authority_revision=authority["authority_revision"],
        expected_authority_fingerprint=authority["authority_fingerprint"],
        expected_run_id=context["run_id"],
        expected_candidate_fingerprint=context["configuration"]["candidate_fingerprint"],
    )


def _phase_bridge_prepare(
    _ports: Any,
    ws: str,
    state: Mapping[str, Any],
    contract: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any] | None:
    from taskplane import review_evidence
    from datetime import datetime, timezone

    context = _ports._phase_bridge_context(ws, state)
    if context is None:
        return None
    config, stage, definition = context["configuration"], context["stage"], context["definition"]
    retro_domain = None
    if stage["stage_kind"] == "retro":
        retro_domain, _ = _ports._phase_bridge_retro_inputs(ws, context)
        envelope["terminal_evidence_fingerprints"] = [
            retro_domain[key]["fingerprint"]
            for key in ("telemetry_ref", "terminal_evidence_ref", "terminal_metrics_ref")
        ]
    if stage["stage_kind"] in {"evaluate", "engineering"}:
        envelope["evaluation_lens_set_fingerprint"] = review_evidence.content_fingerprint([])
    authority = stage["authority"]
    _ports._phase_bridge_authorize(ws, context, context["store"].load(context["run_id"]))
    producer_owned = {
        "plan": {"source-coverage", "decomposition", "seam-manifest"},
        "build": {"stage", "realized-conformance"},
        "evaluate": {"stage"},
        "engineering": {"stage"},
    }.get(stage["stage_kind"], set())
    producer_owned = producer_owned | {"lens-evidence"}
    worker_outputs = {row["artifact_class"] for row in definition["produces"]} - producer_owned
    paths = config["output_paths"].get(stage["stage_kind"], {} if not worker_outputs else None)
    if not isinstance(paths, dict) or set(paths) != worker_outputs:
        raise ValueError("phase output paths do not match declared outputs")
    # Validate declared outputs before mutating the live contract. Bind the
    # phase ceiling before activation so hooks enforce it during execution.
    ceiling = min(int(definition["budget"]["tokens"]), int(contract["budget"]["max_tokens"]))
    contract["budget"].update(
        max_tokens=ceiling,
        target_tokens=max(1, min(int(contract["budget"]["target_tokens"]), ceiling - 1)),
        token_usage_required=True,
    )
    predecessor = _phase_predecessor(_ports, context)
    consumed = input_package(_ports, context)
    package = () if consumed is None else consumed.artifacts
    knowledge = review_evidence.canonical_bytes(
        context["artifacts"].read(config["knowledge_reference"])
    )
    operation = _ports._phase_bridge_operation(context)
    source = _ports.design_host_transport.phase_nonce_source(_ports.tp, ws, context["run_id"])
    source.activate_key()
    now = source.clock.wall_time()
    # Canonicalize once to the microsecond resolution of the closed runtime
    # timestamp before binding the nonce's exact numeric deadline.
    deadline = datetime.fromtimestamp(
        now + definition["budget"]["wall_ms"] / 1000, timezone.utc
    ).timestamp()
    attempt = contract["worker_lifecycle"]["dispatch_intent_id"]
    binding = {
        "run_id": context["run_id"],
        "phase_id": stage["stage_kind"],
        "attempt_id": attempt,
        "operation_id": operation,
        "candidate_fingerprint": config["candidate_fingerprint"],
        "definition_set_fingerprint": context["registry"].definition_set_fingerprint,
        "phase_definition_fingerprint": definition["fingerprint"],
        "sealed_package_fingerprint": _ports.agent_runtime.package_fingerprint(
            package, knowledge, envelope
        ),
        "knowledge_fingerprint": _ports.hashlib.sha256(knowledge).hexdigest(),
        "authority_fingerprint": authority["authority_fingerprint"],
        "host_kind": config["host_kind"],
        "host_version": config["host_version"],
        "deadline": deadline,
    }
    operation = _ports._phase_bridge_preparation_operation(ws, context, source, binding)
    binding["operation_id"] = operation
    issued = source.issue(binding)
    bindings = {
        key: value
        for key, value in binding.items()
        if key not in {"host_kind", "host_version", "deadline"}
    }
    prior_lease = (
        ((_ports.load(ws) or {}).get("attempt_leases") or {}).get(stage["stage_id"]) or {}
    ).get("lease") or {}
    fence = int(prior_lease.get("fencing_token", 0)) + 1 if stage["stage_kind"] == "build" else 1
    bindings.update(
        skill_content_fingerprint=definition["skill_content_fingerprint"],
        validator_identities=definition["domain_validator_refs"],
        validator_inventory_fingerprint=definition["validator_inventory_fingerprint"],
        capability_set_fingerprint=context["registry"].capability_set_fingerprint,
        host_kind_version=config["host_kind"] + ":" + config["host_version"],
        nonce_digest=issued.receipt["nonce_digest"],
        lease_id=operation,
        fencing_token=fence,
        deadline=datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
        budget=definition["budget"],
    )
    for relation, name in (
        ("consumes", "consumed_artifact_schema_versions"),
        ("produces", "produced_artifact_schema_versions"),
    ):
        bindings[name] = [
            {key: row[key] for key in ("artifact_class", "artifact_schema_version")}
            for row in definition[relation]
        ]
    # Enforce the same pre-existing contract that the native host will bind.
    allowed = contract.get("write_allow") or contract["coding"]["scope_paths"]
    for path in paths.values():
        _ports._phase_bridge_output_location(ws, path)
        if not _ports.tp.writable_target(path, allowed, ws):
            raise ValueError("phase output path is outside the worker contract")
    material = {
        "bindings": bindings,
        "nonce_bindings": binding,
        "envelope": dict(envelope),
        "package": [artifact.projection() for artifact in package],
        "knowledge_reference": config["knowledge_reference"],
        "predecessor": predecessor,
        "stage_id": stage["stage_id"],
        "stage_fingerprint": stage["fingerprint"],
        "output_paths": paths,
        "prepared_at": now,
        "routing": context["route"]["result_fingerprint"],
        "contract_slot": contract["task_slot"],
    }
    material["signing_scope"] = sorted(
        contract["coding"]["scope_paths"] if stage["stage_kind"] == "build" else paths.values()
    )
    freshness, impact = _ports._phase_bridge_freshness(ws, material["signing_scope"])
    material["freshness"] = freshness
    material["impact_reference"] = context["artifacts"].put("phase-impact", impact)
    if _ports.phase_harness.resource_policy(context["manifest"], context["run_id"]) is None:
        _ports._phase_bridge_signing(
            ws,
            material,
            admit=True,
            authorize=lambda: _ports._phase_bridge_authorize(
                ws, context, context["store"].load(context["run_id"])
            ),
        )
    if stage["stage_kind"] == "build":
        scopes = contract["coding"]["scope_paths"]
        if not scopes:
            raise ValueError("Build requires its current effect scope")
        material["domain"] = {
            "lease": {
                "lease_id": operation,
                "run_id": context["run_id"],
                "phase_id": "build",
                "attempt_id": attempt,
                "operation_id": operation,
                "owner": contract["task_slot"],
                "issued_at": now,
                "expires_at": deadline,
                "heartbeat_deadline": deadline,
                "effect_scope": ["workspace:" + path for path in scopes],
                "fencing_token": fence,
            }
        }
    runtime, dispatch = _ports._phase_bridge_runtime(ws, context, material)
    if stage["stage_kind"] == "build":
        owner, lease = _ports._phase_bridge_build_owner(ws, context, runtime, material)
        owner.admit(lease, expected_fence=fence - 1)
        runtime.prepare(dispatch)
    elif stage["stage_kind"] in {"evaluate", "engineering"}:
        current_state = _ports.stage_loop.task_phase_state(_ports, ws, _ports.load(ws))
        engineering_source = None
        if stage["stage_kind"] == "engineering":
            kernel = (current_state.get("review_kernel_runs") or {}).get(
                _ports._review_kernel_binding_key("em", _ports._current_task(current_state))
            ) or {}
            if kernel.get("stage") != "review" or _ports.os.path.realpath(
                str(kernel.get("workspace") or "")
            ) != _ports.os.path.realpath(ws):
                raise ValueError("Engineering requires its own current ReviewKernel selection")
            engineering_source = _ports.review.engineering_phase_source(
                ws,
                kernel_run_id=str(kernel.get("run_id") or ""),
                candidate_sha=freshness["candidate_sha"],
            )
            selection_binding = {
                **freshness,
                "impact_manifest_fingerprint": review_evidence.content_fingerprint(
                    engineering_source["impact"]
                ),
                "task_id": "engineering-signoff",
                "requirement_id": current_state.get("requirement_id"),
                "design_fingerprint": current_state.get("design_fingerprint"),
                "plan_fingerprint": current_state.get("plan_fingerprint"),
                "settings_digest": envelope.get("settings_digest"),
            }
        else:
            route = current_state.get("evaluate_child_evidence")
            if not isinstance(route, _ports.Mapping) or route.get("run_id") != context["run_id"]:
                raise ValueError(
                    "phase evaluator requires the incumbent complete evidence selection"
                )
            original = route["binding"]
            selection_binding = {
                key: original[key]
                for key in (
                    "candidate_sha",
                    "source_tree",
                    "impact_manifest_fingerprint",
                    "task_id",
                    "requirement_id",
                    "design_fingerprint",
                    "plan_fingerprint",
                    "settings_digest",
                )
            }
        selection_binding.update(
            {key: bindings[key] for key in ("run_id", "phase_id", "candidate_fingerprint")}
        )
        selection = _ports.review.precommit_evaluator_selection(
            runtime, [dispatch], binding=selection_binding
        )
        material["domain"] = {"selection": selection}
        if stage["stage_kind"] == "evaluate":
            material["domain"]["evidence_binding"] = _ports._copy_json(original)
        if engineering_source is not None:
            material["domain"]["engineering_source"] = engineering_source
        _ports.review.prepare_evaluator_phase(runtime, dispatch, selection_ref=selection)
    elif stage["stage_kind"] == "retro":
        material["domain"] = retro_domain
        _, telemetry = _ports._phase_bridge_retro_inputs(ws, context, retro_domain)
        _ports.retro_engine.prepare_retro_phase(runtime, dispatch, **telemetry)
    else:
        runtime.prepare(dispatch)
    task_state = _ports.stage_loop.task_phase_state(_ports, ws, state)
    task = _ports._current_task(task_state)
    worker_input: dict[str, Any] = {
        "phase_runtime": {"outputs": paths, "package": material["package"]}
    }
    if stage["stage_kind"] == "design":
        baseline = _ports.depgraph.validate_design_decomposition_receipt(
            state.get("design_decomposition_receipt")
        )
        if baseline["graph_fingerprint"] != state.get("design_graph_fingerprint"):
            raise ValueError("Design baseline differs from its selected graph")
        worker_input["graph_baseline"] = review_evidence.portable_artifact_reference(
            context["artifacts"], context["artifacts"].put("graph-baseline", baseline)
        )
    if stage["stage_kind"] == "engineering":
        worker_input["accepted_evaluations"] = accepted_evaluations(_ports, context, task_state)
        retained = _ports._current_phase_contribution_package(
            ws, task_state, require_contributions=False
        )
        if retained is None:
            raise ValueError("Engineering requires its retained Plan graph package")
        plan_package, _ = retained
        conformance = _ports.seal_phase_build_conformance(plan_package.store, plan_package, ws)
        worker_input["graph_conformance"] = review_evidence.portable_artifact_reference(
            context["artifacts"], context["artifacts"].put("realized-conformance", conformance)
        )
    if stage["stage_kind"] in {"build", "evaluate"}:
        if not task or not task.get("id"):
            raise ValueError("task phase requires its exact approved task")
        worker_input["task"] = {
            key: task[key]
            for key in (
                "id",
                "scope",
                "tests",
                "deps",
                "criteria",
                "contracts",
                "design_edges",
                "new_modules",
                "impact_policy",
                "failure_routing",
                "failure_classification",
            )
            if key in task
        }
        worker_input["task"]["operation"] = (
            "fix" if task_state.get("step") == "fix" else stage["stage_kind"]
        )
    _ports.phase_harness.compile_brief(
        context,
        worker_input,
        _ports.reqs.get_requirement(ws, str(state["requirement_id"]))
        if definition["entry"]
        else next(
            context["artifacts"].read(item.reference)
            for item in package
            if item.artifact_class == "requirement"
        ),
        ws,
    )
    if "graph_baseline" in worker_input:
        worker_input["instruction"] += (
            " Read graph_baseline for the selected immutable source graph and decomposition. "
            "Cite its graph_fingerprint as graph.baseline_fingerprint; do not recover it from loop state."
        )
    if any(row["artifact_class"] == "lens-evidence" for row in definition["produces"]):
        worker_input["instruction"] += (
            " After authoring the draft, call tp stage prepare-lenses with this same startup "
            "request. Dispatch each returned brief once as an isolated tp-lens worker. "
            "Use one event wait for the outstanding set; never poll status or message workers "
            "for progress. On completion call tp stage collect-lenses with that startup; "
            "its report contains the full collection, including findings, notes and coverage. "
            "Do not fetch it again or prepare another plan for unchanged drafts. Changed drafts require a "
            "fresh candidate-bound plan; never reuse stale results. Empty dispatch lists "
            "start no workers. Consume every inherited lens-evidence collection by reference. "
            "Do not author a lens-evidence output or old Design/Plan lens receipt yourself."
        )
    material["worker_input_reference"] = _ports.phase_harness.store_input(context, worker_input)
    reference = context["artifacts"].put("phase-preparation", material)
    current = context["store"].load(context["run_id"])
    receipt = _ports.phase_records.commit_phase_record(
        context["store"],
        context["run_id"],
        expected_revision=current["revision"],
        operation_id=operation,
        operation="phase_prepare",
        request_fingerprint=review_evidence.content_fingerprint(material),
        validate_authority=lambda fresh: _ports._phase_bridge_authorize(ws, context, fresh),
        result={
            "reference": reference,
            "stage_id": stage["stage_id"],
            "stage_fingerprint": stage["fingerprint"],
            "routing": material["routing"],
        },
    )
    # Publish custody before the effect reservation. Any crash from this point
    # holds the original attempt; normal next never authorizes another launch.
    _ports._phase_bridge_authorize(ws, context, context["store"].load(context["run_id"]))
    if stage["stage_kind"] == "build":
        _ports.build_c.prepare_build_phase(runtime, dispatch, lease_owner=owner, lease=lease)
    if not runtime.resource_limits_advisory:
        _ports._phase_bridge_signing(ws, material).require_current()
    if stage["stage_kind"] == "retro":
        _ports._phase_bridge_retro_inputs(ws, context, material["domain"])
    source.reserve_dispatch(issued, binding)
    return {
        "schema": "taskplane.phase-preparation-reference/v1",
        "run_id": context["run_id"],
        "operation_id": operation,
        "receipt_fingerprint": receipt["result_fingerprint"],
        "reference": reference,
        "package": material["package"],
        "outputs": paths,
        "input": material["worker_input_reference"],
        "status": "pending",
        "native_identity_claimed": False,
    }


def _phase_bridge_pending(_ports: Any, ws: str, state: Mapping[str, Any]) -> dict[str, Any] | None:
    return pending(_ports, ws, state)


def _phase_bridge_attempt(_ports: Any, ws: str, contract: Mapping[str, Any]) -> Any:
    """Reconstruct the exact attempt independently of a host callback."""
    from taskplane import review_evidence

    requested = contract.get("phase_runtime")
    if requested is None:
        return None
    if not isinstance(requested, _ports.Mapping):
        raise ValueError("invalid phase runtime reference")
    run_id = requested["run_id"]
    store = _ports._stage_store(ws, run_id)
    manifest = store.load(run_id)
    operation = requested["operation_id"]
    rows = _ports.phase_records.phase_records(manifest)
    receipt = rows.get(operation)
    if (
        not isinstance(receipt, dict)
        or receipt["result_fingerprint"] != requested["receipt_fingerprint"]
        or receipt["result"]["reference"] != requested["reference"]
    ):
        raise ValueError("phase preparation receipt missing or foreign")
    artifacts = review_evidence.ArtifactStore(ws)
    material = artifacts.read(requested["reference"])
    if receipt["request_fingerprint"] != review_evidence.content_fingerprint(material) or material[
        "contract_slot"
    ] != contract.get("task_slot"):
        raise ValueError("phase preparation binding changed")
    stage = _ports._indexed_stage(store, manifest, run_id, material["stage_id"])
    if stage["fingerprint"] != material["stage_fingerprint"]:
        raise ValueError("stale phase attempt")
    route = next(
        (
            row
            for row in rows.values()
            if row["operation"] == "phase_routing"
            and row["result_fingerprint"] == material["routing"]
        ),
        None,
    )
    if route is None:
        raise ValueError("phase attempt routing receipt missing")
    registry, validators = _ports._phase_bridge_registry(route["result"]["configuration"])
    _, lifecycle = _ports._stage_lifecycle(ws, store, manifest, stage["authority"])
    context = {
        "store": store,
        "manifest": manifest,
        "run_id": run_id,
        "stage": stage,
        "lifecycle": lifecycle,
        "registry": registry,
        "validators": validators,
        "artifacts": artifacts,
    }
    definition = registry.admit(material["bindings"]["phase_id"], ()).to_dict()
    runtime, dispatch = _ports._phase_bridge_runtime(ws, context, material)
    return requested, material, context, definition, runtime, dispatch


def observe_phase_runtime_hook(
    _ports: Any, ws: str, contract: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Record actual host observations, then collect through the shared port."""
    attempt = _ports._phase_bridge_attempt(ws, contract)
    if attempt is None:
        return None
    requested, material, context, definition, runtime, dispatch = attempt
    operation = requested["operation_id"]
    source = runtime.nonce
    # Lifecycle is independent of candidate validity and serialization.
    observed = _ports.design_host_transport.observe_phase_hook(
        _ports.tp, ws, contract, event, nonce=source, bindings=dispatch.nonce_bindings
    )
    if observed["kind"] == "start":
        _ports.phase_harness.record_dispatch(_ports, ws, contract, material, observed)
        return {"status": "pending", "operation_id": operation, "observed_start": observed["claim"]}
    return reconcile(_ports, ws, _ports.load(ws), operation)


def _reconcile_build_effects(
    _ports: Any, ws: str, attempt: Any, start: dict[str, Any], terminal: dict[str, Any]
) -> Any:
    """Retire an authenticated stopped writer independently of output acceptance."""
    requested, material, context, _, runtime, _ = attempt
    from taskplane import delivery_ports

    owner, lease = _ports._phase_bridge_build_owner(ws, context, runtime, material)
    slot = material["contract_slot"]
    path = _ports.tp.active_contract_path(ws, slot)
    with _ports.tp.file_lock(path):
        contract = _ports.tp.load_json(path, default=None, what="Build terminal contract")
        if contract is None:
            contract = _ports.tp.released_worker_contract(ws, slot)
        lifecycle = contract["worker_lifecycle"]
        if (
            lease.owner != slot
            or lifecycle["dispatch_intent_id"] != lease.attempt_id
            or contract.get("phase_runtime") != requested
            or any(
                lifecycle["owner"]
                != {key: observed["owner"][key] for key in ("session_id", "agent_id", "task_name")}
                for observed in (start, terminal)
            )
            or set(lease.effect_scope)
            != {"workspace:" + item for item in contract["coding"]["scope_paths"]}
        ):
            raise ValueError("Build terminal differs from its bound effect owner")
        with _ports.mutate(ws) as current:
            owner._record(current, lease)
        submission = _ports.tp.stop_submission_decision(ws, contract, loop_state=_ports.load(ws))
        if submission.get("block"):
            raise ValueError("Build terminal submission: " + submission["status"])
        if any(
            other_slot != slot
            and _ports._scopes_overlap(
                contract["coding"]["scope_paths"], (other.get("coding") or {}).get("scope_paths")
            )
            for other_slot, other in _ports.tp._active_worker_contracts(ws)
        ):
            raise ValueError("Build effect observation overlaps another worker")
        if lifecycle["status"] != "released":
            receipt = lifecycle.get("terminal") or _ports.tp.record_worker_terminal(
                ws,
                slot,
                event=None,
                outcome=terminal["outcome"],
                submission_status="phase-terminal:" + terminal["claim"],
                authority="phase-observation",
            )
            _ports.tp.release_worker_contract(
                ws, slot, action=lifecycle["release_action"], terminal_receipt=receipt
            )
        _ports.tp.released_worker_contract(ws, slot)
    # Only the existing signed release proves the writer is gone. Current
    # scoped effects were observed above, not reported committed by a worker.
    lease_terminal = delivery_ports.observe_lease_terminal(
        lease,
        lambda bound: {
            "released": True,
            "effects": {scope: "observed" for scope in bound.effect_scope},
            "terminal_identity": terminal["claim"],
        },
    )
    owner.reconcile(lease, lease_terminal)
    return owner, lease, lease_terminal


def _collect_phase_attempt(
    _ports: Any, ws: Any, attempt: Any, *, completed_worker: Any = None
) -> Any:
    """Validate current candidates under saved scope; never replay host events."""
    from taskplane import review_evidence

    requested, material, context, definition, runtime, dispatch = attempt
    stage, store, artifacts = context["stage"], context["store"], context["artifacts"]
    run_id, registry, operation = context["run_id"], context["registry"], requested["operation_id"]
    _ports._phase_bridge_authorize(ws, context, store.load(run_id))
    runtime.nonce.validate(
        dispatch.issued,
        dispatch.nonce_bindings,
        enforce_deadline=not runtime.resource_limits_advisory,
    )
    start = runtime.nonce._read_phase_hook(dispatch.issued, dispatch.nonce_bindings, "start")
    if completed_worker is not None:
        from taskplane import codex_identity

        terminal = codex_identity.completed_child(ws, start)
        if terminal != completed_worker or stage["stage_kind"] not in {"product", "design", "plan"}:
            raise ValueError("current provider completion differs or is not a pre-build phase")
    else:
        _, terminal = runtime.nonce.phase_hooks(dispatch.issued, dispatch.nonce_bindings)
    prior = _ports.phase_records.phase_records(store.load(run_id)).get(operation + "-complete")
    if prior is not None:
        signed = artifacts.read(prior["result"]["runtime_receipt"])
        signing_material = artifacts.read(
            (prior["result"].get("validation") or {}).get(
                "signing_material", requested["reference"]
            )
        )
        _ports._phase_bridge_signing(ws, signing_material).verify(signed, store=artifacts)
        if signed["payload"]["terminal_identity"] != terminal["claim"]:
            raise ValueError("collected terminal changed")
        return {
            "status": "collected",
            "receipt": prior,
            "replay": True,
            "native_readiness_claimed": False,
        }
    if terminal["outcome"] not in {"success", "complete"}:
        return {
            "status": "pending",
            "operation_id": operation,
            "reason_code": "terminal_not_successful",
        }
    # Native Stop and signed effect release are facts even when a candidate
    # fails validation. Keeping them behind successful output collection traps
    # human recovery behind the very invalid artifact it needs to replace.
    build_effects = (
        _reconcile_build_effects(_ports, ws, attempt, start, terminal)
        if stage["stage_kind"] == "build"
        else None
    )
    authored = phase_candidates(_ports, ws, material)
    lens_plan = None
    if any(row["artifact_class"] == "lens-evidence" for row in definition["produces"]):
        lens_plan = prepare_lenses(
            _ports,
            {**context, "definition": definition},
            material,
            artifacts.read(material["worker_input_reference"]),
            authored,
        )
    package = None
    if stage["stage_kind"] in {"plan", "build"}:
        package = _ports.consume_phase_handoff(
            artifacts,
            material["predecessor"],
            registry=registry,
            phase_id=stage["stage_kind"],
            expected_authority_revision=stage["authority"]["authority_revision"],
            expected_authority_fingerprint=stage["authority"]["authority_fingerprint"],
            expected_run_id=run_id,
            expected_candidate_fingerprint=dispatch.bindings["candidate_fingerprint"],
        )
    if stage["stage_kind"] in {"build", "evaluate", "engineering"}:
        # The authenticated terminal precedes this read. Preserve the current
        # control-plane stage exactly; it is not a worker-authored success or
        # a fabricated stage terminal. Lease reconciliation still gates collection.
        authored["stage"] = stage
    if stage["stage_kind"] in {"evaluate", "engineering"}:
        selection = artifacts.read(material["domain"]["selection"])["binding"]
        judgment = authored["judgment"]
        if (
            judgment.get("task") != selection["task_id"]
            or judgment.get("requirement") != selection["requirement_id"]
        ):
            raise ValueError("review judgment belongs to another task or requirement")
        if stage["stage_kind"] == "evaluate":
            _ports.evaluation_output.validate_evaluator_value(
                judgment,
                expected_lenses=[],
                expected_evidence_binding=material["domain"]["evidence_binding"],
            )
        else:
            inputs = artifacts.read(material["worker_input_reference"])
            if judgment.get("accepted_evaluations") != inputs["accepted_evaluations"]:
                raise ValueError("Engineering judgment must consume every selected task evaluation")
    if stage["stage_kind"] != "plan":
        authored = _ports.produce_spec_phase_candidates(
            artifacts, definition, authored, package=package, state=_ports.load(ws), workspace=ws
        )
    if lens_plan is not None:
        authored["lens-evidence"] = lens_evidence(artifacts, {**material, "lens_plan": lens_plan})
    output_rows = [
        artifact.projection()
        for artifact in _ports.store_spec_phase_outputs(artifacts, definition, authored)
    ]
    observation = _ports.agent_runtime.Observation(
        start["claim"],
        (),
        terminal["claim"],
        "reconciled",
        tuple(
            _ports.agent_runtime.Artifact(
                row["artifact_class"], row["artifact_schema_version"], row["reference"]
            )
            for row in output_rows
        ),
    )
    retro_receipt = None
    if stage["stage_kind"] == "build":
        assert build_effects is not None
        owner, lease, lease_terminal = build_effects
        result = _ports.build_c.complete_build_phase(
            runtime, dispatch, observation, lease_owner=owner, lease=lease, terminal=lease_terminal
        )
    elif stage["stage_kind"] in {"evaluate", "engineering"}:
        result = _ports.review.complete_evaluator_phase(
            runtime, dispatch, observation, selection_ref=material["domain"]["selection"]
        )
    elif stage["stage_kind"] == "retro":
        _, telemetry = _ports._phase_bridge_retro_inputs(ws, context, material["domain"])
        retro_receipt = _ports.retro_engine.complete_retro_phase(
            runtime, dispatch, observation, **telemetry
        )
        result = _ports.retro_engine.read_retro_phase(artifacts, retro_receipt, **telemetry)[
            "runtime_result"
        ]
    else:
        result = runtime.complete(_ports.agent_runtime.PreparedDispatch(dispatch), observation)
    if result["status"] != "accepted":
        reference = artifacts.put("phase-collection-refusal", result)
        return {
            "status": "pending",
            "operation_id": operation,
            "reason_code": result["reason_code"],
            "reference": reference,
        }
    # Current host policy authenticates only the collected accepted envelope.
    # The original phase-result remains immutable evidence for compatibility.
    _ports._phase_bridge_authorize(ws, context, store.load(run_id))
    signing_material = material
    if completed_worker is not None or stage["stage_kind"] == "build":
        freshness, impact = _ports._phase_bridge_freshness(ws, material["signing_scope"])
        signing_material = {
            **material,
            "original_preparation": requested["reference"],
            "freshness": freshness,
            "impact_reference": artifacts.put("phase-impact", impact),
        }
    signed = _ports._phase_bridge_signing(
        ws,
        signing_material,
        admit=runtime.resource_limits_advisory or stage["stage_kind"] == "build",
        authorize=lambda: _ports._phase_bridge_authorize(ws, context, store.load(run_id)),
    ).sign(result, store=artifacts)
    authority = stage["authority"]
    handoff = _ports.produce_phase_handoff(
        artifacts,
        registry=registry,
        phase_result=result,
        dispatch=dispatch,
        predecessor=material["predecessor"],
        producer_stage_id=stage["stage_id"],
        requirement=stage["requirement"],
        design=stage["design"],
        authorization={
            "actor": authority["actor"],
            "session_id": authority["session_id"],
            "authorized_at": stage["created_at"],
            "operation_id": operation,
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema": "taskplane.consolidated-authorization/v1",
                "revision": authority["authority_revision"],
                "fingerprint": authority["authority_fingerprint"],
            },
        },
    )
    complete = {
        "runtime_result": artifacts.put("phase-result", result),
        "handoff": handoff,
        "runtime_receipt": artifacts.put("signed-phase-result", signed),
        "preparation": requested["reference"],
        "terminal_observation": terminal["claim"],
        "timing": {
            "stage_id": stage["stage_id"],
            "started_at": start["observed_at"],
            "completed_at": terminal["observed_at"],
        },
    }
    complete["validation"] = {
        "mode": "current-semantic",
        "validated_at": _ports.time.time(),
        "completion_source": terminal.get("source", "subagent-stop"),
        "historical_stop_recovered": False,
    }
    if signing_material is not material:
        complete["validation"]["signing_material"] = artifacts.put(
            "phase-current-validation", signing_material
        )
    complete["resource_usage"] = {
        **_ports.phase_harness.usage_evidence(ws, material, terminal),
        "limits": dict(material["bindings"]["budget"]),
        "advisory": runtime.resource_limits_advisory,
    }
    if retro_receipt is not None:
        complete["retro_receipt"] = retro_receipt
    current = store.load(run_id)
    _ports._phase_bridge_authorize(ws, context, current)

    def authorize_collection(fresh: Any) -> None:
        _ports._phase_bridge_authorize(ws, context, fresh)
        _ports._phase_bridge_signing(ws, signing_material).verify(signed, store=artifacts)

    committed = _ports.phase_records.commit_phase_record(
        store,
        run_id,
        expected_revision=current["revision"],
        operation_id=operation + "-complete",
        operation="phase_collect",
        request_fingerprint=review_evidence.content_fingerprint(complete),
        validate_authority=authorize_collection,
        result=complete,
    )
    return {"status": "collected", "receipt": committed, "native_readiness_claimed": False}


def _phase_bridge_gate_check(_ports: Any, ws: str, state: Mapping[str, Any]) -> None:
    """Require authentic collection before the existing substantive gate runs."""
    from taskplane import phase_amendment

    amendment = phase_amendment.current(_ports, ws, dict(state))
    if amendment is not None and amendment["phase"] == "design":
        return  # The separate human amendment receipt is the review basis.
    from taskplane import review_evidence, stage_entities, stage_handoff

    pending = _ports._phase_bridge_pending(ws, state)
    context = _ports._phase_bridge_context(ws, state)
    if context is None:
        raise ValueError("gate requires the current agent-runtime phase")
    if pending is None:
        raise ValueError("phase runtime has no dispatched and collected attempt")
    completion = pending["phase_runtime"]["completion"]
    if not isinstance(completion, _ports.Mapping):
        raise ValueError(
            "phase runtime requires matching terminal and collected output before gate"
        )
    artifacts = review_evidence.ArtifactStore(ws)
    result = stage_entities.validate_contract(artifacts.read(completion["runtime_result"]))
    material = artifacts.read(
        (completion.get("validation") or {}).get("signing_material", completion["preparation"])
    )
    signed = artifacts.read(completion["runtime_receipt"])
    if (
        _ports._phase_bridge_signing(ws, material).verify(signed, store=artifacts)["payload"]
        != result
    ):
        raise ValueError("signed runtime differs from accepted output")
    source = _ports.design_host_transport.phase_nonce_source(
        _ports.tp, ws, result["run_id"], existing_only=True
    )
    source.validate(
        source.recover(material["nonce_bindings"]),
        material["nonce_bindings"],
        enforce_deadline=not _ports.run_context.resource_limits_advisory(ws),
    )
    if (completion.get("validation") or {}).get("completion_source") == "codex-task-complete":
        from taskplane import codex_identity

        start = source._read_phase_hook(
            source.recover(material["nonce_bindings"]), material["nonce_bindings"], "start"
        )
        if codex_identity.completed_child(ws, start)["claim"] != result["terminal_identity"]:
            raise ValueError("provider completion changed since current validation")
    if result["status"] != "accepted" or not result["evaluator_dispatch_eligibility"]:
        raise ValueError("phase runtime result is not eligible for gate")
    current = _ports._stage_loop_context(ws, state)
    _ports._phase_bridge_authorize(ws, current, current["store"].load(current["run_id"]))
    stage_handoff.read_v2_manifest(
        artifacts,
        completion["handoff"],
        expected_authority_revision=current["stage"]["authority"]["authority_revision"],
        expected_authority_fingerprint=current["stage"]["authority"]["authority_fingerprint"],
    )


def _phase_bridge_retro_completion(_ports: Any, ws: Any, state: Any) -> Any:
    context = _ports._phase_bridge_context(ws, state)
    pending = _ports._phase_bridge_pending(ws, state)
    if context is None and pending is None:
        return None
    completion = ((pending or {}).get("phase_runtime") or {}).get("completion")
    if (
        context is None
        or context["stage"]["stage_kind"] != "retro"
        or not isinstance(completion, _ports.Mapping)
        or completion.get("retro_receipt") is None
    ):
        raise ValueError(
            "phase Retro requires its telemetry-gated completion receipt; legacy sealing is unavailable"
        )
    _ports._phase_bridge_gate_check(ws, state)
    material = context["artifacts"].read(completion["preparation"])
    _, kwargs = _ports._phase_bridge_retro_inputs(ws, context, material["domain"])
    accepted = _ports.retro_engine.read_retro_phase(
        context["artifacts"], completion["retro_receipt"], **kwargs
    )
    if accepted["runtime_result"] != context["artifacts"].read(completion["runtime_result"]):
        raise ValueError("Retro accepted output differs from telemetry-gated completion")
    _ports._phase_bridge_telemetry(ws, completion)  # Retro's own actual terminal usage
    return completion


def validate_spec_phase_artifact(_ports: Any, value: Mapping[str, Any]) -> dict[str, Any]:
    """Registered domain validation for the inactive specification adapters."""
    from taskplane import stage_entities

    if not isinstance(value, Mapping):
        raise ValueError("phase artifact must be an object")
    if set(value) & {
        "gate",
        "successor",
        "successors",
        "predecessors",
        "evaluation_lenses",
        "working_lenses",
        "dag_edges",
    }:
        raise ValueError("agent artifact contains authority fields")
    schema = value.get("schema")
    artifact_class = next(
        (name for name, version in _ports.stage_artifacts.SCHEMAS.items() if version == schema),
        None,
    )
    _ports.stage_artifacts.validate(artifact_class, value)
    if schema == _ports.test_strategy.SCHEMA:
        return cast(dict[str, Any], _ports.test_strategy.validate_strategy(value))
    if schema == "taskplane.stage/v1":
        return stage_entities.validate_stage(value)
    if schema == _ports.evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID:
        # Structural validation is shared by collect and consume. The phase
        # owner supplies the sealed candidate binding for evidence admission.
        return copy.deepcopy(dict(value))
    if schema == "taskplane.lens-evidence/v1":
        return copy.deepcopy(dict(value))
    if schema in {
        "taskplane.source-touchpoint-coverage/v1",
        "taskplane.dependency-decomposition/v1",
        "taskplane.cross-task-seam-manifest/v1",
        "taskplane.realized-seam-conformance/v1",
    }:
        from taskplane import graph_decomposition, review_evidence

        if schema == "taskplane.source-touchpoint-coverage/v1":
            return graph_decomposition.require_complete_source_coverage(dict(value))
        if value.get("fingerprint") != review_evidence.content_fingerprint(
            {key: item for key, item in value.items() if key != "fingerprint"}
        ):
            raise ValueError("dependency artifact fingerprint is stale")
        return copy.deepcopy(dict(value))
    if schema == "taskplane.requirement/v1":
        if not value.get("id") or not value.get("acceptance_criteria"):
            raise ValueError("requirement needs identity and acceptance criteria")
    elif schema == "taskplane.design/v1":
        if not value.get("requirement") or not _ports._dc.acceptance_test_map(value):
            raise ValueError("Design requires exact acceptance selectors")
        if not isinstance(value.get("test_strategy"), Mapping):
            raise ValueError("Design requires its selected strategy")
    elif schema == "taskplane.plan-task/v1":
        if "plan" in value and (not isinstance(value["plan"], Mapping)):
            raise ValueError("Plan output requires a plan object")
        tasks = value["plan"].get("tasks") if "plan" in value else [value.get("task")]
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("Plan output requires tasks")
        for task in tasks:
            if not isinstance(task, Mapping) or not task.get("test_strategy_authority_receipt"):
                raise ValueError("Plan output requires sealed Design quality authority")
            errors = _ports.tp.plan_test_command_errors(task.get("tests"))
            if errors:
                raise ValueError("; ".join(errors))
    else:
        raise ValueError("unsupported specification artifact schema")
    return copy.deepcopy(dict(value))


def store_spec_phase_outputs(
    _ports: Any,
    store: Any,
    definition: Mapping[str, Any],
    authored: Mapping[str, Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Store validated declared candidate documents; never advance the lifecycle."""
    from taskplane import agent_runtime

    declarations = {row["artifact_class"]: row for row in definition["produces"]}
    if set(authored) - set(declarations) or any(
        (row["required"] and key not in authored for key, row in declarations.items())
    ):
        raise ValueError("candidate outputs differ from the declared produces set")
    prepared = []
    for artifact_class, payload in authored.items():
        value = _ports.validate_spec_phase_artifact(payload)
        schema = declarations[artifact_class]["artifact_schema_version"]
        if value["schema"] != schema:
            raise ValueError("candidate output schema differs from definition")
        prepared.append((artifact_class, schema, value))
    return tuple(
        (
            agent_runtime.Artifact(name, schema, store.put(name, value))
            for name, schema, value in prepared
        )
    )
