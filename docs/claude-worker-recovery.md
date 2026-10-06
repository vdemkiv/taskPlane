# Verify and recover Claude native workers

A source test or successful Claude launch does not establish worker recovery.
`scripts/verify_claude_workers.py` captures fresh automatic host events and checks
the complete scoped lifecycle. Missing identity, context, delivery, termination,
runtime selection or result acceptance remains a failure or unknown observation.
The original farm runs and their transcripts are never modified.

## Prepare the candidate and inspect capabilities

Build the candidate with the supported packaging script, unpack it, and pass its
directory with `--plugin-dir`. The harness also accepts a source candidate during
development. It uses Claude's supported session plugin loader; it never changes
installed plugin registries, user settings, authentication or permission modes.
Live mode supplies normal per-session allowances for the fixture's exact commands,
declared report files and candidate reads. The recorded launch arguments retain
these allowances. Arbitrary commands still require native permission, and all
Taskplane hooks remain active. A denied required operation stops the attempt.
An existing installed Taskplane may compete with the session candidate, so inspect
the captured initialization plugin list and actual runtime paths. Matching version
strings alone are insufficient.

```sh
python3 scripts/verify_claude_workers.py --preflight \
  --plugin-dir /absolute/candidate \
  --output .taskplane/claude-workers/live-claude.json
python3 scripts/verify_claude_workers.py --prepare --prepare-kind capabilities \
  --plugin-dir /absolute/candidate \
  --output .taskplane/claude-workers/live-claude.json
```

Preflight records CLI version, help, auth status and required flags. A sandbox may
be unable to read the normal host keychain; an auth failure there is not proof the
user is logged out. Use the normal permitted host execution environment. Do not
use bare/safe modes or skip-permissions flags to make the check pass.

