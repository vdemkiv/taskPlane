# Workflow Builder v0.1 — delivery scope

Status: Fresh Product checkpoint proposal on the approved compatible baseline. The product requirements remain unchanged; the baseline/scope binding below is new.

## Accepted direction and provenance

On 2026-10-01 the user said **“proceed”** after reviewing [Workflow Builder v0.1](../design/workflow-builder-v0.1.md) and its [illustrative definition](../design/workflow-builder-example.v0.1.json). Taskplane recorded that actual response against standalone Design checkpoint `690fba8b4af64d44a1d0f4db3ec0d0bb` in run `989586863dc645a398c80e3d99fac1c4` and finished that route. This delivery retains those artifacts and decisions as historical input. It has fresh run state and manual phase acceptance.

The settled product direction is both Taskplane workflows and later business automations, starting with Taskplane. Reuse the existing harness. Compose existing routes rather than introducing an arbitrary workflow engine.

## Problem and desired behavior

Today a user repeats the same orchestration instructions for similar work. Taskplane already executes scoped work, but it has no reusable, typed workflow-authoring layer. A user should describe a repeatable outcome, inspect the proposed work and effects, save the definition, and invoke it with fresh inputs.

The first complete example is **Change risk review**: security and code-quality workers inspect the bound change independently, then root synthesis produces one evidence-backed report. The report does not authorize source repair.

A project-local definition is the initial storage default. The authoring interaction is conversational; JSON is the canonical representation. A saved definition can be edited and versioned without changing running instances.

## Included in v0.1

1. A Workflow Builder specialist role and discoverable authoring entry point. It creates and edits definitions from user intent using supported capabilities.
2. A typed definition contract and an explicit capability catalog over existing Taskplane roles/lenses.
3. Validation, exact input binding, deterministic compilation and a readable preview.
4. Root-controlled instantiation and continuation through current scope, task, worker, context, evidence and checkpoint interfaces.
5. Versioned project definitions and run provenance that pin definition, compiler and loaded-runtime identity.
6. Seed definitions for change-risk review, a design brief and standard feature delivery. Deliver the change-review slice first, then complete the remaining supported routes.
7. Documentation, focused automated tests, packaging coverage and an honest live verification record.

All seven phase names remain the existing controller contract. Standalone Product, Design and Engineering and full delivery are supported route choices. Full-delivery templates cannot predetermine approval of Build paths: actual Build tasks, scope and typed verification are finalized through Plan.

## Acceptance criteria

| ID | Observable acceptance |
|---|---|
| WFB-01 — author and reuse | A user can create, inspect, edit and save a workflow through the new entry point. A second invocation reuses the saved definition with different inputs. “Create” alone performs no run; “create and run” may perform both within the actual request. |
| WFB-02 — validate definitions | The parser accepts the documented versioned schema and supported routes/capabilities. Unknown executable capabilities/fields, missing required parameters, duplicate task IDs, cyclic or forward-phase dependencies, and uncovered criteria return actionable errors before dispatch. |
| WFB-03 — bind and preview | Same canonical definition, resolved inputs, compiler and capability identity produce equivalent compiled scope/task packages. Paths expand to exact allowed files; refs resolve to immutable inputs. Preview exposes reads, writes, dependencies, evidence and decisions, with no worker or external-action execution. |
| WFB-04 — execute through Taskplane | Change-risk review runs through the existing native protocol with two distinct lens workers and root synthesis after joined, accepted results. The existing dashboard and phase packet reflect the same run. It edits only declared review outputs and preserves unknown evidence. Other supported route templates preserve their normal phase gates. |
| WFB-05 — preserve authority and versions | Saving, validation and template import grant no execution or checkpoint authority. New runs have fresh identity, grants and decisions. Editing a published definition creates a new version; it cannot mutate an active instance or copy approvals. |
| WFB-06 — recover and reject stale evidence | Interrupted execution resumes the same run. Changed source, inputs, definition/runtime compatibility or required capability causes revalidation or an explicit blocker. Missing native capacity never silently becomes serial coverage. Retry records preserve the observed cause and prior evidence. |
| WFB-07 — ship an inspectable feature | CLI/agent documentation and packaged examples match actual supported behavior. Focused contract, compiler, CLI and packaging tests pass. Live behavior and fixture coverage are reported separately; unsupported host capability is a named release gap, not a passing test. |

