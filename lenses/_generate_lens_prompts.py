"""Generate lenses/<id>.md evaluator prompts from catalog.json + review guides.

The catalog stays the single source of truth for charter/boundary/globs; this
script merges in the hand-authored review guide per lens (what to examine,
what counts as a blocker vs major) and the shared verdict format. Re-run after
editing GUIDES or the catalog.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Hand-authored review guides. examine: the specific things this lens checks
# in a diff. blocker/major: severity anchors so verdicts are consistent.
GUIDES = {
 "security": dict(examine=[
   "Hardcoded secrets, keys, tokens — including in test fixtures and config.",
   "Injection at every input boundary: SQL, shell/command, template, path.",
   "AuthZ (not just authN) on every new/changed endpoint, route, or query — "
   "who is ALLOWED to call this, and is that checked server-side?",
   "Unsafe input handling: deserialization of untrusted data, eval/exec, "
   "unvalidated redirects, file uploads without type/size limits.",
   "Secrets or PII leaking into logs, error messages, or client responses.",
   "New/updated dependencies: known CVEs, typosquats, unpinned versions.",
   "Crypto misuse: home-rolled crypto, fast hashes for passwords, static IVs."],
  blocker="an exploitable path to data or code execution; a committed secret",
  major="a missing authz check with partial mitigation; a risky unpinned dep"),
 "code-quality": dict(examine=[
   "Correctness of the logic itself — trace the unhappy paths, off-by-ones, "
   "None/empty/boundary inputs through the new code.",
   "Error handling: no swallowed exceptions; failures carry context; "
   "cleanup (files, connections, locks) on every exit path.",
   "Names tell the truth — a function does what its name says, no more.",
   "Duplication: does this re-implement an existing helper? Divergent "
   "copy-paste is a future bug.",
   "Dead code, commented-out blocks, debug leftovers.",
   "Consistency with the codebase's established idioms and structure."],
  blocker="a correctness bug — wrong result or unhandled failure on a "
          "reachable path",
  major="a swallowed error, a misleading name, divergent duplication"),
 "testability": dict(examine=[
   "Seams: can collaborators (DB, network, clock, filesystem) be substituted "
   "without patching internals?",
   "Hidden coupling: globals, singletons, module-level state the tests can't "
   "reset.",
   "Determinism: real time, randomness, ordering assumptions, sleeps.",
   "Reachability: can a test drive every new branch from a public surface?",
   "Construction: can the unit be built in a test without dragging in the "
   "whole app?"],
  blocker="a new critical path that cannot be tested without monkey-patching "
          "internals",
  major="hard-wired clock/network/random in new logic; unresettable state"),
 "qa": dict(examine=[
   "Every acceptance criterion has a test that would FAIL if the behavior "
   "broke — point to the pair.",
   "Edge and negative cases: empty, maximum, malformed, duplicate, "
   "concurrent, unauthorized.",
   "Regression risk: existing behavior touched by the diff still covered.",
   "Test honesty: assertions actually assert; no sleep-based waits or "
   "order-dependent tests (flake patterns).",
   "The full user path (e2e or integration) for the feature, not only units."],
  blocker="an acceptance criterion with no failing-capable test evidence",
  major="happy-path-only coverage; a flaky pattern introduced"),
 "design": dict(examine=[
   "All states designed: loading, empty, error, partial, success — not just "
   "the happy screenshot.",
   "The interaction flow matches the intent: entry points, exits, "
   "back/cancel, destructive-action confirmation.",
   "Visual consistency: spacing/typography/color from the system's tokens, "
   "not magic values.",
   "Hierarchy: the most important thing reads first; affordances look "
   "actionable.",
   "Responsive behavior at real breakpoints."],
  blocker="a dead-end or unreachable state; destructive action without "
          "confirmation",
  major="a missing error/empty state; off-system visual values"),
 "accessibility": dict(examine=[
   "Keyboard-only: every interactive element reachable, visible focus, no "
   "traps, Escape closes overlays.",
   "Semantics: native elements first; ARIA roles/states honest and complete "
   "where used.",
   "Labels: inputs, buttons, icons, images all have accessible names.",
   "Focus management: dialogs trap and restore focus; route changes announce.",
   "Contrast meets WCAG AA; state not conveyed by color alone.",
   "Async updates announced (live regions) — spinners aren't silence."],
  blocker="an interactive element unreachable by keyboard or without an "
          "accessible name",
  major="contrast failure; focus lost on modal open/close"),
 "frontend": dict(examine=[
   "Component boundaries: props are an honest contract; no reach-ins.",
   "State: server state vs client state separated; caches invalidate; no "
   "stale-render on mutation.",
   "Render cost: re-render storms, heavy work in render without memo, "
   "unstable keys in lists.",
   "Data edge: loading/error handled where data enters; optimistic updates "
   "roll back.",
   "Bundle impact of new dependencies; code-split where heavy.",
   "Browser/device compat for the APIs used."],
  blocker="a state bug that renders wrong data or crashes a route",
  major="an unhandled fetch failure; a render hot-spot on a common path"),
 "backend": dict(examine=[
   "Business-logic correctness including edge conditions and race windows.",
   "Transactions: multi-write invariants atomic; partial-failure states "
   "impossible or recovered.",
   "Idempotency for anything a client or queue may retry.",
   "Data access: no query-per-item loops; predicates use indexes.",
   "Input validated at the boundary; internal calls trust only validated "
   "data.",
   "Downstream failures (DB, HTTP, queue) handled: timeout, retry policy, "
   "surfaced error."],
  blocker="a broken invariant (partial write, double-apply on retry)",
  major="an N+1 on a hot path; an unhandled downstream failure"),
 "mobile": dict(examine=[
   "Platform lifecycle: state survives backgrounding, rotation, process "
   "death.",
   "Offline and poor-network behavior: queued writes, conflict handling, "
   "user feedback.",
   "Battery/network cost: polling intervals, wakelocks, payload sizes.",
   "Permissions: minimal, requested in context, denial handled.",
   "Store-policy risks (background location, payments, private APIs).",
   "Main-thread discipline: I/O and decoding off the UI thread."],
  blocker="data loss on lifecycle events; a store-policy violation",
  major="unusable offline behavior; main-thread I/O jank"),
 "architecture": dict(examine=[
   "READ `knowledge/architecture.md` FIRST and judge the change against the "
   "documented model — never re-derive the architecture from the codebase.",
   "Boundary integrity: does a new dependency cross a layer/service line "
   "that was deliberately separate?",
   "Data flow & coupling: chatty call patterns, shared databases, implicit "
   "contracts between components.",
   "State & consistency: where state lives is explicit; consistency model "
   "(strong/eventual) chosen, not accidental.",
   "Failure modes of new edges: timeout, retry, backpressure, partial "
   "availability.",
   "Tech-choice fit: new tech earns its place; effort matches the tier "
   "(light = sanity-check the boundary; full = design pass as a subagent).",
   "UPDATE `knowledge/architecture.md` (or file a decision) when the shape "
   "changed — the model must stay current or the lens goes blind."],
  blocker="a silent violation of a settled boundary or recorded decision",
  major="new cross-component coupling left undocumented"),
 "scalability": dict(examine=[
   "Complexity of new hot paths against realistic data growth, not demo "
   "data.",
   "Unbounded work: queries without LIMIT, load-everything collections, "
   "unpaginated APIs.",
   "N+1 and fan-out patterns on request paths.",
   "Blocking calls (sync I/O, locks) inside latency-sensitive paths.",
   "Cache correctness: invalidation, stampede protection, key cardinality.",
   "Resource ceilings: connection pools, memory per request, file handles."],
  blocker="unbounded work on a hot path that grows with data",
  major="an N+1 or blocking call in a latency-sensitive route"),
 "sre": dict(examine=[
   "Will we KNOW it broke: logs with context, metrics, traces on the new "
   "path.",
   "Alerts: actionable, tied to symptoms users feel, not noise.",
   "Every new external call has a timeout, bounded retries with backoff, "
   "and a circuit/failfast strategy.",
   "Graceful degradation: what does the user see when this dependency is "
   "down?",
   "Runbook/rollback notes for the new failure modes."],
  blocker="a new external call with no timeout on a critical path",
  major="a silent failure mode — no log/metric distinguishes it"),
 "devops": dict(examine=[
   "Pipeline correctness: reproducible builds, honest cache keys, pinned "
   "action/tool versions.",
   "IaC hygiene: least-privilege on new resources, no drift from applied "
   "state, plan output reviewed.",
   "Environment parity: config via environment, not branched code.",
   "Secrets: never in code, config files, or CI logs; injected at runtime.",
   "Deploys reversible: a rollback path exists and is documented."],
  blocker="a secret in config/CI; an irreversible deploy step",
  major="over-privileged IaC; unpinned build inputs"),
 "dba": dict(examine=[
   "Engine fit: is the chosen database RIGHT for this workload? Apply "
   "references/database-selection.md (relational by default; a second "
   "engine must earn its place) — at plan time, not after the build.",
   "Schema design: normalization level deliberate; entities and "
   "relationships model the domain.",
   "Indexes match the actual query predicates introduced; no dead or "
   "duplicate indexes.",
   "Constraints (FK, unique, check) guard invariants at the database, not "
   "only in app code.",
   "Data types right-sized; no stringly-typed dates/enums/money.",
   "Query plans for new heavy queries; partitioning/archival for tables "
   "that will grow."],
  blocker="an invariant enforceable by the DB left to app code on critical "
          "data",
  major="a new query with no supporting index; wrong type for money/time"),
 "data-safety": dict(examine=[
   "Migrations additive and reversible (expand/contract); destructive steps "
   "separated and gated.",
   "Existing rows: NULL/default handling for new columns; backfill plan "
   "with verification.",
   "Cascades and ON DELETE behavior reviewed against real relationships.",
   "Lock budget: no long table locks on hot tables (online migration "
   "strategy where needed).",
   "Rollback actually tested — down-migration or documented recovery."],
  blocker="a destructive migration without backfill + verified rollback",
  major="a long lock on a hot table; unhandled NULLs for existing rows"),
 "integrability": dict(examine=[
   "Contract changes versioned; nothing existing consumers parse is "
   "silently changed or removed.",
   "Errors structured and documented: codes, retryability, machine-readable "
   "shape.",
   "Timeout and retry semantics stated for consumers (idempotency keys "
   "where retries are expected).",
   "Schema evolution additive; openapi/proto/schema files updated with the "
   "change.",
   "Pagination, filtering, naming consistent with the API's existing "
   "conventions."],
  blocker="a breaking change to a published contract without a version",
  major="undocumented error shapes; contract files out of sync with code"),
 "product": dict(examine=[
   "Does the change deliver the requirement's USER value — not just its "
   "letter? Judge against the R-record.",
   "Scope fidelity: gaps (acceptance criteria unmet) and creep (work no "
   "requirement asked for).",
   "Journey completeness: the user can finish the flow, including failure "
   "exits and recovery.",
   "Success metrics: is the outcome instrumented so we'll know it worked?",
   "Copy and naming make sense to the user, not the implementer."],
  blocker="an acceptance criterion unmet; the user cannot complete the "
          "core journey",
  major="silent scope creep; an unrecoverable failure exit"),
 "project-management": dict(examine=[
   "Dependency order across tasks is sound; nothing builds on an unbuilt "
   "assumption.",
   "Risk fronted: the riskiest/unknown work scheduled first, not last.",
   "Rollout: flags, phased exposure, and a rollback plan for user-facing "
   "change.",
   "Cross-team/consumer impacts flagged and communicated.",
   "Delivery readiness: docs, migrations, comms accounted for in the plan."],
  blocker="a dependency inversion that invalidates the plan",
  major="riskiest work scheduled last; no rollback plan for a user-facing "
        "change"),
 "tech-writer": dict(examine=[
   "Docs updated to match the behavior THIS diff changes — README, API "
   "reference, changelog.",
   "Examples run as written (commands, snippets, versions).",
   "Terminology consistent with the rest of the docs; no new synonyms for "
   "old concepts.",
   "A decision made here is recorded (ADR/KB), not buried in a PR comment.",
   "Changelog entry says what a USER can now do differently."],
  blocker="docs now actively wrong about changed behavior",
  major="examples that no longer run; a decision left unrecorded"),
 "privacy-compliance": dict(examine=[
   "New personal data collected: lawful basis, minimization (do we need "
   "it?), and where it flows.",
   "Retention and deletion: the new data is coverable by "
   "export/delete-my-data paths.",
   "Consent honored for new tracking/analytics; defaults respect it.",
   "Residency: where the new store/processor keeps data.",
   "PII kept out of logs, analytics events, and error reports.",
   "License exposure of new dependencies (copyleft in a commercial "
   "product)."],
  blocker="PII collected/stored with no deletion path or lawful basis; "
          "license contamination",
  major="PII in logs; tracking that ignores consent state"),
 "cost-finops": dict(examine=[
   "Right-sizing: new compute/storage sized to measured need, not defaults.",
   "Autoscaling has upper bounds; nothing can scale-to-bankruptcy.",
   "Egress and cross-region traffic implications of new data flows.",
   "Storage lifecycle: TTL/archival for data that only grows.",
   "Per-call cost of new external/metered APIs (including LLM calls) "
   "bounded and monitored."],
  blocker="an unbounded metered resource (autoscale/egress/API) with no cap",
  major="significantly over-provisioned defaults; no lifecycle on growing "
        "storage"),
 "tech-strategy": dict(examine=[
   "STRATEGY LEVEL ONLY — judge the requirement/roadmap/plan, never the "
   "diff; code belongs to the engineering lenses.",
   "Enablement: does this work unlock future tracks (platform, tooling, "
   "data foundations) or is it a dead-end special case?",
   "Build-vs-buy: is there an off-the-shelf/OSS answer that changes the "
   "plan materially? State why building wins if so.",
   "Strategic debt: any quick-mode choice here that mortgages the "
   "platform — is it recorded as debt with a payoff plan?",
   "Capability fit: can the team (human + agents) actually operate what "
   "this introduces (new language, new infra, new vendor)?",
   "Technology bets: new tech earns its place with a reason tied to the "
   "roadmap, not novelty."],
  blocker="a plan that forecloses a stated future track or bets on tech "
          "the org cannot operate",
  major="build chosen over an obviously adequate buy without rationale; "
        "strategic debt taken silently"),
 "cost-roi": dict(examine=[
   "STRATEGY LEVEL ONLY — economics of the requirement, not the code.",
   "Cost estimate: use the heuristic (size × applicable lenses × mode) to "
   "state a relative effort band (small/medium/large) — a sizing signal, "
   "not a currency figure.",
   "Return hypothesis: what value (revenue, retention, cost saved, risk "
   "retired) and by when? A requirement with no value hypothesis is a gap.",
   "Cheaper alternative: would quick-mode + tracked debt, a smaller scope, "
   "or a manual process capture 80% of the value?",
   "Run cost: ongoing infra/licence/maintenance cost after shipping, not "
   "just build cost.",
   "Portfolio view: what does saying yes to this defer?"],
  blocker="committing large build cost with no stated value hypothesis",
  major="full mode chosen where quick+debt is clearly the economic call; "
        "run cost ignored"),
 "business-alignment": dict(examine=[
   "STRATEGY LEVEL ONLY — the org's goals, not the feature's pixels.",
   "Goal linkage: the requirement names the OKR/ARR/goal it serves; "
   "'nice to have' is an answer, but it must be explicit.",
   "Customer success: effect on existing customers (support load, "
   "migration pain, trust) not just new-sale appeal.",
   "Innovation balance: is the portfolio drifting all-maintenance or "
   "all-novelty? Flag the skew, not the single item.",
   "Focus: what the org stops or delays by doing this — a yes is a no to "
   "something; name it.",
   "Measurability: will we know it moved the goal (metric + owner)?"],
  blocker="work that contradicts a stated goal/OKR or silently displaces "
          "committed work",
  major="no goal linkage stated; no way to measure whether it worked"),
 "i18n": dict(examine=[
   "User-visible strings externalized to the locale system — none inlined.",
   "No sentence-building by concatenation; templates with named "
   "placeholders.",
   "Dates, numbers, currency formatted per locale, not hardcoded formats.",
   "Pluralization via the locale rules (not `count > 1`).",
   "Layout survives RTL and 2× string length.",
   "Timezones explicit: stored UTC, displayed local."],
  blocker="a user-facing flow that cannot be localized (hardcoded strings "
          "in core UI)",
  major="concatenated sentences; `n > 1` pluralization; TZ-naive datetimes"),
}

# Deep references shipped with the plugin (lenses/references/*) — appended
# to the evaluator prompt of the lenses that own them.
REFS = {
 "security": """## Deep methodology (subagent mode / high-stakes surfaces)

