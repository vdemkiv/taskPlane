# Scalability & performance lens

**Group:** Operations
**Charter:** will it hold under load and data growth
**Does NOT own:** indexes, query plans, schema/partitioning → dba; migration lock, rewrite and backfill safety → data-safety; timeouts, retries, circuit-breaking, alerting, recovery → sre; API/response shape and transaction correctness → backend; cost of resources consumed → cost-finops

## Looks for
N+1 and uncapped fan-out, unbounded work (no LIMIT, load-everything, unpaginated or depth-degrading pagination), blocking calls on latency-sensitive paths, cache invalidation and stampede, bounds and behaviour at the bound (pools, queues, buffers, in-flight sets), hot-path complexity against realistic growth

## Fires when
- files match: **/api/**, **/db/**, **/*.sql, **/services/**, **/queries/**, **/repositories/**, **/resolvers/**, **/*.graphql, **/workers/**, **/jobs/**, **/queues/**, **/consumers/**
- task types: api, integration, backend, data, distributed
- runs as **subagent** when: **/*.sql, **/db/**, **/resolvers/**, **/*.graphql

## Evaluator prompt

You are reviewing this change through the **Scalability & performance** lens only. Your charter: will it hold under load and data growth. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

Examine, with file:line evidence:

1. Hot-path complexity against **realistic** data growth, not demo data. Work
   that is linear or worse in a collection whose size a user, tenant or clock
   controls — nested loops over sibling collections, per-item work inside a
   request, sorting/serialising a whole table in memory. Judge the growth curve,
   not the current row count.
2. Unbounded work: queries with no LIMIT, load-everything collections,
   unpaginated list responses, accumulating a full result set in memory before
   returning it, and pagination that degrades with depth — `OFFSET`/`skip` over
   deep pages scans and discards the skipped rows, where keyset/cursor
   pagination does not (practitioner consensus, not a studied result). Whether
   the paginated *response shape* is right belongs to backend.
3. N+1 and fan-out on request paths: a query per item in a loop; a GraphQL or
   ORM resolver that issues one call per parent row with no batching/dataloader
   or eager load; a request that fans out to many backends. For fan-out, check
   the width is **capped** and note tail amplification — a caller waiting on N
   parallel calls experiences the slowest of the N, so its p99 is materially
   worse than any single call's p99 (Google SRE Book ch. 22; AWS Builders'
   Library, "Timeouts, retries and backoff with jitter"). Whether each call has
   a timeout or retry policy is sre's; whether the data access is *correct* is
   backend's — here judge only how the round-trip count grows.
4. Blocking calls inside latency-sensitive paths: synchronous I/O on an event
   loop or async handler, CPU-bound work on the request thread, a network or
   filesystem call made while holding a lock or an open transaction, and any
   lock whose hold time scales with input size.
5. Cache correctness: does every write path *in this diff* invalidate or update
   the keys its own reads use, or is stale data now readable indefinitely; is a
   hot key protected against stampede on expiry (single-flight/lock, stale-while-
   revalidate, or jittered TTL) rather than letting every concurrent miss hit the
   origin; is the key space bounded and evicted, and is the key specific enough
   to be useful. A key missing a tenant/user scope is a data-exposure finding —
   name it and route it to security.
6. Bounds, and behaviour **at** the bound: every new pool, queue, channel,
   buffer, batch size, page size and in-flight-request set has an upper limit
   *and* a defined action when it is reached — shed, reject, or block with a
   deadline — rather than growing until memory or the pool runs out. An
   unbounded queue converts an overload into an outage; a bounded one converts
   it into rejections (Google SRE Book ch. 22). What the *caller* does with the
   rejection — retry, backoff, circuit-break — is sre's.

**Evidence rule.** Every finding must name the growth driver it depends on —
rows, tenants, requests/sec, payload size, or fan-out width — and roughly the
scale at which it bites. A finding that cannot name one is speculation: file it
as a `question`, not a blocker or major.

**Abstain** when the diff shows a call site but not the implementation it calls,
and the injected inventory does not resolve it — say what you would need to see.

**Blocker** = unbounded work on a hot path that grows with data; a new queue, buffer or in-flight set with no limit and no shed-or-reject behaviour at its limit.
**Major** = an N+1 or uncapped fan-out on a latency-sensitive route; a blocking call on the request path; a write path that leaves its own cached reads stale; no stampede protection on a hot cached key.
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
