# State specification — where taskplane's state lives, and why

## Entry compatibility inventory (2.23.6)

`entry-tools.json` lives under the existing host runtime session directory.
It contains caller-declared tool names, the host session identity, and an
installed-engine fingerprint. Reentry replaces it; an omitted declaration
clears this session's list. Another session or engine update cannot reuse it.
It is disposable compatibility metadata, not host proof, a contract, or a
permission grant. Existing tool and source-write guards remain authoritative.

## Host session ownership (2.23.5)

Identified host conversations partition execution storage by the SHA-256 of
their host session ID. The default home is
`<checkout>/.taskplane/sessions/<fingerprint>/`; an explicit `TASKPLANE_HOME`
is partitioned the same way. Each checkout's private Git directory stores its
locator at `taskplane/sessions/<fingerprint>/workspace.json`. Contracts, meters,
review signing authority and run state resolve through that session's home and
locator. Local review and evaluation outputs also have session subdirectories.

A process restart with the same host session ID can recover its own run. A new
conversation does not adopt any legacy shared state or another conversation's
run. `clear` operates on the calling session only; operator commands must carry
that session's host identity. CLI use without a host identity remains in the
legacy local namespace and cannot implicitly select an identified session.

Native hooks record bounded observations in a private temporary host cache,
keyed by exact session ID; `TASKPLANE_HOST_HOME` can select that cache's location.
The cache contains no contracts, execution results or findings. It lets the same
session verify readiness in a fresh checkout with a different execution home.
Repository bridge observations remain workspace-bound. Missing session identity,
a different session, or explicit denied host policy cannot use native proof.

A standalone review records its active checkout in that session's host cache.
Later screen and lifecycle hooks resolve that checkout while preserving the
original meaning of relative tool paths. Clearing the owning contract makes the
binding inactive. The shared `.taskplane/codex-hook.py` launcher is stateless;
its presence alone never proves hook readiness or selects another session.

taskplane separates source, durable knowledge, and private run data. The rule
that decides every case:

> **Knowledge = the knowledge store, and where it lives is PLAN-AWARE.**
> Decisions, requirements, debt, the dependency graph, and context docs are
> the project's durable memory. On a **personal** plan (the default) the
> store is private — a per-project folder under the project's ignored
> `.taskplane/projects/<key>/`, never committed or pushed. On a
> **Team/Enterprise** plan the store lives IN the repo at `.taskplane-kb/`
> and is committed *deliberately* with the code, so the team shares one
> registry and a fresh clone inherits it.
> **Runtime = project-local and run-scoped.** Managed mirrors/worktrees live under
> `.taskplane/checkouts/`; live enforcement, graph/evidence, leases, raw
> events, and artifacts live under `.taskplane/runs/<run-id>/`. Source,
> execution, and evidence remain separate subtrees. Legacy unmanaged workspaces
> retain their git-ignored local runtime paths for compatibility.
> **Never anywhere in the store: prompt data.** No instructions-to-models,
> no role text, no rendered prompts. Enforced by `tp.py kb lint`.

The change from the earliest versions: the knowledge base used to live in a
plain in-repo `knowledge/` directory and rode along on every `git add -A`, so
even on a solo project decisions, graphs, and strategy notes got pushed with
the code by accident. Since v1.5.0 the store is **plan-aware**: personal work
stays ignored and private; team work is shared in-repo *on purpose*, through
an anchored gitignore that makes exactly the shared store committable (below).
Sharing is a deliberate act, not an accident of `git push`.

## Store resolution (plan-aware)

Every writer resolves the store location through one seam — the kernel's
`get_mode()` / `kb_root(ws)` — so there is a single source of truth for where
knowledge lives. `tp kb where` prints the active path; `tp share status` shows
the resolved mode. (`TASKPLANE_STORE` and `TASKPLANE_HOME` are two of the
behavior-changing environment variables — the full reference, with defaults
and enforcement relevance, is `docs/configuration.md`.) Precedence, highest
first:

1. **`TASKPLANE_STORE` env** — e.g. `TASKPLANE_STORE=repo` forces the in-repo
   store (used by Claude Tag; see below).
2. **Private setting** (`mode.json`) — an individual's `tp share set private`
   on a team plan keeps *their* work in the ignored private store while they explore.
