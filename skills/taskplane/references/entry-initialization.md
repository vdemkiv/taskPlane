# Common entry initialization

Every direct user entry uses this sequence, including help/status, resumed
work, and entry after an update. A specialist never assumes the facade ran.

1. Use the host-selected installed plugin's `taskplane/tp.py`: resolve its root
   from `PLUGIN_ROOT`, then `CLAUDE_PLUGIN_ROOT`, or this loaded skill's location.
   Quote the resolved absolute engine path and invoke it with Python. A valid
   host-selected root takes precedence over any repository launcher. Only when
   that installed root is unavailable, use `.taskplane/codex-hook.py` in the
   current checkout or its Git common-directory parent. Never treat unset root
   variables as `/taskplane/tp.py`. `$TP` denotes this resolved invocation, not
   a shell string assembled from user content.
2. Run `onboard --initialize --json --available-tools "<comma-separated names of tools actually available in this task>" --workspace <checkout>`.
   Use the current tool inventory, not examples from these instructions. This
   declaration is a compatibility check, not proof of host enforcement.
3. Read the result. Initialization creates missing setup and, when needed,
   an optional command-line launcher. Native plugin loading does not need it.
   It preserves existing context/run state and checks the committed checkout,
   run binding, phase definitions, and live hooks through existing onboarding.
   If setup was just initialized and hooks have not yet been observed, run
   `onboard --json --workspace <checkout>` once through the native host so its
   hooks can observe the initialized project. Use that fresh report; do not
   manufacture a receipt or keep retrying when the host does not execute hooks.
   Continue governed work only when `ready` is true. `setup_ready` means the
   checkout and project setup are complete; it does not mean hooks are live.
   This distinction applies equally to Codex, Claude Code and Cowork. Help and
   status may still explain an unmet prerequisite. Preserve completed setup
   when hook readiness is false.
   Inspect `engine.path`, `engine.version`, `engine.fingerprint` and the loaded
   hook observation when an update appears ineffective. A cache directory name
   is not proof of the installed UI version. A different or unrecorded hook
   engine requires host reload and a fresh native observation, not editing
   receipts or clearing the run.
4. Before a read-only review, also require `review_file_tools.ready`. Codex
   may supply `exec_command` plus scoped `apply_patch`, with reads inside its
   installed native `codex sandbox --include-managed-config -P :read-only`. Other hosts need explicit
   file tools. Report missing capabilities before activating a contract.
   Codex owns file access and execution permissions. A missing mapping to its
   native tools is an integration gap, not a reason to implement file IO or a
   process sandbox inside TaskPlane. Workspace setup and review compatibility
   are reported separately; completed setup must not be repeated to resolve a
   tool mismatch.
5. Use existing repository preparation for a different target. Standalone
   review initializes the prepared checkout again after committed-code scope
   checks and before deriving evidence or activating its contract. Start a
   fresh run only when requested; never copy old findings as current evidence.

Repeated entry rechecks current readiness and replaces only this host session's
compatibility declaration. Session isolation stays with the existing storage
and hook owners. A dispatched worker consumes its sealed startup; it does not
reinitialize or replace the coordinator's run.

If an existing read-only contract blocks reentry, invoke the installed engine's
absolute `tp.py` path for the onboarding command above. Existing status and
explicit recovery commands remain reachable when budget telemetry is missing.
After explicit approval for additional tokens, run `$TP budget --grant-tokens N
--approved-by USER --workspace <checkout>` once with that approved amount, then
continue the existing task. `--grant N` increases actions only. Do not ask for
the same approval again or reset the task's usage to bypass the ceiling.
Use the approval already present in the conversation. An approved total ceiling
and an additional grant are different: inspect current usage and the current
ceiling before calculating the grant. Do not label an additional grant as a
total ceiling. Use `budget --help` if the command syntax is uncertain.

Review options, phase controls and evidence commands run through the same
trusted installed engine on both hosts. Invoke one command with its resolved
absolute path and the existing workspace/run; do not add shell chains or
redirect output into source files. These controls remain metered. Human input,
approved budget recovery and delivery of the exact owed artifact remain
reachable at the budget ceiling. A display request is not proof of rendering:
show the returned artifact, then acknowledge the actual obligation.

## Claude startup and recovery

Claude SessionStart passes its observed session identity to later Bash commands
through the host-provided `CLAUDE_ENV_FILE`, including before project setup.
This handoff creates no hook receipt. After setup, the next real native hook
establishes readiness for that exact session and engine. Hosts that omit the
handoff or do not execute hooks remain explicitly unproven.

Use the returned host diagnostics. Recommend `/reload-plugins` as a command only
when Claude Code is identified; a `CLAUDE_SESSION_ID` alone does not establish
which Claude product is running. In Cowork, check that the plugin is enabled
and use the host's available reload/reopen control, then recheck onboarding.
Do not infer that cloud sessions cannot execute hooks from a missing receipt.
Keep the same run, checkout and pinned scope on retry; propose a host change
only after observing that the current host cannot supply the required hooks.

Keep the user's review scope across repository transfer and recovery. A request
for the last three commits must remain that exact comparison, not a root-commit
or whole-codebase review. Track transfer artifacts created by this task and
remove those disposable files when they are no longer needed under the existing
task authorization; do not invent a separate approval requirement for routine
cleanup. Preserve user files and retained review evidence.

## Native Codex calls

Use the executable returned in `review_file_tools.codex_sandbox.executable`.
Keep the outer shell `/bin/sh`, `login: false`, and the exact checkout as
`workdir`. Quote the complete argv with POSIX shell quoting; do not add outer
redirections, environment prefixes or additional shell commands. In code mode,
use a single expression with a JSON argument object (replace example paths):

```javascript
text(await tools.exec_command({"cmd":"/installed/codex sandbox --include-managed-config -P :read-only -- /bin/cat README.md","shell":"/bin/sh","login":false,"workdir":"/reviewed/checkout"}));
```

The installed engine's `host_capabilities.codex_readonly_command` builds these
arguments. This helper describes a native request; it reads no source files and
launches no process. Codex's hook currently projects the call as `Bash/command`;
TaskPlane checks the pending native session call to recover the omitted shell
settings. Missing or ambiguous records fail closed. Do not wrap multiple tools,
add JavaScript statements, or print an object resembling a native result.

macOS may reject nesting its sandbox. The generated request then asks Codex's
native approval reviewer to start the exact narrower sandbox outside the outer
one. This is a permission request, not approval supplied by TaskPlane. Never
change the native profile to obtain a successful read.

`command launch --host codex` returns `native_request` and `launch_requested`.
Run its fixed expression once. `command show`, `wait`, and `cancel` read native
session evidence and, while running, return Codex `write_stdin` requests. Run
those requests through Codex; TaskPlane owns no process, polling service, output
log or cancellation worker. A launch request, an interruption request, missing
history and a nonzero native exit are never successful test evidence. Hard
execution deadlines are unavailable on this desktop tool path and are refused
before launching; semantic checkpoint enforcement retains its separate boundary.

Native static previews open the disposable copy's `index.html` through
`open_in_codex`. Run the returned request, then `preview --request <same request>
--resume <preview-id>` to observe it without starting another command. A queued
panel is reported as queued, not opened or visually reviewed. Public hosting and
server URLs are not inferred from a command or a file panel.

Use the current Codex checkout for local review. Existing ready runs retain
their pinned checkout; do not create a Codex task just to obtain a worktree.
Native token counters remain the sole usage source in `native_session_meter`;
TaskPlane budgets and phase attribution retain their existing owners.
