# Design — v3 Phase 3: engine correctness, routing precision, host portability, docs currency, hygiene

**Requirements:** R-0007 (anchored, WS-A) + R-0008..R-0011 (secondary; WS-B..WS-E).
**Baseline:** graph fingerprint `beeb5b85…`, HEAD `43253c2`, v2.5.1 + freshness gate.
**Scope:** 26 backlog items, 29 acceptance criteria, six named contracts touched — zero new contract nodes, zero new modules.

Everything in this phase is one of three moves: **refuse loudly** (fail-closed),
**degrade to the reference rail** (Task path), or **surface what was silent**
(warn rows, degraded markers, traced reasons). Nothing loosens; the
`contract:lens-brief` and `contract:findings-v2` shapes are untouched.

---

## 1. The blocking decision: A4 — gate/engine version skew

**The defect (Phase 2, t7):** in a parallel wave, the evaluate gate validates a
worktree's evidence with the *primary process's* engine
(`_evaluation_errors`, loop.py:1928-1935 with `act_ws` = the worktree). When a
task's diff changes the engine itself, the worktree's newer engine produced
evidence the older primary validator rejects. t7 passed only after the merge
landed — diagnosed by hand, no mechanical detection.

**Three candidate mechanisms were weighed:**

| | Mechanism | Verdict |
|---|---|---|
| (a) | **Engine-enforced merge-before-gate ordering** — the gate refuses until the branch is merged, so the primary validates with the merged engine | Rejected. Merges **unevaluated code into the mainline as routine**, inverting the evaluate-PASS-then-merge order every instruction and test pins; a failed post-merge evaluation needs revert machinery the engine doesn't have; and the ordering check must run on *every* parallel evaluate gate, so "non-skew paths byte-unchanged" is unprovable. Scoping it to "engine-touching diffs" needs a repo-layout path heuristic — the class of guess A4 exists to remove. |
| (b) | **Validate with the agent workspace's engine** — sys.path-inject the worktree's `taskplane/` and run *its* `_evaluation_errors` | **Disqualified on the guardrail.** The worktree's loop.py is the worker's own diff: this executes agent-written code in the orchestrator's gate context. A worker could ship an `_evaluation_errors` that returns `[]` and gate itself — the L12 trust boundary (evidence validated by code the worker cannot author) dissolves at exactly the gate it protects. Module-cache poisoning of the primary process is a second, independent failure. |
| (c) | **Validator-fingerprint refusal** — stamp the producing engine's identity into the submission; the gate refuses with a *named remedy* when engines differ | **Selected.** Fail-closed, explicit, no code-execution change, no reordering. |

**Selected mechanism (c), precisely:**

