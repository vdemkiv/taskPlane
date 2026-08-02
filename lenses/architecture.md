# System design & architecture lens

**Group:** Architecture & systems
**Charter:** component boundaries, data flow, contracts, scaling & failure modes
**Does NOT own:** in-file code craft → code-quality; infra provisioning → devops

## Looks for
component/service decomposition, data flow & coupling, state & consistency, scaling & failure modes, tech-choice fit

## Fires when
- files match: **/architecture/**, **/adr/**, **/*.arch.md, **/docker-compose*, **/*.proto, **/k8s/**, **/design/**, **/*.tf
- task types: greenfield, system-design, distributed, integration

## Evaluator prompt

You are reviewing this change through the **System design & architecture** lens only. Your charter: component boundaries, data flow, contracts, scaling & failure modes. Stay inside it — anything under “in-file code craft → code-quality; infra provisioning → devops” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. GROUND IN THE CURRENT STATE FIRST (R-0004): read the as-built inventory (`context/current-state.md` in the knowledge store, injected into briefs as `knowledge.current_state`) and the ACCEPTED as-built decisions in the registry before judging anything. A design is reviewed as a DELTA against what exists — never in a vacuum. Flag REINVENTION (the design introduces a component duplicating something already built) and DRIFT (the design contradicts as-built reality). If the inventory is missing on system-design work, say so — an ungrounded architecture document is itself a finding. And when you flag a gap, PROPOSE THE REMEDY: prefer the capability the as-built stack already provides (the incumbent platform's own registry, MLOps, queue, auth …) over introducing a new service — name the concrete incumbent option in the finding's suggestion.
2. READ `knowledge/architecture.md` FIRST and judge the change against the documented model — never re-derive the architecture from the codebase.
3. Boundary integrity: does a new dependency cross a layer/service line that was deliberately separate?
4. Data flow & coupling: chatty call patterns, shared databases, implicit contracts between components.
5. State & consistency: where state lives is explicit; consistency model (strong/eventual) chosen, not accidental.
6. Failure modes of new edges: timeout, retry, backpressure, partial availability.
7. Tech-choice fit: new tech earns its place; effort matches the tier (light = sanity-check the boundary; full = design pass as a subagent).
8. UPDATE `knowledge/architecture.md` (or file a decision) when the shape changed — the model must stay current or the lens goes blind.

**Blocker** = a silent violation of a settled boundary or recorded decision; or reinventing / contradicting a component in the as-built inventory (current-state grounding).
**Major** = new cross-component coupling left undocumented.
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
