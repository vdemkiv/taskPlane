# TypeScript Engineering References

Target language version: **TypeScript 7.0**, including TypeScript 6.0 default
and removal changes.

Sources and attribution: [SOURCES.md](SOURCES.md). Guidance is original text
checked against TypeScript and typescript-eslint documentation.

## Integrability

- Match `moduleResolution` to the runtime: `bundler` for bundler-owned apps;
  `nodenext` for Node execution and libraries whose emitted declarations must
  resolve for Node consumers. Package `type`, file extensions, imports, and
  exports must describe the same runtime format.
- Treat package root exports as the public contract. Avoid barrel files that
  hide cycles and accidental public surface; test through published entry
  points.
- Runtime validation and static types solve different problems. A brand or
  external DTO is trusted only when minted by a validator, not an assertion.

## DevOps

- Use project references for distinct runtime/test/package environments and
  `tsc --build` for ordered incremental builds. Every referenced project has a
  deliberate output and clean boundary.
- TypeScript 6+ deprecates legacy module resolution and module targets; do not
  carry `node10`, `classic`, AMD/UMD/SystemJS, or `outFile` forward as defaults.
- A build command is a trust boundary: review config changes, output paths, and
  clean commands before running them in privileged workspaces.

## Scalability

- Investigate checker cost with `--extendedDiagnostics`, `--generateTrace`,
  and `--explainFiles` before adding skip flags. Project references should
  reduce memory and rebuild scope, not merely mirror folders.
- Prefer named interfaces for reusable object shapes and explicit return types
  on exported APIs to reduce declaration expansion. Break import cycles rather
  than masking them with barrels.
- Typed linting has a real program-build cost; scope project service correctly
  instead of disabling safety rules across a monorepo.

## Architecture

- A package owns a coherent capability and explicit public entry point.
  Cross-package imports follow dependency direction and never reach private
  subpaths.
- Separate server, browser, worker, and test compiler environments when their
  globals or module hosts differ. Connect them with project references and
  contracts, not one permissive tsconfig.
- Reject pass-through modules with no policy, translation, or lifecycle role;
  use the deletion test to identify decorative abstraction.

## Frontend async

- Propagate `AbortSignal` through every layer that owns cancellable work and
  combine independent owners deliberately. Ignore/void is not cancellation.
- Enable both `no-floating-promises` and `no-misused-promises`: promises in
  conditions, event handlers, and `forEach` callbacks are different defects.
- Preserve causal errors with `Error.cause`; define UI loading, empty, error,
  cancellation, and stale-result behavior rather than handling only rejection.

## Security

- Treat external data as `unknown` until runtime validation. Type assertions do
  not validate network, storage, environment, or message payloads.
- Review generated output paths and build-clean commands before execution;
  `tsc` configuration is executable build policy, not inert metadata.
- Parameterize URL, SQL, HTML, and shell boundaries and prohibit unchecked
  double assertions at trust boundaries.
