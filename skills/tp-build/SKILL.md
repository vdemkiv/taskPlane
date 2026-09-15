---
name: tp-build
description: Build a requested feature through to verified output. Reuse existing product decisions and use optional alternatives only when requested or needed.
---

# Build a feature

Read [delivery](../tp-go/SKILL.md) and execute its flow. The root orchestrator
owns completion. Clarify the outcome, inspect the existing implementation, make
the smallest sufficient design, build, verify, and deliver.

Use a visual when it helps settle a UI decision; do not require one for backend
work. Discuss alternatives when a consequential trade-off needs a decision.
Build A/B variants only when requested, in separate checkouts, and let the user
select before integrating mutually exclusive variants. Do not create multiple
implementations or review waves by default.

Reuse authorization already given. Missing telemetry, a stale legacy run, or a
review quota must not block authorized implementation. Native host permissions
remain in force.
