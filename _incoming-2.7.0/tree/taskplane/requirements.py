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

import contextlib
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

# B1 recalibration (R-0008): these NFR axes are NEVER discounted. A missing
# statement on one of them keeps full forecast weight and caps the score
# below the non-blocking threshold no matter how functionally complete the
# requirement is.
#
# The set is the RISK-BEARING family, not a shortlist (Phase 3 EM review,
# deep3 finding #3): {security, data-safety} alone was narrower than B1's
# own no-under-warn principle, and the three axes it omitted bear risk too —
# privacy-compliance (PII/GDPR/regulatory exposure), dba (schema and data
# migrations: data-layer irreversibility), sre (availability: operational
# failure). Measured before the widening, a functionally-complete
# requirement with `dba` unstated scored 0.91 (0.5 at 43253c22), so
# high-cost irreversible work touching a data migration with no dba
# statement returned "proceed — sufficiently refined" where it previously
# hard-BLOCKED. What stays discountable is the fit-and-finish family —
# scalability, architecture, accessibility, integrability, i18n,
# cost-finops — where a late statement costs rework, not an unrecoverable
# outcome.
CRITICAL_NFR_LENSES = {"security", "data-safety", "privacy-compliance",
                       "dba", "sre"}

# Class weights for the iteration forecast (design decision B1, approach A —
# class-weighted, not flattened): functional gaps keep the original 0.5-cycle
# weight; non-critical NFR gaps drop to 0.1 ONLY when the functional axis is
# complete; critical NFR gaps always cost 0.5 (under-warning on risky work is
# strictly worse than the over-warning this recalibration fixes).
#
# 0.1 is a JUDGEMENT weight, not a measured one. The two-phase corpus
# (tests/fixtures/calibration/phase1-2-corpus.json) contains ZERO entries in
# the population this weight governs: every phase-1 entry has an INCOMPLETE
# functional axis (which routes to the byte-identical pre-recalibration
# formula) and every phase-2 entry scored 1.0 with no NFR gap at all. The
# corpus proves the recalibration does not regress the recorded history; it
# holds no counter-example to the discount and no evidence for it either.
# Capture entries of the shape "functional complete + an uncovered
# non-critical NFR axis" before treating 0.1 as calibrated.
GAP_WEIGHT_FUNCTIONAL = 0.5
GAP_WEIGHT_CRITICAL_NFR = 0.5
GAP_WEIGHT_NFR_DISCOUNTED = 0.1

# Score-side weights when the functional axis is complete: functional 1.0,
# each applicable critical NFR axis 1.0, each non-critical axis 0.1. When
# functional is INCOMPLETE the pre-recalibration formula applies unchanged
# (0.5*functional + 0.5*nfr-coverage) — no discount of any kind.
AXIS_WEIGHT_CRITICAL = 1.0
AXIS_WEIGHT_NONCRITICAL = 0.1
# A requirement with an uncovered critical axis can never reach the 0.6
# non-blocking threshold: hard cap just below it.
CRITICAL_GAP_SCORE_CAP = 0.5


def kb_dir(ws: str) -> str:
    # External per-project store, not the repo — see taskplane_lite.kb_root.
    return tp.kb_root(ws)


def _index_path(ws: str) -> str:
    return os.path.join(kb_dir(ws), "index.json")


def load_index(ws: str) -> dict:
    # tp.load_json: missing -> fresh default, CORRUPT -> StateError with a
    # remedy — a torn/tampered index is never silently replaced (v2.3.0).
    idx = tp.load_json(_index_path(ws), default=None,
                       what="requirements/KB index")
    if idx is None:
        return {"decisions": [], "flows": [], "requirements": [], "debt": []}
    idx.setdefault("requirements", [])
    idx.setdefault("debt", [])
    return idx


def _save_index(ws: str, idx: dict) -> None:
    # Atomic (v2.3.0): this is the SAME index.json kb.record_decision guards
    # with flock + tmp/os.replace; a bare open('w') here let a parallel
    # wave's gate decision race a `tp req new` and drop entries (or tear the
    # file for every concurrent reader).
    os.makedirs(kb_dir(ws), exist_ok=True)
    tp.atomic_write_json(_index_path(ws), idx, indent=2)


