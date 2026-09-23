# Shared delivery policy

Every execution phase uses one root workspace and run, the shared dependency graph
with source component decomposition, a task DAG and `.taskplane/dashboard.html`.
This applies to standalone Product, Design and Engineering as well as full delivery.
Help and status are read-only: they never initialize or advance a workflow.

## Harness activation and dashboard handoff

The harness is mandatory for every execution entry, including standalone work and
resumed stages. Direct Taskplane execution prompts, named execution skills and
native reads of installed execution skill files select a session/workspace-bound
initialization gate. For indirect or unsupported host invocation paths, explicitly
run the installed `flow activate --workspace PATH --phase ENTRY --request-reference
REF` first. Read `flow report`: reuse the matching active visit or initialize the
requested scope. Help/status and quoted examples do not activate. Activation grants
neither phase approval nor implementation scope.

Without an active run, prepare exact scope/evidence under `.taskplane/bootstrap/`,
then `flow start --scope FILE --request-reference REF`. Standalone code review uses
`--standalone --phase engineering`; Product/Design have equivalent standalone
entries. Full delivery starts at Product. Never manufacture earlier acceptances or
force a seven-phase route for review-only work. Every resumed phase retains its
existing run and approvals. Loading another skill cannot grant a different phase.

Before initialization, covered implementation and opaque commands are denied.
Native reads/searches, questions, installed execution-skill loads, exact Taskplane
setup commands, `pwd`, `rg --files`, `cat` and structured bootstrap-file writes
remain available. Arbitrary shell commands are not bootstrap exceptions. After a
run starts, the ordinary profile still relies on native permissions and source
audits for opaque shell effects; this is not host-wide containment.

At each checkpoint, submit evidence, regenerate the native dashboard for the exact
run/visit/revision, provide its link and use the host's permitted opening surface.
Record the actual outcome with `flow present --workspace PATH --run ID --evidence
.taskplane/dashboard.html --presentation linked --note TEXT`. Choose `linked` for
an artifact link or queued open, `verified` only after observing the rendered view,
and `blocked` for a presentation restriction with an accessible artifact fallback.
This is an observed handoff, not host attestation or approval. The immutable
checkpoint handoff survives an approval-only revision increment. A new packet,
policy/scope change, repair visit or stale source requires a fresh handoff. Automatic
approval, advance and finish enforce it even without Stop. Never create a replacement dashboard.

Stop requests one corrective continuation for missing initialization, phase output
or dashboard handoff, including read-only reviews. `stop_hook_active` prevents an
endless loop. When actual user input is needed, use the native question tool or
`flow wait --workspace PATH --note <actual-missing-input>`, then state the question.
A wait never approves anything or weakens write checks. New input or further tool
work clears it, and another phase/revision cannot reuse it. Do not call unfinished
work complete when Stop reports a blocker.

Readiness distinguishes selected-but-uninitialized from active. A manifest, lock
file or manually executed hook cannot prove the host loaded and invoked all hooks.
Keep installed version, actual event evidence and live display limits explicit.
Disabled or unloaded hooks cannot enforce these cooperative checks.

## Acceptance and authority

By default, full delivery has seven separate human checkpoints in this order: Product → Design →
Plan → Build → Evaluate → Engineering → Retro. Produce concrete phase output,
validate it, present its evidence and shared dashboard, then wait for explicit
human approval of that checkpoint before advancing. In manual mode, stop after each phase; a
broad request to implement the goal is not acceptance of future unseen outputs.
Explicit additional instructions can authorize automatic decisions under the policy below.
Reuse an existing approval only while its root, run, visit, revision, scope and
artifact binding remain valid. Never ask again for the same valid checkpoint.
Work produced, evidence validated and human approved are distinct states.

The orchestrator prepares work and requests transitions. It cannot manufacture
human approval or authorize its own automatic policy. Progress notes, task completion, dashboard edits, workspace receipts and
hook-shaped input do not grant authority. Native host permissions remain separate.
The shipped default is `native_workflow`: the Controller enforces required evidence,
explicit checkpoint decisions and covered scope paths using versioned local state.
The orchestrator records the actual human message with source reference, recorder,
explicit choice, presentation/ordering evidence and exact checkpoint binding. A
brief approval applies only to the single unchanged checkpoint already shown.
Interpret clear ordinary-language decisions in context and retain the actual
excerpt. Conversational wording is supported; never demand an exact phrase or a
100% string match. If intent, conditions or the intended checkpoint are unclear,
ask a normal clarification. Named phases, provenance, ordering and stale evidence
checks still apply. Explicit requests for an auto-approved full workflow can
authorize a run policy; generic implementation requests cannot.
Known automation, timeout, cleanup and tool-result events do not approve. When
ordering cannot be established, the response must identify its checkpoint.