Follow `lenses/references/security-methodology.md` — the full procedure:
scanner gate first (gitleaks, ecosystem CVE audit, semgrep/bandit/gosec),
then OWASP Web Top 10 (2021) passes incl. access control & RLS, injection,
auth/session, data protection — and the OWASP LLM Top 10 (2025) passes when
the change touches an AI surface (prompt-injection input guard included).
Grade findings by its severity table; a scanner that cannot run is itself a
finding.""",
 "dba": """## Deep references

- **Engine choice** (requirement/plan time): follow
  `lenses/references/database-selection.md` — four workload questions,
  relational-by-default, scenario table, polyglot red flags. Record the
  choice to the KB.
- **Migration scripts**: the schema QUALITY side of
  `lenses/references/migration-scripts.md` §5 (hygiene) and §4 (data
  correctness) — safety belongs to data-safety; don't double-grade.""",
 "data-safety": """## Deep reference — migration scripts

Follow `lenses/references/migration-scripts.md` in full: expand/contract
as the only safe shape, lock analysis on hot tables, tested reversibility,
data correctness for existing rows, idempotency. Its severity anchors
override the generic ones below for migration files.""",
 "design": """## Deep audit (subagent mode / UI-heavy changes)

Follow `lenses/references/ui-audit.md` for the full pass: state inventory
(loading/empty/error/partial/success per surface), flow walk (entry → happy
→ failure → recovery → exit), consistency sweep (tokens, spacing scale,
type ramp), and the usability heuristics checklist. Hand a11y findings to
the accessibility lens — note, don't grade them.""",
 "code-quality": """## Language delegation (apply first)

Detect the changed files' language and apply the matching deep reference in
`lenses/references/` **in addition to** the examine list above:

| Changed files | Reference |
|---|---|
| `.ts` / `.tsx` | `typescript-code-quality.md` |
| `.py` | `python-code-quality.md` |
| `.go` | `go-code-quality.md` |
| other / mixed | the generic examine list; name unknown-language files |

Each language reference carries its own Reuse & Duplication section (run a
copy-paste detector, e.g. `jscpd`) — new code must reuse existing
helpers/components/types, not re-implement them. Deep security review is the
security lens's job (see its methodology); don't duplicate it here.""",
}

