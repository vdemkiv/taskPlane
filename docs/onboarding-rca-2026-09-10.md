# Onboarding readiness incident — 2026-09-10

## Observed failure

Marketplace 2.23.0 was reinstalled in an existing Codex chat and repository.
The engineering-review request did not receive the visible onboarding sequence.
After setup restored the local launcher, onboarding reported 9/10 prerequisites
ready and `start_new_session`, while its dashboard said “Ready to go.”

The exact session's native receipt was last observed at 14:38:46 EDT and its
repository receipt at 12:30:31 EDT. Both described `PreToolUse` but had different
event fingerprints. Both belonged to the current chat; the repository record
also matched the current checkout. No unrelated folder or legacy engine was
needed to reproduce the failure.

## Causes

1. **Two competing definitions of event identity.** The hook entry point already
   called the shared `claim_hook_event` guard, but receipt recording separately
   hashed event fields. Readiness demanded equality between the two paths'
   latest hashes. Sequential delivery necessarily has an interval when one path
   has advanced and the other has not. Different event coverage or an inactive
   path can keep those records different indefinitely. Session retention made
   this visible after reinstall; deleting retained records would only hide it.
2. **Installation and runtime checks disagreed.** A retained native receipt could
   make the report ready even when uninstall had removed the launcher. The hook
   entry point itself requires that launcher. Onboarding also checked only the
   current checkout, while hook commands support its explicit Git-family path.
3. **Success was the dashboard default.** Rendering recognized only folder, Git,
   and initialization actions; every other action fell through to success.
   The text headline had a separate, incomplete action list.
4. **The on-ramp was scoped to a fresh repository.** Facade instructions and
   session context let existing knowledge stand in for first-use onboarding.
   The driver then ran technical setup without presenting the user-facing
   sequence and started a review with an unintended narrow target.

## Bounded correction

- Record the actual shared guard's claim identifier after claiming an event;
  use that receipt to establish stable event identity. Remove the second event
  hashing mechanism. Duplicate suppression, session/checkout isolation, and
  rejection of events without identity remain enforced.
- Require the launcher configuration as well as observed hook readiness. Use
  the same explicit Git-family fallback as the hook commands, and return the
  launcher setup action when reinstallation removed it.
- Use one setup-action vocabulary for both dashboard and headline. Success
  requires an explicit ready report and passing checks. Loaded hooks with no
  claim evidence get a connection check in the current task, not generic restart
  advice.
- Require visible onboarding before every session's first TaskPlane request and
  after installation/update, regardless of intent or retained repository state.
  Preserve the original goal through setup.

## Regression coverage and limits

Validation: 239 targeted tests passed across onboarding, host capabilities,
hook selection, native continuity, bootstrap, phase registry, extracted packages,
release freshness, enforcement, dashboard output, and worker lifecycle. Ruff and
type checking passed for the three changed production modules. The affected
hook-entry tests were rerun after the final change that prevents rejected events
from creating load receipts. The full repository suite and live EM review were
not run as part of this bounded repair.

Production hook-entry regressions exercise both delivery orders, readiness inside
the leading hook, replay of the duplicate, and the next unmatched event. Negative
coverage checks missing event identity and foreign sessions/workspaces. Reinstall
coverage removes and restores the launcher in an initialized repository; linked
checkout coverage verifies the Git-family fallback. Dashboard checks cover every
blocked action, unknown/missing actions, and a contradictory success report.

The old tests equating two directly written matching receipts with proof of
duplicate suppression were corrected or replaced by actual claim-path tests.
These are isolated simulated-host regressions, not evidence that the installed
marketplace package has been updated or that the paused engineering review ran.
The repair adds no state migration, cache purge, alternate guard, or automatic
restart loop. A real-host installation check remains necessary after publishing
the corrected package.

## Hook trust, stale review, and repeated Stop incident

Codex showed nine TaskPlane hooks requiring trust review with their switches
off. Installing the plugin did not authorize those hooks. Once enabled, the
retained read-only contract `task_1281e8b0` correctly refused shell work, but
it belonged to the earlier, unfinished EM review rather than this authorized
repair. The existing human-approved `clear` command released that exact root
contract; the review history remains unapproved. No other checkout was scanned
or cleared.

The dashboard had already been delivered, but `ack` printed success while its
write failed. Reproduction against this repository's external project store
confirmed `Operation not permitted` creating the ledger lock directory: hooks
could write outside the host sandbox while the agent command could not. The
shared best-effort telemetry writer swallowed that failure. Explicit ack and
file-delivery writes now require persistence and report the exact failing store.
A missing delivered file can no longer fall back to the expected fingerprint
and fabricate a zero-byte render. Relative delivery paths resolve against the
selected workspace. After authorizing the exact ledger write through the host,
a fresh status read confirmed one acknowledgment and zero binding obligations.

The Stop hook previously returned a blocking response on every invocation,
even when its stall detector knew no progress was possible. Artifact reminders
now respect Stop re-entry and stop retrying an unchanged obligation set,
including when reminder state cannot be saved. They ignore cleared contracts
and demands explicitly assigned to another task. Submission and completion
evidence gates are unchanged; ending a reminder does not approve a review.

Onboarding now directs the user to review, trust, and enable hooks before
suggesting initial session reload. Past execution receipts do not prove the
current trust or toggle state. This patch does not toggle host trust settings
or manufacture a native hook receipt. Hooks remain disabled during the repair.

CI also found that the facade scenario still recorded the previous flow
fingerprint after onboarding was inserted. The scenario retains its existing
human-gate and delivery expectations and now fingerprints the current source
through the canonical extractor; no functional CI gate was relaxed.
