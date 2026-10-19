# CLI reference — compact candidate

Use the actual loaded entrypoint, explicit selected workspace and relevant
`--help`. Source, an extracted archive and an installed plugin are distinct.
These commands describe the 2.34.2 candidate; an old-contract active run requires
its original compatible runtime for mutation.

```sh
python3 /actual/plugin/taskplane/tp.py version --verify
python3 /actual/plugin/taskplane/tp.py --help
python3 /actual/plugin/taskplane/tp.py flow --help
```

## Shared delivery controls

| Operation | Purpose |
| --- | --- |
| `flow start` | Create the authorized full or standalone route from goal/scope/current output |
| `flow report` | Inspect workflow state and separate live telemetry without journal access |
| `flow report --history --run RUN --full` | Explicit analysis of retained legacy journal history |
| `flow report --record --run RUN --note TEXT --full` | Optionally save one factual analysis record after a run ends |
| `flow context` | Read durable current/task brief and relevant references |
| `flow attach` | Publish current task definitions under existing authority |
| `flow submit` | Validate and atomically seal concrete current output |
| `flow prevalidate` | Optional read-only call to the same result validator |
| `flow decide` | Record actual contextual human decision with current binding/provenance |
| `flow advance` | Move after this phase's valid approval |
| `flow finish` | Finish only after all required visits including Retro are accepted |
| `flow worker` | Actual scoped native preparation/claim/status/result/recovery operations |
| `flow inspect`, `flow diagnose` | Focused evidence/state diagnosis |
| `flow recover`, `flow resume` | Supported provenance-preserving recovery/continuation |
| `flow repair` | Re-enter Build for approved existing tasks after acceptance/completion, preserving unaffected history |
| `flow revise` | Explicit authorized material requirements revision |
| `flow retire`, `flow deactivate` | End obsolete authority without accepting pending work |
| `flow present`, `flow wait` | Optional presentation/wait observations, not approval authority |

Full route: Product → Design → Plan → Build → Evaluate → Engineering → Retro.
Each concrete result retains separate actual human approval. Automatic approval
enrollment and grouping are deferred. Historical policy records may be readable;
they do not enroll a new run or transfer old decisions.

Use `--workspace PATH`, `--run RUN` and actual current revision where the selected
operation requires it. Start uses `--goal`, `--scope`, `--request-reference` and,
for supported standalone routes, `--standalone --phase PHASE`. A scope identifies
stable criteria, material boundaries and current output; future paths may be
omitted until Plan fixes concrete Build paths. Do not manufacture all future
filenames before Product.

Submission uses the current `--output` and any concrete task/evidence inputs
required by that result. No prevalidation token, receipt, universal report packet,
graph rescan, verified dashboard display or substantive-word-count ceremony is required.
Current task decomposition is required: supply `--tasks`, define the Plan DAG
inline, or reuse accepted current task context. `flow advance` allocates the next phase's output path
when `--output` is omitted. Exact run/visit metadata paths do not widen the Plan's
production write scope.

Flow start and checkpoint actions publish the dashboard. Show its link/open result
with the concrete phase result; a queued open is not a verified display. Publication
failure calls for a view retry, not replay of the committed workflow action.

Build admission requires a matching automatic pre/post hook observation for the
current session and phase. Supported shell reads remain available; literal owned
file operations and Plan-declared verification commands follow the same task scope
as structured edits. Unknown executable effects have no blanket shell exemption.
Do not disable live enforcement to get past a refusal. Editing owned candidate
hook source and testing separate installations remain normal implementation work.

`flow repair --run RUN --expected-revision REVISION --decision-json JSON` takes
an object containing `decision` (the existing observed human-decision envelope)
and `tasks` (affected approved Build task IDs). The controller checks the actual
human provenance, task closure and quiescence, then opens fresh governed results.
It does not create a new framework or restart Product/Design/Plan.
Retry only missing, failed or invalidated tasks and their affected checks; retain
all unaffected results, evidence and approvals, without restarting earlier phases.

## Brief and relevant freshness

