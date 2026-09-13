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

## Validation

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
