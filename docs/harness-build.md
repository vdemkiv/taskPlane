# Harness completion: model-led orchestration

The user stopped the deterministic orchestrator build. The model continues to
orchestrate through the existing TaskPlane harness; no new controller, host bridge
or duplicate phase/budget policy engine is installed.

## Retained and completed

- Exact-operation collection delegates to the existing gate. Its replay marker
  commits in the same transaction as the phase transition, including parallel
  tasks. A lost response can be retried without advancing a second phase.
- Workers cannot call gate, collect, select or resolve directly. Existing approval
  and amendment owners continue to reject worker self-approval.
- Additional CLI mutations require fresh installed hook receipts: collection,
  selection, resolution, replan, settings restoration, terminal and applied
  amendments. Read-only inspection remains available.
- Resource waivers are disabled. Historical decisions remain readable but cannot
  relax runtime, root/wave, brief, telemetry, deadline or review-capacity checks.
  Legacy advisory arguments cannot convert missing usage or exhausted capacity
  into permission. Existing explicit action-budget grants remain available.
- Canonical CI ignores local onboarding settings and active-run snapshots.
- Model dispatch instructions use the canonical bounded startup and event wait
  policy. Showing a dashboard is not a reason to stop admitted work.

## 2.23.6 shared entry initialization

The Codex file-access repair adds a bounded `inspect` data operation to the
existing CLI. Its exact isolated installed-engine invocation is screened under
the same Read/Grep/Glob permissions and action/token budgets. It reads regular
files, lists directories, and performs literal searches without loading run
configuration, executing Git, importing reviewed code, or writing source.
General terminal commands remain denied. Native file tools and scoped artifact
edits keep their existing behavior; no new server or approval system is added.
Onboarding now carries `workspace_ready` and `review_ready` separately and
includes incompatible file access in its effective readiness and exit status.

All direct skills use `onboard --initialize --json --available-tools <names>`.
Setup reuses the existing onboarding and launcher owners. The small session
inventory reports compatibility and cannot grant execution or weaken a
contract. A missing required file tool refuses read-only activation before
writing an active contract. Existing inspection/recovery stays available when
usage telemetry is missing. No alternate transport is included.

The scope incident and exact design-history distinction are documented in
[the incident report](incidents/2026-09-13-entry-initialization-overengineering.md).

## 2.23.5 session isolation and fresh-checkout readiness

Execution homes, Git run locators, contracts, meters, review state and local
review outputs are partitioned by host conversation. A hook's event identity is
selected before compatibility checks and state discovery, so another session
cannot inherit or clear the owning session's contracts. Process restarts retain
state only when their host session identity remains the same.

A native hook observation can prove readiness across fresh checkouts in that
same session. Bridge trust remains workspace-bound. A standalone review binds
later screen and lifecycle events to its checkout; path normalization preserves
the tool's actual working directory. Clearing the review detaches its routing.
The shared launcher contains no execution state.

Validation uses fresh temporary test stores and simulated host events. Native
installed end-to-end review remains unverified because the plugin was removed
before this repair. The upload archive and its checksum/provenance are build
artifacts, not evidence of Marketplace publication or live-host sign-off.

## 2.23.4 standalone Review startup repair

Standalone Review now prepares its own signed native worker contracts and the
ignored launcher in the actual review checkout. Retrying the recorded execution
choice preserves leases, native child identity and validation evidence. The
existing lifecycle binds workers on `SubagentStart` without requiring a native
per-child environment argument. Reviewed project hook files are preserved.

The native-binding regression group passed 186 tests and 14 subtests. The final
launcher group passed 111 tests and eight subtests; these groups overlap. The
preceding live installed attempt exposed the missing checkout launcher, failed
to enforce its budgets and refused nested process isolation before candidate
tests started. It was stopped. The final launcher repair has not completed a
fresh live review or human sign-off. The 2.23.4 OpenAI and Claude archives are
prepared for upload; packaging and a source push do not assert those outcomes.

## 2.23.3 validation history

Focused checks on Python 3.13.9; these groups overlap:

| Group | Result |
| --- | --- |
| Budget, runtime, telemetry, native session, settings and CI | 173 passed |
| Phase integration, run context and control boundaries | 107 passed |
| Hook enforcement, tool screening, worker stop, collection and parallel flow | 56 passed; 40 subtests passed |
| Ruff | Passed |
| Strict typing | 114 modules passed |
| Source policy | 129 modules; zero violations |
| Release candidate | 2.23.3; OpenAI archive prepared after CI for manual upload |

Integration checks use the actual harness with simulated host/model responses.
They cover normal Product collection, serial/parallel Build and Evaluate,
Engineering, human sign-off, Retro, foreign operations, lost-return replay,
expired authority, unknown usage and attempted historical waivers. No live model
phase or Hello World run was launched. PR #26 runs the full repository suite
against this selection; its checks are the release validation authority.
Release checks retain strict hook admission for amendments and refuse historical
resource waivers without retrying oversized review capture. Existing custody
capacity and explicit action-budget grants remain covered independently.

## Preservation and boundary

The stopped working diff and 32 original files are retained and hash-verified in
`.taskplane/harness-salvage/stopped-orchestrator.zip`. Driver-only source and tests
were removed from the active tree after archival. The former plan remains local
historical material, marked stopped.

Run `8a74f18a95654afaa178e91f6f098c82` remains at Product (`pm`). Its saved state,
requirement and settings match their original hashes. No approval was created.

These changes enforce supported harness entry points inside the installed host.
A plugin cannot prevent a process with sufficient OS permissions from disabling
hooks or rewriting the enforcer. Human `--by` strings provide attribution, not an
independent host attestation. Before-inference metering of every orchestration
continuation remains outside this model-led implementation's guarantees.
