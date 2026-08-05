# State specification — where taskplane's state lives, and why

taskplane keeps three kinds of state, in three places. The rule that
decides every case:

> **Knowledge = the knowledge store, and where it lives is PLAN-AWARE.**
> Decisions, requirements, debt, the dependency graph, and context docs are
> the project's durable memory. On a **personal** plan (the default) the
> store is external — a per-project folder under
> `~/.taskplane/projects/<key>/`, never committed or pushed. On a
> **Team/Enterprise** plan the store lives IN the repo at `.taskplane-kb/`
> and is committed *deliberately* with the code, so the team shares one
> registry and a fresh clone inherits it.
> **Runtime = local to the checkout.** Live enforcement pointers, scratch
> review artifacts, and raw event streams (`.taskplane/trace.jsonl`) — always
> per-machine and git-ignored, on both plans.
> **Never anywhere in the store: prompt data.** No instructions-to-models,
> no role text, no rendered prompts. Enforced by `tp.py kb lint`.

The change from the earliest versions: the knowledge base used to live in a
plain in-repo `knowledge/` directory and rode along on every `git add -A`, so
even on a solo project decisions, graphs, and strategy notes got pushed with
the code by accident. Since v1.5.0 the store is **plan-aware**: personal work
stays external and private; team work is shared in-repo *on purpose*, through
an anchored gitignore that makes exactly the shared store committable (below).
Sharing is a deliberate act, not an accident of `git push`.

## Store resolution (plan-aware)

Every writer resolves the store location through one seam — the kernel's
`get_mode()` / `kb_root(ws)` — so there is a single source of truth for where
knowledge lives. `tp kb where` prints the active path; `tp share status` shows
the resolved mode. Precedence, highest first:

1. **`TASKPLANE_STORE` env** — e.g. `TASKPLANE_STORE=repo` forces the in-repo
   store (used by Claude Tag; see below).
2. **Private setting** (`mode.json`) — an individual's `tp share set private`
   on a team plan keeps *their* work in the external store while they explore.
3. **Shared config** (`.taskplane-kb/config.json`) — a committed marker that a
   clone inherits, so team members pick up the shared store with zero setup.
4. **Plan** — `personal` → external; `team`/`enterprise` → in-repo. Set via
   `tp share plan …` or `tp init --plan …`.
5. **Default** — external (`~/.taskplane`).

### The external store (personal plan / private mode)

Root: `~/.taskplane/` (override with `$TASKPLANE_HOME`). Inside, one folder
per project, keyed by the project's absolute path slugified the way Claude
keys its own per-project state (`/Users/x/Documents/app` →
`-Users-x-Documents-app`):

```
~/.taskplane/projects/<key>/
  ├─ meta.json                     project abs path + git remote (self-describing)
  ├─ mode.json                     this user's share mode (e.g. private)
  └─ knowledge/
      ├─ decisions/NNNN-*.md       decision records (+ index.json)
      ├─ requirements/R-NNNN-*.md  functional, NFR-by-lens, acceptance, status
      ├─ debt/D-NNNN-*.md          deferred-work records
      ├─ flows/*.md                recurring multi-step playbooks
      ├─ index.json                machine index of the above
      ├─ graph.json                dependency graph (modules, edges, import cache)
      ├─ context/*.md              product / tech-stack / workflow facts
      └─ state/
          ├─ loop.json             active track's loop state (per-user; see below)
          └─ tracks.json (+ tracks/<name>/loop.json)
```

### The in-repo shared store (Team/Enterprise plan)

On a team plan the same `knowledge/` tree lives in the repo under
`.taskplane-kb/`, plus a committed `config.json`:

```
.taskplane-kb/
  ├─ config.json                   committed marker — a clone inherits the shared store
  └─ knowledge/                    decisions, requirements, debt, flows, graph, context, index
```

This is committable *by design*: `tp init` writes an **anchored** gitignore so
the repo ignores stray taskplane runtime paths but explicitly re-includes the
shared knowledge (an anchored `/knowledge/` allow under `.taskplane-kb/`), so a
plain `git add` picks up exactly the shared store and nothing else. Committing
`.taskplane-kb/` is how the team shares the registry.

## Local to the checkout — never committed (git-ignored)

