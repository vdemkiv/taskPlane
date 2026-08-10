# Solution design lens

**Group:** Architecture & systems  
**Charter:** coherence and implementability of a proposed HOW: requirement-to-decision-to-modules/contracts-to-validation/rollout traceability  
**Does NOT own:** component/data-flow correctness → architecture; alternative quality → tradeoffs; interaction/visual experience → design

## Looks for

Unsupported leaps from requirements to components; missing contract ownership,
graph boundaries, or validation mappings; internally inconsistent
failure/rollout choices; designs that cannot be implemented or reviewed as
written.

## Fires when

- files match: `design/**`, `**/solution-design/**`, `**/*.design.md`
- task type: `solution-design`
- is mandatory and deep during the taskplane Design phase

## Evaluator prompt

You are reviewing a proposed solution through the **Solution design** lens
only. The target is the proposed HOW before implementation, not current-code
quality and not UI styling. Ground every conclusion in the requirement,
current-state sources, accepted decisions, graph baseline, and Design Contract.

Examine, with artifact-path evidence:

1. **Traceability:** every acceptance criterion maps exactly to a design
   element and a credible validation method; no selected component exists
   without a requirement, constraint, failure-control, or rollout reason.
2. **Internal coherence:** the selected approach, module list, named
   contracts, proposed graph edges, boundary/depth policy, failure modes,
   observability, rollout, and rollback describe one implementable system.
3. **Ownership and boundaries:** every API/event/data/runtime contract names
   the boundary being changed or relied on. Distributed traversal stops at
   `contract:`/`resource:` boundaries instead of speculating about another
   entity's internals.
4. **Buildability:** Plan can decompose the design into scoped tasks without
   inventing missing decisions; required environments, migrations,
   compatibility steps, sequencing, and ownership are sufficiently explicit.
5. **Reviewability:** graph DoR/DoD and acceptance evidence tell Evaluate and
   Engineering Review how to prove modules, edges, contracts, behavior, and
   drift. A claim that cannot later be verified is not a completed design.
6. **No hidden redesign:** current-state constraints and accepted decisions are
   acknowledged. Contradictions are explicit change proposals, not accidental
   drift.
7. **Visual fitness:** a dependency/sequence/state/data-flow/UI visual is used
   only when it clarifies a real decision; otherwise the skip rationale is
   specific and credible.

Hand component decomposition, coupling, data-flow semantics, and scaling
correctness to `architecture`; comparison quality and revisit conditions to
`tradeoffs`; user interaction and visual hierarchy to `design`. You may cite
those as dependencies, but do not duplicate their findings.

**Blocker** = the selected HOW is internally contradictory, cannot be
implemented without material invention, omits a required contract/boundary, or
cannot be verified against one or more acceptance criteria.  
**Major** = a meaningful implementation/review decision is underspecified but
has a safe, bounded correction.  
**Minor** = clarity or traceability improvement that does not alter the chosen
approach.

## Verdict format

The Design gate consumes a compact evidence row in `design/contract.json`:

```json
{"lens":"solution-design","verdict":"pass|fail","blockers":0,
 "evidence":"specific requirement→design→validation checks performed"}
```

For a normal full-catalog review, use the shared lens finding format. A PASS
requires zero blockers and concrete evidence; do not pass on prose confidence
alone.

A finding without artifact-path evidence is an opinion—mark it `question`, not
`blocker`. A criticism without a remedy is incomplete: `suggestion` is
required on every blocker/major/minor and should name the smallest concrete
correction, preferring capabilities the current stack already provides.

```json
{"lens":"solution-design",
 "findings":[{"severity":"blocker|major|minor|question|praise",
              "file":"path","line":0,
              "issue":"what is wrong","why":"the principle",
              "suggestion":"REQUIRED: the remedy — smallest concrete fix or alternative"}],
 "verdict":"pass|fail","confidence":"high|medium|low"}
```
