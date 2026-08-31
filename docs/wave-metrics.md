# Delivery wave metrics

Taskplane measures one delivery candidate once. `taskplane/wave_metrics.py`
seals a `taskplane.wave-metrics-receipt/v1` only after the run interval is
closed, and every source must identify the same candidate and exact interval
as `non-cumulative`. Settings, CI, dashboard publication, cleanup, portfolio,
token usage, sessions, worktrees, and dispatch each contribute only a SHA-256
digest and bounded aggregate facts. Paths, host/user identity, raw logs, and
secrets are omitted.

The receipt is the shared input for the dashboard, Retro, Engineering sign-off,
and the release gate. Those consumers use `consumer_projection`; they do not
walk traces, archived ledgers, CI reruns, canceled heads, generated HTML, or DOM
counts. `integration_ready_at` is part of the sealed run interval, so time to
first CI and active delivery duration share one explicit boundary.

## Baseline and target coverage

The receipt records settings ownership and duplicate defaults; suite files,
cases, lines, and redundant families removed; exact/proportional feedback p95;
CI first-start, matrix/red counts, critical path, runner-minutes and parallelism;
cleanup leaks and worktree state; tokens and sessions; and active/end-to-end
delivery duration.
The delivery-churn row preserves 21 Plan returns as its baseline and permits at
most two before the separately governed stabilization-successor rule applies.

The historical baseline remains explicit: 258 settings-spread files; 266 test
files, 4,909 cases, and 95,601 lines; first CI after 31h37m; 12 matrices with 9
red; about 15 critical-path minutes, 38 runner-minutes, and 2.59x parallelism;
132 stale worktrees using 17.3 GB; 540.3M host-observed tokens; a separate
1.292B cumulative archive upper bound; and 40h35m end-to-end.

Targets are 100% settings ownership and zero duplicate defaults; at most 230
test files and 4,200 cases with at least six redundant families removed; exact
p95 at most 60 seconds and proportional p95 at most five minutes; first CI
within two hours, at most three matrices, at most 30 runner-minutes, and at
least 4x parallelism when four shards exist; zero leaks and active worktrees at
most active shards plus one; 100M total and 15M uncached observed tokens; at
most 24 planned sessions; active delivery at most eight hours and the phase
through Retro at most twelve hours.

## Truth classes and terminal guardrails

Billing, host-observed usage, and cumulative archive upper bounds are different
truth classes. Missing billing stays `unavailable`; it is never inferred from
host logs. The archive value is labelled `upper-bound-not-billing` and cannot
replace observed usage.

The hard ceilings remain 150M total tokens, 25M uncached tokens, 60 sessions,
and eight active-delivery hours. A breach must have an explicit classification.
Any unexplained breach or nonzero owned cleanup leak makes `signoff.ready`
false. Every deliberate serialization is named with its reason. Pairwise-
disjoint build work and CI cells remain parallel.
