"""Audit sweep cadence + router-regression auto-filing — owned by taskplane.

Extracted VERBATIM from loop.py (R-0006 / D-0004, v3 Phase 2): this module
owns the audit machinery — the persistent completed-em-review counter, the
every-Nth/release/corrupt-state audit_due rule, the em brief's audit block
(with the recorded routing decision), the findings-vs-routing diff
(router_audit), and the em-gate half that auto-files n/a-lens findings as
router regressions into findings.json and blocks sign-off on them.

Behavior is BYTE-FROZEN by taskplane/tests/test_audit_extraction.py: a
differential corpus captured from the pre-extraction loop.py replays against
this module and must produce identical gate error lists, findings.json
bytes, and trace event names. No guardrail here may loosen.

loop.py re-exports every public and underscore name below, so existing
callers (and monkeypatching tests) keep resolving them at loop.<name>; the
gate math itself still lives with the frozen `finding_blocks` rule in
loop.py — this module CALLS it (late-bound, never a reimplementation).

Routed reviews save tokens only if skipping stays HONEST: every Nth em
review (default 5, TASKPLANE_AUDIT_EVERY overridable, min 1) — plus any
review flagged as a release review — runs as a full-catalog AUDIT. The
audit's merged findings are diffed against the recorded routing decision;
a finding attributable to a lens the router marked n/a is a detector miss
and is AUTO-FILED into the findings set as a router regression
(class: regression, owner: router) — which blocks the gate through the
frozen v2.3.1 `finding_blocks` rule, with no guardrail change.
"""

from __future__ import annotations

import os

import lens as lens_router
import taskplane_lite as tp

AUDIT_FILE = "audit.json"
AUDIT_EVERY_DEFAULT = 5


def _loop():
    """The engine module, imported LAZILY and looked up at call time.

    loop.py imports this module at load time (to re-export the audit
    names), so a module-level back-import would be circular. Late binding
    also keeps the seam honest to loop.py's ownership: `finding_blocks`,
    `normalize_finding_class`, `load` and `_state_dir` are resolved on
    loop.py at every call, so a monkeypatched loop.<name> still governs the
    audit path exactly as it did pre-extraction."""
    import loop
    return loop


def _audit_path(ws: str) -> str:
    # Lives beside loop.json: cadence is per-user coordination state, and
    # state_dir() owns that location rule (v2.3.0).
    return os.path.join(_loop()._state_dir(ws), AUDIT_FILE)


