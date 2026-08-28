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

import copy
import hashlib
import json
import os

import glob_match
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

# Language depth is useful only when it reaches the worker.  Keep resolution
# deterministic and data-shaped so Review, Build, Design, Claude and Codex all
# receive the same repo-relative references without embedding their bodies in
# every brief.
_LANGUAGE_REFERENCES = {
    "go": {
        "extensions": (".go",), "manifests": ("go.mod", "go.sum"),
        "references": {
            "code-quality": {"path": "lenses/references/go-code-quality.md"},
            "solution-design": {"path": "lenses/references/go-solution-design.md"},
            "architecture": {"path": "lenses/references/go-engineering.md", "section": "Architecture"},
            "backend": {"path": "lenses/references/go-engineering.md", "section": "Backend"},
            "sre": {"path": "lenses/references/go-engineering.md", "section": "SRE"},
            "security": {"path": "lenses/references/go-engineering.md", "section": "Security"},
            "qa": {"path": "lenses/references/go-engineering.md", "section": "QA"},
            "testability": {"path": "lenses/references/go-engineering.md", "section": "Testability"},
            "scalability": {"path": "lenses/references/go-engineering.md", "section": "Scalability"},
            "integrability": {"path": "lenses/references/go-engineering.md", "section": "Integrability"},
            "data-safety": {"path": "lenses/references/go-engineering.md", "section": "Data safety"},
        },
    },
    "python": {
        "extensions": (".py",),
        "manifests": ("pyproject.toml", "requirements.txt", "setup.py"),
        "references": {
            "code-quality": {"path": "lenses/references/python-code-quality.md"},
            "solution-design": {"path": "lenses/references/python-solution-design.md"},
            "scalability": {"path": "lenses/references/python-engineering.md", "section": "Scalability"},
            "qa": {"path": "lenses/references/python-engineering.md", "section": "QA"},
            "testability": {"path": "lenses/references/python-engineering.md", "section": "Testability"},
            "devops": {"path": "lenses/references/python-engineering.md", "section": "Packaging and DevOps"},
            "integrability": {"path": "lenses/references/python-engineering.md", "section": "Integrability"},
            "security": {"path": "lenses/references/python-engineering.md", "section": "Security"},
            "sre": {"path": "lenses/references/python-engineering.md", "section": "SRE"},
        },
    },
    "typescript": {
        "extensions": (".ts", ".tsx"),
        "manifests": ("tsconfig.json",),
        "references": {
            "code-quality": {"path": "lenses/references/typescript-code-quality.md"},
            "solution-design": {"path": "lenses/references/typescript-solution-design.md"},
            "integrability": {"path": "lenses/references/typescript-engineering.md", "section": "Integrability"},
            "devops": {"path": "lenses/references/typescript-engineering.md", "section": "DevOps"},
            "scalability": {"path": "lenses/references/typescript-engineering.md", "section": "Scalability"},
            "architecture": {"path": "lenses/references/typescript-engineering.md", "section": "Architecture"},
            "frontend": {"path": "lenses/references/typescript-engineering.md", "section": "Frontend async"},
            "security": {"path": "lenses/references/typescript-engineering.md", "section": "Security"},
        },
    },
}


def detected_languages(files) -> list[str]:
    """Languages declared by file extensions or root/build manifests."""
    paths = [str(p).replace("\\", "/") for p in files or []]
    found = []
    for language, spec in _LANGUAGE_REFERENCES.items():
        present = any(
            p.lower().endswith(tuple(spec["extensions"]))
            or os.path.basename(p).lower() in spec["manifests"]
            for p in paths)
        if present:
            found.append(language)
    return found


def _reference_record(language: str, lens_id: str, spec: dict) -> dict:
    path = str(spec["path"]).replace("\\", "/")
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    absolute = os.path.realpath(os.path.join(plugin_root, *path.split("/")))
    if os.path.commonpath((os.path.realpath(plugin_root), absolute)) != \
            os.path.realpath(plugin_root) or not os.path.isfile(absolute):
        raise FileNotFoundError(f"language reference is missing or unsafe: {path}")
    with open(absolute, "rb") as stream:
        content = stream.read()
    section = str(spec.get("section") or "")
    if section:
        heading = "## " + section
        if heading not in content.decode("utf-8", errors="replace").splitlines():
            raise ValueError(
                f"language reference section is missing: {path}#{section}")
    digest = hashlib.sha256(content).hexdigest()
    row = {"language": language, "lens": lens_id, "path": path,
           "content_sha256": digest}
    if section:
        row["section"] = section
    return row


