# R-0008 — Size-safe ReviewKernel dispatch

## Outcome

Large canonical review envelopes and dashboards must never inflate a leased producer prompt. ReviewKernel will keep one immutable canonical envelope, then derive each lens slot through a deterministic, reference-first projection whose serialized view is at most 16 KiB. The view keeps its complete identity/provenance spine inline; lens-relevant sections consume the remaining budget; overflow is stored once in the existing content-addressed `ArtifactStore` and cited by verified references. An unresolvable reference stops the slot before dispatch and cannot become a successful zero-slot review.

This extends the existing `taskplane/review_evidence.py` envelope, artifact reference, scoped-view, lease, and result machinery. Today large requirements and impact can be referenced, but `graph_quality`, `runnability`, `change`, `evidence`, contracts, and target data are copied wholesale and the final size check only raises. Dashboard generation is downstream, but this failure prevents lenses reaching it.

## Alternatives

### A. Deterministic reference-first section projection — selected

Extend the incumbent artifact store with governed section artifacts and a projector that reserves mandatory inline provenance, ranks lens-relevant context using sealed routing facts, stores each unique overflow section once, and inventories omissions.

Gains: bounded views independent of canonical/dashboard size; one evidence authority; deterministic bytes; narrow compatibility change. Costs: versioned view/reference contracts and verification at dispatch/collection. Revisit when every supported transport guarantees a materially larger bounded payload and duplication cost is negligible.

### B. Increase or remove 16 KiB

Gains: smallest patch. Costs: failure moves to provider limits, prompt/storage multiply by lens count, and no deterministic bound remains. Revisit when all transports enforce the same substantially larger minimum context.

### C. Paginate each lens

Gains: bounded exhaustive pages. Costs: breaks one-lens/one-lease authorship, adds result merge/deduplication, multiplies producers, and risks inconsistent verdicts. Revisit when evidence proves lenses require exhaustive sequential reading rather than targeted verified resolution.

## Selected design

`contract:review-kernel-evidence-reference/v2` adds target binding, canonical revision, section identity, digest, byte length, semantic fingerprint, and immutable artifact identity to a portable reference. It contains no absolute path. Resolution derives the canonical store path from kind/fingerprint; rejects traversal, symlinks, aliases, and authorization mismatch; verifies bytes/digest/fingerprint/target/revision; then returns only the named section.

`contract:review-kernel-scoped-view/v3` has two layers:

1. Mandatory inline spine: schema, target/context fingerprints, canonical revision, routing fingerprint, lens ids, slot id, lease precursor, producer identity, envelope fingerprint/digest, reference-manifest fingerprint, view fingerprint, integrity algorithm, and omission inventory.
2. Deterministic projection: normalize candidates and sort by `(lens relevance class, canonical section id, content fingerprint)`. Mandatory relevant summaries enter first. Each candidate is inline if exact canonical JSON fits, otherwise it becomes one verified reference. Every optional omission records section id, reason, bytes, digest, and reference.

The projector reserves spine/manifest bytes first and fails with `review_scoped_view_budget_impossible` if mandatory provenance cannot fit. It never truncates JSON or drops a routed lens. Pinned inputs therefore produce stable fingerprints regardless of ordering.

`taskplane/review.py` prepares every routed slot, verifies its manifest, and enforces `selected == prepared == dispatched == collected` for non-zero selection. Failure becomes a named slot error with safe digests, never an empty success. Collection re-resolves references and rejects mismatched view, manifest, lease, target, producer, revision, lens, or slot.

`taskplane/dashboard.py` projects canonical collected state only. Dashboard size and pagination have no input edge to routing, leasing, findings, or gates and may grow independently.

## Bounds, failure, rollout, validation

- Each canonical UTF-8 JSON producer view is `<= 16,384` bytes at dispatch.
- One content-addressed copy exists per unique overflow digest; total inline bytes are at most `16,384 × prepared slots`.
- Mandatory provenance is never truncated. No availability/RPO/RTO applies because this is a local retryable artifact pipeline.
- Signals: `review_projection`, `review_reference_verify`, `review_slot_conservation`, and `review_dashboard_projection`; diagnostics contain safe digests/counts, never paths, source text, credentials, or secrets.
- Missing/stale/mismatched/unauthorized/traversal/symlink/mutated references stop before producer execution or canonical publication. Retry rebuilds views from the pinned envelope without re-deriving facts.
- Roll out behind `TASKPLANE_REFERENCE_FIRST_VIEWS`, compare fitting-review parity, then make v3 authoritative while retaining v2 readers for active leases. Rollback disables new v3 preparation but continues collecting issued v3 leases; immutable artifacts require no migration.
- Unit tests cover exact bounds, multi-megabyte sections, permutations, relevance, deduplication, and rejection matrices. Integration tests compare small/large routed slots, findings, and gates; dashboard size/pages vary metamorphically. Collection tests tamper every identity. A regression recreates a non-zero routed review whose old scoped view exceeded 16 KiB.

The data-flow visual is included because separation of canonical evidence, bounded views, and independently growing dashboard output is the central decision.
