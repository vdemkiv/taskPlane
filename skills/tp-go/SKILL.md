---
name: tp-go
description: "The internal delivery driver behind the taskplane facade — goal-shaped asks ('build X', 'fix X', the word taskplane) land on the facade, which routes here. Reach for this skill directly only when the user explicitly drives the loop: 'start governed work', 'run the loop', 'run tasks in parallel', 'dispatch the wave', 'run the retro', 'log tech debt'. Drives governed delivery end to end, routing to the right persona — tp-product (define WHAT), tp-design (propose HOW), tp-build (realize), tp-engineering (validate) — with every step under an enforced contract and every human gate honored."
---

# /tp-go — goal in, governed delivery out

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Drive the whole loop;
pause ONLY at the human gates. Follow each step's returned `instruction`.

This is the internal delivery driver behind the user-facing `taskplane`
facade — user phrasing like "build X" arrives via the facade and routes
here. Keep role names, CLI choreography, graph policies, and evidence files
out of the user's way unless they ask. Do not simplify any of them for
agents. The CLI surfaces named below are current as of v2.7 — verify against
`$TP --help` before citing anything not listed here.

**New in v2.14 — one selective evidence kernel across every review stage.**
Graph quality and bounded caller impact are established before routing all 26
lenses. Dispatch is exactly the deep set plus at most one light sweep; an
`impact_incomplete` run dispatches nobody. Review, Evaluate, and final EM reuse
one immutable diff/impact/requirements/DoR/DoD envelope, deterministic scoped
views, leased lens results, and one canonical revision. Never remap or rederive
those facts inside a lens. Claude and Codex consume byte-equivalent canonical
artifacts; a newly installed build needs a new host task to load lifecycle
hooks before provenance can pass.

**In v2.13 — one opening call, one copy of the context.**
`tp review start <target> --base <ref>` returns tools, target pin, graph,
impact, contract, obligations, routing, runnability and the briefs as one
payload. Large artifacts come back as `RENDER-BY-REFERENCE: <path>` —
deliver the file, do not paste it. `--max-tokens` sets an effective-token
ceiling read from the host's transcript.

That payload is the canonical review context for standalone Review, per-task
Evaluate, and final engineering review: one diff, one graph-quality/blast-radius
record, one requirements/contracts and DoR/DoD record, one 26-lens disposition,
then exactly the deep slots plus at most one light sweep. Agents consume scoped
artifact references and never re-derive diff, graph, routing, or runnability.
Insufficient graph evidence is `impact_incomplete` and dispatches zero agents;
it never expands to all lenses.

**In v2.12 — bind the review to a tree.** `tp new --target <pr>
--fetch --base <ref>` fetches `pull/N/head`, pins the checkout, and writes
the pin into the contract; findings cite it in `meta.target`. `tp target
tools` says whether `git` and `gh` are present and authenticated, and
`--install` installs gh through the host's package manager.

**In v2.11 — routed, not exhaustive.** `lens dispatch` asks the
applicability engine which lenses this change actually summons; unrouted
lenses get no agent and carry the evidence for why. `--all` still forces the
whole catalog and now says that it disables the engine. Also: `tp ack` is
unmetered, a budget's last actions are reserved for closing rather than
spent on work, and `tp init` ignores runtime paths via `.git/info/exclude`
instead of dirtying a reviewed repo's `.gitignore`.

**In v2.10 — a fan-out of lens agents actually fans out.** Sibling lens
contracts (every member read-only, every write-allow under one common root)
now merge their write-allows and SUM their budgets, instead of intersecting
to the empty set and handing six agents one agent's action budget. The
findings headline reports the engine's blocking split — `N BLOCK (R·H·P·O)`
— rather than a severity count that can read `0 high` over a regression.
`graph impact` sees intra-repo Go: a root `go.mod` is consumed as a module
PREFIX instead of being skipped.

