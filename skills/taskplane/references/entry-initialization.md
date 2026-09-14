# Common entry initialization

Every direct user entry uses this sequence, including help/status, resumed
work, and entry after an update. A specialist never assumes the facade ran.

1. Resolve the currently installed engine. Use the stable workspace launcher
   when present; otherwise use the installed plugin's `taskplane/tp.py`.
2. Run `onboard --initialize --json --available-tools "<comma-separated names of tools actually available in this task>" --workspace <checkout>`.
   Use the current tool inventory, not examples from these instructions. This
   declaration is a compatibility check, not proof of host enforcement.
3. Read the result. Initialization creates missing setup and the launcher,
   preserves existing context/run state, and checks the committed checkout,
   run binding, phase definitions, and live hooks through existing onboarding.
   Continue governed work only when `ready` is true. Help and status may still
   explain an unmet prerequisite.
4. Before a read-only review, require `review_file_tools.ready`. Use native file
   tools when `read_transport` is `native`. When it is `engine_inspection`, use
   the bounded installed-engine `inspect` operation below and the existing
   scoped `apply_patch` tool for evidence. Report an unsupported tool set before
   activating a contract. Never claim a tool exists, allow general terminal
   commands, or bypass a failed compatibility check.
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

## Codex file inspection

`review_file_tools.inspection` supplies the exact installed `engine` and
`python` paths. Call `exec_command` with `shell: "/bin/sh"`, `login: false`,
`workdir` set to the review checkout, and this command (quote each path using
POSIX shell quoting):

```text
exec <python> -I -S -B <engine> inspect <request>
```

`request` is URL-safe base64 of UTF-8 JSON with `operation` (`read`, `list`, or
`search`) and `path`. Optional `start` is a one-based line or directory-entry
offset; `limit` defaults to 200 and is at most 400. `search` requires a literal
`pattern` and searches one file. List directories to discover files; read and
search responses carry line numbers and explicit truncation. Encode data in
the host orchestration tool; do not launch another terminal command to encode
it while a review contract is active. The complete command must be the canonical
POSIX-quoted argv, without prefixes, redirects, pipelines, or extra commands.

This operation uses the existing hook's contract and action/token budget. It
does not grant general terminal access, execute source, write files, activate a
contract, approve a gate, or replace worker evidence. The engine must be outside
the reviewed checkout. Native file tools remain the path on hosts where this
isolated invocation is unavailable. `ready`, `review_ready`, the checklist and
the exit status include file compatibility; `workspace_ready` reports setup
separately. A missing tool is not a reason to repeat completed setup.
