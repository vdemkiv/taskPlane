# Configuration — every environment variable taskplane reads

taskplane has no config file of its own; every knob is an environment
variable. This page is the complete reference, derived from the code
(`taskplane/taskplane_lite.py`, `tp.py`, `loop.py`) — when a release adds
a variable, it must be added here (grep for `TASKPLANE_` to audit). An
unset variable always means the documented default; nothing here is
required for normal use.

**Enforcement-relevant** marks variables that change what the guardrails
do — set those deliberately, and treat them as part of your governance
configuration, not personal preference.

## Supported Python runtime

Taskplane's validated support range is **CPython 3.10 through 3.13 inclusive**.
Other Python versions are outside the validated support range; an interpreter
newer than 3.13 may start, but remains unvalidated until it is added to this
matrix. The push and pull-request CI matrix proves each supported minor
independently: before any test command, every leg compiles and imports all
tracked Python modules shipped in the plugin plus its hook entry points, then
runs representative version, CLI entry, graph, status, and evaluation-corpus
flows.

An interpreter older than Python 3.10 is refused with exit status 2 and a
concise `taskplane requires Python 3.10 or newer` message before importing
shipped modules and before creating or changing Taskplane state. This early
compatibility refusal emits no Python traceback because the engine has not
been imported yet.

## Store and state

