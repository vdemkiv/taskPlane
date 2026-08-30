---
name: taskplane
description: "The simple user-facing entry point for taskplane. Use whenever the user says taskplane or asks it to set up taskplane, get started with taskplane, design, build, implement, review, validate, plan, resume, or show status. The user supplies a goal and decisions; internally taskplane keeps the full strict harness: requirements and contracts, dependency-graph DoR/DoD, scoped execution, independent evidence, 26 review lenses, orchestrator-only gates, and human approval. Routes to tp-design, tp-go, tp-build, tp-engineering, tp-product, or tp-status without asking the user to learn those internals."
---

# /taskplane — simple for the user, strict for agents

## Focused routing invariant

Every governed flow uses Product/Design minimum-sufficient focused routes,
Plan exactly three or four quick lenses for non-trivial work, and
Build/Fix/Evaluate/EM zero lens workers. Product, Design, and Plan record all
26 dispositions; only selected execution dispositions launch workers. Plan
overflow must split the scope or obtain authenticated expanded-route approval.
Evaluate performs direct evidence judgment only over the sealed diff, tests,
criteria, graph impact, requirements/contracts, Design conformance, and
provenance. Zero-lens stages stay zero on success, failure, cancellation,
interruption, and handoff.

The user should need to say only one of these:

- `taskplane design <new feature, approach, or technical change>`
- `taskplane build <goal>`
- `taskplane review <branch, diff, PR, feature, or codebase>`
- `taskplane status`

Do not make them choose personas, commands, graph depth, lenses, or loop
stages. Infer the flow, run the control plane, and surface only progress,
evidence, blockers, and decisions that materially need their judgment. This is
a user-interface simplification only. Never remove, shorten, or self-waive an
internal gate to make the interaction look simpler.

