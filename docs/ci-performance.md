# CI coverage and context delivery performance

The test runner keeps a full local suite by default:

```sh
python scripts/ci_local.py --check tests --junitxml /tmp/taskplane-tests.xml
```

It isolates Codex, Claude and Taskplane environment/session data. Every test run
prints the 25 slowest durations. `--junitxml` writes a machine-readable result even
when tests fail; the runner preserves pytest's failing exit code. GitHub Actions
uploads a separately named result for every suite/platform/host job, including
failed jobs. The retained platform status checks require all suites and supporting
quality, browser and interpreter jobs to succeed. The full storage stress run is
separate from routine CI; it has its own required pass/fail result when invoked.

## Suite ownership

| `--suite` | Tests | CI matrix |
| --- | --- | --- |
| `all` | Every non-browser test; default local behavior | Use locally for integration |
| `portability` | Context, workspace binding, worker lifecycle, path/relay and verifier regressions | Linux and Windows |
| `core` | All other ordinary tests, including package metadata/instruction checks | Four disjoint shards per OS |
| `native` | Native workflow tests, including eleven-checkpoint repair history with small evidence | Two disjoint shards per OS; both host adapters |
| `packages` | Member parity, isolated Workflow Builder, declared hooks and one complete native journey | `--host codex` and `--host claude` on both OSs |
| `capacity` | Full repeated-repair storage stress fixture | Separate weekly/manual workflow: Linux/Codex, Linux/Claude, Windows/Codex |

Portability, core, native and archive jobs start independently. The aggregate gates
still require all of them; a portability failure cannot become a successful CI run.
Superseded branch runs are cancelled. Test jobs have a 15-minute timeout, and the
separate capacity jobs have a 20-minute timeout. Timeouts fail the job.

Coverage has an explicit owner. The exact approval-language regression corpus runs
against the parser; representative controller cases check atomic refusal, blocked
advancement, persisted decisions and exact response provenance. Archive checks
compare every member with source and execute extracted imports, Workflow Builder,
declared hooks, standalone activation and a seven-phase journey. Exhaustive policy,
recovery, prompt-routing and dashboard variants run in the source suite instead of
being repeated inside each of four archive jobs. Unsupported Claude transport still
refuses on Windows; a fixture pass does not certify a live Claude session.

Routine native tests retain all eleven checkpoints, two repair routes, unchanged
prior packets/decisions and legacy-store migration with small evidence. Core tests
check compact byte limits at limit-1, limit and limit+1. The original stress fixture
retains 3000 structured records per checkpoint and the actual 8 MiB crossing. Run
**Storage capacity stress** manually before a release or a storage/context change;
it also runs Mondays at 05:23 UTC. A routine CI pass is not a full-capacity result.
The default local `all` suite still includes both full-capacity host parameters.

The delivery fixture uses POSIX paths for graph and verification keys, and native
`Path` strings for its exact write scope and change inventory. This matches each
existing runtime boundary on Windows without broadening the scope check.

Examples:

```sh
python scripts/ci_local.py --check tests --suite portability --junitxml /tmp/portability.xml
python scripts/ci_local.py --check tests --suite core --shard-index 1 --shard-count 4
python scripts/ci_local.py --check tests --suite packages --host claude --junitxml /tmp/claude.xml
python scripts/ci_local.py --check tests --suite capacity --host codex --junitxml /tmp/capacity.xml
python scripts/ci_local.py --check package --package-output /tmp/taskplane-packages
```

Host selection is permitted only for package/capacity suites. Invalid combinations
fail before running tests. Collection tests verify the full partition, expected
capacity/archive cases, exact host filtering and exhaustive/disjoint shards. New ordinary tests belong to
core by default; browser tests remain in the explicit browser check. Pytest's
`--taskplane-collection-report PATH` writes the complete suite/host/selection map
for diagnosis.

## Bounded required-page batches

Prepare and consume a current handoff normally. When required bodies remain,
consumers can request a batch instead of launching one command for each page:

```text
flow context --workspace PATH --run RUN
flow context --workspace PATH --run RUN --consume HANDOFF_SHA256
flow context --workspace PATH --run RUN --read-required HANDOFF_SHA256
```

Repeat the last operation while `remaining_required` is positive, retaining every
returned body. `taskplane.context-read-batch/v1` includes `handoff_ref`, `pages`,
`context_receipt`, and `remaining_required`. Responses stay below 16 KiB and contain
at most 64 existing page envelopes. A batch shares one freshly validated Session;
prepared input references are reused within that Session. There is no persistent
Session cache or time-based freshness exception. Existing single-reference reads
remain supported.

The current Controller/run/revision checks, immutable-object verification, required
body coverage and approval boundaries remain in force. A stale handoff refuses.
Candidate pages are counted as delivered only after the full response fits its
budget; a corruption/overflow refusal cannot advance the delivery ledger. A receipt
proves returned data, not model attention or approval.

## Initial task isolation

`flow start --tasks FILE` validates a bounded task DAG against the exact requested
scope and snapshots its normative definitions with that run. Progress observations
are excluded. A retry cannot replace that snapshot. If a run began without tasks,
a retry may refresh the dashboard with scoped task rows, but its empty context
snapshot stays unchanged. Accepted phase packets provide later task definitions. Without a snapshot, context falls back to that run's own
scope instead of using an unrelated `.taskplane/tasks.json` left by another run.

## Measuring changes

Compare identical semantic input payloads, record counts, host/Python environments
and required-body completion. Record preparation, delivery, hook/CLI process time,
command/process counts, returned bytes and repeated fresh-workspace samples.
Separate batching improvements from reduced duplicate tests and parallel CI jobs.

The repair workload uses the original 3000 records (253891 UTF-8 JSON bytes),
with payload SHA-256
`f2f57dffb07f90c7eb57563d5df0f5d29022ea2fcc7c9cf65992007507c878de`.
The unchanged macOS/Python 3.13 baseline required 65 context commands and 195
declared-hook/CLI processes, with a median of 56.516 seconds over three samples.
The repaired median is 15.697 seconds (72.2% lower), using
26 commands and 78 processes (60% fewer). All three samples complete both required
inputs. Returned bytes decrease from 318152 to 307105 (3.5%); this is primarily a
process/startup improvement. It measures complete delivery of one inherited large
context, not an entire workflow or GitHub Actions run. Do not infer billed-token
savings or Windows speed from these local timings.

Remote Linux and Windows success must be recorded for the exact repair/release
commit before calling it release-ready. A passing local suite or fast shard alone
does not establish that result.
