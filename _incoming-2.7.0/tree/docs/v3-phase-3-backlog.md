# v3 Phase 3 backlog — everything the month-1 retro left open

Compiled from the 16 decision records, both phase retros, the open debt
ledger, the Phase 2 EM findings that were accepted rather than fixed, the
Codex-compatibility review, and the skills staleness audit. Each item names
its source so the pm step can lift acceptance criteria directly. Grouped as
five candidate work streams; suggested requirement split R-0007..R-0011.

## WS-A · Engine correctness — the dogfooded gaps (R-0007)

Every item here was found by the loop governing its own build; none was
caught by the test suite first. All are fail-closed hardening; none loosens.

- **A1 · `loop claim` must refuse in serial mode.** Phase 2 wave 1 claimed
  4 tasks while `state.parallel` was false; submits then deadlocked and the
  orchestrator hand-corrected state (decision 0011). Claim should fail
  closed at claim time with a remedy ("init --parallel or approve a plan
  amendment"), not let a wave form it cannot land.
- **A2 · Per-task DoD excludes LOOP_OWNED.** Orchestrator-synced loop
  artifacts (design/, plan/, specs/) tripped every wave-1 task gate; the
  documented re-claim recovery re-snapshots and blanks the evidence diff —
  strictly worse than excluding loop-owned paths the way the sign-off
  aggregate already does (traced engine gap, Phase 2).
- **A3 · DoD test subprocess sanitizes the slot env.** A gate run under
  `TASKPLANE_TASK=<slot>` leaks the slot into `dod_check`'s test run and
  slot-sensitive tests fail (traced gap; worked around via legacy snapshot).
  `dod_check` should strip `TASKPLANE_TASK` from the child env.
- **A4 · Gate validates with the evidence-producing engine.** t7's
  re-evaluation was rejected because the primary's validator predated the
  worktree's engine (version skew); it passed only after the merge landed.
  Either pin: merge-before-gate as an engine-enforced ordering, or validate
  in the agent workspace's engine (Phase 2 retro lesson 2).
- **A5 · `router_audit` counts unattributed findings.** Findings with no/
  unknown lens attribution are silently skipped by the audit backstop
  (audit.py:150) — a reviewer omitting `lens` evades the router regression
  check. Unattributed findings should be surfaced (warn row), not dropped.
- **A6 · `_stage_wave_run` fails open to the Task path.** A malformed wave
  entry crashes emission via raw `e["task"]["id"]` (tp.py:1072) instead of
  degrading to the mandatory fallback.
- **A7 · `components.yaml` floors clamped.** Negative/zero floor overrides
  are accepted unclamped (decompose.py:130); clamp to ≥1 with a degraded
  marker, per the fail-open convention.

## WS-B · Routing precision + planning (R-0008)

- **B1 · Recalibrate the refinement forecast.** BOTH phase retros flagged
  the same miss: every well-scoped task predicted friction 0.33 and ran
  with 0 fix cycles. The scorer's NFR axes over-penalize scoped waves.
  Recalibrate against the two phases' recorded outcomes (17 tasks, 1 fix
  cycle) and pin the calibration corpus so future retros re-score it.
- **B2 · Planner treats brief-shape changes as golden-regen dependencies.**
  The t6∥t7 sequencing gap (Phase 2 retro lesson 1): any task touching
  lens.py/lens_signals.py/tp.py dispatch surfaces must be ordered before
  the golden-regen task by a mechanical plan-gate check, not planner
  memory.
- **B3 · Symbol-less big-file decomposition honesty.** A ≥600-line Python
  file with no top-level symbols vanishes into an empty `::core`, and the
  layer permanently disengages for that module with a remedy that cannot
  work (decompose.py:492, Phase 2 EM med). Fold such files into `::core`
  WITH their content, or mark the module below-floor honestly.
- **B4 · Component lens maps include requirement keywords at assembly.**
  Cached maps are derived without `requirement_text`, so requirement-driven
  signals can be narrowed away on the component path (Phase 2 EM low).
  Re-run the requirement-keyword detector live at assembly (cheap) and
  union it in.
- **B5 · Fixture-classifier product-dir guard.** A real product directory
  literally named `fixtures/` is discounted — the dangerous direction
  (under-routing). Add a graph-informed exception: a fixture-classed path
  that is a module with dependents keeps full weight (Phase 2 EM low +
  design-accepted risk worth retiring).