## Non-goals

Arbitrary phase names/order, branching/loops, nested workflows, scheduled/event triggers, business connector execution, remote writes, stored credentials, a marketplace, a visual canvas, plugin installation, release publishing and deployment are excluded.

Business automation remains the intended next extension. Preserve typed capability/effect and artifact interfaces, but do not implement dummy connectors or imply present support.

## Dependencies and constraints

- Reuse the accepted design and independent feasibility/experience analyses. No repeat research is needed for the established approach.
- Reuse the current phase state machine, exact-scope/DAG validators, native worker protocol, bounded context, evidence freshness and dashboard.
- Bind compatibility to the loaded runtime and actual contracts. The compatible checkout matches the installed top-level runtime modules; preserve explicit loaded-runtime and typed Plan/Build contract checks during implementation.
- No new definition may weaken normal host permissions or Taskplane's manual checkpoints.
- Source/dependency/test read coverage must be explicit at binding. A list of changed files alone is insufficient evidence for a complete review.
- Native worker identity, claim, context and join checks remain real runtime observations. Pure preview tests cannot establish live execution.
- The repository contains unrelated untracked documents. Preserve them. Only the declared implementation scope may change during Build.

The historical source graph had 67 components; the fresh baseline scan has 41 modules and 69 components. The main integration seams are `taskplane::workflow`, `taskplane::context_handoff` and `taskplane::core`. The earlier impact analysis was depth-bounded and is not a complete implementation change list. Plan must finalize the exact write scope after technical Design.

## Delivery decomposition

| Work | Prerequisite | Result |
|---|---|---|
| Contract and capability manifest | Accepted scope and design | Versioned schema, supported route/capability rules, negative cases |
| Binder/compiler/preview | Contract | Deterministic exact packages and clear diagnostics |
| Harness adapter and authoring role | Compiler | Root-owned invocation, native execution, versioned reuse and existing dashboard |
| Example workflows | Contract; adapter for live execution | Change review first, then design brief and full delivery |
| Verification and packaging | Implemented slices | Relevant checks, resume/drift evidence, package contents and user documentation |

This outline is not the accepted Build task DAG. Plan will assign exact paths, task owners, prerequisites, criterion mappings and typed check commands. Independent implementation work can use scoped native workers once its prerequisites and permissions exist.

## Decisions already resolved

Conversational authoring first; JSON first; project-local definitions initially; fixed Taskplane routes; manual invocation; separate authoring and run authority; no external actions in this release. Personal workflow storage and the first business connector use case remain later product decisions.

## Product verification

The scope and criteria were checked against the accepted design and the example. All seven criteria have observable success/failure behavior and map to the delivery outline. This Product pass changes no production implementation and runs no feature tests.

## Compatible-baseline delivery binding

The user approved the concrete baseline/scope transition after reviewing the implementation Plan proposal. This Product checkpoint belongs to fresh run `ea76260680b84274939afe6403c5e917` in the managed workflow-builder checkout. Previous delivery `2a4c63fc42144a4884189cfbffa8d93d` was explicitly retired without accepting its unsubmitted Plan. Its evidence and earlier decisions remain in the original checkout as history.

The new checkout is pinned to `848a225ee7be822c692c3b9b62bdc4cdd82baea9`. All 37 installed top-level Python runtime modules were compared again and match exactly. Runtime compatibility no longer requires a backport into the old branch. This parity check does not establish feature behavior or complete package compatibility.

The initial scope now reserves 37 exact Build paths and 210 verification inputs, including the additional harness test, eight distinct verification logs and six immutable baseline read snapshots. All seven criteria, supported routes, project-local storage, manual invocation and non-goals remain unchanged. Plan must stay inside this outer scope. The retained technical design and concrete Plan draft remain useful inputs; they are not new-run phase approvals.

Plugin installation and external actions remain excluded. Actual candidate-loaded host execution is still required for the live behavior criterion and must be reported separately from fixtures. This checkpoint seeks acceptance of the unchanged product outcome under the approved compatible baseline and exact outer scope; it does not accept Design, Plan or Build.
