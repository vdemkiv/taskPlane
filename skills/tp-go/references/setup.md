
# /tp-setup — make a repo governable

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`.

0. **Cold start.** `$TP onboard --json` reports readiness — a folder to work
   in, a git repo with a snapshot, and taskplane initialized. `$TP onboard`
   (no `--json`) prints the onboarding dashboard for a brand-new user with
   nothing attached; its `next_action` is one of `attach_folder` (help them
   connect a folder or clone a URL), `init_git` (offer to init + commit),
   `tp_init` (step 1), or `ready`. Don't proceed to a governed run until
   `ready` — the gates need a real folder and a commit to diff against.

1. `$TP init` — creates `knowledge/context/{product,tech-stack,workflow}.md`
   templates, scans the dependency graph, checks for a git snapshot (gates
   fail closed without one — `git init && git add -A && git commit` if
   needed).
2. Fill the three context docs WITH the user (from the conversation or
   their answers) — the product doc feeds the product lens AND its Direction /
   north star line feeds the north-star review, tech-stack feeds engineering
   lenses, workflow sets gate conventions.
3. Register the first track: `$TP track new <name> "<goal>"`. More
   workstreams later: `track new` / `track switch` / `track close` — the
   KB, graph, and requirements are shared across tracks by design.
4. Hand off to `/tp-go` for the first governed goal.
