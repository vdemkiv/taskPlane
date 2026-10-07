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

Compiled delivery currently declares `native-default/v1`, but does not emit the
separate `implementation/v1` planning contract or a complete implementation
intent. Its implementation feasibility is therefore `legacy_unknown`.
Compilation and a runnable preview do not establish source/test readiness.
Native ownership and the accepted Plan still constrain the work. Adding only a
planning-contract label would not supply the missing implementation/test
bindings, per-criterion coverage, three Build outputs and root exception data;
historical packages retain their original contract. A complete compiler intent
migration is separate work, not part of the native ownership repair.

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

The original development evidence used installed 2.31.7, which did not contain
Workflow Builder, and recorded WFB-LIVE as `not_run`. That historical result is
preserved. Newer source, release archives or installed version labels do not
replace it with a passing live result. WFB-LIVE remains unverified until a
separately authorized host actually loads the selected package and
executes two compiled change reviews with distinct native lens identities,
claim/context/hooks, joined accepted results, root synthesis, same-run dashboard
and interruption/resumption evidence. Parser/compiler, CLI and package fixtures
are reported separately and cannot close that gate. Installation or reload is
separate from definition authoring and requires the applicable user authority.

## Read-only actual-host evidence verifier

From the source checkout, run:

```sh
python3 scripts/verify_workflow_builder_live.py --evidence FILE
```

The command prints one JSON result to stdout and exits zero only when all
required evidence checks pass. Missing files, stale hashes, `not_run`, partial
context, unobserved termination, missing rendering and incomplete final outcomes
return nonzero. It reads evidence and installed files; it does not launch a
host, execute a referenced command, import the referenced runtime, generate
hook events, mutate a controller or write the index. Passing tests of this
inspector establish inspector behavior only. They never close WFB-LIVE.

The index schema is `taskplane.workflow-builder-live-evidence/v1`. Every `REF`
below is `{"path":"/absolute/or/index-relative/path","sha256":"64 lowercase hex"}`.
Paths resolve relative to the index, not the shell working directory. A large
native JSONL transcript may use `{"path":"...","offset":123,"bytes":456,
"sha256":"..."}`: the hash covers those exact original bytes, and the slice
must contain complete original JSONL records. A transcript field also accepts
`{"segments":[REF,REF]}` for noncontiguous original records. Retain both halves
of each tool call/result pair and the referenced native headers/notifications;
do not author replacement transcript records. Reads are bounded to 32 MiB per
reference, 128 segments per transcript and 256 MiB overall. Symlink references,
duplicate JSON keys, non-finite numbers and stale bytes refuse.

```json
{
  "schema": "taskplane.workflow-builder-live-evidence/v1",
  "status": "observed",
  "definition": "REF to the exact saved definition",
  "authoring_transcript": "REF or segments containing the actual save call/result",
  "invocations": [
    {
      "workspace": "/actual/durable/acceptance/checkout",
      "root": "actual-native-session-id",
      "run": "actual-run-id",
      "controller": "REF to the raw native controller store or run-state snapshot",
      "compilation": "REF to the actual package compilation.json",
      "root_transcript": "REF or segments from the native Claude JSONL",
      "worker_transcripts": [
        {"worker_id": "actual-security-worker", "transcript": "REF or segments"},
        {"worker_id": "actual-quality-worker", "transcript": "REF or segments"}
      ],
      "launch": "REF or segments from the parent's actual TTY call/result",
      "dashboard": {
        "snapshot": "REF to the native snapshot JSON",
        "html": "REF to its native HTML",
        "render": "REF or segments from the actual CUA tool call/result",
        "screenshot": "REF to the retained PNG or JPEG"
      },
      "interruption": {
        "before": "REF to the same run's unaccepted controller snapshot",
        "interrupt": "REF or segments with the terminal interrupt and exit result",
        "resume": "REF or segments from the same session's actual --resume TTY call/result"
      }
    },
    "Second complete invocation object; interruption is optional on this one"
  ]
}
```

The strings standing for references in this example are explanatory placeholders,
not valid evidence. Exactly two complete invocation objects are required. At
least one needs the interruption object. An honest unperformed index can contain
`"status":"not_run"` and explain the blocker; it intentionally cannot pass.

### Original human approval relayed from Codex

An invocation may add `approval_relay` when its actual human checkpoint response
occurred in the coordinating Codex conversation. Recorded cross-session approval
requires the controller's explicit `taskplane.observed-decision/v2` contract.
The read-only inspector checks the corresponding native evidence and does not
record a decision.
Keep the full original response, including whitespace and conditions. Never
create a Claude user frame or rewrite the original conversation identity.

