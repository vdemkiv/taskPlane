
# /tp-setup — make a repo governable

Resolve `$TP` using the launcher rules in `../SKILL.md`; never use an empty plugin-root fallback.

0. **Cold start.** `$TP onboard --json` reports readiness — a folder to work
   in, a git repo with a snapshot, and taskplane initialized. `$TP onboard`
   (no `--json`) prints the onboarding dashboard for a brand-new user with
   nothing attached; its `next_action` is one of `attach_folder` (help them
   provide a local path/URL/PR and run `$TP repository prepare <target>`;
   ask and resume any returned user action in the same conversation),
   `init_git` (offer to init + commit),
   `recover_run_binding` (repair only the declared locator),
   `repair_phase_configuration` (use a validated build), `tp_init` (step 2),
   a named host-readiness action, or `ready`. Don't proceed to a governed run until
   `ready` — the gates need a real folder and a commit to diff against.

   Repository preflight owns mirrors/worktrees under the external taskplane
   home. Its run manifest owns private state, graph, evidence, lens outputs,
   and deliverables. Never clone source into `.em-review` or another artifact
   tree, and never require a new Codex task for a recoverable precondition.

1. **Knowledge storage (ask FIRST).** Ask the user: *keep taskplane knowledge
   private/local, or share it with the team in the repository?* This is a
   storage choice, not the name of their Claude, ChatGPT, or Codex subscription.
   Then `$TP share plan personal|team|enterprise`. `personal` keeps all
   knowledge in the private external store (`~/.taskplane`). `team` or
   `enterprise` switches to the SHARED in-repo store (`.taskplane-kb/`,
   committed with the work; every teammate's clone inherits it).
   Both are changeable any time. On a team plan, an individual can still
   work privately: `$TP share set private` keeps their decisions in the
   private store, and when they're ready to make work visible to the team —
   like pushing commits — `$TP share push [--ids 0001,0002]` publishes
   selected decisions into `.taskplane-kb/` (then commit it). `$TP share
   status` shows the current mode and the unpublished count.
2. `$TP init` — creates the context doc templates
   (`product.md` / `tech-stack.md` / `workflow.md` / `current-state.md`),
   scans the dependency graph, checks for a git snapshot (gates
   fail closed without one — `git init && git add -A && git commit` if
   needed). `init --plan team|enterprise|personal` records the plan in the
   same step; without it, init's JSON includes `plan_question` — ask it.
3. Fill the context docs WITH the user (from the conversation or
   their answers) — the product doc feeds the product lens AND its Direction /
   north star line feeds the north-star review, tech-stack feeds engineering
   lenses, workflow sets gate conventions.
4. **Phase settings.** Read the phase settings and digest from `$TP onboard
   --json`. The canonical operational settings file owns each phase's model,
   reasoning, lens policy and limits, including Retro. Models inherit and
   reasoning defaults to high. The single phase registry owns role and skill
   links; mismatches block readiness. Existing runs consume their exact sealed
   snapshot rather than today's defaults or environment.
5. Register the first track: `$TP track new <name> "<goal>"`. More
   workstreams later: `track new` / `track switch` / `track close` — the
   KB, graph, and requirements are shared across tracks by design.
6. Hand off to the `taskplane` façade for the first governed goal; it routes
   internally to `/tp-go` without exposing the loop choreography.
