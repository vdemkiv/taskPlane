
# Security lens — deep methodology

The security lens's deep procedure, applied to **implemented code**, not just the plan. The lens is a **baseline** — security debt accrues on plain feature work as readily as on auth work, so it routes on every change whose deliverable is code, and runs deep (subagent) when auth/sql/env surfaces are touched.

It additionally routes on an explicit set of non-source globs (CI workflows, Dockerfiles, lockfiles, manifests, Terraform, MCP config) because `code_extensions` contains no `.yml`/`.yaml`, `.json`, `.lock`, `.tf`, or extensionless `Dockerfile` — without those globs the lens that owns supply-chain risk cannot see supply-chain files.

Requirement NFRs (`security:` axis on the R-record) define the intended posture at refinement time; this methodology proves it against the diff at review time.

## When This Evaluator Is Used

Applied at EVALUATE/EM whenever the lens router selects the security lens (any code change). Mandatory depth (subagent mode) before work touching auth, data, payments, external input, or production config passes its gate.

## Inputs Required

1. The requirement record (intended security posture — its `security` NFR + acceptance criteria)
2. Changed files / git diff
3. Dependency manifests + lockfiles (`package.json`/`package-lock.json`, `requirements.txt`/`pyproject.toml`, `go.mod`/`go.sum`)
4. CI workflow and build config (`.github/workflows/**`, `Dockerfile`, `docker-compose*.yml`, `.npmrc`)
5. Migration / policy files (SQL, Supabase migrations) where present
6. `.env.example` and config

## Tooling Gate (automated scanners — run first)

Run the scanners for the languages present; missing scanner config is itself a finding. These feed the passes below with concrete evidence.

```bash
# Secrets — all repos
gitleaks detect --no-banner --redact            # any leak → CRITICAL

# Dependency / supply-chain CVEs — by ecosystem
npm audit --audit-level=high                     # JS/TS
pip-audit                                        # Python   (or: safety check)
govulncheck ./...                                # Go

# SAST — by ecosystem
npx semgrep --config p/owasp-top-ten --config p/typescript   # JS/TS (no native equivalent otherwise)
#   Python → bandit and Go → gosec are covered by the python/go code-quality references; consume their output here
```

Scanner output is evidence, not the review. Do not spend the deep budget re-deriving what a scanner already prints — spend it on the judgement rows below, which no scanner decides.

## Pass 1: Dependency, Build & Supply-Chain Integrity (OWASP A03:2025)

A03:2025 **Software Supply Chain Failures** is new in the current edition and supersedes the 2021 "Vulnerable and Outdated Components" category; its scope is the whole build path, not just the advisory list.

| Check | Standard |
|---|---|
| Known CVEs | No HIGH/CRITICAL advisories in `audit`/`pip-audit`/`govulncheck` output |
| Lockfile present & committed | Reproducible installs; no floating ranges on security-sensitive deps |
| Lockfile honoured in CI | CI installs with `npm ci` / `--frozen-lockfile` / hash-pinned `pip install`, not a fresh resolve that can drift from the reviewed lockfile |
| Manifest/lockfile consistency | Lockfile regenerated with the manifest change; no lockfile-only or manifest-only drift |
| Third-party CI actions pinned | `uses:` references a full commit SHA, not a moving tag or branch. A moving tag in a credentialed job is a takeover path |
| Install-time scripts | Packages executing `postinstall`/lifecycle hooks are noted; **blocking** when they run in a job holding publish or deploy credentials |
| Credential scope in pipelines | Publish/deploy tokens are least-privilege and not exposed to third-party steps; prefer OIDC trusted publishing over long-lived tokens |
| Artifact provenance | Where the ecosystem supports it, published artifacts carry provenance/attestation (npm trusted publishing; PyPI attestations via PEP 740) |
| Unmaintained / typosquat risk | Flag deps with no releases in >2y or suspiciously-named packages |
| SBOM | Note whether an SBOM is generated and retained. Do **not** gate on specific SBOM field lists — verify the current CISA minimum-elements edition before encoding any |

## Pass 2: Secrets & Configuration (OWASP A02:2025 Security Misconfiguration)

Security Misconfiguration rose from #5 (2021) to **#2** in the 2025 edition. Treat this pass as high priority, not hygiene.