- `tp.engine_fingerprint()` = sha256 over the sorted `(module, sha256(file
  bytes))` of the validator-surface modules **as loaded by the running
  process** (`loop, lens, lens_signals, depgraph, audit, taskplane_lite,
  design_contract, decompose, requirements` via each module's `__file__`).
- `loop submit` stamps it into the submission (additive field).
- The **evaluate gate** compares it against its own fingerprint *before*
  `_evaluation_errors`. Equal (every non-dogfood repo; every dogfood task not
  touching engine files): the comparison is the only added instruction —
  **byte-unchanged**. Unequal or absent: **refuse**, no transition, traced
  `loop_gate_blocked reason=engine_skew {submitted, validator}`, error text:

  > evidence was produced under engine `<fp12>` but this gate validates with
  > `<fp12>` — merge the task branch into the primary
  > (`git merge tp/<task>`) so one engine owns production and validation,
  > then `loop submit` again.

- **Cannot strand a re-evaluation:** the loop stays at evaluate; after the
  merge, both processes run one engine, fingerprints match, and the identical
  submission path proceeds. This mechanizes exactly the recovery Phase 2
  performed by hand — with the diagnosis in the error message.
- **Stated limit:** this detects producer-*process* vs validator-*process*
  skew (the recorded t7 topology). Evidence hand-authored under a third
  engine remains covered by the staleness fingerprint and DoD.
  Escalation if dogfood ever hits the residual case: stamp the fingerprint
  into `.eval/verdict.json` itself.

See `design/visual.html` for the gate sequence with the skew branch.

## 2. WS-A — the other six engine fixes (R-0007, anchored)

| Item | Change | Where | Pinned by |
|---|---|---|---|
| A1 | `claim` refuses when `state.parallel` is false, **before** any contract/DoR work; remedy names `init --parallel` or a plan amendment; traced `loop_claim_blocked reason=serial_mode`. Backstop behind `wave()`'s existing refusal — closes the direct-claim path from decision 0011. | loop.py:563-622 | test_loop.py |
| A2 | `dod_check` gains `ignore_prefixes` (default `()` — non-loop callers unchanged); the per-task DoD (serial + parallel gate) passes `lens.LOOP_OWNED`, mirroring the sign-off aggregate (loop.py:2113). No more re-claim/re-snapshot workaround. | taskplane_lite.py:1185-1204, loop.py:1229-1235/1794 | test_dor_dod.py (+ aggregate parity pin) |
| A3 | `dod_check` runs the DoD test with `TASKPLANE_TASK` stripped from the **child** env (parent untouched). | taskplane_lite.py:1206-1212 | test_dor_dod.py slot-canary |
| A5 | `router_audit` stops silently skipping unattributed/unknown-lens findings (audit.py:149-150): warn rows — class `observation` (non-blocking; the underlying finding still gates normally), owner `router`, domain `router+unattributed` / `router+unknown:<lens>` — appended idempotently, traced `router_audit_unattributed`. Attribution omission becomes visible instead of an evasion. | audit.py | test_audit_sweep.py |
| A6 | `_stage_wave_run` validates entries; malformed → **Task-path fallback** with traced reason, never KeyError. Well-formed emission byte-unchanged. | tp.py:1084-1086 | test_stage_waves.py negative fixture |
| A7 | `load_floors` clamps `<=0` floors to 1 with a `degraded_floors {key: given}` marker in stats + trace; `floors_hash` hashes clamped values. | decompose.py:130-157 | test_decompose.py |

## 3. WS-B — routing precision + planning (R-0008)

- **B1 (recalibration approach):** class-weighted, not flattened. Functional
  gaps keep today's 0.5-cycle weight. NFR-coverage gaps drop to 0.1 **only
  when the functional axis is complete**; `security`/`data-safety` NFR gaps
  are **never** discounted. The 17-task/1-fix-cycle corpus from phases 1+2 is
  checked in as a versioned fixture (`fixtures/calibration/phase1-2-corpus.json`)
  and re-scored by the test; 2-3 synthetic under-specified requirements are the
  pinned **no-under-warn** corpus (new friction ≥ today's friction for each).
  Well-scoped-wave fixtures must land below the old 0.33.
- **B2:** engine plan-gate rule (not planner memory): every task whose scope
  touches `taskplane/lens.py|lens_signals.py|tp.py` must be a dep of every
  task touching `taskplane/tests/fixtures/briefs/`. Violating plans are
  refused naming both tasks; t6∥t7 is the replay fixture. This rule governs
  **this phase's own plan** too.
- **B3:** a ≥600-line symbol-less file joins `::core` **with** its (file,
  hash) member instead of vanishing (decompose.py:492). The layer stays
  engaged; the `::core` map is computed over the file's full content, so the
  `::core` route proposes everything the file's *own* signals support —
  pinned by a superset-of-own-signals assertion.
- **B4:** component assembly unions the **live requirement-keyword
  contribution** into the proposed set (attributed `requirement-keywords`)
  before the narrowing at lens.py:459-464 — cached maps are requirement-blind
  (decompose.py:596-603) and can no longer narrow keyword-driven lenses away.
  Union only widens.
- **B5:** fixture-discount exception: a fixture-classed path whose graph
  module has ≥1 dependents keeps full weight (evidence names the exemption).
  Restores weight only — never deepens a discount. D-0002 test-fixture
  discounting preserved by the negative fixture.

## 4. WS-C — host portability (R-0009)

- **C1:** stage prompts carry `set TASKPLANE_TASK=<slot>` (Windows/cmd,
  the hooks.json `commandWindows` precedent) beside the POSIX export — same
  validated slot. One regen.py goldens diff. The slot-less union-screen
  fallback (taskplane_lite.py:938-943) is pinned by an explicit test: no
  silent behavior change.
- **C2:** `components.yaml` joins `DEFAULT_OUT_OF_SCOPE`
  (taskplane_lite.py:1241). Deliberately **not** sacred: the plan-minted
  literal override must keep working (Phase 2 shipped decomposition through
  it). Deny case **and** the plan-minted literal positive case ship in the
  same file — `test_governance_invariants.py` — so neither regresses silently.
- **C3 (recorded product decision):** explicit `--emit workflow` on a
  workflow-less host **refuses**: stderr reason naming the Task-path fallback
  + the detector's reason, exit nonzero, traced `{path: refused}`. Both
  surfaces (stage emitter tp.py:1112, review dispatch tp.py:1241). Default
  and `--emit task` byte-unchanged; `workflow_available()` stays the single
  detector; no gate reachable only via workflows.

## 5. WS-D — docs & skills currency (R-0010)

- **D1:** tp-go/tp-status/tp-product/tp-tag/tp-northstar truthed to v2.5;
  per-skill required-mentions table + stale-phrase denylist in
  `test_release_freshness.py`.
- **D2:** tp-go described as the *internal delivery driver invoked via the
  taskplane facade*; facade keeps the "implement X" triggers; both pinned.
- **D3:** `references/harness-rules.md` — the single canonical statement of
  submit/gate/human-checkpoint invariants; both skills point at it;
  keyword-coverage drift check proves no invariant dropped; restatement
  detector prevents re-forking.
- **D4:** tp-go pinned to the current minor exactly as tp-help
  (test_release_freshness.py:37-44 pattern).
- **D5 (decision):** new `tp help --md` autogenerates `docs/cli-reference.md`
  from the live argparse tree — the generator **refuses any flag with empty
  help text** (the ratchet gets stricter, not weaker). Committed + drift-gated
  in CI (the ci.yml:113-118 generated-artifacts pattern). The freshness corpus
  already globs `docs/*.md`, so `_LEGACY_UNDOCUMENTED` (34 flags) burns to
  empty mechanically. D-0005 resolved on ship.

## 6. WS-E — test/CI/UX hygiene (R-0011)

- **E1 (decision): floor + manifest, convert nothing.** The 10 pytest-only
  files include the parity/no-loosening/stage-wave suites — rewriting them to
  `TestCase` risks silent collection drift in exactly the tests that guard
  guardrails (E1's own defect class, reintroduced by its fix).
  `scripts/ci_unittest_floor.py`: collected count ≥ pinned floor (995 at
  design time) **and** the TestCase-less file set equals a named 10-file
  manifest — a new pytest-only file fails the leg. Floor only rises; manifest
  only shrinks.
- **E2:** `TASKPLANE_HOME` restore via addCleanup (test_debt_burndown.py:219);
  conftest-level env-mutation guard (snapshot before / assert byte-identical
  after each module, naming offenders).
- **E3:** component nodes get the module-node `keydown/Escape` dismissal
  (depgraph.py:1381 pattern at 1404-1416); ring radius `r(m)+24` →
  `r(m)+f(component count)`, monotonic.
- **E4:** decompose.py docstrings aligned to behavior (unsupported
  components.yaml *line shapes* raise → whole file fails open, reported;
  `_symbol_clusters` returns a 5-tuple) + pinning assertions.
- **E5:** every task id validated against `_TASK_SLOT_RE`
  (taskplane_lite.py:1360) **before** any `export TASKPLANE_TASK=` line is
  composed; invalid → loud refusal (exit nonzero, id + charset named,
  traced), never sanitized.
- **E6 (decision): fix the comment, don't widen the seam.** loop.py:1449-1456
  over-claims that patching `loop.<name>` governs the audit path;
  `audit_due` resolves `audit_counter` module-locally (audit.py:117-132), and
  `_loop()` late-binds only 4 loop names (audit.py:41-51). The comment is
  corrected to name `audit.<name>` as the machinery seam and
  `loop.finding_blocks`/etc. as the gate-math seam; the late-binding
  regression test patches **both real seams** and asserts the comment text —
  a stale comment fails the suite. Widening would churn byte-frozen code for
  a testing convenience.

## 7. Guardrail statement (strict-or-stricter, enumerated)

1. A1/A4/C3/E5 **add refusals**; no path that passed today starts passing more.
2. A2 excludes only orchestrator-authored `LOOP_OWNED` artifacts — the same
   tuple the sign-off aggregate already excludes; parity-pinned.
3. A3 removes an env **leak**; no ambient trust added.
4. A5 surfaces findings that were silently dropped; blocking behavior of
   attributed findings byte-unchanged; findings-v2 shape untouched.
5. A6 replaces a crash with the mandatory reference rail, traced.
6. A7 clamps toward **more** decomposition, marked degraded.
7. B1 is corpus-pinned with explicit no-under-warn negative cases;
   security/data-safety never discount.
8. B4/B5 only widen or restore routing weight; B3 preserves the folded file's
   own signal set (superset pin); B2 adds a plan refusal.
9. C2 only **widens** the default deny family; the plan-minted literal
   override is proven by a positive test in the same file.
10. D3's keyword coverage proves every harness invariant survives extraction;
    D5's generator refuses undocumented flags (stricter ratchet).
11. E1's floor/manifest only ratchet tighter; suite count only rises
    (unittest ≥995, pytest >1122); workers still submit-never-advance; the
    PreToolUse screen, slot protocol, and human-gate authority are untouched.

## 8. Rollout / rollback

Fail-closed, additive, item-sized commits with their pinned tests in-diff.
Goldens regenerate **only** via `regen.py`, one reviewed diff per causing task
(C1; any B-stream routed-set change) — and the B2 rule this phase ships
enforces that ordering mechanically on this phase's own plan. A4 lands stamp +
comparison together. Rollback is per-item commit revert everywhere; no data
migrations; C2 rollback = remove one list entry; C3 rollback = restore
force-print (the previous documented behavior).

## 9. Graph honesty notes (for the approver)

- Baseline `beeb5b85…` is bound to the loop. `scanned_head 320f47ae` predates
  HEAD `43253c2` by six commits; three touch code files (tp.py +14,
  test_release_freshness.py +100, test_onboarding_docs.py +20). Design is
  read-only toward the as-built graph, so no mid-design rescan (it would
  invalidate this gate by the engine's own isolation rule). The drift is
  entirely inside this phase's scope; the execute baseline re-captures at the
  plan gate.
- `skills/`, `hooks/`, and the new `references/` are **not** graph modules
  (the scanner indexes code files only) and are deliberately not declared —
  WS-D is validated by named tests in `taskplane/tests` instead.
- R-0007's store record still lists the A4 open question **this design
  resolves**. Before the orchestrator can attach R-0007 to the loop
  (`design_attach_requirement` refuses open questions), the product seat must
  record the A4 decision (option c, validator-fingerprint refusal) and clear
  the question.

## 10. Open decisions left for the human gate

None inside the design. For the approver to confirm explicitly:
1. **A4 = option (c)** — accept the stated false-negative limit (mixed-engine
   evidence) with its escalation path, in exchange for zero trust-boundary and
   zero ordering changes.
2. **E1 = floor + manifest** (no conversions this phase).
3. **D5 = generated CLI reference** (`tp help --md` → `docs/cli-reference.md`).
4. **C2 stays plan-overridable** (not sacred) — the deadlock-avoidance choice.
