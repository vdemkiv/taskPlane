# Taskplane CLI

Invoke `python3 <plugin>/taskplane/tp.py` with one of these commands.

| Command | Purpose |
| --- | --- |
| `flow start --workspace PATH --scope FILE --request-reference REF [--standalone --phase PHASE] --goal TEXT` | Start or reuse an explicitly scoped native workflow |
| `flow progress --workspace PATH --phase PHASE --note TEXT` | Record same-phase observations; phase changes require guarded advance |
| `flow attach --workspace PATH --tasks FILE --reviews FILE --evidence FILE` | Attach shared tasks and evidence |
| `flow report --workspace PATH [--run ID]` | Read profile-bound decisions, separate observations and native token coverage |
| `flow submit --workspace PATH --output FILE --tasks FILE --expected-revision N` | Validate and seal current phase evidence for human review |
| `flow decide --workspace PATH --decision-json JSON --expected-revision N` | Record a complete observed human decision against the exact pending checkpoint |
| `flow advance --workspace PATH --phase PHASE --expected-revision N` | Advance only to the next authorized visit after its predecessor is accepted |
| `flow finish --workspace PATH --expected-revision N --note TEXT` | Finish only after all required visits have current human acceptance |
| `flow hook` | Enforce verified workflow boundaries where active; separately observe native metadata and usage |
| `context`, `screen`, `tool-observe`, `subagent-start`, `subagent-stop`, `session-verify`, `human-input` | Installed native hook entry points; read event JSON on stdin and bind the event type to the command |
| `graph --workspace PATH scan --decompose` | Scan source dependencies and components |
| `graph --workspace PATH impact --files a.py,b.py --json` | Inspect affected consumers |
| `graph --workspace PATH edge SRC DST --kind runtime` | Record an observed relationship |
| `graph --workspace PATH html --out graph.html` | Render the interactive graph |
| `dashboard --workspace PATH [--out dashboard.html]` | Render the shared dashboard |
| `review start --workspace PATH --scope repository` | Capture tracked source for review |
| `review start --workspace PATH --base REF` | Capture a comparison for review |
| `lens --workspace PATH --files a.py,b.py --stage engineering` | Suggest applicable lenses |
| `version --verify` | Check host manifest versions |

Product, Design, and Engineering can each start execution, including standalone
requests. Every execution uses the shared dependency graph with source
decomposition, an attached task decomposition, and the shared dashboard. Engineering
findings can become Product inputs without starting another active run. Help and
status only inspect existing state. A phase's scope determines what work is authorized.

Full delivery requires human acceptance after Product, Design, Plan, Build,
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

After submission, `flow report` exposes `workflow.pending_checkpoint`. Present the
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
