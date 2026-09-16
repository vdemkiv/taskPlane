# taskplane

**v2.25.0** — one delivery flow, task decomposition, dependency graph and dashboard
for Claude and Codex, with native lens usage and advisory telemetry.

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

**Carry the goal through to working software.** Taskplane helps Claude and Codex
clarify the outcome, design when needed, build, verify, and deliver. The root
orchestrator owns advancement and the final result. Native host permissions
remain authoritative.

## Delivery and observation

- Use `taskplane build` or `taskplane` with a concrete goal to execute the flow.
- Use `taskplane design` for design-only work and `taskplane review` for review.
- Use `taskplane status` for progress, ownership, and available usage.
- Keep work proportionate: no mandatory review count, separate worker per phase,
  signed handoff, graph ledger, or repeated approval for already authorized work.
- Hooks observe activity and available native token counters. They never decide
  whether a tool call may run. Taskplane imposes no token ceiling on delivery.
- Advisory signals flag repeated actions, long stretches without reported
  progress, excessive delegation, and token growth. The orchestrator uses these
  signals to simplify work and focus on the next testable outcome.

The installed `taskplane/tp.py flow` commands provide `start`, `progress`, `finish`,
and `report`. Observations live locally in `.taskplane/flow-events.jsonl`; keep
that file out of product commits. Token figures are deltas between observed
native counters, with incomplete coverage explicit. Unknown usage is never zero.
No prompts, command bodies, or tool responses are copied to this journal.
Telemetry failures do not block delivery.

Claude and Codex use the same task decomposition, dependency graph and
`.taskplane/dashboard.html`. Claude's plugin hooks bind its session automatically;
its usage reader includes cache writes and reads and counts each streamed API
message once. Lens usage comes from native subagent transcripts. Review indexes
use Claude's actual `agent_id` (Codex uses its native agent handle). Hosts without
readable session counters show unavailable coverage while delivery continues.

See [delivery instructions](skills/tp-go/SKILL.md) for the complete procedure.

## Install

Install this repository as the Taskplane plugin in your host's plugin marketplace,
using the permissions available to your account. Codex uses `.codex-plugin/plugin.json`;
Claude uses `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
Hooks are provided by the installed plugin and never require workspace launchers.

## Development

Python 3.10 or newer is required. Run `python3 scripts/ci_local.py` to test the
remaining runtime and build the Codex and Claude packages. Use `--browser` to
include real-browser checks. Packages are written to `dist/`.

See [CLI commands](docs/cli-reference.md) and [lens catalog](docs/lens-catalog.md).

The old contract, gate, lease and phase-runtime system has been removed, including
its tests and compatibility commands. Existing historical files are not read or
migrated by the current flow.
