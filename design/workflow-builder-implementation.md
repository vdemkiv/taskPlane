# Workflow Builder v0.1 — implementation design

Status: Fresh Design checkpoint proposal on the approved compatible baseline, 2026-10-01. No production implementation.
Run: `ea76260680b84274939afe6403c5e917`; Design visit: `930b95d402d040de9efe79890deb910a`.

This specifies the accepted [Product scope](../specs/workflow-builder-v0.1-product.md) and retains the earlier [working design](workflow-builder-v0.1.md). Product approval was recorded from the actual user reply “approve”. The earlier draft JSON remains illustrative; the contract below is the proposed implementation target.

## Decision and prerequisite

Build a conversational authoring role backed by a strict JSON compiler. It produces the scope, task definitions and provenance consumed by Taskplane's current harness. The orchestrator continues to start runs, dispatch native workers, accept evidence and present checkpoints.

**The source-baseline prerequisite is resolved.** This run uses pinned main `848a225ee7be822c692c3b9b62bdc4cdd82baea9`; its 37 top-level runtime modules match the installed harness as verified in the accepted Product evidence. The approved initial scope reserves 37 exact Build paths and 210 verification inputs. No harness backport is required. Plan must bind its actual DAG, ownership and typed checks inside that scope; it cannot silently widen it.

The current Design review is [recorded separately](../.taskplane/workflow-builder-delivery/design-evidence.md). It retains the historical compatibility analysis as evidence, without transferring native results or approvals. An older or incompatible loaded runtime must still return a capability blocker; that is not successful full-delivery coverage or completion of WFB-04.

## User interaction and commands

The `tp-workflow` entry handles “create”, “edit”, “preview” and “run” intents. The specialist `tp-workflow-builder` authors definitions and explains unresolved choices. A scoped builder worker only writes its assigned definition artifacts; root handles lifecycle operations.

A creation request opens a standalone Design authoring scope with exact definition/preview outputs. The final artifact is a reusable definition. Creation does not launch the workflow it describes. A subsequent run request, or the run portion of “create and run”, binds fresh inputs after the authoring checkpoint is resolved. An occupied workspace is surfaced as its actual current run; no nested run or implicit replacement.

Proposed CLI family, all through the verified loaded `tp.py`:

| Command | Result and effects |
|---|---|
| `workflow catalog --workspace ROOT` | Read available capabilities, contracts, missing requirements and loaded identity. |
| `workflow validate --workspace ROOT --definition FILE` | Strict static validation; no run or worker. |
| `workflow preview --workspace ROOT --definition FILE --inputs FILE` | Resolve available inputs; display work, reads, writes, decisions, gaps and provenance. Incomplete previews say they cannot run. No files are written by default. |
| `workflow save --workspace ROOT --definition FILE --out FILE` | Validate and create one exact, versioned project file. No overwrite; same bytes at an existing target are an idempotent success. |
| `workflow compile --workspace ROOT --definition FILE --inputs FILE --out DIR` | Require complete inputs and compatible runtime, then write the deterministic package to its exact bootstrap destination. No harness run starts. |
| `workflow check --workspace ROOT --run RUN` | Read the existing run binding and report compatibility/freshness for continuation. Does not repair, replace or approve it. |

Route read-only commands before generic CLI initialization. Preview/validate must not call a persistent context Session, workspace initialization, graph scan or run-start helper. Read an existing graph as optional supporting data and report absent coverage.

Diagnostics use `{code, location, message, remedy}` with JSON-pointer field locations where applicable. Invalid data exits nonzero; preview can return `incomplete` with explicit unresolved inputs. Parser abbreviations and duplicate workspace selectors are refused. Human-readable CLI output and machine JSON describe the same results.

There is no second CLI scheduler. For “run”, the skill invokes existing `flow activate/start/context/worker/attach/submit/present/decide/advance` operations in their normal order. Compilation returns typed command arguments for root, never an executable shell string supplied by a definition.

