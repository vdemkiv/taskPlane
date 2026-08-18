# R-0008 plan — size-safe ReviewKernel dispatch

This plan realizes the approved reference-first Design Contract in
`design/contract.json` (SHA-256
`3f0b049b1a179ab580e7c41d632714991e15d0874ab1950647805d0d15b9a046`).
Its graph baseline fingerprint
`5e86f66a9adfcd871703b3fa175325ec890d93843b305c8636a9560d83f1fcd5`
matches the single bounded impact projection. The prior R-0007 plan is
preserved unchanged in `plan/r0007-plan.pre-r0008.md` and
`plan/r0007-tasks.pre-r0008.json`; the older R-0006 artifacts also remain
untouched.

## Impact and design fidelity

The required one-time graph projection covered all seven designed local
surfaces with local depth 3, `contract-only` boundaries, contract depth 1, and
requirement depth 1. It returned 30 impacted nodes and no unknown modules. The
gate's graph-coverage interface uses `new_modules` as its explicit declaration
of approved graph nodes even when those nodes already exist, so the tasks list
every exact `graph.proposed_modules` value there; this is coverage metadata,
not a claim that existing files or contracts are newly created. The original
three tasks collectively cover every approved module, all 13 proposed edges,
all four exact contracts, the depth policy, and all 12 acceptance criteria
verbatim. The approved security/integrity amendment adds one bounded final
task without reopening or invalidating credited work.

## Risk-first delivery

1. **Reference-first projection.** Extend the incumbent evidence authority,
   not a parallel store. Add v2 target/revision-bound immutable section
   references and deterministic v3 scoped views. Reserve the complete inline
   identity spine and omission manifest before lens-relevant content; emit
   exact canonical JSON only when it fits, otherwise one verified reference.
   Fail `review_scoped_view_budget_impossible` rather than truncate provenance
   or drop a lens. Focused tests cover the 16,384-byte edge, 4–8 MiB input,
   100 input permutations, per-lens pressure, deduplication, aggregate bounds,
   and the seven reference rejection classes.
2. **Slot conservation and collection integrity.** Prepare and verify every
   routed slot, then enforce non-zero `selected == prepared == dispatched ==
   collected`. Bind view, reference manifest, lease, target, producer,
   revision, lens, and slot through dispatch and collection. Any preparation
   or provenance mismatch returns a named fail-closed error before producer
   execution or canonical publication. The evidence, runtime-evaluation, and
   loop consumers may project machine-owned facts but may not remap them.
3. **Dashboard independence and compatibility.** Render only sealed canonical
   collection/gate state. Vary dashboard size and pagination without changing
   routing, leases, results, findings, or gate fingerprints. Golden fitting
   fixtures must retain equivalent routing, evidence semantics, findings, and
   gates through v3.
4. **Inline integrity and untrusted-evidence guard.** Preserve t1/t2 as passed
   and credit the completed t3 dashboard behavior, then make one bounded pass
   over `review.py` and `review_evidence.py`. Canonically verify that
   `inline_sections`, referenced sections, and omission inventory are complete
   and pairwise disjoint for the pinned envelope; reject duplicates, gaps,
   undeclared sections, or contradictory omission state before dispatch and
   collection. Treat PR-controlled diff, requirement, and change evidence as
   untrusted data behind a mandatory delimiter. Deterministically detect,
   obstruct, and flag prompt-injection attempts without treating the embedded
   text as reviewer instructions. The flag is safe provenance/evidence, never
   permission to silently discard the underlying code-review data.

The order is deliberately serial: collection cannot safely consume references
until the projector/verifier contract is deterministic, and dashboard
independence cannot be proven until canonical slot conservation is sealed.
Implementation-file scopes remain tight; the exact `taskplane/tests` graph
module is assigned to the final compatibility task for mechanical Design
coverage, and the serial dependencies prevent its shared test surface from
creating concurrent edits.

## Runnable validation map

| Focus | Acceptance coverage | Command |
|---|---|---|
| Reference-first bounds, determinism, relevance, deduplication, reference safety | 7 criteria | `python3 -m pytest -q taskplane/tests/test_review_reference_first_projection.py` |
| Large/small slot parity, zero-slot prevention, collection tamper matrix | 3 criteria | `python3 -m pytest -q taskplane/tests/test_review_slot_conservation.py` |
| Dashboard metamorphism and fitting-review compatibility | 2 criteria | `python3 -m pytest -q taskplane/tests/test_review_dashboard_independence.py` |
| Inline completeness/disjointness/omissions and untrusted-evidence injection defense | Approved amendment | `python3 -m pytest -q taskplane/tests/test_review_inline_integrity.py taskplane/tests/test_review_untrusted_evidence.py` |

The integrity suite independently tampers view, manifest, lease, target,
producer, revision, and slot. The reference suite rejects missing, stale,
mismatched, unauthorized, traversal, symlink-escape, and mutated artifacts at
both preparation and collection boundaries. Diagnostics contain only safe
digests/counts—never paths, source text, credentials, or secrets.

The amendment tests cover missing, duplicated, intersecting, reordered, and
undeclared inline/reference/omission identities; canonical byte/fingerprint
verification; delimiter presence at every producer boundary; benign text that
resembles instructions; direct and obfuscated override/exfiltration attempts;
and fail-closed detect/obstruct/flag behavior that preserves safe evidence for
the collector.

## Risks and controls

- **Mandatory provenance exceeds the budget.** Reserve and measure the spine
  first; fail explicitly rather than truncate JSON or erase selected work.
- **Reference substitution or escape.** Resolve from kind/fingerprint inside
  the canonical store, reject symlinks/aliases/traversal and authorization
  mismatch, then verify digest, byte length, semantic fingerprint, target, and
  revision before returning the named section.
- **Evidence multiplies by lens count.** Store once per unique digest and cap
  aggregate inline bytes at `16,384 × prepared slots`.
- **Collection publishes mixed provenance.** Re-resolve references and compare
  every identity before canonical publication; any mismatch fails closed.
- **Presentation affects semantics.** Keep dashboard pagination downstream of
  the sealed collection and gate fingerprint, with no routing input edge.
- **PR-controlled evidence impersonates control instructions.** Delimit every
  untrusted evidence class, interpret it only as review data, detect and
  obstruct override/exfiltration patterns, and emit a bounded safe flag for
  canonical collection.

## Rollout and rollback

Roll out behind `TASKPLANE_REFERENCE_FIRST_VIEWS`: first compare fitting-review
parity, then make v3 authoritative while retaining v2 readers for active
leases. Rollback disables new v3 preparation but continues collecting already
issued v3 leases. Immutable artifacts need no migration or deletion. Retrying
rebuilds views from the pinned canonical envelope without re-deriving review
facts.
