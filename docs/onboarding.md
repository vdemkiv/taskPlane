# Onboarding (`tp onboard`) — the full setup

This is the complete onboarding reference: what `tp onboard` checks before it
hands you to a governed run, the Claude and Codex host-specific paths, the
knowledge storage / sharing-mode choice, and the two setup decisions (model
tiers and context storage) that decide how efficiently the whole system runs. The
README's [Onboarding summary](../README.md) covers the short version.

## The three green checks

Say **taskplane help** for the tour, or just state a goal — `taskplane` routes
it and runs onboarding for you on a fresh folder. `tp onboard` shows the
onboarding dashboard and won't hand you to a governed run until three
checks are green for a local target. A repository URL or pull request first
runs the automatic repository precondition, which creates a verified managed
checkout and then applies these checks there:

1. **A real folder to work in** — connect/open your project (an empty
   scratch dir or the session root is refused: a contract scoped there
   would govern everything).
2. **A git commit to diff against** — the gates fail closed without a
   snapshot. For a new local folder, taskplane asks permission to initialize
   and commit it, then resumes the same run.
3. **`tp init`** — scaffolds the four context docs
   (`product.md` / `tech-stack.md` / `workflow.md` / `current-state.md`),
   scans the dependency graph, and creates the external knowledge base.

   **Fill `current-state.md` first on a brownfield project.** It is the
   as-built inventory — what already runs, what data/integrations exist,
   what hardware is in place. Once filled, it is injected into every task
   brief (`knowledge.current_state`), and the design lenses (architecture,
   trade-offs, services selection, time-to-market) ground their reviews in
   it: a design is judged as a *delta against what exists*, and
   **reinventing an existing component or contradicting as-built reality is
   a blocker-class finding**. Record the big as-built choices as accepted
   decisions too (`tp decision new "<title>" --modules <globs>`) so they
   govern future work automatically.

## Claude onboarding

1. Install taskplane through the path allowed by your account. Personal users
   can add the GitHub marketplace; Team/Enterprise members install from their
   organization's catalog or an allowed file upload. The README's
   [Install section](../README.md#install) has the exact decision tree.
2. In Claude Code, run `/reload-plugins` after installation. In Claude Chat or
   Cowork, start a new conversation if the newly installed skills are not yet
   visible.
3. Open/attach a local target, or name a repository URL or pull request in the
   prompt. Prompt **"set up taskplane"** or **"use taskplane for …"**.
4. If a prerequisite needs authentication, a tool, storage access, or local
   initialization, answer taskplane's exact prompt. It resumes this run; it
   does not send you to an external terminal or new conversation.
5. Choose whether taskplane knowledge stays **private/local** (`personal`) or
   is **shared in the repository** (`team`/`enterprise`). This is storage and
   collaboration policy, not a model choice.
6. Let taskplane initialize the context documents. On an existing project,
   fill `current-state.md` first so Product and Design reason from the as-built
   system rather than inventing a parallel one.
7. State the goal. taskplane stops for explicit Design approval when used,
   plan approval, and final sign-off; Claude never self-approves those gates.

Claude Code loads taskplane's bundled hook after plugin reload. Chat/Cowork
still uses the same engine-owned state, graph, evidence, and human gates, while
tool interception remains limited to what that host exposes. Keep Claude's own
permissions and sandbox controls enabled.

## Codex onboarding

1. Install and enable **taskplane** from the published plugin directory: in the
   desktop app use **Codex → Plugins**; in Codex CLI use `/plugins`. Then start
   a **new** Codex task/session. The GitHub marketplace commands in the README
   remain the development/catalog fallback.
2. For local code, make the repository the working folder. You may instead
   name a repository URL or pull request; taskplane acquires and verifies a
   managed checkout automatically inside the current environment.
3. Prompt **"set up taskplane"** or **"use taskplane for …"**. The plugin runs
   `tp onboard --json` before governed work. On first use it installs the
   portable `.codex/hooks.json` workspace configuration plus an ignored local
   `.taskplane/codex-hook.py` bridge. A new task is required only for this
   one-time initial host hook load, never for checkout/auth/storage recovery.
4. Answer any prerequisite prompt in chat. taskplane runs only its stored
   bounded action after approval and resumes the same run.
5. Choose whether taskplane knowledge stays **private/local** (`personal`) or
   is **shared in the repository** (`team`/`enterprise`). This is a storage
   choice; it is not tied to the name of your ChatGPT or Codex subscription.
6. Let taskplane initialize the context documents, then fill
   `current-state.md` first for an existing project. State the goal; taskplane
   will stop at Design approval when that phase is used, plan approval, and
   final sign-off for your explicit decision.

For independent briefs, Codex uses its native subagent task orchestration:
each taskplane brief provides an exact `task_name`, taskplane role and
`role_marker`, the exact `role_instructions` file path, optional model, and
`reasoning_effort`. The driver includes that exact role marker plus the complete
role instructions and payload in the delegated message, spawns scope-disjoint
work concurrently, waits in bounded intervals for every requested result, and
interrupts/escalates a stalled or mis-scoped agent rather than declaring partial
work complete.
Repo-local `SubagentStart`/`SubagentStop` hooks add bounded context and lifecycle traces; the
PreToolUse screen and evidence gates remain authoritative. For a long run you
may start Goal mode with `/goal`; it changes neither permissions nor gates.

When inline HTML widgets are unavailable, Codex still relays the plain-text
`HEADLINE:` and provides the managed run's dashboard by reference (legacy
unmanaged workspaces use `.taskplane/dashboard.html`). The governance state
and human gates do not depend on widget support.

