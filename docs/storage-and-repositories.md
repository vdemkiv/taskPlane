# Workspace storage and repositories

Use the project selected by the user and an explicit actual execution path.
Taskplane's current local runtime state lives under that workspace's `.taskplane/`,
which self-ignores generated private runtime data. Authored `specs/`, `design/`,
source, tests and versioned workflow definitions may be ordinary repository files.
A git remote or connected folder alone does not prove local execution or authorize
another checkout, network action, commit or publication.

There is no current plan-aware external repository mirror/loop registry to prepare.
Do not follow obsolete `repository prepare`, `loop`, `share` or `.taskplane-kb`
examples. Graph data under `.taskplane/knowledge/` and generated dashboard/usage
views are optional observations, distinct from controller and immutable evidence.

Cowork/explicit binding records selected-project identity and separately observed
command/worker location policy. Preserve stricter restrictions; report unavailable
locality evidence instead of guessing from a mount path. Linked worktrees and
source archives must resolve to the intended workspace and actual runtime.

Back up complete quiescent runtime state, referenced authored evidence/logs and
compatible runtime identity. Supported inactive relocation preserves archived
bytes and authentic ordinary/import provenance, refusing active or tampered
stores. A new store gets fresh authority; archived approvals stay historical.
See [state specification](state-spec.md) and [onboarding](onboarding.md).
