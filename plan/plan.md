# Plan — stateless `tp pickup` front door

## Authority and bounds

This Plan realizes only R-0001 from the approved Design Contract at baseline `726acd108d3ca431e680183de129918842202eda`. The current engine-approved Design fingerprint is `f3ccf610cb8b6cc17c8ce68a087f5edf691693e42ddc3c3441d354026952970d`; the repository decision record and engine state agree on that fingerprint. The approved graph baseline is `e0d54a84434269c488941a265f865803c90c7e8adaa0796159ea60a3257adc8b` with 50 modules and 165 edges.

The one required bounded impact query covered all fourteen proposed module paths in one comma-separated `--files` value. It returned a complete, non-degraded scan at the exact baseline SHA, 26 impacted modules through depth 3, no truncation, no blocked policy boundary, and zero unknown modules. Every task therefore carries the engine policy unchanged: local depth 3, contract-only boundary mode, contract depth 1, and requirement depth 1. All fourteen approved proposed-module strings are declared exactly in their owning tasks for machine-enforced Design coverage; that declaration includes the two genuinely new file surfaces and does not introduce an unapproved graph surface.

This is one isolated, stateless `tp pickup <design-contract>` element. It creates or inherits no run, track, claim, lease, wave, stage, per-task-lens, or equivalent orchestration state, and it requires no active loop. The command consumes repository-signed Design authority only when a clean checkout is at the exact authorized SHA and the incumbent engine receipt matches that SHA and the recomputed Design fingerprint. Caller-authored approval or engine evidence is never authority.

The six canonical boundaries are preserved exactly:

- `contract:pickup.stateless-front-door`
- `contract:design.approved-contract`
- `contract:build-c.direct-assignment`
- `contract:build-c.acceptance-checkpoint`
- `contract:repository.merge-on-green`
- `resource:exports.pickup-receipts`

The plan changes no hook, loop, requirement, backlog, graph declaration, CI, deployment, secret, paused branch, retained worktree, or unrelated Taskplane home. Phase 0, E0, R-0011, and every other R-0013 item remain out of scope. There is no automatic deep, full, all-lens, serial-all, or 26-lens sweep, and there is no second approval, checkpoint, merge, worktree, requirement, or private receipt system.

## Delivery order

The implementation uses six serialized checkpoints. T01 through T05 advance exactly one acceptance criterion at a time. Production behavior and the focused test for the current AC must be in the same implementation commit; a red, interrupted, stale, or receipt-incomplete checkpoint leaves durable evidence but cannot authorize the next task or integration. No task may dispatch BUILD-C before its own preflight boundary is green.

### T01 — AC1: signed authority, stateless CLI, and live green path

Add the single public command and the bounded coordinator. The CLI accepts one repository-relative regular Design Contract path, rejects absolute paths, `..`, symlinks, non-regular files, and paths outside the checkout, and delegates once to `pickup.run(...)`. The approved-contract owner recomputes the Design evidence fingerprint and verifies the incumbent signed approval and engine receipt against the exact authorized source SHA. Pickup derives only the selected one-element in-memory micro-plan and calls the incumbent explicit-input `assign_direct_scopes`, one-AC checkpoint, and merge-on-green owners.

This checkpoint carries the highest security risk and therefore runs first. Its focused signed-shelf trace must show pickup → BUILD-C → checkpoint → repository integration while storage instrumentation and an empty-home before/after comparison prove zero orchestration mutation. `taskplane/storage.py` and `taskplane/taskplane_lite.py` are negative witnesses and must remain byte-unchanged, as must `taskplane/loop.py`, `hooks/**`, and `.taskplane/codex-hook.py`.

The same focused commit must include explicit refusal tests for:

- a dirty tracked checkout or untracked product file;
- a missing engine receipt or an engine receipt bound to a different source SHA or Design fingerprint; and
- a tampered Design Contract, stale Design bytes, or missing/invalid signed approval.

Each refusal must name its boundary, exit nonzero before `assign_direct_scopes` or any BUILD-C checkpoint dispatch, create no run/track/claim/lease/wave-equivalent state, write no pickup receipt, and perform no integration. This is also where forged caller evidence and unknown authority fields are refused.

### T02 — AC2: cold-start budget

From a fresh checkout at the same authorized SHA, an empty unrelated `TASKPLANE_HOME`, no warmed process, and no inherited runtime state, measure monotonic time from public CLI entry to `pickup.checkpoint.started`. The result must be strictly less than 120.0 seconds. Pre-checkpoint work is limited to repository authority, cleanliness, exact identity, one-element selection, receipt-lineage inspection, and direct assignment; private-loop bootstrap is forbidden.

### T03 — AC3: mandatory BUILD-C edge and compatibility

