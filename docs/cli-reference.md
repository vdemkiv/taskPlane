# `tp.py` CLI reference

> This file is GENERATED from `tp.py`'s live argparse tree — don't
> hand-edit. Regenerate with
> `python3 taskplane/tp.py help --md > docs/cli-reference.md`.
> CI regenerates and diffs this file on every push, so a stale copy fails
> the build.

Every subcommand of `tp.py` and every long flag it accepts, walked
from the parser the CLI actually dispatches with: a flag cannot be listed
here without existing, and cannot exist without being listed. The generator
REFUSES to emit when a subcommand or a long flag carries no help text, so
the documentation ratchet is enforced at generation time rather than by an
exemption list.

argparse's own `-h` / `--help` is accepted by every command below and is
not repeated in the tables.

## Commands

| Command | What it does |
| --- | --- |
| `tp.py ack` | discharge an obligation the engine issued (WS-F evals); --status lists what is open |
| `tp.py budget` | record a cooperative spend estimate, or --grant N more actions (the budget approval gate) |
| `tp.py clear` | deactivate the workspace contract |
| `tp.py command` | durable governed host-command lifecycle |
| `tp.py command cancel` | cancel a durable command |
| `tp.py command launch` | launch direct argv through the durable command runtime |
| `tp.py command reconnect` | reconnect a durable command |
| `tp.py command show` | show a durable command |
| `tp.py command wait` | wait a durable command |
| `tp.py context` | session-start context summary |
| `tp.py contracts` | list every active contract slot, including stale ones a union is silently applying |
| `tp.py dashboard` | render the mission-control view |
| `tp.py decision` | decision registry — structured ADRs with lifecycle, links and supersede chains |
| `tp.py decision accept` | move a proposed decision to accepted |
| `tp.py decision list` | list recorded decisions |
| `tp.py decision new` | record a new decision (ADR) |
| `tp.py decision show` | print one decision in full |
| `tp.py decision supersede` | mark a decision replaced by a newer one |
| `tp.py dod` | Definition-of-Done exit gate (+ kb lint) |
| `tp.py findings` | render a review findings dashboard (all severities, filterable) from a findings JSON |
| `tp.py gc` | prune runtime artifacts (tombstones, stale locks, orphaned tmp) — never governance records |
| `tp.py graph` | dependency graph: scan, impact, contracts, requirement links, visualization |
| `tp.py graph contract` | record an explicit distributed boundary; consumers depend on the contract |
| `tp.py graph edge` | record an edge the scanner cannot see |
| `tp.py graph html` | render the graph as a standalone HTML view |
| `tp.py graph impact` | what a change reaches: blast radius across the graph |
| `tp.py graph link` | product layer: link a requirement to the modules that plan/realize it |
| `tp.py graph scan` | rebuild the dependency graph from the working tree |
| `tp.py help` | print this help; with --md, the generated markdown CLI reference (docs/cli-reference.md) |
| `tp.py init` | scaffold context docs + KB + graph |
| `tp.py kb` | knowledge base (decisions) |
| `tp.py kb lint` | check the knowledge base for malformed or empty records |
| `tp.py kb list` | list every recorded decision |
| `tp.py kb migrate` | move a legacy in-repo knowledge/ to the external store, untrack it, and gitignore it |
| `tp.py kb record` | record a decision in the knowledge base |
| `tp.py kb retrieve` | recall the decisions that govern given files or tags |
| `tp.py kb where` | show the external store path for this project (and whether a legacy in-repo KB remains) |
| `tp.py lens` | route lenses for a change |
| `tp.py lens dispatch` | ready-to-dispatch selected quick review brief; normal governed review uses 4–5 concurrent leased slots |
| `tp.py lens list` | every lens in the catalog |
| `tp.py lens route` | decide which lenses a change needs |
| `tp.py lens show` | the full brief for one lens |
| `tp.py loop` | drive the Evaluate-Loop engine |
| `tp.py loop approve` | record a human approval at a checkpoint gate |
| `tp.py loop authorize` | derive routine authority for a real host/facade flow from the bound consolidated receipt |
| `tp.py loop claim` | a worker claims one wave task into its own worktree |
| `tp.py loop command` | run a durable command through the live loop root |
| `tp.py loop command cancel` | cancel a durable command |
| `tp.py loop command launch` | launch direct argv through the durable command runtime |
| `tp.py loop command reconnect` | reconnect a durable command |
| `tp.py loop command show` | show a durable command |
| `tp.py loop command wait` | wait a durable command |
| `tp.py loop evidence` | assemble every mechanically-derivable fact the evaluate gate will check (suite result, diff, criteria, routed lenses, graph obligations) with the judgment slots left empty for the evaluator to fill |
| `tp.py loop gate` | orchestrator-only: judge the evidence and advance the loop |
| `tp.py loop guide` | before pass submission, check deterministic workflow facts and return one bounded drift correction |
| `tp.py loop host-input` | consume one trusted-session host event JSON object from stdin through the governed human-input boundary |
| `tp.py loop init` | start an Evaluate-Loop for a goal |
| `tp.py loop next` | print the next stage brief for the active loop |
| `tp.py loop replan` | human: archive frozen tasks and return to Plan for a corrected plan plus fresh approval |
| `tp.py loop resolve` | resolve a blocked loop: retry, pass, skip, defer or abort |
| `tp.py loop retro` | print the loop retrospective |
| `tp.py loop select` | A/B selection gate: pick the variant that ships (or 'hybrid') |
| `tp.py loop status` | show the loop's stage, tasks and gates |
| `tp.py loop submit` | worker submits evidence without transitioning state; the orchestrator gates |
| `tp.py loop verify-dispatch` | audit whether dispatched agents used the models the briefs resolved (tier routing) |
| `tp.py loop wave` | print the EXECUTE wave: one brief per scope-disjoint task |
| `tp.py new` | create + activate a Task Contract |
| `tp.py north-star` | on-demand strategic review: print the project's north star, or render a strategic note |
| `tp.py onboard` | cold-start readiness — folder + git snapshot + init; renders the onboarding dashboard |
| `tp.py ready` | Definition-of-Ready entry gate |
| `tp.py repository` | automatic source precondition: resolve, authenticate, acquire, checkout, verify, and resume |
| `tp.py repository migrate` | register clean legacy .em-review/scratch clones without moving or deleting anything |
| `tp.py repository prepare` | prepare a local repository or remote pull request |
| `tp.py repository resume` | apply an explicit user action and resume the same run |
| `tp.py repository status` | print one canonical run manifest |
| `tp.py req` | requirements: record, refine, mode, debt |
| `tp.py req amend` | revise the same requirement after Product requests changes |
| `tp.py req debt` | record technical debt taken on knowingly |
| `tp.py req list` | list recorded requirements |
| `tp.py req mode` | pick the delivery mode for a refinement score and change size |
| `tp.py req new` | record a requirement (or a change request) |
| `tp.py req score` | score a requirement's refinement against the bar |
| `tp.py req signoff` | record the human Product gate |
| `tp.py review` | open a review in ONE call — tools, target pin, graph, impact, contract, obligations, routing, runnability and the ready-to-dispatch briefs, as one JSON payload |
| `tp.py review activate-contract` | verify one signed leased-review action and activate only its producer slot |
| `tp.py review collect` | validate leased lens results and publish one canonical findings revision |
| `tp.py review evidence` | record approved dynamic validation or render evidence |
| `tp.py review option` | record the human's optional dynamic review/render choice |
| `tp.py review resume` | apply one explicit user decision and continue the same repository preflight and review |
| `tp.py review sandbox` | create a disposable writable PR copy for validation-only build repair and dynamic checks |
| `tp.py review signoff` | record the human decision for a collected standalone review |
| `tp.py review start` | establish the facts and activate the read-only contract |
| `tp.py review validate` | run one argv-only dynamic check inside the registered validation sandbox and record its evidence |
| `tp.py screen` | PreToolUse hook entrypoint (stdin event) |
| `tp.py screen-dispatch` | PreToolUse hook for the Agent tool: verify tier-routed model was passed (inert unless TASKPLANE_ENFORCE_DISPATCH=warn\|strict) |
| `tp.py screen-render` | PreToolUse hook for the inline-render tool: record that a render RAN, and with which bytes. Observes only — never denies |
| `tp.py screen-skill` | PreToolUse collision gate for Skill invocations during governed work |
| `tp.py session-verify` | Stop/SessionEnd hook: exit 2 listing artifacts this run owes and never showed |
| `tp.py share` | plan-aware knowledge sharing: status / plan / set private\|shared / push |
| `tp.py share plan` | set the knowledge-storage plan |
| `tp.py share push` | publish private decisions to the shared store |
| `tp.py share set` | set the default visibility of new decisions |
| `tp.py share status` | show what is private and what is shared |
| `tp.py stage` | drive isolated stage lifecycle and bounded handoffs |
| `tp.py stage history` | read a bounded page of immutable stage summaries |
| `tp.py stage resume` | create a fresh attempt in an active stage root |
| `tp.py stage reuse` | explicitly authorize non-default artifact reuse |
| `tp.py stage split` | close a parent and atomically create isolated children |
| `tp.py stage start` | start a root or verified successor stage |
| `tp.py stage terminalize` | record one immutable terminal outcome |
| `tp.py stage terminalize-and-start` | atomically terminalize a predecessor and start its verified successor |
| `tp.py status` | show project loop status and the active contract |
| `tp.py subagent-start` | SubagentStart lifecycle trace, bounded contract context, and leased review-child identity binding (stdin event) |
| `tp.py subagent-stop` | SubagentStop lifecycle trace (stdin event; advisory, never a completion gate) |
| `tp.py summary` | simple human view: progress and decisions, while agents keep the detailed harness |
| `tp.py target` | what is being reviewed — acquire a pull request, pin the checkout, or check that git and gh are actually available |
| `tp.py target fetch` | fetch a pull request into this checkout and pin it (git fetch pull/N/head) |
| `tp.py target pin` | record what THIS checkout is — origin, head, base, dirty state, fingerprint |
| `tp.py target show` | print the pinned target record |
| `tp.py target tools` | is git present, is gh present and authenticated — a remote PR review needs both |
| `tp.py track` | multi-track workstreams |
| `tp.py track close` | close a track |
| `tp.py track list` | list every track |
| `tp.py track new` | open a new track |
| `tp.py track switch` | make another track the active one |
| `tp.py version` | print the plugin version; --verify cross-checks every derived version surface against the single source (.codex-plugin/plugin.json) — CI-callable, exit 1 on drift |
| `tp.py worktree-cleanup` | replay receipt-scoped post-merge cleanup once; never force-removes or deletes branches |
| `tp.py yield` | what the harness returns (lens yield and where findings are caught) — advisory, gates nothing |
| `tp.py yield mark` | record a human verdict on one finding: acted or dismissed |

