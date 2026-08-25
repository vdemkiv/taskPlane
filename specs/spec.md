# Stateless `tp pickup` front door

## Problem

Taskplane has a proven BUILD-C execution path, but using it currently depends on
private loop/runtime state. A previously approved Design Contract that is
already on a repository shelf needs a small, trustworthy front door that can
begin and resume one bounded delivery directly from repository facts.

## Users and context

- A human operator has an already signed and approved Design Contract in the
  repository and explicitly selects it with `tp pickup <design-contract>`.
- The checkout is at the exact source SHA named by that authority and is clean.
- The operator wants only the selected contract element delivered, with the
  existing BUILD-C checkpoint and merge-on-green guarantees, without starting
  or inheriting a Taskplane loop.
- The target release is Taskplane 2.17.20. Final release review may prepare
  evidence, but pushing, tagging, publishing, or changing `origin/main` remains
  a separate human authority decision.

## In scope

1. Provide the public command `tp pickup <design-contract>` for one signed,
   repository-resident Design Contract element.
2. Fail closed before execution unless the checkout is clean, its exact SHA is
   the authorized SHA, the repository Design Contract and its approval are
   authentic and current, and the required engine receipt is valid for that
   same SHA and Design fingerprint.
3. Keep the existing hook security layer unchanged. Pickup must neither bypass
   it nor claim stronger hook evidence than the host provides.
4. Remain stateless with respect to Taskplane orchestration: pickup creates no
   run, track, claim, lease, wave, or equivalent private coordination state and
   does not require an active loop.
5. Bound the delivery to a micro-plan for the one selected contract element.
   No unrelated Design, Plan, requirement, program, or backlog content may be
   imported into that micro-plan.
6. Enter the existing direct-scope assignment and BUILD-C acceptance-checkpoint
   contracts, and permit integration only when the exact assigned revision has
   a valid green engine checkpoint and the existing merge boundary accepts it.
7. Use manual checkpoint discipline: execute and record one acceptance
   criterion at a time. The production behavior and the tests proving each
   acceptance criterion land in the same implementation commit.
8. Commit durable pickup receipts below `exports/`. Receipt identity is keyed
   by the exact repository SHA plus the Design evidence fingerprint, is
   content-addressed/tamper-evident, records the bounded checkpoint lineage and
   merge outcome, and contains enough repository-resident evidence for another
   checkout at the same SHA to resume without any private-store handoff.
9. On the final clean SHA, run quick security and QA review concurrently, then
   an engineering-manager review. Stop after those results for explicit human
   push authority.
10. Update shipped version metadata and release notes to 2.17.20 only after the
    functional acceptance path is green; do not push, tag, publish, or mutate
    `origin/main` within this delivery authority.

## Out of scope

- Phase 0, E0, every R-0011 feature or redesign item, and every other R-0013
  intake, design, plan, or delivery item.
- Replaying completed T01-T08a or adopting any paused R-0013 content.
- Changing the host hook, hook policy, security interception, enforcement
  strength, or hook receipt semantics.
- Changing, replacing, or routing through the legacy loop path; changing its
  run/track/claim/lease/wave lifecycle or its behavior.
- Creating a second checkpoint engine, merge implementation, worktree manager,
  Design approval system, requirement flow, or private receipt store.
- Broad Design or Plan work, automatic Phase 0/E0 evaluation, or an automatic
  full/deep/serial-all/all-lens review sweep.
- Push, tag, release, marketplace publication, package publication, or any
  direct mutation of `origin/main` before explicit human authority.
- Changes to retained worktrees, paused branches, dormant runtimes, or any
  Taskplane home other than the isolated pickup delivery home.

## Acceptance criteria

1. **AC1 — a shelf contract runs end-to-end via pickup with zero run/track
   state (trace evidence).** Verify with an approved signed shelf fixture and a
   trace assertion that covers pickup through the existing BUILD-C checkpoint
   and green integration boundary, while proving no run, track, claim, lease,
   wave, or equivalent orchestration record was created.
2. **AC2 — cold start on a fresh checkout at the same SHA to first executing
   checkpoint in <2 minutes.** Verify with a wall-clock integration test from a
   new checkout, empty private Taskplane home, exact authorized SHA, and no
   warmed process or inherited runtime state.
