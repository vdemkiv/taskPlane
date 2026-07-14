# Model capability tiers — match the model to the task

taskplane pins **no** model in any agent's frontmatter. Every agent stays
`model: inherit` so the plugin is portable across runtimes (a hardcoded model
name is exactly why a sibling orchestrator's agents fail to spawn on a host
that names models differently). Instead, a loop **step**, a planned **task**, or
a review **lens** carries an abstract *capability tier*, and the loop **driver**
resolves that tier to a concrete model at dispatch time — the Agent tool's
`model` parameter. Match model power to task difficulty: mechanical work runs on
a cheaper/faster model, hard reasoning on a stronger one. Lower cost and latency
are the natural benefit of capability-tiering — this is **not** a pricing
feature and carries no pricing data (kb-lint still forbids that in the store).

## The three tiers

- `cheap` — simple, mechanical, well-specified work (a formatting fix, a
  rote edit, the quick full-catalog sweep). Defaults to `haiku`.
- `standard` — the default for build/verify work. Inherits the session model.
- `deep` — hard reasoning (planning, the engineering review, the security /
  architecture lenses). Inherits the session model until you point it at a
  stronger one.

Only `cheap` maps to a concrete model out of the box; `standard` and `deep`
inherit, so **behaviour is unchanged until you opt in** — nothing is forced.

## Configure per tier (portable, no code change)

```
export TASKPLANE_MODEL_CHEAP=haiku      # the cost saver (default)
export TASKPLANE_MODEL_STANDARD=sonnet  # or leave unset = inherit
export TASKPLANE_MODEL_DEEP=opus        # stronger model for planning/review
```

A value of `inherit` or empty means "inherit the session model". An unknown
tier degrades to inherit rather than erroring, so a typo never blocks the loop.

## How each surface carries a tier

- **Step** — `STEP_DEFAULT_TIER` (taskplane_lite): `pm`/`plan`/`em` default to
  `deep`; `execute`/`fix`/`evaluate` default to `standard`.
- **Task** — a planner marks an individual simple task in `plan/tasks.json`:
  `{"id": "t3", "scope": ["docs/**"], "model": "cheap", ...}`. A valid per-task
  tier overrides the step default; an invalid one is ignored.
- **Lens** — the quick sweep runs `cheap`; deep lenses run `standard`, except
  the hard-reasoning lenses (security, architecture, scalability, data-safety,
  concurrency, dba, sre, privacy-compliance) which run `deep`.

## What the driver does

`tp loop next` returns `model_tier` and a resolved `model` (a concrete id, or
`null` = inherit) in its payload; `tp lens dispatch` puts `model_tier` + `model`
on every brief. When the driver dispatches the role/lens agent, it passes that
`model` to the Agent tool's `model` param — `null` means omit it and inherit the
session model. The agent frontmatter is never touched; the pin lives only at the
dispatch call, which is what keeps taskplane portable.
