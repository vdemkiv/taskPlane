# Specification — size-safe ReviewKernel dispatch for large reviews

## Problem

ReviewKernel currently allows a canonical review artifact or dashboard to make
a scoped lens view exceed an internal byte limit. The review can then stop
before dispatch with zero lens slots. Larger reviews naturally create larger
artifacts; artifact size is not a valid reason to omit applicable review.

## Users and context

Engineers reviewing large pull requests, repositories, evidence sets, and
dashboards need every applicable lens to run with bounded model context while
retaining immutable identity and provenance for the canonical input judged.

## In scope

- Decouple canonical review-artifact and dashboard size from leased-view size.
- Bound scoped views with summaries and verified references to large evidence.
- Preserve mandatory target, run, revision, routing, lens, slot, lease,
  producer-contract, provenance, integrity, and authorization data.
- Fail closed when required referenced content cannot be resolved or verified.
- Prevent size-related preprocessing from silently yielding zero slots.
- Cover large envelopes, evidence, dashboards, bounded views, reference
  integrity, dispatch counts, and canonical collection with regressions.
- Keep dashboard rendering faithful without treating dashboard bytes as lens
  input or review authority.

## Out of scope

- Removing context limits or sending the full dashboard/artifact to each lens.
- Weakening applicability, security/architecture floors, lease isolation,
  producer validation, collection, graph semantics, or human gates.
- Treating dashboard HTML as canonical evidence or routing authority.
- Unverified filesystem/network/model-authored references or mutable aliases.
- Truncating mandatory provenance, silently dropping selected evidence, or
  converting unresolved evidence into empty, zero, or pass.
- Lens-catalog and dashboard visual redesign; unrelated review behavior.
- Version, release, marketplace, publication, tag, or push work.

## Acceptance criteria

1. **Large artifacts do not block dispatch.** A canonical envelope, evidence
   set, or dashboard larger than the scoped-view budget still dispatches every
   routed slot. **Verify:** below-limit and substantially above-16-KiB fixtures
   produce identical selected-lens and dispatched-slot sets.

2. **Every view stays bounded.** Serialized bytes delivered to each producer
   never exceed its configured view budget. **Verify:** budget-minus-one,
   exact-budget, budget-plus-one, and multi-megabyte inputs.

3. **Identity/provenance stay inline.** Each view retains target fingerprint,
   run/review id, canonical revision, routing id, lens/slot/lease ids, schema
   and producer contract, plus reference provenance/integrity metadata. None
   may be truncated or replaced by prose. **Verify:** schema assertions at all
   boundaries and rejection of every missing/altered mandatory field.

4. **Overflow uses verified references.** Non-fitting content is stored once
   and represented by a stable reference with content digest, revision binding,
   media/schema type, length, and authorized resolver data. **Verify:** resolved
   bytes and digest for large diff, graph, DoR, dynamic-validation, and prior-
   findings evidence match the canonical source without per-lens duplication.

5. **References fail closed.** Missing, unreadable, stale-revision,
   digest-mismatched, wrong-target, unauthorized, traversal, symlink-escape,
   and post-lease-mutated references cannot dispatch or collect as valid.
   **Verify:** each negative fixture yields no valid result/pass and names the
   affected slot plus a safe recovery action.

6. **No silent zero-slot outcome.** If routing selects at least one lens,
   preprocessing either materializes exactly those leased slots or stops in an
   explicit non-success state naming stage and lens. Size must never produce a
   completed zero-slot review. **Verify:** mutation/failure injection proves
   `selected > 0` implies `dispatched == selected`, except for a named
   fail-closed terminal error that cannot be reported as completion.

7. **Dashboard is presentation-only.** Dashboard size, pagination, and inline
   rendering cannot affect routing, leasing, view construction, dispatch, or
   collection. **Verify:** small and large/paged dashboards from one canonical
   review retain equal revision, routing, slot, finding, and gate values.

8. **Selection is deterministic.** A pinned target, revision, routing decision,
   lens, and budget produce byte-stable summaries/references despite input
   ordering. **Verify:** repeated and permuted runs produce identical view
   fingerprints and reference manifests.

9. **Useful context survives pressure.** After mandatory fields are reserved,
   deterministic lens relevance—not first-N truncation—selects summaries and
   references; omitted optional material is inventoried. **Verify:** a large
   mixed fixture gives every lens required evidence classes plus an explicit
   referenced/omitted manifest.

10. **Collection verifies the dispatched contract.** Results with a different
    view fingerprint, reference manifest, lease, target, producer, or revision
    are rejected. **Verify:** copied, wrong-view/reference/revision/slot,
    stale-lease, and valid-large-review fixtures.

11. **Small reviews stay compatible.** Existing fitting reviews retain routing,
    evidence, schema, findings, and gate behavior. **Verify:** golden fixtures
    remain unchanged or use an explicit compatible schema migration, with no
    weakened assertions.

12. **Resource use stays bounded.** Preparation does not copy full canonical
    payloads per slot or scale model input with dashboard size. **Verify:** a
    many-lens multi-megabyte fixture has one governed copy per unique overflow
    artifact, bounded diagnostics, per-view bytes within budget, and aggregate
    inline bytes bounded by the sum of slot budgets.

## Non-functional requirements

- `security`: Only authorized, target-bound, digest-verified references cross
  the boundary; traversal, symlink escape, mutable aliases, and secret-bearing
  diagnostics fail closed.
- `architecture`: Canonical state, dashboard projection, bounded views, and
  referenced evidence have separate responsibilities under one ReviewKernel
  authority for routing, leases, provenance, and collection.
- `data-safety`: Evidence is revision-immutable, stored once, integrity checked
  before dispatch/collection, and never silently dropped or overwritten.
- `sre`: Large inputs complete with bounded behavior or a named fail-closed
  error; selected work cannot disappear into false zero-slot completion.
- `integrability`: View/reference schema changes are versioned and remain
  compatible with shipped adapters, fitting reviews, and canonical collection.
- `performance`: Processing/storage scale with unique evidence plus configured
  slot budgets, not dashboard size multiplied by lens count.
- `privacy-compliance`: References and diagnostics expose minimum metadata and
  exclude personal absolute paths, credentials, secrets, and unrelated data.

## Contract handoff

- `scope_paths`: `taskplane/review.py`, `taskplane/review_evidence.py`,
  `taskplane/evidence.py`, `taskplane/runtime_eval.py`, `taskplane/loop.py`,
  `taskplane/dashboard.py`, `taskplane/tests/**`, `specs/spec.md`.
- `out_of_scope`: catalog/applicability redesign, graph semantics, dashboard
  visual redesign, removal of limits, unverified external transports, release
  work, and unrelated review/evaluation behavior.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependencies: none.
- contracts: `contract:review-kernel-slot`,
  `contract:review-kernel-scoped-view`,
  `contract:review-kernel-evidence-reference`,
  `contract:review-dashboard-projection`.

This security-sensitive cross-module protocol change requires Design before
Build. There are no blocking Product questions.