Observed provenance is not a host attestation. The local account can edit this store
or fabricate observations; Taskplane does not claim protection against that actor.
All four host protections stay unavailable. `workflow_available` and
`authority_verified` are separate facts. Known structured writes reaching hooks
are checked; opaque commands use native permissions and their visible source effects
are audited. Unknown process census and uncovered later stdin remain explicit.

An explicit `protected_host` request requires the existing trusted owner, protected
storage, independent human origin and complete tool/process controls. It refuses
when these are absent and never falls back to native_workflow. A fixture passing
cannot certify either host. Existing recovery exceptions retain their exact scope
and do not silently migrate old observation journals into accepted decisions.

## Shared evidence at every phase

1. Read `flow report --workspace <root> [--run <id>]` and reuse the relevant scope,
   approvals, task plan and findings. Do not select an unrelated latest run. A full
   route starts at Product. Standalone entry uses `--standalone --phase
   <product|design|engineering>` and cannot grant Build.
2. Inspect the source dependency graph and component decomposition. Use `graph
   --workspace <root> scan --decompose --strict` when source changed or no graph
   exists, then inspect bounded change impact. Planned edges are proposals;
   scans show realized source relationships. Attach a task DAG with stable IDs,
   owners, prerequisites, exact paths, criterion IDs and verification. Both source
   components and execution tasks are required; one cannot substitute for the other.
3. Produce the current phase's evidence within its exact write scope. Attach it
   to the same run; update the shared tasks and dashboard at meaningful milestones.
   Source-changing work is confined to accepted Plan/Build scope. Evaluate and
   Engineering report defects; a human-approved repair route creates a fresh Build
   visit and fresh downstream verification. Material requirements/design/scope
   changes require renewed affected acceptance before implementation.
4. Submit a `taskplane.phase-output/v1` packet for the current run and visit. The
   required fields are listed below. Referenced artifacts identify their path,
   kind, schema, phase, visit, task IDs and criterion IDs. Required files must exist.
   Seal normative artifacts and verification inputs; graph/tasks context is copied
   into the profile-bound checkpoint. Refreshing token counters does not stale approval.
5. Present the result and dashboard link, name gaps and resolve the applicable human or policy decision.
   Changes requested or rejection returns to the current scope for correction and
   a new checkpoint. Cancellation grants no continuation. Stale normative artifacts
   or verified source invalidate affected acceptance and descendants while retaining
   history. Finish only after every required visit, including Retro, is accepted.

If the user explicitly asks to start over, preserve the prior findings and create
a fresh scope under `.taskplane/bootstrap/`. Read the active run/revision and use
`flow start --replace-run OLD_ID --expected-revision N --scope FILE
--request-reference REF` in the same workspace. This supersedes the previous run
without accepting it, retains its evidence, and selects a new native dashboard.
Never copy approvals or autonomous policy into the new run. Sealed/stale evidence
does not prevent this control action; exact CLI help/status, read-only diagnostics
and fresh bootstrap proposals remain available. Do not ask the user to disable
hooks or reopen the old checkpoint merely to start the run they requested.

| Phase | Required output |
| --- | --- |
| Product | Scope, stable acceptance criteria, non-goals, dependencies, task outline, finding references |
| Design | Approach, alternatives, interfaces/state, authority boundaries, failure/recovery, graph impact, acceptance test map |
| Plan | Task DAG, ownership, exact Build write scope, criterion coverage, verification strategy, integration order |
| Build | Change inventory, task/criterion map, actual Build checks, known gaps |
| Evaluate | Each criterion's pass/fail/unknown status, existing evidence files and explanation, source/test fingerprints, regression evidence, unknowns/failures |
| Engineering | Findings and severity, actual lens coverage, source locations, requirements comparison, remaining risk |
| Retro | Accepted outcome, actual verification, deferred items with owners, lessons |

Keep outputs proportionate, but preserve these distinctions. The validator checks
structure and bindings; it does not prove the truth of an agent's claims. Human
review and appropriate checks remain necessary. Tokens are advisory: missing usage
is unknown, never zero, and cannot approve or reject work. Missing required evidence
blocks submission; optional usage failures do not erase a valid human decision.

## Findings-led routes and shared views

Engineering may be the starting point. Preserve finding IDs, severity, source
revision/locations and evidence when proposing Product scope. Human acceptance of
an explicit delivery extension adds Product → Design → Plan → Build → Evaluate →
Engineering → Retro to the same active run. Never fabricate earlier completed phases.
Standalone acceptance ends only that requested scope. A finished predecessor remains
historical evidence for a separately authorized new run.

`flow attach` accepts shared task/review files and evidence paths; `flow progress`
records same-phase observations. Neither can approve a checkpoint. Start, submission,
decision, advancement, progress and attachment refresh the same dashboard. Link or
open it using the host's allowed surface; do not work around a denied browser action.
The HTML is a read-only projection, including superseded/stale decisions and legacy
unverified observations. An old finish record must never look like human acceptance.

