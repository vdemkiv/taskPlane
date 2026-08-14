"""Lens router — context decides which perspectives apply, and how each runs.

A lens is a perspective specification (in lenses/catalog.json). This module
answers two questions deterministically and explainably, so you never name a
role: given the changed files (+ optional task type), (a) WHICH lenses apply,
with a reason for each, and (b) HOW each runs — `inline` (cheap, default) or a
dedicated governed `subagent` (for high-stakes / large changes).

Design: baselines always run on any code change; a lens also fires on a glob
or task-type match; the mode escalates to `subagent` when a lens's deep-globs
are touched or the change is large. Pure stdlib.
"""

from __future__ import annotations

import fnmatch
import json
import os

import taskplane_lite as tp
from path_roles import change_adds_no_test as _adds_no_test

_CATALOG_CACHE: dict | None = None

# Lenses whose judgement is worth a stronger model when the operator has
# configured a `deep` model; the rest of the deep-tier lenses run `standard`
# and the quick full-catalog sweep runs `cheap`. All resolve to inherit until
# an operator sets TASKPLANE_MODEL_* — see taskplane_lite.model_for_tier.
# Every entry MUST be a real lenses/catalog.json id (a drift test asserts
# this): v2.3.0 fixed the dead 'concurrency' entry — the catalog lens that
# owns that reasoning (idempotency, transactions, races at service
# boundaries) is `backend`.
_HARD_LENSES = {"security", "architecture", "scalability", "data-safety",
                "backend", "dba", "sre", "privacy-compliance"}


def _lens_tier(lens_id: str, brief_tier: str) -> str:
    """Capability tier for a lens brief: the quick sweep is `cheap`; a deep
    lens is `deep` for the hard-reasoning lenses, else `standard`."""
    if brief_tier == "sweep":
        return "cheap"
    return "deep" if lens_id in _HARD_LENSES else "standard"


def _plugin_root() -> str:
    # lenses/ sits at the plugin root, one level up from taskplane/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_catalog(root: str | None = None) -> dict:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and root is None:
        return _CATALOG_CACHE
    path = os.path.join(root or _plugin_root(), "lenses", "catalog.json")
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)
    if root is None:
        _CATALOG_CACHE = cat
    return cat


def _match(path: str, glob: str) -> bool:
    """Path/glob match supporting '**' as 'any directories'."""
    if fnmatch.fnmatch(path, glob):
        return True
    if glob.startswith("**/"):
        tail = glob[3:]
        if fnmatch.fnmatch(path, tail) or fnmatch.fnmatch(
                os.path.basename(path), tail):
            return True
        # match the tail against any suffix segment of the path
        parts = path.split("/")
        for i in range(len(parts)):
            if fnmatch.fnmatch("/".join(parts[i:]), tail):
                return True
    return False


def _any_match(files, globs) -> list:
    hits = []
    for g in globs or []:
        for fpath in files:
            if _match(fpath, g):
                hits.append((fpath, g))
                break
    return hits


def _is_code(path: str, code_ext) -> bool:
    return any(path.endswith(e) for e in code_ext)


# Signals that a change is architecturally significant (multi-service / infra /
# cross-cutting), so the architecture lens scales its effort to the task
# instead of over-analysing a simple client-server change.
_ARCH_SYSTEM_GLOBS = ["**/docker-compose*", "**/k8s/**", "**/*.tf",
                      "**/*.proto", "**/helm/**"]
_ARCH_BOUNDARY_GLOBS = ["**/api/**", "**/services/**", "**/architecture/**",
                        "**/adr/**", "**/*.arch.md", "**/interfaces/**"]
_ARCH_SYSTEM_TASKS = {"system-design", "distributed", "greenfield"}


# Graph hubness thresholds (v2.0.0): a change to a module that N other
# modules depend on IS an architectural event, whatever its path looks like.
_HUB_LIGHT = 3    # >= this many direct dependents -> at least a light pass
_HUB_FULL = 8     # >= this many -> a full design pass


# D-0005. The deep cap is a DISPATCH budget — how many lenses get their own
# subagent — and route v2 enforced it while the legacy path did not. That
# gap opened exactly where it hurts most: `breadth="all"` DISABLES route v2
# (see `route`), and `--all` is what a whole-codebase review runs. Every
# routed lens then saw `large` (a diff at or over deep_threshold_files) and
# became its own subagent, so a final review fanned out 26 deep agents under
# a cap of 8 — the one review where the budget was silently absent was the
# most expensive review the product performs.
#
# The cap DEMOTES, never drops, mirroring lens_signals.apply_budget: an
# over-budget lens still runs, inline, and says why it was demoted.
def _deep_cap() -> int:
    """The one cap, read from the engine that defines it.

    Deliberately not a second constant here. A local default would be a
    second reader of one number, which is the drift shape this codebase
    already carries in RUNTIME_OWNED vs LOOP_OWNED; if lens_signals cannot
    be imported the budget cannot be honoured, and the fail-safe direction
    for a review is MORE coverage, so the cap lifts rather than guessing.
    """
    try:
        import lens_signals
        return int(lens_signals.DEEP_CAP)
    except Exception:
        return 0          # 0 == no cap applied, and it is disclosed


def _cap_deep_dispatch(selected: list, cap: int) -> list:
    """Demote subagent-mode lenses past `cap` to inline, best evidence first.

    Ranked by how EXPLICIT the deep signal was: a lens whose own deep_globs
    matched the diff, or an architecture full pass, outranks one that is
    deep only because the diff happened to be large — which on a
    whole-codebase review is all of them. Ties break on evidence count and
    then catalog order, so the choice is deterministic.
    """
    if cap <= 0:
        return selected
    deep = [e for e in selected if e.get("mode") == "subagent"]
    if len(deep) <= cap:
        for e in selected:
            e.pop("_explicit_deep", None)
        return selected
    # Floors survive the budget, exactly as lens_signals applies them AFTER
    # apply_budget: an architecture FULL pass is defined by this module to
    # run as its own subagent, so the budget may not quietly take that away.
    # It still COUNTS against the cap — the budget is about total spend.
    exempt = [e for e in deep if e.get("effort") == "full"]
    order = {id(e): i for i, e in enumerate(selected)}
    ranked = sorted((e for e in deep if e not in exempt),
                    key=lambda e: (not e.get("_explicit_deep"),
                                   -len(e.get("reasons") or ()),
                                   order[id(e)]))
    cap = max(0, cap - len(exempt))
    for rank, e in enumerate(ranked, start=1):
        if rank > cap:
            e["mode"] = "inline"
            e["reasons"] = list(e.get("reasons") or []) + [
                f"budget: demoted subagent->inline (rank {rank} > deep cap "
                f"{cap}) — still reviewed, in the same pass"]
    for e in selected:
        e.pop("_explicit_deep", None)
    return selected