## `tp.py ack`

discharge an obligation the engine issued (WS-F evals); --status lists what is open

Positional arguments:

- `id` (optional) — obligation id, e.g. o-1a2b3c4d5e

| Flag | Value | What it does |
| --- | --- | --- |
| `--delivered` | PATH | discharge by DELIVERING the engine's artifact file (SendUserFile / the host's artifact channel) instead of retyping it inline — same bytes, same fingerprint, none of the re-authoring cost |
| `--evidence` | EVIDENCE | one line on how it was shown |
| `--fingerprint` | FINGERPRINT | content fingerprint of what was actually shown (defaults to the artifact the obligation names) |
| `--status` | flag | print issued / acknowledged / open / mismatched |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py budget`

record a cooperative spend estimate, or --grant N more actions (the budget approval gate)

| Flag | Value | What it does |
| --- | --- | --- |
| `--approved-by` | APPROVED_BY | human chat identity authorizing this budget grant |
| `--grant` | N | raise the enforced action ceiling by N — for the human / ungoverned main session after approving more budget (a governed agent cannot grant itself) |
| `--spent` | SPENT | cooperative $ estimate (advisory) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py clear`

deactivate the workspace contract

| Flag | Value | What it does |
| --- | --- | --- |
| `--all` | flag | release EVERY active slot, not just this process's — the way out when a wave leaked contracts |
| `--approved-by` | APPROVED_BY | human chat identity authorizing recovery past an exhausted budget |
| `--slot` | SLOT | release one named slot (see `tp contracts`) without setting TASKPLANE_TASK |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py command`

durable governed host-command lifecycle

## `tp.py command cancel`

cancel a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py command launch`

launch direct argv through the durable command runtime

Positional arguments:

- `argv` — direct command argv after --

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--cwd` | CWD | command directory within the workspace |
| `--deadline-seconds` | DEADLINE_SECONDS | optional execution deadline from launch |
| `--host` | one of: claude, codex | host adapter contract |
| `--run-id` | RUN_ID (required) | canonical governed run identity |
| `--task-id` | TASK_ID (required) | canonical governed task identity |
| `--wave-id` | WAVE_ID | optional governed command-wave identity |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py command reconnect`

reconnect a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py command show`

show a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py command wait`

wait a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--consumer` | CONSUMER | durable delivery receipt consumer |
| `--timeout` | TIMEOUT | single blocking wait timeout |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py context`

session-start context summary

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py contracts`

list every active contract slot, including stale ones a union is silently applying

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py dashboard`

render the mission-control view

| Flag | Value | What it does |
| --- | --- | --- |
| `--out` | OUT | write the standalone report to this path |
| `--paged` | flag | emit ordered <=14KB pages (JSON) for reliable inline rendering + a never-skippable headline |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision`

decision registry — structured ADRs with lifecycle, links and supersede chains

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision accept`

move a proposed decision to accepted

Positional arguments:

- `id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision list`

list recorded decisions

| Flag | Value | What it does |
| --- | --- | --- |
| `--status` | STATUS_FILTER | list only decisions in this lifecycle state |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision new`

record a new decision (ADR)

Positional arguments:

- `title`

| Flag | Value | What it does |
| --- | --- | --- |
| `--alternative` | ALTERNATIVE (repeatable) | repeatable: 'option \| gained \| given up' |
| `--context` | CONTEXT | the situation that forced the decision |
| `--decision` | DECISION | what was decided, in one sentence |
| `--modules` | MODULES | comma-separated module globs this decision governs (drives always-on context injection) |
| `--rationale` | RATIONALE | why this option won over the alternatives |
| `--req` | REQ | linked requirement R-XXXX |
| `--status` | one of: proposed, accepted | lifecycle state to record it in (default: accepted) |
| `--supersedes` | SUPERSEDES | decision id this one replaces |
| `--tags` | TAGS | comma-separated tags for retrieval |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision show`

