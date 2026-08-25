# Remaining `tp pickup` delivery — attributed operator trust

## Attributed human scope decision

`human:user` selected an operator-trust continuation for R-0002. AC4 requires
the explicit invocation `--trust-source <exact-source-sha>`; it does not claim
cryptographic authenticity. The asymmetric-approval proposal is shelved and
must not enter this delivery.

## Problem

The stateless `tp pickup <design-contract>` path and its first four bounded
tasks are complete, but repository-only AC4 resume needs an explicit human
authority input that a fresh checkout can compare with the shelf and receipt
lineage without inheriting private Taskplane state. The remaining delivery
must record and enforce that attributed operator-trust input while describing
its security limitation honestly.

## Users and context

- A human operator selects one existing shelf Design Contract from a clean
  checkout and supplies `--trust-source <exact-source-sha>`.
- The flag value is the exact source SHA the human chooses to trust. It must
  equal the shelf's exact authorized source SHA and the source identity already
  bound to the Design fingerprint and repository receipt lineage.
- A second checkout starts with no private Taskplane home and no private-store
  handoff. It resumes using Git-tracked shelf and `exports/` evidence plus the
  newly supplied attributed operator-trust flag.
- The existing v1 symmetric shelf-signature path remains available only
  through its incumbent private-secret runtime path and is unchanged.
- The target candidate is Taskplane 2.17.20. Push, tag, publication, release,
  and `origin/main` mutation remain separate human authority.

## Inherited limitation — standing state, not new R-0002 debt

Human gate approvals currently record unauthenticated actor strings. The only
shelf signature currently available is computed with a symmetric secret held
in private runtime state. The `--trust-source` mode neither removes nor
strengthens those facts and must never be described as authenticating the
human, producer, shelf signature, or engine receipt. This is documented
inherited standing state, not debt newly incurred by R-0002, and no `req debt`
record is authorized for it.

## Completed inventory — retain, do not replay

The amended Plan must retain these facts as non-executable history:

1. **T01 / AC1 evidence:**
   `70f311ad75de33a530a6ba43ac213883a1e95c3f`.
2. **T02 / AC2 evidence:**
   `5cc647cabd8bd8528b3044184e38d3317f593f27`.
3. **T03 released-tip prerequisite evidence:**
   `5c28165f800fffcac20aa2004d9a2b38efb195cf`.
4. **T04 / AC3 evidence:**
   `0c19087e3eb28d70869f2752f12c2d3742f33810`.
5. Repository-only resume implementation at
   `3ee3a17a695bf059f90504507a8eb5fe690fb52d` and retry-safe atomic receipt
   publication at `a73f125e762670323d0e4a8fbbef3a1edf3ea958` remain
   implementation inventory and must not be replayed or replaced.

AC1–AC3 may execute only as unchanged regression coverage within AC5. They and
the atomic-publication repair must not receive new implementation, checkpoint,
evaluation, or acceptance tasks.

## In scope

1. Extend the public pickup invocation with the required exact flag
   `--trust-source <exact-source-sha>` for repository-only AC4 resume.
2. Treat the flag as attributed human operator authority for this invocation.
   Record the exact flag name and exact value verbatim in the pickup receipt;
   do not normalize, abbreviate, replace, or infer the recorded authority.
3. Require the value to be a well-formed exact Git source SHA and to equal the
   shelf's exact authorized source SHA. Preserve the existing binding among
   that SHA, the canonical Design fingerprint, receipt path/digests,
   predecessor chain, element, criterion, assigned revision, checkpoint, and
   merge outcome.
4. From a fresh second checkout with no private Taskplane home or first-
   checkout handoff, use the explicitly repeated trust-source flag plus
   Git-tracked shelf and receipt evidence to resume the next manual criterion
   through the incumbent pickup path.
5. Fail closed before BUILD-C on a missing flag, malformed SHA, SHA mismatch,
   missing or malformed shelf evidence, structural shelf tampering, receipt
   digest/path mismatch, predecessor mismatch, fork, gap, collision, or mixed
   SHA/Design-fingerprint/lineage identity.
6. On every such refusal, preserve every prior committed receipt byte-for-byte,
   create no authoritative partial receipt, and authorize no checkpoint or
   merge.
7. Preserve zero run, track, claim, lease, wave, active-loop, or private-
   handoff state and preserve one-acceptance-criterion-at-a-time checkpoint
   discipline. AC4 behavior and its direct positive and negative proofs land
   in the same bounded implementation commit.
8. Preserve the existing `tp pickup` → direct BUILD-C assignment → engine
   checkpoint → merge-on-green path, exact assigned-revision checks,
   retry-safe atomic `exports/` publication, and receipt directory identity
   keyed by exact source SHA plus Design fingerprint.
