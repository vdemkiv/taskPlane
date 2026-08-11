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
        with open(path, encoding="utf-8") as f:
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

# ONE contract-id prefix rule (v2.3.0 L1). This is the STRICTER of the two
# rules that used to diverge: plan readiness (depgraph.py) accepts only
# these prefixes on task contract ids, so Design DoD enforces the same rule
# — a design approvable here is always plannable there. depgraph's plan DoR
# should consume this constant when it is next touched.
CONTRACT_ID_PREFIXES = ("contract:", "resource:")

# Boundary nodes the import scanner can never produce (mirrors
# depgraph._is_boundary). Edges touching one of these enter the as-built
# graph only by hand-recording — presence alone is not realization proof.
BOUNDARY_NODE_PREFIXES = ("contract:", "resource:", "svc:", "ext:")


def _text(value) -> bool:
    return bool(str(value or "").strip())


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


def _primary_workspace(ws: str) -> str | None:
    """The orchestrator workspace a wave agent worktree belongs to, or None.

    The requirement store is a LOOP-level resource keyed by workspace path.
    A parallel wave evaluates task DoD inside `.tp-work/<task>` worktrees,
    whose paths key a DIFFERENT (empty) store — so requirement_fingerprint
    read \0MISSING\0 there and the design approval looked tampered-with in
    every agent worktree (found live by the v3 Phase-1 dogfood loop).

    Resolution uses the engine's OWN layout convention — wave worktrees are
    always created at `<primary>/.tp-work/<task>` (see loop wave/claim) — not
    git common-dir, which over-resolves when the primary workspace is itself
    a linked worktree of some outer repo. Fails toward None (current
    behavior: hash records \0MISSING\0, approval invalidates — closed)."""
    marker = os.sep + ".tp-work" + os.sep
    p = os.path.abspath(ws)
    idx = p.find(marker)
    if idx <= 0:
        return None
    primary = p[:idx]
    return primary if os.path.isdir(primary) else None


