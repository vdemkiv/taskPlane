# Taskplane onboarding

Follow [installation and hook trust](../README.md#install-and-run-your-first-task) first. This guide
matches the current `taskplane/tp.py` CLI. Older references to `tp onboard`, `tp init`,
inline setup forms, workspace hook launchers, storage-plan migration or consolidated
pre-implementation approval describe retired workflows; those commands are not
available in this runtime.

## Choose the installed runtime and project

1. Use a host with local plugin execution and Python 3.10+. Install and enable
   Taskplane from the permitted catalog. Codex users start a new task/session after
   installation; Claude Code users follow plugin activation/reload instructions.
2. Open the intended local repository. Confirm the actual checkout and working
   directory, particularly when the host creates a worktree. Ask the host to acquire
   remote code separately if needed; Taskplane does not silently initialize Git,
   commit a baseline, migrate knowledge or acquire a repository during help/status.
3. Locate the loaded skill's plugin directory. It contains `taskplane/tp.py`,
   `skills/`, the host manifest and `hooks/hooks.json`. Verify with
   `python3 /actual/plugin/taskplane/tp.py version --verify` and `help --md`.
4. Keep a run's state in its original checkout. `.taskplane/` contains local workflow
   state, its initialization marker, graph data, the journal and dashboard snapshots.
   An external dashboard publication target does not move the workflow.

A source clone and an installed cached plugin are different copies. Editing the
clone does not update the running plugin. Verify the package source/version before
using new commands; the version parity check validates manifest agreement, not the
identity of every unreleased source change.

## Review and trust hook definitions

Taskplane bundles these event handlers:

| Host event | Installed entry point | Expected responsibility |
| --- | --- | --- |
| SessionStart | `context` | Discover runtime/session readiness and existing workflow. |
| PreToolUse | `screen` | Apply active workflow checks to covered tools. |
| PostToolUse | `tool-observe` | Observe tool activity and known process handles. |
| SubagentStart / SubagentStop | `subagent-start` / `subagent-stop` | Observe actual native child identities when delegation is authorized. |
| UserPromptSubmit | `human-input` | Consume a complete observed decision/policy envelope when supplied. Plain prompt text does not establish checkpoint binding. |
| Stop | `session-verify` | Report pending work or missing quiescence without forcing an approval loop. |

Inspect the manifest and commands before trusting them. The hook command should
resolve the installed plugin root and invoke its Python runtime. Do not duplicate
these definitions in project config or restore a retired `.taskplane/codex-hook.py`.

In Codex CLI, use `/hooks` to review, trust and enable the Taskplane definitions.
Changed definitions need renewed review; a previously trusted version does not
cover new bytes. Desktop controls depend on the installed host version. Managed
sources follow policy. See [Codex hook review](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).
In Claude Code, use `/hooks` and `/plugin` to inspect the effective configuration
and loading errors, and satisfy the host's project trust and permission prompts.

There are four separate decisions: install/enable a plugin, trust its hooks, approve
a phase (or authorize a run policy), and grant a native tool permission. None of
these implies all the others. Taskplane never recommends disabling host permissions
as a setup step.

## Verify the first task

Ask: **“Use taskplane to design a small change in this checkout and show the Design
output with the Taskplane dashboard.”** Check the following before proceeding:

- The loaded skill/runtime comes from the installed plugin and reports consistent
  manifest versions. `taskplane help` alone should not initialize a workflow.
- The host's hook view shows Taskplane enabled/trusted, and its hook activity or
  diagnostics show a real event from this task. Named-hook commands run manually
  are diagnostics, not proof the host invoked them.
- The run starts at the requested standalone Design entry with the right goal,
  checkout, source components, task DAG and criterion IDs. Status reports
  `workflow_available: true` when the ordinary local profile is usable.
- The agent provides the actual `.taskplane/dashboard.html` link for this run and
  requests opening it using the host's permitted surface. Verify the visible run
  and phase. A queued open, generated HTML or old tab is not proof of display.
- Tokens have measured/partial/unavailable coverage and a baseline. Missing native
  logs stay Unknown. Earlier work without phase boundaries cannot be reconstructed.
- Manual mode waits for the Design output's human acceptance. A hook event or
  completed task row does not count as acceptance.

The ordinary profile intentionally shows **Workflow gates active; host-wide
protection unavailable**. Discovery of a plugin/executable/session is observational;
it does not certify protected storage, independent human origin, full tool
containment or complete process tracking.

## Verify harness activation

Ask: **“Use Taskplane to review this code without starting a delivery flow.”**
Expect one Engineering visit with its source graph, tasks and native dashboard.
The harness is required even when the seven-phase flow was not selected. Product
and Design have standalone entries; existing runs resume their matching phase.

For an indirect host invocation, the execution skill first runs:

```sh
python3 /actual/plugin/taskplane/tp.py flow activate --workspace /project --phase engineering --request-reference conversation/message-id
python3 /actual/plugin/taskplane/tp.py flow report --workspace /project
```

`initialization_required` means Taskplane is selected but no run exists. Prepare
exact scope under `.taskplane/bootstrap/`, then start with `--standalone --phase
engineering --scope .taskplane/bootstrap/scope.json --request-reference REF`.
A full delivery starts at Product. Scope names actual output paths and criteria;
a new Build still requires accepted prerequisites.

Verify separately: installed runtime identity, a real host-triggered hook, active
harness binding, and the correct native dashboard/open outcome. A workflow lock
alone does not prove these. Restore a missing/corrupt binding in the original task;
never borrow another session's run. Claude's SessionStart exports its native
session and transcript to the host environment file; Codex retains its task ID.

Before initialization, covered source writes and opaque commands are denied.
Read/search/question tools and structured `.taskplane/bootstrap/` writes remain
available, along with exact installed setup commands and simple bootstrap reads
(`pwd`, `rg --files`, `cat`). Resolve start failures before completing a review.
After initialization the existing phase checks apply; native permissions and later
source audits still govern opaque shell effects.

After submission, provide the exact native dashboard link and record the outcome:

```sh
python3 /actual/plugin/taskplane/tp.py flow present --workspace /project --run RUN_ID --evidence .taskplane/dashboard.html --presentation linked --note 'Provided the artifact link and opening is queued with display unverified'
```

Use `verified` only after observing the rendered view, or `blocked` with the host
restriction and artifact fallback. The receipt binds the run, visit, checkpoint,
scope and artifact digests without approving anything. A matching handoff survives
the approval-only revision increment. A new output, policy change, repair or stale
evidence needs a fresh handoff. Automatic approval, advance and finish check it
even without Stop; Stop also catches unfinished standalone reviews.

For real missing input, ask through the native question tool or use `flow wait
--workspace /project --note 'Need the requested comparison revision'` and state the
question. This records a wait, not completion; all write/approval checks remain.
New user input or further tool work clears it.

If no hook activity appears, inspect the effective host settings and plugin loading
errors. A manual hook command is a diagnostic, not evidence Desktop executed it.

## Select an approval mode

Manual mode needs no setup. Review the concrete checkpoint and use `approve`,
`approved`, `Changes requested: <reason>`, `reject` or `cancel`.

For autonomous continuation, give explicit additional instructions, for example:

> For this task, auto-approve Product, Design, Plan and Build after required checks
> pass. Stop before Evaluate. Pause for failed or unknown conditions and scope changes.

The orchestrator records the real message, normalized phases/stops/conditions and
scope under `flow policy`. Inspect the dashboard's interpretation. The original
instructions are always an observed condition that must have sealed evidence and
an explanation at each automatic decision. Unclear consent or ambiguous conditions
require clarification; no generic implementation request opts in.

Automatic approval requires submitted evidence, current scope and revision, no known
live work, and passing conditions. It does not authorize scope expansion, extra
visits or protected-host capabilities. Say **“Return to manual approval”** to revoke.
Human intervention and evidence drift suspend continuation. A pause is not an
invitation to repeat unchanged automatic-decision attempts.

## Recover the dashboard and graph

Regenerate with the exact `--workspace` and `--run`, then refresh the existing tab.
Compare its generation time with the new result. Static mode does not monitor
freshness or fetch updated state automatically. Per-generation snapshots remain
available as `snapshot-*.html` and `snapshot-*.json` under `.taskplane/`.

The graph shows observed source relationships, with planned scope or actual changes
explicitly identified. Task prerequisites appear separately. The default focus is
two hops; Full repository contains omitted nodes. A current label requires a
workspace-bound scan receipt and matching source/resolution inputs, including dirty
files. Run `graph --workspace PATH scan --decompose --strict` when needed, then
regenerate. A matching Git commit alone does not establish freshness. Aggregate
component evidence does not imply an exact source-line witness.

When browser presentation is denied, report the limitation and provide the file
link. Do not route the same denied artifact through an alternate server or surface.
Keep generated, opening requested and visibly verified as distinct observations.

## Updating and diagnosing failures

Update from the same permitted source, reload as directed, verify the installed
runtime, review changed hooks and repeat first-task checks. Keep active workflow
state intact. If a package changes supported schemas, follow its documented migration;
do not silently copy or reset the old control store.

For missing skills, check installation and session loading. For skipped hooks,
check current trust/enablement and plugin-root/Python resolution. For wrong views,
check run and checkout before regenerating. For unavailable usage, inspect coverage
and native log access. For state/source audit errors, preserve evidence and correct
the named issue; a refused operation is not an approval. See
[CLI contracts and coverage limits](cli-reference.md).


For a workspace that exceeds the source inventory limit, use
`python3 /actual/plugin/taskplane/tp.py flow diagnose --workspace /project`.
Inspect its partial/complete label and largest inspected files. A native clean
HEAD worktree can provide a supported recovery checkout; uncommitted work remains
in the original. Initialize the new checkout's own exact scope. Do not delete user
files, silently exclude source, or disable hooks as the normal recovery route.
For a sealed or stale active run, the explicit replacement command in the
[CLI guide](cli-reference.md#start-again-without-losing-the-previous-run) preserves
history and starts fresh without copying approvals or automatic authorization.

After an update, compare the actual loaded runtime, skills and hook command bytes
with the release archive. Catalog version, package creation, installed-file parity,
live host invocation and visible display are separate checks. A queued tab or a
manually invoked hook does not establish the latter two.

## Repeated runs and honest completion

Keep one native dashboard file per workspace and ask the host to focus its existing view. A queued open proves only a linked handoff. Record visibly verified display only after observing the rendered run/visit/revision. Sealed report wording can remain “pending” after acceptance; the dashboard projects the actual checkpoint decisions without rewriting historical evidence.

Use `flow report` storage capacity and `flow diagnose` before recovery. Supported retention preserves inactive history. Retire obsolete work only with the actual user request reference; retirement grants no approval. A package build does not prove hosted Windows/Linux checks or that an existing task loaded a new plugin version.
