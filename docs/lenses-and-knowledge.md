# Lenses, focused routing, and durable knowledge

Taskplane keeps review perspectives separate from workers. A lens is one of 26
catalogued perspectives with a distinct charter; a route is a versioned,
evidence-backed decision about which perspectives need independent work for one
stage and target.

## Complete disposition, focused execution

Product, Design, Plan, and Evaluate each produce exactly one disposition for
all 26 lenses:

- `execute_deep` — dispatch a dedicated producer when a separately authorized
  deep/audit route calls for it;
- `execute_light` — dispatch a bounded quick producer;
- `covered_by` — dispatch nothing and name the selected lens whose evidence
  covers the same material risk;
- `not_applicable` — dispatch nothing and record machine-readable negative
  evidence.

Only `execute_deep` and `execute_light` create work. Missing, duplicated,
unsupported, cyclic, or unevidenced rows fail closed. Normal delivery is
quick-only: Product and Design choose a minimum-sufficient focused route, while
every non-trivial Plan and Evaluate runs exactly 3–4 quick lenses. Build and
Fix launch zero lens workers; final engineering synthesis consumes canonical
Evaluate evidence rather than rerunning the catalog.

If more than four independent mandatory Plan/Evaluate risks remain, Taskplane
does not silently drop or demote one. It proposes deterministic scope splits or
stops for an exact expanded-route approval. Expanded authority is protected by
a separately executed content-addressed provider, external 0600 custody,
authenticated exact-target approval, expiry, and atomic one-use consumption.

## Deterministic routing and selective reuse

`taskplane/lens_signals.py` derives bounded semantic evidence from the target.
`taskplane/lens_route_policy.py` validates the 26-row catalog, groups overlapping
risks, applies mandatory floors, orders independent risks deterministically,
and fingerprints the complete decision.

Each selected lens also receives a `lens_input_fingerprint` over only its
relevant acceptance, design, change, impact, test, finding, catalog, and policy
inputs. After Fix, no lens runs while editing. Evaluate recomputes the route,
reuses sealed passing evidence whose input fingerprint is unchanged, and
dispatches only invalidated selections. Prior failure, changed input, stale
policy, missing result, or invalid provenance always invalidates reuse.

## Bounded route telemetry

The private `taskplane.lens-route-telemetry/v1` record contains stage,
pseudonymous target, selected count and bounded reasons, estimated and actual
tokens, runtime, cache reuse, invalidation cause, terminal status, and route
fingerprint. Reasons are limited to 512 UTF-8 bytes, paths are
repository-relative, raw content is represented by SHA-256, and each artifact
is capped at 128 KiB. Telemetry is governance evidence stored with local or
team Taskplane state; it is not remote product analytics.

## Knowledge that survives the run

The machine trace records what happened. The knowledge store records why:
requirements, accepted decisions, debt, and reusable flows. Personal plans use
the external per-project store under `~/.taskplane/projects/<key>/`; Team and
Enterprise plans use the repository's `.taskplane-kb/` store. Retrieval is
bounded by files, graph components, requirement links, and tags so workers see
relevant settled decisions without reopening the entire history.

High-signal gates capture durable decisions. Superseded decisions stay linked
instead of being rewritten, and private run artifacts remain external unless a
human explicitly publishes them to a shared store.
