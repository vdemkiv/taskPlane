# Spec — taskPlane v3 Phase 3: engine correctness + routing precision + host portability + docs currency + hygiene

Requirements: **R-0007, R-0008, R-0009, R-0010, R-0011** (store:
`knowledge/requirements/`). Source of truth: `docs/v3-phase-3-backlog.md`
(26 items, five work streams, compiled from the month-1 retro).

> **Human pre-approval**: scope approved by Volodymyr from
> `docs/v3-phase-3-backlog.md` before formalization ("approved all
> R-0007..R-0011"). This spec formalizes that approved backlog — it does not
> re-litigate it. The Design and Plan gates still come to him normally.

## Problem

One month of governed dogfooding (v1.0.0 → v2.5.1, decisions 0001–0017, two
phase retros) left three recurring defect classes and a debt tail:

1. **The loop finds its own engine gaps faster than the test suite does.**
   Every WS-A item (serial-mode claim, LOOP_OWNED gate trips, slot-env leak,
   gate version skew, silent audit skips, fail-open crash, unclamped floors)
   was hit live by the loop governing its own build — none was caught by a
   test first (decisions 0011, 0016).
2. **Precision misses recur across phases.** Both retros flagged the same
   forecast miscalibration (0.33 friction predicted, 0 fix cycles observed);
   the t6∥t7 sequencing gap was planner memory, not a mechanical check.
3. **Docs and skills fall behind releases whenever they are outside task
   scope** — the proven recurring class the freshness gate (decision 0017)
   now makes mechanical, but five skills and 34 exempted flags (D-0005)
   predate the gate.

Plus a Codex-compatibility review (WS-C) and an accepted-not-fixed EM tail
(WS-E) that should be retired before Phase 4.

## Users & context

- **taskPlane developers** (dogfood): every WS-A/WS-B item is a cost the loop
  itself paid this month; fixes compound into every future governed run.
- **Cross-host adopters** (Claude Code, Cowork, Codex, Windows shells): WS-C
  is their stream; the Codex Task path stays the reference implementation.
- **New users via skills/docs**: WS-D is the currency debt they hit first.

## In scope — the five streams

### Stream A · R-0007 — Engine correctness (A1–A7) — first priority

All fail-closed hardening; nothing loosens. Acceptance (mirrors R-0007;
these are the DoD):

1. **A1** `loop claim` refuses in serial mode, fail-closed at claim time,
   remedy names `init --parallel` or a plan amendment — decision-0011
   scenario pinned in `taskplane/tests/test_loop.py`; no worktrees created,
   submit-time deadlock unreachable.
2. **A2** Per-task DoD excludes LOOP_OWNED paths (design/, plan/, specs/)
   as the sign-off aggregate already does — `test_dor_dod.py`; loop-owned-only
   diff passes with a clean evidence diff; aggregate behavior parity-pinned.
3. **A3** `dod_check` strips `TASKPLANE_TASK` from the test subprocess env —
   `test_dor_dod.py` slot-canary case passes under a gate.
4. **A4** The gate validates with the evidence-producing engine (or the
   engine enforces merge-before-gate ordering — Design choice, see open
   questions): t7 version-skew scenario replayed as a regression test;
   non-skew gate paths byte-unchanged. **The most delicate change in the
   phase** — see Risks.
5. **A5** `router_audit` surfaces unattributed findings as warn rows instead
   of skipping (audit.py:150) — `test_audit_sweep.py`; a lens-less finding
   cannot evade the router-regression check.
6. **A6** `_stage_wave_run` degrades to the mandatory Task path on a
   malformed wave entry instead of crashing on raw `e["task"]["id"]`
   (tp.py:1072) — `test_stage_waves.py` negative fixture, traced reason.
7. **A7** `components.yaml` floors ≤0 clamp to ≥1 with a degraded marker
   (decompose.py:130) — `test_decompose.py`.
8. **Stream-wide: no guardrail loosened; battery green**;
   `contract:lens-brief` and `contract:findings-v2` shapes untouched.

### Stream B · R-0008 — Routing precision + planning (B1–B5) — second

1. **B1** Refinement forecast recalibrated against the pinned two-phase
   corpus (17 tasks, 1 fix cycle), corpus checked in as a versioned fixture —
   `test_requirements.py`; well-scoped-wave fixture drops below 0.33
   friction AND every risky/underspecified fixture warns at least as loudly
   as today (explicit no-under-warning negative cases).
2. **B2** Plan gate mechanically orders brief-shape-affecting tasks
   (lens.py / lens_signals.py / tp.py dispatch) before golden-regen —
   t6∥t7 scenario replayed; violating plans refused with reason.
3. **B3** Symbol-less ≥600-line files fold into `::core` WITH content, or
   the module is marked below-floor honestly (decompose.py:492) — no empty
   `::core`, no disengaged layer with an unusable remedy.
4. **B4** Component-path lens assembly re-runs the requirement-keyword
   detector live and unions it in — `test_lens_route_v2.py` superset
   assertion for requirement-driven signals.
5. **B5** Fixture-classed path that is a graph module with dependents keeps
   full weight — positive + negative fixtures in
   `test_lens_signals_fixtures.py`; D-0002 discount behavior preserved for
   real test fixtures.
6. **Stream-wide**: no routed set narrower than today except the pinned,
   corpus-justified B1 recalibration; battery green.

### Stream C · R-0009 — Host portability (C1–C3)

1. **C1** Stage briefs carry a Windows-alternative slot activation alongside
   the POSIX `export TASKPLANE_TASK=<slot>` (hooks' existing pattern) —
   `test_stage_waves.py` + `test_codex_compat.py`; union-screen treatment of
   the Windows form explicitly pinned (no silent behavior change).
2. **C2** `components.yaml` joins `DEFAULT_OUT_OF_SCOPE`
   (taskplane_lite.py:1241), strict-or-stricter — deny case AND
   literal-override precedence positive case in
   `test_governance_invariants.py`: a plan-minted contract with explicit
   literal `components.yaml` scope must still write it. **No guardrail
   loosened; battery green.**
3. **C3** Product decision, made here: on hosts with no workflow runtime,
   explicit `--emit workflow` **refuses with a self-describing reason
   naming the Task-path fallback, exit nonzero** — instead of force-printing
   an uninvokable payload. Default emission byte-unchanged;
   no-gate-reachable-only-via-workflows still holds — `test_codex_compat.py`.

### Stream D · R-0010 — Docs & skills currency (D1–D5)

1. **D1** tp-go, tp-status, tp-product, tp-tag, tp-northstar truthed up to
   v2.5 reality — per-skill surface-mention + doc-pointer checks in
   `test_release_freshness.py` (extended).
2. **D2** tp-go described as the internal delivery driver invoked via the
   `taskplane` facade (or explicit loop-driving) so "implement X" lands on
   the facade deterministically — `test_onboarding_docs.py`.
3. **D3** Harness rules (submit/gate/human-checkpoint invariants)
   single-sourced into `references/`; both skills point at it; drift check
   with keyword coverage proves no invariant dropped in extraction.
4. **D4** Freshness gate pins tp-go to the current minor, same mechanism as
   tp-help — `test_release_freshness.py`.
5. **D5** `_LEGACY_UNDOCUMENTED` burned down to empty (debt **D-0005**,
   marked addressed-by-R-0010 in the store — done at spec time); every flag
   documented in docs/ or a generated CLI reference (`tp help --md`
   candidate); the new-flag ratchet stays.

### Stream E · R-0011 — Test/CI/UX hygiene (E1–E6)

1. **E1** unittest-discover CI leg honesty: convert pytest-style files or
   fail the leg below a pinned collected-count floor; the 4-file Phase 2
   widening closed.
2. **E2** `TestDebtBurndownMechanism` restores `TASKPLANE_HOME`; suite-wide
   env-mutation sweep with a guard against new unrestored mutations.
3. **E3** Dashboard component nodes gain Escape-key tooltip dismissal
   (depgraph.py:1414, parity with module nodes); ring radius scales with
   component count (depgraph.py:1390) — `test_dashboard_v2.py`.
4. **E4** decompose.py docstrings aligned to behavior (unknown-keys raise;
   `_symbol_clusters` return shape) — behavior-pinning assertions.
5. **E5** Slot charset validated at emission before ids are embedded into
   `export TASKPLANE_TASK=` lines (tp.py:1049); invalid slots refuse with
   reason — `test_stage_waves.py`; **no guardrail loosened; battery green**.
6. **E6** loop.py seam comment matches the real monkeypatch seam (or seam
   widened) + the explicit patch-based late-binding regression test
   (t5 evaluator suggestion) — `test_audit_extraction.py`.
7. **Stream-wide**: battery green; suite count only goes up.

## Out of scope (unchanged from the v3 strategy)

- **WS1 flow-as-data** (`taskplane.flow/v1`, preset catalog, R-F1..R-F6) —
  later phase.
- **No new lens catalog entries** — the catalog stays at 26.
- **No host-specific UI** — dashboard stays host-portable; E3 is a11y/layout
  inside the existing portable renderer, not a new surface.
- **No breaking changes to governed-loop semantics**: step names, human-gate
  authority, worker-submits-never-advances, `loop.json` compatibility.
- **No change to Codex parity direction**: the Task path remains the
  reference; C3 changes only the explicit-override error path.
- Marketing/launch copy beyond the D1–D5 truth-ups.

## Risks

- **A4 (gate version skew) touches gate ordering — the most delicate change
  in the phase.** Both candidate mechanisms (engine-enforced
  merge-before-gate vs validate-in-the-agent-workspace-engine) reorder or
  re-home a step of the most load-bearing sequence in the engine. Mitigation:
  the mechanism choice is an explicit Design decision (open question 1); the
  t7 scenario is the pinned fixture; every non-skew gate path must be proven
  byte-unchanged; the v2.3.1 regression gate runs on every increment.
- **B1 recalibration must not under-warn risky work.** A calibration that
  fixes the 0.33-friction over-prediction by flattening the scorer would be
  strictly worse than the miss it fixes. Mitigation: the corpus is pinned as
  a fixture; the acceptance includes explicit negative cases where
  risky/underspecified fixtures must warn at least as loudly as today.
- **C2 must not re-create the scope-precedence deadlock.** Adding
  `components.yaml` to the default deny family protects routing floors, but
  Phase 2 shipped decomposition work by writing that file under plan-minted
  contracts. Mitigation: the acceptance REQUIRES the literal plan-minted
  override positive test — a contract with explicit literal
  `components.yaml` scope keeps writing it; the deny case and the override
  case ship in the same test file so neither can regress silently.
- **Goldens churn** (C1 changes brief bytes; B2 orders regen): regen only
  via the documented `regen.py`; CI parity leg green post-regen;
  hand-edited goldens are a finding.
- **Docs extraction drift** (D3): keyword-coverage check in the drift test
  proves every invariant survives the move to `references/`.

## Contract handoff (→ Design / Plan)

- `scope_paths`: `taskplane/loop.py`, `taskplane/audit.py`, `taskplane/tp.py`,
  `taskplane/decompose.py`, `taskplane/taskplane_lite.py`,
  `taskplane/requirements.py`, `taskplane/lens.py`,
  `taskplane/lens_signals.py`, `taskplane/depgraph.py`,
  `taskplane/tests/**`, `skills/**`, `docs/**`, `references/**`, `hooks/**`,
  `.github/**` (CI legs).
- `out_of_scope`: `taskplane/kb.py`, `taskplane/design_contract.py`,
  `taskplane/regression.py`, lens catalog entries, `workflows/**` runtime
  semantics beyond the C3 error path, hook/screen enforcement semantics
  (verify-only — C2 only WIDENS the default deny family), `PRIVACY`/legal
  copy, flow-as-data schema files.
- `dod.test_command`: `python3 -m unittest discover -s taskplane/tests -q`
  (full suite; no-loosening battery, parity goldens, and the Codex fixture
  leg green; suite count only goes up from 1122; the E1 collected-count
  floor becomes part of this DoD once it lands).

Named contracts Phase 3 touches (recorded on the requirement graph):

| Contract | Relation | Stream |
| --- | --- | --- |
| `contract:loop-gate` | **changes** — claim refusal (A1), LOOP_OWNED exclusion (A2), env sanitization (A3), skew handling (A4) | R-0007 |
| `contract:wave-workflow` | **changes** — malformed-entry fallback (A6); Windows slot form (C1); **consumed** by E5 validation | R-0007, R-0009, R-0011 |
| `contract:findings-v2` | **consumes** — shape untouched; A5 only stops skipping | R-0007 |
| `contract:lens-brief` | **consumes** — shape untouched; B2 keys on its surfaces | R-0008 |
| `contract:component-map` | **consumes** — B3/B4 honesty, E3 rendering | R-0008, R-0011 |
| `contract:default-out-of-scope` | **changes** — gains `components.yaml`, strict-or-stricter (C2) | R-0009 |

Dependency spine (recorded with `--depends` equivalents in the graph):
R-0007 ← R-0004, R-0006 · R-0008 ← R-0003, R-0006 · R-0009 ← R-0004 ·
R-0010 ← R-0005 · R-0011 ← R-0003, R-0006. All five streams parallel-safe;
B2's ordering rule constrains any plan the same way t6/t7 should have been
ordered. Priority: WS-A first, WS-B second, WS-D third, WS-C/WS-E folding
into waves as capacity allows (per the approved backlog).

## Open questions (for Design)

1. **A4 mechanism — RESOLVED by Design (decision 0018)**:
   validator-fingerprint refusal. Submit stamps `engine_fingerprint` (hash
   of the validator-surface modules); the evaluate gate refuses on mismatch
   with a named remedy (merge, re-submit), traced `loop_gate_blocked
   reason=engine_skew`. Options (a) merge-before-gate (merges unevaluated
   code routinely) and (b) agent-engine validation (executes agent-written
   code in the gate context) were rejected. R-0007's open question is
   cleared in the store; rescored 1.0.
2. Non-blocking, Design-level: E1 convert-vs-floor choice, D5 generated
   CLI-reference format (`tp help --md`), and the exact B1 axis reweighting
   are HOW decisions inside already-testable acceptance bounds.

## Recommendation

**Design phase required** — engine gate ordering (A4), two changed named
contracts, enforcement-adjacent deny-family widening (C2), and cross-module
scope. The plan-approval recommendation is this seat's; the final sign-off
recommendation is tp-engineering's; both decisions belong to the human —
the backlog pre-approval covers scope only, not the Design/Plan gates.
