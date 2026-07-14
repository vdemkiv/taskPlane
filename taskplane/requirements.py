"""Requirements — the spine of the knowledge base and the optimization lever.

Well-refined requirements (functional *and* non-functional) go near straight-
through to a build; under-refined ones cost several fix cycles. So this module
lets the loop spend effort up front on *refinement* to save it downstream:

  1. Requirements are first-class KB records (knowledge/requirements/R-NNNN-*).
  2. `score_refinement` scores a requirement on two axes — functional
     completeness and NFR coverage — using the LENS ROUTER to know which
     non-functional axes even apply to this change, and returns the specific
     gaps plus an iteration forecast (advisory, not a hard block).
  3. Task mode (quick | full) is a cost decision; the quick path is
     first-class and records a tracked `debt` item so nothing is silently
     half-done. Change requests use the same machinery (a changed requirement).

Pure stdlib. Distinct from the trace (audit) — this is durable memory.
"""

from __future__ import annotations

import datetime
import json
import os
import re

import lens
import taskplane_lite as tp

# The non-functional "-ilities" a requirement should state up front. The router
# tells us which apply to a given change; the intersection is what refinement
# checks for. (Craft/verification lenses — code-quality, testability, qa — are
# about HOW it's built/tested, not requirement-level NFRs, so they're excluded.)
NFR_LENSES = {
    "security", "scalability", "architecture", "data-safety",
    "accessibility", "privacy-compliance", "sre", "integrability",
    "i18n", "cost-finops", "dba",
}


def kb_dir(ws: str) -> str:
    # External per-project store, not the repo — see taskplane_lite.kb_root.
    return tp.kb_root(ws)


def _index_path(ws: str) -> str:
    return os.path.join(kb_dir(ws), "index.json")


def load_index(ws: str) -> dict:
    p = _index_path(ws)
    if not os.path.exists(p):
        return {"decisions": [], "flows": [], "requirements": [], "debt": []}
    with open(p) as f:
        idx = json.load(f)
    idx.setdefault("requirements", [])
    idx.setdefault("debt", [])
    return idx


def _save_index(ws: str, idx: dict) -> None:
    os.makedirs(kb_dir(ws), exist_ok=True)
    with open(_index_path(ws), "w") as f:
        json.dump(idx, f, indent=2)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "req"


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------- record

def record_requirement(ws: str, title: str, *, functional=None, nfr=None,
                       acceptance=None, open_questions=None,
                       status: str = "draft", tags=None, context_files=None,
                       links=None, changed_from: str | None = None,
                       date: str | None = None) -> dict:
    """Write a requirement record + index entry. Returns the entry.

    `nfr` is a dict keyed by NFR lens id, e.g. {"security": "no PII in logs"}.
    A change request is just a requirement with `changed_from` set — same store.
    """
    idx = load_index(ws)
    n = len(idx["requirements"]) + 1
    rid = f"R-{n:04d}"
    slug = _slug(title)
    links = dict(links or {})
    if changed_from:
        links["changed_from"] = changed_from
        status = "changed"
    entry = {
        "id": rid,
        "title": title,
        "status": status,
        "date": date or _today(),
        "tags": list(tags or []),
        "functional": list(functional or []),
        "nfr": dict(nfr or {}),
        "acceptance": list(acceptance or []),
        "open_questions": list(open_questions or []),
        "context_files": list(context_files or []),
        "links": links,
        "file": f"requirements/{rid}-{slug}.md",
    }
    idx["requirements"].append(entry)
    _save_index(ws, idx)

    os.makedirs(os.path.join(kb_dir(ws), "requirements"), exist_ok=True)

    def bullets(items):
        return "\n".join(f"- {x}" for x in items) or "—"

    nfr_lines = "\n".join(f"- **{k}**: {v}" for k, v in entry["nfr"].items()) \
        or "—"
    body = f"""# {rid} · {title}

- status: {entry['status']}
- date: {entry['date']}
- tags: {', '.join(entry['tags']) or '—'}
- context_files: {', '.join(entry['context_files']) or '—'}
- links: {json.dumps(entry['links']) if entry['links'] else '—'}

## Functional requirements
{bullets(entry['functional'])}

## Non-functional requirements (by lens)
{nfr_lines}

## Acceptance criteria (→ DoD)
{bullets(entry['acceptance'])}

## Open questions
{bullets(entry['open_questions'])}
"""
    with open(os.path.join(kb_dir(ws), entry["file"]), "w") as f:
        f.write(body)
    tp.trace(ws, "requirement_recorded", id=rid, title=title,
             status=entry["status"], changed_from=changed_from)
    return entry


