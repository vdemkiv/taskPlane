# Plan — taskPlane 2.7 Codex-native workflow and release hardening

Requirement **R-0001** is refined to 0.91 with no blocking questions. The user
approved all seven review recommendations. Work remains serial because the four
tasks deliberately touch shared enforcement and documentation surfaces; the
main orchestrator keeps authority over gates, commit, tag, and submission.

## Order

1. **t1-regression-gate — restore the core proof claim first.** Wire the gate
   into real loop contracts, make Python radius discovery repository-generic,
   widen visibly when narrowing is not provable, and make every runner failure
   a blocker. This establishes the stronger DoD used by following tasks.
2. **t2-codex-native — add the current host adapter.** Keep taskPlane briefs and
   Claude workflows unchanged while giving Codex stable native task identity,
   reasoning-effort tiers, exact spawn/wait/interrupt guidance, and lifecycle
   tracing. Fix strict dispatch against current spawn input.
3. **t3-package-ci — make the shipped artifact truthful.** Package README/docs,
   validate reference closure inside the archive, update onboarding to `/plugins`,
   isolate host tests, and add the Codex CI leg.
4. **t4-clean-release — remove duplicated scratch and prove the release.** Clean
   the index, add ignore rules, run every test/ratchet/package check, perform the
   full engineering review, then create the commit and `v2.7.0` tag.

## Dependencies and contracts

```text
t1 regression/DoD
  -> t2 Codex transport + lifecycle
    -> t3 marketplace package + target-host CI
      -> t4 repository cleanup + complete release evidence
```

- `contract:loop-gate` changes in t1 and is consumed by t2.
- `contract:codex-dispatch` is defined in t2 and validated/documented in t3.
- `contract:openai-package` is closed in t3 and release-validated in t4.
- Distributed reasoning stops at these named contracts; no external service
  implementation is in scope.

## Risk controls

- The regression runner must distinguish assertion failures from infrastructure
  failures; tests cover both and use the active interpreter.
- Dispatch changes are additive fields with explicit Claude/Codex parity tests.
- Lifecycle hooks are observability/context only and are described honestly.
- Package closure is computed from the exact file set written to the ZIP.
- Cleanup uses exact tracked roots and `git rm`, making every removal recoverable
  from Git history until the release is committed.

## Definition of Done

- Eight R-0001 acceptance criteria pass with independent evidence.
- Full neutral suite, explicit Codex-host suite, unittest floor, cost ratchets,
  generated artifacts, manifests, package closure and deterministic ZIP pass.
- Engineering review finds no unresolved high regression.
- Repository is clean after the release commit and points to tag `v2.7.0`.
