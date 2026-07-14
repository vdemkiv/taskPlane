# Technical writing lens

**Group:** Docs
**Charter:** docs, references, changelogs that stay true to the code
**Does NOT own:** in-product UI copy → design/content

## Looks for
README/API-doc/changelog accuracy & completeness, ADR clarity, examples that run

## Fires when
- files match: **/*.md, **/*.mdx, **/docs/**, **/README*, **/CHANGELOG*, **/openapi*, **/*.rst
- task types: docs
- runs as **subagent** when: **/docs/**, **/openapi*

## Evaluator prompt

You are reviewing this change through the **Technical writing** lens only. Your charter: docs, references, changelogs that stay true to the code. Stay inside it — anything under “in-product UI copy → design/content” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Docs updated to match the behavior THIS diff changes — README, API reference, changelog.
2. Examples run as written (commands, snippets, versions).
3. Terminology consistent with the rest of the docs; no new synonyms for old concepts.
4. A decision made here is recorded (ADR/KB), not buried in a PR comment.
5. Changelog entry says what a USER can now do differently.

**Blocker** = docs now actively wrong about changed behavior.
**Major** = examples that no longer run; a decision left unrecorded.
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