9. Preserve v1 symmetric shelf handling through its current private-secret
   path without changing its contract, key source, signature behavior, or
   claims.
10. After AC4 passes, update only the bounded shipped version/release surfaces
    to 2.17.20 and run AC5 on the exact final clean candidate SHA.
11. After AC5 passes, run one quick security and one quick QA review
    concurrently against that exact SHA and evidence set, then one
    engineering-manager review against the same SHA. Stop for explicit human
    push authority.

## No-authenticity claim

The operator-trust mode proves only that the caller supplied an attributed
exact source SHA and that repository structures agree with it. It does not
cryptographically authenticate the actor string, human approval, producer,
shelf document, engine receipt, or origin of repository bytes. Public output,
receipts, documentation, tests, review evidence, and release notes must use
operator-trust language and must not use `authenticated`, `cryptographically
verified`, `signed by the human`, or an equivalent authenticity claim for this
mode.

## Out of scope

- Repository-verifiable asymmetric authenticity in the current delivery.
- Any signer, verifier, public/private key workflow, signing dependency,
  allowed-signers runtime, certificate authority, key-management service,
  rotation/revocation mechanism, or hand-rolled cryptography.
- Changes to the existing v1 symmetric shelf-signature implementation or its
  private-secret runtime path.
- Changes to protected completed-work surfaces beyond the narrow public CLI,
  pickup authority/receipt projection, direct pickup tests, and bounded release
  metadata named below.
- Reimplementation or replay of T01–T04, AC1–AC3, repository-resume
  groundwork, or retry-safe atomic receipt publication.
- Phase 0, E0, any R-0011 work, or any unrelated R-0013 intake, Design, Plan,
  review, or delivery content.
- Changes to hook security, hook receipts, enforcement strength, the legacy
  loop, or the legacy run/track/claim/lease/wave lifecycle.
- A second BUILD-C, checkpoint, merge, Design approval, worktree, or receipt
  engine; automatic full/deep/serial-all/all-lens review sweeps.
- Push, tag, marketplace/package publication, release, or any mutation of
  `origin/main` before explicit human authority.
- Changes to retained worktrees, paused branches, dormant runtimes, or any
  Taskplane home other than the isolated pickup delivery home.

## Acceptance criteria

1. **AC4 — a fresh second checkout resumes through explicit attributed
   operator trust with no private-store handoff.** After one manual criterion
   and its atomic pickup receipt are committed, a fresh second checkout with an
   empty/nonexistent Taskplane home and no access to the first checkout must
   invoke pickup with `--trust-source <exact-source-sha>`. The exact flag and
   full SHA value must be recorded verbatim in the new pickup receipt as
   attributed human authority; the SHA must equal the shelf's exact authorized
   source SHA and the existing exact-SHA/Design-fingerprint/receipt-lineage
   identity. The next existing BUILD-C checkpoint must run with zero
   run/track/claim/lease/wave/private-handoff state, and output must make no
   cryptographic-authenticity claim. Focused negative proofs for a missing
   flag, malformed SHA, SHA mismatch, malformed/missing shelf evidence,
   structural tampering, digest/path/predecessor mismatch, fork, gap,
   collision, and mixed lineage identity must all refuse before BUILD-C,
   preserve prior receipts byte-identically, create no authoritative partial
   receipt, and authorize no merge. The unchanged v1 private-secret symmetric
   path must retain its existing focused green coverage.
2. **AC5 — full suite on the final SHA: no new failures.** After the AC4 commit
   and consistent 2.17.20 version/release metadata are committed, verify the
   checkout is clean and run `python -m pytest taskplane/tests -q` with the
   pinned delivery interpreter on that exact candidate SHA. The suite must
   include unchanged pickup, v1 symmetric, atomic-publication, hook, and
   legacy-loop regressions and report zero new failures. Any failure blocks
   final review and grants no broad repair authority.
3. **2.17.20 release surfaces are consistent and bounded.** README,
   CHANGELOG, Codex plugin manifest, Claude plugin manifest, and marketplace
   manifest must all identify 2.17.20, parse successfully, describe the
   operator-trust limitation accurately, and perform no push, tag, publication,
   release, or `origin/main` mutation.

## Post-acceptance release gate — loop owned

**Final review and authority stop are exact-SHA bound.** Quick security and QA
must complete concurrently against the AC5 SHA, followed by an engineering-
manager review against the identical SHA and evidence fingerprints. The
workflow stops before every external release mutation and requires explicit
human push authority.

