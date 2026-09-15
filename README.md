# taskplane

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

See [delivery instructions](skills/tp-go/SKILL.md) for the complete procedure.
Older `loop`, `stage`, contract, and gate commands remain for explicit legacy
inspection and compatibility. Their records are retained; normal delivery does
not initialize or depend on that machinery. Documentation below about onboarding
and enforced contracts describes that legacy system.

## Install

How you install taskplane depends on your **account type**, and the paths are
genuinely different — the most common one (an org member on a Team/Enterprise
plan) is also the most restricted. Start with the row that matches you. Install
facts are current per the Claude docs as of August 2026:
[Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
and [Plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces).

### I'm on a Team or Enterprise account (not an org admin)

Honest first: **you cannot add taskplane from GitHub yourself.** On
Team/Enterprise accounts, plugins come from your organization's plugin catalog
(Customize → Plugins), which your org's owners curate; the personal
add-a-marketplace path is not available to you, and on Enterprise your admin may
restrict the catalog further (in Claude Code, managed settings can likewise block
marketplace adds — see [managed marketplace restrictions](https://code.claude.com/docs/en/plugin-marketplaces#managed-marketplace-restrictions)).

Your two real paths:

1. **Ask an org admin to publish taskplane** to your organization's marketplace —
   send them the [exact admin steps](#im-an-org-admin-teamenterprise) below. Once
   published, taskplane appears in your org plugin catalog and you install it from
   there in one click.
2. **File upload, where your org allows it:** download this repository as a ZIP
   from GitHub (**Code → Download ZIP**), then in Claude (Customize → Plugins)
   upload it as a custom plugin file. If no upload option is shown, your org has
   disabled personal plugin uploads — path 1 is the way.

Fallback: taskplane also works on a **personal Pro or Max account** with the
direct GitHub path below, if you want to evaluate it before asking your admin.

### I'm an org admin (Team/Enterprise)

Publish taskplane to your organization's marketplace from **Organization
settings → Plugins** (admin guide: [Manage plugins for your organization](https://support.claude.com/en/articles/13837433-manage-plugins-for-your-organization)):

- **Upload a file:** Add plugins → *Upload a file* → name the marketplace → drag
  in the plugin file (under 50 MB) → Upload. Re-uploading under the same name
  overwrites the previous version.
- **GitHub sync:** organization sync only reads **private or internal**
  repositories (through the Claude GitHub App), so mirror `vdemkiv/taskPlane`
  into a private repository in your org first, then connect it in `owner/repo`
  form. The initial sync runs automatically; toggle *Sync automatically* to pick
  up merged version bumps, or use *Update* for manual syncs.

Then set availability per plugin: **Installed by default**, **Available for
install** (listed in the catalog), **Required** (installed for everyone, not
removable), or **Not available**. Changes reach members on their next session or
plugin refresh. For org-managed Claude Code machines, allowlist the marketplace
via `strictKnownMarketplaces` / `extraKnownMarketplaces` in managed settings so
members' installs resolve without a blocked marketplace add
([settings reference](https://code.claude.com/docs/en/settings)).

### Personal, Pro, or Max account

The direct GitHub marketplace path works as-is. **Claude Desktop or claude.ai
(Chat / Cowork):** Customize → Plugins → **+** in *Personal plugins* →
**Add marketplace** → **"Add from a repository"** → paste
`https://github.com/vdemkiv/taskPlane` → *taskplane* appears → **Install**.

**Claude Code (terminal)** — same thing; the first command adds this repo as the source:

```
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
/reload-plugins
```

### Codex (OpenAI)

**Recommended — published plugin directory:** in the ChatGPT desktop app,
select **Codex**, open **Plugins**, search for **taskplane**, choose **+** to
install it, then start a new Codex task in your repository. In Codex CLI, run
`codex`, open `/plugins`, install/enable **taskplane** from the marketplace tab,
and start a new CLI session. Newly installed skills load in new tasks/sessions.

Plugins are supported in ChatGPT desktop Codex and Codex CLI, not the Codex IDE
extension. See [Codex plugins](https://developers.openai.com/codex/plugins).

**GitHub source — development or catalog fallback:**

```bash
codex plugin marketplace add vdemkiv/taskPlane
codex plugin add taskplane
codex
# inside Codex: /plugins, verify taskplane is enabled, then start a new session
```

Taskplane uses the lifecycle hooks supplied by the enabled plugin. Repository
setup creates an ignored `.taskplane/codex-hook.py` launcher when needed; it
does not register a second set in `.codex/hooks.json`. Review and enable the
plugin hooks in Codex settings. A new task is needed only if their initial
loading still requires it. The launcher resolves the newest valid installed
taskplane engine on every call.
Codex still loads newly changed skill or MCP definitions at a task boundary.
Repository URLs, refs, and pull requests do not require opening a new task:
taskplane acquires them into a managed checkout, inherits the current host
session, and prompts in the same chat when GitHub authentication, a tool, or
storage authorization is needed.
Keep Codex's sandbox and approval controls enabled — taskplane's scope contract
is an additional guardrail, not a replacement.

Requires `git` in your workspace (the gates need a commit snapshot) and
**CPython 3.10 or newer** (standard library only; `python3` on macOS/Linux or
the `py` launcher on Windows). The validated range is CPython 3.10–3.13; see
[docs/configuration.md](docs/configuration.md#supported-python-runtime). Nothing
else to set up.

## Quickstarts

Command-first, one per host. Each ends the same way: say **"set up taskplane"**
and onboarding takes it from there.

### Quickstart: Claude Code (terminal)

```
# personal accounts (org members: install from your org's plugin catalog instead)
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
/reload-plugins
```

Open your project repository (it needs a git commit), then say **"set up taskplane"**.

### Quickstart: Cowork / Claude Desktop

1. Install taskplane by your account path above — org plugin catalog on
   Team/Enterprise, *Personal plugins → Add marketplace* on personal accounts.
2. Connect the folder / repository you want governed (Cowork: attach the folder).
3. Say **"set up taskplane"**.

### Quickstart: Codex

```bash
codex
# inside Codex: /plugins → find taskplane → install and enable
# then start a NEW CLI session
```

`cd` to your project repository first (desktop: open it as a local environment),
then in the new task say **"set up taskplane"**. Use the GitHub source commands
in the Install section only for local development or when the published catalog
is unavailable.

You can also start from a repository URL or pull request. Say what you want to
build or review; taskplane runs its repository precondition automatically,
keeps source under the managed checkout root (never under `.em-review`), and
continues in this task. A recoverable auth/tool/storage requirement appears as
one approval prompt rather than a failed review or terminal handoff.

## Onboarding — the short version

Say **"set up taskplane"**, or just state a goal. Before routing the first request
in a session, and after installation or update, TaskPlane presents onboarding —
including for review, status, and an existing repository. It retains your request
and continues it when setup is ready. `tp onboard` checks a real project folder
(an empty scratch directory or the session root is refused), a git commit,
the run binding, phase configuration, and the configured and observed hook path.
It also checks `tp init`, which scaffolds the four context docs,
scans the dependency graph, and creates the knowledge base. On a brownfield
project, fill `current-state.md` first: it grounds every design review in as-built
reality, and reinventing an existing component is a blocker-class finding.
Onboarding presents editable setup controls inline in the Dashboard style.
Choose whether taskplane knowledge stays private/local
(`personal`, `.taskplane/projects/<repository-key>/knowledge`) or shared
in-repo (`team`/`enterprise`, `.taskplane-kb/knowledge`) — and reports the
resolved model-tier map. Source checkout, private run state, graph/evidence,
and artifacts use separate roots under the project's ignored `.taskplane/`;
existing bound runs retain their recorded location. See
[repository preconditions and hybrid storage](docs/storage-and-repositories.md).
Claude Code users
reload plugins after installation; Chat/Cowork and Codex users start a new
conversation/task only for the host's initial plugin/hook load. Managed
checkouts and later plugin patch versions continue in the same task. Full Claude
and Codex onboarding, sharing modes (`tp share`), model tiers, cost routing, and
context storage: [docs/onboarding.md](docs/onboarding.md).

## Honest about what the guardrails are

The scope/command guardrails are a real, mechanical help for the everyday failure —
an agent that drifts out of its lane or fires a destructive command by mistake. For
matched mutating tool routes the host exposes to plugin hooks, the PreToolUse screen
checks scope, denied commands, and the action budget **before** the call, resolves
`..`/absolute/symlink paths, treats destructive programs (`rm`, `chmod`, …) as
writes, and fails **closed** on a corrupt contract or screening error. Codex
subagent lifecycle hooks are deliberately advisory; optional
`TASKPLANE_ENFORCE_DISPATCH=strict` additionally fails closed unless a native spawn
matches an emitted task name, role marker, model, and reasoning effort. This is
keep-the-agent-on-topic, not a security sandbox: plugin hooks do not intercept tool
routes the host does not expose, arbitrary effects performed inside an allowed
process, or remote side effects. A task that grants `Bash` grants arbitrary code
execution, and no string-screen can fully contain a *determined adversary* — for a
hard boundary, keep Codex/Claude approvals and sandboxing enabled and add OS-level
isolation where needed (the token/$ budget is cooperative in the same way).
Likewise, "a worker cannot advance its own stage" is a
**protocol + audit** guarantee, not process isolation. What holds mechanically is
the **evidence**: a gate only advances on a submission whose fingerprints (changed
source plus the exact evaluator/engineering evidence bytes) still match the
workspace, so even a worker invoking the gate itself cannot pass unproven, stale,
or post-submission-edited work.

## Layout

```
taskplane/
├── taskplane/              # the enforcement core (kernel + hook screener)
├── hooks/hooks.json        # PreToolUse → taskplane screen
├── agents/                 # product/designer/planner/executor/evaluator/fixer/engineering/orchestrator + tp-lens + tp-northstar
├── skills/                 # taskplane façade + tp-go/product/design/build/engineering/northstar/tag/status/help
├── lenses/                 # the 26-lens catalog
├── scripts/                # generators (e.g. the lens-catalog doc)
├── discipline/             # TDD, debugging, worktrees — the operating disciplines
├── docs/                   # state spec + design notes + feature deep-dives
# note: private runtime/knowledge stays ignored in .taskplane/; shared Team/Enterprise knowledge uses .taskplane-kb/
├── PRIVACY.md              # privacy policy (local-only, no telemetry)
└── LICENSE                 # Apache License 2.0
```

## Going deeper

- [docs/cli-reference.md](docs/cli-reference.md) — the complete generated command,
  positional-argument, and flag reference.
- [docs/specialist-routes.md](docs/specialist-routes.md) — the specialist skill
  routes (`tp-design`, `tp-engineering`, `tp-build`, `tp-go`, `tp-product`,
  `tp-northstar`, `tp-status`, `tp-help`), what you'll see during a governed run,
  and the CLI under the hood. Definition and judgment stay separate seats — the
  grader never grades their own spec.
- [docs/onboarding.md](docs/onboarding.md) — the full setup reference.
- [docs/claude-tag.md](docs/claude-tag.md) — taskplane as your org's @Claude in
  Slack channels via Claude Tag (beta), with the `tp-tag` skill.
- [docs/routing-and-flows.md](docs/routing-and-flows.md) — routing v2, component
  decomposition, the review and stage waves with their mandatory byte-identical
  Task fallback, the audit cadence, and Evaluate's direct-evidence judgment —
  each with a dogfood example from this repository.
- [docs/configuration.md](docs/configuration.md) — every environment variable.
- [docs/loop-design.md](docs/loop-design.md) · [docs/authority-matrix.md](docs/authority-matrix.md) ·
  [docs/state-spec.md](docs/state-spec.md) — engine design, who may do what at
  every step, and the on-disk state.

**License:** free and open source under the **Apache License 2.0** — use it
personally or at work, commercially or not, no strings. See `LICENSE`.
**Privacy:** taskplane runs locally, collects nothing, and sends nothing — no
telemetry, no accounts, no network calls of its own; all state stays on your disk,
in the project's ignored `.taskplane/` by default, or deliberately shared in
`.taskplane-kb/` on Team/Enterprise. Explicit storage selections and existing
bound runs retain their recorded locations. See `PRIVACY.md`.