## Canonical definition

Schema: `taskplane.workflow-blueprint/v1`. UTF-8 JSON only. Reject duplicate JSON keys, non-finite numbers, unknown fields, unsupported schema versions and oversized documents before capability resolution. Initial limits: 256 KiB definition; 128 task templates; compiled task publication must satisfy the harness's 64 KiB snapshot limit. Do not truncate.

| Field | Contract |
|---|---|
| `id, version, name, description` | Stable lowercase identifier, semantic version string and bounded nonempty human text. |
| `route` | Exactly `{kind:"delivery"}` or `{kind:"standalone", phase:"product"|"design"|"engineering"}`. Delivery begins at Product. |
| `trigger` | Exactly `{kind:"manual"}`. |
| `inputs` | Map of named parameters to `type, required, default?`. Types: text, git_ref, workspace_file_list, workspace_output_file_list, workspace_relative_directory, acceptance_criteria. Defaults must satisfy their type. |
| `policy` | Manual checkpoints; external actions empty; failure behavior stop-and-report; source writes permitted only for the delivery route and only inside bound Build paths. Policy is descriptive, never an approval record. |
| `tasks` | Stable ID, existing phase, registered capability, optional registered lens, bounded instructions/purpose, dependencies, typed read bindings, named outputs, criteria binding and verification expectations. Execution mode follows capability constraints. |
| `acceptance_scenarios` | Nonempty observable success/failure statements for reuse and validation. These are expectations, not test results. |
| `runtime_requirements` | Known required contract IDs. Unknown contracts block compilation. |

Input values are supplied separately. Acceptance criteria have unique IDs and nonempty statements. An output file may be missing; a read file must exist or have an explicit preceding producer. Directories are output prefixes only, never wildcard write grants.

Bindings use typed objects: `{"input":"source_files"}` or `{"artifact":{"task":"security","name":"findings"}}`. Literal relative output names combine with one named directory input. For delivery, `{"input":"build_files"}` expands an exact output-file list. No expressions, environment expansion, shell interpolation, network imports or code evaluation. Reject conflicting output owners, traversal, protected metadata, symlinks and absolute output paths using the existing path validator.

Task IDs are unique across the definition. Dependencies must exist, be acyclic and point to the same or an earlier phase. Artifact references require a dependency on their producer. Earlier-phase dependencies resolve through accepted phase packets; same-phase dependencies require joined and accepted results. All bound criteria must have coverage. A phase task file may retain earlier task rows needed for DAG references; only current-phase rows are dispatchable.

The previous draft's `preview_binding` and `status` fields are not executable schema fields. Convert the seed examples explicitly; do not silently accept the illustrative document as v1.

## Capability catalog

`blueprint_catalog.py` is an explicit registry over installed role/lens files. It does not register capabilities from user prose or arbitrary Markdown paths.

Start with Product analysis, Design analysis, Plan decomposition, Build execution, Evaluate verification, Engineering lens review and root phase synthesis. Map them to the actual installed product/designer/planner/executor/evaluator/lens/orchestrator assets. The change-review seed uses only security and code-quality lenses. Additional catalogued lenses may be selected when present and compatible; no downloaded roles.

Each entry contains ID/version, legal phases, input/output types, effect class, required execution mode, role/lens paths and their hashes. Effect classes are local-read, artifact-write and scoped-source-write. There are no external-effect capabilities. Root synthesis is limited to integration/checkpoint preparation and must include an explicit reason/reference in generated tasks; it cannot substitute for a required native review.

Compatibility records include resolved runtime root, interpreter identity, supported contract IDs, the actual loaded workflow/evidence/worker/context modules, compiler modules, role/lens assets and transitive prompt assets declared by the catalog. The existing native runtime identity omits some verification-contract modules, so supplement it with their actual file hashes. A version label or successful import is insufficient. Use known contract shape checks and contract tests; do not dynamically load a second runtime into the process.