@contextlib.contextmanager
def _index_lock(ws: str):
    """Serialize index read-modify-write. tp.file_lock locks
    <kb>/index.json.lock — the SAME lock file kb.mutate flocks — so a gate's
    record_decision and a requirements/debt write serialize on one lock, and
    two concurrent `req new` can no longer mint the same R-id. Never
    silently lock-free (mkdir fallback + StateError on flock-less hosts)."""
    os.makedirs(kb_dir(ws), exist_ok=True)
    with tp.file_lock(_index_path(ws)):
        yield


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "req"


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------- record

def record_requirement(ws: str, title: str, *, functional=None, nfr=None,
                       acceptance=None, open_questions=None,
                       status: str = "draft", tags=None, context_files=None,
                       links=None, changed_from: str | None = None,
                       depends_on=None, contracts=None,
                       date: str | None = None) -> dict:
    """Write a requirement record + index entry. Returns the entry.

    `nfr` is a dict keyed by NFR lens id, e.g. {"security": "no PII in logs"}.
    A change request is just a requirement with `changed_from` set — same store.
    """
    slug = _slug(title)
    links = dict(links or {})
    if changed_from:
        links["changed_from"] = changed_from
        status = "changed"
    entry = {
        "id": None,   # assigned under the index lock below
        "title": title,
        "status": status,
        "date": date or _today(),
        "tags": list(tags or []),
        "functional": list(functional or []),
        "nfr": dict(nfr or {}),
        "acceptance": list(acceptance or []),
        "open_questions": list(open_questions or []),
        "depends_on": list(depends_on or []),
        "contracts": list(contracts or []),
        "context_files": list(context_files or []),
        "links": links,
        "file": None,   # assigned with the id below
    }
    # Lock around the whole read-modify-write: id minting AND the append must
    # be atomic w.r.t. concurrent kb/requirements writers (v2.3.0).
    with _index_lock(ws):
        idx = load_index(ws)
        rid = f"R-{len(idx['requirements']) + 1:04d}"
        entry["id"] = rid
        entry["file"] = f"requirements/{rid}-{slug}.md"
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
- depends_on: {', '.join(entry['depends_on']) or '—'}
- contracts: {json.dumps(entry['contracts']) if entry['contracts'] else '—'}

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
    with _index_lock(ws):
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

    Recalibrated (B1, R-0008) against the two-phase calibration corpus
    (tests/fixtures/calibration/phase1-2-corpus.json): functional
    completeness dominates. When the functional axis is complete,
    non-critical NFR-coverage gaps are discounted; the risk-bearing axes
    (CRITICAL_NFR_LENSES) are NEVER discounted and cap the score below the
    non-blocking threshold. When the functional axis is incomplete, the
    pre-recalibration behavior applies unchanged — nothing warns less than
    it used to.
    """
    files = changed_files if changed_files is not None \
        else req.get("context_files", [])
    applicable = applicable_nfr_lenses(files, task_type=task_type,
                                       catalog=catalog)
    return score_axes(req, applicable)


def score_axes(req: dict, applicable) -> dict:
    """Pure scoring core: score `req` against an explicit list of applicable
    NFR axes (no router call). `score_refinement` routes then delegates
    here; the calibration corpus tests replay recorded axes through this
    directly. Same return shape as `score_refinement`."""
    gaps = []

    # ---- functional axis (unchanged)
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
    functional_complete = fpts == ftot

    # ---- nfr axis (gap detection unchanged; weighting recalibrated)
    applicable = list(applicable or [])
    stated = set(req.get("nfr", {}))
    covered = [lz for lz in applicable if lz in stated]
    for lz in applicable:
        if lz not in stated:
            gaps.append({"axis": "nfr", "lens": lz,
                         "detail": f"no {lz} NFR stated"})
    nfr = 1.0 if not applicable else len(covered) / len(applicable)

    if not functional_complete:
        # Pre-recalibration formula, byte-identical: an unresolved
        # functional shape is what actually predicts fix cycles, so no NFR
        # discount applies and nothing warns less than before.
        score = round(0.5 * functional + 0.5 * nfr, 2)
    else:
        # Functional axis complete: class-weighted coverage average.
        crit = [lz for lz in applicable if lz in CRITICAL_NFR_LENSES]
        noncrit = [lz for lz in applicable if lz not in CRITICAL_NFR_LENSES]
        num = 1.0  # the complete functional axis
        den = 1.0
        for lz in crit:
            den += AXIS_WEIGHT_CRITICAL
            if lz in stated:
                num += AXIS_WEIGHT_CRITICAL
        for lz in noncrit:
            den += AXIS_WEIGHT_NONCRITICAL
            if lz in stated:
                num += AXIS_WEIGHT_NONCRITICAL
        score = num / den
        if any(lz not in stated for lz in crit):
            # a risk-bearing (CRITICAL_NFR_LENSES) gap: never scores
            # at/above threshold, no matter how many covered axes pad the
            # average.
            score = min(score, CRITICAL_GAP_SCORE_CAP)
        score = round(score, 2)

    return {
        "score": score,
        "functional": round(functional, 2),
        "nfr": round(nfr, 2),
        "applicable_nfr": applicable,
        "covered_nfr": covered,
        "gaps": gaps,
        "forecast": forecast(gaps, functional_complete),
    }


def _functional_complete_from(gaps) -> bool:
    return not any(g.get("axis") == "functional" for g in gaps)


def forecast_detail(gaps, functional_complete=None) -> dict:
    """Structured iteration forecast: {friction, cycles, note}.

    Class-weighted (B1): functional gaps 0.5 cycles each; non-critical NFR
    gaps 0.1 each ONLY when the functional axis is complete (else 0.5);
    risk-bearing NFR gaps (CRITICAL_NFR_LENSES) always 0.5 — never
    discounted. `friction`
    is the expected fix cycles as a float; `cycles` rounds it half-up, which
    reproduces the old (n+1)//2 whenever no discount applies. When
    `functional_complete` is omitted it is inferred from the gap list."""
    if functional_complete is None:
        functional_complete = _functional_complete_from(gaps)
    friction = 0.0
    for g in gaps:
        if g.get("axis") == "functional":
            friction += GAP_WEIGHT_FUNCTIONAL
        elif g.get("lens") in CRITICAL_NFR_LENSES:
            friction += GAP_WEIGHT_CRITICAL_NFR
        elif functional_complete:
            friction += GAP_WEIGHT_NFR_DISCOUNTED
        else:
            friction += GAP_WEIGHT_FUNCTIONAL
    cycles = int(friction + 0.5)
    if not gaps:
        note = "refined"
    elif cycles == 0:
        note = ("non-critical NFR gaps on a functionally-complete "
                "requirement — low risk, state them for the record")
    else:
        note = "unresolved gaps tend to cost a fix cycle each"
    return {"friction": round(friction, 2), "cycles": cycles, "note": note}


def forecast(gaps, functional_complete=None) -> str:
    """Iteration forecast string, derived from `forecast_detail`."""
    n = len(gaps)
    if n == 0:
        return "refined — expect near straight-through build (0 fix cycles)"
    d = forecast_detail(gaps, functional_complete)
    if d["cycles"] == 0:
        return (f"{n} gap(s) → expect ~0 fix cycles ({d['note']})")
    return (f"{n} gap(s) → expect ~{d['cycles']} fix cycle(s) if built "
            "as-is; refining now is cheaper than discovering these mid-build")


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
    slug = _slug(title)
    entry = {
        "id": None,   # assigned under the index lock below
        "title": title,
        "status": "open",
        "date": date or _today(),
        "requirement_id": requirement_id,
        "reason": reason,
        "follow_up": follow_up,
        "tags": list(tags or []),
        "context_files": list(context_files or []),
        "file": None,
    }
    with _index_lock(ws):
        idx = load_index(ws)
        did = f"D-{len(idx['debt']) + 1:04d}"
        entry["id"] = did
        entry["file"] = f"debt/{did}-{slug}.md"
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
    with _index_lock(ws):
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
        if r.get("depends_on"):
            lines.append("    depends on: " + ", ".join(r["depends_on"]))
        if r.get("contracts"):
            labels = [c.get("relation", "uses") + ":" + c.get("id", "?")
                      if isinstance(c, dict) else str(c)
                      for c in r["contracts"]]
            lines.append("    contracts: " + ", ".join(labels))
    lines.append("Every task must trace to a requirement id; honor acceptance "
                 "criteria as the DoD.")
    return "\n".join(lines)
