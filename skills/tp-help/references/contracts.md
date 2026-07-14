
# Govern a task under a taskplane contract

Use this to bound a piece of work: declare what it may touch, what it must
not, and how "done" is verified — then do the work under that contract. The
plugin's PreToolUse hook enforces the boundaries mechanically (out-of-scope
writes, denied commands, and disallowed tools are blocked before they run).

`PLUGIN=${CLAUDE_PLUGIN_ROOT}` and `TP="python3 $PLUGIN/taskplane/tp.py"` in the
commands below. Always run them from the task's working directory (the repo
or folder being changed) so the contract binds to that workspace.

## Honest limitation (state this to the user once, up front)

Inside Cowork, taskplane **cannot** intercept the agent's model calls, so the
dollar/token budget is **cooperative** — tracked and surfaced as a stop
signal, not enforced before spend. What IS enforced mechanically: **filesystem
scope**, the **tool allowlist**, **command deny-rules**, and the
**Definition-of-Done** gate. (The full mechanical budget enforcement needs the
taskplane proxy running a local Claude Code session — see the project README.)

## Workflow

### 1 — Open a contract (before doing the work)

Ask the user, or infer from their request: what may this task touch, what is
off-limits, and how is it verified (a test command)? Then activate it:

```bash
python3 "$PLUGIN/taskplane/tp.py" new \
    --scope "src/**,tests/**" \
    --deny "git push" \
    --tests "pytest -q" \
    "add retry logic to the API client"
```

- `--scope` — comma-separated globs, relative to the workspace, that the
  task MAY write. Everything else is denied. Keep it tight.
- `--deny` — extra shell command patterns to block (repeatable). Sensible
  defaults are always included (`git push`, `rm -rf /`, publishes).
- `--tests` — the Definition-of-Done command the gate will run.
- `--tools` — optional comma-separated tool allowlist (omit to allow any).
- `--budget` — optional cooperative $ ceiling.

If the workspace is a git repo with a commit, the snapshot is recorded so the
DoD scope-diff has a baseline. If it is not a repo, tell the user the
scope-diff will fail closed and suggest `git init && git add -A && git commit`.

Show the user the activated scope and confirm it matches their intent before
proceeding.

### 2 — Definition of Ready (entry gate) — clear it before starting

`tp.py new` prints the DoR verdict, and you can re-check any time:

```bash
python3 "$PLUGIN/taskplane/tp.py" ready
```

- **NOT READY ❌** — there are blockers (no scope set, not a git repo / no
  commit so the DoD can't verify, no task statement). Do **not** start the
  work. Fix the blockers first: set a real `--scope`, or
  `git init && git add -A && git commit -m init`, then re-activate/re-check.
- **READY ✅ with warnings (!)** — safe to start, but surface the warnings to
  the user (no test command → DoD checks scope only; a catch-all `**` scope →
  weak governance; an already-dirty tree → those files count against the DoD
  diff, so commit/stash first).

DoR is the bookend to DoD: **ready to start** (entry) → work → **done**
(exit). Don't begin governed work from a NOT READY state — the guarantees
won't hold.

### 3 — Do the work (the hook enforces scope automatically)

Work normally. The PreToolUse hook screens every Write / Edit / Bash against
the active contract:

- a write outside `--scope` (or inside the out-of-scope list) is **blocked**;
- a command matching a deny pattern (e.g. `git push`, even `git -C . push`)
  is **blocked**;
- a shell command that writes out of scope via redirect or `tee`/`cp`/`dd`/
  `sed -i` is **blocked**;
- a tool not in the allowlist (when set) is **blocked**.

When a block happens, the reason is surfaced. Do not try to route around it
(no `../`, absolute paths, or shell tricks — those are already caught). If a
block is wrong, the scope is too tight: re-run `tp.py new` with a corrected
`--scope` and tell the user what you widened and why.

Track budget cooperatively if the user cares about cost: after a large step,
optionally record an estimate with
`python3 "$PLUGIN/taskplane/tp.py" budget --spent 0.40`, and stop if it exceeds
the ceiling.

### 4 — Close with the Definition-of-Done gate

When the work is complete, run the gate — do not declare success on your own
word:

```bash
python3 "$PLUGIN/taskplane/tp.py" dod
```

It checks that the diff stayed inside scope and runs the test command. On
**PASS ✅**, report the in-scope files changed. On **FAIL ❌**, fix the listed
problems (out-of-scope files, failing tests) and re-run — never report the
task done while the gate fails.

### Inspecting state

`python3 "$PLUGIN/taskplane/tp.py" status` prints the active contract. The
decision trail (activations, blocks, DoD results) is appended to
`.taskplane/trace.jsonl` in the workspace — cite it as the audit record.

## When NOT to use this

Skip for read-only or exploratory tasks with no writes, and for pure
conversation. This skill is for changes that need bounded scope and a
verifiable finish.
