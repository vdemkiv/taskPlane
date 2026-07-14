# Privacy Policy

_Last updated: 2026_

taskplane is a local developer tool — a plugin that runs inside your own
coding agent (e.g. Claude Code / Cowork) on your machine. This policy
describes what it does and does not do with data.

## The short version

**taskplane collects nothing, sends nothing, and stores nothing on any server
operated by its author.** It has no telemetry, no analytics, no accounts, and
no network calls of its own. Everything it produces stays on your filesystem.

## What taskplane stores, and where

taskplane writes only to your own machine, under your control:

- **External knowledge store** (`~/.taskplane/projects/<key>/`, one folder
  per project): decisions, requirements, tracked debt, the dependency graph,
  context docs, and loop coordination state. This lives OUTSIDE your git repo
  by design — so taskplane's artifacts are never committed or pushed with
  your code. It is *decision data only* — the `kb lint` check mechanically
  blocks prompt text, raw model content, and pricing/commercial strategy from
  the store. (`$TASKPLANE_HOME` moves the root; `tp kb where` shows the path.)
- **Local runtime files** (e.g. `.taskplane/`, `.em-review/`, worktrees):
  the active contract, an append-only audit trace of tool decisions, action
  meters, and scratch review artifacts. These stay local to the checkout and
  are not transmitted anywhere by taskplane.

You own all of it. Deleting these files removes the data; nothing persists
elsewhere.

## What taskplane does NOT do

- No telemetry, usage tracking, crash reporting, or "phone home."
- No cookies, fingerprinting, or advertising identifiers.
- No accounts, logins, or personal information collected by taskplane.
- No network requests initiated by the plugin itself. taskplane is pure
  Python standard library plus `git`; it has no runtime dependencies and
  opens no sockets.

## Data handled by the host agent (not by taskplane)

taskplane runs *inside* a host coding agent. When you run a governed loop, the
host agent may send code, prompts, and your instructions to an AI model to do
the work. That data flow is handled by the **host agent and its model
provider** (for Claude Code / Cowork, that is Anthropic) under **their**
privacy policy and terms — not by taskplane. taskplane neither controls nor
receives a copy of those model interactions; it only reads the resulting files
on your disk and enforces its contracts locally.

If you use taskplane with a different host or model, that host's/provider's
privacy terms govern the model interaction.

## Third parties

taskplane integrates with no third-party services and shares data with no one.
Installing it from a marketplace (e.g. GitHub) is a normal `git` fetch subject
to that host's terms; taskplane itself transmits nothing back.

## Changes

If this policy changes, the updated version will be committed to the
repository with a new "Last updated" date.

## Contact

Questions about privacy: Volodymyr Demkiv — vdemkiv@gmail.com