print one decision in full

Positional arguments:

- `id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py decision supersede`

mark a decision replaced by a newer one

Positional arguments:

- `id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--by` | BY (required) | id of the decision that replaces this one |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py dod`

Definition-of-Done exit gate (+ kb lint)

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py findings`

render a review findings dashboard (all severities, filterable) from a findings JSON

| Flag | Value | What it does |
| --- | --- | --- |
| `--file` | FILE | findings JSON (default .em-review/findings.json) |
| `--html` | flag | emit ONE self-contained HTML document (palette and dark mode included) — the documented fallback when the host cannot render inline fragments |
| `--out` | OUT | also write the fragment to this path |
| `--paged` | flag | emit ordered <=14KB pages (JSON) for reliable inline rendering + a never-skippable headline |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py gc`

prune runtime artifacts (tombstones, stale locks, orphaned tmp) — never governance records

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py graph`

dependency graph: scan, impact, contracts, requirement links, visualization

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py graph contract`

record an explicit distributed boundary; consumers depend on the contract

Positional arguments:

- `name`

| Flag | Value | What it does |
| --- | --- | --- |
| `--consumer` | CONSUMER (repeatable) | module that consumes the contract (repeatable) |
| `--provider` | PROVIDER | module that provides the contract |

## `tp.py graph edge`

record an edge the scanner cannot see

Positional arguments:

- `src`
- `dst`

| Flag | Value | What it does |
| --- | --- | --- |
| `--confidence` | one of: high, medium, low | how sure the edge is (default medium) |
| `--kind` | KIND | edge kind, e.g. runtime or build (default runtime) |
| `--note` | NOTE | why this edge exists |

## `tp.py graph html`

render the graph as a standalone HTML view

| Flag | Value | What it does |
| --- | --- | --- |
| `--base` | BASE | git base to diff against (default HEAD) |
| `--files` | FILES | comma-separated changed files to highlight (default: git diff + untracked) |
| `--focus` | DEPTH | crop to the changed set plus everything within DEPTH dependency hops — the same map, small enough to render inline in chat |
| `--fragment` | flag | emit an embeddable fragment (the same page, carried byte-for-byte in an srcdoc iframe) so the graph can be shown inline instead of as a file |
| `--out` | OUT | write the HTML here instead of stdout |

## `tp.py graph impact`

what a change reaches: blast radius across the graph

| Flag | Value | What it does |
| --- | --- | --- |
| `--base` | BASE | git base to diff against (default HEAD) |
| `--boundary` | one of: contract-only, stop, expand | what the walk does at a distributed boundary (default contract-only) |
| `--contract-depth` | CONTRACT_DEPTH | hops to keep walking past a contract boundary (default 1) |
| `--depth` | DEPTH | dependency hops to walk locally (default 3) |
| `--files` | FILES | comma-separated changed files (default: git diff + untracked) |
| `--json` | flag | print the impact set as JSON |
| `--requirement-depth` | REQUIREMENT_DEPTH | hops to walk into the requirement layer (default 1) |

## `tp.py graph link`

product layer: link a requirement to the modules that plan/realize it

| Flag | Value | What it does |
| --- | --- | --- |
| `--files` | FILES (required) | comma-separated files or scope globs |
| `--keep` | flag | append instead of replacing existing links |
| `--kind` | one of: planned, realizes | link kind: a planned or a realized requirement (default realizes) |
| `--req` | R-XXXX (required) | the requirement being linked |

## `tp.py graph scan`

rebuild the dependency graph from the working tree

| Flag | Value | What it does |
| --- | --- | --- |
| `--decompose` | flag | derive the component layer (graph.json 'components'; R-0003 contract:component-map) |
| `--json` | flag | print machine JSON (the backward-compatible default) |
| `--strict` | flag | persist the normal fail-open record, then return nonzero when any graph producer is degraded |
| `--text` | flag | print a concise human graph-quality report |

## `tp.py help`

print this help; with --md, the generated markdown CLI reference (docs/cli-reference.md)

| Flag | Value | What it does |
| --- | --- | --- |
| `--md` | flag | print the deterministic markdown CLI reference walked from the live argparse tree, instead of argparse's own help |

## `tp.py init`

scaffold context docs + KB + graph

| Flag | Value | What it does |
| --- | --- | --- |
| `--plan` | one of: personal, team, enterprise | choose knowledge storage at init — personal is private/external; team/enterprise is shared in-repo |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py kb`

knowledge base (decisions)

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py kb lint`

check the knowledge base for malformed or empty records

## `tp.py kb list`

list every recorded decision

## `tp.py kb migrate`

move a legacy in-repo knowledge/ to the external store, untrack it, and gitignore it

## `tp.py kb record`

record a decision in the knowledge base

Positional arguments:

- `title`

| Flag | Value | What it does |
| --- | --- | --- |
| `--context` | CONTEXT | the situation the decision was made in |
| `--decision` | DECISION | what was decided, in one sentence |
| `--files` | FILES | comma-separated context file globs |
| `--rationale` | RATIONALE | why this option won |
| `--tags` | TAGS | comma-separated tags for retrieval |

## `tp.py kb retrieve`

recall the decisions that govern given files or tags

| Flag | Value | What it does |
| --- | --- | --- |
| `--files` | FILES | comma-separated file globs — retrieve decisions that govern them |
| `--limit` | LIMIT | most decisions to return (default 5) |
| `--tags` | TAGS | comma-separated tags to match |

## `tp.py kb where`

show the external store path for this project (and whether a legacy in-repo KB remains)

## `tp.py lens`

route lenses for a change

## `tp.py lens dispatch`

ready-to-dispatch selected quick review brief; normal governed review uses 4–5 concurrent leased slots

| Flag | Value | What it does |
| --- | --- | --- |
| `--all` | flag | explicit user diagnostic: full catalog with deep routed lenses; never used automatically |
| `--artifact-type` | ARTIFACT_TYPE | route on an artifact instead of the diff — 'strategy' summons the advisory (board) tier |
| `--base` | BASE | git base to diff against (default HEAD) |
| `--dashboard` | flag | print the live lens-wave progress board instead of the JSON briefs (render this BEFORE dispatch) |
| `--emit` | one of: workflow, task, auto | dispatch path: 'workflow' wraps the briefs as /taskplane:review-wave args, 'task' prints today's Task-dispatch payload byte-identically, 'auto' (default) picks workflow only when the host runtime is detected (Codex: always task) |
| `--max-actions` | MAX_ACTIONS | per-agent action ceiling written into each dispatched lens brief. Automatic quick review defaults to 30; an explicit user `--all` request may create 45-action deep briefs. An explicit value applies to every brief. |
| `--only` | ONLY | comma list — dispatch only these lenses |
| `--resume` | flag | re-dispatch ONLY the lanes that have no findings.json yet — an interrupted wave costs the lenses that did not land, not all of them |
| `--skip` | SKIP | comma list — do not dispatch these lenses |
| `--task-type` | TASK_TYPE | declared task type (feature, bugfix, refactor, ...) — widens the routed set |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py lens list`

