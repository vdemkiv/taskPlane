# One flow for all delivery agents

Product, Design, Plan, Build, Evaluate, Engineering, lenses and Retro use the same
workspace, run ID, task decomposition, dependency graph and dashboard. The root
orchestrator advances the stages and integrates evidence. Workers never start a
second flow or invent a separate task graph/dashboard for their stage.

## Every stage or delegated task

1. Read `flow report --workspace <root-workspace> --run <run-id>` once. Reuse the
   attached task plan, requirements, design and review evidence. If this is a
   standalone request with no active delivery, keep its requested scope; do not
   initialize a delivery solely for inspection.
2. Use the shared source graph. The orchestrator scans it with `graph
   --workspace <root-workspace> scan --decompose` when source changes. Product and
   Design inspect relevant dependencies; the planned task decomposition records
   prerequisites and acceptance criteria. Build follows that order. Evaluate and
   Engineering check affected consumers against actual graph impact. Mark proposed
   relationships as planned; a source scan is evidence of realized relationships.
3. Work only on the assigned scope. Return findings, verification and an evidence
   file to the orchestrator. Attach it to the existing run using `flow attach
   --workspace <root-workspace> --run <run-id> --evidence <relative-path>`.
   Missing telemetry is disclosed and never becomes a permission gate.
4. The orchestrator records each material milestone with `flow progress`, updates
   the shared task/review files. Start, progress, attachment and finish refresh
   `.taskplane/dashboard.html` automatically. Every actor uses that same view;
   no stage-specific replacement. Use `dashboard --workspace <root-workspace>
   --out <dashboard.html>` to refresh a final snapshot after late native responses.

## Shared artifacts

Attach the files once; later renders read their current contents:

```
flow attach --workspace <root-workspace> --run <run-id> \
  --tasks plan/tasks.json --reviews plan/reviews.json \
  --evidence specs/feature.md --evidence design/design.md
```

Tasks JSON has a `tasks` array. Each task records `id`, `title`, `dependencies`
(task IDs), `paths`, `status`, `verification`, and actual start/completion timestamps.
A review index is an array, or phase-keyed arrays, with `lens`, canonical native
`agent` handle, `phase`, and workspace-relative `evidence` for each real review.
Attach changed paths with repeated `--changed <path>` to drive graph impact.
The dashboard embeds attached evidence and the native graph; it never treats a
milestone alone as proof that acceptance criteria passed.

Native session lineage collects root and lens counters even when child hooks do
not fire. Milestones, finish and attachment persist the counters; reports and
renders refresh readable counters without changing the journal. Missing declared
reviewers remain visibly unmeasured. Host approval reviews are shown separately.
Use fresh, bounded review contexts and follow up on concrete findings only.

## Claude sessions

Claude uses these same commands, artifacts and dashboard. Resolve the runtime
from the loaded Claude plugin, not a workspace's old Codex launcher. SessionStart
binds the native Claude session and transcript to subsequent Bash commands using
Claude's session environment file. Product, Design and Engineering workers attach
to the root run; they never create another delivery because they are subagents.
Use the actual Claude `agent_id` in the review index, not the agent type or a
made-up Codex path. Subagent transcripts supply lens usage even without tool hooks.
Cache writes count as uncached input; duplicate streamed message records count
once. If this host does not expose transcripts or session binding, report that
coverage as unavailable and continue the same delivery flow.
