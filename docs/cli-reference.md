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
| `tp.py budget` | record a cooperative spend estimate, or --grant N more actions (the budget approval gate) |
| `tp.py clear` | deactivate the workspace contract |
| `tp.py context` | session-start context summary |
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
| `tp.py lens dispatch` | ready-to-dispatch lens-agent briefs — one read-only agent per deep lens, fanned out in parallel |
| `tp.py lens list` | every lens in the catalog |
| `tp.py lens route` | decide which lenses a change needs |
| `tp.py lens show` | the full brief for one lens |
| `tp.py loop` | drive the Evaluate-Loop engine |
| `tp.py loop approve` | record a human approval at a checkpoint gate |
| `tp.py loop claim` | a worker claims one wave task into its own worktree |
| `tp.py loop evidence` | assemble every mechanically-derivable fact the evaluate gate will check (suite result, diff, criteria, routed lenses, graph obligations) with the judgment slots left empty for the evaluator to fill |
| `tp.py loop gate` | orchestrator-only: judge the evidence and advance the loop |
| `tp.py loop init` | start an Evaluate-Loop for a goal |
| `tp.py loop next` | print the next stage brief for the active loop |
| `tp.py loop resolve` | resolve a blocked loop: retry, skip, defer or abort |
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
| `tp.py req` | requirements: record, refine, mode, debt |
| `tp.py req debt` | record technical debt taken on knowingly |
| `tp.py req list` | list recorded requirements |
| `tp.py req mode` | pick the delivery mode for a refinement score and change size |
| `tp.py req new` | record a requirement (or a change request) |
| `tp.py req score` | score a requirement's refinement against the bar |
| `tp.py screen` | PreToolUse hook entrypoint (stdin event) |
| `tp.py screen-dispatch` | PreToolUse hook for the Agent tool: verify tier-routed model was passed (inert unless TASKPLANE_ENFORCE_DISPATCH=warn\|strict) |
| `tp.py share` | plan-aware knowledge sharing: status / plan / set private\|shared / push |
| `tp.py share plan` | set the knowledge-storage plan |
| `tp.py share push` | publish private decisions to the shared store |
| `tp.py share set` | set the default visibility of new decisions |
| `tp.py share status` | show what is private and what is shared |
| `tp.py status` | show the active contract |
| `tp.py subagent-start` | SubagentStart lifecycle trace and bounded contract context (stdin event) |
| `tp.py subagent-stop` | SubagentStop lifecycle trace (stdin event; advisory, never a completion gate) |
| `tp.py summary` | simple human view: progress and decisions, while agents keep the detailed harness |
| `tp.py track` | multi-track workstreams |
| `tp.py track close` | close a track |
| `tp.py track list` | list every track |
| `tp.py track new` | open a new track |
| `tp.py track switch` | make another track the active one |
| `tp.py version` | print the plugin version; --verify cross-checks every derived version surface against the single source (.codex-plugin/plugin.json) — CI-callable, exit 1 on drift |

## `tp.py budget`

record a cooperative spend estimate, or --grant N more actions (the budget approval gate)

| Flag | Value | What it does |
| --- | --- | --- |
| `--grant` | N | raise the enforced action ceiling by N — for the human / ungoverned main session after approving more budget (a governed agent cannot grant itself) |
| `--spent` | SPENT | cooperative $ estimate (advisory) |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py clear`

deactivate the workspace contract

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py context`

session-start context summary

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py dashboard`

render the mission-control view

| Flag | Value | What it does |
| --- | --- | --- |
| `--out` | OUT | also write the fragment to this path |
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

ready-to-dispatch lens-agent briefs — one read-only agent per deep lens, fanned out in parallel

| Flag | Value | What it does |
| --- | --- | --- |
| `--all` | flag | full catalog: routed lenses run deep, the rest as a quick sweep — nothing skipped |
| `--artifact-type` | ARTIFACT_TYPE | route on an artifact instead of the diff — 'strategy' summons the advisory (board) tier |
| `--base` | BASE | git base to diff against (default HEAD) |
| `--dashboard` | flag | print the live lens-wave progress board instead of the JSON briefs (render this BEFORE dispatch) |
| `--emit` | one of: workflow, task, auto | dispatch path: 'workflow' wraps the briefs as /taskplane:review-wave args, 'task' prints today's Task-dispatch payload byte-identically, 'auto' (default) picks workflow only when the host runtime is detected (Codex: always task) |
| `--max-actions` | MAX_ACTIONS | per-agent action ceiling written into each dispatched lens brief (default 30) |
| `--only` | ONLY | comma list — dispatch only these lenses |
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
| `--all` | flag | full catalog: routed lenses run deep, the rest as a quick sweep — nothing skipped |
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
| `--by` | BY | who approved and where (e.g. a Slack user + quoted reply) — recorded in trace + KB |
| `--force` | flag | pass a BLOCKED refinement gate anyway |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py loop claim`

