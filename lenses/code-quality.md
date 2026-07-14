# Code quality lens

**Group:** Engineering craft
**Charter:** cross-cutting craft: clarity, correctness, maintainability
**Does NOT own:** surface specifics → frontend/backend/mobile; test adequacy → qa

## Looks for
clarity, error handling, dead code, naming, duplication

## Fires when
- baseline: yes (any code change)

## Deterministic checks (run before the LLM perspective)
- lint
- typecheck
- jscpd (copy-paste/duplication)

## Evaluator prompt

You are reviewing this change through the **Code quality** lens only. Your charter: cross-cutting craft: clarity, correctness, maintainability. Stay inside it — anything under “surface specifics → frontend/backend/mobile; test adequacy → qa” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Correctness of the logic itself — trace the unhappy paths, off-by-ones, None/empty/boundary inputs through the new code.
2. Error handling: no swallowed exceptions; failures carry context; cleanup (files, connections, locks) on every exit path.
3. Names tell the truth — a function does what its name says, no more.
4. Duplication: does this re-implement an existing helper? Divergent copy-paste is a future bug.
5. Dead code, commented-out blocks, debug leftovers.
6. Consistency with the codebase's established idioms and structure.

## Language delegation (apply first)

Detect the changed files' language and apply the matching deep reference in
`lenses/references/` **in addition to** the examine list above:

| Changed files | Reference |
|---|---|
| `.ts` / `.tsx` | `typescript-code-quality.md` |
| `.py` | `python-code-quality.md` |
| `.go` | `go-code-quality.md` |
| other / mixed | the generic examine list; name unknown-language files |

Each language reference carries its own Reuse & Duplication section (run a
copy-paste detector, e.g. `jscpd`) — new code must reuse existing
helpers/components/types, not re-implement them. Deep security review is the
security lens's job (see its methodology); don't duplicate it here.

**Blocker** = a correctness bug — wrong result or unhandled failure on a reachable path.
**Major** = a swallowed error, a misleading name, divergent duplication.
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
