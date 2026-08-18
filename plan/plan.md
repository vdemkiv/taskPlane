# R-0011 plan — host-native workflow UX for Codex and Claude

This plan realizes the human-approved Design Contract fingerprint
`a1d4612d2f606932c01a36f9882b6c15fe7bb1f3a72c3246b867d55efb87bd86`.
The current contract artifact SHA-256 is
`6b59d1dac55af786226ebf39eab2bf19769a52db405caaf0d669a4b60cb8efec`;
the approved fingerprint remains the decision identity. The prior R-0010 plan
is preserved unchanged in `plan/r0010-plan.pre-r0011.md` and
`plan/r0010-tasks.pre-r0011.json`.

## Graph and Design coverage

The required single bounded impact call covered every proposed implementation
surface and matched the approved baseline fingerprint
`a37ace66548f41464159678b3f8d88b315db4b63a6f488349842fdf93a92779b`
at HEAD `383d77bd70d53d33aa2977df0720e69d085b8a08`. It returned 31 impacted
nodes, no unknown modules, and the approved policy: local depth 3,
`contract-only` boundaries, contract depth 1, requirement depth 1.

The five tasks collectively declare every one of the 23 proposed graph nodes,
all six exact contracts, all 18 proposed edges, and AC1–AC15 verbatim. The
first task establishes the canonical schema. Dashboard, workflow/approval, and
preview tasks then use disjoint source/test scopes and may execute in parallel
isolated worktrees. The final host-packaging/compatibility task depends on all
of them and owns the generic `taskplane/tests` graph node.

## Risk-first delivery

1. **Canonical surface model and capability negotiation.** Add one versioned
   snapshot/event/audit model and one serialized adapter queue. Persist each
   canonical update before delivery; acknowledge by workflow, revision,
   sequence, and surface; replay from the last acknowledgement. Capability
   records include host/version, source/confidence/freshness, limitations,
   selected surface, and explicit fallback. Unknown, stale, contradictory,
   partial, or changed-mid-run evidence never becomes support. Fallbacks expose
   the same values/evidence/decisions/actions and say unavailable, not declined.
2. **Live workflow, fan-out, and approval authority.** Publish ordered stage,
   wave, agent, token, attention, gate, and terminal events from existing
   canonical owners. PiP opens only for live persistent work, coalesces to at
   most four <=16 KiB updates/second without dropping attention/terminal
   transitions, reconnects, and closes once. Token values remain observed,
   source-attributed, partial, or unavailable. Native approval cards show the
   complete decision with <=2 primary actions; only authenticated current
   actor/decision/target/revision receipts advance exactly once.
3. **Native dashboard, carousel, and accessibility.** Project workflow, DoR,
   dependency/impact, agents, lenses, criteria, findings, validation, artifacts,
   and gate from canonical state. Zero/one items use concise cards; 3–8 use one
   page; 9+ use deterministic pages of eight with a final page rebalanced to
   at least three where possible. Preserve item ids, filters, focus, totals,
   composer access, <=14 KiB inline payloads, and lossless artifact links. Use
   system tokens and meet WCAG AA, keyboard, 200% text, reduced motion,
   light/dark, alt-text, semantic-label, focus, responsive, and non-color cues.
4. **Governed working previews.** Register design/build/review previews against
   target/revision, private authorization, sandbox/hosting identity, CPU/memory/
   time/network policy, no-push transport, teardown deadline, and capability
   receipt. Browser/side-panel interaction becomes canonical evidence. Denial,
   unavailability, build failure, timeout, push, path escape, external network,
   public exposure, or teardown failure stays bounded and cannot change source
   checkout/remotes or synthesize success.
5. **Cross-host packaging and compatibility.** Declare/observe capabilities in
   the Codex and Claude packages, align hooks/skills/agents/docs with the same
   semantics, and run cross-host/native-disabled goldens. Host styling and API
   names may differ; canonical values, ordering, provenance, actions, audit,
   gates, and accessible fallbacks may not. Existing non-native design, build,
   review, status, approval, and artifact flows retain full evidence and gate
   strength.

## Exact non-functional requirements

- **security**: Native actions, previews, sandbox/hosting, browser interaction, and capability claims are authenticated, target-bound, least-privilege, and fail closed; UI state is not authority and previews cannot push, escape isolation, leak secrets, or become public by default.
- **architecture**: One versioned canonical semantic model and audit stream drive all Claude/Codex native projections and fallbacks; host adapters negotiate/render capabilities without creating parallel workflow truth.
- **accessibility**: Native and fallback surfaces meet WCAG AA, support keyboard and responsive text, retain focus/composer access, provide alt text and semantic labels, and never communicate status by color alone.
- **integrability**: PiP, visualization, approval, dashboard, carousel, preview, and capability contracts are versioned and tolerate additive host evolution without hard-coding identical Claude/Codex APIs.
- **privacy-compliance**: UI and preview telemetry minimize repository/user data, redact credentials, secrets, personal paths, prompts, and unrelated content, and expose preview access only to the authorized conversation/session.
- **sre**: Every native surface has explicit open, update, attention, terminal, error, reconnect, and teardown lifecycle with bounded retries/payloads and an actionable fallback rather than stuck or false completion.
- **scalability**: Agent waves, token events, findings, criteria, and dashboards remain responsive through bounded updates and 3-to-8-item pages; host payloads do not copy full history on every update.
- **cost-finops**: Token reporting is source-attributed and never estimated as observed; UI update coalescing and previews have bounded resource budgets and do not trigger redundant model turns or persist hosting after completion.

## Runnable validation

| Task | Criteria | Command |
|---|---|---|
| Canonical model/capabilities | AC11–AC12 | `python3 -m pytest -q taskplane/tests/test_host_native_capabilities.py` |
| PiP/fan-out/approval | AC1–AC5 | `python3 -m pytest -q taskplane/tests/test_host_native_workflow.py` |
| Dashboard/carousel/a11y | AC6–AC8, AC13 | `python3 -m pytest -q taskplane/tests/test_host_native_dashboard.py` |
| Preview runtime | AC9–AC10 | `python3 -m pytest -q taskplane/tests/test_host_preview_runtime.py` |
| Cross-host/legacy compatibility | AC14–AC15 | `python3 -m pytest -q taskplane/tests/test_host_native_compatibility.py` |

## Rollout and rollback

Roll out behind `TASKPLANE_HOST_NATIVE_UX`: canonical audit-only projectors,
capability/fallback, dashboard/fan-out, PiP/tokens, approval receipts, then
previews independently per capability. UI updates never wake the model. Retry
each surface transition at most twice; teardown once plus one forced provider
cleanup. Rollback disables new native sessions, closes active PiP/private
previews, retains event/receipt readers, and routes canonical snapshots through
legacy bounded accessible widgets/artifacts. No canonical data is migrated,
rewritten, or deleted, and no package/Python-floor/dependency change is needed.
