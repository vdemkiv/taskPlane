# CLI reference — compact candidate

Use the actual loaded entrypoint, explicit selected workspace and relevant
`--help`. Source, an extracted archive and an installed plugin are distinct.
These commands describe the 2.34.6 candidate; an old-contract active run requires
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
| `flow verify` | Execute an accepted Plan check and retain its immutable attempt |
| `flow decide` | Record actual contextual human decision with current binding/provenance |
| `flow advance` | Move after this phase's valid approval |
| `flow finish` | Finish only after all required visits including Retro are accepted |
| `flow worker` | Actual scoped native preparation/claim/status/result/recovery operations |
| `flow inspect`, `flow diagnose` | Focused evidence/state diagnosis |
| `flow recover`, `flow resume` | Supported provenance-preserving recovery/continuation |
| `flow repair` | Re-enter Build for approved existing tasks after acceptance/completion, preserving unaffected history |
| `flow revise` | Explicit requirements revision or Plan-only exact-path amendment |
| `flow retire`, `flow deactivate` | End obsolete authority without accepting pending work |
| `flow present`, `flow wait` | Optional presentation/wait observations, not approval authority |

Full route: Product → Design → Plan → Build → Evaluate → Engineering → Retro.
Manual mode requires a separate actual human approval for each concrete phase
result. Explicit optional automatic policies remain supported through `flow policy`
and `flow auto-decide`, with their scope, conditions, assessments and stop phases.
Approving a checkpoint does not enroll a policy or accept a future phase.

### Compatible runtime recovery

An installed runtime may have a defect that prevents normal same-run recovery.
Do not patch its cache in place or send a refused helper to Terminal. The host
must first select a separate, tested compatible installation. Then the new
runtime's `flow recover --runtime-repair` can resume the existing open Build
without repeating accepted phases or restarting the conversation.

The operation installs nothing and edits neither bundle. It changes only the
run's enforcing runtime identity and revision, records the recovery request in
the existing controller, and clears callback readiness from the old runtime.
Existing phase decisions, task ownership and evidence remain preserved. Changed
package identity invalidates old verification reuse; child readiness must be
observed again. This run handover requires explicit human authorization separate
from Build approval and from the host's installation action.

The bounded request is a hash-pinned JSON file under `.taskplane/bootstrap/`,
with schema `taskplane.runtime-repair/v1`. Its `binding` contains the exact
`workspace`, `root`, `run`, `revision` and `visit`. Include the actual human
`request_reference`, the complete `from_runtime` and `to_runtime` identities,
and `member_sha256`, the incoming installation's code/hook/manifest fingerprint.
The fingerprint is the canonical content fingerprint of the member-path-to-SHA-256
map. `flow diagnose` prepares this proposal from the current controller and
selected installation; supply the actual user request reference before saving it.
The old installation need not remain on disk after the host update. Its persisted
identity is historical provenance. This is cooperative observation, not host
attestation or independent authentication of human consent.

```sh
python3 /new/installed/plugin/taskplane/tp.py flow recover --workspace /actual/workspace \
  --runtime-repair .taskplane/bootstrap/recovery.json --expected-sha256 REQUEST_SHA256 \
  --recovery-mode inspect
```

After reviewing the concrete handover and receiving its authorization,
`--recovery-mode apply` uses the same request. It requires a quiescent, unfenced
Build with an accepted Plan. Both installations must have matching workflow,
context and ownership contracts and compatible patch versions in the same
major/minor line. Incoming member bytes and the current binding are rechecked. The
target must be the runtime already selected by the host; a candidate checkout
cannot nominate itself. A retry after a committed transition is idempotent.

If the old installation lacks this operation, a supported host update is still
required to introduce it. Candidate source cannot grant itself live admission.
The handover does not approve a phase, install a release or weaken file guards.

Use `--workspace PATH`, `--run RUN` and actual current revision where the selected
operation requires it. Start uses `--goal`, `--scope`, `--request-reference` and,
for supported standalone routes, `--standalone --phase PHASE`. A scope identifies
stable criteria, material boundaries and current output; future paths may be
omitted until Plan fixes concrete Build paths. Do not manufacture all future
filenames before Product.

New repository starts require a clean dedicated linked worktree in the actual
native session. Optional scope fields `repository`, `base_ref` and `base_revision`
pin the intended destination; without a base field, the known origin default is
used. Start retries resume matching initialized work before fresh-start checks.
Automatic pre/post pairs report callback coverage for this root, run, visit,
workspace, initialization and runtime. Missing pairs do not veto root submission,
acceptance or entry into an approved Build. Native worker preparation still
requires its observed parent readiness; missing coverage never becomes a claim
of host containment.

