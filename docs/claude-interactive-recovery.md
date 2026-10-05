# Interactive Claude worker recovery

The 2.32.1 interactive incident differs from the previously tested headless
worker lifecycle. The observed Claude Code 2.1.289 session used `auto` permission
mode, which exposes `SubagentHandback`. Its workers can
emit several statusless stop observations while enforcing report delivery.
If delivery never happens, the host sends a terminal notification saying that
no report was delivered. That is evidence of unsuccessful termination, not the
worker's result text.

Recovery preserves these boundaries:

- Execute returned claim/context commands exactly. Pipes, redirects, truncation
  and shell chains are not startup-control commands. After a rejection, retry
  the exact command identified by the diagnostic.
- A bound worker may report startup failure to its implicit parent. This does
  not satisfy claim, context, readiness or successful result acceptance.
- A statusless stop alone does not establish report delivery. Correlate native
  launch identity, stop events and the parent completion notification.
- In the observed auto-mode contract, `SubagentHandback` returns a delivery
  acknowledgment and sends the report as a separate native peer message between turns or as a
  system attachment during the parent turn. The
  stop has no report text, and completion refers to that child's message. Join
  the exact admitted input, acknowledgment, pinned peer report, stop and redirect
  by child, runtime and event order. A success flag or redirect alone is insufficient.
  Keep worker timing separate from parent delivery timing: admission precedes the
  acknowledgment, which precedes stop; stop precedes the completion's enqueue
  timestamp. The peer report may reach the parent after both stop and that enqueue
  timestamp. Its verified transcript span must still precede the completion span,
  and its timestamp must fall between admission and the current observation.
  Partial proof remains pending. Complete rejected redirect proof records a
  `redirect_join_rejected` diagnostic without accepting a result.
- Preserve an unsuccessful terminal attempt and prepare a fresh grant. Never
  relabel an unknown/live attempt or a missing report as an accepted review.
- The next attempt must claim, consume context and establish automatic hook
  readiness before the remainder of the native cohort can launch.

During an active Claude run, bounded `ToolSearch` requests can select `SendMessage`,
`TaskStop`, or both (`query: "select:SendMessage,TaskStop", max_results: 2`). This
read-only discovery grants no target authority. Other tools, free-text searches,
duplicate selections and larger result limits remain unsupported by this adapter.

The root may send a bounded plain-text message using `SendMessage` with `to` set
to the exact native worker ID, `message`, and an optional `summary` of at most
200 characters. `TaskStop` accepts only `task_id` set to that same exact ID.
The worker must belong to this root, run, current binding and attempt, with fresh
launch identity and matching automatic root/runtime proof. Foreign sessions,
display names, broadcasts, remote/shell targets, structured approval/shutdown
messages, subscriptions and ended attempts are refused. A worker can discover
these tools after startup but cannot operate them on a sibling or parent; it uses
its implicit-parent handback contract to return results.

A stop request marks the attempt `cancel_requested`. Its response alone cannot
prove termination, accept a report or release ownership. Preserve the attempt and
wait for the supported independent native terminal evidence. Sending new input
to a cancelled attempt is refused. Host discovery or permission may still report
a tool unavailable; retain that limit instead of substituting another target or
serial root work.

`scripts/verify_claude_interactive.py` prepares a disposable candidate and review
project, launches the real terminal UI, and inspects native evidence. It refuses
headless fallback. The scenario reports a deliberate startup failure, retries
with a fresh worker, exercises a refused wrapped claim followed by the exact
valid command, and requires two independently accepted useful reviews.

The script matches that `auto` permission mode, uses normal scoped tool permissions and forwards automatic hook
events to the unchanged candidate implementation. Only hook capture wrappers
are added to the disposable plugin copy. Original source hashes, staged hashes,
native transcripts, automatic hook records and unsuccessful attempts are kept.
Its observations are cooperative evidence, not host attestation.

Prepare with `--prepare --output REPORT`, launch with `--launch --fixture DIR`
in a terminal, and inspect with `--inspect --fixture DIR --output REPORT` after
the native workers finish. Each source revision needs a fresh prepared fixture.
An interactive pass must include actual handback tool use; a headless pass or a
production-shaped event replay is separately labelled coverage.

The first terminal probe used `dontAsk` and correctly reported handback as
unavailable. That run is retained as a failed validation attempt. Permission
mode is part of the host contract: a terminal session alone does not establish
that the handback tool is exposed. No feature flag is forced by this harness.
