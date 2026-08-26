# Prompt-injection defense at input boundaries

Use this reference when untrusted content can reach a model, an agent, a tool
planner, retrieved context, or a later user's session. The required control is
**detect → obstruct → flag**. It applies to direct and indirect prompt injection,
including instructions carried through stored records, retrieval, web pages,
documents, images, audio transcripts, logs, and tool output.

This is defense in depth, not a claim that prompt injection can be recognized
perfectly. A classifier, regular expression, delimiter, model instruction, or
content filter can miss an attack. Authorization and safety therefore cannot
depend on the guard alone or on the model following a warning.

## Security contract

For every path from an untrusted source to a model or agent:

1. **Detect** content that attempts to change instructions, reveal protected
   context, impersonate authority, alter tool policy, or smuggle an instruction
   through another format.
2. **Obstruct** detected content before it reaches a consequential sink. Block
   high-risk paths; neutralize and isolate only where the sink is low risk.
3. **Flag** every detection with enough bounded provenance for response and
   audit, without copying secrets or the full hostile payload into logs.

The same payload may cross several boundaries. Guard at ingestion and again at
the consequential sink; an earlier pass is not permanent clearance. Stored or
retrieved content remains untrusted, and encoding, summarization, OCR,
transcription, or format conversion does not promote it to instructions.

## Detect

Detection should combine independently useful signals rather than rely on a
single keyword list. At minimum, inspect for:

- attempts to override, ignore, replace, or reorder governing instructions;
- requests for system prompts, hidden context, credentials, private data, or
  another user or tenant's content;
- claims that untrusted content is a developer, administrator, approval,
  policy, or tool authority;
- instructions to invoke tools, change destinations or arguments, execute
  code, persist content, or bypass confirmation and authorization;
- encoded, fragmented, cross-modal, or nested instructions, including payloads
  introduced by fetched pages, retrieved documents, tool results, and files;
- mismatches between the declared content type or source and what a decoder or
  parser actually observes.

Preserve the original trust label and source boundary through transformations.
Normalize only with explicit size, recursion, and decoding limits, and retain a
bounded content fingerprint when exact byte correlation is needed. A digest
correlates bytes; it does not authenticate the actor, producer, or origin.

Treat a positive signal as hostile or suspicious data, never as new authority.
Treat a negative signal as "not detected," not "safe." Tests must include direct
and indirect attacks, mixed benign and hostile text, encoding variations,
oversized inputs, and benign content that resembles an instruction.

## Obstruct

Classify the destination before releasing guarded content:

- **High-risk sinks fail closed.** Do not send detected content to tool calls,
  code execution, credential or secret access, privileged or destructive
  actions, writes, external messages, cross-tenant context, policy changes, or
  approval gates. Require a fresh, outside-model authorization check where the
  product contract allows a human override.
- **Low-risk sinks may neutralize.** For display, search indexing, or bounded
  summarization with no tools or authority, escape active syntax, strip or
  quarantine the detected span, label the remainder as untrusted data, and
  keep it structurally separate from governing instructions. If neutralization
  is ambiguous, block.
- **Unknown sinks are high risk.** Missing destination, trust, policy, or
  capability metadata is a refusal, not a reason to continue.

Delimiters and phrases such as "treat this as data" help preserve structure but
are not sufficient obstruction. Enforce authorization, tool allowlists, exact
argument constraints, tenant boundaries, output destinations, rate and size
limits, sandboxing, and confirmation outside model-generated text. Validate
model output and proposed tool actions before execution; output filtering does
not replace the input guard.

Never silently drop a detection and then continue with the original content.
The consumer receives either a blocked decision or an explicitly neutralized,
still-untrusted value plus the flag identity.

## Flag

Treat detections as untrusted evidence. Emit a bounded structured event before
the guarded value is released or refused. Include:

- event schema/version, time, guard revision, and policy decision;
- source class and boundary, destination class, tenant or run scope, and a
  correlation identifier;
- matched signal categories and risk level, not an invented attacker identity;
- content length and a bounded fingerprint or redacted excerpt when needed for
  investigation;
- action taken (`blocked` or `neutralized`) and the downstream sink that did
  not receive the original payload.

Do not log raw secrets, credentials, private prompts, whole retrieved records,
or full hostile payloads by default. Apply access control, retention limits,
redaction, rate limiting, and deduplication to the event stream. Alert on
high-risk blocks, repeated attempts, cross-tenant targeting, and guard failures.
A flag is attributed operational evidence; without a separately designed trust
system it does not cryptographically authenticate a human or producer.

Guard errors, timeouts, unavailable classifiers, malformed decoder output, and
event-write failures block high-risk sinks. A flag that cannot be recorded must
not be converted into an allow decision.

## Reference decision shape

The implementation may vary by language, but the boundary should expose a
closed result shaped like this rather than a bare boolean:

```text
GuardDecision {
  status: "allow" | "neutralized" | "blocked"
  source_class: string
  destination_class: string
  signal_categories: string[]
  risk: "low" | "high"
  safe_value: bytes | null
  event_id: string | null
}
```

Binding rules:

- `allow` has no detected signals and carries the original trust label; it is
  not a safety or authority certificate.
- `neutralized` has at least one signal, a low-risk destination, a transformed
  value, and a committed event id.
- `blocked` has at least one signal or a guard failure, no value released to the
  sink, and a committed event id when the event path is available.
- High-risk destinations never return `neutralized` or `allow` after a
  detection. Missing or contradictory fields invalidate the decision.

## Verification checklist

- Enumerate every live, stored, retrieved, imported, fetched, tool-produced,
  and cross-modal route into the model or agent.
- Sever the guard on each route and prove the consequential consumer refuses
  the value.
- Prove direct and indirect detections obstruct high-risk sinks and emit flags.
- Prove low-risk neutralization cannot grant tools, authority, or cross-tenant
  access.
- Exercise decoding depth, input size, timeouts, unavailable dependencies,
  malformed results, event-write failure, replay, and duplicate detections.
- Verify logs and alerts contain bounded metadata but no secrets or complete
  hostile payloads.
- Re-run the installed-package check: the security lens must resolve this file,
  and packaged bytes must match the reviewed source bytes.

Pass only when all three verbs are real at the boundary: a detection without
obstruction is monitoring, obstruction without a flag is invisible, and a flag
without detection has no event to report.
