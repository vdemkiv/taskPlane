# Integrability lens

**Group:** Interfaces
**Charter:** contracts BETWEEN systems: shape, compatible evolution, versioning and retirement, error semantics
**Does NOT own:** implementation behind the contract — transactions, dual-write/outbox, the server-side implementation of an idempotency key, and this service's internal error-shape consistency → backend; retry/timeout *policy* → sre; changing data already stored, migrations and backfills → data-safety; schema DESIGN, data types, indexes → dba; whether this boundary should exist at all, service decomposition and coupling → architecture; pipeline configuration and gate wiring quality → devops; adequacy of the contract tests themselves → qa; authz on the endpoint and mass assignment → security; prose docs, changelog and reference accuracy → tech-writer

## Looks for
API/data contracts, an explicit breaking-vs-additive taxonomy per contract style (REST/OpenAPI, protobuf/gRPC, GraphQL, Avro/event schemas), versioning, deprecation & sunset signalling with a stated consumer notice period, error semantics to a named standard, documented retryability, unknown-field tolerance on both producer and consumer, automated compatibility gating and contract testing, spec/SDK sync, pagination & naming conventions

## Fires when
- files match: **/api/**, **/contracts/**, **/schema/**, **/schemas/**, **/*.proto, **/openapi*, **/swagger*, **/asyncapi*, **/*.graphql, **/*.graphqls, **/*.gql, **/*.avsc, **/*.thrift, **/buf*.yaml, **/webhooks/**, **/sdk/**, **/sdks/**, **/clients/**, **/generated/**
- task types: api, backend, integration, distributed
- runs as **subagent** when: **/*.proto, **/*.graphql, **/*.graphqls, **/openapi*, **/swagger*, **/asyncapi*, **/*.avsc

## Evaluator prompt

You are reviewing this change through the **Integrability** lens only. Your charter: contracts BETWEEN systems: shape, compatible evolution, versioning and retirement, error semantics. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

In particular: whether the server *correctly implements* idempotency or transactions is **backend**'s and whether its retry/timeout policy is sound is **sre**'s; whether this boundary should exist at all is **architecture**'s; whether stored data survives the change is **data-safety**'s.

**Establish the blast radius first.** Is this contract *published* — consumed by a party that cannot be redeployed in lockstep with this change (external customers, another team's service, a mobile app already in users' hands, a persisted event stream)? Or is it internal with a single known consumer deployed together? Read it from the diff, the as-built inventory or the dependency graph. Every severity below is stated for a published contract; on an internal single-consumer contract the same findings drop to **minor**. If you cannot tell, say so and raise check 1 as `question`, not `blocker` — this gating clause is the difference between a lens teams use and a lens teams mute.

Examine, with file:line evidence:

1. **Breaking vs additive, against an explicit taxonomy — not just "was something removed".** Most real breaks remove nothing. Apply the taxonomy for the contract style in the diff:
   - *All styles:* removing or renaming a field, method, endpoint, enum value or resource name; changing a field's type **even when wire-compatible**; adding a **required** field or parameter to an existing request; changing a default value; changing the format or algorithm used to construct an existing field's value (an opaque ID that becomes structured, a timestamp that changes precision); tightening validation, length bounds or enum membership on an existing input. [Google AIP-180 — vendor-authored but explicitly normative RFC-2119 text, actively maintained]
   - *protobuf/gRPC:* changing a field number; deleting a field or enum value without `reserved`-ing **both** its number and its name (the name reservation is what keeps JSON and TextFormat parsing); moving a field into or out of a `oneof`. [protobuf.dev, "Updating A Message Type"]
   - *GraphQL:* the reference implementation enumerates these as breaking — type/field/argument removed, type changed kind, type removed from union, value removed from enum, required input field or required argument added, implemented interface removed, field or argument changed kind (which is where **output non-null → nullable** and **input nullable → non-null** live), directive/directive-arg removed. Adding an enum value or a union member is classified *dangerous*, not breaking: safe unless a client switches exhaustively. GraphQL convention is to evolve rather than version, so `@deprecated` doing real work here is expected, not optional. [graphql-js `BreakingChangeType`/`DangerousChangeType`, GraphQL Foundation reference implementation; GraphQL specification, September 2025 edition]
   - *Avro / event schemas:* a reader errors on a field the writer omitted **unless the reader's field has a default**, and errors on an unknown enum symbol **unless the reader's enum has a default**; a writer's extra field is ignored; renames are only safe via `aliases`. Direction matters: under the Schema Registry default of BACKWARD compatibility, **consumers must be upgraded before producers** — a change that is "compatible" in the wrong deploy order is still an outage. BACKWARD is checked against the *last* version only, so a chain of individually-compatible changes can break a consumer that skipped a version; say so when a topic's schema has moved more than once. [Apache Avro specification, "Schema Resolution"; Confluent Schema Registry compatibility types — vendor source, de facto for Kafka]

   State which side of the line each change falls on. A breaking change is acceptable **only** behind a new version (path, media type, subject or topic) with the predecessor still served — not behind "we told them in Slack".
2. **How the old version dies, with a notice period.** Versioning without retirement strands consumers silently. Require, in-band and machine-readable: `Deprecation` (RFC 9745, IETF Standards Track, March 2025 — an Item Structured Header whose value is a Date), and once a removal date is fixed, `Sunset` (RFC 8594, Informational) with the **sunset timestamp no earlier than the deprecation timestamp**, plus a `deprecation` link relation pointing at the migration guide. For non-HTTP contracts the equivalent is `@deprecated` (GraphQL), `deprecated = true` (protobuf), `deprecated: true` (OpenAPI/AsyncAPI) — carrying a reason and a replacement, not a bare flag. Then ask the question the headers do not answer: **is the notice period stated, and is it at least one full release cycle of the slowest consumer?** Concrete public anchors to calibrate against, not to copy: Kubernetes gives GA APIs the life of the major version and beta APIs 9 months or 3 minor releases, whichever is longer; Google Cloud commits to 12 months for a GA API. A mobile client on a monthly store cadence needs longer than a co-deployed service. Abstain if nothing in the diff retires anything.
3. **Errors and retryability are part of the contract, to a named standard.** For HTTP/JSON that standard is RFC 9457 `application/problem+json` (IETF, Proposed Standard, July 2023, obsoletes RFC 7807): a stable `type` URI that clients key off — *not* the HTTP status code — with `title`/`status`/`detail`/`instance`, and clients required to **ignore extension members they do not recognise** so the error shape can grow. Any single consistent, documented, machine-readable scheme is acceptable; an ad-hoc per-endpoint shape on a published boundary is not. Alongside it, the contract must state **which operations are safe to retry** and whether an idempotency key is honoured on this endpoint — consumers cannot infer it. Whether the server actually implements that key correctly, and whether this service's errors are internally consistent and non-leaking, are **backend**'s (its checks 3 and 7); whether the promise is *made to the consumer at all* is yours.
4. **Both sides tolerate the unknown.** Additive evolution only works if consumers ignore what they do not recognise. Hunt, in this repo's consumer code and its response schemas: `additionalProperties: false` on a **response** schema, strict deserializer settings (`FAIL_ON_UNKNOWN_PROPERTIES` and equivalents), exhaustive enum switches with no default arm, and Avro/GraphQL enum handling with no fallback. Each of these converts every *additive* producer change into an outage, which is the failure mode teams never see coming. Deserializer strictness at the boundary is the contract, not internal logic — but if the finding is really about business logic downstream of parsing, hand it to **backend** in one line.
5. **A machine enforces compatibility, not this review.** Ask only whether the gate **exists and is required** on the contracts external parties depend on: a protobuf breaking-change check (Buf's ladder runs FILE → PACKAGE → WIRE_JSON → WIRE, with WIRE_JSON documented as the recommended minimum and FILE as the default), an OpenAPI/GraphQL schema diff, a Schema Registry compatibility level that is not `NONE`, or consumer-driven contract tests shared with the provider so they run in the provider's pipeline [Fowler, "ContractTest" / Robinson, "Consumer-Driven Contracts" — practitioner pattern literature, not empirical; no authoritative primary source for contract-testing efficacy was found, and no specific tool is prescribed]. A reviewer catches breakage on the diffs they happen to read; a gate catches it on all of them. Whether the pipeline is *well built* is **devops**'; whether the contract tests are *good* is **qa**'s — name the owner and move on.
6. **The description matches the thing described, and matches its neighbours.** The spec artifact (`openapi*`/`swagger*`, `asyncapi*`, `.proto`, SDL, `.avsc`) is updated in this same change, and any generated SDK or client directory is regenerated with it — a spec that lags the code is a contract nobody can trust, and a stale generated client is a break that has already shipped. Within the artifact, pagination, filtering, sorting, error naming and field naming follow the conventions the rest of this API already uses; a second convention on the same surface is a permanent tax on every consumer.

**Blocker** = a breaking change to a **published** contract shipped without a new version that keeps the predecessor served — including the non-obvious forms: a new required field or argument on an existing request, a changed default value, a changed value format, a field moved into or out of a `oneof`, a reused protobuf field number, a field or enum value deleted without `reserved`, an output field made nullable or an input made non-null in GraphQL, an Avro field added with no default. Also: a change that is only compatible in a deploy order nobody has specified.
**Major** = a version or endpoint retired with no `Deprecation`/`Sunset` (or style-equivalent) signal, or with no stated notice period; an error contract on a published boundary that is not machine-readable or keys clients off the status code; retryability undocumented where retries are expected; a consumer in this repo that rejects unknown fields; no required automated compatibility gate on an externally-consumed contract; a spec file or generated SDK out of sync with the code it describes.
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
