# Taskplane CLI

Invoke `python3 <plugin>/taskplane/tp.py` with one of these commands.

| Command | Purpose |
| --- | --- |
| `flow activate --workspace PATH --phase ENTRY --request-reference REF` | Select the session harness without starting or approving a run |
| `flow present --workspace PATH --run ID --evidence .taskplane/dashboard.html --presentation OUTCOME --note TEXT` | Record the actual native dashboard handoff for this visit/revision |
| `flow wait --workspace PATH --note TEXT` | Record actual missing user input while retaining write and approval checks |
| `flow start --workspace PATH --scope FILE --request-reference REF [--standalone --phase PHASE] --goal TEXT` | Start or reuse an explicitly scoped native workflow |
| `flow progress --workspace PATH --phase PHASE --note TEXT` | Record same-phase observations; phase changes require guarded advance |
| `flow attach --workspace PATH --tasks FILE --reviews FILE --evidence FILE` | Attach shared tasks and evidence |
| `flow report --workspace PATH [--run ID]` | Read profile-bound decisions, separate observations and native token coverage |
| `flow submit --workspace PATH --output FILE --tasks FILE --expected-revision N` | Validate and seal current phase evidence for human review |
| `flow decide --workspace PATH --decision-json JSON --expected-revision N` | Record a complete observed human decision against the exact pending checkpoint |
| `flow advance --workspace PATH --phase PHASE --expected-revision N` | Advance only to the next authorized visit after its predecessor is accepted |
| `flow finish --workspace PATH --expected-revision N --note TEXT` | Finish only after all required visits have current human or authorized policy acceptance |
| `flow hook` | Enforce verified workflow boundaries where active; separately observe native metadata and usage |
| `context`, `screen`, `tool-observe`, `subagent-start`, `subagent-stop`, `session-verify`, `human-input` | Installed native hook entry points; read event JSON on stdin and bind the event type to the command |
| `graph --workspace PATH scan --decompose` | Scan source dependencies and components |
| `graph --workspace PATH impact --files a.py,b.py --json` | Inspect affected consumers |
| `graph --workspace PATH edge SRC DST --kind runtime` | Record an observed relationship |
| `graph --workspace PATH html --out graph.html` | Render the interactive graph |
| `dashboard --workspace PATH [--run ID] [--out dashboard.html]` | Render the shared dashboard |
| `review start --workspace PATH --scope repository` | Capture tracked source for review |
| `review start --workspace PATH --base REF` | Capture a comparison for review |
| `lens --workspace PATH --files a.py,b.py --stage engineering` | Suggest applicable lenses |
| `version --verify` | Check host manifest versions |

Product, Design, and Engineering can each start execution, including standalone
requests. Every execution uses the shared dependency graph with source
decomposition, an attached task decomposition, and the shared dashboard. Engineering
findings can become Product inputs without starting another active run. Help and
status only inspect existing state. A phase's scope determines what work is authorized.

By default, full delivery requires human acceptance after Product, Design, Plan, Build,
Evaluate, Engineering and Retro. Standalone Product, Design or Engineering accepts
only its own scope. Explicit human-approved delivery or repair amendments add fresh
visits while retaining history. Finding IDs survive Engineering-to-Product handoff.
`progress`, `attach`, task status and dashboard HTML are not approval mechanisms.

The shipped default `--profile native_workflow` enforces the Taskplane API using
local state and observed human responses. `workflow_available` can be true while
`authority_verified` and all four host-protection capabilities remain false. Native
host permissions govern execution; this is not a sandbox or a protected issuer.

An explicit `--profile protected_host` request still requires the admitted native
owner. It uses `--native-event REF` for independent scope/decision resolution and
refuses when unavailable. No automatic downgrade or workspace flag enables its
protected capabilities. Local-account tampering and fabricated otherwise valid
provenance are outside native_workflow's guarantees.

Start scope JSON has `criteria` (unique IDs), `paths` (all seven phase names mapped
to exact workspace-relative file lists), and optional `verification_inputs`. Use
`.taskplane/` for generated workflow artifacts. Declare source/task files written
during a phase in that phase's scope. Scope and decision envelopes are data, never
executable configuration. Start requires the actual user's request reference and
does not accept any future phase.

In manual mode, after submission `flow report` exposes `workflow.pending_checkpoint`. Present the
output to the user, wait for their actual response and supply this envelope through
`--decision-json` (properly shell-quoted, or passed as one subprocess argument):

