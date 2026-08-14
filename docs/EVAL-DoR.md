# Definition of Ready — the model-behavior evaluation

**Requirement:** R-0001 — score how a live model drives each taskplane skill,
with a per-skill baseline that fails on degradation.

**What this gates:** the first **real** evaluation run — a live model driving
a governed skill end to end, recorded, scored, and turned into a baseline.
Everything to date has been proven against **stub drivers**. This document is
the entry gate for the step that has never run.

Every ✅ below was verified in the tree; every ❌ is a real blocker; every ⚠️
is a known limit that does **not** block but must be understood before the
first number is trusted. Nothing here is asserted from memory.

---

## 0. The one-line summary

**NOT READY.** Four blockers, all in section 4. Everything the run *reads* is
built and green (2,552 tests). What is missing is a driver, a place to put the
answer, and one honest decision about what a first score means.

---

## 1. The instrument exists and is green

| ✅ | Evidence |
| --- | --- |
| Suite green | 2,552 passed, 5 skipped |
| Corpus scorer untouched | `score()`, `AREAS`, `_pct`, FACT/CLAIM split pinned by sha256 against the pre-change blob; `ci_evals.py --corpus` exits 0 at the four pinned rates |
| Records the scorer reads | `trace.jsonl`, `obligations.jsonl`, `dispatch.json`, `derivations.jsonl`, `context.jsonl` |
| Verdict vocabulary | `pass`, `fail`, `no_evidence`, `n/a` |
| Check vocabulary | `exists`, `absent`, `before`, `after`, `count`, `repeats`, `field_equals`, `pairs`, `all` |
| Ordering anchors | `first_write`, `first_dispatch`, `first_brief`, `completion_claim` |
| Universal tags | `contract`, `dor`, `dod`, `no_rederive` |
| Scenarios | all 7 governed skills: `taskplane`, `tp-build`, `tp-design`, `tp-engineering`, `tp-go`, `tp-product`, `tp-tag` |
| Falsifiability | 13 negative fixtures; a meta-test fails the suite if a check kind has no fixture that fails it |
| Fixture repo | plain-file trees + builder; two builds under hostile identity/clock/TZ/locale reproduce the pinned SHAs; one appended byte is caught |

## 2. The recording surfaces the run depends on

Each was landed as **recording-only** and proven no-denial against a baseline
blob, including with the writer raising on every call.

| ✅ | Surface | What it makes observable |
| --- | --- | --- |
| `derivation.jsonl` | `derived {key, input_key}` + `command {verb, decision}` | re-derivation (R7a) and invented surfaces (R10) |
| release-verb rows | recorded at the abstain path | `tp status`/`ack` polling, previously invisible |
| `review_context_written` | literal paths + per-file sha256, with `status` | diff-stored-before-lenses; refusal distinguishable from never-ran |
| `graph_impact` | `head` + `scanned_head` at all three sites | whether the blast radius describes the reviewed tree |
| `lens_route` | `requested_breadth` + `engine_ran` | engine-routed vs `--all`, **recorded, no longer inferred** |

## 3. The gate's rules are settled

| ✅ | Rule |
| --- | --- |
| Per-item gating, never the scalar | `pass→fail` REGRESSION · `pass→no_evidence` EVIDENCE LOST · `pass→n/a` STEP RETIRED · `pass→absent` STEP DROPPED |
| Scalar is reported, never gated | `evals/negative/no-ledger` pins `score 1.0` with `instrument: broken` as the standing argument |
| Absent record | `no_evidence`, never `pass` — enforced once, ahead of every check |
| Waiver bounds | both mandatory: `inputs_fingerprint` (≥12 hex) and `expires` (≤90 days). Unbounded → refused; expired → blocks loudly; expiring ≤14 days → named by a green run |
| Baseline eligibility | computed, not asserted: `subagent` mode or `hook_active: false` ⇒ never eligible |
| Staleness | three comparisons, all blocking, all naming the changed input |

---

## 4. BLOCKERS — the run cannot start until these are closed

### ❌ B1 — There is no driver

`record_run(*, root, dest, driver, skill, run_id, mode, out_dir, transcript)`
takes `driver` as a callable receiving a `RunContext`. Every test passes a
**stub**. Nothing has ever connected it to a real model.

*Closed when:* a driver exists that dispatches the skill under evaluation and
returns when the flow completes, and one run produces a record that
`eval_rubric.read_record()` loads with `unreadable == ()`.

