# Claude Mods: Taskplane checkpoint and evidence

Taskplane's Claude Mod adds a status band above the prompt, a checkpoint pane,
and an evidence browser. They share the selected run and one controller snapshot.
The Mod is registered by `modules: ["../mods/taskplane.tsx"]` in
`hooks/hooks.json`, alongside the existing command guards.

The implementation targets Claude's native `AbovePrompt` and `Pane` surfaces.
Installed CLI declarations and fixture tests can establish API compatibility;
they do not establish live Desktop paint, terminal clipping, keyboard focus
restoration, or real checkpoint action delivery. Those require an observed
loaded session in each host. A passing fixture labeled `desktop` is still a
fixture, not a Desktop session.

## Use the pane

Select **Open checkpoint** in the band or run `/taskplane-status`. The band shows
phase, decision state, live workers, unresolved findings, and the last confirmed
update. The pane exposes work status, evidence validation, finding count,
decision, workflow revision, packet revision, and complete checkpoint identifiers.
Validated evidence does not mean findings are resolved or a phase is approved.

Use **Run ID** and **Select run** to inspect a run; an empty ID selects the current
run. A missing selection and no run have different states. Selection persists
per workspace and Claude session and is revalidated after reload. Historical runs belonging to the selected session are read-only. Foreign-session
CLI access remains restricted by the existing 2.31.7 controller.

**Run tokens** come from Taskplane's run accounting. **Context window** usage
comes from the Claude session. Their sources and measurement details are separate.
Missing data displays **Unknown**; a measured zero remains zero. Partial coverage
does not imply a complete total or permit an inferred context percentage.

The coordinator refreshes every two seconds and on explicit **Refresh** or run
selection. Reads are serialized; obsolete selection responses are discarded.
A failed refresh, host detach, invalid/future confirmation time, or five seconds
without a current confirmation marks the view **Stale** and disables decisions.
The last successful confirmation remains visible. A successful current refresh
restores eligibility only if the controller also permits the action.

## Decide explicitly

Enter a **Revision comment**, choose **Accept** or **Request changes**, then read
the frozen choice, full binding, and exact comment. Press **Confirm Accept** or
**Confirm Request changes** to submit that observation. Opening, closing, typing,
or preparing the confirmation does not approve a checkpoint.

The request binds workspace, owning session, run, visit, checkpoint, workflow
revision, evidence manifest digest, and scope digest. Taskplane records an
observed UI event through its existing controller. This is cooperative local
provenance, not independently authenticated host proof; native permissions and
the owning session's authority remain in force. The Mod cannot advance phases
or change approval policy.

Only a matching authoritative receipt produces **Decision recorded by Taskplane**.
On failure or an uncertain reply, keep the pane open and use **Check decision
receipt**. Reload reconciles the saved attempt without automatically replaying
the decision. Duplicate clicks share the same durable attempt identity. A changed
checkpoint requires review of its current evidence; old comments remain available
as read-only previous drafts and are never moved to the new checkpoint.

**Cancel confirmation (retain draft)** discards a known, unrecorded confirmation;
an uncertain attempt must first be reconciled. If draft persistence fails, the
text remains in memory and submission stops. Closing the host before persistence
recovers can lose that in-memory text. Payload limits are checked without
truncating the comment: the complete request/envelope is limited to 16 KiB and
the controller's full confirmation to 4,096 characters, including its binding.
Shorten oversized text explicitly. The bounded ledger retains unresolved attempts
and refuses new writes if full.

## Inspect findings

Use **Numbered findings**, **Previous finding**, and **Next finding**. Selection
uses stable finding IDs across refresh/reordering. If a selected finding disappears,
the pane asks for a new selection instead of silently choosing its neighbor.
Empty findings and unavailable evidence are distinct.

**Finding**, **Source evidence**, and **Verification** expose recorded disposition,
verification state, source references, related tasks, and review lenses. Reviewer
identity and method appear only when recorded. Current checks remain separate
from historical finding records. Long evidence is paged; controller excerpts
explicitly identify truncation. Missing source bodies remain unavailable rather
than being reconstructed from mutable files.

