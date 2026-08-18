# R-0011 — Host-native workflow UX for Codex and Claude

## Decision

Taskplane will add one canonical host-surface model and ordered audit event stream, projected by capability-negotiated Claude and Codex adapters. Host UI is presentation, never authority. Existing loop, review, command, token, evidence, and gate records remain authoritative; a new projector converts them into PiP progress, agent topology, approval, dashboard/carousel, preview, or bounded fallback views.

This is grounded in current seams: `host_capabilities.py` already records immutable source/confidence/status snapshots and treats unknown/contradictory evidence explicitly; `loop.py` owns stages, agent waves, human gates, and host dispatch; `runtime_eval.py` distinguishes measured token totals; `command_runtime.py`/`command_adapters.py` own durable commands and isolation; `review.py` owns target-bound consent, sandbox observations, and review truth; `dashboard.py` enforces 14,000-byte bounded pages and bridge-aware actions. The current-state inventory is empty, so these cited sources and baseline graph are the evidence.

The Python solution-design reference was verified at SHA-256 `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`. Synchronous Taskplane state remains the owner. Adapter update delivery may be asynchronous, but each session has one runtime-owned queue, coalescing bound, cancellation path, and terminal teardown; concurrent failures are recorded together rather than hidden. Runtime JSON/receipts are validated at trust boundaries. No new dependency, public package namespace, Python floor, lock policy, or wheel-content change is required. Mutable adapter/session state remains protected by existing file locks/serialized transitions and does not rely on the GIL.

## Alternatives

### A. Canonical semantic model with negotiated host projectors — selected

Add a versioned host-neutral surface snapshot/event contract and thin host projectors. Gains: semantic parity, auditable actions, deterministic fallback, additive host evolution. Costs: adapter conformance suite and explicit lifecycle machinery. Revisit when every supported host implements one standardized native UI protocol with identical receipt and preview semantics.

### B. Independent native implementations per host

Each host reads workflow state and implements PiP, approvals, dashboards, and previews directly. Gains: fastest access to native features. Costs: parallel truth, inconsistent capability claims, approval security drift, duplicated tests. Revisit only if semantic parity is removed from the product requirement.

### C. Enhanced HTML/widget only

Keep bounded inline HTML and static previews for every host. Gains: smallest runtime change and universal fallback. Costs: no persistent PiP, native fan-out, authoritative native receipts, or integrated preview lifecycle. Revisit as the fallback, never as the primary supported experience.

## Contracts and flow

`contract:host-capability-negotiation` extends immutable host snapshots with PiP, visualization, approval, carousel, sandbox, hosting, browser, and side-panel observations. Each records host/version, source, confidence, freshness, limitations, selected surface, and fallback. Unknown, stale, partial, contradictory, or mid-run changes select a deterministic safe fallback; support is never inferred from files or prose.

`contract:host-native-progress` consumes ordered canonical workflow events. PiP opens only for a live persistent workflow, updates stage/work/attention/last-update/token state, reconnects by workflow/run/revision and closes exactly once on completion, cancellation, or failure. Updates are coalesced to at most four per second and 16 KiB per event, while attention and terminal transitions are never dropped. Tokens carry raw/effective value, scope, provider/source, observed status, and canonical record fingerprint; unavailable/partial is not zero.

`contract:host-native-visualization` projects the canonical dispatch graph: stable task/slot/agent identity, role, scope, wave/dependency edges, state, attention, retry lineage, and outcome. The adapter cannot invent agents or completion.

`contract:host-native-approval` renders one decision snapshot with target/revision, reason, evidence, consequences, owner, approvability, and at most two primary inline actions. Rich evidence opens detail/fullscreen while retaining the composer. An action yields an authenticated receipt bound to host session, actor, decision, target, revision, action, nonce, and expiry; canonical gate code verifies it and advances exactly once. UI state alone has no authority.

`contract:host-native-dashboard` defines canonical component snapshots for workflow, DoR, dependency/impact, agents, lenses, criteria, findings, validation, artifacts, and gate. Repeated datasets use stable ordering and item ids. Zero/one items use concise cards; three to eight use one carousel page; nine or more use deterministic pages of eight, with the final page rebalanced to at least three when possible. Pages show current/total, concise metadata, one CTA/item, and preserve filters/focus. No nested scrolling or deep inline navigation; rich exploration uses fullscreen. Inline payload remains <=14,000 bytes, while artifacts remain lossless.

`contract:host-preview-runtime` registers a target/revision-bound disposable sandbox with CPU/memory/time/network policy, push disabled, private session access, teardown deadline, and capability/authorization receipts. Design, build, and dynamic review can expose a pinned interactive browser/side-panel preview; observed behavior becomes evidence. Source checkout/remotes are fingerprinted before/after. Attempted push, path escape, unauthorized network, public exposure, timeout, build failure, or teardown failure closes/fails safely and never changes canonical source.

The canonical snapshot and audit event are stored before adapter delivery. Adapters acknowledge `(workflow, revision, event_sequence, surface)` idempotently. Reconnect replays from the last acknowledged sequence. Fallbacks consume the same snapshot and expose the same values, evidence, decisions, and safe actions while naming the missing capability as unavailable—not declined.

## UI guidance and accessibility

PiP is restricted to persistent live parallel sessions, responds dynamically, and auto-closes terminally. Inline cards are single-purpose with no more than two primary actions, nested scrolling, or deep navigation. Carousels show 3–8 consistent items, concise metadata, and one CTA per item. Fullscreen handles rich multi-step/detail work while keeping the composer reachable. Every surface uses host/system tokens, fonts, spacing, responsive layout, WCAG AA contrast, alt text, semantic labels, visible focus, reduced-motion support, and non-color cues.

## Failure, telemetry, rollout

Signals: `host_capability_selection`, `host_surface_session`, `host_surface_update`, `host_token_projection`, `host_agent_projection`, `host_approval_receipt`, `host_dashboard_page`, and `host_preview_lifecycle`. They contain bounded ids, counts, statuses, durations, sources, and fingerprints—never prompts, credentials, secrets, personal paths, or unrelated repository content.

Failure modes include stale/contradictory capability, event gap/duplicate, PiP reconnect/close failure, token source mismatch, phantom agent, invalid approval receipt, carousel loss/duplication, render/accessibility failure, preview isolation violation, and teardown failure. Each selects a named accessible fallback or fail-closed state; retries are capped at two per surface transition and teardown is attempted once plus one forced provider cleanup. UI failure never mutates canonical workflow/gate state.

Roll out additively behind `TASKPLANE_HOST_NATIVE_UX`: first canonical snapshots and audit-only adapters, then dashboard/fan-out, PiP, approval receipts, and previews per independently negotiated capability. Compare Claude/Codex semantic goldens and retain bounded legacy widgets/artifacts throughout. Rollback disables new native sessions, closes active PiP/previews, retains receipt/audit readers, and routes every canonical snapshot to the legacy accessible fallback. No stored canonical data is migrated or deleted.

Module owners: host-capability owner extends negotiation; presentation owner owns `host_native.py` and dashboard projection; runtime/validation owner owns `preview_runtime.py`; existing loop/review/command owners publish canonical events only. Delivery order is semantic model → negotiation/fallback → dashboard/fan-out → PiP/tokens → approval receipts → preview runtime → cross-host compatibility.