If delivery delayed the first Product submission after the human approved its
content, an observed conversation decision may preserve that approval with
`presentation.content_binding`: SHA-256 content fingerprints of `output`, `scope`
and canonical task definitions in `tasks`. All three must match the submitted
packet. The original presentation and response times/provenance remain unchanged.
This recovery applies only to the first Product with no prior submission history;
later phases, changed content, foreign sources and invalid chronology refuse.

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

Build admission requires accepted Product, Design and Plan results. Supported
shell reads remain available; literal owned
file operations and Plan-declared verification commands follow the same task scope
as structured edits. Unknown executable effects have no blanket shell exemption.
Do not disable live enforcement to get past a refusal. Editing owned candidate
hook source and testing separate installations remain normal implementation work.

`flow verify --run RUN --expected-revision REV --check-id ID --history-path PATH
--log-path PATH` executes that exact accepted Plan check for a root-owned task
and records its actual immutable attempt through the existing evidence API.
The command cannot supply replacement code or write outside current ownership;
prerequisites, source freshness and process conflicts still apply. The declared
current log is an atomic projection; retries retain every immutable attempt/log
body in history. Sealed logs cannot be reopened. `--check-timeout SECONDS` bounds
execution; optional `--coverage-json JSON` preserves actual observed browser or
deployed coverage details. A pass here does not accept a task or phase.

`flow repair --run RUN --expected-revision REVISION --decision-json JSON` takes
an object containing `decision` (the existing observed human-decision envelope)
and `tasks` (affected approved Build task IDs). The controller checks the actual
human provenance, task closure and quiescence, then opens fresh governed results.
It does not create a new framework or restart Product/Design/Plan.
Retry only missing, failed or invalidated tasks and their affected checks; retain
all unaffected results, evidence and approvals, without restarting earlier phases.

When an unopened requirements revision becomes stale while waiting, keep its
fence, join known work, then preview and begin a new request ID with current
revision/generation and artifact bindings. This supersedes the old intent in the
same run and preserves its history. Missing optional graph coverage conservatively
affects every task instead of preventing correction.

For omitted Build paths within unchanged accepted requirements and Design, the
same revision request supports `return_phase: "plan"` with `changes: {}`. Its
`scope_amendment` may change only exact Build paths and fresh Plan output paths;
all other scope fields and Product/Design paths must stay identical. The preview
lists only Plan as requiring renewal and conservatively affects every Build
task. Begin waits for known worker quiescence, preserves Product/Design decisions
and requirements epochs, and opens fresh Plan and later visits in the same run.
The amended output must bind `requirements_revision` to the request ID. New Build
writes remain denied until that Plan is submitted, separately approved and
advanced. Use fresh evidence paths for the new Build visit; previous attempts
remain immutable historical evidence. A material requirements change still uses
the Product route and its three renewed decisions.

Continuation may carry an `intent` object with `candidate: "resume"`, the exact
`run`, and current `phase`, alongside the complete actual human excerpt and
source provenance. The excerpt must identify that run; the contextual candidate
does not replace identity, chronology, source freshness or process checks.

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
After requesting changes or rejecting a checkpoint, the human may approve that
same unchanged result without resubmission. Bind the later reply to the current
revision and original presentation; retain the earlier decision. Changed evidence,
an older reply or cancellation cannot use this continuation.

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

The flow refreshes source scan/decomposition at initialization and relevant
milestones, reusing unchanged inputs. The exact installed command
`python3 /actual/plugin/taskplane/tp.py graph --workspace /absolute/project scan --decompose --strict`
is also available during setup. Its managed outputs do not grant source writes.
Dashboards separate current task observations from phase decisions and expose
missing artifacts and failed/stale scans. These views, lens catalogs and usage
do not authorize or veto a phase; required check evidence remains binding.
Old `loop`, `repository`, `share`, wave transports, maintenance
reconciliation and context-upgrade/rollback rituals are not current new-run
procedures. Consult actual help rather than reuse historical commands.

For a Claude root with an automatically selected native transcript,
`flow decide --decision-json <observed-envelope>` verifies an actually observed
current checkpoint response and its original native presentation reference.
The UUID selects an original assistant record; source, chronology and current
binding are checked without parsing its prose. Missing association stays pending.
Codex and ordinary observed v1 decision envelopes retain their existing
structured provenance route.
