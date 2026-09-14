# Native Codex reuse audit

Audit date: 2026-09-14 UTC. Repository baseline: `07694b5`; the later
`b171f24` file-reader repair is being removed. This is a targeted architecture
audit, not a completed governed EM review or release sign-off.

Follow-up: [Claude initialization and budget recovery](../incidents/2026-09-14-claude-launcher-budget-recovery.md)
also identified launcher-first hook resolution as a duplicate of native plugin
selection. Hooks and entry skills now prefer the host-selected plugin; the
project launcher is optional. The findings below retain the original audit's
baseline and installation observations.

**Yes: earlier changes implemented capabilities that Codex already provides.**
The clearest active overlaps are command execution, process lifecycle/waiting,
OS sandbox policy, and opening preview surfaces. There is also a host-tool
compatibility error that prevents review startup. Requirements, review criteria,
dependency graphs and evidence-to-task binding remain TaskPlane responsibilities.

The audit scanned all 114 tracked production Python modules for process, wait
and execution entry points, then traced the cited implementations and callers.
The scan found 85 subprocess/wait/thread/signal call sites across 25 modules;
those counts are an investigation inventory, not a count of defects. Git history
was checked for the introduction of the cited components. Current native tool
definitions and official Codex documentation supplied the comparison.

## Confirmed active overlap

| Area | What TaskPlane implements | Native owner and disposition |
| --- | --- | --- |
| Command execution | `governed_commands.execute(..., "launch", ...)` starts a detached Python worker, which starts the requested process, drains output, enforces a deadline and publishes its own terminal state. It supports a `host="codex"` label but does not call Codex to launch that process. | Use native `exec_command`/`write_stdin`, or the existing host's documented command execution API when integrating outside a model turn. Keep TaskPlane's exact test assignment and evidence binding. Retire the general process runner after callers consume native results. |
| Process lifecycle and waits | `CommandRuntime` adds opaque handles, snapshots, output storage, delivery leases, running/terminal states and a 50 ms polling loop. `CommandAdapter` has a native-wait port, but `governed_commands._adapter` supplies no native wait and uses TaskPlane's PID cancellation. | Codex already owns command sessions and completion. Use that session identity and native wait/output result; retain only workflow evidence and durable references needed by TaskPlane. Do not rename the current polling implementation “native event waiting.” |
| Sandbox enforcement | Review and previews independently construct Seatbelt policy text and invoke `/usr/bin/sandbox-exec`. The preview path also sets resource limits and owns process-group teardown. | Use Codex permission profiles and its sandbox execution path. TaskPlane may declare required source/artifact scope and verify the observed result. Resource caps that native Codex does not expose are a separate requirement, not a reason to claim complete native equivalence. |
| Opening previews | `native_surface_transport` requires `TASKPLANE_SIDE_PANEL_COMMAND`, `TASKPLANE_BROWSER_COMMAND` or `TASKPLANE_HOSTING_COMMAND` and starts another configured process. The production preview entry wires this transport directly. | This desktop host already exposes `open_in_codex` for files/browser panels and native browser control. Use those for local display. Public hosting is a distinct action; opening a native panel does not publish a site. Keep preview content, revision binding and observations. |
| Review file access | `review_file_tool_readiness` requires the literal `Read` tool. The read-only screen rejects every shell command. Codex's actual inventory here contains `exec_command` and `apply_patch`, so setup can be present while review admission remains incompatible. | Bind review access to native Codex permissions and actual tool capabilities. A declared tool name alone does not prove read-only enforcement. Remove the custom reader added in `b171f24`; do not invent a replacement `Read` tool or silently admit unrestricted commands. |

Evidence and active callers:

- Command launch: `taskplane/governed_commands.py:2015` and `_worker` at
  `:2594`; composed by `taskplane/loop.py:2006`. The semantic checkpoint path is
  also used by `taskplane/gates.py:231` and `:241`.
- Native-wait port versus actual composition:
  `taskplane/command_adapters.py:597`,
  `taskplane/governed_commands.py:1689`,
  `taskplane/command_runtime.py:1245`.
- Two sandbox implementations: `taskplane/review.py:2022` and
  `taskplane/command_adapters.py:258`. The production preview composition is
  `taskplane/preview_runtime.py:1125`.
- Preview process transport: `taskplane/command_adapters.py:235`.
- Review compatibility and command denial:
  `taskplane/taskplane_lite.py:6545` and `:2694`.

These components predate this repair. Command state began in `4275337`
(2026-08-17), command adapters in `c3fa570` (2026-08-17), production preview
transport in `7b2e0bd` (2026-08-18), review process isolation in `6354a80`
(2026-08-18), and governed commands in `bba3354` (2026-08-23). These are
introduction references, not a claim that all current lines appeared then.

