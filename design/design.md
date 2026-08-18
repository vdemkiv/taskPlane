# R-0009 — Host-parity governed PR review

## Outcome and current state

Claude and Codex will drive the same canonical ReviewKernel session from a pinned PR through DoR discovery, routing, optional disposable dynamic validation, partial-safe collection, artifacts, inline evidence, and final disposition. Host adapters transport prompts, consent, tool observations, and widgets only; they cannot create review truth.

This extends existing seams rather than replacing them. `review.py` already normalizes consent without magic phrases, records host-observed execution separately, models disposable push-disabled sandboxes, and owns routing/dispatch/collection. `review_evidence.py` already provides content-addressed immutable artifacts, bounded v3 views, target/revision-bound references, leases, slot results, canonical revisions, and projections. `command_runtime.py`/`command_adapters.py` provide durable resumable command execution. `dashboard.py` provides bounded inline pages and standalone rendering. Missing today are a canonical DoR evidence ledger, per-criterion verdicts, a consolidated consent scope, provisional revisions, constrained mechanical repair, affected-slot retry, and a lossless three-format artifact transaction.

The Python reference was verified at SHA-256 `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`. The design keeps synchronous ReviewKernel ownership; dynamic commands may block in the existing runtime. Cancellation propagates from review session → sandbox command → process tree. JSON/persistence is validated at trust boundaries. No new runtime dependency, package namespace, or import-time global is introduced; Python floor and wheel contents remain unchanged. Mutable state stays behind existing file locks, so free-threaded Python does not rely on the GIL.

## Alternatives

### A. Canonical review session with thin host adapters — selected

Add host-neutral session, DoR/criterion ledger, consent, provisional revision, repair, retry, presentation, and artifact contracts inside ReviewKernel. Claude/Codex translate only native interaction and observed tool results.

Gains: one authority, semantic parity, resumable partial work, narrow reuse of shipped storage/command/view primitives. Costs: coordinated schema evolution and parity fixtures across adapters. Revisit when a future host cannot transport the canonical interaction/event schema without losing required evidence.

### B. Host-owned orchestration over shared result schemas

Each host discovers DoR, asks questions, chooses dynamic checks, and retries independently, then emits shared results.

Gains: native UX freedom and smaller kernel delta. Costs: duplicated policy, approval loops, divergent routing/repair semantics, and two sources of truth. Revisit only if host policy prevents a canonical state machine and parity is explicitly removed as a requirement.

### C. Strict all-or-nothing collection with full reruns

Keep current complete-only collection; any invalid slot reruns the whole wave.

Gains: simplest canonical revision rule. Costs: loses valid findings, wastes model turns, cannot expose honest partial state, and repeats approvals. Revisit only if producer cost is negligible and provisional evidence is legally prohibited.

## Canonical flow

1. **Pin.** `contract:review-host-adapter` normalizes target/ref, host capabilities, and transport metadata into one session id. Target fingerprint and remote write prohibition are immutable.
2. **Discover DoR.** `contract:review-dor-evidence` probes title, body, comments, commits, changelog, linked issue/spec/acceptance text, and repository contracts in deterministic order. Every source records identity, revision, access status, freshness, provenance reference, and contradiction links. A classifier emits objective, acceptance criterion, review directive, constraint, or context with confidence and source spans. Only ambiguity that changes routing, executable validation, or pass/fail interpretation becomes one consolidated clarification item.
3. **Consolidate consent.** `contract:review-human-consent` presents one complete-review decision containing render mode, dynamic validation choices, sandbox repair permission, known non-destructive commands, and artifact publication. Natural-language consent is normalized to the same immutable scope fingerprint; exact phrases are never required. Re-consent occurs only on the six requirement-defined authority changes, and records the new fact and requested authority.
4. **Route and validate.** Directives route lenses independently of feature criteria. Dynamic checks use the durable command runtime inside a disposable copy with `push_disabled=true`; the pinned checkout and remote are observed before/after. Sandbox modifications are evidence only and results distinguish submitted PR from repaired sandbox behavior.
5. **Collect progressively.** `contract:review-kernel-partial-revision` freezes every valid slot immediately into an immutable non-approvable provisional revision. It inventories missing/invalid slots and supersedes rather than mutates. Zero-slot selection is a named failure, never completion.
6. **Repair/retry.** `contract:review-kernel-mechanical-repair` accepts only fields derivable from sealed lease/producer/view/reference authority. It records before/after canonical bytes, derivation source, equivalence proof, repair rule/version, actor, and fingerprint. Findings, evidence, target, producer, slot, or unverifiable identity are substantive and cannot be repaired. Retry manifests include only affected slots; valid result fingerprints are reused and never rewritten. Two mechanical attempts and two affected-slot attempts are the hard bounds before `unavailable`.
7. **Judge criteria.** Each extracted criterion gets `pass|fail|unproven|not-applicable`, rationale, evidence references, verification method, responsible lens/step, and revision binding. Unproven or unjustified n/a makes the gate non-approvable.
8. **Publish atomically.** `contract:review-artifact-set` derives lossless JSON, Markdown, and HTML from the same immutable revision model. All three are written to temporary content-addressed objects, semantically parsed/compared, then one manifest is committed. Failure leaves the revision intact and publication retryable; no partial set is advertised.
9. **Present.** `contract:review-inline-presentation` projects bounded keyboard-operable pages/references from the artifact model. Pages preserve revision/provenance, focus, filter state, and action receipts. The inline widget may omit bytes but never semantic rows; complete exports remain lossless. Buttons emit signed session/revision/action receipts and cannot execute undeclared actions.
10. **Gate.** Only a complete canonical revision with all criteria justified, every routed slot resolved, verified artifacts, and matching consent may enable final approval.

