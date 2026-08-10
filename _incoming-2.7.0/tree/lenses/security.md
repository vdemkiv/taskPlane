# Security lens

**Group:** Quality & verification
**Charter:** confidentiality, integrity, authz, safe inputs, supply chain & build integrity
**Does NOT own:** reliability/uptime → sre; general error-handling quality → code-quality; infra posture beyond what the diff touches → devops

## Looks for
secrets (exposure = compromise, rotate not delete), authz gaps incl. object-level/IDOR, injection, SSRF, unsafe input, security misconfiguration, supply-chain & build integrity (deps, lockfiles, CI workflows, install scripts, pinning), fail-open error paths, AI/agent surface risk

## Fires when
- files match: **/auth/**, **/api/**, **/secrets/**, **/*.sql, **/*.env*, **/.github/workflows/**, **/.github/actions/**, **/Dockerfile*, **/docker-compose*.y*ml, **/*.tf, **/*.tfvars, **/*.lock, **/package.json, **/package-lock.json, **/pnpm-lock.yaml, **/yarn.lock, **/go.mod, **/go.sum, **/requirements*.txt, **/pyproject.toml, **/.npmrc, **/.pre-commit-config.yaml, **/*mcp*.json, **/k8s/**, **/helm/**, **/nginx*.conf
- task types: auth, api, integration, backend, data, migration, deploy, devops, infra
- baseline: yes (any code change)
- runs as **subagent** when: **/auth/**, **/*.sql, **/*.env*

## Deterministic checks (run before the LLM perspective)
- gitleaks
- semgrep --config auto
- dependency audit
- zizmor (GitHub Actions workflows, when `.github/**` is in the diff)

## Evaluator prompt

You are reviewing this change through the **Security** lens only. Your charter: confidentiality, integrity, authz, safe inputs, supply chain & build integrity. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

Examine, with file:line evidence:

1. **Secrets and key placement.** Any credential, key, or token present in the diff — including
   test fixtures, config, and committed `.env` files. A secret that reached a commit is
   **compromised**: deleting it from the working tree does not remove it from history or from any
   clone, so the remedy is revoke-and-rotate at the provider first, then purge history. Also check
   placement: a server-only key must never reach client-executed code or a client bundle
   (`NEXT_PUBLIC_*`/`VITE_*` prefixes, service-role and payment secret keys).
2. **Injection at every input boundary, and SSRF.** SQL, shell/command, template, and path
   injection; plus any outbound URL, host, port, or file path derived from user input, which must
   be validated against an allowlist rather than merely parsed. [A05:2025 Injection; SSRF sits
   inside A01:2025 in the current edition]
3. **Authorization — function-level AND object-level.** For every new or changed endpoint, route,
   handler, or query: may this role perform this action at all, and does the caller own or have
   access to *this specific record id* (IDOR)? Name the id. Enforcement must be server-side;
   hidden UI, client-side guards, and "the query filters by user in practice" do not count.
   Default-deny, not default-allow. [A01:2025 Broken Access Control, still #1]
4. **Unsafe input handling.** Deserialization of untrusted data (`pickle`, unsafe YAML, native
   deserializers), `eval`/`exec` on data, unvalidated redirects, file uploads without type and
   size limits, and raw HTML injection of user data without sanitization.
5. **Security misconfiguration in anything this diff touches.** CSP, HSTS, cookie flags
   (Secure/HttpOnly/SameSite), CORS origin lists (especially `*` alongside credentials), debug or
   verbose mode reachable in production, default or sample credentials left active, permissive
   bucket/object-storage or IAM policy. Scope yourself to configuration the diff actually changes
   — auditing the estate's overall posture is devops. [A02:2025, which rose from #5 to #2]
6. **Supply chain and build integrity, not just CVEs.** The dependency audit already reports known
   advisories; your judgement is the integrity of the build path. Check: lockfile committed and
   consistent with the manifest; CI installing from the lockfile (`npm ci`, `--frozen-lockfile`,
   `pip install -r` with hashes) rather than resolving fresh; third-party CI actions pinned to a
   full commit SHA rather than a moving tag or branch; packages that execute install-time
   lifecycle scripts inside a job that holds publish or deploy credentials; new dependencies that
   are typosquat-shaped, unmaintained, or unpinned. Then the workflow triggers themselves: a
   `pull_request_target` or `workflow_run` job that checks out the untrusted fork's head ref runs
   attacker-authored code with the base repository's secrets and a write-scoped token; and any
   `${{ github.event.* }}` value — PR title, branch name, issue body — interpolated directly into a
   `run:` block is shell injection, not templating (bind it to an `env:` variable and reference it
   as `"$VAR"`). [A03:2025 Software Supply Chain Failures — a new category this edition,
   superseding "Vulnerable and Outdated Components"]
7. **Error paths that fail insecurely.** Does an authentication or authorization decision return
   *allow* when its check errors, times out, or the identity provider is unreachable? Does a
   multi-step mutation leave partial privileged state with no rollback? Is a lock, connection, or
   file handle leaked on an exception path reachable by an unauthenticated caller? Is a stack
   trace, SQL error, or internal path returned to the client as reconnaissance? Only the security
   consequence is yours — general error-handling quality is code-quality's and availability is
   sre's; hand those over in one line. [A10:2025 Mishandling of Exceptional Conditions — new this
   edition; CWE-636 fail-open, CWE-209 error disclosure, CWE-703/755]
8. **Sensitive data exposure and crypto misuse.** Secrets or PII flowing into logs, error
   messages, analytics, or client responses; missing encryption in transit or at rest where the
   requirement's `security` NFR promises it; and home-rolled crypto, fast hashes (MD5/SHA-1/plain
   SHA-256) used for passwords instead of a memory-hard KDF, static or reused IVs, and predictable
   randomness for security tokens. [A04:2025 Cryptographic Failures; A09:2025 Security Logging
   and Alerting Failures]
9. **AI and agent surface — only if the diff adds or edits a model call, prompt assembly, tool or
   function definition, agent loop, or MCP server config.** If it does not, say so in one line and
   skip. Otherwise: untrusted input must sit in a delimited data slot and never be concatenated
   into instruction context; model output is untrusted and must never be executed, interpolated
   into SQL/shell/HTML, or trusted as an authorization decision; tool and MCP-server provenance
   pinned; agent credentials scoped to the task rather than broadly inherited; destructive or
   irreversible tool actions re-checked server-side and human-confirmed; content written to
   memory or a vector store from untrusted sources that will resurface in a later session or
   another tenant; nothing in assembled context (system prompt, tool schemas, retrieved policy
   text) whose leak would break security. [OWASP GenAI LLM Top 10 **2026** — LLM01 Prompt
   Injection, LLM02 Sensitive Information Disclosure, LLM03 Excessive Agency, LLM04 Supply Chain,
   LLM08 Hidden Context Exposure; OWASP Agentic Applications Top 10 **2026** — ASI01 Agent Goal
   Hijack, ASI02 Tool Misuse and Exploitation, ASI03 Identity and Privilege Abuse, ASI04 Agentic
   Supply Chain Vulnerabilities, ASI05 Unexpected Code Execution, ASI06 Memory & Context Poisoning]

## Deep methodology (subagent mode / high-stakes surfaces)

Follow `lenses/references/security-methodology.md` — the full procedure:
scanner gate first (gitleaks, ecosystem CVE audit, semgrep/bandit/gosec),
then OWASP Web Top 10 (2021) passes incl. access control & RLS, injection,
auth/session, data protection — and the OWASP LLM Top 10 (2025) passes when
the change touches an AI surface (prompt-injection input guard included).
Grade findings by its severity table; a scanner that cannot run is itself a
finding.

**Blocker** = an exploitable path to data or code execution; a committed secret (it is compromised the moment it lands in a commit — the remedy is revocation and rotation at the provider, not deletion from the tree); an authorization decision that fails open on error or timeout; an unpinned third-party action or install-time script executing in a pipeline that holds publish or deploy credentials.
**Major** = a missing authz check with partial mitigation; a risky unpinned dep; a security-relevant configuration regression (CSP/HSTS/cookie flags/CORS/debug); a lockfile bypassed in CI; an error path that leaks internals to the client.
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
