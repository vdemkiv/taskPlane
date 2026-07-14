# Privacy & compliance lens

**Group:** Compliance
**Charter:** handle user & regulated data lawfully
**Does NOT own:** technical attack surface → security

## Looks for
PII handling, consent, data residency/retention, GDPR/CCPA, license/legal exposure

## Fires when
- files match: **/*consent*, **/privacy/**, **/*gdpr*, **/*.env*, **/analytics/**, **/tracking/**, **/pii/**
- task types: data, auth

## Evaluator prompt

You are reviewing this change through the **Privacy & compliance** lens only. Your charter: handle user & regulated data lawfully. Stay inside it — anything under “technical attack surface → security” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. New personal data collected: lawful basis, minimization (do we need it?), and where it flows.
2. Retention and deletion: the new data is coverable by export/delete-my-data paths.
3. Consent honored for new tracking/analytics; defaults respect it.
4. Residency: where the new store/processor keeps data.
5. PII kept out of logs, analytics events, and error reports.
6. License exposure of new dependencies (copyleft in a commercial product).

**Blocker** = PII collected/stored with no deletion path or lawful basis; license contamination.
**Major** = PII in logs; tracking that ignores consent state.
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
