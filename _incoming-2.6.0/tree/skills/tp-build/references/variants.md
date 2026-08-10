# A/B variants — build it twice, choose once

When a feature's design space is genuinely wide (two credible UX shapes,
two credible architectures, or both), one build examines one point in the
space. Variants examine two — under identical governance — and let the
human choose with evidence instead of imagination.

## Why this is NOT a normal parallel wave

The loop's `wave` machinery runs scope-DISJOINT tasks and merges each on
evaluate-pass. Variants are the opposite: scope-IDENTICAL alternatives
that must never merge. The `selection` step replaces the merge — and it
is NATIVE: when plan/tasks.json declares `"mode": "ab-selection"` (or
tasks carry `variant` fields), the loop switches to A/B mode by itself —
waves stop serializing different variants, wave entries carry
`merge_on_pass: false`, and after ALL variants evaluate-pass the loop
pauses at the `selection` human gate (a first-class spine node with its
own dashboard buttons). The harness is unchanged — per agent, per
workspace — same contracts, budgets, and hook enforcement as any task.

## Procedure

1. **One requirement, shared criteria.** A single R-record; every
   acceptance criterion applies to BOTH variants. If a criterion only fits
   one variant, it's a spec smell — fix the requirement first.
2. **Two variant specs, deliberately divergent.** Name the axes: UX shape
   (e.g. overview cards vs drill-down trend) and/or technical approach
   (e.g. server-side SQL aggregation vs client-computed pure lib). Give
   each spec the SAME finish bar: tests green, type-check clean, commit.
3. **Plan with `mode: ab-selection`.** Both tasks in plan/tasks.json carry
   the same `req` and criteria, a `variant` field ("A"/"B"), and their own
   scope lists (overlap is expected and fine — they never meet). The plan
   gate detects this and arms the loop's A/B mode automatically.
4. **Isolate and contract.** Per variant: `git worktree add
   .tp-work/variant-X -b tp-ab/variant-X`, then activate a contract IN
   that workspace (`$TP new --workspace <worktree> --max-actions N
   --scope "<variant scope>" --tests "<cmd>"`). Copy the variant spec into
   the worktree (worktrees don't carry untracked files).
5. **Dispatch concurrently.** One governed subagent per variant, same
   prompt skeleton: spec, criteria, environment facts (deps provisioned —
   don't let agents burn budget on installs), TDD, commit on finish,
   return structured JSON (tests, files, per-criterion notes, decisions).
6. **Evaluate BOTH, comparatively.** Re-run each variant's tests yourself
   (trust but verify), then one read-only tp-engineering review over both
   diffs: per-criterion walk per variant, full lens catalog, plus a
   comparison — correctness risk, complexity, maintainability,
   extensibility, performance at scale, UX fit per persona. Ask for a
   pick recommendation: A, B, or hybrid (and what the hybrid takes from
   each).
7. **Render both — real over mock.** Boot both implementations (separate
   ports/DBs, seeded with enough data to be meaningful) and screenshot
   the actual screens, including one interaction (month nav, drill-down).
   If booting is impossible, faithful HTML mocks from the components'
   real classes — and SAY they're mocks. Show side by side with the
   criteria scoreboard, findings, and per-variant resource spend
   (actions/budget, tokens, time, diff size).
8. **The selection gate — native.** The loop is paused at step
   `selection`; the dashboard renders one button per variant plus hybrid
   and neither. Never pre-empt it. On the human's explicit choice:
   `$TP loop select <variant|task-id|hybrid> --note "<their why>"` —
   the WHY is recorded to the KB automatically (the rationale outlives
   the losing branch).
9. **After the pick.** A winner → the loop moves to the engineering
   review: merge `tp/<winner>`, keep the losing branch as reference until
   the retro, clear the variant worktree contracts. Hybrid → the loop
   returns to `plan`: write the graft task (winner's branch as base,
   grafted parts named), plan approval and the build/evaluate cycle apply,
   and the loop pauses at `selection` again for the hybrid's confirmation.
   Then visual sign-off as usual.

## Costs, honestly

Two variants ≈ 2× build spend (budgets make it visible: e.g. 43/50 and
50/50 actions, ~63k vs ~83k tokens in the reference run) for one feature
— buy it when the design decision is expensive to reverse, skip it when
any reasonable shape will do. The selection evidence (real screens, lens
findings, resource meters) is reusable in the sign-off and the retro.
