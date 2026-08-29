# R-0002 remediation Retro

## What worked

- Pairwise-disjoint test sharding reduced the aggregate critical path to the
  slowest shard rather than the 8,885 seconds of summed pytest time.
- Independent evaluation caught a verifier weakening before it could be
  silently resealed.
- Preserving green receipts and rerunning only affected selectors avoided a
  second repository-wide pass.
- Extracting the audit projection into a dependency-neutral leaf removed LOC
  pressure without weakening retention or privacy behavior.

## What caused avoidable delay

- Production fixes, fixture updates, trust pins, and strict module inventories
  were not stabilized before the first aggregate run.
- A package-style test import created a second `depgraph` module identity and
  overwrote a process-global registration seam.
- The shard digest prompt incorrectly said “trailing newline” even though the
  canonical digest omits it.
- One zsh worker passed a newline-joined manifest as one argument; it collected
  zero tests before the corrected array invocation.
- Self-hosting the engine while using the same engine as the delivery harness
  repeatedly mixed product defects with harness defects.

## Durable changes

- Fixture-first validation is now treated as part of the production slice.
- New top-level modules must enter the strict quality inventory in the same
  commit.
- Cross-module registration tests use one canonical import identity.
- Trust-baseline changes require an exact old/new blob packet and explicit
  human authority.
- A failed aggregate run triggers classification and affected-selector closure,
  not automatic repetition of every green test.

## Remaining accepted debt

- Thirteen high selector-receipt findings remain attributed, non-independent
  exceptions.
- Two high retention findings remain attributed, non-independent exceptions.
- Eighty-three top-level modules remain in the staged strict-typing debt set;
  the six strict modules and all newly introduced modules remain fail-closed.
- Strict AC8 lacks a complete exact-final-SHA aggregate receipt by explicit
  selective-rerun direction. Any later release gate may choose to require that
  receipt, but this remediation does not synthesize it.
