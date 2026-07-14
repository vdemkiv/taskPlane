---
name: tp-status
description: "Use when the user asks where things stand with taskplane-governed work: 'status', 'where are we', 'what's the state of the loop/track/requirements/debt'. Read-only snapshot rendered as the inline mission-control dashboard: active track, loop step, tasks, open requirements, tracked debt, KB size, dependency graph — with an explicit action banner (gate buttons if a decision is yours, 'no action needed' if agents are working)."
---

# /tp-status — where the governed work stands

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`. Gather compactly
(skip empty sections):

- `$TP context` — the one-screen summary (track, loop, reqs, debt, graph, KB)
- `$TP loop status` — step, per-task status + fix cycles, checkpoints
- `$TP track list` — all tracks + which is active
- `$TP req list` — requirements incl. open debt items
- `$TP status` — the active contract, if any (who's governed right now)

**Present it as the dashboard, not a wall of JSON.**

1. TEXT first, three lines max: what's running, what state it's truly in,
   and — most important — **whether anything is waiting on the human**.
   If the loop's book lags reality (e.g. a custom flow like an A/B
   selection gate, or an agent that couldn't record its gate), SAY SO and
   name the real pending action; the dashboard renders the recorded state.
2. Then `$TP dashboard` and show the fragment inline via
   `mcp__visualize__show_widget` as the LAST thing in the reply. Title:
   `taskplane_status_<step-or-context>` — UNIQUE per render; a repeated
   title updates the earlier widget in place instead of drawing a new one
   where the user is looking. The dashboard carries an **action banner** at
   the top of the loop tab: gate buttons (approve / sign-off / resolve,
   wired to `sendPrompt`) when a decision is the human's, an explicit
   "no action needed from you — <role> is on <step> · next human gate: X"
   strip when agents are working, and a retro button at done. Their click
   IS the proceed — no command to remember.

Never mutate anything from this skill — status reads and renders only.
