# Design Contract — `taskplane.design/v1`

`design/contract.json` is proposed-HOW evidence. It is an overlay on the current system, not permission to mutate code or the as-built graph.

## Required shape

```json
{
  "schema": "taskplane.design/v1",
  "requirement": "R-0001",
  "title": "Short design title",
  "summary": "What changes and why this shape",
  "current_state": {
    "summary": "Relevant as-built behavior and constraints",
    "sources": ["src/feature/file.ts", "context/current-state.md"]
  },
  "alternatives": [
    {
      "id": "a",
      "name": "Approach A",
      "description": "Concrete approach",
      "tradeoffs": {
        "gains": ["what improves"],
        "costs": ["what is given up"],
        "revisit_when": "observable condition that changes the call"
      }
    },
    {
      "id": "b",
      "name": "Approach B",
      "description": "Real alternative, including status quo when relevant",
      "tradeoffs": {
        "gains": ["what improves"],
        "costs": ["what is given up"],
        "revisit_when": "observable condition"
      }
    }
  ],
  "selected_approach": "a",
  "decision": "Why A is selected under current constraints",
  "modules": {
    "existing": ["orders/core"],
    "new": ["orders/cancellation"]
  },
  "contracts": [
    {
      "relation": "changes",
      "id": "contract:order-cancelled-v2",
      "description": "Named API/event/data/runtime boundary"
    }
  ],
  "graph": {
    "baseline_fingerprint": "engine-provided fingerprint",
    "proposed_modules": ["orders/core", "orders/cancellation"],
    "proposed_edges": [
      {
        "from": "orders/cancellation",
        "to": "contract:order-cancelled-v2",
        "kind": "provides",
        "reason": "Cancellation publishes the versioned event"
      }
    ],
    "depth_policy": {
      "local_depth": 3,
      "boundary_mode": "contract-only",
      "contract_depth": 1,
      "requirement_depth": 1
    },
    "dor": [
      {"check": "baseline graph is current", "evidence": "fingerprint …"}
    ],
    "dod": [
      {"check": "realized graph matches proposal", "evidence": "final review"}
    ]
  },
  "acceptance_map": [
    {
      "criterion": "Exact requirement acceptance criterion",
      "design_element": "Module/contract/decision that satisfies it",
      "validation": "Test, probe, or review evidence that will prove it"
    }
  ],
  "risks": [
    {"risk": "Failure or delivery risk", "mitigation": "Control", "owner": "Owner"}
  ],
  "failure_modes": [
    {"mode": "What fails", "detection": "How known", "recovery": "How restored"}
  ],
  "observability": {
    "signals": ["metric/log/trace/probe"],
    "alerts": ["actionable alert or explicit none rationale"]
  },
  "rollout": {
    "strategy": "How introduced safely",
    "rollback": "How reverted or disabled"
  },
  "visualization": {
    "required": true,
    "kind": "dependency-graph|sequence|state-transition|data-flow|ui-flow",
    "path": "design/visual.html",
    "reason": "Decision this visual clarifies"
  },
  "lens_evidence": [
    {
      "lens": "solution-design",
      "verdict": "pass",
      "blockers": 0,
      "evidence": "What was checked"
    }
  ],
  "open_questions": []
}
```

If no visual is useful, use `"required": false`, `"kind": "none"`, `"path": null`, and a non-empty reason.

## Graph rules

- Copy the baseline fingerprint from the Design action payload exactly.
- Every module under `modules.existing` and `modules.new` must appear in `graph.proposed_modules`.
- Proposed edges may reference proposed/current modules and named `contract:`, `resource:`, `svc:`, or `ext:` boundary nodes.
- An edge always includes `from`, `to`, `kind`, and the reason it exists.
- Do not run graph mutation commands during Design. The engine rejects approval if the as-built graph fingerprint changes.
- `contract-only` is the default distributed boundary: local dependencies may be explored to `local_depth`, but another service/entity is represented by its named contract, not its internals.

## Review conformance

Final `.em-review/findings.json` adds:

```json
{
  "meta": {
    "design": {
      "fingerprint": "approved fingerprint",
      "verdict": "conformant",
      "modules_checked": ["orders/core", "orders/cancellation"],
      "edges_checked": ["orders/cancellation->contract:order-cancelled-v2:provides"],
      "contracts_checked": ["contract:order-cancelled-v2"],
      "drift": []
    }
  }
}
```

Any drift blocks sign-off. Return to Design, update the contract, obtain a new human approval, and re-plan.

During Plan, every proposed edge is copied to the owning task's
`design_edges` list as `FROM->TO:KIND`. This makes edge coverage a mechanical
Plan DoR check rather than an implication inferred from file scope.
