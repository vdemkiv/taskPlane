
# /tp-setup — make a repo governable

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`.

0. **Cold start.** `$TP onboard --json` reports readiness — a folder to work
   in, a git repo with a snapshot, and taskplane initialized. `$TP onboard`
   (no `--json`) prints the onboarding dashboard for a brand-new user with
   nothing attached; its `next_action` is one of `attach_folder` (help them
   connect a folder or clone a URL), `init_git` (offer to init + commit),
   `tp_init` (step 1), or `ready`. Don't proceed to a governed run until
   `ready` — the gates need a real folder and a commit to diff against.

1. **Plan & knowledge storage (ask FIRST).** Ask the user: *personal plan
   (Free/Pro/Max), or Team/Enterprise?* Then `$TP share plan
   personal|team|enterprise`. Personal keeps all knowledge in the private
   external store (`~/.taskplane` — the classic mode). Team/Enterprise
   switches to the SHARED in-repo store (`.taskplane-kb/`, committed with
   the work — Claude-Tag compatible, every teammate's clone inherits it).
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
4. **Model tiers (cost routing).** `$TP onboard --json` includes
   `model_tiers` — the resolved tier→model map. Explain the default to the
   user: only `cheap` is pinned (`haiku`, used for the lens sweep and tasks a
   planner marks cheap); `standard`/`deep` inherit the session model until
   `TASKPLANE_MODEL_STANDARD` / `TASKPLANE_MODEL_DEEP` are set. Offer to set
   them now if they want cost-differentiated routing, and mention
   `TASKPLANE_ENFORCE_DISPATCH=warn` + `tp loop verify-dispatch` for making
   the routing verified rather than assumed (discipline/model-tiers.md).
   Defaults are fine — skip if unsure.
5. Register the first track: `$TP track new <name> "<goal>"`. More
   workstreams later: `track new` / `track switch` / `track close` — the
   KB, graph, and requirements are shared across tracks by design.
6. Hand off to `/tp-go` for the first governed goal.