On Codex, resolve the stable launcher from the current checkout or its Git
repository family before using the command notation `$TP` below:
`TP_LAUNCHER="$(git rev-parse --path-format=absolute --git-common-dir
2>/dev/null)/../.taskplane/codex-hook.py"`; prefer
`.taskplane/codex-hook.py` when that current-checkout file exists. Invoke it as
`python3 "$TP_LAUNCHER"`; it resolves the newest valid installed taskplane
engine on every call. Only when neither launcher exists, during first setup or
on another host, use
`python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. If both host
variables are unset, stop with the bootstrap error; never collapse it to
`/taskplane/tp.py`.

## Approved flow contract

`flow.json` is the canonical facade graph: **user goal → intent/state route →
the smallest matching specialist flow**. The Review branch is never complete
when the report is generated: engineering review must deliver its canonical
workflow/graph/findings dashboard and stop at the explicit **human approve /
request changes** gate. Only the human decision closes Review/sign-off.
When the facade routes a delivery loop, its specialist `tp-go`/`tp-build`
flow continues after approval through engine-owned Retro; standalone Review
ends with its own attributed sign-off and persisted synthesis.

## Route the request

- Design a new feature or approach before code changes, compare technical
  options, define system shape, or settle contracts and rollout: follow
  `../tp-design/SKILL.md`. Design owns the proposed HOW; it is read-only
  toward product code and ends at an explicit human approval gate.
- Build, fix, refactor, migrate, or implement: follow `../tp-go/SKILL.md`.
  For a genuinely new feature or A/B request, also apply
  `../tp-build/SKILL.md`. Route complex, cross-module, contract-changing,
  distributed, risky, or materially ambiguous Build work through Design;
  small local reversible work may go directly to Plan/Build.
- Review, validate, blast radius, architecture/security review, or sign-off:
  follow `../tp-engineering/SKILL.md`; remain read-only toward reviewed code
  and wait at its human sign-off gate after presenting the final dashboard.
  A standalone decision is recorded with `review signoff`; dashboard delivery
  alone is never approval.
- Requirements, acceptance criteria, or change requests without delivery:
  follow `../tp-product/SKILL.md`.
- Status or “what needs me?”: follow `../tp-status/SKILL.md` and run
  `$TP summary` first.

On a fresh repository, run `$TP onboard --json` before governed work. Resolve
only the missing prerequisite it names. Do not dump setup mechanics unless the
user asks; say what is missing and help complete it.
When the goal names a local path, repository URL, ref, or pull request, source
acquisition is an engine-owned precondition, not a manual setup task. Run
`$TP repository prepare <target>` before the specialist flow (standalone
`review start` invokes the same precondition itself). A `ready` response names
the verified managed checkout and external run store. A `needs_user` response
contains one structured action: ask its exact prompt in this conversation,
wait for the user's decision, then call `$TP repository resume --run-id ...
--action-id ... --response ... --by "<human>"`. Never clone into `.em-review`,
put source inside an artifact directory, send the user to an external terminal,
or ask them to open a new Codex task merely because checkout, authentication,
or storage authorization needs recovery. The same host session continues after
the approved action.
On Codex, if `next_action` is `install_codex_hooks`, run
`$TP onboard --install-codex-hooks --json` within the repository. Ask the user
to start a new Codex task only for the host's one-time initial hook load; an
existing loaded hook and stable launcher govern managed checkouts and follow
later plugin versions without a restart.
If `next_action` is `continue_advisory`, an existing loop is already bound to
the workspace but this task has no live hook receipt. Do not ask for a new
task. Keep enforcement visibly advisory and, after explicit human direction
to continue here, pass `--advisory --by <human>` to the next governed command.
This never upgrades the session to live enforcement; a new task remains an
option only when the human requires live hook enforcement.
Do not dispatch governed workers until the
`codex_hooks` check is ready: marketplace skills do not themselves establish
the repo-local lifecycle/write receipts required by taskplane provenance.

## Keep the harness internal

### Stage-isolated handoffs

Each governed specialist receives one bounded stage dispatch.
`taskplane.stage-dispatch/v1` contains `taskplane.stage-startup/v1`; its
`input_handoff` is the versioned bounded `taskplane.stage-handoff/v1` manifest.
The startup also carries explicitly selected content-addressed artifacts and
the current stage authority, budget, and scope. Never pass predecessor agents,
conversations, event logs, tool transcripts, leases, runtime roots, or other
mutable execution context to the successor.

A non-build stage may end `done`, `closed`, or `discarded` with no implicit
Build. Record the exact terminal outcome and its required evidence or
attributable reason, retain the immutable artifacts and handoff for audit, and
do not reopen or rewrite the stage. A later successor may use retained
`closed` or `discarded` artifacts only through an explicit `stage reuse`
operation with explicit new authority and exact selected fingerprints. Stage
terminalization does not run worktree cleanup or weaken any R-0003 gate.

For a delivery loop, obey the engine payload from `$TP loop next` exactly.
Dispatch the named role with its full role-instruction file on either Claude or
Codex; do not improvise a shorter worker prompt.
On Codex, the internal driver must also use the emitted native `task_name`,
`model` when non-null, and `reasoning_effort`, then wait for every requested
subagent result. The full spawn/wait/interrupt contract is in
`../tp-go/references/codex-native-dispatch.md`.

The submit/gate/human-checkpoint invariants are stated once, canonically,
in `references/harness-rules.md` (this skill's own reference dir, so every
distribution that ships `skills/` ships it) — read it before driving a
loop. In one line each: workers write their evidence, run
`$TP loop submit pass|fail` (or evaluator-only `unavailable` for a proven
host/model outage; with `--task <id>` only in a parallel EXECUTE
wave) and stop; only the orchestrator calls the matching `$TP loop gate`;
the engine — never worker prose — decides whether DoR/DoD evidence is
sufficient; human checkpoints (consolidated pre-implementation authorization,
A/B selection, escalation/replan, destructive or external actions, and final
sign-off) stop for an explicit human yes; and no worker clears its contract
after a submission, weakens tests, silently widens scope, or treats an
incomplete action list as completion. Worker contracts are child-scoped and
are terminalized automatically by native lifecycle, committed gates, or
SessionStart recovery; they never become orchestrator authority.

Product, Design, and Plan completion is mechanical; incomplete contracts,
dependencies, acceptance mapping, NFRs, graph evidence, or required lenses
block with a named non-human reason. Their complete evidence is presented
together in the single consolidated pre-implementation authorization packet.

### Fixture-first validation discipline

When a production interface, schema, return shape, or failure order changes,
update its directly affected fixtures and assertions in the same bounded work
slice. Do not wait for a long suite to rediscover an already-known fixture
change. Freeze the shared interface before production and test owners finish,
and keep their file ownership disjoint.

Validate in increasing cost order: static/diff checks, the exact changed
selectors with fail-fast enabled, the changed-file suite, then one
proportional declared suite after production and fixtures are stable. Do not
start aggregate or full-repository suites while concurrent edits are still in
flight. When a run fails, classify the failure before changing production. A
fixture/setup/assertion defect is corrected as a test defect and reruns only
its exact selector; it is not evidence of a product regression and does not
automatically restart the aggregate run. Run a broader suite again only when
the correction can affect behavior outside that selector or a release gate
explicitly requires a clean aggregate receipt.

For slow integration tests, use fail-fast on the first proportional pass and
capture the exact selector immediately. Preserve already-green receipts and
avoid repeating unchanged layers. Run review/lens sweeps only against a
stable committed target, after code and fixtures agree, so review findings do
not reflect transient test scaffolding.

At every human checkpoint, render the engine-provided dashboard fragment
inline through the host widget so its approval controls remain interactive.
Use the standalone HTML path only as a fallback when inline rendering fails.
Standalone PR review has one consolidated initial consent covering selected
dynamic validation, disposable sandbox repair, routine collection/recovery,
artifact publication, and inline delivery. Ask again only when the canonical
session names a material authority change or final disposition; never demand a
magic reply or interpret missing host interaction as a decline. Claude and
Codex render the same <=14 KB review pages from one revision and automatically
deliver its complete JSON/Markdown/HTML artifact set.

## Dependency graph is part of Ready and Done

Requirements record product dependencies with `--depends R-XXXX` and named
API/event/data/runtime boundaries with repeatable
`--contract provides|consumes|changes:NAME`. Plans inherit those boundaries,
declare deliberately new graph modules, and use a typed impact policy.

When Design is used, its graph declaration is a proposed overlay on the
current graph, never a mutation of as-built state. Design DoR (the entry
gate) requires a refined requirement with acceptance criteria, a current
baseline graph, and no blocking questions. Design DoD (the exit gate, where
the engine validates the policy) REQUIRES alternatives and trade-offs, a
selected approach, modules/edges/contracts, bounded depth with an explicit
boundary policy, graph DoR/DoD, acceptance-to-validation mapping,
failure/rollout evidence, the mandatory solution-design lens, and a useful
visual or an explicit reason to skip it.
Approval fingerprints this evidence; Plan, Build, Evaluate, and Review cannot
silently drift from it.

The graph DoR refreshes before plan approval and blocks distributed/high-cost
work whose dependencies, contracts, new surfaces, or depth policy are
ambiguous. The graph DoD refreshes from the actual diff before evaluation and
engineering review. Evaluators disposition impacted nodes with evidence,
re-check affected requirements and contracts, and fail on unplanned or stale
surface.

For distributed systems, model only the contract between entities. Use
`contract:` or `resource:` nodes and stop at that boundary; inspect another
service's internals only when they are in the current repository and explicitly
in scope. Default policies are engine-owned; ask the user about depth only when
changing it would alter a material risk or delivery decision.

## What the user sees

### Host-native projection contract

Codex and Claude may expose PiP progress, agent fan-out, approval controls,
dashboard carousels, integrated previews, and hosting through different native
APIs and evolving styles. Project all of them from the canonical host-surface
snapshot and ordered audit stream. Preserve workflow/run, target, revision,
task/slot, evidence, gate, and action identity across reconnects and host
switches. A fresh runtime receipt is required for each optional capability;
otherwise use its accessible bounded fallback and report unavailable, not
declined. Native UI is presentation only: it cannot approve, weaken a gate,
invent evidence, or synthesize preview success.

Every transition already returns its current step, headline/dashboard path,
and next action. Reuse that payload; do not call `$TP summary` after internal
transitions. Run it only for an explicit status request or once at a human
gate when the transition payload is unavailable. Lead with the available
plain-text headline and say:

- what is happening or what finished;
- whether the harness passed or blocked it, with the concrete reason;
- the one decision needed from the user, if any.

Render the richer dashboard when the host supports it; otherwise link the
local HTML artifact. The plain-text summary is always sufficient to operate
the loop.

Claude Code/Cowork and Codex use the bundled hook plus engine gates for
mechanical scope/evidence enforcement while preserving the host's own sandbox
and approval controls. Claude Tag has no tool interception: state, evidence,
attributed human gates, and repository-persisted audit remain enforced, but
scope discipline is cooperative and must be stated honestly.
