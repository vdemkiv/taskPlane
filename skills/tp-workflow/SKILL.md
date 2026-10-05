---
name: tp-workflow
description: Create, inspect, edit, save, preview or invoke a reusable typed Taskplane workflow through the existing harness.
---

# Author and invoke workflows

Keep the user's intent explicit: create, edit, inspect, preview, run, or create
and run. Creating or saving a definition alone never invokes its tasks. Read the
[supported contract and examples](../../docs/workflow-builder.md) and use the
[builder role](../../agents/tp-workflow-builder.md) for scoped authoring.
Use the actual installed runtime and its `workflow catalog`; a source checkout
or version label does not establish loaded support. Report missing commands,
contracts or capability assets without installing a plugin or weakening gates.

## Inspect and preview

Help, catalog, validation, definition inspection and preview are read-only.
Do not activate or initialize a run for these requests. Use exact explicit
`--workspace` and `--definition` paths, and a strict JSON object for `--inputs`.
`workflow preview` permits omitted inputs and reports what remains unresolved.
Its JSON includes task dependencies, exact reads/writes, phase evidence paths,
runtime blockers and manual decisions. It neither persists a package nor
observes native capacity. Never describe `runnable: true` as authorization.

## Create and edit

Resolve the selected workspace under the
[shared flow](../tp-go/references/shared-flow.md), then have the root inspect
the current run before writing. Authoring uses a scoped standalone Design run
when none exists: activate `--phase tp-workflow`, prepare exact Design scope and
task/evidence paths, and start `--standalone --phase design`. Include the intended
versioned definition file in that scope. Native workers claim their own grants
and consume their own context; they do not operate root controls.

If a matching authoring Design run is active, continue it. If a delivery Design
visit already permits the requested definition paths, use that visit and its
existing checkpoint. Any other occupied run must be resolved explicitly: preserve
its state and explain whether the user can continue it, finish its authorized
route, or explicitly request replacement/retirement. Never start a second active
run, infer replacement from "create and run", or expand scope implicitly.

Reuse accepted decisions. Establish the outcome, supported route, necessary
inputs, criterion IDs, declared source/dependency/test reads, exact outputs and
native workers. Use typed bindings, not string interpolation. Prefer the change
risk review seed for independent security/code-quality review; Design brief and
feature delivery are separate fixed routes. For delivery, obtain a finite outer
Build file allowlist including future check history and retry logs; actual Build
decomposition and typed checks are finalized and accepted in Plan.

Validate the draft and preview available bindings. Save through `workflow save`
to `workflows/<id>.<version>.workflow.json` within the accepted Design scope.
Publishing is no-clobber: identical canonical bytes are reusable; a changed
definition needs a new version and its exact authorized path. Present the saved
definition, readable preview, unresolved inputs and limits with the shared
Design evidence and dashboard. Resolve the Design checkpoint through the
existing human or explicitly authorized policy decision. Create-only ends there.

## Root execution handoff

For an execution request, the root checks the actual authorization already in
the conversation. "Create and run" permits the handoff after the authoring
checkpoint is resolved; it does not accept unseen Design output or future phase
checkpoints. Reuse valid decisions without asking again. Finish the standalone
authoring route before starting the saved workflow; an active delivery Design
visit must continue its existing route and cannot launch a nested invocation.

Collect unresolved bindings, preview again and use a fresh output prefix for
each invocation. Compile only under `.taskplane/bootstrap/workflow-<invocation>`
through `workflow compile`. Compilation writes a pinned package but launches
nothing. Inspect its scope, first-phase tasks, provenance and `start_arguments`.
The root uses these through the existing installed `flow start`, with the actual
request reference. Standalone Product/Design/Engineering retain their route;
delivery starts at Product and preserves all seven checkpoints.

Follow [native dispatch](../tp-go/references/codex-native-dispatch.md): observe
capacity and automatic hooks, prepare/claim each required worker, drain its
context, join and accept its verified result before dependent work. Each review
lens needs a distinct native identity. Root synthesis waits for every reviewer.
Unavailable capacity remains a blocker; do not relabel serial work as independence.
Use the same run, graph, task DAG and `.taskplane/dashboard.html` throughout.

For continuation, inspect that exact run and call `workflow check --workspace
ROOT --run RUN`. Resume with its existing state, not another compilation/start.
Changed pinned inputs, runtime or capability assets require diagnosis and
revalidation; preserve failed attempts. Authorized Build changes are handled by
accepted Plan scope and fresh verification. A changed saved draft cannot retarget
an active run. Report live candidate execution separately from parser/compiler
or harness fixtures; no fixture closes the WFB-LIVE acceptance gate.
