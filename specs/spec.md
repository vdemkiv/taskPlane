# Spec — taskPlane 2.7 Codex-native workflow and release hardening

Requirement: **R-0001 — Harden taskPlane 2.7 and adopt native Codex workflows**.
The user explicitly approved implementation of all seven recommendations from
the v2.1→v2.7 review. This spec fixes the implementation gaps without changing
the release version from 2.7.0.

## Problem

taskPlane 2.7 has a substantially stronger Design/Build/Review engine, but four
gaps weaken its proof-oriented promise: the advertised regression gate is not
wired into governed contracts and is hard-coded to taskPlane's own package
layout; Codex uses portable briefs but not current native subagent metadata or
reasoning effort; the OpenAI ZIP contains dead README/docs references; and
committed release scratch pollutes routing, graph scans, and normal discovery.
Codex-host tests and release provenance also stop short of the actual target
environment.

## In scope

1. Wire graph-scoped regression checks into governed execute/fix, per-task DoD,
   and final sign-off contracts. Generalize Python module/test discovery beyond
   `taskplane/`, fall back visibly to the full discovered Python suite when
   radius mapping is incomplete, use the active interpreter, and treat missing
   pytest, collection, usage, interruption, and internal errors as blockers.
2. Add host-neutral dispatch identity plus Codex-native `task_name` and
   `reasoning_effort` fields to briefs. Verify role, model, and effort against
   current `spawn_agent`-shaped hook input without weakening Claude dispatch.
3. Document the native Codex transport: parallel spawn, bounded wait, result
   collection, interruption/escalation, role instructions, lifecycle tracing,
   and optional user-started `/goal`; keep Claude Dynamic Workflows unchanged.
4. Add `SubagentStart`/`SubagentStop` lifecycle hooks that trace agent identity
   and add contract context without claiming they are a security boundary.
5. Package README and the complete referenced documentation set; make the
   built package fail validation on any shipped README/docs pointer missing
   from the archive.
6. Add a Codex-host CI leg and isolate host-specific onboarding tests.
7. Remove `_incoming-2.6.0`, `_incoming-2.7.0`, `.fixwave`, and `_to_delete`
   from version control and ignore those scratch families.
8. Keep all manifests at 2.7.0, run the complete suite and marketplace-package
   validation, commit the hardening release, and create tag `v2.7.0`.

## Out of scope

- No new product persona, review lens, or user-facing loop stage.
- No port of Claude Dynamic Workflow JavaScript into the OpenAI package.
- No provider-specific Codex model id defaults.
- No weakening of human approval, scope, graph, evidence, or review gates.
- No automatic activation of Goal mode; `/goal` remains a user action.

## Acceptance criteria

1. Every governed execute/fix contract, task DoD reconstruction, and final
   sign-off test contract enables `dod.regression_gate`; a baseline-green,
   current-red radius test produces a `regression:` blocker.
2. Runner startup failure, missing pytest, collection/usage/internal error, or
   interruption produces a named `regression_gate:` blocker, never `[]`.
3. A Python source/test pair outside `taskplane/` is selected; an unmapped
   changed Python module visibly falls back to every discovered Python test.
4. Every emitted Codex dispatch contains a valid stable `task_name`, `role`,
   inherited-or-explicit model, and tier-derived reasoning effort. Strict mode
   rejects mismatched model or effort for current Codex hook input.
5. Codex guidance uses native subagents and lifecycle tools; Claude workflows,
   fallback briefs, human gates, and OpenAI workflow-file exclusion remain.
6. Every local README/docs path referenced by a shipped skill or runtime file
   is present in the OpenAI ZIP, enforced by package build and tests.
7. A `CODEX_HOME` CI leg passes. `pytest` at the repository root collects the
   canonical suite without duplicate incoming trees.
8. Full pytest, unittest-discover floor, cost ratchets, manifest validation,
   deterministic package build, and the detailed post-build review pass before
   commit/tag/submission.

## Contract handoff

- `scope_paths`: `taskplane/regression.py`, `taskplane/taskplane_lite.py`,
  `taskplane/loop.py`, `taskplane/tp.py`, `taskplane/tests/**`, `skills/**`,
  `hooks/hooks.json`, `scripts/package_openai.py`, `.github/workflows/ci.yml`,
  `README.md`, `docs/**`, `.gitignore`, and removal of the four scratch roots.
- `out_of_scope`: Claude workflow JavaScript semantics, lens catalog behavior,
  product requirement schema, legal/privacy copy, and release version numbers.
- `dod.test_command`: `python -m pytest -q taskplane/tests`.
- changed contracts: `contract:loop-gate`, `contract:codex-dispatch`,
  `contract:openai-package`.

## Risks and controls

- Regression checking can add cost: select graph/import-radius tests where
  possible and use full-suite fallback only when narrowing cannot be proven.
- Dispatch payload changes can churn goldens: regenerate mechanically and pin
  Claude/Codex parity separately.
- Lifecycle hooks cannot block a subagent start: use them for context and
  traceability; keep PreToolUse and evidence gates as enforcement.
- Broader package content can create dead-link drift: validate archive closure
  and deterministic bytes in CI.

There are no open product questions. The user approved all seven items; only
implementation choices within these acceptance bounds remain.
