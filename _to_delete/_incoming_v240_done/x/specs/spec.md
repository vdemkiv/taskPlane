# Spec — v3 Phase 1: intelligent lens routing + workflow review wave

Full strategy and evidence: docs/v3-strategy-flows-lenses-onboarding.md
(WS2 layers 1+2 and the R-W addendum). Anchored requirements: R-0001
(primary), R-0002 (depends on R-0001).

## Problem

Lens routing is filename-level only (globs + task type + hub score); reviews
mandate breadth=all, so all non-routed lenses sweep regardless of relevance
(~1.3–1.5M tokens per full review measured). There is no content awareness —
an i18n lens runs against components with zero user-facing strings — and no
stage awareness. Separately, the Claude host now ships Dynamic Workflows
(deterministic, journaled, resumable, schema-validated fan-out) that the
review wave should use where available; Codex has no equivalent, so the
current Task-based dispatch must remain byte-identical there.

## In scope

1. **Stage lens profiles** as catalog data (`stage_profiles`): design /
   build(verify) / review candidate sets; router restricted to the active
   stage's profile.
2. **Applicability engine**: per-lens deterministic detectors over content
   (i18n signals, UI markup, SQL/migrations, HTTP/queue clients, auth/PII,
   concurrency, platform APIs), graph (component kind, hub score, boundary
   contracts), and requirement (acceptance-criteria keywords) signals →
   verdict deep | light | n/a-with-negative-evidence per lens. Budget: 5–7
   deep target, hard cap 8 (overflow → light, never dropped). Floors:
   security never n/a on enforcement/boundary diffs; architecture ≥ light on
   code. `--lens` forces; `--breadth all` remains for audits.
3. **Coverage honesty**: dashboard coverage map + HEADLINE show all 26
   lenses as deep/light/n-a-with-reason; findings meta carries the routing
   decision with evidence. Skipping is never silent.
4. **Detector test discipline**: every detector ships positive + negative
   fixtures; full-catalog audit run (breadth=all) diffs findings vs the
   routing decision and auto-files findings from n/a lenses as router
   regressions (class: regression).
5. **Workflow review wave (R-0002)**: `workflows/review-wave.js` shipped in
   the plugin (Claude hosts): briefs in via args, one agent per routed lens
   with schema-pinned findings output, TASKPLANE_TASK slots honored, merge
   to .em-review/. Capability detection with MANDATORY fallback to today's
   dispatch (Codex / workflows disabled) — byte-equivalent artifacts,
   traced path choice, CI parity guard.

## Out of scope (later phases)

Component decomposition of the graph (WS2 layer 3), flow-as-data (WS1),
README/onboarding overhaul (WS3), any change to Codex behavior beyond
keeping it identical.

## Acceptance

The union of R-0001 and R-0002 acceptance criteria. Non-negotiables carried
from standing project rules: no guardrail may weaken (strict-or-stricter,
proven); every change verified for regressions in-radius at DoD (the v2.3.1
regression gate applies to this build itself); Codex path byte-identical.
