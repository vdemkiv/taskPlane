# Git worktrees (parallel waves)

Every parallel worker gets its own worktree — same repo, separate working
dir, separate branch — so contracts are enforceable per agent and merges
are explicit.

- Create: `git worktree add .tp-work/<task> -b tp/<task>` (from the
  approved baseline).
- Claim: `tp.py loop claim <task> --agent-workspace .tp-work/<task>` —
  the task's contract activates THERE; the hook confines that agent.
- Work happens only inside the worktree; the main tree stays clean.
- COMMIT in the worktree (`git add -A && git commit -m "<task>"`) BEFORE
  gating — the tp/<task> branch is the vehicle; an uncommitted worktree
  merges as nothing, and the engine now refuses to gate it.
- On evaluate PASS: `git merge tp/<task>` in the main tree, then
  `git worktree remove .tp-work/<task>` and delete the branch.
- Conflicts on merge mean scopes weren't truly disjoint — that's a
  planning finding for the retro, not something to patch silently.