def hub_signal(workspace, files) -> int:
    """Max count of DIRECT dependents among the modules a change touches —
    the dependency graph's zero-token answer to "is this a hub?"."""
    try:
        import depgraph
        g = depgraph.load(workspace)
        if not g.get("modules"):
            return 0        # legitimately empty graph — no hub evidence
        rev = {}
        for e in g.get("edges") or []:
            rev.setdefault(e["to"], set()).add(e["from"])
        # resolve changed files the way the SCAN did: in a workspace the
        # graph's keys are the declared ids (@acme/ui), and a path-derived
        # `ui` matches nothing — the hub signal would silently read 0.
        _ids = depgraph.declared_module_ids(g)
        touched = {depgraph.module_of(f, _ids) for f in files or []}
        return max((len(rev.get(m, ())) for m in touched), default=0)
    except Exception as exc:
        # Fail toward MORE review coverage, never less — and never silently.
        # A corrupt graph (depgraph.load raises StateError) or a malformed
        # edge row used to return 0 here, quietly switching off the hub
        # escalation forever. Warn on stderr, trace it, and return the
        # full-pass threshold so architecture review escalates instead of
        # a broken graph shrinking the review.
        import sys
        print(f"taskplane: hub signal unavailable ({exc}) — escalating "
              "architecture review to a full pass (fail-safe: more review "
              "coverage, not less). Repair the dependency graph to restore "
              "precise hub routing.", file=sys.stderr)
        try:
            tp.trace(workspace, "hub_signal_failed", error=str(exc))
        except Exception:
            pass
        return _HUB_FULL


def architecture_effort(files, task_type, large: bool,
                        hub_dependents: int = 0) -> str:
    """How much architecture work THIS task warrants: skip | light | full.

    - full  : new/changed system shape — multi-service infra, distributed or
              greenfield task, a large structural change, OR a change to a
              heavy hub module (>= _HUB_FULL direct dependents).
    - light : touches a boundary/contract (API, service, interface, ADR) or
              a moderate hub (>= _HUB_LIGHT dependents).
    - skip  : no architectural signal (a localized change) — don't overthink it.

    The hub signal comes from the dependency graph (see hub_signal): a
    one-line edit to the module everything imports is an architecture
    review, whatever directory it lives in.
    """
    if (task_type in _ARCH_SYSTEM_TASKS or _any_match(files, _ARCH_SYSTEM_GLOBS)
            or (large and len({f.split("/")[0] for f in files}) >= 3)
            or hub_dependents >= _HUB_FULL):
        return "full"
    if _any_match(files, _ARCH_BOUNDARY_GLOBS) or hub_dependents >= _HUB_LIGHT:
        return "light"
    return "skip"


def route(changed_files, task_type: str | None = None,
          artifact_type: str | None = None, catalog: dict | None = None,
          only=None, skip=None, breadth: str = "routed",
          hub_dependents: int = 0, stage: str | None = None,
          use_signals: bool | None = None, workspace: str | None = None,
          requirement_text=None, content_by_file=None) -> dict:
    """Return the routing decision.

    {"lenses": [{id, name, mode, tier, reasons[], checks[], looks_for}],
     "context": {...}}  — deterministic and explainable.

    breadth="routed" (default): only the lenses the change summons.
    breadth="all": the FULL catalog — routed lenses run "deep" (their
    routed mode), and every other lens joins as a quick inline "sweep"
    pass, so a final review never misses a category the router didn't
    predict. (Motivated by dogfood: a routed-only review was narrower
    than an ungoverned full pass.)

    Route v2 (v3 Phase 1, opt-in, gated on DATA): when `stage` is given
    (or use_signals=True) AND the catalog carries a `stage_profiles` key,
    the candidate set is restricted to the stage's profile and per-lens
    verdicts (deep | light | n/a-with-negative-evidence) come from the
    deterministic applicability engine (lens_signals.route_verdicts:
    content + graph + requirement signals, budget cap 8, security /
    architecture floors). EVERY catalog lens appears in the output —
    n/a entries are included with their negative evidence (coverage
    honesty: the renderer needs them). With stage=None, use_signals=False,
    breadth="all", or no stage_profiles key: the explicit legacy/calibration
    path. A normal routed engine failure returns mapper_unavailable with zero
    lenses; uncertainty can never be recovered by full-catalog fan-out.

    Every exit RECORDS the breadth it decided (`_record_breadth`, event
    `lens_breadth`) into a governed workspace's trace, so `--all` is a fact
    on the record instead of something a consumer has to infer from the
    routed set. Recording only — see the block below this function.
    """
    cat = catalog or load_catalog()
    v2 = (use_signals is not False
          and isinstance(cat.get("stage_profiles"), dict)
          and breadth != "all"
          and (stage is not None or use_signals is True))
    if v2:
        try:
            routed = _route_v2(changed_files, cat, stage=stage,
                               task_type=task_type,
                               artifact_type=artifact_type, only=only,
                               skip=skip, hub_dependents=hub_dependents,
                               workspace=workspace,
                               requirement_text=requirement_text,
                               content_by_file=content_by_file)
        except Exception as exc:
            # R-0005: uncertainty is not recoverable with breadth. A broken
            # mapper has no valid 26-lens decision, so normal delivery must
            # dispatch ZERO rather than silently running all lenses.
            import sys
            print(f"taskplane: lens applicability engine unavailable "
                  f"({exc}) — mapper_unavailable; dispatching zero lenses. "
                  "Repair lens_signals and retry from the same envelope.",
                  file=sys.stderr)
            if workspace:
                try:
                    tp.trace(workspace, "lens_engine_failed",
                             error=str(exc), stage=stage)
                except Exception:
                    pass
            refused = {"lenses": [], "context": {
                "status": "mapper_unavailable", "breadth": "routed",
                "stage": stage, "lens_engine_failed": str(exc),
                "changed_files": len(list(changed_files or []))}}
            _record_breadth(workspace, requested=breadth, effective="routed",
                            engine_ran=False, stage=stage, routing=refused,
                            reason=f"mapper_unavailable: {exc}")
            return refused
        _record_breadth(workspace, requested=breadth, effective="routed",
                        engine_ran=True, stage=stage, routing=routed)
        return routed
    legacy = _route_legacy(changed_files, task_type, artifact_type, cat,
                           only, skip, breadth, hub_dependents)
    _record_breadth(workspace, requested=breadth, effective=breadth,
                    engine_ran=False, stage=stage, routing=legacy,
                    reason=_engine_off_reason(cat, breadth, stage,
                                              use_signals))
    return legacy