Module ownership is explicit for Plan: the DoR owner owns `review_dor.py`; ReviewKernel/session owner owns `review_session.py`; recovery owner owns `review_recovery.py`; artifact owner owns `review_artifacts.py`; existing presentation and command owners retain `dashboard.py` and the command runtime/adapters. Delivery order is DoR/session contracts → provisional revisions → repair/retry → artifact transaction → inline host parity, with compatibility fixtures at every boundary.

## Numeric quality and failure policy

- Producer views remain `<=16,384` canonical UTF-8 bytes; inline pages remain `<=14,000` bytes. JSON/MD/HTML exports have no semantic truncation.
- Fixtures cover at least 26 lenses, 120 findings, 126 KiB Markdown, 342 KiB HTML, and multi-megabyte source evidence.
- At most one initial consolidated consent, one ambiguity clarification, two mechanical repair attempts per slot, and two substantive retries per affected slot before stable non-success.
- Valid slots are written once; retries invoke only affected producers; artifact bodies are stored once per digest.
- Availability/RPO/RTO are not applicable to this local resumable pipeline; durability is fsync/atomic-replace plus immutable content addressing.

Signals are `review_session_transition`, `review_dor_source`, `review_consent`, `review_slot_revision`, `review_repair`, `review_retry`, `review_dynamic_sandbox`, `review_artifact_publish`, and `review_inline_action`. They carry safe fingerprints/counts/statuses, not secrets, personal paths, raw credentials, or unrelated transcript data.

Failures are explicit: inaccessible/stale/conflicting DoR, ambiguous requirement, host limitation, dynamic unavailable, invalid reference, partial slots, repair rejection/exhaustion, renderer/write failure, sandbox escape/change, artifact semantic mismatch, and stale action receipt. Recovery actors and bounds are encoded in the contract; none may synthesize pass, zero findings, declined choice, or completion.

## Rollout, rollback, and validation

Use additive versioned schemas behind `TASKPLANE_CANONICAL_PR_REVIEW`. Phase 1 dual-runs deterministic fixtures and existing small goldens without changing authority. Phase 2 enables DoR/consent and provisional collection, then repair/retry, artifact transaction, inline widgets, and finally host parity. Existing readers continue to consume canonical complete v1 projections; provisional and new fields are additive. Rollback disables new sessions, finishes issued leases with retained readers/resolvers, and publishes legacy complete projections. Immutable revisions/artifacts are never rewritten or deleted.

Validation includes cross-host golden semantic normalization; DoR source/classifier corpora; consent paraphrases and authority-change matrices; zero/one/several/all invalid slot matrices; the observed frontend declaration repair; tamper/equivalence rejection; affected-only retry call counts; 120-finding JSON↔MD↔HTML round trips; multi-megabyte bounded-view/page tests; keyboard/focus/action receipt tests; disposable broken-build validation with git/remote invariants; fault injection at every stage; clean-wheel install, strict typing at new boundaries, and graph verification.

The visual is required because the immutable revision/supersession sequence and separation between lossless artifacts and bounded inline pages materially clarify the design.
