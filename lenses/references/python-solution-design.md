# Python Solution Design

Target language version: **Python 3.14**.

Sources and attribution: [SOURCES.md](SOURCES.md).

- Choose synchronous or asynchronous ownership per call path. If async is
  selected, name cancellation propagation, task ownership, load bounds, and
  the `ExceptionGroup` failure contract before implementation.
- Put protocols at consuming boundaries and keep runtime validation at trust
  boundaries. Static typing does not validate JSON, environment variables, or
  persistence data.
- Separate domain behavior from framework/application wiring. Import-time
  settings, global clients, and service locators erase test and lifecycle
  boundaries.
- Define packaging as part of the design: import namespace, public surface,
  runtime dependencies, development dependency groups, lock policy, wheel
  contents, and supported Python floor.
- For free-threaded execution, identify mutable shared state and extension
  compatibility explicitly; do not infer safety from code that happened to run
  under a GIL-enabled build.
- Design DoD includes strict type checking, cancellation/failure tests,
  clean-wheel installation, and graph verification of import/package edges.
