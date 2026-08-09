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

## Store and state

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_STORE` | *(unset)* | Highest-precedence store override: `repo` forces the in-repo shared store (`.taskplane-kb/` — used by Claude Tag so state survives the ephemeral sandbox), `external` forces the external store. Overrides plan, private mode, and shared config (see `docs/state-spec.md`, "Store resolution"). | **Yes** — silently redirects the entire knowledge store, including loop state and worker-submission evidence, into (or out of) the committed repo. |
| `TASKPLANE_HOME` | `~/.taskplane` | Moves the external store root (all per-project knowledge on a personal plan). `tp kb where` shows the resolved path. | **Yes** — a wrong value redirects the whole knowledge store; the KB, decisions, and loop state follow it. |

## Contract lifecycle (the hook's wall)

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_AGENT_PID` | *(unset)* | Exported by the activating agent; recorded on the contract as the authoritative liveness token. A live PID is **never** idle-released; a dead PID orphans (and auto-releases) the contract immediately. Must be a real positive PID. | **Yes** — with it, liveness beats the TTL in both directions. |
| `TASKPLANE_ORPHAN_TTL` | `3600` (seconds) | Idle backstop for a contract with **no** recorded PID: after this many seconds with no screening activity the contract is treated as orphaned and auto-released (a crashed agent shouldn't lock the workspace forever). A per-contract `orphan_ttl_seconds` field takes precedence. Budget-**exhausted** contracts are a human gate and are never auto-released, regardless of TTL. | **Yes** — a short TTL weakens the wall (an abandoned contract sheds governance sooner); CI sandboxes with long quiet phases may need it *longer*. |
| `TASKPLANE_TASK` | *(unset — legacy single slot)* | Selects the per-task contract slot (`.taskplane/active/<slot>.json`, v2.3.0) governing this process; the dispatch brief exports it to each governed agent so parallel agents cannot overwrite or release each other's contracts. Value must match the task id (`[A-Za-z0-9][A-Za-z0-9._-]*`, ≤ 64 chars). Set with **no** matching slot file → hard refusal (StateError), never a fallback to the legacy slot. | **Yes** — fail-closed by design: an ill-formed or unmatched value blocks rather than letting an agent run under a sibling's contract or escape screening. |
| `TASKPLANE_BARE_ROOT` | *(unset)* | `os.pathsep`-separated **extra** roots for the bare-workspace guard (`tp new` refuses to activate a contract in a bare root / session home). Only ever ADDS protected roots — the built-in set (`~`, `/`, `/root`, `/home/claude`) cannot be removed via this variable. | **Yes**, additive-only — it can strengthen the guard for a new host layout, never weaken it. |
| `TASKPLANE_AUDIT_EVERY` | `5` | Audit-sweep cadence: every Nth completed engineering review runs the full-catalog audit (breadth=all) whose findings are diffed against the lens-routing decision — a finding from an n/a-routed lens is auto-filed as a router regression. Minimum 1 (a lower/garbage value falls back to the default). | No — it tunes how OFTEN the audit runs; the audit itself and the auto-filing are not disableable via this variable. |
| `TASKPLANE_WORKFLOWS` | *(unset)* | Review-wave dispatch path opt-in: `1` lets `tp lens dispatch --emit auto` choose the Claude workflow path (`workflows/review-wave.js`) when the host supports it; `0` is a kill-switch forcing the Task-dispatch path. Unset = conservative Task path. On Codex hosts the Task path is ALWAYS used regardless of this variable. | No — both paths are traced (`review_dispatch_path`), the Task path's output is byte-identical, and no gate is reachable only via workflows. |

## Model tiers (cost routing)

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_MODEL_CHEAP` | Claude: `haiku`; Codex: inherit | Model id for the `cheap` tier (lens sweep, planner-marked simple tasks). `""` or `inherit` → inherit the session model. | No (cost/quality routing). |
| `TASKPLANE_MODEL_STANDARD` | inherit | Model id for the `standard` tier (execute / evaluate / fix). | No. |
| `TASKPLANE_MODEL_DEEP` | inherit | Model id for the `deep` tier (spec, plan, engineering review, hard lenses). | No. |
| `TASKPLANE_ENFORCE_DISPATCH` | *(unset — inert)* | Turns on the dispatch-time model-tier check in the PreToolUse `Task` hook: `warn` logs a mismatch, `strict` blocks the dispatch. Unset/other values: the check is fail-open and does nothing. `tp loop verify-dispatch` audits after the fact either way. | **Yes** (`strict`) — it is the only *mechanical* tier-routing enforcement; unset means routing is verified, not enforced. |

An unknown tier or model value degrades to "inherit" rather than blocking
the loop. `tp onboard --json` reports the resolved `model_tiers` map.

## Diagnostics

| Variable | Default | Effect | Enforcement-relevant |
| --- | --- | --- | --- |
| `TASKPLANE_DEBUG` | *(unset)* | `1` re-raises unexpected errors in the `tp` CLI with the full traceback instead of the one-line governed error + exit code, for interactive debugging. | No — it changes error *presentation* only; refusals still refuse. |

## Host detection and plugin location (read, not set by you)

| Variable | Set by | Effect |
| --- | --- | --- |
| `CODEX_HOME`, `CODEX_THREAD_ID` | Codex | Presence marks the host as Codex: model tiers inherit the session model by default and host-specific onboarding/dashboard fallbacks apply. |
| `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` | the host's plugin runtime | Where the installed plugin lives; hooks, skills, and agent briefs locate `taskplane/tp.py` via `${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}`. |

## Seeing what's in effect

- `tp kb where` — the resolved store path.
- `tp share status` — the resolved store mode (plan / private / forced).
- `tp onboard --json` — onboarding state including the resolved model-tier
  map.
- `tp loop verify-dispatch` — after-the-fact audit that dispatches used the
  tiers their briefs carried.
