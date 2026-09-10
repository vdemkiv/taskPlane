# Harness refactor pickup — S0 instruments and trustworthy startup

Status: registered Product requirement R-0006 (draft); Design required. No Design, Plan, implementation, independent review, or human approval is claimed here. Date: 2026-09-09. Program label: R-HARNESS-01 (a source working label, not a registered R-id).

## Problem and users

Maintainers cannot safely judge the phase-runtime refactor when review bounds disagree, usage omits attempts or restarts, tests inherit the active host, and onboarding declares readiness for an unavailable bound run. S0 makes those instruments and startup results trustworthy before activation or retirement, while preserving the graph and review backlog as separately bounded delivery work.

Users are the project owner, fresh Product/Design/Plan/Build workers, independent evaluators, maintainers reviewing releases, and operators on Claude and Codex. The first requirement is the complete S0 step plus the closely related RF-8 startup correction; it is not the whole refactor.

## Authoritative intake and baseline

Read completely: `backlog/harness-refactor-spec.md` (H1–H11, S0–S3, G1–G7, N1–N4, C1–C6, D1–D5) and `backlog/graph-decomposition-review-backlog.md` (A1–A6, B1–B3, GD-1–GD-9, RF-1–RF-8). RF-8 was added during this pickup at the user's request and is additional to H5's hook-receipt criteria.

Current checkout: `da3cd4d9090b644c195b5bb02cc804f000149294`, after PR #20 and #21. Historical source reviews used `9a6a87e` and include advisory/unsealed findings; the older 70/341 SCC, 47.3–47.6% root share, 11/63 attempts, 65 failures/17 errors and timing/line totals are historical observations, not current measured facts. No inherited Design, R-0001 Design inventory, prior approval, or source review recommendation authorizes this requirement. The overwritten generic spec is retained at `specs/harness-refactor/pre-pickup-spec.md` as history only.

Bounded source inspection (read-only; not a code review or runtime reproduction):

| Source observation | Current evidence | Product consequence |
|---|---|---|
| H8.1 scanner lacks function-local edges | `taskplane/import_cycles.py` uses `ast.walk`; `scripts/ci_local.py` already runs its policy check. `taskplane/tests/fixtures/import-cycles.json` records 70 modules/343 edges, revision `fd5ff0c…` | Preserve and verify the existing instrument; do not build a duplicate or force the old 341-edge number. Establish a current report against the existing ratchet without loosening it. |
| H6/RF-2 mismatch and vague refusal | `review.canonical_diff_patch` defaults to 400,000 bytes; `loop._review_kernel` passes 2,000,000; standalone `cmd_review_start` uses `tp_target_diff` and raises generic derivation failure | Shared bounded review policy and specific refusal remain executable S0 work. The separate 16 MiB raw-delivery artifact ceiling is a different boundary and must not be conflated. |
| H5.2 host contamination | `tests/conftest.py` isolates the store and detects leaked mutations; `tests/__init__.py` scopes runtime patches but does not scrub inherited host-marker prefixes | Add explicit test invocation isolation; preserve store isolation and leak detection. No historical failure count is claimed reproduced. |
| H7.2 restart/full lineage | `native_session_meter.aggregate` counts physical sources; `fold_root_observations` rejects changed sources/backwards or unreconciled counters | Reuse existing sealed meter; prove or close restart and all-attempt coverage gaps. Never infer completion from helper existence or fabricate usage. |
| RF-8 false readiness | `tp._onboard_report` derives `ready` from project/Git/context and host capability, without a bound-run check. `cmd_repository` already has supported preparation recovery | Validate bound state before readiness, using incumbent owners. Do not bypass hooks or invent another setup layer. |
| GD-1–GD-4/GD-9 | `test_decompose.py` retains `unittest.mock` with only `import unittest`; graph code retains 60,000ms, elapsed measurement and global fanout logic; live-repository test remains | Retain graph correction slice. Runtime reproduction and current line binding belong to that slice's Design/Build. |
| RF-1 locales packaging | Claude package required/member lists inspected do not declare locales | Retain release-blocking installed-package smoke proof; no new archive was built in Product. |

