# taskplane

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

**Design, build, and review AI-generated software with evidence, not trust.** taskplane
is the AI software-delivery control plane for people who ship and review code
with Claude or Codex every day. You ask it to design, build, review, or show status;
behind that simple request it checks whether the work is ready, keeps every
agent inside an approved scope, and requires current implementation, test, and
review evidence before anything can be called done.

![taskplane 2.1 in action — a real safe-order-cancellation project: graph-aware Definition of Ready blocks an undeclared audit module → a human approves the corrected dependency-aware plan → the execution contract blocks a Codex edit outside scope → worker evidence cannot self-advance → an independent evaluator checks acceptance criteria, routed lenses, dependents, and the distributed contract → the full engineering review runs → final human sign-off](docs/assets/taskplane-cowork-flow.gif)

taskplane is not another prompt collection, review bot, or project tracker. It
is the governed execution and assurance layer between your intent and
agent-generated changes. Requirements, dependencies, contracts, implementation,
and review stay connected from Definition of Ready through Definition of Done.
A 26-lens engineering review makes architecture, solution design, security, data, operability,
UX, and other technical consequences explicit for engineers, EMs, PMs, and
nontechnical decision-makers.

**Simple for the user; strict for the agents.** State the goal, review the
evidence, and make only the decisions that require human judgment. taskplane
keeps the machinery — scoped contracts, dependency depth, independent
submissions, lifecycle gates, durable memory, and the live dashboard — behind
that interaction without weakening it.

## Four prompts are enough

> **taskplane design:** design safe order cancellation before we build it

> **taskplane build:** add CSV export to the monthly report

> **taskplane review:** review this branch against main; do not change code

> **taskplane status**

The `taskplane` skill routes those requests to the existing product, design,
build, engineering, status, and orchestration skills. You do not need to choose a
persona, remember loop commands, select review lenses, or set dependency
depth. taskplane reports a concise text summary after each material
transition and shows the richer dashboard when the host supports it.

This simplicity does **not** reduce agent work. A worker may only submit a
result; it cannot advance its own stage. The orchestrator independently runs
the engine gate, which recomputes source and review-artifact fingerprints and
rejects missing, stale, out-of-scope, under-tested, or under-reviewed work.
Human Design approval (when used), plan approval, and final sign-off remain
explicit.

## What's new

This table summarizes the three most recent releases.
[CHANGELOG.md](CHANGELOG.md) is the authoritative, complete history — if the
two ever disagree, the CHANGELOG wins.

