---
name: tp-northstar
description: "The on-demand STRATEGIC lens of taskplane — the north-star review. Summon it, don't schedule it: 'north-star this', 'board this', 'strategic take on this', 'does this serve the direction', 'is this worth doing given where we're going', 'strategic review of this task, diff, PR, plan or idea'. It is NOT a loop stage — tp-product (the WHAT) and tp-engineering (the SOUND) are the two automatic seats; this is the third lens you call when you want a direction check. Read-only and advisory: it measures a target against the project's north star and returns one strategic note (alignment + Leverage, Reversibility, Opportunity cost, Coherence + the sharpest tension + a recommendation). It never gates the loop, never edits code, never simulates executives."
---

# /tp-northstar — the north-star review (summoned, advisory, never a gate)

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`. Product owns the WHAT,
engineering owns whether it's SOUND. This is the third lens — **strategy** —
and it is **summoned, not automatic**. Point it at anything: an idea, a
requirement, a task, a diff/PR, or a finished review. It changes nothing; it
gives the human one strategic note to weigh.

No executive personas, no CFO, no cost/pricing — taskplane keeps pricing out of
the product. This is honest strategic *lenses*, not a boardroom cosplay.

## Run it

1. **Get the north star** — `$TP north-star`. It prints the project's
   `Direction / north star:` line from `context/product.md`. If it's unset,
   say so and offer to help the human write it (one sentence) before judging —
   alignment against nothing is theatre.
2. **Read the target** — the idea/requirement/task/diff/review the human named.
   For a diff, read the change; for a task, its scope + requirement; for a
   review, its findings.
3. **Apply the five lenses** (honestly named — each is a real decision check,
   not a role):
   - **Alignment** — does this move us toward the north star, sideways, or
     away? Rate `on-course` / `drift` / `off-course`. Name the **opportunity
     cost** (doing this = not doing what?) and any **scope drift**.
   - **Leverage** — does it unlock disproportionate future value, or is it a
     dead-end? (`high` / `med` / `low`)
   - **Reversibility** — one-way door or two-way door? Bias toward speed on
     reversible calls, toward caution on irreversible ones.
   - **Opportunity cost** — what we forgo by doing this now.
   - **Coherence** — does it fit the system's story, or add a special case /
     second concept the model now has to carry?
4. **Name the single sharpest tension** and a **recommendation**: `proceed` /
   `proceed-with-eyes-open` / `reconsider`. A note with no tension named
   reviewed nothing — find the real trade-off.
5. **Render it** — write the note JSON, then
   `$TP north-star --render note.json` prints the inline widget fragment; show
   it via `mcp__visualize__show_widget` (title `northstar_<target-slug>`). The
   note is the deliverable and the human acts on it — it does NOT feed a gate.
6. **Record it (optional)** — `$TP kb record "North-star review: <target>"
   --tags north-star` so the decision and its rationale persist.

## Note shape (for `--render`)

```json
{
  "target": "add A/B variant worktrees",
  "alignment": {"verdict": "drift",
                "note": "strengthens the loop but variants are a power-user branch; north star is trust, not breadth. Opp-cost: the default-deny screen (a trust bug) waits while this lands."},
  "lenses": [
    {"name": "Leverage",       "read": "low",  "note": "few flows use it; doesn't unlock later work"},
    {"name": "Reversibility",  "read": "two-way", "note": "isolated in worktrees, cheap to pull"},
    {"name": "Opportunity cost","read": "high", "note": "displaces the screen fix"},
    {"name": "Coherence",      "read": "med",  "note": "adds a second 'selection' gate concept to the state machine"}
  ],
  "tension": "breadth vs the trust promise",
  "recommendation": "proceed-with-eyes-open",
  "rationale": "ship it, but after the screen fix."
}
```

`north_star` is filled in for you from `context/product.md` when you render.

## Boundaries

Read-only toward code (enforce with `$TP new --read-only --write-allow
".em-review/**" "north-star review: <target>"` if you want the harness on).
Advisory only — it informs `plan_approval` / `signoff` decisions but is never
itself a gate, and it never simulates executives or touches pricing.