# ------------------------------------------------- recorded routing breadth
#
# WHICH lenses a review ran is traced (`lens_route`: step + lenses). WHY that
# set was chosen was not: `route` branches on `breadth != "all"` above, and
# `--all` — the flag that switches the applicability engine OFF — was decided
# here and then thrown away. The eval rubric scores exactly that distinction
# (forcing all 26 lenses is the "ignore the routing engine" behaviour the
# review layer exists to catch), so the recorder had to INFER it from the
# routed set: routed-set ⊇ catalog ⇒ "all".
#
# That inference cannot work, and not only at the edge: `_route_v2` emits an
# entry for EVERY catalog lens — n/a ones included, carrying their negative
# evidence, because coverage honesty needs them — so a signal-routed review's
# lens list IS the whole catalog and reads as `--all` every single time. The
# engine being on and the engine being off produced the same record.
#
# So the fact is recorded instead of guessed. RECORDING ONLY: nothing below
# is read back by routing, and `_record_breadth` swallows everything — a
# broken audit log must not narrow, widen or crash a review (the `trace`
# precedent in taskplane_lite). A differential test pins the returned
# routings, the dispatch briefs and the exception path byte-for-byte against
# the previous revision, including with `tp.trace` patched to raise.
LENS_BREADTH_EVENT = "lens_breadth"


def _engine_off_reason(cat, breadth, stage, use_signals) -> str:
    """Every reason the applicability engine did not run, in one line.

    All applicable causes, not the first: `--all` on a catalog that also has
    no stage_profiles is two different repairs, and a reader who is told only
    one of them fixes the wrong thing. `breadth="all"` leads because it is
    the operator-chosen cause and the one being scored.
    """
    why = []
    if breadth == "all":
        why.append("breadth='all' — the full-catalog sweep disables the "
                   "applicability engine")
    if use_signals is False:
        why.append("use_signals=False")
    if not isinstance(cat.get("stage_profiles"), dict):
        why.append("catalog carries no stage_profiles")
    if stage is None and use_signals is not True:
        why.append("no stage requested")
    return "; ".join(why) or "engine not engaged"


def _record_breadth(workspace, *, requested, effective, engine_ran, stage,
                    routing, reason=None) -> None:
    """Write the breadth decision to the workspace trace. Never raises.

    Fields, and what a consumer reads:
      requested_breadth  the caller's `breadth` argument, VERBATIM — "all"
                         means `--all` was passed, whatever routing then did.
      effective_breadth  the breadth the routing actually ran under; differs
                         from the request only on the fail-open path, where a
                         routed request is widened to the full catalog.
      engine_ran         True only when the applicability engine produced the
                         verdicts. "the engine ran and happened to select
                         everything" is engine_ran=True; "the engine was
                         switched off" is engine_ran=False. The two can never
                         collide, whatever the lens list looks like.
      engine_off_reason  present ONLY when engine_ran is False — which of the
                         several off-switches was thrown.
      stage, lens_count  join keys back to the loop's own `lens_route` row,
                         which this row is written immediately before.

    Writes ONLY into a workspace that already has a `.taskplane/` record.
    Routing is handed checked-in fixture directories as `workspace=` by the
    existing suites; creating a governance dir inside the repo as a side
    effect of reading a diff would be a new behaviour, not a new record.
    """
    if not workspace:
        return
    try:
        if not os.path.isdir(tp.tp_dir(workspace)):
            return
        row = {
            "requested_breadth": requested,
            "effective_breadth": effective,
            "engine_ran": bool(engine_ran),
            "stage": stage,
            "lens_count": len(routing.get("lenses") or []),
        }
        if not engine_ran:
            row["engine_off_reason"] = reason or "engine not engaged"
        tp.trace(workspace, LENS_BREADTH_EVENT, **row)
    except Exception:
        pass


