# Taskplane

Plan, build and review software with Codex or Claude, keeping decisions and actual
verification connected. Use full delivery, standalone Product/Design/Engineering,
or a reusable workflow with fresh inputs.

Candidate version: **2.34.5**. Source, extracted packages, installed runtimes and
live host acceptance are separate observations. This candidate does not certify
a loaded Codex/Claude update or publication.

## Choose the work

| Outcome | Example |
| --- | --- |
| Product | `taskplane define scope and acceptance criteria for CSV export` |
| Design | `taskplane design safe order cancellation before implementation` |
| Delivery | `taskplane build CSV export for monthly reports` |
| Review | `taskplane review this branch against main; do not change code` |
| Reusable workflow | `taskplane create a change-risk workflow; save without running` |
| Existing progress/help | `taskplane status` or `taskplane help` |

Help/status/catalog/validation/preview are read-only and do not start a run.
A review or design request does not authorize source changes.

## Delivery

Product → Design → Plan → Build → Evaluate → Engineering → Retro.
Each phase presents a concrete result and retains its own approval decision.
Ordinary contextual assent works; no exact phrase or question title is required.
Manual approval is the default. Explicit optional automatic approval uses a
run-bound policy, scoped conditions, named stop phases and evidence-backed
assessments. Ordinary approval of one result never enrolls that policy.

New repository runs start in a clean dedicated linked worktree at the intended
base, with the actual native session bound there. Automatic pre/post hook coverage
is reported separately from controller phase authority. Missing callbacks do not
block root phase work or require restarting the chat. Native worker dispatch keeps
its own readiness checks. Existing work, decisions and unchanged Product consent
survive delayed checkpoint delivery.

Start with the goal, stable acceptance criteria, material boundaries and current
output. Design chooses the approach; Plan fixes exact Build paths, ownership,
relevant inputs, prerequisites and meaningful checks before implementation.
Build records actual changes/checks. Evaluate judges criteria. Engineering reports
actual findings and coverage. Retro records outcomes and remaining owners.

Root integrated work, serial ownership and useful native workers are valid.
Explicit independence remains binding; actual launch identity and joined terminal
completion are required for native claims. There is no worker/lens quota, startup
probe, context receipt, byte accounting or unrelated-file hash acknowledgement.

Resume through a durable brief and relevant references. Changed dependencies
invalidate affected work; unrelated documents or status revisions do not.
Verification attempts/logs remain immutable, and later fail/unknown cannot fall
back to an old pass. Remove obsolete tests/helpers/fixtures with removed code.

Flow start and phase checkpoints publish `.taskplane/dashboard.html`; show the
dashboard with the current phase result. Hooks check phase, approval and task
ownership before supported actions. Typed DoR/DoD require actual evidence and
accept equivalent wording. Retry only missing, failed or invalidated tasks and
their affected checks; retain all unaffected results, evidence and approvals,
without restarting earlier phases.

Owned candidate hook source is editable. Agents must not disable, uninstall or
bypass the live harness to get past a refusal. Test allowed and rejected actions
on fresh Codex and Claude installations while preserving the live installation.
Supported-path failures block completion; stronger host containment is a separate
existing limitation. Advisory telemetry cannot change enforcement decisions;
missing usage is unknown, not zero. See [shared flow](skills/tp-go/references/shared-flow.md).

## Install and update

Use your host's supported plugin/marketplace controls and organization policy.
Load the intended project and review hook permissions. Python 3.10+ is needed for
the runtime; inspect the actual environment before execution. See
[onboarding](docs/onboarding.md) for workspace/recovery distinctions.

Check the actual loaded installation with:

```sh
python3 /actual/plugin/taskplane/tp.py version --verify
```

Both host manifests use `2.34.5`. A source edit or archive does not update a
running session. Preserve its original runtime and evidence; update/reload through
the host between runs. Candidate active old-contract mutation refuses rather than
silently migrating history. Installed caches are never modified by package build.

Codex and Claude share the compact contract. Actual hook/worker/permission support
must be observed per installed host. Cowork needs an explicitly selected-project
binding plus separate command/worker location observations; a mount alone proves
neither. Local fixtures do not certify native restart, Windows launcher or
cross-host authority transfer. See [host surfaces](docs/host-native-ux.md).

## Reusable workflows

Packaged current seeds cover change-risk review, Design brief and feature delivery.
Create/edit/save is distinct from run. Authoring presents a Design checkpoint;
saving/previewing/compiling grants no execution or future phase approval. Invoke
with fresh typed inputs/output paths through existing flow controls. Preserve old
published definitions and pinned active packages; changed definitions need a new
version. See [Workflow Builder](docs/workflow-builder.md).

## Development and packages

```sh
awk 'sub(/^# test-lock: /, "")' requirements-dev.lock > .requirements-test.lock
python3 -m pip install --require-hashes --no-deps -r .requirements-test.lock
rm .requirements-test.lock
python3 -m pip install --require-hashes --no-deps -r requirements-dev.lock
python3 scripts/ci_local.py --help
python3 scripts/ci_local.py --check quality
python3 scripts/ci_local.py --check tests --suite all
python3 scripts/package_claude.py
python3 scripts/package_openai.py
```

First install the complete test profile using the extraction commands in
[Contributing](CONTRIBUTING.md#run-the-tests). The quality lock alone does not
install pytest or its dependencies; even quality runs the isolation canary.

Packages go to `dist/taskplane-2.34.6.plugin` and
`dist/taskplane-2.34.6-openai.zip`. Sidecars record source/dirty lineage and exact
archive/member hashes at this package boundary. Building does not install or
publish. Use actual targeted/combined check evidence and keep unavailable live
host/platform acceptance explicit. See [CI coverage](docs/ci-performance.md).

Taskplane enforces cooperative workflow/path/identity checks using local state
and observed host events. It is not an OS sandbox, independently authenticated
human channel or complete opaque-shell containment. Host permissions remain in
force. [Security](docs/security.md) describes actual boundaries.

[CLI](docs/cli-reference.md) · [State](docs/state-spec.md) ·
[Changelog](CHANGELOG.md) · [Privacy](PRIVACY.md) · [Terms](TERMS.md) ·
[Apache-2.0 license](LICENSE)

### Telemetry and optional run records

Active flow operations use workflow state and separate advisory
`.taskplane/live-telemetry-<run-key>.json` aggregates. The analysis journal is
never used to select a run, enforce a gate, count current activity, or record
every hook. Its absence or failure does not change execution.

After a run ends, optionally save a factual summary with
`python3 taskplane/tp.py flow report --run RUN --record --note "Lesson learned" --full`.
This creates one `.taskplane/run-record-<key>.json` analysis record; it neither
reopens the run nor adds an approval phase. Runs need no journal entry. Existing
legacy journals remain available only through explicit
`flow report --history --run RUN --full` analysis.
