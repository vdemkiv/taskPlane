# Review entry and recovery failure — 2026-09-14

## Impact and scope

A request to review the last three commits stalled after review activation.
The read-only screen rejected the engine's own review-option command, a native
human-input tool and dashboard presentation. Approved budget recovery did not
result in a completed review. The original review remains incomplete.

A separate Claude report showed completed setup (`ready: true`) followed by
`review start` refusing unproven screen enforcement. It recommended
`/reload-plugins` from a Claude session ID alone and proposed moving out of
Cowork without establishing that the current host could not execute hooks.

## Confirmed causes

1. **Control commands fell into the source-execution gate.** The early native
   read-only branch rejected trusted `review option` before its handler could
   validate the user's option receipt. The existing unmetered exemptions did
   not cover ordinary metered review continuation or human questions. This
   reproduces from the current source with usage below the ceiling; it is
   independent of an exhausted token budget.
2. **Onboarding used different readiness criteria across hosts.** Live hook
   checks were inside a Codex-only branch. Claude could report ready from
   workspace setup alone, while review correctly required live enforcement.
3. **Claude's session identity stopped at the hook subprocess.** Storage binds
   hook events to their session ID, but subsequent Bash commands were never
   given that ID through `CLAUDE_ENV_FILE`. A cold-start reproduction shows
   that the identity handoff must precede the uninitialized-project shortcut.
   Identity propagation is separate from proof that enforcement executed.
4. **Loading diagnostics could conceal stale code.** Launcher readiness checked
   whether its family resolved, not whether its generated body was current.
   Runtime receipts did not identify the engine that executed the hook, so an
   old loaded engine could appear to prove readiness for a different command
   engine in the same session.

## Correction

All direct skills already reference the common entry initializer. The shared
screen now recognizes one canonical engine invocation for metered review and
phase controls, applies command-denial and output/workspace checks, and
leaves human input, approved recovery and exact owed-artifact delivery reachable
at the ceiling. It does not acknowledge or invent rendering evidence.

Both hosts now require live hook observations in onboarding and expose completed
setup separately. Claude's actual SessionStart path carries its session ID into
the host-provided Bash environment, including before setup. Real subsequent
tool events establish readiness. Direct CLI invocations create neither a native
receipt nor a host environment handoff.

Hook receipts now identify the executing engine. Onboarding exposes command
engine path, version and fingerprint; a different or unrecorded hook engine is
unproven until the host loads the selected plugin and executes a fresh hook.
Initialization refreshes an existing stale project launcher without replacing
the review run. Recovery guidance distinguishes Claude Code from an otherwise
unidentified Claude/Cowork session and preserves the pinned scope.

The common entry instructions also preserve requested commit scope through
transfer/recovery, distinguish additional token grants from total ceilings,
reuse approvals already given, and permit routine cleanup of task-created
transfer files under the task's existing authorization.

## Evidence and limits

Regression coverage exercises the real screen subprocess, both native startup
hook commands, subsequent Claude shell initialization, exact-session isolation,
engine mismatch, stale-launcher repair, budget grants and artifact delivery.
Tests use disposable projects and host state.

Validation completed: 372 focused tests and 18 subtests passed across the
recovery, host integration, onboarding, session isolation and worker-lifecycle
suites. Ruff passed; strict mypy reported no issues in 114 source files.
Both package validators passed. Unpacked Codex and Claude artifacts passed
isolated cold-start, live-readiness, human-input and budget-recovery smoke
checks, covering all 9 Codex and 10 Claude direct skill entry points. Those
host inputs are simulated; they are not live-host release evidence.

The original installed UI version versus the observed `2.23.7` hook/cache path
cannot be resolved from the now-removed installation. A cache path alone is not
proof of which version the UI showed. The Claude report supplies no host logs;
the session-handoff gap is reproduced locally, not asserted as the sole cause
of that remote incident. Missing evidence does not establish that Cowork cloud
cannot run hooks.

Claude's supported environment handoff is documented in its
[hooks reference](https://code.claude.com/docs/en/hooks#persist-environment-variables).
Source fixes and local tests do not constitute deployment to an installed
plugin or a completed engineering review.

The subsequent direct EM pass covered the latest three commits and these
fixes through code quality, integrability, scalability, and testability;
security was deferred at the user's request. Its broader suite exposed an
installed-package test that still expected full readiness immediately after
setup. Version 2.23.9 corrects that expectation to require completed setup
first, retaining the later runtime-readiness checks. The corrected journey
was verified for both extracted package layouts in an isolated copy before
applying the same test correction to the source. This is not full release
approval or a live-host result.
