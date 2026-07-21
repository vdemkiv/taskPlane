# taskplane

**See what your AI agents are doing — and keep them on track.** Claude Code
and Cowork are powerful, but driving them can feel like flying blind: work
scrolls past, agents wander off the task, and it's hard to tell what's done,
what's pending, and what's waiting on you. taskplane is the layer that makes
it legible. It keeps each agent inside a clear task scope, renders the whole
run as a live dashboard, and holds the thread of progress — so you always
know where things stand and what needs your call.

Built for PMs, EMs, and engineers who want to move fast with the Claude
ecosystem without losing the plot. Three things, everywhere: **you can see
what's happening, it stays on topic, and you keep the thread.** The scope
guardrails and gates are how it delivers that — not the point, the means.

## Install

```
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
```

Then say **taskplane help** for the tour. Requires `git` in your workspace
(the gates need a commit snapshot) and `python3` (standard library only).

## Three ways to use it

One entry point (`tp-go` picks up whatever you prompt and routes it) and
three personas — a way to define, build, and review agent work while keeping
it visible, on-scope, and easy to steer.

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
  (`~/.taskplane/projects/<key>/`) — the next task starts from what the last
  one learned instead of re-deriving it (that's your token bill going down).
  The store lives OUTSIDE your repo, so taskplane's artifacts never get
  committed or pushed with your code; `kb lint` keeps prompt text and pricing
  strategy out of it, and runtime telemetry stays local
  (`docs/state-spec.md`). `tp kb where` shows the path.

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
| 22 lenses (as agents) | the diff picks the reviewers (security, a11y, DBA, performance, …); each is a governed read-only agent, fanned out in PARALLEL so a wide review runs in one pass — architecture & system design ALWAYS on |
| Requirements engine | refinement scoring + iteration forecast; quick-vs-full with tracked debt |
| Knowledge base | decisions, requirements, debt — retrieved by relevance at every step; kept in an external per-project store (`~/.taskplane`), out of your repo |
| Dependency graph | deterministic scan + change blast-radius + interactive map |
| Model tiers | portable `cheap`/`standard`/`deep` capability tiers routed per step, task, and lens — mapped to models by env config, verifiable with `tp loop verify-dispatch` |

**Six commands, three personas:** `tp-go` (the entry point — routes
everything), `tp-product` (the WHAT seat: requirements, scores,
decisions), `tp-build` (new features: refinement + a north-star check first, visual
mocks, A/B variants with a selection gate), `tp-engineering` (the SOUND
seat: full-catalog review, impact, verdicts, retro), `tp-status`,
`tp-help`. Definition and judgment are deliberately separate seats — the
grader never grades their own spec.

**License:** free and open source under the **Apache License 2.0** — use it
personally or at work, commercially or not, no strings. See `LICENSE`.

**Privacy:** taskplane runs locally, collects nothing, and sends nothing — no
telemetry, no accounts, no network calls of its own. All state stays on your
disk — your code in the repo, taskplane's knowledge base in a separate
external store (`~/.taskplane`), never transmitted anywhere. See `PRIVACY.md`.

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

## Layout

```
taskplane/
├── taskplane/              # the enforcement core (kernel + hook screener)
├── hooks/hooks.json        # PreToolUse → taskplane screen
├── agents/                 # the loop roles — tp-product/tp-engineering + planner/executor/evaluator/fixer/orchestrator + tp-lens (one lens, one governed agent)
├── skills/                 # tp-go, tp-product, tp-build, tp-engineering, tp-status, tp-help
├── lenses/                 # the 25-lens catalog
├── scripts/                # generators (e.g. the lens-catalog doc)
├── discipline/             # TDD, debugging, worktrees — the operating disciplines
├── docs/                   # state spec + design notes
# note: the knowledge base is NOT here — it lives in ~/.taskplane/projects/<key>/
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