3. **AC3 — severed-edge tests: cutting the pickup→build_c entry fails; legacy
   loop path untouched and its suite still green.** Verify with a mutation or
   seam test that removes the pickup-to-BUILD-C edge and must fail, plus the
   unchanged legacy-loop selectors proving their existing path remains green.
4. **AC4 — a second checkout resumes from repo-resident receipts with no
   private-store handoff.** Verify by completing at least one manual criterion,
   discarding access to the first checkout's private Taskplane home, and
   resuming from a second checkout using only Git-tracked Design authority and
   `exports/` receipts at the same SHA/fingerprint identity.
5. **AC5 — full suite on the final SHA: no new failures.** Verify with
   `python -m pytest taskplane/tests -q` on the final clean commit and compare
   the result with the exact baseline; any new failure blocks release review.

## Required failure behavior

- Dirty bytes, untracked product files, SHA drift, stale Design bytes, missing
  or invalid approval, Design-fingerprint drift, missing or mismatched engine
  receipt, forged caller evidence, receipt collisions, or an already-consumed
  incompatible outcome must refuse before execution or integration by naming
  the failed boundary.
- A failed acceptance checkpoint remains durable evidence but never authorizes
  the next criterion or merge.
- Interruption must not create private handoff state, erase prior receipts,
  overwrite evidence for another SHA/fingerprint pair, or affect the legacy
  loop.

## Release evidence and authority boundary

After AC1-AC5 are green on one final clean commit, obtain one quick security
pass and one quick QA pass concurrently against that exact SHA, then obtain an
engineering-manager review against the same SHA and evidence set. Report the
2.17.20 release candidate and stop. A human must separately authorize pushing
`origin/main`, creating a tag, publishing, or any release-side mutation.

## Contract handoff

### Canonical boundary ids

```yaml
contracts:
  - contract:pickup.stateless-front-door
  - contract:design.approved-contract
  - contract:build-c.direct-assignment
  - contract:build-c.acceptance-checkpoint
  - contract:repository.merge-on-green
  - resource:exports.pickup-receipts
```

### Scope

```yaml
scope_paths:
  - taskplane/tp.py
  - taskplane/pickup.py
  - taskplane/build_c.py
  - taskplane/checkpoint.py
  - taskplane/design_contract.py
  - taskplane/repository.py
  - taskplane/storage.py
  - taskplane/taskplane_lite.py
  - taskplane/tests/test_pickup.py
  - exports/**
  - README.md
  - CHANGELOG.md
  - .codex-plugin/plugin.json
  - .claude-plugin/plugin.json
  - .claude-plugin/marketplace.json
out_of_scope:
  - hooks/**
  - .taskplane/codex-hook.py
  - taskplane/loop.py
  - plan/**
  - backlog/**
  - design/**
  - requirements/**
  - components.yaml
  - .github/**
  - deploy/**
  - '**/.env'
  - '**/secrets/**'
dod:
  test_command: python -m pytest taskplane/tests -q
```

Existing direct assignment, checkpoint, Design-fingerprint, and repository
merge contracts are consumption boundaries. Their internal realization is not
product authority for a redesign. If an implementation cannot meet this
requirement without changing an out-of-scope boundary, it must stop for a new
human scope decision.

## Non-functional requirements

- **security:** Fail closed on checkout, SHA, signed approval, Design
  fingerprint, engine receipt, scope, and integration-identity mismatch. Do not
  weaken or modify hook security, and do not accept caller-authored evidence as
  engine authority.
- **architecture:** Preserve the one-way pickup-to-existing-BUILD-C boundary,
  the existing checkpoint and merge owners, and the unchanged legacy loop.
  Pickup is a bounded front door, not a second orchestration system.
- **data-safety:** Repository receipts are append-safe, tamper-evident, keyed by
  exact SHA plus Design fingerprint, and cannot overwrite, confuse, or discard
  another attempt's evidence.
- **sre:** A cold fresh checkout reaches its first executing checkpoint in less
  than 120 seconds, failures are deterministic and named, and interruption is
  resumable from committed repository evidence.
- **integrability:** The public CLI works from a fresh same-SHA checkout,
  consumes the existing Design/BUILD-C/checkpoint/merge contracts without
  changing the legacy loop, and remains covered by the full portable suite.

## Open questions

None. Any need to widen scope, alter the legacy loop or hook layer, or perform a
release mutation is a new human decision rather than an implementation choice.
