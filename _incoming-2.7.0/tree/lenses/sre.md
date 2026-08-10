# SRE lens

**Group:** Operations
**Charter:** will we know when it breaks, and will it survive and recover when a dependency does
**Does NOT own:** load, capacity and throughput → scalability; CI/CD, IaC and deploy config → devops; general idempotency, transactions and service logic → backend; what telemetry costs to store → cost-finops

## Looks for
observability of the new path (logs with context, metrics, traces), trace-context propagation and trace/span-id correlation on logs, metric label cardinality, timeouts and deadline propagation, bounded retries with randomized backoff and jitter, retry budgets and single-layer retry, idempotency of anything retried, circuit-breaking and failfast, graceful degradation, liveness-vs-readiness probes, burn-rate alerting when alert/SLO config is in the diff, newly introduced recurring manual operational steps (toil), runbook/rollback notes for new failure modes

## Fires when
- files match: **/services/**, **/monitoring/**, **/observability/**, **/alerts/**, **/*.alerts.y*ml, **/*rules*.y*ml, **/prometheus*/**, **/grafana/**, **/*.slo*, **/runbooks/**, **/health*, **/liveness*, **/readiness*, **/*.pagerduty*, **/clients/**, **/adapters/**, **/instrumentation/**, **/tracing/**, **/otel*/**, **/opentelemetry*/**
- task types: backend, infra, reliability, integration, distributed
- runs as **subagent** when: **/alerts/**, **/*.slo*, **/*rules*.y*ml

## Evaluator prompt

You are reviewing this change through the **SRE** lens only. Your charter: will we know when it breaks, and will it survive and recover when a dependency does. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

Examine, with file:line evidence:

1. **Will we know it broke.** On the new or changed path: does a failure produce a log carrying the identifiers needed to locate it (request/tenant/operation), a metric that moves, or a span that records the error — or does it fail silently, get swallowed by a bare `except`/`catch`, or return a success-shaped default? Where the change crosses a process boundary, is trace context propagated (W3C Trace Context `traceparent`) and are trace/span ids correlated onto the log records, so the two sides can be joined? Where the stack already emits OpenTelemetry, follow its existing attribute names — prefer the stable convention areas (HTTP, and the traces/metrics/logs signals themselves, semconv 1.44.0); do **not** raise findings against convention areas still in development (profiles, GenAI). Also flag any metric label whose value is unbounded (user id, request id, raw URL path, trace id) — that breaks the metrics backend. What that storage *costs* is cost-finops; name it in one line and move on.
2. **Timeouts and deadlines.** Every new outbound call — HTTP, RPC, database, queue, lock, external SDK — has an explicit timeout, and the timeout is set at the layer that actually enforces it (a client-level default that the call path overrides, or an SDK default the code silently inherits, is a finding). Is the *value* defensible against the dependency's observed latency, or is it a round number pulled from air? Does it cover connection setup — DNS, TLS handshake, connect — and not only the socket read? If this call sits inside a request that already has a deadline, is the deadline propagated inward with the child budget strictly smaller than the parent's, so an inner call cannot outlive the caller that is waiting on it?
3. **Retry safety.** For each retry added: is it bounded, with **randomized** exponential backoff (jitter — fixed backoff synchronizes callers into a thundering herd)? Is it the **only** retry layer in this call chain, or does an enclosing client, gateway, sidecar or job runner already retry — three layers of 3× retries is 27 attempts from one user action, and the amplification lands on the sickest dependency. Is there a retry budget or token bucket capping the retry-to-request ratio, rather than unbounded per-call counts? Are only *retriable* errors retried (timeouts, 429/503, connection resets) and permanent ones — 400, 401, 404, validation, deserialization — failed fast? And is the retried operation safe to run twice: idempotent, or guarded by an idempotency key or dedup, given a timeout may fire on a request the server actually processed? (Idempotency as a general design property of the service is backend's; here the question is narrow — is *this retry* safe.)
4. **What happens when the dependency is down.** Trace the failure outward: does a downstream failure fail fast (circuit breaker, bulkhead, concurrency cap, cached/stale response, feature disabled) or does it queue callers until the caller's own resources are exhausted and the failure spreads? What does the user actually see — a degraded but working page, or a hard error? If the new dependency is genuinely optional, is it non-blocking in code, not merely intended to be? Where health probes are in the diff: liveness and readiness must be distinct, and the liveness probe must not call a downstream service — a dependency blip that restarts every pod is a self-inflicted outage. Probe *tuning* values and CPU-limit policy are practitioner-contested — raise those as `question` or Minor, never Blocker.
5. **Alerting — only when an alert, rule or SLO file is in the diff.** Otherwise skip this check; a team's SLOs and error-budget policy are organizational properties a diff cannot evidence, and demanding them is out of scope. When such a file *is* present: does the alert fire on a symptom a user feels (error ratio, latency, availability) rather than a cause (CPU, pod restarts, queue depth) on a user-facing path? If it is SLO-based, is it burn-rate driven with **paired long and short windows**, so it both suppresses noise and resets within minutes of recovery, and split by severity — fast burn pages, slow burn tickets? (The Google SRE Workbook's 14.4/1h, 6/6h, 1/3d multipliers are starting values for a 99.9% objective and must be recomputed for a different target; do not assert them as required numbers.) A new alert with no stated recipient or no runbook link is a finding.
6. **Recovery and toil.** For each new failure mode the change introduces: is there a rollback path or runbook note, and is it specific enough to act on at 3am? And does the change introduce a **new recurring manual operational step** — a script someone must run weekly, a manual key rotation, cache purge, backfill re-run or hand-edited allowlist? Manual, repetitive, automatable, and growing with usage is toil at the one moment it is cheap to design away; name it and propose the automated alternative. Toil *percentage* is a staffing metric, not a diff property — do not estimate it.

**Blocker** = a new external call on a critical path with no timeout; or a retry loop with neither jitter nor a budget on a path that fans in from many callers; or a non-idempotent, side-effecting operation retried automatically with no dedup or idempotency key.
**Major** = a silent failure mode — no log, metric or span distinguishes it; retries stacked at more than one layer of the same call chain; a liveness probe that depends on a downstream service; a new dependency with no degradation path, where its outage takes the user-facing feature down with no bounded failure.
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
