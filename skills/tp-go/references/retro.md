
# /tp-retro — the track teaches the next track

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`. Run at `done` (or
after abort — failed runs teach the most).

1. `$TP loop retro` — the engine computes: refinement-forecast accuracy
   per task (did low scores predict the fix cycles?), hook denials (scope
   friction), waves, per-task fix cycles; lessons auto-record to the KB.
2. Read the report WITH the user and go one level deeper than the
   mechanics: which lens caught the expensive finding late (→ move it to
   refinement as an NFR next time)? Which scope was wrong? Was quick/full
   the right call — check the debt list.
3. Turn each lesson into an artifact, not a vibe: a KB decision
   (`$TP kb record`), a requirement change, a catalog/lens tweak, or a
   context-doc update. A lesson that isn't retrievable is lost.
4. Finish per `discipline/finishing-work.md` (debt, graph rescan,
   `$TP track close`).
