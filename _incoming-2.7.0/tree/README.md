# taskplane

[![CI](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml/badge.svg)](https://github.com/vdemkiv/taskPlane/actions/workflows/ci.yml)

**Design, build, and review AI-generated software with evidence, not trust.** taskplane
is the AI software-delivery control plane for people who ship and review code
with Claude or Codex every day. You ask it to design, build, review, or show status;
behind that simple request it checks whether the work is ready, keeps every
agent inside an approved scope, and requires current implementation, test, and
review evidence before anything can be called done.

![taskplane 2.1 in action — a real safe-order-cancellation project: graph-aware Definition of Ready blocks an undeclared audit module → a human approves the corrected dependency-aware plan → the execution contract blocks a Codex edit outside scope → worker evidence cannot self-advance → an independent evaluator checks acceptance criteria, routed lenses, dependents, and the distributed contract → the full engineering review runs → final human sign-off](docs/assets/taskplane-cowork-flow.gif)

taskplane is not another prompt collection, review bot, or project tracker. It
is the governed execution and assurance layer between your intent and
agent-generated changes. Requirements, dependencies, contracts, implementation,
and review stay connected from Definition of Ready through Definition of Done.
A 26-lens engineering review makes architecture, solution design, security, data, operability,
UX, and other technical consequences explicit for engineers, EMs, PMs, and
nontechnical decision-makers.

**Simple for the user; strict for the agents.** State the goal, review the
evidence, and make only the decisions that require human judgment. taskplane
keeps the machinery — scoped contracts, dependency depth, independent
submissions, lifecycle gates, durable memory, and the live dashboard — behind
that interaction without weakening it.

## Four prompts are enough

> **taskplane design:** design safe order cancellation before we build it

> **taskplane build:** add CSV export to the monthly report

> **taskplane review:** review this branch against main; do not change code

> **taskplane status**

The `taskplane` skill routes those requests to the existing product, design,
build, engineering, status, and orchestration skills. You do not need to choose a
persona, remember loop commands, select review lenses, or set dependency
depth. taskplane reports a concise text summary after each material
transition and shows the richer dashboard when the host supports it.

This simplicity does **not** reduce agent work. A worker may only submit a
result; it cannot advance its own stage. The orchestrator independently runs
the engine gate, which recomputes source and review-artifact fingerprints and
rejects missing, stale, out-of-scope, under-tested, or under-reviewed work.
Human Design approval (when used), plan approval, and final sign-off remain
explicit.

## What taskplane does

- **The governed Evaluate-Loop.** Optional design → human Design approval → plan →
  human plan approval → build → evaluate → fix (≤2) → engineering review → human
  sign-off; serial or parallel waves, one enforced contract per agent, and only the
  orchestrator invokes the state-transition gate.
  [docs/loop-design.md](docs/loop-design.md), [docs/authority-matrix.md](docs/authority-matrix.md).
- **Enforced contracts.** Every agent runs inside a contract — file scope, action
  budget, denied commands, read-only for reviewers — screened by the PreToolUse
  hook before each tool call. Literal scope overrides carry provenance: only the
  human-approved plan's `plan_minted` mark authorizes them, never a CLI flag. [docs/state-spec.md](docs/state-spec.md).
- **26 lenses with intelligent routing v2.** The lens catalog (generated:
  [docs/lens-catalog.md](docs/lens-catalog.md)) is routed per stage profile (design
  8 · build 5 · review 26) by a signal engine that scores each lens against the
  actual diff and returns `deep` / `light` / `n/a` — every `n/a` carries
  machine-checkable negative evidence, the cap-8 budget demotes (never drops),
  security floors hold on enforcement diffs, and engine failure fails open to the
  full catalog. [docs/routing-and-flows.md](docs/routing-and-flows.md).
- **Graph decomposition.** `tp graph scan --decompose` derives a component layer
  inside the dependency graph (directory convention + import cohesion + AST
  clustering; floors overridable via `components.yaml`) with fingerprint-cached
  per-component lens maps. Reviews route the capped union of touched components'
  maps, `component_attribution` names which component proposed each routed lens, and
  the fail-open ladder only ever widens (component → module → full catalog). Same doc.
- **Governed flows.** The review wave and the execute/evaluate/fix waves each
  dispatch as one journaled, resumable Dynamic Workflow on Claude; the Task-dispatch
  path stays mandatory and byte-identical everywhere — it is the only Codex path —
  and every human gate stays reachable with workflows off (`TASKPLANE_WORKFLOWS`). Same doc.
- **The regression gate.** Each change is verified for actual regressions within its
  dependency-graph blast radius at the DoD gate: Tier 1 blocks a was-green-now-red
  test against the change's baseline; Tier 2 flags a changed enforcement/public
  entry point with no covering test. [docs/regression-gate-design.md](docs/regression-gate-design.md).
- **Audit cadence + router audits.** Every Nth review (`TASKPLANE_AUDIT_EVERY`) a
  full-catalog sweep diffs its findings against the routing; any finding from an
  `n/a`-routed lens is auto-filed as a router regression that blocks sign-off.
- **A knowledge base that compounds.** Requirements (refinement-scored, with
  dependencies and named contracts), decision records (ADRs with lifecycle and
  supersede chains), and tracked debt (`tp req debt`) persist in a plan-aware
  per-project store and are recalled by relevance at every step.
  [docs/requirements-core.md](docs/requirements-core.md), [docs/lenses-and-knowledge.md](docs/lenses-and-knowledge.md).
- **Dependency-graph DoR/DoD.** Requirements declare dependencies and the
  API/event/data/runtime contracts they provide, consume, or change; before plan
  approval the refreshed graph checks depth, boundaries, and every new module, and
  the DoD compares planned vs realized modules and rejects a stale graph fingerprint.

The moving parts: an enforcement kernel (contracts + lifecycle hook +
orchestrator-only gates + audit trace), the loop engine, the Design Contract phase,
26 read-only lens agents fanned out in parallel, the requirements/decisions/debt
knowledge base, a deterministic dependency graph with a zero-token blast-radius map,
and portable `cheap`/`standard`/`deep` model tiers routed per step, task, and lens —
mapped to models by env config, verifiable with `tp loop verify-dispatch`.

## What's new

The three most recent releases. [CHANGELOG.md](CHANGELOG.md) is the
authoritative, complete history — if the two ever disagree, the CHANGELOG wins.

| Version | Highlights |
| --- | --- |
| **v2.7.0** | **Lenses 2.0.** All 26 review lenses rewritten against current industry practice, one at a time, with every source's authority and limitation recorded and four superseded citations corrected. The largest fix was routing: twelve lenses could not fire on the change class they exist to judge, so their Blockers were unreachable — security could not see CI workflows or lockfiles, i18n only saw translation files, `qa` could not see a change that shipped with no tests. All closed. Routing rows and review guides move to JSON beside the generators and are validated at generation time, so an invalid task type fails loudly instead of becoming a dead key. `qa` now fires on untested changes rather than every change (2 of 40 real changes versus 32, same defect reachable), and CI pins the review routing surface alongside per-task cost. 1,415 tests. |
| **v2.6.0** | **Phase 3 plus the performance and review-honesty work.** Every engine-correctness gap the loop hit while governing its own build is closed (serial-claim refusal, loop-owned DoD exclusion, slot-env sanitizing, unattributed-finding surfacing, fail-open stage emission, clamped floors); routing precision recalibrated and plan ordering made mechanical; Windows slot activation; all five stale skills truthed up behind a generated CLI reference. **Performance:** per-task cost had grown about thirteenfold in a month — the suite is now cited rather than re-run over byte-identical content, `tp loop evidence` assembles an evaluation in one call instead of about sixty, and a CI ratchet pins what the engine mandates per task. **Review honesty:** A4's fingerprint refusal had shipped inert and now bites on a real producer/validator divergence; a finding may block a gate only with a trigger, outcome and repro, so commentary stops reading as a bug; `"set up taskplane"` routes again; and the suite's temp-workspace leak (185,541 directories, about 30 GB) is fixed at the root. No gate loosened; two got stricter. 1,415 tests. |
| **v2.5.1** | **Post-release review follow-ups.** Dedicated Codex-compatibility review of Phase 2 against the SHIPPED package under a simulated Codex host: byte-identical dispatch verified, full suite green with zero host leakage, workflow files correctly absent — one real gap found and fixed: `tp onboard` on a Codex host now prints the Codex plugin-tooling install path instead of the Claude org-admin universe (regression-tested). README rebuilt lean: 659 → 320 lines with a new "What taskplane does" feature-definition section; onboarding detail, specialist routes, and Claude Tag moved to docs/{onboarding,specialist-routes,claude-tag}.md — every content pin kept, zero test edits. The tp-help tour now covers routing v2, decomposition, waves, audit cadence, the regression gate, and the install-truth pointer (it had been stale since v2.2.0). 1119 tests. |

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

**Codex CLI:** add the GitHub marketplace, install taskplane, and open the plugin
browser to verify it is enabled:

```bash
codex plugin marketplace add vdemkiv/taskPlane
codex plugin add taskplane
codex
# inside Codex: /plugins
```

**Codex in the ChatGPT desktop app:** select **Codex**, open **Plugins**, choose
the `taskplane-marketplace` source after adding it, and install **taskplane**.
Open the repository as a local environment and start a new task.

Codex loads newly installed skills and hooks only in a new task/session, and asks
you to review and trust the bundled lifecycle hooks (`/plugins` confirms taskplane
is enabled). Keep Codex's own sandbox and approval controls enabled — taskplane's
scope contract is an additional guardrail, not a replacement. Plugins are supported
in Codex CLI and the Codex desktop app, not the IDE extension; until the public
listing is approved, install from this GitHub marketplace source. See
[Codex plugins](https://developers.openai.com/codex/plugins).

Requires `git` in your workspace (the gates need a commit snapshot) and Python 3
(standard library only; `python3` on macOS/Linux or the `py` launcher on
Windows). Nothing else to set up.

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
codex plugin marketplace add vdemkiv/taskPlane
codex plugin add taskplane
codex   # start a NEW task; approve the bundled hooks when prompted
```

`cd` to your project repository first (desktop: open it as a local environment),
then in the new task say **"set up taskplane"**.

## Onboarding — the short version

Say **"set up taskplane"**, or just state a goal — `taskplane` routes it and runs
onboarding for you on a fresh folder. `tp onboard` won't hand you to a governed run
until three checks are green: a real project folder to work in (an empty scratch
dir or the session root is refused), a git commit to diff against (the gates fail
closed without a snapshot), and `tp init` — which scaffolds the four context docs,
scans the dependency graph, and creates the knowledge base. On a brownfield
project, fill `current-state.md` first: it grounds every design review in as-built
reality, and reinventing an existing component is a blocker-class finding.
Onboarding then asks one question — keep taskplane knowledge private/local
(`personal`, `~/.taskplane`) or shared in-repo (`team`/`enterprise`,
`.taskplane-kb/`) — and reports the resolved model-tier map. Full detail — Codex
onboarding, sharing modes (`tp share`), model tiers and cost routing, and context
storage: [docs/onboarding.md](docs/onboarding.md).

## Honest about what the guardrails are

The scope/command guardrails are a real, mechanical help for the everyday failure —
an agent that drifts out of its lane or fires a destructive command by mistake. The
PreToolUse hook screens scope, denied commands, and the action budget **before**
each tool call, resolves `..`/absolute/symlink paths, screens destructive programs
(`rm`, `chmod`, …) as writes, and fails **closed** on a corrupt contract or an
error. But this is keep-the-agent-on-topic, not a security sandbox: a task that
grants `Bash` grants arbitrary code execution, and no string-screen can fully
contain a *determined adversary* — for a hard boundary, pair taskplane with a
restricted toolset or OS-level isolation (the token/$ budget is cooperative in the
same way). Likewise, "a worker cannot advance its own stage" is a
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
# note: the knowledge base is NOT here — personal plans keep it in ~/.taskplane/projects/<key>/; Team/Enterprise keeps it in-repo at .taskplane-kb/
├── PRIVACY.md              # privacy policy (local-only, no telemetry)
└── LICENSE                 # Apache License 2.0
```

## Going deeper

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
  Task fallback, the audit cadence, and evaluate's build-stage routing — each
  with a dogfood example from this repository.
- [docs/configuration.md](docs/configuration.md) — every environment variable.
- [docs/loop-design.md](docs/loop-design.md) · [docs/authority-matrix.md](docs/authority-matrix.md) ·
  [docs/state-spec.md](docs/state-spec.md) — engine design, who may do what at
  every step, and the on-disk state.

**License:** free and open source under the **Apache License 2.0** — use it
personally or at work, commercially or not, no strings. See `LICENSE`.
**Privacy:** taskplane runs locally, collects nothing, and sends nothing — no
telemetry, no accounts, no network calls of its own; all state stays on your disk,
in an external store (`~/.taskplane`, personal plan) or in-repo (`.taskplane-kb/`,
Team/Enterprise). See `PRIVACY.md`.