`flow context --workspace PATH --run RUN` returns a `taskplane.brief/v1` descriptor
with `binding`, `brief`, `reference` and `unknown_inputs`. Add `--task ID` when
useful. Follow ordinary material references; `--read REF` reads a selected
verified reference when needed. No `--consume`, `--read-required`, `--drain`,
startup-only transport or response-completion accounting applies.

Task generation, criterion epochs, relevant source/test/config inputs, prerequisite
result identities, command/environment and workspace/package identity govern
reuse. Unrelated new files and progress revisions require no acknowledgement.
Unknown dependency coverage prevents a verified reuse claim. Read/search/open
remain available while sealed; host access permissions still apply.

## Human decisions

The host/orchestrator supplies `--decision-json` for the actual response: complete
original text, contextual semantic candidate, actual source/actor/conversation,
reference/time and shown checkpoint target. `--native-event` identifies an observed
event, not approval text. Preserve current binding, chronology and replay checks.
The runtime does not independently authenticate human origin or parse a wording
language. An explanatory result presentation followed by ordinary assent is valid;
explicit other targets or ambiguous conditions stay pending.

Approval applies only to this unchanged concrete result and scope. It neither
accepts future phases nor grants host tool permissions. Reuse a valid decision
instead of asking again. Route eligibility is checked before sealing/presentation
and again at decision; invalid amendments must not consume a human response.

## Native ownership and command boundaries

Root/serial work and useful native workers are valid; explicit native independence
obligations remain binding. `flow worker` retains prepare, claim, status,
accept-result/reject-result, abandon/capacity and supported recovery operations.
Use exact current exposed request schemas. There is no source-probe, startup-status,
startup accounting, first-worker cohort or mandatory lens count.

Bind actual launch/attempt/native child identity. The ordinary one-use Claude
invocation bridge may identify an observed principal; grants/environment labels
cannot. Join actual terminal completion and known command handles before result
acceptance. A message, Stop, wait success or cancellation request is insufficient.
See [native ownership](../skills/tp-go/references/codex-native-dispatch.md).

Structured mutations enforce approved phase/Plan, exact owner/path, no traversal,
symlink escape, live conflict or sealed-result overwrite. Opaque shell effects
rely on host permissions and later auditing; Taskplane is not an OS sandbox or
complete effect classifier. Sending messages/external writes require real action
authorization, regardless of tool names.

## Verification and history

Plan declares meaningful checks. Build/Evaluate produce immutable attempts/logs
with unique identity, producing visit, status and relevant fingerprints. Evaluate
may select fresh evidence without modifying Build history. Later consumers keep
the newest applicable fail/unknown instead of returning to old pass. Acceptance
of unknown never makes it pass. Distinguish pending/accepted/historical evidence
and fixture/package/platform/live-host observations.

The candidate reads old controller/visit/decision/check/import records for history,
diagnosis and archival. Active old-contract mutation refuses explicitly; no silent
migration, receipt/approval transfer or old-snapshot overwrite is supported.
See [state ownership](state-spec.md) and [import](state-import-and-release.md).

## Workflow Builder and optional tools

`workflow catalog`, `validate`, `preview` and run-bound `check` are read-only.
`workflow save` publishes a versioned definition without running it; `compile`
creates pinned inputs for existing flow controls and launches nothing. There is
no separate `workflow run` command. See [Workflow Builder](workflow-builder.md)
for exact definition fields, invocation and compatibility.

Graph scan/decomposition, lens catalogs, dashboards and usage remain optional
inspection aids. Missing measurements are unknown. Their outputs do not authorize
or veto a phase. Old `loop`, `repository`, `share`, wave transports, maintenance
reconciliation and context-upgrade/rollback rituals are not current new-run
procedures. Consult actual help rather than reuse historical commands.

For a Claude root with an automatically selected native transcript,
`flow decide --decision-json <observed-envelope>` verifies an actually observed
current checkpoint response and its original native presentation reference.
The UUID selects an original assistant record; source, chronology and current
binding are checked without parsing its prose. Missing association stays pending.
Codex and ordinary observed v1 decision envelopes retain their existing
structured provenance route.
