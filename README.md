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
  and every human gate stays reachable with workflows off (`TASKPLANE_WORKFLOWS`).
  On Codex, every brief carries a collision-safe native `task_name`, exact taskplane
  role marker and instructions, model tier, optional model, and tier-derived
  reasoning effort; execute waves register those identities before spawn so strict
  dispatch can reject a renamed, mis-routed, or partial handoff. Same doc.
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

Recent releases. [CHANGELOG.md](CHANGELOG.md) is the
authoritative, complete history — if the two ever disagree, the CHANGELOG wins.

| Version | Highlights |
| --- | --- |
| **v2.14.1** | **Codex marketplace installs now establish the enforcement boundary they depend on.** Codex plugins supply taskplane's skills, while onboarding installs a portable repo-local `.codex/hooks.json` plus an ignored machine-local bridge to the installed engine; readiness stays false until that bridge is current, and plugin upgrades are detected as stale instead of silently using an old hook runtime. Review routing now reads one bounded canonical diff with nearby hunk context—not unrelated whole-file content—so security retains enclosing authorization signals while selective routing avoids false-positive lens fan-out. Routing input is capped at 200 files and 64 KiB per file, and large shared review envelopes deduplicate repeated file/symbol/impact data before scoped views are sealed. |
| **v2.14.0** | **Governed review is now one dependency-aware, selective evidence pipeline across Review, Evaluate, and final engineering sign-off.** taskplane establishes graph quality and the bounded blast radius before routing, maps all 26 lenses to `deep`, `light`, or `n/a`, preserves architecture and security floors, and dispatches only the deep set plus at most one light sweep. If graph evidence is insufficient, it stops with `impact_incomplete` and dispatches nobody. Every reviewer receives a scoped reference to one immutable diff/impact/requirements/DoR/DoD envelope; leased results are fingerprint-bound, canonical findings/report/dashboard/gate surfaces share one revision, and the evaluator cites matching execute-gate evidence instead of rerunning it. The frozen PR-9464 oracle protects the known blocking regression while structural and token-efficiency counters make cost visible. Dashboards now use taskplane's report visual language for the dependency graph, workflow state, and approval/rejection gates. Claude and Codex consume byte-equivalent canonical artifacts; host lifecycle hooks remain the provenance boundary and load when the updated plugin starts in a new task. |
| **v2.13.0** | **The budget counted tool calls; the cost was tokens — and the render obligation was the single most expensive thing in the product.** One measured review: 3.77M effective tokens over 99 turns. Four mechanisms, each aimed at a line in that breakdown. **450k went to inline dashboards**, because v2.9.0 made showing an artifact enforceable and the only compliance path was pasting the engine's HTML back through a widget tool — ~52k characters re-authored at output weight, of a file taskplane had already written to disk. `tp findings` now hands back a PATH above `TASKPLANE_INLINE_MAX` (24k chars) and `tp ack <id> --delivered <path>` discharges the obligation with the SAME bytes and the same fingerprint check; a delivered substitute is still recorded as a mismatch. **777k went to 69 shell commands**, about ten of them before a single lens saw the diff: `tp review start` establishes tools, target pin, graph, impact, contract, obligations, routing, runnability and the ready-to-dispatch briefs in ONE payload, deciding nothing — the same move `tp loop evidence` made for the evaluate step in v2.6. **754k went to four lens agents, "each carrying its own copy of the diff and the blast-radius brief"** — the diff is identical for all of them, so it is written once to `.em-review/context/` and the briefs cite the path with an explicit do-not-re-derive instruction. **And the action ceiling could never be tuned:** an action cost ~11,261 effective tokens on that review with a two-order-of-magnitude spread, so raising 40 to 80 bought ~440k tokens sight unseen. Contracts now take `--max-tokens`, read from the host's own transcript and weighted the way cost falls (cache reads ×0.1, cache writes ×2, output ×5 — the same review was ~22M raw and ~3.8M effective). It fails open in every direction — no transcript, a torn line, a missing usage block — because a budget that blocks when its instrument breaks makes a broken instrument into a broken product, and the action ceiling still stands underneath. 2,048 tests, 10 mutations observed failing. |
| **v2.12.0** | **A review can now prove what it reviewed — and `git`/`gh` are dependencies, not conveniences.** Two field reviews of `aws/karpenter-provider-aws#9464` both cloned the repository and neither could prove it: `tp new` took the target as free text and the contract recorded no origin, no base, no head, no record of how the code arrived. Both reports stated the workspace and the diff base in PROSE, by hand, and a review conducted entirely from a rendered web diff would have produced identical artifacts and an identical gate. `tp target` closes it: `fetch` acquires a pull request with the same two git commands every time and records them, `pin` reads what the checkout actually is (origin, head, base, merge-base, dirty paths) and reduces it to a fingerprint, and `tp new --target <pr> --fetch --base <ref>` does both at activation and writes the pin into the contract. Findings cite that fingerprint in `meta.target`; `tp findings` says UNBOUND when they cite nothing or a different tree, and the PreToolUse screener refuses `dod`, `loop submit`, `loop approve` and `loop retro` on a read-only contract until the workspace is pinned — the same obligation-to-prohibition conversion as v2.9.0, never blocking the review itself. **And `gh` is now a declared dependency.** A clone carries the code and none of the intent: a PR's title, body, linked issues and review conversation are not in the git objects, so in the field that context arrived over unauthenticated web reads nothing recorded. `tp onboard` and `tp target tools` report git and gh with versions and auth state, `tp target tools --install` installs gh through the host's own package manager, and a remote-PR review without gh fails loudly instead of quietly degrading. taskplane deliberately does NOT download and execute a release tarball — a hardcoded checksum nobody maintains is a worse guarantee than the package source the user already trusts, and a test pins that target.py never reaches for curl, urlopen or tarfile. 2,013 tests, 9 mutations observed failing. |
| **v2.11.0** | **The applicability engine was never wired to the wave — and three v2.10.0 claims were not true.** Route v2 (content, graph and requirement signals producing a per-lens `deep | light | n/a` verdict, every skip carrying machine-checkable negative evidence) shipped in v2.4.0 and was unreachable from the CLI for six releases: `route()` enables it only when `stage` is passed, `cmd_lens` passed nothing, and the one caller that did pass `stage="review"` was the coverage REPORTER. The engine scored the diff for a report and the wave ignored it. Compounding it, `tp-engineering/SKILL.md` mandated `--all` on every review command, and `--all` disables the engine by construction — two independent causes, so fixing either alone changed nothing. On a Go type change plus a docs edit the glob router summoned 6 lenses deep and marked none n/a; the engine routes 2 deep, 4 light, 20 n/a. **And v2.10.0 claimed three fixes that had not landed.** `graph impact` still could not see intra-repo Go: the root-module prefix stripping went into the JavaScript resolver, the Go branch was never touched, and the helper could not have worked anyway — it looked the root path up by key in a SET, which is what the scanner holds. The three "minor" papercuts were reported done and all three reproduce. **Then the exit path, from a second field run.** `session-verify` demanded `tp ack <id>` while the budget refused `tp ack <id>` — twelve firings, no reachable state that satisfied it; `ack` is now unmetered (it discharges an obligation and cannot widen scope — unlike `clear`, which stays walled), the last actions of a budget are RESERVED for closing rather than added to it, and the hook names the real blocker instead of repeating an impossible instruction. `tp ack <id>` in the wrong directory returned `acknowledged` for an id nobody issued, and `graph html` there emitted 5,684 bytes of valid-looking dependency graph for a workspace that had never been scanned — both now refuse and say where the contract actually lives. `tp init` writes `.git/info/exclude`, never a reviewed repo's `.gitignore`. A read-only contract can create the directory it authorizes. `python3 -c` is screened as a grammar instead of refused as a blob — an allowlist of stdlib reads passes, every write shape and anything unparseable still fails closed. 1,974 tests, 14 mutations observed failing. |

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

Codex loads newly installed skills in a new task/session. On first setup,
taskPlane installs a portable repo-local `.codex/hooks.json` and an ignored
machine-local bridge, then asks you to start one more task so Codex loads the
lifecycle hooks. Keep Codex's own sandbox and approval controls enabled — taskplane's
scope contract is an additional guardrail, not a replacement. Plugins are supported
in Codex CLI and the Codex desktop app. Install the published plugin from the
catalog, or use this GitHub marketplace source for local development. See
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
codex   # start a NEW task; taskPlane onboarding installs repo-local hooks
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
