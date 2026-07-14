# Reshape — lenses, context routing, and a knowledge base

Draft for your input. This reworks three things you flagged into one model, and
adds the knowledge base as its own layer. taskplane stays the spine throughout.

## The shift, in one line

From **"invoke a named role-agent for each action"** → to **"you describe the
work; the system routes the applicable *lenses* from context, runs them inside
a taskplane-governed step, and remembers the decisions."**

Three of your points are the same idea seen from different sides:

- *EM was too narrow (code-review only).* → EM becomes the **cross-functional
  synthesis** of whatever lenses apply — Product, Design, Engineering — not a
  code reviewer.
- *Roles feel like something you must dispatch.* → a role becomes a **lens**: a
  composable perspective, not an actor you call.
- *You shouldn't have to name the role.* → a **router** picks the lenses from
  context; you just trigger the action.

The fourth point — the knowledge base — is what stops every run starting from
scratch (and cuts tokens).

## 1. Lenses (perspectives, not agents)

A **lens** is a named evaluation perspective with a trigger and a focused check.
Lenses are *data* (small files), not agents you invoke, and **many apply to the
same artifact at once**.

Catalog (starter set — extend freely):

| Lens | Fires on | Looks for |
| --- | --- | --- |
| **product** | specs, tickets, a diff vs its acceptance criteria | requirements met, scope fidelity, user-journey completeness |
| **design** | `**/*.tsx,jsx,vue,css`, UI/screens | UX, states, visual consistency, accessibility |
| **security** | `**/auth/**`,`**/*.sql`,`**/api/**`,`.env*` + **baseline on any code** | secrets, authz, injection, unsafe input, deps |
| **code-quality** | any code (baseline) | clarity, error handling, dead code, naming, duplication |
| **scalability** | services, queries, hot paths | N+1, unbounded work, blocking calls |
| **testability** | any code | coverage, seams, mockability |
| **integrability** | APIs, contracts, schemas | contracts, error recovery, schema hygiene |
| **data-safety** | migrations, schema | additive/rollback, backfill |

Each lens combines, where it can, a **deterministic check** (SAST/secret-scan/
lint/coverage) with an **LLM perspective** — same taskplane ethos: enforce what
you can, reason about the rest. This is exactly your `definition-of-ready-done`
lens set, promoted from prose into first-class, routable objects.

Lens file (`lenses/security.md`):

```
---
id: security
name: Security
applies_when:
  globs: ["**/auth/**","**/*.sql","**/api/**","**/*.env*"]
  task_types: ["integration","api","auth"]
  baseline: true            # runs on ANY code, even if globs miss
checks: ["gitleaks", "semgrep --config auto", "npm audit --production"]
severity: [critical, high, medium, low]
---
<what this lens looks for + its own DoR/DoD criteria>
```

## 2. The router — no role selection

You trigger an action ("review this", "govern this task", "is this ready?").
The router reads the **context** — changed globs, task/artifact type, keywords —
and returns the set of lenses that apply, **with a reason for each**. It's
deterministic and explainable (a taskplane trait), and always includes the
**baseline** lenses so a perspective is never silently dropped. You can override
("just the security lens") but never *have* to.

```
$ tp lens route --diff HEAD~1
  design       ← changed **/*.tsx
  code-quality ← baseline
  security     ← baseline + touches src/api/**
  product      ← diff has an acceptance-criteria file
  → 4 lenses will run; override with --only / --skip
```

**Persona starter prompts.** Separately, *you* (the human) have a role. A light
persona config gives a couple of tailored openers — a PM starts with
problem/users/success-metric; an engineer with change/risk/tests. This shapes
*how the session opens*, independent of which lenses fire on the artifact.

## 3. EM, widened

The EM stops being "code review" and becomes the **human-gated synthesis across
the applicable lenses** — it pulls Product (does it meet the spec?), Design (does
it hold up?), and Engineering (quality/security/scale) into one review + a
recommendation, and hands the call to you. It's the cross-functional validator
you described — which is what a real EM is. It also **writes the decision to the
knowledge base** (below), so the "why" survives.

Concretely, the loop's EVALUATE and EM steps become *lens-driven*: run the routed
lenses (in parallel), synthesize, gate. No "invoke the security agent" — the diff
decided security applies.

## 4. Knowledge base — decisions & flows that persist

Two different records, don't conflate them:

- **Trace** (`.taskplane/trace.jsonl`) — every event, machine, audit. *What
  happened.* Already built.
- **Knowledge base** (`knowledge/`) — curated **decisions** and **flows**, human-
  and agent-readable, retrievable. *Why, and how we work.* New.

Decision record (`knowledge/decisions/0007-optimistic-locking.md`):

