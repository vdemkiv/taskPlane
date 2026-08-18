# Specification — close R-0009 final approval blockers

## Problem

R-0009 is implemented but cannot reach truthful final approval while two
confirmed gaps remain: structured acceptance criteria supplied separately from
`requirement.text` can disappear from canonical DoR and the criterion ledger,
and disposable dynamic validation can bypass its push-disabled claim by using
an explicit remote URL with `git push --no-verify`.

## Users and context

This focused follow-up serves engineers completing the approved R-0009
host-parity governed PR review. It preserves R-0009's scope and behavior and
closes only the two blockers independently observed at final evaluation.

## In scope

- Preserve structured acceptance criteria supplied outside `requirement.text`
  through canonical DoR extraction, criterion-ledger generation, evidence
  evaluation, rendered results, and approval gating.
- Enforce the disposable validation sandbox's no-push boundary for the real
  validation process tree, including explicit remote URLs and `--no-verify`.
- Add focused positive, negative, and end-to-end regression evidence for these
  two conditions.

## Out of scope

- Any new R-0009 feature, workflow, UI, artifact format, lens, approval rule,
  DoR source, or dynamic-validation capability.
- Changing acceptance-criterion meaning, inventing criteria, or accepting
  malformed/unauthorized criteria.
- Editing or pushing the reviewed PR, enabling network publication, or
  weakening disposable-copy isolation.
- Refactoring unrelated review, collection, dashboard, command-runtime, host-
  adapter, workflow, lens, graph, release, or marketplace behavior.
- Reopening passed R-0009 acceptance criteria or broadening its implementation
  surface beyond what these two blockers require.

## Acceptance criteria

1. **Structured criteria survive canonical DoR.** When a review request carries
   valid structured acceptance criteria outside `requirement.text`, canonical
   DoR contains every criterion exactly once, in source order, with its source
   identity and target/revision provenance. **Verify:** a fixture with empty or
   unrelated `requirement.text` and multiple structured criteria asserts exact
   canonical DoR equality and provenance.

2. **Structured criteria reach the criterion ledger and outputs.** Every
   structured criterion preserved by DoR appears exactly once in the canonical
   criterion ledger and in the approval evidence consumed by JSON, Markdown,
   HTML, and inline projections. **Verify:** a review using only structured
   criteria round-trips through all projections with matching criterion ids,
   text, status, evidence, and counts.

3. **Approval cannot lose structured criteria.** A structured criterion that
   is failed, unproven, or missing valid evidence prevents approval under the
   same rules as a criterion extracted from `requirement.text`; a passing
   criterion permits approval only when every other R-0009 gate is satisfied.
   **Verify:** pass, fail, unproven, and missing-evidence fixtures assert the
   corresponding canonical gate reason and no false approval.

4. **Mixed sources remain deterministic.** When the same criterion is present
   in structured input and `requirement.text`, it is represented once without
   losing authoritative source provenance; distinct criteria from both sources
   are preserved. **Verify:** duplicate, reordered, distinct, empty-text, and
   malformed structured-input fixtures assert deterministic identity, order,
   provenance, and fail-closed rejection where appropriate.

5. **Explicit-URL push is blocked.** From a registered disposable dynamic-
   validation sandbox, `git push --no-verify <explicit-url> <refspec>` cannot
   transmit repository data or update any local or remote destination, even
   when it bypasses configured remotes and hooks. **Verify:** an isolated bare
   destination reachable by explicit URL remains byte/ref unchanged and the
   command returns a stable blocked result.

6. **The entire validation process tree inherits the boundary.** Direct
   validation commands and their child, grandchild, shell, package-script, and
   executable-wrapper processes cannot push with explicit URLs, alternate Git
   config, `--no-verify`, or hook overrides. **Verify:** process-tree fixtures
   attempt each form and assert no destination mutation, with the responsible
   validation run/sandbox identified.

7. **The boundary is fail closed and truthful.** If no-push isolation cannot be
   established or verified before execution, dynamic validation does not run
   and cannot be recorded as executed/pass. A blocked push is reported as an
   isolation enforcement event, not as a product build/test failure.
   **Verify:** unavailable, tampered, escaped-working-directory, and blocked-
   attempt fixtures assert named non-success states, intact source/remote refs,
   and no false dynamic evidence.

8. **Permitted validation remains usable.** Normal read-only builds, tests,
   dependency reads, and local disposable-file writes continue to work inside
   the registered sandbox, while the original checkout and remotes remain
   unchanged. **Verify:** the focused R-0009 dynamic scenario passes its real
   checks, records process-tree isolation evidence, and proves source and
   destination refs are unchanged before and after.

9. **No R-0009 regression.** Existing R-0009 cross-host, DoR, criterion,
   partial-collection, repair, inline, artifact, large-review, and sandbox
   behavior remains unchanged except for closing these blockers. **Verify:**
   the focused new tests and complete R-0009 regression suite pass without
   removed, skipped, xfailed, loosened, or reclassified assertions.

## Non-functional requirements

- `security`: The no-push policy is enforced at the real validation process-
  tree boundary and cannot be bypassed by explicit URLs, `--no-verify`, Git
  configuration, hooks, wrappers, or descendant processes; failure to prove
  isolation blocks execution.
- `architecture`: Structured acceptance is one canonical input to DoR, ledger,
  projections, and approval, and validation isolation is one host-neutral
  process-tree contract rather than host-, command-, or remote-name checks.
- `data-safety`: Neither the reviewed checkout nor any local/remote Git
  destination may change during disposable validation; structured criteria and
  evidence cannot be silently dropped, duplicated, or reordered.
- `sre`: Isolation establishment, blocked attempts, and criterion-propagation
  failures produce stable actionable states with bounded execution and no
  partial or false-success record.
- `integrability`: Existing R-0009 request, DoR, ledger, result, artifact, and
  sandbox consumers remain compatible; structured inputs use their existing
  contract rather than a new parallel representation.

## Contract handoff

- `scope_paths`:
  - `taskplane/review.py`
  - `taskplane/review_dor.py`
  - `taskplane/review_evidence.py`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`: every R-0009 behavior except structured-criterion continuity
  and real process-tree no-push enforcement for disposable validation.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependency/change context: `R-0009`.
- contracts:
  - `contract:review-dor-evidence`
  - `contract:review-validation-sandbox`

This is a narrow security and canonical-evidence correction. It does not add a
new product workflow or reopen the approved R-0009 design beyond the two named
blockers. There are no blocking Product questions.
