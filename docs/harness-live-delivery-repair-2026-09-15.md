# Live delivery runtime repair — 2026-09-15

## Scope and status

Repairs the four defects observed in the preserved native delivery run `67c8b79169f543eeae879e51ac5d0b49`. Changes are isolated in `/private/tmp/taskplane-live-delivery-repair`, branch `codex/live-delivery-repair`, based on `998b0e4c3e35feb5862c6fa23c376e38f1a48743`. The original source checkout and failed run remain intact. No historical receipt is rewritten.

## Changes

- Product startup describes the canonical closed requirement schema. Candidate admission validates that same schema before issuing specialist leases.
- Stop records bind the exact raw candidate bytes, including malformed drafts. Collection refuses edits made after Stop. An explicitly authorized retry of a stopped, uncollected Product, Design or Plan attempt uses the existing retry journal to grant one new slot, nonce and worker identity; the previous evidence stays unaccepted.
- Native usage selects the latest complete counter at or before the authenticated Stop within the existing bounded tail. Later counters, foreign lineage, resumed segments, missing counters and corrupt metadata retain their refusal behavior. Native terminal accounting closes independently of candidate acceptance.
- Whole-run termination retires its exact signed phase worker slots before claiming clean resource cleanup. An active worker needs its authenticated Stop. Other slots remain intact. Session-start cleanup preserves interruption/cancellation outcomes.
- Specialist leases register their exact expected native task, model and reasoning route. Reopening a consumed lease does not create another dispatch expectation. Specialist review provenance continues to use the review kernel.

## Evidence and checks

Diagnostics: `/Users/vdemkiv/Documents/taskPlane/.taskplane/diagnostics/live-repair-2026-09-15-01a0a595`.

Focused regression tests cover invalid-draft admission, changed output after Stop, explicit retry authority, immutable previous evidence, new attempt identity, terminal accounting, immediate exact-run cleanup, missing-Stop refusal, and idempotent native lens expectations. Existing identity, usage, lifecycle, phase, handoff and telemetry tests are also exercised. These use simulated host events and are not native acceptance evidence.

Static lint, diff checks and strict typing of the changed typed modules pass. One old continuity test expected obsolete onboarding prose; it failed identically on the untouched baseline and now checks the actual continuation invariant that session restoration grants no approval.

Evaluate integration initially could not see validation tools installed only in the user site, which its isolated environment deliberately excludes. A temporary virtual environment was populated from the already installed Ruff, mypy, Bandit and dependencies, without downloads or global installation changes. All four real quality probes pass there. The previously blocked Plan/Build/Evaluate integration and the focused repair tests pass (6 tests).

## Actual original counter recovered

A read-only recheck against the original authenticated Stop selects native ordinal 215, timestamp `2026-09-15T15:15:52.593Z`, before the Stop at `15:15:52.984843Z`. It measures 1,771,137 input tokens, including 1,696,640 cached input tokens, and 7,847 output tokens: **1,778,984 total tokens**. The old Product limit was **100,000**. This genuine measurement cannot make the old attempt pass; the corrected behavior must retain budget exhaustion. The previous latest counter was 385 seconds after the stopped attempt.

## Native rerun

A new disposable fixture is prepared at `/private/tmp/taskplane-live-repair-20260915-01a0a595`, baseline `9472ffc795d4048a6dd977fb69fc65bded0415a2`, with a fresh R-0001 containing separate acceptance criteria. No original failure state is copied. Native rerun and package verification are pending at this report revision.
