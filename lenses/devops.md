# DevOps lens

**Group:** Operations
**Charter:** build and ship: CI/CD, IaC, deploy config
**Does NOT own:** run-time reliability → sre

## Looks for
pipeline correctness, build reproducibility, IaC hygiene, env parity, secrets in config

## Fires when
- files match: **/.github/**, **/Dockerfile, **/docker-compose*, **/*.tf, **/k8s/**, **/*.helm*, **/Jenkinsfile, **/.gitlab-ci*, **/Makefile, **/*.cicd.yml
- task types: infra, devops, deploy
- runs as **subagent** when: **/*.tf, **/k8s/**

## Deterministic checks (run before the LLM perspective)
- terraform validate
- hadolint
- actionlint

## Evaluator prompt

You are reviewing this change through the **DevOps** lens only. Your charter: build and ship: CI/CD, IaC, deploy config. Stay inside it — anything under “run-time reliability → sre” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Pipeline correctness: reproducible builds, honest cache keys, pinned action/tool versions.
2. IaC hygiene: least-privilege on new resources, no drift from applied state, plan output reviewed.
3. Environment parity: config via environment, not branched code.
4. Secrets: never in code, config files, or CI logs; injected at runtime.
5. Deploys reversible: a rollback path exists and is documented.

**Blocker** = a secret in config/CI; an irreversible deploy step.
**Major** = over-privileged IaC; unpinned build inputs.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "smallest fix that resolves it"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