every lens in the catalog

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | print the catalog as JSON |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py lens route`

decide which lenses a change needs

| Flag | Value | What it does |
| --- | --- | --- |
| `--all` | flag | explicit user diagnostic: full catalog with deep routed lenses; never used automatically |
| `--artifact-type` | ARTIFACT_TYPE | route on an artifact instead of the diff — 'strategy' summons the advisory (board) tier |
| `--base` | BASE | git base to diff against |
| `--json` | flag | print the routing decision as JSON |
| `--only` | ONLY | comma list — only these lenses |
| `--skip` | SKIP | comma list — skip these lenses |
| `--task-type` | TASK_TYPE | declared task type (feature, bugfix, refactor, ...) — widens the routed set |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py lens show`

the full brief for one lens

Positional arguments:

- `id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop`

drive the Evaluate-Loop engine

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop approve`

record a human approval at a checkpoint gate

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--by` | BY | who approved and where (e.g. a Slack user + quoted reply) — recorded in trace + KB |
| `--force` | flag | pass a BLOCKED refinement gate anyway |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop authorize`

derive routine authority for a real host/facade flow from the bound consolidated receipt

Positional arguments:

- `flow` — routine flow identity (facade, delivery, product, design, build, engineering, status, help, north_star or tag_slack)

## `tp.py loop claim`

a worker claims one wave task into its own worktree

Positional arguments:

- `task_id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--agent-workspace` | AGENT_WORKSPACE (required) | the worker's worktree — its contract activates there |
| `--by` | BY | human identity required with --advisory |

## `tp.py loop command`

run a durable command through the live loop root

## `tp.py loop command cancel`

cancel a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop command launch`

launch direct argv through the durable command runtime

Positional arguments:

- `argv` — direct command argv after --

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--cwd` | CWD | command directory within the workspace |
| `--deadline-seconds` | DEADLINE_SECONDS | optional execution deadline from launch |
| `--host` | one of: claude, codex | host adapter contract |
| `--run-id` | RUN_ID (required) | canonical governed run identity |
| `--task-id` | TASK_ID (required) | canonical governed task identity |
| `--wave-id` | WAVE_ID | optional governed command-wave identity |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop command reconnect`

reconnect a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop command show`

show a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop command wait`

wait a durable command

Positional arguments:

- `handle` — opaque durable command handle

| Flag | Value | What it does |
| --- | --- | --- |
| `--authorization` | AUTHORIZATION (required) | actor/session identity bound to the handle |
| `--consumer` | CONSUMER | durable delivery receipt consumer |
| `--timeout` | TIMEOUT | single blocking wait timeout |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop evidence`

assemble every mechanically-derivable fact the evaluate gate will check (suite result, diff, criteria, routed lenses, graph obligations) with the judgment slots left empty for the evaluator to fill

| Flag | Value | What it does |
| --- | --- | --- |
| `--task` | TASK | task id (default: the loop's current task) |
| `--write` | flag | also drop the skeleton at .eval/verdict.json when no verdict is already there (never overwrites) |

## `tp.py loop gate`

orchestrator-only: judge the evidence and advance the loop

Positional arguments:

- `outcome`

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--by` | BY | human identity required with --advisory |
| `--note` | NOTE | one-line note recorded with the gate decision |
| `--req` | REQ | attach requirement R-id to the loop before DoR evaluation (design anchor) |
| `--task` | TASK | task id (parallel execute waves) |

## `tp.py loop guide`

before pass submission, check deterministic workflow facts and return one bounded drift correction

| Flag | Value | What it does |
| --- | --- | --- |
| `--task` | TASK | task id (parallel execute waves) |

## `tp.py loop host-input`

consume one trusted-session host event JSON object from stdin through the governed human-input boundary

## `tp.py loop init`

start an Evaluate-Loop for a goal

Positional arguments:

- `goal` (zero or more)

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | continue with visibly advisory screen enforcement |
| `--by` | BY | human identity required with --advisory and with TASKPLANE_STAGE_NATIVE=new-run; the new-run value becomes the root stage authority.actor and must use identifier syntax (for example human:vdemkiv; no spaces) |
| `--checkpoints` | CHECKPOINTS | comma list: plan,em (default both) |
| `--design` | flag | run the Design Contract + human design approval before implementation planning |
| `--design-only` | flag | stop after the human approves the Design Contract instead of continuing to Plan/Build/Review |
| `--force` | flag | replace an in-flight loop (the old loop.json is archived first — without this flag re-init refuses) |
| `--max-fix-cycles` | MAX_FIX_CYCLES | fix cycles the loop may run before it escalates to the human (default 2) |
| `--parallel` | flag | execute waves of scope-disjoint tasks concurrently, one governed agent per task |
| `--req` | REQ | anchor the loop to a requirement R-id; TASKPLANE_STAGE_NATIVE=new-run requires an exact existing requirement |
| `--reuse-approved-design` | flag | start at Plan from an unchanged completed design-only loop with the same requirement/spec and attributable --by authority |
| `--spec` | SPEC | path to an existing spec (skips PM) |

## `tp.py loop next`

print the next stage brief for the active loop

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--by` | BY | human identity required with --advisory |
| `--emit` | one of: workflow, task, auto | stage dispatch surface (R-0004): 'workflow' wraps an evaluate/fix stage payload as ONE ready-to-run stage-wave workflow invocation, 'task' prints today's payload byte-identically (the mandatory fallback and the only Codex path), 'auto' consults workflow_available() (default) |
| `--req` | REQ | attach requirement R-id to the loop before DoR evaluation (design anchor) |

## `tp.py loop replan`

human: archive frozen tasks and return to Plan for a corrected plan plus fresh approval

| Flag | Value | What it does |
| --- | --- | --- |
| `--by` | BY (required) | human approving the return to Plan |
| `--reason` | REASON (required) | configuration defect or changed decision |

## `tp.py loop resolve`

resolve a blocked loop: retry, pass, skip, defer or abort

Positional arguments:

- `decision`

## `tp.py loop retro`

print the loop retrospective

## `tp.py loop select`

A/B selection gate: pick the variant that ships (or 'hybrid')

Positional arguments:

- `choice` — variant letter, task id, or 'hybrid'

| Flag | Value | What it does |
| --- | --- | --- |
| `--note` | NOTE | the WHY — recorded to the KB |

## `tp.py loop status`

show the loop's stage, tasks and gates

## `tp.py loop submit`

worker submits evidence without transitioning state; the orchestrator gates

Positional arguments:

- `outcome`

| Flag | Value | What it does |
| --- | --- | --- |
| `--note` | NOTE | one-line evidence note recorded with the submission |
| `--task` | TASK | task id (parallel execute waves) |

## `tp.py loop verify-dispatch`

audit whether dispatched agents used the models the briefs resolved (tier routing)

## `tp.py loop wave`

print the EXECUTE wave: one brief per scope-disjoint task

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--by` | BY | human identity required with --advisory |
| `--emit` | one of: workflow, task, auto | stage dispatch surface (R-0004): 'workflow' wraps the EXECUTE wave as ONE ready-to-run execute-wave workflow invocation covering every wave entry, 'task' prints today's wave payload byte-identically (the mandatory fallback and the only Codex path), 'auto' consults workflow_available() (default) |

