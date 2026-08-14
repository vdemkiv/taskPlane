# Go Solution Design

Target language version: **Go 1.26**. Go 1.27 remains unreleased draft
material as of 2026-08-14 and is not used as an adopted baseline.

Use this reference with the `solution-design` lens when the repository or
proposed work is Go. It complements, rather than replaces,
`go-code-quality.md`.

## Package and dependency boundaries

- Start flat. Create a package when it owns a coherent domain capability or a
  dependency boundary, not merely to mirror a directory taxonomy.
- Dependency direction points from policy toward narrow capabilities. Keep
  transport, storage, and vendor clients behind the smallest interface the
  consumer needs.
- Put an interface where it is consumed. A producer-owned interface normally
  exposes implementation shape instead of the caller's actual contract.
- Use `internal/` only when the compiler-enforced import boundary is valuable
  across multiple commands or modules. It is not the default home for every
  shared helper.
- Avoid package cycles by changing ownership or extracting a genuinely stable
  contract; callback registries and global service locators hide the same cycle.

## Failure and concurrency contracts

- Every blocking API accepts `context.Context` first and states who owns
  cancellation and deadlines.
- A goroutine introduced by the design has an explicit owner, completion path,
  and shutdown signal. Background work without lifecycle ownership is a leak.
- Errors cross package boundaries with stable semantics (`errors.Is`/`As`),
  while context is added with `%w`. Logging and retry policy stay at the layer
  that can decide the outcome.
- Concurrency is not an implementation footnote: name shared state, its owner,
  its synchronization mechanism, and the race-test evidence in the Design DoD.

## Design handoff

The Design Contract names proposed packages, dependency edges, external
contracts, migration/rollback behavior, and graph depth. For distributed
systems, graph depth stops at the contract between entities; internal remote
implementation is reviewed in its owning repository.

Primary references: [Go modules reference](https://go.dev/ref/mod),
[Effective Go](https://go.dev/doc/effective_go), and the
[Go memory model](https://go.dev/ref/mem).
