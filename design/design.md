# R-0001 — Consolidated progressive delivery and engineering review

## Decision

Extend Taskplane with one canonical consolidated authorization packet and authority ledger spanning Product, conditional Design, Plan, Build, Engineering, recovery, artifacts, and final-signoff readiness. Product, Design, and Plan remain mechanically rigorous but auto-advance when evidence passes. Byte changes to design artifacts are audit facts, not human gates: an evolution classifier compares accepted requirement semantics, named contract meanings, target/scope, and requested authority. Contract-preserving/non-material evolution proceeds automatically; changed acceptance, contract meaning, material scope/authority, destructive/external action, exhausted recovery, A/B selection, or final sign-off requires attributable human attention.

The original progressive-review design remains intact with its risk-scaled floor: documentation-only and simple low-risk work runs exactly one attributable risk-selected deep lens, while substantive, risky, mixed, or evidenced ambiguous/corrupt work runs architecture, code-quality, security, and QA as four deep floors. Missing module mapping alone does not widen. Evidence-selected review adds at most one light sweep, with attributable promotions, immediate immutable provisional request-changes for severe harm, complete exactly-once conservation for approval, metadata-only repair, exact evaluator/root binding and outage caching, documentation-aware routing, measured convergence, and isolated lens telemetry.

## Grounding and constraints

Current authorities already exist in `loop.py` (stages/gates/worktrees), `review.py` and `review_evidence.py` (slots/revisions/repair/evidence), `review_dor.py` (requirements), `lens.py`/`lens_signals.py` (routing), `taskplane_lite.py`/`repository.py`/`preflight.py`/`storage.py` (engine/root/setup), command runtime/adapters (bounded execution), and runtime_eval/spend/dashboard/views (truthful status/delivery). R-0011 supplies host-native progress/approval/fallback semantics. This design composes them rather than adding authority.

Python reference SHA-256 `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7` was verified. Orchestration stays synchronous/file-locked; worker concurrency remains runtime-owned with cancellation and bounds. Persisted JSON/receipts/thread actions validate at trust boundaries. No dependency, namespace, Python floor, lock, or wheel-content change; mutable state is serialized without relying on the GIL.

## Alternatives

### A. One authority ledger with semantic evolution classification — selected

Persist one packet/receipt, mechanically derive stage authority, auto-run bounded recovery, and prompt only at the closed boundary set. Gains: fewer ceremonial prompts, one cross-host truth, lossless recovery, semantic rather than byte-fragile governance. Costs: authority/evolution/recovery matrices. Revisit when hosts standardize equivalent authority transactions.

### B. Approval per Product, Design, Plan, and recovery

Gains: simple stage-local logic. Costs: repeated prompts, approval fatigue, harmless drift deadlocks, divergent thread behavior. Revisit only for opt-in regulated independent signers.

### C. Silent autonomy until sign-off

Gains: least interruption. Costs: silence expands authority and can execute changed scope/contracts or external actions. Rejected.

## Added contract intents

- `contract:consolidated-authorization`: packet binds actor/thread, requirement/ACs, target/scope, contract meanings, Design/Plan, dynamic/sandbox intent, recovery bounds, delivery, and routine execution. Each stage cites packet plus unchanged semantic facts.
- `contract:automatic-recovery`: classifies transient, metadata, evaluator, collection, artifact/render, setup, safety, authority, and replan failures; routine classes retry 2–3 times or while convergence progresses.
- `contract:status-progress-telemetry`: snapshot-only active owner/agent/phase, observed tokens, focus elapsed, execution/tool/agent/human wait, and sourced/confident/fresh ETA or unavailable; never recomputes or gates.
- `contract:onboarding-worktree-continuity`: self-repairable checks execute automatically; authority-required asks once; host-policy/external states wait for change; repository-family launcher resolves exact sibling worktree/latest valid engine.
- `contract:product-internal-north-star`: conditional seven-field internal advice, never a gate.
- `contract:large-markdown-delivery`: JSON authority, inline small output, automatic complete Markdown for large output, optional/nonblocking HTML, no truncation.
- `contract:attributed-thread-continuation`: one actor/thread/requirement/target/revision/packet-bound Claude/Slack receipt authorizes routine continuation; accessible Markdown/reply fallback preserves actions and never maps unavailable to declined.

The original eight contracts retain slot conservation, provisional lineage, mechanical repair, risk progression, evidence binding, evaluator cache, convergence, and isolated telemetry.

## Evolution, review, and human boundaries

Evolution classes are `byte-only` (format/order/regeneration), `non-material` (HOW change within accepted requirement/contracts/authority), `material-contract` (acceptance/contract meaning/target/scope/destructive/external/spend/credential/publication/gate weakening), and `unsafe-or-ambiguous`. The first two audit, mechanically revalidate, and continue; the latter two reauthorize or pause. Evidence fingerprints invalidate stale evidence/cache but never alone create a human gate.

Engineering review runs exactly one attributable risk-selected deep lens for documentation-only and simple low-risk work. Substantive, risky, mixed, or explicitly evidenced ambiguous/corrupt work runs architecture, code-quality, security, and QA as four deep floors and records the widening reason; missing module mapping alone never triggers that widening. Other lenses enter through at most one sweep and attributable promotion. Blocker, High, security vulnerability, or harmful/destructive bug seals request-changes immediately; approval waits for complete conserved slots/acceptance evidence. Repair changes only derivable metadata; substance retries affected slots. Cache keys include evaluator/engine/capability/repository/exact worktree/window. Convergence may exceed three cycles; two no-progress cycles, repetition, oscillation, worsening, task bound, safety/scope/authority change, or human stop escalates.

The only human boundaries are initial packet, A/B selection, material authority/evolution, exhausted/no-progress/replan, destructive/irreversible/external/credential/publication/spend, gate-weakening recovery, and final sign-off. Preview feedback is an attributable change classified by the same semantics.

## Observability and rollout

Signals: `authority_packet`, `authority_derivation`, `semantic_evolution`, `mechanical_gate`, `automatic_recovery`, `owner_wave`, `repository_preparation`, the eight existing review signals, `status_snapshot`, `onboarding_continuity`, `thread_continuation`, and `artifact_delivery`. They store bounded fingerprints/status/counts, not prompts/secrets/credentials/personal paths. Recovery is idempotent and bounded; UI/thread absence never mutates canonical state; immutable evidence is superseded, never overwritten.

Roll out behind `TASKPLANE_CONSOLIDATED_FLOW`: dual-record authority/evolution; enable mechanical auto-advance; preparation/recovery; progressive review; status/onboarding; delivery/thread. Compare Codex/Claude/Slack/worktree/legacy goldens each phase. Rollback disables new packets, completes in-flight work with retained readers, falls back to bounded/Markdown delivery, preserves evidence, and cannot auto-approve.

Owners/order: authority owner (`authority.py`), recovery owner (`recovery.py`), status owner (`progress.py`), onboarding owner (repository/preflight), Product/north-star owner, delivery owner (views/dashboard), thread adapter owner, and existing review owners. Ship authority/evolution → gates → recovery/preparation → review → status/onboarding → delivery/thread.

The visual is retained because authorization derivation, semantic evolution, progressive review, and cross-host delivery form a branching flow.
