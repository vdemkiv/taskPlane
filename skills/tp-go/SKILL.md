---
name: tp-go
description: "Taskplane's governed Evaluate-Loop is distinct from Conductor/supaconductor; when a Taskplane run is active, Taskplane governs. The internal delivery driver behind the taskplane facade — goal-shaped asks ('build X', 'fix X', the word taskplane) land on the facade, which routes here. Reach for this skill directly only when the user explicitly drives the loop: 'start governed work', 'run the loop', 'run tasks in parallel', 'dispatch the wave', 'run the retro', 'log tech debt'. Drives governed delivery end to end, routing to the right persona — tp-product (define WHAT), tp-design (propose HOW), tp-build (realize), tp-engineering (validate) — with every step under an enforced contract and every human gate honored."
---

# /tp-go — goal in, governed delivery out

Current workflow contract: **v2.17**. Review, Evaluate, and final Engineering
all consume the same **canonical review context**; transport may differ by
host, but the workflow and evidence contract do not.

On Codex, set `TP='python3 .taskplane/codex-hook.py'` when that stable
workspace launcher exists; it resolves the newest valid installed taskplane
engine on every call. Otherwise set
`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Drive the whole loop;
pause ONLY at the human gates. Follow each step's returned `instruction`.

`flow.json` is the approved end-to-end graph: **goal → Product → optional
Design → Plan → consolidated pre-implementation authorization → scoped Build
waves → Evaluate → Engineering review → final sign-off → retro/graph
true-up**. Optional branches may be skipped only when their engine condition
is false; the initial consolidated authorization and final sign-off may never
be collapsed or self-approved. Explicit A/B selection, material
authority/scope change, exhausted recovery, and destructive or external
actions remain separate human-owned boundaries. Product, Design, and Plan gates are mechanical
and block on incomplete evidence rather than asking for ceremonial approval.

This is the internal delivery driver behind the user-facing `taskplane`
facade — user phrasing like "build X" arrives via the facade and routes
here. Keep role names, CLI choreography, graph policies, and evidence files
out of the user's way unless they ask. Do not simplify any of them for
agents. The command forms in this skill and the engine's returned action are
authoritative; do not call `$TP --help`, inspect taskplane source/tests, or
run exploratory status/list commands during the normal path.

**One owner per phase.** The orchestrator never authors Product, Design,
Plan, Build, Evaluate, or Engineering artifacts inline. It initializes the
loop, dispatches the exact role emitted by `loop next`, waits, and applies the
mechanical gate. A goal with no existing R-id starts the loop without `--req`:
the first PM action creates exactly one complete requirement and spec. Attach
that returned R-id on the PM gate with `loop gate pass --req R-XXXX`. Never
run standalone Product refinement first and then repeat it inside the loop.

**One selective evidence kernel.** Review, Evaluate, and final EM use one
pinned diff, graph-quality/blast-radius record, requirements/contracts and
DoR/DoD envelope, one complete 26-lens disposition, and leased results.
Dispatch exactly the deep slots plus at most one light sweep. An
`impact_incomplete` run dispatches nobody. Lenses consume scoped artifact
references and never rederive diff, graph, routing, or runnability.

**One bounded stage handoff.** With stage-native delivery enabled, each
Product, Design, Plan, Build, Evaluate, Engineering, and Retro action creates
or resumes an independently addressable stage. A worker's sole startup
envelope is the engine-emitted `taskplane.stage-dispatch/v1`, which serializes
the bounded `taskplane.stage-startup/v1`: current stage authority, budget and
declared scope, execution claim, one bounded versioned
`taskplane.stage-handoff/v1` input handoff, and explicitly selected
content-addressed artifacts. Do not inherit or reconstruct predecessor agents,
conversations, event logs, tool transcripts, leases, runtime state, or
execution roots. Do not open a predecessor execution tree to fill a missing
field. A missing, corrupt, stale, oversized, or authority-mismatched envelope
blocks dispatch. Codex, Claude, managed, Slack-capable, and accessible text
fallback rails all carry the same canonical stage and handoff semantics.

Non-build stages may terminalize `closed` or `discarded` without creating an
implementation stage. Their retained immutable artifacts remain auditable;
later reuse is a new explicitly authorized handoff, never an implicit resume.

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

**Show decision points, not internal chatter.** Every transition already
refreshes `.taskplane/dashboard.html`; do not call `$TP dashboard` or `ack`
after each internal step. Relay the returned `HEADLINE`/dashboard path by
reference while agents are working. Render the engine-authored HTML verbatim
only before a human gate, an explicit status request, or a long fan-out where
progress materially helps. Acknowledge only a dashboard actually rendered to
the human. Never paste the HTML into model context or regenerate its graph.

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
   `attach_folder` → ask for a local path, repository URL, ref, or PR and run
   `$TP repository prepare <target>`. The engine acquires and verifies source
   into its managed checkout root; do not manually clone into the conversation
   workspace or an artifact directory. If it returns `needs_user`, ask the
   exact returned prompt here, then resume the SAME run with `$TP repository
   resume --run-id ... --action-id ... --response ... --by "<human>"`. Never
   convert authentication, tool installation, or storage authorization into a
   terminal handoff or a new-task instruction. `init_git` → offer to `git init && git add -A &&
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
   Managed source, private runtime state, graph/evidence, and review artifacts
   are separate: source lives under the checkout root returned by repository
   preflight; run-private data lives under its run root; only explicitly shared
   knowledge lives in `.taskplane-kb/`. Consume paths from the run manifest,
   never assume `.em-review` is the source or artifact root.