```
---
id: 0007
title: optimistic locking for todo.complete
status: accepted
date: 2026-07-11
tags: [todo, concurrency]
context_files: ["src/todo/**"]
links: {track: v01, lens: scalability}
---
Context: ...   Decision: ...   Alternatives considered: ...   Consequences: ...
```

**Captured automatically** at the high-signal moments the loop already stops at:
plan approval, escalations/resolves, a lens finding that changes direction, and
EM sign-off. **Retrieved automatically** at the start of a step: the router
matches decisions by `context_files`/`tags` to the current task and loads the top
few into the agent's context.

Why this matters (your point): the agent starts a step with *"here are the 3
prior decisions that touch these files"* instead of re-deriving history — **lower
tokens, and consistency** (it won't re-litigate a settled call). Larger **flows**
(playbooks, recurring multi-step procedures) live under `knowledge/flows/` and are
loaded the same way.

taskplane owns this: the KB is a governed artifact, written at gate points,
retrieved at step start — part of the loop, not a bolt-on.

## How this maps onto what's already built

Small, additive changes — the loop engine and contracts don't change shape:

- **New:** `lenses/*.md` (the catalog) + `taskplane/lens.py` (router: context →
  lenses, deterministic + explainable) + `tp lens route`.
- **New:** `knowledge/` + `taskplane/kb.py` (write decision, retrieve by
  context) + hooks in the loop's `gate`/`next` to capture/inject.
- **Changed:** EVALUATE and EM steps call the router and run the routed lenses
  instead of a fixed reviewer; EM synthesizes + writes the decision to the KB.
- **Unchanged:** the taskplane core, the contracts, the DoR/DoD gates, the loop
  state machine, the human gates.

## Risks & mitigations (evenhanded)

- *Router misses a lens.* → baseline lenses always run; the fired set is shown
  and overridable; a "what didn't I check?" completeness note.
- *KB rot / noise.* → capture *decisions* (high-signal), not everything;
  structured ADRs; retrieval scoped by files/tags; decisions can be superseded.
- *Auto-routing hides intent.* → you can always name a lens; the router explains
  itself every time.
- *Writing the KB costs tokens too.* → only at gate points, short structured
  entries; the read-side savings dominate.

## Decisions (locked 2026-07-11)

1. **Lenses run entry + exit** — routed at PLAN/DoR ("does the plan cover
   security/design?") and at EVALUATE/EM.
2. **Router is auto + transparent + overridable** — context picks the lenses
   (plus baselines), shows which fired and why, and honors `--only`/`--skip`.
3. **Knowledge base = in-repo markdown ADRs** under `knowledge/`, git-versioned,
   retrieved by files/tags.
4. **Personas: light version now** — a small per-human-role starter-prompt
   config in v0.1.
5. **Keep "EM", widen it** — cross-functional synthesis, not code review.
6. **Lens execution is tiered** (see below).
7. **Lenses prime + review** (see below).

## Execution model — a lens is a spec; how it runs is a policy

A lens is a **perspective specification** (its triggers, deterministic checks,
and criteria). *How* it runs is a separate axis the **router chooses**:

- **Inline (default):** the lens is applied as a checklist inside one review
  session. Cheap — the artifact is read once. Right for most lenses / most
  changes.
- **Dedicated governed subagent:** for a high-stakes or large change (a security
  lens on `**/auth/**` or `**/*.sql`, a design lens on a new screen, or a diff
  over the size threshold), the lens runs as its **own parallel subagent under
  its own read-only taskplane contract** (writes only to `.review/<lens>/`),
  giving isolation + independence, then the EM synthesizes.

The router emits, per lens, both **why it fired** and **how it will run**
(`inline` | `subagent`). This is the one model that honors both the quality
instinct (isolation where depth warrants) and the token constraint (inline
everywhere else). The knowledge base feeds both modes — prior decisions loaded
once.

### Prime + review (two moments, not just one)

Applicable lenses act at two points:

- **Prime (in):** before/at EXECUTE, the routed lenses' guidance is injected into
  the executor's context, so code is written security/design-aware from the
  start — defect prevention, cheap.
- **Review (out):** at EVALUATE/EM, the routed lenses assess the result.

(Live "watcher" lens-agents observing every write are deliberately out of scope —
too expensive for the value; prime + post-review captures most of it.)

## First build increment (the keystone)

`taskplane/lens.py` — the router: `route(changed_files, task_type) → [{id,
reason, mode}]`, deterministic + explainable, with always-on baselines and the
`inline`/`subagent` mode policy. Plus `lenses/catalog.json` (lens metadata) and
`lenses/<id>.md` (each lens's evaluator prompt), and `tp lens route`. Everything
else (KB, prime/review wiring, subagent spawn, personas) builds on this.