```json
{
  "schema": "taskplane.approval-relay/v1",
  "origin_conversation": "original-Codex-session-id",
  "session_meta": "REF to the first original Codex JSONL row, offset 0",
  "human": "REF to one complete original native human message",
  "presentation": "REF to the preceding parent assistant presentation",
  "launch": { "segments": ["REF to the actual launch call", "REF to its actual result"] },
  "launch_flag": "--session-id",
  "native_presentation": "REF to the raw target harness containing presentation",
  "snapshot": "REF to that presentation's immutable native snapshot JSON",
  "html": "REF to that presentation's immutable native snapshot HTML",
  "binding": {
    "root": "target-native-session-id", "run": "target-run-id",
    "workspace": "/actual/acceptance/checkout", "visit": "target-visit-id",
    "checkpoint": "target-checkpoint-id", "packet_revision": 1,
    "scope_digest": "actual-scope-digest", "manifest_digest": "actual-packet-digest"
  },
  "decision": "complete taskplane.observed-decision/v2 envelope, including relay"
}
```

Every parent reference must include explicit `path`, `offset`, `bytes` and
`sha256` fields and select complete records from the same original
`~/.codex/sessions/.../rollout-...-ORIGIN.jsonl`; copied logs are rejected.
`session_meta` pins the native session identity and its `vscode` or `cli` human
session source; subagent or automation session origins are rejected. The human source must be a
retained complete native `response_item/message`, role `user`, with
`content_item_kinds: ["user.text"]`, matching retained message ID and no system,
automation or tool origin. The assistant presentation also needs complete retained
message metadata. It must name the exact checkpoint ID and link its immutable
native `snapshot-....html`; a dashboard link alone is insufficient. Native target
submit/present results, the harness checkpoint binding and both immutable snapshot
digests must agree. HTML and snapshot references cover the whole bounded regular
files, with explicit `offset: 0`, byte count and SHA-256.
Chronology requires run start, submission, presentation, then human response.
The launch flag is `--session-id` or `--resume`; the selected native parent
call/result must actually launch that target, before the presentation.

The v2 envelope keeps **`source.conversation` equal to the original Codex session**;
`binding.root` remains the target Claude session. `origin_conversation` in the
index must equal that unchanged source identity. Its source reference is exactly
`ORIGINAL_ABSOLUTE_JSONL#offset=N&bytes=N`, its timestamp is the original frame's
timestamp, and its excerpt is the full exact human text. Its presentation
reference uses the same byte-reference format and exact presentation timestamp.
Its `binding` uses `revision` in place of the relay's `packet_revision`.
Use `recorder: "root_orchestrator"`, `source.kind: "conversation"`,
`source.actor: "user"`, `source.automatic: false`, and `choice: "approved"`.
The envelope's `relay` object has schema `taskplane.original-source-relay/v1`
and exactly these fields copied from the verified index evidence: `session_meta`,
`human`, `presentation`, `launch`, `launch_flag`, `html` and `snapshot`. It contains
no invented human message or target-session replacement for the source identity.
The actual parent launch must use a direct native `exec_command` JSON call or
the single wrapper `text(await tools.exec_command(<JSON object>));`, with
`tty: true`, the exact target `workdir`, an absolute Claude executable, only
`--plugin-dir`, `--session-id` or `--resume` options, and at most one prompt.
The paired result must contain an actual running native session ID. Dynamic
JavaScript launch construction is outside this v2 contract.

The inspector currently accepts only the complete simple approval phrases
`approve`, `approved`, `approve as is`, `approved as is`, `looks good, proceed`,
or `go ahead`, ignoring case and surrounding whitespace only for interpretation.
Other wording needs review, not excerpt editing.

`verify_approval_relay(reader, item, state, transcript, decision=None)` can inspect
a pending or approved checkpoint independently of finish or invocation B. A
pending pass returns `recorded: false`. An approved checkpoint additionally needs
the exact recorded event/binding/provenance, including `provenance.schema` and
the unchanged `provenance.relay`, and a successful native direct
`python .../tp.py flow decide` call with the identical parsed envelope. Quoted
commands, `flow wait` notes, assistant assertions and nested claimed results
cannot satisfy that call. Existing direct-native human/dialog evidence remains
supported with v1 when no relay is declared. Historical v1 evidence that remapped
the source to the target can be inspected only while unrecorded, returning
`historical_only: true`; it cannot certify an applied cross-session approval.
Old evidence, including a passing pending inspection, cannot authorize a fresh
checkpoint. A relay pass is cooperative evidence
consistency, not host attestation, a new authorization, or permission to bypass a
host refusal. All two-invocation, finish, context, worker, recovery and rendered
dashboard checks remain required for the full verifier.

