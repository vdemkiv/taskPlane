# Recovery from a sealed or stale run

## Incident and root cause

Taskplane 2.27.0 trapped the user's task at a Product checkpoint. After the
checkpoint was submitted, the user requested a marketplace upload and a new
README/Engineering repair run. The generated ZIP and sidecars under `dist/`, plus
Finder's `.DS_Store`, differed from the old Product source baseline. The actual
`Changes requested` response was correctly bound but `flow decide` refused it
with `scope_violation`. Reading recovery documentation and starting the prepared
run were then denied by PreToolUse because the old output remained sealed.

Three independent implementation choices produced the deadlock:

1. `Controller.start` returned any active run before considering a new request or
   scope. There was no explicit replacement transaction.
2. `Controller.apply` audited source/evidence before recording every human
   response, including rejection and requests for changes. It treated a refusal
   to accept evidence like acceptance of that evidence.
3. The sealed-output guard admitted a narrow set of exact control commands, but
   omitted `flow start` and ordinary shell-based reads. Fresh bootstrap proposals
   were allowed only before initialization. The agent therefore could not
   prepare or invoke a supported recovery within the task.

The user's authorization was not missing. Repeating the approval question did
not resolve the source mismatch. Earlier attempts using the wrong cached runtime
path are separate observations and do not establish a 2.27 runtime defect;
the source-drift decision refusal and sealed-control denials above were observed
with the installed 2.27 runtime.

## Why existing validation missed it

Tests covered out-of-scope drift refusal, normal negative responses, source
invalidation and ordinary start/reuse separately. Negative-response fixtures used
unchanged source. New-run tests started after an accepted finish. Hook tests did
not attempt a fresh run from a sealed checkpoint after an unrelated user-requested
file appeared. Some workflow fixtures called controller methods or wrote files
directly, so they could not expose a PreToolUse denial of recovery preparation.

This was a missing recovery journey across the controller and hook boundary,
not evidence that the hooks were unloaded. Disabling hooks was a user-directed
workaround for repairing the product; it is not the shipped recovery procedure.

## Repair contract

`flow start --replace-run OLD_ID --expected-revision N --scope FILE
--request-reference REF` explicitly replaces one active native run. Validation of
the run/revision, new scope, request reference and known-process quiescence happens
before the atomic state update. Failure keeps the old run active. A matching retry
returns the replacement instead of creating duplicates. Conflicting starts no
longer silently return an unrelated active scope.

The old packet and decisions remain historical and unaccepted if they were
unaccepted before. Replacement revokes the old grants and suspends its autonomous
policy. The new run has a new ID, visits and source baseline; no approvals or
policy are imported. Its native dashboard identifies the predecessor, while the
old dashboard identifies the replacement and shows no active phase. Protected
host ownership and corrupt-store recovery are unchanged.

Exactly bound negative human responses may be recorded without passing a source
audit. They still require the existing provenance, presentation, event and
checkpoint checks, and preserve the original source baseline. They do not approve
drift or authorize advancement. Approval and ordinary transitions retain those
checks.

The hook admits exact installed recovery/help/status commands, bounded read-only
diagnostics and fresh structured bootstrap proposals. Sealed files, path traversal,
shell operators, executable search helpers, ordinary source writes and live-process
replacement remain denied. Starting over does not require uninstalling the plugin,
disabling hooks, deleting history or accepting the old checkpoint.

## Regression evidence

`taskplane/tests/test_workflow_recovery.py` reproduces the original same-run return,
then exercises replacement with unchanged, unrelated and changed-evidence inputs;
wrong bindings and scope; live processes; replay; all seven phase checkpoints;
negative responses and forbidden approval over drift; and shell/path exclusions.

The Codex journeys invoke the manifest's actual hook commands and the CLI for
Product, Design and Engineering standalone entries, then replace them with a full
delivery route. They add an upload ZIP and Finder metadata after sealing, record
the negative response, mutate evidence, replace the run, check historical and
dashboard identity, and submit fresh Product evidence while confirming that
unapproved advancement remains blocked. The same journey runs against an
extracted Codex package whose runtime files are compared with the candidate.

These are automated hook/CLI and package tests, not a claim that the desktop host
has reloaded the candidate plugin or that its dashboard was visually verified.
The recovery repair was explicitly requested with hooks disabled; no earlier
unaccepted phase was relabeled as approved to perform it.
