# Stateless phase pickup retrospective

Date: 2026-09-04. Delivery: Taskplane 2.19.1, PR #15.

## Outcome and boundaries

Design, Plan, and Build can pick up committed repository handoffs in a fresh
checkout. Same-phase resume schedules remaining work; next-phase pickup starts
the next phase's obligations. Build submission uses the existing BUILD-C path.
The implementation has one handoff owner and one Build pickup coordinator,
with adapters to existing phase owners.

The original pickup evaluation is pinned to `444002f` and passed 165 tests,
including public fresh-clone transitions, real committed Build submission,
authority ancestry, forged-evidence refusal, and publication recovery. Later
release and integration corrections require their separately recorded evidence.

## What caused delay

- The saved legacy loop still addressed the earlier R-0004 Plan, while this
  delivery addressed the R-0001 stateless pickup fix. Approvals and work from
  one cannot truthfully advance the other.
- Early tests did not prove the complete public submission path. Evaluation
  exposed caller-controlled Build evidence, missing public startup data, and
  completed work being scheduled again.
- A pure authority-chain check required every approval to name the latest
  source commit, contradicting the repository verifier's valid ancestor chain.
- The final merge included a graph helper from the baseline. Its duplicate-ID
  handling silently omitted a request and could falsely report full coverage.
- The initial verification matrix omitted strict typing. Hosted quality checks
  caught annotation/import errors in the two new modules.
- The full suite also caught stale Design test selectors, an outdated refusal
  message assertion, and missing explicit subprocess encoding. These were
  corrected without changing acceptance criteria or weakening refusals.
- The dependency check was omitted from the early matrix. One backward import
  from stage entities to Design contracts joined the existing 17-module and
  7-module cycles into a 44-module cycle. Deferred imports do not remove an
  architectural dependency; shared validation belongs in the dependency-free
  handoff owner.

## Corrections and reusable decisions

1. Use repository handoffs as continuation authority. Keep phase work and
   approval lineage separate. This decision is also recorded in the existing
   Taskplane knowledge store as decision `0001`.
2. Prove complete public journeys before calling an integration ready. Raw
   caller evidence cannot stand in for BUILD-C output; fresh clones and empty
   private homes are part of the regression evidence.
3. Reject duplicate request identities before reading source. The regression
   covers either input order and collisions with generated IDs.
4. Run strict typing, dependency, and release checks alongside focused tests.
   Package tests read the manifest version instead of copying a version string.
5. Keep each correction local to its owner. These corrections reuse existing
   validation and BUILD-C boundaries and add no lifecycle engine.
6. Keep startup transport behind the stage API and human attribution checks
   in the shared handoff owner. The final correction restores the unchanged
   dependency policy exactly: cycles of 17 modules/49 edges and 7 modules/13
   edges. Synthetic actors now fail earlier, while normal human decision
   input normalization and the public exception contract remain compatible.

## Recorded delivery evidence

The [review evidence index](reviews/stateless-pickup-2.19.1/README.md) links the
original PASS, the later FAIL, each bounded correction judgment, and final
Engineering review. Earlier producer reports are preserved byte-for-byte;
none is silently rewritten to name a later source revision. GitHub checks on
the final PR head are a separate mandatory merge condition.

## Workflow limitations retained honestly

The native standalone review opener rejected the 530,022-byte candidate diff
at its 400,000-byte bound, returning `canonical diff derivation failed`.
The original evaluator published its own commit-bound report, and final
Evaluate/EM consume explicit evidence. No successful native ReviewKernel gate
is inferred from those reports.

The unrelated R-0004 loop remains at Plan. This retrospective does not mark
that work completed or invent a terminal loop receipt. Source merge, version
metadata, review evidence, and retrospective publication are separate from a
release tag, Marketplace publication, or installation.

## Measures

Known results are recorded with their exact candidate identities in the
evaluation and EM reports. Missing cost and session-meter totals are unknown,
not zero; no aggregate is reconstructed from conversation history.