The startup incident is observed pickup evidence: `loop resume` said `not_started`, onboarding said `ready:true`, initialization then refused because the locator referenced absent manifest for run `86f27d0105874c348b615f66bc4bfdca`. Granting normal filesystem access did not cure that state. Supported repository preparation established valid run `c2161602c3be470499783d96f7b2b682`, after which initialization succeeded. The old artifacts were preserved. This is distinct from a hook receipt failure.

## First requirement scope

S0 owns H8.1 (retain/verify cycle ratchet), H5.2–H5.3 (host test isolation/dependency declarations), H7.2 (restart-aware complete-lineage meter), H6.1–H6.3 and RF-2 (shared review diff bound, scoped override/recovery), RF-8 (bound-run readiness/recovery), and measurement baselines required before S1. H5.1/H5.4 existing fail-closed/advisory semantics are preservation constraints; full live-host phase receipt work remains S1/S3.

No existing requirement dependency is asserted for this first slice. Historical R-0001/R-0003/R-0005 references identify semantics/evidence, not a new dependency on their mutable runtime. Later registered requirements must use actual `--depends` R-ids; the roadmap labels below are not fabricated R-ids.

## Testable acceptance criteria (first requirement DoD)

1. **S0-AC01 — Preserve and measure the cycle ratchet (H8.1).** The existing scanner includes function-local import edges, emits deterministic semantic topology for the same source tree, and the CI check fails when a fixture adds an SCC member or prohibited internal edge while allowing genuine decreases. Record current revision, SCC membership/counts/edges and policy result; do not replace a stricter policy with the historical 70/341 snapshot. Verify with the existing public scanner/check entry point plus a small positive/negative graph fixture and the CI integration receipt. A failure is reported and corrected within scope or returned, never waived by baseline rewrite.
2. **S0-AC02 — Isolate ordinary tests from active hosts (H5.2).** Default test/CLI fixture invocations remove inherited `CLAUDE_*`, `CODEX_*`, and `TASKPLANE_*` host/session values before installing only explicit test-controlled store/identity values. Host tests opt in with declared markers; the caller environment is restored. A test seeded with sentinel values proves ordinary children cannot see them and opt-in host tests can see only their supplied values. Existing store isolation and environment-leak guard remain effective. Verify focused isolation cases and full suite cells launched under Claude and Codex markers; require zero failures/errors attributable to inherited host state and a green supported full suite.
3. **S0-AC03 — Make optional dependencies explicit (H5.3).** Tests needing Chromium, mypy or a specific Python capability declare that dependency and give a named skip when absent; a supported CI cell with the dependency installed must run the required behavior. Missing prerequisites must not hide a product failure or turn a mandatory host proof into PASS. Verify one absent/present case per affected dependency and record executed/skipped counts and reasons in the suite evidence.
4. **S0-AC04 — Count the complete run lineage exactly once (H7.2).** A sealed run includes root and every dispatched worker attempt/pickup, including resumed sessions, failed/cancelled attempts and terminal attempts with missing usage. Publish observed/expected attempt counts and missing identities. A counter restart, including a new cumulative value equal to the previous last-usage value, starts a new segment only when host-native identity/restart evidence proves it; exact replay adds zero. Ambiguous reset/source replacement remains explicitly unavailable/partial, not guessed. Verify a public meter/seal producer journey with root plus at least three workers, a worker pickup, proven equal-value reset, duplicate replay and ambiguous-reset negatives; asserted totals match known positive counters.
5. **S0-AC05 — Report honest coverage and the baseline (H7.2/H2.4 prerequisite).** Full lineage coverage is 100%; anything less marks the seal `partial` and the existing summary/retro headline states partial coverage. Unavailable usage is not zero. Record root share, total and uncached usage, coverage, host, source revision and wall time for a three-task governed wave. Root-share calculation is reproducible from the sealed totals; if coverage is incomplete the percentage is explicitly partial and cannot substantiate S1's ≤25% claim. Verify complete and one-missing-attempt variants and their consumer projection. This adds no unrelated retro redesign.
6. **S0-AC06 — One canonical diff policy at both review entries (H6.1/RF-2).** Standalone and loop Review call the incumbent canonical diff producer with one shared default byte bound and consistent scoped-input semantics. Set the shared default to the current loop's 2,000,000 bytes, preserving the existing loop capacity; neither path silently truncates, returns success for dropped evidence, nor borrows the separate 16 MiB raw artifact limit. An explicit positive `--max-diff-bytes N` override and `--paths` selection are accepted by standalone review, validated before collection, bound into target/evidence identity, and cannot broaden the active contract. Verify identical input/limit parity, invalid and out-of-scope values, and changed-parameter invalidation.
7. **S0-AC07 — Give actionable oversize refusals (H6.2/RF-2).** A synthetic 3 MB review diff fails closed at the shared default with `canonical_diff_too_large`, actual UTF-8 byte count, allowed byte bound and executable recovery using the supported scoped path or explicit limit option. A non-size derivation error stays distinguishable. Scoped retry or an authorized larger bound succeeds and contains exactly the selected evidence. Verify through public standalone and loop review entry points; the default refusal must not say only “derivation failed.”
8. **S0-AC08 — Review the release without synthetic history (H6.3).** A bounded public-CLI CI integration case opens the source release target `9a6a87e` versus `1cf41e9` using `--paths taskplane/` and an explicit sufficient limit if the measured selected diff exceeds the shared default. Record selected paths, bytes, resolved revisions and effective bound. Use available local history; no network or invented base is required. A small deterministic fixture covers the same selection and refusal semantics independently of that historical repository test. Zero-lens loop Evaluate/EM and the standalone-only review routing surface remain unchanged.
9. **S0-AC09 — Refuse false startup readiness (RF-8).** Before reporting `ready:true`/`next_action:ready`, onboarding validates any existing bound locator's manifest readability, schema/run identity, repository identity and checkout identity. Missing, corrupt, mismatched and access-denied state produce distinguishable non-ready reasons; a missing manifest is not represented as intentional rollback or stage-mode disablement. Valid bindings stay ready when other prerequisites pass; an unbound fresh checkout retains its supported setup path. Verify public onboarding cases for absent manifest, invalid JSON, wrong run/repository/checkout, denied access, valid binding and no locator.
10. **S0-AC10 — Recover without losing retained runs (RF-8).** Missing/corrupt/mismatched bound state supplies the incumbent `repository prepare <workspace>` recovery when applicable; access denial identifies access recovery instead of promising preparation can cure permissions. A public-CLI journey creates a stale locator and absent manifest, observes non-ready, executes the supplied supported preparation, then observes ready and successfully initializes the loop. All pre-existing retained run artifacts keep identical bytes; no automatic deletion, fabricated receipt, runtime-home substitution, or direct locator patch is permitted. Verify with isolated repositories/stores and sentinel retained artifacts, plus negative proof that invalid identity cannot be admitted.
11. **S0-AC11 — Preserve governance and deterministic evidence.** Claude and Codex paths retain R-0003 enforcement, real session-compatible hook checks, human-attributed advisory `verified_source:false`, existing human gates, and zero-lens Evaluate/EM. Changed instrument/refusal/readiness/usage fields use the incumbent schemas/owners or explicitly versioned additive contracts; unsupported evidence stays unavailable. Verify existing enforcement/host-parity negatives plus the S0 public journeys, and repeat semantic seal/projection checks for identical bound inputs. Metrics may change as telemetry but must not masquerade as source content identity.
12. **S0-AC12 — Deliver a traceable measured handoff.** The S0 evidence artifact maps every AC to its exact producer, command/selector, result and revision, separates observed results from unavailable host evidence, preserves the roadmap below, and reports current cycle, suite, root-share and lineage baselines. It records no S1 activation, S2 retirement or later backlog completion. Verify the acceptance map has all 12 AC ids and actual evidence; missing mandatory proof prevents completion under existing gates.

