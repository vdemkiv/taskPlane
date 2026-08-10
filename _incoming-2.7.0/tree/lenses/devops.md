# DevOps lens

**Group:** Operations
**Charter:** build and ship: pipeline correctness, build reproducibility, deploy and environment configuration
**Does NOT own:** run-time reliability, alerting, burn-rate/SLO config, runbooks → sre; resource sizing, allocation tags, waste and egress → cost-finops; capacity and autoscaling bounds under load → scalability; committed secrets, third-party action SHA pinning, lockfile-install integrity, untrusted-input handling in CI, IAM/network/storage policy permissiveness → security; migration content — expand/contract, backfill, lock budget, down-migration → data-safety; schema and index design → dba

## Looks for
pipeline correctness, build reproducibility, honest cache keys, CI secret flow and federated credentials, environment parity, rollout shape and rollback, release sequencing of schema-with-code, IaC state and version pinning

## Fires when
- files match: **/.github/**, **/Jenkinsfile, **/.gitlab-ci*, **/.circleci/**, **/azure-pipelines*, **/.buildkite/**, **/bitbucket-pipelines*, **/*.cicd.yml, **/Makefile, **/Dockerfile*, **/*.dockerfile, **/Containerfile, **/docker-compose*, **/.dockerignore, **/*.tf, **/*.tfvars, **/terragrunt*, **/*.bicep, **/cloudformation*, **/pulumi*, **/ansible/**, **/k8s/**, **/helm/**, **/charts/**, **/*.helm*, **/argocd/**, **/flux/**, **/skaffold*, **/serverless*, **/Procfile, **/.env.example
- task types: infra, infrastructure, devops, deploy
- runs as **subagent** when: **/*.tf

## Deterministic checks (run before the LLM perspective)
- terraform validate
- hadolint
- actionlint
- shellcheck (embedded shell in CI steps, entrypoints and Dockerfiles)
- kubeconform (k8s manifest schema)

## Evaluator prompt

You are reviewing this change through the **DevOps** lens only. Your charter: build and ship: pipeline correctness, build reproducibility, deploy and environment configuration. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

In
particular: a committed credential, an over-permissive IAM or bucket policy, and untrusted input
interpolated into a CI step are **security's**, not yours — hand each over in one line and do not
review it further. You own where a secret *travels* in the pipeline, not whether one is present.

Examine, with file:line evidence:

1. **Build reproducibility: the same inputs must produce the same artifact.** Base images
   referenced by digest rather than a floating tag (`latest`, `3`, `stable` all move under you);
   language runtime and build-tool versions pinned to an exact version and matching what runs in
   production; no build step that resolves "newest" at build time or fetches an unversioned URL.
   Then the failure mode that hides all of this — **cache keys that omit an input that changes the
   output.** A key hashed on the source tree but not the lockfile, or a restore-key prefix broad
   enough to match a differently-configured job, serves a stale layer and produces a green build
   that does not correspond to the diff. Name the input the key is missing.
   *[Third-party action pinning and lockfile-install integrity are security's — do not re-report.]*
2. **Where secrets travel in the pipeline.** Is a credential exposed to a step that does not need
   it, or to one that executes third-party or untrusted code? Does it reach a place that persists —
   echoed by `set -x` or a debug flag into a log, baked into an image layer via `ARG`/`ENV`, written
   into a build artifact, test report, or a `terraform` plan file that is uploaded? Is it scoped
   organisation- or repo-wide where the job needs one environment? And: is a **long-lived static
   cloud key stored as a CI secret where the platform supports short-lived federated credentials**
   (OIDC role assumption)? Where the platform does not support it — a self-hosted runner, an
   on-prem Jenkins — say so and check scoping and rotation instead; do not demand OIDC blindly.
3. **Environment parity.** Differences between environments belong in configuration, not in the
   code or the build: no `if env == "prod"` branching in application or pipeline logic, no
   environment-specific build variant that means staging tested a different artifact. Check
   completeness of the change across environments — a config key, chart value, or variable added
   for one environment must exist for all of them (and in the sample/`.env.example` if one is
   tracked), or the next deploy fails on the environment nobody edited. Best evidence of parity:
   the artifact promoted to production is byte-identical to the one that passed tests, not a
   rebuild from the same ref.
4. **Rollout shape and reversibility.** How does this change actually reach production — all
   instances at once, or canary, blue/green, rolling with a surge budget, or behind a flag? If it
   replaces every instance simultaneously, say so; simultaneous large-scale deployment is a known
   cascading-failure trigger (Google SRE ch. 22), and cold caches after a full restart are part of
   it. **Name the signal that would halt a bad rollout and point to where it is configured** — a
   readiness probe that actually tests dependency health rather than returning 200 unconditionally,
   `maxUnavailable`/`maxSurge`, a deployment gate, an alarm bound to the rollout. Then the reverse
   direction: does a rollback path exist, is it in the diff or documented, and does this change
   contain a step that **cannot be undone** — a resource the plan destroys and recreates, a one-way
   data or config move, a deletion that runs before its replacement is proven?
   *[Whether the halting signal is the right SLI, and alert routing → sre.]*
5. **Release sequencing when a schema change ships with code — pipeline configuration only.** If
   this deploy runs migrations: does the pipeline run them as an ordered, verifiable step that
   completes before the code depending on them serves traffic, and can the application be rolled
   back **without** rolling the schema back? A migration coupled into the same unorderable step as
   the app rollout means neither can be reverted alone, which is what turns a bad deploy into an
   outage. Abstain in one line if no migration runner, job, or chart hook appears in the diff.
   *[Whether the migration itself is expand/contract, backfilled, lock-safe and reversible →
   data-safety, which reviews the migration file directly. Review only the ordering the pipeline
   encodes.]*
6. **IaC state and change safety.** Provider and module versions pinned to an exact version — a
   range re-resolves later and makes the same configuration produce a different plan, which is the
   IaC form of check 1. Remote state configured with locking, not local or lock-free state that two
   concurrent applies can corrupt. And the plan's effect on what already exists: does this edit
   force **replacement** of a stateful resource (a database, a volume, a queue) rather than an
   in-place update? Abstain in one line if no IaC file is in the diff.
   *[Least privilege and policy permissiveness → security; sizing, tags and waste → cost-finops;
   autoscaling bounds under load → scalability.]*

**Blocker** = an irreversible deploy step with no rollback path; a production credential exposed to a pipeline step that executes third-party or untrusted code, or persisted into a log, image layer, or published artifact; a deploy that ships a schema change and the code requiring it as one unorderable step, so neither can be rolled back alone.
**Major** = build inputs unpinned to an immutable identifier, or a cache key that omits an input, so the artifact is not reproducible; environment divergence encoded in code or build rather than config, or a config key added for one environment and missing in another; a change that replaces all instances at once with no named halting signal; IaC pinned to a version range, state without locking, or a plan that silently replaces a stateful resource.
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
