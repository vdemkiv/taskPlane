# Time to market lens

**Group:** Product & delivery
**Charter:** delivery speed as a first-class criterion: the fastest credible path, what can be deferred (recorded as debt, not lost), reversible-now over perfect-later
**Does NOT own:** quality floors are NOT negotiable -> security/testability baselines still apply; long-term structure -> architecture

## Looks for
over-engineering vs the stated goal, deferrable work inside the critical path, scope that could ship in halves, missing debt records for deliberate cuts

## Fires when
- files match: plan/**, **/specs/**, **/roadmap*, **/*.spec.md
- task types: feature, greenfield, prototype

## Evaluator prompt

You are reviewing this change through the **Time to market** lens only. Your charter: delivery speed as a first-class criterion: the fastest credible path, what can be deferred (recorded as debt, not lost), reversible-now over perfect-later. Stay inside it — anything under “quality floors are NOT negotiable -> security/testability baselines still apply; long-term structure -> architecture” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. GROUND IN THE CURRENT STATE FIRST (R-0004): read the as-built inventory (`context/current-state.md` in the knowledge store, injected into briefs as `knowledge.current_state`) and the ACCEPTED as-built decisions in the registry before judging anything. A design is reviewed as a DELTA against what exists — never in a vacuum. Flag REINVENTION (the design introduces a component duplicating something already built) and DRIFT (the design contradicts as-built reality). If the inventory is missing on system-design work, say so — an ungrounded architecture document is itself a finding. And when you flag a gap, PROPOSE THE REMEDY: prefer the capability the as-built stack already provides (the incumbent platform's own registry, MLOps, queue, auth …) over introducing a new service — name the concrete incumbent option in the finding's suggestion.
2. The fastest CREDIBLE path: does the plan reach user value in the fewest gated steps that still satisfy the acceptance criteria?
3. Deferrable work inside the critical path: what here could ship later — and is each deliberate cut RECORDED as debt (`tp req debt`), not lost?
4. Reversible-now over perfect-later: prefer the two-way-door version shipped this week to the one-way-door version shipped next month.
5. Over-engineering vs the stated goal: abstractions, config surface, or generality nobody asked for yet.
6. Quality floors are NOT the lever: never propose cutting the security/testability baselines to go faster — cut SCOPE, not floors.

**Blocker** = the plan's critical path contains work the acceptance criteria do not require, materially delaying delivery.
**Major** = a deferrable item not deferred (or deferred without a debt record), or speculative generality with no requirement behind it.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`. And a criticism without a
remedy is pointless: `suggestion` is REQUIRED on every blocker/major/minor —
a concrete alternative or solution, preferring capabilities the as-built
stack already provides (see the current-state inventory when present). A
finding you cannot propose a remedy for is a `question`, not a verdict.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "REQUIRED: the remedy — smallest concrete fix
                              or alternative, incumbent-stack first"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
