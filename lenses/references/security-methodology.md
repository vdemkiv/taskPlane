
# Security lens — deep methodology

The security lens's deep procedure, applied to **implemented code**, not just the plan. The lens is a **baseline** — security debt accrues on plain feature work as readily as on auth work, so it routes on every change whose deliverable is code, and runs deep (subagent) when auth/sql/env surfaces are touched.

Requirement NFRs (`security:` axis on the R-record) define the intended posture at refinement time; this methodology proves it against the diff at review time.

## When This Evaluator Is Used

Applied at EVALUATE/EM whenever the lens router selects the security lens (any code change). Mandatory depth (subagent mode) before work touching auth, data, payments, external input, or production config passes its gate.

## Inputs Required

1. The requirement record (intended security posture — its `security` NFR + acceptance criteria)
2. Changed files / git diff
3. Dependency manifests + lockfiles (`package.json`/`package-lock.json`, `requirements.txt`/`pyproject.toml`, `go.mod`/`go.sum`)
4. Migration / policy files (SQL, Supabase migrations) where present
5. `.env.example` and config

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

## Pass 1: Dependency & Supply-Chain (OWASP A06)

| Check | Standard |
|---|---|
| Known CVEs | No HIGH/CRITICAL advisories in `audit`/`pip-audit`/`govulncheck` output |
| Lockfile present & committed | Reproducible installs; no floating ranges on security-sensitive deps |
| Unmaintained / typosquat risk | Flag deps with no releases in >2y or suspiciously-named packages |
| Install scripts | Note packages with postinstall hooks |

## Pass 2: Secrets & Configuration (OWASP A05)

| Check | Standard |
|---|---|
| No secrets in source | `gitleaks` clean; no keys/tokens/passwords in code, tests, or fixtures |
| No secrets in client bundle | Server-only keys never imported into client code (e.g. Supabase **service-role** key, Stripe secret key, Gemini key never in `NEXT_PUBLIC_*`/browser) |
| Env hygiene | `.env.example` complete; dev/staging/prod separated; missing vars fail loudly |
| Security headers / config | CSP, HSTS, secure cookies, no debug mode in prod |

## Pass 3: Authentication & Session (OWASP A07)

Auth flows correct (sign up/in/out, refresh, expiry → re-auth not crash); tokens stored and transmitted safely (httpOnly cookies or secure storage, never in URL/localStorage for sensitive tokens); session fixation and fixation-on-privilege-change handled; rate limiting / lockout on auth endpoints.

## Pass 4: Authorization & Access Control (OWASP A01 — highest priority)

Broken access control is OWASP 2021 #1 and the most common real defect. Check the **code**, not just the login:

| Check | Standard |
|---|---|
| Object-level authz (IDOR) | Every record fetch/mutation verifies the caller owns/may access *that* id — not just that they're logged in |
| Function-level authz | Privileged actions (admin, delete, billing) re-check role server-side |
| Default-deny | Routes/handlers deny unless explicitly allowed |
| Server-side enforcement | Authz never relies on hidden UI / client checks alone |

