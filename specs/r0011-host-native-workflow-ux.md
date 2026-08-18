# Specification — host-native workflow UX for Codex and Claude

## Problem

Taskplane's canonical workflow state is richer than the experience currently
available inside Codex and Claude. Users need persistent progress, agent fan-
out, approvals, dashboards, and executable previews expressed through each
host's native surfaces without losing semantic parity, auditability, truthful
telemetry, accessibility, or safe fallbacks.

## Users and context

Engineers use Taskplane for design, governed build, and dynamic code review in
Codex and Claude. They should understand what is running, what agents are doing,
why a decision is needed, what evidence supports it, and what the built or
reviewed experience actually looks like without leaving the conversation.

The Product contract follows the official OpenAI plugin UI guidance at
https://developers.openai.com/plugins/concepts/ui-guidelines: Picture-in-
Picture is for persistent live sessions and updates until it closes when the
session ends; inline cards remain single-purpose with at most two primary
actions and avoid nested scrolling/deep navigation; carousels present 3–8
visually consistent items with concise metadata; fullscreen supports rich
multi-step exploration while retaining conversation; native system tokens,
responsive layout, alt text, and WCAG AA apply. Claude may expose different
native capabilities, so parity is semantic and capability-negotiated rather
than dependent on identical APIs.

## In scope

- Picture-in-Picture live workflow progress on supported Codex and Claude
  surfaces, including stage, active work, completion, attention state, and
  truthful token usage.
- Native agent fan-out visualization showing dispatched agents, roles, scoped
  work, dependency/wave relationships, current state, and completed outcome.
- Context-rich native approval presentation with the decision, evidence,
  consequences, gate owner, and no more than two primary actions per inline
  decision surface.
- Native dashboard projection by host, with each canonical component rendered
  through supported native UI and large repeated datasets paged into 3–8-item
  carousels rather than nested scrolling.
- Capability-negotiated disposable sandbox and hosted/browser preview for
  design prototypes, build results, and dynamic code-review validation,
  including an integrated side-panel experience where supported.
- One canonical semantic state and audit trail shared by all host projections,
  with explicit capability and fallback evidence for every requested surface.
- Accessible, responsive conversational fallbacks whenever a host-native
  surface is unavailable or cannot represent the complete state safely.

## Out of scope

- Requiring Claude and Codex to expose identical APIs, component names,
  lifecycle hooks, browser products, hosting products, or rendering behavior.
- Replacing Taskplane's workflow, requirement, graph, review, gate, token, or
  audit authority with state held only inside a host UI component.
- Inventing token counts, costs, completion percentages, agent activity,
  approvals, preview availability, or sandbox safety from model prose.
- Using Picture-in-Picture for completed static reports or leaving it open
  after its workflow session ends.
- Packing deep navigation, nested scrolling, or more than two primary actions
  into a single inline card.
- Auto-approving gates, hiding material decision context behind a fallback, or
  treating UI interaction without an authenticated receipt as human approval.
- Running untrusted prototypes or review targets outside a governed disposable
  sandbox, pushing sandbox changes, or exposing a hosted preview publicly by
  default.
- Redesigning individual Taskplane product flows, the dependency graph, review
  lens policy, model routing, billing semantics, or release/marketplace flow.

## Acceptance criteria

1. **Live progress uses a session-appropriate native surface.** On a host that
   supports persistent Picture-in-Picture, an active Taskplane workflow opens
   one PiP session showing workflow identity, current stage, active/completed
   work, attention-needed state, and last update; it responds to state changes
   and closes automatically at terminal completion/cancellation/failure.
   **Verify:** lifecycle tests on Codex and every Claude capability profile
   assert one open session, ordered updates, no stale state, and automatic
   close; completed static workflows never open persistent PiP.

2. **Token usage is truthful.** Live progress reports observed raw/effective
   token usage and its scope/source when the host provides it; missing,
   delayed, partial, or unsupported telemetry is labeled unavailable or
   partial rather than zero or estimated. **Verify:** complete, incremental,
   delayed, absent, malformed, and cross-provider usage fixtures reconcile to
   the canonical token record and never fabricate a value.

