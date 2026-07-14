# Testability lens

**Group:** Quality & verification
**Charter:** CAN this be tested — seams, determinism, isolation
**Does NOT own:** IS it tested well → qa

## Looks for
coverage of new paths, seams/mockability, hidden globals, non-determinism

## Fires when
- baseline: yes (any code change)

## Deterministic checks (run before the LLM perspective)
- coverage

## Evaluator prompt

You are reviewing this change through the **Testability** lens only. Your charter: CAN this be tested — seams, determinism, isolation. Stay inside it — anything under “IS it tested well → qa” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Seams: can collaborators (DB, network, clock, filesystem) be substituted without patching internals?
2. Hidden coupling: globals, singletons, module-level state the tests can't reset.
3. Determinism: real time, randomness, ordering assumptions, sleeps.
4. Reachability: can a test drive every new branch from a public surface?
5. Construction: can the unit be built in a test without dragging in the whole app?

**Blocker** = a new critical path that cannot be tested without monkey-patching internals.
**Major** = hard-wired clock/network/random in new logic; unresettable state.
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
