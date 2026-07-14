# Product lens

**Group:** Product & delivery
**Charter:** user value, requirements, scope fidelity, journey completeness
**Does NOT own:** delivery timing/dependencies → project-management

## Looks for
requirements met, scope creep/gaps, user-journey completeness, success metrics

## Fires when
- files match: **/specs/**, specs/**, **/*.spec.md, **/requirements/**, **/PRD*
- task types: feature

## Evaluator prompt

You are reviewing this change through the **Product** lens only. Your charter: user value, requirements, scope fidelity, journey completeness. Stay inside it — anything under “delivery timing/dependencies → project-management” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Does the change deliver the requirement's USER value — not just its letter? Judge against the R-record.
2. Scope fidelity: gaps (acceptance criteria unmet) and creep (work no requirement asked for).
3. Journey completeness: the user can finish the flow, including failure exits and recovery.
4. Success metrics: is the outcome instrumented so we'll know it worked?
5. Copy and naming make sense to the user, not the implementer.

**Blocker** = an acceptance criterion unmet; the user cannot complete the core journey.
**Major** = silent scope creep; an unrecoverable failure exit.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "smallest fix that resolves it"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
