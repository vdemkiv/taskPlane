# Onboarding (`tp onboard`) — the full setup

This is the complete onboarding reference: what `tp onboard` checks before it
hands you to a governed run, the Claude and Codex host-specific paths, the
knowledge storage / sharing-mode choice, and the two setup decisions (model
tiers and context storage) that decide how efficiently the whole system runs. The
README's [Onboarding summary](../README.md) covers the short version.

## Readiness checks

Say **taskplane help** for the tour, or just state a goal — `taskplane` routes
it after onboarding on the first request in a host session and after each install,
reinstall, or update. This includes Review, Status, and Help in an existing
repository. Existing knowledge or run state never substitutes for setup.
TaskPlane retains your original request and continues it when ready, without
asking you to state the goal again. `tp onboard` shows the
onboarding dashboard and won't hand you to a governed run until the prerequisite
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

   **Before authorizing `tp init` in a brownfield repository, check for a
   tracked legacy `knowledge/` directory.** On a personal plan, initialization
   moves that directory into the external project store, runs `git rm --cached`
   to untrack its contents, and adds `knowledge/` to `.gitignore`. On a Team or
   Enterprise plan, the shared store remains in-repo under
   `.taskplane-kb/knowledge/`. Review the full
   [legacy knowledge migration contract](state-spec.md#migration-from-an-in-repo-knowledge-base)
   before approving this repository-changing step.

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
7. State the goal. Product, optional Design, and Plan pass mechanical evidence
   gates and are presented in one consolidated pre-implementation
   authorization packet. taskplane stops for that authorization, final
   sign-off, and named exceptional boundaries; Claude never self-approves them.

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
   `tp onboard --json` and presents the onboarding dashboard before routing
   the request. On first use it installs the
   portable `.codex/hooks.json` workspace configuration plus an ignored local
   `.taskplane/codex-hook.py` bridge. A new task is required only for this
   one-time initial host hook load, never for checkout/auth/storage recovery.
   A linked Codex worktree reuses the primary checkout's validated bridge via
   Git's common directory until onboarding creates its own ignored local copy;
   it does not depend on plugin-root environment variables being inherited.
   Reinstallation restores a missing launcher before trusting an earlier session
   receipt. Onboarding checks the same Git-family launcher path the hooks use.
4. Answer any prerequisite prompt in chat. taskplane runs only its stored
   bounded action after approval and resumes the same run.
5. Choose whether taskplane knowledge stays **private/local** (`personal`) or
   is **shared in the repository** (`team`/`enterprise`). This is a storage
   choice; it is not tied to the name of your ChatGPT or Codex subscription.
6. Let taskplane initialize the context documents, then fill
   `current-state.md` first for an existing project. State the goal; taskplane
   will stop at one consolidated pre-implementation authorization and final
   sign-off (plus a named exceptional boundary such as A/B selection or
   material authority change).

For each phase, Codex receives the current bounded startup envelope and the
exact host dispatch fields. The worker uses `stage read-input` to consume its
pinned phase definition and selected immutable artifacts. It does not inherit
predecessor conversations, mutable execution state or sibling workspaces.
Parallel Build, Fix and Evaluate tasks retain separate bindings and evidence;
EM receives all accepted evaluations at the join, and sign-off leads to Retro.
Repo-local `SubagentStart`/`SubagentStop` hooks bind exact child contracts and
terminalize/quarantine them while adding bounded context and lifecycle traces; the
PreToolUse screen and evidence gates remain authoritative. For a long run you
may start Goal mode with `/goal`; it changes neither permissions nor gates.

When inline HTML widgets are unavailable, Codex still relays the plain-text
`HEADLINE:` and provides the managed run's dashboard by reference (legacy
unmanaged workspaces use `.taskplane/dashboard.html`). The governance state
and human gates do not depend on widget support.

Both native and repository hooks pass through the same event-claim guard, which
executes an event once and replays its result for duplicates. Onboarding consumes
that guard's receipt; the two paths do not need to have identical latest events.
Hook delivery order, a late repository hook, and concurrent checkouts must not
make readiness oscillate. An event without a valid identity cannot establish
this capability. Records remain isolated by host session and, for repository
hooks, by checkout.

The dashboard and plain-text headline use the same setup actions. A missing or
unrecognized action stays incomplete. A new session is suggested only when hooks
are configured but no load has been observed; loaded hooks without claim evidence
require checking the connection in the current task. Neither case shows a Start
action or claims that setup is complete.

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

Lens routing is stage-owned. Product and Design use focused routes; Plan uses
three or four quick lenses for non-trivial work. Build, Fix, Evaluate,
Engineering and Retro launch zero lens workers. Routed phases retain all 26
dispositions, and later phases consume the sealed results.

Models and reasoning come from the canonical
[operational settings](configuration.md), including a separate Retro entry.
All phases inherit the session model by default and request `high` reasoning;
there is no implicit Claude model pin. Environment tier aliases remain
compatibility inputs, while each current phase role selects its own settings.
`tp onboard --json` reports those phase settings, the effective digest and the
validated phase registry.

The registry and its skill links must validate before readiness. A mismatch
returns `repair_phase_configuration`; use one consistent installed build rather
than borrowing another version's files. A declared run manifest must also match
the current checkout. Recover only that binding through the named action.
Existing runs keep their sealed settings; onboarding never adopts old Plan or
Design artifacts as inputs to a fresh Product phase.

## Context storage (token efficiency)

Fill all **four context docs** with your project's reality:

- `current-state.md` records what is already built and running; fill it first
  for a brownfield repository.
- `product.md` records the user, problem, boundaries, and Direction / north star
  that `tp-northstar` measures against.
- `tech-stack.md` records the languages, frameworks, services, and technical
  constraints.
- `workflow.md` records how the team builds, tests, reviews, and releases.

From then on decisions, requirements, tracked debt, and the dependency graph
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
