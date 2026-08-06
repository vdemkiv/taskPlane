"""Design Contract validation (taskplane.design/v1) — extracted from
loop.py in v2.2.1 (review finding: ~470 embedded lines were turning the
state machine into a god module). loop.py delegates here; the public
surface and behavior are unchanged.

Decomposition note (M5): _design_dod_errors below remains the single
entry point; its sections are being split into per-concern helpers as
they change — new validation goes in a helper, not inline.
"""
from __future__ import annotations
import hashlib
import json
import os

import depgraph
import kb
import lens as lens_router
import requirements as reqs
import taskplane_lite as tp


def read_json(path: str) -> tuple[dict | None, list]:
    try:
        with open(path) as f:
            value = json.load(f)
    except FileNotFoundError:
        return None, [f"required evidence missing: {path}"]
    except (OSError, ValueError) as exc:
        return None, [f"invalid evidence {path}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"invalid evidence {path}: root must be an object"]
    return value, []


# --------------------------------------------------------------- design

DESIGN_SCHEMA = "taskplane.design/v1"
DESIGN_CONTRACT = os.path.join("design", "contract.json")
DESIGN_NARRATIVE = os.path.join("design", "design.md")


def edge_key(row) -> str:
    """Canonical proposed-edge identity (L8, v2.2.1) — one format string."""
    row = row if isinstance(row, dict) else {}
    return f"{row.get('from')}->{row.get('to')}:{row.get('kind')}"


def design_path(ws: str, rel: str) -> str:
    return os.path.join(ws, rel)


def design_contract(ws: str) -> tuple[dict | None, list]:
    return read_json(design_path(ws, DESIGN_CONTRACT))


def design_safe_rel(rel) -> str | None:
    rel = str(rel or "").replace("\\", "/").strip()
    if (not rel or os.path.isabs(rel) or rel == ".."
            or rel.startswith("../") or "/../" in rel
            or not rel.startswith("design/")):
        return None
    return rel


def design_evidence_paths(ws: str, contract: dict | None = None) -> list:
    paths = [DESIGN_CONTRACT, DESIGN_NARRATIVE]
    contract = contract or (design_contract(ws)[0] or {})
    visual = contract.get("visualization") or {}
    if visual.get("required"):
        rel = design_safe_rel(visual.get("path"))
        if rel:
            paths.append(rel)
    return paths


def design_evidence_fingerprint(ws: str,
                                 contract: dict | None = None) -> str:
    """Fingerprint exactly the approved design evidence, not source code."""
    h = hashlib.sha256()
    for rel in sorted(set(design_evidence_paths(ws, contract))):
        h.update(b"\0path\0")
        h.update(rel.encode("utf-8", errors="surrogateescape"))
        try:
            with open(design_path(ws, rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"\0MISSING\0")
    return h.hexdigest()


def design_current_errors(ws: str, state: dict) -> list:
    if not state.get("design_required") or not state.get("design_fingerprint"):
        return []
    contract, errors = design_contract(ws)
    if errors:
        return ["approved design is unavailable: " + e for e in errors]
    current = design_evidence_fingerprint(ws, contract)
    if current != state.get("design_fingerprint"):
        return ["approved design evidence changed after approval — return to "
                "Design and obtain a new human approval"]
    return []


def design_dor(ws: str, state: dict) -> dict:
    """Entry gate for the proposed-HOW phase."""
    blockers, warnings = [], []
    rid = state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if not rid:
        blockers.append("Design must be anchored to a requirement R-id")
    elif rec is None:
        blockers.append(f"Design requirement {rid} does not exist")
    else:
        if not rec.get("acceptance"):
            blockers.append("Design requirement has no acceptance criteria")
        if rec.get("open_questions"):
            blockers.append("Design requirement has unresolved questions: "
                            + "; ".join(rec["open_questions"]))
    graph = depgraph.load(ws)
    meta = graph.get("meta") or {}
    if not meta.get("content_fingerprint"):
        blockers.append("baseline dependency graph is missing — run graph scan")
    elif meta.get("scanned_head") and meta.get("scanned_head") != tp.git_head(ws):
        blockers.append("baseline dependency graph is stale for the current HEAD")
    if not graph.get("modules"):
        warnings.append("baseline graph has no source modules; treat this as "
                        "greenfield and declare every proposed module")
    if not kb.current_state(ws):
        warnings.append("current-state inventory is empty; ground the design "
                        "in cited repository sources and the baseline graph")
    return {"ready": not blockers, "blockers": blockers,
            "warnings": warnings}


def design_dod_errors(ws: str, state: dict) -> list:
    """Mechanical Design Contract completion and graph-isolation proof."""
    contract, errors = design_contract(ws)
    if errors:
        return errors
    assert contract is not None

    def text(value) -> bool:
        return bool(str(value or "").strip())

    def object_field(name: str) -> dict:
        value = contract.get(name)
        if not isinstance(value, dict):
            errors.append(f"design {name} must be an object")
            return {}
        return value

    def text_list(value) -> bool:
        return (isinstance(value, list) and bool(value)
                and all(text(item) for item in value))

    if contract.get("schema") != DESIGN_SCHEMA:
        errors.append(f"design schema must be {DESIGN_SCHEMA}")
    if contract.get("requirement") != state.get("requirement_id"):
        errors.append("design requirement does not match the loop requirement")
    for field in ("title", "summary", "decision"):
        if not text(contract.get(field)):
            errors.append(f"design {field} is missing")

    current = object_field("current_state")
    if not text(current.get("summary")) or not text_list(current.get("sources")):
        errors.append("design current_state needs a summary and cited sources")

    alternatives = contract.get("alternatives") or []
    if not isinstance(alternatives, list) or len(alternatives) < 2:
        errors.append("design must compare at least two approaches")
        alternatives = []
    alt_ids = set()
    for alt in alternatives:
        if not isinstance(alt, dict):
            errors.append("every design alternative must be an object")
            continue
        aid = str(alt.get("id") or "").strip()
        if not aid or aid in alt_ids:
            errors.append("design alternatives need unique non-empty ids")
        alt_ids.add(aid)
        trade = alt.get("tradeoffs")
        if not isinstance(trade, dict):
            trade = {}
        if (not text(alt.get("name")) or not text(alt.get("description"))
                or not text_list(trade.get("gains"))
                or not text_list(trade.get("costs"))
                or not text(trade.get("revisit_when"))):
            errors.append(f"alternative {aid or '?'} needs description, "
                          "gains, costs, and revisit_when")
    if contract.get("selected_approach") not in alt_ids:
        errors.append("selected_approach does not name a declared alternative")

    modules = object_field("modules")
    declared_modules = {str(x).strip() for x in
                        list(modules.get("existing") or [])
                        + list(modules.get("new") or []) if str(x).strip()}
    if not declared_modules:
        errors.append("design modules must name existing or new modules")

    contracts = contract.get("contracts") or []
    if not isinstance(contracts, list):
        errors.append("design contracts must be a list")
        contracts = []
    contract_ids = set()
    for row in contracts:
        if not isinstance(row, dict) or not text(row.get("id")) \
                or not text(row.get("relation")) \
                or not text(row.get("description")):
            errors.append("every design contract needs relation, id, and description")
            continue
        if row.get("relation") not in ("provides", "consumes", "changes"):
            errors.append("design contract relation must be provides, consumes, or changes")
        contract_ids.add(str(row["id"]))
    rec = reqs.get_requirement(ws, state.get("requirement_id"))
    required_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in ((rec or {}).get("contracts") or [])
        if str(row.get("id") if isinstance(row, dict) else row).strip()
    }
    missing_contracts = sorted(required_contracts - contract_ids)
    if missing_contracts:
        errors.append("design omits requirement contracts: "
                      + ", ".join(missing_contracts))

    graph = object_field("graph")
    current_fp = (depgraph.load(ws).get("meta") or {}).get(
        "content_fingerprint")
    baseline_fp = state.get("design_graph_fingerprint")
    if not baseline_fp:
        errors.append("design graph baseline was not captured by the "
                      "engine — run `loop next` once at the design step to "
                      "capture it, then gate again")
    if current_fp != baseline_fp:
        errors.append("as-built graph changed during Design; proposed edges "
                      "must remain an overlay")
    if graph.get("baseline_fingerprint") != baseline_fp:
        errors.append("design graph does not cite the captured baseline fingerprint")
    proposed_modules = {str(x).strip() for x in
                        (graph.get("proposed_modules") or []) if str(x).strip()}
    if not proposed_modules:
        errors.append("design graph has no proposed_modules")
    if not declared_modules <= proposed_modules:
        errors.append("design graph does not include every declared module")
    edges = graph.get("proposed_edges")
    if not isinstance(edges, list):
        errors.append("design graph proposed_edges must be a list")
        edges = []
    known = set((depgraph.load(ws).get("modules") or {})) | proposed_modules
    edge_nodes = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("every proposed graph edge must be an object")
            continue
        for end in ("from", "to"):
            node = str(edge.get(end) or "").strip()
            if not node:
                errors.append(f"proposed graph edge is missing {end}")
            elif node not in known and not node.startswith(
                    ("contract:", "resource:", "svc:", "ext:")):
                errors.append(f"proposed graph edge has undeclared node: {node}")
            else:
                edge_nodes.add(node)
        if not text(edge.get("kind")) or not text(edge.get("reason")):
            errors.append("proposed graph edges need kind and reason")
    if contract_ids - edge_nodes:
        errors.append("design contracts are missing from the proposed graph: "
                      + ", ".join(sorted(contract_ids - edge_nodes)))
    policy = graph.get("depth_policy")
    if not isinstance(policy, dict):
        errors.append("design graph depth_policy must be an object")
        policy = {}
    try:
        local_depth = int(policy.get("local_depth"))
        contract_depth = int(policy.get("contract_depth"))
        requirement_depth = int(policy.get("requirement_depth"))
    except (TypeError, ValueError):
        local_depth = contract_depth = requirement_depth = -1
    if not 1 <= local_depth <= 10:
        errors.append("design graph local_depth must be between 1 and 10")
    if policy.get("boundary_mode") not in ("stop", "contract-only", "expand"):
        errors.append("design graph boundary_mode is invalid")
    if contract_depth < 0 or requirement_depth < 0:
        errors.append("design graph contract/requirement depth must be non-negative")
    if policy.get("boundary_mode") == "contract-only" and contract_depth > 1:
        errors.append("contract-only design may traverse only one contract level")
    for field in ("dor", "dod"):
        rows = graph.get(field)
        if not isinstance(rows, list) or not rows:
            errors.append(f"design graph {field} checks are missing")
            continue
        for row in rows:
            if not isinstance(row, dict) or not text(row.get("check")) \
                    or not text(row.get("evidence")):
                errors.append(f"every graph {field} check needs check and evidence")

    criteria = list((rec or {}).get("acceptance") or [])
    mapping = contract.get("acceptance_map") or []
    if not isinstance(mapping, list):
        errors.append("design acceptance_map must be a list")
        mapping = []
    mapped = [row.get("criterion") for row in mapping
              if isinstance(row, dict)]
    for criterion in criteria:
        rows = [row for row in mapping if isinstance(row, dict)
                and row.get("criterion") == criterion]
        if len(rows) != 1 or not text(rows[0].get("design_element")) \
                or not text(rows[0].get("validation")):
            errors.append("acceptance criterion lacks one complete design mapping: "
                          + criterion)
    extras = sorted({str(x) for x in mapped if x not in criteria})
    if extras:
        errors.append("design maps unknown acceptance criteria: "
                      + ", ".join(extras))

    for field, required in (("risks", ("risk", "mitigation", "owner")),
                            ("failure_modes", ("mode", "detection", "recovery"))):
        rows = contract.get(field)
        if not isinstance(rows, list) or not rows:
            errors.append(f"design {field} evidence is missing")
            continue
        for row in rows:
            if not isinstance(row, dict) or any(not text(row.get(k))
                                                for k in required):
                errors.append(f"every {field} row needs " + ", ".join(required))
    observability = object_field("observability")
    if not text_list(observability.get("signals")):
        errors.append("design observability signals are missing")
    if not text_list(observability.get("alerts")):
        errors.append("design observability alerts or an explicit none rationale are missing")
    rollout = object_field("rollout")
    if not text(rollout.get("strategy")) or not text(rollout.get("rollback")):
        errors.append("design rollout needs strategy and rollback")

    visual = object_field("visualization")
    if not isinstance(visual.get("required"), bool):
        errors.append("design visualization.required must be boolean")
    elif visual.get("required"):
        rel = design_safe_rel(visual.get("path"))
        if visual.get("kind") not in (
                "dependency-graph", "sequence", "state-transition",
                "data-flow", "ui-flow") or not rel:
            errors.append("required design visualization needs kind and safe design/ path")
        elif not os.path.isfile(design_path(ws, rel)) \
                or os.path.getsize(design_path(ws, rel)) == 0:
            errors.append("required design visualization is missing or empty")
    elif not text(visual.get("reason")):
        errors.append("skipped design visualization needs a reason")

    lens_evidence = contract.get("lens_evidence") or []
    if not isinstance(lens_evidence, list):
        errors.append("design lens_evidence must be a list")
        lens_evidence = []
    evidence = [row for row in lens_evidence
                if isinstance(row, dict)
                and row.get("lens") == "solution-design"]
    try:
        solution_blockers = int(evidence[0].get("blockers") or 0) \
            if len(evidence) == 1 else -1
    except (TypeError, ValueError):
        solution_blockers = -1
    if (len(evidence) != 1 or evidence[0].get("verdict") != "pass"
            or solution_blockers != 0
            or not text(evidence[0].get("evidence"))):
        errors.append("solution-design lens must pass with evidence and no blockers")
    if not isinstance(contract.get("open_questions"), list):
        errors.append("design open_questions must be a list")
    elif contract.get("open_questions"):
        errors.append("design has unresolved open_questions")
    try:
        with open(design_path(ws, DESIGN_NARRATIVE), encoding="utf-8") as f:
            if not f.read().strip():
                errors.append("design narrative is empty")
    except OSError:
        errors.append("design narrative is missing: " + DESIGN_NARRATIVE)
    return errors


def design_plan_errors(ws: str, state: dict) -> list:
    """Approved Design Contract → implementation plan conformance."""
    errors = design_current_errors(ws, state)
    if errors or not state.get("design_required"):
        return errors
    contract, read_errors = design_contract(ws)
    if read_errors:
        return read_errors
    assert contract is not None
    tasks = state.get("tasks") or []
    planned_modules = set()
    planned_contracts = set()
    planned_edges = set()
    for task in tasks:
        planned_modules.update(depgraph.modules_for_scope(task.get("scope") or []))
        planned_modules.update(str(x) for x in (task.get("new_modules") or []))
        for row in task.get("contracts") or []:
            cid = row.get("id") if isinstance(row, dict) else row
            if str(cid or "").strip():
                planned_contracts.add(str(cid))
        for row in task.get("design_edges") or []:
            if isinstance(row, dict):
                planned_edges.add(
                    edge_key(row))
            elif str(row or "").strip():
                planned_edges.add(str(row))
    graph = contract.get("graph") or {}
    expected_modules = {str(x) for x in graph.get("proposed_modules") or []}
    missing_modules = sorted(expected_modules - planned_modules)
    if missing_modules:
        errors.append("approved design modules are not covered by the plan: "
                      + ", ".join(missing_modules))
    expected_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in contract.get("contracts") or []
    }
    missing_contracts = sorted(expected_contracts - planned_contracts)
    if missing_contracts:
        errors.append("approved design contracts are not covered by the plan: "
                      + ", ".join(missing_contracts))
    expected_edges = {
        edge_key(row)
        for row in graph.get("proposed_edges") or [] if isinstance(row, dict)
    }
    missing_edges = sorted(expected_edges - planned_edges)
    if missing_edges:
        errors.append("approved design edges are not covered by the plan: "
                      + ", ".join(missing_edges))
    planned_policy = depgraph.aggregate_impact_policy(tasks)
    expected_policy = graph.get("depth_policy") or {}
    ranks = {"stop": 0, "contract-only": 1, "expand": 2}
    if (planned_policy.get("local_depth", 0)
            < int(expected_policy.get("local_depth", 0) or 0)
            or planned_policy.get("contract_depth", 0)
            < int(expected_policy.get("contract_depth", 0) or 0)
            or planned_policy.get("requirement_depth", 0)
            < int(expected_policy.get("requirement_depth", 0) or 0)
            or ranks.get(planned_policy.get("boundary_mode"), 1)
            < ranks.get(expected_policy.get("boundary_mode"), 1)):
        errors.append("plan dependency depth policy is narrower than the "
                      "approved design depth policy")
    return errors


