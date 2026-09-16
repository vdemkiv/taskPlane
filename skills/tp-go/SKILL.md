---
name: tp-go
description: Carry authorized delivery through requirements, design when needed, implementation, verification, and handoff. The orchestrator owns advancement; telemetry advises without blocking.
---

# Delivery: carry the goal through to working output

The root orchestrator is responsible for the complete flow and the final result.
Workers, when useful and authorized, own bounded tasks. The harness observes;
it does not approve tool calls, assign mandatory reviewers, or advance stages.
Native host permissions and the user's scope remain authoritative.

Read [the shared flow](references/shared-flow.md) for the common graph, task,
evidence and dashboard steps used by every stage and delegated agent.

## Execute the flow

1. **Product:** identify the requested outcome and concrete acceptance criteria.
   Reuse decisions already made. For a small fix this can be a few sentences.
2. **Design and Plan:** inspect the relevant code and choose the smallest change
   that satisfies the criteria. Explain consequential trade-offs. Write a design
   document only when the complexity or user request warrants one. Do not invent
   architectural layers, generalized frameworks, or future requirements.
3. **Build:** implement the authorized change using native tools. Continue through
   routine implementation decisions. Ask only for missing information that truly
   blocks work or a material change beyond existing authorization.
4. **Evaluate and Engineering:** run checks appropriate to the changed behavior,
   inspect the resulting diff, and fix actual defects. Keep iterating toward the
   outcome. Do not repeat passing checks without a changed input or concrete gap.
5. **Deliver and Retro:** report what works, the verification actually performed,
   and any remaining limitation. Retain a useful lesson if one emerged. Do not
   create a retrospective document merely to complete a checklist.

A stage is complete because its work is done and supported by evidence. The
orchestrator judges that evidence and moves on. A milestone note is an observation,
not proof of acceptance. Never claim success because a command returned zero.
No mandatory lens count, separate phase worker, contract activation, signed
submission, graph ledger, plan approval, or final sign-off is added to a task.
Respect approvals the user actually requested and the host's native boundaries.

## Observe cheaply

Resolve `taskplane/tp.py` from this skill's installed plugin root. Use Python to
invoke it (shown below as `python3 <tp.py>`). At the start of delivery, record:

```
python3 <tp.py> flow start --workspace <checkout> --goal "<requested outcome>"
```

At a meaningful milestone, use `flow progress --phase <phase> --note "<evidence>"`
with the same workspace. Record only useful progress, not each tool call. At
handoff, call `flow finish --note "<actual outcome>"`. `flow report` reads the latest
observations. Report telemetry as unavailable if recording fails; continue work.
Do not debug the observer as a prerequisite to delivering the user's change.

Hooks record tool metadata, action fingerprints and native token counters locally
in `.taskplane/flow-events.jsonl`. They do not store command bodies or tool output.
Native lineage discovers lens sessions even without child hooks. Root counters
use the start baseline; child sessions created during the flow count from zero.
Reports refresh final counters, with native session totals and missing coverage explicit.
There is no Taskplane token limit. Usage is a measured lower bound from the first
observation, not a billing total. Hosts without readable native counters show
unknown usage, not zero. Keep this local telemetry out of product commits.

Advisories flag repeated actions, calls without a milestone, excessive delegation,
and token growth. They are possible waste signals, not proof of overengineering.
When one appears, compare the current work against acceptance criteria, drop
unnecessary abstractions and reviews, read smaller relevant excerpts, and take
one concrete step toward tested output. Never reset milestones just to silence a
warning. Do not ask the user to approve an advisory or increase a token budget.

Use [native delegation](references/codex-native-dispatch.md) only if delegating.
