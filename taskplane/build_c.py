"""BUILD-C phase authority and the bounded DEFINE review projection.

This module adds orchestration edges only.  Review membership, depth, slot
leases, and event waiting remain owned by the incumbent review/lens/loop
surfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os

import depgraph
import review
import taskplane_lite as tp


PROGRAM_LEDGER_SCHEMA = "taskplane.program-phase-ledger/v1"
DEFINE_PROJECTION_SCHEMA = "taskplane.define-projection/v1"


class ProgramAuthorityError(RuntimeError):
    """The requested program phase has no predecessor authority."""


class DefineProjectionError(RuntimeError):
    """DEFINE could not preserve the bounded quick-review contract."""


def _program_authority(ledger: Mapping[str, object]) -> Mapping[str, object]:
    if ledger.get("schema") != "taskplane.r0012-program-ledger/v1":
        raise ProgramAuthorityError("program ledger schema is invalid")
    authority = ledger.get("program_authority")
    if not isinstance(authority, Mapping) or \
            authority.get("schema") != PROGRAM_LEDGER_SCHEMA:
        raise ProgramAuthorityError("program phase authority is missing")
    return authority


def require_program_phase(ledger: Mapping[str, object], phase: str) -> dict:
    """Fail closed unless the requested R-0012 phase is currently eligible."""
    authority = _program_authority(ledger)
    approval = authority.get("consolidated_approval")
    approval = approval if isinstance(approval, Mapping) else {}
    if approval.get("approved") is not True or \
            not str(approval.get("actor") or "").strip() or \
            not str(approval.get("authority_receipt") or "").strip():
        raise ProgramAuthorityError(
            "attributed consolidated human approval is required")

    normalized = str(phase or "").strip().lower().replace("-", "")
    if normalized not in {"r0009", "r0010", "r0011"}:
        raise ProgramAuthorityError(f"unknown program phase {phase!r}")
    if normalized in {"r0010", "r0011"}:
        r0009 = authority.get("r0009")
        r0009 = r0009 if isinstance(r0009, Mapping) else {}
        if r0009.get("accepted") is not True or \
                not str(r0009.get("evidence_digest") or "").strip():
            raise ProgramAuthorityError(
                "R-0009 acceptance is required before R-0010")
    if normalized == "r0011":
        r0011 = authority.get("r0011")
        r0011 = r0011 if isinstance(r0011, Mapping) else {}
        if r0011.get("exact_sha_green") is not True or \
                not str(r0011.get("signed_off_by") or "").strip():
            raise ProgramAuthorityError(
                "R-0010 exact-SHA proof and human sign-off are required "
                "before R-0011")
    if normalized == "r0010":
        r0010 = authority.get("r0010")
        r0010 = r0010 if isinstance(r0010, Mapping) else {}
        if r0010.get("status") != "active":
            raise ProgramAuthorityError("R-0010 is not active")
    material = {
        "schema": PROGRAM_LEDGER_SCHEMA,
        "phase": normalized,
        "actor": str(approval["actor"]),
        "authority_receipt": str(approval["authority_receipt"]),
        "r0009_evidence": str(
            ((authority.get("r0009") or {}).get("evidence_digest") or "")),
    }
    return {**material, "status": "authorized", "fingerprint":
            hashlib.sha256(json.dumps(
                material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()}


def _load_ledger(ws: str) -> dict:
    path = os.path.join(ws, "exports", "r0012-program-ledger.json")
    value = tp.load_json(path, default=None, what="R-0012 program ledger")
    if not isinstance(value, dict):
        raise ProgramAuthorityError("R-0012 program ledger is missing")
    return value


def program_enabled(ws: str) -> bool:
    """Return whether this checkout declares the governed R-0012 program."""
    try:
        _program_authority(_load_ledger(ws))
        return True
    except (OSError, ValueError, ProgramAuthorityError):
        return False


def _design_modules(ws: str) -> list[str]:
    contract = tp.load_json(
        os.path.join(ws, "design", "contract.json"), default=None,
        what="approved Design Contract")
    modules = ((contract or {}).get("graph") or {}).get("proposed_modules")
    if not isinstance(modules, list) or not modules:
        raise DefineProjectionError(
            "approved Design Contract has no proposed modules")
    return sorted({str(path) for path in modules if str(path).strip()})


def _define_impact(graph: Mapping[str, object], files: list[str]) -> dict:
    touched = []
    covered = set()
    modules = graph.get("modules")
    modules = modules if isinstance(modules, Mapping) else {}
    selected = set(files)
    for module_id, row in modules.items():
        module_files = set((row or {}).get("files") or []) \
            if isinstance(row, Mapping) else set()
        overlap = selected & module_files
        if overlap:
            touched.append(str(module_id))
            covered.update(overlap)
    return {
        "touched": sorted(touched), "impacted": {},
        "total_impacted": len(touched),
        "unknown": sorted(selected - covered),
    }


def _validated_define_evidence(manifest: Mapping[str, object]) -> dict:
    """Validate and retain the ReviewKernel evidence emitted for DEFINE."""
    if manifest.get("status") != "ready" or manifest.get("stage") != "define":
        raise DefineProjectionError("DEFINE review did not become ready")
    if manifest.get("routing_mode") != "selective":
        raise DefineProjectionError(
            "DEFINE requires selective quick-only routing")
    slots = manifest.get("slots")
    if not isinstance(slots, list) or not 4 <= len(slots) <= 5:
        raise DefineProjectionError(
            "DEFINE must emit exactly four or five quick sweep slots")
    selected = []
    slot_ids = set()
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise DefineProjectionError("DEFINE slot is malformed")
        slot_id = str(slot.get("slot_id") or "")
        lens_ids = slot.get("lens_ids")
        if not slot_id.startswith("sweep.") or \
                not isinstance(lens_ids, list) or len(lens_ids) != 1:
            raise DefineProjectionError(
                "DEFINE may contain quick sweep slots only")
        if slot_id in slot_ids or str(lens_ids[0]) in selected:
            raise DefineProjectionError("DEFINE slots must be unique")
        slot_ids.add(slot_id)
        selected.append(str(lens_ids[0]))
    if "architecture" not in selected:
        raise DefineProjectionError(
            "DEFINE quick sweep requires the architecture floor")

    slot_ids = [str(row["slot_id"]) for row in slots]
    depth = manifest.get("review_depth_policy")
    if not isinstance(depth, Mapping) or \
            depth.get("depth") != "quick-only" or \
            depth.get("deep_slots_allowed") is not False or \
            depth.get("deep_slots") != [] or \
            depth.get("promotion_attempts") != 0 or \
            not isinstance(depth.get("quick_slots"), list) or \
            sorted(depth["quick_slots"]) != sorted(slot_ids):
        raise DefineProjectionError(
            "DEFINE requires observed quick-only depth evidence")
    routing_counts = manifest.get("routing_counts")
    if not isinstance(routing_counts, Mapping) or \
            int(routing_counts.get("sweep") or 0) != len(slots) or \
            set(routing_counts) - {"sweep", "n/a"}:
        raise DefineProjectionError(
            "DEFINE may not dispatch deep, full, or 26-lens review")

    dispatch_sets = [row.get("dispatch_set") for row in slots]
    if not all(isinstance(row, Mapping) for row in dispatch_sets):
        raise DefineProjectionError(
            "DEFINE slots require router-produced dispatch evidence")
    dispatch_set = dict(dispatch_sets[0])
    if any(dict(row) != dispatch_set for row in dispatch_sets[1:]) or \
            dispatch_set.get("schema") != "taskplane.dispatch-set/v1" or \
            not str(dispatch_set.get("id") or "").strip() or \
            dispatch_set.get("concurrent") is not True or \
            int(dispatch_set.get("member_count") or 0) != len(slots):
        raise DefineProjectionError(
            "DEFINE requires one concurrent router-produced dispatch set")
    wait_policies = [row.get("wait_policy") for row in slots]
    if not all(isinstance(row, Mapping) for row in wait_policies) or any(
            row.get("schema") != "taskplane.wait-policy/v1" or
            row.get("outstanding_set") != dispatch_set["id"] or
            int(row.get("outstanding_count") or 0) != len(slots) or
            row.get("mode") != "event" or
            int(row.get("timeout_seconds") or 0) < 1800 or
            row.get("scheduled_polling") is not False or
            set(row.get("reissue_after") or []) != {"completion", "attention"}
            for row in wait_policies):
        raise DefineProjectionError(
            "DEFINE requires router-produced event wait evidence")
    return {
        "selected_lenses": [
            "architecture", *sorted(set(selected) - {"architecture"})],
        "dispatch_set": dispatch_set,
        "automatic_deep": bool(depth.get("deep_slots")),
        "automatic_full": manifest.get("routing_mode") == "all",
        "serial_fallback": dispatch_set.get("concurrent") is not True,
    }


def validate_define_projection(manifest: Mapping[str, object]) -> list[str]:
    """Validate only the invariants added at DEFINE; never select again."""
    return _validated_define_evidence(manifest)["selected_lenses"]


def project_define(
        ws: str, state: Mapping[str, object], *,
        start_review: Callable[..., dict] = review.start_review,
        selector: Callable[..., dict] | None = None,
        bind_actions: Callable[..., dict] | None = None,
        graph: dict | None = None, revision: str | None = None) -> dict:
    """Invoke ReviewKernel once and expose its quick slots concurrently.

    The emitted inline catalog is contained in ReviewKernel briefs as priming
    evidence.  This function does not route it again or create any other slot.
    """
    authority = require_program_phase(_load_ledger(ws), "r0010")
    actor = str(state.get("design_approved_by") or "").strip()
    fingerprint = str(state.get("design_fingerprint") or "").strip()
    if not actor or actor == "(unattributed)" or not fingerprint:
        raise ProgramAuthorityError(
            "DEFINE requires attributed approved Design authority")
    files = _design_modules(ws)
    graph = graph if isinstance(graph, dict) else depgraph.load(ws)
    revision = str(revision or tp.git_head(ws) or "")
    target_material = {
        "stage": "define", "revision": revision,
        "design_fingerprint": fingerprint,
    }
    target = {"head": revision, **target_material, "fingerprint":
              hashlib.sha256(json.dumps(
                  target_material, sort_keys=True, separators=(",", ":")
              ).encode("utf-8")).hexdigest()}
    requirement = {
        "id": str(state.get("requirement_id") or "R-0012"),
        "text": str(state.get("goal") or "Approved Design definition"),
        "review_policy": {"depth": "quick-only"},
    }
    if selector is None:
        raise DefineProjectionError(
            "DEFINE requires an observable ReviewKernel selector")
    selector_invocations = 0

    def observed_router() -> dict:
        nonlocal selector_invocations
        selector_invocations += 1
        return selector(
            files, task_type="design", breadth="routed", stage="define",
            workspace=ws, requirement_text=requirement["text"])

    manifest = start_review(
        ws, target=target, graph=graph,
        impact=_define_impact(graph, files),
        diff={"files": files, "changed_symbols": []},
        requirement=requirement,
        acceptance=list(state.get("acceptance") or []),
        contracts=["contract:define.design-review",
                   "contract:review.routing",
                   "contract:review.depth-boundary"],
        stage="define", task_type="design", base="HEAD",
        router=observed_router)
    if selector_invocations != 1:
        raise DefineProjectionError(
            "DEFINE requires exactly one selector invocation")
    evidence = _validated_define_evidence(manifest)
    if bind_actions is None:
        import loop
        bind_actions = loop._bind_stateless_review_contract_actions
    bound = bind_actions(ws, manifest, task_id="define")
    wait = bound.get("wait_invocation") if isinstance(bound, Mapping) else None
    if not isinstance(wait, Mapping) or wait.get("scheduled") is not False or \
            wait.get("reissue") is not False or \
            wait.get("operation") != "wait_for_events" or \
            int(wait.get("timeout_seconds") or 0) < 1800:
        raise DefineProjectionError(
            "DEFINE requires exactly one unscheduled event wait")
    members = list(wait.get("outstanding_members") or [])
    slots = list(bound.get("slots") or [])
    if members != [str(row.get("slot_id") or "") for row in slots]:
        raise DefineProjectionError(
            "DEFINE wait must cover the emitted quick slots exactly once")
    if any(not isinstance(row, Mapping) or
           dict(row.get("dispatch_set") or {}) != evidence["dispatch_set"]
           for row in slots):
        raise DefineProjectionError(
            "DEFINE binding must preserve router-produced dispatch evidence")
    return {
        "schema": DEFINE_PROJECTION_SCHEMA,
        "status": "ready", "stage": "define",
        "program_authority": authority,
        "run_id": str(bound.get("run_id") or ""),
        "selected_lenses": evidence["selected_lenses"],
        "dispatch_set": evidence["dispatch_set"],
        "slots": slots, "wait_invocation": dict(wait),
        "selector_invocations": selector_invocations,
        "automatic_deep": evidence["automatic_deep"],
        "automatic_full": evidence["automatic_full"],
        "serial_fallback": evidence["serial_fallback"],
    }
