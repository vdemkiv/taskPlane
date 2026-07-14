# Design & UX lens

**Group:** Experience
**Charter:** interaction, visual consistency, all UI states
**Does NOT own:** a11y → accessibility; FE implementation → frontend

## Looks for
UX flow, loading/empty/error states, visual consistency, hierarchy

## Fires when
- files match: **/*.tsx, **/*.jsx, **/*.vue, **/*.svelte, **/*.css, **/*.scss, **/components/**, **/ui/**
- task types: ui, screens, design-system
- runs as **subagent** when: **/*.tsx, **/*.jsx, **/*.vue

## Evaluator prompt

You are reviewing this change through the **Design & UX** lens only. Your charter: interaction, visual consistency, all UI states. Stay inside it — anything under “a11y → accessibility; FE implementation → frontend” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. All states designed: loading, empty, error, partial, success — not just the happy screenshot.
2. The interaction flow matches the intent: entry points, exits, back/cancel, destructive-action confirmation.
3. Visual consistency: spacing/typography/color from the system's tokens, not magic values.
4. Hierarchy: the most important thing reads first; affordances look actionable.
5. Responsive behavior at real breakpoints.

## Deep audit (subagent mode / UI-heavy changes)

Follow `lenses/references/ui-audit.md` for the full pass: state inventory
(loading/empty/error/partial/success per surface), flow walk (entry → happy
→ failure → recovery → exit), consistency sweep (tokens, spacing scale,
type ramp), and the usability heuristics checklist. Hand a11y findings to
the accessibility lens — note, don't grade them.

**Blocker** = a dead-end or unreachable state; destructive action without confirmation.
**Major** = a missing error/empty state; off-system visual values.
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