## Host setup at a glance

| Host | Activate the plugin | Repository step | Reload boundary |
| --- | --- | --- | --- |
| Claude Code | GitHub or managed marketplace | Open a local repo or name a repo/PR URL | `/reload-plugins` |
| Claude Chat / Cowork | Personal or organization plugin catalog | Attach the folder in Cowork when local files are required | New conversation if needed |
| ChatGPT desktop Codex | Published Plugins directory | Open local code or name a repo/PR URL | One new task only for initial hook load |
| Codex CLI | `/plugins` marketplace tab | Run from local code or name a repo/PR URL | One new session only for initial hook load |

## Knowledge storage and sharing mode

Onboarding asks one question first: *keep taskplane knowledge private/local,
or share it with the team in the repository?* (`tp share plan
personal|team|enterprise` — or `tp init --plan …`). `personal` keeps every
decision, requirement and loop state in your private store (`~/.taskplane`).
`team`/`enterprise` moves the store into the repo (`.taskplane-kb/`,
committed — also compatible with Claude Tag), so the whole team shares one
registry and a fresh clone inherits it with zero setup. Both are changeable
any time. And on a team plan you can still work **privately**: `tp share set
private` keeps your work in your own store while you explore, and when you're
ready to make it visible — like pushing commits — `tp share push [--ids
0001,0002]` publishes the selected decisions into the shared store (then
commit `.taskplane-kb/`). `tp share status` shows your current mode and
unpublished count.

## Models (cost routing)

Every loop step, task, and lens brief carries a capability tier —
`cheap` / `standard` / `deep` — and taskplane resolves it to a model at
dispatch time. `tp onboard` reports the resolved map. Claude retains the
historical `cheap → haiku` default; Codex inherits its session model for
every tier unless you explicitly map one:

| Tier | Default | Used for | Override |
| --- | --- | --- | --- |
| `cheap` | Claude: `haiku`; Codex: inherit session model | the lens sweep; tasks a planner marks simple | `TASKPLANE_MODEL_CHEAP` |
| `standard` | inherit session model | execute / evaluate / fix | `TASKPLANE_MODEL_STANDARD` |
| `deep` | inherit session model | spec, plan, engineering review, hard lenses (security, architecture, …) | `TASKPLANE_MODEL_DEEP` |

On Codex those same tiers also resolve to native reasoning effort: `cheap →
low`, `standard → medium`, and `deep → high`. Override with
`TASKPLANE_REASONING_CHEAP`, `TASKPLANE_REASONING_STANDARD`, or
`TASKPLANE_REASONING_DEEP` using a Codex-supported effort value.

For cost-differentiated runs, set the overrides before starting with model
ids your host understands — on Claude e.g. `export
TASKPLANE_MODEL_STANDARD=sonnet TASKPLANE_MODEL_DEEP=opus`; on Codex use your
host's model ids the same way. No cross-provider model ids are hardcoded;
tiers are yours to map as models change. Routing is *verified*, not assumed:
`tp loop verify-dispatch` audits a run, and
`TASKPLANE_ENFORCE_DISPATCH=warn|strict` turns on a dispatch-time check
(opt-in, inert by default). Details: `discipline/model-tiers.md`.

## Context storage (token efficiency)

Fill the three context docs with your project's reality — the product doc's
*Direction / north star* line is what `tp-northstar` measures against. From
then on decisions, requirements, tracked debt, and the dependency graph
accumulate in an **external per-project store**
(`~/.taskplane/projects/<key>/` — `tp kb where` shows the path). That
location is deliberate resource economics: every loop step recalls only the
few records *relevant to the task at hand* instead of re-reading the repo or
replaying history, so context stays small and the token bill goes down as
the project's memory grows. Where that store lives is plan-aware: on a
personal plan it stays external (`~/.taskplane`) and never touches your repo
(nothing to commit or push); on a Team/Enterprise plan it lives in-repo at
`.taskplane-kb/` and is committed deliberately so the team shares one
registry. Either way `kb lint` — a marker scan enforced fail-closed at the
DoD and engineering-review gates — keeps prompt text and pricing out of it,
and the zero-token dependency graph answers blast-radius questions without
spending model calls at all.

Then you're governed from the first task.