| Version | Highlights |
| --- | --- |
| **v2.3.1** | **Graph-scoped regression gate + review discipline** — each change is verified for actual regressions within its dependency-graph blast radius at the DoD gate, right away. Tier 1 runs the radius's tests at the change's baseline vs now and blocks only on a was-green-now-red test; Tier 2 flags an enforcement/public entry point changed with no covering test (how v2.3.0's CI break shipped). Reviews classify findings `regression \| pre-existing \| observation` so only real regressions (and new highs in the diff) block — a 26-lens sweep reads "N block · M to triage", not "100 issues". Opt-in via `dod.regression_gate`; degrades visibly, never crashes the gate. 768 tests. |
| **v2.3.0** | **Fix all 122 findings from the whole-codebase 26-lens review of v2.2.1** (12 high · 57 med · 53 low) under a binding rule: **no fix reduces a guardrail** — every enforcement change is strict-or-stricter, proven by a 66-case differential battery (zero block→pass regressions) and a `TestNoLoosening` suite. Parallel agents now get per-task contract slots that fail closed to the most-restrictive union; Windows hooks and interpreter escapes (`python file.py`, `sh script.sh`) fail closed; the test suite is isolated for both runners; the EM gate maps unknown/blocker/major severities up to high. One atomic-write + never-silently-lock-free primitive backs every shared state file (graph, mode→private, tracks, meter, requirements index, loop) so a torn write fails closed with a remedy. Plus single-source versioning, `.gitignore`-aware scans, `tp gc`, and doc/CI truth-up. See CHANGELOG.md (authoritative) for the full list. 744 tests. |
| **v2.2.1** | **Fix all 32 findings from the full 26-lens self-review of v2.2.0** (5 high · 11 med · 16 low, every severity addressed) plus 2 renderer-contract findings the human filed mid-review. Highs: worker submissions can no longer be misattributed across tasks (`--task` is validated everywhere), gate transitions apply under the state lock so a parallel wave worker's update is never clobbered, the design graph baseline re-captures after a legitimate rescan instead of deadlocking, the pm gate is fail-closed (an authored requirement must exist before Define advances), and post-approval design tampering is now pinned by tests at all four gates. Structure: Design Contract validation extracted to `design_contract.py` (loop.py −18%), policy/contract-id normalization unified in depgraph, DoR checks are pure (only the gate applies mutations). Renderer: wave-board lanes derive status from findings files so re-rendering shows the live fan-out, and paged dashboards mandate byte-for-byte verbatim rendering. Java packages no longer collapse across group ids; fingerprints never silently disable (HEAD fallback); anonymous approvals are recorded as `(unattributed)` with a warning. 477 tests. |
| **v2.2.0** | **First-class Design before Build** — `taskplane design` turns a refined requirement plus current code into alternatives and an approvable Design Contract without changing product code. The contract carries a proposed dependency overlay, named API/event/data/runtime contracts, bounded depth, graph DoR/DoD, acceptance-to-validation traceability, risks, failure modes, observability, rollout/rollback, and a conditional technical visual. Complex Build work can route through Design; approved evidence is fingerprinted, Plan must cover it, and Review blocks any recorded drift (drift returns to Design for a new human approval). A distinct solution-design lens brings the catalog to 26. 456 tests. |
| **v2.1.0** | **AI software delivery with proof, not agent self-reporting** — `taskplane build`, `taskplane review`, and `taskplane status` become the simple entry points over the full harness. Workers submit source-and-artifact fingerprints but cannot advance lifecycle state; the orchestrator independently gates and rejects missing, stale, out-of-scope, under-tested, or under-reviewed work. Requirements carry dependencies and named contracts into planning; graph-aware DoR requires every new module to be declared and bounds distributed traversal at explicit contract/resource nodes; graph-aware DoD checks realized modules, affected consumers and requirements, and a current graph fingerprint. 444 tests. |

All earlier releases (v1.0.0 – v2.0.0): see [CHANGELOG.md](CHANGELOG.md).

## Install

taskplane is live in the plugin marketplace. If the listing has not reached
your client yet, or you want to install directly from source, add this Git
repository as a marketplace; the host-specific source instructions are below.

**Claude Desktop or claude.ai (Chat / Cowork):**
Customize → Plugins → **+** in *Personal plugins* → **Add marketplace** →
**"Add from a repository"** → paste `https://github.com/vdemkiv/taskPlane`
→ *taskplane* appears → **Install**.

**Claude Code (terminal)** — same thing; the first command adds this repo
as the source:

```
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
/reload-plugins
```

**Codex CLI:** add the GitHub marketplace, install taskplane, and then open the
plugin browser to verify it is enabled:

```bash
codex plugin marketplace add vdemkiv/taskPlane
codex plugin add taskplane
codex
# inside Codex: /plugins
```

**Codex in the ChatGPT desktop app:** select **Codex**, open **Plugins**, choose
the `taskplane-marketplace` source after adding it, and install **taskplane**.
Open the repository as a local environment and start a new task.

Codex loads newly installed skills and hooks only in a new task/session. It
also asks you to review and trust the bundled lifecycle hooks when prompted
(`/plugins` confirms taskplane is enabled). Keep Codex's own sandbox and
approval controls enabled — taskplane's
scope contract is an additional guardrail, not a replacement. Plugins are
currently supported in Codex CLI and Codex in the ChatGPT desktop app, not the
IDE extension. Until the public listing is approved, install from this GitHub
marketplace source. See [Codex plugins](https://learn.chatgpt.com/docs/plugins)
and [Codex hooks](https://learn.chatgpt.com/docs/hooks).

Requires `git` in your workspace (the gates need a commit snapshot) and
Python 3 (standard library only; `python3` on macOS/Linux or the `py` launcher
on Windows). Nothing else to set up.

## Onboarding (`tp onboard`) — the full setup

Say **taskplane help** for the tour, or just state a goal — `taskplane` routes
it and runs onboarding for you on a fresh folder. `tp onboard` shows the
onboarding dashboard and won't hand you to a governed run until three
checks are green:

1. **A real folder to work in** — connect/open your project (an empty
   scratch dir or the session root is refused: a contract scoped there
   would govern everything).
2. **A git commit to diff against** — the gates fail closed without a
   snapshot; `git init && git add -A && git commit` if the repo is new.
3. **`tp init`** — scaffolds the four context docs
   (`product.md` / `tech-stack.md` / `workflow.md` / `current-state.md`),
   scans the dependency graph, and creates the external knowledge base.

   **Fill `current-state.md` first on a brownfield project.** It is the
   as-built inventory — what already runs, what data/integrations exist,
   what hardware is in place. Once filled, it is injected into every task
   brief (`knowledge.current_state`), and the design lenses (architecture,
   trade-offs, services selection, time-to-market) ground their reviews in
   it: a design is judged as a *delta against what exists*, and
   **reinventing an existing component or contradicting as-built reality is
   a blocker-class finding**. Record the big as-built choices as accepted
   decisions too (`tp decision new "<title>" --modules <globs>`) so they
   govern future work automatically.

### Codex onboarding

1. Install and enable taskplane, approve its bundled hooks when prompted,
   then start a
   **new** Codex task/session.
2. Make the target repository the working folder. In Codex CLI, `cd` to the
   repository before running `codex`; in the desktop app, open or create a
   local environment for that repository.
3. Prompt **"set up taskplane"** or **"use taskplane for …"**. The plugin runs
   `tp onboard --json` before governed work.
4. If the folder is not a committed Git repository, approve initialization or
   make the first commit yourself. taskplane needs the commit as its diff and
   Definition-of-Done baseline.
5. Choose whether taskplane knowledge stays **private/local** (`personal`) or
   is **shared in the repository** (`team`/`enterprise`). This is a storage
   choice; it is not tied to the name of your ChatGPT or Codex subscription.
6. Let taskplane initialize the context documents, then fill
   `current-state.md` first for an existing project. State the goal; taskplane
   will stop at Design approval when that phase is used, plan approval, and
   final sign-off for your explicit decision.

When inline HTML widgets are unavailable, Codex still relays the plain-text
`HEADLINE:` and provides `.taskplane/dashboard.html` as the local dashboard
artifact. The governance state and human gates do not depend on widget support.

**Knowledge storage and sharing mode.** Onboarding asks one question first:
*keep taskplane knowledge private/local, or share it with the team in the
repository?* (`tp share plan personal|team|enterprise` — or `tp init --plan
…`). `personal` keeps every decision, requirement and loop state in your
private store (`~/.taskplane`). `team`/`enterprise` moves the store into the
repo (`.taskplane-kb/`, committed — also compatible with Claude Tag), so
the whole team shares one registry and a fresh clone inherits it with zero
setup. Both are changeable any time. And on a team plan you can still work
**privately**: `tp share set private` keeps your work in your own store
while you explore, and when you're ready to make it visible —
like pushing commits — `tp share push [--ids 0001,0002]` publishes the
selected decisions into the shared store (then commit `.taskplane-kb/`).
`tp share status` shows your current mode and unpublished count.

Two setup choices then decide how efficiently the whole system runs:

**Models (cost routing).** Every step, task, and lens carries a capability
tier, and `tp onboard` reports the resolved map. Claude retains the historical
`cheap → haiku` default; Codex inherits its session model for every tier unless
you explicitly map one:

| Tier | Default | Used for | Override |
| --- | --- | --- | --- |
| `cheap` | Claude: `haiku`; Codex: inherit session model | the lens sweep; tasks a planner marks simple | `TASKPLANE_MODEL_CHEAP` |
| `standard` | inherit session model | execute / evaluate / fix | `TASKPLANE_MODEL_STANDARD` |
| `deep` | inherit session model | spec, plan, engineering review, hard lenses (security, architecture, …) | `TASKPLANE_MODEL_DEEP` |

For cost-differentiated runs, set the overrides before starting with model
ids your host understands — on Claude e.g. `export
TASKPLANE_MODEL_STANDARD=sonnet TASKPLANE_MODEL_DEEP=opus`; on Codex use your
host's model ids the same way. No cross-provider model
ids are hardcoded; tiers are yours to map as models change. Routing is *verified*,
not assumed: `tp loop verify-dispatch` audits a run, and
`TASKPLANE_ENFORCE_DISPATCH=warn|strict` turns on a dispatch-time check.
Details: `discipline/model-tiers.md`.

**Context storage (token efficiency).** Fill the three context docs with
your project's reality — the product doc's *Direction / north star* line is
what `tp-northstar` measures against. From then on decisions, requirements,
tracked debt, and the dependency graph accumulate in an **external
per-project store** (`~/.taskplane/projects/<key>/` — `tp kb where` shows
the path). That location is deliberate resource economics: every loop step
recalls only the few records *relevant to the task at hand* instead of
re-reading the repo or replaying history, so context stays small and the
token bill goes down as the project's memory grows. Where that store lives
is plan-aware: on a personal plan it stays external (`~/.taskplane`) and
never touches your repo (nothing to commit or push); on a Team/Enterprise
plan it lives in-repo at `.taskplane-kb/` and is committed deliberately so
the team shares one registry. Either way `kb lint` — a marker scan enforced
fail-closed at the DoD and engineering-review gates — keeps prompt text and
pricing out of it, and the zero-token dependency graph answers blast-radius
questions without spending model calls at all.

Then you're governed from the first task.

## Specialist routes (optional power-user surface)

The four prompts above cover normal use. If you want to address a specialist
seat directly, taskplane keeps these routes available:

### 1. Design the proposed HOW, change no code → `tp-design`

You have a new feature, architecture change, or approach that should be made
precise before anyone implements it.

> **tp-design: design safe order cancellation across API and events**

taskplane grounds the design in the refined requirement, accepted decisions,
current code, and baseline dependency graph. It compares at least two real
approaches, selects one, and produces a human-readable design plus a mechanical
Design Contract: modules, proposed edges, named contracts, bounded dependency
depth, Design and graph DoR/DoD, acceptance-to-validation mapping, risks,
failure modes, observability, rollout, rollback, and a technical visual only
when one helps. A distinct `solution-design` lens checks that the proposed HOW
is coherent, buildable, and reviewable. The designer cannot edit product code,
mutate the as-built graph, or approve its own work.

*Good for: new features, distributed-system contracts, migrations,
architecture choices, and expensive-to-reverse decisions.*

### 2. Review code, change nothing → `tp-engineering`

You have a branch, a PR, or a diff and want a thorough review — and the
confidence that the review itself won't touch a thing.

> **tp-engineering: review the approvals-reporting PR against main**

taskplane activates a **read-only contract** (the hook blocks any write to
the reviewed source), routes the **full 26-lens catalog** — deep on what the
change touches, a quick sweep on the rest, and **architecture & system
design always on** — leads with the dependency-graph **blast radius**,
checks each acceptance criterion, and hands you a findings report ranked
blockers-first with `file:line` evidence and a merge verdict. UI changes
get rendered, not just read. You sign off. The code was never touched.

*Good for: PR gating, security review, "is this safe to merge", audits.*

### 3. Build a new feature, refined first → `tp-build`

You have an idea and want it built right — or built twice, to choose.

> **tp-build: spending insights for managers — try it as A/B variants**

A north-star review on demand for significant features (alignment +
Leverage · Reversibility · Opportunity cost · Coherence) → requirement refined and scored
until the forecast is clean → Design first when system shape, contracts, or
risk need approval → a **visual mock of the spec before any code**
→ the governed loop with your gates — and when the design space is wide,
**A/B variants**: the same requirement built two deliberate ways by two
governed agents in isolated worktrees, evaluated comparatively, rendered
side by side (live screenshots, criteria scoreboard, per-variant budget
meters), and decided at a **human selection gate**. Pick A, B, or a hybrid.

*Good for: new features, prototypes, design decisions that are expensive
to reverse.*

### 4. Everything else → `tp-go`

You have a goal and want it done — visibly, on-scope, one clear thread.

> **tp-go: add CSV export to the monthly report**

Requirement (via `tp-product`) → refinement score with a fix-cycle
forecast → optional Design → **your Design approval** → plan → **your plan approval** → execution (parallel agents when tasks
are independent, each kept to its own files) → engineering review (via
`tp-engineering`) → **your sign-off** → retrospective. You watch it happen on
the live dashboard; an agent drifting out of its lane or firing a destructive
command is stopped with a reason before it runs, so a wandering run can't
quietly make a mess.

*Good for: shipping features, fixes, refactors, and migrations you can
actually follow.*

### 5. Own the WHAT → `tp-product`

You need the thing defined before anyone builds it — or a product decision
recorded so it survives the session.

> **tp-product: spec CSV export — testable acceptance criteria, then score it**

tp-product turns a rough goal into a contract-ready spec: problem, users,
in/out of scope, and **testable acceptance criteria that become the
Definition of Done**. It scores the requirement's refinement and forecasts
fix cycles — close the gaps *before* planning, when they're cheap. Mid-flight
changes are **change requests** against the original requirement (re-scored,
re-approved at the plan gate, never silently absorbed), and product
decisions and debt are recorded in the knowledge base. It defines and
decides; it never implements, fixes, or reviews code — the grader never
grades their own spec.

*Good for: specs, acceptance criteria, prioritization, change requests,
decision records.*

### 6. A direction check, when you ask for it → `tp-northstar`

Before an expensive build — or over any idea, task, diff, or finished
review — you can summon the strategic lens.

> **north-star this: is the integrations hub worth building given where
> we're going?**

tp-northstar measures the target against your project's **Direction /
north star** line (from the product context doc) and returns one strategic
note: an alignment verdict (on-course / drift / off-course), four decision
lenses — **Leverage, Reversibility, Opportunity cost, Coherence** — the
single sharpest tension, and a recommendation (proceed /
proceed-with-eyes-open / reconsider). It is **summoned, not scheduled**:
read-only, advisory, never a gate, no executive cosplay. The product and
engineering seats run automatically; this third lens runs when you want a
direction check.

*Good for: "should we build this", roadmap calls, scope-creep checks,
strategic review of a plan or PR.*

### Compose them → review, then fix

> **tp-engineering: review this branch** → *(findings written to
> `.em-review/findings.json` in your working copy)* → **tp-go: fix the
> blockers from the review**

The review's findings become the fix loop's input: tp-go plans a scoped fix,
you approve, a governed wave runs, it re-verifies, you sign off. The result
is a surgical, provably in-scope diff. Honest mechanics: `.em-review/` is
git-ignored scratch local to the checkout — it does not travel with the
branch. The review protocol records its synthesis as a knowledge-base
decision, but blockers you intend to fix in a *later session* (or on an
ephemeral host like Claude Tag, whose sandbox is discarded) should be
recorded as tracked debt (`tp req debt`) before the session ends, so the fix
loop has durable input.

## What you'll see

The whole reason it exists — legibility, focus, and a thread you don't lose:

- **A live dashboard**: mission control renders inline — the run's stage, a
  lane per parallel agent, per-agent budgets, the dependency map, the routed
  lenses, and a review-findings view — updating at every step. When something
  needs you, the dashboard says so with a button; when nothing does, it says
  that too.
- **Gates that keep the thread**: when Design is used, the loop first pauses
  for Design approval; it also pauses at plan approval and sign-off. Nothing
  advances those but you — so you're never surprised by
  what shipped.
- **A graph-aware Ready/Done bar**: requirements name what they depend on and
  which API/event/data/runtime contracts they provide, consume, or change.
  Before plan approval taskplane refreshes the graph and checks dependency
  depth, boundaries, and every deliberately new module; undeclared graph
  surface blocks Ready for ordinary work too. During evaluation and
  final review it compares planned versus realized modules, requires evidence
  for impacted consumers and affected requirements, and rejects a stale graph
  fingerprint. Across distributed systems, the default review boundary is the
  contract between entities — not speculative access to another service's
  internals.
- **Independent completion validation**: builders, fixers, evaluators, and
  engineering reviewers submit results; only the
  orchestrator invokes the state-transition gate. The fingerprint includes
  changed work plus evaluator/engineering evidence files, so editing a verdict
  after submission invalidates it. Agent prose is never the evidence source.
- **On-topic by default**: an agent writing outside its task scope, or firing
  a destructive command, is stopped with a reason before it runs — the run
  stays on the thing you asked for instead of wandering.
- **Memory that compounds**: decisions, requirements, tracked debt, and the
  dependency graph persist in an external per-project store
  — the next task starts from what the last
  one learned instead of re-deriving it (that's your token bill going down).
  Where the store lives is plan-aware: on a personal plan it stays OUTSIDE
  your repo (`~/.taskplane/projects/<key>/`) and taskplane's knowledge is
  never committed or pushed with your code; on a Team/Enterprise plan it
  lives in-repo at `.taskplane-kb/` and is committed deliberately so the team
  shares one registry. Either way the `kb lint` gate check keeps prompt text
  and pricing strategy out of it, and runtime telemetry (the `.taskplane/` trace) stays
  local and git-ignored in both (`docs/state-spec.md`). `tp kb where` shows
  the path.

## Honest about what the guardrails are

The scope/command guardrails are a real, mechanical help for the everyday
failure — an agent that drifts out of its lane or fires a destructive command
by mistake. The PreToolUse hook screens scope, denied commands, and the action
budget **before** each tool call; path checks resolve `..`, absolute paths and
symlinks; destructive programs (`rm`, `chmod`, …) are screened as writes; the
screener fails **closed** on a corrupt contract or an error.

But it's worth being precise so you trust it for what it is: this is
keep-the-agent-on-topic, not a security sandbox. A task that grants `Bash`
grants arbitrary code execution, and no string-screen over a command the agent
controls can fully contain a *determined adversary* — the guardrails stop
honest drift and casual mistakes, which is the failure you actually hit day to
day. If you ever need a hard boundary, pair taskplane with a restricted
toolset (no `Bash`, writes via screened `Write`/`Edit`) or OS-level isolation.
The token/$ budget is cooperative in the same way — a plugin can't intercept
the model's own calls.

The same precision applies to the gate protocol. "A worker cannot advance
its own stage" is a **protocol + audit** guarantee, not process isolation:
any process with workspace access *could* invoke the engine gate, and gate
calls are traced for after-the-fact attribution. What holds mechanically is
the **evidence**: a gate only advances on a submission whose fingerprints
(changed source plus the exact evaluator/engineering evidence bytes) still
match the workspace, so even a worker invoking the gate itself cannot pass
unproven, stale, or post-submission-edited work.

## What's inside

| Capability | What it does |
| --- | --- |
| Enforcement kernel | contracts + lifecycle hook + worker submissions + orchestrator-only DoR/DoD gates + action budget + audit trace |
| Evaluate-Loop | optional design → human Design approval → plan → human plan approval → build → evaluate → fix (≤2) → review → sign-off; serial or parallel waves, one enforced contract per agent |
| Design Contract | a read-only proposed-HOW phase with alternatives, graph overlay, named contracts, bounded depth, Design/graph DoR/DoD, validation traceability, failure/rollout evidence, conditional visualization, and human approval |
| 26 lenses (as agents) | the diff picks the reviewers (security, solution design, a11y, DBA, performance, …); each is a governed read-only agent, fanned out in PARALLEL so a wide review runs in one pass — architecture & system design ALWAYS on |
| Requirements engine | refinement scoring + iteration forecast; requirement dependencies and named contracts; quick-vs-full with tracked debt |
| Knowledge base | decisions, requirements, debt — retrieved by relevance at every step; plan-aware store: personal plan keeps it in an external per-project store (`~/.taskplane`, out of your repo), Team/Enterprise commits it in-repo (`.taskplane-kb/`) so the team shares one registry |
| Decision registry | structured ADRs (`tp decision`) with lifecycle, alternatives + trade-offs, and supersede chains — accepted decisions linked to a task's modules are ALWAYS injected into that task's brief |
| Current-state grounding | the as-built inventory (`context/current-state.md`) injected into every brief; design lenses review as a delta against what exists — reinvention and doc-vs-reality drift are blocker-class |
| Dependency graph | deterministic scan + provenance/fingerprint + typed local/contract/requirement depth + graph DoR/DoD + interactive blast-radius map |
| Model tiers | portable `cheap`/`standard`/`deep` capability tiers routed per step, task, and lens — mapped to models by env config, verifiable with `tp loop verify-dispatch` |

**One simple entry point plus specialist skills:** `taskplane` routes
design/build/review/status without exposing the harness. Power users can call
`tp-go` (the delivery driver), `tp-product` (the WHAT seat:
requirements, scores, decisions), `tp-build` (new features: refinement + a
north-star check first, visual mocks, A/B variants with a selection gate),
`tp-design` (the proposed HOW seat: alternatives, Design Contract,
dependency/contract overlay, Design DoR/DoD, approval),
`tp-engineering` (the SOUND seat: full-catalog review, impact, verdicts,
retro), `tp-northstar` (the summoned STRATEGY lens — advisory, never a
gate), `tp-tag` (governed work as your org's @Claude in a Slack channel —
see below), `tp-status`, `tp-help`. Definition and judgment are deliberately separate seats — the
grader never grades their own spec.

**License:** free and open source under the **Apache License 2.0** — use it
personally or at work, commercially or not, no strings. See `LICENSE`.

**Privacy:** taskplane runs locally, collects nothing, and sends nothing — no
telemetry, no accounts, no network calls of its own. All state stays on your
disk — taskplane's knowledge base lives in an external store (`~/.taskplane`,
personal plan) or in-repo (`.taskplane-kb/`, Team/Enterprise), and nothing is
ever transmitted anywhere by taskplane. See `PRIVACY.md`.

## Model tiers (cost routing)

Every loop step, task, and lens brief carries a capability tier —
`cheap` / `standard` / `deep` — and taskplane resolves it to a model at
dispatch time. Out of the box only `cheap` is pinned (to `haiku` — the lens
sweep and planner-marked simple tasks); `standard` and `deep` inherit your
session model. Point tiers at concrete models with env config
(`TASKPLANE_MODEL_CHEAP` / `_STANDARD` / `_DEEP`) — no model ids are
hardcoded, so the plugin stays portable as models change. And because a
brief's model only matters if the dispatch actually used it, the routing is
verifiable: `tp loop verify-dispatch` audits a run, and
`TASKPLANE_ENFORCE_DISPATCH=warn|strict` turns on a dispatch-time check
(opt-in, inert by default). Details: `discipline/model-tiers.md`.

## Claude Tag (beta) — taskplane in your Slack channels

[Claude Tag](https://claude.com/docs/claude-tag/overview) runs @Claude as
your organization's shared identity in Slack (Team/Enterprise, public
beta). taskplane adapts to that environment with three mechanisms:

- **Repo-persisted store.** Tag's sandbox is ephemeral — `~` is discarded
  when the conversation idles. Set `TASKPLANE_STORE=repo` and the knowledge
  store (decisions, requirements, loop state) lives at `.taskplane-kb/`
  inside the repo, committed and pushed with the work. The next Tag session
  resumes the loop by cloning the branch.
- **Attributable human gates.** There is no PreToolUse hook layer in Tag,
  so gates are process + audit: at `plan_approval` and `signoff` the loop
  parks, the gate summary goes to the thread, and only a real person's
  reply unlocks it — recorded with `tp loop approve --by "Dana — 'approved'
  in #platform-eng"`. The approver lands in the trace and the KB, so every
  gate pass is attributable. An approve without `--by` is detectable as a
  self-approval.
- **The `tp-tag` skill** carries the full thread protocol: compact status
  posts, the dashboard attached at every gate, scope restated before each
  execute step, and a hard rule the skill never breaks — it does not
  approve gates on its own, under any phrasing of urgency.

To deploy: an Owner attaches the taskplane plugin to a scope (channel,
workspace, or org) from the Access bundle's Plugins tab or a skills
repository — see [Customize Claude
Tag](https://claude.com/docs/claude-tag/admins/customize). Honest limit:
Tag's plugin surface today is skills-only, so enforcement is by process,
visibility, and trace — not by mechanical interception. The hook layer
remains fully active in Claude Code and Cowork. Individuals can work
privately even on a team plan and publish selected decisions to the
channel's shared store with `tp share push` — see the changelog's v1.5.0
entry.

## Layout

```
taskplane/
├── taskplane/              # the enforcement core (kernel + hook screener)
├── hooks/hooks.json        # PreToolUse → taskplane screen
├── agents/                 # product/designer/planner/executor/evaluator/fixer/engineering/orchestrator + tp-lens + tp-northstar
├── skills/                 # taskplane façade + tp-go/product/design/build/engineering/northstar/tag/status/help
├── lenses/                 # the 26-lens catalog
├── scripts/                # generators (e.g. the lens-catalog doc)
├── discipline/             # TDD, debugging, worktrees — the operating disciplines
├── docs/                   # state spec + design notes
# note: on a personal plan the knowledge base is NOT here — it lives in ~/.taskplane/projects/<key>/;
#       on a Team/Enterprise plan it lives in-repo at .taskplane-kb/ (committed with the code)
├── PRIVACY.md              # privacy policy (local-only, no telemetry)
└── LICENSE                 # Apache License 2.0
```

## Under the hood (optional)

The skills drive everything, but the CLI beneath is the power layer:

```bash
# a read-only review contract, by hand (tp-engineering does this for you):
python3 taskplane/tp.py new --read-only --write-allow ".em-review/**" \
    --tools "Read,Grep,Glob,Bash,Write,Edit" "review of <target>"
python3 taskplane/tp.py lens route --base main --all   # the full catalog for a diff
python3 taskplane/tp.py graph impact --files src/db.js  # blast radius, zero tokens
python3 taskplane/tp.py clear                            # release the contract
```

Pure `python3` standard library + `git`. No runtime dependencies.
