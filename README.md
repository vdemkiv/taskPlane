# taskplane

**v2.26.0** — one delivery flow, task decomposition, dependency graph and dashboard
for Claude and Codex, with human phase checkpoints and advisory token usage.

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

**Carry the goal through to working software.** Taskplane helps Claude and Codex
clarify the outcome, design when needed, build, verify, and deliver. The root
orchestrator prepares each result; the human accepts each checkpoint before
advancement. Native host permissions remain separate.

Product, Design, and Engineering always use the shared dependency graph, source
and task decomposition, and dashboard, including standalone requests. Any of
these phases can start the work. Engineering findings can become Product inputs
and proceed through the remaining authorized phases in the same active run.

## Delivery and observation

- Use `taskplane build` or `taskplane` with a concrete goal to execute the flow.
- Use `taskplane design` for design-only work and `taskplane review` for review.
- Use `taskplane status` for progress, ownership, and available usage.
- Every Product, Design, Plan, Build, Evaluate, Engineering and Retro checkpoint
  needs explicit human approval of its concrete output. Reuse valid existing
  acceptance; general task authorization does not accept unseen later output.
- Use a dependency graph with source components, a task DAG and the shared dashboard
  in every phase, including standalone Product, Design and Engineering.
- Mandatory workflow decisions and tool scope are separate from advisory telemetry.
  There is no token ceiling or mandatory reviewer count.
- The shipped `native_workflow` profile enforces Taskplane's workflow commands and
  covered structured-write hooks using local state and observed human responses.
  Host-wide protection is unavailable. An explicit `protected_host` request still
  refuses without a verified host owner; there is no silent fallback.

The `flow` commands expose `start`, `submit`, `decide`, `advance`, `finish`,
`progress`, `attach` and `report`. Start requires exact phase scope and an actual
user-request reference. Submitted evidence, human responses and phase advancement
are separate operations. Decisions bind the conversation and exact reviewed
checkpoint. Changed artifacts invalidate affected acceptance. Missing/corrupt local
state refuses automatic reset; legacy journals never become accepted decisions.

Native-workflow approval provenance is observed by the plugin/orchestrator, not
host-authenticated. The local account can modify its state or fabricate otherwise
valid input. Structured paths are checked where hooks run; opaque commands remain
under native permissions, with visible source drift audited before transitions.
Known live processes block sealing, but full census and uncovered later stdin remain
unknown. The dashboard states **Workflow gates active; host-wide protection unavailable**
and retains all four unavailable host capabilities separately from plugin discovery.

Token figures are native counter deltas with incomplete coverage explicit. Unknown
usage is never zero. Hooks do not copy prompts, command bodies or tool responses
into the observation journal. The local decision store retains only the explicit
response excerpt and provenance needed for checkpoint review. Optional usage failure
does not erase a valid decision. See [CLI contracts](docs/cli-reference.md).

Claude and Codex use the same task decomposition, dependency graph and
`.taskplane/dashboard.html`. Claude's plugin hooks bind its session automatically;
its usage reader includes cache writes and reads and counts each streamed API
message once. Lens usage comes from native subagent transcripts. Review indexes
use Claude's actual `agent_id` (Codex uses its native agent handle). Hosts without
readable session counters show unavailable coverage while delivery continues.

See [delivery instructions](skills/tp-go/SKILL.md) for the complete procedure.

## Install

Install this repository as the Taskplane plugin in your host's plugin marketplace,
using the permissions available to your account. Installing these packages does
enable the native-workflow profile, with the limits above. Protected-host adapters
remain unavailable without a verified owner. Codex uses `.codex-plugin/plugin.json`;
Claude uses `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
Hooks are provided by the installed plugin and never require workspace launchers.

## Development

Python 3.10 or newer is required. Run `python3 scripts/ci_local.py` to test the
remaining runtime and build the Codex and Claude packages. Use `--browser` to
include real-browser checks. Packages are written to `dist/`.

See [CLI commands](docs/cli-reference.md) and [lens catalog](docs/lens-catalog.md).

Historical contract files and observation journals are preserved as evidence;
they are not migrated into trusted human approval records. The ordinary profile
starts a new explicitly scoped run; it never imports old milestones as approvals.