def language_references(files, task_type: str | None = None,
                        lens_ids=None) -> list[dict]:
    """Resolve scoped, content-bound references from repo-relative paths.

    The public default remains the code-quality reference for compatibility.
    Callers pass their active lens ids and receive the complete
    lens-owned set without widening which lenses execute.
    """
    wanted = ({"solution-design"} if task_type == "solution-design" else
              ({str(x) for x in lens_ids} if lens_ids is not None else
               {"code-quality"}))
    refs = []
    for language in detected_languages(files):
        for lens_id, ref_spec in _LANGUAGE_REFERENCES[language][
                "references"].items():
            if lens_id in wanted:
                refs.append(_reference_record(language, lens_id, ref_spec))
    return sorted(refs, key=lambda row: (
        row["language"], row["lens"], row["path"], row.get("section", "")))


def workspace_language_markers(workspace: str | None,
                               scope_globs=None) -> list[str]:
    """Cheap manifest hints for pre-diff Build and Design routing.

    Inspect the root, literal scope prefixes, and one directory below those
    prefixes.  This covers ordinary monorepos without turning priming into a
    repository walk.
    """
    if not workspace:
        return []
    names = {m for spec in _LANGUAGE_REFERENCES.values()
             for m in spec["manifests"]}
    roots = {workspace}
    for glob in scope_globs or []:
        literal = str(glob).replace("\\", "/")
        literal = literal[:min(
            [literal.find(c) for c in "*?[" if c in literal]
            or [len(literal)])].rstrip("/")
        candidate = os.path.join(workspace, literal)
        if not os.path.isdir(candidate):
            candidate = os.path.dirname(candidate)
        if os.path.isdir(candidate):
            roots.add(candidate)
            try:
                roots.update(entry.path for entry in os.scandir(candidate)
                             if entry.is_dir(follow_symlinks=False))
            except OSError:
                pass
    found = []
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                found.append(os.path.relpath(path, workspace).replace(
                    os.sep, "/"))
    return sorted(set(found))


def _attach_language_context(routing: dict, files,
                             task_type: str | None) -> dict:
    active_lenses = {
        str(row.get("id")) for row in routing.get("lenses") or []
        if row.get("tier") != "n/a" and row.get("verdict") != "n/a"
        and row.get("mode") not in {"none", "n/a"}
    }
    refs = language_references(files, task_type, active_lenses)
    if not refs:
        return routing
    routing.setdefault("context", {})["language_references"] = refs
    by_lens = {}
    for ref in refs:
        by_lens.setdefault(ref["lens"], []).append(ref)
    for row in routing.get("lenses") or []:
        if row.get("id") in by_lens:
            row["language_references"] = list(by_lens[row["id"]])
    return routing


def _language_note(refs) -> str:
    entries = sorted({
        str(r.get("path")) + ("#" + str(r.get("section"))
                              if r.get("section") else "")
        + " sha256=" + str(r.get("content_sha256"))
        for r in refs or [] if r.get("path") and r.get("content_sha256")})
    if not entries:
        return ""
    return ("\nLANGUAGE REFERENCES: read and apply " + ", ".join(entries)
            + ". Resolve these plugin-relative paths against the plugin root "
              "that contains your role_instructions file. These pinned "
              "standards are part of this brief; do not substitute model "
              "memory for them. Copy the exact reference records into "
              "references_applied in the leased result.\n")


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
    """Compatibility facade over the shared dependency-neutral matcher."""
    return glob_match.path_matches(path, glob)