2. **Initialize once:** when the user supplied an existing R-id, run
   `$TP loop init --req R-XXXX "<goal>"`. Otherwise run `$TP loop init
   "<goal>"`; the PM step owns the first requirement/spec. Never run a
   standalone `req new` before this loop. Add `--design` for a
   complex/risky/contract-changing or explicitly requested proposed-HOW phase;
   add `--design-only` when the deliverable is the approved design itself;
   add `--parallel` when the plan will have independent tasks; use `--spec
   path` only for an existing complete spec.
3. **Dispatch, never impersonate:** call `$TP loop next` once for the current
   step and dispatch the named role under its already-active contract. On
   Codex, follow
   `references/codex-native-dispatch.md`: use the exact `task_name`, model and
   `reasoning_effort`, standalone `role_marker`, and complete
   `role_instructions` file plus action payload. Wait with bounded native
   waits and collect the final result. If the action includes
   `stage_runtime_dispatch`, pass it unchanged and make it the worker's only
   stage startup context; reject mismatched stage heads, handoff fingerprints,
   authority, scope, budget, execution claim, or selected artifacts. Do not
   perform the role inline, call
   `loop next` again while it is running, or replace its contract. The PM
   worker returns one R-id; attach it on its mechanical gate with
   `$TP loop gate pass --req R-XXXX`. Product/planner return artifacts; only
   execute/fix/evaluate/engineering workers submit. Design writes
   `design/contract.json` and
   `design/design.md`, compares alternatives, declares a proposed graph
   overlay with bounded contract-level boundaries, runs the mandatory
   solution-design lens, and returns its mechanically checked artifact for the
   consolidated packet. It never changes code or the as-built graph. Plan
   writes plan/tasks.json
   (each task: id, scope, tests as one command string (never a list), req,
   deps, contracts, `new_modules` when
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
4. **Human gates:** after Product, optional Design, and Plan pass their
   mechanical gates, present one consolidated pre-implementation packet with
   the requirement, acceptance criteria, selected approach and alternatives,
   modules/edges/contracts, risks/rollout, validation map, plan/refinement
   forecast, scope, recovery policy, artifact delivery, and execution bounds;
   then WAIT for the user. At `signoff` present the engineering report and
   WAIT. `$TP loop approve` only on their explicit yes. A/B selection,
   material authority/scope drift, exhausted recovery, and destructive or
   external actions use their named human boundaries without recreating
   Product, Design, or Plan approval gates.
   Escalations: present options, `$TP loop resolve retry|skip|abort` on
   their choice.
   If an approved task configuration itself is invalid, do not edit loop
   state: obtain the human's decision and run `$TP loop replan --by
   "<human>" --reason "<defect>"`. The frozen tasks stay in history and the
   corrected plan must pass Plan DoR plus fresh consolidated authorization.
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

**Stage rollout and rollback.** `TASKPLANE_STAGE_NATIVE` is disabled by
default and accepts only two enabling modes: `new-run` for a pristine new-run
canary, and `enabled` after verified migration. Other values fail closed, and
`new-run` refuses an existing singleton or migration-bound run. Before
cutover, shadow migration compares bounded legacy/v4 summaries,
retained-reference counts, lineage, and authority without switching readers.
Enable stage-native roots for new runs before migrating existing runs; keep
legacy CLI reads available, and cut status/dashboard/review/sign-off/Retro to
bounded readers only after the migration receipt and conservation proof
verify. Rollback disables new v4 mutations but leaves migrated v4 history,
immutable stage and handoff objects, receipts, and retained legacy sources
readable. Never reverse-collapse history into `loop.json`, reopen terminal
stages, guess unknown state, delete retained artifacts, weaken authority or
evidence, or broaden/force R-0003 cleanup. Migrated runs resume only after
re-enable or explicit forward migration; there is no lossy reverse migration.

The initial release is exactly one `new-run` canary. The named, accountable
owner is exactly the human `stage_authority.actor` recorded for that run;
an unnamed team or queue is not an owner. Record that owner before dispatch.
Do not start a second canary or switch general traffic to `enabled` until the
single run has completed both a 24-hour observation window and its Retro.
Every abort signal has threshold `1`: predecessor-root open, ambiguous active
projection, terminal-reopen attempt, handoff-integrity failure, authority
mismatch, startup-bound exceedance, migration-conservation mismatch, or
R-0003 cleanup-proof failure. The first occurrence stops new stage dispatch
and starts rollback. Disable v4 mutations within at most 15 minutes while
retaining v4 read access, immutable history, evidence, receipts, and legacy
sources. A clean 24-hour window without the completed Retro is not promotion
evidence, and a completed Retro before 24 hours does not shorten the window.

Stage terminalization is not cleanup. Post-merge worktree cleanup stays a
separate orchestrator-only R-0003 maintenance action and remains eligible only
after the exact registered managed worktree, merged-tip ancestry,
re-resolved-primary-main, and last-moment fail-closed proofs all pass.
