# R-0006 Plan — Five ratcheted remediation waves

## Outcome

Implement the approved R-0006 Design Contract as five strictly ordered waves. The plan starts from the clean bootstrap baseline at commits `8bdbaa0` and `76926b3`; those commits are evidence and are not re-planned as uncommitted work. The approved Design fingerprint is `c19fc85dcd3bc679da2cef118b050bcc852831014999540344dc6aaa940b279e` and the approved design-content fingerprint is `4e4e035843bb2ba45b44bdd3d3ba80f6550cd26169ec537981e83427e7bf46da`.

The plan preserves the approved graph policy without reinterpretation: local depth 3, `contract-only` boundaries, contract depth 1, and requirement depth 1. Its single bounded impact snapshot returned six exact unknown graph surfaces—`(root)`, `.github/workflows`, `evals`, `scripts`, `taskplane`, and `taskplane/tests`—and those are declared in `plan/tasks.json`. Each task also declares its exact approved proposed-module identifiers through the engine's canonical `new_modules` ownership field, and binds its canonical registered outcomes through exact `acceptance_refs`.

## Delivery order

1. **Wave 1 — establish truthful authority and review depth.** `t01` records the fresh baseline, authoritative external-knowledge preservation, prior Design object identity, refreshed graph, inactive stale Plan authority, and honest `live|unproven|advisory` enforcement. `t02` then makes R-0006 `quick-only` machine-readable in routing, progression, and collection. Every mutating task depends on `t02`, so every later Evaluate and Engineering manifest must contain quick/light sweep slots only. A substantive quick finding blocks or returns the affected task for correction; it never creates a deep slot, worker, lease, result, or promotion.
2. **Wave 2 — compatibility before mutation or tests.** `t03` removes the CPython parsing defect, introduces the named eager compatibility refusal, removes the four lazy seams, and proves governed stores remain byte-identical on refusal. `t04` ratchets CI and documentation to CPython 3.10–3.13, exact tracked-Python compilation and closed-consumer imports before pytest, including an outside-directory sentinel.
3. **Wave 3 — visible and remotely attributable truth.** `t05` exposes base-scanner and decomposition degradation through one strict quality contract. `t06` installs the closed public CLI error registry while preserving debug and unexpected traceback behavior. `t07` adds process-wide no-egress, byte-deterministic zero-token corpus CI and accepts `pushed_green` only for fetched exact-SHA evidence with zero ahead/behind commits and all required receipts green.
4. **Wave 4 — ratchet before cuts.** `t08` is the standalone 4A slice: it lands the measured AST/Tarjan SCC inventory, bounds, CI check, and history proof while every S1/S2 edge still exists. Only after 4A is committed and green may 4B begin. `t09` adds the acyclic graph primitives/decomposition boundary and preserves direct scan callers; `t10` moves review-context composition to explicit inputs; `t11` moves graph-impact composition to the governed callers and limits sparse fallback. `t12` regenerates the complete post-cut inventory and accepts shrinkage only. Any bound increase or new cycle returns to Design.
5. **Wave 5 — bounded evaluation confidence.** `t13` adds immutable, idempotent, bounded repeated sampling and trial completeness. `t14` adds the three intended incomplete-work refusals and a selective, bounded eval impact map. `t15` projects the same uniquely counted seeded-failure record across Retro, dashboard, machine, Markdown, text, and accessible HTML, with zero samples rendered unavailable and numeric fields omitted.

No task in a later wave may begin before the preceding wave's final task is green. The sequence is intentionally serialized because the approved design revisits shared owners such as `tp.py`, `depgraph.py`, `ci.yml`, and the cycle inventory. The plan does not claim false parallelism across overlapping scopes.

## Verification

Each machine task owns one runnable focused pytest command and the verbatim acceptance criteria it proves. Builders validate in increasing cost order: focused selectors first, then changed-file suites once production and fixtures agree. At the end of each wave, run the accumulated wave criteria before advancing. Final local DoD runs:

