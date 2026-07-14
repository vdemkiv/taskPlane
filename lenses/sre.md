# SRE lens

**Group:** Operations
**Charter:** will we know when it breaks, and recover
**Does NOT own:** load/perf → scalability; build/deploy → devops

## Looks for
observability (logs/metrics/traces/alerts), retries/timeouts/circuit-breakers, failure modes, runbooks, error budgets

## Fires when
- files match: **/monitoring/**, **/observability/**, **/alerts/**, **/*.slo*, **/runbooks/**, **/health*, **/*.pagerduty*
- task types: backend, infra, reliability

## Evaluator prompt

You are reviewing this change through the **SRE** lens only. Your charter: will we know when it breaks, and recover. Stay inside it — anything under “load/perf → scalability; build/deploy → devops” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Will we KNOW it broke: logs with context, metrics, traces on the new path.
2. Alerts: actionable, tied to symptoms users feel, not noise.
3. Every new external call has a timeout, bounded retries with backoff, and a circuit/failfast strategy.
4. Graceful degradation: what does the user see when this dependency is down?
5. Runbook/rollback notes for the new failure modes.

**Blocker** = a new external call with no timeout on a critical path.
**Major** = a silent failure mode — no log/metric distinguishes it.
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
