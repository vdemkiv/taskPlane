# Archived R-0002: Engineering review remediation — high first, all severities

## Product authority

This specification defines the WHAT and DONE state for correcting every open
finding from the full deep Engineering Manager review of Taskplane 2.17.25.
The review is bound to Git revision
`00cd4f2c8183e57b6eae3f0cb6b0c580e00fe085`; its canonical evidence is
`.em-review/findings.json` (34 high, 28 medium, 10 low).

The user explicitly requires Design to begin now, high-severity corrections to
be fixed first, all medium and low findings to remain in scope, and the work to
be split into safe waves that exploit pairwise-disjoint parallel lanes. Low
findings must accompany the earliest dependency-safe high or medium wave; they
must not form a low-only tail or delay a blocking high owner. This Product
artifact authorizes requirement definition and Design/Plan only. It does not
authorize implementation, merge, push, tag, packaging, publication, or
release.

## Problem

The deep review found 72 open issues spanning durability,
authority, production wiring, security, privacy, accessibility, operational
boundedness, release compatibility, CI reproducibility, documentation, and
test integrity. The raw review evidence is not yet a delivery contract: every
finding must be traceable to a testable outcome, high-risk defects must be
closed before medium-only work begins, low work must be placed without
competing with high priority, and shared ownership must not create unsafe
parallel edits.

## Users and context

- Maintainers need a complete, stable inventory so no reviewed finding
  disappears during Design, planning, deduplication, or implementation.
- Operators need durable, bounded, truthful runtime behavior and release gates.
- Contributors need production entry points, CI quality gates, reproducible
  dependencies, isolated tests, and clear ownership boundaries.
- Dashboard users need accessible controls whose state and actions reflect
  actual workflow truth.
- Repository owners and data subjects need accurate privacy disclosures,
  minimized retention, and protected read-only review execution.

## In scope

- All 34 high findings `H-01` through `H-34`, all 28 medium findings `M-01`
  through `M-28`, and all 10 low findings `L-01` through `L-10` listed in the
  traceability register below.
- Production code, tests, CI, contracts, dashboards, documentation, privacy
  policy, packaging/release gates, and tracked terminal evidence directly
  implicated by those findings.
- Design of explicit ownership, dependency, interface, fixture, migration, and
  verification boundaries before Build.
- Focused and integrated verification sufficient to prove each finding closed,
  plus the final Taskplane suite and applicable static/type/lint checks.

## Out of scope

- New product features, unrelated refactors, cosmetic cleanup, historical
  R-0001/R-0012 work, or reopening completed R-0013 delivery outcomes.
