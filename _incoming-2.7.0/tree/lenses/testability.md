# Testability lens

**Group:** Quality & verification
**Charter:** CAN the production code be tested — seams, determinism, isolation, hermeticity
**Does NOT own:** IS it tested well — test strategy, coverage adequacy, flaky/dishonest test code → qa; CI runners, test containers, pipeline config → devops; production retries/timeouts/observability → sre

## Looks for
seams and substitutability (clock, network, filesystem, DB, model/LLM client), hidden globals and shared state OUTSIDE the process, non-determinism, parallel-safety, reachability of new branches from a public surface, a pure side-effect-free core that invariants could be stated against

## Fires when
- task types: api, backend, integration, distributed
- baseline: yes (any code change)

## Deterministic checks (run before the LLM perspective)
- coverage — use ONLY as evidence for check 4: which new branches NO test can reach at all. Do not report a coverage percentage or judge coverage adequacy; that is qa's. [Inozemtseva & Holmes, ICSE 2014: coverage correlates only weakly-to-moderately with suite effectiveness once suite size is controlled — a number here would be an unsupported quality claim]

## Evaluator prompt

You are reviewing this change through the **Testability** lens only. Your charter: CAN the production code be tested — seams, determinism, isolation, hermeticity. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

**Boundary with qa, stated because both lenses see the same symptom (flakiness).** You judge the **production code**: does its structure make a reliable, isolated, repeatable test *possible*? qa judges the **tests**: are the ones written good enough. A sleep in a test file, a missing edge case, a weak assertion → qa. Production code that leaves a caller no way to await completion, no way to seed randomness, no way to substitute a collaborator → yours. Never file the same flake twice. If the diff contains no production code (test files only), abstain and say so.

Examine, with file:line evidence:

1. **Seams, construction and hermeticity.** Can collaborators — DB, network, clock, filesystem, environment, and **model/LLM or other inference clients** — be substituted at a boundary the caller controls, without monkey-patching internals? Can the unit be constructed in a test without booting the whole app? And the sharper question: **could this run with the network disabled?** A client (HTTP, SDK, or model) constructed inline inside business logic, reading its endpoint or key from module-level config, has no seam. Prefer a real implementation, else a fake, else a stub/mock. [Winters et al., *Software Engineering at Google* ch.13: real > fake > stub/mock; a hermetic instance is one whose lifecycle the test controls]
2. **Hidden coupling — in-process AND outside it.** In-process: globals, singletons, module-level state, caches and registries the test cannot reset. Outside the process, which is the bigger source: DB rows or tables written without a transaction or scoped teardown, files at fixed paths, shared temp directories, env vars mutated at import, external caches or queues. Ask whether running this code twice, or alongside another test, leaves state that changes the other's result. [Gruber et al., ICST 2021 — 22,352 PyPI projects, 876,186 tests: 59% of flaky tests were order-dependent polluter/victim/brittle. The proportion is Python-specific; the category is not]
3. **Determinism.** Real time (`now()`, `Date.now`, monotonic deadlines) with no injected clock; randomness that is not seedable **and whose seed is not surfaced on failure**; dependence on timezone, locale, or default encoding; reliance on map/set/dict iteration or filesystem listing order; UUIDs and DB auto-IDs baked into observable output; and **async completion signalling** — does the code expose a way to observe or await a finished state (a returned future/promise, a callback, a status the caller can poll), or must a caller sleep and guess? A code path that can only be waited on by sleeping is a testability defect in the production code, not in the test. [practitioner consensus; Gruber et al.: non-order-dependent flakes were dominated by network and randomness]
4. **Reachability.** Can a test drive every new branch from a public surface, or do some branches require reaching into private state? Use the coverage check strictly as evidence for unreachable branches — not as a score.
5. **Parallel-safety.** Can two instances of this run in the same process, and can a suite exercising it be sharded across workers? Flag hard-coded ports, fixed absolute or shared temp paths, module-level connection pools, process-global registries, and singletons keyed by nothing. [Gruber et al. 2021; Google Testing Blog 2016 — order-of-magnitude anchor, single company, not peer-reviewed: ~16% of tests showed some flakiness and the insertion rate roughly matched the fix rate, so preventing it at the seam is the only durable lever]
6. **A pure core to state invariants against.** Is there a side-effect-free core here — parser, serializer, validator, reducer, state machine, pricing or permission rule — that could be exercised directly on values? Or is the logic entangled with I/O so that only end-to-end examples are possible? Report the *entanglement* and name the extractable core; do not report a missing test — that is qa's. [Ravi & Coblenz, PACMPL/OOPSLA 2025, 426 Hypothesis-using projects, mutation testing on 40: per test, a property-based test killed ~50× the mutants of an average unit test; 55% of those kills came from a single generated input. Mutation score is an effectiveness proxy, and the corpus self-selected for PBT adoption]

**Blocker** = a new critical path that cannot be exercised without monkey-patching internals, or that unavoidably reaches the real network, real clock, or a live third-party/model endpoint.
**Major** = hard-wired clock, network, randomness, or model client in new logic; state — in-process or external — that a test cannot reset; a new parallel-unsafe fixed resource (port, absolute path, shared table) with no scoping; an async path with no completion signal.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`. And a criticism without a
remedy is pointless: `suggestion` is REQUIRED on every blocker/major/minor —
a concrete alternative or solution, preferring capabilities the as-built
stack already provides (see the current-state inventory when present). A
finding you cannot propose a remedy for is a `question`, not a verdict.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "REQUIRED: the remedy — smallest concrete fix
                              or alternative, incumbent-stack first"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
