---
name: tp-go
description: "The internal delivery driver behind the taskplane facade — goal-shaped asks ('build X', 'fix X', the word taskplane) land on the facade, which routes here. Reach for this skill directly only when the user explicitly drives the loop: 'start governed work', 'run the loop', 'run tasks in parallel', 'dispatch the wave', 'run the retro', 'log tech debt'. Drives governed delivery end to end, routing to the right persona — tp-product (define WHAT), tp-design (propose HOW), tp-build (realize), tp-engineering (validate) — with every step under an enforced contract and every human gate honored."
---

# /tp-go — goal in, governed delivery out

## Initialize this entry

Before following this skill, apply [common entry initialization](../taskplane/references/entry-initialization.md)
using entry point `tp-go`. Every direct invocation rechecks readiness;
sealed stage and lens workers validate their supplied startup instead.

## Focused routing invariant

Every delivery uses Product/Design minimum-sufficient focused routes,
Plan exactly three or four quick lenses for non-trivial work, and
Build/Fix/Evaluate/EM zero lens workers. Product, Design, and Plan record all
26 dispositions, but only selected execution dispositions launch workers. If
more than four independent Plan risks remain, split the scope or stop for
authenticated expanded-route approval naming the added lenses and cost.
Evaluate performs direct evidence judgment only over the sealed diff, tests,
criteria, graph impact, requirements/contracts, Design conformance, and
provenance. Zero-lens stages remain zero on success, failure, cancellation,
interruption, and handoff.

Current workflow contract: **v2.17**. Review, Evaluate, and final Engineering
all consume the same **canonical review context**; transport may differ by
host, but the workflow and evidence contract do not.

Use the installed engine selected by [common entry initialization](../taskplane/references/entry-initialization.md); `$TP` below denotes that CLI invocation. Drive the whole loop; pause ONLY at the human gates.
Follow each step's returned `instruction`.

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
Plan, Build, Evaluate, or Engineering artifacts inline. It initializes from
the exact selected requirement, dispatches the role emitted by `loop next`,
waits, and applies the mechanical gate. Product refines the supplied requirement
through its declared candidate output; it does not create another requirement
or replace the run's input. Missing requirement identity is a named startup
precondition, not permission to scan old work or initialize without `--req`.

**One direct evidence kernel.** Evaluate and final EM use one pinned diff,
graph-quality/blast-radius record, requirements/contracts, DoR/DoD envelope,
tests, Design conformance, and provenance. Evaluate creates no lens route or
slots; final Engineering consumes the sealed direct judgment without a lens
sweep. An `impact_incomplete` run dispatches nobody.

**Update fixtures with the interface, then test from narrow to broad.** When
code changes a schema, signature, payload, failure order, or persisted state,
the owner of the affected tests updates those fixtures and assertions during
the same bounded change; do not wait for an aggregate suite to reveal known
drift. Freeze the shared contract before owners finish and keep file ownership
disjoint. Run static/diff checks, exact changed selectors with fail-fast, the
changed-file suite, and only then one proportional declared suite after all
code and fixtures are stable. Never launch a broad suite while parallel edits
are still moving.

Classify every failure before touching production. If it is a fixture,
setup, variable-name, or stale-assertion defect, fix the test and rerun only
that selector; do not present it as a product finding or automatically restart
the long aggregate command. Preserve the green receipt from unchanged layers.
Repeat the broader suite only when the test correction changes shared
behavior or the release gate specifically requires a clean aggregate receipt.
Run lens sweeps only on a stable committed target, never on transient
production/test combinations.

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

0. **Onboarding:** Follow the facade's one-time setup sequence. Check readiness
   with `$TP onboard --json`; a ready workspace continues without another setup
   visualization. Present `$TP onboard` only for initial setup, a missing
   prerequisite, or an explicit setup/settings request. Tasks, phases, resumed
   sessions, and updates do not reset completed setup. Retain the user's goal.
   For an existing run, use `$TP loop resume` first. It reads durable scope
   without dispatch, lifecycle effects, an advisory waiver or a session receipt.
   Follow `resume_run` even when `ready` is false; `loop next` separately
   revalidates authorization and transport before effects. Do not create a
   replacement loop or recover authority from predecessor conversation.
   For missing setup, show the onboarding dashboard
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
   `resume_run` → read the saved run in this task. Recovery does not upgrade
   unproven hook enforcement or authorize a worker.
   The buttons drive this via `sendPrompt`. Don't guess a workspace — a
   governed run needs a real folder + a git commit, and this is where a
   brand-new user gets them in place.
   If the report includes `artifacts`, read the latest snapshot before
   re-deriving plan, review, graph, or progress state; it is the durable
   cross-session/team handoff.
