# Workflow Builder

Describe a repeatable outcome, inspect the proposed work, save a versioned
definition, and invoke it with fresh inputs. Workflow Builder compiles a typed
JSON definition into the existing Taskplane harness. The root keeps the run,
native workers, phase evidence and shared dashboard together.

```text
taskplane create a reusable change risk review workflow with separate security
and code-quality reviewers, then one combined report. Save it without running it.

taskplane preview that workflow for this branch against main.

taskplane run the saved change risk review with these inputs.

taskplane edit the workflow to include a testability lens in a new version.
```

Creating, saving, validating and previewing grant no execution or phase
acceptance. "Create and run" authorizes a root execution handoff after the
concrete authoring Design checkpoint is resolved. Manual phase checkpoints
remain the default; an explicitly authorized run policy uses the existing
Taskplane policy mechanism and is never stored in a reusable definition.

## Authoring and occupied runs

`tp-workflow` is the conversational entry point. Inspect/preview/help requests
are read-only. Create/edit requests use scoped Design authoring, with exact
definition and evidence paths. A builder worker returns artifacts to the root;
it cannot start or replace runs, accept checkpoints or delegate.

The root first inspects the selected workspace's current run:

| Existing state | Next action |
| --- | --- |
| No active run | Start a scoped standalone Design authoring visit for create/edit, or the compiled route for an authorized saved-workflow invocation. |
| Matching authoring Design visit | Continue that run and reuse its valid decisions. |
| Delivery Design visit with matching permitted paths | Author within that visit; continue its existing route after acceptance. |
| Another active visit or run | Preserve it and resolve whether to continue, finish, or explicitly replace/retire it. Do not infer replacement from a new workflow request. |

Finish a standalone authoring route before invoking its result. There is no
nested workflow or second active run. Existing `flow` replacement/retirement
controls require the actual user request and current run/revision; a template
cannot supply that authority.

## Packaged seeds

| Definition | Route and work | Required bindings |
| --- | --- | --- |
| [Change risk review](../workflows/change-risk-review.workflow.json) | Standalone Engineering: distinct security and code-quality workers, then root synthesis after both joined results are accepted. Review artifacts only. | Request, exact source/dependency/test lists, base/head Git refs and fresh output prefix. Default review criteria can be replaced. |
| [Design brief](../workflows/design-brief.workflow.json) | Standalone Design: native analysis, root design synthesis and Design checkpoint. | Request, criteria, exact source/dependency/test lists and fresh output prefix. |
| [Feature delivery](../workflows/feature-delivery.workflow.json) | Product → Design → Plan → Build → Evaluate → Engineering → Retro. Native phase work, distinct Engineering reviewers and root synthesis. | Design-brief bindings plus an exact outer `build_files` allowlist. |

Seed filenames are packaged examples. Project publication uses
`workflows/<id>.<version>.workflow.json`. A changed published definition needs a
new semantic version. Identical canonical bytes can be saved again; existing
different bytes are never replaced. Active invocations pin copies of their
definition and bindings, so editing another draft does not retarget them.

## Command walkthrough

Use `python3 <actual-plugin>/taskplane/tp.py` as the launcher below. `ROOT` is the
selected execution workspace; definition and input paths resolve against that
explicit workspace. For source-development fixtures use the candidate launcher
only in the fixture workspace. Do not substitute it for an installed live run.

```sh
python3 <actual-plugin>/taskplane/tp.py workflow catalog --workspace ROOT
python3 <actual-plugin>/taskplane/tp.py workflow validate --workspace ROOT --definition DEFINITION
python3 <actual-plugin>/taskplane/tp.py workflow preview --workspace ROOT --definition DEFINITION
python3 <actual-plugin>/taskplane/tp.py workflow preview --workspace ROOT --definition DEFINITION --inputs INPUTS
python3 <actual-plugin>/taskplane/tp.py workflow save --workspace ROOT --definition DEFINITION --out workflows/change-risk-review.0.1.0.workflow.json
python3 <actual-plugin>/taskplane/tp.py workflow compile --workspace ROOT --definition workflows/change-risk-review.0.1.0.workflow.json --inputs INPUTS --out .taskplane/bootstrap/workflow-review-001
python3 <actual-plugin>/taskplane/tp.py workflow check --workspace ROOT --run RUN
```