Review indexes retain the actual lens, native agent identity, phase and evidence.
Use only reviewers who actually performed the work; no synthetic agent identities.
Workers reuse the root run, scope, graph and tasks. Delegate only when authorized.
Claude and Codex share this policy; resolve the installed runtime from the actual
plugin root, not a stale workspace launcher. Native session lineage supplies usage
where readable; missing counters never remove graph, decomposition or dashboard.

Native discovery and pending-call parsing remain observations. The protected adapter
admits no owner from environment, workspace data or CLI flags. Native-workflow
prompts need a complete observed-decision envelope; plain hook text does not establish
what checkpoint was presented. The root may record the actual conversation response
through the explicit decision command. Keep only the minimal response excerpt and
source references, never a whole transcript.

Known running command handles block sealing and old/terminal handles reject observed
stdin. Unknown coverage is reported, not claimed quiescent. Protected-host submission
still requires the complete native process proof. Graph, task and dashboard evidence
are mandatory in both profiles. Use [CLI contracts](../../../docs/cli-reference.md)
for exact start/decision inputs and profile-specific limits.


## Explicit autonomous authorization

Manual approval remains the default. When the user explicitly requests automatic
phase approvals with additional instructions, record `taskplane.approval-policy/v1`
through `flow policy` with the actual message reference/excerpt, current run/scope,
allowed phases, mandatory stop phases and conditions. Repeat the interpreted policy
in the dashboard. Do not ask another enablement question when that instruction is
clear. Generic implementation requests, tool events and previous runs never opt in.

After producing and sealing each phase, inspect the active policy. If it permits
this phase, prepare `taskplane.policy-assessment/v1` against the exact pending
checkpoint and policy digest, with every condition assessed and supported by sealed
files. The original instructions always remain an observed condition. Pass the
bounded assessment directly with `flow auto-decide --assessment-json JSON`; do not
write a new assessment file after sealing. Present the native checkpoint first,
then use `advance`/`finish` only on success. Unknown/failed conditions,
missing evidence, source drift, new scope/route or known live work pause continuation.
Do not spin on an unchanged refusal. Stop phases require the human checkpoint.

The Controller checks phase evidence, ordering, scope, eligibility and required
passing results. Free-text instructions need an honest evidence-backed assessment;
they are not mechanically proven natural-language predicates. Optional telemetry
is advisory unless the user makes it a required condition. Policy decisions must
say automatic=true and human=false, retain policy version/digest and assessments,
and never reuse a fabricated human approval excerpt.

On “return to manual approval” or explicit revocation, record a fresh manual policy.
Human rejection/changes/cancellation or evidence drift suspends automatic approval;
fresh authorization is needed to resume. Existing decisions keep their historical
policy provenance. New or repaired visits and material scope changes still require
human authorization. Native permissions, hook trust and protected_host requirements
remain independent.

Workers only prepare evidence. The root resolves acceptance using this shared policy;
human-checkpoint instructions in role/lens guidance describe the default manual
mode and do not override an explicitly authorized policy supported by the Controller.
No worker independently enables autonomy, broadens scope or creates an extra route.

## Dashboard delivery and measurement

Use the installed native dashboard command with the exact run and execution checkout.
Do not create a replacement dashboard, use an unrelated latest run, or treat a
second hidden/old tab as presentation. Link the generated artifact at Product and
every checkpoint; request opening/focus via the host and verify visible identity
where allowed. Track generated, opening requested and visibly verified separately.
A denied browser action has an artifact-link fallback, never a bypass server.

Every meaningful milestone regenerates the same selected snapshot. Static mode
labels unmonitored freshness: refresh the existing tab after regeneration and compare
its timestamp. Keep historical snapshots independently accessible. All task, approval,
evidence, graph and token sections use the same captured run identity.

Show current phase/visit tokens separately from run totals. Preserve work/review/
follow-up intervals, repeated visits and coverage; missing/reset/late counters remain
unknown or unallocated. Never estimate a Product count from a legacy run total.
Use the usage ledger to explain unresolved amounts by session, interval and reason.
An unchanged visit across a missing revision can establish phase ownership, while
its work/review split remains unsegmented. Keep post-run follow-up outside phases
and show pre-run lifetime usage separately from the run's measured expenditure.
Reconciled known totals do not imply complete native discovery or precise activity
labels when observations are missing.
Graph views disclose scanned checkout/input fingerprints, dirty-file freshness,
coverage, scoped/full context and edge provenance. Planned task paths are not proof
of implemented source relationships; keep execution prerequisites separate.


For an inventory-limit refusal, use the read-only `flow diagnose` operation before
choosing a recovery checkout. It reports bounded metadata and partial coverage;
source audit limits stay unchanged. Native HEAD worktree recovery retains original
files and validates the observed destination before allowing bounded setup there.
That checkout still needs its own initialized workflow and exact scope. Exact
Taskplane native administration remains outside phase prerequisites, subject to the
user's explicit instruction and host permissions. Never turn this into a generic
shell/cache-write exception or an invented approval.

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
