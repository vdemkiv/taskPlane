# Taskplane runtime

One compact shared controller serves Codex and Claude new runs with
`taskplane.workflow/v2`, durable `brief/v1` and scoped `ownership/v1`.
Product → Design → Plan → Build → Evaluate → Engineering → Retro each retain a
separate decision. Manual human approval is the default; optional automatic mode
requires explicit run-bound policy authorization, scoped conditions, stop phases
and assessments. Ordinary intent is interpreted in context with observed
provenance, unchanged target, chronology and replay checks; one phase assent
never enrolls a policy or approves future results.

Direct submission validates concrete output once. Graph/dashboard/telemetry and
prevalidation are optional. Root/serial/useful native ownership is valid; actual
native identity and terminal/cancellation evidence remain required. Relevant
inputs govern reuse, and immutable check history preserves latest fail/unknown.

Workspace-local state, atomic/CAS storage, native locks and authentic import
provenance survive; receipt/startup accounting and unrelated-file acknowledgement
do not. Old-contract records remain historical/readable, without active mutation
or approval transfer. See [shared flow](../skills/tp-go/references/shared-flow.md),
[CLI](../docs/cli-reference.md), [state](../docs/state-spec.md) and
[package verification](../docs/ci-performance.md). Tests/packages do not certify
installation, loaded hooks, live native journeys or publication.
