# Python Engineering References

Target language version: **Python 3.14**.

Sources and attribution: [SOURCES.md](SOURCES.md). Guidance is original text
checked against Python 3.14 and PyPA specifications.

## Scalability

- Use `asyncio.TaskGroup` for owned concurrent work and a semaphore or bounded
  queue for load control. Unstructured `create_task` requires a retained task
  reference, failure observation, and shutdown owner.
- Never block the event loop; move genuinely blocking work through
  `asyncio.to_thread` or a process boundary. Reuse clients and connection pools
  instead of creating them per request.
- `TaskGroup` failures arrive as exception groups. Handle the intended members
  with `except*` and preserve cancellation.

## QA

- Guard against false-green tests: conditional assertions, presence-only
  checks blind to duplicates, mocks of the unit under test, and fixtures whose
  values make correct and buggy behavior equal.
- Regression tests prove both directions: fail with the defect restored and
  pass with the repair. Disable unexpected network access in unit tests.
- Verify built wheels by listing contents, installing into a clean environment,
  and importing a real symbol from outside the repository root.

## Testability

- Treat CWD, `HOME`, `XDG_CONFIG_HOME`, environment variables, module caches,
  and import order as inputs. Restore every process-global mutation.
- Do not construct settings or clients at import time; collection must succeed
  before fixtures exist. Use consuming-side `Protocol` seams for boundaries.
- Prefer `ExitStack`/`AsyncExitStack` when resources are dynamic and verify that
  `__exit__` does not accidentally suppress exceptions by returning truthy.

## Packaging and DevOps

- Keep runtime dependencies in project metadata and development tools in PEP
  735 `[dependency-groups]`; extras describe optional product capabilities,
  not the developer toolchain.
- Commit the repository's chosen lock format and verify its hashes. A successful
  build is insufficient: inspect and clean-install the wheel.
- Scan dependencies and CI configuration separately from source (`pip-audit`,
  workflow validation, and secret scanning serve different boundaries).

## Integrability

- Public types model stable contracts: use `NewType` for primitive identities,
  discriminated unions plus `assert_never` for closed states, and `Protocol`
  for structural consumer contracts. Use an ABC when runtime inheritance and
  shared implementation are actually required.
- `@overload` variants must match one runtime implementation. Type-only imports
  protect optional dependencies and import cycles but cannot hide runtime use.
- Do not ship both `name.py` and `name/`; verify import identity from the built
  artifact rather than the source checkout.

## Security

- Parse untrusted formats with safe loaders and avoid `pickle`, `eval`, shell
  execution, and user-controlled format strings. Validate path containment
  after resolution.
- Dependency vulnerability checks, secret scanning, and source analysis are
  separate controls; a clean Bandit result does not establish supply-chain
  safety.

## SRE

- Cancellation is operational control. `CancelledError` is a `BaseException`;
  if explicitly caught for cleanup, re-raise it. Do not swallow cancellation
  inside structured concurrency.
- Preserve exception causes with `raise ... from ...`. Logging formatters must
  not mutate a shared `LogRecord` seen by later handlers.
- Bound network timeouts and resource pools explicitly and expose saturation,
  retry, and exception-group outcomes as structured signals.
