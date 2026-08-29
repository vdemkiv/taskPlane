---
name: tp-status
description: "Use when the user asks where things stand with taskplane-governed work: 'status', 'where are we', 'what's the state of the loop/track/requirements/debt'. Read-only snapshot rendered as the inline mission-control dashboard: active track, loop step, tasks, open requirements, tracked debt, KB size, dependency graph — with an explicit action banner (gate buttons if a decision is yours, 'no action needed' if agents are working)."
---

# /tp-status — where the governed work stands

On Codex, set `TP='python3 .taskplane/codex-hook.py'` when that stable
workspace launcher exists; it resolves the newest valid installed taskplane
engine on every call. Otherwise set
`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Gather compactly
(skip empty sections):

`flow.json` is the approved read-only graph: one status request fans out to a
bounded stage/manifest/lineage read, loop/task state, requirements/debt, and
dependency graph reads; those converge into one mission-control dashboard and
one truthful action/no-action banner. This flow introduces no new gate and
mutates no state.

## Stage-native bounded status

For a stage-native v4 run, the stage branch consumes only bounded stage
summaries and the verified handoff projection. Show the current stage,
predecessor outcome, handoff fingerprint, child lineage, and pending human
action. Read no more than the returned page bound and cursor. Never open a
predecessor execution root, conversation, event log, tool transcript, lease,
meter, active contract, runtime environment, or mutable worktree merely to
render status. A missing, corrupt, ambiguous, oversized, or
fingerprint-mismatched projection stays visibly unavailable/fail-closed; never
choose the first stage, infer an outcome, or recover by loading predecessor
runtime context.

For a `taskplane.stage-dispatch/v1` invocation, its verified bounded startup is
the complete stage execution context: current stage authority, budget, and
scope, one `taskplane.stage-handoff/v1` versioned bounded manifest, explicitly
selected content-addressed artifact references, execution claim, and attempt
id. Status may render bounded summaries about that context but may not expand its
scope or treat an artifact reference as new authority. Retained terminal-stage
artifacts remain addressable for audit; later reuse requires an explicitly
authorized handoff with new authority, and discarded results are not
consumable by default without exact explicit nonconsumable-reuse authority.

Status never starts an implementation child, reopens a terminal stage, or
invokes cleanup. Stage terminalization and status rendering do not change
R-0003 enforcement, ReviewKernel evidence/provenance, collision isolation,
orchestrator-only gates, final sign-off, or exact-worktree cleanup eligibility.
When stage-native execution is disabled or the run is unmigrated, preserve the
existing v3/legacy read behavior and do not synthesize stage lifecycle facts.

When status concerns a managed repository/run, use `$TP repository status
--run-id <id>` and the manifest's checkout/run/artifact paths rather than
guessing from the current directory or `.em-review`. A `needs_user` repository
action is a real pending human action and must be shown in the banner.

- `$TP summary` — the user-facing headline, human decision, progress, graph,
  and enforcement assurance. Use this as the primary read model.
- `$TP context` — the one-screen summary (track, loop, reqs, debt, graph, KB)
- `$TP loop status` — step, per-task status + fix cycles, checkpoints
- `$TP track list` — all tracks + which is active
- `$TP req list` — requirements incl. open debt items
- `$TP status` — project loop state plus the active contract, if any (what is
  happening and who's governed right now), with an additive bounded stage
  projection when one is authoritative

**Present it as the dashboard, not a wall of JSON.**

1. TEXT first, three lines max: what's running, what state it's truly in,
   and — most important — **whether anything is waiting on the human**.
   If the loop's book lags reality (e.g. a custom flow like an A/B
   selection gate, or an agent that couldn't record its gate), SAY SO and
   name the real pending action; the dashboard renders the recorded state.
2. Then `$TP dashboard` — it prints a `HEADLINE:` line first (relay it as
   plain text — the never-skippable carrier of step + gate, so status lands
   even if the render is skipped), then the fragment. Show the fragment
   inline via `mcp__visualize__show_widget` when available as the LAST thing
   in the reply; otherwise link `.taskplane/dashboard.html` as the final
   dashboard artifact.
   Title: `taskplane_status_<step-or-context>` — UNIQUE per render; a
   repeated title updates the earlier widget in place instead of drawing a
   new one where the user is looking. (For an unusually large board,
   `$TP dashboard --paged` returns ordered ≤14 KB pages — render each in
   order, same contract as tp-engineering findings.) The dashboard carries an **action banner** at
   the top of the loop tab: gate buttons (approve / sign-off / resolve,
   wired to `sendPrompt`) when a decision is the human's, an explicit
   "no action needed from you — <role> is on <step> · next human gate: X"
   strip when agents are working. After sign-off, `retro` is an automatic
   non-human stage (lessons + graph true-up); `done` means that seal already
   exists, so the dashboard never asks the user to run it again.
3. Two v2.5 surfaces to get right — relay them where they actually are,
   never invent them where they are not:
   - **Component surfaces (know which board shows what):** the dashboard's
     Graph tab is MODULE-level only — modules / internal edges / external
     deps, the most-connected hubs, and the blast radius of the current
     tasks' scope. It does not draw component nodes; never say it does.
     On a decomposed graph (`tp graph scan --decompose`) two component
     surfaces do appear: the review dashboard's graph line gains a
     "N components (decomposed)" count, and the coverage map marks routed
     lenses with their component attribution (which component(s) proposed
     them). The drawn component layer — a node per component ringed around
     its owning module — is in the standalone `tp graph html` map, not the
     dashboard; point the user there when they want to see it. Graph with
     no `components` → module-level everywhere, say so.
   - **Coverage map v2:** review status dispositions EVERY catalog lens as
     deep · light · n/a, each n/a only with its stated negative evidence
     (e.g. "0 i18n markers in the diff"); the HEADLINE carries the coverage
     counts (deep/light/n-a). A bare, unevidenced n/a is a blocker to report,
     not a formatting nit.

Never mutate anything from this skill — status reads and renders only.