def _any_match(files, globs) -> list:
    return glob_match.matches_by_pattern(files, globs)


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
    files = list(changed_files or [])
    cat = catalog or load_catalog()
    v2 = (use_signals is not False
          and isinstance(cat.get("stage_profiles"), dict)
          and breadth != "all"
          and (stage is not None or use_signals is True))
    if v2:
        try:
            routed = _route_v2(files, cat, stage=stage,
                               task_type=task_type,
                               artifact_type=artifact_type, only=only,
                               skip=skip, hub_dependents=hub_dependents,
                               workspace=workspace,
                               requirement_text=requirement_text,
                               content_by_file=content_by_file)
            routed = _attach_language_context(routed, files, task_type)
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
                "changed_files": len(files)}}
            _record_breadth(workspace, requested=breadth, effective="routed",
                            engine_ran=False, stage=stage, routing=refused,
                            reason=f"mapper_unavailable: {exc}")
            return refused
        _record_breadth(workspace, requested=breadth, effective="routed",
                        engine_ran=True, stage=stage, routing=routed)
        return routed
    legacy = _route_legacy(files, task_type, artifact_type, cat,
                           only, skip, breadth, hub_dependents)
    try:
        legacy = _attach_language_context(legacy, files, task_type)
    except (OSError, ValueError) as exc:
        import sys
        print(f"taskplane: language reference unavailable ({exc}) — "
              "mapper_unavailable; dispatching zero lenses. Repair the "
              "bundled reference and retry.", file=sys.stderr)
        if workspace:
            try:
                tp.trace(workspace, "lens_engine_failed",
                         error=str(exc), stage=stage)
            except Exception:
                pass
        refused = {"lenses": [], "context": {
            "status": "mapper_unavailable", "breadth": breadth,
            "stage": stage, "lens_engine_failed": str(exc),
            "changed_files": len(files)}}
        _record_breadth(workspace, requested=breadth, effective=breadth,
                        engine_ran=False, stage=stage, routing=refused,
                        reason=f"mapper_unavailable: {exc}")
        return refused
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
        # Review floors can add a lens after component candidates are
        # assembled.  A floor is a whole-review guardrail, not a proposal
        # made by the touched component, so it must remain unattributed.
        # Claiming component attribution here makes the route lie about the
        # cached proposal and breaks the component/floor conservation rule.
        return vmap, proposed, {"components": sorted(touched)}
    except Exception as exc:
        return None, None, {"miss": str(exc)}


_AUTOMATIC_SWEEP_FALLBACKS = (
    "architecture", "code-quality", "security", "qa", "testability",
)


def _automatic_selector_ids(values, *, field: str) -> set:
    """Normalize one explicit selector without silently changing its meaning."""
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    try:
        normalized = {str(value).strip() for value in values}
    except TypeError as exc:
        raise ValueError(f"automatic sweep {field} must be lens ids") from exc
    if "" in normalized:
        raise ValueError(f"automatic sweep {field} contains an empty lens id")
    return normalized