`python3 -m pytest -q taskplane/tests && python3 scripts/ci_evals.py --corpus`

The final pushed result is not complete until required checks bind the same fetched SHA where `HEAD == refs/remotes/origin/main == checked_sha`, ahead and behind counts are both zero, and all required check receipts are green for that SHA. Live-model sampling remains scheduled or explicitly invoked and is never required by ordinary push/pull-request CI.

Engineering evaluation and final review use quick/light sweep evidence only through the user's final approval. There is no deep-lens fallback: a substantive quick finding returns the affected bounded task for correction and the quick sweep is rerun against a stable committed target.

## Risks and controls

- **Baseline evidence can be accidentally reconstructed from the wrong or empty knowledge tree.** Resolve only the canonical locator/project root and require the retained trusted pre-cleanup closed manifest. Missing or mismatched evidence blocks; it is never waived as preserved.
- **Advisory enforcement can be mislabeled live.** Persist the evidence/session/hook receipt and, for advisory work only, actor, reason, exact scope, expiry, and accepted limitations. Only `live` is rendered enforced.
- **Compatibility checks can miss tracked Python or a new stage consumer.** Compile the exact NUL-safe `git ls-files '*.py'` set and statically assert the closed production consumer set on every supported minor before tests or stateful flows.
- **Graph fail-open behavior can hide damage.** Normal mode remains available but visibly degraded; strict scan, readiness, Review, and DoD consume the same producer-complete quality record and refuse.
- **Known CLI mapping can swallow defects.** Derive the known set from one closed public registry; unregistered failures retain exit-70 diagnostic behavior and debug re-raises.
- **Local or stale CI can masquerade as pushed green.** Require an explicit fetch, SHA equality, zero ahead/behind counts, and check receipts bound to that exact SHA.
- **Cycle expectations can bless growth or land after cuts.** Commit and pass 4A before any cut, measure the actual tree, record members/edges/LOC/revision, and permit only shrinkage without a new approved Design.
- **Explicit input inversion can lose graph or review context.** Audit every named composition root and preserve direct `depgraph.scan(..., decompose=True)` and payload goldens; omission on a complete non-sparse graph is an error.
- **Repeated trials can duplicate spend or compute partial success.** Bound repeats to 1–100, key immutable trials by scenario/model/version/index/revision, reuse only exact retries, and refuse incomplete, duplicate, or mixed samples.
- **Eval impact can become a catch-all.** Permit only canonical corpus prefixes and 1–32 exact tracked engine files per row, 128 relations total; reject wildcards, directories, escapes, duplicates, unmatched rows, and unrelated product/CLI/reporting impact.
- **Seeded catch-rate can be overstated.** Count unique declared complete units, require `sample_size == denominator`, use the exact label `seeded-failure catch-rate`, and never relabel it as production defect rate or universal model reliability.
- **Quick-only can become silent acceptance.** Collection rejects deep artifacts and the progression resolver converts any would-be promotion into correction/block. Final approval remains human-only.

## Rollout and rollback

Roll out one wave at a time on `main`; preserve every earlier quality floor as later behavior changes land. Wave 4 requires two distinct ordered commits: 4A measurement/ratchet, then 4B edge cuts. A later task may be rolled back only within its wave while retaining the prior compatibility, graph-quality, CLI, no-egress, exact-SHA, cycle-ratchet, and quick-only floors. Existing trial and baseline evidence is immutable; rollback never rewrites it. Reverting a wave-4 cut is allowed only if the retained ratchet and direct/caller parity still pass.

Wave 6 remains entirely deferred. This plan contains no package move or rename, component/schema/orchestration redesign, workspace or plugin identity change, model-tier policy change, attestation, MCP, persistence replacement, physical isolation work, release/tag/marketplace publication, or other architecture/security bet outside R-0006. Any such need is design drift and requires a separate requirement plus new human-approved Design Contract.