The three command modules contain 4,697 lines combined. That is the size of the
current affected modules, **not 4,697 safely deletable lines**: authority, scope,
cleanup and evidence responsibilities are mixed with process transport.

Codex documents sandboxed command execution, streaming output and explicit
termination in its [App Server reference](https://learn.chatgpt.com/docs/app-server#command-execution).
Its [permission profiles](https://learn.chatgpt.com/docs/permissions) include
`:read-only` and scoped workspace access. The
[sandbox helper](https://learn.chatgpt.com/docs/developer-commands?surface=cli#codex-sandbox)
runs commands with Codex's own policies. These APIs establish native capability;
they do not establish that this plugin has already integrated with it.

## Partial overlap: retain the domain behavior

**Usage tracking.** `native_session_meter.read_snapshot` reads Codex's cumulative
counters, while `dispatch_telemetry` binds baselines and usage to attempts and
TaskPlane budgets. Native usage is already the source; this is not a replacement
model billing service. Prefer native usage events when available and reduce
transcript-format parsing. Keep per-phase attribution and explicit TaskPlane
limits. The native account usage tool reports account-wide limits, not the
spend of a particular review. See `taskplane/native_session_meter.py:213`,
`taskplane/dispatch_telemetry.py:1560`, and `taskplane/spend.py:443`.

The live review also encountered a separate existing policy: the per-pickup cap
was compared with this long conversation's cumulative native usage. Changing
that scope requires an explicit budget-policy decision; neither resetting the
session nor removing a native-tool duplicate resolves it honestly.

**Worktrees.** `repository.py` acquires exact Git revisions and manages detached
checkouts. Codex provides native task/worktree operations, so user-owned task
creation should use those when requested. Its current task-creation tool is not
a drop-in API for every internal review checkout. Keep revision verification,
source acquisition and task ownership; prefer an existing Codex checkout before
creating another. Evidence: `taskplane/repository.py:518` and `:1423`.

**Approvals.** Codex owns permission prompts and user interaction. TaskPlane's
Design, Plan and EM sign-off decisions are different domain decisions and must
remain recorded against the exact work. A duplicate prompt for an already
authorized operation should be removed; a native tool approval must not be
relabelled as product or EM approval. `review_session.record_consent` records
static/dynamic scope; `authority.approve` binds a workflow decision. Existing
documentation correctly states that `--by` is attribution, not independent
proof of a human message. See `docs/authority-matrix.md:20`.

**Dashboards and context.** TaskPlane's graph/findings content and bounded stage
inputs carry domain information. They should be displayed through native
panels and passed through native agent tools. Their existence does not mean
TaskPlane has replaced the Codex UI or compaction. `host_native.deliver_dashboard`
is a publication/evidence adapter; it is not itself a browser implementation.

## Already native or previously removed

- Current Codex worker instructions use native subagent dispatch, isolated
  startup and native lifecycle hooks. `agent_runtime.py` prepares and validates
  domain packages/results; it does not implement a model provider or scheduler.
  See `skills/tp-go/references/codex-native-dispatch.md:1` and
  `taskplane/agent_runtime.py:1`.
- The proposed `orchestrator.py` and `host_execution.py` are absent from the
  current tracked tree. The stopped draft is preserved in
  `.taskplane/harness-salvage/stopped-orchestrator.zip`. The untracked
  `design/thin-orchestrator/` documents describe historical work and must not
  be treated as proof that it is installed. `3d0d6ad` records keeping model-led
  orchestration; `docs/harness-build.md` records the archival/removal.
- The earlier custom MCP/file transport draft was already abandoned; its
  incident is documented in
  `docs/incidents/2026-09-13-entry-initialization-overengineering.md`.
  No current project `.mcp.json` supplies such a replacement.
- The new custom `inspect` reader from `b171f24` has now been removed from
  source, CLI, packaging and skill instructions. The workspace launcher has
  been restored to the installed plugin family; it no longer selects the
  temporary diagnostic build.

## Native check and remaining work

The installed Codex binary successfully ran a file read using
`codex sandbox -P ':read-only'`. With the same profile, an attempted overwrite
of `/private/tmp/taskplane-native-permissions-check/source.txt` failed with
`Operation not permitted`, and the file retained `unchanged`.
macOS rejected starting a second sandbox inside this task's sandbox; the native
test succeeded when the sandbox launcher was allowed through Codex's existing
approval review. No custom sandbox code was added. This tests the native
boundary, not TaskPlane's review integration.

Migration order: connect review file access to native permissions; replace the
general command launch/wait/cancel transport while preserving evidence; route
review/preview isolation and display through native facilities; then consolidate
usage ingestion and prefer existing Codex worktrees. Validate each real host
path before deleting its old implementation. Do not launch another controller,
server, provider, permission system or polling service to perform this migration.

The governed EM review remains incomplete. The removed file reader and passing
component tests do not resolve native review admission or authorize a new budget.

Removal/readiness validation: 158 tests and 66 subtests passed across entry
initialization, read-only enforcement, screener bypasses, Codex compatibility and
onboarding. Production lint, the TaskPlane skill validator and whitespace checks
passed. Packaging lists and the generated CLI reference are restored to the
pre-reader baseline. No replacement process runtime was introduced in this audit.

## Implementation following approval

The approved migration is implemented in the working branch. The table above
describes the audited baseline, not the new behavior below.

| Area | Implemented behavior | Retained boundary or limitation |
| --- | --- | --- |
| Review reads | Native `exec_command` enters the installed Codex `:read-only` profile, with managed restrictions included. The projected Bash hook is checked against the full pending native call, including its non-login `/bin/sh` and working directory. Scoped `apply_patch` remains the artifact writer. | Missing or ambiguous native records, arbitrary JavaScript wrappers, outer shell effects, wider profiles and caller-authored permission claims are refused. This POSIX mapping does not claim Windows sandbox support. |
| General Codex commands | Launch emits a native tool request. Show/wait/cancel project native session results and return native `write_stdin` requests where needed. TaskPlane launches no general Codex worker and maintains no second output log or process state machine. | The existing intent store holds assignment identity and native references. Unknown history and requested interruption cannot prove success. Codex cannot select the Claude transport as a workaround. |
| Test evidence | The existing committed-copy preparer supplies the disposable checkout. Codex's profile permits writes only there; native environment arguments place test home/temp files there too. Evidence is checked against the exact command, assignment and source revision. The copy is removed after observed completion. | Semantic checkpoint enforcement remains separate. Native desktop tools do not expose a hard execution deadline; a general command requesting one is refused before launch. |
| Review/preview isolation | The Codex branches now use native permission profiles rather than constructing Seatbelt policy text. Live validation through the updated review entry allowed a copy write and denied a source write. | The existing bounded validation action and preview CPU/memory limits remain, because those guarantees are not supplied by the current desktop command tool. The Claude isolation path is retained. |
| Preview display | A static disposable `index.html` opens through native `open_in_codex`. The preview can resume observation without launching another command. | A native queued response remains queued. It is not an opened panel or visual-review evidence. A server URL and public hosting are not inferred. |
| Usage and checkouts | Confirmed that `native_session_meter` remains the single Codex counter reader. Local targets use the supplied checkout; ready runs retain their pinned checkout through preflight. | Phase attribution, exact repository acquisition and internal validation copies remain TaskPlane domain behavior. No new task/worktree service or usage meter was added. |

Live host checks used Codex `0.154.0-alpha.6.2`. They verified a read-only read,
denied source writes, allowed writes in a disposable copy, managed-profile
resolution through the updated validation entry, native interruption and its
nonzero terminal event, and recovery of the full pending native command from
the actual session record. The native panel tool returned `queued`; no visual
completion is claimed. These checks are integration evidence, not a governed
EM sign-off.

Final integration checks also exercised the public command entry point. Its
old default injected a hard deadline into every launch, which made native
Codex reject even requests without a deadline. The Codex entry now leaves that
optional field unset unless requested; the Claude transport retains its
configured default. A live disposable fixture followed the public launch,
native execution and public show path, observing both a native failure for an
unavailable executable and native success for the installed executable. A
completed native session can no longer authorize further polling/interruption.

Regression fixtures for the old detached runner now name the Claude transport
explicitly. Native tests exercise request/result separation, forged stdout,
replayed launches, foreign process IDs, omitted shell metadata, scope widening,
read-only polling/interruption, assignment-bound copy execution and cleanup,
and queued preview behavior. The first broad run found that Ruff and mypy were
missing from the user-site-free test interpreter. Validation was moved to a
temporary environment installed from the repository's hash-locked test and
quality dependencies; environment restrictions were preserved.
The extracted-package journey's startup fixture was corrected to initialize
the workspace and declare its tools before expecting readiness; its refusal
checks for missing and foreign host evidence remain intact.

The workspace launcher still selects the Marketplace installation of 2.23.6.
The source changes are not an in-place edit of that installed plugin. A release
or an explicitly selected local development installation must carry the updated
engine and skills before this workspace can use the new review path. No second
hook registration, controller, server or custom installer was added.

The original governed EM review still lacks completed evaluation and sign-off.
Its previously observed pickup-budget refusal remains a separate unresolved
policy decision; this migration does not reset usage or grant another budget.