Collection requirements:

- Save the controller evidence after actual finish. The store may contain both
  runs; the index selects each exact run. Keep the original context objects under
  `.taskplane/context-v1/objects/`, pinned source files, review outputs and
  installed runtime files readable. Do not copy approvals or context receipts
  into a replacement run. Preserve failed attempts and retired history.
- The compilation reference must point at the actual immutable package inside
  the selected workspace. The verifier checks its digest and member bytes,
  identical saved definition, actual differing input values (differences only
  in labels or output prefixes do not count), fresh output prefixes and package
  and run identities. Installed module/interpreter/member hashes are checked
  against the package's loaded-runtime observation and worker hook evidence.
- Retain actual native Claude call/result frames for compilation/start,
  Agent/Task dispatch, claims, every required `flow context` body response and
  the final drain, ordinary matched
  automatic-hook calls, native completion notifications, accepted results,
  decisions and `flow finish`. Distinct security and code-quality workers must
  have their own grants, full immutable receipts, fresh inputs/outputs and
  accepted terminal results. A done message or result file alone cannot pass.
- Required context delivery is checked against the digest-verified canonical
  CAS handoff, signed view and required trees in `.taskplane/context-v1/objects`.
  Every body page must appear completely in the consumer's native tool results,
  with matching data, digest, kind, form and cursor metadata. Complete inline
  `--consume` views and shared-body provenance are supported. A valid final
  receipt cannot replace an omitted page, truncated output or a
  `<persisted-output>` preview. Preserve failed delivery history separately;
  a later empty drain does not repair missing original response bodies.
- On a host that persists 32 KiB drain results, use the installed runtime's
  supported `--read-required HANDOFF_SHA` transport, which keeps each complete
  response below 16 KiB. Run one unfiltered foreground command at a time with
  the exact workspace/run/task binding, retain every full response, and repeat
  until `remaining_required` is zero. Then run `--drain HANDOFF_SHA` once for
  the terminal `done: true` receipt (normally with an empty `pages` array).
  Individual `--read SHA --page N` results are also valid when every required
  page is delivered. Never pipe, filter, use `head`, redirect or discard context
  or `flow worker --operation accept-result` responses. If a host persists a
  response, stop and preserve that failed evidence; a cache pathname alone is
  not delivery to the consumer.
- Launch and resume references are actual parent Codex or Claude execution tool
  frames. They must show `tty: true`, `--session-id` or `--resume`, the original
  session identity and returned terminal handle/result. The actual compile call
  must use the pinned installed runtime path, whether Claude selected it through
  its global plugin configuration or an explicit plugin argument. An interruption needs observed terminal exit;
  sending Ctrl-C alone is insufficient. After resume, retain successful
  `workflow check` and `flow report` calls for the original run, with unchanged
  package and prior decisions. Do not start a replacement invocation.
- Retain native `.taskplane/dashboard.html` snapshots before the next invocation
  regenerates that shared file. The JSON/HTML and actual CUA output must identify
  the same run, visit and revision. Retain the screenshot from that rendering.
  A generated artifact, queued opening, link or `rendered: true` assertion alone
  is insufficient. A denied rendering remains a named blocker.
- Finish requires the actual accepted checkpoint, native controller decision
  and `flow finish`, plus the original user message or native AskUserQuestion
  result cited by its provenance. Automatic policy must belong to this run;
  another run's authorization cannot transfer.

These are cooperative consistency checks, not cryptographic host attestation or
proof against a local account rewriting every source. The verifier cannot
observe model attention, interpret screenshot pixels, independently determine
human intent or certify the semantic quality of a review. Review the retained
tool output and screenshot as well as the JSON result. A partial export or
unsupported native frame stays a failure with its reason; do not replace absent
observations with convenient boolean assertions. The native dashboard and
`.taskplane/knowledge/graph.json` remain the shared runtime surfaces.

The Python helper `verify_context_delivery(reader, workspace, transcript,
binding, receipt, task=None)` uses the same strict checks for a worker, root task,
or whole phase. Supply the exact expected binding and native transcript records
for that handoff; omit `task` only for whole-phase context. Its returned coverage
counts are a partial context audit, never a full live acceptance result. The
normal `--evidence` CLI still requires both complete invocations, actual human
authority, rendered dashboards and recovery evidence.

v1 excludes arbitrary phases/order, branches, loops, nested workflows, schedules,
event triggers, business connectors, external writes, credentials, a marketplace
and a visual canvas. Business automation is a later extension, not a callable
capability in this catalog.