```json
{
  "schema": "taskplane.observed-decision/v1",
  "event_id": "actual-response-id",
  "choice": "approved",
  "binding": "replace with the complete pending_checkpoint object",
  "excerpt": "Approved",
  "recorder": "root_orchestrator",
  "source": {
    "kind": "conversation",
    "reference": "actual-response-reference",
    "conversation": "the workflow root conversation ID",
    "actor": "user",
    "automatic": false,
    "observed_at": "2026-09-16T22:02:00+00:00"
  },
  "presentation": {
    "checkpoint": "the pending checkpoint ID",
    "reference": "actual presentation message reference",
    "at": "2026-09-16T22:01:00+00:00"
  }
}
```

Example identifiers and times above are placeholders; never copy them as actual
provenance. Choices are `approved`, `changes_requested`, `rejected` or `cancelled`.
The excerpt must express that choice explicitly (for example `Changes requested:`
followed by a reason). A brief approval needs presentation identity and earlier
presentation time. If ordering is unavailable, use `checkpoint_explicit: true` and
an actual response such as `Approve: <checkpoint ID>`; the response must itself
name the checkpoint. Automatic, assistant/tool, timeout/cleanup, ambiguous, stale
and mismatched records refuse. The account supplying this evidence is not
independently authenticated by the plugin.

A named native prompt may carry the same envelope as `taskplane_decision`, with
source kind `native_prompt` and recorder `native_prompt_hook`. Ordinary prompt text
alone does not establish the reviewed checkpoint; the root can record the actual
conversation response through the explicit command. Recording a decision never
advances the phase: use `advance` separately after approval.

Required phase outputs and route rules are in
[the shared flow](../skills/tp-go/references/shared-flow.md). `--output` names JSON
with schema `taskplane.phase-output/v1`, the exact `run`, `phase`, `visit`, ordered
`criteria`, and that phase's required fields. `artifacts` references name `path`,
`kind`, `schema`, `phase`, `visit`, `tasks` and `criteria`. Paths are literal,
workspace-relative, without symlinks or protected metadata. Task JSON has a `tasks`
array with unique `id`, `phase`, `dependencies`, `paths`, `owner`, `criteria` and
`verification`. The Plan's `write_scope` exactly matches its Build task paths and
cannot widen the human-authorized outer scope.

Design's `acceptance_test_map` and Engineering's `requirements_comparison` map
every criterion ID to substantive content. Plan embeds the actual shared `task_dag`,
matching `ownership`, prerequisite-respecting `integration_order` and criterion-to-task
`acceptance_coverage`. Build lists exact paths in `change_inventory`, maps criteria in
`task_acceptance_map` and provides `build_checks` with `name`, `status` and an existing
`evidence` file. Engineering lists findings with `id`, `severity`, `source` and an
existing `evidence` file; `lens_coverage` lists each `lens`, `reviewer` and `rationale`.
Evaluate's `criterion_results` maps each criterion to `status`, existing `evidence`
file(s) and `explanation`. Check statuses are `pass`, `fail` or `unknown`.

A decision binds workspace, root, run, visit, checkpoint, revision, packet digest
and scope digest. Identical repeats are idempotent; conflicting/stale reuse refuses.
Evidence drift invalidates affected acceptance and descendants. A pending checkpoint
blocks ordinary covered writes while read/status and exact installed workflow-control
commands remain usable; those commands still perform the same Controller checks.

Native workflow audits regular source files and symlink identity before transitions,
including creations and deletions. The bounded audit excludes `.git`, `.taskplane`,
`.venv`, `venv`, `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache` and
`.ruff_cache` directories. It refuses beyond 20,000 entries or 512 MiB. These are
explicit coverage limits, not a host-wide write barrier. Structured tools reaching
hooks enforce declared paths; opaque commands retain native permissions and have
only their visible source effects audited afterward.

PostToolUse `exec_command`/`Bash`/`write_stdin` responses with structured `session_id`
and `exit_code` fields update observed handles. Known running work blocks sealing.
Terminal or stale handles reject observed input; unsupported response shapes and a
complete process census remain unknown. Protected-host command tracking separately
requires actual identity, cancellation/revocation and complete quiescence proof.
Cancellation requests never manufacture termination.

Local profile state and its initialization marker live under `.taskplane`; locks
and atomic writes handle normal concurrent updates. Missing/corrupt initialized
state refuses automatic reset. Local state, source audit and prompt provenance
remain editable by the local account. Protected-host state remains outside the
worker boundary and is never silently adopted by the local profile.