Both profiles require native-default/v1, bounded/v2 and current worker/evidence support. Delivery additionally requires typed Plan verification and verification-history support. Missing support produces a named blocker. There is no fallback to untyped Plan or root-only lens coverage.

## Binding, compiler and package

Compile in two layers:

1. `blueprint.py`: strict parsing, canonical serialization, authoring/version rules and static errors.
2. `blueprint_compile.py`: resolve workspace inputs and catalog identity, construct exact scope/task patterns, call existing scope/DAG/path validators and emit preview/provenance.

Resolve Git refs to full commit IDs without fetching or modifying the repository. For the change-review seed, head must match the selected checkout. Selected working-file bytes must match the bound source inventory; relevant modifications cause rebind or an explicit blocker. Deleted files are represented by their immutable base-commit content, not treated as missing evidence. Read coverage includes the declared dependencies and tests, with graph suggestions and stated exclusions; changed filenames alone never prove complete review.

Canonicalize objects with sorted keys and stable UTF-8 encoding; preserve semantically ordered arrays. Sort set-valued paths and capability manifests. The compilation digest covers definition bytes, resolved inputs/criteria, source manifest, compiler and capability identity, and output namespace. Timestamps, run IDs, visits, grants and observed capacity are absent from deterministic content.

The root chooses a fresh invocation namespace in the binding inputs. Compile writes a no-clobber package under `.taskplane/bootstrap/workflow-<invocation>/`:

- pinned `definition.json`, `bindings.json`, `capabilities.json`;
- `compilation.json` with digests, compatibility result, task mapping and phase patterns;
- `scope.json` with all seven phase keys, exact current/future output paths, verification inputs and native-default/v1;
- first-phase `tasks.json`, plus the readable preview.

Avoid circular hashes: compilation identity covers the semantic scope before adding workflow_binding, plus pinned members and task patterns. scope.json adds that identity afterward; admission reconstructs and compares the semantic scope. The compilation record never hashes its own digest field or a scope envelope containing it.

Only complete bindings can produce runnable packages. The directory is a locator, not a blanket grant. Its finite output list is validated before any write. Write a final compilation manifest after all members succeed; interrupted partial output is never runnable. An identical complete package is reusable as data; each execution still needs a new invocation/output namespace and fresh harness identity.

Phase outputs live under that invocation's declared output prefix. Allocate separate files for each phase's packet, task publication, reports and evidence so future refinement never edits a sealed predecessor. Runtime-generated run/visit IDs and receipts are inserted only after the harness supplies them; packet skeletons are not valid submitted evidence.

## Harness integration and authority

Add optional `scope.workflow_binding` with a versioned schema, package path, package digest, definition ID/version/digest and compiler/capability contract digests. Scope digests already include this data. The new adapter must interpret and validate it; storing an extra field alone provides no protection.

Use `LocalWorkflow.verify_start`, state validation and action checks to verify the immutable package and loaded identity. Check dispatch preparation, claim/continuation and evidence acceptance paths as well as ordinary root commands. Wire the native hook path in `flow.py` where needed, including direct existing flow commands, so bypassing the authoring CLI does not skip validation. Read-only report, diagnosis and explicit retirement remain available on compatibility failure; invalid provenance must not make the run impossible to inspect or retire.

`context_handoff.py` supplies the pinned definition/compilation and task-specific bindings as required bounded context. Do this explicitly: generic context discovery skips many files under `.taskplane/`. Do not transport request approvals or predecessor conversations as executable authority. `flow_dashboard.py` displays workflow name/version/digest, current run, compatibility and unresolved inputs from this same binding, with escaped text.