Prove that `taskplane/pickup.py -> taskplane/build_c.py` is a real production edge. The normal signed-shelf flow must pass, and severing that call must make the end-to-end test fail. BUILD-C continues through the incumbent checkpoint owner; it does not accept a caller-authored green result. The existing legacy loop selectors remain green, while Git diff evidence proves the loop and hook sources are byte-unchanged from the baseline.

### T04 — AC4: repository-only receipt lineage and resume

Persist append-only `taskplane.pickup-receipt/v1` evidence under:

`exports/pickup/<authorized-source-sha>/<design-evidence-fingerprint>/<element-id>/<ordinal>-<ac-id>-<receipt-digest>.json`

The key is always the original authorized source SHA plus the approved Design fingerprint. Each closed receipt binds the approval digest, engine receipt digest, element and micro-plan identities, criterion ordinal/id, predecessor digest, exact assigned revision and scope, complete checkpoint receipt, incumbent merge receipt and outcome, integrated tree fingerprint, producer id, terminal status, and canonical digest. Files are content-addressed and never overwritten; the lineage must have one contiguous successor per completed criterion and no fork, gap, collision, foreign identity, incompatible consumed outcome, path/digest mismatch, or self-referential future commit SHA.

The focused proof completes one criterion, commits its export evidence, removes access to the first checkout's private home, and resumes from a second checkout using only Git-tracked Design authority and receipts. Collision/tamper and interrupted-checkpoint tests must preserve prior receipts, authorize neither a later AC nor merge, and create no private handoff state.

### T05 — AC5: final functional regression checkpoint

Run `python -m pytest taskplane/tests -q` on the exact clean functional SHA and compare it with the baseline result. Any new failure blocks release metadata and review. T05 is verification, not a broad repair license: a regression returns to its owning AC task, and any fix must remain within the approved surface with its focused same-commit proof.

### T06 — 2.17.20 release surfaces

Only after T05 is green, update README, CHANGELOG, `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` consistently to 2.17.20. This metadata-only commit reruns the full portable suite so AC5 remains green on the final clean candidate SHA and rechecks that loop and hook sources are unchanged. It performs no push, tag, publication, marketplace action, package release, or `origin/main` mutation.

## Approved module and edge coverage

The task scopes collectively cover every approved existing and new module: the public CLI, pickup coordinator, BUILD-C, checkpoint, Design authority, repository owner, the two unchanged private-state witnesses, focused pickup tests, README, CHANGELOG, and all three manifests. The proposed runtime/test overlay is copied canonically into `plan/tasks.json`: CLI → pickup; approved Design authority → pickup; direct assignment → pickup; pickup → BUILD-C → checkpoint/repository; checkpoint and repository contract consumption; pickup → export receipts; and focused tests → pickup/storage. No relation-prefixed contract alias is used.

The incumbent boundaries remain owners rather than redesign targets. `design_contract.py` verifies repository authority, `build_c.py` owns direct assignment and green authorization, `checkpoint.py` owns exact-revision engine receipts, and `repository.py` owns worktree identity and ordinary merge. Pickup never shells out to merge directly and never creates an alternate checkpoint or receipt authority.

## Risks and stop conditions

- Authority lookalikes: only incumbent signed approval and engine receipt verification may authorize pickup. Any missing, malformed, mismatched, stale, unknown-field, or caller-authored evidence refuses before BUILD-C dispatch or receipt/state creation.
- Hidden orchestration: imports or mutations involving loop/run/track/claim/lease/wave state are a blocker, not an implementation shortcut.
- Wrong-revision integration: merge requires the exact assigned revision, declared scope, current one-AC engine-green checkpoint, and matching predecessor lineage.
- Receipt ambiguity: collision, overwrite, fork, gap, digest mismatch, foreign identity, or unexplained history refuses before assignment and preserves existing evidence.
- Design drift: any need to change hook security, legacy loop semantics, an incumbent contract, more than one element, an out-of-scope path, or the 3/1/1 depth policy stops for a new human Design decision.
- Premature release: 2.17.20 metadata cannot land before functional green, and no external release-side mutation is authorized here.

## Final review and authority boundary

After T06 is green on one exact clean SHA, dispatch exactly two quick reviews concurrently against that same SHA and evidence set: one security pass and one QA pass. There is no automatic deep/full sweep. Both quick results must complete before a subsequent engineering-manager review of the identical SHA, AC1–AC5 checkpoint lineage, full-suite result, graph/depth conformance, receipt lineage, protected-boundary diff, and version surfaces.

Report the 2.17.20 release candidate and stop. The Plan does not submit a gate or approval and authorizes no push, tag, publication, package/marketplace release, or mutation of `origin/main`; each remains a separate explicit human decision.
