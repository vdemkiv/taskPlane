---
name: tp-product
description: Define and refine what to build, acceptance criteria, dependencies, and Product readiness/completion. Author Product documents without changing implementation.
---

# Product — define the requested outcome

Preserve the user's problem, repository, selected requirement, scope and existing
decisions. Product authors and reviews requirements; it does not implement code.
Use the current native conversation and file tools. Reuse readable local source;
use repository preparation only when the requested source is unavailable.

## Standalone Product work

Resolve the installed engine from the loaded plugin root, then this skill's
location. Use its absolute `taskplane/tp.py` path with Python. Do not select an
older engine merely because a workspace launcher still names it.

1. Reuse the selected requirement when one exists. For a new request, collect the
   problem, intended behavior, applicable risks, exclusions and falsifiable
   acceptance criteria before recording one complete requirement.
2. Start the scoped Product contract with `new --product --available-tools
   "<actual comma-separated native tool names>" --workspace <checkout> "<goal>"`.
   This permits documents under `docs/**`, `specs/**` and `knowledge/**`, checks
   that a read and write tool are available, and preserves existing contracts
   and active delivery. It does not initialize Build or approve a requirement.
   When setup is missing, diagnose the named prerequisite once through the
   existing onboarding path; do not retry an unchanged refusal or replace a run.
3. Inspect evidence with native Read/Grep/Glob, or Codex's native read-only
   command described below. Write documents with scoped Write/Edit/apply_patch.
   Runtime setup, compatibility, successful execution and content readiness are
   separate facts. Never call all of them ready from an executable lookup.
4. Record `req new` once with functional statements, acceptance criteria, context
   files, dependency IDs and contracts. Include canonical security and
   architecture NFRs plus material risk axes. Amend the same R-record when
   refining it. Use the existing requirements reference for exact field syntax.
5. Run `req score R-XXXX --files "<globs>"` once, then `graph link --req R-XXXX
   --kind planned --files "<same globs>"` and `req mode` when mode selection is
   relevant. `req show R-XXXX` retrieves the same record and its Product gates.
   These are exact installed-engine controls, not arbitrary interpreter access.
   On Codex, run each control with `/bin/sh`, `login: false`, and the exact
   checkout as `workdir`, using one native call per expression. The same rule
   applies to normal finish. Product graph links are explicitly `planned`;
   realized implementation links belong to delivery.
6. Review the product risks through a minimum-sufficient focused route. Record
   all 26 lens dispositions, with reasons and evidence; launch only selected
   specialist work. Clearly distinguish author assessment, independent review,
   simulated tests and installed-host observations. A coverage ledger alone is
   not evidence that 26 workers ran.
7. Deliver the report and requirement with the explicit DoR/DoD table below.
   Text and file links are sufficient. An optional panel/widget failure never
   prevents delivery or normal finish. Record an applicable human decision only
   when the user actually provided it; preserve earlier authorization and do not
   request it again. Product completion does not imply implementation approval.
8. Finish the exact standalone contract with `clear --task-id <task_id from
   activation> --workspace <checkout>`. This retains artifacts and decisions and
   opens no Build authority. It cannot release another task, a worker, or an
   active delivery. Use the same finish operation for a stopped Product draft;
   keep its incomplete DoD visible. Do not use delivery `dod` to close Product.

Do not activate another contract to recover from the current one. Human input,
showing the saved Product artifact and exact normal finish remain reachable at
resource limits. A named capability failure is an integration gap, not a defect
in the product being specified or a reason to start a FIX loop.

## Native Codex reads

The existing `host_capabilities.codex_readonly_command(argv, workspace)` returns
an `exec_command` request using the installed Codex `sandbox
--include-managed-config -P :read-only`, `/bin/sh`, and `login: false`.
Activation prints a native read request to establish the exact executable and
outer invocation. Run it unchanged for the initial read; retain that prefix and
outer invocation for subsequent bounded read argv. Codex owns permissions and sandbox enforcement;
Taskplane launches no file transport or process. On macOS, the request may ask
native approval to start the narrower sandbox outside a non-nestable sandbox.
Do not weaken the profile to get a successful read.

For a projected Codex hook, use one native call per expression:
`text(await tools.exec_command(<JSON request>));`. The hook compares it with the
actual pending native call. Missing or ambiguous host records are an explicit
unsupported read, never authorization for arbitrary shell execution. Keep reads
bounded; this adapter does not support long-running interactive read sessions.

## Always show readiness and completion separately

Every Product result includes a table with criterion, evidence, status, gap and
next owner, covering:

- **Product DoR — content:** problem, functional behavior, acceptance criteria,
  critical NFRs, dependencies/contracts and unresolved questions. Consume
  `product_gates.dor`; a score alone is insufficient.
- **Product DoR — operations:** which required read, document, control,
  presentation and finish operations actually worked. Label simulated or
  unavailable evidence; a tool being installed is not proof of execution.
- **Product DoD — phase:** retained report and same requirement, required review
  evidence, applicable human disposition, accessible artifacts and normal finish.
  `product_gates.dod` cannot infer stage-review evidence from a requirement record;
  consume the existing current stage gate when that evidence exists, otherwise
  retain `not_verified` or `pending`.
- **Implementation DoD:** the requirement's acceptance criteria remain unverified
  until Engineering supplies candidate-bound results. Product authorship and
  approval never stand in for implemented behavior.

Also present the original goals, prioritized problem spaces, proposed remedies,
trade-offs, exclusions, measurement, and outstanding ownership. For a repeat
review compare the same goals and criteria against the earlier baseline and
report regressions, unchanged gaps and improvements separately.

## Product inside stateless delivery

A stage worker consumes only its emitted `taskplane.stage-dispatch/v1` startup,
selected artifacts and versioned handoff. Do not activate a standalone Product
contract or initialize another requirement/run. Refine the supplied requirement
through the declared candidate output, prepare and collect the selected lenses
through the existing stage interfaces, and return the declared artifacts.
The orchestrator applies the mechanical gate and carries existing human authority.
Do not inherit predecessor conversations, runtime roots, leases or event logs.

Non-Build terminal outcomes retain immutable artifacts and create no implicit
Build. Reuse later requires the existing explicit handoff and new authority.
Do not manufacture a receipt, sign-off or completion result. A stage's content
and decisions remain distinct from its execution lifecycle.

See [requirement fields](references/requirements.md),
[the Product persona](../../agents/tp-product.md), and
[the delivery driver](../tp-go/SKILL.md) for their respective responsibilities.
