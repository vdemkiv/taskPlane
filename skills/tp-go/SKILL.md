---
name: tp-go
description: Carry delivery through seven explicitly accepted phases with shared graph, decomposition, dashboard and verified evidence.
---

# Delivery with explicit approval policy

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-go --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Read [the shared flow](references/shared-flow.md) before execution. It owns the
phase policy, shared artifacts, required evidence and host capability boundary.
Product, Design, Plan, Build, Evaluate, Engineering and Retro each require acceptance
of concrete output before the next phase. Human approval is the default; explicit
run-bound authorization may enable policy decisions under the shared flow. The orchestrator
prepares evidence and advances only after that valid decision; it never self-accepts.

At each phase use the shared dependency graph, source component decomposition,
task DAG and dashboard. Standalone Product, Design and Engineering use the same
defaults. Engineering findings can define Product inputs after a human-approved
route extension that retains the finding evidence.

Do useful work within the current authorized phase until its output is reviewable.
Build follows the accepted Plan's exact paths and prerequisites. Verify affected
behavior; repeat checks only after changed inputs, failure or a specific gap.
Evaluate and Engineering do not silently fix source: propose a scoped repair visit
for acceptance. Finish only after every required visit has valid human or authorized policy acceptance.

Use native_workflow inside Codex/Claude: start with an exact scope JSON and the actual
user-request reference; submit evidence and record the explicit response using the
observed-decision envelope documented in the CLI reference. Every transition still
requires a validated checkpoint and the applicable human or policy decision. The local store and provenance are cooperative workflow
controls, not host-authenticated approval or containment. Show workflow availability
separately from the four unavailable host protections. An explicitly requested
protected_host profile must refuse without its trusted owner; never silently downgrade.

Usage remains advisory. Record meaningful progress and evidence, keep unknown usage
visible and use waste advisories to simplify the next deliverable. A telemetry
failure does not erase acceptance; missing required phase evidence blocks submission.
Use [native delegation](references/codex-native-dispatch.md) only when authorized.

Keep event references and observed command handles bound to their conversation,
checkpoint and phase revision. Actor labels and hook activity alone never approve.
Known live work prevents sealing; a cancellation request is not a completion event.
Incomplete native process coverage stays unknown in native_workflow and blocks
protected_host execution. See [CLI contracts](../../docs/cli-reference.md).

Use `flow policy` only for explicit additional user instructions; preserve their actual
provenance, phases, stops and conditions. After each submission, use `flow auto-decide`
only when the current policy permits it, then advance. Pause on refusal; never invent
a human response or ask again when a valid explicit policy already authorizes the
checkpoint. See the shared flow and CLI reference for the exact envelopes.

## Consume bounded context

After activation, start, advancement or resumption, use the installed runtime's
`flow context --workspace <checkout> --run <run>` and consume the returned
handoff with `--consume <handoff-ref-sha256>`. Use `--task <current-task-id>` for
focused work. Read every omitted required input using `--read <ref-sha256>`;
follow index children and page cursors until all required bodies are returned.
A fresh consumer receives these bodies, scoped source and relevant findings;
do not copy the predecessor conversation or tool history into its instructions.
No context operation authorizes dispatch or a phase transition.

For phase submission, obtain a current whole-phase receipt (without `--task`)
and include the returned `context_receipt` object in the phase output. Renew it
after source, scope, revision or accepted input changes. New bounded/v1 runs
reject missing, partial, foreign or stale receipts. Legacy runs keep their
original evidence contract. A receipt proves returned data, not model attention.

Routine command summaries preserve the current gate and reference full details.
Expand specific references when needed; request `--full` only for a consumer that
requires the complete legacy report. Native dashboards retain complete evidence.
Reuse a prior passing check only when its verification record matches current
source/dependency, tests, runtime, environment, command and criteria fingerprints.
Retain failed/unknown results and findings; reuse never transfers approval.

## Scoped native workers

When authorized, use the installed native dispatch protocol: root prepares a run-bound
task grant, the observed worker claims its identity and consumes its own task context,
and root verifies the joined result before satisfying dependencies. Fill observed
capacity with useful independent work; two is only the minimum live acceptance test.
Workers return evidence and cannot operate root phase controls or publish task definitions.
Require actual loaded-runtime and native execution evidence for live claims.
