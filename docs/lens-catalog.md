# Lens catalog — the full set

26 lenses, grouped by the team perspective they represent. The design rule: **every lens has a distinct charter and an explicit "does NOT own" boundary, so they compose** — a `.tsx` change fires *design* (UX), *frontend* (implementation) and *accessibility* (a11y) without three of them reporting the same thing. Machine definitions live in `lenses/catalog.json`; each lens also has a `lenses/<id>.md` stub for its evaluator prompt.

> This file is GENERATED from `lenses/catalog.json` by `scripts/gen_lens_catalog.py`. Edit the catalog (or the generator's prose), then regenerate.

## The set, by group

| Group | Lens | Charter (what it uniquely owns) |
| --- | --- | --- |
| **Product & delivery** | product | user value, requirement quality and satisfaction, scope fidelity, journey completeness |
|  | project-management | scope, sequencing, dependencies, risk, rollout readiness — as properties of the PLAN |
|  | time-to-market | delivery speed as a first-class criterion: the fastest credible path, deferrals that are recorded AND priced, and reversible-now over perfect-later — so the cost of being wrong stays low |
| **Engineering craft** | code-quality | cross-cutting craft: clarity, correctness, maintainability *(signal baseline; not automatic dispatch)* |
|  | frontend | FE implementation: components, state, async correctness, render/load path (Core Web Vitals), bundle, compat |
|  | backend | service logic, data access, boundaries, transactions |
|  | mobile | native/mobile: platform contract, offline, lifecycle, store shippability |
| **Architecture & systems** | tradeoffs | every significant design choice names >=2 real alternatives with an explicit trade-off table: gained / given up / revisit-when; the chosen option is recorded as a proposed decision (D-record) in the registry |
|  | solution-design | soundness, proportionality and implementability of a PROPOSED design before any code exists — requirement/constraint → decision → modules/contracts → validation → failure/rollout traceability |
|  | services-selection | whether a chosen dependency, library, service or vendor earns its place at all — incumbent capability vs new dependency, build vs buy, managed vs self-hosted, maturity, licence, operational load, lock-in and exit cost |
|  | architecture | component boundaries, data flow, contracts, scaling & failure modes *(source signal; not automatic dispatch)* |
| **Quality & verification** | security | confidentiality, integrity, authz, safe inputs, supply chain & build integrity *(signal baseline; not automatic dispatch)* |
|  | testability | CAN the production code be tested — seams, determinism, isolation, hermeticity *(signal baseline; not automatic dispatch)* |
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

## Advisory selection

Source paths, content and dependency impact suggest relevant lenses. The orchestrator chooses useful reviews for the requested scope. There is no mandatory review count, fixed depth cap or dispatch authority in these suggestions.

## Using lenses in the shared flow

- Product, Design, Plan, Build, Evaluate, Engineering and Retro reuse the same run, graph, task decomposition and dashboard.
- Attach actual review evidence and native agent IDs to that run. Suggestions alone are not completed reviews.
- Delegate only when authorized and useful. Missing token counters remain visibly unavailable and never stop delivery.

## Adding a lens

Append an entry to `lenses/catalog.json` (id, name, group, charter, boundary, globs, task_types, baseline?, deep_globs), author its `lenses/<id>.md` evaluator prompt, then run `python3 scripts/gen_lens_catalog.py` to refresh this doc. The router picks the lens up automatically.