a worker claims one wave task into its own worktree

Positional arguments:

- `task_id`

| Flag | Value | What it does |
| --- | --- | --- |
| `--agent-workspace` | AGENT_WORKSPACE (required) | the worker's worktree — its contract activates there |

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
| `--note` | NOTE | one-line note recorded with the gate decision |
| `--req` | REQ | attach requirement R-id to the loop before DoR evaluation (design anchor) |
| `--task` | TASK | task id (parallel execute waves) |

## `tp.py loop init`

start an Evaluate-Loop for a goal

Positional arguments:

- `goal` (zero or more)

| Flag | Value | What it does |
| --- | --- | --- |
| `--checkpoints` | CHECKPOINTS | comma list: plan,em (default both) |
| `--design` | flag | run the Design Contract + human design approval before implementation planning |
| `--design-only` | flag | stop after the human approves the Design Contract instead of continuing to Plan/Build/Review |
| `--force` | flag | replace an in-flight loop (the old loop.json is archived first — without this flag re-init refuses) |
| `--max-fix-cycles` | MAX_FIX_CYCLES | fix cycles the loop may run before it escalates to the human (default 2) |
| `--parallel` | flag | execute waves of scope-disjoint tasks concurrently, one governed agent per task |
| `--req` | REQ | anchor the loop to a requirement R-id |
| `--spec` | SPEC | path to an existing spec (skips PM) |

## `tp.py loop next`

print the next stage brief for the active loop

| Flag | Value | What it does |
| --- | --- | --- |
| `--emit` | one of: workflow, task, auto | stage dispatch surface (R-0004): 'workflow' wraps an evaluate/fix stage payload as ONE ready-to-run stage-wave workflow invocation, 'task' prints today's payload byte-identically (the mandatory fallback and the only Codex path), 'auto' consults workflow_available() (default) |
| `--req` | REQ | attach requirement R-id to the loop before DoR evaluation (design anchor) |

## `tp.py loop resolve`

resolve a blocked loop: retry, skip, defer or abort

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
| `--emit` | one of: workflow, task, auto | stage dispatch surface (R-0004): 'workflow' wraps the EXECUTE wave as ONE ready-to-run execute-wave workflow invocation covering every wave entry, 'task' prints today's wave payload byte-identically (the mandatory fallback and the only Codex path), 'auto' consults workflow_available() (default) |

## `tp.py new`

create + activate a Task Contract

Positional arguments:

- `goal` (one or more)

| Flag | Value | What it does |
| --- | --- | --- |
| `--budget` | BUDGET | cooperative $ ceiling |
| `--deny` | DENY (repeatable) | extra deny command (repeatable) |
| `--max-actions` | MAX_ACTIONS | hook-enforced action ceiling (default 60) |
| `--read-only` | flag | review/plan role — block filesystem writes |
| `--scope` | SCOPE | comma-separated scope globs (relative) |
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
| `--json` | flag | print the readiness report instead of the widget |
| `--out` | OUT | also write the fragment to this path |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py ready`

Definition-of-Ready entry gate

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py req`

requirements: record, refine, mode, debt

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

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

## `tp.py screen`

PreToolUse hook entrypoint (stdin event)

## `tp.py screen-dispatch`

PreToolUse hook for the Agent tool: verify tier-routed model was passed (inert unless TASKPLANE_ENFORCE_DISPATCH=warn\|strict)

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

## `tp.py status`

show the active contract

| Flag | Value | What it does |
| --- | --- | --- |
| `--workspace` | WORKSPACE | repo root this command operates on (default: the cwd) |

## `tp.py subagent-start`

SubagentStart lifecycle trace and bounded contract context (stdin event)

## `tp.py subagent-stop`

SubagentStop lifecycle trace (stdin event; advisory, never a completion gate)

## `tp.py summary`

simple human view: progress and decisions, while agents keep the detailed harness

| Flag | Value | What it does |
| --- | --- | --- |
| `--json` | flag | print the summary as JSON |
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
