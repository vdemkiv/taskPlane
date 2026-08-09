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
    with open(path) as f:
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
        touched = {depgraph.module_of(f) for f in files or []}
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
          requirement_text=None) -> dict:
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
    breadth="all", or no stage_profiles key: the legacy path, byte-
    identical to today. Any engine failure falls back to legacy
    breadth="all" routing — MORE coverage, never less (the hub_signal
    fail-safe precedent) — and marks the degradation in context.
    """
    cat = catalog or load_catalog()
    v2 = (use_signals is not False
          and isinstance(cat.get("stage_profiles"), dict)
          and breadth != "all"
          and (stage is not None or use_signals is True))
    if v2:
        try:
            return _route_v2(changed_files, cat, stage=stage,
                             task_type=task_type,
                             artifact_type=artifact_type, only=only,
                             skip=skip, hub_dependents=hub_dependents,
                             workspace=workspace,
                             requirement_text=requirement_text)
        except Exception as exc:
            # Fail toward MORE review coverage, never less — and never
            # silently (the hub_signal precedent above). A broken engine
            # must widen the review, not shrink it.
            import sys
            print(f"taskplane: lens applicability engine unavailable "
                  f"({exc}) — falling back to legacy breadth=all routing "
                  "(fail-safe: more review coverage, not less). Repair "
                  "lens_signals to restore signal-driven routing.",
                  file=sys.stderr)
            if workspace:
                try:
                    tp.trace(workspace, "lens_engine_failed",
                             error=str(exc), stage=stage)
                except Exception:
                    pass
            fallback = _route_legacy(changed_files, task_type,
                                     artifact_type, cat, only, skip,
                                     "all", hub_dependents)
            fallback["context"]["lens_engine_failed"] = str(exc)
            fallback["context"]["degraded"] = (
                "applicability engine failed — legacy breadth=all routing")
            return fallback
    return _route_legacy(changed_files, task_type, artifact_type, cat,
                         only, skip, breadth, hub_dependents)


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

        deep = bool(_any_match(files, lens.get("deep_globs"))) or large
        if effort == "full":
            deep = True   # a full design pass runs as its own subagent
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
        selected.append(entry)

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
        },
    }


def _route_v2(changed_files, cat, *, stage, task_type, artifact_type,
              only, skip, hub_dependents, workspace,
              requirement_text) -> dict:
    """Signal-driven routing (v3 Phase 1). Candidates restricted to the
    stage profile; verdicts from lens_signals.route_verdicts (budget cap 8
    + security/architecture floors applied inside); legacy glob/task-type
    reasons merged with signal evidence. EVERY catalog lens gets an output
    entry — n/a included, carrying its negative evidence (coverage
    honesty). Raises on any engine problem; route() catches and fails
    open to legacy breadth=all."""
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

    # Applicability engine: verdicts for EVERY catalog lens, budget-capped
    # (hard cap 8, overflow demoted to light, never dropped), floors applied
    # after the budget (security on enforcement/boundary diffs;
    # architecture >= light on any code change).
    vmap = lens_signals.route_verdicts(
        workspace or ".", files, stage=stage,
        requirement_text=requirement_text)

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
            # halt v2 routing (route() then fails open to breadth=all).
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
        selected.append(entry)

    return {
        "lenses": selected,
        "context": {
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
        },
    }


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
                              capture_output=True, text=True).stdout

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


def dispatch_briefs(routing: dict, base: str = "HEAD",
                    max_actions: int = 30,
                    impact_context: str | None = None) -> dict:
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
    deep = [x for x in routing["lenses"]
            if x.get("tier") not in ("sweep", "light", "n/a")]
    sweep = [x for x in routing["lenses"]
             if x.get("tier") in ("sweep", "light")]
    briefs = []
    for x in deep:
        lid = x["id"]
        mtier = _lens_tier(lid, "deep")
        brief = {
            "id": lid, "name": x["name"], "tier": "deep", "agent": "tp-lens",
            "model_tier": mtier, "model": tp.model_for_tier(mtier),
            "task_slot": f"lens-{lid}",
            "output": f".em-review/lens-{lid}/findings.json",
            "contract": {"read_only": True,
                         "task_slot": f"lens-{lid}",
                         "write_allow": [f".em-review/lens-{lid}/**"],
                         "max_actions": max_actions},
            "prompt": _slot_instr(f"lens-{lid}") + _lens_prompt(x, base) + (
                "\nBLAST RADIUS (from the dependency graph - factor "
                "these dependents into your verdict):\n"
                + impact_context + "\n" if impact_context else ""),
            "looks_for": x.get("looks_for", ""), "checks": x.get("checks", []),
        }
        if "verdict" in x:   # contract:lens-brief — ADDITIVE v2 fields only
            brief["verdict"] = x["verdict"]
            brief["score"] = x.get("score")
            brief["evidence"] = x.get("evidence", [])
        briefs.append(brief)
    sweep_brief = None
    if sweep:
        names = ", ".join(s["name"] for s in sweep)
        sweep_brief = {
            "ids": [s["id"] for s in sweep], "agent": "tp-lens",
            "model_tier": "cheap", "model": tp.model_for_tier("cheap"),
            "task_slot": "lens-sweep",
            "output": ".em-review/lens-sweep/findings.json",
            "contract": {"read_only": True,
                         "task_slot": "lens-sweep",
                         "write_allow": [".em-review/lens-sweep/**"],
                         "max_actions": max_actions},
            "prompt": _slot_instr("lens-sweep") + (
                f"Quick SWEEP of these lenses against the diff vs `{base}`: "
                f"{names}. Run each lens's top checks only — flag or clear in "
                f"one line each. READ-ONLY: write findings (each with a "
                f"`lens` field and a `class` field — regression|pre-existing|"
                f"observation) to `.em-review/lens-sweep/findings.json`, "
                f"change no code.\n" + CLEAR_ALWAYS),
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