def design_review_errors(ws: str, state: dict, meta: dict) -> list:
    """Approved design → final as-built review evidence."""
    errors = design_current_errors(ws, state)
    if errors or not state.get("design_required") or state.get("design_only"):
        return errors
    evidence = meta.get("design")
    if not isinstance(evidence, dict):
        return ["engineering review is missing approved-design conformance evidence"]
    if evidence.get("fingerprint") != state.get("design_fingerprint"):
        errors.append("engineering review uses the wrong design fingerprint")
    if evidence.get("verdict") != "conformant":
        errors.append("engineering review reports design drift; return to Design "
                      "and re-plan before sign-off")
    for field in ("modules_checked", "edges_checked", "contracts_checked",
                  "drift"):
        if not isinstance(evidence.get(field), list):
            errors.append(f"engineering design evidence {field} must be a list")
    contract, _ = design_contract(ws)
    graph = (contract or {}).get("graph") or {}
    expected_modules = {str(x) for x in graph.get("proposed_modules") or []}
    as_built = depgraph.load(ws)
    actual_modules = set(as_built.get("modules") or {})
    unrealized_modules = expected_modules - actual_modules
    if unrealized_modules:
        errors.append("as-built graph is missing designed modules: "
                      + ", ".join(sorted(unrealized_modules)))
    checked_modules = {str(x) for x in evidence.get("modules_checked") or []} \
        if isinstance(evidence.get("modules_checked"), list) else set()
    if expected_modules - checked_modules:
        errors.append("engineering review did not check every designed module: "
                      + ", ".join(sorted(expected_modules - checked_modules)))
    expected_edges = {
        edge_key(row)
        for row in graph.get("proposed_edges") or [] if isinstance(row, dict)
    }
    actual_edges = {
        edge_key(row)
        for row in as_built.get("edges") or [] if isinstance(row, dict)
    }
    unrealized_edges = expected_edges - actual_edges
    if unrealized_edges:
        errors.append("as-built graph is missing designed edges: "
                      + ", ".join(sorted(unrealized_edges)))
    checked_edges = {str(x) for x in evidence.get("edges_checked") or []} \
        if isinstance(evidence.get("edges_checked"), list) else set()
    if expected_edges - checked_edges:
        errors.append("engineering review did not check every designed edge: "
                      + ", ".join(sorted(expected_edges - checked_edges)))
    expected_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in (contract or {}).get("contracts") or []
    }
    unrealized_contracts = expected_contracts - actual_modules
    if unrealized_contracts:
        errors.append("as-built graph is missing designed contracts: "
                      + ", ".join(sorted(unrealized_contracts)))
    checked_contracts = {str(x) for x in evidence.get("contracts_checked") or []} \
        if isinstance(evidence.get("contracts_checked"), list) else set()
    if expected_contracts - checked_contracts:
        errors.append("engineering review did not check every designed contract: "
                      + ", ".join(sorted(expected_contracts - checked_contracts)))
    if isinstance(evidence.get("drift"), list) and evidence.get("drift"):
        errors.append("engineering review contains unexplained design drift")
    return errors


