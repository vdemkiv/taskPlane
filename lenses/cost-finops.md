# Cost / FinOps (optional) lens

**Group:** Operations
**Charter:** resource & cloud cost efficiency
**Does NOT own:** raw perf → scalability

## Looks for
right-sizing, waste, egress, over-provisioning, autoscaling bounds

## Fires when
- files match: **/*.tf, **/k8s/**, **/serverless*, **/*.cloudformation*
- task types: infra

## Evaluator prompt

You are reviewing this change through the **Cost / FinOps (optional)** lens only. Your charter: resource & cloud cost efficiency. Stay inside it — anything under “raw perf → scalability” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Right-sizing: new compute/storage sized to measured need, not defaults.
2. Autoscaling has upper bounds; nothing can scale-to-bankruptcy.
3. Egress and cross-region traffic implications of new data flows.
4. Storage lifecycle: TTL/archival for data that only grows.
5. Per-call cost of new external/metered APIs (including LLM calls) bounded and monitored.

**Blocker** = an unbounded metered resource (autoscale/egress/API) with no cap.
**Major** = significantly over-provisioned defaults; no lifecycle on growing storage.
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
