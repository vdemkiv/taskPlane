# Routing and delivery

Taskplane routes the requested work directly: Product, Design, full delivery,
Engineering review, reusable workflow authoring/invocation, status or help.
Help/status/catalog/validation/preview inspect existing state without activation.
Standalone Product/Design/Engineering stop at their requested result; a review
request does not grant implementation.

Full delivery is Product → Design → Plan → Build → Evaluate → Engineering → Retro.
Every phase has a concrete result and separate decision. Default manual mode
uses actual human approval; optional automatic mode requires explicit run-bound
policy authorization, scoped conditions, stop phases and assessments. Start with goal,
stable criteria, material boundaries and current output; Plan later fixes exact
Build ownership, dependencies, paths and checks. No early exhaustive future-path
inventory is required, and ordinary phase assent never enrolls automatic policy.

Use current source, requirements and relevant dependencies for decisions. Graphs,
lens catalogs and decomposition are optional aids. There is no all-catalog
ledger, mandatory three/four-lens Plan wave, zero-worker Evaluate rule, audit
cadence gate or forced fallback transport. Root integrated work, serial work and
useful native workers are all valid. Preserve explicit requested independence and
report actual reviewer coverage rather than infer it from labels.

Actual prerequisites and read/write conflicts govern concurrency. A task cannot
depend on a later phase. Native work needs real launch/attempt identity and joined
terminal completion; a mailbox message or cancellation request is insufficient.
Unrelated files/progress do not invalidate unchanged relevant inputs.

Route proposals use the same eligibility validation before sealing and deciding.
Invalid transitions fail before presentation. Repair keeps accepted Plan scope
and appends fresh Build → Evaluate → Engineering → Retro visits; it transfers no
old phase approval. New verification attempts preserve history, and later
fail/unknown always outranks an older applicable pass.

See [the shared flow](../skills/tp-go/references/shared-flow.md),
[native ownership](../skills/tp-go/references/codex-native-dispatch.md) and
[CLI reference](cli-reference.md). Old `repository`, `loop`, `stage`, `share`,
wave transports and external plan-aware stores described in previous releases
are not current operational commands.
