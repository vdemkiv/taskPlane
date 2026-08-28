# R-0013 exact-candidate terminal export

The former `106af4631ab5b5c041055b9b9b918d78a18ae50b.json` projection is a
superseded historical candidate, not the current integration result. Its
tracked replacement is an explicit tombstone so a consumer cannot mistake it
for live terminal evidence.

`successor-template.json` is the deterministic contract for the next
candidate. A commit cannot contain its own Git object id, so the exact SHA is
resolved only after the candidate is committed. `verify.py` then requires the
resolved SHA on all eight terminal surfaces and every H3-D selector receipt.
Any stale SHA, missing surface, missing selector, failed selector, or changed
digest is rejected.

The prepared successor is intentionally non-authoritative. It records that no
new full-suite receipt exists and that release, main mutation, and publication
authority were not granted. Final integration may mint the authoritative
terminal bundle only after the real exact-candidate evidence is available; it
must never rewrite the tombstoned history.
