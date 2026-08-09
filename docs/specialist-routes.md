# Specialist routes, the run experience, and the CLI underneath

The README's four prompts (`taskplane design / build / review / status`)
cover normal use. This reference is the power-user surface: the specialist
skill routes you can address directly, what a governed run looks and feels
like while it happens, and the CLI layer beneath the skills.

## Specialist routes (optional power-user surface)

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