3. **Agent fan-out is natively understandable.** Supported hosts visualize
   every dispatched agent with stable task/slot identity, role, scope, wave or
   dependency relationship, state, attention status, and outcome; parallel and
   serial relationships remain distinguishable. **Verify:** serial, parallel,
   mixed-wave, retry, cancellation, failure, and resumed-agent fixtures match
   the canonical dispatch/audit record without duplicate or phantom agents.

4. **Approval surfaces carry decision context.** Each native approval view
   shows the exact decision, why it is required, target/revision, relevant DoR
   or findings evidence, consequences of each choice, gate owner, and current
   approvability. It exposes at most two primary actions in an inline card and
   moves richer exploration to an appropriate detail/fullscreen surface.
   **Verify:** plan, design, dynamic-validation, review, and final-signoff
   fixtures assert complete decision context, action count, and evidence links.

5. **Approval remains authoritative and idempotent.** A native action advances
   a gate only after a valid human interaction receipt is bound to the current
   decision, target, revision, and actor; stale, duplicate, replayed, disabled,
   unauthenticated, or wrong-revision actions cannot advance state.
   **Verify:** positive and negative receipt fixtures produce exactly one audit
   decision and no action from presentation-only or fallback content.

6. **Dashboard components use native host presentation.** The canonical
   workflow, DoR, dependency/impact, agents, lenses, criteria, findings,
   validation, artifacts, and gate components each have a semantically complete
   native projection when supported. Host styling may differ while values,
   ordering rules, status, provenance, and available actions stay equivalent.
   **Verify:** canonical-state snapshots project on Codex and Claude with
   semantic equality despite allowed host-specific presentation differences.

7. **Large repeated datasets use bounded carousels.** When a dashboard
   component contains more items than fit its concise inline form, the native
   projection uses carousel pages containing 3–8 visually consistent items
   with concise metadata, deterministic ordering, total/current position, and
   no finding or item loss. **Verify:** 0, 1, 3, 8, 9, 120, and multi-page item
   fixtures cover navigation, stable identity, filtering, and round-trip count.

8. **Inline experiences stay conversational.** Inline cards remain single-
   purpose, responsive, free of nested scrolling and deep navigation, and use
   fullscreen/detail presentation for rich multi-step exploration while
   retaining the conversational composer. **Verify:** automated structure and
   viewport tests cover action limits, overflow, focus, composer availability,
   and small/large datasets.

9. **Design, build, and review can show working previews.** When supported and
   authorized, Taskplane can present a design prototype, built result, or
   dynamically reviewed application in an integrated hosted/browser preview,
   including the right-side panel where the host provides it, rather than only
   a static image or page artifact. **Verify:** one scenario per flow opens the
   pinned preview, records its identity and lifecycle, supports user
   interaction, and links observed behavior to canonical evidence.

10. **Preview execution is governed and disposable.** Executable previews run
    only in a registered sandbox/hosting scope with pinned source, explicit
    capability and authorization, bounded lifetime/resources, no push by
    default, and auditable network, build, repair, and teardown outcomes.
    **Verify:** valid, unavailable, denied, build-failed, timeout, attempted-
    push, escaped-path, external-network, and teardown fixtures fail safely and
    leave the source checkout and remotes unchanged.

11. **Capabilities negotiate behavior explicitly.** Before presenting PiP,
    visualization, carousel, approval, sandbox, hosting, browser, or side-panel
    behavior, Taskplane records host/version, capability source/confidence,
    selected native surface, limitations, and fallback. Unknown or contradictory
    capability never becomes a false supported state. **Verify:** supported,
    unsupported, partial, unknown, stale, contradictory, and changed-mid-run
    profiles select deterministic behavior on both hosts.

12. **Fallbacks preserve semantic parity.** If a native capability is absent,
    the user still receives the same canonical state, evidence, decision and
    safe action through bounded accessible conversation/artifact presentation;
    the fallback states what is unavailable and never claims the user declined
    it. **Verify:** disable each capability independently and compare canonical
    values, audit records, gate outcomes, and user-reachable information with
    the native scenario.