**In v2.9 — a run declares what it owes you, and cannot close without
it.** `tp new --owes <run-type>` records the artifacts a run owes BEFORE the
work begins; taskplane's own completion commands stay blocked until each is
shown and acknowledged. Doing the work is never blocked — only declaring it
finished. `TASKPLANE_OBLIGATIONS=off` disables the block while still
recording.

**In v2.8 — trust the graph, and watch the fan-out.**
Module ids now come from build manifests where a repo declares them, so on a
monorepo `graph impact` answers `@acme/ui` rather than an invented `ui` — an
id you can carry back to the codebase. Markdown skills/agents/lenses, SQL,
IaC and CI are graph nodes with their files, and references between
components (a skill naming an agent, a module reading a catalog) are edges,
so blast radius covers the non-code half of a repo for the first time. Two
consequences for you: the graph tab will be much denser than you remember,
and a `--all` review now DEMOTES lenses past the deep cap to inline rather
than dispatching a subagent each — everything still runs, and each demotion
records why. A `tests_pass` satisfied by CITING an identical-content run says
so in the DoD output now; if you are signing off, read that line. And when
the dashboard fails to render, the payload says so explicitly instead of
silently omitting the field — do not present a stale board as current.

**New in v2.7 — the lenses got sharper and the fan-out got a budget.**
All 26 lenses were rewritten against current industry practice. Two things
change what you will see: many lenses now carry an ABSTAIN rule and will
return no findings on a diff they have nothing to say about — that is the
lens working, not a lens failing — and several carry a standing caveat that
bounds what they may claim (a coverage percentage, for instance, is never on
its own a blocker). Twelve lenses previously could not fire on the change
class they exist to judge; that is fixed, so expect security to fire on CI
workflows and lockfiles, i18n on components rather than only locale files,
and `qa` on a change that adds no test at all. Review cost is now pinned in
CI (`scripts/ci_loop_cost.py`) alongside per-task cost, so widening a lens's
routing is a decision someone makes on the record.

**New in v2.6 — stop paying twice for the same evidence.** At the
evaluate step, START with `$TP loop evidence --write`: one call returns
the suite result, the diff, and the exact criteria, routed-lens and
graph obligations the gate will demand, with every judgment slot left
EMPTY. Do not rebuild any of that by hand — hand-assembly cost about
sixty shell calls per evaluation and produced nothing the engine did
not already hold. The bundle states obligations; it never discharges
one, and a bundle submitted unchanged is refused at the gate.
The DoD test command is now cited rather than re-run when an identical
run over byte-identical content already exists (same command, same
engine, same governing env); `TASKPLANE_NO_SUITE_CACHE=1` forces a
real run. And a finding may block a gate only if it carries a claim —
trigger, outcome, repro — so commentary stops reading like a bug.

**Model tiers.** Each `loop next` payload and each `lens dispatch` brief carries
an exact Codex-safe `task_name`, the taskplane `role`/`agent`, a `model` (a
concrete id, or `null` = inherit), and tier-derived `reasoning_effort`.
On Codex, pass those exact native dispatch fields and omit `model` when null.
A planner marks a simple task
`"model": "cheap"` in tasks.json to route just that task cheaper. Never pin a
model in agent frontmatter — the pin lives only at the dispatch call, which is
what keeps taskplane portable. Full detail: `discipline/model-tiers.md`.
The complete Codex spawn/wait/interrupt procedure is mandatory:
`references/codex-native-dispatch.md`.

**Four user intents, one driver — route by the ask, combine freely:**

| The ask is about… | Persona | Skill |
|---|---|---|
| WHAT to build, requirements, change requests | tp-product | `../tp-product/SKILL.md` |
| HOW a new feature or approach should work before code | tp-designer | `../tp-design/SKILL.md` |
| BUILDING something new (spec-first, visual-first, optional A/B variants) | tp-build | `../tp-build/SKILL.md` |
| whether built work is SOUND — review, impact, sign-off, retro | tp-engineering | `../tp-engineering/SKILL.md` |

