# Go Engineering References

Target language version: **Go 1.26**.

Sources and attribution: [SOURCES.md](SOURCES.md). Concepts from
`spf13/go-skills` are adapted under MIT from commit
`e67851cfcca008592c7c4965b8220c7cb37e2f1c`; all rules below were checked
against current Go documentation.

## Architecture

- Keep packages flat until a cohesive domain capability or compiler-enforced
  boundary justifies another level. Avoid `utils`, `common`, and layer-only
  packages that create dependency cycles without owning behavior.
- Define interfaces in the consuming package. Accept the narrow capability
  needed by the caller and return concrete implementations.
- `internal/` is a deliberate import boundary, not the default home for shared
  application code. A command's routing stays in `cmd/`; domain behavior does
  not.

## Backend

- Blocking and remote operations accept `context.Context` first and propagate
  cancellation. HTTP handlers use request context rather than a background
  context.
- Middleware is `func(http.Handler) http.Handler`; typed configuration and
  domain objects cross boundaries, not global Viper instances or service
  locators.
- Every retry states which errors are retryable, the deadline, backoff, and
  idempotency contract. Never retry an unsafe mutation by default.

## SRE

- Use `signal.NotifyContext` for shutdown ownership and a fresh bounded context
  for server shutdown. Every goroutine names its exit condition.
- Set server and client timeouts deliberately; `http.DefaultClient` has no
  overall timeout. Long-lived handlers watch `r.Context()`.
- Log or return an error, never both. Put structured operational fields at the
  layer that knows the outcome and keep package-level loggers out of libraries.

## Security

- Set `ReadHeaderTimeout` on public HTTP servers and explicit timeouts on
  clients. Review reverse proxies for the safe `Rewrite` API rather than the
  deprecated `Director` hook.
- Use `crypto/rand` for secrets and tokens. Join untrusted path segments under
  a trusted root and verify containment; cleaning an already-combined path is
  not a boundary.
- Run `govulncheck` for changed dependency and exported-call surfaces. Parameterize
  SQL and pass command arguments without a shell.

## QA

- Prefer table-driven subtests with values chosen so the correct and buggy
  implementations produce different outcomes. Helpers call `t.Helper()`.
- Use `t.Context()` for test lifetime and deterministic synchronization; never
  use `time.Sleep` as a concurrency assertion.
- A regression test must fail when the target behavior is deliberately broken,
  not merely exercise the happy path.

## Testability

- Construct commands and services per test; package-level command variables and
  global configuration instances leak state between runs.
- Use small fakes at the consumer boundary instead of generated mocks of the
  implementation. Filesystem seams should accept the capability they need.
- Reset caches and environment both before and after a test; parallel tests may
  not share mutable process state.

## Scalability

- Bound concurrency with `errgroup.Group.SetLimit` or an equally explicit
  owner. Hand-rolled worker pools need a demonstrated lifecycle requirement.
- Every channel send has a receiver/cancellation path. Treat goroutine growth,
  queue depth, and allocation changes as measured claims, not intuition.

## Integrability

- Adding a method to an exported interface is breaking. Check exported API
  changes with `apidiff`/`gorelease`, not source appearance alone.
- The `go` directive is a compatibility floor imposed on dependants. Never
  release with a local `replace`; retract a bad version rather than retagging.
- Preserve error identity through `%w` so callers' `errors.Is`/`As` contracts
  continue to work.

## Data safety

- File moves may cross filesystems; an atomic rename assumption must have a
  copy-and-fsync fallback or an explicit same-filesystem contract.
- Destination collision handling is deterministic and never overwrites by
  accident. Continue with the path an operation actually returned, not merely
  the requested one.
