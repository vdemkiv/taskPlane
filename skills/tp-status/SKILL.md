---
name: tp-status
description: "Use when the user asks where things stand with taskplane-governed work: 'status', 'where are we', 'what's the state of the loop/track/requirements/debt'. Read-only snapshot rendered as the inline mission-control dashboard: active track, loop step, tasks, open requirements, tracked debt, KB size, dependency graph — with an explicit action banner (gate buttons if a decision is yours, 'no action needed' if agents are working)."
---

# /tp-status — where the governed work stands

Use native conversation/task status when it answers the question. For an existing
Taskplane delivery, invoke the installed `taskplane/tp.py summary` once. Show the
current work, actual outcome, and any pending user decision. Do not initialize setup,
activate contracts, or run onboarding to inspect status. Expand a specific detail
only when needed; a dashboard is optional when requested or useful.

Read [delivery status](references/delivery-status.md) only for detailed stage or
artifact inspection of an existing delivery run.
