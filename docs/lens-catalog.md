# Lens catalog — the full set

22 lenses, grouped by the team perspective they represent. The design rule: **every lens has a distinct charter and an explicit "does NOT own" boundary, so they compose** — a `.tsx` change fires *design* (UX), *frontend* (implementation) and *accessibility* (a11y) without three of them reporting the same thing. Machine definitions live in `lenses/catalog.json`; each lens also has a `lenses/<id>.md` stub for its evaluator prompt.

> This file is GENERATED from `lenses/catalog.json` by `scripts/gen_lens_catalog.py`. Edit the catalog (or the generator's prose), then regenerate — don't hand-edit.

## The set, by group

| Group | Lens | Charter (what it uniquely owns) |
| --- | --- | --- |
| **Product & delivery** | product | user value, requirements, scope fidelity, journey completeness |
|  | project-management | scope, sequencing, dependencies, risk, rollout readiness |
| **Engineering craft** | code-quality | cross-cutting craft: clarity, correctness, maintainability *(baseline on any code)* |
|  | frontend | FE implementation: components, state, render, bundle, compat |
|  | backend | service logic, data access, boundaries, transactions |
|  | mobile | native/mobile: platform, offline, lifecycle, store |
| **Architecture & systems** | architecture | component boundaries, data flow, contracts, scaling & failure modes *(always-on — light pass on any change, full pass when structural)* |
| **Quality & verification** | security | confidentiality, integrity, authz, safe inputs, supply chain *(baseline on any code)* |
|  | testability | CAN this be tested — seams, determinism, isolation *(baseline on any code)* |
|  | qa | IS the change tested well and safe to ship |
| **Data** | data-safety | changing stored data without corrupting it |
|  | dba | schema design, indexing, query efficiency, data modeling |
| **Operations** | scalability | will it hold under load and data growth |
|  | devops | build and ship: CI/CD, IaC, deploy config |
|  | sre | will we know when it breaks, and recover |
|  | cost-finops · *opt* | resource & cloud cost efficiency |
| **Interfaces** | integrability | contracts BETWEEN systems: shapes, versioning, errors |
| **Experience** | design | interaction, visual consistency, all UI states |
|  | accessibility | usable by everyone — WCAG, keyboard, screen readers |
|  | i18n · *opt* | works across languages and locales |
| **Docs** | tech-writer | docs, references, changelogs that stay true to the code |
| **Compliance** | privacy-compliance | handle user & regulated data lawfully |

*opt* = suggested/optional (off unless its files appear).

## Always-on floor: architecture & system design

**Architecture is routed on every code change** — a light pass on any diff, a full pass when the change is structurally significant. That floor is enforced by the engine (`tp lens route --all`), not by memory: component boundaries, data flow, contracts, and failure modes get a look even when no architecture files changed.

## Strategy: the north-star review

Strategy is deliberately NOT a lens tier. The *should-we-build-this* question is answered on demand by the **north-star review** (`/tp-northstar`) — a summoned, advisory pass that measures a target against the project's `Direction / north star` (alignment + Leverage, Reversibility, Opportunity cost, Coherence). It never gates the loop and is not part of this per-change catalog.

## Routing notes

- **Baselines are intentionally only four** — `code-quality`, `security`, `testability`, and always-on `architecture` — so a typical change fires ~4–7 lenses, not all 22. Role lenses fire by context (files/task type).
- **Mode** (`inline` vs governed `subagent`) is per-lens, set by `deep_globs` or change size; a wide review fans them out as parallel `tp-lens` agents (`tp lens dispatch`).
- **`tp lens route`** shows exactly which fired and why; `--only`/`--skip` override; `--all` returns the full catalog (deep + sweep).

## Adding a lens

Append an entry to `lenses/catalog.json` (id, name, group, charter, boundary, globs, task_types, baseline?, deep_globs), author its `lenses/<id>.md` evaluator prompt, then run `python3 scripts/gen_lens_catalog.py` to refresh this doc. The router picks the lens up automatically.
