# taskplane — the governance harness

`taskplane/` is the enforcement core for Conductor roles: a Task Contract
bounds what a role may touch, and the plugin's **PreToolUse hook** enforces
that boundary *before* each Write/Edit/Bash runs. Stdlib-only Python (no pip),
so it runs anywhere the plugin does.

## How the hook works

`hooks/hooks.json` registers `PreToolUse → tp.py screen`. On every
Write/Edit/MultiEdit/NotebookEdit/Bash the screener loads the workspace's
active contract (`.taskplane/active_contract.json`) and returns
`{"decision":"approve"}`, `{"decision":"block","reason":…}`, or — when no
contract governs the cwd — **no decision at all** (it abstains).

**When no contract is active, it ABSTAINS** (emits no decision, deferring to
the host's normal permission flow) — it neither blocks nor rubber-stamps. So
the harness is a true no-op for ungoverned work and only enforces when a role
opts in by activating a contract; it never auto-approves tools in a repo
where the plugin merely happens to be installed. Two non-abstain edges: a
contract file that is present but unreadable fails **closed** (block), and an
orphaned contract (dead owner PID, or — if never budget-exhausted — idle past
the TTL) auto-releases first, then abstains. A budget-exhausted contract is a
human gate and is **never** auto-released: the human clears or grants it from
outside the workspace. That's how a single global hook governs any role
without interfering with the rest of the orchestrator.

## `tp.py` commands

| Command | Purpose |
| --- | --- |
| `tp.py new [--scope …] [--read-only] [--write-allow GLOB] [--tools …] [--tests …] GOAL` | activate a contract for the workspace (records a git snapshot) |
| `tp.py ready` | Definition-of-Ready entry gate — blocks on missing scope / no snapshot / no task |
| `tp.py screen` | PreToolUse entrypoint (reads the hook event on stdin) |
| `tp.py dod` | Definition-of-Done exit gate — git scope-diff (fails closed) + test command |
| `tp.py status` / `tp.py clear` | inspect / deactivate the active contract |

## Read-only review contracts (the EM role)

`--read-only` blocks all writes except an allowlist:

```bash
python3 taskplane/tp.py new --read-only --write-allow ".em-review/**" \
    --tools "Read,Grep,Glob,Bash,Write,Edit" "EM review: <target>"
```

The `engineering-manager` role activates exactly this, so its cardinal rule
("validate, never change") is **mechanically enforced** — writes are confined
to `.em-review/**` (reports, scratch checkouts, mocks); any Write/Edit or
shell redirect touching the reviewed source is denied before it runs. Run
`tp.py clear` when the review ends.

## What it enforces vs. not (in a plugin context)

Mechanical (via the hook): filesystem scope, tool allowlist, command deny,
shell write-target screening, read-only + write-allow, the **action budget**
(each governed tool call is metered; the ceiling blocks before the action
runs, and exhaustion is a human `budget --grant` gate), and the DoR/DoD gates.
Cooperative only: the **dollar/token** budget — a plugin can't intercept the
host agent's model calls, so the dollar estimate is advisory (a stop signal),
not a pre-spend interception. The command screener is a cooperative
best-effort layer, not an OS security boundary.

Apache-2.0 (taskplane); bundled inside the MIT-licensed plugin.
