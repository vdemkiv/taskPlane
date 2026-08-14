"""Deterministic graph-quality evidence for pre-routing review gates.

The dependency graph is useful only when the record says what it covered.
This module turns the graph, canonical target and module impact into one
content-fingerprinted ``graph-quality-v1`` record.  Sparse module evidence may
invoke exactly one caller adapter; freshness/truncation failures are terminal
because querying a known-stale snapshot cannot make it current.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Callable


DEFAULT_CALLER_BOUNDS = {
    "max_symbols": 128,
    "max_hops": 6,
    "max_edges": 512,
    "timeout_seconds": 10,
}

_LANGUAGE_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".cs": "csharp", ".java": "java", ".rb": "ruby",
}
_COMPLETE_COVERAGE = {"complete", "full", "all", "supported"}


class GraphQualityError(ValueError):
    """The caller expansion or graph evidence violates its bounded schema."""


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sorted_strings(values) -> list[str]:
    return sorted({str(v).strip() for v in (values or []) if str(v).strip()})


def _scanner_rows(graph: dict, changed_files: list[str]) -> list[dict]:
    scanners = dict(((graph.get("meta") or {}).get("scanners") or {}))
    relevant = sorted({_LANGUAGE_BY_EXT.get(os.path.splitext(p)[1].lower())
                       for p in changed_files}
                      - {None})
    rows = []
    for language in sorted(set(scanners) | set(relevant)):
        raw = scanners.get(language)
        if isinstance(raw, str):
            raw = {"coverage": raw}
        elif not isinstance(raw, dict):
            raw = {}
        # The graph producer records exceptions (for example partial Go
        # coverage), while its built-in source scanners historically omit a
        # row when their normal coverage is complete.  Every language in
        # ``relevant`` came from that supported source-extension contract, so
        # absence is the producer's complete shape rather than unsupported.
        coverage = str(raw.get("coverage") or (
            "complete" if language in scanners or language in relevant
            else "unsupported"))
        row = {"language": language, "coverage": coverage,
               "relevant": language in relevant}
        for key in ("files", "covered_files", "total_files", "limitation",
                    "unsupported", "unresolved"):
            if key in raw:
                row[key] = copy.deepcopy(raw[key])
        rows.append(row)
    return rows


def _unresolved_edges(graph: dict) -> list:
    meta = graph.get("meta") or {}
    rows = list(meta.get("unresolved_internal_edges") or [])
    for edge in graph.get("edges") or []:
        if edge.get("unresolved") and edge.get("internal", True):
            rows.append({k: edge.get(k) for k in
                         ("from", "to", "kind", "reason") if edge.get(k)})
    # Rows can be dicts.  Canonical bytes are both deterministic and total.
    unique = {canonical_bytes(row): copy.deepcopy(row) for row in rows}
    return [unique[key] for key in sorted(unique)]


def _module_confidence(graph: dict, impact: dict, scanner_rows: list[dict],
                       unresolved: list, stale: bool, truncated: bool) -> str:
    explicit = str(impact.get("module_confidence") or
                   (graph.get("meta") or {}).get("module_confidence") or "")
    if explicit in {"high", "medium", "low"}:
        return explicit
    relevant_partial = any(row["relevant"] and
                           row["coverage"].lower() not in _COMPLETE_COVERAGE
                           for row in scanner_rows)
    if stale or truncated or unresolved or impact.get("unknown"):
        return "low"
    if relevant_partial:
        return "medium"
    return "high"


def _bounds(value: dict | None) -> dict:
    out = dict(DEFAULT_CALLER_BOUNDS)
    out.update(value or {})
    for key, minimum in (("max_symbols", 1), ("max_hops", 1),
                         ("max_edges", 1), ("timeout_seconds", 1)):
        try:
            out[key] = max(minimum, int(out[key]))
        except (TypeError, ValueError):
            raise GraphQualityError(f"invalid caller-expansion bound: {key}")
    return out


def _run_expander(expander: Callable, *, snapshot, changed_symbols, bounds):
    """Invoke the protocol once and normalize its deterministic result."""
    result = expander(snapshot=snapshot, changed_symbols=list(changed_symbols),
                      bounds=dict(bounds))
    if not isinstance(result, dict):
        raise GraphQualityError("caller expansion must return an object")
    callers = _sorted_strings(result.get("callers"))
    contracts = _sorted_strings(result.get("contracts"))
    unresolved = _sorted_strings(result.get("unresolved"))
    try:
        edges_examined = max(0, int(result.get("edges_examined", 0)))
    except (TypeError, ValueError):
        raise GraphQualityError("caller expansion edges_examined is invalid")
    limit_exceeded = (len(changed_symbols) > bounds["max_symbols"] or
                      edges_examined > bounds["max_edges"] or
                      bool(result.get("truncated")) or
                      bool(result.get("timed_out")))
    complete = bool(result.get("complete")) and not unresolved \
        and not limit_exceeded
    return {
        "callers": callers,
        "contracts": contracts,
        "unresolved": unresolved,
        "complete": complete,
        "truncated": bool(result.get("truncated")),
        "timed_out": bool(result.get("timed_out")),
        "edges_examined": edges_examined,
        "adapter": str(result.get("adapter") or "caller-adapter"),
    }


def assess(graph: dict, *, target_head: str, changed_files,
           changed_symbols, impact: dict, caller_expander: Callable | None = None,
           snapshot=None, bounds: dict | None = None) -> dict:
    """Create the complete pre-routing graph-quality record.

    ``caller_expander`` is called zero or one times.  Its input is the pinned
    ``snapshot`` supplied by the caller (or a minimal target/graph identity),
    never the ambient working tree.
    """
    graph = graph if isinstance(graph, dict) else {}
    impact = copy.deepcopy(impact if isinstance(impact, dict) else {})
    changed_files = _sorted_strings(changed_files)
    changed_symbols = _sorted_strings(changed_symbols)
    meta = graph.get("meta") or {}
    scanned_head = str(meta.get("scanned_head") or "")
    target_head = str(target_head or "")
    stale = not scanned_head or not target_head or scanned_head != target_head
    scanner_rows = _scanner_rows(graph, changed_files)
    unresolved_edges = _unresolved_edges(graph)
    policy_limited = bool(impact.get("policy_blocked"))
    raw_depth_truncated = impact.get("depth_truncated")
    if raw_depth_truncated is None:
        # Backward-compatible interpretation for stored v1 impacts: when the
        # only named reason is a policy stop, the radius is complete under
        # that policy.  New impacts always carry depth_truncated explicitly.
        raw_depth_truncated = (bool(impact.get("truncated"))
                               and not policy_limited)
    truncated = bool(meta.get("truncated") or raw_depth_truncated)
    confidence = _module_confidence(graph, impact, scanner_rows,
                                    unresolved_edges, stale, truncated)
    relevant_partial = any(row["relevant"] and
                           row["coverage"].lower() not in _COMPLETE_COVERAGE
                           for row in scanner_rows)
    structural_reasons = []
    if stale:
        structural_reasons.append("stale_graph")
    if truncated:
        structural_reasons.append("truncated_graph_or_impact")
    if unresolved_edges:
        structural_reasons.append("unresolved_internal_edges")
    if relevant_partial:
        structural_reasons.append("scanner_coverage_incomplete")

    module_insufficient = confidence != "high" or bool(impact.get("unknown"))
    normalized_bounds = _bounds(bounds)
    expansion = {"attempted": False, "count": 0,
                 "bounds": normalized_bounds, "status": "not_needed"}
    callers, contracts, caller_unresolved = [], [], []
    caller_complete = not module_insufficient
    # Fresh but sparse module evidence gets exactly one bounded chance.
    if module_insufficient and not structural_reasons:
        if caller_expander is None:
            expansion["status"] = "unavailable"
            caller_complete = False
        else:
            snap = snapshot if snapshot is not None else {
                "target_head": target_head,
                "graph_fingerprint": meta.get("content_fingerprint") or
                fingerprint(graph),
            }
            result = _run_expander(
                caller_expander, snapshot=snap,
                changed_symbols=changed_symbols, bounds=normalized_bounds)
            expansion.update({"attempted": True, "count": 1,
                              "status": "complete" if result["complete"]
                              else "incomplete",
                              "edges_examined": result["edges_examined"],
                              "adapter": result["adapter"],
                              "truncated": result["truncated"],
                              "timed_out": result["timed_out"]})
            callers = result["callers"]
            contracts = result["contracts"]
            caller_unresolved = result["unresolved"]
            caller_complete = result["complete"]
            if caller_complete:
                impact["expanded_callers"] = callers
                impact["expanded_contracts"] = contracts
                impact["caller_expansion"] = copy.deepcopy(expansion)

    reasons = list(structural_reasons)
    if module_insufficient and not caller_complete:
        reasons.append("caller_coverage_incomplete")
    sufficient = not reasons
    requested = len(changed_symbols)
    resolved = requested if not module_insufficient else (
        requested if caller_complete else max(0, requested-len(caller_unresolved)))
    record = {
        "schema": "taskplane.graph-quality/v1",
        "graph_fingerprint": str(meta.get("content_fingerprint") or
                                 fingerprint(graph)),
        "scanned_head": scanned_head,
        "target_head": target_head,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "scanner_coverage": scanner_rows,
        "unresolved_internal_edges": unresolved_edges,
        "stale": stale,
        "truncated": truncated,
        "policy_limited": policy_limited,
        "module_confidence": confidence,
        "changed_symbol_caller_coverage": {
            "requested": requested, "resolved": resolved,
            "ratio": 1.0 if requested == 0 else resolved / requested,
            "callers": callers, "contracts": contracts,
            "unresolved": caller_unresolved,
            "status": ("complete" if caller_complete else "incomplete"),
        },
        "expansion": expansion,
        "contracts": _sorted_strings(list(impact.get("contracts") or []) +
                                     contracts),
        "impact": impact,
        "sufficient": sufficient,
        "status": "complete" if sufficient else "impact_incomplete",
        "reasons": sorted(set(reasons)),
    }
    record["fingerprint"] = fingerprint(record)
    return record


def dispatch_manifest(record: dict) -> dict:
    """The only legal recovery for insufficient impact: no dispatch at all."""
    if not isinstance(record, dict) or not record.get("sufficient"):
        return {"status": "impact_incomplete", "slots": [], "briefs": [],
                "agents": [], "breadth": None}
    return {"status": "ready", "slots": None, "briefs": None,
            "agents": None, "breadth": "routed"}


# Explicit aliases keep the contract readable at call sites.
assess_graph_quality = assess
graph_quality_record = assess