**Supabase RLS (this stack's authorization layer — review explicitly):**

| Check | Standard |
|---|---|
| RLS enabled | Every table holding user/tenant data has `ENABLE ROW LEVEL SECURITY` |
| Policies present & scoped | `SELECT/INSERT/UPDATE/DELETE` policies bind rows to `auth.uid()` / tenant, not `USING (true)` |
| Service-role isolation | `service_role` key used only in trusted server context; client uses `anon`/user JWT |
| No RLS bypass | No broad `security definer` functions or views that leak across tenants |

## Pass 5: Input Validation & Injection (OWASP A03)

| Check | Standard |
|---|---|
| SQL/NoSQL injection | Parameterized queries / query builder only; never string-concatenated or f-string SQL |
| Command injection | `exec`/`subprocess`/`exec.Command` take arg arrays, never shell-interpolated user input |
| XSS | No raw HTML injection of user data (`dangerouslySetInnerHTML`, `v-html`) without sanitization |
| SSRF (OWASP A10) | Outbound URLs from user input validated against an allowlist |
| Boundary validation | All external input parsed/validated (zod / pydantic / explicit checks) before use — delegate detail to the language skill |
| Deserialization (A08) | No `pickle`/unsafe YAML/native deserialization of untrusted data |

## Pass 6: Data Protection & Privacy

Sensitive data encrypted in transit (TLS) and at rest where required; PII not logged to console/logs; data retention/deletion paths exist where promised; backups not world-readable; payment data never stored raw (PCI — defer to processor).

## Pass 7: AI / LLM Surface — OWASP Top 10 for LLM Applications (2025)

The web Top 10 does not cover model-specific risk. This pass maps to the dedicated **OWASP Top 10 for LLM Applications, 2025 edition** (OWASP GenAI Security Project). Apply to every Gemini/model call path; mark a row N/A only when the task genuinely has no model surface for it.

| LLM risk (2025) | Check |
|---|---|
| **LLM01 Prompt Injection** | System instructions cannot be overridden by user or third-party content; both *direct* and *indirect* injection (via retrieved/fetched content) are considered; untrusted text is delimited and treated as data, not instructions |
| **LLM02 Sensitive Information Disclosure** | Secrets/PII are not placed into prompts or system context; the model cannot echo another user's/tenant's data; output is filtered for sensitive content before display |
| **LLM03 Supply Chain** | Model, SDK, and plugin provenance and versions are pinned and trusted (overlaps Pass 1) |
| **LLM04 Data & Model Poisoning** | If fine-tuning or user/RAG-contributed data feeds the model: sources are validated and bounded (often N/A for API-only consumers — state which) |
| **LLM05 Improper Output Handling** | Model output is treated as untrusted: never `eval`'d/executed, escaped before render, and validated before flowing into SQL/shell/HTML/downstream calls |
| **LLM06 Excessive Agency** | Model-invocable tools follow least privilege; destructive or irreversible actions require human confirmation; no unbounded autonomous action |
| **LLM07 System Prompt Leakage** | The system prompt holds no secrets, keys, or authorization logic that would break if leaked; security never depends on prompt secrecy |
| **LLM08 Vector & Embedding Weaknesses** | If RAG / Supabase `pgvector` is used: retrieval enforces per-user/tenant access control; embeddings cannot leak cross-tenant; the store cannot be poisoned by untrusted content (N/A if no RAG) |
| **LLM09 Misinformation** | High-stakes outputs (legal/medical/financial) are guarded or labeled; overreliance is mitigated; grounding/citations where correctness matters |
| **LLM10 Unbounded Consumption** | Rate limits, token/cost caps, and timeouts on generation endpoints; no user-controllable unbounded loop or cost amplification |

### Mandatory: input-boundary injection guard (detect → obstruct → flag)

**Requirement (blocking):** no data entered through any input field or API endpoint may act as an instruction that changes application behavior. Any untrusted input that can reach the model — directly, or later via storage, RAG, logs, or another user's session — MUST pass through a guard that detects injection payloads, **obstructs** them (block at high-risk sinks, neutralize at low-risk ones), and **flags** every detection. See `references/prompt-injection-defense.md` for the defense-in-depth pattern and a reference implementation.

Verify, for every model-feeding boundary:

| Control | Standard | Severity if absent |
|---|---|---|
| Structural separation | Untrusted input is never concatenated into system/instruction context; it sits in a delimited data slot and the model is told to treat it as data | **CRITICAL** |
| Guard coverage | Every input field / API endpoint whose data can reach the model runs the detect→obstruct→flag guard | **HIGH** |
| Indirect-injection paths | Stored / RAG-retrieved / imported / fetched content is guarded, not just live form fields | **HIGH** |
| Obstruction at high-risk sinks | Detected injection that drives a tool call, code exec, privileged/destructive action, or another user's context is **blocked**, not just logged | **CRITICAL** |
| Model is not the authz boundary | Privileged/destructive actions re-checked server-side + human-confirmed regardless of model output (LLM06) | **CRITICAL** |
| Flagging | Every detection is logged (redacted), alerted, and rate-tasked per actor (LLM01 + OWASP A09) | **MEDIUM** |

Detection is a backstop, not a guarantee — a task that relies on pattern-matching alone, without structural separation and least-privilege actions, fails this control even if a detector is present.

## OWASP Web Top 10 (2021) Coverage Map

| OWASP 2021 | Covered by |
|---|---|
| A01 Broken Access Control | Pass 4 |
| A02 Cryptographic Failures | Pass 6 |
| A03 Injection | Pass 5 |
| A04 Insecure Design | spec/plan review + CSO at EVALUATE_PLAN |
| A05 Security Misconfiguration | Pass 2 |
| A06 Vulnerable Components | Pass 1 |
| A07 Auth Failures | Pass 3 |
| A08 Integrity Failures | Pass 5 (deserialization) + Pass 1 |
| A09 Logging & Monitoring Failures | Pass 6 |
| A10 SSRF | Pass 5 |

## OWASP LLM Top 10 (2025) Coverage Map

For any task with an AI/model surface, this list governs (see Pass 7). Two editions intentionally apply: the web Top 10 for the application, the LLM Top 10 for the model surface.

| OWASP LLM 2025 | Covered by |
|---|---|
| LLM01 Prompt Injection | Pass 7 |
| LLM02 Sensitive Information Disclosure | Pass 7 (+ Pass 2/6) |
| LLM03 Supply Chain | Pass 7 + Pass 1 |
| LLM04 Data & Model Poisoning | Pass 7 |
| LLM05 Improper Output Handling | Pass 7 + Pass 5 |
| LLM06 Excessive Agency | Pass 7 + Pass 4 |
| LLM07 System Prompt Leakage | Pass 7 + Pass 2 |
| LLM08 Vector & Embedding Weaknesses | Pass 7 + Pass 4 |
| LLM09 Misinformation | Pass 7 |
| LLM10 Unbounded Consumption | Pass 7 |

> Edition note: lists current as of the 2021 web edition and the 2025 LLM edition. OWASP revises these periodically — confirm against owasp.org for any release after early 2026.

## Severity Model & Verdict

Each finding is graded; the grade drives the gate (this replaces keyword-based high-stakes detection):

| Severity | Examples | Gate action |
|---|---|---|
| **CRITICAL** | Secret leaked, auth bypass, IDOR on sensitive data, SQLi, RLS disabled on PII table | **FAIL** → block; CSO leads full Board (HIGH_IMPACT). Never auto-skip |
| **HIGH** | XSS, missing function-level authz, HIGH CVE, service-role key client-side | **FAIL** → block until fixed (HIGH_IMPACT) |
| **MEDIUM** | Missing rate limit, weak session expiry, MEDIUM CVE | **PASS WITH CONDITIONS** → logged + scheduled fix |
| **LOW** | Missing security header, verbose error | **PASS** → backlog note |

```markdown
## Security Evaluation Report

**Task**: [task-id]   **Evaluator**: eval-security   **Date**: [YYYY-MM-DD]

### Scanners
- gitleaks: [clean / N leaks] · deps: [N high, N crit] · SAST: [N findings]

### Findings (by severity)
| Severity | Type | Location | OWASP | Remediation |
|----------|------|----------|-------|-------------|
| CRITICAL | ...  | file:line| A01   | ...         |

### Results
| Pass | Status |
|------|--------|
| 1 Dependencies | PASS/FAIL |
| 2 Secrets/Config | PASS/FAIL |
| 3 Auth/Session | PASS/FAIL |
| 4 Access Control (+RLS) | PASS/FAIL |
| 5 Injection | PASS/FAIL |
| 6 Data Protection | PASS/FAIL |
| 7 AI/LLM Surface | PASS/FAIL/N/A |

### Verdict: PASS ✅ / PASS-WITH-CONDITIONS ⚠️ / FAIL ❌
- FAIL if any unresolved CRITICAL or HIGH.
- PASS-WITH-CONDITIONS if only MEDIUM (conditions logged to metadata.json).
- [If FAIL, list fix actions for loop-fixer; route CRITICAL/HIGH to Board per authority matrix]
```

## Handoff

- **PASS** → return to `loop-execution-evaluator`.
- **PASS-WITH-CONDITIONS** → return with conditions recorded; Conductor schedules follow-up; does not block.
- **FAIL (CRITICAL/HIGH)** → return to `loop-execution-evaluator` → Conductor dispatches `loop-fixer`; CRITICAL/HIGH are HIGH_IMPACT and convene the Board (CSO leading), never silently dropped.
