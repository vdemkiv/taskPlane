---
name: taskplane
description: Route requests to review code, design a change, implement work, inspect Taskplane status, or explain Taskplane. Preserve the user's requested scope and existing decisions.
---

# Taskplane

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase taskplane --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Route the requested work directly. Product, Design, and Engineering always use the
shared dependency graph, task decomposition, and dashboard, including standalone
requests. Read [the shared flow](../tp-go/references/shared-flow.md) for these defaults.
Help and status inspect existing state without starting a run. The default native_workflow
profile enforces Taskplane gates using observed human responses and local state. Host-wide
protection is unavailable; explicitly requested protected_host execution still refuses
without a verified owner. Never substitute one profile for the other.

- Source review, architecture/security review, or validation: read
  [engineering](../tp-engineering/SKILL.md) and use native tools in the available checkout.
- Status: read [status](../tp-status/SKILL.md). Inspect existing state without initializing it.
- Help: read [help](../tp-help/SKILL.md). Answer the question without setup.
- Product requirements: read [product](../tp-product/SKILL.md).
- Design before implementation: read [design](../tp-design/SKILL.md).
- Build or fix an approved delivery plan: read [delivery](../tp-go/SKILL.md).
  For a new feature or explicit A/B prototype, use [build](../tp-build/SKILL.md).
- Explicit installation, setup, or configuration diagnostics: read
  [CLI reference](../../docs/cli-reference.md).

Product, Design, Plan, Build, Evaluate, Engineering and Retro each require acceptance
of their concrete output before advancement: human approval by default, or a valid
explicitly authorized policy decision under the shared flow. Engineering can be the entry point: its findings can define subsequent
Product scope, Design, and implementation when authorized. Keep native execution,
permissions, waiting, and session identity with the
host. Load only the selected workflow's references; don't recite internal setup or
introduce approval steps that the user has already satisfied.

Native discovery is observational. Use the installed named hook entry points and
report their actual capability status; restoring native routines does not admit
a host owner or change any human checkpoint.

## Scoped native workers

Native dispatch is the default for useful independent work under this skill.
Every new run scope must declare `execution_contract: "native-default/v1"`; this
is mandatory in this entry flow. Publish typed `execution: "native_required"`
tasks for ready independent work and one worker per selected Engineering lens,
with a unique `review_lens` on each lens task. This instruction authorizes that
bounded delegation; do not ask again solely because the user did not name agents.

Explicit user serial/no-delegation constraints take priority. Dependencies,
read/write conflicts and trivial scope can justify `execution: "root"` with a
substantive `execution_reason` and `execution_reference`. Root lens coverage is
`serial_scope`, never native independence. Observe host capacity, launch ready
independent tasks together and refill slots as prerequisites complete. Limited
capacity queues distinct workers; reusing one identity for several lenses does
not satisfy independence. An unavailable adapter needs an observed reason/reference;
required native tasks remain incomplete and block sealing.

Use the installed prepare/claim/context/join/result protocol. Execute the exact
returned `next_action` with the installed runtime launcher; it retains workspace,
run and task. Follow `--read-required` actions until `remaining_required` is zero.
Workers return scoped evidence. Only the root verifies and accepts joined fresh
results and advances phases under the existing approval policy. `native_verified`
lens coverage binds `task_id`, `grant` and the actual worker `reviewer`; labels alone
cannot satisfy frozen requirements. Historical untyped evidence stays unverified.


## Efficient native startup and context

For every new native task, declare exact `read_inputs` from the run verification
inputs or accepted Build paths, a concise `purpose`, and `context_budget_bytes`
(default 128 KiB of unique required bodies). Include source dependencies and tests
needed for the conclusion; narrow inputs must not hide a relevant dependency.
Missing read inputs retain conservative legacy coverage. Inspect preparation's
`context_preflight` before launch. Declare source/log/test detail artifacts as
`source`, `raw-log`, `verification` or `supporting`; keep concise requirements and
reports normative. Use `required_for` when supporting bodies are mandatory.

Always supply `fork_turns: "none"` explicitly. Start the first useful scoped task,
then observe its successful claim, complete context and matching automatic pre/post
hook pair before preparing the rest of the cohort. Scoped preparation enforces
this automatically; `readiness_after` can name an additional same-phase startup
prerequisite. Release remaining ready work together while the first task works.
Do not use throwaway probes or relaunch unchanged failures. A repeated scoped
attempt needs `retry_reason` naming the observed defect or changed input.

Execute the exact returned context action. New scoped workers use `--drain`, which
returns at most 32 KiB including bodies and receipt. Return every response to the
consumer, then follow `next_action` only while `remaining_required` is nonzero.
Terminal responses have `done: true` and no action. Accumulate all subprocess/tool
chunks until exit before parsing; never parse a running handle's partial output.
Keep the combined response budget large enough and use bounded long waits (up to
60 seconds between user updates), not repeated short model-facing polls.

After a narrow repair, repeat affected checks and reviewers only. Unchanged
scoped results remain fresh within their original binding; across visits use
independently fingerprinted check evidence and a fresh scoped delta review.
Never transfer approval, worker identity or a context receipt to another binding.
Inspect attempt purposes, retry causes and delivered bytes in worker status and
the dashboard. Delivered bytes, native tokens and Codex allowance are different
measurements; no allowance saving can be inferred from byte counts alone.