All commands return JSON. There is no `--format` flag. Use complete option names;
unknown, duplicated and abbreviated flags refuse. Preview's `--inputs` is
optional; save and compile require `--out`. Inputs are a bounded strict JSON
object: duplicate keys, non-finite numbers and undeclared names refuse. Missing
required values appear as unresolved preview inputs and prevent compilation.

Example bindings for the change-review seed, after selecting real files and
refs in the target project:

```json
{
  "request": "Review this change for security and correctness risks.",
  "source_files": ["src/orders.py"],
  "dependency_files": ["src/permissions.py"],
  "test_files": ["tests/test_orders.py"],
  "base_ref": "main",
  "head_ref": "HEAD",
  "output_prefix": "reports/change-review-001"
}
```

These paths are illustrative, not discovered evidence. Supply the dependency and
test inventory needed for the claim; changed filenames alone do not establish
complete coverage. Empty lists are permitted when deliberately appropriate.
`head_ref` must resolve to the selected checkout HEAD, and declared working file
bytes must match it. Dirty files refuse this Git-bound seed. All declared Git
refs resolve to immutable commit IDs; a deleted source file can be read from its
base blob when it is absent at head. No glob or expression expansion occurs.

Preview reports `ready`, `incomplete` or `blocked`, exact source manifests, task
dependencies, phase writes and evidence paths, runtime compatibility and manual
decisions. `runnable` means the preview is complete and unblocked; execution is
still unauthorized and native capacity is still unknown. Preview writes no
files, starts no run and dispatches no worker.

Compilation publishes only under `.taskplane/bootstrap/workflow-<invocation>`.
It produces `definition.json`, `bindings.json`, `capabilities.json`,
`compilation.json`, `scope.json`, first-phase `tasks.json`, `preview.json`,
`preview.md`, and any required deleted-file blobs. Definition, inputs, selected
role/lens assets, compiler and loaded runtime identities are pinned. Equivalent
canonical data at the same destination yields equivalent packages. Publication
does not overwrite existing content; inspect a failed partial publication and
choose a fresh namespace. It is not a runnable package without a valid final
compilation manifest.

After inspecting the package, only the root uses its returned `start_arguments`
with the installed `flow start` and the actual `--request-reference`. There is
no `workflow run` command or second scheduler. Standalone routes start at their
selected phase; delivery starts at Product. The ordinary scope, graph, task DAG,
worker, context, evidence and checkpoint checks remain in force. The same
`.taskplane/dashboard.html` displays the invocation.

## Definition contract: v1

The canonical schema is `taskplane.workflow-blueprint/v1`. The historical
`design/workflow-builder-example.v0.1.json` is an illustrative draft, not this
strict schema. Unknown fields at every object level and duplicate JSON keys
refuse before capability lookup or dispatch. Limits include 256 KiB per
definition, 128 tasks, 128 inputs and 256 criteria per criteria input.

| Required top-level field | Contract |
| --- | --- |
| `schema`, `id`, `version`, `name`, `description` | Exact schema, lowercase stable ID, semantic version and nonempty human-readable text. |
| `route` | `{"kind":"standalone","phase":"design"}`, with phase `product`, `design` or `engineering`; or `{"kind":"delivery"}` for the fixed seven phases. |
| `trigger` | `{"kind":"manual"}`. |
| `inputs` | Named typed declarations, each with `type`, boolean `required`, and optional typed `default`. |
| `policy` | `approval_default: "manual"`, boolean `source_writes`, `external_actions: []`, `on_failure: "stop_and_report"`. Source writes are permitted only for delivery Build. |
| `tasks` | Nonempty array of typed task patterns described below. |
| `acceptance_scenarios` | Nonempty array of text scenarios; scenarios are descriptions, not test results. |
| `runtime_requirements` | Explicit supported contract names. All routes require `native-default/v1`, `bounded/v2`, `taskplane.task-definitions/v1` and `taskplane.phase-output/v1`; delivery also requires `taskplane.verification-strategy/v1` and `taskplane.verification-history/v1`. |

Input types are `text`, `git_ref`, `workspace_file_list`,
`workspace_output_file_list`, `workspace_relative_directory` and
`acceptance_criteria`. Criteria contain `{ "id": "C1", "statement": "..." }`
objects. Workflow/task IDs begin with a lowercase letter and contain lowercase
letters, digits and hyphens (at most 64 characters); input/output names also
allow underscores. Paths are exact, normalized, workspace-relative literals outside
protected metadata. Traversal, symlinks, globs, interpolation and output
collisions refuse. A referenced input must be bound even if marked optional.

