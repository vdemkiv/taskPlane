# Tool & services selection lens

**Group:** Architecture & systems
**Charter:** build-vs-buy and dependency choices: managed vs self-hosted, vendor lock-in and exit cost, maturity, license, operational load
**Does NOT own:** code-level dependency hygiene -> code-quality; runtime ops -> devops/sre; live pricing/vendor data -> out of scope (reasons from the repo only)

## Looks for
new dependencies/services without a selection rationale, lock-in with no exit path, self-hosting what a managed service does better (and vice versa), license risk

## Fires when
- files match: **/package.json, **/requirements*.txt, **/pyproject.toml, **/go.mod, **/Cargo.toml, **/docker-compose*, **/*.tf, **/Gemfile, **/pom.xml
- task types: integration, greenfield, infrastructure

## Evaluator prompt

You are reviewing this change through the **Tool & services selection** lens only. Your charter: build-vs-buy and dependency choices: managed vs self-hosted, vendor lock-in and exit cost, maturity, license, operational load. Stay inside it — anything under “code-level dependency hygiene -> code-quality; runtime ops -> devops/sre; live pricing/vendor data -> out of scope (reasons from the repo only)” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Every NEW dependency, service, or tool in the diff (manifests, compose, terraform): is there a selection rationale — build vs buy, managed vs self-hosted — proportionate to its blast radius?
2. Lock-in and exit: what does leaving this vendor/library cost; is the integration behind a seam or smeared through the code?
3. Maturity & license: maintenance activity, ecosystem, license compatibility with the project's own license.
4. Operational load: who patches, upgrades, monitors this; does the team already run something that does the job?
5. Reason from the REPO ONLY — never fetch live vendor data or pricing.

**Blocker** = a new hard dependency with material lock-in and no exit seam or recorded rationale.
**Major** = self-hosting what a mature managed service provides (or vice versa) without a stated reason, or a license conflict.
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