def automatic_sweep_route(routing: dict, *, pinned_lenses=(),
                          skipped_lenses=(), strict_pins: bool = False) -> dict:
    """Project a complete automatic route to exactly one 4–5 lens sweep.

    Context and directive text may affect membership only.  They cannot
    create deep/full work, another worker, or a promotion path. Fuzzy prose
    pins are ranked within the budget; ``strict_pins`` is reserved for an
    explicit selector, whose over-budget request must fail rather than be
    silently truncated.
    """
    routed = copy.deepcopy(routing or {})
    rows = list(routed.get("lenses") or [])
    by_id = {str(row.get("id") or ""): row for row in rows}
    if "architecture" not in by_id or len(rows) < 4:
        raise ValueError("automatic sweep requires the complete lens catalog")
    catalog_ids = set(by_id)
    pins = _automatic_selector_ids(pinned_lenses, field="only")
    skips = _automatic_selector_ids(skipped_lenses, field="skip")
    unknown = (pins | skips) - catalog_ids
    if unknown:
        raise ValueError(
            "automatic sweep selector names unknown lenses: " +
            ", ".join(sorted(unknown)))
    overlap = pins & skips
    if overlap:
        raise ValueError(
            "automatic sweep cannot both select and skip: " +
            ", ".join(sorted(overlap)))
    if "architecture" in skips:
        raise ValueError(
            "automatic sweep cannot skip mandatory architecture floor")
    pins.discard("architecture")
    if strict_pins and len(pins) > 4:
        raise ValueError(
            "automatic sweep explicit selection exceeds the 5-lens budget")
    eligible = [row for row in rows
                if row.get("id") != "architecture"
                and str(row.get("id") or "") not in skips]
    if len(eligible) < 3:
        raise ValueError(
            "automatic sweep exclusions leave fewer than 4 eligible lenses")
    ranked = sorted(
        eligible,
        key=lambda row: (
            str(row.get("id")) not in pins,
            -float(row.get("score") or 0), str(row.get("id") or "")),
    )
    positive_fourth = len(ranked) >= 4 and (
        float(ranked[3].get("score") or 0) > 0 or
        bool(ranked[3].get("evidence")))
    target = 5 if len(pins) >= 4 or positive_fourth else 4
    selected = ["architecture"]
    for row in ranked:
        lens_id = str(row.get("id") or "")
        if lens_id and lens_id not in selected:
            selected.append(lens_id)
        if len(selected) == target:
            break
    for fallback in _AUTOMATIC_SWEEP_FALLBACKS:
        if (fallback in by_id and fallback not in skips and
                fallback not in selected):
            selected.append(fallback)
        if len(selected) == target:
            break
    if len(selected) != target:
        raise ValueError(
            f"automatic sweep could not fill its {target}-lens budget")
    selected_set = set(selected)
    for row in rows:
        lens_id = str(row.get("id") or "")
        if lens_id in selected_set:
            prior = str(row.get("verdict") or row.get("tier") or "n/a")
            row["tier"] = row["verdict"] = "sweep"
            # `mode` controls whether the evaluator actually invokes the
            # governed worker.  Sweep is the bounded review depth; subagent
            # is the dispatch mechanism, not a promotion to deep review.
            row["mode"] = "subagent"
            row.setdefault("evidence", []).append(
                "automatic bounded sweep selection" if
                lens_id != "architecture" else
                "automatic architecture sweep floor")
            row.setdefault("reasons", []).append(
                f"automatic sweep membership (initial verdict {prior})")
            row.pop("negative_evidence", None)
        else:
            row["tier"] = row["verdict"] = "n/a"
            row["mode"] = "none"
            prior_negative = list(row.get("negative_evidence") or [])
            if lens_id in skips:
                negative = prior_negative or [f"operator --skip {lens_id}"]
            else:
                negative = [
                    "not selected by automatic bounded sweep (4–5 lens cap)"
                ] + prior_negative
            row["negative_evidence"] = negative
            row["reasons"] = list(negative)
    context = routed.setdefault("context", {})
    context["automatic_review"] = True
    context["review_progression"] = {
        "schema": "taskplane.review-progression/v1",
        "deep_slots": [], "sweep_count": len(selected),
        "sweep_lenses": selected,
        "sweep_slots": [f"sweep.{lens_id}" for lens_id in selected],
        "deferred_light": [],
    }
    context["automatic_tiers"] = ["n/a", "sweep"]
    return routed


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
    automatic = stage in {"review", "build"}
    only = (_automatic_selector_ids(only, field="only") if automatic
            else set(only or []))
    skip = (_automatic_selector_ids(skip, field="skip") if automatic
            else set(skip or []))

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
    if stage in {"review", "build"}:
        # Component routing and module routing consume the same bounded
        # document evidence.  A component-map miss is never a reason to
        # widen documentation to the full catalog.
        import review_progression
        review_progression.apply_document_signals(
            vmap, files, content_by_file
        )

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

        # Automatic Review/Evaluate treats --only as a membership pin inside
        # its bounded sweep.  It is not the separately attributed human-deep
        # command and therefore cannot force depth or exclude every filler.
        forced = lid in only and not automatic
        if forced:
            # --lens/--only force: the lens runs deep REGARDLESS of verdict.
            if verdict != "deep":
                evidence.append("forced: --lens/--only override "
                                f"(engine verdict was '{verdict}')")
            verdict = "deep"
        elif only and not automatic:
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
        for key in ("review_risk_class", "review_risk_reason",
                    "review_required_deep"):
            if key in v:
                entry[key] = v[key]
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
    if stage in {"review", "build"}:
        import review_progression
        required_rows = [row for row in selected
                         if row.get("review_required_deep")]
        if required_rows:
            ctxd["review_risk"] = {
                "class": required_rows[0]["review_risk_class"],
                "reason": required_rows[0]["review_risk_reason"],
                "required_deep_lenses": sorted(
                    row["id"] for row in required_rows
                ),
            }
        return automatic_sweep_route(
            {"lenses": selected, "context": ctxd},
            pinned_lenses=only, skipped_lenses=skip, strict_pins=True)
    return {"lenses": selected, "context": ctxd}


