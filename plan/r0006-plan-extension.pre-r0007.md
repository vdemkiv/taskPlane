# R-0006 plan extension — one governed review experience on Claude and Codex

This extends the still-unapproved R-0006 plan without changing the approved
authority model or Design fingerprint
`3e7ceb20fe28d9194c7c2ca1bfca334a34236de6b337d24a5d0c596f9872b7d2`.
The HostCapabilitySnapshot, EvaluationOutputContract, ReviewKernel leases and
collector, submission-aware lifecycle, host receipts, and orchestrator/human
gate ownership remain the only authorities.

## Delivery order

1. **t1 — runtime and canonical dashboard.** Finish the approved capability
   contracts, then make `review start/collect` produce one engine-authored,
   content-addressed dashboard that embeds the dependency graph. Claude and
   Codex render the same bytes inline when supported and otherwise deliver the
   same artifact by reference. It contains workflow/waves, findings and notes,
   graph, preflight choices/evidence, diagnostic fingerprints, and exactly one
   human approve/request-changes gate.
2. **t2 — semantic parity, guidance, and validation.** Add a frozen
   Claude/Codex PR comparison requiring identical routing dispositions,
   canonical identities, and semantic finding clusters while allowing wording
   and raw count differences. Update shipped agent/skill/docs guidance, run
   focused checks, then run the complete suite once.
3. **t3 — artifact-aware DoR and faithful dynamic-review sandbox.** Before
   selecting any review, build, design, or delivery flow, inventory the
   specification artifacts already supplied by the target. For a PR this
   includes commit subjects/bodies, changelog or release-note changes,
   linked requirement/spec files, and repository-local contribution or test
   instructions. Bind the selected artifacts and their fingerprints into DoR
   and the canonical context. A dynamic-review failure becomes a finding;
   validation-only repair runs in an independently cloned, push-disabled copy
   containing the exact reviewed tracked patch and eligible untracked inputs.
   The original checkout remains unchanged and remains the verdict authority.

The tasks are serial and have disjoint implementation scopes. All retain the
approved graph policy: three local hops, `contract-only` boundaries, one
contract hop, and one requirement hop. The bounded impact result found no
unknown modules, reached 21 nodes, and affected R-0001, R-0005, and R-0006.
All 24 designed modules, 38 proposed edges, five exact contracts, and fourteen
R-0006 acceptance criteria remain owned.

## Added acceptance and validation map

| Acceptance | Validation |
|---|---|
| Both hosts deliver the exact canonical dashboard; the graph is merged into it, no host substitute is accepted, and durable dashboard/standalone-graph references remain. | Golden bytes/digests, inline/reference transport matrix, substitute rejection, and single-render assertions in `test_review_dashboard_contract.py` and `test_review_host_parity.py`. |
| Standalone review pauses once for explicit human approve/request-changes; remote source/storage state cannot approve. | Local, remote, resumed, storage-backed, no-response, approve, and request-changes cases in `test_review_human_gate.py`, with one gate identity and zero inferred transitions. |
| Preflight offers optional dynamic validation and optional functionality rendering through structured approval actions; static-only remains labeled. | Selected/declined/unavailable/executed permission, install, process, and browser matrices in `test_review_preflight.py`; no side effect before approval and durable evidence for every state. |
| Clean lenses provide source-anchored checked evidence and render notes; semantic deduplication retains provenance and severity disagreements. | Clean, multi-lens, near-duplicate, and conflicting-severity fixtures in `test_review_semantic_dedup.py`. |
| Host parity compares routing dispositions and semantic clusters, not exact prose/count. | Paired frozen reviews in `test_review_host_parity.py`; disposition/cluster/fingerprint/gate/provenance mutations fail while wording/order-only changes pass. |
| Dashboard includes engine/version, routing-policy, graph, and routing-decision fingerprints plus workflow, findings/notes, graph, validation/render evidence, and the one gate. | Source-of-truth recomputation and mismatch/staleness tests in `test_review_dashboard_contract.py`. |
| Every flow inventories available DoR artifacts before choosing or executing the flow; PR commit messages and changed changelog/release notes are specification candidates, not incidental prose. | Artifact-presence/absence/conflict/precedence fixtures across Review, Build, Design, and governed delivery; canonical-context fingerprint changes when an admitted specification artifact changes. |
| Dynamic command failure is a high-severity review finding. Validation-only repair uses an exact disposable copy and can add conditional evidence without erasing the original failure. | Failed-without-receipt persistence, dirty tracked patch, eligible untracked file, artifact exclusion, original immutability, disabled push, and repaired-evidence provenance tests in `test_review_preflight.py`. |

## Guardrails

- The graph is visually merged, not duplicated. Its standalone artifact may
  remain an internal durable reference, but the human governs one dashboard.
- Inline display is presentation only; engine-authored bytes/fingerprints are
  authoritative.
- Dynamic validation/rendering is optional, but permissions, installs,
  processes, and browser access require structured user approval. Declined or
  unavailable capability is labeled static-only, never fabricated execution.
- Deduplication clusters without deleting lens/slot/source/revision provenance.
- Remote comments, labels, storage, or prior decisions never answer the gate.
- Workers never gate, approve, advance, or clear contracts.
- No skips, xfails, waivers, ceiling increases, assertion weakening, or
  de-gating are permitted.
- Flow selection must follow artifact discovery. When commit/changelog/spec
  evidence conflicts with an inferred goal, surface the conflict at DoR rather
  than silently choosing a process from the user's short prompt alone.

No version, release, marketplace, package, destructive cleanup, or user
artifact deletion is in scope. Preserve existing untracked evaluation runs and
`first-run.tgz`.
