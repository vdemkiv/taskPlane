# Workflow Builder

Save repeatable work as a typed versioned `taskplane.workflow-blueprint/v1`
definition, preview fresh bindings, then invoke through existing Taskplane flow
controls when authorized. Create/edit/save is distinct from run. A valid preview
or compiled package grants no execution, approval or native capacity.

## Routes and seeds

| Current seed | Version | Route |
| --- | --- | --- |
| `workflows/design-brief.workflow.json` |0.2.0| Standalone Design |
| `workflows/change-risk-review.workflow.json` |0.2.0| Standalone Engineering with explicit independent security/code-quality work |
| `workflows/feature-delivery.workflow.json` |0.2.0| Product → Design → Plan → Build → Evaluate → Engineering → Retro |

New compilation requires compact `brief/v1` and `ownership/v1`, plus actual typed
phase/task/verification contracts reported by the loaded catalog. Ordinary runtime
root/serial work is valid. These saved seeds deliberately declare native tasks;
those explicit obligations cannot silently become serial when capacity is missing.
No startup account, receipt, source probe or mandatory dashboard is implied.

`workflows/acceptance-change-review.0.1.0.workflow.json` is a published historical
old-runtime definition. Preserve its bytes and original evidence. It is not
silently converted, reapproved or certified by the candidate; reuse needs a
separately reviewed new version and fresh invocation.

## Inspect, author and invoke

Use the actual loaded runtime's `workflow --help` and `workflow catalog`.
Catalog/validate/preview/definition inspection are read-only and do not activate a
run. Preview permits missing bindings and reports unresolved values, exact
reads/writes, dependencies and runtime blockers, without observing host capacity.

Author in a scoped standalone Design visit, or a matching current Design visit
whose scope permits the definition. Resolve an occupied unrelated run explicitly;
never nest or silently replace it. Reuse accepted decisions. Present definition,
effects, diagnostics and a useful preview for separate Design approval.
Create-only ends there. Create-and-run permits the root handoff after authoring
acceptance/route completion, not unseen future approvals.

`workflow save` uses a project-local semantic versioned path. Identical canonical
bytes can be reused; changed content needs a new version without clobbering a
published definition or pinned active invocation. `workflow compile` writes a
fresh bootstrap package/output prefix, launches nothing and returns the actual
`start_arguments`. The root inspects scope/tasks/bindings and invokes existing
`flow start` with actual user-request provenance. There is no `workflow run` CLI.

Initial delivery has goal, stable criteria, material boundaries and current
outputs; Plan fixes concrete executable Build ownership/paths/checks before
implementation. A compiled finite `build_files` binding remains a human-authorized
outer boundary for that invocation, not a universal requirement to know every
future filename before Product. Full delivery keeps seven separate approvals.

## Definition fields

| Field | Contract |
| --- | --- |
| `schema`, `id`, `version`, `name`, `description` | Exact schema, stable lowercase ID, semantic version and useful text |
| `route` | Fixed `delivery`, or standalone Product/Design/Engineering |
| `trigger` | Manual invocation |
| `inputs` | Named typed declarations with `type`, `required` and optional typed `default` |
| `policy` | Manual checkpoint default, boolean source writes, no external actions, stop-and-report failure |
| `tasks` | Typed task patterns with dependencies/read bindings/outputs/criteria/verification |
| `acceptance_scenarios` | Descriptive scenarios, not claims of passing tests |
| `runtime_requirements` | Exact supported compact/type contracts from actual catalog |

Input types are text, git ref, workspace file list, workspace output file list,
workspace-relative directory and acceptance criteria. Criteria use stable IDs and
statements. Unknown fields/duplicate JSON keys refuse. Paths must be exact safe
workspace-relative literals; traversal, symlinks, globs, interpolation and output
collisions refuse. Definition prose is inert data, never executable authority.

Tasks identify `id`, `phase`, `capability`, `purpose`, `execution`, `depends_on`,
`read_bindings`, `outputs`, `criteria_binding` and `verification`. Catalog capability
contracts determine supported execution/roles. Root task metadata retains the
schema's reason/reference fields where required; these are not a runtime root
exception/fan-out quota. Native declarations remain explicit obligations.

Read bindings refer to typed inputs or a prerequisite's named artifact. Artifact
reads require a direct producer dependency. IDs/outputs are unique; prerequisites
must exist, be acyclic and never point to a later phase. Every criterion input
needs coverage. Artifact outputs have name/kind/directory binding/relative path;
Build source outputs bind workspace output file lists. No arbitrary executable,
connector or command field exists. Use the actual catalog for roles/lenses/kinds.

## Continuation and honest evidence

Use `workflow check --workspace PATH --run RUN` to inspect the actual pinned
package/current inputs. Resume that run instead of compiling/restarting another.
A changed saved draft cannot retarget it. Relevant input/package/definition changes
need diagnosis/revalidation; accepted Plan changes still need fresh checks.
Preserve failed/unknown attempts and original provenance.

Actual native identity, joined completion and relevant freshness satisfy declared
native tasks. One worker cannot impersonate two explicit independent reviewers.
Missing capacity remains an incomplete obligation. Root synthesis follows its
true prerequisites; independent worker startup does not impose a cohort gate.

Catalog/parser/compiler tests, extracted packages, actual platform CI and live
installed-host acceptance are distinct. Historical WFB-LIVE evidence remains
unverified until a separately authorized host actually loads the final candidate
and demonstrates its journey/restart behavior. Source/version labels and fixtures
cannot close it. Candidate installation/reload/publication are separate actions;
no cache overwrite, permission bypass or invented identity establishes success.

The frozen `scripts/verify_workflow_builder_live.py` inspector reads historical
v0.1 evidence, including old context receipts, dashboard observations and declared
lens obligations. Its report explicitly sets `historical_only: true` and
`current_runtime_compatible: false`, even when historical consistency passes.
It does not impose those retired obligations on current brief/v1 delivery.
Historical fixtures and immutable transcript/package checks remain useful for
reading old evidence; a passing inspector result cannot certify a current run.