13. **Native UI is accessible and system-consistent.** All native/fallback
    surfaces use host/system design tokens, fonts, spacing, responsive text,
    meaningful labels and alt text, visible focus, keyboard operation, WCAG AA
    contrast, and non-color status cues. **Verify:** automated accessibility,
    keyboard, text-scaling, reduced-motion, light/dark theme, and responsive
    viewport checks on both host projections.

14. **One canonical state drives every surface.** PiP, fan-out, approvals,
    dashboards, carousels, previews, fallbacks, and durable artifacts reference
    the same workflow/run, target, canonical revision, task/slot identities,
    evidence, and gate state; host UI cannot independently mutate review truth.
    **Verify:** concurrent updates, reconnect/resume, stale UI, duplicated event,
    host switch, and terminal close fixtures preserve one ordered audit history
    and reject conflicting presentation state.

15. **Existing non-native flows remain compatible.** Hosts or environments
    without these capabilities continue to complete existing Taskplane design,
    build, review, status, approval, and artifact flows without lost evidence or
    weaker gates. **Verify:** existing golden scenarios plus native-capability
    matrices pass without removed, skipped, xfailed, loosened, or reclassified
    governance assertions.

## Non-functional requirements

- `security`: Native actions, previews, sandbox/hosting, browser interaction,
  and capability claims are authenticated, target-bound, least-privilege, and
  fail closed; UI state never becomes authority and executable previews cannot
  push, escape isolation, leak secrets, or become public by default.
- `architecture`: One versioned canonical semantic model and audit stream drive
  all Claude/Codex native projections and fallbacks; host adapters negotiate and
  render capabilities without creating parallel workflow truth.
- `accessibility`: Native and fallback surfaces meet WCAG AA, support keyboard
  and responsive text, retain focus/composer access, provide alt text and
  semantic labels, and never communicate status by color alone.
- `integrability`: PiP, visualization, approval, dashboard, carousel, preview,
  and capability contracts are versioned and tolerate additive host evolution
  without hard-coding identical Claude/Codex APIs.
- `privacy-compliance`: UI and preview telemetry minimize user/repository data,
  redact credentials, secrets, personal paths, prompts, and unrelated content,
  and expose preview access only to the authorized conversation/session.
- `sre`: Every native surface has explicit open/update/attention/terminal/error
  lifecycle, bounded retries and payloads, reconnect/resume behavior, teardown,
  and an actionable fallback rather than a stuck or falsely completed state.
- `scalability`: Agent waves, token events, findings, criteria, and dashboard
  data remain responsive through bounded updates and 3–8-item carousel pages;
  host payloads do not scale as full-history copies per update.
- `cost-finops`: Token reporting is source-attributed and never estimated as
  observed; UI update coalescing and previews have bounded resource budgets and
  do not trigger redundant model turns or persistent hosting after completion.

## Contract handoff

- `scope_paths`:
  - `taskplane/dashboard.py`
  - `taskplane/loop.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/command_runtime.py`
  - `taskplane/command_adapters.py`
  - `taskplane/host_capabilities.py`
  - `taskplane/review.py`
  - `workflows/**`
  - `skills/**`
  - `agents/**`
  - `hooks/**`
  - `.codex-plugin/**`
  - `.claude-plugin/**`
  - `docs/**`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`: canonical workflow redesign, graph/lens-policy changes,
  billing inference, unauthenticated approvals, non-disposable/public-by-default
  previews, identical-host-API assumptions, and release/marketplace work.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependencies: none.
- contracts:
  - `contract:host-native-progress`
  - `contract:host-native-visualization`
  - `contract:host-native-approval`
  - `contract:host-native-dashboard`
  - `contract:host-preview-runtime`
  - `contract:host-capability-negotiation`

This cross-host, capability-negotiated, security-sensitive UX contract changes
several runtime boundaries and requires Design before Build. There are no
blocking Product questions.