`workflow_local.py` recognizes `tp-workflow` as standalone Design authoring. Add narrow parsing for the new CLI commands: verified launcher/workspace, exact arguments and paths, no catch-all permission. Compile is admitted only for its declared bootstrap package or current scoped artifacts; save requires the exact authoring path. Active phase scope and sealed checkpoints still apply. Diagnostics/preview cannot execute writes through optional flags.

Use the existing root/worker protocol unchanged: publish typed tasks, consume context, observe native capacity and first-worker readiness, prepare fresh grants, claim in real child sessions, join results, inspect outputs, then accept-result. Security and code-quality use distinct actual reviewer identities. Root synthesis starts after both accepted results. No synthetic worker identity or template-supplied approval.

Before the first native task in a checkout, verify the selected workspace and run across root controls, native hooks and children. Worktree creation alone does not establish that propagation. Missing root readiness, child claim, complete context or matching automatic hook observations stops dependent execution. The current Design performs no native dispatch and does not certify this checkout's future worker startup.

These checks are cooperative Taskplane controls and observed host provenance, not an operating-system containment claim.

## Full delivery and refinement

A full-delivery invocation requires an exact outer Build allowlist before start. When paths are unknown, authoring may produce an incomplete preview, but binding must inspect/refine or request the material missing scope. It cannot start with an empty Build scope and assume Plan may widen it.

The initial package freezes reusable phase patterns and the outer scope, not invented implementation tasks. Product and Design publish their actual phase tasks. Plan materializes the complete Build DAG, ownership, integration order, criterion coverage, output packet/report/history paths and typed checks. Check contracts specify explicit argument vectors, source/test inputs, evidence outputs, environment, kind and required status.

The final Plan packet must pass the loaded harness preflight and actual human checkpoint. Build tasks must match that accepted DAG; only progress observations may change. Checks must own their logs and depend on the producers they inspect. Reserve exact distinct retry-log paths in Plan; do not overwrite a prior verification attempt. Exhausted reservations require the existing scope/replanning procedure. Later source/result changes use existing drift and repair semantics. No universal scaffold can truthfully predetermine project-specific commands.

Mutable Build outputs are not pinned forever to their pre-Build bytes. Definition/compilation/capabilities remain immutable; phase/task freshness uses the current admitted source and accepted evidence. Rescan/reseal after authorized edits. A global frozen HEAD rule would incorrectly block normal delivery.

## Storage and recovery

Project drafts may be edited in place before publication. Save creates `workflows/<id>.<version>.workflow.json`; existing published bytes are never overwritten by the tool. Shipped `workflows/<id>.workflow.json` files are seed inputs. Changing published content requires a new version. Every run uses its pinned copy, so editing another draft/version cannot retarget it.

The existing harness is the only run-state authority. Do not create a second run index, scheduling loop or approval cache. Find and continue the current run via flow report/context; repeated compile or save does not resume execution.

| Condition | Required behavior |
|---|---|
| Invalid capability, unresolved path or cyclic dependency | Locate the error; no package admission or dispatch. |
| Source changes after binding | Revalidate affected inputs/results using existing drift controls; keep old evidence. No automatic rerun. |
| Loaded runtime or pinned role changes | Block further dependent execution; preserve diagnostic access and existing run identity. Restore compatibility or explicitly recover under supported lifecycle controls. |
| Draft/new version changes while run is active | Active pinned version remains unchanged; report newer version separately. |
| Pinned package changes or disappears | Integrity blocker; never silently recompile it into the active run. |
| Worker/capacity unavailable | Keep native-required task blocked/unknown; retry only with observed cause and a fresh grant. |
| Interrupted command or partial compile | Inspect actual recorded run/process/package state before retry. Never infer execution success from missing output. |
| Output path already used by a prior invocation | Refuse overwrite; bind a fresh namespace. |
| Requested scope grows | Present an explicit scope/lifecycle change; Plan cannot widen its parent scope. |

Business workflows can later extend typed effects and artifact bindings, but connector credentials, account binding, external action receipts and idempotency/reconciliation require a separate design. No placeholder connector is executable in v0.1.