## Nonfunctional requirements

- **security:** Validate path selections, limit overrides, run/checkout identities and host receipts at existing trust boundaries. Fail closed on mismatched/ambiguous evidence; preserve R-0003 and advisory provenance. Negative tests must reject out-of-scope reads and false readiness; no weaker bypass is introduced.
- **architecture:** Reuse `import_cycles`, canonical Review, native/dispatch metering and run-storage/preflight owners. No new harness, secondary scanner, meter, store or readiness authority. Preserve/decrease the existing cycle ratchet. Design must name producer/consumer boundaries and keep ordinary telemetry separate from content identity.
- **data-safety:** Meter replay is idempotent; recovery does not delete or overwrite retained run artifacts. Verify sentinel byte identity and reset/replay totals; no migration or synthetic receipts.
- **integrability:** Same effective review policy across standalone/loop; preserve consumers with explicit field/version handling. Claude/Codex test and CLI contracts have equivalent refusal and readiness semantics.
- **sre:** Startup and review failures include typed reasons and a usable supported recovery. Coverage distinguishes observed, expected and unavailable; ambiguous counters remain diagnostic. Every S0 baseline carries revision, host and relevant bounds.
- **cost-finops:** Existing bounds remain enforced; shared diff default is 2,000,000 UTF-8 bytes and overrides are explicit and recorded. Run costs cover every attempt or say partial. No unbounded scan, transcript ingestion or lens fan-out is added.
- **privacy-compliance:** Lineage uses existing minimal native identifiers/fingerprints and counters. Do not add transcript contents, secrets or user prompts to usage/readiness artifacts or public diagnostics; verify failure outputs expose only necessary bounded identity/reason data.

