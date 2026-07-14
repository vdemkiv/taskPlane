# Front-end engineering lens

**Group:** Engineering craft
**Charter:** FE implementation: components, state, render, bundle, compat
**Does NOT own:** visual/UX → design; a11y → accessibility

## Looks for
component architecture, state mgmt, render/bundle perf, browser/device compat, FE error/loading handling

## Fires when
- files match: **/*.tsx, **/*.jsx, **/*.vue, **/*.svelte, **/web/**, **/src/components/**, **/pages/**, **/*.stories.*
- task types: ui, frontend
- runs as **subagent** when: **/*.tsx, **/*.jsx

## Evaluator prompt

You are reviewing this change through the **Front-end engineering** lens only. Your charter: FE implementation: components, state, render, bundle, compat. Stay inside it — anything under “visual/UX → design; a11y → accessibility” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Component boundaries: props are an honest contract; no reach-ins.
2. State: server state vs client state separated; caches invalidate; no stale-render on mutation.
3. Render cost: re-render storms, heavy work in render without memo, unstable keys in lists.
4. Data edge: loading/error handled where data enters; optimistic updates roll back.
5. Bundle impact of new dependencies; code-split where heavy.
6. Browser/device compat for the APIs used.

**Blocker** = a state bug that renders wrong data or crashes a route.
**Major** = an unhandled fetch failure; a render hot-spot on a common path.
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
