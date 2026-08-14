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
| Go R5 — current authoritative reference | Revised, implemented | Adopt Go 1.26, not unreleased Go 1.27. The split Go references now cover code quality, engineering, and solution design with recorded source provenance. Official Go 1.27 notes still call the release a draft: <https://go.dev/doc/go1.27>. |

## Python and TypeScript reference depth

| Draft requirement | Disposition | Decision |
|---|---|---|
| Lang R1 — machine-findable target versions | Accepted, implemented | Python 3.14 and TypeScript 7.0 are current stable baselines. Sources: <https://www.python.org/downloads/> and <https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>. |
| Lang R2 — Python async/types/tests/packaging depth | Accepted, implemented | Python 3.14 guidance now covers structured concurrency, cancellation, modelling with types, false-green tests, packaging, and supply-chain checks in lens-owned sections. |
| Lang R3 — TypeScript modules/build-scale/async depth | Accepted, implemented | TypeScript 7 guidance now covers native-toolchain migration, ESM resolution, project references/build scale, boundaries, and cancellation with the TypeScript 6 compatibility transition explicit. |
| Lang R4 — verified grep and typed-lint defects | Accepted, implemented | Python uses PCRE mode; TypeScript's gate now requires typed parser services for type-aware rules. |
| Lang R5 — material lands with its owning lens | Revised, implemented | Language material is split across code-quality, engineering, and solution-design references; routing selects only the owning lens sections and does not widen fan-out. |
| Lang R6 — provenance register | Accepted, implemented | `lenses/references/SOURCES.md` records versions, primary sources, compatibility sources, licences, and adaptation boundaries. |

## Review convergence

| Draft requirement | Disposition | Decision |
|---|---|---|
| Convergence R1 — admissibility at filing | Accepted, implemented | Canonical collection routes only structural defects and declared-standard violations into findings; commentary becomes a durable note. |
| Convergence R2 — violations name a declaration | Accepted, implemented | Resolution uses canonical requirement/decision/config/budget/reference identities, not free-form prose. |
| Convergence R3 — briefs carry settled findings | Accepted, implemented | One bounded, scoped artifact reference carries fingerprints and dispositions; historical bodies are not copied into every brief. |
| Convergence R4 — recurrence needs new evidence | Accepted, implemented | Canonical collection refuses a settled recurrence unless it names materially new evidence. |
| Convergence R5 — `not-a-defect` disposition | Accepted, implemented | `not-a-defect` is a durable human disposition and cannot be selected as an agent escape hatch. |
| Convergence R6 — convergence measurement | Accepted, implemented | A frozen two-pass scenario pins zero new admissible findings after settlement without adding a model run to ordinary review. |

## Delivery order

1. Language-reference wiring and verified defects — completed.
2. Review convergence/adjudication memory — completed.
3. Python/TypeScript content expansion and source provenance — completed.

This ordering favors behavior that changes actual model execution before adding
more reference volume to every marketplace package.
