# taskplane

**v2.27.0 source tree** — a shared delivery workflow for Codex and Claude, with
product decisions, a dependency graph, task decomposition, evidence, and one dashboard.
Version 2.27.0 adds explicitly authorized autonomous approvals
and improve dashboard identity and phase token accounting. Check your installed
runtime: an older cached package does not acquire changes made in this checkout.

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

Taskplane takes a concrete goal through **Product → Design → Plan → Build →
Evaluate → Engineering → Retro**. Manual approval is the default. You can explicitly
authorize automatic phase approvals for one run, with additional instructions and
mandatory stops. Both modes validate the same evidence, scope and phase order.
Native tool permissions and hook trust remain separate.

## Installation

### Prerequisites

- A host that supports local plugin skills and lifecycle hooks: Codex desktop/CLI
  or Claude Code. Account and organization policy must allow the plugin.
- Python **3.10 or newer** on the host's command path: `python3 --version` on
  macOS/Linux, or `py -3 --version` on Windows. The shipped runtime uses the standard library.
- Git and a local project folder accessible to the host. Open the intended checkout
  before starting delivery; repository acquisition is a separate host operation.
- Read/write access to the project's `.taskplane/` runtime directory. Keep it local
  and ignored by Git. It holds workflow decisions and observations; do not delete it
  to get past a checkpoint.

### Codex

1. Open **Plugins** in the desktop app, or enter `/plugins` in Codex CLI.
2. Find **taskplane**, inspect its source and installed components, and install/enable
   the version offered by your permitted catalog. If it is absent, ask your workspace
   administrator to supply an approved catalog/package.
3. Start a new task/session in your project so the plugin skills load.
4. Complete **Trust the hooks** below before relying on interception or observations.

