# R-0009 plan — host-parity governed PR review

This plan implements the human-approved Design Contract in
`design/contract.json`, identified by the approved design fingerprint
`74c41bf3d111c14a496bfb642aa1d6f9e3ba47c223b924a4bc0a1f0c1d4b05e7`.
The implementation-loop payload's `design=null` is stale and does not weaken
that authority. The current on-disk artifact SHA-256 is
`8ce27b4323a28bd3d055a41b3a31a8489f20e0134440bdcb84aea912af794a66`.
The previous R-0008 plan remains unchanged in
`plan/r0008-plan.pre-r0009.md` and `plan/r0008-tasks.pre-r0009.json`; earlier
R-0006/R-0007 archives also remain untouched.

## Bounded impact and complete Design coverage

The required single graph-impact call covered every designed implementation
surface with local depth 3, `contract-only` boundaries, contract depth 1, and
requirement depth 1. Its graph fingerprint
`cb14d3ebb580f2ccaa86d0a978933b2865b9c4e040c74ded737210273b65e38e`
matches the approved Design baseline. It returned 29 impacted nodes and no
unknown modules. Because the conformance gate reads explicit graph declarations
from `new_modules`, the task union lists every exact proposed module and
contract node there even when an existing file is not literally new.

Collectively the six tasks own all 25 proposed graph nodes, all eight exact
contracts, all 19 proposed edges, the full depth policy, and AC1–AC16 verbatim.
Each task has one runnable focused test command. Source scopes are disjoint;
the final integration task owns the generic `taskplane/tests` graph node and
the cross-host repository surfaces, so earlier contract owners can work in
isolated worktrees without colliding.

## Risk-first delivery waves

1. **DoR and criterion ledger.** Discover all eight PR/specification source
   classes with identity, revision, freshness, access state, provenance, and
   contradictions before routing. Classify objectives, criteria, directives,
   constraints, and context; clarify at most once only when the ambiguity can
   change routing, validation, or verdict. Unproven and unjustified n/a remain
   non-approvable. This owns AC2–AC5.
2. **Session, consent, and host validation.** Normalize one complete-review
   consent scope without magic phrases; re-consent only for the six authority
   changes or final disposition. Keep host adapters transport-only. Dynamic
   validation runs in a disposable push-disabled copy, preserves pinned and
   remote fingerprints, and records submitted-versus-sandbox outcomes. This
   owns AC6, AC7, AC14, and AC15.
3. **Partial revision kernel.** Freeze valid results immediately into immutable
   provisional revisions, inventory every missing/invalid slot, disable
   approval, and supersede rather than mutate when collection becomes
   canonical. Zero routed/collected work cannot synthesize success. This owns
   AC8.
4. **Mechanical repair and affected-only retry.** Repair only lease-derived
   declaration fields with canonical before/after bytes, derivation source,
   versioned rule, actor, and equivalence proof. Any substantive or
   unverifiable change rejects repair and retries only affected slots, at most
   twice; valid artifacts stay byte-identical and collection remains
   idempotent. This owns AC9–AC10.
5. **Lossless artifact transaction.** Derive JSON, Markdown, and HTML from one
   immutable revision, write content-addressed temporary objects, parse and
   compare semantics, then atomically commit one manifest. No partial set is
   advertised. The 120-finding round trip and small-review migration audit own
   AC12 and AC16.
6. **Inline host parity and scale.** Project <=14,000-byte accessible pages
   with stable revision/provenance, focus, filters, keyboard operation, and
   signed action receipts while exports remain lossless. A final Claude/Codex
   semantic golden strips transport metadata and proves the entire canonical
   model equivalent at 26 lenses, 120 findings, >=126 KiB Markdown, >=342 KiB
   HTML, and multi-megabyte evidence. This owns AC1, AC11, and AC13.

The dependency chain follows the approved authority order: DoR → session →
partial revision → repair/retry → artifacts → inline parity. This is not a
license for full-suite polling: each owner runs its one focused command, then
the final host-parity task performs the bounded integrated proof.

## Runnable validation map

| Owner | Criteria | Command |
|---|---|---|
| DoR/criteria | AC2–AC5 | `python3 -m pytest -q taskplane/tests/test_review_dor.py` |
| Session/consent/dynamic | AC6, AC7, AC14, AC15 | `python3 -m pytest -q taskplane/tests/test_review_session.py` |
| Partial revisions | AC8 | `python3 -m pytest -q taskplane/tests/test_review_partial_revision.py` |
| Repair/retry | AC9–AC10 | `python3 -m pytest -q taskplane/tests/test_review_recovery.py` |
| Artifact transaction | AC12, AC16 | `python3 -m pytest -q taskplane/tests/test_review_artifacts.py` |
| Inline/cross-host/scale | AC1, AC11, AC13 | `python3 -m pytest -q taskplane/tests/test_review_host_parity.py` |

## Principal risks and controls

- **Host policy becomes a second source of truth.** Canonicalize semantics in
  ReviewKernel; adapters expose only prompts, consent, observed tools, and
  interactive receipts. Cross-host goldens ignore transport metadata only.
- **DoR evidence is missing, stale, or contradictory.** Record attempted and
  available sources before routing, surface four-state failures, bind every
  criterion to provenance and revision, and block approval when unproven.
- **Partial failure discards valid findings.** Freeze each valid slot once in
  an immutable provisional revision; supersede explicitly and never rewrite a
  valid result during retry.
- **Mechanical repair invents substance.** Allow only lease-derived declaration
  fields with equivalence proof. Changes to findings, evidence, target,
  producer, slot, or unverifiable identity always rerun the affected producer.
- **Artifacts or inline pages truncate review truth.** Publish all formats
  atomically from one semantic model; pages may omit bytes but not rows and
  retain revision-bound references to complete exports.
- **Dynamic review mutates the PR or remote.** Execute only in a disposable
  push-disabled copy, compare pre/post fingerprints, record sandbox delta as
  evidence, and keep submitted and sandbox verdicts distinct.
- **Approval/render/retry loops recur.** Bound consent, ambiguity, repair, and
  retries numerically; stable failure states never become declined choice,
  zero findings, completion, or pass.

## Rollout and rollback

Ship additive versioned schemas behind `TASKPLANE_CANONICAL_PR_REVIEW`. First
dual-run deterministic fixtures and small goldens without changing authority;
then enable DoR/consent, provisional collection, repair/retry, artifact
transactions, inline pages, and finally host parity. Rollback disables new
sessions, finishes issued leases with retained readers/resolvers, and publishes
legacy complete projections. Immutable revisions and artifacts are never
rewritten or deleted. New Python boundaries remain strictly validated, the
wheel/package floor stays unchanged, and no test removal, skip, xfail,
threshold reduction, or weakened governance assertion is permitted.