| Path | Contents | Why local |
| --- | --- | --- |
| `.taskplane/` | ACTIVE contract, snapshot ref, `meter.json`, `trace.jsonl` (raw audit events) | live enforcement + telemetry are per-machine; a parallel worker needs its own under `.tp-work/`, and none of it must ever be committed |
| `.eval/`, `.em-review/`, `.security-review/` | raw review artifacts and scratch | verdict *decisions* go to the KB store; the raw reports don't |
| `.tp-work/` | parallel workers' worktrees | vehicles, not cargo — work merges via `tp/<task>` branches |
| `plan/`, `specs/`, `design/` | authored requirement, proposed-HOW Design Contract/visual, and implementation-plan sources | these MAY stay in the repo if you want them version-controlled; the loop treats them as its own evidence rather than product-code diff |

`.taskplane/` self-ignores via its own `.gitignore`; `tp init` adds the
rest to the repo-root `.gitignore` (idempotent). On a team plan the gitignore
is **anchored** so `.taskplane-kb/knowledge/` stays committable while the
runtime paths above remain ignored; on a personal plan the whole store is
external and nothing taskplane-generated enters the repo.

### Loop coordination state is per-user — even on a team plan

Only *knowledge* is shared. The loop **state machine** (`state/loop.json`,
`tracks.json`) is per-user and lives in the external store even on a team
plan — one person's active track, current step and fix-cycle count are not
the team's. A team shares the registry of decisions/requirements/debt, not
each other's in-flight loop.

**Exception — `TASKPLANE_STORE=repo`:** in Claude Tag's ephemeral sandbox the
whole store (including loop state) is forced in-repo so the next session
resumes the loop by cloning the branch. There the state machine travels with
the work precisely because the sandbox is discarded between sessions.

### Design state and evidence

`loop.json` records `design_required`, `design_only`, the baseline graph
fingerprint captured on entry to Design, and the approved evidence fingerprint.
The proposed HOW itself lives in `design/contract.json` (schema
`taskplane.design/v1`) with the human narrative in `design/design.md` and an
optional `design/visual.html`. Approval fingerprints exactly those files.
Changing them later makes Plan, Evaluate, and Review fail closed until the
loop returns through Design and receives a new human approval. Proposed graph
edges remain an overlay in the contract; the persistent as-built graph is not
changed during Design.

## Migration from an in-repo knowledge base

A project created before the plan-aware store still has a git-tracked
plain `knowledge/`. On a **personal** plan `tp init` (or `tp kb migrate`)
relocates it: the directory is moved into the external store, `git rm
--cached` untracks it, and `knowledge/` is added to `.gitignore`. Until
migration runs, reads fall back to the in-repo location so nothing breaks
mid-flight. After it, the repo carries no taskplane artifacts — the
acceptance invariant on a personal plan is that `git status` is clean after
`tp init` plus a recorded decision. (On a Team/Enterprise plan the store is
deliberately kept in-repo at `.taskplane-kb/` instead — see above.)

## The no-prompt-data rule

Store files hold structured decision fields only — no "You are…", no
rendered evaluator prompts, no system-prompt text. Prompts live in the
PLUGIN (lenses/, agents/, skills/), versioned with the product, not with
your project's data. `tp.py kb lint` scans the store for prompt markers,
oversized free-text fields, AND commercial/pricing strategy (which must not
travel with a shared or exported store); the DoD fails closed on violations.

## Collaboration (shipped, v1.5.0+)

Sharing is a deliberate act, and it is built. The mechanism is the in-repo
shared store described above: on a Team/Enterprise plan the knowledge tree
lives at `.taskplane-kb/` and is committed with the code, so the team shares
one registry and a fresh clone inherits it via the committed
`.taskplane-kb/config.json`. This replaces the old idea of an external
export/sync — the shared store simply IS in the repo, made committable by the
anchored gitignore.

### `tp share` — the sharing commands

| Command | What it does |
| --- | --- |
| `tp share status` | show the resolved mode (plan, private-or-shared) and the count of unpublished local records |
| `tp share plan personal\|team\|enterprise` | set the project's plan (changeable any time); also settable at `tp init --plan …` |
| `tp share set private` | on a team plan, keep *your* work in the external store (`~/.taskplane`) while you explore — recorded in `mode.json` |
| `tp share push [--ids …]` | publish selected records from your private store into the shared `.taskplane-kb/` store (re-numbered into the shared index), like a git push; then a human commits `.taskplane-kb/` |

### Private mode and publishing

On a team plan an individual can `tp share set private` to keep their work in
their own external store, invisible to the team, and later `tp share push
[--ids …]` to publish selected work into the shared store. Publishing covers
**decisions and flows only** — it does NOT push requirements or context docs.
Like a git push it is always a deliberate, idempotent human act; the actual
commit of `.taskplane-kb/` is still done by a person.