The default external home uses the hybrid layout documented in
[`storage-and-repositories.md`](storage-and-repositories.md): repository
identity/mirrors/worktrees, per-repository knowledge, per-run private state and
artifacts, and content-addressed graph cache are distinct roots. The source
checkout is never an artifact directory. Team/enterprise knowledge may still
be deliberately shared under `.taskplane-kb/knowledge/`.

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_STORE` | *(unset)* | Highest-precedence store override: `repo` forces the in-repo shared store (`.taskplane-kb/` — used by Claude Tag so state survives the ephemeral sandbox), `external` forces the external store. Overrides plan, private mode, and shared config (see `docs/state-spec.md`, "Store resolution"). | **Yes** — silently redirects the entire knowledge store, including loop state and worker-submission evidence, into (or out of) the committed repo. |
| `TASKPLANE_HOME` | `~/.taskplane` | Moves the external store root (all per-project knowledge on a personal plan). `tp kb where` shows the resolved path. | **Yes** — a wrong value redirects the whole knowledge store; the KB, decisions, and loop state follow it. |

## Contract lifecycle (the hook's wall)

### Proportional verification

Verification follows the changed risk surface. Documentation-only corrections
use static checks; they do not trigger a repository runtime suite, detached
baseline, or unrelated regression bundle. A documentation-only correction
does not invalidate runtime suite evidence when production code, runtime
configuration, tests, command, engine, and governing environment are unchanged;
it produces separate static evidence for the edited documentation. If a runtime
suite is explicitly requested, the general suite-cache identity remains strict
and content-addressed. This distinction avoids turning a typo or missing
configuration sentence into another full product test cycle.

Repair cycles batch related fixes before verification: run each distinct
failure cluster once, then one combined dependency-graph affected-radius check,
then submit once. The remote CI matrix is the full-suite authority unless the
task contract or human explicitly requires a local full run. A newly exposed
cluster reruns only its failed selector and the final radius check; it does not
restart every already-green cluster.

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_AGENT_PID` | *(unset)* | Exported by the activating agent; recorded on the contract as the authoritative liveness token. A live PID is **never** idle-released; a dead PID orphans (and auto-releases) the contract immediately. Must be a real positive PID. | **Yes** — with it, liveness beats the TTL in both directions. |
| `TASKPLANE_ORPHAN_TTL` | `3600` (seconds) | Idle backstop for a contract with **no** recorded PID: after this many seconds with no screening activity the contract is treated as orphaned and auto-released (a crashed agent shouldn't lock the workspace forever). A per-contract `orphan_ttl_seconds` field takes precedence. Budget-**exhausted** contracts are a human gate and are never auto-released, regardless of TTL. | **Yes** — a short TTL weakens the wall (an abandoned contract sheds governance sooner); CI sandboxes with long quiet phases may need it *longer*. |
| `TASKPLANE_TASK` | *(unset — legacy single slot)* | Selects the per-task contract slot (`.taskplane/active/<slot>.json`, v2.3.0) governing this process; the dispatch brief exports it to each governed agent so parallel agents cannot overwrite or release each other's contracts. Value must match the task id (`[A-Za-z0-9][A-Za-z0-9._-]*`, ≤ 64 chars). Set with **no** matching slot file → hard refusal (StateError), never a fallback to the legacy slot. | **Yes** — fail-closed by design: an ill-formed or unmatched value blocks rather than letting an agent run under a sibling's contract or escape screening. |
| `TASKPLANE_BARE_ROOT` | *(unset)* | `os.pathsep`-separated **extra** roots for the bare-workspace guard (`tp new` refuses to activate a contract in a bare root / session home). Only ever ADDS protected roots — the built-in set (`~`, `/`, `/root`, `/home/claude`) cannot be removed via this variable. | **Yes**, additive-only — it can strengthen the guard for a new host layout, never weaken it. |
| `TASKPLANE_AUDIT_EVERY` | `5` | Legacy compatibility counter only. It never widens automatic review: every cadence and release path still runs exactly 4–5 selected quick lenses. Full or exact deep execution requires a direct attributable user request. | No — retained so existing run state remains readable; it creates no work. |
| `TASKPLANE_QA_BASELINE` | unset | Forces the `qa` lens back to baseline firing (every code change) instead of its default untested-change trigger (a code change that adds no test file). The trigger reaches the same Blocker — "an acceptance criterion with no failing-capable test evidence, including the case where the change ships with no tests at all" — at a fraction of the cost: measured over 40 real changes, baseline fired on 32 and the trigger on 2. Set it on a codebase where tests are routinely omitted and you want QA on every change regardless. Accepts `1`/`true`/`yes`/`on`. Machinery: `taskplane/lens.py::_adds_no_test`. | Yes — unset it to return to the default trigger. |
| `TASKPLANE_NO_SUITE_CACHE` | *(unset — cache on)* | `1`/`true` forces the DoD test command to execute even when an identical run is already on record. The cache keys a completed run to the exact workspace **content** (HEAD, the tracked diff against it, and every untracked non-ignored file's path and bytes), the exact command, the engine fingerprint, and the governing `TASKPLANE_*`/`CODEX_*`/`PYTEST_*`/`PYTHON*` env — so a hit means that same command already ran to completion over byte-identical governed content under the same engine. Any doubt about tree identity (no git, a git error, an untracked payload above 32 MB) skips the cache and runs. Hits are traced as `suite_cache_hit`, real runs as `suite_run`. | No — it can only force MORE execution. Failures cache exactly like passes, so a broken tree stays broken until its content changes; the variable exists for hosts that suspect environmental nondeterminism. |
| `TASKPLANE_SUITE_CACHE_MAX_AGE` | `86400` (24 h) | Seconds a completed suite run may stand in for execution at the `tests_pass` gate. The cache key binds everything taskplane CONTROLS — tree content, command, engine fingerprint, governing env — but it cannot bind the interpreter minor version, the installed package set, or the OS libraries, and those drift. Past this window a record is refused (traced `suite_cache_stale`) and the suite runs. `0` or negative disables citation entirely. Every citation that IS used is now stated in the DoD output and the sign-off payload, not only in the trace. | No — it can only force MORE execution; lowering it is always the safer direction. |
| `TASKPLANE_REGRESSION_TIMEOUT_SECONDS` | `1200` (seconds) | Shared hard timeout for both the current-checkout and detached-baseline pytest processes in the graph-scoped regression gate. Accepts an integer from `30` through `1800`; invalid or out-of-range values block as runner-configuration errors. A process that reaches the bound remains a named gate failure—it is never treated as skipped, comparable, or passing. Machinery: `taskplane/regression.py`. | **Yes** — it controls how long the gate permits both sides of the comparison to establish trustworthy evidence. The same bounded value always applies to current and baseline runners. |
| `TASKPLANE_PUBLISH_REVIEW` | *(unset — withheld)* | `1`/`true`/`yes`/`on` allows model-authored review artifacts (managed run `findings.json`, `report.md`, `retro.md`, and the rendered dashboard; legacy unmanaged workspaces use `.em-review/`) to be copied into an **in-repo** store (`.taskplane-kb/`, the team/enterprise plan) by the automatic gate snapshot. Unset, managed artifacts remain in the private external run store and the snapshot NAMES what it withheld. Publishing to a shared store remains a deliberate human act. | No — it changes publication, never review validity. |
| `TASKPLANE_OBLIGATIONS` | *(unset — blocking on)* | `off`/`0`/`false`/`advisory` disables the **binding-obligation** block while leaving the ledger recording exactly as before. A binding obligation is the one place where an obligation is converted into a PROHIBITION: a run started with `tp new --owes <run-type>` records the artifacts it owes a human BEFORE the work begins, and the PreToolUse screener then refuses taskplane's own completion commands (`dod`, `loop submit`, `loop approve`, `loop retro`, `loop gate`) until each has been shown and acknowledged. It exists because a hook can DENY an action but cannot COMPEL one, so every prohibition in this product held at 100% while every instruction to render something held at 0%. Deliberately narrow: it can never block an edit, a test, a search, or any other part of doing the work — only the act of declaring it finished. Discharge with `tp ack <id>` (or by rendering the engine's exact bytes, which the `mcp__visualize__.*` hook observes); list with `tp ack --status`. | Yes — setting it `off` removes a block. It is documented BECAUSE a governance mechanism with no stated way out is one people route around by uninstalling; the escape is recorded in the refusal message itself. |
| `TASKPLANE_WORKFLOWS` | *(unset)* | Workflow dispatch-path opt-in for the review wave AND the stage waves: `1`/`true`/`yes`/`on` lets `tp lens dispatch --emit auto`, `tp loop wave --emit auto`, and `tp loop next --emit auto` choose the Claude workflow path (`workflows/review-wave.js`, `execute-wave.js`, `evaluate-wave.js`, `fix-wave.js`); any of `0`/`false`/`no`/`off` is the kill-switch forcing the Task-dispatch path everywhere. Unset = the `CLAUDE_CODE_WORKFLOWS` runtime marker decides, else the conservative Task path. On Codex hosts the Task path is ALWAYS used regardless of this variable. See `docs/routing-and-flows.md`. | No — both paths are traced (`review_dispatch_path` / `stage_dispatch_path`), the Task path's output is byte-identical, and no gate is reachable only via workflows. |
| `TASKPLANE_CONSOLIDATED_FLOW` | *(unset — legacy transition compatibility)* | Enables the R-0001 consolidated authority derivation and bounded automatic recovery path. A truthy value (`1`/`true`/`yes`/`on`) derives routine Product-through-evaluation authority from one bound pre-implementation receipt; material scope/authority drift and final sign-off remain human-owned. | **Yes** — it selects the consolidated authority transition model; it never authenticates a reply or weakens a mechanical gate. |
| `TASKPLANE_HOST_SESSION_EVENT` | *(unset)* | JSON host-adapter observation for the current trusted local/private Codex, Claude, or Slack-capable session. The adapter binds actor, thread, revision, target, event reference, source, and event-content fingerprint; missing or mismatched fields fail closed. | **Yes** — it supplies attribution only. Native UI state is not authority, and Taskplane does not add signing keys or a second same-user trust boundary. |
| `TASKPLANE_INLINE_MAX` | `24000` (characters) | Above this size, `tp findings` stops handing back a renderable HTML blob and hands back a **path** — `RENDER-BY-REFERENCE: <file>` — for the driver to DELIVER (SendUserFile / the host's artifact channel) rather than retype through a widget tool. It exists because the v2.9.0 render obligation, which made showing an artifact enforceable, also made the cheapest compliance path the most expensive one: on one measured review the driver pasted back ~52k characters of HTML that taskplane had already written to disk, and inline dashboards came to 450k effective tokens — the largest single addressable slice, caused by the enforcement rather than by the work. A delivered file is the SAME bytes, so `tp ack <id> --delivered <path>` corroborates exactly as a widget render does; the fingerprint is what the ledger compares either way. `0` disables reference mode entirely (always inline). | No — it changes the CHANNEL an artifact arrives on, never whether one is owed. The obligation still blocks completion until the artifact is shown, and a delivered substitute is still recorded as a mismatch. |
| `TASKPLANE_RUNNABILITY` | *(unset — probe on)* | `off`/`0`/`false`/`no` skips the build/test **runnability probe** that `tp lens dispatch` runs before composing briefs. The probe answers one question about the CHECKOUT — would `go test ./...`, local TypeScript `tsc --noEmit`, `npm test`, or `pytest` get off the ground here — using a bounded, cheap subcommand (`go list ./...`, local `tsc --version`, `node --version`, `import pytest`, or `cargo metadata --offline`), never the suite itself, and states the verdict in every dispatched brief plus the wave board. It exists because on `aws/karpenter-provider-aws#9464` six lens agents were dispatched in parallel and all six independently spent actions discovering that `go test` could not run in that sandbox: one environment fact, paid for six times. The answer is cached in `.taskplane/runnability.json`, keyed by the manifests, local dependency/compiler presence, and `PATH` that resolve the toolchain, so a whole wave shares one probe while installing the missing toolchain mid-review re-probes. | No — it is information, never a gate: no screener, contract, or gate consults it (pinned by `test_runnability_probe.py::TestItIsInformationNotEnforcement`). Setting it `off` only makes agents rediscover the fact themselves. |

## Host capability receipts (set by adapters, not guessed)

### Native workflow surfaces and preview transports

These optional variables are adapter observations or native transport commands.
Unset capabilities remain unknown and use the accessible bounded fallback; unset
commands make the corresponding preview surface unavailable rather than guessed.

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_NATIVE_PIP` | *(unset)* | Host-observed Picture in Picture support. | No. |
| `TASKPLANE_NATIVE_VISUALIZATION` | *(unset)* | Host-observed native visualization support. | No. |
| `TASKPLANE_NATIVE_CAROUSEL` | *(unset)* | Host-observed native carousel support. | No. |
| `TASKPLANE_NATIVE_APPROVAL` | *(unset)* | Host-observed native approval surface support. | **Yes** — approval still requires an authenticated canonical receipt. |
| `TASKPLANE_NATIVE_SANDBOX` | *(unset)* | Host-observed disposable sandbox support. | **Yes** — it is evidence only; validation still requires the isolation receipt. |
| `TASKPLANE_NATIVE_HOSTING` | *(unset)* | Host-observed private preview hosting support. | No. |
| `TASKPLANE_NATIVE_BROWSER` | *(unset)* | Host-observed integrated browser support. | No. |
| `TASKPLANE_NATIVE_SIDE_PANEL` | *(unset)* | Host-observed side-panel support. | No. |
| `TASKPLANE_SIDE_PANEL_COMMAND` | *(unset)* | Native side-panel transport command used for a governed preview. | **Yes** — parsed without a shell and bound to the preview receipt. |
| `TASKPLANE_BROWSER_COMMAND` | *(unset)* | Native browser transport command used for a governed preview. | **Yes** — parsed without a shell and bound to the preview receipt. |
| `TASKPLANE_HOSTING_COMMAND` | *(unset)* | Native private-hosting transport command used for a governed preview. | **Yes** — parsed without a shell and bound to the preview receipt. |

These values are runtime observations supplied by the Claude or Codex host
adapter. File presence proves configuration only; it never proves that a hook
loaded, a repository is trusted, or a native dispatch argument is supported.
Missing values remain `unknown`, and malformed or contradictory values fail
toward the governed fallback. Users normally inspect these through
`tp onboard --json` rather than setting them manually.

| Variable | Accepted value / format | Observation |
| --- | --- | --- |
| `TASKPLANE_NATIVE_HOOKS_LOADED` | `supported`/`unsupported`/`unknown`/`contradictory` (boolean aliases accepted) | Native plugin hooks loaded in this host session. |
| `TASKPLANE_BRIDGE_HOOKS_LOADED` | same status vocabulary | Repository hook bridge loaded in this host session. |
| `TASKPLANE_REPOSITORY_TRUST` | same status vocabulary | Host-observed repository trust. |
| `TASKPLANE_MANAGED_HOOK_POLICY` | same status vocabulary | Organization policy permits taskPlane hooks. |
| `TASKPLANE_WORKFLOWS_AVAILABLE` | same status vocabulary | Claude Dynamic Workflow transport availability. |
| `TASKPLANE_NATIVE_STRUCTURED_OUTPUT` | same status vocabulary | Native versioned structured-output support. |
| `TASKPLANE_MODEL_SELECTION` | same status vocabulary | Native child-model selection support. |
| `TASKPLANE_EFFORT_SELECTION` | same status vocabulary | Native reasoning-effort selection support. |
| `TASKPLANE_STABLE_HOOK_EVENT_ID` | same status vocabulary | Stable host event identity for exactly-once hook handling. |
| `TASKPLANE_SUPPORTED_MODEL_ALIASES` | JSON string array or comma-separated names | Exact model aliases the host accepts. |
| `TASKPLANE_SUPPORTED_EFFORT_VALUES` | JSON string array or comma-separated names | Exact reasoning-effort values the host accepts. |
| `TASKPLANE_INSTALL_CONTEXT` | `personal` or adapter-defined managed context | Whether managed hook policy is applicable. |
| `TASKPLANE_HOST_VERSION` | bounded version string | Host version attached to the capability snapshot. |
| `TASKPLANE_HOST_RECEIPT_AT` | bounded timestamp string | Observation time supplied by the host. |
| `TASKPLANE_HOST_RECEIPT_REASON` | bounded diagnostic string | Shared explanation for environment-supplied observations. |
| `TASKPLANE_HOOK_PATH` | absolute hook source path | Host-observed hook executable used for lifecycle verification. |

## Model tiers (cost routing)

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_MODEL_CHEAP` | Claude: `haiku`; Codex: inherit | Model id for the `cheap` tier (lens sweep, planner-marked simple tasks). `""` or `inherit` → inherit the session model. | No (cost/quality routing). |
| `TASKPLANE_MODEL_STANDARD` | inherit | Model id for the `standard` tier (execute / evaluate / fix). | No. |
| `TASKPLANE_MODEL_DEEP` | inherit | Model id for the `deep` tier (spec, plan, engineering review, hard lenses). | No. |
| `TASKPLANE_REASONING_CHEAP` | `low` | Native Codex reasoning effort for the `cheap` tier. Invalid values fall back to `low`. | No (cost/quality routing). |
| `TASKPLANE_REASONING_STANDARD` | `medium` | Native Codex reasoning effort for the `standard` tier. | No. |
| `TASKPLANE_REASONING_DEEP` | `high` | Native Codex reasoning effort for the `deep` tier. | No. |
| `TASKPLANE_ENFORCE_DISPATCH` | *(unset — inert)* | Turns on the dispatch-time check in the PreToolUse agent hook: `warn` reports a mismatch; `strict` blocks it and fails closed when verification state/input is corrupt. Native Codex checks the exact emitted task name, taskplane role marker in the delegated message, model, and reasoning effort; a rejected attempt remains pending for an exact retry. Legacy Claude Task dispatch keeps model-tier compatibility. `tp loop verify-dispatch` audits after the fact either way. | **Yes** (`strict`) — it mechanically enforces emitted dispatch identity/routing when enabled. |
| `TASKPLANE_COLLISION_SCREEN` | `on` | `on` blocks registry-known competing delivery skills/agents during exact-workspace governed work; `strict` also blocks unknown foreign identities; `off` is the rollback mode and records an observation without claiming a denial. Format/document helpers remain silently allowed. | **Yes** (`on`/`strict`) — the Skill/Agent hook decision changes. |
| `TASKPLANE_SKILL_ALLOW` | *(format helpers only)* | Comma-separated list of additional non-delivery Skill identities to allow silently. It cannot allow an agent or change the known-competitor registry. | **Yes** — additional Skill identities bypass collision advice. |
| `TASKPLANE_SKILL_STRICT` | *(unset)* | `1`, `true`, `yes`, or `strict` upgrades unknown foreign Skill/agent advice to denial while a governed run is active. | **Yes** — unknown foreign invocations are denied. |
| `TASKPLANE_AUTO_WORKTREE_CLEANUP` | `on` | After an orchestrator-owned Evaluate PASS, merge the exact registered non-variant task branch, persist the merge receipt, retain canonical evidence, and attempt one no-force cleanup. `off`/`manual` returns to manual merge/cleanup and never fabricates a receipt. | **Yes** — enables the post-merge mutation boundary. |

An unknown tier or model value degrades to "inherit" rather than blocking
the loop. `tp onboard --json` reports the resolved `model_tiers` and
`reasoning_tiers` maps.

## Diagnostics

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_DEBUG` | *(unset)* | `1` re-raises unexpected errors in the `tp` CLI with the full traceback instead of the one-line governed error + exit code, for interactive debugging. | No — it changes error *presentation* only; refusals still refuse. |

## Host detection and plugin location (read, not set by you)

| Variable | Set by | Effect |
| --- | --- | --- |
| `CODEX_HOME`, `CODEX_THREAD_ID` | Codex | Presence marks the host as Codex: model tiers inherit by default, reasoning tiers map to low/medium/high, native subagent task dispatch applies, and `workflow_available()` always answers no for Claude Dynamic Workflows. |
| `CLAUDE_CODE_WORKFLOWS` | Claude Code | Truthy presence marks a Dynamic Workflow runtime; consulted by `workflow_available()` only when `TASKPLANE_WORKFLOWS` is unset. |
| `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` | the host's plugin runtime | First-run plugin location supplied by the host. On Codex, cached native hooks prefer `.taskplane/codex-hook.py`; that stable launcher validates the installation family and resolves its newest semantic version on every call. Direct `${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py` execution remains the first-setup/other-host fallback. |

## Seeing what's in effect

- `tp kb where` — the resolved store path.
- `tp share status` — the resolved store mode (plan / private / forced).
- `tp onboard --json` — onboarding state including the resolved model-tier
  map.
- `tp loop verify-dispatch` — after-the-fact audit that dispatches used the
  tiers their briefs carried.