def requirement_fingerprint(ws: str, rid) -> str:
    """Content hash of the anchored requirement record (v2.3.0 M2):
    the index entry AND the KB markdown record. Any edit to either after
    design approval changes this hash and so invalidates the approval —
    requirement→design traceability is pinned, not assumed.

    v3 dogfood fix: in a linked worktree (parallel wave agent workspace) the
    workspace-keyed store is empty — resolve the record from the PRIMARY
    workspace's store instead, so task DoD inside `.tp-work/<task>` sees the
    same requirement the approval pinned. Fail-closed is preserved: a truly
    missing/edited record still changes the hash."""
    h = hashlib.sha256()
    rid = str(rid or "").strip()
    rec = reqs.get_requirement(ws, rid) if rid else None
    if rec is None and rid:
        primary = _primary_workspace(ws)
        if primary:
            ws = primary
            rec = reqs.get_requirement(ws, rid)
    if rec is None:
        h.update(b"\0MISSING\0")
        return h.hexdigest()
    h.update(json.dumps(rec, sort_keys=True, separators=(",", ":"),
                        default=str).encode("utf-8",
                                            errors="surrogateescape"))
    rel = str(rec.get("file") or "").strip()
    if rel:
        h.update(b"\0record\0")
        try:
            with open(os.path.join(reqs.kb_dir(ws), rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"\0MISSING\0")
    return h.hexdigest()


def design_evidence_fingerprint(ws: str,
                                 contract: dict | None = None) -> str:
    """Fingerprint exactly the approved design evidence, not source code.

    v2.3.0 M2: the anchored requirement record is pinned too — a
    requirement edit after approval invalidates the design approval."""
    contract = contract if contract is not None \
        else (design_contract(ws)[0] or {})
    h = hashlib.sha256()
    for rel in sorted(set(design_evidence_paths(ws, contract))):
        h.update(b"\0path\0")
        h.update(rel.encode("utf-8", errors="surrogateescape"))
        try:
            with open(design_path(ws, rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"\0MISSING\0")
    rid = str(contract.get("requirement") or "").strip()
    h.update(b"\0requirement\0")
    h.update(rid.encode("utf-8", errors="surrogateescape"))
    h.update(requirement_fingerprint(ws, rid).encode("ascii"))
    return h.hexdigest()


def design_content_fingerprint(ws: str,
                                contract: dict | None = None) -> str:
    """Fingerprint of the design CONTENT a lens run judged: the contract
    body WITHOUT its own lens_evidence (so evidence can bind to the content
    without self-reference), the narrative, and any required visualization.
    A lens-evidence row must carry this value as content_fingerprint —
    change the design and the old attestation mechanically goes stale."""
    contract = contract if contract is not None \
        else (design_contract(ws)[0] or {})
    h = hashlib.sha256()
    body = {k: contract[k] for k in sorted(contract)
            if k != "lens_evidence"}
    h.update(b"\0contract-body\0")
    h.update(json.dumps(body, sort_keys=True, separators=(",", ":"),
                        default=str).encode("utf-8",
                                            errors="surrogateescape"))
    for rel in sorted(set(design_evidence_paths(ws, contract))
                      - {DESIGN_CONTRACT}):
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
        return ["approved design evidence or its anchored requirement "
                "changed after approval — return to Design and obtain a new "
                "human approval"]
    return []


def design_attach_requirement(ws: str, state: dict, rid) -> list:
    """Mechanical exit for a design loop started without --req (v2.3.0 H1).

    The documented journey (`loop init --design` without --req, author the
    spec, pass the pm gate) used to dead-end at the design DoR with no
    sanctioned way to attach an R-id mid-loop. This attaches one WITHOUT
    weakening the gate: it validates exactly what the design DoR itself
    demands (the requirement exists, has acceptance criteria, and carries
    no open questions) and refuses to swap an already-anchored requirement.
    Mutates state["requirement_id"] on success; the caller persists state
    and re-runs the DoR — nothing here bypasses any check downstream.
    Returns a list of errors; empty means attached (or already attached)."""
    rid = str(rid or "").strip()
    if not rid:
        return ["a requirement R-id is required to anchor the design"]
    existing = str(state.get("requirement_id") or "").strip()
    if existing and existing != rid:
        return [f"loop is already anchored to {existing} — refusing to swap "
                "requirements mid-loop; start a new loop to re-anchor"]
    rec = reqs.get_requirement(ws, rid)
    if rec is None:
        return [f"requirement {rid} does not exist — record it first with "
                "`tp req new \"<title>\" --acceptance ...`"]
    if not rec.get("acceptance"):
        return [f"requirement {rid} has no acceptance criteria — the design "
                "DoR requires testable acceptance before the HOW is proposed"]
    if rec.get("open_questions"):
        return [f"requirement {rid} has unresolved open questions: "
                + "; ".join(str(q) for q in rec["open_questions"])]
    if existing != rid:
        state["requirement_id"] = rid
        tp.trace(ws, "design_requirement_attached", id=rid)
    return []


def design_dor(ws: str, state: dict) -> dict:
    """Entry gate for the proposed-HOW phase."""
    blockers, warnings = [], []
    rid = state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if not rid:
        blockers.append(
            "Design must be anchored to a requirement R-id — record one "
            "with `tp req new \"<title>\" --acceptance ...`, then attach it "
            "to this loop with `loop gate pass --req R-xxxx` (or `loop next "
            "--req R-xxxx`); the in-flight loop is preserved")
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
        blockers.append("baseline dependency graph is stale for the current "
                        "HEAD — run graph scan to refresh it")
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
        cid = str(row["id"])
        # v2.3.0 L1: one rule with plan readiness — an id the plan DoR would
        # reject must never survive Design approval (dead-end otherwise).
        if not cid.strip().startswith(CONTRACT_ID_PREFIXES):
            errors.append("design contract ids need contract: or resource: "
                          "prefixes (the plan readiness rule): " + cid)
        contract_ids.add(cid)
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
    # v2.3.0 L2: an actionable alert and a rationale-for-none are distinct,
    # reviewable shapes — exactly one of the two fields must be present.
    has_alerts = "alerts" in observability
    has_none_rationale = "alerts_none_rationale" in observability
    if has_alerts == has_none_rationale:
        errors.append("design observability needs exactly one of "
                      "alerts: [...] (actionable alerts) or "
                      "alerts_none_rationale: \"...\" (why none are needed)")
    elif has_alerts and not text_list(observability.get("alerts")):
        errors.append("design observability alerts must be a non-empty list "
                      "of actionable alert descriptions")
    elif has_none_rationale \
            and not text(observability.get("alerts_none_rationale")):
        errors.append("design observability alerts_none_rationale must "
                      "explain why no alerts are needed")
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
    else:
        # v2.3.0 M3: the row must be BOUND to the content it judged and to
        # WHO judged it — a bare designer-typed pass row is no longer enough.
        row = evidence[0]
        if not text(row.get("produced_by")):
            errors.append("solution-design lens evidence must record "
                          "produced_by — WHO ran the lens")
        expected_fp = design_content_fingerprint(ws, contract)
        if row.get("content_fingerprint") != expected_fp:
            errors.append("solution-design lens evidence is not bound to the "
                          "current design content — re-run the lens against "
                          "this design and record its content_fingerprint "
                          f"(design_content_fingerprint, now {expected_fp[:12]}…)")
        independent = row.get("independent") is True
        self_attested = row.get("self_attested") is True
        if independent == self_attested:
            errors.append("solution-design lens evidence must declare exactly "
                          "one of independent: true or self_attested: true — "
                          "implicit self-attestation is never accepted "
                          "silently; self_attested rows are surfaced to the "
                          "human at the approval gate")
    if not isinstance(contract.get("open_questions"), list):
        errors.append("design open_questions is required — list unresolved "
                      "questions, or [] when none")
    elif contract.get("open_questions"):
        errors.append("design has unresolved open_questions")
    try:
        with open(design_path(ws, DESIGN_NARRATIVE), encoding="utf-8") as f:
            if not f.read().strip():
                errors.append("design narrative is empty")
    except OSError:
        errors.append("design narrative is missing: " + DESIGN_NARRATIVE)
    return errors


def design_approval_notices(ws: str, contract: dict | None = None) -> list:
    """Human-visible notices the design approval gate must render (v2.3.0
    M3). These never unblock anything — but a self-attested lens row is
    surfaced HERE, at the human gate, instead of being silently accepted."""
    contract = contract if contract is not None \
        else (design_contract(ws)[0] or {})
    notices = []
    rows = contract.get("lens_evidence")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("self_attested") is True:
            who = str(row.get("produced_by") or "").strip() or "unknown"
            notices.append(
                f"{str(row.get('lens') or 'lens').strip()} evidence is "
                f"SELF-ATTESTED by {who} — no independent lens run backs "
                "it; verify the lens checks yourself before approving")
    return notices


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
        planned_modules.update(
            depgraph.scope_modules(ws, task.get("scope") or []))
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
    # v2.3.0 M4: an edge the import scanner can never see (a boundary
    # endpoint) enters the as-built graph only by hand-recording, so its
    # presence proves the edge was typed in — not that the code realizes
    # it. Each such edge needs an explicit human-visible declaration with
    # concrete evidence, surfaced at the EM gate.
    unscannable = {
        edge_key(row) for row in graph.get("proposed_edges") or []
        if isinstance(row, dict)
        and (str(row.get("from") or "").startswith(BOUNDARY_NODE_PREFIXES)
             or str(row.get("to") or "").startswith(BOUNDARY_NODE_PREFIXES))}
    edge_rows = evidence.get("edge_evidence")
    if edge_rows is not None and not isinstance(edge_rows, list):
        errors.append("engineering design evidence edge_evidence must be a list")
        edge_rows = []
    declared = {str(row.get("edge") or "").strip(): row
                for row in edge_rows or [] if isinstance(row, dict)}
    for key in sorted(unscannable):
        row = declared.get(key)
        if not isinstance(row, dict) or not _text(row.get("evidence")) \
                or not _text(row.get("declared_by")):
            errors.append(
                "scanner-invisible designed edge needs an explicit "
                "realization declaration in edge_evidence — edge, evidence "
                "(file:line, test, or probe) and declared_by: " + key)
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
    # v2.3.0 L3: the real rule is ANY drift entry blocks — say so, and give
    # explained/accepted deviations a representation the EM gate renders
    # instead of incentivizing under-reporting.
    if isinstance(evidence.get("drift"), list) and evidence.get("drift"):
        errors.append(
            "engineering review records design drift — any drift entry "
            "blocks sign-off; return through Design, or move a "
            "human-accepted deviation to accepted_drift with drift, "
            "reason, and accepted_by")
    accepted = evidence.get("accepted_drift")
    if accepted is not None:
        if not isinstance(accepted, list):
            errors.append("engineering design evidence accepted_drift "
                          "must be a list")
        else:
            for row in accepted:
                if not isinstance(row, dict) or any(
                        not _text(row.get(k))
                        for k in ("drift", "reason", "accepted_by")):
                    errors.append("every accepted_drift entry needs drift, "
                                  "reason, and accepted_by")
    return errors


def design_review_notices(meta: dict) -> list:
    """Render lines for the EM/sign-off gate (v2.3.0 L3 + M4): accepted
    design drift and hand-declared edge realizations are VISIBLE on a
    passing review instead of dead-on-pass."""
    evidence = (meta or {}).get("design")
    if not isinstance(evidence, dict):
        return []
    notices = []
    accepted = evidence.get("accepted_drift")
    for row in accepted if isinstance(accepted, list) else []:
        if isinstance(row, dict):
            who = str(row.get("accepted_by") or "").strip() or "unknown"
            notices.append(
                f"accepted design drift (by {who}): "
                f"{str(row.get('drift') or '').strip()} — "
                f"{str(row.get('reason') or '').strip()}")
    edge_rows = evidence.get("edge_evidence")
    for row in edge_rows if isinstance(edge_rows, list) else []:
        if isinstance(row, dict):
            who = str(row.get("declared_by") or "").strip() or "unknown"
            notices.append(
                "declared realization of scanner-invisible edge "
                f"{str(row.get('edge') or '').strip()}: "
                f"{str(row.get('evidence') or '').strip()} "
                f"(declared by {who})")
    return notices


