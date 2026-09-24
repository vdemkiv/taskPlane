# taskplane

**Design, build, and review AI-assisted software with a clear plan and evidence that it works.**

Taskplane coordinates software delivery in Claude and Codex. Give it a goal: it helps define the outcome, understand the affected code, plan the work, implement it, and verify the result. Requirements, decisions, dependencies and review findings stay connected throughout the task.

It is for developers and teams who use coding agents for more than a one-off edit and want to understand what is being built, why, and whether it is ready.

![Taskplane workflow overview: design, build, review and status](docs/assets/taskplane-cowork-flow.gif)

*Choose a task, agree on the outcome, and follow the work through verified results. Review each stage yourself or authorize automatic continuation with your conditions.*

## Why Taskplane

Authorized native workers share the run, accepted task plan and dashboard. Ready
independent tasks can run concurrently up to observed host capacity; dependent
tasks wait for verified results. Two overlapping workers is a minimum live
acceptance test, not a scheduling limit. Each worker consumes its own bounded
context and receives exact task paths. See the
[native worker protocol](skills/tp-go/references/codex-native-dispatch.md).

Long coding tasks can lose the original requirement, overlook a dependency, or finish with a claim that has little verification behind it. Taskplane keeps the work connected:

- **Agree on the outcome before building.** Turn a request into a clear scope and observable acceptance criteria.
- **Understand the effects of a change.** Use the source dependency graph and component map to inform planning and review.
- **Carry decisions into implementation.** Keep the design, task plan and implementation tied to the same goal.
- **Know what was verified.** Link completed work to test results and review findings, and make gaps visible.
- **See where the work stands.** One dashboard shows progress, dependencies, open findings and available token usage.

## Four prompts are enough

| What you need | Ask Taskplane | What you get |
| --- | --- | --- |
| Design a change | `taskplane design safe order cancellation before we build it` | A design with trade-offs, affected components and a validation plan. |
| Build a feature | `taskplane build CSV export for the monthly report` | A planned implementation with verification and review tied to the agreed outcome. |
| Review code | `taskplane review this branch against main; do not change code` | Actionable findings, source locations and supporting evidence. |
| Check progress | `taskplane status` | The current stage, remaining work, responsible owner and available usage. |

Use `taskplane help` to see the available routes. You do not need to choose review lenses or operate Taskplane's internal commands yourself.

## From a goal to working software

A full delivery follows **Product → Design → Plan → Build → Evaluate → Engineering → Retro**:

| Stage | The question it answers |
| --- | --- |
| Product | What should change, for whom, and how will we know it worked? |
| Design | How should it work, and what choices or dependencies matter? |
| Plan | What needs doing, in what order, and how will it be checked? |
| Build | What implementation delivers the agreed result? |
| Evaluate | Does the implementation meet each acceptance criterion? |
| Engineering | What correctness, design, security or maintenance risks remain? |
| Retro | What was delivered, learned or deliberately left for later? |

You can also request Product, Design or Engineering review on its own. A review request does not authorize changing the code. Existing review findings can become the inputs to a later delivery, so repairs remain connected to the problems they address.

By default, you review each stage's concrete result and approve it before the next stage. Ask for changes when the result needs correcting. Taskplane keeps the accepted decisions and evidence with the run.

### Working autonomously

Give explicit permission and useful boundaries when you want Taskplane to continue between stages:

```text
taskplane build CSV export for the monthly report.
For this run, auto-approve phases after the required checks pass.
Keep the existing report format and permissions. Pause on a failed check,
uncertain result or scope change. Stop before Retro for my review.
```

The dashboard shows those instructions and which stages may continue automatically. Say **"Return to manual approval"** to take back each checkpoint. Automatic continuation still needs the phase's evidence and your conditions to pass.

## Install and run your first task

You need Python **3.10 or newer**, Git, a local project folder and a Claude Code or Codex installation that supports plugins. Your organization must permit the plugin. Check Python with `python3 --version` on macOS/Linux or `py -3 --version` on Windows.

### Codex

1. Open **Plugins** in the desktop app, or `/plugins` in Codex CLI.
2. Install and enable **taskplane** from your permitted marketplace. If your organization manages plugins, use its catalog or ask an administrator to add the package.
3. Review and trust Taskplane's hook definitions once after installation, and again when definitions change. Codex CLI exposes this under `/hooks`; desktop controls depend on the host version.
4. Start a new task in your project so the installed skills load.

For a configured CLI marketplace, `codex plugin list` shows the available entries and `codex plugin add taskplane@MARKETPLACE` installs the selected one. Confirm the loaded version when updating; a catalog label alone does not prove which package a current task is using.

### Claude Code

Where your organization allows adding a marketplace:

```text
/plugin marketplace add vdemkiv/taskPlane
/plugin install taskplane@taskplane-marketplace
```

Follow the installation's activation or reload instructions, inspect `/plugin` for errors and `/hooks` for the loaded definitions, and accept the host's project trust prompts as appropriate. Managed users install through their organization's catalog.

### Try a small real change

Open your repository and ask:

```text
taskplane build a CSV export for the monthly report.
First show me the proposed scope, acceptance criteria and Taskplane dashboard.
```

Taskplane should clarify the result, identify affected components and show the first stage in its own dashboard. Review the scope and reply `approved` or `Changes requested: ...`. Continue with the same task so the decisions and evidence remain connected.

For detailed setup, version checks, updates and troubleshooting, see [Onboarding](docs/onboarding.md). Taskplane's controls use the host's available integration; normal host permissions remain in effect.

## Follow the work

The shared dashboard lives at `.taskplane/dashboard.html`. It brings together the current goal and stage, task dependencies, the source graph, review findings and verification evidence.

Available token measurements help you understand the work already done. Missing measurements are shown as unknown, not zero. Phase and run usage must refer to the same run. The dashboard is a snapshot: regenerate it after progress, then refresh the view. Always check that its goal and checkout match the task you are following.

## Learn more

- [Onboarding and troubleshooting](docs/onboarding.md)
- [CLI reference](docs/cli-reference.md)
- [Engineering review lenses](docs/lens-catalog.md)
- [Release history](CHANGELOG.md)
- [Privacy](PRIVACY.md) and [Apache-2.0 license](LICENSE)

## Development

Install the pinned developer dependencies from `requirements-dev.lock` in a virtual environment. Run `python3 scripts/ci_local.py` for the standard checks; add `--browser` for the browser suite. Build the Codex upload archive with `python3 scripts/package_openai.py`. Generated packages go to `dist/`.

Taskplane now supplies bounded phase context and command summaries. Use
`flow context` to consume current required inputs; new runs require the returned
receipt in phase evidence. Complete reports remain available with `--full`.
See [context optimization](docs/cli-reference.md#bounded-context-transport) for receipt semantics,
verification reuse, and the distinction between transport bytes and billed tokens.
