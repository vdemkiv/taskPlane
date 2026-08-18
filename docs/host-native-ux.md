# Host-native workflow surfaces

Taskplane presents one canonical workflow record through Codex and Claude.
Host-native UI is a projection, never workflow authority. Every projection is
bound to the same workflow/run, target, revision, sequence, task/slot,
evidence, gate state, actions, and ordered audit history.

## Capability negotiation

The packages declare optional PiP, visualization, carousel, approval, sandbox,
hosting, browser, and side-panel surfaces. Actual support is established only
by a fresh host runtime receipt. Missing, partial, stale, contradictory, or
changed evidence disables that one surface and selects the accessible bounded
fallback. Other capabilities remain independent. This is reported as
unavailable, not declined.

Native styling and API names may evolve independently on Codex and Claude.
Adapters must preserve canonical values, ordering, provenance, evidence,
actions, accessibility, and human-gate authority. UI events alone cannot
approve a decision or synthesize a successful preview.

## Existing flows

- `design` retains its alternatives, Design Contract, evidence, and human gate.
- `build` retains scoped execution, tests, evidence, evaluation, and human gate.
- `review` retains DoR, validation, all lens results, artifacts, and sign-off.
- `status` retains the current canonical state and actionable gate owner.
- `approval` advances only with an authenticated current decision receipt.
- `artifact` delivery remains lossless when inline or native payloads are bounded.

Reconnect, host switch, concurrent delivery, duplicate events, stale UI, and
terminal close use stable identities and sequence ordering. Duplicates are
idempotent, stale updates are rejected, and each terminal surface closes once.
The complete JSON, Markdown, and HTML artifacts remain available whenever a
host surface is absent or too small for the canonical evidence.

## Preview safety

Interactive design, build, and dynamic-review previews run only in registered,
pinned, private disposable scopes. They cannot push, mutate the source checkout
or remotes, escape the sandbox, access undeclared external networks, or claim
success after build, launch, timeout, authorization, or teardown failure.