def _route_legacy(changed_files, task_type, artifact_type, cat,
                  only, skip, breadth, hub_dependents) -> dict:
    """Today's glob/task-type/baseline/hub routing — the byte-identical
    legacy path (existing tests pin it)."""
    code_ext = cat.get("code_extensions", [])
    deep_n = cat.get("deep_threshold_files", 8)
    files = list(changed_files or [])
    has_code = any(_is_code(f, code_ext) for f in files)
    large = len(files) >= deep_n
    only = set(only or [])
    skip = set(skip or [])

    selected = []
    for lens in cat["lenses"]:
        lid = lens["id"]
        reasons = []

        gl = _any_match(files, lens.get("globs"))
        if gl:
            reasons.append(f"touches {gl[0][1]} ({gl[0][0]})")
        if task_type and task_type in (lens.get("task_types") or []):
            reasons.append(f"task type '{task_type}'")
        if artifact_type and artifact_type in (lens.get("artifact_types") or []):
            reasons.append(f"artifact '{artifact_type}'")
        baseline = lens.get("baseline")
        if baseline == "code" and has_code:
            reasons.append("baseline (any code change)")
        if lens.get("untested_trigger") and _adds_no_test(files, code_ext):
            reasons.append("untested change (code changed, no test file)")

        # The architecture lens scales effort to the task (light/full) but is
        # ALWAYS available on code changes — system design is governance-
        # critical, so the floor is a light pass, never a skip. Non-code
        # changes still route only via its globs (ADRs, architecture docs).
        effort = None
        if lid == "architecture":
            effort = architecture_effort(files, task_type, large,
                                         hub_dependents=hub_dependents)
            if hub_dependents >= _HUB_LIGHT:
                reasons.append(f"hub module: {hub_dependents} direct "
                               "dependents (dependency graph)")
            if effort == "skip":
                if not has_code:
                    continue
                effort = "light"   # governance floor: never skip on code
            if not reasons:
                reasons = [f"architectural signal ({effort})"
                           if effort == "full" else
                           "baseline (system design is always on)"]

        if not reasons:
            continue
        if lid in skip or (only and lid not in only):
            continue

        explicit = bool(_any_match(files, lens.get("deep_globs")))
        deep = explicit or large
        if effort == "full":
            deep = True   # a full design pass runs as its own subagent
            explicit = True
        entry = {
            "id": lid,
            "name": lens["name"],
            "mode": "subagent" if deep else "inline",
            "tier": "deep",
            "reasons": reasons,
            "checks": lens.get("checks", []),
            "looks_for": lens.get("looks_for", ""),
        }
        if effort:
            entry["effort"] = effort
        entry["_explicit_deep"] = explicit
        selected.append(entry)

    # D-0005: the dispatch budget, applied BEFORE the sweep so the sweep's
    # own inline entries never compete for it.
    cap = _deep_cap()
    selected = _cap_deep_dispatch(selected, cap)

    if breadth == "all":
        have = {e["id"] for e in selected}
        for lens in cat["lenses"]:
            lid = lens["id"]
            if lid in have or lid in skip or (only and lid not in only):
                continue
            selected.append({
                "id": lid,
                "name": lens["name"],
                "mode": "inline",
                "tier": "sweep",
                "reasons": ["full-catalog sweep — nothing skipped at "
                            "final review"],
                "checks": (lens.get("checks") or [])[:3],
                "looks_for": lens.get("looks_for", ""),
            })

    n_deep = sum(1 for e in selected if e.get("mode") == "subagent")
    return {
        "lenses": selected,
        "context": {
            "changed_files": len(files),
            "has_code": has_code,
            "large_change": large,
            "task_type": task_type,
            "artifact_type": artifact_type,
            "breadth": breadth,
            "hub_dependents": hub_dependents,
            # D-0005: the budget is part of the routing DECISION, so it is
            # reported with it. `deep_cap: 0` means the cap could not be
            # read and none was applied — visible, not silent.
            "deep_cap": cap,
            "deep_dispatched": n_deep,
        },
    }


class _ComponentLayerMiss(Exception):
    """Internal (R-0003): the component layer EXISTS but cannot serve this
    diff (unmapped file, stale fingerprint, degraded component, corrupt
    layer). The caller traces `component_layer_failed` and WIDENS to the
    module-level route — the fail-open ladder's structural guarantee."""


def _scan_hash(workspace: str, rel: str) -> str | None:
    """The CURRENT on-disk content hash of one file, computed EXACTLY as
    depgraph's scan computes it (sha1[:12] over utf-8/replace text), so it
    is comparable with graph.json's per-file hash. Unreadable -> None
    (compares unequal -> stale -> the safe, wider route)."""
    import hashlib
    p = os.path.join(workspace, rel)
    # Containment (Phase 2 EM fix, HIGH): `rel` comes from graph.json's
    # components layer — repo-supplied data. Without the realpath check a
    # crafted graph made this read ../-traversal, absolute paths, and
    # out-pointing symlinks (sha equality as an oracle + route narrowing).
    # Same pattern as decompose._read_text / lens_signals.Ctx.read: a path
    # escaping the real workspace is None -> compares unequal -> stale ->
    # the SAFE, WIDER route. Fail-open direction preserved.
    root = os.path.realpath(workspace)
    real = os.path.realpath(p)
    if real != root and not real.startswith(root + os.sep):
        return None
    try:
        with open(real, encoding="utf-8", errors="replace") as f:
            return hashlib.sha1(f.read().encode()).hexdigest()[:12]
    except OSError:
        return None


