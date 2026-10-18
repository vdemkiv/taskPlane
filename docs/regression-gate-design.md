# Verification and review discipline

Choose checks that can fail for the relevant behavior. Plan records command,
inputs, required environment and criterion associations. Run affected checks after
meaningful changes; once they pass, repeat only for changed inputs, failures or a
specific uncovered case. No permanent test should mirror deleted machinery merely
to prove its absence.

Each check attempt has unique identity, declaring Plan, producing visit/phase,
observed pass/fail/unknown, immutable log/record references and relevant source/
test/command/environment fingerprints. Evaluate can add fresh evidence without
rewriting accepted Build history. Later consumers select the newest applicable
result, retaining fail/unknown rather than falling back to an old pass. Missing
capability is unknown with a reason; acceptance does not turn unknown into pass.

Review actual source, dependencies and requirements. Distinguish actionable
confirmed defects, uncertainty and uncovered areas. Record severity/location,
concrete trigger and failure. Root integrated review is valid; useful independent
review remains optional unless explicitly requested/frozen. Report actual
reviewers and independence without a lens quota or coverage ledger ceremony.

When retiring code, remove its obsolete assertions, helpers, fixtures, parser
flags, imports and CI node IDs. Preserve useful behavior from mixed files or move
it first. Do not grow skip lists to hide deleted scenarios. Local fixtures,
extracted-package checks and live installed-host acceptance are different kinds
of evidence and must remain labeled.
