# State specification — where taskplane's state lives, and why

taskplane keeps three kinds of state, in three places. The rule that
decides every case:

> **Knowledge = the external store.** Decisions, requirements, debt, the
> dependency graph, context docs, and loop state live OUTSIDE the repo, in
> a per-project folder under `~/.taskplane/projects/<key>/`. They are the
> project's durable memory — but they are NOT code, so they never get
> committed or pushed with it.
> **Runtime = local to the checkout.** Live enforcement pointers, scratch
> review artifacts, and raw event streams — per-machine, git-ignored.
> **Never anywhere in the store: prompt data.** No instructions-to-models,
> no role text, no rendered prompts. Enforced by `tp.py kb lint`.

The change from earlier versions: the knowledge base used to live in an
in-repo `knowledge/` directory and rode along on every `git add -A`, so
decisions, graphs, and even strategy notes got pushed with the code. It now
lives in an external store so the repo stays clean. Sharing that store with
a team is a separate, deliberate step (see *Collaboration*, below) — not a
side effect of `git push`.

## The external knowledge store

Root: `~/.taskplane/` (override with `$TASKPLANE_HOME`). Inside, one folder
per project, keyed by the project's absolute path slugified the way Claude
keys its own per-project state (`/Users/x/Documents/app` →
`-Users-x-Documents-app`):

```
~/.taskplane/projects/<key>/
  ├─ meta.json                     project abs path + git remote (self-describing)
  └─ knowledge/
      ├─ decisions/NNNN-*.md       decision records (+ index.json)
      ├─ requirements/R-NNNN-*.md  functional, NFR-by-lens, acceptance, status
      ├─ debt/D-NNNN-*.md          deferred-work records
      ├─ index.json                machine index of the three above
      ├─ graph.json                dependency graph (modules, edges, import cache)
      ├─ context/*.md              product / tech-stack / workflow facts
      └─ state/
          ├─ loop.json             active track's loop state
          └─ tracks.json (+ tracks/<name>/loop.json)
```

Every writer resolves this location through one seam —
`taskplane_lite.kb_root(ws)` — so there is a single source of truth for
where knowledge lives. `tp kb where` prints it for the current project.

## Local to the checkout — never committed (git-ignored)

| Path | Contents | Why local |
| --- | --- | --- |
| `.taskplane/` | ACTIVE contract, snapshot ref, `meter.json`, `trace.jsonl` (raw audit events) | live enforcement + telemetry are per-machine; a parallel worker needs its own under `.tp-work/`, and none of it must ever be committed |
| `.eval/`, `.em-review/`, `.security-review/` | raw review artifacts and scratch | verdict *decisions* go to the KB store; the raw reports don't |
| `.tp-work/` | parallel workers' worktrees | vehicles, not cargo — work merges via `tp/<task>` branches |
| `plan/`, `specs/` | the contract SOURCE (per-task scope, tests, deps) and authored specs | these MAY stay in the repo if you want them version-controlled; they carry no generated artifacts |

`.taskplane/` self-ignores via its own `.gitignore`; `tp init` adds the
rest — and `knowledge/` — to the repo-root `.gitignore` (idempotent), and
migrates any legacy in-repo `knowledge/` out to the store.

## Migration from an in-repo knowledge base

A project created before the external store still has a git-tracked
`knowledge/`. `tp init` (or `tp kb migrate`) relocates it: the directory is
moved into the external store, `git rm --cached` untracks it, and
`knowledge/` is added to `.gitignore`. Until migration runs, reads fall back
to the in-repo location so nothing breaks mid-flight. After it, the repo
carries no taskplane artifacts — the acceptance invariant is that
`git status` is clean after `tp init` plus a recorded decision.

## The no-prompt-data rule

Store files hold structured decision fields only — no "You are…", no
rendered evaluator prompts, no system-prompt text. Prompts live in the
PLUGIN (lenses/, agents/, skills/), versioned with the product, not with
your project's data. `tp.py kb lint` scans the store for prompt markers,
oversized free-text fields, AND commercial/pricing strategy (which must not
travel with a shared or exported store); the DoD fails closed on violations.

## Collaboration (planned)

The store is designed to be shareable, but sharing is a deliberate act, not
a `git push` side effect. `meta.json` records each store's project path and
git remote, so a future `tp kb export`/`sync` can map a shared knowledge
base back to its project — via a synced drive (`$TASKPLANE_HOME` on shared
storage), a dedicated knowledge repo, or a hosted sync. That mechanism is
intentionally not built yet; the external store is the foundation it needs.
