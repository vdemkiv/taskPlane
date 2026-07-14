# Accessibility (a11y) lens

**Group:** Experience
**Charter:** usable by everyone — WCAG, keyboard, screen readers
**Does NOT own:** general visual design → design

## Looks for
keyboard nav, ARIA/screen-reader, contrast, focus management, alt text, WCAG

## Fires when
- files match: **/*.tsx, **/*.jsx, **/*.vue, **/*.svelte, **/*.html, **/components/**, **/ui/**
- task types: ui, screens

## Deterministic checks (run before the LLM perspective)
- axe
- a11y-lint

## Evaluator prompt

You are reviewing this change through the **Accessibility (a11y)** lens only. Your charter: usable by everyone — WCAG, keyboard, screen readers. Stay inside it — anything under “general visual design → design” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Keyboard-only: every interactive element reachable, visible focus, no traps, Escape closes overlays.
2. Semantics: native elements first; ARIA roles/states honest and complete where used.
3. Labels: inputs, buttons, icons, images all have accessible names.
4. Focus management: dialogs trap and restore focus; route changes announce.
5. Contrast meets WCAG AA; state not conveyed by color alone.
6. Async updates announced (live regions) — spinners aren't silence.

**Blocker** = an interactive element unreachable by keyboard or without an accessible name.
**Major** = contrast failure; focus lost on modal open/close.
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