Named hook commands override conflicting event names in supplied JSON. PreToolUse
errors deny; Stop errors/waiting return a message without forced continuation.
Direct `flow hook` remains a compatibility entry for host-supplied event types.

Workflow refusals return exit 2 with `blocked` or `capability_blocked` and a reason.
Mandatory PreToolUse refusal uses the host's deny response; malformed hook input
blocks. Stop permits waiting for a human instead of forcing an approval loop.
Optional token/render warnings do not erase a durable valid governance decision.

Observations, graph data and native workflow state live in `.taskplane/`.
Reports do not start a workflow. Legacy progress/finish is shown as
`legacy_unverified`, never accepted or automatically migrated. Missing counters
are unknown, not zero. Tokens remain advisory and are not billing totals.

## Automatic approval policy

Manual is the default. `native_workflow` additionally supports **explicit user-authorized**
automatic approval for one run. Existing runs with no policy stay manual. This does
not enable an unattended scheduler, alter permissions, or provide protected-host authority.

| Command | Contract |
| --- | --- |
| `flow policy --workspace PATH --run ID --policy-json JSON --expected-revision N` | Record, replace or revoke observed user authorization. This never accepts a phase. |
| `flow auto-decide --workspace PATH --run ID --assessment FILE --expected-revision N` | Assess one submitted checkpoint under its current policy; commit a policy decision or pause. |

Read the current report first. A policy envelope has this shape; **replace every
example identifier, timestamp and instruction with actual observed data**:

```json
{
  "schema": "taskplane.approval-policy/v1",
  "event_id": "actual-user-event",
  "mode": "autonomous",
  "binding": {
    "workspace": "/actual/checkout",
    "root": "actual-conversation",
    "run": "actual-run",
    "revision": 0,
    "scope_digest": "fingerprint of the current exact scope"
  },
  "recorder": "root_orchestrator",
  "excerpt": "For this task, auto-approve phases after required checks pass. Stop before Retro.",
  "source": {
    "kind": "conversation",
    "reference": "actual-user-message-reference",
    "conversation": "actual-conversation",
    "actor": "user",
    "automatic": false,
    "observed_at": "2026-09-17T18:00:00+00:00"
  },
  "allowed_phases": ["product", "design", "plan", "build", "evaluate", "engineering"],
  "stop_phases": ["retro"],
  "conditions": []
}
```

The orchestrator normalizes only what the user authorized. `policy_binding(state)`
in `workflow_approval.py` derives the binding from the current report/state; do not
invent digests. For an instruction received before `start`, also supply
`request_reference` equal to that run's original request reference. A later message
must have an observed time within the run. Consent must explicitly mention automatic
approval; generic requests such as “build this” are not accepted as opt-in.

The stored policy adds an immutable ID/version/digest, the authorized scope and a
mandatory `user_instructions` condition containing the original excerpt. Additional
conditions have unique `id`, `kind` and `instruction` fields. Supported kinds are
`observed` (evidence-backed assessment) and `required_check` (also requires `check`,
an exact passing Build check name). A required check absent in the current phase
pauses; authorize that condition only for the phases where it can be satisfied.
No policy field is evaluated as shell code. Conditions the runtime cannot interpret
mechanically stay observed, with that limitation visible.

Submit the phase normally. Read the **new** `pending_checkpoint` and policy digest,
then write an assessment inside the checkout, typically under `.taskplane/`:

```json
{
  "schema": "taskplane.policy-assessment/v1",
  "binding": "replace with the complete current pending_checkpoint object",
  "policy_digest": "the current approval_policy.digest",
  "conditions": [
    {
      "id": "user_instructions",
      "status": "pass",
      "explanation": "Describe the actual checks against all original instructions.",
      "evidence": [".taskplane/phase-output.md"]
    }
  ]
}
```

Include exactly every policy condition. Evidence paths must be in the submitted
packet's sealed manifest; attachment alone is insufficient. Each condition needs
an explanation and at least one existing sealed evidence file. `fail` or `unknown`
pauses. Built-in rules additionally require complete/current phase evidence, the
exact revision and scope, permitted phase, no known live process, passing Build
checks without unresolved gaps, passing Evaluate criteria without failures/unknowns,
and no explicit high/critical/P0/P1 Engineering blocker. Automatic acceptance cannot
approve route amendments or widen scope. Plan may narrow the outer Build grant.