def _assemble_components(workspace, files, cat, *, stage, requirement_text,
                         content_by_file=None):
    """Route v2 COMPONENT assembly (R-0003, contract:component-map).

    The graph's `components` layer maps changed files -> touched components;
    the candidate lens set is the CAPPED UNION of the touched components'
    cached lens_maps. The cache only PROPOSES: final verdicts are re-run on
    the REAL diff ctx (live signals dispose), and the R-0001 budget (cap 8,
    demote-never-drop) + security/architecture floors run AFTER assembly on
    that live ctx — never served from cached maps.

    Returns (vmap, proposed, info):
      vmap      final verdict map, or None when the component path does not
                engage;
      proposed  {lens_id: [component ids]} — which touched component(s)
                proposed each candidate lens (component_attribution
                material, contract:lens-brief); a lens earned by the
                requirement text's own keywords is attributed to the
                pseudo-source 'requirement-keywords' (B4, R-0008);
      info      {"components": [...]} on success; {"miss": reason} when the
                layer EXISTS but failed (caller traces
                `component_layer_failed` and widens to the module route);
                None when the layer is ABSENT (byte-identical Phase 1
                module routing — the layer never engaged at all).

    Structural superset guarantee (design stream 1): the live ctx here is
    the SAME diff ctx the module-level route scores, so the component route
    is exactly the module route intersected with the proposed candidates
    (floors excepted, which survive both). Every fallback rung can only
    WIDEN coverage, never narrow it.
    """
    import lens_signals
    try:
        import depgraph
        g = depgraph.load(workspace)
    except Exception as exc:
        # Corrupt graph: layer presence unknowable — widen, and say so.
        return None, None, {"miss": f"graph unreadable: {exc}"}
    comps = g.get("components")
    if comps is None:
        return None, None, None       # layer absent — Phase 1, untraced
    try:
        if not isinstance(comps, list) or not comps or not all(
                isinstance(c, dict) and c.get("id") for c in comps):
            raise _ComponentLayerMiss("component layer empty or corrupt")
        by_id = {c["id"]: c for c in comps}
        index: dict = {}
        for c in comps:
            for f in c.get("files") or []:
                index.setdefault(f, set()).add(c["id"])
        touched: dict = {}
        for f in files:
            cids = index.get(f)
            if not cids:
                raise _ComponentLayerMiss(
                    f"changed file maps to no component: {f}")
            for cid in sorted(cids):
                touched[cid] = by_id[cid]
        if not touched:
            raise _ComponentLayerMiss("empty diff — no touched component")
        # Fingerprint currency: every touched component's cached lens_map
        # reflects its files AS SCANNED. Any span file whose on-disk hash
        # differs from the scan hash makes the cache stale -> widen.
        ghashes = {rel: (row or {}).get("hash", "")
                   for rel, row in (g.get("files") or {}).items()}
        for cid in sorted(touched):
            c = touched[cid]
            if c.get("degraded") or not isinstance(c.get("lens_map"), dict) \
                    or not c.get("lens_map"):
                raise _ComponentLayerMiss(
                    f"component degraded or unmapped: {cid}")
            for f in c.get("files") or []:
                if _scan_hash(workspace, f) != ghashes.get(f):
                    raise _ComponentLayerMiss(
                        f"stale fingerprint: {cid} ({f} changed since scan)")
        # The union: cached maps PROPOSE the candidate lenses...
        proposed: dict = {}
        for cid in sorted(touched):
            for lid, e in sorted(touched[cid]["lens_map"].items()):
                if isinstance(e, dict) and e.get("verdict") in ("deep",
                                                                "light"):
                    proposed.setdefault(lid, []).append(cid)
        if not proposed:
            raise _ComponentLayerMiss(
                "touched components propose no lenses")
        # ...and the LIVE diff signals dispose: same ctx the module route
        # would score (never trust the cache for final verdicts).
        ctx = lens_signals.make_ctx(workspace, files,
                                    requirement_text=requirement_text,
                                    stage=stage,
                                    content_by_file=content_by_file)
        raw = lens_signals.verdicts([l["id"] for l in cat["lenses"]], ctx,
                                    floors=False)
        # B4 (R-0008): cached lens_maps are derived WITHOUT requirement_text,
        # so a lens the requirement's own keywords earn appears in no cached
        # proposal and the narrowing below would delete it — a NARROWING the
        # ladder forbids. Re-run the requirement-keyword detector LIVE on the
        # ctx we already hold (it carries requirement_text) and UNION the
        # result into `proposed` BEFORE narrowing: the cache may only ADD
        # candidates, never subtract them. No requirement text -> empty union
        # -> byte-unchanged routing. Floors and the budget still run AFTER,
        # on this same live ctx.
        for lid in sorted(lens_signals.requirement_keyword_lenses(ctx)):
            if lid in raw and lid not in proposed:
                proposed[lid] = ["requirement-keywords"]
        comp_note = ("component assembly: not proposed by any touched "
                     "component (" + ", ".join(sorted(touched)) + ")")
        for lid, v in raw.items():
            if lid not in proposed and v["verdict"] != "n/a":
                v["negative_evidence"] = [
                    f"{comp_note} — live verdict '{v['verdict']}' "
                    "narrowed to n/a"] + list(v["negative_evidence"])
                v["verdict"] = "n/a"
        # Budget (cap 8, demote-never-drop) + floors AFTER assembly, on the
        # REAL diff ctx — a floor can never be narrowed away by the layer.
        vmap = lens_signals.apply_budget(raw, cap=lens_signals.DEEP_CAP,
                                         target=lens_signals.DEEP_TARGET,
                                         ctx=ctx)
        return vmap, proposed, {"components": sorted(touched)}
    except Exception as exc:
        return None, None, {"miss": str(exc)}


