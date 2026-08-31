"""Audit sweep cadence + router-regression auto-filing — owned by taskplane.

Extracted VERBATIM from loop.py (R-0006 / D-0004, v3 Phase 2): this module
owns the audit machinery — the persistent completed-em-review counter, the
every-Nth/release/corrupt-state audit_due rule, the em brief's audit block
(with the recorded routing decision), the findings-vs-routing diff
(router_audit), and the em-gate half that auto-files n/a-lens findings as
router regressions into findings.json and blocks sign-off on them.

Behavior is protected directly by taskplane/tests/test_audit_sweep.py: a
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


def audit_every(*, authority: dict | None = None) -> int:
    """The audit cadence N: every Nth em review is a full audit sweep.
    TASKPLANE_AUDIT_EVERY overrides the default of 5; a floor of 1 is
    enforced (N=1 audits every review); garbage falls back to the default —
    a typo must not silently disable the audit backstop."""
    from taskplane.settings import load_settings
    return load_settings(
        environment=os.environ, authority=authority).runtime.audit_every


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
            # No/unknown lens attribution: NOT a computable routing diff, so
            # not a regression. A5 (R-0007) surfaces these as WARN rows on
            # the GATE path (_unattributed_rows, filed by _router_audit_gate)
            # — this function's return for such inputs is byte-frozen by the
            # differential corpus (scenario 'router-audit'/'ignored') and
            # must keep skipping them here.
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
            # R-0013: the machinery files a BLOCKING row, so it owes the
            # same defect claim it demands of a human reviewer. Exempting
            # the engine's own output would be an exemption — and an
            # exemption is exactly how the A5 warn-row spoof got in.
            "claim": {
                "trigger": (f"route this diff, read lens '{lid}' as n/a, "
                            "then review the same diff with it applied"),
                "outcome": (f"lens '{lid}' yields a real finding the router "
                            "said could not apply, so the routing decision "
                            "under-covered this change"),
                "repro": ("tp lens route --base <baseline>, then compare the "
                          f"'{lid}' verdict against this finding: "
                          + str(f.get("title") or "the attached finding")),
            },
            "finding": dict(f),
        })
    return out


def _unattributed_rows(routing_decision, findings) -> list:
    """A5 (R-0007): findings with NO lens attribution, or attributed to a
    lens the routing decision does not know, used to be silently dropped by
    `router_audit` (the skip above) — omitting `lens` was an evasion channel
    around the router regression backstop. Convert each into a WARN row —
    the approved Design Contract's shape (contract:findings-v2, A5), exactly:

      severity PRESERVED      — safe even for 'high': finding_blocks checks
                                class BEFORE severity, and 'observation'
                                never blocks under the frozen v2.3.1 rule.
                                The row is a DUPLICATE view of a defect that
                                is already a row of its own, so the human
                                consumer must not count it twice: the
                                findings renderer buckets a warn row whose
                                nested original is present in the same set
                                as an advisory note that still NAMES the
                                underlying severity (dashboard.
                                _advisory_rows / _row_sev_info). Severity
                                stays preserved HERE — the shape is part of
                                contract:findings-v2 and the em gate reads
                                it — the double-count is fixed where it was
                                introduced, in the rendering.
      class    'observation'  — the underlying finding stays in the findings
                                set and gates normally on its own
      owner    'router'       — the machinery owner; warn rows stay out of
                                the regression path because
                                _is_router_regression ALSO requires class
                                'regression', which these never carry
      warn     True           — the discriminator between warn rows and
                                auto-filed router regressions
      domain   'router+unattributed' | 'router+unknown:<lens>'

    The frozen differential corpus pins router_audit's own return for
    unattributed inputs (scenario 'router-audit'/'ignored'), so the
    surfacing lives HERE and is filed by _router_audit_gate — the one path
    the corpus does not exercise with unattributed findings."""
    out = []
    decision = routing_decision if isinstance(routing_decision, dict) else {}
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        if f.get("owner") == "router":
            continue        # machinery-authored rows are never re-audited
        lid = str(f.get("lens") or f.get("domain") or "").strip()
        if lid and lid in decision:
            continue        # attributed + known → router_audit's territory
        out.append({
            "severity": f.get("severity"),
            "class": "observation",
            "owner": "router",
            "warn": True,
            "domain": ("router+unknown:" + lid) if lid
                      else "router+unattributed",
            "title": ("unattributed finding: "
                      + (f"lens '{lid}' is not in the recorded routing "
                         "decision" if lid else "no lens attribution")
                      + " — attribute every finding to a catalog lens so "
                        "the router audit can diff it"),
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


def _is_machinery_warn_row(f) -> bool:
    """The machinery warn-row SHAPE (A5) — warn flag, machinery owner,
    non-blocking class, router+ domain, nested original finding.

    Shape is NECESSARY but NOT SUFFICIENT for the em gate's unresolved-high
    exemption: every one of these five fields lives in worker-authored
    .em-review/findings.json, so a real blocker can be dressed in the
    costume. `_machinery_warn_exempt` pairs this with membership in the
    freshly RE-DERIVED legitimate set (`_machinery_warn_keys`); nothing in
    the gate path may exempt on shape alone."""
    return (isinstance(f, dict) and f.get("warn") is True
            and f.get("owner") == "router"
            and _loop().normalize_finding_class(f.get("class"))
            == "observation"
            and str(f.get("domain") or "").startswith("router+")
            and isinstance(f.get("finding"), dict))


def _router_regression_key(r):
    nested = r.get("finding") if isinstance(r.get("finding"), dict) else {}
    return (r.get("domain"), nested.get("title"), nested.get("file"),
            nested.get("line"))


def _machinery_warn_rows(meta, rows) -> list:
    """The warn ROWS the machinery ITSELF would file for THIS findings set,
    re-derived at gate time from the recorded routing decision + the findings
    on disk — the same `_unattributed_rows` call `_router_audit_gate` files
    from, returned whole (not reduced to a key).

    This is the anti-spoof half of A5: a row wearing the machinery costume
    that does NOT correspond to a genuinely unattributed finding is not in
    this set and therefore still hits the v2.3.0 unresolved-high backstop.
    Returning ROWS (rather than the `_router_regression_key` identities the
    first cut used) is what closes the residual channel — see
    `_machinery_warn_exempt`."""
    decision = _routing_decision_from_meta(meta)
    if not decision:
        return []               # no routing recorded → no legitimate rows
    dict_rows = [r for r in rows if isinstance(r, dict)]
    return _unattributed_rows(decision, dict_rows)


def _machinery_warn_matches(f, derived) -> bool:
    """Is `f` the row `derived` — every field the machinery AUTHORS equal?

    The nested original is compared by its `_router_regression_key` identity
    rather than field-for-field: the filed row carries a SNAPSHOT of the
    original taken when the gate first ran, and a later triage edit to the
    original (status: resolved, an added fix note) must not turn a genuine
    machinery row into a blocker nobody can clear. Every field the machinery
    decides — severity, class, owner, warn, domain, title — must match
    exactly."""
    if _router_regression_key(f) != _router_regression_key(derived):
        return False
    return all(f.get(k) == v for k, v in derived.items() if k != "finding")


def _machinery_warn_exempt(f, legit) -> bool:
    """Exempt from the unresolved-high sweep ONLY when the row both wears the
    machinery shape AND IS a row the machinery would file right now.

    Membership used to be keyed on `_router_regression_key` alone — (domain,
    nested title/file/line) — which a forged row could simply COPY from a
    genuine one while carrying `severity: blocker, status: open` of its own,
    riding a real unattributed finding's key past the backstop. The whole
    re-derived row is compared now, so the exemption requires the row to BE
    the machinery's own output: a forgery that reproduces it is a duplicate
    of a legitimate row, and cannot inflate its severity past the original's
    (which still blocks on its own row)."""
    return (_is_machinery_warn_row(f)
            and any(_machinery_warn_matches(f, d) for d in legit))


def _unresolved_high_errors(meta, rows) -> list:
    """The em gate's raw unresolved-high sweep (v2.3.0): unknown severities
    normalize UP to high and BLOCK. Extracted from loop.py so the A5
    exemption can be re-derived here rather than trusted from the file;
    the gate math itself is still loop's `normalize_severity`, called
    late-bound (never reimplemented)."""
    exempt = _machinery_warn_rows(meta, rows)
    errors = []
    for finding in rows:
        if (isinstance(finding, dict)
                and not _machinery_warn_exempt(finding, exempt)
                and _loop().normalize_severity(
                    finding.get("severity")) == "high"
                and str(finding.get("status", "open")).lower()
                not in ("resolved", "accepted", "closed")):
            errors.append("engineering review has an unresolved "
                          f"{finding.get('severity') or 'unclassified'} "
                          f"finding: {finding.get('title', 'untitled')}")
    return errors


def _blocking_claim_errors(ws: str, state, rows) -> list:
    """R-0013: a finding may block the em gate only if it says what breaks.

    Lives here because audit.py owns the em-gate evidence half. Purely
    additive — it never un-blocks anything, and the frozen `finding_blocks`
    rule (passed in, never reimplemented) still decides WHICH findings block.
    A review that mixes commentary into the blocking set trains everyone to
    skim it, which is how a genuinely inert guardrail (A4) sat in the same
    pile as a note about ring geometry."""
    import defect_claim
    loop = _loop()
    changed = [f for f in loop._diff_files(
        ws, (state or {}).get("baseline") or "HEAD")
        if not f.startswith(loop.lens_router.LOOP_OWNED)]
    return defect_claim.blocking_errors(
        rows, lambda f: loop.finding_blocks(f, changed))


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
    # A5 (R-0007): surface unattributed/unknown-lens findings as WARN rows —
    # appended idempotently (same file discipline as the regressions above),
    # traced, never blocking. When every finding is attributed (the entire
    # frozen corpus), this is a no-op: no write, no trace, bytes unchanged.
    # Dedup via the EXISTING key mechanism (_router_regression_key — the
    # same (domain, nested title/file/line) tuple); warn rows never collide
    # with regression rows because their domains are disjoint.
    warn = _unattributed_rows(decision, dict_rows)
    seen = {_router_regression_key(r) for r in dict_rows if r.get("warn")}
    fresh_warn = [r for r in warn if _router_regression_key(r) not in seen]
    if fresh_warn:
        rows.extend(fresh_warn)
        doc["findings"] = rows
        tp.atomic_write_json(path, doc, indent=2)
        tp.trace(ws, "router_audit_unattributed", count=len(fresh_warn),
                 lenses=sorted({r["domain"] for r in fresh_warn}))
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