| Check | Standard |
|---|---|
| No secrets in source | `gitleaks` clean; no keys/tokens/passwords in code, tests, or fixtures |
| Exposure = compromise | A secret that reached any commit is compromised. Remediation is **revoke and rotate at the provider**, then purge history — removing it from the working tree is not remediation, because history and every clone retain it |
| No secrets in client bundle | Server-only keys never imported into client code (e.g. Supabase **service-role** key, Stripe secret key, model API keys never in `NEXT_PUBLIC_*`/`VITE_*`/browser) |
| Env hygiene | `.env.example` complete; dev/staging/prod separated; missing vars fail loudly |
| Security headers | CSP, HSTS, `X-Content-Type-Options`; cookies `Secure`/`HttpOnly`/`SameSite` |
| CORS | Explicit origin allowlist; never `*` in combination with credentials |
| Debug & defaults | No debug/verbose/stack-trace mode reachable in prod; no default or sample credentials left active |
| Storage & IAM policy | Buckets and object stores not world-readable/writable; IAM grants least-privilege |

Scope to configuration the diff touches. Auditing the estate's general infrastructure posture is the devops lens's.

## Pass 3: Authentication & Session (OWASP A07:2025)

Auth flows correct (sign up/in/out, refresh, expiry → re-auth not crash); tokens stored and transmitted safely (httpOnly cookies or secure storage, never in URL/localStorage for sensitive tokens); session fixation and fixation-on-privilege-change handled; rate limiting / lockout on auth endpoints.

## Pass 4: Authorization & Access Control (OWASP A01:2025 — highest priority)

Broken access control remains #1 in the 2025 edition, and **SSRF is now consolidated into A01** (it was its own A10 in 2021). Check the **code**, not just the login:

| Check | Standard |
|---|---|
| Object-level authz (IDOR) | Every record fetch/mutation verifies the caller owns/may access *that* id — not just that they're logged in. Name the id in the finding |
| Function-level authz | Privileged actions (admin, delete, billing) re-check role server-side |
| Default-deny | Routes/handlers deny unless explicitly allowed |
| Server-side enforcement | Authz never relies on hidden UI / client checks alone |
| SSRF | Outbound URLs, hosts, ports and file paths derived from user input are validated against an allowlist, not merely parsed |

