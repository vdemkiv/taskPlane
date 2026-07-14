# Database selection — which engine for which scenario

Applied by the **dba** and **architecture** lenses at requirement/plan time,
when a task introduces or changes a data store. The decision is recorded to
the knowledge base (it's exactly the kind of call the next track must not
relitigate). No pricing here — engine fit first; cost follows fit.

## The four questions that decide it

1. **Shape of the data** — rows with relations? documents that travel
   together? key→value? time-ordered events? connections-as-the-point?
2. **Consistency needs** — do invariants span entities (money, inventory,
   bookings)? Then transactions are non-negotiable.
3. **Query patterns** — known access paths (KV/document thrive) vs ad-hoc
   queries, joins, and reporting (relational thrives).
4. **Write/read profile** — write-heavy append streams, read-heavy lookups,
   or balanced OLTP?

## Default rule

**Start relational (PostgreSQL or equivalent) unless a question above
disqualifies it.** Relational engines handle document columns (JSONB),
moderate KV, full-text, and time-series respectably; the reverse is false.
A specialized engine must earn its place with a workload the relational
default demonstrably can't serve — that justification goes in the R-record.

## Scenario → engine

| Scenario | Reach for | Because |
| --- | --- | --- |
| Business entities with relations, invariants, reporting (orders, users, billing) | **Relational** (PostgreSQL/MySQL/SQL Server) | joins, transactions, constraints enforce invariants at the store |
| Self-contained documents, schema varies per record, known access paths (catalogs, profiles, CMS) | **Document** (MongoDB/Cosmos) — or JSONB in the relational DB first | the document is the unit; no cross-document invariants |
| Hot lookups, sessions, caching, rate counters | **KV/cache** (Redis) *in front of* the system of record | latency; never the only copy of durable data |
| Append-heavy event/metric streams queried by time window | **Time-series** (Timescale/Influx) | retention, downsampling, time-bucketed compression |
| Text search, faceting, relevance ranking | **Search index** (OpenSearch/Meili) *beside* the source of truth | inverted indexes; rebuildable projection, not primary store |
| Similarity/RAG over embeddings | **Vector** (pgvector first, dedicated store at scale) | ANN indexes; pgvector defers a second engine |
| Relationship-traversal as the product (fraud rings, social graphs, ≥3-hop queries) | **Graph** (Neo4j/Gremlin) | recursive joins die where traversals live |
| Massive write throughput, multi-region, query-by-partition-key | **Wide-column** (Cassandra/Cosmos) | linear write scaling; you give up joins & ad-hoc queries knowingly |

## Red flags the lens raises

- A second database engine introduced for a workload the existing one
  handles (polyglot persistence multiplies operational burden — backups,
  migrations, expertise, failure modes — per engine).
- KV/document chosen "for speed" where invariants span records — that's
  buying data corruption with latency savings.
- The cache as the only copy of anything durable.
- A search/vector/analytics store treated as a source of truth instead of a
  rebuildable projection of one.
- No stated migration path for data already living somewhere else.

## Record the decision

`tp.py kb record "DB choice: <workload> → <engine>" --context "<the four
answers>" --decision "<engine + why the default was/wasn't enough>"
--tags db-selection --files "<data-layer globs>"`