1. **Setup (once a folder + repo exist):** if onboarding reports that context is
   missing, run `$TP init` yourself (details: `references/setup.md`) and fill
   the four context docs from the conversation — only ask what you can't
   infer.
   Managed source, private runtime state, graph/evidence, and review artifacts
   are separate: source lives under the checkout root returned by repository
   preflight; run-private data lives under its run root; only explicitly shared
   knowledge lives in `.taskplane-kb/`. Consume paths from the run manifest,
   never assume `.em-review` is the source or artifact root.
2. **Initialize once:** only when no run exists. New v4 runs require the
   exact selected R-id, accountable human and stable session identity:
   `$TP loop init --req R-XXXX --by "human:owner" "<goal>"`.
   Product receives that selected requirement and starts from its bounded input;
   initialization never discovers another run's requirement or artifacts.
   Add `--parallel` for independent tasks. An existing run uses `loop resume`;
   do not initialize a replacement or copy authority from a prior session.
3. **Dispatch the bounded startup:** `$TP loop next` returns exactly `schema`,
   `stage_runtime_dispatch`, and `obligations`. Use the host launch fields in
   `obligations`; follow `references/codex-native-dispatch.md`. Pass only the
   unchanged startup envelope, standalone role marker, and exact
   `contract_bootstrap.environment` to the worker with `fork_turns="none"`.
   The worker calls `$TP stage read-input --request -` with that envelope, then
   consumes its pinned phase definition and declared artifact references. Never
   forward the full action payload, full prior briefs, or ambient state.
   Follow the emitted wait policy and collect the result. Product, Design and
   Plan author their declared candidates; the host collector retains them and
   the orchestrator alone requests the gate. Do not dispatch again while the
   current operation is pending.
   Product, Design and Plan author their draft before invoking
   `stage prepare-lenses` with their exact startup request. Use the shared
   dispatcher, signed activation and wait policy for every selected lens.
   `stage collect-lenses` runs the common collector against that same current
   candidate; consume the full results before completing the phase. A changed
   draft invalidates prior results. All later phases consume the retained
   `lens-evidence` package, including every parallel task at EM and Retro.
   Worker protocol lives in `agents/tp-lens.md`; no phase-specific lens
   receipt format or self-attested result is supported. Design writes
   `design/contract.json` and
   `design/design.md`, compares alternatives, declares a proposed graph
   overlay with bounded contract-level boundaries, runs the mandatory
   solution-design lens, and returns its mechanically checked artifact for the
   consolidated packet. It never changes code or the as-built graph. Plan
   writes plan/tasks.json
   (each task: id, scope, tests as one command string (never a list), req,
   deps, contracts, `new_modules` when
   applicable, and typed `impact_policy`), execute builds TDD-first
   (`discipline/tdd.md`) without launching lens workers; Evaluate directly
   judges the exact diff, bound tests, criteria, graph impact,
   requirements/contracts, approved Design conformance, and provenance with
   zero lens routes, slots, workers, or verdicts. Engineering uses the same
   canonical context without launching a lens sweep. Full detail:
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

**Current runtime.** Every new run starts at Product on the v4 stage aggregate.
Use the exact configured workspace locator, selected requirement and accountable
human identity. No environment opt-in or migration selects another runtime.
An existing v3 run must be explicitly archived before a clean run starts.
Never discover prior run inputs by scanning other folders or conversations.

`loop next` returns only `schema`, `stage_runtime_dispatch` and `obligations`.
Dispatch each worker with its bounded startup and the supplied host environment.
Read its committed input through `stage read-input`; a worker cannot read a sibling's
input. Observe real host Start and Stop events, collect the exact attempt, then
request the existing gate. Human approval remains required where declared.

A parallel wave may contain Build, Fix and Evaluate tasks at the same time.
Each task owns its workspace, phase binding, submission and evidence. Dispatch
only the returned ready entries. Evaluate's `obligations.children` are its two
independent evidence workers; each receives its own startup. Complete those
workers before submitting Evaluate's judgment. EM review consumes all accepted
task judgments after the join; sign-off leads to the stateless Retro phase.
Dependency graphs evolve during these phases. Every handoff retains the exact
graph snapshot it consumed; subsequent graph updates do not rewrite that evidence.

Stage terminalization is not cleanup. Post-merge worktree cleanup stays a
separate orchestrator-only R-0003 maintenance action and remains eligible only
after the exact registered managed worktree, merged-tip ancestry,
re-resolved-primary-main, and last-moment fail-closed proofs all pass.
