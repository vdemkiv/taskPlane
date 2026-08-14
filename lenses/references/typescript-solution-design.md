# TypeScript Solution Design

Target language version: **TypeScript 7.0**.

Sources and attribution: [SOURCES.md](SOURCES.md).

- Select the module host first: Node, bundler, or library distribution. Align
  `module`, `moduleResolution`, package `type`, file extensions, exports, and
  declaration output with that host.
- Draw package and project-reference boundaries around runtime environments and
  coherent capabilities, not directory aesthetics. Define public entry points
  and forbid private-subpath imports.
- State where runtime validation occurs and which layer may mint trusted or
  branded values. Static types do not cross process or persistence boundaries.
- Define async ownership: cancellation source, `AbortSignal` propagation,
  concurrency bounds, stale-result policy, and unhandled-rejection behavior.
- Compare exception-based and `Result`-style failure at the codebase boundary;
  half-adopting both creates two call contracts and is worse than choosing one.
- Design DoD includes type-aware linting, project-reference build checks,
  consumer resolution of emitted declarations, graph edge verification, and
  runtime validation tests.