def get_requirement(ws: str, rid: str) -> dict | None:
    for r in load_index(ws)["requirements"]:
        if r["id"] == rid:
            return r
    return None


def list_requirements(ws: str) -> list:
    return load_index(ws)["requirements"]


def set_status(ws: str, rid: str, status: str) -> None:
    idx = load_index(ws)
    for r in idx["requirements"]:
        if r["id"] == rid:
            r["status"] = status
    _save_index(ws, idx)


# --------------------------------------------------------------- refinement

def applicable_nfr_lenses(files, task_type=None, catalog=None) -> list:
    """Which NFR axes the router says apply to this change (ids)."""
    routing = lens.route(files or [], task_type=task_type, catalog=catalog)
    return [x["id"] for x in routing["lenses"] if x["id"] in NFR_LENSES]


def score_refinement(req: dict, *, changed_files=None, task_type=None,
                     catalog=None) -> dict:
    """Score a requirement's readiness to build, and name the gaps.

    Two axes:
      - functional: acceptance criteria stated & testable, open questions
        closed, functional statements present.
      - nfr: for each NFR lens the router says applies, is an NFR stated?

    Returns {score, functional, nfr, gaps[], applicable_nfr[], forecast}.
    Advisory: a low score recommends refining now (cheap) rather than
    discovering the gap mid-build (a full cycle each).
    """
    gaps = []

    # ---- functional axis
    fpts, ftot = 0, 3
    if req.get("functional"):
        fpts += 1
    else:
        gaps.append({"axis": "functional",
                     "detail": "no functional statements"})
    if req.get("acceptance"):
        fpts += 1
    else:
        gaps.append({"axis": "functional",
                     "detail": "no acceptance criteria (needed for DoD)"})
    if not req.get("open_questions"):
        fpts += 1
    else:
        gaps.append({"axis": "functional",
                     "detail": f"{len(req['open_questions'])} open question(s)"})
    functional = fpts / ftot

    # ---- nfr axis (router decides which apply)
    files = changed_files if changed_files is not None \
        else req.get("context_files", [])
    applicable = applicable_nfr_lenses(files, task_type=task_type,
                                       catalog=catalog)
    stated = set(req.get("nfr", {}))
    covered = [lz for lz in applicable if lz in stated]
    for lz in applicable:
        if lz not in stated:
            gaps.append({"axis": "nfr", "lens": lz,
                         "detail": f"no {lz} NFR stated"})
    nfr = 1.0 if not applicable else len(covered) / len(applicable)

    score = round(0.5 * functional + 0.5 * nfr, 2)
    return {
        "score": score,
        "functional": round(functional, 2),
        "nfr": round(nfr, 2),
        "applicable_nfr": applicable,
        "covered_nfr": covered,
        "gaps": gaps,
        "forecast": forecast(gaps),
    }


def forecast(gaps) -> str:
    """Iteration forecast — each unresolved gap tends to cost a fix cycle."""
    n = len(gaps)
    if n == 0:
        return "refined — expect near straight-through build (0 fix cycles)"
    cycles = (n + 1) // 2
    return (f"{n} gap(s) → expect ~{cycles} fix cycle(s) if built as-is; "
            "refining now is cheaper than discovering these mid-build")


def gate(req: dict, *, threshold: float = 0.6, high_cost: bool = False,
         changed_files=None, task_type=None, catalog=None) -> dict:
    """Advisory refinement gate for the plan step (open decision #1, locked:
    advisory with a loud forecast; a HARD block only for high-cost/irreversible
    work). Returns the score plus a recommendation and whether it blocks."""
    s = score_refinement(req, changed_files=changed_files,
                          task_type=task_type, catalog=catalog)
    below = s["score"] < threshold
    blocking = bool(below and high_cost)
    if not below:
        rec = "proceed — sufficiently refined"
    elif blocking:
        rec = ("BLOCK: high-cost/irreversible work below the refinement "
               "threshold — refine before building")
    else:
        rec = ("refine now recommended (advisory) — " + s["forecast"])
    return {**s, "threshold": threshold, "below_threshold": below,
            "blocking": blocking, "recommendation": rec}