## Exclusions and program constraints

S0 does not activate the bridge, change phase envelopes, replace the singleton, retire/migrate runs, delete legacy code/tests, consolidate journeys, deduplicate general primitives, rewrite personas, repair graph decomposition, change review reports/locales, or add a new approval UI. Later slices own those changes. Fixes made solely to pass optional-dependency/host isolation must remain within that cause, not turn S0 into wholesale test redesign.

All source non-goals persist: N1 no new harness/meta-orchestrator/approval surface; N2 no change to human gates, R-0003 or 26-lens catalog; N3 no persona-brief rewrite except later mechanically obsolete envelope prose; N4 no unreleased 2.18/2.19 migration tooling. C1 use the existing governed loop; C2 preserve Claude/Codex parity (Dynamic Workflows exception unchanged); C3 never `--all`, Evaluate/EM zero-lens; C4 preserve human approval/sign-off and attributable deferred-evaluation acceptance; C5 confirm release-state assumptions before retirement/publication, do not infer public release history from stale notes; C6 keep manifests/seals/packages reproducible. No synthetic review branch is pushed. Product authors only its artifacts and registered requirement.

## Dependency-ordered pickup roadmap

These are future requirement labels, not registered ids or approvals. Each S-step remains exactly one requirement with its own Design, Plan, Build, Evaluate and Engineering Review. Register later slices only when ready with the actual predecessor R-ids; overlap below is one owner with trace links, never duplicate implementation.

