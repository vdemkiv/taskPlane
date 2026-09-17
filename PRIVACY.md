# Privacy Policy

_Last updated: 2026-09-16_

Taskplane is a local developer tool running inside a host coding agent, such as
Claude or Codex. It has no author-operated service, advertising identifiers,
analytics endpoint, or background upload to the author. It does collect and store
**local execution and token-usage observations** to support its shared dashboard.

## Local data processing

Taskplane reads repository files and Git metadata to inventory source, build a
dependency graph, suggest review lenses, and capture requested review diffs.
User-supplied goals, progress notes, task plans, reviews, and evidence may contain
sensitive information. Include only material appropriate for the workspace.

Native host session files are read to identify tasks and subagents and reconcile
token usage. The Codex reader reads bounded metadata and counter regions. The
Claude reader scans its selected session and subagent transcripts for message
identities, timestamps, and usage. Conversation text can be present in the files
being parsed, but these readers return identities and counters, not conversation
content. Host session IDs and transcript paths can be retained in observations.

Hooks record event/tool names, action fingerprints, identifiers, timestamps and
available counters. Hook command bodies, prompts, tool responses and assistant
messages are not copied into the flow journal. Explicitly supplied goals and notes
are separate user-authored fields and are stored as provided, within size limits.

## Storage and retention

Derived runtime observations are workspace-local under `.taskplane/`:

- `flow-events.jsonl` stores run lifecycle, goals, notes, artifact references,
  hook metadata, session identities and usage measurements.
- `knowledge/graph.json` and optional graph-cache files store derived file
  identities, dependencies, components and recorded relationships.
- `review-source.json` stores the selected source inventory and, for a diff
  review, the raw patch. It can include sensitive source content. It is replaced
  by the next captured review and otherwise remains until you delete it.
- `dashboard.html` and graph HTML files contain generated views. The dashboard
  can embed previews of explicitly attached evidence, task/review details, paths
  and usage. Treat sharing that HTML as sharing its embedded content.
- `trace.jsonl` stores minimized graph/audit observations. It rotates after
  exceeding 5 MiB. Cleanup during trace writes or explicit retention operations
  removes rotated archives older than seven days and enforces limits of eight
  archives and 40 MiB. Cleanup does not run on a background timer; inactive
  workspaces can retain old archives until another cleanup operation.

Flow journals, review inventories/patches, graphs, and dashboards have **no
automatic time-based expiry**. They remain until replaced or deleted. Attached
evidence remains at its original workspace path; Taskplane does not delete it.
There is no current Team/Enterprise store switch or automatic shared-store sync.

The default native_workflow profile stores versioned workflow state under
`.taskplane/workflow-<root-hash>.json`, with an initialization marker alongside it.
It retains scope/criteria, visits, revisions, source fingerprints, submitted output,
graph/task context, observed process handles and human decision records. A decision
retains its explicit response excerpt (up to 512 characters), message reference,
conversation identity, recorder, observation/presentation times, choice and exact
checkpoint binding. It does not copy a whole transcript. Submitted output and
generated dashboard copies can contain sensitive evidence supplied by the user.

Local workflow records have no automatic expiry and are writable by the local
account. Provenance is observed, not independently host-authenticated. Known lost or
corrupt initialization/state refuses silent reset. Removing observation journals
alone does not reset the separate workflow store or approve a checkpoint.

An explicitly requested protected_host integration would retain state outside the
worker write boundary, with independently verified decisions and process ownership.
The shipped adapters provide no such owner or protection. Its control-store lifecycle
is independent of local cleanup; native workflow does not silently replace it.

The runtime creates an ignore rule for `.taskplane/`. Deliberately overriding Git
ignore rules, copying files, or sharing generated dashboards can disclose local
data. Git ignore is not encryption or an access-control mechanism. Deleting files
removes Taskplane's local copy; backups, Git history, exported files, and other
people's copies require separate handling.

## Host providers and network activity

The host coding agent may send code, instructions and selected evidence to its
model provider under that provider's privacy terms. Taskplane does not control
the host's data handling or retention. The original native session files remain
managed by the host even if Taskplane's `.taskplane/` directory is removed.

User-requested repository acquisition, publishing, or plugin installation may
use native Git, marketplace or other host tools that contact the selected service
with your existing credentials. Those services receive the information needed
for the requested operation. Taskplane's local observation runtime does not send
its journal, counters, source, or dashboard to an author-operated endpoint.

Taskplane does not sell personal information, operate an account database, or
use local observations for advertising.

## Contact and changes

Updates to this notice are committed with a new date. Questions about Taskplane's
local data handling: Volodymyr Demkiv — vdemkiv@gmail.com.