The loop dispatches them automatically (`pm` = tp-product, `design` =
tp-designer, `em` = tp-engineering). Reach for tp-design when the proposed HOW
needs alternatives, dependency/contract decisions, or rollout evidence before
implementation; reach for tp-build whenever the goal is a new feature rather
than a fix or review.

**SHOW THE WORK — render the live dashboard at every transition.** Use the
host's inline HTML/widget capability when available. When it is not, relay the
`HEADLINE:` and provide `.taskplane/dashboard.html` as the dashboard artifact;
never pretend unavailable widget buttons were shown.

**Progress-first, not result-only.** Render BEFORE a burst of work, not
just after it. When you're about to dispatch agents (a parallel wave, a
lens fan-out), render the "starting" board FIRST so the person sees the
work forming — then render again as it lands. A dashboard that only
appears at the end is the failure mode; the whole point is to watch
progress. If a step will take several tool calls, show the board going in.

After each `loop next`, `loop submit`, `loop gate`, `loop wave`, and
`loop approve`:
0. **The fragment is already on disk.** Every successful `loop gate` /
   `loop next` refreshes `.taskplane/dashboard.html` and returns a
   `dashboard` field in its JSON — rendering is part of the flow, not an
   optional extra call. Read that file (or run `$TP dashboard`) and SHOW it;
   never skip a transition. The board now also carries the **step journey**
   (click any traversed step for its execution + decision detail) and an
   always-on **stats band with the agent→model table** (who ran which
   step/lens on which model — expected vs dispatched).
1. `$TP dashboard` — prints the mission-control HTML fragment. Four tabs:
   **loop** (governance rail PM→Design→Approve Design→Plan→Approve→Build→EM→Sign-off→Done,
   with Design omitted for simple direct builds; inside
   Build, one lane per task showing its own build → evaluate ⟲ fix
   mini-pipeline — parallel lanes visible side by side — plus live feed and,
   at `plan_approval`/`signoff`/`escalated`, gate buttons wired to
   `sendPrompt`), **stats** (agents/steps/waves/fixes/blocks + KB counts),
   **graph** (hubs + blast radius of the current scope), **context**
   (requirement, acceptance criteria, routed lenses, recent decisions, debt).