# --------------------------------------------------------------- task mode

def suggest_mode(refinement_score: float, change_size: int, *,
                 threshold: float = 0.6, small: int = 5) -> dict:
    """Quick vs full — a cost decision (open decision #2, locked default: when
    refinement is low AND the change is small, default to quick + tracked debt;
    else full). Human picks; this only suggests, with the reason."""
    small_change = change_size <= small
    if refinement_score < threshold and small_change:
        mode, why = "quick", (
            f"low refinement ({refinement_score:.2f}) + small change "
            f"({change_size} file(s)) → do the minimal correct change now, "
            "track the full follow-up as debt")
    else:
        mode, why = "full", (
            "refinement or size warrants the properly-refined implementation "
            "across all applicable lenses")
    return {"mode": mode, "reason": why, "change_size": change_size,
            "refinement_score": refinement_score}


def estimate_cost(change_size: int, applicable_nfr) -> dict:
    """Rough heuristic cost (open decision #3, locked: heuristic now, real
    token/$ once the paid proxy lands). Units are relative, not dollars."""
    units = change_size + 2 * len(applicable_nfr)
    band = "small" if units <= 4 else "medium" if units <= 10 else "large"
    return {"units": units, "band": band,
            "basis": f"{change_size} file(s) + {len(applicable_nfr)} NFR axis"
                     "(es)"}


# --------------------------------------------------------------- debt

def record_debt(ws: str, title: str, *, requirement_id: str | None = None,
                reason: str = "", follow_up: str = "", tags=None,
                context_files=None, date: str | None = None) -> dict:
    """Record a tracked debt item for a quick-path task, so 'do it properly
    later' is retrievable and can be scheduled as its own requirement."""
    idx = load_index(ws)
    n = len(idx["debt"]) + 1
    did = f"D-{n:04d}"
    slug = _slug(title)
    entry = {
        "id": did,
        "title": title,
        "status": "open",
        "date": date or _today(),
        "requirement_id": requirement_id,
        "reason": reason,
        "follow_up": follow_up,
        "tags": list(tags or []),
        "context_files": list(context_files or []),
        "file": f"debt/{did}-{slug}.md",
    }
    idx["debt"].append(entry)
    _save_index(ws, idx)

    os.makedirs(os.path.join(kb_dir(ws), "debt"), exist_ok=True)
    body = f"""# {did} · {title}

- status: open
- date: {entry['date']}
- requirement: {requirement_id or '—'}
- tags: {', '.join(entry['tags']) or '—'}
- context_files: {', '.join(entry['context_files']) or '—'}

## Why deferred (quick path taken)
{reason or '—'}

## Full follow-up (do it properly)
{follow_up or '—'}
"""
    with open(os.path.join(kb_dir(ws), entry["file"]), "w") as f:
        f.write(body)
    tp.trace(ws, "debt_recorded", id=did, title=title,
             requirement_id=requirement_id)
    return entry


def list_debt(ws: str, *, open_only: bool = True) -> list:
    items = load_index(ws)["debt"]
    return [d for d in items if not open_only or d["status"] == "open"]


def resolve_debt(ws: str, did: str) -> None:
    idx = load_index(ws)
    for d in idx["debt"]:
        if d["id"] == did:
            d["status"] = "resolved"
    _save_index(ws, idx)


def render_context(reqs: list) -> str:
    """Compact payload injected at step start (token-lean)."""
    if not reqs:
        return ""
    lines = ["Requirements anchoring this work (from the knowledge base):"]
    for r in reqs:
        oq = f", {len(r['open_questions'])} open Q" if r.get(
            "open_questions") else ""
        lines.append(f"  [{r['id']}] {r['title']} ({r['status']}{oq})")
    lines.append("Every task must trace to a requirement id; honor acceptance "
                 "criteria as the DoD.")
    return "\n".join(lines)
