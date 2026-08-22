"""Compatibility facade for the lower-owned lens applicability engine.

The deterministic detector corpus lives in ``graph_primitives`` so fresh
direct decomposition calls need no higher-layer import or activation.  This
module composes review-only document evidence and retains the established
lens-signals API, including its test/embedding monkeypatch seam.
"""
from __future__ import annotations

import sys
import types

import graph_primitives as _engine
import review_progression

graph_primitives = _engine

# Re-export the lower engine's established constants, tables, and helpers.
# The two composition functions below deliberately override their lower
# counterparts so review-only evidence remains owned by the higher layer.
for _name in dir(_engine):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_engine, _name)

_LOWER_ROUTE_VERDICTS = _engine.route_verdicts


def make_ctx(workspace, files, requirement_text=None, graph=None,
             stage=None, content_by_file=None) -> Ctx:
    """Build the lower context with the higher review evidence provider."""
    if graph is None:
        graph = _graph_payload(workspace, files)
    return Ctx(workspace, files, requirement_text, graph, stage,
               content_by_file=content_by_file,
               review_progression=review_progression)


def route_verdicts(workspace, files, stage=None, requirement_text=None,
                   graph=None, content_by_file=None) -> dict:
    """Route through the lower engine with explicit review composition."""
    cat = load_catalog()
    ctx = make_ctx(workspace, files, requirement_text=requirement_text,
                   graph=graph, stage=stage,
                   content_by_file=content_by_file)
    vmap = verdicts([lens["id"] for lens in cat["lenses"]], ctx,
                    floors=False)
    if stage == "review":
        review_progression.apply_document_signals(
            vmap, files, content_by_file)
    return apply_budget(vmap, cap=DEEP_CAP, target=DEEP_TARGET, ctx=ctx)


_FACADE_ROUTE_VERDICTS = route_verdicts

# ---------------------- routed-audit hybrid MEASUREMENT (R-0006, D-0003)
#
# MEASUREMENT ONLY — audit execution is unchanged this phase either way
# (the em step's full-catalog breadth="all" mandate is untouched). This
# harness compares, on a frozen dogfood/replay corpus of this repo's own
# diffs, the CURRENT full audit shape (breadth="all": routed lenses deep +
# one full-catalog sweep brief) against the HYBRID shape (routed deep +
# light batch + ONE batched negative-evidence verification sweep over the
# n/a lenses). Token proxy: prompt BYTES of the dispatched briefs. Escaped
# finding: a corpus finding (from a real em review, lens-attributed) whose
# lens the routed path marks n/a — the exact coverage the hybrid would
# have skipped. Adoption bar (default DECLINE unless met): token reduction
# >= 30% AND zero escaped findings; the outcome is RECORDED as a decision,
# never flipped into behavior here.

HYBRID_BAR = {"min_token_reduction_pct": 30.0, "max_escaped_findings": 0}


def hybrid_verdict(token_reduction_pct: float, escaped_findings: int) -> str:
    """The D-0003 adoption bar. Default DECLINE unless the bar is met."""
    if (token_reduction_pct >= HYBRID_BAR["min_token_reduction_pct"]
            and escaped_findings <= HYBRID_BAR["max_escaped_findings"]):
        return "adopt"
    return "decline"


def _prompt_bytes(payload: dict) -> int:
    """Token proxy for one dispatch payload: UTF-8 bytes of every prompt
    that would actually be sent (deep briefs + the batched sweep brief)."""
    total = 0
    for b in payload.get("deep") or []:
        total += len((b.get("prompt") or "").encode("utf-8"))
    sw = payload.get("sweep")
    if sw:
        total += len((sw.get("prompt") or "").encode("utf-8"))
    return total