def _route_v2(changed_files, cat, *, stage, task_type, artifact_type,
              only, skip, hub_dependents, workspace,
              requirement_text, content_by_file=None) -> dict:
    """Signal-driven routing (v3 Phase 1). Candidates restricted to the
    stage profile; verdicts from lens_signals.route_verdicts (budget cap 8
    + security/architecture floors applied inside); legacy glob/task-type
    reasons merged with signal evidence. EVERY catalog lens gets an output
    entry — n/a included, carrying its negative evidence (coverage
    honesty). Raises on any engine problem; route() catches and returns a
    named zero-dispatch mapper_unavailable result.

    v3 Phase 2 (R-0003): when the graph carries a `components` layer, the
    candidate verdicts come from _assemble_components (capped union of the
    touched components' cached maps, re-evidenced on the live diff). The
    fallback ladder permits component assembly -> module-level route; if the
    module mapper fails too, route() returns mapper_unavailable with zero
    dispatch. With the layer absent this path is Phase 1."""
    import lens_signals

    code_ext = cat.get("code_extensions", [])
    deep_n = cat.get("deep_threshold_files", 8)
    files = list(changed_files or [])
    has_code = any(_is_code(f, code_ext) for f in files)
    large = len(files) >= deep_n
    only = set(only or [])
    skip = set(skip or [])

    profile = (cat.get("stage_profiles") or {}).get(stage)
    all_ids = [l["id"] for l in cat["lenses"]]
    # Unknown/absent stage -> the FULL catalog (fail open to more coverage).
    candidates = set(profile) if profile else set(all_ids)

    # R-0003 component path — engages ONLY when the graph carries the
    # `components` layer; any layer failure is traced and WIDENS to the
    # module-level route below (fail-open ladder, superset guarantee).
    vmap = None
    proposed = None
    comp_info = None
    if workspace:
        vmap, proposed, comp_info = _assemble_components(
            workspace, files, cat, stage=stage,
            requirement_text=requirement_text,
            content_by_file=content_by_file)
        if comp_info is not None and comp_info.get("miss"):
            import sys
            print("taskplane: component layer unusable "
                  f"({comp_info['miss']}) — widening to module-level "
                  "routing (fail-open: more review coverage, not less). "
                  "Re-run `tp graph scan --decompose` to restore "
                  "component-precise routing.", file=sys.stderr)
            try:
                tp.trace(workspace, "component_layer_failed",
                         error=comp_info["miss"], stage=stage)
            except Exception:
                pass
    if vmap is None:
        # Applicability engine (module level): verdicts for EVERY catalog
        # lens, budget-capped (hard cap 8, overflow demoted to light, never
        # dropped), floors applied after the budget (security on
        # enforcement/boundary diffs; architecture >= light on any code
        # change).
        vmap = lens_signals.route_verdicts(
            workspace or ".", files, stage=stage,
            requirement_text=requirement_text,
            content_by_file=content_by_file)

    selected = []
    for lens in cat["lenses"]:
        lid = lens["id"]
        v = vmap[lid]           # a missing id is catalog drift -> fail open
        verdict, score = v["verdict"], v["score"]
        evidence = list(v["evidence"])
        negative = list(v["negative_evidence"])
        floored = "floor" in v

        # Legacy glob/task-type reasons, merged with the signal evidence so
        # every entry stays explainable in yesterday's vocabulary too.
        reasons = []
        gl = _any_match(files, lens.get("globs"))
        if gl:
            reasons.append(f"touches {gl[0][1]} ({gl[0][0]})")
        if task_type and task_type in (lens.get("task_types") or []):
            reasons.append(f"task type '{task_type}'")
        if artifact_type and artifact_type in (lens.get("artifact_types") or []):
            reasons.append(f"artifact '{artifact_type}'")
        if lens.get("baseline") == "code" and has_code:
            reasons.append("baseline (any code change)")
        if lens.get("untested_trigger") and _adds_no_test(files, code_ext):
            reasons.append("untested change (code changed, no test file)")

        forced = lid in only
        if forced:
            # --lens/--only force: the lens runs deep REGARDLESS of verdict.
            if verdict != "deep":
                evidence.append("forced: --lens/--only override "
                                f"(engine verdict was '{verdict}')")
            verdict = "deep"
        elif only:
            negative = [f"excluded by --only ({', '.join(sorted(only))})"] \
                + negative
            verdict = "n/a"
        elif lid in skip:
            negative = [f"operator --skip {lid}"] + negative
            verdict = "n/a"
        elif lid not in candidates and not floored:
            # Stage-profile restriction. A FLOORED lens survives it: floors
            # are guardrails and may never be profile-narrowed away.
            negative = [f"not in stage profile '{stage}' (engine verdict "
                        f"'{verdict}', score {score})"] + negative
            verdict = "n/a"

        if verdict == "n/a" and not negative:
            # Coverage honesty is non-negotiable: an unevidenced n/a must
            # halt v2 routing (route() then returns mapper_unavailable).
            raise ValueError(f"lens {lid}: n/a without negative evidence")

        merged = reasons + [e for e in evidence if e not in reasons]
        entry = {
            "id": lid,
            "name": lens["name"],
            "mode": ("subagent" if verdict == "deep" else
                     "inline" if verdict == "light" else "none"),
            "tier": verdict,                    # "deep" | "light" | "n/a"
            "verdict": "deep (forced)" if forced else verdict,
            "score": score,
            "reasons": merged if verdict != "n/a" else list(negative),
            "evidence": evidence,
            "checks": lens.get("checks", []),
            "looks_for": lens.get("looks_for", ""),
        }
        if verdict == "n/a":
            entry["negative_evidence"] = negative
        if floored:
            entry["floor"] = v["floor"]
        if proposed is not None and verdict != "n/a" and lid in proposed:
            # contract:lens-brief ADDITIVE key — which component(s)
            # contributed this routed lens (component path only).
            entry["component_attribution"] = list(proposed[lid])
        selected.append(entry)

    ctxd = {
        "changed_files": len(files),
        "has_code": has_code,
        "large_change": large,
        "task_type": task_type,
        "artifact_type": artifact_type,
        "breadth": "routed",
        "hub_dependents": hub_dependents,
        "stage": stage,
        "stage_profile": sorted(candidates),
        "signals": True,
        "content_source": ("canonical-diff" if content_by_file is not None
                           else "current-files"),
    }
    if proposed is not None:
        # Component path engaged: record the touched components and the
        # per-routed-lens attribution (ADDITIVE — absent on the module
        # path, so undecomposed routing stays byte-identical).
        ctxd["component_route"] = True
        ctxd["components"] = list(comp_info["components"])
        ctxd["component_attribution"] = {
            x["id"]: x["component_attribution"] for x in selected
            if "component_attribution" in x}
    elif comp_info is not None and comp_info.get("miss"):
        ctxd["component_layer_failed"] = comp_info["miss"]
    return {"lenses": selected, "context": ctxd}


def prime_scope(scope_globs, task_type: str | None = None,
                catalog: dict | None = None, **kw) -> dict:
    """Route lenses from a task's SCOPE GLOBS, before any file exists.

    Used to PRIME the executor at EXECUTE/FIX: the same lenses that will
    review the change afterwards are named up front, so the work is built
    with those perspectives in mind instead of discovering them at review.
    Each glob is expanded to a representative pseudo-path (e.g.
    ``src/auth/**`` → ``src/auth/x.py``) so dir-scoped and baseline lenses
    fire; file-specific deep matches still apply at review time on the
    real diff.
    """
    files = []
    for g in scope_globs or []:
        files.append(g)
        base_name = os.path.basename(g)
        if g.endswith("**"):
            files.append(g.rstrip("*").rstrip("/") + "/x.py")
        elif "*" in base_name:
            files.append(base_name.replace("*", "x"))
    routing = route(files, task_type=task_type, catalog=catalog, **kw)
    routing["context"]["primed_from_scope"] = True
    routing["context"]["changed_files"] = 0
    return routing


# Paths the loop/runtime writes for itself (state, plans, KB records, review
# artifacts). Lens review routes on the WORK, not the loop's own bookkeeping —
# otherwise every run drags in product/PM lenses and inflates change size.
LOOP_OWNED = (".taskplane", ".eval/", ".em-review/", "plan/", "specs/",
              "design/", "knowledge/")