Preparation creates a disposable fixture and reports its absolute project,
candidate and probe paths. It stops before launching Claude. The harness retains
explicit `TASKPLANE_WORKSPACE` selection, which requires the normal workspace
binding. Independently read and hash the reported probe through the actual host,
record that tool observation and the real command environment, then use the
installed [`workspace inspect` / `workspace bind` interface](cli-reference.md#workspace-binding-and-execution-policy).
The execution-side read cannot supply the separate host observation. Keep worker
location unknown unless independently observed; the root's location is not worker
evidence. The user did not request stricter locality merely by requesting this
test. Do not weaken any policy the user did request.

After supported binding, continue with the reported preparation directory:

```sh
python3 scripts/verify_claude_workers.py --capabilities \
  --prepared-dir /absolute/prepared-capability-directory \
  --output .taskplane/claude-workers/live-claude.json
```

Every execution gets a fresh Claude session and separate raw capture directory.
Before replacing an existing report, the harness retains its exact bytes and
SHA-256 in a separate evidence file and links that file from the new report.

Capability capture runs the real configured hooks. A disposable plugin copy wraps
each original hook command, executes that command unchanged, and retains the
automatic stdin, original stdout/stderr, exit status and executing runtime hashes.
Only `hooks/hooks.json` differs from the candidate. Its original and instrumented
hashes are both retained. This is explicit instrumentation, not a claim that the
unmodified production package has been installed or certified. The loaded runtime
must be compared with the package and installation evidence separately.

A single exact harmless Bash sentinel is deliberately rewritten with `updatedInput`
in capability mode. The report distinguishes the proposed rewrite, actual post-hook
input representation and observed rewritten command output. This capability fixture
cannot satisfy any scoped lifecycle check. `--live` never applies this rewrite.

The harness reads schemas from the **fresh native transcript's** tool attachments.
It also retains actual child `agent_id`, tool call identity, handback pre/post hooks,
native completion notifications, and SubagentStop payloads. A tool name or agent-written description does not prove
the input schema. Absence of a handback post-hook is recorded as absence. A terminal
handback may end the child before such a hook occurs; only an independently observed,
supported transcript delivery/terminal adapter may resolve that case. Claude Code
2.1.289 CLI captures include background Agent results and native completion
notifications without exposing `SubagentHandback`. A completion adapter must bind
the pinned root transcript, native origin metadata, exact launch call and child,
and independently observed stop/result evidence. Neither ordinary user text that
resembles a notification nor a statusless stop alone establishes delivery.
Result comparison applies the observed XML encoding exactly once, preserving
literal entity text. An actual content conflict remains unknown on later reads.
Both observed delivery forms are supported: a system-origin user notification
between turns and a structured system attachment during an active turn. Queue
enqueue/removal records and rendered display wrappers do not prove delivery.

## Run the scoped end-to-end check

Prepare after integration and packaging so the staged runtime contains the final
candidate. A preparation pins a copy; later source edits do not update that copy.

```sh
python3 scripts/verify_claude_workers.py --prepare --prepare-kind live \
  --plugin-dir /absolute/final-candidate \
  --output .taskplane/claude-workers/live-claude.json
```

Independently inspect and bind both reported execution workspaces as above. Then:

```sh
python3 scripts/verify_claude_workers.py --live \
  --output .taskplane/claude-workers/live-claude.json
```

The exact command above follows the explicit `prepared_fixture_ref` stored by
preparation in that same report. It verifies the reference hash, candidate and
instrumented runtime hashes, source/task/scope fixtures and hook delegate capture
before launching. `--prepared-dir /absolute/prepared-live-directory` remains an
explicit alternative. A missing/stale reference refuses; the harness never guesses
a fixture or binding. Live/unknown workers left in that workspace also block a new
session until genuine recovery resolves their ownership.

The two fresh sessions perform bounded standalone Engineering reviews, using real
prepared native tasks and distinct output ownership. They review small supplied
Python inputs with reproducible boundary defects. The root must observe the first
worker's claim, fully delivered context and ordinary automatic hook pair before
preparing the other. A bounded dwell permits observed overlapping worker lifetimes.
Overlap is measured from each worker's claim to its independently observed stop;
delayed controller reconciliation cannot extend a worker's lifetime.
Report actual host capacity when exposed. Claude may omit a host limit; record
that as unknown with the observed schema reference and the explicit two-worker
fixture admission budget. A configured budget cannot invent host slots, and a
host launch refusal remains a failed attempt.

The second scenario uses a disposable Git worktree while Claude starts in the
original project. Passing requires actual child execution in the worktree and the
parent transcript under a different native project directory. The harness neither
inserts transcript records nor pads an old launch. Long-transcript, delayed-record
and unsafe-path cases still need their focused automated tests.

The worktree scenario also deliberately executes a real failing command in B's
first attempt. The worker returns the failure, the root observes its independent
terminal event, and the failed task result remains unaccepted. A single fresh grant
with a concrete retry reason performs the useful review. A failed command and a
successfully terminated host agent are reported separately. Unknown/live ownership
blocks retry. This validates real failure reporting and fresh retry without
pretending to exercise a cancellation interface the host has not exposed.

The harness inspects actual controller records and raw automatic/native evidence
for preparation, exact claim calls, real invocation rewriting, complete context,
ordinary hook pairs, distinct native identities and scoped output hashes, delivered
result delivery, independent terminal join and root `accept-result`. It also checks
concurrency, the fresh native delivery contract, selected runtime, worktree location and retained
failure/retry evidence. Agent prose or a JSON `pass` label cannot replace those
records. No manual hook event is supplied to the candidate by the harness.

This bounded review may submit and present its Engineering checkpoint, but it never
records human approval, enables an approval policy, advances phases or finishes a
seven-phase route. Accepted worker results and accepted human checkpoints are
different facts.

Exit code zero in capability mode means a capture completed, **not** CW-07 success.
Only live mode with every required check passing reports `e2e_status: pass`. Raw
logs, transcript source hashes, controller evidence and all previous reports are
retained. A timeout terminates the observed CLI process group and reports child
termination as unknown; it never creates an accepted result or a replacement task.

## Diagnose a failed attempt

1. Read the JSON report and referenced raw capture directory. Identify the first
   missing capability or refused command; retain the exact attempt and its runtime.
2. If workspace selection is pending, inspect and bind using genuine independent
   probe observations, then start a fresh session in the same unchanged fixture.
3. If the wrong plugin loaded, correct selection with supported plugin installation
   or session loading and verify actual hook paths/hashes before retrying.
4. If claim identity is missing or conflicting, inspect the exact parent launch,
   structured result and child header. A prompt grant or inherited parent environment
   cannot stand in for native child identity. Delayed records permit only bounded
   fresh calls with the observed reason; conflicts remain failures.
5. If handback delivery or termination is unknown, reconcile its exact native call
   and independent terminal evidence. Do not equate report text, timeout, mailbox
   return or cancellation request with successful process termination.
   Parent delivery may occur after worker stop: verify admission → acknowledgment
   → stop separately from parent transcript peer → completion spans. A completion
   attachment can retain its earlier enqueue timestamp. Do not rewrite timestamps
   or increase timeouts to force this join.
6. Use the installed root-only recovery operation only when its supported native
   evidence exists. Abandon only a reservation that was never launched. Retry under
   a fresh grant after real terminal/recovery evidence releases ownership, keeping
   the original failure. Do not substitute serial root work for required workers.

For the observed Claude 2.1.289 contract, read-only discovery supports exact
`select:SendMessage,TaskStop` (or either tool alone), with at most two results.
The [interactive recovery contract](claude-interactive-recovery.md) documents
the accepted plain-message and exact-worker-stop forms. Discovery never grants
permission to address a foreign session, shell, sibling worker or ended attempt.
Taskplane checks the owned current attempt when the actual operation is called;
the host still controls whether the tool is available and permitted. A successful
`TaskStop` response remains a cancellation request observation, not a joined result.

These are cooperative local observations. They do not authenticate a hostile local
account or establish protected-host authority. Required unknowns must remain visible
in Build/Evaluate rather than being converted into a recovery claim.