After `auto-decide` succeeds, use `advance` or `finish` separately. The stored decision
has `kind: policy`, `human: false`, `automatic: true`, its policy ID/version/digest,
checkpoint binding and assessment. Human decision envelopes retain their existing
semantics; do not manufacture an “approved” excerpt for an automatic decision.

To revoke, submit another `flow policy` envelope with the actual instruction such
as “Return to manual approval,” `mode: manual`, `allowed_phases: []` and the current
binding. Rejection, changes requested, cancellation, route change or evidence drift
suspends automatic continuation. Fresh authorization is needed to resume. Prior
automatic decisions remain auditable under their original policy version.

Identical event/assessment retries are idempotent; changed retries refuse. Revocation
and automatic acceptance share the Controller lock and expected revision, so a stale
authorization cannot win a later race. `protected_host` rejects observed policies.
A native prompt hook may carry a `taskplane_policy` envelope, using the same rules;
plain prompt text and agent/tool events cannot enable automatic approval.

## Exact-run snapshots and usage

`dashboard --workspace PATH --run ID [--out PATH.html]` and phase operations use the
same native renderer/publication helper. Explicit `--run` selects that run; otherwise
the current task binding is used. Missing bindings do not fall back to another task's
latest run in native workflows. Older journal-only views retain their latest advisory
entry, explicitly labelled `legacy_unverified`, and never confer acceptance. Per-generation `snapshot-*.html`/`.json` files retain the captured view;
`dashboard.selection.json` identifies the selected entry. A competing background
publisher keeps its own snapshot rather than replacing a different selected run.

The snapshot includes execution checkout, root/run, phase/visit, workflow/policy
revision, source revision, graph fingerprint, generation time and measurement context.
Publication rechecks workflow/source consistency. Historical views use sealed graph
and task context. The source graph view reuses Taskplane's interactive template and
shows focused/full views and scan provenance. `graph scan --decompose --strict`
records the scan receipt; legacy scans without a workspace-bound receipt stay unverified.

Static HTML labels freshness as unmonitored. Regenerate, then refresh/reopen the
intended file; its reload button does not regenerate backend data. Generated HTML
or a queued host open is not visual verification. Link the exact native artifact at
every checkpoint, including Product, and report blocked presentation honestly.

Usage observations record cumulative per-session boundaries at start and successful
transitions, with progress samples. Comparable monotonic intervals are attributed to
phase visits as work, review or post-completion follow-up. Missing/reset/late counters
remain partial/unallocated, including a child with no comparable start observation.
Existing native readers reconcile run totals and identify actual children; host
approval-review remains separate. Read failures retain recorded counters with older
measurement times. No early phase boundary means no retrospective phase estimate.
Counter/discovery gaps are visible; optional telemetry cannot erase a committed decision.

## Execution entry and readiness

All execution skills activate the harness before substantive work, including
standalone reviews and resumed stages. Supported direct execution prompts,
namespaced skills and native reads of installed execution skill files can select
it through hooks; indirect entry uses `activate`. ENTRY accepts an execution skill
name or phase. It creates no run or approval. Help/status, quoted examples and
unrelated sessions do not activate. A selected session with no run denies covered
implementation and completion while allowing bounded bootstrap preparation.
See [onboarding](onboarding.md#verify-harness-activation) for supported setup tools.

Standalone review uses `start --standalone --phase engineering`; Product/Design
also have standalone starts. Existing phases retain their valid scope and approval
bindings. Activation cannot grant Build or opt into autonomous decisions.

`report` includes observed `harness` readiness (inactive, initialization_required,
active), hook observation, current binding and presentation receipt. `present`
validates the current native dashboard identity and stores its immutable artifact
hash outside normative phase outputs. OUTCOME is `linked`, `verified` or `blocked`:
queued opens are linked/unverified; verified requires actual rendered evidence;
blocked records the restriction and artifact fallback. These are cooperative
observations, not proof the host displayed the artifact or that the user saw it.

Stop requests one corrective continuation for missing setup, unsubmitted phase
output or missing current handoff. A repeated stop emits a message to avoid loops.
`wait` records a real missing-input reason and never approves or completes work.
New user input/further tools clear it; a different visit/revision cannot reuse it.
Unloaded/disabled hooks remain outside these cooperative guarantees. No live hook
trust or native permission is changed.
