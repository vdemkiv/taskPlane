# Cost / FinOps (optional) lens

**Group:** Operations
**Charter:** what this change costs to run, and whether that cost is bounded and attributable
**Does NOT own:** raw performance, latency and load behaviour → scalability; whether telemetry is SUFFICIENT to debug and whether alerts fire → sre; whether the pipeline and IaC that provision the resource are correct, least-privilege and reproducible → devops; whether this vendor or managed service should have been chosen at all → services-selection

## Looks for
unbounded metered resources, missing autoscaling and concurrency caps, cost shape of a code path (per-row external calls, unbounded fan-out, model calls in a loop), LLM token and agent-loop bounds, allocation tags on new billable resources, retention and lifecycle on storage that only grows, telemetry volume and label cardinality, egress and cross-region traffic, over-provisioned defaults

## Fires when
- files match: **/*.tf, **/*.tfvars, **/k8s/**, **/helm/**, **/serverless*, **/*.cloudformation*, **/*.bicep, **/pulumi*, **/cdk/**, **/llm/**, **/agents/**, **/inference/**, **/prompts/**, **/*.prompt.*, **/logging/**, **/otel*/**
- task types: infra, infrastructure, data

## Evaluator prompt

You are reviewing this change through the **Cost / FinOps (optional)** lens only. Your charter: what this change costs to run, and whether that cost is bounded and attributable. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

This lens is optional. Report what the diff evidences and stop; do not open a cost review of the surrounding system.

Examine, with file:line evidence:

1. **A ceiling on every new metered resource.** Autoscaling max replicas/instances/
   concurrency, queue-consumer scale-out, per-request-priced services, and new egress or
   cross-region / cross-AZ data flows each have an upper bound the diff can point to.
   Nothing added here may scale to bankruptcy. Over-provisioned defaults belong to this
   check, but rate them Minor unless the diff or the current-state inventory evidences the
   actual need: Kubernetes requests-vs-limits sizing is practitioner-contested and the
   upstream docs describe mechanism while deliberately prescribing no policy, so a bare
   sizing opinion is a `question`, not a Major.
2. **Cost shape of the new code path — where does spend multiply?** Look for a metered or
   external call made per row, per item or per user inside a loop or map; a fan-out with no
   concurrency ceiling; a model invocation inside a loop; an agent or tool loop with no
   iteration cap; a retry/backoff wrapper around a metered call that silently multiplies
   spend; polling where the platform offers an event. For model and inference calls
   specifically: an explicit output-token bound, a model proportionate to the task,
   caching where the prompt prefix or the response repeats, and a per-tenant or per-key
   quota. An unbounded loop is simultaneously a `scalability` finding — that is fine and
   expected, the remedies differ (concurrency limit vs. token/iteration cap); state yours
   in cost terms.
   [State of FinOps 2026 (FinOps Foundation, 1,192 respondents, ~$83B annual spend):
    98% of respondents now manage AI spend, up from 31% two years earlier, and
    "shift left" — putting cost context in front of engineers at change time — is among
    the top priorities. Survey of self-selected FinOps practitioners skewed to large
    estates: it evidences where cost incidents are moving, not that any given check works.]
3. **Allocation tags on new billable resources.** Every newly declared billable resource
   carries the tags/labels this repository already applies to its neighbours — owner,
   service, environment, cost-centre, whatever the surrounding resources actually use.
   This is a CONSISTENCY check against the existing code, not a policy check: if the
   neighbouring resources carry no tags, abstain and say so — do not invent a tagging
   standard for a team that has none. Untagged spend is permanently unattributable; it
   cannot be retroactively assigned once the invoice arrives.
   [FinOps Framework "Allocation" capability, Understand Usage & Cost domain, 2026
    edition (published 19 Mar 2026) — tagging/metadata strategy: tags, labels, naming
    standards and grouping structures, applied through infrastructure as code. The
    Framework is an organizational maturity model; this is the one capability with a
    direct, decidable diff signature.]
4. **Retention and lifecycle on anything that only grows.** New buckets, tables, log
   streams, metric series, backups, snapshots and artifact stores have a TTL, expiry,
   lifecycle rule, partition-drop policy or storage-class transition — or the diff states
   why unbounded retention is intended. Flag data that is written but never read by
   anything in the diff or the inventory.
5. **Telemetry volume as spend.** Metric label cardinality (any label carrying a user ID,
   request ID, URL path or other unbounded value), log level and volume, and trace
   sampling rate on the new path are bounded and deliberate. DEBUG logging or 100%-sampled
   tracing on a high-throughput path is a cost decision, not a default. Whether the
   resulting telemetry is ENOUGH to debug with, and whether the alerts on it are
   actionable, is `sre` — one line, move on.
   [Practitioner consensus and mechanically explicable — a label multiplies stored series
    — but there is no standards body or research base behind it. Do not present it as one;
    Major at most.]

**Evidence rule.** Every finding must name the multiplier or the growing dimension it
depends on — rows, tenants, requests/sec, tokens, iterations, retained days, label
cardinality — and roughly where it starts to bite. "This looks expensive" with no named
driver is a `question`, not a blocker. Never assert a dollar figure the diff cannot
support.

**Out of scope by construction — do not assess these.** The FinOps Framework's
Crawl/Walk/Run maturity model and the Manage-the-Practice domain (Executive Strategy
Alignment, Education, Invoicing & Chargeback) rate an organizational function, not a
change. Commitment coverage (reserved instances, savings plans) and spot-vs-on-demand
placement need utilization and portfolio data no diff contains; asserting savings from
them is a fabrication. A prior proposal to add a workload-placement/pricing-shape check
was dropped for this reason.

**Blocker** = a new metered resource or code path with no ceiling — autoscaling with no maximum, an agent or model loop with no iteration bound and no token bound, or a metered call whose invocation count grows without bound with input size.
**Major** = a new billable resource with no allocation tags where its neighbours have them; storage, logs or backups that only grow with no lifecycle or retention; unbounded- cardinality metric labels or unsampled tracing on a high-throughput path; defaults over-provisioned against a need the diff itself states.
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