| Slice | Dependency | Deliverables / source coverage | Entry and exit decisions |
|---|---|---|---|
| **S0 — instruments and startup** (this requirement) | No requirement predecessor | H8.1; H5.2–3; H7.2; H6.1–3 = RF-2; RF-8. Preserve H5.1/4 and H6.4 invariants | New Design/Plan. Exit: all S0 ACs evidenced and existing human sign-off. |
| **G1 — bounded deterministic graph corrections** | S0; resolve GD-5 before Design approval | GD-1 import/negative assertions; GD-2 appropriate phase budget; GD-3 measurement-free content identity for complete and partial receipts; GD-4 dedicated localized fanout; GD-5 explicit partial semantics and stopping inputs; GD-9 injectable clock and fixed mini-repo/slow isolation. A1–A5 and related A6 test notes | Reproduce current defects first. Choose GD-5 policy without claiming partial source is complete. Preserve valid components only if approved semantics prove admissibility. |
| **G2 — graph ownership and reusable wiring** | G1 | GD-6 pure Plan classification, direct imports, traceability owner/meta contract; GD-7 requirement-parametric identity/runner and language-aware endpoint resolution; A6 error boundary, component-step extraction, typing/naming/format/comprehension cleanup where touched | Exact contracts per requirement; preserve R-0001 default behavior without universal hardcoding. GD-8 is owned by S3, not repeated here. |
| **R1 — packaged review works after install** | S0; may proceed independently of G1 | RF-1 include locales in Claude/OpenAI packages, deterministic installed-package smoke that actually completes review collection | Release blocker until shipped-package proof exists; preserve locale behavior and avoid fixture-only proof. |
| **S1 — activate the bridge** | S0; G1 if Plan/Design consume affected graph completeness | H1 all, H2 all, H4 all; H5.1 live shipped-hook Claude/Codex phase-path proof. Remove only the mode/prose/asymmetry replaced here | Seven-phase public CLI journey, exact bounded/path-free typed envelopes, three-task root share ≤25% with complete S0 meter. No inherited Design approval. |
| **S2 — one loop and retirement** | S1; G2 where module ownership overlaps | H3 all; H10 all; H8.2–4 boundaries/size goals; H11 removal, journey consolidation, runtime and coverage targets | Resolve D1–D3 and public-release-state assumptions first. Each removed journey maps to preserved transition coverage; keep uncovered behavior. Activation must already be real before deleting incumbent state. |
| **S3 — evidence consumers and primitives** | S2; G2 for GD-8 call sites | H7.1/3/4; H9 all = GD-8 canonical fingerprint helper; remaining H6.4 fallback naming; H11 additions for changed consumers. Complete H5 live-host evidence preservation | Resolve D4 before starting. Every Build task receives engine-native eval disposition or explicit unsupported-host acceptance under existing gates; retro reports time, coverage, outcomes. |
| **R2 — faithful review collection and context** | S0 and R1; consume current Review/Design contracts from G2/S3 only where needed | RF-3 ≤14 KB published pages and page-count parity; RF-4 substantive common Markdown/HTML/JSON semantic model with slot fingerprints and readable DoR; RF-5 standalone `--req` criteria/contracts; RF-6 retained evidence-detail text | Version/bind common semantic model; refusal for missing pages. Do not credit thin-model semantic equality as substantive parity. |
| **R3 — review execution and hygiene** | S0; R2 if dispatch/report schema changes | RF-7 execute a concurrent dispatch set as one fan-out under host limits and record actual dispatch evidence; B2.8 fixture refs/stop-hook hygiene | No blanket parallelism or new lens routes. Keep synthetic review scaffolding out of publish advice/branches via a reviewed mechanism. |

Program completion additionally reconciles H8 target SCC ≤20 and no named stage-module SCC, `loop.py` ≤8,000/each function ≤200, production ≤120,000, ≥2,500 removed lines, zero cross-module duplicate bodies and prohibited legacy references, and H11 ≤70,000 test lines/≤200 files, ≤10 min CI/≤8 min local `-n 4`, module test ratios 0.3–2.0 with reasons. These source targets are retained for S2/S3 Design, with a current measured baseline and behavioral coverage taking precedence over blind deletion. Any proposed target change returns explicitly to Product/human gate; it is not silently waived.

### Deduplication and completeness ledger

