# Shared Taskplane flow

## Scope and phase decisions

Use the selected project, actual loaded runtime and existing matching run. Full
delivery is Product → Design → Plan → Build → Evaluate → Engineering → Retro.
Each concrete result requires a separate human approval before advancing.
Grouping and automatic approval enrollment are not part of this contract.
Standalone Product, Design and Engineering retain their requested entry/ending.
A review/design request never grants implementation authority.

Preserve prior valid decisions and the original request. A user-authorized manual
continuation after retirement stays manual; do not reactivate the retired harness
or invent a replacement run/approval transfer. Host tool permissions remain
separate from phase acceptance.

## Workspace and execution contract

Resolve the selected project and actual execution path before state creation.
Cowork needs explicit project binding and independent storage/command/worker
locality observations. A connected folder alone does not prove local execution.
Respect user location restrictions and report a concrete setup blocker if needed.
Use supported binding/initialization controls; do not synthesize missing history.

New runs use `taskplane.workflow/v2`, `brief/v1` and `ownership/v1`. Keep the loaded
runtime fixed for an active run. Candidate source, packages and installed caches
are separate copies. Old-contract runs remain historical/readable and require
their original compatible runtime for mutation. Never silently migrate an active
run or transfer its approvals to a new one.

## Progressive scope and Plan

Start with the goal, stable criteria, material boundaries and current phase output.
Do not require all future filenames, complete implementation scope, test commands
or duplicated evidence packets before Product. Design supplies the approach and
validation map. Plan then fixes the executable Build task DAG, exact paths, one
owner per path, relevant inputs, prerequisites and meaningful check coverage.

Root, serial and useful independent native work are valid. This flow authorizes
bounded delegation where it adds useful independence or concurrency, subject to
user constraints. There is no worker/lens quota or root-exception packet.
Explicit requested independence remains binding. Phase prerequisites cannot point
to later phases. True read/write conflicts serialize work; shared status revisions
or unrelated files do not.

The Plan cannot widen the human-authorized boundary. Obtain actual authorization
for material new scope; do useful in-scope work first. Build starts after Plan
approval. Remove obsolete code's tests, helpers, fixtures and CI references with
it; preserve useful mixed-file behavior instead of skipping failures.

## Durable brief

`flow context` returns the current goal/phase, accepted decisions, useful tasks,
relevant findings and source/evidence references. Read the material needed for the
assigned conclusion, following references as needed. Missing inputs remain visible.
A fresh consumer resumes from the durable brief rather than predecessor chat
replay. Optional paging bounds transport only. No consume/read receipt, byte
ledger, startup probe, timing/accounting or response-completion token is required.

Reuse depends on relevant source/test/config inputs, task generation, criterion
epochs, prerequisite accepted results, command/environment and workspace/package
identity. Unrelated documents or progress revisions require no acknowledgement
and do not relaunch unchanged workers. Missing dependency coverage means reuse is
unknown; it is not permission to declare a pass.

## Produce, submit and present

| Phase | Useful result |
| --- | --- |
| Product | Outcome, material scope and observable acceptance criteria |
| Design | Approach, interfaces, trade-offs, dependencies and validation map |
| Plan | Executable task ownership, exact Build paths and meaningful checks |
| Build | Actual changes, verification results and remaining gaps |
| Evaluate | Criterion-by-criterion pass/fail/unknown with applicable evidence |
| Engineering | Findings, actual review coverage and limitations |
| Retro | Accepted outcome, remaining owners and lessons |

Submit the concrete current result and necessary identity/criteria/evidence/task
metadata once. The runtime validates and seals atomically. Optional `prevalidate`
uses the same validator; no prevalidation token or repeated ceremony is required.
No universal graph, separate report/task packet or word-count heuristic applies.
Plan may carry its DAG inline; later phases reuse the accepted task definitions.
`flow advance` can allocate the next output path when `--output` is omitted.
Run/visit metadata paths are distinct from the Plan's exact production write scope.

Show the concrete result in conversation with useful links and scope. The optional
`.taskplane/dashboard.html` is a generated view, not approval authority. Opening
or verifying its display is not required. Describe a queued open honestly.
Graph, telemetry and measurement failures cannot veto a valid result.

## Human intent and provenance

Interpret the actual human reply in context. Preserve the complete response and
its observed source, actor/conversation, time/reference and checkpoint target.
Supply the semantic candidate approved/changes_requested/rejected/cancelled/
ambiguous. The runtime checks current unchanged checkpoint, owning conversation,
chronology and conflicting replay; it does not authenticate human origin beyond
reported cooperative host observations or prove semantics with a phrase parser.

Ordinary assent after explanatory prose can approve the shown result. There is
no required question title, exact phrase, presentation grammar or synonym matrix.
An explicit different target overrides adjacency. Unclear mixed/conditional intent
stays pending for normal clarification. Approval accepts only the current result;
it neither enrolls policy nor accepts future phases. Reuse a valid existing
approval rather than ask again. An invalid route proposal must fail before sealing
or consuming the human reply.

## Reads, writes and native work

Ordinary reads/search/open remain available while sealed or stale, subject to
host permissions. Structured mutations enforce approved phase/Plan, exact current
owner/path, no traversal/symlink escape, no live-writer conflict and no writes over
another run's sealed result. Tool names do not authorize messages or external
mutations. Opaque shell effects rely on host permissions and later auditing; this
is not complete pre-execution containment or an OS sandbox.

[Native ownership](codex-native-dispatch.md) retains actual launch identity,
attempt correlation and terminal/cancellation evidence. A message, wait success,
Stop label or cancellation request is not completion. Root accepts artifacts only
after real completion, relevant freshness and terminal known command handles.
Workers do not decide phases or widen their own write authority.

## Verification, interruption and completion

Record immutable attempts/logs with unique check/attempt/visit identity, observed
status, input/test/command/environment fingerprints and ordered selections.
Evaluate can produce a fresh attempt without rewriting accepted Build history.
All later consumers use the newest applicable validated result; later fail/unknown
must not expose an older pass as current success. Human acceptance of unknown does
not turn it into pass. Fixtures and live installed-host results stay distinct.

Resume using the actual run and durable brief. Preserve failed attempts and known
in-flight commands; retry only after a relevant changed condition. Retirement
ends authority without approving pending work. Finish only after every required
phase including Retro is accepted. Installation/publication remains a separate
action. Report unavailable host/platform coverage plainly and give remaining work
an owner without inventing identities or changing host permissions.