## Placement, keyboard, and capability fallback

The band stays in `AbovePrompt`. Claude places the native pane beside the
conversation when supported and inline above the prompt otherwise. Controls stack
below 72 body columns, IDs wrap, and evidence pages limit long bodies. Native host
scrolling still matters in short panes.

The pane requests focus on open, uses descriptive native control labels, focuses
**Refresh** initially, and enables Escape to close. Terminal guidance is
**Ctrl+X then Tab** to focus the pane, **Tab** to traverse controls, **Enter** to
activate, and **Escape** or **Close checkpoint** to close. Numbered selection uses
the host's native Select control. Status is textual as well as visual.
Focus visibility, scrolling, and return to the opener or prompt are host-managed
behavior and still need live keyboard validation; source assertions and synthetic
presses cannot certify them.

The Mod checks snapshot schema, generation, run identity, freshness, and advertised
decision capabilities before enabling actions. Unsupported surfaces show the
existing `.taskplane/dashboard.html` path and the conversation fallback. Unsupported
contracts, missing transport, permissions, or broken storage do not produce
success. If the host lacks Mods entirely, use the existing Taskplane dashboard
and skills; the band cannot render on that host. Inspect the dashboard's own
timestamp because its HTML is a separately published, read-only view.

Preview is enabled through the module's `preview` option. It supplies no decision
transport, requests `--mods-preview` snapshots, and cannot submit live decisions.
The backend also refuses preview preparation/recording. A read-only snapshot or
dashboard never grants authority to bypass native permissions.

## Package and verify

The Claude archive explicitly includes `mods/taskplane.tsx`,
`mods/taskplane.test.ts`, `mods/tsconfig.json`, `mods/package.json`,
`mods/package-lock.json`, and this guide. The bridge ships with the shared Python
runtime. Codex receives the same command guards with the Claude `modules` field
removed from its archived hook JSON; it receives no `mods/` assets or this guide.
The source hook file is unchanged by packaging. Sidecar hashes describe the exact
archived bytes, including that deliberate Codex transformation, and do not imply
that transformed bytes match a source commit.

Generated `.claude-plugin/types/` and `.codex-plugin/types/`, declaration files,
`node_modules`, bytecode, and tooling caches are excluded. Keep generated host
declarations and dependency installations in disposable staging, not release assets.
The five explicit Mod files include pinned TypeScript tooling for reproducible
checks; they do not require an npm install to render the bundled Mod.

From the checkout, inspect installed `claude plugin validate --help` and
`claude plugin test --help` before running supported checks:

```sh
python3 -m pytest -q taskplane/tests/test_workflow_packages.py
claude plugin validate . --strict --json
claude plugin test .
```

`npm --prefix mods test` invokes the same host fixture runner. For strict typing,
provide the installed host declarations through `CLAUDE_CODE_TYPES` and the pinned
TypeScript dependency through an isolated `NODE_PATH`, then run
`npm --prefix mods run typecheck`. Missing declarations are a blocker to that
check, not permission to invent an API.

Package tests compare independent archive membership and exact expected bytes,
check regular members and retained guards, exclude generated tooling, compare
repeated archive hashes, refuse a symlinked module, and smoke-test the extracted
CLI in isolated host stores. Strict validation and `plugin test` check the installed
CLI's plugin/fixture behavior. None installs, publishes, certifies a real decision,
or proves Desktop/terminal rendering. Record actual commands, host version, source
hashes, failures, and remaining live checks with delivery evidence before claiming
the full MVP verified.

## Session identity and startup recovery

The Mod passes the native Claude session ID to every Taskplane report and decision
process. Missing identity stops the bridge; a mismatched workspace/session snapshot
stays stale. Historical runs owned by the selected session remain read-only. A foreign
session cannot acquire read or decision authority through a run ID. An empty run view says findings are
unavailable, rather than implying a changed checkpoint or validated zero findings.

Use the existing 2.31.7 standalone review flow documented in the
[CLI reference](cli-reference.md). This port does not replace its bootstrap,
workflow, context, telemetry or permission contracts with later implementations.