2. Put the decision context in TEXT first (what happened, what's the call),
   THEN call `mcp__visualize__show_widget` when available, with that fragment
   as `widget_code`, as the LAST thing in the reply. Otherwise link the
   refreshed `.taskplane/dashboard.html` artifact after the text. Title:
   `taskplane_<goal-slug>_<step>` — UNIQUE per render; a repeated title
   updates the earlier widget in place instead of drawing a new one at the
   current position.

3. **Acknowledge it: `$TP ack <id>`.** Every transition payload carries
   `dashboard.obligation` — an id the engine recorded when it built the
   artifact. The engine can render, write and point at the dashboard; it
   cannot see whether it reached a human, because `show_widget` happens in
   the host. So an obligation left unacknowledged is RECORDED AS NOT SHOWN,
   and `scripts/ci_evals.py` counts it. This is not a gate: skipping the ack
   blocks nothing, costs nothing, and refuses nothing. It only means the
   session's record says the human never saw the board — which is the
   complaint this whole mechanism exists to make visible instead of
   deniable. Acknowledge what you actually showed, and nothing else: `tp ack`
   reads the fingerprint off the artifact the obligation names, so citing
   your own hand-built chart instead is recorded as a substitute, not a
   success. `$TP ack --status` lists what is still open.

**Render contract (v1.5.3/4) — the same flow every taskplane command uses.**
`$TP dashboard` prints a `HEADLINE:` line first — relay it to the human as
plain text, always: it is the never-skippable carrier of step + gate +
lens/graph coverage, so the status lands even if a render is skipped. For an
unusually large board use `$TP dashboard --paged` (ordered ≤14 KB pages) and
render EACH page in order via `show_widget`, each page's html VERBATIM
(no edits, restyling, or re-authoring) — never collapse them into one
giant widget and never replace them with a prose recap. The loop board's
**context tab shows the full lens catalog** (sourced from `catalog.json`, so
a newly added lens appears automatically) and the **graph tab shows blast
radius**; if the graph is empty on a polyglot repo, say so — don't omit it.

**Shared progress artifacts (v2.0.0).** Every `loop gate`/`next`/`approve`/
`retro` also snapshots the decision artifacts (dashboard, plan, findings,
graph, `HEADLINES.md`, retro) into the active store — the payload's
`artifacts.path` names the folder. On a team/enterprise store that folder is
inside `.taskplane-kb/`: COMMIT it with the work, so the org sees progress
from a fresh clone. Treat it as a context cache too — before re-deriving
plan/review/graph state, read the snapshot; it's cheaper than recomputing.

At a human gate, STOP after showing the widget or dashboard artifact. Widget
buttons can drive the next prompt where supported; otherwise ask for the same
explicit approval in conversation. Never run the loop silently.

0. **Cold start (nothing attached yet):** FIRST run `$TP onboard --json`.
   If `ready` is false, don't dive in — show the onboarding dashboard
   (`$TP onboard` prints the fragment) inline via `mcp__visualize__show_widget`
   and help with the one missing piece its `next_action` names:
   `attach_folder` → the user needs to connect/open a folder or give you a git
   URL to clone. In Codex CLI, start from the repo directory; in the desktop
   app, open/create a local environment for that repo and start a new task
   after installation. Then re-check. `init_git` → offer to `git init && git add -A &&
   git commit` for them (gates need a snapshot); `tp_init` → run step 1.
   The buttons drive this via `sendPrompt`. Don't guess a workspace — a
   governed run needs a real folder + a git commit, and this is where a
   brand-new user gets them in place.
   If the report includes `artifacts`, read the latest snapshot before
   re-deriving plan, review, graph, or progress state; it is the durable
   cross-session/team handoff.
1. **Setup (once a folder + repo exist):** if `knowledge/context/` is
   missing, run `$TP init` yourself (details: `references/setup.md`) and fill
   the three context docs from the conversation — only ask what you can't
   infer.
2. **Requirement (the product phase):** author it WITH the user and score
   it — full procedure in `../tp-product/references/requirements.md`
   (record, score, refine on gaps, quick vs full with tracked debt). For a
   NEW FEATURE, follow `../tp-build/SKILL.md` instead: a north-star check first for
   significant ones, refine until the forecast is clean, render a visual
   mock of the spec BEFORE building.
3. **Loop:** `$TP loop init --req R-XXXX "<goal>"` (add `--design` for a
   complex/risky/contract-changing or explicitly requested proposed-HOW phase;
   add `--design-only` when the deliverable is the approved design itself;
   add `--parallel` when
   the plan will have independent tasks; `--spec path` if a spec exists).
   Then repeat `$TP loop next` and DO what its `instruction` says, playing
   the named role under its activated contract. On Codex, follow
   `references/codex-native-dispatch.md`: use the exact `task_name`, model and
   `reasoning_effort`, and preserve the complete `role_instructions` file plus
   action payload; never improvise a reduced role prompt. Design writes
   `design/contract.json` and
   `design/design.md`, compares alternatives, declares a proposed graph
   overlay with bounded contract-level boundaries, runs the mandatory
   solution-design lens, and stops for human approval. It never changes code
   or the as-built graph. Plan writes plan/tasks.json
   (each task: id, scope, tests, req, deps, contracts, `new_modules` when
   applicable, and typed `impact_policy`), execute builds TDD-first
   (`discipline/tdd.md`) honoring the primed lenses, evaluate proves
   criteria + runs routed lenses and dispositions graph impact — its briefs
   are routed with `stage="build"` (route v2: build-profile candidates
   scored against the wave's real diff, cap-8 budget, floors, evidenced
   n/a; when a component layer exists — `tp graph scan --decompose` — the
   touched components assemble the candidates and each routed lens names
   its proposers). The engineering review uses the same canonical review
   context and exact selective routing decision, and every Nth completed
   review (default 5,
   `TASKPLANE_AUDIT_EVERY`) also runs the full-catalog audit sweep: a
   finding on a lens the router marked n/a auto-files as a router
   regression that blocks sign-off. Full routing detail:
   `docs/routing-and-flows.md`.
   Execute/fix/evaluate/engineering workers
   end with `loop submit` and stop; the orchestrator alone calls `loop gate`
   and trusts only the engine's recomputed evidence — the canonical
   submit/gate/human-checkpoint invariants live in
   `../taskplane/references/harness-rules.md`. Product/planner return their
   artifacts for the orchestrator's mechanical gate.
   If a Design Contract is approved, each proposed dependency edge is copied
   into the owning task's `design_edges` as `FROM->TO:KIND`; the plan gate
   checks the complete set along with modules, contracts, depth, and criteria.
4. **Human gates:** at `design_approval` present the selected approach,
   alternatives, modules/edges/contracts, risks/rollout, validation map, and
   useful visual, then WAIT for the user. At `plan_approval` present the plan
   + refinement forecast and WAIT; at `signoff` present the engineering
   report and WAIT. `$TP loop approve` only on their explicit yes.
   Escalations: present options, `$TP loop resolve retry|skip|abort` on
   their choice.
   **Visual sign-off (UI changes):** if the change touched a UI (any task
   with `type: ui`, or a diff under a client/component/screen path), don't
   sign off on a diff alone — RENDER THE FIXED SCREEN. Boot the real app
   and screenshot it when possible; otherwise build a faithful,
   self-contained HTML mock of the changed view populated with mock data
   (reproduce the components' actual classes; inline the CSS — CDNs may be
   blocked), and show it inline via `mcp__visualize__show_widget` right
   above the sign-off gate so the human reviews the working result, not
   just the code. State what's mocked. The visual IS part of the sign-off.
5. **Parallel:** when `loop next` returns a wave, follow
   `references/parallel.md` (worktree + claim + one governed subagent per
   task, commit before submitting, orchestrator gate, merge on evaluate PASS — EXCEPT entries
   with `merge_on_pass: false`: those are A/B variants, never merge them).
   **Stage emitters:** `$TP loop wave --emit workflow|task|auto` (execute)
   and `$TP loop next --emit workflow|task|auto` (evaluate/fix) pick the
   dispatch rail — the review fan-out has the same switch on
   `$TP lens dispatch`. On a Claude Code host with Dynamic Workflows the
   whole stage can run as ONE journaled, resumable workflow run; the
   Task-dispatch payload stays the mandatory fallback, byte-identical, and
   the only Codex path. Detection is `workflow_available()` alone (opt in
   with `TASKPLANE_WORKFLOWS=1`; any of 0/false/no/off disables). Workflows
   are transport only: same contracts, same briefs, same gates — no gate is
   reachable only via workflows, and human gates stay conversation-level.
   When all variants pass, the loop pauses at the native `selection` gate:
   present both variants rendered side by side, then
   `$TP loop select <variant|hybrid> --note "why"` on the human's choice —
   full procedure in `../tp-build/references/variants.md`.
   On Codex, spawn independent entries concurrently, wait for and collect all
   requested results, and interrupt/escalate stalled or mis-scoped agents as
   specified in `references/codex-native-dispatch.md`. For a long governed run,
   recommend optional user-started `/goal`; Goal mode never replaces a gate.
6. **Finish:** after sign-off run the retro per `references/retro.md`,
   then `discipline/finishing-work.md` (debt, graph rescan, track close).