def prime_scope(scope_globs, task_type: str | None = None,
                catalog: dict | None = None, workspace: str | None = None,
                **kw) -> dict:
    """Route lenses from a task's SCOPE GLOBS, before any file exists.

    Used to PRIME the executor at EXECUTE/FIX: the same lenses that will
    review the change afterwards are named up front, so the work is built
    with those perspectives in mind instead of discovering them at review.
    Each glob is expanded to a representative pseudo-path (e.g.
    ``src/auth/**`` → ``src/auth/x.py``) so dir-scoped and baseline lenses
    fire; file-specific deep matches still apply at review time on the
    real diff.
    """
    cat = catalog or load_catalog()
    markers = workspace_language_markers(workspace, scope_globs)
    inferred = detected_languages(markers)
    extension_by_language = {"go": ".go", "python": ".py",
                             "typescript": ".ts"}
    representative_exts = [
        extension_by_language[language] for language in inferred
        if language in extension_by_language]
    if not representative_exts:
        representative_exts = [(cat.get("code_extensions") or [""])[0]]
    files = list(markers)
    for g in scope_globs or []:
        files.append(g)
        base_name = os.path.basename(g)
        if g.endswith("**"):
            prefix = g.rstrip("*").rstrip("/") + "/x"
            files.extend(prefix + ext for ext in representative_exts)
        elif "*" in base_name:
            files.append(base_name.replace("*", "x"))
    routing = route(files, task_type=task_type, catalog=cat, **kw)
    routing["context"]["primed_from_scope"] = True
    routing["context"]["changed_files"] = 0
    routing["context"]["files"] = sorted(set(files))
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
                    context_paths: dict | None = None,
                    sweep_concerns=None,
                    already_promoted=()) -> dict:
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
    review_policy = copy.deepcopy(
        (routing.get("context") or {}).get("review_depth_policy"))
    if isinstance(review_policy, dict) and \
            review_policy.get("depth") == "quick-only":
        # Defense in depth: dispatch owns the final model-facing payload, so
        # it reapplies the requirement ceiling even when a caller hands it a
        # pre-policy route containing explicit/forced deep dispositions.
        import review_progression
        routing = review_progression.apply_depth_policy(
            routing, review_policy)
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
    progressive = (routing.get("context") or {}).get("review_progression")
    if progressive is not None:
        ordered_sweep = list(progressive.get("sweep_lenses") or [])
        allowed_sweep = set(ordered_sweep)
        sweep = [x for x in sweep if x.get("id") in allowed_sweep]
        sweep.sort(key=lambda x: ordered_sweep.index(x.get("id")))
    concern_outcomes = None
    if sweep_concerns is not None:
        # This is the production boundary between the bounded light sweep and
        # its adaptive deep follow-up.  Keep resolution in review_progression
        # (normalization/charter/idempotence) while this dispatcher owns the
        # actual brief creation.  A concern may promote only a lens that was
        # in this routing's light sweep; every other apparent promotion is
        # retained as an explicit out-of-charter rejection.
        import review_progression
        concern_outcomes = review_progression.resolve_sweep_concerns(
            sweep_concerns, already_promoted=already_promoted,
            review_policy=review_policy,
        )
        sweep_by_id = {str(row.get("id")): row for row in sweep}
        accepted = []
        for promotion in concern_outcomes["promotions"]:
            lens_id = promotion["lens"]
            row = sweep_by_id.get(lens_id)
            if row is None:
                concern_outcomes["rejections"].append({
                    "concern_id": promotion["concern_id"],
                    "lens": lens_id,
                    "severity": promotion["severity"],
                    "reason": "out-of-charter",
                    "fingerprint": promotion["fingerprint"],
                })
                continue
            promoted = dict(row)
            promoted["tier"] = "deep"
            promoted["verdict"] = "deep"
            promoted["evidence"] = list(promoted.get("evidence") or []) + [
                "adaptive promotion from bounded sweep: "
                + promotion["evidence_ref"]
            ]
            promoted["promotion"] = dict(promotion)
            deep.append(promoted)
            accepted.append(promotion)
        concern_outcomes["promotions"] = accepted
        promoted_ids = {row["lens"] for row in accepted}
        sweep = [row for row in sweep if row.get("id") not in promoted_ids]
        deep.sort(key=lambda row: str(row.get("id") or ""))
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
            "prompt": _slot_instr(f"lens-{lid}") + _lens_prompt(x, base)
                + _language_note(x.get("language_references")) + (
                "\nBLAST RADIUS (from the dependency graph - factor "
                "these dependents into your verdict):\n"
                + impact_context + "\n" if impact_context else "")
                + ctx_note + run_note,
            "looks_for": x.get("looks_for", ""), "checks": x.get("checks", []),
        }
        if x.get("language_references"):
            brief["language_references"] = list(x["language_references"])
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
        sweep_refs = []
        seen_refs = set()
        for row in sweep:
            for ref in row.get("language_references") or []:
                key = json.dumps(ref, sort_keys=True, separators=(",", ":"))
                if key not in seen_refs:
                    sweep_refs.append(ref)
                    seen_refs.add(key)
        sweep_brief = {**tp.dispatch_fields(
            "lens", "tp-lens", "sweep", "cheap"),
            "ids": [s["id"] for s in sweep], "agent": "tp-lens",
            **({"tier": "light", "depth": "quick"}
               if review_policy and
               review_policy.get("depth") == "quick-only" else {}),
            "task_slot": "lens-sweep",
            "output": ".em-review/lens-sweep/findings.json",
            "contract": {"read_only": True,
                         "task_slot": "lens-sweep",
                         "write_allow": [".em-review/lens-sweep/**"],
                         "max_actions": actions_for("sweep", max_actions)},
            "dispatch_set": {"schema": "taskplane.dispatch-set/v1",
                             "id": "automatic-review-sweep",
                             "concurrent": True,
                             "member_count": len(sweep)},
            "wait_policy": {"schema": "taskplane.wait-policy/v1",
                            "outstanding_set": "automatic-review-sweep",
                            "outstanding_count": len(sweep), "mode": "event",
                            "timeout_seconds": 1800,
                            "minimum_timeout_seconds": 300,
                            "reissue_after": ["completion", "attention"],
                            "scheduled_polling": False},
            "prompt": _slot_instr("lens-sweep") + (
                f"Quick SWEEP of these lenses against the diff vs `{base}`: "
                f"{names}. Run each lens's top checks only — flag or clear in "
                f"one line each. READ-ONLY: write findings (each with a "
                f"`lens` field and a `class` field — regression|pre-existing|"
                f"observation) to `.em-review/lens-sweep/findings.json`, "
                f"change no code.\n" + _language_note(sweep_refs)
                + ctx_note + run_note + CLEAR_ALWAYS),
        }
        if sweep_refs:
            sweep_brief["language_references"] = sweep_refs
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
        if concern_outcomes is not None:
            for promotion in concern_outcomes["promotions"]:
                entry = decision[promotion["lens"]]
                entry["initial_verdict"] = entry["verdict"]
                entry["verdict"] = "deep"
                entry["promotion"] = dict(promotion)
                entry["evidence"] = list(entry.get("evidence") or []) + [
                    "adaptive promotion from bounded sweep: "
                    + promotion["evidence_ref"]
                ]
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
        if isinstance(review_policy, dict):
            out["review_depth_policy"] = copy.deepcopy(review_policy)
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
    if isinstance(review_policy, dict):
        out["review_depth_policy"] = copy.deepcopy(review_policy)
    if review_policy and review_policy.get("depth") == "quick-only":
        out["instruction"] = (
            "Dispatch the single QUICK sweep under its governed read-only "
            "contract. A complete quick output is sufficient collection "
            "evidence; dispatch no deep lens. Any substantive regression "
            "returns the same task for correction, then rerun this quick "
            "sweep against the stable corrected target."
        )
    if decision is not None:
        out["routing_decision"] = decision
    if concern_outcomes is not None:
        out["review_progression"] = concern_outcomes
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