- H1 → S1; H2 → S1 with S0 meter prerequisite; H3 → S2; H4 → S1; H5 → S0 isolation, S1 host proof, S3 evidence-consumer proof; H6 → S0/RF-2 bound/refusal/scoping, S3 fallback naming; H7 → S0 meter, S3 eval/retro/positive telemetry; H8 → existing S0 ratchet retained, S2 decomposition targets; H9 → S3/GD-8; H10 → S2; H11 → S0 necessary isolation, S1 new phase tests, S2 consolidation/runtime/coverage, S3 consumer tests.
- GD-1/2/3/4/5/9 → G1; GD-6/7 → G2; GD-8 → S3/H9. GD-3 excludes measured elapsed values from semantic fingerprints; structural fanout/stopping reasons that alter meaning remain bound. The source's “remove fan-out” shorthand must not erase correctness-relevant structure.
- RF-1 → R1; RF-2 → S0/H6; RF-3/4/5/6 → R2; RF-7 → R3; RF-8 → S0 as its own readiness criterion. B2.3 enforcement receipt debt links H5/H7, not RF-8. B2.8 hygiene remains R3 even without an original RF id.
- B1 confirmed benefits are preservation constraints: one canonical diff/impact, selective routing, structural finding admissibility/settled ledger, language references, attributed dynamic validation, equal substantive publication/gate revision and explicit recovery. B3 historical cost baseline is retained as history, not current performance or a requirement for five lenses.
- A6 residual details (error normalization/runtime assertions, component extraction, direct imports, typed seams, semantic names, duplicate comprehension and configured formatting) remain G2; test runtime, bad-AST reason assertions and clock seams remain G1; generalized identical-body/canonical-hash work remains S3. No A6 note is silently dropped.

## Preserved unresolved product decisions

None blocks bounded S0 Design: its shared default preserves the current loop's 2 MB bound; specific override validation/error schema are Design work within ACs.

- **D1 (before S2):** accept refusing old run/v3 with exact archive recovery versus unsupported migration; no retirement consent is inferred.
- **D2 (before S2):** confirm continuity packet format has no external consumer; retain capabilities as attributable stage operations. No unilateral deletion of working recovery.
- **D3 (before S2):** approve journey consolidation only with transition-to-replacement evidence; sampled 35–40% redundancy is not a deletion quota.
- **D4 (before S3):** decide whether explicit unsupported Claude evaluation plus attributable acceptance is acceptable if real receipts remain impossible; unavailable/deferred is not PASS.
- **D5 (all slices):** preserve one requirement per S-step; an unlisted new abstraction returns to Design/Product rather than expanding the plan.
- **GD-5 (before G1 Design approval):** choose repository-wide block versus per-touchpoint degradation for unsupported language/parser failures. Either choice must expose stopping input/reason and prevent partial evidence from masquerading as complete. This Product pickup does not silently choose for polyglot users.
- **GD-2/GD-4 (G1 Design):** justify a per-touchpoint budget versus telemetry-only time and dedicated local fanout limits using the approved GD-5 semantics and bounded fixture evidence; no copied 60-second/machine-speed cliff.
- **Release/compatibility (before S2 and publication):** verify the source's last-public-release and no-external-consumer assumptions against current release state. Old advisory reviews and unrelated Design decisions are not new authority.

## Contract handoff to Design and Plan

Startup maintenance was explicitly authorized after Product. Commit
`b9b043f4b562ac8dbe46df9866e94fd8c59eb671` repairs fresh-input isolation,
onboarding manifest readiness, and immutable Product-to-Design artifact
ownership. Design must assess these fixes as the current source baseline;
they do not establish completion of S0's remaining acceptance criteria.

Design identified one bounded context omission: `taskplane/wave_metrics.py`
is the existing terminal evidence, seal, validation, and projection owner
needed by unchanged AC04/AC05/AC11. It is now included through the supported
R-0006 context amendment and in the scope ceiling below. This enables one
canonical accounting path with additive versioned coverage, preserving
complete-only admission and existing readers. No acceptance criterion or
later-slice scope changed.