## WS-C · Host portability (R-0009)

From the dedicated Codex review (all verified against the shipped zip):

- **C1 · Windows slot activation.** `export TASKPLANE_TASK=<slot>` is
  POSIX-only; briefs should carry a `commandWindows`-style alternative like
  the hooks already do (falls back safely today, but the stricter union
  screen is a silent behavior change).
- **C2 · `components.yaml` joins the default deny family.** Today it is
  protected only by contract scope (taskplane_lite.py:1241); an unscoped
  contract can rewrite routing floors. Add it to DEFAULT_OUT_OF_SCOPE
  (strict-or-stricter; literal plan-minted override still available).
- **C3 · Decide `--emit workflow` semantics on Codex.** Explicit override
  currently force-prints an uninvokable payload with a self-describing
  reason (judged acceptable, but revisit: refuse-with-reason may be
  kinder than emitting a payload nothing can run).

## WS-D · Docs & skills currency (R-0010)

- **D1 · Skills truth-up.** tp-go (still pre-v2.5: no stage waves, no
  `--emit`, no routing v2/decomposition/audit), tp-status (no component
  layer), tp-product, tp-tag, tp-northstar (v1.6-era). Bring each to
  v2.5 reality.
- **D2 · Facade/driver routing determinism.** Sharpen tp-go's description
  to "internal delivery driver invoked via the taskplane skill (or when
  the user explicitly drives the loop)" so 'implement X' phrasing lands on
  the `taskplane` facade deterministically (skill-comparison review).
- **D3 · Single-source the harness rules.** `taskplane` and `tp-go`
  restate the submit/gate/human-checkpoint invariants in different words —
  drift surface. Extract one canonical statement into `references/` and
  point both skills at it.
- **D4 · Extend the freshness gate to tp-go.** The release-freshness test
  currently pins only tp-help to the current minor; the driver skill is
  the other doc that must move with releases.
- **D5 · Burn down the 34-flag exemption list (debt D-0005).** Document
  each legacy flag in docs/ or an autogenerated CLI reference, shrinking
  `_LEGACY_UNDOCUMENTED` to empty; consider `tp help --md` generating the
  reference so flags can never be undocumented again.

## WS-E · Test/CI/UX hygiene (R-0011)

- **E1 · unittest-discover CI leg honesty.** The second runner silently
  collects 0 tests from pytest-style files and the gap widened by 4 files
  in Phase 2 (EM low). Either convert the files or make the leg fail when
  its collected-count drops below a pinned floor.
- **E2 · Test env hygiene.** `TestDebtBurndownMechanism` sets
  TASKPLANE_HOME without restoring (EM observation); sweep the suite for
  unrestored env mutations.
- **E3 · Dashboard component-layer a11y + layout.** Component nodes lack
  the Escape-key tooltip dismissal module nodes have (depgraph.py:1414);
  fixed ring radius overlaps labels on many-component modules
  (depgraph.py:1390).
- **E4 · decompose.py doc/behavior truth.** Two docstring mismatches
  ("unknown keys are ignored"; `_symbol_clusters` return shape) — align
  docs to behavior (EM low).
- **E5 · Emitted slot-line validation.** Task ids are embedded into
  `export TASKPLANE_TASK=<slot>` prompt lines without compose-time
  validation (tp.py:1049); validate the slot charset at emission.
- **E6 · audit.py seam comment truth.** The monkeypatch seam is narrower
  than loop.py's comment implies (patching `loop.audit_counter` no longer
  affects `audit_due`) — fix the comment or widen the seam, and add the
  explicit patch-based late-binding regression test the t5 evaluator
  suggested.

## Suggested shape

Priority: **WS-A first** (engine correctness; every item is a guardrail
gap the loop itself hit), WS-B second (precision compounds every future
run), then WS-D (currency debt is the proven recurring class), WS-C and
WS-E folding into the same wave as capacity allows. All five streams are
independent enough for parallel waves; B2 constrains plan ordering the
same way t6/t7 should have been ordered.

Out of scope for Phase 3 (unchanged from the v3 strategy): WS1 flow-as-data
(R-F1..R-F6), new lens catalog entries, host-specific UI.
