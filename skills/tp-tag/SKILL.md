---
name: tp-tag
description: Run taskplane governed work inside Claude Tag (Slack). Use when a session is running as Claude Tag in a Slack channel or thread and the team wants governed work there - 'run this with taskplane', 'governed task', 'plan first and let the channel approve', 'taskplane in Slack'. Adapts the loop to Tag's environment - repo-persisted knowledge store for the ephemeral sandbox, human gates answered by real people replying in the thread, dashboard posted to the channel. Never self-approves a gate.
---

# /tp-tag — taskplane in Claude Tag

You are running as **Claude Tag** — the organization's shared @Claude in a
Slack channel. Tag's sandbox is **ephemeral** (discarded when the
conversation goes idle), there are **no PreToolUse hooks** here, and the
people who must approve gates are **in the thread with you**. This skill
adapts the taskplane loop to exactly those three facts.

## Approved flow contract

`flow.json` is the canonical graph for this skill. Follow its order exactly:

**Slack thread goal → repo-persisted KB → governed loop → role dispatch +
evidence → dashboard attached → human reply in thread → attributed approval
→ commit KB + resume state.**

Do not skip directly from work to approval, and do not synthesize the human
reply. A request for changes is not approval: leave the loop parked at its
human gate, report the requested change, and resume only through the governed
role the orchestrator assigns. The `approve` node is reached only after an
explicit approving reply and always carries `--by`.

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
The current v2.15 layout: `knowledge/` (decisions, requirements, debt,
flows — and, in repo mode only, `knowledge/state/loop.json`: Tag is the ONE
mode where loop state is committed, which is exactly what lets the next
session resume), `artifacts/` (the per-gate decision snapshots — dashboard,
plan, findings, HEADLINES), and `meta.json` (which project the store
belongs to). `.taskplane/` (runtime trace) stays local — but paste key
trace lines into the thread at each gate so the audit survives the sandbox.

## The thread protocol

- **Post the loop's state compactly** as you go: step, task, what happened.
  Tag already posts checklists; keep taskplane updates to a few lines.
- **At every human gate, attach the engine-authored dashboard**
  (`.taskplane/dashboard.html`) to the thread by reference so the channel can
  review the workflow, dependency graph/blast radius, evidence, and the exact
  approval/request-changes decision without leaving Slack. Do not re-author
  its HTML in the conversation.
- **Keep replies short**; long artifacts (specs, plans, reports) go as file
  attachments, with a two-line summary in the thread.

## Human gates — the one rule that is absolute

At `design_approval`, `plan_approval`, and `signoff` (and A/B `selection`):

1. Attach `.taskplane/dashboard.html` and post the gate summary to the
   thread: the decision, acceptance criteria, graph impact, and the exact
   approve/request-changes question.
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

The state and evidence gates still run mechanically in Tag. For
`execute`, `fix`, `evaluate`, and `em`, the worker role must run
`tp.py loop submit pass|fail` after writing its artifacts. That submission
does not advance or clear anything: it records an engine-computed source and
evidence-artifact fingerprint. The Tag driver acting as orchestrator then runs the matching
`tp.py loop gate`. A missing, mismatched, or stale submission is rejected.
Never let the same role claim that its own submission passed the gate; the
orchestrator reads the evidence and invokes the transition.

The invariants themselves are unchanged and canonical in
`../taskplane/references/harness-rules.md`; this section is their Tag
translation — visibility replaces interception, never the rules.

## Scope discipline without hooks

Tag still has no tool interception, so scope stays
cooperative and must be stated honestly.
The contract's scope still governs even though nothing intercepts writes:
before each execute step, restate the task's scope paths in the thread;
touch nothing outside them; and at evaluate, show the diff file list against
the scope. Out-of-scope changes are a gate FAIL you report, not a detail
you absorb.

## Resuming after the sandbox is recycled

A new conversation = a fresh sandbox. To resume governed work:

1. Run `tp.py repository prepare <target>` so the same repository precondition
   acquires/verifies the working branch. If it returns `needs_user`, a real
   person replies in the thread and `repository resume` continues the run.
   The branch carries `.taskplane-kb/`; private runtime/artifacts stay outside
   the checkout.
2. `export TASKPLANE_STORE=repo`, then `tp.py loop status` — the loop is
   exactly where the last session parked it (usually at a human gate).
3. Post a one-line recap of where things stand before continuing.

## Private mode and publishing (team plans)

**Private mode is unavailable inside Tag itself**: the private store lives
in the sandbox home, which is destroyed when the conversation idles — and
`TASKPLANE_STORE=repo` forces the shared store, so `tp share set private`
refuses with an error here. A teammate who wants to work privately does so
from Claude Code or Cowork on their own machine, then publishes.

Individuals on the team may be working PRIVATELY (`tp share set private`
from Claude Code/Cowork): their decisions live in their own store,
invisible to the channel. When
someone says they want to share their work with the team, run
`tp.py share push --ids <ids>` in their session — it publishes the selected
decisions and flows into `.taskplane-kb/` (re-numbered into the shared
index), then
commit it. Like a git push, publishing is always a deliberate human ask —
never push someone's private knowledge on your own initiative.

## What this skill never does

- Never approves a human gate on its own, under any phrasing of urgency.
- Never widens a task's scope silently.
- Never claims a stage happened that the trace does not show.
