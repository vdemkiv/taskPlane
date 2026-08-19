# R-0001 — Progressive convergent engineering review

## Decision

Extend the existing ReviewKernel with one canonical progressive-review ledger. Initial dispatch contains catalog-declared mandatory deep floors, evidence-selected deep lenses, and at most one bounded light sweep. Sweep concerns can only promote named in-charter lenses through normalized, referenced trigger evidence. Valid blocking evidence immediately seals an immutable provisional `request-changes` revision; approval remains impossible until every selected/promoted slot and acceptance-evidence obligation is conserved exactly once.

This is a delta against the shipped system. `lens.py`/`lens_signals.py` already perform signal routing and bounded dispatch; `review.py` already owns routing, v3 leases, promotions, slot conservation, DoR, and production models; `review_evidence.py` already supports immutable partial collections/revisions; `review_dor.py` classifies documents and criteria; `taskplane_lite.py` already fingerprints engine/worktree and caches suites; `loop.py` owns fix cycles/human authority; `spend.py` records provider-correct usage; R-0011 supplies canonical cross-host UX. The known gaps are module-miss over-widening, complete-only blocking publication, producer metadata contradictions, repeated evaluator launches, global two-cycle convergence, and sparse lens-value telemetry.

The verified Python solution-design reference SHA-256 is `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`. Review orchestration stays synchronous and file-locked; producer waves use the existing runtime. Runtime JSON/cache/evidence are validated at trust boundaries. No new dependency, package namespace, Python floor, lock, or wheel-content change is required; mutable ledgers are serialized and do not rely on the GIL.

## Alternatives

### A. Canonical progressive ledger — selected

Add risk progression, evidence binding, evaluator health cache, convergence, and lens telemetry as versioned records under ReviewKernel. Gains: early truthful blocking results, less dispatch/spend, exact recovery, one host-neutral authority. Costs: more explicit state transitions and compatibility fixtures. Revisit when the lens catalog becomes small/cheap enough that exhaustive review is measurably faster without quality loss.

### B. Exhaustive up-front review

Always dispatch every applicable lens deeply. Gains: simple scheduling and maximum initial breadth. Costs: slow/costly, repeats known failures, delays blockers, and still does not solve repair/convergence. Revisit for an explicitly human-selected forensic audit mode, never default.

### C. Heuristic progressive review without canonical revisions

Dispatch progressively but keep partial results transient until completion. Gains: smaller persistence delta. Costs: loses early findings on failure, cannot truthfully request changes, weak resume/audit. Revisit only if provisional evidence retention becomes prohibited.

## State and contracts

`contract:review-risk-progression` seals the routing fingerprint, mandatory floors, evidence-selected deep set, optional single sweep, normalized promotion triggers, and deterministic slot ids. A high/major sweep concern must reference evidence, rationale, charter and responsible lens; it is promoted or canonically rejected with reason. Light never substitutes for deep.

`contract:review-kernel-slot` becomes one slot ledger with states `selected`, `prepared`, `dispatched`, `produced`, `validated`, `collected`, `promoted-pending`, `retrying`, `missing`, `invalid`, and `infrastructure-unavailable`. Exactly-once conservation is checked across every transition and acceptance evidence.

`contract:review-kernel-partial-revision` publishes immutable provisional request-changes immediately after the first admissible blocker. It contains all known valid findings, gaps, routing/promotions, dynamic evidence, provenance, artifacts, and `approval_enabled=false`; later revisions supersede without mutation/loss/duplication.

`contract:review-kernel-mechanical-repair` normalizes verdict/count/severity/completion metadata to canonical bytes. Only identity/count/summary/schema values derivable from the sealed lease/result may repair once, with before/after, authority and equivalence fingerprints. Any finding/evidence/target/view/lease/slot/producer/substantive verdict mutation retries only that slot.

`contract:review-evidence-binding` binds repository identity, exact real worktree root, target/base/head, engine fingerprint/version, run, lens, slot, lease and observed producer. Paths exposed outside the kernel are redacted fingerprints; symlink, sibling, clone, moved root, stale head, copied result and engine skew fail closed.

`contract:evaluator-infrastructure-health` caches only verified evaluator infrastructure-unavailable facts keyed by evaluator + engine/version + capability fingerprint + repository + exact worktree root + validity window. TTL is 15 minutes, maximum one reuse per review wave; any key change, expiry, repair receipt, or successful probe invalidates. Cache never creates a lens verdict.

`contract:review-fix-convergence` compares immutable finding fingerprints across cycles: closed, persisted, regressed, new, evidence progress, oscillation, unsafe/scope change. Continue while measurable progress is positive and within the task-specific maximum declared by Plan; escalate on two consecutive no-progress cycles, repeated fingerprint, oscillation, regression, unsafe action, scope change, or human stop. It never auto-approves or broadens scope.

`contract:lens-quality-telemetry` derives post-review metrics only from sealed revisions and later adjudication: eligible/selected/promoted/collected, admissible/confirmed/unique/overlap/duplicate/invalidated/false-positive findings, retries/repairs, latency, tokens/cost and infrastructure unavailability, all with schema/version/denominators. Telemetry is unavailable rather than zero and is invisible to concurrent lens producers/current routing.

Documentation routing uses content, directives, contracts, audiences and graph evidence. Missing/inapplicable module mapping never widens by itself. Ambiguous/corrupt docs retain floors and add only the smallest evidence-backed explained set.

## Bounds, failure, rollout

- Initial work: mandatory floors + evidence-selected deep + `0..1` light sweep; no unconditional full catalog.
- Sweep promotions are idempotent by `(routing fingerprint, concern fingerprint, lens)`.
- Provisional publish occurs within the same collection transaction as first admissible blocker.
- Metadata repair: at most once/result. Affected-slot retry follows Plan bounds. Health-cache TTL: 15 minutes, one reuse/wave.
- Convergence escalation: two consecutive no-progress cycles; task-specific total bound replaces the global two-cycle cap.
- Telemetry never affects the current review and stores bounded fingerprints/counts, not prompts/secrets/personal paths.

Signals: `review_risk_progression`, `review_slot_ledger`, `review_revision_lineage`, `review_metadata_repair`, `review_evidence_binding`, `evaluator_health_cache`, `review_convergence`, `lens_quality_metric`. Failures are named and preserve valid evidence. Recovery actors are ReviewKernel, evaluator-health owner, or human gate owner within the bounds above.

Roll out additively behind `TASKPLANE_PROGRESSIVE_REVIEW`: dual-record routing/ledger first, then provisional publication, repair/cache, convergence, and post-review telemetry. Compare existing complete-review and cross-host goldens. Rollback disables new progressive starts, finishes issued slots/readers, retains immutable revisions, and returns new reviews to current routing/fix behavior; no data migration or deletion.

Owners/order: routing owner → `review_progression.py`; evidence owner → existing `review_evidence.py`; recovery owner → `review_repair.py`; infrastructure owner → `evaluator_health.py`; loop owner → `review_convergence.py`; telemetry owner → `lens_telemetry.py`. Ship ledger/binding first, progression/provisional second, repair/cache third, convergence fourth, telemetry last.

The visual is useful because promotion, early provisional publication, affected recovery, and convergence form a non-linear state sequence.