This gate is mandatory after AC5, but it is not an implementation task or an
evaluator-owned acceptance criterion. The loop owns its security/QA → EM →
human push-authority sequence, matching the original `Ship:` instruction.

## Required failure behavior

- Trust-source validation is pre-BUILD-C. No rejected authority/shelf/lineage
  case may reach assignment, checkpoint execution, receipt publication, or
  integration.
- The receipt records the human-supplied flag and full SHA without converting
  the record into a cryptographic-authenticity claim.
- A matching SHA never excuses dirty checkout bytes, malformed shelf evidence,
  structural/digest/path/predecessor/fork/gap/collision failure, stale Design
  fingerprint, mixed checkpoint identity, or a non-green result.
- An interruption or publication failure keeps prior committed receipts
  byte-identical and remains safe to retry through the already-delivered atomic
  publication behavior.

## Design backlog — not picked up now

- **Element:** `asymmetric-approval-authority`
- **Inventory:** the blocked/conditional Design document only; it is not an
  approved current-delivery contract, Plan task, implementation target,
  evaluation claim, or new R-0002 debt record.
- **Preferred future runtime:** OpenSSH `ssh-keygen -Y` verification with a
  committed allowed-signers trust file. Verification fails closed when
  OpenSSH is unavailable. The future design adds no signing dependency and no
  hand-rolled cryptography.
- **Pickup triggers:** a second operator, an untrusted producer host, or an
  external evidence-verification requirement.
- **Current disposition:** shelved by attributed human decision; explicitly not
  picked up in this delivery.

Because Taskplane 2.17.19 cannot delete contracts from requirement metadata,
`contract:pickup.asymmetric-authenticity` and
`resource:repository.pickup-public-verification-material` remain historical
backlog inventory on R-0002. They MUST NOT create Plan tasks, implementation
scope, acceptance/evaluation claims, graph-realization claims, or release
claims in this delivery.

## Contract handoff

### Active canonical boundary ids

```yaml
contracts:
  - contract:pickup.stateless-front-door
  - contract:pickup.operator-trust-source
  - contract:design.approved-contract
  - contract:build-c.direct-assignment
  - contract:build-c.acceptance-checkpoint
  - contract:repository.merge-on-green
  - resource:exports.pickup-receipts
```

### Historical backlog-only metadata

```yaml
historical_contracts_not_authorized_for_delivery:
  - contract:pickup.asymmetric-authenticity
  - resource:repository.pickup-public-verification-material
```

### Scope

```yaml
scope_paths:
  - taskplane/tp.py
  - taskplane/pickup.py
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
  - taskplane/build_c.py
  - taskplane/checkpoint.py
  - taskplane/design_contract.py
  - taskplane/repository.py
  - taskplane/storage.py
  - taskplane/taskplane_lite.py
  - plan/**
  - backlog/**
  - components.yaml
  - .github/**
  - deploy/**
  - '**/.env'
  - '**/secrets/**'
dod:
  test_command: python -m pytest taskplane/tests -q
```

If AC4 cannot be met inside these active scope paths without modifying a
protected or asymmetric/signing surface, stop for a new human scope decision.

## Non-functional requirements

- **security:** Operator-trust mode validates a human-supplied exact source SHA
  against the shelf and existing SHA/Design-fingerprint/lineage identities and
  fails closed before BUILD-C on every named mismatch. It makes no
  cryptographic-authenticity claim, adds no secret/key/verifier dependency, and
  leaves the v1 private-secret symmetric path unchanged.
- **architecture:** Add only the narrow `--trust-source` CLI-to-pickup authority
  projection and receipt field. Preserve the incumbent BUILD-C, checkpoint,
  merge, atomic-publication, hook, legacy-loop, and v1 symmetric boundaries;
  create no signer/verifier/key workflow or alternate runtime.
- **data-safety:** The full flag and SHA are recorded verbatim as attributed
  human authority; structural and lineage checks remain exact. All negative
  paths preserve prior receipts byte-identically, create no authoritative
  partial file, and authorize no checkpoint or merge.
- **sre:** A cold same-SHA checkout with no private Taskplane home resumes
  deterministically when the explicit trust source matches and fails by named
  pre-BUILD-C reason when it is missing, malformed, mismatched, or paired with
  invalid shelf/lineage evidence.
- **integrability:** The new optional-authority mode preserves the completed
  stateless pickup path, existing v1 symmetric behavior, public CLI contract,
  exact full-suite command, and bounded 2.17.20 release surfaces without
  changing hooks or the legacy loop.

## Open questions

None. The human selected attributed operator trust and explicitly shelved
asymmetric authenticity until a named trigger occurs.
