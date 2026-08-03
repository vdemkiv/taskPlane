# taskplane

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

**See what your AI agents are doing — and keep them on track.** Claude Code
and Cowork are powerful, but driving them can feel like flying blind: work
scrolls past, agents wander off the task, and it's hard to tell what's done,
what's pending, and what's waiting on you. taskplane is the layer that makes
it legible. It keeps each agent inside a clear task scope, renders the whole
run as a live dashboard, and holds the thread of progress — so you always
know where things stand and what needs your call.

![taskplane in action — a governed run on a real PR: Definition of Ready gate → read-only review with the product rendered and findings pinned → your approval → parallel fix wave with a live out-of-scope block → Definition of Done gate → sign-off](docs/assets/taskplane-cowork-flow.gif)

Built for PMs, EMs, and engineers who want to move fast with the Claude
ecosystem without losing the plot. Three things, everywhere: **you can see
what's happening, it stays on topic, and you keep the thread.** The scope
guardrails and gates are how it delivers that — not the point, the means.

## What's new

| Version | Highlights |
| --- | --- |
| **v1.5.4** | **Lens coverage & dependency graph, surfaced where you decide** — new review lenses kept drifting out of the visualization and the dependency graph kept getting skipped. Both now derive from their single source of truth and appear automatically: the dashboard renders a **lens-coverage panel from `catalog.json`** (add a lens, it shows up — no hand-editing), the findings dashboard carries a **blast-radius graph panel** (an empty graph on a polyglot repo *explains* the cross-service gap and how to record edges with `tp graph edge`, instead of showing nothing), and the never-skippable `HEADLINE:` now reports lens coverage (`N deep / M sweep of 25`) and modules touched. The same render flow — HEADLINE first, `--paged` widgets, catalog in the context tab, graph in the graph tab — is now shared by **every** command: go, engineering, product, and north-star. Rendering is pinned to the cheap/standard model tier. |
| **v1.5.3** | **Render-reliability contract for inline dashboards** — the decision data in a dashboard no longer depends on one big widget that might get skipped. Every dashboard command prints a never-skippable plain-text `HEADLINE:` with the key numbers; `tp findings --paged` / `tp dashboard --paged` split a large view into ordered, self-contained fragments (summary → high → medium → low), each under 14 KB, rendered one after another; and the skills now mandate rendering every page in order rather than summarizing. Small reviews still render as a single widget. |
| **v1.5.2** | **Hardening from the full 25-lens self-review of v1.5.1** (62 findings across every severity, all addressed): write-screen now covers `git apply/am`, `patch`, `sort -o`, and `cp/mv -t` and treats unscopeable mutation as default-deny; dependency-graph HTML escapes repo-supplied names (XSS); shared-store decision ids are collision-free; `publish`/`set_status`/`supersede` and the dispatch queue are lock-serialized; the team store is committable (anchored gitignore); private mode refuses inside Tag; `init --plan` scaffolds into the right store; docs (README, PRIVACY, state-spec, loop-design) truthed-up to the plan-aware store; keyboard + ARIA on the dashboard; and a raft of smaller correctness/UX fixes. 373 tests. |
| **v1.5.1** | **Hardening from a full 3-lens review of v1.5.0** (22 findings, all fixed): shared-store bookkeeping no longer trips the DoD gates; private mode refuses loudly inside Claude Tag instead of silently writing to the shared store; `init --plan` scaffolds into the right store; loop coordination state stays per-user (share knowledge, not the state machine); publish hardened — corrupt shared index aborts instead of erasing history, stale/lost markers repair via content-based idempotency, collision-free hash-suffixed shared ids, flows now push too, unknown ids and malformed entries reported; KB writes are atomic + locked; repo-supplied shared config carries a visible notice; plan/privacy settings follow the repo across checkouts. |
| **v1.5.0** | **Plan-aware onboarding, private mode & share push** — onboarding asks whether you're on a personal or Team/Enterprise plan (changeable any time with `tp share plan`): personal keeps knowledge in the private store (`~/.taskplane`), team/enterprise shares it in-repo (`.taskplane-kb/`, Claude-Tag compatible, inherited by every clone). On a team plan you can still work privately (`tp share set private`) and publish selected decisions to the team later with `tp share push [--ids …]` — deliberate and idempotent, like pushing commits. |
| **v1.4.0** | **Claude Tag support (beta)** — run governed work as your org's @Claude in Slack: `TASKPLANE_STORE=repo` persists the knowledge store inside the repo (Tag's sandbox is ephemeral — the KB travels with the branch/PR), `tp loop approve --by "<who> — '<their words>'"` makes every human-gate pass attributable to a real person in the thread, and the new `tp-tag` skill carries the thread protocol: post the gate, stop, wait for a human reply, attach the dashboard. |
| **v1.3.1** | **Remedy required on every finding** — the shared verdict format across all 25 lenses makes `suggestion` mandatory on every blocker/major/minor: criticism must come with a concrete alternative or solution, preferring capabilities your stack already runs. |
| **v1.3.0** | **Current-state grounding** — `tp init` scaffolds `context/current-state.md` (the as-built inventory); once filled it is injected into every task brief, and the design lenses review any design as a *delta against what exists*: reinventing an existing component or contradicting as-built reality is blocker-class. Grounding panel in the dashboard. |
| **v1.2.0** | **Decision registry** — `tp decision`: structured ADRs with lifecycle, alternatives (`option \| gained \| given up`), and supersede chains; accepted decisions linked to a task's modules are always injected into that task's brief. Plus three new design lenses: trade-offs, services selection, time-to-market (catalog: 25). |
| **v1.1.0** | **Dashboard v2** — auto-rendered on every gate and step; clickable loop rail + step journey with per-step execution and decision details; full acceptance criteria and execution plan shown for review; always-on stats with the agent → model table. |
| **v1.0.1** | Dispatch verification (`tp loop verify-dispatch` + opt-in Task-dispatch hook), FUSE-safe store cleanup, richer task statuses (`done`/`external` satisfy dependencies), model-tier setup in onboarding. |
| **v1.0.0** | First public release — enforced task contracts (PreToolUse hook), human-gated Evaluate-Loop, context-routed lens catalog, requirements engine, external per-project knowledge base, model tiers, on-demand north-star review. |