**Supabase RLS (this stack's authorization layer — review explicitly):**

| Check | Standard |
|---|---|
| RLS enabled | Every table holding user/tenant data has `ENABLE ROW LEVEL SECURITY` |
| Policies present & scoped | `SELECT/INSERT/UPDATE/DELETE` policies bind rows to `auth.uid()` / tenant, not `USING (true)` |
| Service-role isolation | `service_role` key used only in trusted server context; client uses `anon`/user JWT |
| No RLS bypass | No broad `security definer` functions or views that leak across tenants |

## Pass 5: Input Validation & Injection (OWASP A05:2025)

| Check | Standard |
|---|---|
| SQL/NoSQL injection | Parameterized queries / query builder only; never string-concatenated or f-string SQL |
| Command injection | `exec`/`subprocess`/`exec.Command` take arg arrays, never shell-interpolated user input |
| XSS | No raw HTML injection of user data (`dangerouslySetInnerHTML`, `v-html`) without sanitization |
| Boundary validation | All external input parsed/validated (zod / pydantic / explicit checks) before use — delegate detail to the language skill |
| Deserialization (A08:2025) | No `pickle`/unsafe YAML/native deserialization of untrusted data |

SSRF moved to Pass 4 with A01:2025.

## Pass 6: Data Protection, Privacy & Logging (OWASP A04:2025, A09:2025)

Sensitive data encrypted in transit (TLS) and at rest where required; no home-rolled crypto; passwords hashed with a memory-hard KDF (argon2id/scrypt/bcrypt), never a fast hash; no static or reused IVs; CSPRNG for security tokens. PII and secrets not logged to console/logs/analytics; security-relevant events (authn/authz failure, privilege change, injection-guard detections) are logged and alertable; data retention/deletion paths exist where promised; backups not world-readable; payment data never stored raw (PCI — defer to processor).

## Pass 7: AI / LLM & Agent Surface — OWASP GenAI Top 10s (2026 editions)

The web Top 10 does not cover model-specific risk. This pass maps to the **OWASP Top 10 for LLM Applications, 2026 edition** (published 3–4 Aug 2026) and, where the change introduces autonomy or tools, the **OWASP Top 10 for Agentic Applications, 2026 edition** (9 Dec 2025), both from the OWASP GenAI Security Project. Apply to every model call path; mark a row N/A only when the task genuinely has no surface for it.

| LLM risk (2026) | Check |
|---|---|
| **LLM01 Prompt Injection** | System instructions cannot be overridden by user or third-party content; both *direct* and *indirect* injection (via retrieved/fetched content) are considered, including cross-modal payloads (image/audio/document); untrusted text is delimited and treated as data, not instructions |
| **LLM02 Sensitive Information Disclosure** | Secrets/PII are not placed into prompts or system context; the model cannot echo another user's/tenant's data; output is filtered for sensitive content before display |
| **LLM03 Excessive Agency** | *(rose to #3 in 2026)* Model-invocable tools follow least privilege; destructive or irreversible actions require server-side re-check and human confirmation; no unbounded autonomous action |
| **LLM04 Supply Chain** | Model, SDK, plugin and model-artifact provenance and versions are pinned and trusted (overlaps Pass 1) |
| **LLM05 Data & Model Poisoning** | If fine-tuning or user/RAG-contributed data feeds the model: sources are validated and bounded; fine-tuning subversion considered (often N/A for API-only consumers — state which) |
| **LLM06 Unbounded Consumption** | Rate limits, token/cost caps, and timeouts on generation endpoints; no user-controllable unbounded loop or cost amplification |
| **LLM07 Misinformation** | High-stakes outputs (legal/medical/financial) are guarded or labeled; overreliance is mitigated; grounding/citations where correctness matters |
| **LLM08 Hidden Context Exposure** | *(renamed and broadened from 2025's "System Prompt Leakage")* Nothing assembled into the context window — system prompt, **tool schemas**, retrieved policy text, injected inventories — holds secrets, keys, or authorization logic that would break if leaked; security never depends on context secrecy |
| **LLM09 Vector & Embedding Weaknesses** | If RAG / Supabase `pgvector` is used: retrieval enforces per-user/tenant access control; embeddings cannot leak cross-tenant; the store cannot be poisoned by untrusted content (N/A if no RAG) |
| **LLM10 Improper Output Handling** | Model output is treated as untrusted: never `eval`'d/executed, escaped before render, and validated before flowing into SQL/shell/HTML/downstream calls |

**Agentic rows — apply when the change adds or edits an agent loop, tool/function definition, or MCP server config:**

| Agentic risk (2026) | Check |
|---|---|
| **ASI02 Tool Misuse and Exploitation** | Tool parameters are validated and bounded; a tool cannot be steered into an unintended target or scope |
| **ASI03 Identity and Privilege Abuse** | The agent's credentials are scoped to the task, not broadly inherited; no token pass-through that lets the agent act with a user's full authority |
| **ASI04 Agentic Supply Chain Vulnerabilities** | Tools, plugins and **MCP servers** are provenance-checked and version-pinned |
| **ASI05 Unexpected Code Execution** | Agent-generated code or shell commands run sandboxed, never in the review or deploy context |
| **ASI06 Memory & Context Poisoning** | Untrusted content written to memory or a vector store cannot resurface as instruction in a later session or another tenant |
| **ASI07 Insecure Inter-Agent Communication** | Messages between agents are authenticated and integrity-checked; an agent does not trust a peer message as privileged instruction |

### Mandatory: input-boundary injection guard (detect → obstruct → flag)

**Requirement (blocking):** no data entered through any input field or API endpoint may act as an instruction that changes application behavior. Any untrusted input that can reach the model — directly, or later via storage, RAG, logs, or another user's session — MUST pass through a guard that detects injection payloads, **obstructs** them (block at high-risk sinks, neutralize at low-risk ones), and **flags** every detection. See `references/prompt-injection-defense.md` for the defense-in-depth pattern and a reference implementation.

Verify, for every model-feeding boundary:

| Control | Standard | Severity if absent |
|---|---|---|
| Structural separation | Untrusted input is never concatenated into system/instruction context; it sits in a delimited data slot and the model is told to treat it as data | **CRITICAL** |
| Guard coverage | Every input field / API endpoint whose data can reach the model runs the detect→obstruct→flag guard | **HIGH** |
| Indirect-injection paths | Stored / RAG-retrieved / imported / fetched content is guarded, not just live form fields | **HIGH** |
| Obstruction at high-risk sinks | Detected injection that drives a tool call, code exec, privileged/destructive action, or another user's context is **blocked**, not just logged | **CRITICAL** |
| Model is not the authz boundary | Privileged/destructive actions re-checked server-side + human-confirmed regardless of model output (LLM03) | **CRITICAL** |
| Flagging | Every detection is logged (redacted), alerted, and rate-tasked per actor (LLM01 + A09:2025) | **MEDIUM** |

Detection is a backstop, not a guarantee — a task that relies on pattern-matching alone, without structural separation and least-privilege actions, fails this control even if a detector is present.

## Pass 8: Exceptional Conditions (OWASP A10:2025)

**Mishandling of Exceptional Conditions** is new in the 2025 edition. Review only the *security* consequence of the error path — general error-handling quality belongs to the code-quality lens and availability to sre; hand those over in one line.

| Check | Standard |
|---|---|
| Fail closed | An authn/authz check that errors, times out, or cannot reach its provider denies. Never `catch → return true` / `allow` (CWE-636) |
| No partial privileged state | Multi-step mutations that grant access, change roles, or move money are transactional or compensated; a failure midway does not leave a privilege granted |
| Resource release on error | Locks, connections, file handles and temp files released on the exception path; no unauthenticated-reachable exhaustion |
| No error disclosure | Stack traces, SQL errors, internal paths and version banners are not returned to the client (CWE-209); generic message out, detail to the log |
| Exceptions not swallowed | Security-relevant failures are not silently caught and ignored (CWE-703/755); they are logged and alertable (A09:2025) |

## OWASP Top 10:2025 Coverage Map

| OWASP 2025 | Covered by |
|---|---|
| A01 Broken Access Control (incl. SSRF) | Pass 4 |
| A02 Security Misconfiguration | Pass 2 |
| A03 Software Supply Chain Failures | Pass 1 |
| A04 Cryptographic Failures | Pass 6 |
| A05 Injection | Pass 5 |
| A06 Insecure Design | spec/plan review at design/EVALUATE_PLAN — not diff-visible here |
| A07 Authentication Failures | Pass 3 |
| A08 Software or Data Integrity Failures | Pass 5 (deserialization) + Pass 1 |
| A09 Security Logging and Alerting Failures | Pass 6 + Pass 8 |
| A10 Mishandling of Exceptional Conditions | Pass 8 |

## OWASP GenAI Coverage Map (2026 editions)

For any task with an AI/model surface, these lists govern (see Pass 7). Editions intentionally stack: the web Top 10 for the application, the LLM Top 10 for the model surface, the Agentic Top 10 for autonomy and tools.

| OWASP LLM 2026 | Covered by |
|---|---|
| LLM01 Prompt Injection | Pass 7 (+ injection guard) |
| LLM02 Sensitive Information Disclosure | Pass 7 (+ Pass 2/6) |
| LLM03 Excessive Agency | Pass 7 + Pass 4 |
| LLM04 Supply Chain | Pass 7 + Pass 1 |
| LLM05 Data & Model Poisoning | Pass 7 |
| LLM06 Unbounded Consumption | Pass 7 |
| LLM07 Misinformation | Pass 7 |
| LLM08 Hidden Context Exposure | Pass 7 + Pass 2 |
| LLM09 Vector & Embedding Weaknesses | Pass 7 + Pass 4 |
| LLM10 Improper Output Handling | Pass 7 + Pass 5 |

| OWASP Agentic 2026 | Covered by |
|---|---|
| ASI01 Agent Goal Hijack | Pass 7 (+ injection guard) |
| ASI02 Tool Misuse and Exploitation | Pass 7 |
| ASI03 Identity and Privilege Abuse | Pass 7 + Pass 4 |
| ASI04 Agentic Supply Chain Vulnerabilities | Pass 7 + Pass 1 |
| ASI05 Unexpected Code Execution | Pass 7 + Pass 5 |
| ASI06 Memory & Context Poisoning | Pass 7 |
| ASI07 Insecure Inter-Agent Communication | Pass 7 |
| ASI08 Cascading Failures | Partly Pass 8; availability aspects → sre |
| ASI09 Human-Agent Trust Exploitation | Design-level; not reliably diff-visible — note, do not gate |
| ASI10 Rogue Agents | Runtime/operational; outside a diff review — note, do not gate |

> **Edition note — verified against owasp.org and genai.owasp.org on 2026-08-10.**
> Current editions: **OWASP Top 10:2025** (the project page states the most current released
> version is the 2025 Top Ten; the 2021 edition previously cited here is superseded);
> **OWASP ASVS 5.0.0**, released 30 May 2025 — the *verification* standard, and the right
> instrument to cite when a finding needs a testable, numbered requirement (the Top 10 is
> awareness-only); **OWASP GenAI LLM Top 10 2026**, published 3–4 Aug 2026; **OWASP Top 10 for
> Agentic Applications 2026**, published 9 Dec 2025. Category names above were read from the OWASP
> pages and PDFs directly, not from vendor summaries. Re-verify each quarter; a superseded
> citation in this file is itself a finding.
>
> Deliberately **not** gated on: the OWASP Agentic Skills Top 10 (AST01–AST10) — an incubator
> draft, may be referenced but must not gate; SLSA levels — org/CI-level and largely not
> diff-visible, only the CI-config slice is reviewable (covered by Pass 1); specific CISA SBOM
> minimum-element field lists — the current edition was not readable, so Pass 1 asks only whether
> an SBOM exists.

## Severity Model & Verdict

Each finding is graded; the grade drives the gate (this replaces keyword-based high-stakes detection):

| Severity | Examples | Gate action |
|---|---|---|
| **CRITICAL** | Secret leaked (compromised — requires provider-side revocation/rotation, not deletion), auth bypass, authz decision that fails open, IDOR on sensitive data, SQLi, RLS disabled on PII table, unpinned third-party action or install script in a job holding publish/deploy credentials | **FAIL** → block. Never auto-skip |
| **HIGH** | XSS, missing function-level authz, SSRF, HIGH CVE, service-role key client-side, lockfile bypassed in CI | **FAIL** → block until fixed |
| **MEDIUM** | Missing rate limit, weak session expiry, MEDIUM CVE, CSP/HSTS/cookie-flag regression, internals leaked in an error response | **PASS WITH CONDITIONS** → logged + scheduled fix |
| **LOW** | Missing minor security header, verbose non-sensitive error | **PASS** → backlog note |

Evidence discipline: a claim you cannot anchor at file:line — for example "was this key rotated at the provider?", which a diff cannot show — is a `question`, not a blocker. The *presence* of the secret in the diff is the blocker; the rotation instruction is its `suggestion`.

```markdown
## Security Evaluation Report

**Task**: [task-id]   **Evaluator**: eval-security   **Date**: [YYYY-MM-DD]

### Scanners
- gitleaks: [clean / N leaks] · deps: [N high, N crit] · SAST: [N findings]

### Findings (by severity)
| Severity | Type | Location | OWASP | Remediation |
|----------|------|----------|-------|-------------|
| CRITICAL | ...  | file:line| A01:2025 | ...      |

### Results
| Pass | Status |
|------|--------|
| 1 Dependencies & Supply Chain | PASS/FAIL |
| 2 Secrets/Config | PASS/FAIL |
| 3 Auth/Session | PASS/FAIL |
| 4 Access Control (+RLS, SSRF) | PASS/FAIL |
| 5 Injection | PASS/FAIL |
| 6 Data Protection & Logging | PASS/FAIL |
| 7 AI/LLM & Agent Surface | PASS/FAIL/N/A |
| 8 Exceptional Conditions | PASS/FAIL |

### Verdict: PASS ✅ / PASS-WITH-CONDITIONS ⚠️ / FAIL ❌
- FAIL if any unresolved CRITICAL or HIGH.
- PASS-WITH-CONDITIONS if only MEDIUM (each condition recorded as a finding).
- [If FAIL, state the concrete failures as blocker findings so the evaluate
  gate fails and the loop's fix cycle (tp-fixer) receives them]
```

## Handoff (taskplane terms)

You return findings to the review that dispatched you — you never dispatch
anyone yourself.

- **PASS** → return the verdict and evidence to the dispatching review
  (tp-evaluator or the tp-engineering synthesis).
- **PASS-WITH-CONDITIONS** → return with every condition recorded as a
  MEDIUM/major finding, and recommend the durable ones be tracked as debt
  (`tp req debt`) so follow-up survives the session; does not block the gate.
- **FAIL (CRITICAL/HIGH)** → blocker findings. Per the authority matrix, a
  CRITICAL/HIGH security finding must not pass its gate: it fails EVALUATE
  (routing the loop's existing fix cycle to tp-fixer), and at engineering
  review an unresolved critical/high finding blocks sign-off mechanically.
  If fix cycles exhaust without clearing it, the loop escalates to the
  human (`escalated`). Never silently dropped. Strategy input is the
  summoned, advisory `tp-northstar` review only.