Each task requires `id`, `phase`, `capability`, `purpose`, `execution`,
`depends_on`, `read_bindings`, `outputs`, `criteria_binding` and `verification`.
Optional fields are `lens`, `instructions`, `execution_reason` and
`execution_reference`. Only root tasks carry the last two, and both are required
for root execution. Native tasks cannot use them as an execution downgrade.

Read bindings are `{ "input": "source_files" }` or
`{ "artifact": { "task": "security-review", "name": "report" } }`.
Artifact reads need a direct dependency on the named producer. Criteria bind
with `{ "input": "criteria" }`; every criteria input must be covered. Task IDs
are unique and output names are unique within each task. Dependencies must exist
and be acyclic, and an earlier phase cannot depend on a later one.

An artifact output has `name`, `kind`, a typed `directory` input binding and
`relative_path`, for example:

```json
{
  "name": "report",
  "kind": "report",
  "directory": { "input": "output_prefix" },
  "relative_path": "security.md"
}
```

Build source outputs instead use `kind: "source"` and
`binding: { "input": "build_files" }`, where `build_files` has type
`workspace_output_file_list`. No arbitrary executable, command or connector
field exists. Instructions and verification text are inert descriptions.

| Capability | Phase | Execution |
| --- | --- | --- |
| `taskplane.product.analysis` | Product | Required native worker |
| `taskplane.design.analysis` | Design | Required native worker |
| `taskplane.plan.decomposition` | Plan | Required native worker |
| `taskplane.build.execution` | Build | Required native worker; exact source outputs |
| `taskplane.evaluate.verification` | Evaluate | Required native worker |
| `taskplane.review.lens` | Engineering | Required native worker with a registered, unique lens |
| `taskplane.phase.synthesis` | Any existing phase | Root |

Artifact kinds are `report`, `phase-output`, `supporting`, `verification`,
`requirements`, `design` and `plan`; only the Build capability also accepts
`source`. Use the loaded catalog for the exact lens inventory and asset hashes.
Engineering root synthesis must depend on all declared lens reviewers.

## Delivery, continuation and evidence

Delivery compilation requires a nonempty finite outer Build allowlist. Include
future implementation/test outputs plus the verification-history file and
separate attempt logs needed by Plan. The compiler reserves separate packet,
tasks, report and evidence files for each phase and adds root checkpoint tasks.
Its immutable phase task patterns are proposals. Plan must finalize the actual
Build DAG, exact ownership and typed check commands within that outer scope;
Build must match the accepted Plan. No template or compiler approves Build.

All source/dependency/test lists are fingerprinted, including lists not inferred
from a diff. Fresh invocation outputs must not already exist. A new invocation
uses fresh outputs and run/grant/decision identities. Continuation uses the same
run and its existing evidence. `workflow check --run RUN` is a read-only check
of that run's pinned package and current inputs; it is not approval or repair.

Changed immutable package members, semantic scope, runtime/capability assets,
refs or bound input bytes block dependent execution. An accepted Plan permits
only its declared Build mutations, which still need fresh checks. Preserve
failed or unknown attempts, use the existing diagnostics and supported recovery,
and never delete history or transfer approvals to make a check pass. Limited
capacity queues distinct workers; unavailable required native capacity cannot
silently become root or serial execution.

## Compatibility and release evidence

The catalog inspects the actually loaded interpreter, module paths, Python
bodies, file bytes, selected prompt assets and supported contracts. It is an
observation, not host attestation or an OS sandbox. Matching version strings,
source edits and extracted-package tests do not prove that a host loaded the
candidate or that its automatic hooks and worker protocol function there.

This source candidate is distinct from the installed 2.31.7 runtime used to
develop it, which does not contain Workflow Builder. WFB-LIVE remains
`not_run` until a separately authorized host actually loads the candidate and
executes two compiled change reviews with distinct native lens identities,
claim/context/hooks, joined accepted results, root synthesis, same-run dashboard
and interruption/resumption evidence. Parser/compiler, CLI and package fixtures
are reported separately and cannot close that gate. Installation or reload is
outside this feature's Build scope.

v1 excludes arbitrary phases/order, branches, loops, nested workflows, schedules,
event triggers, business connectors, external writes, credentials, a marketplace
and a visual canvas. Business automation is a later extension, not a callable
capability in this catalog.
