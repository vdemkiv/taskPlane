# Test the 2.32.0 candidate

This candidate combines Workflow Builder and Claude worker identity recovery.
Use a fresh test project/session. Keep the original farm-viewer runs as evidence;
they are not fixtures to rewrite or reset.

## Packages and source identity

Build from the merged, committed checkout:

```sh
python3 scripts/package_claude.py
python3 scripts/package_openai.py
```

The outputs are `dist/taskplane-2.32.0.plugin` (Claude) and
`dist/taskplane-2.32.0-openai.zip` (Codex). Each has a `.json` sidecar with the
archive SHA-256, source commit and per-member hashes. Require
`matches_source_commit: true` and an empty `source_member_differences` list.
Both sidecars must name the same commit. An archive receipt does not establish
that a host installed or invoked those bytes.

Both archives use ZIP format. Extract into separate new directories:

```sh
python3 -m zipfile -e dist/taskplane-2.32.0.plugin /absolute/test/claude
python3 -m zipfile -e dist/taskplane-2.32.0-openai.zip /absolute/test/codex
python3 /absolute/test/claude/taskplane/tp.py version --verify
python3 /absolute/test/codex/taskplane/tp.py version --verify
```

Both commands should report `2.32.0` and `ok: true`.

## Local regression and exact archive checks

From the matching source checkout, install `requirements-dev.lock` in a virtual
environment and use that environment's Python:

```sh
python3 scripts/ci_local.py --check quality
python3 scripts/ci_local.py --check tests --suite all
python3 -m pytest taskplane/tests/test_workflow_packages.py -q --taskplane-archive-dir dist
```

The archive check compares the supplied files with a fresh deterministic build,
checks member hashes, and exercises both extracted runtimes in isolated fixtures.
It covers Workflow Builder compilation and existing workflow behavior. It does
not establish native host execution. For dashboard browser testing, configure a
real Chrome/Chromium executable and run `python3 scripts/ci_local.py --check browser`.

## Load the candidate

Claude Code supports a session candidate directory:

```sh
claude --plugin-dir /absolute/test/claude
```

In a fresh session, inspect the loaded plugin and hooks. Confirm the actual
runtime root and hashes; an older installed Taskplane may compete with the
session candidate. Follow the host's normal trust/permission prompts.

For Codex, add the Codex archive through your host's supported plugin upload or
permitted marketplace workflow, enable it, review the changed hooks, and start a
new chat in the test project. The locally observed CLI supports
`codex plugin marketplace add LOCAL_MARKETPLACE` and
`codex plugin add taskplane@MARKETPLACE`; a ZIP alone is not a marketplace source.
Use the host's configured test marketplace or its administrator for that step.
See [installation and hook trust](../README.md#install-and-run-your-first-task).
Changing the source checkout does not replace an already loaded plugin cache.

## External acceptance tests

1. Ask each host to create a reusable change-risk review with separate security
   and code-quality workers, save it without running, and show its preview.
   Save/preview must launch no workers. Invoke the saved version in the fresh
   project, using real source, dependency and test paths and Git refs. See the
   [Workflow Builder walkthrough](workflow-builder.md#command-walkthrough).
2. Record the actual loaded runtime path, version and member hashes. Observe
   automatic hooks, distinct native worker identities, complete task context,
   scoped outputs, independent termination and root result acceptance. Record
   host/version and missing capabilities; fixtures cannot replace this evidence.
3. For Claude, run the packaged harness preflight, then prepare fresh capability
   and lifecycle fixtures. Follow the independent workspace probe/binding steps
   in the [recovery guide](claude-worker-recovery.md) before launching them:

```sh
python3 /absolute/test/claude/scripts/verify_claude_workers.py --preflight \
  --plugin-dir /absolute/test/claude --output /absolute/test/claude-preflight.json
python3 /absolute/test/claude/scripts/verify_claude_workers.py --prepare --prepare-kind live \
  --plugin-dir /absolute/test/claude --output /absolute/test/claude-live.json
# After independently inspecting and binding the reported fixture workspaces:
python3 /absolute/test/claude/scripts/verify_claude_workers.py --live \
  --output /absolute/test/claude-live.json
```

## Known live Claude blockers

The observed Claude Code 2.1.289 host did not expose `SubagentHandback` or a
verified successful handback acknowledgement. The candidate accepts only the
historically observed message-only input shape and keeps delivery unknown until
a supported acknowledgement adapter exists. Unknown delivery still blocks root
result acceptance. A `SubagentStop` without explicit terminal status also cannot
join a worker as completed; ordinary Task final text is not substituted for it.

Exact asynchronous launch identity, automatic command rewriting, OS boot
identity and ordinary child hook readiness must be available. A sandbox may
deny the required macOS boot identity or Claude keychain access; capture that
failure and test in the normal permitted host environment. No capability or
live CW-07 pass is implied by these packages. Preserve failed/unknown reports
and their capture directories for the next adapter repair.
