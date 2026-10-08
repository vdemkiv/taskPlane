# Test the 2.33.4 candidate

This candidate repairs the Workflow Builder freshness cycle by keeping generated
phase evidence outside graph source inputs. It also preserves required native
workers through refinement, publication, Plan acceptance and guarded execution.
Regression checks cover convergence and rejection of real source drift and
altered evidence. It retains the earlier Claude delivery and recovery repairs.
This version also includes exact launch-denial recovery and retention of late
lifecycle contradictions when the optional observation cache is full.
Version 2.33.3 also corrects Windows scope-path comparisons and the cross-platform
CI fixtures, with four disjoint core-test shards and cancellation of superseded
branch runs. Version 2.33.4 adds approval wording and cancellation repairs plus
preserved delegated policy, checkpoint and reaction observations. It uses regular
version numbering; plugin runtime behavior is unchanged from the preceding
2.33.4-proofplane.3 package. Fresh installed-host acceptance is still required.

Live Claude validation was explicitly deferred to external testing after the
local closeout and Retro. The preceding 2.33.2 local suite passed 6,774 tests, with 6 skipped and
7 subtests passed. A separate integrity check verified the preserved result
against 284 unchanged file fingerprints and the recorded command, runtime and
environment. External dependency coverage remains incomplete; this is not a
fresh installed-host result or automatic test-cache hit.

The 2.33.2 local archive check passed 11 tests and failed one Claude fixture
with `OS boot identity or invocation clock is unavailable.` Both archives and
their member hashes matched the source. The failed fixture remains recorded;
it was not retried or counted as passing. Fresh Claude host testing remains
external.

The latest in-session Claude attempt failed while consuming a host-persisted
30.7 KB context response: its follow-up read was blocked before required intake
completed. Keep that failure and the unapproved B checkpoint history. In your
external session, verify complete required context delivery and full native
worker-acceptance responses before invoking the strict verifier. Do not infer
a pass from fixture tests or repeatedly retry an unchanged host failure.
Use a fresh test project/session. Keep the original farm-viewer runs as evidence;
they are not fixtures to rewrite or reset.

## Packages and source identity

Build from the checkout selected for validation:

```sh
python3 scripts/package_claude.py
python3 scripts/package_openai.py
```

The outputs are `dist/taskplane-2.33.4.plugin` (Claude) and
`dist/taskplane-2.33.4-openai.zip` (Codex). Each has a `.json` sidecar with the
archive SHA-256, source commit, working-tree status and per-member hashes.
Both sidecars must name the same source commit. A build containing uncommitted
changes reports `matches_source_commit: false`; its member hashes identify the
actual candidate bytes. Before release, rebuild from committed source and require
`matches_source_commit: true` with an empty `source_member_differences` list.
An archive receipt does not establish that a host installed or invoked those bytes.

Both archives use ZIP format. Extract into separate new directories:

```sh
python3 -m zipfile -e dist/taskplane-2.33.4.plugin /absolute/test/claude
python3 -m zipfile -e dist/taskplane-2.33.4-openai.zip /absolute/test/codex
python3 /absolute/test/claude/taskplane/tp.py version --verify
python3 /absolute/test/codex/taskplane/tp.py version --verify
```

Both commands should report `2.33.4` and `ok: true`.

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

## Claude regression scenarios and evidence limits

Exercise root context through real hooks that omit a `host` field; an Agent call
using the currently exposed schema; an answered question; and recovery after the
shell enters the project's `.taskplane` directory. Returning to the project must
remain a narrow recovery action, without permitting a nested workflow store.

Claude Code 2.1.289 exposes different Agent schemas across modes. The observed
headless CLI background launch returned structured child identity and subsequently emitted
a statusless `SubagentStop` and a native completion notification. It exposed no
`SubagentHandback`. Verify result delivery against the actual host contract;
ordinary final prose or a stop alone does not establish accepted work. Preserve
unknown delivery, identity conflicts and incomplete context as failures.

Exact asynchronous launch identity, automatic command rewriting, OS boot
identity and ordinary child hook readiness must be available. A sandbox may
deny the required macOS boot identity or Claude keychain access; capture that
failure and test in the normal permitted host environment. No capability or
live pass is implied by a version number or package receipt. Use the current
candidate's complete live report, and preserve failed/unknown attempts and raw
captures. The external 2.32.0 farm-viewer run is historical failure evidence.

## Interactive recovery check

Use the separate [interactive recovery guide](claude-interactive-recovery.md).
The 2.32.1 farm-viewer failure exposed `SubagentHandback` enforcement in the
interactive CLI, which the headless harness did not exercise. Run the prepared
interactive test in a real terminal; it refuses a headless fallback. Require
actual startup failure reporting, fresh retry, exact-command recovery and ten
distinct accepted native review results, followed by a sealed pending Engineering
checkpoint. Select `--worker-count 10` for this complete review journey. The default
remains the focused two-worker recovery scenario. Retain failures when a check does not pass.

```sh
python3 /absolute/test/claude/scripts/verify_claude_interactive.py --prepare --worker-count 10 \
  --plugin-dir /absolute/test/claude --output /absolute/test/interactive-prepared.json
# Launch in a real terminal with the fixture directory returned above:
python3 /absolute/test/claude/scripts/verify_claude_interactive.py --launch --fixture FIXTURE
# After the pending checkpoint is sealed, close the terminal normally, then:
python3 /absolute/test/claude/scripts/verify_claude_interactive.py --resume --fixture FIXTURE
# Close normally after the read-only continuation check, then inspect:
python3 /absolute/test/claude/scripts/verify_claude_interactive.py --inspect \
  --fixture FIXTURE --output /absolute/test/interactive-results.json
```

The ten-worker scenario also requires actual tool discovery, read-only worker
listing, an owned-worker message and the native decision-label dialog. Its UI fixture answer is never
treated as human checkpoint consent. The checkpoint stays pending. Deterministic
tests separately cover delayed delivery, unsafe control targets, negative decision
wording, large retained states, deduplicated bodies and native-layout continuation.
The harness resumes the original native session, reads its original run and a
registered result, and verifies that its checkpoint and accepted results remain
unchanged. This does not transfer ownership to a new root or fabricate human
consent. Report fixture checks separately from actual native execution. For
general continuation, follow the exact request and same-session sequence in the
[CLI reference](cli-reference.md#native-session-continuation); changing sessions
does not itself transfer ownership or acceptance.
