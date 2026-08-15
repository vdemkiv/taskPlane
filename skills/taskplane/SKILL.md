---
name: taskplane
description: "The simple user-facing entry point for taskplane. Use whenever the user says taskplane or asks it to set up taskplane, get started with taskplane, design, build, implement, review, validate, plan, resume, or show status. The user supplies a goal and decisions; internally taskplane keeps the full strict harness: requirements and contracts, dependency-graph DoR/DoD, scoped execution, independent evidence, 26 review lenses, orchestrator-only gates, and human approval. Routes to tp-design, tp-go, tp-build, tp-engineering, tp-product, or tp-status without asking the user to learn those internals."
---

# /taskplane — simple for the user, strict for agents

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

Set `TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`.

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
On Codex, if `next_action` is `install_codex_hooks`, run
`$TP onboard --install-codex-hooks --json` within the repository and ask the
user to start a new Codex task. Do not dispatch governed workers until the
`codex_hooks` check is ready: marketplace skills do not themselves establish
the repo-local lifecycle/write receipts required by taskplane provenance.

## Keep the harness internal

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
`$TP loop submit pass|fail` (with `--task <id>` only in a parallel EXECUTE
wave) and stop; only the orchestrator calls the matching `$TP loop gate`;
the engine — never worker prose — decides whether DoR/DoD evidence is
sufficient; human checkpoints (Design
Contract approval, plan approval, A/B selection, escalation/replan, final
sign-off) stop for an explicit human yes; and no worker clears its contract
after a submission, weakens tests, silently widens scope, or treats an
incomplete action list as completion.

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
