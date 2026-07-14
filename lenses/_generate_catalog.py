import json

# group, id, name, charter, boundary(what it does NOT own), looks_for,
# globs, task_types, baseline('code' or None), deep_globs, checks
L = [
# ---- existing (kept; charters clarified) ----
("Product & delivery","product","Product",
 "user value, requirements, scope fidelity, journey completeness",
 "delivery timing/dependencies → project-management",
 "requirements met, scope creep/gaps, user-journey completeness, success metrics",
 ["**/specs/**","specs/**","**/*.spec.md","**/requirements/**","**/PRD*"],
 ["feature"],None,[],[]),
("Quality & verification","security","Security",
 "confidentiality, integrity, authz, safe inputs, supply chain",
 "reliability/uptime → sre",
 "secrets, authz gaps, injection, unsafe input, vulnerable deps",
 ["**/auth/**","**/*.sql","**/api/**","**/*.env*","**/secrets/**"],
 ["auth","api","integration"],"code",["**/auth/**","**/*.sql","**/*.env*"],
 ["gitleaks","semgrep --config auto","dependency audit"]),
("Engineering craft","code-quality","Code quality",
 "cross-cutting craft: clarity, correctness, maintainability",
 "surface specifics → frontend/backend/mobile; test adequacy → qa",
 "clarity, error handling, dead code, naming, duplication",
 [],[],"code",[],["lint","typecheck","jscpd (copy-paste/duplication)"]),
("Quality & verification","testability","Testability",
 "CAN this be tested — seams, determinism, isolation",
 "IS it tested well → qa",
 "coverage of new paths, seams/mockability, hidden globals, non-determinism",
 [],[],"code",[],["coverage"]),
("Experience","design","Design & UX",
 "interaction, visual consistency, all UI states",
 "a11y → accessibility; FE implementation → frontend",
 "UX flow, loading/empty/error states, visual consistency, hierarchy",
 ["**/*.tsx","**/*.jsx","**/*.vue","**/*.svelte","**/*.css","**/*.scss","**/components/**","**/ui/**"],
 ["ui","screens","design-system"],None,["**/*.tsx","**/*.jsx","**/*.vue"],[]),
("Operations","scalability","Scalability & performance",
 "will it hold under load and data growth",
 "runtime reliability/observability → sre",
 "N+1 / unbounded queries, blocking calls, resource ceilings, hot paths",
 ["**/api/**","**/db/**","**/*.sql","**/services/**","**/queries/**"],
 ["api","integration","backend"],None,["**/*.sql","**/db/**"],[]),
("Interfaces","integrability","Integrability",
 "contracts BETWEEN systems: shapes, versioning, errors",
 "internal service logic → backend",
 "API/data contracts, versioning, error codes, error recovery, schema hygiene",
 ["**/api/**","**/*.proto","**/schema/**","**/contracts/**","**/openapi*"],
 ["api","integration"],None,[],[]),
("Data","data-safety","Data & migration safety",
 "changing stored data without corrupting it",
 "schema DESIGN/perf → dba",
 "additive/rollback-safe migrations, nullable/defaulted columns, backfill, cascades",
 ["**/migrations/**","**/*.sql","**/schema/**"],["migration"],None,
 ["**/migrations/**"],[]),
# ---- your 9 role-lenses ----
("Docs","tech-writer","Technical writing",
 "docs, references, changelogs that stay true to the code",
 "in-product UI copy → design/content",
 "README/API-doc/changelog accuracy & completeness, ADR clarity, examples that run",
 ["**/*.md","**/*.mdx","**/docs/**","**/README*","**/CHANGELOG*","**/openapi*","**/*.rst"],
 ["docs"],None,["**/docs/**","**/openapi*"],[]),
("Quality & verification","qa","QA",
 "IS the change tested well and safe to ship",
 "CAN it be tested → testability",
 "test strategy, coverage adequacy, regression risk, edge/negative cases, E2E paths",
 ["**/tests/**","**/*.test.*","**/*.spec.*","**/e2e/**","**/cypress/**","**/playwright/**","**/__tests__/**"],
 ["feature","qa"],None,[],[]),
("Operations","devops","DevOps",
 "build and ship: CI/CD, IaC, deploy config",
 "run-time reliability → sre",
 "pipeline correctness, build reproducibility, IaC hygiene, env parity, secrets in config",
 ["**/.github/**","**/Dockerfile","**/docker-compose*","**/*.tf","**/k8s/**","**/*.helm*","**/Jenkinsfile","**/.gitlab-ci*","**/Makefile","**/*.cicd.yml"],
 ["infra","devops","deploy"],None,["**/*.tf","**/k8s/**"],
 ["terraform validate","hadolint","actionlint"]),
("Data","dba","DBA",
 "schema design, indexing, query efficiency, data modeling",
 "migration SAFETY → data-safety",
 "normalization, indexes, query plans, constraints/keys, data types, partitioning",
 ["**/*.sql","**/models/**","**/entities/**","**/*.prisma","**/schema/**","**/repositories/**"],
 ["migration","backend"],None,["**/schema/**","**/*.prisma"],[]),
("Operations","sre","SRE",
 "will we know when it breaks, and recover",
 "load/perf → scalability; build/deploy → devops",
 "observability (logs/metrics/traces/alerts), retries/timeouts/circuit-breakers, failure modes, runbooks, error budgets",
 ["**/monitoring/**","**/observability/**","**/alerts/**","**/*.slo*","**/runbooks/**","**/health*","**/*.pagerduty*"],
 ["backend","infra","reliability"],None,[],[]),
("Product & delivery","project-management","Project / delivery",
 "scope, sequencing, dependencies, risk, rollout readiness",
 "user value/requirements → product",
 "dependency order, cross-team impact, timeline/risk, rollout & rollback plan, delivery readiness",
 ["**/plan/**","plan/**","**/roadmap*","**/*.plan.md","**/milestones*"],
 [],None,[],[]),
("Engineering craft","frontend","Front-end engineering",
 "FE implementation: components, state, render, bundle, compat",
 "visual/UX → design; a11y → accessibility",
 "component architecture, state mgmt, render/bundle perf, browser/device compat, FE error/loading handling",
 ["**/*.tsx","**/*.jsx","**/*.vue","**/*.svelte","**/web/**","**/src/components/**","**/pages/**","**/*.stories.*"],
 ["ui","frontend"],None,["**/*.tsx","**/*.jsx"],[]),
("Engineering craft","backend","Back-end engineering",
 "service logic, data access, boundaries, transactions",
 "cross-system contracts → integrability; DB design → dba",
 "API design, business-logic correctness, data-access patterns, service boundaries, idempotency, transactions",
 ["**/api/**","**/services/**","**/handlers/**","**/controllers/**","**/routes/**","**/*.proto","**/usecases/**"],
 ["backend","api"],None,[],[]),
("Architecture & systems","architecture","System design & architecture",
 "component boundaries, data flow, contracts, scaling & failure modes",
 "in-file code craft → code-quality; infra provisioning → devops",
 "component/service decomposition, data flow & coupling, state & consistency, scaling & failure modes, tech-choice fit",
 ["**/architecture/**","**/adr/**","**/*.arch.md","**/docker-compose*","**/*.proto","**/k8s/**","**/design/**","**/*.tf"],
 ["greenfield","system-design","distributed","integration"],None,[],[]),
("Engineering craft","mobile","Mobile engineering",
 "native/mobile: platform, offline, lifecycle, store",
 "shared business logic → backend/frontend",
 "iOS/Android specifics, offline/sync, battery/network, app lifecycle, permissions, store guidelines, native perf",
 ["**/*.swift","**/*.kt","**/*.m","**/*.mm","**/ios/**","**/android/**","**/*.dart","**/*.xcodeproj/**","**/AndroidManifest.xml"],
 ["mobile"],None,["**/ios/**","**/android/**"],[]),
# NOTE: the former "Advisory (strategy)" tier (tech-strategy / cost-roi /
# business-alignment — the old "board") was REMOVED in v1.0. Strategy is no
# longer a code-review lens tier; it lives in the on-demand `north-star review`
# (skills/tp-northstar), a summoned strategic pass, never an auto stage. The
# code-review catalog is now engineering-only.
# ---- suggested additions (gaps) ----
("Experience","accessibility","Accessibility (a11y)",
 "usable by everyone — WCAG, keyboard, screen readers",
 "general visual design → design",
 "keyboard nav, ARIA/screen-reader, contrast, focus management, alt text, WCAG",
 ["**/*.tsx","**/*.jsx","**/*.vue","**/*.svelte","**/*.html","**/components/**","**/ui/**"],
 ["ui","screens"],None,[],["axe","a11y-lint"]),
("Compliance","privacy-compliance","Privacy & compliance",
 "handle user & regulated data lawfully",
 "technical attack surface → security",
 "PII handling, consent, data residency/retention, GDPR/CCPA, license/legal exposure",
 ["**/*consent*","**/privacy/**","**/*gdpr*","**/*.env*","**/analytics/**","**/tracking/**","**/pii/**"],
 ["data","auth"],None,[],[]),
("Operations","cost-finops","Cost / FinOps (optional)",
 "resource & cloud cost efficiency",
 "raw perf → scalability",
 "right-sizing, waste, egress, over-provisioning, autoscaling bounds",
 ["**/*.tf","**/k8s/**","**/serverless*","**/*.cloudformation*"],
 ["infra"],None,[],[]),
("Experience","i18n","Localization / i18n (optional)",
 "works across languages and locales",
 "content accuracy → tech-writer",
 "externalized strings, locale formatting, pluralization, RTL, timezone/currency",
 ["**/locales/**","**/i18n/**","**/*.po","**/*.pot","**/lang/**","**/translations/**"],
 ["ui"],None,[],[]),
]

cat = {
  "deep_threshold_files": 8,
  "code_extensions": [".py",".js",".ts",".tsx",".jsx",".vue",".svelte",".go",
                      ".rs",".java",".rb",".php",".c",".cpp",".cs",".sql",".sh",
                      ".kt",".swift",".m",".mm",".dart",".scala"],
  "lenses": []
}
for g,i,n,charter,boundary,lf,globs,tt,base,deep,checks in L:
    e = {"id":i,"name":n,"group":g,"charter":charter,"boundary":boundary,
         "looks_for":lf}
    if globs: e["globs"]=globs
    if tt: e["task_types"]=tt
    if base: e["baseline"]=base
    if deep: e["deep_globs"]=deep
    if checks: e["checks"]=checks
    cat["lenses"].append(e)

import os
_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
with open(_out, "w") as _f:
    json.dump(cat, _f, indent=2)
print(len(cat["lenses"]),"lenses across",len({e['group'] for e in cat['lenses']}),"groups")
from collections import defaultdict
g=defaultdict(list)
for e in cat["lenses"]: g[e["group"]].append(e["id"])
for k,v in g.items(): print(f"  {k}: {', '.join(v)}")
