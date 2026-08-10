# Model capability tiers — match the model to the task

taskplane pins **no** model in any agent's frontmatter. Every agent stays
`model: inherit` so the plugin is portable across runtimes (a hardcoded model
name is exactly why a sibling orchestrator's agents fail to spawn on a host
that names models differently). Instead, a loop **step**, a planned **task**, or
a review **lens** carries an abstract *capability tier*, and the loop **driver**
resolves that tier to a concrete model at dispatch time. On Codex it also
resolves the tier to native `reasoning_effort`. Match power to task difficulty: mechanical work runs on
a cheaper/faster model, hard reasoning on a stronger one. Lower cost and latency
are the natural benefit of capability-tiering — this is **not** a pricing
feature and carries no pricing data (kb-lint still forbids that in the store).

## The three tiers

- `cheap` — simple, mechanical, well-specified work (a formatting fix, a
  rote edit, the quick full-catalog sweep). Defaults to `haiku` on Claude;
  inherits the session model on Codex so a provider-specific id is never sent.
- `standard` — the default for build/verify work. Inherits the session model.
- `deep` — hard reasoning (planning, the engineering review, the security /
  architecture lenses). Inherits the session model until you point it at a
  stronger one.

On Claude, only `cheap` maps to a concrete model out of the box. On Codex all
three tiers inherit until explicitly mapped, so **behaviour is unchanged until
you opt in** and no cross-provider model identifier is forced.

## Configure per tier (portable, no code change)

```
export TASKPLANE_MODEL_CHEAP=<host-model-id>
export TASKPLANE_MODEL_STANDARD=<host-model-id>  # or leave unset = inherit
export TASKPLANE_MODEL_DEEP=<host-model-id>      # stronger planning/review
export TASKPLANE_REASONING_CHEAP=low
export TASKPLANE_REASONING_STANDARD=medium
export TASKPLANE_REASONING_DEEP=high
```

A value of `inherit` or empty means "inherit the session model". An unknown
tier degrades to inherit rather than erroring, so a typo never blocks the loop.
Invalid reasoning-effort overrides fall back to the tier defaults above;
supported values are `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`.

## How each surface carries a tier

- **Step** — `STEP_DEFAULT_TIER` (taskplane_lite): `pm`/`plan`/`em` default to
  `deep`; `execute`/`fix`/`evaluate` default to `standard`.
- **Task** — a planner marks an individual simple task in `plan/tasks.json`:
  `{"id": "t3", "scope": ["docs/**"], "model": "cheap", ...}`. A valid per-task
  tier overrides the step default; an invalid one is ignored.
- **Lens** — the quick sweep runs `cheap`; deep lenses run `standard`, except
  the hard-reasoning lenses (security, architecture, scalability, data-safety,
  dba, sre, privacy-compliance) which run `deep`.

## What the driver does

`tp loop next` and `tp lens dispatch` emit a stable Codex-safe `task_name`, the
taskplane `role`/`agent`, `model_tier`, resolved `model` (or `null` = inherit),
and `reasoning_effort`. A native Codex dispatch must use the exact task name and
effort and omit `model` when null. Agent frontmatter remains portable; routing
lives only at dispatch.

## Verify it worked (don't assume)

Emitting a model is intent; the dispatch is the fact. Two affordances close
the gap:

- **Audit after the fact** — `tp loop verify-dispatch` compares every brief
  the run emitted (`.taskplane/expected_dispatch.json`) against the dispatches
  the hook observed, and reports mismatches. This is the by-hand
  `trace.jsonl` analysis, mechanized.
- **Enforce at dispatch (opt-in)** — set `TASKPLANE_ENFORCE_DISPATCH=warn`
  (or `strict`) and the shipped PreToolUse hook on the Agent tool checks each
  dispatch against the matching brief: `warn` surfaces a correction message;
  for native Codex, `strict` denies an unknown taskplane task name or a task,
  model, role, or effort mismatch. Unset,
  the hook is inert — enforcement is opt-in by design.

**Know the default:** on Claude only `cheap` pins a model (`haiku`); on Codex
it also resolves to `null` = inherit. `standard` and `deep` inherit on both.
If you
want differentiated routing, set `TASKPLANE_MODEL_STANDARD` /
`TASKPLANE_MODEL_DEEP` — otherwise a run on a top-tier session model runs
everything on that model by design unless you map the tiers for that host.

## Rendering runs on the cheap tier (v1.5.4)

Visualization work — building a dashboard fragment, a findings page, a
lens-wave board, or a shareable poster — is **rendering, not reasoning**. It
does not need the deep tier; a chipper, fast model (Sonnet-class) does it
without any problem, and spending the deep tier on HTML assembly is pure
waste. So route rendering to `cheap`/`standard`, never `deep`:

- The `tech-writer` lens and any dedicated render/report step default to the
  `standard` tier (Sonnet), and the parallel lens **sweep** already runs on
  `cheap`.
- Reserve `deep` for the hardest *judgment* — security, architecture, the
  adversarial verify passes — not for turning findings into a widget.
- If you dispatch a helper agent purely to assemble a visualization, pin it
  with the Agent tool's `model` to a Sonnet-class model (or `effort: low`);
  the dashboards themselves are rendered by the stdlib kernel with no model
  at all, which is the cheapest path of all.
