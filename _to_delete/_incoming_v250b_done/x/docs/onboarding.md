# Onboarding (`tp onboard`) — the full setup

This is the complete onboarding reference: what `tp onboard` checks before it
hands you to a governed run, the Codex-specific onboarding path, the knowledge
storage / sharing-mode choice, and the two setup decisions (model tiers and
context storage) that decide how efficiently the whole system runs. The
README's [Onboarding summary](../README.md) covers the short version.

## The three green checks

Say **taskplane help** for the tour, or just state a goal — `taskplane` routes
it and runs onboarding for you on a fresh folder. `tp onboard` shows the
onboarding dashboard and won't hand you to a governed run until three
checks are green:

1. **A real folder to work in** — connect/open your project (an empty
   scratch dir or the session root is refused: a contract scoped there
   would govern everything).
2. **A git commit to diff against** — the gates fail closed without a
   snapshot; `git init && git add -A && git commit` if the repo is new.
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

## Codex onboarding

1. Install and enable taskplane, approve its bundled hooks when prompted,
   then start a **new** Codex task/session.
2. Make the target repository the working folder. In Codex CLI, `cd` to the
   repository before running `codex`; in the desktop app, open or create a
   local environment for that repository.
3. Prompt **"set up taskplane"** or **"use taskplane for …"**. The plugin runs
   `tp onboard --json` before governed work.
4. If the folder is not a committed Git repository, approve initialization or
   make the first commit yourself. taskplane needs the commit as its diff and
   Definition-of-Done baseline.
5. Choose whether taskplane knowledge stays **private/local** (`personal`) or
   is **shared in the repository** (`team`/`enterprise`). This is a storage
   choice; it is not tied to the name of your ChatGPT or Codex subscription.
6. Let taskplane initialize the context documents, then fill
   `current-state.md` first for an existing project. State the goal; taskplane
   will stop at Design approval when that phase is used, plan approval, and
   final sign-off for your explicit decision.

When inline HTML widgets are unavailable, Codex still relays the plain-text
`HEADLINE:` and provides `.taskplane/dashboard.html` as the local dashboard
artifact. The governance state and human gates do not depend on widget support.

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
