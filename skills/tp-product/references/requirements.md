
# /tp-requirements — the spine of the knowledge base

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`.

- **Record:** `$TP req new "<title>" --functional "..." --acceptance "..."
  --nfr security=... --files "src/x/**"` — acceptance criteria become the
  DoD, so make them testable statements.
- **Score:** `$TP req score R-XXXX --files <changed>` — functional axis +
  NFR axis (the lens router adds contextual axes; Product always requires
  explicit `security` and `architecture` statements). Its readiness result is
  the same Product DoR used by sign-off, so “proceed” cannot precede refusal.
- **Refine:** amend the same record with `$TP req amend R-XXXX ...`; close
  open questions and state the missing NFRs. `req new` prints the absolute
  external-store file for reading, but the CLI is the authoritative mutation
  path—do not create a similarly named file in the repository.
- **Mode:** `$TP req mode --refinement <score> --size <files>` → quick
  (minimal change + tracked debt) vs full. Quick REQUIRES a debt record:
  `$TP req debt "<title>" --req R-XXXX --reason ... --follow-up ...`.
- **List:** `$TP req list` (includes open debt).

High-cost work below threshold hard-blocks at plan approval — that's
`req score --high-cost` territory; the human can `loop approve --force`.