The two backlog files above are the explicit Product intake. Dependency
analysis uses R-0006's amended `context_files`: the named production/test/CI
sources, `specs/spec.md`, and exactly `docs/import-cycle-baseline.md`,
`docs/onboarding.md`, and `docs/storage-and-repositories.md`. Historical
`docs/**` and `specs/**` are not admitted as inputs. The scope ceiling below
still describes potential outputs; it does not authorize reading prior runs
or historical artifacts. No acceptance criterion changed in this amendment.

Design is required because S0 changes CLI/readiness/evidence boundaries across owners. It must produce a new requirement-bound Design and exact acceptance map. Existing consolidated pre-implementation and final human gates remain the only approval surfaces.

Canonical boundary ids, separately from relations:

```yaml
contracts:
  - contract:taskplane.import-cycle-topology
  - contract:taskplane.test-host-isolation
  - contract:taskplane.run-usage-seal
  - contract:taskplane.canonical-review-diff
  - contract:taskplane.onboarding-readiness
  - contract:taskplane.repository-preparation
  - contract:taskplane.enforcement-receipt
relations:
  changes:
    - taskplane.test-host-isolation
    - taskplane.run-usage-seal
    - taskplane.canonical-review-diff
    - taskplane.onboarding-readiness
  consumes:
    - taskplane.import-cycle-topology
    - taskplane.repository-preparation
    - taskplane.enforcement-receipt
depends_on: []
scope_paths:
  - taskplane/import_cycles.py
  - taskplane/native_session_meter.py
  - taskplane/dispatch_telemetry.py
  - taskplane/yield_meter.py
  - taskplane/wave_metrics.py
  - taskplane/review.py
  - taskplane/review_session.py
  - taskplane/review_evidence.py
  - taskplane/tp.py
  - taskplane/loop.py
  - taskplane/loop_status.py
  - taskplane/retro.py
  - taskplane/preflight.py
  - taskplane/run_store.py
  - taskplane/run_context.py
  - taskplane/storage.py
  - taskplane/host_native.py
  - taskplane/host_capabilities.py
  - taskplane/tests/**
  - scripts/ci_local.py
  - scripts/ci_loop_cost.py
  - .github/workflows/**
  - docs/**
  - specs/**
  - CHANGELOG.md
out_of_scope:
  - S1 activation, envelopes and validators
  - S2 singleton retirement, migration deletion and general test consolidation
  - S3 general primitives, live Evaluate redesign and full retro redesign
  - G1/G2 graph-decomposition repairs and topology/wiring redesign
  - R1/R2/R3 locales packaging, review publishing, concurrent orchestration and fixture hygiene
  - changing R-0003, catalog, zero-lens Evaluate/EM or human gates
  - direct runtime-state repair, new stores/harnesses or publication
dod:
  test_command: python3 -m pytest taskplane/tests/test_harness_s0.py taskplane/tests/test_native_session_meter.py taskplane/tests/test_native_root_session.py taskplane/tests/test_repository_preflight.py taskplane/tests/test_review_refusals.py taskplane/tests/test_review_target.py taskplane/tests/test_workflow_review_kernel_parity.py
```

`test_harness_s0.py` is a proposed acceptance suite, not an existing or executed artifact. Design may map ACs to better focused existing suites, but must preserve all positive/negative/public-producer proof. The primary command is followed by existing import-cycle CI check and the full supported Claude/Codex-marker suite cells required by AC02; baseline wave/host evidence is required and may not be substituted by this unit-test command. Planner narrows the context ceiling above to exact task paths and graph dependencies; broad tests/docs context grants no unrelated edit authority. Import-cycle implementation/policy need no change unless current evidence demonstrates a gap; no policy relaxation is authorized.

The engine-emitted 26-row focused disposition ledger is preserved verbatim in `specs/harness-refactor/focused-route.json`. It selected Product and Security as execute_light. Product authored this artifact; no separate bound Security result was provided, so selected does not mean independently reviewed. The PM gate must judge this limitation; no fabricated fan-out or approval is recorded.