VERDICT = """## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "smallest fix that resolves it"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle."""

USAGE = """## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON."""


def build(lz):
    g = GUIDES[lz["id"]]
    lines = [f"# {lz['name']} lens", ""]
    lines += [f"**Group:** {lz['group']}",
              f"**Charter:** {lz['charter']}",
              f"**Does NOT own:** {lz['boundary']}", ""]
    lines += ["## Looks for", lz["looks_for"], ""]
    fires = []
    if lz.get("globs"):
        fires.append("- files match: " + ", ".join(lz["globs"]))
    if lz.get("task_types"):
        fires.append("- task types: " + ", ".join(lz["task_types"]))
    if lz.get("baseline"):
        fires.append("- baseline: yes (any code change)")
    if lz.get("deep_globs"):
        fires.append("- runs as **subagent** when: "
                     + ", ".join(lz["deep_globs"]))
    if fires:
        lines += ["## Fires when"] + fires + [""]
    if lz.get("checks"):
        lines += ["## Deterministic checks (run before the LLM perspective)"]
        lines += [f"- {c}" for c in lz["checks"]] + [""]
    lines += ["## Evaluator prompt", "",
              f"You are reviewing this change through the **{lz['name']}** "
              f"lens only. Your charter: {lz['charter']}. Stay inside it — "
              f"anything under “{lz['boundary']}” belongs to that "
              "lens; note it in one line and move on.", "",
              "Examine, with file:line evidence:", ""]
    lines += [f"{i}. {item}" for i, item in enumerate(g["examine"], 1)]
    if lz["id"] in REFS:
        lines += ["", REFS[lz["id"]]]
    lines += ["", f"**Blocker** = {g['blocker']}.",
              f"**Major** = {g['major']}.",
              "Minor = worth fixing, doesn't gate. Prefer the smallest "
              "suggestion that resolves each finding.", "",
              USAGE, "", VERDICT, ""]
    return "\n".join(lines)


cat = json.load(open(os.path.join(HERE, "catalog.json")))
missing = [lz["id"] for lz in cat["lenses"] if lz["id"] not in GUIDES]
assert not missing, f"no review guide for: {missing}"
for lz in cat["lenses"]:
    with open(os.path.join(HERE, lz["id"] + ".md"), "w") as f:
        f.write(build(lz))
print(f"wrote {len(cat['lenses'])} lens prompts")
