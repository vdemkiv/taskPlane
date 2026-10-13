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
- Claude 2.1.290 also delivers a system notification as a transcript-only user
  record without `turnOrigin`. This form requires exact session-task origin,
  `promptSource: system`, both `queueSkipAttachments: true` and
  `queueTranscriptOnly: true`, and valid `uuid` and `promptId` identifiers.
  The existing launch, child, workspace, output-file, timestamp and pinned-span
  checks still apply. Ordinary user XML, enqueue records and display wrappers
  do not establish completion. A notification cursor from an older parser
  rescans once and revalidates retained evidence before returning a fresh match.
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
`TaskStop` and `ListAgents` (`query: "select:SendMessage,TaskStop,ListAgents", max_results: 3`). This
read-only discovery grants no target authority. Other tools, free-text searches,
duplicate selections and larger result limits remain unsupported by this adapter.

`ListAgents` accepts only empty input `{}` from the current automatic Claude
root hook with matching runtime proof. Workers and foreign adapters cannot use
this root inventory exception. Its response is observational: even `completed`
or `failed` labels cannot terminate an attempt, bind another worker, release
ownership or accept a report. Completion still requires the independent native
launch, handback, acknowledgment, stop and notification evidence.

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

## Guarded report reconciliation

Claude Code 2.1.296 can prepend its exact `settings-json` harness notice to a
delivered peer report. An observed native completion had an intact admitted
report after that notice; exact equality with the delivered text previously
prevented its join. The adapter recognizes only that fixed notice and version,
inside a verified native peer envelope, with a matching settings-path pattern.
It preserves the complete delivered record and body hashes and separately
compares the unchanged remainder's body and input hashes with the original
automatic handback admission. The acknowledgment, runtime, child, grant, attempt,
binding, stop, chronology and pinned transcript span checks still apply.

This is a bounded correlation rule, not permission to trust instructions in a
report. Arbitrary prefixes, near-match notices, changed report bytes, additional
guard transforms, unrecognized host versions, model-authored claims and missing
proof cannot authorize a join. A literal notice already present in the admitted
report remains part of that report. Existing cached projections gain the new
field only after their original transcript spans are revalidated.

From the current root, `flow worker --operation reconcile-handback --workspace
PATH --run RUN --grant GRANT --expected-revision N` revalidates an existing
attempt without sending input to the child. Do not provide `--worker-json` proof
or edit the state. A successful join leaves the result pending; ordinary result
verification, declared inputs, live-command checks, DoR/DoD and human approval
remain required. Other accepted results and the original attempt are retained.
`flow worker --operation status` exposes the reconciliation outcome; unsupported
evidence stays blocked with a diagnostic rather than claiming completion.

The regression fixtures cover the actual notice, a retained old cursor and
15 already accepted peers, plus near-match notices, report mutations, foreign
children, forged cached projections and missing native evidence. A read-only
replay of captured native evidence is separate from fresh installed-runtime
acceptance. Installing a corrected runtime and continuing a live run require
explicit activation authorization; source tests never activate hooks or repair
active state.

## Bounded Claude history-limit startup reconciliation

Unrelated human transcript records can exceed the native evidence record limit.
The reader classifies only bounded, structurally verified human text separately;
oversized tool, assistant, notification or ambiguous records remain unsupported.
For observed Claude 2.1.296 framing it also preserves native and admitted input
hashes when the native `run_in_background: "true"` field was omitted from the
automatic admission, and recognizes the exact system completion origin containing
`runId`. All other launch arguments, source spans and actor checks remain exact.

Existing sealed conflicts are not silently cleared. The owning root may invoke
`flow worker --operation reconcile-startup --workspace PATH --run RUN --grant GRANT
--expected-revision N` for the exact retained history-limit startup case. It accepts
no supplied proofs or identities. Original admission, frozen inputs, empty legacy
cursor, oversized human record, native launch/child/stop and no-report completion
must agree. The original failure and proof are retained in immutable objects.
Only the failed attempt changes; phases, approvals, accepted results and historical
runtime identity stay intact. Retry one unfinished task with a new grant after
the currently executing runtime's parent hooks are observed. Do not repeat accepted
phases, erase evidence, retarget a run or manufacture a successful result.

The isolated original-log command replay and synthetic controller tests are not
a fresh native lifecycle smoke test or evidence that an installed runtime changed.

## Claude 2.1.290 repair evidence, 2026-10-05

The clean `92b98186014a272294b520fa161550394acea092` candidate retained nine
accepted workers and left `CW-LIVE-E` running in disposable fixture
`taskplane-claude-interactive-4pf9pdol`. The original native run is
`2b5212e1db6a42a8b0371b332a861fc5`; its parent is
`de178cc8-5039-40f9-944d-987a954d639a` and E's worker is `a1292f2f91dbf68f9`.
The original fixture and runtime are preserved as unsuccessful live evidence.

Parent transcript line 439 contains one notification, not a batch. It has the
transcript-only flags above and no `turnOrigin`, so the prior parser discarded
it despite the acknowledged `SubagentHandback` and textless `SubagentStop`.
Line 621 also records the actual root `ListAgents` call with empty input, which
the prior guard refused as unsupported.

A read-only replay of the original source-selected transcript and stored cursor
now returns `matched`, upgrades the cursor to notification version 2 in memory,
and matches `_redirect_delivery` to E's exact stored acknowledged handback.
The completion span is byte offset `1233222`, length `1657`, SHA-256
`e8ed34a11acc14f3764c19285a6fc9aec9bbb4ed2fb5585c1daef004bad65191`.
Its peer span begins at `1213237`. The actual report body digest is
`5a635d9a035fdbaba6aab5077d95d2eb4a26f09daf0997ced77bef5da58d59c4`.
Controller and parent transcript bytes were checked unchanged after replay:

| Original evidence | SHA-256 |
| --- | --- |
| `original-project/.taskplane/workflow-77e67ef5c8128143f86982039936e38d.json` | `f39cff2d27c711b8ddf5622a664d52f0a90ae2ed642ae4511d5e93753bb77b9f` |
| Native parent JSONL | `0e15bec42cc207d250000687d4debb43358ec9483177ed6b2e04d770cdbdcf60` |

The new exact-shaped regression failed against the old parser with missing
`completion`. The repaired focused vector passed 61 tests (394 deselected):

```sh
python3 -m pytest taskplane/tests/test_claude_worker_lifecycle.py taskplane/tests/test_claude_interactive_recovery.py -q -k 'transcript_only or transcript_notification_cursor or listagents or inventory_discovery or direct_notification' --maxfail=2
```

Coverage includes missing, false and non-boolean queue flags; origin and role
spoofs; absent or malformed IDs; foreign launch/worker/session/workspace/output
identity; invalid chronology; modified pinned bytes; conflicting and duplicate
delivery; one-time cursor migration; complete handback joining and acceptance;
and `ListAgents` ownership/runtime refusals without terminal status inference.
Direct report bodies retain literal and escaped angle brackets, entities and
quotes across all three supported notification forms. Nested or concatenated
task-notification framing is refused. Focused Ruff and `git diff --check` passed.
These are parser and integration-fixture checks. A fresh exact-candidate live run
must separately establish ten accepted useful workers and all required scenarios;
the failed original run was not repaired or accepted by this replay.
