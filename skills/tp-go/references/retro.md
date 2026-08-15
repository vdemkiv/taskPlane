
# /tp-retro — the track teaches the next track

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Human
sign-off moves the loop to `retro`; run it there (or after abort — failed
runs teach the most). The loop is not `done` until this stage seals.

1. `$TP loop retro` — one idempotent engine action computes refinement-
   forecast accuracy, hook denials, waves, per-task fix cycles, selective lens
   routing and final finding ownership; refreshes and fingerprints the
   dependency graph; writes the report and KB decision; then moves the loop
   to `done`. A retry returns the sealed report without rescanning or creating
   another decision.
2. Read the report WITH the user and go one level deeper than the
   mechanics: which lens caught the expensive finding late (→ move it to
   refinement as an NFR next time)? Which scope was wrong? Was quick/full
   the right call — check the debt list.
3. Turn each lesson into an artifact, not a vibe: a KB decision
   (`$TP kb record`), a requirement change, a catalog/lens tweak, or a
   context-doc update. A lesson that isn't retrievable is lost.
4. Finish per `discipline/finishing-work.md` (record any chosen debt and
   `$TP track close`). The graph rescan is already part of the sealed Retro;
   do not pay for it twice.