def verification_brief_prompt(decision: dict) -> str:
    """The hybrid's ONE batched negative-evidence verification sweep: a
    single prompt asking a cheap agent to verify each n/a lens's
    negative-evidence claims against the diff (never a deep review)."""
    nas = [(lid, list(d.get("negative_evidence") or []))
           for lid, d in sorted((decision or {}).items())
           if d.get("verdict") == "n/a"]
    if not nas:
        return ""
    lines = ["Batched NEGATIVE-EVIDENCE VERIFICATION sweep (routed-audit "
             "hybrid): for each n/a-routed lens below, verify its "
             "negative-evidence claims against the diff — flag any claim "
             "the diff contradicts, one line per lens. READ-ONLY."]
    for lid, claims in nas:
        lines.append(f"- {lid}: " + ("; ".join(claims) or "no claims"))
    return "\n".join(lines) + "\n"


def measure_audit_hybrid(corpus_entries, workspace=None, base: str = "HEAD",
                         stage: str = "review") -> dict:
    """Run the D-0003 comparison over corpus entries
    [{label, files, requirement_text, findings:[{lens, ...}]}] and return
    the `audit_hybrid_measured` event payload:
    {tokens_full, tokens_hybrid, token_reduction_pct, escaped_findings,
     verdict, bar, corpus_size, rows}. Deterministic for a frozen corpus.
    Imports lens at call time (lens imports THIS module at load time, so a
    module-level import would be a cycle)."""
    import lens  # noqa: runtime import — see docstring

    rows = []
    tokens_full = tokens_hybrid = escaped_total = 0
    for entry in corpus_entries:
        files = list(entry.get("files") or [])
        req = entry.get("requirement_text") or ""
        full = lens.dispatch_briefs(
            lens.route(files, breadth="all"), base=base)
        routed = lens.route(files, stage=stage, workspace=workspace,
                            requirement_text=req)
        hyb = lens.dispatch_briefs(routed, base=base)
        decision = hyb.get("routing_decision") or {}
        ftok = _prompt_bytes(full)
        htok = (_prompt_bytes(hyb)
                + len(verification_brief_prompt(decision).encode("utf-8")))
        na = {lid for lid, d in decision.items()
              if d.get("verdict") == "n/a"}
        esc = sorted({(f.get("lens") or "?") for f in
                      (entry.get("findings") or [])
                      if (f.get("lens") or "") in na})
        n_esc = sum(1 for f in (entry.get("findings") or [])
                    if (f.get("lens") or "") in na)
        tokens_full += ftok
        tokens_hybrid += htok
        escaped_total += n_esc
        rows.append({"label": entry.get("label", "?"),
                     "files": len(files), "tokens_full": ftok,
                     "tokens_hybrid": htok, "escaped_findings": n_esc,
                     "escaped_lenses": esc})
    pct = (round(100.0 * (1.0 - tokens_hybrid / tokens_full), 2)
           if tokens_full else 0.0)
    return {"event": "audit_hybrid_measured",
            "tokens_full": tokens_full,
            "tokens_hybrid": tokens_hybrid,
            "token_reduction_pct": pct,
            "escaped_findings": escaped_total,
            "verdict": hybrid_verdict(pct, escaped_total),
            "bar": dict(HYBRID_BAR),
            "corpus_size": len(rows),
            "rows": rows}


class _LensSignalsFacade(types.ModuleType):
    """Keep the historical assignment seam without a lower-layer registry.

    Existing fail-open tests and embedders assign ``lens_signals.route_verdicts``
    directly.  Forward only that assignment to the actual lower owner; lower
    modules never import, discover, or retain the higher facade.
    """

    def __setattr__(self, name, value):
        if name == "route_verdicts":
            _engine.route_verdicts = (
                _LOWER_ROUTE_VERDICTS
                if value is _FACADE_ROUTE_VERDICTS else value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if name == "route_verdicts":
            _engine.route_verdicts = _LOWER_ROUTE_VERDICTS
        super().__delattr__(name)


sys.modules[__name__].__class__ = _LensSignalsFacade
