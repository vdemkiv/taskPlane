# Claude Tag (beta) — taskplane in your Slack channels

How taskplane runs governed work inside Claude Tag, Slack's shared @Claude
identity — what changes in that environment and what stays enforced.

[Claude Tag](https://claude.com/docs/claude-tag/overview) runs @Claude as
your organization's shared identity in Slack (Team/Enterprise, public
beta). taskplane adapts to that environment with three mechanisms:

- **Repo-persisted store.** Tag's sandbox is ephemeral — `~` is discarded
  when the conversation idles. Set `TASKPLANE_STORE=repo` and the knowledge
  store (decisions, requirements, loop state) lives at `.taskplane-kb/`
  inside the repo, committed and pushed with the work. The next Tag session
  resumes the loop by cloning the branch.
- **Attributable human gates.** There is no PreToolUse hook layer in Tag,
  so gates are process + audit: at `plan_approval` and `signoff` the loop
  parks, the gate summary goes to the thread, and only a real person's
  reply unlocks it — recorded with `tp loop approve --by "Dana — 'approved'
  in #platform-eng"`. The approver lands in the trace and the KB, so every
  gate pass is attributable. An approve without `--by` is detectable as a
  self-approval.
- **The `tp-tag` skill** carries the full thread protocol: compact status
  posts, the dashboard attached at every gate, scope restated before each
  execute step, and a hard rule the skill never breaks — it does not
  approve gates on its own, under any phrasing of urgency.

To deploy: an Owner attaches the taskplane plugin to a scope (channel,
workspace, or org) from the Access bundle's Plugins tab or a skills
repository — see [Customize Claude
Tag](https://claude.com/docs/claude-tag/admins/customize). Honest limit:
Tag's plugin surface today is skills-only, so enforcement is by process,
visibility, and trace — not by mechanical interception. The hook layer
remains fully active in Claude Code and Cowork. Individuals can work
privately even on a team plan and publish selected decisions to the
channel's shared store with `tp share push` — see the changelog's v1.5.0
entry.