## `tp.py new`

create + activate a Task Contract

Positional arguments:

- `goal` (one or more)

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | continue with visibly advisory screen enforcement |
| `--allow-foreign-state` | ROOT (repeatable) | repeatable exact signed foreign-state root to include; requires --by and is recorded on the contract |
| `--base` | REF | diff base for the target pin (e.g. origin/main) |
| `--budget` | BUDGET | cooperative $ ceiling |
| `--by` | BY | human identity required with --advisory or a foreign-state override |
| `--deny` | DENY (repeatable) | extra deny command (repeatable) |
| `--fetch` | flag | with a PR --target, fetch pull/N/head into this checkout first (needs git; `gh` is what supplies the PR's title, body and discussion) |
| `--max-actions` | MAX_ACTIONS | hook-enforced action ceiling (default 60) |
| `--max-tokens` | N | EFFECTIVE-token ceiling for this contract (cache reads x0.1, cache writes x2, output x5 — the weighting cost actually follows). Counts what the host recorded, so it tracks spend where the action ceiling only counts tool calls. Unset = action ceiling only, exactly as before. |
| `--owes` | RUN_TYPE | seed the artifacts this run type owes as BINDING obligations (e.g. `review`): recorded before the work starts, and taskplane's own completion commands stay blocked until each is shown |
| `--read-only` | flag | review/plan role — block filesystem writes |
| `--scope` | SCOPE | comma-separated scope globs (relative) |
| `--target` | SPEC | what is being reviewed — a PR url, OWNER/REPO#N, or a ref. Pins this checkout (origin, head, base, dirty state) so the findings can cite the tree they came from and the completion gate can check it |
| `--tests` | TESTS | DoD test command |
| `--tools` | TOOLS | comma-separated allowed tools (default: any) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |
| `--write-allow` | GLOB (repeatable) | in read-only mode, dirs that ARE writable (e.g. .em-review/**) — repeatable |

## `tp.py north-star`

on-demand strategic review: print the project's north star, or render a strategic note

| Flag | Value | What it does |
| --- | --- | --- |
| `--out` | OUT | also write the fragment to this path |
| `--render` | RENDER | a strategic-note JSON to render as the inline widget fragment |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py onboard`

cold-start readiness — folder + git snapshot + init; renders the onboarding dashboard

| Flag | Value | What it does |
| --- | --- | --- |
| `--install-codex-hooks` | flag | install/refresh the repo-local Codex lifecycle hook bridge before reporting readiness |
| `--json` | flag | print the readiness report instead of the widget |
| `--out` | OUT | also write the fragment to this path |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py ready`

Definition-of-Ready entry gate

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py repository`

automatic source precondition: resolve, authenticate, acquire, checkout, verify, and resume

## `tp.py repository migrate`

register clean legacy .em-review/scratch clones without moving or deleting anything

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py repository prepare`

prepare a local repository or remote pull request

Positional arguments:

- `spec` — PR URL, OWNER/REPO#N, ref, or local target

| Flag | Value | What it does |
| --- | --- | --- |
| `--run-id` | RUN_ID | optional stable run id for idempotent retry |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py repository resume`

apply an explicit user action and resume the same run

| Flag | Value | What it does |
| --- | --- | --- |
| `--action-id` | ACTION_ID (required) | exact pending user-action id |
| `--by` | BY (required) | human chat identity approving the action |
| `--response` | one of: approve, retry, initialize, cancel (required) | the user's decision for the pending action |
| `--run-id` | RUN_ID (required) | run-id from the needs_user response |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py repository status`

print one canonical run manifest

| Flag | Value | What it does |
| --- | --- | --- |
| `--run-id` | RUN_ID (required) | canonical repository/run manifest id |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py req`

requirements: record, refine, mode, debt

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py req amend`

revise the same requirement after Product requests changes

Positional arguments:

- `R-XXXX`

| Flag | Value | What it does |
| --- | --- | --- |
| `--acceptance` | ACCEPTANCE (repeatable) | replace acceptance criteria (repeatable) |
| `--clear-open` | flag | close every open product question |
| `--files` | FILES | replace comma-separated context globs |
| `--functional` | FUNCTIONAL (repeatable) | replace functional statements (repeatable) |
| `--nfr` | LENS=STATEMENT (repeatable) | add or replace an NFR by lens (repeatable) |
| `--open` | OPEN (repeatable) | replace open questions (repeatable) |

## `tp.py req debt`

record technical debt taken on knowingly

Positional arguments:

- `title`

| Flag | Value | What it does |
| --- | --- | --- |
| `--files` | FILES | comma-separated file globs the debt lives in |
| `--follow-up` | FOLLOW_UP | what would pay the debt off |
| `--reason` | REASON | why the debt was taken on |
| `--req` | REQ | requirement id this debt belongs to |
| `--tags` | TAGS | comma-separated tags for retrieval |

## `tp.py req list`

list recorded requirements

## `tp.py req mode`

pick the delivery mode for a refinement score and change size

| Flag | Value | What it does |
| --- | --- | --- |
| `--refinement` | REFINEMENT (required) | the requirement's refinement score (0.0-1.0) |
| `--size` | SIZE (required) | files changed |

## `tp.py req new`

record a requirement (or a change request)

Positional arguments:

- `title`

| Flag | Value | What it does |
| --- | --- | --- |
| `--acceptance` | ACCEPTANCE (repeatable) | an acceptance criterion (repeatable) |
| `--changed-from` | CHANGED_FROM | R-id this change request derives from |
| `--contract` | RELATION:CONTRACT (repeatable) | repeatable requirement boundary: provides, consumes, or changes a named API/event/data/runtime contract |
| `--depends` | R-XXXX (repeatable) | R-id this requirement depends on (repeatable) — recorded as a product edge in the graph |
| `--files` | FILES | comma-separated context file globs |
| `--functional` | FUNCTIONAL (repeatable) | a functional statement (repeatable) |
| `--nfr` | LENS=STATEMENT (repeatable) | a non-functional requirement by lens (repeatable) |
| `--open` | OPEN (repeatable) | an open question (repeatable) |
| `--tags` | TAGS | comma-separated tags for retrieval |

## `tp.py req score`

score a requirement's refinement against the bar

Positional arguments:

- `id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--files` | FILES | comma-separated changed-file globs |
| `--high-cost` | flag | hard-block below threshold (irreversible work) |
| `--task-type` | TASK_TYPE | declared task type — sets the refinement bar this requirement is scored against |
| `--threshold` | THRESHOLD | refinement score the requirement must reach (default 0.6) |

## `tp.py req signoff`

record the human Product gate

Positional arguments:

- `R-XXXX`
- `decision`

| Flag | Value | What it does |
| --- | --- | --- |
| `--by` | BY (required) | the human approval or change-request words |
| `--note` | NOTE | optional decision rationale |

## `tp.py review`

open a review in ONE call — tools, target pin, graph, impact, contract, obligations, routing, runnability and the ready-to-dispatch briefs, as one JSON payload

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review activate-contract`

verify one signed leased-review action and activate only its producer slot

| Flag | Value | What it does |
| --- | --- | --- |
| `--expected-identity` | EXPECTED_IDENTITY (required) | URL-safe encoded exact worker/lease identity |
| `--signed-action` | SIGNED_ACTION (required) | URL-safe encoded signed ReviewKernel action |
| `--task-slot` | TASK_SLOT (required) | exact signed producer slot visible to host screen |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review collect`

validate leased lens results and publish one canonical findings revision

| Flag | Value | What it does |
| --- | --- | --- |
| `--no-publish` | flag | skip the external artifact-store snapshot (tests and isolated calibration only) |
| `--run-id` | RUN_ID | select one active review when several starts coexist |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review evidence`

record approved dynamic validation or render evidence

Positional arguments:

- `kind`
- `status`

| Flag | Value | What it does |
| --- | --- | --- |
| `--detail` | DETAIL | bounded evidence summary |
| `--receipt` | RECEIPT | optional host message/turn reference; receipt content is resolved from the host transcript |
| `--run-id` | RUN_ID (required) | active review run |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review option`

record the human's optional dynamic review/render choice

When ReviewKernel returns `status: needs_user`, execute the selected
`action.choices[*].command` verbatim through the stable workspace launcher.
Use `python3` on macOS/Linux and `py` on Windows:

```bash
python3 .taskplane/codex-hook.py review option dynamic --run-id <run-id>
python3 .taskplane/codex-hook.py review option dynamic-render --run-id <run-id>
python3 .taskplane/codex-hook.py review option static --run-id <run-id>
py .taskplane/codex-hook.py review option dynamic --run-id <run-id>
py .taskplane/codex-hook.py review option dynamic-render --run-id <run-id>
py .taskplane/codex-hook.py review option static --run-id <run-id>
```

Do not substitute `review resume`: that command resolves repository
preflight decisions, not review-execution mode. Render the opening canonical
dashboard from `visuals.workflow_and_wave.inline.path` and the collected
canonical dashboard from `visuals.final_dashboard.inline.path`.

Positional arguments:

- `selection`

| Flag | Value | What it does |
| --- | --- | --- |
| `--by` | BY | deprecated display attribution; receipt actor is authoritative |
| `--receipt` | RECEIPT | optional host message/turn reference; receipt content is resolved from the host transcript |
| `--run-id` | RUN_ID (required) | active review run |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review resume`

apply one explicit user decision and continue the same repository preflight and review

| Flag | Value | What it does |
| --- | --- | --- |
| `--action-id` | ACTION_ID (required) | exact pending user-action id |
| `--advisory` | flag | continue with visibly advisory screen enforcement |
| `--by` | BY (required) | the user's approving/cancelling chat identity |
| `--goal` | GOAL | contract goal text after preflight resumes |
| `--max-actions` | MAX_ACTIONS | action ceiling for the resumed review contract |
| `--max-tokens` | MAX_TOKENS | effective-token ceiling for the resumed review |
| `--response` | one of: approve, retry, initialize, cancel (required) | the user's decision for the pending action |
| `--run-id` | RUN_ID (required) | run-id from the needs_user preflight response |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review sandbox`

create a disposable writable PR copy for validation-only build repair and dynamic checks

| Flag | Value | What it does |
| --- | --- | --- |
| `--run-id` | RUN_ID (required) | active review run |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review signoff`

record the human decision for a collected standalone review

Positional arguments:

- `decision`

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | acknowledge degraded screen enforcement |
| `--by` | BY (required) | the human approval or change-request words |
| `--note` | NOTE | optional decision rationale |
| `--run-id` | RUN_ID | select the collected review run |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review start`

establish the facts and activate the read-only contract

Positional arguments:

- `spec` (optional) — PR url, OWNER/REPO#N, or a ref

| Flag | Value | What it does |
| --- | --- | --- |
| `--advisory` | flag | continue with visibly advisory screen enforcement |
| `--base` | BASE | diff base ref |
| `--by` | BY | human identity required with --advisory |
| `--fetch` | flag | fetch pull/N/head into this checkout first |
| `--goal` | GOAL | contract goal text (default: derived) |
| `--max-actions` | MAX_ACTIONS | action ceiling for the review contract (default 40). Prefer --max-tokens: an action cost ~11k effective tokens on the measured review, with a two-order-of-magnitude spread |
| `--max-tokens` | MAX_TOKENS | effective-token ceiling for the review contract |
| `--run-id` | RUN_ID | resume or deterministically name the repository preflight run |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py review validate`

run one argv-only dynamic check inside the registered validation sandbox and record its evidence

Positional arguments:

- `command` — command argv after --; no shell interpretation

| Flag | Value | What it does |
| --- | --- | --- |
| `--cwd` | CWD | sandbox-relative working directory |
| `--run-id` | RUN_ID (required) | active review run |
| `--timeout` | TIMEOUT | command timeout in seconds (maximum 1800) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py screen`

PreToolUse hook entrypoint (stdin event)

## `tp.py screen-dispatch`

PreToolUse hook for the Agent tool: verify tier-routed model was passed (inert unless TASKPLANE_ENFORCE_DISPATCH=warn\|strict)

## `tp.py screen-render`

PreToolUse hook for the inline-render tool: record that a render RAN, and with which bytes. Observes only — never denies

## `tp.py screen-skill`

PreToolUse collision gate for Skill invocations during governed work

## `tp.py session-verify`

Stop/SessionEnd hook: exit 2 listing artifacts this run owes and never showed

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py share`

plan-aware knowledge sharing: status / plan / set private\|shared / push

## `tp.py share plan`

set the knowledge-storage plan

Positional arguments:

- `value`

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py share push`

publish private decisions to the shared store

| Flag | Value | What it does |
| --- | --- | --- |
| `--ids` | IDS | comma-separated private decision ids; default = everything unpublished |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py share set`

set the default visibility of new decisions

Positional arguments:

- `value`

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py share status`

show what is private and what is shared

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage`

drive isolated stage lifecycle and bounded handoffs

### Closed stage-command request

Every stage subcommand accepts one UTF-8 JSON object from
`--request FILE` or standard input with `--request -`. The object is
bounded to 1,048,576 bytes and may declare schema
`taskplane.stage-command/v1`. Unknown fields and predecessor runtime
context (agents, conversations, event logs, tool transcripts, leases,
runtime state, workspaces, paths, or execution roots) are rejected.
The table distinguishes fields required on every call from optional
or outcome-dependent fields. Fields joined by `OR` are exclusive
alternatives. Values remain subject to identity, authority, lifecycle,
and artifact validation.

| Stage command | Required fields | Optional or conditional fields |
| --- | --- | --- |
| `history` | `run_id` | `schema`, `cursor`, `limit` |
| `start` | `stage`, `expected_revision`, `operation_id`, `authority` | `schema`, `expected_predecessor_fingerprints` (required for a successor; omit for a root), `foreground`, `declared_scope` |
| `reuse` | `stage` OR `successor_stage`, `expected_revision`, `operation_id`, `expected_predecessor_fingerprints`, `authority`, `reason`, `actor` | `schema`, `foreground`, `declared_scope` |
| `resume` | `run_id`, `stage_id`, `expected_head_fingerprint`, `expected_revision`, `operation_id`, `authority` | `schema`, `attempt_id`, `declared_scope` |
| `terminalize` | `run_id`, `stage_id`, `expected_head_fingerprint`, `expected_revision`, `operation_id`, `outcome`, `actor`, `terminalized_at`, `authority` | `schema`, `reason_code` + `reason` (required for closed/discarded; forbidden for done), `completed_deliverables` + `completion_evidence` (all deliverables and non-empty evidence required for done), `handoff_manifest` |
| `terminalize-and-start` | `predecessor_stage_id`, `stage` OR `successor_stage`, `expected_head_fingerprint`, `expected_revision`, `operation_id`, `outcome`, `actor`, `terminalized_at`, `authority` | `schema`, `run_id`, `reason_code` + `reason` (required for closed/discarded; forbidden for done), `completed_deliverables` + `completion_evidence` (all deliverables and non-empty evidence required for done), `foreground`, `declared_scope` |
| `split` | `run_id`, `stage_id`, `expected_head_fingerprint`, `expected_revision`, `operation_id`, `child_specs`, `actor`, `terminalized_at`, `reason`, `authority` | `schema`, `declared_scopes` |

#### Automatic pristine new-run bootstrap

Set `TASKPLANE_STAGE_NATIVE=new-run` before `tp.py loop init`. Supply
an exact existing requirement with `--req` and the accountable human
with `--by`; use stage identifier syntax such as
`human:vdemkiv` (letters, digits, `.`, `_`, `:`, or `-`; no spaces).
That value becomes the root stage `authority.actor`. A
stable session identity must already be present in
`TASKPLANE_SESSION_ID`, `CODEX_THREAD_ID`, or `CLAUDE_SESSION_ID`.
The workspace must already have a governed locator bound to an
unmigrated v3 run with an exact target revision.

Only that successful normal initialization mints the private
pristine-new-run marker; do not add, copy, or infer the marker later.

The first normal `tp.py loop next` atomically creates, commits, and
dispatches one deterministic root stage through the internal
lifecycle. It derives root authority from verified governed run facts
and stores the bounded input handoff
internally. Replaying the same call reuses the committed operation.
The loop caller must not create stage JSON, authority JSON, a handoff
artifact, or a separate `tp.py stage start` request.
`tp.py loop wave` never bootstraps a root: it requires the already
bound v4 journey and fails closed when that binding is missing.

New-run initialization also refuses any existing singleton history,
including terminal history and `--force`; use a fresh governed run.
Initialization refuses without singleton or stage mutation when the
requirement is missing or unknown, `--by` is missing, stable session
identity is missing, the governed locator is missing, the bound run is
not unmigrated v3, or its exact target revision is unavailable.
Bootstrap also refuses when `new-run` was enabled only after init, the
private marker is absent, the singleton is no longer structurally
pristine, legacy progress exists, or the bound locator/run/store
identity becomes mismatched or corrupt. After the v4 root commit, the
singleton retains a durable run binding; losing or corrupting its
locator or store remains a
fail-closed refusal rather than a fallback to legacy dispatch.

#### Closed nested shapes

A `taskplane.stage/v1` request value is a closed active-stage object.
It requires every key shown below except `fingerprint`, which may be
omitted and is recomputed canonically. `requirement` has exactly `id`,
`revision`, and `fingerprint`; `design` is either null or has exactly
`revision` and `fingerprint`. An input stage must have `state: active`,
`outcome: null`, `default_consumable: true`, and `terminal: null`.
Collections must already be sorted and unique. The execution root is
always `execution-<stage_id>`.

```json
{
  "schema": "taskplane.stage/v1",
  "run_id": "run-r0004",
  "stage_id": "stage-evaluate-001",
  "requirement": {
    "id": "R-0004",
    "revision": "4",
    "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "design": {
    "revision": "2",
    "fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "stage_kind": "evaluate",
  "parent_stage_ids": [],
  "predecessor_stage_ids": [
    "stage-build-001"
  ],
  "input_manifest_ref": {
    "schema": "taskplane.artifact-reference/v1",
    "kind": "stage-handoff",
    "fingerprint": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "bytes": 1024,
    "locator": "artifact://stage-handoff/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "transport": "artifact-reference"
  },
  "execution_root_id": "execution-stage-evaluate-001",
  "deliverables": [
    "evaluation-verdict"
  ],
  "selected_artifacts": [
    {
      "schema": "taskplane.artifact-reference/v1",
      "kind": "source",
      "fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "digest": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "bytes": 4096,
      "locator": "artifact://source/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "transport": "artifact-reference"
    }
  ],
  "budget": {
    "attempt_limit": 3,
    "token_limit": 8000
  },
  "dependencies": [
    "t06-cross-host-rollout"
  ],
  "contracts": [
    "contract:stage-artifact-handoff"
  ],
  "authority": {
    "schema": "taskplane.stage-authority-binding/v1",
    "run_id": "run-r0004",
    "repository_id": "github.com/vdemkiv/taskplane",
    "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
    "worktree_id": "t06-worktree",
    "target_revision": "1111111111111111111111111111111111111111",
    "worktree_revision": "2222222222222222222222222222222222222222",
    "requirement_id": "R-0004",
    "requirement_revision": "4",
    "design_revision": "2",
    "design_fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "actor": "human:operator",
    "session_id": "codex-thread-1",
    "authority_revision": 7,
    "authority_fingerprint": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  },
  "state": "active",
  "outcome": null,
  "default_consumable": true,
  "terminal": null,
  "created_at": "2026-08-21T18:00:00Z",
  "aggregate_revision": 1
}
```

`authority` is a closed `taskplane.stage-authority-binding/v1` object
with exactly the keys shown above. All identity and revision values
must match the live run, checkout, requirement, design, actor, and
session. When `design` is null, both authority design fields are null.
The top-level request `authority` and the stage's nested `authority`
must describe the same current binding.

Every `input_manifest_ref`, `selected_artifacts` entry, and
`completion_evidence` entry is a closed
`taskplane.artifact-reference/v1` object with exactly `schema`, `kind`,
`fingerprint`, `digest`, `bytes`, `locator`, and `transport`. The
locator is `artifact://<kind>/<fingerprint>`, both hashes are 64
lowercase hexadecimal characters, bytes is a non-negative integer,
and transport is `artifact-reference`. For example:

```json
{
  "schema": "taskplane.artifact-reference/v1",
  "kind": "test-report",
  "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "bytes": 128,
  "locator": "artifact://test-report/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "transport": "artifact-reference"
}
```

`declared_scope` is either absent or a closed object with exactly
`scope_paths` and `out_of_scope_paths`. Each is a sorted, unique array
of at most 64 non-empty strings. `declared_scopes` on `split` is an
object keyed by generated child stage id whose values have this exact
shape.

#### Runnable request templates

History needs no lifecycle payload. Save this as `history.json` and
replace `run-r0004` with an existing run id:

```json
{
  "schema": "taskplane.stage-command/v1",
  "run_id": "run-r0004",
  "cursor": "0",
  "limit": 25
}
```

```bash
tp.py stage history --request history.json
```

Atomic predecessor terminalization and successor startup use one
shape-complete request and one receipt:

```bash
tp.py stage terminalize-and-start --request request.json
```

Save the following as `request.json`. Before running it, replace the
example identifiers, revisions, hashes, byte counts, and timestamps
with values from the live predecessor, stored handoff, artifact, and
authority receipts. Replace whole values; do not use string
placeholders or local paths:

```json
{
  "schema": "taskplane.stage-command/v1",
  "predecessor_stage_id": "stage-build-001",
  "successor_stage": {
    "schema": "taskplane.stage/v1",
    "run_id": "run-r0004",
    "stage_id": "stage-evaluate-001",
    "requirement": {
      "id": "R-0004",
      "revision": "4",
      "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    },
    "design": {
      "revision": "2",
      "fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    },
    "stage_kind": "evaluate",
    "parent_stage_ids": [],
    "predecessor_stage_ids": [
      "stage-build-001"
    ],
    "input_manifest_ref": {
      "schema": "taskplane.artifact-reference/v1",
      "kind": "stage-handoff",
      "fingerprint": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "bytes": 1024,
      "locator": "artifact://stage-handoff/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "transport": "artifact-reference"
    },
    "execution_root_id": "execution-stage-evaluate-001",
    "deliverables": [
      "evaluation-verdict"
    ],
    "selected_artifacts": [
      {
        "schema": "taskplane.artifact-reference/v1",
        "kind": "source",
        "fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "digest": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "bytes": 4096,
        "locator": "artifact://source/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "transport": "artifact-reference"
      }
    ],
    "budget": {
      "attempt_limit": 3,
      "token_limit": 8000
    },
    "dependencies": [
      "t06-cross-host-rollout"
    ],
    "contracts": [
      "contract:stage-artifact-handoff"
    ],
    "authority": {
      "schema": "taskplane.stage-authority-binding/v1",
      "run_id": "run-r0004",
      "repository_id": "github.com/vdemkiv/taskplane",
      "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
      "worktree_id": "t06-worktree",
      "target_revision": "1111111111111111111111111111111111111111",
      "worktree_revision": "2222222222222222222222222222222222222222",
      "requirement_id": "R-0004",
      "requirement_revision": "4",
      "design_revision": "2",
      "design_fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "actor": "human:operator",
      "session_id": "codex-thread-1",
      "authority_revision": 7,
      "authority_fingerprint": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    },
    "state": "active",
    "outcome": null,
    "default_consumable": true,
    "terminal": null,
    "created_at": "2026-08-21T18:00:00Z",
    "aggregate_revision": 1
  },
  "expected_head_fingerprint": "9999999999999999999999999999999999999999999999999999999999999999",
  "expected_revision": 12,
  "operation_id": "build-to-evaluate-001",
  "outcome": "done",
  "actor": "human:operator",
  "terminalized_at": "2026-08-21T18:00:00Z",
  "completed_deliverables": [
    "build-commit",
    "declared-tests"
  ],
  "completion_evidence": [
    {
      "schema": "taskplane.artifact-reference/v1",
      "kind": "test-report",
      "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "bytes": 128,
      "locator": "artifact://test-report/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "transport": "artifact-reference"
    }
  ],
  "foreground": true,
  "authority": {
    "schema": "taskplane.stage-authority-binding/v1",
    "run_id": "run-r0004",
    "repository_id": "github.com/vdemkiv/taskplane",
    "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
    "worktree_id": "t06-worktree",
    "target_revision": "1111111111111111111111111111111111111111",
    "worktree_revision": "2222222222222222222222222222222222222222",
    "requirement_id": "R-0004",
    "requirement_revision": "4",
    "design_revision": "2",
    "design_fingerprint": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "actor": "human:operator",
    "session_id": "codex-thread-1",
    "authority_revision": 7,
    "authority_fingerprint": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  },
  "declared_scope": {
    "scope_paths": [
      "taskplane/**"
    ],
    "out_of_scope_paths": []
  }
}
```

The command atomically records the predecessor's immutable terminal
outcome and starts the successor from its verified bounded handoff.
A validation or authority failure changes neither stage.

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage history`

read a bounded page of immutable stage summaries

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage resume`

create a fresh attempt in an active stage root

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage reuse`

explicitly authorize non-default artifact reuse

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage split`

close a parent and atomically create isolated children

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage start`

start a root or verified successor stage

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage terminalize`

record one immutable terminal outcome

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py stage terminalize-and-start`

atomically terminalize a predecessor and start its verified successor

| Flag | Value | What it does |
| --- | --- | --- |
| `--request` | FILE\|- (required) | closed stage-command JSON object; '-' reads standard input |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py status`

show project loop status and the active contract

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py subagent-start`

SubagentStart lifecycle trace, bounded contract context, and leased review-child identity binding (stdin event)

## `tp.py subagent-stop`

SubagentStop lifecycle trace (stdin event; advisory, never a completion gate)

## `tp.py summary`

simple human view: progress and decisions, while agents keep the detailed harness

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | print the summary as JSON |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py target`

what is being reviewed — acquire a pull request, pin the checkout, or check that git and gh are actually available

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | print the target record as JSON |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py target fetch`

fetch a pull request into this checkout and pin it (git fetch pull/N/head)

Positional arguments:

- `spec` — PR url, OWNER/REPO#N, or #N

| Flag | Value | What it does |
| --- | --- | --- |
| `--base` | BASE | diff base (default: the remote's default branch) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py target pin`

record what THIS checkout is — origin, head, base, dirty state, fingerprint

| Flag | Value | What it does |
| --- | --- | --- |
| `--base` | BASE | diff base ref |
| `--spec` | SPEC | the target this checkout represents (PR url, ref) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py target show`

print the pinned target record

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | JSON report |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py target tools`

is git present, is gh present and authenticated — a remote PR review needs both

| Flag | Value | What it does |
| --- | --- | --- |
| `--install` | flag | install gh via this host's package manager |
| `--json` | flag | JSON report |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py track`

multi-track workstreams

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py track close`

close a track

Positional arguments:

- `name`

| Flag | Value | What it does |
| --- | --- | --- |
| `--status` | STATUS | status to close the track in (default done) |

## `tp.py track list`

list every track

## `tp.py track new`

open a new track

Positional arguments:

- `name`
- `goal` (zero or more)

| Flag | Value | What it does |
| --- | --- | --- |
| `--req` | REQ | requirement R-id this track delivers |

## `tp.py track switch`

make another track the active one

Positional arguments:

- `name`

## `tp.py version`

print the plugin version; --verify cross-checks every derived version surface against the single source (.codex-plugin/plugin.json) — CI-callable, exit 1 on drift

| Flag | Value | What it does |
| --- | --- | --- |
| `--verify` | flag | cross-check every derived version surface against the single source; exit 1 on drift |

## `tp.py worktree-cleanup`

replay receipt-scoped post-merge cleanup once; never force-removes or deletes branches

Positional arguments:

- `action` — bounded maintenance action

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py yield`

what the harness returns (lens yield and where findings are caught) — advisory, gates nothing

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | emit the raw report instead of the table |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py yield mark`

record a human verdict on one finding: acted or dismissed

Positional arguments:

- `finding` — the finding fingerprint from `tp yield`
- `verdict` — durable human disposition

| Flag | Value | What it does |
| --- | --- | --- |
| `--by` | BY | who decided (attribution, like gates) |
| `--note` | NOTE | why, in one line |
