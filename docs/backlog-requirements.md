# Backlog requirement review

Re-evaluated 2026-08-14 from the ignored Claude planning drafts. This is the
canonical disposition; `backlog/` remains local scratch and is not a release
artifact.

## Language depth reaches the agent

| Draft requirement | Disposition | Decision |
|---|---|---|
| Go R1 — lens agents receive language references | Accepted, implemented | Resolve references from the canonical file set, carry repo-relative paths in briefs, and explicitly instruct the worker to read them. Bodies stay artifact-by-reference. |
| Go R2 — Go is a routing signal | Revised, implemented | `.go` was already in `code_extensions`; `go.mod`/`go.sum` already routed security and service-selection. Do not widen fan-out. Go now additionally selects language references, which was the missing signal. |
| Go R3 — priming is language-correct | Accepted, implemented | Build priming derives a representative extension from scope/root manifests and catalog data instead of always inventing `x.py`. |
| Go R4 — Design receives Go input | Accepted, implemented | A Go root manifest attaches the Go solution-design reference to the mandatory design lens. |
| Go R5 — current authoritative reference | Revised, partial | Adopt Go 1.26, not unreleased Go 1.27. Correct the two unsafe rules now; a larger content rewrite needs its own source/provenance pass. Official Go 1.27 notes still call the release a draft: <https://go.dev/doc/go1.27>. |

## Python and TypeScript reference depth

| Draft requirement | Disposition | Decision |
|---|---|---|
| Lang R1 — machine-findable target versions | Accepted, implemented | Python 3.14 and TypeScript 7.0 are current stable baselines. Sources: <https://www.python.org/downloads/> and <https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>. |
| Lang R2 — Python async/types/tests/packaging depth | Accepted, queued | Valuable content work, but independent of delivery wiring and Windows CI. Implement as one source-reviewed reference batch. |
| Lang R3 — TypeScript modules/build-scale/async depth | Accepted, queued | Same content batch as R2; keep TypeScript 6/7 transition behavior explicit. |
| Lang R4 — verified grep and typed-lint defects | Accepted, implemented | Python uses PCRE mode; TypeScript's gate now requires typed parser services for type-aware rules. |
| Lang R5 — material lands with its owning lens | Revised, queued | First make the existing code-quality references reachable. Split future material into lens-owned references only when doing so does not duplicate prompt bodies or widen routing. |
| Lang R6 — provenance register | Accepted, prerequisite | Required before adapting third-party prose/examples. This batch uses original text and primary documentation, so it does not create a misleading partial register. |

## Review convergence

| Draft requirement | Disposition | Decision |
|---|---|---|
| Convergence R1 — admissibility at filing | Accepted, next feature track | Keep separate from routing/CI: it changes the meaning and storage of findings. |
| Convergence R2 — violations name a declaration | Accepted with refinement | Resolution must use canonical requirement/decision/config/reference identities, not free-form prose. |
| Convergence R3 — briefs carry settled findings | Accepted | Carry a bounded, scoped artifact reference; never embed the historical finding bodies in every brief. |
| Convergence R4 — recurrence needs new evidence | Accepted | Enforce at canonical result collection, after provenance validation. |
| Convergence R5 — `not-a-defect` disposition | Accepted | Add as a durable human disposition, not an agent-selected escape hatch. |
| Convergence R6 — convergence measurement | Accepted | Use a two-pass frozen scenario and measure admissible findings; do not run it in every ordinary review. |

## Delivery order

1. Language-reference wiring and verified defects — completed in the current
   repair batch.
2. Review convergence/adjudication memory — next behavioral feature because it
   directly reduces repeated lens cycles and token spend.
3. Python/TypeScript content expansion — one provenance-reviewed documentation
   batch, with static syntax/source checks rather than broad workflow tests.

This ordering favors behavior that changes actual model execution before adding
more reference volume to every marketplace package.
