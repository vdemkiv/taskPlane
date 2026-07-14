
# /tp-requirements — the spine of the knowledge base

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`.

- **Record:** `$TP req new "<title>" --functional "..." --acceptance "..."
  --nfr security=... --files "src/x/**"` — acceptance criteria become the
  DoD, so make them testable statements.
- **Score:** `$TP req score R-XXXX --files <changed>` — functional axis +
  NFR axis (the lens router decides which NFR axes apply). Present the
  gaps and the fix-cycle forecast; refining now is cheaper than building.
- **Refine:** re-record with `--changed-from R-XXXX` (same machinery as a
  change request); close open questions, state the missing NFRs.
- **Mode:** `$TP req mode --refinement <score> --size <files>` → quick
  (minimal change + tracked debt) vs full. Quick REQUIRES a debt record:
  `$TP req debt "<title>" --req R-XXXX --reason ... --follow-up ...`.
- **List:** `$TP req list` (includes open debt).

High-cost work below threshold hard-blocks at plan approval — that's
`req score --high-cost` territory; the human can `loop approve --force`.