def route_git_diff(workspace: str, base: str = "HEAD",
                   task_type: str | None = None,
                   exclude_loop_owned: bool = True, **kw) -> dict:
    """Route against a git diff in `workspace` (changed + untracked files)."""
    import subprocess

    def run(args):
        return subprocess.run(["git", *args], cwd=workspace,
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout

    files = [f for f in (run(["diff", "--name-only", base]) +
                         run(["ls-files", "--others", "--exclude-standard"])
                         ).splitlines() if f]
    if exclude_loop_owned:
        files = [f for f in files
                 if not f.startswith(LOOP_OWNED)
                 and not f.endswith(".taskplane_output.json")]
    kw.setdefault("hub_dependents", hub_signal(workspace, files))
    kw.setdefault("workspace", workspace)   # route v2 content scans (t2);
    routing = route(sorted(set(files)), task_type=task_type, **kw)
    routing["context"]["files"] = sorted(set(files))
    return routing


def catalog_summary(catalog: dict | None = None) -> list:
    """Every lens as a one-line card — id, name, group, what it looks for.
    Exposes the catalog directly (for `tp lens list` / the lens gallery)."""
    cat = catalog or load_catalog()
    return [{"id": l["id"], "name": l["name"], "group": l.get("group", ""),
             "looks_for": l.get("looks_for", "")} for l in cat["lenses"]]


def lens_brief(lens_id: str, catalog: dict | None = None) -> dict | None:
    """The full brief for ONE lens — charter, boundary, checks, looks_for.
    This is what a lens-agent is briefed with (for `tp lens show <id>`)."""
    cat = catalog or load_catalog()
    l = next((x for x in cat["lenses"] if x["id"] == lens_id), None)
    if l is None:
        return None
    return {"id": l["id"], "name": l["name"], "group": l.get("group", ""),
            "charter": l.get("charter", ""), "boundary": l.get("boundary", ""),
            "looks_for": l.get("looks_for", ""), "checks": l.get("checks", []),
            "globs": l.get("globs", [])}


# Appended to EVERY dispatched agent prompt. try/finally semantics for a
# prompt-driven agent: release the contract in ALL outcomes — success, error,
# or budget-block. A lens agent that died without clearing once locked an
# entire session. There is deliberately NO self-service escape from a
# budget-blocked state (the wall is intentional); the escalation path is the
# human, and the orphan auto-release (dead PID / idle TTL) is the backstop.
CLEAR_ALWAYS = (
    "FINALLY — ALWAYS, in every outcome (done, error, or blocked): release "
    "your contract as your LAST action, with your slot still exported: "
    '`TASKPLANE_TASK=$TASKPLANE_TASK python3 '
    '"${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py" '
    'clear`. Treat this as '
    "the finally-block of your whole task — a leaked contract locks the "
    "workspace for everyone after you. If the clear itself is blocked "
    "(budget exhausted), STOP and report the leaked contract in your final "
    "message so the dispatcher/human can release it (`tp.py clear "
    "--workspace <ws>` from an ungoverned context); never try to work "
    "around the block.")


def _slot_instr(slot: str) -> str:
    """Per-task contract-slot activation (v2.3.1). WITHOUT this, every parallel
    lens agent's `tp.py new` writes the single legacy active_contract.json and
    the agents overwrite each other's contracts — the exact multi-writer defect
    the per-task-slot protocol exists to close, which shipped un-wired in
    v2.3.0. Each agent exports a UNIQUE slot so its contract lands in
    active/<slot>.json and the union stays honest."""
    return (
        f"FIRST — before you activate any contract — export your unique "
        f"per-task contract slot so parallel lens agents cannot overwrite each "
        f"other's governance: `export TASKPLANE_TASK={slot}`. Keep it set for "
        f"every `tp.py` call you make (new / screen / clear).\n")


def _lens_prompt(entry: dict, base: str) -> str:
    """The task prompt handed to a governed read-only lens-agent."""
    checks = "; ".join(entry.get("checks") or []) or "(use your judgment)"
    return (
        f"Apply the {entry['name'].upper()} lens to the diff against `{base}`.\n"
        f"LOOK FOR: {entry.get('looks_for','')}.\n"
        f"CHECKS: {checks}.\n"
        f"You are READ-ONLY toward code — inspect the diff and the files it "
        f"touches, run non-mutating checks, but change NOTHING. Write your "
        f"findings ONLY to `.em-review/lens-{entry['id']}/findings.json` as "
        f'{{"lens":"{entry["id"]}","findings":[{{"severity":"high|med|low",'
        f'"class":"regression|pre-existing|observation",'
        f'"file":"path","line":N,"title":"...","scenario":"concrete failure",'
        f'"fix":"direction"}}]}} — an empty list means the lens is clean. '
        f"CLASS each finding honestly: `regression` = this diff broke it, "
        f"`pre-existing` = defect predates the diff, `observation` = "
        f"improvement idea, not a defect. "
        f"Stay strictly in your lens; another agent owns the others.\n"
        + CLEAR_ALWAYS)


# Per-brief action ceilings (v2.11.0). A flat 30 truncated a verification
# agent mid-research on karpenter#9464: it was fetching and reading external
# sources to check the lenses' load-bearing claims, hit the ceiling, and
# returned partial findings naming the questions it could not close. It
# degraded honestly — the ceiling was simply the wrong size for the shape of
# work. A DEEP lens owns one subject at full depth and reads widely; the
# SWEEP runs each lens's top checks and is meant to be quick. One number for
# both sized the deep agent by the cheap one's needs.
#
# This raises no CONTRACT scope: a lens agent still writes only to
# `.em-review/lens-<id>/**` and still cannot touch reviewed source. An
# explicit `max_actions` overrides every tier, so callers that pin a number
# (the parity fixtures) keep getting exactly that number.
DEEP_ACTIONS = 45
SWEEP_ACTIONS = 30


def actions_for(tier: str, override=None) -> int:
    if override is not None:
        return int(override)
    return DEEP_ACTIONS if tier == "deep" else SWEEP_ACTIONS


def dispatch_briefs(routing: dict, base: str = "HEAD",
                    max_actions: int | None = None,
                    impact_context: str | None = None,
                    runnability: dict | None = None,
                    context_paths: dict | None = None) -> dict:
    """Turn a routing into READY-TO-DISPATCH lens-agent briefs — one governed
    read-only agent per DEEP lens (fanned out in parallel = much faster than
    one reviewer running them in sequence), the SWEEP lenses batched into a
    single quick agent. Each brief carries its own read-only contract spec so
    the harness/guardrails are preserved: a lens-agent can read the diff but
    never modify code, and it's budget-capped.
    """
    # v2 routings carry per-lens verdicts: "deep" fans out one governed agent
    # each, "light" batches into the single sweep-style brief, and "n/a"
    # lenses get NO brief — they do not run. The full disposition set
    # (all catalog lenses, n/a included with negative evidence) still rides
    # on the decision object below: briefs are for work, the decision is
    # for coverage honesty. Legacy routings ("deep"/"sweep" tiers only) are
    # partitioned exactly as before.
    # B9 (v2.10.0): whether the build/tests can run here is a property of the
    # CHECKOUT, probed once by the dispatcher. Six agents rediscovering it —
    # which is exactly what happened on karpenter#9464 — is six times the
    # tokens for one environment fact. Stated, never enforced.
    # v2.13.0: ONE copy of the diff and the blast radius, on disk, cited by
    # every brief — instead of N embedded copies at output weight. Four lens
    # agents cost ~754k effective tokens on the measured review, "each
    # carrying its own copy of the diff and the blast-radius brief".
    ctx_note = ""
    if context_paths:
        try:
            import review as _rv
            ctx_note = _rv.context_note(context_paths)
        except Exception:
            ctx_note = ""
    run_note = ""
    if runnability:
        try:
            import runnability as _run
            run_note = _run.brief_note(runnability)
        except Exception:
            run_note = ""
    deep = [x for x in routing["lenses"]
            if x.get("tier") not in ("sweep", "light", "n/a")]
    sweep = [x for x in routing["lenses"]
             if x.get("tier") in ("sweep", "light")]
    briefs = []
    for x in deep:
        lid = x["id"]
        mtier = _lens_tier(lid, "deep")
        brief = {**tp.dispatch_fields("lens", "tp-lens", lid, mtier),
            "id": lid, "name": x["name"], "tier": "deep", "agent": "tp-lens",
            "task_slot": f"lens-{lid}",
            "output": f".em-review/lens-{lid}/findings.json",
            "contract": {"read_only": True,
                         "task_slot": f"lens-{lid}",
                         "write_allow": [f".em-review/lens-{lid}/**"],
                         "max_actions": actions_for("deep", max_actions)},
            "prompt": _slot_instr(f"lens-{lid}") + _lens_prompt(x, base) + (
                "\nBLAST RADIUS (from the dependency graph - factor "
                "these dependents into your verdict):\n"
                + impact_context + "\n" if impact_context else "")
                + ctx_note + run_note,
            "looks_for": x.get("looks_for", ""), "checks": x.get("checks", []),
        }
        if "verdict" in x:   # contract:lens-brief — ADDITIVE v2 fields only
            brief["verdict"] = x["verdict"]
            brief["score"] = x.get("score")
            brief["evidence"] = x.get("evidence", [])
        if "component_attribution" in x:
            # R-0003, contract:lens-brief ADDITIVE key: present ONLY on the
            # component path — undecomposed dispatch stays byte-identical.
            brief["component_attribution"] = list(x["component_attribution"])
        briefs.append(brief)
    sweep_brief = None
    if sweep:
        names = ", ".join(s["name"] for s in sweep)
        sweep_brief = {**tp.dispatch_fields(
            "lens", "tp-lens", "sweep", "cheap"),
            "ids": [s["id"] for s in sweep], "agent": "tp-lens",
            "task_slot": "lens-sweep",
            "output": ".em-review/lens-sweep/findings.json",
            "contract": {"read_only": True,
                         "task_slot": "lens-sweep",
                         "write_allow": [".em-review/lens-sweep/**"],
                         "max_actions": actions_for("sweep", max_actions)},
            "prompt": _slot_instr("lens-sweep") + (
                f"Quick SWEEP of these lenses against the diff vs `{base}`: "
                f"{names}. Run each lens's top checks only — flag or clear in "
                f"one line each. READ-ONLY: write findings (each with a "
                f"`lens` field and a `class` field — regression|pre-existing|"
                f"observation) to `.em-review/lens-sweep/findings.json`, "
                f"change no code.\n" + ctx_note + run_note + CLEAR_ALWAYS),
        }
    # Full routing-decision object (v2 only): EVERY lens's disposition —
    # n/a lenses run no agent but their verdict + negative evidence must
    # reach the renderer/coverage map (coverage honesty).
    decision = None
    if any("verdict" in x for x in routing["lenses"]):
        decision = {}
        for x in routing["lenses"]:
            d = {"verdict": x.get("verdict", x.get("tier")),
                 "score": x.get("score")}
            if x.get("tier") == "n/a":
                d["negative_evidence"] = list(
                    x.get("negative_evidence") or x.get("reasons") or [])
            else:
                d["evidence"] = list(
                    x.get("evidence") or x.get("reasons") or [])
            if "component_attribution" in x:
                # R-0003: rides into findings meta via meta.routing_decision
                # (ADDITIVE — only the component path sets it).
                d["component_attribution"] = list(x["component_attribution"])
            decision[x["id"]] = d
    if not briefs and sweep_brief is None:
        # A no-op diff routed no lenses at all — don't tell the caller to
        # dispatch agents that don't exist. Signal "nothing to review".
        out = {
            "base": base,
            "changed_files": routing["context"].get("changed_files", 0),
            "deep": [], "sweep": None,
            "nothing_to_review": True,
            "instruction": (
                "No lenses routed for this diff — there is nothing to review. "
                "Dispatch no agents; report a clean/no-op review to the human."),
        }
        if decision is not None:
            out["routing_decision"] = decision
        if runnability:
            out["runnability"] = runnability
        return out
    out = {
        "base": base,
        "changed_files": routing["context"].get("changed_files", 0),
        "deep": briefs, "sweep": sweep_brief,
        "nothing_to_review": False,
        "instruction": (
            "Dispatch ONE tp-lens agent per DEEP brief IN PARALLEL (single "
            "message, multiple Task calls) plus one for the SWEEP — each "
            "activates its read-only contract, applies exactly its lens to "
            "the diff, and writes its own findings.json. None can modify "
            "code (read-only harness). When they return, merge every lens's "
            "findings into one findings dashboard (`tp findings`) for the "
            "human review gate."),
    }
    if decision is not None:
        out["routing_decision"] = decision
    if runnability:
        out["runnability"] = runnability
        out["instruction"] += (
            " Build/test runnability was probed ONCE and is stated in every "
            "brief — carry `runnability.summary` into the findings "
            "`meta.tests` so the headline says it, and do not let any agent "
            "re-probe it.")
    return out


def render(routing: dict) -> str:
    """Human-readable explanation (for `tp lens route`)."""
    ls = routing["lenses"]
    if not ls:
        return "no lenses apply to this change."
    out = [f"{len(ls)} lens(es) apply "
           f"({routing['context']['changed_files']} files changed):"]
    for x in ls:
        if x.get("tier") == "n/a":
            tag = "○ n/a     "   # v2 coverage honesty: skips stay visible
        elif x["mode"] == "subagent":
            tag = "▸ subagent"
        else:
            tag = "· inline  "
        out.append(f"  {tag}  {x['id']:<13} ← {'; '.join(x['reasons'])}")
    return "\n".join(out)