## Files, dependencies and packaging

| Change | Responsibility |
|---|---|
| `taskplane/blueprint.py` | Parse, validate, canonicalize, save versions; CLI family parser may live here to keep the initial module count small. |
| `taskplane/blueprint_catalog.py` | Known capabilities and loaded contract/asset identity. |
| `taskplane/blueprint_compile.py` | Binding, deterministic package, preview and provenance checks. |
| `taskplane/tp.py` | Route the new CLI family. |
| `taskplane/flow.py`, `workflow_local.py` | Native start/control/hook integration and authoring entry. |
| `taskplane/context_handoff.py`, `flow_dashboard.py` | Same-run bounded provenance and user visibility. |
| New agent/skill; router/help skills | Authoring procedure, run handoff and discoverability. |
| Three `workflows/*.workflow.json` seeds | Change risk review first; Design brief; feature delivery. |
| Focused tests, docs and `scripts/package_plugin.py` | Contract/live evidence separation; package assets and examples. |

Existing packaging includes flat taskplane Python modules and agents/skills, but only selected docs and no workflows directory. Explicitly add the workflow docs and seed definitions with the existing regular-file/symlink safeguards. Verify both host archives contain the actual runtime, roles, referenced lens assets and examples.

Source graph integration remains centered on workflow, context_handoff and core. New blueprint modules must be scanned/decomposed and imports checked during Build. This Design does not assert the earlier depth-bounded impact scan is exhaustive. Source-baseline alignment is resolved. The exact outer scope already includes the proposed harness test and verification logs; Plan can refine their ownership and checks but cannot add paths.

Alternatives considered: a second workflow engine would duplicate current authority/recovery; executing prose-only recipes would lack a typed reusable contract; duplicating strict Plan validation in the compiler would drift from the harness. Use a compiler plus a compatible existing harness.

## Acceptance test map and delivery order

| Criterion | Required checks |
|---|---|
| WFB-01 | Create produces only definition/preview; versioned save/edit; reuse with two bindings; create-and-run follows the authorized handoff without creating a nested run. |
| WFB-02 | Positive seeds; duplicate keys/IDs, unknown schema/field/capability, bad type, unresolved/missing producer, cycles, forward-phase refs and uncovered criteria all refuse before dispatch. |
| WFB-03 | Canonical equivalence; refs resolved to immutable commits; changed/deleted paths and exact read coverage; traversal/symlink/collision cases; preview observes no worker, external action or flow start. |
| WFB-04 | Real security and code-quality child sessions with separate identities; claim/context/readiness/join/root acceptance; synthesis reports evidence and unknowns; dashboard/packet same run; compatible full-delivery task/Plan/gate behavior. |
| WFB-05 | Tampered package and role hashes reject; old approval/receipt/grant rejected across runs; published version cannot overwrite; active pinned version unaffected by a new draft. |
| WFB-06 | Interrupt before/after recorded start and worker completion; resume same run; stale result/changed input/runtime refuses; expected Build edits remain valid under accepted Plan; capacity failure cannot become serial coverage. |
| WFB-07 | Focused parser/compiler/CLI/hook/packaging checks; installed-runtime contract verification; honest live demonstration ledger; no fixture claim substitutes for host execution. |

Delivery order: finalize the actual Plan within the resolved compatible baseline and outer scope; implement contract/catalog; complete change-review compile-to-harness slice; integrate authoring/reuse/resume; complete standalone Product/Design and full-delivery patterns; verify packages/docs and criterion coverage.

Unit and fixture tests establish parser, compiler and adapter behavior. Live host verification establishes worker identity, tool observations and user checkpoint presentation. This Design updates the historical technical design through root scope/source inspection. The independent native compatibility analysis belongs to the historical run; no fresh native execution or feature testing is claimed here. No workflow-builder execution is claimed yet.

