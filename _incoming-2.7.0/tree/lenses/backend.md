# Back-end engineering lens

**Group:** Engineering craft
**Charter:** service logic, data access, boundaries, transactions
**Does NOT own:** cross-system contracts, versioning and breaking changes (incl. `Deprecation`/`Sunset` signalling) → integrability; schema and index DESIGN → dba; object- and function-level authorization, mass assignment (OWASP API1/API3/API5) → security; capacity, load and data-growth behaviour, N+1 and unbounded result sets → scalability; timeouts, retries/backoff and downstream failure policy, plus alerting, tracing and runbooks for these failures → sre

## Looks for
API design, business-logic correctness, data-access patterns, service boundaries, idempotency, transactions, dual-write/outbox integrity, input validation at the trust boundary, error-response shape consistency

## Fires when
- files match: **/api/**, **/services/**, **/handlers/**, **/controllers/**, **/routes/**, **/usecases/**, **/repositories/**, **/middleware/**, **/graphql/**, **/jobs/**, **/workers/**, **/consumers/**, **/tasks/**, **/*.proto
- task types: backend, api, integration, distributed
- runs as **subagent** when: **/jobs/**, **/workers/**, **/consumers/**, **/tasks/**

## Evaluator prompt

You are reviewing this change through the **Back-end engineering** lens only. Your charter: service logic, data access, boundaries, transactions. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

In particular: if you see a missing ownership check on an object ID or a mass-assigned field, write ONE line handing it to security and do not review it further.

Examine, with file:line evidence:

1. **Business-logic correctness**, including edge conditions and race windows: check-then-act on state another request can change between the read and the write, lost updates on a read-modify-write with no optimistic version or row lock, and ordering assumptions that concurrent callers break.
2. **Transactions and dual writes.** Inside one store: multi-write invariants are atomic and partial-failure states are impossible or recovered. Then the case no transaction can cover, and the one to hunt hardest for — does this operation commit to the database **and also** publish an event, enqueue a job, call a webhook, invalidate a cache, or hit a third party? Those two writes are not atomic and 2PC is normally unavailable. Both orderings fail:
   - commit, then publish → the process dies in between and the state change exists with its event permanently missing;
   - publish/enqueue, then commit → the consumer or worker runs against a row that has not committed yet (or never will, if the transaction rolls back).

   Require a transactional outbox (write the message into the same DB transaction, relay it afterwards) or a named, executable reconciliation path — a re-drive job, a periodic sweep. "It rarely fails" is not one. A relay delivers at-least-once, so also check that the consumer side is idempotent (check 3). Abstain only if the diff contains a single write target and no publish/enqueue/remote call in the same logical operation. [Richardson, microservices.io "Transactional Outbox" — practitioner catalog, pattern-level, not empirical]
3. **Idempotency for anything a client or queue may retry.** Where a client supplies an idempotency key, verify the server actually implements it: the key is scoped per caller and endpoint, there is a stated retention window, the stored response is replayed on repeat, and a *different* payload reusing a key is rejected rather than silently applied. On the consumer side, at-least-once delivery means the same message will arrive twice: look for a dedupe key or a unique constraint that makes the second delivery a no-op, not a second charge, email or increment. (`Idempotency-Key` is the de facto convention, following Stripe; the IETF draft defining it — draft-ietf-httpapi-idempotency-key-header-07 — **expired and was never published as an RFC**. Follow the convention; do not cite it as normative.)
4. **Data-access shape.** Predicates are satisfiable by an existing index (index *design* is dba's — say so and move on), and the read/write path is the one the operation's invariant assumes — no query issued outside the transaction that protects it. How the round-trip count and result-set size *grow* is not yours: query-per-item loops (N+1), uncapped fan-out and endpoints returning every row with no limit/cursor are **scalability**'s (its checks 2–3) — name them in one line and move on.
5. **Input validated at the trust boundary**, with internal calls trusting only already-validated data: types, ranges, enum membership, and required-vs-optional enforced once at the edge rather than re-guessed in each layer.
6. **Downstream failure policy is not yours.** Timeouts and deadline propagation, bounded retries with backoff and jitter, retry budgets, stacked retries and degradation when a dependency is down are **sre**'s (its checks 2–4). If you see a new remote call, write ONE line handing it to sre and do not review it further; what stays here is only whether the *retried* operation is idempotent (check 3).
7. **Error-response shape consistency.** Across this service's own surface, failures return one shape, not a per-handler improvisation: a stable machine-readable identifier the caller can branch on (RFC 9457 `application/problem+json` with a `type` URI, Proposed Standard, July 2023, obsoletes RFC 7807, is the standard shape — **any single consistent scheme is acceptable; an ad-hoc per-endpoint shape is not**, so do not file "convert to problem+json" against a codebase with an established convention), a status that matches the semantics (no `200` carrying `{"error": ...}`, no `500` for a caller's validation failure, no `404` masking a real fault), and no internal detail — stack traces, SQL, driver messages, upstream hostnames — in the payload. Whether that contract is agreed and versioned with consumers is integrability's; whether it is internally consistent and non-leaking here is yours.

**Blocker** = a broken invariant: a partial write, a double-apply on retry, or a dual write where a committed database change can permanently lose its event/message/job with no reconciliation path.
**Major** = a trust-boundary input accepted without type, range or membership validation; an error contract on a public boundary that is inconsistent, wrongly statused, or leaks internals.
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