- <a id="deferred-r0013-p1-w31-cold-start"></a>R-0013 W31 live-host and
  cold-start follow-up is deferred under priced debt
  [D-1301](#debt-d-1301).
- <a id="deferred-r0013-p1-release-repair"></a>R-0013 historical tag and
  release repair is deferred under priced debt [D-1302](#debt-d-1302).
- <a id="deferred-r0013-p2-pushed-sha-closure"></a>R-0013 pushed-SHA release
  closure is deferred under priced debt [D-1303](#debt-d-1303). These three
  records exhaust the deferred P1/P2 inventory; another deferral requires a
  new individually priced debt record and Product link.
- Reclassifying, suppressing, or deleting a finding merely to reduce counts.
- A broad rewrite when a bounded correction can satisfy the acceptance
  evidence; Design owns the choice of approach and trade-offs.
- Push, tag, marketplace upload, publication, release, PR merge, or mutation of
  `origin/main`.

### Priced deferred-work authority

<a id="debt-d-1301"></a>**D-1301 — W31 live-host/cold-start.** Owned by the
host-runtime maintainer; it re-enters when a live-host compatibility milestone
is scheduled and requires a new governed requirement plus cold-start proof.
Current repayment is estimated at 6 relative work units versus 11 after
compatibility and operator re-teaching accumulate.

<a id="debt-d-1302"></a>**D-1302 — historical release repair.** Owned by the
release-evidence maintainer; it re-enters when a supported historical release
is selected for repair and requires a repair requirement plus fetched-tag
evidence. Current repayment is 4 units versus 8 after migration/backfill grows.

<a id="debt-d-1303"></a>**D-1303 — pushed-SHA release closure.** Owned by the
release-closure maintainer; it re-enters when publication authority is granted
for an R-0013 successor and requires a release requirement plus fetched remote
SHA evidence. Current repayment is 5 units versus 9 after compatibility and
operator re-teaching grow.

<!-- taskplane:priced-debt-authority:v1:start -->
[
  {
    "debt_id": "D-1301",
    "deferred_item": "R0013-P1-W31-cold-start",
    "owner": "owner:host-runtime",
    "reentry_trigger": {
      "signal": "live-host-compatibility-milestone-scheduled",
      "threshold": "A named milestone has an owner, target host, and execution window",
      "action": "Open a governed requirement and require fresh W31 cold-start evidence"
    },
    "follow_up": "Complete live-host W31 and cold-start compatibility proof",
    "now_cost": {
      "unit": "relative-work-units", "backfill": 1, "migration": 1,
      "compatibility": 2, "operator_reteaching": 1, "other": 1,
      "total": 6, "basis": "Current bounded host matrix and retained W31 fixtures"
    },
    "later_cost": {
      "unit": "relative-work-units", "backfill": 2, "migration": 2,
      "compatibility": 3, "operator_reteaching": 2, "other": 2,
      "total": 11, "basis": "Additional host drift, migration, and operator re-teaching"
    },
    "references": [
      "specs/spec.md#deferred-r0013-p1-w31-cold-start",
      "specs/spec.md#debt-d-1301"
    ]
  },
  {
    "debt_id": "D-1302",
    "deferred_item": "R0013-P1-release-repair",
    "owner": "owner:release-evidence",
    "reentry_trigger": {
      "signal": "historical-release-selected-for-repair",
      "threshold": "A supported historical release and exact repair target are approved",
      "action": "Open a repair requirement and require fetched-tag evidence"
    },
    "follow_up": "Repair the selected historical tag and release evidence chain",
    "now_cost": {
      "unit": "relative-work-units", "backfill": 1, "migration": 1,
      "compatibility": 1, "operator_reteaching": 0, "other": 1,
      "total": 4, "basis": "Current release evidence and retained compatibility fixtures"
    },
    "later_cost": {
      "unit": "relative-work-units", "backfill": 2, "migration": 2,
      "compatibility": 2, "operator_reteaching": 1, "other": 1,
      "total": 8, "basis": "Expected tag drift, backfill, and compatibility migration"
    },
    "references": [
      "specs/spec.md#deferred-r0013-p1-release-repair",
      "specs/spec.md#debt-d-1302"
    ]
  },
  {
    "debt_id": "D-1303",
    "deferred_item": "R0013-P2-pushed-sha-closure",
    "owner": "owner:release-closure",
    "reentry_trigger": {
      "signal": "r0013-successor-publication-authorized",
      "threshold": "Attributed publication authority names the successor and remote",
      "action": "Open a release requirement and require fetched remote SHA proof"
    },
    "follow_up": "Close pushed-SHA release evidence for the authorized successor",
    "now_cost": {
      "unit": "relative-work-units", "backfill": 1, "migration": 1,
      "compatibility": 1, "operator_reteaching": 1, "other": 1,
      "total": 5, "basis": "Current remote verification path and release fixtures"
    },
    "later_cost": {
      "unit": "relative-work-units", "backfill": 2, "migration": 1,
      "compatibility": 2, "operator_reteaching": 2, "other": 2,
      "total": 9, "basis": "Expected remote drift, compatibility, and operator re-teaching"
    },
    "references": [
      "specs/spec.md#deferred-r0013-p2-pushed-sha-closure",
      "specs/spec.md#debt-d-1303"
    ]
  }
]
<!-- taskplane:priced-debt-authority:v1:end -->

## Functional requirements

1. Preserve one immutable remediation mapping for every `H-*`, `M-*`, and
   `L-*` entry below, including original severity, lens, file/line anchor,
   title, status, Design owner, planned task, test/evidence selector, and final
   disposition.
2. Treat all 34 high findings as blocking. No medium-only Build task may start
   until the high-severity closure gate proves every `H-*` entry fixed or the
   human explicitly changes scope. A low companion lane may run before that
   gate only when it is pairwise-disjoint from unfinished high work or is
   owned by the same high integration task.
3. Split high work into the three ordered outcome waves below. Work inside a
   wave is parallel only when Design/Plan proves pairwise-disjoint file and
   interface ownership; shared files, fixtures, schemas, and public contracts
   require one named integration owner or an explicit dependency. Place the
   nominated low companions in H2/H3 without reducing high priority.
4. After the high gate passes, split the 28 medium findings into the two
   outcome waves below under the same ownership and dependency rules. Place
   the remaining low findings into their earliest related M1/M2 lanes; do not
   create a low-only final wave.
5. Corroborating findings that share one root cause may share one correction,
   but every finding retains independent traceability and its own closure
   evidence. Deduplication never removes an acceptance obligation.
6. Correct dead or unwired production surfaces only by making their supported
   production responsibility reachable and tested, or by removing the unused
   surface and its claims. Tests alone do not count as production wiring.
7. Python quality closure includes enforceable lint and strict type checking,
   explicit compatibility typing instead of bare suppressions, production
   reachability checks for reviewed entry points, duplication disposition,
   bounded module ownership, and test isolation.
8. The final candidate must preserve supported behavior, pass all focused
   finding selectors, pass applicable accessibility and documentation checks,
   pass the complete Taskplane test suite once at final integration, and have
   no unresolved high, medium, or low finding.

## Required delivery waves

These are outcome/prioritization boundaries, not an implementation design.
Plan may create multiple pairwise-disjoint tasks inside each wave but may not
move a medium-only task ahead of the high closure gate. Low companions are
non-blocking lanes: they enter the earliest named wave below only when their
file/interface ownership is disjoint from unfinished high work, or they share
the same named integration owner. Any overlap is serialized behind that owner,
never used to delay high closure, and never moved into a low-only tail.

| Wave | Eligibility and outcome | Finding obligations |
| --- | --- | --- |
| H1 — integrity and authority foundation | First. Close atomicity, durability, exclusive authority, read-only safety, release authority, compatibility, and blocking-wait foundations. | H-03–H-08, H-14, H-15, H-19, H-22, H-26, H-30, H-34 |
| H2 — production wiring and operating bounds | Starts after H1 interfaces are accepted. Close Python quality enforcement, dead/unwired production surfaces, review cost/scale bounds, architecture-map consumption, and enforceable native-usage truth. The dependency-neutral glob consolidation is a disjoint low companion. | H-09–H-13, H-27–H-29, H-31, H-33; L-02 |
| H3 — human-facing and terminal truth | Starts after H1 authority/durability contracts are stable. Close accessible interaction, truthful dashboard behavior, privacy retention, and current exact-SHA terminal evidence. Contrast and workstation-identity minimization are owned with the related dashboard/privacy integrations. | H-01, H-02, H-16–H-18, H-20, H-21, H-23–H-25, H-32; L-01, L-04 |
| High closure gate | Required before any medium-only Build. Every H finding has independently checkable passing evidence, shared integrations are green, and no high issue is merely waived or relabeled. | H-01–H-34 |
| M1 — engineering foundations | After the high gate. Close architecture/scanner inputs, typing/cost-cap boundaries, CI security/reproducibility, mandatory proof paths, retry/test isolation, and recorded architectural trade-offs. Seal the asset-generation dependency and scoped runtime test seams in the related dependency/testability lanes. | M-02–M-04, M-06, M-07, M-14, M-15, M-17–M-19, M-24, M-26–M-28; L-06, L-10 |
| M2 — user-facing truth and documentation | After the high gate; may run alongside disjoint M1 lanes. Close motion/state semantics, localization, privacy defaults/disclosure, product copy, concurrency proof, documentation, and priced-debt traceability. Own grapheme-safe display with i18n, and version-neutral help plus CLI/navigation/onboarding corrections with their related documentation owners. | M-01, M-05, M-08–M-13, M-16, M-20–M-23, M-25; L-03, L-05, L-07–L-09 |
| Final integration and evaluation | After M1/M2. Reconcile shared interfaces, run each finding’s focused proof, then run the full suite once on the exact clean candidate. | H-01–H-34, M-01–M-28, and L-01–L-10 |

## Acceptance criteria

1. **AC1 — inventory and Design traceability are complete.** Design and Plan
   contain exactly 72 open remediation obligations matching the canonical
   review: 34 high, 28 medium, and 10 low. Every `H-*`, `M-*`, and `L-*` id
   maps to a named owner, affected boundary, dependency classification,
   planned task, and verification evidence. Each `L-*` row also names its
   companion wave and whether it is pairwise-disjoint or shares an integration
   owner. A missing id, severity drift, unowned shared path, unsafe early low
   lane, or untraceable deduplication blocks Design/Plan approval. Verify by
   machine-comparing the accepted Design/Plan traceability table with
   `.em-review/findings.json` at the bound review SHA.
2. **AC2 — H1 integrity and authority findings are closed.** Every H1 id
   (`H-03`–`H-08`, `H-14`, `H-15`, `H-19`, `H-22`, `H-26`, `H-30`, `H-34`)
   has a focused positive proof and a failure/negative proof where the finding
   concerns atomicity, durability, authority, mutation, compatibility, or
   bounded waiting. Verify on the production entry point and persisted state,
   not only on a helper in isolation.
3. **AC3 — H2 production wiring and operating-bound findings are closed.**
   Every H2 id (`H-09`–`H-13`, `H-27`–`H-29`, `H-31`, `H-33`) is closed.
   Verification proves enforceable Python lint/type gates, supported production
   reachability or intentional removal of every reviewed dead surface,
   bounded processing for reviewed unbounded paths, executable consumption of
   accepted architecture data, and honest budget behavior on hosts without
   token totals. `L-02` also proves one dependency-neutral glob matcher is
   consumed by both routing layers with parity evidence.
4. **AC4 — H3 human-facing and terminal-truth findings are closed.** Every H3
   id (`H-01`, `H-02`, `H-16`–`H-18`, `H-20`, `H-21`, `H-23`–`H-25`, `H-32`)
   is closed. Verify keyboard and ARIA behavior, honest pending/success/error
   transitions, functional or truthfully absent fallback actions, sanitized
   and bounded retention, and exact-SHA terminal evidence that describes the
   current integration candidate. `L-01` proves the shipped metadata color
   pair reaches WCAG AA contrast, and `L-04` proves shared metadata omits or
   pseudonymizes workstation identity while the private locator remains usable.
5. **AC5 — the high closure gate is fail-closed.** Before any medium-only Build
   begins, an independent evaluation proves `H-01` through `H-34` closed at
   the same candidate SHA, their focused tests pass, shared integrations are
   green, and no high finding remains open, suppressed, downgraded, or backed
   only by self-attestation. Injecting one open/missing high result must block
   medium eligibility.
6. **AC6 — all engineering-foundation medium findings are closed.** Every M1
   id (`M-02`–`M-04`, `M-06`, `M-07`, `M-14`, `M-15`, `M-17`–`M-19`, `M-24`,
   `M-26`–`M-28`) has focused passing evidence. CI dependency integrity,
   credential minimization, mandatory proof execution, bounded retry, test
   isolation, architecture decisions, and scanner/typing/cost-cap boundaries
   are verified on their real consumers. `L-06` proves Pillow is pinned only
   in the development/asset-generation toolchain with a documented reproducible
   regeneration command; `L-10` proves runtime dependency bindings restore
   safely across nested and parallel tests.
7. **AC7 — all user-facing medium findings are closed.** Every M2 id (`M-01`,
   `M-05`, `M-08`–`M-13`, `M-16`, `M-20`–`M-23`, `M-25`) has focused passing
   evidence. Motion/state semantics, localization behavior, storage defaults,
   public product/privacy claims, documentation, deterministic concurrency
   proof, and priced-debt links match actual supported behavior. `L-03` proves
   human-visible truncation preserves Unicode grapheme clusters and the full
   accessible value. `L-05` proves the built-in tour is version-neutral or
   derived from current release data. `L-07` proves generated positional enum
   choices are documented, `L-08` proves the generated CLI reference is
   reachable from primary documentation navigation, and `L-09` proves
   onboarding consistently describes all four context documents.
8. **AC8 — exact-candidate final evaluation passes.** At one clean exact
   candidate SHA, an independent evaluator verifies all 72 trace rows closed,
   runs each focused selector and applicable static/type/lint/accessibility/
   documentation check, then runs `python3 -m pytest taskplane/tests -q` once.
   The candidate has zero new failures, no open high, medium, or low finding,
   no finding silently deleted, and no unplanned low-only tail.

## Non-functional requirements

- **security:** Read-only contracts cannot launch opaque mutating commands;
  authority-sensitive writes, packaging, release gates, credentials, CI
  dependencies, and terminal capabilities are least-privilege, integrity
  checked, and fail closed. Security-sensitive negative tests must exercise
  the real production boundary.
- **architecture:** Every supported validator, preview, coordinator, and
  architecture-map surface has one explicit production composition root and
  consumer, or is removed with its claims. Shared modules/interfaces have one
  owner per wave; Design records dependency direction, compatibility, recovery
  trade-offs, and revisit triggers without expanding existing import-cycle
  ratchets.
- **code-quality:** Python CI enforces the selected lint and strict type policy;
  unexplained bare suppressions are absent; reviewed duplication is removed or
  consolidated behind a dependency-neutral boundary or justified against an
  explicit architectural boundary; dead/unwired code is
  reachable through supported production flows or removed; large-module edits
  remain bounded to named ownership seams.
- **data-safety:** Critical state, migrations, journals, observations, CAS
  successors, immutable evidence, and terminal projections are durable,
  atomic at their declared boundary, restart-safe, idempotent where required,
  and never acknowledge partial authority as complete.
- **privacy-compliance:** Persisted commands, diffs, identities, free text, and
  storage defaults follow explicit minimization, sanitization, retention, and
  consent rules. Shared metadata does not disclose absolute workstation paths
  or raw workstation identity; the private locator retains only what is needed.
  `PRIVACY.md` and public documentation accurately describe actual local and
  remote behavior.
- **accessibility:** Dashboard tabs, graph controls, motion, state, focus,
  keyboard input, announcements, and small metadata contrast satisfy their
  declared semantics and WCAG AA where applicable, verified with focused
  automated checks plus an interaction review.
- **integrability:** Compatibility checks include the last released generation;
  shared schemas and public boundaries have migration/consumer evidence; one
  root-cause correction may satisfy corroborating lenses without dropping any
  trace row.
- **sre:** Sandbox preparation, repository acquisition, historical transcript
  processing, preview scanning, and review budgets are bounded and expose
  actionable timeout/error states without indefinite blocking or retry storms.
- **cost-finops:** Standalone review and deep-routing behavior retain enforceable
  ceilings even when optional imports or telemetry sources fail; failure is
  explicit rather than silently disabling limits.
- **testability:** High-risk paths have positive and adversarial focused proofs;
  canonical proof suites are required in CI; tests do not permanently mutate
  process-global runner or runtime-service state; scoped dependency bindings
  restore correctly for nested and parallel tests; concurrency tests use
  events/conditions rather than timing sleeps.
- **i18n:** User-facing dashboard strings and plural rules use a locale-capable
  path with deterministic fallback behavior, rather than hard-coded English
  semantics presented as localization support; visible truncation preserves
  Unicode grapheme clusters and the full accessible value.
- **services-selection:** Development-only asset tooling is explicitly
  separated from the stdlib-only runtime and pinned reproducibly.
- **tech-writer:** Generated CLI enum values, primary documentation navigation,
  the four-document onboarding model, and the current-release help journey are
  accurate, reachable, and protected by regeneration or content checks.

## Finding traceability register

The ids below are stable for this remediation requirement and map one-to-one,
in canonical review order, to `.em-review/findings.json`.

### High severity

| ID | Lens | Anchor | Required outcome |
| --- | --- | --- | --- |
| H-01 | accessibility | `taskplane/dashboard.py:4846` | Complete keyboard-operable ARIA tab behavior. |
| H-02 | accessibility | `taskplane/depgraph.py:2083` | Dependency-graph button nodes activate from the keyboard. |
| H-03 | architecture | `taskplane/terminal_truth.py:366` | Terminal truth has a supported production composition root. |
| H-04 | architecture | `taskplane/terminal_truth.py:397` | Restart preserves or safely reacquires finalization authority. |
| H-05 | architecture | `taskplane/terminal_truth.py:580` | Immutable evidence cannot expose a truncated final object. |
| H-06 | backend | `taskplane/delivery_ports.py:496` | CAS permits only one valid successor per predecessor. |
| H-07 | backend | `taskplane/run_store.py:797` | Stage commit and run-journal event cannot diverge permanently. |
| H-08 | backend | `taskplane/producer_observation.py:705` | Durable observation intent precedes authority consumption. |
| H-09 | code-quality | `.github/workflows/ci.yml:98` | Python lint and strict type gates are enforced by CI. |
| H-10 | code-quality | `taskplane/native_authority.py:537` | Native-authority validation is production-wired or removed. |
| H-11 | code-quality | `taskplane/design_sweep.py:446` | Design-sweep validation is production-wired or removed. |
| H-12 | code-quality | `taskplane/preview_runtime.py:487` | Supported preview entry points are live-wired or removed. |
| H-13 | cost-finops | `taskplane/tp.py:5447` | Standalone reviews enforce a default token ceiling. |
| H-14 | data-safety | `taskplane/taskplane_lite.py:99` | Critical state is durable before success acknowledgement. |
| H-15 | data-safety | `taskplane/taskplane_lite.py:5775` | Interrupted cross-filesystem migration cannot make a partial copy authoritative. |
| H-16 | design | `taskplane/dashboard.py:5237` | Fallback dashboard actions work or are truthfully unavailable. |
| H-17 | design | `taskplane/dashboard.py:5205` | Fallback dashboard retains required workflow evidence. |
| H-18 | design | `taskplane/dashboard.py:4619` | Gate actions report success only after confirmed delivery. |
| H-19 | devops | `scripts/package_openai.py:573` | Marketplace packaging cannot bypass release authority. |
| H-20 | frontend | `taskplane/dashboard.py:5238` | Host-native actions/composer are functional or not rendered. |
| H-21 | frontend | `taskplane/dashboard.py:4621` | Async gate controls expose pending, confirmed, and failed states honestly. |
| H-22 | integrability | `design/compatibility.json:47` | Compatibility gate covers the last released generation. |
| H-23 | privacy-compliance | `taskplane/command_runtime.py:693` | Durable command logs do not retain personal data verbatim. |
| H-24 | privacy-compliance | `taskplane/loop.py:4145` | Canonical review artifacts have a bounded raw-diff retention policy. |
| H-25 | privacy-compliance | `taskplane/taskplane_lite.py:6080` | Audit identities and free text are sanitized before append. |
| H-26 | project-management | `design/compatibility.json:47` | Release gate includes the last released production generation. |
| H-27 | scalability | `taskplane/tp.py:2363` | Screened actions do not repeatedly reparse unbounded transcript history. |
| H-28 | scalability | `taskplane/review.py:698` | Review actions avoid scanning every historical host transcript. |
| H-29 | scalability | `taskplane/preview_runtime.py:217` | Preview startup bounds workspace materialization and hashing. |
| H-30 | security | `taskplane/taskplane_lite.py:349` | Read-only contracts cannot execute opaque mutating launchers. |
| H-31 | solution-design | `components.yaml:13` | Accepted architecture mapping is consumed by an executable graph check. |
| H-32 | solution-design | `exports/terminal/r0013/106af4631ab5b5c041055b9b9b918d78a18ae50b.json:1` | Terminal evidence names the current main-integration candidate. |
| H-33 | solution-design | `exports/terminal/r0013/.native-usage-receipt.json:46` | Budget claims match enforceable host telemetry capabilities. |
| H-34 | sre | `taskplane/review.py:1174` | Validation-sandbox preparation has a bounded timeout and recovery state. |

### Medium severity

| ID | Lens | Anchor | Required outcome |
| --- | --- | --- | --- |
| M-01 | accessibility | `taskplane/dashboard.py:487` | Indefinite motion honors reduced motion and has a pause/stop path. |
| M-02 | architecture | `design/contract.json:756` | Accepted decomposition is an actual scanner input. |
| M-03 | code-quality | `taskplane/dispatch_telemetry.py:20` | Compatibility typing uses explicit, checked boundaries rather than bare ignores. |
| M-04 | cost-finops | `taskplane/lens.py:318` | Routing import failure cannot disable the deep-agent cost cap. |
| M-05 | design | `taskplane/dashboard.py:5225` | Component states distinguish success, pending, warning, and failure. |
| M-06 | devops | `.github/workflows/ci.yml:437` | Pull-request jobs do not retain unused checkout credentials. |
| M-07 | devops | `.github/workflows/ci.yml:90` | CI test dependencies are reproducibly pinned. |
| M-08 | i18n | `taskplane/dashboard.py:192` | Dashboard copy uses a locale-backed translation path. |
| M-09 | i18n | `taskplane/dashboard.py:197` | Plural behavior supports locale rules beyond English one/other. |
| M-10 | privacy-compliance | `PRIVACY.md:58` | Privacy disclosure matches collection and third-party network behavior. |
| M-11 | privacy-compliance | `taskplane/taskplane_lite.py:5616` | Repository settings cannot silently default a new user to shared storage. |
| M-12 | product | `README.md:72` | Primary journey documents the current approval model. |
| M-13 | product | `.codex-plugin/plugin.json:27` | Marketplace copy accurately describes actual lens execution. |
| M-14 | qa | retired | Historical design-sweep replay was removed from CI; present-state design-sweep behavior remains covered by current wiring contracts. |
| M-15 | qa | `taskplane/tests/test_r0001_live_host_canary.py:16` | A required end-to-end Codex producer-event path exists. |
| M-16 | qa | `taskplane/tests/test_review_routing.py:1405` | Publication-concurrency proof is event-driven, not sleep-ordered. |
| M-17 | security | `.github/workflows/ci.yml:90` | CI dependency installation has pinned integrity evidence. |
| M-18 | services-selection | `.github/workflows/ci.yml:90` | CI resolves a sealed pytest dependency tree. |
| M-19 | sre | `taskplane/repository.py:758` | Repository acquisition uses bounded, non-duplicated backoff. |
| M-20 | tech-writer | `PRIVACY.md:59` | Network-use documentation matches remote repository acquisition. |
| M-21 | tech-writer | `README.md:248` | Public prerequisites state Python 3.10+ accurately. |
| M-22 | tech-writer | `docs/loop-design.md:124` | Loop synopsis includes the supported `unavailable` outcome. |
| M-23 | tech-writer | `docs/onboarding.md:24` | Onboarding discloses `tp init` legacy-KB migration behavior. |
| M-24 | testability | `taskplane/tests/__init__.py:34` | Test imports do not permanently rewire process-global runner state. |
| M-25 | time-to-market | `specs/spec.md:218` | Deferred work is linked to explicit priced debt records. |
| M-26 | tradeoffs | `design/contract.json:727` | Terminal-capability choice records the recoverability it spends. |
| M-27 | tradeoffs | `docs/loop-design.md:157` | One-way stage migration records alternatives and a revisit trigger. |
| M-28 | tradeoffs | `docs/loop-design.md:224` | Loop-engine ownership is represented in the decision registry. |

### Low severity

Low ids preserve the canonical order of low-severity rows in
`.em-review/findings.json`. Their companion-wave assignment is a Product
priority boundary; Design still owns the exact implementation graph and must
prove file/interface disjointness or name the shared integration owner.

| ID | Companion wave | Lens | Anchor | Required outcome |
| --- | --- | --- | --- | --- |
| L-01 | H3 | accessibility | `taskplane/dashboard.py:560` | Small step metadata reaches WCAG AA contrast in the shipped palette. |
| L-02 | H2 | code-quality | `taskplane/graph_primitives.py:467` | Routing layers consume one dependency-neutral glob matcher with parity tests. |
| L-03 | M2 | i18n | `taskplane/dashboard.py:1560` | Human-visible truncation preserves grapheme clusters and full accessible text. |
| L-04 | H3 | privacy-compliance | `taskplane/taskplane_lite.py:5753` | Shared metadata omits or pseudonymizes workstation identity and absolute private paths. |
| L-05 | M2 | product | `skills/tp-help/SKILL.md:45` | The built-in tour is version-neutral or derives current highlights from maintained release data. |
| L-06 | M1 | services-selection | `CONTRIBUTING.md:12` | Pillow is pinned in the development asset toolchain without entering the stdlib-only runtime. |
| L-07 | M2 | tech-writer | `docs/cli-reference.md:836` | Generated positional enum documentation includes choices and required/optional semantics. |
| L-08 | M2 | tech-writer | `README.md:358` | Primary documentation navigation links to the complete generated CLI reference. |
| L-09 | M2 | tech-writer | `docs/onboarding.md:166` | Context-storage instructions consistently describe the four-document model and each document's purpose. |
| L-10 | M1 | testability | `taskplane/build_c.py:68` | Runtime dependency bindings restore safely for nested and parallel tests. |

## Contract handoff

```yaml
scope_paths:
  - .em-review/findings.json
  - .github/workflows/ci.yml
  - .codex-plugin/plugin.json
  - CONTRIBUTING.md
  - taskplane/**/*.py
  - taskplane/tests/**/*.py
  - scripts/package_openai.py
  - skills/tp-help/SKILL.md
  - design/**
  - components.yaml
  - docs/**
  - README.md
  - PRIVACY.md
  - exports/terminal/r0013/**
  - specs/spec.md
out_of_scope:
  - unrelated product features or broad rewrites
  - historical R-0001/R-0012 remediation
  - push, tag, publication, release, PR merge, or origin/main mutation
contracts:
  - changes: contract:quality.review-remediation
  - changes: contract:runtime.durable-state-and-authority
  - changes: contract:delivery.production-wiring
  - changes: contract:dashboard.accessible-truthful-actions
  - changes: contract:privacy.retention-and-disclosure
  - changes: contract:release.compatibility-and-authority
  - changes: contract:ci.reproducible-python-quality
  - provides: resource:review.finding-traceability
dod:
  test_command: python3 -m pytest taskplane/tests -q
```

## Open questions

None. Design must select the bounded technical approach, exact ownership map,
interfaces, fixtures, compatibility strategy, and safe parallel task graph
without changing the 72-finding scope, the high-before-medium gate, or the
requirement that low findings have no dedicated tail wave.