## Install

taskplane isn't in the built-in plugin catalog yet — you add it straight
from this Git repo (there's an option exactly for this).

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

Requires `git` in your workspace (the gates need a commit snapshot) and
`python3` (standard library only). Nothing else to set up.

## Onboarding (`tp onboard`) — the full setup

Say **taskplane help** for the tour, or just state a goal — `tp-go` routes
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

**Plan & sharing mode.** Onboarding asks one question first: *personal
plan, or Team/Enterprise?* (`tp share plan personal|team|enterprise` — or
`tp init --plan …`). Personal keeps every decision, requirement and loop
state in your private store (`~/.taskplane`). Team/Enterprise moves the
store into the repo (`.taskplane-kb/`, committed — the Claude-Tag mode), so
the whole team shares one registry and a fresh clone inherits it with zero
setup. Both are changeable any time. And on a team plan you can still work
**privately**: `tp share set private` keeps your work in your own store
while you explore, and when you're ready to make it visible —
like pushing commits — `tp share push [--ids 0001,0002]` publishes the
selected decisions into the shared store (then commit `.taskplane-kb/`).
`tp share status` shows your current mode and unpublished count.

Two setup choices then decide how efficiently the whole system runs:

**Models (cost routing).** Every step, task, and lens carries a capability
tier, and `tp onboard` reports the resolved map. Out of the box only
`cheap` is pinned:

| Tier | Default | Used for | Override |
| --- | --- | --- | --- |
| `cheap` | `haiku` | the lens sweep; tasks a planner marks simple | `TASKPLANE_MODEL_CHEAP` |
| `standard` | inherit session model | execute / evaluate / fix | `TASKPLANE_MODEL_STANDARD` |
| `deep` | inherit session model | spec, plan, engineering review, hard lenses (security, architecture, …) | `TASKPLANE_MODEL_DEEP` |

For cost-differentiated runs, set the overrides before starting, e.g.
`export TASKPLANE_MODEL_STANDARD=sonnet TASKPLANE_MODEL_DEEP=opus` — the
bulk build work runs mid-tier while judgment-heavy steps get the strong
model, and the wide lens sweep stays on `haiku`. No model ids are
hardcoded; tiers are yours to map as models change. Routing is *verified*,
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
the team shares one registry. Either way `kb lint` keeps prompt text and
pricing out of it, and the zero-token dependency graph answers blast-radius
questions without spending model calls at all.

Then you're governed from the first task.

## Five ways to use it

One entry point (`tp-go` picks up whatever you prompt and routes it), three
working personas, and a summoned strategy lens — a way to define, build,
and review agent work while keeping it visible, on-scope, and easy to
steer.

### 1. Review code, change nothing → `tp-engineering`

You have a branch, a PR, or a diff and want a thorough review — and the
confidence that the review itself won't touch a thing.

> **tp-engineering: review the approvals-reporting PR against main**

taskplane activates a **read-only contract** (the hook blocks any write to
the reviewed source), routes the **full 25-lens catalog** — deep on what the
change touches, a quick sweep on the rest, and **architecture & system
design always on** — leads with the dependency-graph **blast radius**,
checks each acceptance criterion, and hands you a findings report ranked
blockers-first with `file:line` evidence and a merge verdict. UI changes
get rendered, not just read. You sign off. The code was never touched.

*Good for: PR gating, security review, "is this safe to merge", audits.*

### 2. Build a new feature, refined first → `tp-build`

You have an idea and want it built right — or built twice, to choose.

> **tp-build: spending insights for managers — try it as A/B variants**

A north-star review on demand for significant features (alignment +
Leverage · Reversibility · Opportunity cost · Coherence) → requirement refined and scored
until the forecast is clean → a **visual mock of the spec before any code**
→ the governed loop with your gates — and when the design space is wide,
**A/B variants**: the same requirement built two deliberate ways by two
governed agents in isolated worktrees, evaluated comparatively, rendered
side by side (live screenshots, criteria scoreboard, per-variant budget
meters), and decided at a **human selection gate**. Pick A, B, or a hybrid.

*Good for: new features, prototypes, design decisions that are expensive
to reverse.*

### 3. Everything else → `tp-go`

You have a goal and want it done — visibly, on-scope, one clear thread.

> **tp-go: add CSV export to the monthly report**

Requirement (via `tp-product`) → refinement score with a fix-cycle
forecast → plan → **your approval** → execution (parallel agents when tasks
are independent, each kept to its own files) → engineering review (via
`tp-engineering`) → **your sign-off** → retrospective. You watch it happen on
the live dashboard; an agent drifting out of its lane or firing a destructive
command is stopped with a reason before it runs, so a wandering run can't
quietly make a mess.

*Good for: shipping features, fixes, refactors, and migrations you can
actually follow.*

### 4. Own the WHAT → `tp-product`

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

### 5. A direction check, when you ask for it → `tp-northstar`

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

> **tp-engineering: review this branch** → *(findings land in the
> knowledge base)* → **tp-go: fix the blockers from the review**

The review's findings become the fix loop's input: tp-go plans a scoped fix,
you approve, a governed wave runs, it re-verifies, you sign off. The result
is a surgical, provably in-scope diff.

## What you'll see

The whole reason it exists — legibility, focus, and a thread you don't lose:

- **A live dashboard**: mission control renders inline — the run's stage, a
  lane per parallel agent, per-agent budgets, the dependency map, the routed
  lenses, and a review-findings view — updating at every step. When something
  needs you, the dashboard says so with a button; when nothing does, it says
  that too.
- **Gates that keep the thread**: the loop pauses at plan-approval and
  sign-off. Nothing advances those but you — so you're never surprised by
  what shipped.
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
  shares one registry. Either way `kb lint` keeps prompt text and pricing
  strategy out of it, and runtime telemetry (the `.taskplane/` trace) stays
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

## What's inside

| Capability | What it does |
| --- | --- |
| Enforcement kernel | contracts + PreToolUse hook + DoR/DoD gates + action budget + audit trace |
| Evaluate-Loop | plan → build → evaluate → fix (≤2) → review → sign-off; serial or parallel waves, one enforced contract per agent |
| 25 lenses (as agents) | the diff picks the reviewers (security, a11y, DBA, performance, …); each is a governed read-only agent, fanned out in PARALLEL so a wide review runs in one pass — architecture & system design ALWAYS on |
| Requirements engine | refinement scoring + iteration forecast; quick-vs-full with tracked debt |
| Knowledge base | decisions, requirements, debt — retrieved by relevance at every step; plan-aware store: personal plan keeps it in an external per-project store (`~/.taskplane`, out of your repo), Team/Enterprise commits it in-repo (`.taskplane-kb/`) so the team shares one registry |
| Decision registry | structured ADRs (`tp decision`) with lifecycle, alternatives + trade-offs, and supersede chains — accepted decisions linked to a task's modules are ALWAYS injected into that task's brief |
| Current-state grounding | the as-built inventory (`context/current-state.md`) injected into every brief; design lenses review as a delta against what exists — reinvention and doc-vs-reality drift are blocker-class |
| Dependency graph | deterministic scan + change blast-radius + interactive map |
| Model tiers | portable `cheap`/`standard`/`deep` capability tiers routed per step, task, and lens — mapped to models by env config, verifiable with `tp loop verify-dispatch` |

**Eight commands, three working personas plus a summoned strategy lens:**
`tp-go` (the entry point — routes everything), `tp-product` (the WHAT seat:
requirements, scores, decisions), `tp-build` (new features: refinement + a
north-star check first, visual mocks, A/B variants with a selection gate),
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
├── agents/                 # the loop roles — tp-product/tp-engineering + planner/executor/evaluator/fixer/orchestrator + tp-lens (one lens, one governed agent); + tp-northstar (summoned strategy)
├── skills/                 # tp-go, tp-product, tp-build, tp-engineering, tp-northstar, tp-tag, tp-status, tp-help
├── lenses/                 # the 25-lens catalog
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
