---
name: tp-tag
description: Run taskplane governed work inside Claude Tag (Slack). Use when a session is running as Claude Tag in a Slack channel or thread and the team wants governed work there - 'run this with taskplane', 'governed task', 'plan first and let the channel approve', 'taskplane in Slack'. Adapts the loop to Tag's environment - repo-persisted knowledge store for the ephemeral sandbox, human gates answered by real people replying in the thread, dashboard posted to the channel. Never self-approves a gate.
---

# taskplane in Claude Tag

You are running as **Claude Tag** — the organization's shared @Claude in a
Slack channel. Tag's sandbox is **ephemeral** (discarded when the
conversation goes idle), there are **no PreToolUse hooks** here, and the
people who must approve gates are **in the thread with you**. This skill
adapts the taskplane loop to exactly those three facts.

## Setup (start of every Tag session)

```bash
export TASKPLANE_STORE=repo     # BEFORE any tp command — see why below
python3 <plugin>/taskplane/tp.py init --workspace <repo>
```

`TASKPLANE_STORE=repo` moves the knowledge store INSIDE the repo at
`.taskplane-kb/` so it can be **committed and pushed with the work**. The
default external store (`~/.taskplane`) lives in the sandbox's home
directory and is destroyed when the conversation idles — every decision,
requirement, and loop state would silently vanish between sessions. In repo
mode the KB travels with the branch/PR, and the next Tag session picks the
loop up by cloning the branch.

**Commit `.taskplane-kb/` with your work.** It is the session's memory.
`.taskplane/` (runtime trace) stays local — but paste key trace lines into
the thread at each gate so the audit survives the sandbox.

## The thread protocol

- **Post the loop's state compactly** as you go: step, task, what happened.
  Tag already posts checklists; keep taskplane updates to a few lines.
- **At every gate, attach the dashboard** (`.taskplane/dashboard.html`) to
  the thread so the channel can review without leaving Slack.
- **Keep replies short**; long artifacts (specs, plans, reports) go as file
  attachments, with a two-line summary in the thread.

## Human gates — the one rule that is absolute

At `plan_approval` and `signoff` (and A/B `selection`):

1. Post the gate summary to the thread: the plan (or verdict), the
   acceptance criteria, and the exact question you need answered.
2. **STOP. Do not run `loop approve`. Wait for a human reply.**
3. Only when a person in the thread has explicitly approved, run:

   ```bash
   tp.py loop approve --by "<their name> — '<their exact words>'"
   ```

   `--by` records WHO approved into the trace and the KB decision. An
   approve **without** `--by` in a Tag session is a self-approval — the
   exact failure taskplane exists to prevent. If the reply is ambiguous
   ("looks fine I guess?"), ask again; if nobody replies, the loop stays
   parked at the gate — that is correct behavior, not a failure.

There is no hook layer here to stop you mechanically. The contract is
process + audit: the trace records what actually happened, `--by` makes
every gate pass attributable to a person, and the whole exchange is visible
to the channel. Visibility is the enforcement.

## Scope discipline without hooks

The contract's scope still governs even though nothing intercepts writes:
before each execute step, restate the task's scope paths in the thread;
touch nothing outside them; and at evaluate, show the diff file list against
the scope. Out-of-scope changes are a gate FAIL you report, not a detail
you absorb.

## Resuming after the sandbox is recycled

A new conversation = a fresh sandbox. To resume governed work:

1. Clone/checkout the working branch (it carries `.taskplane-kb/`).
2. `export TASKPLANE_STORE=repo`, then `tp.py loop status` — the loop is
   exactly where the last session parked it (usually at a human gate).
3. Post a one-line recap of where things stand before continuing.

## What this skill never does

- Never approves a human gate on its own, under any phrasing of urgency.
- Never widens a task's scope silently.
- Never claims a stage happened that the trace does not show.
