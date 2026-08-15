# R-0006 plan — capability-bound host parity

This plan realizes approved Design Contract fingerprint
`3e7ceb20fe28d9194c7c2ca1bfca334a34236de6b337d24a5d0c596f9872b7d2`
without changing its authority boundaries. Host-specific transports may vary,
but hook execution, ReviewKernel collection, evaluator output, submission
validation, dispatch receipts, and telemetry converge on the five approved
contracts. Workers never gain gate, approval, state-advance, or contract-clear
authority.

This is a recovery replan after t1 capability implementation was committed and
its 10 focused tests passed. Evaluation then exposed one ordering blocker: the
ReviewKernel scoped view exceeds 16,384 bytes. No feature scope is added. The
bounded review-evidence correction is folded into t1 so its foundation is
complete before later transport adoption and evaluation resume.

## Bounded impact and graph policy

The one required impact derivation used all 24 approved modules in one
comma-separated `--files` value. It returned 21 impacted nodes, no unknown
modules, and affected requirements R-0001, R-0005, and R-0006, with R-0002 as
a dependent requirement. The result reported `truncated=true` but
`depth_truncated=false`; the named stops are the approved one-hop requirement
boundary, not an implicit request to inspect or change dependent requirements.

Every task therefore copies the engine policy unchanged: three local hops,
`contract-only` boundaries, one contract hop, and one requirement hop. The
bounded result returned `unknown=[]`; nevertheless, this repository's
stage-wave convention uses `new_modules` as exact approved graph-module
ownership, so the 24 proposed ids are distributed once across their matching
tasks. This does not declare 24 new files: it binds execution ownership to the
approved design graph. The two genuinely new designed files remain explicit
task scopes and own their approved edges into existing named contract nodes.

Collectively the tasks cover all 24 proposed modules through exact,
non-overlapping `new_modules` ownership, all 38 proposed edges, all five exact
contract ids, the depth policy, and all 14 verbatim acceptance criteria. Module
ownership is t1=3, t2=6, t3=8, t4=5, and t5=2. Edge ownership is exclusive:
t1 owns 4, t2 owns 8, t3 owns 17, t4 owns 6, and t5 owns 3.

## Ordered repair batch

1. **t1 — capability and bounded review foundation (recovery).** Preserve the
   committed immutable, source-attributed capability snapshot and truthful
   onboarding work whose 10 focused tests already passed. Before evaluation
   resumes, bound the ReviewKernel scoped view below 16,384 bytes in
   `review_evidence.py`. The task's one focused command covers both capability
   and review-evidence files; it must retain fail-closed capability behavior and
   the canonical immutable envelope rather than weakening evidence.
2. **t2 — canonical output and lifecycle.** Put native and bridge hook entry
   points behind the same exactly-once claim/replay boundary, add the canonical
   output validator, bind active slots to immutable submission expectations,
   and make Stop/SubagentStop observational and submission-aware. Execute/Fix
   workflows adopt the schema receipt without gaining lifecycle authority.
3. **t3 — transport adoption.** Move Claude review/evaluate workflows, bounded
   evaluator adapters, loop dispatch, live-skill dispatch, roles, and runtime
   guidance onto the same ReviewKernel/output/routing contracts. Resume identity
   remains target/context/view/lease/schema/slot/producer/revision; a mismatch
   gets at most one fresh attempt and never reaches a sink.
4. **t4 — telemetry and pinned failures.** Normalize provider usage without
   cache double counting, persist only bounded redacted lifecycle facts, refresh
   only the current tp-go fingerprint, and compose runtime guidance with the
   engine's unproven-criterion refusal. Its single targeted command covers the
   observability/provider matrices and the three known failure clusters,
   including stale-scenario mutation behavior.
5. **t5 — truthful guidance and final validation.** Update the five approved
   host-facing references only after behavior is fixed, then run the unchanged
   full test suite once. The Python 3.10/3.11/3.12, macOS, Windows, packaging,
   manifest, release-history, docs, hook, and dispatch-parity floors remain CI
   responsibilities and may not be removed, skipped, xfailed, loosened, or
   de-gated.

## Validation budget

The already-completed baseline is not repeated: it recorded 7 failed
assertions in three clusters, 2774 passed, 2 skipped, and 861 subtests. Build
work is one coherent serialized repair batch. The audited recovery keeps the
already-implemented t1 capability work and adds only its bounded review-evidence
prerequisite. Each task runs only its named focused test command; the failing
scenario/evidence clusters run once in t4.
Only t5 runs `python3 -m pytest taskplane/tests -q`, after all implementation
and documentation changes are complete. No executor should run the full suite
as an edit-by-edit loop.

Acceptance ownership is complete and non-overlapping:

- t1: truthful personal/managed trust, policy, load, and effective-path state;
- t2: exactly-once/idempotent hooks plus fail-closed, non-mutating submission
  stop enforcement;
- t3: ReviewKernel parity and resume, strict evaluation output fallback, and
  effective host model/effort routing;
- t4: bounded lifecycle records, provider-correct tokens, and the pinned CI
  repairs;
- t5: the unchanged cross-version/platform CI and governance floors.

## Risks, rollout, and rollback

The principal risks are false capability authority, duplicate hook races,
stale workflow resume, foreign or unsupported dispatch arguments, destructive
leak recovery, provider token double counting, and regression-floor erosion.
The focused matrices precede transport adoption and strict enforcement so each
failure direction is proved before the next dependency starts.

Rollout remains additive: readers/validators and shadow records first;
exactly-once selection and truthful onboarding second; workflow/evaluator and
submission-aware enforcement third; strict explicit routing, pinned repairs,
docs, and the final unchanged matrix last. Existing ReviewKernel v2 identities
finish unchanged or are explicitly cancelled and restarted.

Rollback may change only transport selection. It may not accept prose or
unvalidated JSON, duplicate side effects, unsupported explicit routing,
unproved submissions, translated in-flight leases/revisions, fabricated token
zeros, or worker-owned clear/gate/approval. A bridge fallback is valid only when
trusted, policy-permitted, loaded, and still protected by exactly-once claims;
otherwise the affected governance operation fails closed.