### ❌ B2 — `hook_active` must be true, and no run has proven it in the field

The recorder installs `hooks/hooks.json` and sets the dispatch-observation
env, but a stub satisfies it by calling `tp.record_observed_dispatch`
directly. A run where the PreToolUse hook does not actually fire is recorded
`baseline_eligible: false` — correct, and it means an unobserved first run
produces no baseline at all.

*Closed when:* one real run reports `hook_active: true` from the hook itself,
not from a stub.

### ❌ B3 — Mode must be `out-of-band`, and that path is unexercised

Only a clean out-of-band run may set or satisfy a baseline. An in-session
subagent run is `mode: "subagent"`, informational forever. The out-of-band
runner has never been run against a live model.

*Closed when:* a run completes with `mode: "out-of-band"`, in a throwaway
checkout, with `GH_TOKEN`/`GITHUB_TOKEN` scrubbed and
`GIT_CONFIG_GLOBAL=/dev/null` asserted.

### ❌ B4 — The pre-flight probe must return a row id

`derivation.probe(ws)` writes a row, reads it back, and returns its id;
`None` raises `InstrumentBroken` and the driver never runs. This is
deliberate — a healthy-looking id over an empty ledger is the exact failure
the pre-flight exists to catch — but it means a ledger that cannot be written
in the real runner aborts the run rather than producing a misleading score.

*Closed when:* the probe returns an id in the real runner's environment.

---

## 5. ⚠️ Known limits — not blockers, but read before trusting a number

**L1 — R5 can score `pass` on an instrument gap.** `absent` returns PASS over
zero rows, so a run whose `lens_route` rows carry no breadth at all scores R5
`pass` — violating the layer's own top rule. Filed as **D-0001**. I attempted
the fix and got it wrong: my constraint selected `step: em`, and the em step
**legitimately** sweeps the full catalog, which broke two end-to-end vectors.
Reverted. The selector needs the em/non-em split settled first, with a
fixture per case.

**L2 — The waiver acceptor is not authenticated.** In this product the
committer is routinely the model, and it satisfies the check by typing a
human's name. The bounds are not authentication either: `expires` is read
against the gate machine's own clock, `inputs_fingerprint` against a run
record the same committer produced. What they buy is that an unauthenticated
waiver **costs something to keep**.

**L3 — The first baseline is a photograph, not a standard.** It records what
one model did once. It does not establish that the behaviour was *good* — only
that a later run doing worse will be caught. Do not read the first vector as a
quality verdict.

**L4 — `engine_ran` couples to a lens.py internal** (`"signals" in
routing["context"]`). If that key ever becomes falsy-but-present, `loop.py`
and `_record_breadth` will disagree.

**L5 — Cost is null unless a transcript is passed.** `effective_tokens` comes
from `spend.py` reading a host transcript; without `record_run(transcript=…)`
the field is explicitly null rather than absent.

**L6 — `--gate` exits 2 for every skill today.** No runs, no baselines. If CI
runs the gate before the first run exists, it needs a no-runs-anywhere
convention.

---

## 6. The decision the first run forces

The gate compares to a baseline. The first run **is** the baseline, so it
gates nothing and cannot fail. Two consequences to accept deliberately:

1. **A first run scoring below 1.0 is the honest outcome**, not a bug to fix
   before recording. Baselining a hand-improved run makes the bar describe a
   flow nobody drove.
2. **`no_evidence` in a first vector is a finding about the instrument**, not
   the model — and it is the one worth chasing before the second run, because
   `pass→no_evidence` is a blocking transition forever after.

---

## 7. Ready check

| # | Gate | State |
| --- | --- | --- |
| 1 | Instrument built and green | ✅ |
| 2 | Recording surfaces landed, no-denial proven | ✅ |
| 3 | Gate rules settled and mutation-tested | ✅ |
| 4 | Fixture reproduces byte-identically | ✅ |
| 5 | Negative fixtures prove every check can fail | ✅ |
| 6 | A live driver exists | ❌ B1 |
| 7 | Hook observation proven in the field | ❌ B2 |
| 8 | Out-of-band mode exercised | ❌ B3 |
| 9 | Probe returns an id in the real runner | ❌ B4 |
| 10 | L1–L6 read and accepted | ⬜ human |

**Verdict: NOT READY.** B1 is the only one that needs building; B2–B4 are
assertions the first run either satisfies or refuses on its own. Row 10 is
yours.