The current CLI also supports `codex plugin list` and
`codex plugin add taskplane@MARKETPLACE`, where `MARKETPLACE` is the exact catalog
name reported by `list`. Use `codex plugin add --help` for your installed CLI; do not
assume a GitHub repository is already a configured Codex marketplace.
See the [official plugin installation guide](https://learn.chatgpt.com/docs/plugins).

### Claude Code

In Claude Code, add this repository's marketplace and install its plugin:

```text
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
```

Choose the installation scope your organization permits. Use `/plugin` to check
Installed and Errors. Follow the install summary; run `/reload-plugins` if activation
is requested. Then open the intended local project and inspect the loaded hooks.
For a managed installation, use your organization's catalog instead of adding one.
These commands follow [Claude Code's marketplace instructions](https://code.claude.com/docs/en/discover-plugins).

A chat surface without local filesystem, process and hook support cannot provide
the complete local workflow. Do not infer those capabilities from a visible skill.

## Trust the hooks after installation

**Installing or enabling the plugin does not automatically trust Codex hooks.**

1. Inspect the installed `hooks/hooks.json` and the commands it invokes. Taskplane
   uses the installed `taskplane/tp.py` entry points; it does not need a copied
   project launcher or duplicate hook registration.
2. In Codex CLI, open **`/hooks`**. Review each Taskplane definition, trust it and
   confirm it is enabled. In the desktop app, use the available hook review/settings
   controls; labels may differ by host version. Managed hooks follow administrator policy.
3. When an update changes a hook definition, review and trust the new definition.
   Codex binds trust to the current definition hash and skips untrusted changes.
4. In Claude Code, inspect `/hooks`, plugin loading errors and project trust/permission
   prompts. Use that host's controls rather than assuming Codex's trust UI applies.
5. Verify actual hook activity on a fresh task as described in
   [onboarding](docs/onboarding.md#verify-the-first-task). A manifest or a successful
   manual Python command proves availability, not that the host ran a lifecycle hook.

See [OpenAI's hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).
Hook trust permits those hook commands to run. It does not approve Taskplane phases,
authorize autonomous mode, or change native tool permissions.

## Harness activation, including standalone reviews

Every Taskplane execution uses the harness, including standalone code review and
resumed stages. A review starts an Engineering run with a source graph, tasks and
the native dashboard. Product and Design can also run standalone; the seven-phase
flow is not required for a review-only task.

Supported execution prompts and skill entries select an initialization gate. If
setup is skipped, covered implementation and premature review completion are
blocked. Help/status remain read-only. Indirect invocations explicitly activate the
harness before scope preparation. Activation never approves a phase or enables
autonomy. The agent records the native dashboard link/open result at every
checkpoint, and clearly reports queued or blocked presentation.

See [harness setup and recovery](docs/onboarding.md#verify-harness-activation),
including legitimate user waits and checking actual host-triggered hooks.

## First task and installed runtime verification

Open your project, attach/invoke the Taskplane plugin, and ask:

```text
Use taskplane to describe its available commands and check the installed version.
Then design a small change to this project; show the Product output and Taskplane dashboard.
```

`taskplane help` and `taskplane status` inspect existing state without starting a run.
A delivery request starts a scoped run. Product, Design and Engineering also support
standalone work, using the same source graph, task DAG and dashboard.

Resolve `TASKPLANE_PLUGIN` to the **actual installed directory** reported by the host
or the loaded skill path (the directory containing `taskplane/tp.py`). Do not select
a cache directory by sorting version names or use a stale workspace launcher.
For example, after setting that path in your shell:

```sh
python3 "$TASKPLANE_PLUGIN/taskplane/tp.py" version --verify
python3 "$TASKPLANE_PLUGIN/taskplane/tp.py" help --md
```

Use `py -3` in place of `python3` on Windows. The version check must return `ok: true`.
In the first delivery run, verify the dashboard's goal, checkout, task/run identity,
phase, source graph and available token measurements. See
[the full onboarding checklist](docs/onboarding.md) for evidence and recovery.

## Manual and autonomous delivery

Ordinary instructions such as `taskplane build this feature` retain human checkpoints.
At each checkpoint, review the concrete output and reply `approve` or `approved`.
To correct or stop it, use `Changes requested: ...`, `reject`, or `cancel`.
The current human-response parser does not recognize every natural-language synonym.
Valid existing approvals are reused while their evidence and scope remain unchanged.

To authorize automatic approvals, put explicit additional instructions in the request:

```text
Use taskplane to implement the settings page. For this task, run autonomously and
 auto-approve phases after required checks pass. Stay within the agreed scope;
 pause for failures, unknown verification, or scope changes. Stop before Retro
 so I can review the final outcome.
```

Taskplane records the actual instruction, run/scope binding, allowed phases, mandatory
stops and conditions. It shows the interpreted policy on the dashboard. Clear consent
does not require another redundant enablement prompt. Ambiguous instructions stay
manual until clarified. A request to implement autonomous mode is not itself consent
to run autonomously.

Every phase still produces and submits evidence. Automatic approvals are labelled
**policy decisions**, with the authorizing policy version and condition assessment;
they are never represented as human responses. Required evidence, phase order,
Build scope and passing checks are enforced. Additional user conditions require
an evidence-backed assessment; unknown conditions pause. Optional usage gaps alone
do not block approval unless your instructions make usage a required condition.

To stop automatic continuation, say **“Return to manual approval.”** Rejection,
requested changes, cancellation or evidence drift suspends the policy. Resuming
automatic approval requires a fresh explicit authorization. Restarting the host
preserves the run's current policy; authorization never carries into a different run.
External publishing, installation/trust changes and native permission prompts still
follow the host and the user's authorized scope.

The engine interface is `flow policy`, `flow submit`, `flow auto-decide`, then
`flow advance` (or `finish`). These are separate guarded operations, not an unattended
background scheduler. See [policy JSON and CLI contracts](docs/cli-reference.md#automatic-approval-policy).

## Read the Taskplane dashboard

The shared entry is **`.taskplane/dashboard.html`**. Every phase uses Taskplane's
existing renderer; no separate Product page replaces it. The agent links it and
requests opening through a permitted host surface. Generated, opening requested,
and visibly verified are different states. If opening is blocked, use the artifact
link; a queued request is not confirmation that it appeared.

- **Identity:** check the goal, run, task, execution checkout, phase/visit, workflow
  revision, generation time and data observation time. The publication location can
  differ from the execution checkout; this does not merge their state.
- **Tokens:** current visit and cumulative run usage are separate. The detail table
  includes input, cached/uncached input, output, coverage and gaps. Phase totals
  combine visits; work, review and post-completion follow-up have distinct intervals.
  Missing/reset/late session boundaries remain unknown or unallocated. Old runs do
  not receive invented retrospective phase counts. Host approval-review usage is
  separate. Cached input is already in input and reasoning is already in output;
  these figures are not billing estimates.
- **Freshness:** this is a static snapshot. Regenerate it after meaningful progress,
  then reload the open tab. Reload alone does not collect new data. Per-generation
  HTML/JSON snapshots preserve historical views.
- **Graph:** the default view shows affected components and two dependency hops;
  Full repository retains the wider graph. Source relationships and task prerequisites
  are separate. Planned scope is labelled separately from actual changes. Inspect
  scan identity, dirty-input freshness, coverage and edge evidence before relying on it.

To regenerate a particular run rather than accidentally inspecting another task:

```sh
python3 "$TASKPLANE_PLUGIN/taskplane/tp.py" flow report --workspace /path/to/project --run RUN_ID
python3 "$TASKPLANE_PLUGIN/taskplane/tp.py" dashboard --workspace /path/to/project --run RUN_ID
```

Without `--run`, commands use the current task's binding. They do not pick an unrelated
latest run. An explicit historical view uses its recorded context.

## Updates and troubleshooting

Update through the host's installed-plugin manager or permitted marketplace. Reload
as directed, verify the installed runtime, review changed hooks, and repeat the first-task
checks. Do not mix skills from one cache version with another runtime or delete an
active run to install an update. New source features need a package built from this tree.

| Symptom | Next step |
| --- | --- |
| Skill missing | Confirm installation/enabled state and start the required new session or reload. |
| Hooks skipped or needing review | Inspect `/hooks`, trust the current definitions and check enablement. |
| Python/plugin root unavailable | Confirm Python on the host command path and the actual installed plugin directory. |
| Dashboard shows an earlier task | Compare run and checkout, regenerate with explicit `--run`, then refresh the same file. |
| Tokens say Unknown | Check native session coverage and baseline; no estimate replaces missing counters. |
| Graph is stale | Run `graph --workspace PATH scan --decompose --strict`, then regenerate the dashboard. |
| Automatic approval pauses | Inspect policy version, stop phases, condition evidence, source drift and failed/unknown checks. |
| Workflow store missing/corrupt | Preserve it and report the exact error; automatic reset is refused. |

## Development

From a source checkout, install the pinned developer dependencies with
`python3 -m pip install -r requirements-dev.lock` in a virtual environment.
Run `python3 scripts/ci_local.py` for tests, Ruff, mypy, version parity and both
packages. Add `--browser` for the real-browser suite; missing browser infrastructure
is a failure, not a successful skipped check. Archives go to `dist/` by default;
individual packaging scripts support `--output-dir`.

The default `native_workflow` uses observed provenance and local state. The local
account can edit both; this is not host authentication or containment. Covered
structured hooks check scope, and transitions audit source effects. An explicitly
requested `protected_host` still refuses without a verified owner. Details and
limits are in [CLI reference](docs/cli-reference.md),
[shared delivery policy](skills/tp-go/references/shared-flow.md), and
[the lens catalog](docs/lens-catalog.md).
