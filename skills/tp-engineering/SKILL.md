---
name: tp-engineering
description: Review source code, changes, architecture, or security and report actionable findings. Also consumes Engineering evidence when explicitly invoked by a Taskplane delivery stage. Review does not implement fixes.
---

# Review the requested code

Preserve the requested repository, comparison, scope and lenses. Inspect actual
source, relevant dependencies, tests and accepted requirements. Report actionable
findings with severity, exact location, concrete failure and evidence. Separate
confirmed defects, uncertainty and uncovered areas. Review does not implement.

Use [the shared flow](../tp-go/references/shared-flow.md) for standalone Engineering
or an explicit delivery Engineering visit. Reuse supplied Build/Evaluate evidence
and the newest applicable verification results; later fail/unknown cannot fall
back to an old pass. Do not relabel fixtures as live host evidence.

A root integrated review is valid. This skill authorizes useful scoped native
reviewers for independent risks when appropriate; it does not impose a reviewer
or lens count. Preserve explicitly requested independence and report who actually
covered which scope. One worker covering two lenses is one reviewer.
See [native ownership](../tp-go/references/codex-native-dispatch.md).

Present concrete Engineering findings/coverage for separate approval. A standalone
review ends there unless the user authorizes the next work. No graph, dashboard,
context receipt or presentation wording grammar is an acceptance prerequisite.
