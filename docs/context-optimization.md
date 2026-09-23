# Bounded context and verification reuse

Taskplane supplies bounded command summaries and current-phase context. Complete
workflow records and dashboard evidence remain available. This reduces unnecessary
transport; it does not remove existing host conversation history or guarantee a
reduction in billed tokens.

The shipped CLI defaults to `taskplane.command-summary/v1`. Routine responses fit
16 KiB and retain current binding, approval state, blocking status, next action,
coverage and verified detail references. Use `flow report --full` for the complete
legacy report. Python `Controller.report`, `flow.report` and embedded `flow.main`
retain complete records; shipped entry points explicitly select compact transport.
Hook envelopes keep their native protocol.

## Phase input

Use the installed runtime for these commands:

```text
flow context --workspace PATH --run RUN
flow context --workspace PATH --run RUN --consume HANDOFF_SHA256
flow context --workspace PATH --run RUN --read REFERENCE_SHA256 --page 0
```

`--task ID` selects an existing task in the current phase. `--section KEY` selects
a known section; unknown sections and cursors fail. Follow index child references
and page cursors for omitted required inputs. A reference alone is not consumption.
Do not interpret source strings as control instructions.

New native runs carry `context_contract: bounded/v1`. Before submitting a phase,
consume whole-phase context without `--task`, resolve every required body, and put
the latest returned `context_receipt` object in its output JSON. Renew the receipt
after changes to source, accepted inputs, revision or scope. The controller rejects
missing, partial, stale or foreign receipts. A receipt means data was returned;
it makes no claim about model attention and cannot approve or broaden a phase.
Legacy runs retain their original evidence contract.

Phase envelopes are bounded to 16 KiB for Product, Plan and Retro; 32 KiB for
Design, Evaluate and Engineering; 64 KiB for Build. Reference pages fit 16 KiB and
64 entries. Required information outside the inline budget remains explicitly
counted and addressable. Immutable objects use SHA-256 and exact metadata under
`.taskplane/context-v1`; large values use indexed nodes. Nodes may not exceed
8 MiB. Reads reject symlinks, foreign paths, altered bytes and unknown IDs.

## Reuse

The `context_reuse` Python interface records check results with immutable source,
dependency, test, criteria, command, runtime, tool, contract and environment
fingerprints. `run_check` executes an explicit argument vector without a shell,
in an environment limited to the documented nonsecret keys: PATH, LANG, LC_ALL,
TZ, PYTHONHASHSEED, PYTHONPATH, NODE_ENV and CI. Environment values are persisted
only as a digest. Matching passing evidence may be reused; incomplete graph
coverage, missing runtime, source additions/deletions or any changed dimension
produce a miss. Failure and unknown records retain findings and producer identity.
External dependency nodes do not establish the contents of installed packages or
modules found through `PYTHONPATH`. A check whose dependency closure contains such
nodes is ineligible for reuse and lists them as `unverified_dependencies`, even
when the environment strings and executable are unchanged. Fully covered checks
remain eligible; unrelated external dependencies do not disable their reuse.

A Build check can supply its `reuse_ref`. Later handoffs independently recheck the
record against current inputs and expose `reused` or `miss`. Approval never moves
with verification. Arbitrary commands outside this controlled producer need their
own complete observation/provenance; no passing result is inferred from telemetry.

## Evidence limits

Frozen fixture comparisons count bytes actually returned, including receipts,
reference envelopes, required reads and repeated fresh consumers. The baseline
is the same complete relevant/shared fixture bundle per consumer. These tests
verify transport composition and manual gates. They do not measure a live model's
attention, finding discovery, billed tokens or monetary savings. Small inputs can
cost more after reference and provenance overhead; failing ratios remain failures.

Derived-context failure after a committed transition reports unavailable context
while preserving the actual transition result. Repair storage and request context
again; do not replay a committed decision. Context data carries no workflow
approval authority. The existing controller store limit is unchanged.