3. **Shared config** (`.taskplane-kb/config.json`) — a committed marker that a
   clone inherits, so team members pick up the shared store with zero setup.
4. **Plan** — `personal` → private; `team`/`enterprise` → shared in-repo. Set via
   `tp share plan …` or `tp init --plan …`.
5. **Default** — private, beneath the project's `.taskplane/`.

### The private store (personal plan / private mode)

Root: `<project>/.taskplane/` (override explicitly with `$TASKPLANE_HOME`).
Existing run locators retain their recorded home, including legacy external
homes. An explicit project-storage selection may supersede only an unused
preflight binding after verifying that no execution, stage, or contract has
started. The previous binding remains available for audit; active runs require
their named recovery or migration action. Managed repositories
use a stable normalized repository key, so equivalent HTTPS/SSH origins and
different checkout paths share identity without sharing run state:

```
<project>/.taskplane/projects/<key>/
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

Repository records, managed mirrors/worktrees, run-private state, and graph
cache are sibling roots described in `docs/storage-and-repositories.md`.

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

## Runtime paths — project-local, isolated by run

| Path | Contents | Why local |
| --- | --- | --- |
| `.taskplane/runs/<run-id>/state/control/` | active contracts, snapshot ref, meter and trace for a managed run | enforcement remains bound to the run and its trusted Git-metadata locator |
| `.taskplane/runs/<run-id>/stages/objects/<stage-id>/` | immutable, content-addressed `taskplane.stage/v1` aggregate revisions | stage history remains independently addressable and is never inferred from a mutable active pointer |
| `.taskplane/runs/<run-id>/stages/executions/<stage-id>/` | one claimed execution root per stable stage, with fresh attempt roots beneath it | successor and resumed-stage execution cannot inherit a predecessor's mutable runtime tree |
| `.taskplane/runs/<run-id>/{graph,evidence,lenses,artifacts}/` | graph, immutable evidence/views, leased results, reports/dashboards, and explicitly selected stage artifacts | private run products stay distinct from source and shared knowledge |
| `.taskplane/` | standalone contract/meter/trace | managed runs keep control state in their own run subtree |
| `.taskplane/active_contract.json` | the standalone root contract | one governed process per workspace, the common case |
| `.taskplane/active/<slot>.json` | PER-TASK contract slots (v2.3.1) | each emitted worker contract is pending until `SubagentStart` binds it to one exact child, which receives `TASKPLANE_TASK=<slot>`. A process with that variable is bound to exactly its slot (missing/corrupt fails closed). A slot-less orchestrator reads only its root contract; it never combines worker contracts. `SubagentStop` terminalizes and quarantines the slot on every terminal outcome; committed gates and SessionStart sweep completed-worker leftovers. |
| `.taskplane/quarantine/contracts/` | released worker contract records | diagnostic copy of terminal worker authority; the active slot and snapshot are removed only after the signed exact-slot terminal receipt validates |
| `.eval/`, `.em-review/`, `.security-review/` | standalone review artifacts | managed reviews use the run root and never place source here |
| `.taskplane/checkouts/<repository-key>/worktrees/tasks/<run-id>/` | managed parallel workers' worktrees | source vehicles remain in the checkout registry; work merges via `tp/<task>` branches |
| `.tp-work/` | standalone workers' worktrees | managed phases use isolated execution roots |
| `plan/`, `specs/`, `design/` | authored requirement, proposed-HOW Design Contract/visual, and implementation-plan sources | these MAY stay in the repo if you want them version-controlled; the loop treats them as its own evidence rather than product-code diff |

Standalone `.taskplane/` self-ignores via its own `.gitignore`; `tp init` adds the
remaining compatibility paths to the repo-root `.gitignore` (idempotent). On a team plan the gitignore
is **anchored** so `.taskplane-kb/knowledge/` stays committable while the
runtime paths above remain ignored; on a personal plan the whole store is
ignored and no runtime output is committed.

### Loop coordination state is per-user — even on a team plan

Only *knowledge* is shared. The loop **state machine** (`state/loop.json`,
`tracks.json`) is per-user and lives in the ignored private store even on a team
plan — one person's active track, current step and fix-cycle count are not
the team's. A team shares the registry of decisions/requirements/debt, not
each other's in-flight loop.

**Exception — `TASKPLANE_STORE=repo`:** in Claude Tag's ephemeral sandbox the
whole store (including loop state) is forced in-repo so the next session
resumes the loop by cloning the branch. There the state machine travels with
the work precisely because the sandbox is discarded between sessions.

### What `loop.json` actually contains (v2.2.1)

`loop.json` is the whole per-track state machine, not just flags. As shipped
it persists:

- **Run identity/config** (set at `loop init`): `governance_revision`,
  `submission_required`, `graph_governance`, `goal`, `parallel`,
  `design_required`, `design_only`, `requirement_id`, `spec_path`,
  `max_fix_cycles`, `checkpoints`, `step`, `current_task`; an A/B build
  additionally carries `ab` and, once the human decides, the recorded
  `selection`.
- **Retro seal** (`retro`): after human sign-off the non-terminal `retro`
  step remains open until `loop retro` stores one completed report containing
  forecast/scope/routing/finding evidence and the refreshed graph fingerprint.
  The stored report makes retries idempotent; only this seal advances to
  `done` (an aborted run remains `failed` after learning is recorded).
- **Tasks** (`tasks`): each with id, scope, tests, deps, status,
  fix-cycle count, and — in a parallel wave — the claimed worktree
  `workspace` path.
- **Worker `_submission` evidence blocks** — top-level for serial runs and
  per-task in parallel waves. Each block records `step`, `task`, `outcome`,
  `note`, the submitting `workspace` (an **absolute path**), the git
  `snapshot`, the workspace `fingerprint`, the `changed_files` list, the
  `evidence_paths`, an optional `graph_fingerprint`, and `submitted_at`.
  Privacy note for `TASKPLANE_STORE=repo` (Claude Tag): these blocks —
  absolute paths and changed-file lists included — are committed with the
  branch.
- **Design state**: `design_required`, `design_only`,
  `design_graph_fingerprint` (the baseline graph fingerprint — see below),
  and after approval `design_approved`, `design_fingerprint` (the approved
  evidence fingerprint) and `design_approved_by`.

Baseline capture is **not** a one-shot "on entry to Design": since v2.2.1
(H3), while the design is still unapproved the graph baseline follows the
CURRENT scan — every pre-approval rescan re-baselines
`design_graph_fingerprint` (with a `design_rebaseline` trace event), so a
legitimate rescan can't deadlock the step. Only human approval freezes the
evidence fingerprint.

The proposed HOW itself lives in `design/contract.json` (schema
`taskplane.design/v1`) with the human narrative in `design/design.md` and an
optional `design/visual.html`. Approval fingerprints exactly those files.
Changing them later makes Plan, Evaluate, and Review fail closed until the
loop returns through Design and receives a new human approval. Proposed graph
edges remain an overlay in the contract; the persistent as-built graph is not
changed during Design.

### Stage authority

The `taskplane.run/v4` manifest is the atomic index of immutable stage heads,
lineage, operation receipts, and the replaceable active-stage projection.
The projection must agree with the indexed heads and can be rebuilt under the
run lock. Lifecycle writes use the same stage transaction owner in every phase.
Only the current run format is supported. Initialization does not convert old
state, and no fallback writer or reader can activate it. A terminal predecessor
is never reopened; continuation creates a successor with an explicit handoff.

## Migration from an in-repo knowledge base

A project created before the plan-aware store still has a git-tracked
plain `knowledge/`. On a **personal** plan `tp init` (or `tp kb migrate`)
relocates it: the directory is moved into the ignored private store, `git rm
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
| `tp share set private` | on a team plan, keep *your* work in the ignored project store (`.taskplane/`) while you explore — recorded in `mode.json` |
| `tp share push [--ids …]` | publish selected records from your private store into the shared `.taskplane-kb/` store (re-numbered into the shared index), like a git push; then a human commits `.taskplane-kb/` |

### Private mode and publishing

On a team plan an individual can `tp share set private` to keep their work in
their own private store, invisible to the team, and later `tp share push
[--ids …]` to publish selected work into the shared store. Publishing covers
**decisions and flows only** — it does NOT push requirements or context docs.
Like a git push it is always a deliberate, idempotent human act; the actual
commit of `.taskplane-kb/` is still done by a person.
