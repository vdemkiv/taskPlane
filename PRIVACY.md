# Privacy Policy

_Last updated: 2026_

taskplane is a local developer tool — a plugin that runs inside your own
coding agent (for example Claude Code, Cowork, or Codex) on your machine. This policy
describes what it does and does not do with data.

## The short version

**taskplane has no telemetry, analytics, advertising identifiers, accounts, or
author-operated service.** It does process repository content, governance
records, command/review artifacts, and identifiers supplied for approvals, and
it stores those records on your filesystem. When you ask it to acquire a remote
repository or pull request, it invokes local Git/GitHub tooling that contacts
the selected provider using your machine's configuration and credentials.

## What taskplane stores, and where

taskplane writes only to your own machine, under your control:

- **Knowledge store** (decisions, requirements, tracked debt, the dependency
  graph, context docs, and loop coordination state). Where it lives is
  plan-aware:
  - **Personal plan (default):** an external store at
    `~/.taskplane/projects/<key>/`, one folder per project. It lives OUTSIDE
    your git repo, so on a personal plan taskplane's knowledge is never
    committed or pushed with your code.
  - **Team/Enterprise plan:** an in-repo store at `.taskplane-kb/`, committed
    *deliberately* alongside your code so the whole team shares one registry
    and a fresh clone can discover that sharing is available. A new local user
    still starts in the private external store; `tp share set shared` is the
    explicit opt-in before their writes use `.taskplane-kb/`. On a team plan,
    shared knowledge IS in your repo and IS committed — by design, not by
    accident.

  In both cases the store is meant to hold *decision data only*. The honest
  mechanics of that rule: `tp kb lint` is a marker-based scan for prompt
  text, raw model content markers, oversized free-text fields, and
  pricing/commercial strategy, and it is enforced **fail-closed at the
  Definition-of-Done exit gate and the engineering-review gate** — governed
  work cannot pass those gates with a flagged store. It does **not** run at
  the moment a record is first written (`tp decision` / `tp req`) or when
  `tp share push` publishes records into the shared store, and marker
  matching cannot detect every form of sensitive content. On a
  Team/Enterprise plan, review what you publish before committing
  `.taskplane-kb/` — publishing is a deliberate human act, and the lint gate
  is a backstop, not a guarantee. (`$TASKPLANE_HOME` moves the personal
  root; `tp kb where` shows the active path.)
- **Local runtime files** (e.g. `.taskplane/` — including the `trace.jsonl`
  audit trace — `.em-review/`, worktrees): the active contract, an
  append-only audit trace of tool decisions, action meters, and scratch
  review artifacts. These stay local to the checkout and git-ignored on
  **both** plans, and are not transmitted anywhere by taskplane. (Only
  knowledge is ever shared on a team plan — never the runtime trace.)

The data processed can include repository URLs and identifiers, source/history
and diffs, file paths, commands and bounded output summaries, requirements,
decisions, debt, task/session identifiers, and the actor or approval text a
human supplies. Taskplane uses it to acquire the requested code, enforce a
governed delivery contract, preserve attributable decisions, and produce
review evidence. Durable command output is minimized/redacted and closed
command records and raw review diffs have a 24-hour retention bound; rotated
audit archives are bounded and expire after seven days. Knowledge records and
the active minimized audit trace remain until you delete them.

Deleting the private store or local runtime files removes Taskplane's local
copy. Data deliberately committed to `.taskplane-kb/` can remain in Git
history, remote repositories, teammates' clones, or backups and must be
removed under the repository host's procedures as well.

## What taskplane does NOT do

- No telemetry, usage tracking, crash reporting, or "phone home."
- No cookies, fingerprinting, or advertising identifiers.
- No Taskplane account, hosted database, or server operated by the author.
- No background upload of source, prompts, governance records, or diagnostics
  to the author.
- No sale of personal information or use of processed data for advertising.

## Data handled by the host agent (not by taskplane)

taskplane runs *inside* a host coding agent. When you run a governed loop, the
host agent may send code, prompts, and your instructions to an AI model to do
the work. That data flow is handled by the **host agent and its model
provider** (for Claude Code / Cowork, that is Anthropic; for Codex, OpenAI)
under **their**
privacy policy and terms — not by taskplane. taskplane neither controls nor
receives a copy of those model interactions; it only reads the resulting files
on your disk and enforces its contracts locally.

If you use taskplane with a different host or model, that host's/provider's
privacy terms govern the model interaction.

## Third parties

Taskplane does not send telemetry to the author, but user-requested workflows
can contact third parties:

- Remote repository and pull-request preparation invokes local `git` and,
  where configured, GitHub tooling/API access. The provider receives ordinary
  request and connection metadata plus the repository/PR/ref being requested;
  authentication uses credentials already configured on your machine.
- Installing or updating Taskplane through a marketplace, Git host, or package
  manager uses that host tool's network path.
- The host coding agent may send the material described above to its model
  provider, as explained in the previous section.

These transfers are initiated by the action you request, not by telemetry.
GitHub or another selected Git/marketplace provider and the host/model provider
process the transfer under their own privacy terms and retention controls.
Review the remote URL and provider before starting acquisition, especially for
private repositories or regulated data.

## Changes

If this policy changes, the updated version will be committed to the
repository with a new "Last updated" date.

## Contact

The project owner is accountable for this notice and Taskplane's local data
handling. Questions or privacy requests: Volodymyr Demkiv — vdemkiv@gmail.com
