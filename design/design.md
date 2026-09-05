# R-0002 — Current-run control plane

Taskplane will use one active-run snapshot as the input to every governed stage. `loop.py` is the composition root, not a new policy owner: it loads the fully populated canonical settings document once, captures the run/requirement/candidate/host binding, and sequences the existing decomposition, lens, dashboard, validation, failure, cleanup, telemetry, and release owners.

## Governed sequence

Design always runs `snapshot → decomposition → dynamic risk selection → automatic host assignment → parallel quick lens wave → one wait → terminal result conservation → consolidation → gate publication`. The selected lens count is bounded at sixteen but never filled for its own sake; every optional lens needs an independently evidenced uncovered risk. Planned, assigned, started, and terminal worker identities must match exactly. A settings-only change creates a fresh Design snapshot and must change the actual host assignments when the effective policy changes.

After each Build slice, zero-lens quality validation runs focused static checks and typed boundaries in parallel where disjoint, collects and executes exact public selectors, exercises changed consumers and semantic severed edges, checks serialized fixtures in the same slice, and runs one proportional suite. A green layer is reused only for identical inputs. Any red result is classified before correction.

Build, Evaluate, and CI emit the same typed failure record. Product failures may enter product Fix. Test, infrastructure, and environment failures use their own correction or recovery state. Mixed and unknown failures hold. A human retry cannot convert an unclassified failure into product authority.

GitHub Actions runs one real, unsharded Python 3.12 suite. Quality/package, browser, interpreter-import, and true OS-boundary validation may run concurrently because their environments and contracts are distinct. No join job or compatibility alias may masquerade as a test.

## Dashboard and graph truth

The orchestrator seals one stage graph into the canonical dashboard snapshot. Fresh Design shows the current decomposition graph before a Design contract exists; later stages show their current-run solution or task graph. Renderers never reopen mutable Design, Plan, graph, or loop files. Publication uses an expected-head compare-and-swap, so delayed prior-run or prior-stage output cannot become current. The visible provenance includes run, requirement, stage, revision, settings digest, authority receipt, graph state, and snapshot fingerprint.

Degraded, unavailable, stale, generating, and not-created are distinct states. Only ready current-run surfaces may expose governed actions. Responsive and localized output retains graph direction, semantic node/edge lists, keyboard access, full accessible labels, and explicit static/offline limitations.

## Durable run artifacts and cleanup

Each private run has one adjacent artifact root with separate `dashboard`, `dependency-graphs`, `telemetry`, `agent-activity`, `validation`, `cleanup`, and `retro` classes. This is not a second run state store: the private RunStore remains the sole mutable lifecycle authority, while the artifact root contains append-only or content-addressed evidence referenced by it.

Every agent assignment, worker identity, stage/task/lens, start, progress, attention, terminal outcome, usage reference, and evidence reference is appended to the agent-activity class. Detailed identities and diagnostics remain private; portable dashboards and Retro ZIPs use minimized pseudonymous attempt references and explicit inclusion/exclusion manifests.

Cleanup runs on success, failure, cancellation, interruption, handoff, and recovery. It seals evidence first, deletes only exact manifest-owned temporary resources, refuses ambiguous or unsafe targets, excludes the durable run-artifact root, then proves both zero owned leaks and continued artifact readability. Cleanup never rewrites the original outcome.

## Delivery targets

The measured prior wave lasted 24h58m09s while successful CI was about 13m10s with 2.11× effective parallelism. The target is at most twelve hours end to end, with exact local feedback under sixty seconds, changed-radius feedback under five minutes, CI p95 at most fifteen minutes, no more than 28 runner-minutes, zero cleanup leaks, and measured token totals or explicit unavailable status. Test counts and physical file sizes are telemetry, never pass/fail limits.

Rollback is whole-candidate rollback with the new run artifacts retained read-only. Taskplane never rolls back by silently accepting stale graphs, unclassified Fix authority, a second settings source, or deletion of terminal evidence.
