# R-0013 exact-candidate terminal export

`106af4631ab5b5c041055b9b9b918d78a18ae50b.json` is immutable historical
evidence. Its original bytes and filename are preserved. The separate
`106af4631ab5b5c041055b9b9b918d78a18ae50b.tombstone.json` binds that exact
filename and SHA-256 fingerprint to the fixed supersession reason; it never
replaces or rewrites the projection.

`successor-template.json` is the deterministic contract for the next
candidate. A commit cannot contain its own Git object id, so the exact SHA is
resolved only after the candidate is committed. `verify.py` obtains the SHA
through a content-bound Git executable and closed environment, includes
untracked files in its cleanliness check, rejects external or symlinked
template/evidence paths, and rechecks HEAD before and after composition.
The terminal coordinator executes the H3-D selectors and persists their
content-addressed receipts; callers cannot submit result dictionaries.

The coordinator is the production consumer for this prepared successor, but
the successor remains intentionally non-authoritative. It records that no new
full-suite receipt exists and that release, main mutation, and publication
authority were not granted. FINAL-I may mint exact terminal authority only
after the real final evidence is available.
