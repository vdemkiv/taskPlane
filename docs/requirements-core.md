# Requirements at the core — refinement as the optimization lever

Your insight, made into design: **requirements are the spine of the knowledge
base, and requirement refinement is the single biggest lever on cost.** Well-
refined requirements (functional *and* non-functional) → near-straight-through
implementation. Under-refined → several iterations. So the system should spend
effort *up front on refinement* to save it downstream — and treat change
requests identically.

## 1. Requirements are a first-class KB record (not just decisions)

Alongside `decisions/`, the KB gets `requirements/` — the durable spine:

```
knowledge/requirements/R-0007-export-user-data.md
---
id: R-0007
status: refined | draft | in-progress | done | changed
refinement: 0.0–1.0            # how ready this is to build
functional: [...]              # testable statements
nfr: {performance, security, scale, a11y, ...}   # non-functional, by lens
acceptance: [...]              # → becomes the DoD
open_questions: [...]          # what's still fuzzy
links: {decisions, tasks, arch}
---
```

The PM/product lens owns these. Everything downstream (plan, tasks, DoD, EM
sign-off) traces back to a requirement id, so nothing is built that isn't
anchored to a refined requirement.

## 2. A refinement gate before build (the optimization point)

Before the loop lets EXECUTE start, it scores the requirement's **refinement**
across two axes and, critically, **routes the relevant lenses at refinement
time** — the NFR axis is literally "have security / scalability / architecture /
a11y been considered for this requirement yet?":

- **Functional completeness** — are the acceptance criteria testable and
  unambiguous? open questions closed?
- **Non-functional coverage** — for each lens the router says applies to this
  change (security, scalability, architecture, data-safety, a11y, …), is there
  an NFR stated? A gap = an unrefined NFR.

Output: a **refinement score + the specific gaps**, and an **iteration
forecast** ("2 gaps in NFR → expect ~2 fix cycles"). Low score → the loop
recommends refining *now* rather than discovering the gaps during EXECUTE/FIX
(where they cost a full build cycle each). This is the DoR for the plan step,
sharpened.

## 3. Task mode — quick vs full, cost-decided (your point)

Every requirement or change can be delivered at a **mode**, recorded on the
task and the KB:

| Mode | When | What it does |
| --- | --- | --- |
| **quick** | need it now; full cost not yet justified | minimal correct change to satisfy the acceptance criteria; **records a `debt` entry** linking to the full follow-up |
| **full** | cost is acceptable now | the properly-refined implementation across all applicable lenses |
| *(auto)* | — | the loop suggests quick vs full from the refinement score + change size + a cost estimate; the human picks |

The **quick path is first-class, not a hack**: it's a governed task with a
recorded debt item (`knowledge/debt/…`) so the "do it properly later" is
tracked, retrievable, and can be scheduled as its own requirement. Nothing gets
silently half-done.

## 4. Change requests use the same machinery

A change request is a **new/changed requirement** (`status: changed`, links to
the original). It goes through the same refinement gate and mode choice. Because
requirements are in the KB with their prior decisions, a change starts with the
context of what was already decided — cheap, and it won't contradict a settled
call.

## 5. The architecture lens fits here (context-window trap)

Architecture is an NFR axis on a requirement *and* an effort-tiered lens (built
today: skip / light / full). It avoids the LLM context problem by working from a
**maintained `knowledge/architecture.md` model** — read + incrementally update,
never re-derive from the whole codebase. Refinement asks "is the architectural
approach decided?"; the lens checks/updates the model at build and review.

## Sequencing (why order is critical, encoded)

```
requirement ─▶ refine (score + NFR-lens coverage + forecast)  ← spend effort HERE
     │              │ low score → refine now (cheap) 
     │              ▼
     └─▶ choose mode (quick w/ tracked debt | full)  ← cost decision
                    │
                    ▼
              plan → execute → evaluate → fix → EM → sign-off
              (every task anchored to R-id; decisions + debt land in the KB)
```

## Decisions (locked & implemented in `taskplane/requirements.py`)

1. **Refinement gate strictness** — **advisory with a loud forecast; a hard
   block only for high-cost/irreversible tasks.** Implemented: `gate(req,
   high_cost=…)` returns the score + forecast + recommendation and only sets
   `blocking=True` when below threshold *and* `high_cost`.
2. **Default mode** — when refinement is low **and** the change is small,
   default to **quick + tracked debt**, else **full**. Implemented:
   `suggest_mode(refinement_score, change_size)`; the quick path writes a
   `knowledge/debt/D-NNNN` item linked to the requirement.
3. **Cost estimate source** — **rough heuristic now** (`estimate_cost` =
   files + 2×NFR axes → small/medium/large); the estimate stays a
   size band, not a currency figure.

## What's built (this pass)

- `knowledge/requirements/R-NNNN-*.md` first-class records (functional, NFR-by-
  lens, acceptance, open questions); change requests are a `changed_from`
  requirement in the same store.
- `score_refinement()` — functional axis + NFR axis, where the **lens router**
  decides which NFR axes even apply to the change, so a plain code edit isn't
  penalised for lacking a security/scale NFR it never needed.
- `suggest_mode()`, `estimate_cost()`, and tracked `record_debt()`.
- CLI: `tp req new | score | mode | debt | list`. 13 tests (43 total green).

**Next (loop wiring):** call the refinement `gate()` at the plan→execute
boundary so the forecast shows before a build starts, and anchor each task to
its `R-id`.