def audit_every() -> int:
    """The audit cadence N: every Nth em review is a full audit sweep.
    TASKPLANE_AUDIT_EVERY overrides the default of 5; a floor of 1 is
    enforced (N=1 audits every review); garbage falls back to the default —
    a typo must not silently disable the audit backstop."""
    raw = str(os.environ.get("TASKPLANE_AUDIT_EVERY") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return AUDIT_EVERY_DEFAULT


def audit_counter(ws: str) -> int:
    """Completed em reviews so far. Missing file → 0; a CORRUPT file raises
    tp.StateError (fail closed with a remedy — never a silent reset), while
    `audit_due` treats that unreadable state as 'audit now' (fail toward
    MORE coverage, the hub_signal precedent)."""
    path = _audit_path(ws)
    doc = tp.load_json(path, default=None, what="audit cadence state") or {}
    try:
        return max(0, int(doc.get("reviews") or 0))
    except (TypeError, ValueError):
        raise tp.StateError(path, "corrupt audit cadence state",
                            "delete it to reset the audit counter") from None


def record_audit_review(ws: str) -> int:
    """Increment the persistent review counter for one COMPLETED em review.

    Atomic-write discipline (tp.atomic_write_json) under the shared file
    lock: a concurrent reader only ever sees a complete counter, and a crash
    mid-write leaves the previous value intact — never a torn file."""
    path = _audit_path(ws)
    with tp.file_lock(path):
        try:
            n = audit_counter(ws)
        except tp.StateError:
            n = 0     # corrupt cadence state: reset rather than stall sign-off
        n += 1
        tp.atomic_write_json(path, {"reviews": n}, indent=2)
    return n


def _release_review_flagged(state) -> bool:
    """A release review always audits: state marker (release_review/release)
    or any task flagged release (marker or type)."""
    if not isinstance(state, dict):
        return False
    if state.get("release_review") or state.get("release"):
        return True
    return any(isinstance(t, dict)
               and (t.get("release") or t.get("type") == "release")
               for t in state.get("tasks") or [])


def audit_due(ws: str, state: dict | None = None) -> bool:
    """Is the UPCOMING em review an audit? True every Nth completed review
    (default 5), on a release flag, or when the cadence state is unreadable
    (fail toward more coverage, never less)."""
    if state is None:
        try:
            state = _loop().load(ws)
        except Exception:
            state = None
    if _release_review_flagged(state):
        return True
    try:
        completed = audit_counter(ws)
    except tp.StateError:
        return True
    return (completed + 1) % audit_every() == 0


def router_audit(ws: str, routing_decision, findings) -> list:
    """Diff a breadth=all review's findings against the routing decision.

    Every finding attributable (via its `lens`/`domain` field) to a lens the
    router marked n/a is converted into an auto-filed router regression:
    severity preserved, class regression, owner router, the original finding
    nested. Findings from deep/light lenses — the router predicted those —
    are left alone. Accepts both the v2 decision shape
    ({lens: {verdict, ...}}) and plain string verdicts."""
    out = []
    decision = routing_decision if isinstance(routing_decision, dict) else {}
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        lid = str(f.get("lens") or f.get("domain") or "").strip()
        if not lid or lid not in decision:
            continue
        v = decision.get(lid)
        verdict = str((v.get("verdict") if isinstance(v, dict) else v)
                      or "").strip().lower()
        if verdict not in ("n/a", "na"):
            continue
        out.append({
            "severity": f.get("severity"),
            "class": "regression",
            "owner": "router",
            "domain": "router+" + lid,
            "title": (f"router regression: n/a lens '{lid}' produced a "
                      "finding — detector missed a real signal"),
            "finding": dict(f),
        })
    return out


def _routing_decision_from_meta(meta) -> dict:
    """The recorded per-lens routing decision, from meta.routing_decision or
    a v2-shaped meta.lens_coverage (contract:findings-v2: values carry
    {verdict, score, evidence|negative_evidence})."""
    d = (meta or {}).get("routing_decision")
    if isinstance(d, dict) and d:
        return d
    cov = (meta or {}).get("lens_coverage")
    if isinstance(cov, dict) and any(isinstance(v, dict) and "verdict" in v
                                     for v in cov.values()):
        return {k: v for k, v in cov.items() if isinstance(v, dict)}
    return {}


def _is_router_regression(f) -> bool:
    return (isinstance(f, dict) and f.get("owner") == "router"
            and _loop().normalize_finding_class(f.get("class"))
            == "regression")


def _router_regression_key(r):
    nested = r.get("finding") if isinstance(r.get("finding"), dict) else {}
    return (r.get("domain"), nested.get("title"), nested.get("file"),
            nested.get("line"))


def _router_audit_gate(ws: str, path: str, doc: dict, meta, rows) -> list:
    """The em-gate half of the audit loop: auto-file n/a-lens findings as
    router regressions APPENDED into findings.json (atomic write; idempotent
    across gate re-runs), then block on each unresolved one via the frozen
    finding_blocks rule."""
    decision = _routing_decision_from_meta(meta)
    if not decision:
        return []                       # no routing recorded → no diff to run
    dict_rows = [r for r in rows if isinstance(r, dict)]
    auto = router_audit(ws, decision, dict_rows)
    existing = {_router_regression_key(r) for r in dict_rows
                if _is_router_regression(r)}
    fresh = [r for r in auto if _router_regression_key(r) not in existing]
    if fresh:
        rows.extend(fresh)
        doc["findings"] = rows
        tp.atomic_write_json(path, doc, indent=2)
        tp.trace(ws, "router_regression_filed", count=len(fresh),
                 lenses=sorted({r["domain"] for r in fresh}))
    errs = []
    for r in rows:
        if not _is_router_regression(r):
            continue
        if str(r.get("status", "open")).lower() in ("resolved", "accepted",
                                                    "closed"):
            continue
        # class regression → finding_blocks is True by the frozen v2.3.1
        # rule; the CALL (not a reimplementation) keeps the gate math
        # single-sourced.
        if _loop().finding_blocks(r):
            errs.append("router regression blocks sign-off: "
                        + str(r.get("title") or "untitled"))
    return errs


def _routing_decision_of(routing) -> dict | None:
    """Extract the per-lens decision object from a v2 routing (verdict-
    carrying entries), the same shape lens.dispatch_briefs records:
    {lens: {verdict, score, evidence|negative_evidence}}. None for legacy
    routings (no verdicts → no diff is computable)."""
    lenses = (routing or {}).get("lenses") or []
    if not any("verdict" in x for x in lenses):
        return None
    decision = {}
    for x in lenses:
        d = {"verdict": x.get("verdict", x.get("tier")),
             "score": x.get("score")}
        if x.get("tier") == "n/a":
            d["negative_evidence"] = list(
                x.get("negative_evidence") or x.get("reasons") or [])
        else:
            d["evidence"] = list(x.get("evidence") or x.get("reasons") or [])
        decision[x["id"]] = d
    return decision


def _audit_brief(ws: str, state: dict | None) -> dict:
    """The em brief's audit block: whether the upcoming review is an audit,
    why, and — when due — the recorded routing decision (stage='review'
    signal routing) so the findings-vs-routing diff is computable at the
    gate. The em review itself KEEPS breadth=all (the full-catalog mandate
    is unchanged); audit mode adds the decision recording + auto-filing."""
    every = audit_every()
    release = _release_review_flagged(state)
    try:
        completed = audit_counter(ws)
        broken = False
    except tp.StateError:
        completed, broken = None, True
    due = release or broken or ((completed + 1) % every == 0)
    if release:
        reason = "release review"
    elif broken:
        reason = ("audit cadence state unreadable — auditing "
                  "(fail toward more coverage)")
    elif due:
        reason = f"every-{every}th (n={completed + 1})"
    else:
        nxt = -(-(completed + 1) // every) * every     # next multiple of N
        reason = (f"not due (n={completed + 1}; next audit at review {nxt})")
    info = {"due": due, "reason": reason, "every": every,
            "reviews_completed": completed}
    if due:
        info["mandate"] = (
            "AUDIT review: run the full catalog (breadth=all — the standing "
            "em mandate) and record the routing decision below in "
            ".em-review/findings.json meta (meta.routing_decision, or v2 "
            "meta.lens_coverage) so any finding from an n/a-routed lens is "
            "auto-filed as a router regression at the gate.")
        try:
            shadow = lens_router.route_git_diff(
                ws, base=(state or {}).get("baseline") or "HEAD",
                breadth="routed", stage="review")
            decision = _routing_decision_of(shadow)
            if decision:
                info["routing_decision"] = decision
        except Exception as exc:      # noqa: BLE001 — brief must still render
            info["routing_decision_error"] = str(exc)
    return info
