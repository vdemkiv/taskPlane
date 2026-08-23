# Lens catalog — the full set

26 lenses, grouped by the team perspective they represent. The design rule: **every lens has a distinct charter and an explicit "does NOT own" boundary, so they compose** — a `.tsx` change fires *design* (UX), *frontend* (implementation) and *accessibility* (a11y) without three of them reporting the same thing. Machine definitions live in `lenses/catalog.json`; each lens also has a `lenses/<id>.md` stub for its evaluator prompt.

> This file is GENERATED from `lenses/catalog.json` by `scripts/gen_lens_catalog.py`. Edit the catalog (or the generator's prose), then regenerate — don't hand-edit. CI regenerates and diffs this file (and the other generated lens artifacts) on every push, so a stale copy fails the build.

## The set, by group

| Group | Lens | Charter (what it uniquely owns) |
| --- | --- | --- |
| **Product & delivery** | product | user value, requirement quality and satisfaction, scope fidelity, journey completeness |
|  | project-management | scope, sequencing, dependencies, risk, rollout readiness — as properties of the PLAN |
|  | time-to-market | delivery speed as a first-class criterion: the fastest credible path, deferrals that are recorded AND priced, and reversible-now over perfect-later — so the cost of being wrong stays low |
| **Engineering craft** | code-quality | cross-cutting craft: clarity, correctness, maintainability *(baseline on any code)* |
|  | frontend | FE implementation: components, state, async correctness, render/load path (Core Web Vitals), bundle, compat |
|  | backend | service logic, data access, boundaries, transactions |
|  | mobile | native/mobile: platform contract, offline, lifecycle, store shippability |
| **Architecture & systems** | tradeoffs | every significant design choice names >=2 real alternatives with an explicit trade-off table: gained / given up / revisit-when; the chosen option is recorded as a proposed decision (D-record) in the registry |
|  | solution-design | soundness, proportionality and implementability of a PROPOSED design before any code exists — requirement/constraint → decision → modules/contracts → validation → failure/rollout traceability |
|  | services-selection | whether a chosen dependency, library, service or vendor earns its place at all — incumbent capability vs new dependency, build vs buy, managed vs self-hosted, maturity, licence, operational load, lock-in and exit cost |
|  | architecture | component boundaries, data flow, contracts, scaling & failure modes *(always-on — light pass on any change, full pass when structural)* |
| **Quality & verification** | security | confidentiality, integrity, authz, safe inputs, supply chain & build integrity *(baseline on any code)* |
|  | testability | CAN the production code be tested — seams, determinism, isolation, hermeticity *(baseline on any code)* |
|  | qa | IS the change tested well and safe to ship |
| **Data** | data-safety | changing stored data without corrupting it, and shipping that change without an outage |
|  | dba | schema design, indexing, query efficiency, data modeling |
| **Operations** | scalability | will it hold under load and data growth |
|  | devops | build and ship: pipeline correctness, build reproducibility, deploy and environment configuration |
|  | sre | will we know when it breaks, and will it survive and recover when a dependency does |
|  | cost-finops · *opt* | what this change costs to run, and whether that cost is bounded and attributable |
| **Interfaces** | integrability | contracts BETWEEN systems: shape, compatible evolution, versioning and retirement, error semantics |
| **Experience** | design | interaction, all UI states, visual consistency against the product's own design system |
|  | accessibility | usable by everyone — WCAG 2.2 Level AA, keyboard, screen readers |
|  | i18n · *opt* | works across languages, scripts and locales |
| **Docs** | tech-writer | developer- and operator-facing documentation that stays true to the code — references, guides, READMEs, changelogs, examples |
| **Compliance** | privacy-compliance | personal data — what is collected, where it flows, what deletes it, what the defaults are, and who owns the decision |

*opt* = suggested/optional (off unless its files appear).

## Always-on floor: architecture & system design

**Architecture is selected on every code change** as one of the 4–5 quick automatic lenses. Structural significance changes what evidence it considers, not its automatic depth. Exact deep architecture review requires a direct user request.

## Routing notes

- **Automatic execution is exactly 4–5 quick lenses** — always-on `architecture` plus the most relevant context lenses. The remaining catalog entries are evidenced `n/a`; full or exact deep review requires a direct user request.
- **Mode** (`inline` vs governed `subagent`) is an execution detail; the production kernel emits the selected quick lanes concurrently.
- **`tp lens route`** shows relevance and evidence. Normal production review applies the bounded 4–5 selector afterward. `--all` is a direct-user diagnostic surface, never an automatic fallback.

## Adding a lens

Append an entry to `lenses/catalog.json` (id, name, group, charter, boundary, globs, task_types, baseline?, deep_globs), author its `lenses/<id>.md` evaluator prompt, then run `python3 scripts/gen_lens_catalog.py` to refresh this doc. The router picks the lens up automatically.
