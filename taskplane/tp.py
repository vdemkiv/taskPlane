#!/usr/bin/env python3
"""tp — taskplane-governance plugin CLI (stdlib only).

Subcommands the govern-under-contract skill drives:

  tp.py new --scope "src/**,tests/**" --deny "git push" --tests "pytest -q" GOAL
      Create + activate a contract for the current workspace. Records a git
      snapshot so the DoD scope-diff has a baseline. Activating a contract
      turns on the PreToolUse enforcement hook for this workspace.

  tp.py screen                 (called by the PreToolUse hook; reads event JSON)
      Emit a Cowork hook decision: {"decision":"approve"} or
      {"decision":"block","reason":...}. Blocks out-of-scope writes, denied
      commands, and disallowed tools BEFORE they run.

  tp.py ready                  Definition-of-Ready ENTRY gate: is the task
      well-formed and safe to start? Exit 1 with blockers if not.
  tp.py status                 Print the active contract + budget note.
  tp.py budget --spent 0.42    Record a cooperative spend estimate (advisory).
  tp.py dod                    Definition-of-Done EXIT gate; exit 1 on fail.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taskplane_lite as tp  # noqa: E402


# Shared help text for the universal --workspace plumbing flag. It is
# declared on 34 parsers; the CLI-reference generator refuses any flag
# with empty help, so one constant keeps all 34 honest and identical.
_WS_HELP = "repo root this command operates on (default: the cwd)"


def _workspace(explicit: str | None = None) -> str:
    return os.path.abspath(explicit or os.getcwd())


def north_star(ws: str) -> str | None:
    """The project's Direction / north star — the one line the on-demand
    north-star review measures every strategic call against. Read from the
    Direction line of context/product.md. Returns None if the doc is missing,
    the line is absent, or it still holds only the scaffold's placeholder hint
    (parenthetical), so the review can tell the human to fill it in."""
    p = os.path.join(tp.kb_root(ws), "context", "product.md")
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    for line in text.splitlines():
        if "north star" in line.lower():
            val = line.split(":", 1)[1] if ":" in line else ""
            val = val.strip().lstrip("*").strip().rstrip("*").strip()
            if val.startswith("(") and val.endswith(")"):
                return None          # unfilled scaffold placeholder
            return val or None
    return None


def _is_commit_sha(head: str | None) -> bool:
    """A real commit hash — SHA-1 (40 hex) OR SHA-256 (64 hex). The old
    40-only pattern treated a SHA-256 repo with a real commit as having
    none, stalling onboarding forever."""
    return bool(head and re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", head))


def _bare_root(ws: str) -> bool:
    """True when ws is the session home / filesystem root WITHOUT being a
    real committed git project — the zero state. A contract must never be
    scoped here: a leaked one would govern the whole session (the
    locked-contract incident started exactly this way). Shared by the
    onboarding report and the `tp new` refusal so both apply ONE rule."""
    home = os.path.abspath(os.path.expanduser("~"))
    # Known agent-sandbox session homes as of v2.x: "/home/claude" is the
    # Cowork/Claude sandbox session home and "/root" a common container
    # default — a point-in-time snapshot of the host layouts this plugin has
    # shipped on (the locked-contract incident started in exactly such a
    # home). REVISIT TRIGGER: any newly supported host runtime with a
    # different session home (e.g. /workspace, /home/user). Until then a
    # deployment can extend the guard without a code change via
    # TASKPLANE_BARE_ROOT (os.pathsep-separated extra roots); the env var
    # only ADDS protected roots — the default set is unchanged.
    bare = {home, "/", "/root", "/home/claude"}
    # `os.path.expanduser` consults USERPROFILE on Windows and never HOME, so
    # a host that sets one and not the other left the real session home
    # UNPROTECTED — the guard silently passed there. Add every home the
    # environment names: this can only ever ADD protected roots, which is the
    # fail-safe direction for a guard whose whole job is refusing to scope a
    # contract at the session home.
    for var in ("HOME", "USERPROFILE"):
        val = (os.environ.get(var) or "").strip()
        if val:
            bare.add(os.path.abspath(os.path.expanduser(val)))
    for extra in (os.environ.get("TASKPLANE_BARE_ROOT") or "").split(os.pathsep):
        if extra.strip():
            bare.add(os.path.abspath(os.path.expanduser(extra.strip())))
    # Windows path comparison is case-insensitive and separator-agnostic;
    # os.path.normcase is the identity elsewhere, so this is a no-op there.
    bare = {os.path.normcase(os.path.normpath(b)) for b in bare}
    if os.path.normcase(os.path.normpath(ws)) not in bare:
        return False
    inside_git = tp._run(["git", "rev-parse", "--is-inside-work-tree"],
                         cwd=ws).stdout.strip() == "true"
    return not (inside_git and _is_commit_sha(tp.git_head(ws)))


# --------------------------------------------------------- install truth
# The observed launch failure (R-0005): org/Team members CANNOT install
# plugins from GitHub — only org admins can publish to the org marketplace,
# and file-upload is the other path. Onboarding must speak to the account
# type it can detect, and fall back to the honest by-account-type triage
# when it can't. Detection is deliberately best-effort and mechanical:
#   * org-managed — a Claude Code managed-settings file is present on this
#     host (the only org marker hosts expose today);
#   * personal   — this plugin is running from a user-scope plugin install
#     (a `.claude/plugins` path);
#   * unknown    — no marker; print the triage, never a member-dead-end.

_MANAGED_SETTINGS_PATHS = (
    "/etc/claude-code/managed-settings.json",
    "/Library/Application Support/ClaudeCode/managed-settings.json",
    r"C:\ProgramData\ClaudeCode\managed-settings.json",
)

_PERSONAL_INSTALL_MARKERS = (".claude/plugins", ".claude\\plugins")


def _install_context(plugin_path: str | None = None) -> str:
    """Best-effort detection of HOW taskplane reached this host.
    Returns 'org-managed' | 'personal' | 'unknown'. Never guesses: with no
    mechanical marker it answers 'unknown' so onboarding prints the
    by-account-type triage instead of a path the user might not have."""
    for p in _MANAGED_SETTINGS_PATHS:
        try:
            if os.path.isfile(p):
                return "org-managed"
        except OSError:
            continue
    path = plugin_path or os.path.abspath(__file__)
    if any(m in path for m in _PERSONAL_INSTALL_MARKERS):
        return "personal"
    return "unknown"


def _install_paths_lines(ctx: str) -> list[str]:
    """Plain-text install/update guidance per detected context. INVARIANT
    (tested): no line addressed to an org member ever contains a step a
    member cannot run (no GitHub marketplace-add / install command).
    Codex-host fix (v2.5.x, codex-compat review): on a host onboard itself
    reports as codex, Claude-only paths (Organization settings > Plugins)
    are the wrong universe — print the Codex marketplace path instead."""
    if os.environ.get("CODEX_HOME") or os.environ.get("CODEX_THREAD_ID"):
        return [
            "install (Codex host): taskplane ships as an OpenAI marketplace "
            "package — install/update it with the Codex plugin tooling "
            "(`codex plugin` in the CLI, or the desktop app's plugin "
            "catalog). See README > Quickstart: Codex.",
            "The Claude org-admin/marketplace paths do not apply on this "
            "host.",
        ]
    if ctx == "org-managed":
        return [
            "install: this host is org-managed — get taskplane (and its "
            "updates) from your organization's plugin catalog.",
            "If it's missing from the catalog, ask an org admin to publish "
            "it (Organization settings > Plugins — file upload, or GitHub "
            "sync from a private/internal mirror). See README > Install.",
        ]
    if ctx == "personal":
        return [
            "install: personal plugin install detected — update from your "
            "plugin catalog / marketplace as usual. See README > Install.",
        ]
    return [
        "install paths by account type (host context not detectable):",
        "  - Team/Enterprise member (not an admin): you cannot add "
        "taskplane from GitHub yourself — install it from your org's "
        "plugin catalog, or ask an org admin to publish it there.",
        "  - Org admin: Organization settings > Plugins — upload the "
        "plugin file, or GitHub-sync a private/internal mirror of the "
        "repo; then set it Available or Required.",
        "  - Personal Pro/Max account: add the GitHub marketplace "
        "directly (README > Install has the commands).",
    ]


def _onboard_report(ws: str) -> dict:
    """Cold-start readiness: does the workspace have the three things a
    governed run needs — a real folder to work in, a git repo with a
    snapshot (gates fail closed without one), and taskplane initialized.
    Returns a checklist + the single next action, so the onboarding UI can
    walk a brand-new user in from a zero state (no folder, no repo)."""
    inside_git = tp._run(["git", "rev-parse", "--is-inside-work-tree"],
                         cwd=ws).stdout.strip() == "true"
    head = tp.git_head(ws)
    has_commit = _is_commit_sha(head)
    try:
        entries = [e for e in os.listdir(ws)
                   if e not in (".git", ".taskplane", "knowledge", "plan",
                                ".gitignore", ".DS_Store")]
    except OSError:
        entries = []
    has_files = bool(entries)
    # A "bare" workspace is the session home or filesystem root — the classic
    # zero state where nothing has been attached. But a real git PROJECT that
    # happens to live at $HOME (files + a commit — exactly this environment's
    # cwd) is a genuine workspace, not the empty zero state: don't force it to
    # "attach a folder it's already in". Home/root is bare only when it's NOT
    # a committed git tree.
    bare_root = _bare_root(ws)
    # A committed checkout is a project even when its only tracked file is an
    # otherwise ignored marker such as .gitignore (or the commit is empty).
    looks_like_project = (has_files or (inside_git and has_commit)) and not bare_root
    has_context = os.path.isdir(os.path.join(tp.kb_root(ws), "context"))

    is_codex = bool(os.environ.get("CODEX_HOME")
                    or os.environ.get("CODEX_THREAD_ID"))
    host = "codex" if is_codex else "claude"
    workspace_hint = (
        "Open this repository as the working folder in Codex (CLI: `cd` to "
        "the repo before starting `codex`; desktop: open/create a local "
        "environment for the repo), then start a new task."
        if is_codex else
        "Connect a folder (Cowork: attach a folder; Claude Code: open your "
        "project) — or use this one if it's where you want to work."
    )
    checks = [
        {"id": "workspace", "label": "A folder to work in",
         "ok": looks_like_project,
         "detail": ws if looks_like_project else
         f"{ws} — looks empty or scratch",
         "hint": workspace_hint},
        {"id": "git", "label": "A git repo with a snapshot",
         "ok": inside_git and has_commit,
         "detail": (head[:12] if has_commit else
                    "git repo, no commit yet" if inside_git else "not a repo"),
         "hint": "Gates need a commit to diff against. `git init` here (I can "
                 "do it), or point me at a repo URL to clone."},
        {"id": "init", "label": "taskplane initialized",
         "ok": has_context,
         "detail": "context docs present" if has_context else
         "not initialized",
         "hint": "`tp init` scaffolds context docs, the KB, and the graph — "
                 "I run it for you once a folder + repo are in place."},
    ]
    ready = all(c["ok"] for c in checks)
    if not looks_like_project:
        nxt = "attach_folder"
    elif not (inside_git and has_commit):
        nxt = "init_git"
    elif not has_context:
        nxt = "tp_init"
    else:
        nxt = "ready"
    artifacts = None
    try:
        _art = os.path.join(tp.store_root(ws), "artifacts")
        _tracks = sorted(os.listdir(_art)) if os.path.isdir(_art) else []
        if _tracks:
            artifacts = {"path": tp.to_posix(_art), "tracks": _tracks,
                         "note": "prior gate snapshots - a context "
                                 "cache; read before re-deriving"}
    except Exception:
        artifacts = None
    _ictx = _install_context()
    return {"workspace": ws, "host": host, "artifacts": artifacts,
            # R-0005 install truth: the account-type install/update paths,
            # matched to the detected context (org-managed / personal) or
            # the honest by-account-type triage when undetectable — never
            # a step an org member cannot run.
            "install": {"context": _ictx,
                        "paths": _install_paths_lines(_ictx)},
            "looks_like_project": looks_like_project,
            "is_git": inside_git, "has_commit": has_commit,
            "has_context": has_context, "ready": ready,
            "checks": checks, "next_action": nxt,
            # Resolved model routing, visible at cold start: with defaults
            # Claude pins only `cheap`; Codex inherits all tiers so another
            # provider's model id is never dispatched. Overrides remain
            # available through TASKPLANE_MODEL_<TIER> (discipline/
            # model-tiers.md). Surfacing it here is what makes the routing
            # discoverable instead of a silent no-op.
            "model_tiers": {t: (tp.model_for_tier(t) or "inherit")
                            for t in tp.MODEL_TIERS},
            "reasoning_tiers": {t: tp.reasoning_for_tier(t)
                                for t in tp.MODEL_TIERS}}


# --------------------------------------------------------------- new

# Advisory cooperative dollar ceiling attached to CLI-created contracts.
# Not a harness invariant (Cowork can't intercept spend) — a stop signal for
# `tp budget`. The kernel's action budget is the enforced ceiling.
DEFAULT_MAX_COST_USD = 3.0


def cmd_new(a) -> int:
    ws = _workspace(a.workspace)
    if _bare_root(ws):
        print("taskplane: REFUSING to activate a contract here — "
              f"{ws} is the session home / filesystem root, not a project. "
              "A contract scoped here would govern the entire session, and a "
              "leaked one is exactly how a session gets locked. cd into (or "
              "--workspace) a real project checkout, or `git init && git "
              "commit` first if this really is your project folder.",
              file=sys.stderr)
        return 1
    # Build via the shared kernel builder so a CLI-created contract has the
    # EXACT shape the loop engine builds — one contract schema, not two.
    # (The old local CONTRACT_TEMPLATE diverged and crashed cmd_status/
    # cmd_budget on any loop-created contract.)
    scope = ([s.strip() for s in a.scope.split(",") if s.strip()]
             if a.scope else [])
    tools = ([t.strip() for t in a.tools.split(",") if t.strip()]
             if a.tools else [])
    deny_extra = [d.strip() for d in a.deny] if a.deny else []
    c = tp.build_contract(
        " ".join(a.goal),
        scope=scope,
        read_only=bool(getattr(a, "read_only", False)),
        write_allow=(list(a.write_allow)
                     if getattr(a, "write_allow", None) else None),
        tools=tools,
        test_command=a.tests or None,
        deny_extra=deny_extra,
        max_actions=(int(a.max_actions)
                     if getattr(a, "max_actions", None) is not None
                     else None),
    )
    # cooperative dollar advisory (kept on the shared shape as an optional
    # key). `is not None`, NOT truthiness: `--budget 0` means a ZERO ceiling
    # (maximally strict — any spend is over), never the $3 default.
    if a.budget is not None and a.budget < 0:
        print("taskplane: --budget must be >= 0 (0 means no cooperative "
              "spend allowed).", file=sys.stderr)
        return 1
    c["budget"]["max_cost_usd"] = float(a.budget) if a.budget is not None \
        else DEFAULT_MAX_COST_USD

    snapshot = tp.git_head(ws)
    tp.activate(ws, c, snapshot=snapshot)

    mode = "READ-ONLY review" if c.get("read_only") else "build"
    print(f"taskplane: contract {c['task_id']} active ({mode}).")
    if c.get("read_only"):
        print(f"  writable  : {c.get('write_allow') or '(nothing — reads only)'}")
    print(f"  scope     : {c['coding']['scope_paths'] or '(any — set --scope!)'}")
    print(f"  deny cmds : {c['coding']['command_policy']['deny']}")
    print(f"  tests     : {c['coding']['dod']['test_command'] or '(none)'}")
    snap_disp = snapshot[:12] if snapshot else "NONE (git commit first)"
    print(f"  snapshot  : {snap_disp}")
    if not snapshot:
        print("  ! not a git repo / no commit: run `git init && git add -A "
              "&& git commit -m init` for the DoD scope-diff to work.",
              file=sys.stderr)
    print("\nThe PreToolUse hook now blocks out-of-scope writes, denied "
          "commands, and disallowed tools.")
    # Report the Definition-of-Ready verdict at activation time.
    ready, blockers, warnings = tp.dor_check(c, ws, snapshot)
    _print_dor(ready, blockers, warnings)
    print("Then do the work, and run `tp.py dod` to close.")
    return 0


def _print_dor(ready, blockers, warnings) -> None:
    print("\ntaskplane DoR (ready to start?): "
          + ("READY ✅" if ready else "NOT READY ❌"))
    for b in blockers:
        print("  ✗ " + b)
    for w in warnings:
        print("  ! " + w)


# --------------------------------------------------------------- ready

def cmd_screen_dispatch(a) -> int:
    """PreToolUse hook for the Agent/Task tool: verify the driver dispatched
    the model the most recent matching brief resolved (tier routing). OPT-IN
    — inert unless TASKPLANE_ENFORCE_DISPATCH=warn|strict. Warn is advisory;
    strict fails closed on a mismatch or verification error so the driver
    must re-dispatch with the brief's exact native Codex identity, role marker,
    model, and reasoning effort."""
    mode = (os.environ.get("TASKPLANE_ENFORCE_DISPATCH") or "").strip().lower()
    try:
        event = json.load(sys.stdin)
    except Exception as exc:
        if mode == "strict":
            reason = ("taskplane dispatch check: malformed hook input "
                      f"({type(exc).__name__}); strict verification cannot "
                      "prove this dispatch, so it is denied.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        return 0
    if mode not in ("warn", "strict"):
        return 0                                   # opt-in: default inert
    try:
        ti = event.get("tool_input") or {}
        native_codex = bool(ti.get("task_name"))
        agent = (ti.get("task_name") or ti.get("subagent_type")
                 or ti.get("agent_type") or "")
        model = ti.get("model")
        effort = ti.get("reasoning_effort")
        message = ti.get("message") or ti.get("prompt") or ""
        if not isinstance(message, str):
            message = ""
        ws = _workspace(event.get("cwd"))
        strict = mode == "strict"
        exp = tp.peek_expectation(ws, agent, strict=strict)
        name_ok = True
        if native_codex and exp is None:
            exp = tp.peek_expectation(ws, strict=strict)
            name_ok = exp is None
        expected_model = exp and exp.get("model")
        expected_effort = exp and exp.get("reasoning_effort")
        unknown_governed = exp is None and native_codex and agent.startswith(
            "tp_")
        model_ok = exp is None or (model == expected_model if native_codex
                                  else expected_model is None or
                                  model == expected_model)
        effort_ok = exp is None or not native_codex or effort == expected_effort
        marker = exp and (exp.get("role_marker") or tp.role_marker(
            exp.get("agent", "")))
        marker_present = bool(marker) and any(
            line.strip() == marker for line in message.splitlines())
        role_ok = exp is None or (
            marker_present if native_codex else
            not ti.get("role") or ti.get("role") == exp.get("agent"))
        ok = name_ok and not unknown_governed and model_ok and effort_ok \
            and role_ok
        ok = tp.commit_dispatch_verification(
            ws, agent, model, exp, ok, effort, strict=strict)
        if ok:
            return 0
        if exp is None:
            reason = (f"taskplane dispatch check: native Codex task_name "
                      f"{agent!r} claims taskplane ownership but no matching "
                      "emitted brief exists; use the exact task_name from "
                      "`tp loop next` or `tp lens dispatch`.")
        else:
            reason = (f"taskplane dispatch check: brief "
                      f"'{exp.get('ref') or exp['agent']}' requires "
                      f"task_name={exp.get('task_name')}, "
                      f"role={exp.get('agent')}, model="
                      f"{expected_model or '<inherit>'}, reasoning_effort="
                      f"{expected_effort}; observed task_name={agent}, "
                      f"role_marker={'present' if marker_present else 'missing'}, "
                      f"model={model or '<inherit>'}, reasoning_effort="
                      f"{effort or '<unset>'}. Re-dispatch with the exact "
                      "native Codex fields from the brief.")
        if mode == "strict":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        else:
            print(json.dumps({"systemMessage": reason}))
        return 0
    except Exception as e:
        if mode == "strict":
            reason = ("taskplane dispatch check errored "
                      f"({type(e).__name__}); strict verification cannot "
                      "prove this dispatch, so it is denied.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        elif mode == "warn":
            print(json.dumps({"systemMessage":
                              f"taskplane dispatch check errored: {e}"}))
        return 0


def _subagent_event() -> dict:
    try:
        event = json.load(sys.stdin)
        return event if isinstance(event, dict) else {}
    except Exception:
        return {}


def _subagent_workspace(event: dict) -> str:
    """Lifecycle hooks stay advisory even on semantically malformed input."""
    cwd = event.get("cwd")
    return _workspace(cwd if isinstance(cwd, str) and cwd else None)


def cmd_subagent_start(a) -> int:
    """Codex lifecycle trace plus bounded, advisory contract context."""
    event = _subagent_event()
    ws = _subagent_workspace(event)
    agent_id = event.get("agent_id")
    agent_type = event.get("agent_type")
    tp.trace(ws, "subagent_start", agent_id=agent_id,
             agent_type=agent_type, turn_id=event.get("turn_id"),
             permission_mode=event.get("permission_mode"))
    try:
        contract = tp.load_active(ws)
    except Exception as exc:
        contract = None
        tp.trace(ws, "subagent_context_error", agent_id=agent_id,
                 error=type(exc).__name__)
    if isinstance(contract, dict) and contract:
        coding = contract.get("coding")
        coding = coding if isinstance(coding, dict) else {}
        scopes = coding.get("scope_paths")
        scope_count = len(scopes) if isinstance(scopes, list) else 0
        raw_task_id = str(contract.get("task_id") or "unknown")
        safe_task_id = re.sub(r"[^A-Za-z0-9_.:/-]+", "_",
                              raw_task_id).strip("_")[:96] or "unknown"
        context = ("[taskplane] Governed subagent lifecycle is active. "
                   "Lifecycle hooks trace activity only; PreToolUse "
                   "screening and DoD evidence remain authoritative. "
                   f"Contract={safe_task_id}; "
                   f"read_only={bool(contract.get('read_only'))}; "
                   f"scope_entries={scope_count}. Preserve the "
                   "emitted taskplane role and task slot.")
        context = context[:560] + ("…" if len(context) > 560 else "")
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": context}}))
    return 0


def cmd_subagent_stop(a) -> int:
    """Trace Codex subagent completion without creating a new gate."""
    event = _subagent_event()
    ws = _subagent_workspace(event)
    tp.trace(ws, "subagent_stop", agent_id=event.get("agent_id"),
             agent_type=event.get("agent_type"),
             turn_id=event.get("turn_id"),
             has_transcript=bool(event.get("agent_transcript_path")),
             has_message=bool(event.get("last_assistant_message")))
    print("{}")  # SubagentStop requires JSON on successful exit.
    return 0


def cmd_decision(a) -> int:
    """Decision registry (R-0002): structured ADRs with lifecycle, links and
    supersede chains — `tp decision new/list/show/accept/supersede`."""
    import kb as _kb
    ws = _workspace(a.workspace)
    act = a.decision_action
    if act == "new":
        alts = []
        for spec in (a.alternative or []):
            parts = [p.strip() for p in spec.split("|")]
            alts.append({"option": parts[0],
                         "gained": parts[1] if len(parts) > 1 else "",
                         "given_up": parts[2] if len(parts) > 2 else ""})
        alt_md = "\n".join(
            f"- **{x['option']}** — gained: {x['gained'] or '—'}; "
            f"given up: {x['given_up'] or '—'}" for x in alts)
        links = {}
        if a.req:
            links["requirement"] = a.req
        if a.modules:
            links["modules"] = [m.strip() for m in a.modules.split(",")]
        rec = _kb.record_decision(
            ws, a.title, context=a.context or "",
            decision=a.decision or "", rationale=a.rationale or "",
            alternatives=alt_md, tags=(a.tags or "").split(",") if a.tags
            else ["decision"], links=links, status=a.status)
        if a.supersedes:
            _kb.supersede(ws, a.supersedes, rec["id"])
        print(json.dumps({"recorded": rec["id"], "status": rec["status"],
                          "links": links,
                          "alternatives": len(alts),
                          "supersedes": a.supersedes}, indent=2))
    elif act == "list":
        ds = _kb.list_decisions(ws)
        if a.status_filter:
            ds = [d for d in ds if d["status"] == a.status_filter]
        print(json.dumps([{"id": d["id"], "title": d["title"],
                           "status": d["status"]} for d in ds], indent=2))
    elif act == "show":
        d = _kb.get_decision(ws, a.id)
        if not d:
            print(f"taskplane: no decision {a.id}", file=sys.stderr)
            return 1
        print(json.dumps(d, indent=2))
        p = os.path.join(_kb.kb_dir(ws), d["file"])
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                print(f.read())
    elif act == "accept":
        d = _kb.set_status(ws, a.id, "accepted")
        if d is None:                       # unknown id — don't exit 0 silently
            print(json.dumps({"error": f"no decision {a.id}"}, indent=2))
            return 1
        print(json.dumps({"id": a.id, "status": d["status"]}, indent=2))
    elif act == "supersede":
        if _kb.get_decision(ws, a.id) is None:
            print(json.dumps({"error": f"no decision {a.id}"}, indent=2))
            return 1
        _kb.supersede(ws, a.id, a.by)
        print(json.dumps({"superseded": a.id, "by": a.by}, indent=2))
    return 0


def cmd_gc(a) -> int:
    """Prune taskplane-minted RUNTIME artifacts only — FUSE tombstones,
    orphaned .tmp files, stale .lock/.lockdir leftovers. Never governance
    records (contracts, KB, trace, loop state stay untouched)."""
    ws = _workspace(a.workspace)
    out = tp.gc_runtime(ws)
    print(json.dumps(out, indent=2))
    return 0


def cmd_clear(a) -> int:
    """Deactivate THIS process's contract (e.g. when a review ends), so the
    enforcement hook stops governing subsequent work.

    The guard resolves the contract through `tp.active_contract_path` — the
    SAME slot-aware resolution the kernel's `clear()` acts on (TASKPLANE_TASK
    selects .taskplane/active/<slot>.json; unset selects the legacy single
    slot). It used to hardcode the legacy path, so a slotted agent following
    the documented release step got "no active contract to clear" and exit 0
    while its slot file stayed on disk — and because `load_active()` governs a
    slot-less process by the MOST RESTRICTIVE UNION of every active slot, each
    leaked slot permanently tightened what every later agent could do."""
    ws = _workspace(a.workspace)
    path = tp.active_contract_path(ws)    # slot-aware: what clear() removes
    if not os.path.exists(path):
        print("taskplane: no active contract to clear.")
        return 0
    try:
        c = tp.load_json(path, default={}, what="active contract")
    except tp.StateError:
        c = {}                            # corrupt slot: still clearable
    if not isinstance(c, dict):
        c = {}
    slot = tp.task_slot()
    tp.clear(ws)                          # FUSE-safe removal (safe_remove)
    print(f"taskplane: contract {c.get('task_id','')} cleared"
          + (f" (slot {slot})" if slot else "")
          + " — workspace is ungoverned again.")
    return 0


def cmd_ready(a) -> int:
    ws = _workspace(a.workspace)
    c = tp.load_active(ws)
    if c is None:
        print("taskplane: no active contract — run `tp.py new …` first.",
              file=sys.stderr)
        return 1
    snap_path = os.path.join(tp.tp_dir(ws), "snapshot")
    snapshot = None
    if os.path.exists(snap_path):
        with open(snap_path, encoding="utf-8") as f:
            snapshot = f.read().strip() or None
    ready, blockers, warnings = tp.dor_check(c, ws, snapshot)
    tp.trace(ws, "dor", ready=ready, blockers=blockers, warnings=warnings)
    _print_dor(ready, blockers, warnings)
    if not ready:
        print("\nFix the ✗ blockers before starting — the task isn't safely "
              "governable yet.")
    return 0 if ready else 1


# --------------------------------------------------------------- screen

class MeterCorrupt(Exception):
    """The meter file exists but is unreadable — the budget count can't be
    trusted, so the caller must fail CLOSED rather than reset it to zero."""


def _meter_load(ws, strict=False) -> dict:
    """Load the action meter. Default (strict=False) tolerates a missing OR
    corrupt file by returning {} — fine for display/estimates. strict=True
    raises MeterCorrupt when the file EXISTS but won't parse, so the budget
    gate fails CLOSED instead of silently reading the count as zero (which
    would lift an exhausted wall — the meter is control-plane too)."""
    p = os.path.join(tp.tp_dir(ws), "meter.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            if strict:
                raise MeterCorrupt(p)
    return {}


def _meter_bump(ws, task_id, key) -> dict:
    import time
    now = time.time()
    path = os.path.join(tp.tp_dir(ws), "meter.json")
    # The meter is control-plane (the enforced max_actions ceiling counts
    # through it), so the read-modify-write is serialized under the shared
    # file_lock — two concurrent screens must never both read N and both
    # write N+1 (that undercount silently raises the budget wall). file_lock
    # never degrades to lock-free; if it can't be acquired it raises
    # StateError and the screen boundary fails CLOSED (blocks).
    with tp.file_lock(path):
        try:
            m = _meter_load(ws, strict=True)
        except MeterCorrupt:
            m = {}                  # bumping rebuilds a clean file atomically
        e = m.setdefault(task_id, {"actions": 0, "denies": 0})
        e[key] = e.get(key, 0) + 1
        # last_seen_ts = the owner was alive AT ALL (any screen call, approve
        # or deny) — used by the orphan idle-backstop to tell a crashed owner
        # (no calls) from a live one. last_action_ts = last APPROVED action.
        e["last_seen_ts"] = now
        if key == "actions":
            e["last_action_ts"] = now
        # Atomic write so a concurrent reader never sees a torn file.
        tp.atomic_write_json(path, m, indent=None)
    return e


# Per-process memo of `git rev-parse --show-toplevel` per cwd — see the
# comment inside _governed_root. Never persisted.
_GIT_TOP_CACHE: dict = {}


def _governed_root(cwd: str) -> str:
    """Resolve the workspace whose contract governs `cwd`, walking UP to the
    nearest ancestor that holds an active contract. Contract discovery used to
    be exact-cwd only, so a governed agent that merely `cd`'d into a
    subdirectory escaped its own contract (the subdir has no
    .taskplane/active_contract.json → ungoverned → ABSTAIN).

    The walk stops at: the filesystem root, $HOME, AND the git worktree/repo
    boundary of `cwd`. The worktree stop matters for parallel workers: a
    wave worker lives in its own `.tp-work/<id>` git worktree nested under
    the parent project; without the boundary, when that worker has no active
    contract (cleared/released), the walk would climb OUT of the worktree and
    screen its actions against the PARENT project's contract — a scope never
    written for it (false denies, or false approvals under a broader parent
    write_allow). A distinct worktree with no contract of its own must ABSTAIN,
    not inherit a sibling/parent contract. If no ancestor within the boundary
    is governed, returns the original cwd unchanged (ungoverned stays so)."""
    start = _workspace(cwd)
    home = os.path.realpath(os.path.expanduser("~"))
    # git worktree/repo top of cwd — the walk must not climb past it. This
    # shells out to git on EVERY PreToolUse screen event: an accepted
    # per-action latency cost of the no-server design (fine on a laptop,
    # noticeable on slow/networked filesystems). Memoized per cwd WITHIN
    # this process only — a hook invocation is one short-lived process, so
    # the cache can never go stale across invocations (no cross-invocation
    # caching by design: a repo can be re-rooted between events).
    if start in _GIT_TOP_CACHE:
        top = _GIT_TOP_CACHE[start]
    else:
        top = tp._run(["git", "rev-parse", "--show-toplevel"],
                      cwd=start).stdout.strip()
        # macOS commonly reports the same temp path as /var/... to Python and
        # /private/var/... to git. Compare real paths or the boundary check
        # misses the worktree root and can inherit a parent contract.
        top = os.path.realpath(top) if top else None
        _GIT_TOP_CACHE[start] = top
    cur = start
    while True:
        if os.path.exists(os.path.join(tp.tp_dir(cur), "active_contract.json")):
            return cur
        parent = os.path.dirname(cur)
        real_cur = os.path.realpath(cur)
        if parent == cur or real_cur == home or real_cur == top:
            return start
        cur = parent


def _screen(a) -> int:
    """The screening body — wrapped by cmd_screen so ANY unexpected error
    fails CLOSED (blocks) instead of emitting no decision."""
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    ws = _governed_root(event.get("cwd"))
    tool_name = event.get("tool_name", event.get("tool", ""))
    tool_input = event.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    contract = tp.load_active(ws)
    if contract is None:
        # Distinguish "no contract at all" (ungoverned → ABSTAIN) from
        # "contract file present but unreadable/corrupt" (tamper or breakage
        # → fail CLOSED). A governed workspace whose control plane is
        # damaged must not silently become ungoverned.
        cpath = os.path.join(tp.tp_dir(ws), "active_contract.json")
        if os.path.exists(cpath):
            print(json.dumps({
                "decision": "block",
                "reason": "taskplane: the active contract is present but "
                          "unreadable (corrupt or tampered). Failing closed. "
                          "Ask the human / the ungoverned main session to "
                          "run `tp.py clear --workspace <this workspace>` "
                          "and re-activate a contract.",
            }))
            return 0
        # Ungoverned: ABSTAIN — emit no decision so Claude Code's normal
        # permission flow applies. Forcing {"decision":"approve"} here would
        # auto-approve every Write/Edit/Bash in ANY ungoverned repo where
        # taskplane is installed, silently bypassing the user's own
        # permission prompts. Governance vouches for in-scope actions; the
        # ABSENCE of a contract must defer, never rubber-stamp.
        return 0

    # ORPHANED-CONTRACT AUTO-RELEASE — a contract whose owner is gone (dead
    # recorded PID, or no approved activity past the TTL) must not keep
    # governing the workspace: that is exactly how a leaked review contract
    # locked an entire session. Auto-clear it, note the release, and ABSTAIN
    # (the workspace is now ungoverned → defer to normal permissions, same as
    # the no-contract path above).
    orphaned, why = tp.orphan_status(ws, contract)
    if orphaned:
        tp.clear(ws)
        tp.trace(ws, "contract_orphan_released",
                 task_id=contract.get("task_id"), reason=why)
        return 0

    tid = contract.get("task_id", "_")

    # budget gate first — an exhausted harness does no further work. The RULE
    # lives in the kernel (tp.budget_status); the CLI only meters + forwards.
    # FAIL CLOSED on a corrupt meter for a governed contract: a torn/tampered
    # meter.json must not silently read as 0 used and lift an exhausted wall
    # (the meter is control-plane, like the contract file).
    try:
        used = _meter_load(ws, strict=True).get(tid, {}).get("actions", 0)
    except MeterCorrupt:
        tp.trace(ws, "meter_corrupt_block", tool=tool_name)
        print(json.dumps({
            "decision": "block",
            "reason": f"taskplane contract {tid}: the action meter is "
                      "unreadable (corrupt or tampered) — failing closed so "
                      "an exhausted budget can't silently reset. Ask the "
                      "human to `tp.py clear --workspace <ws>` (from outside "
                      "the workspace) and re-activate.",
        }))
        return 0
    ok, reason = tp.budget_status(contract, used)
    if not ok:
        _meter_bump(ws, tid, "denies")
        tp.trace(ws, "budget_deny", tool=tool_name, used=used,
                 max=(contract.get("budget") or {}).get("max_actions"))
        print(json.dumps({
            "decision": "block",
            "reason": f"taskplane contract {tid}: {reason}",
        }))
        return 0

    allow, reason = tp.screen_tool(contract, tool_name, tool_input, ws)
    if allow:
        _meter_bump(ws, tid, "actions")
        # Codex does not support the legacy PreToolUse
        # {"decision":"approve"} shape. A successful hook with no output
        # means continue while preserving Codex's normal sandbox/approval
        # policy. Claude events do not carry Codex's turn_id extension, so
        # retain the existing approval response there for backwards
        # compatibility.
        if "turn_id" not in event:
            print(json.dumps({"decision": "approve"}))
        return 0
    _meter_bump(ws, tid, "denies")
    tp.trace(ws, "hook_deny", tool=tool_name, reason=reason)
    print(json.dumps({
        "decision": "block",
        "reason": f"taskplane contract {contract.get('task_id','')}: {reason}. "
                  "This action is outside the task's contract. Adjust scope "
                  "with `tp.py` or choose an in-scope path.",
    }))
    return 0


def cmd_screen(a) -> int:
    """PreToolUse hook entrypoint. Reads the event JSON from stdin.
    Enforces the harness both ways: ON TOPIC (scope/tools/commands) and
    WITHIN BUDGET (max_actions — every governed tool call is metered and
    the ceiling blocks BEFORE the action runs). FAILS CLOSED: any
    unexpected error emits a block, never a silent no-decision."""
    try:
        return _screen(a)
    except Exception as exc:  # noqa: BLE001 — the boundary must never leak
        print(json.dumps({
            "decision": "block",
            "reason": f"taskplane screener error ({type(exc).__name__}) — "
                      "failing closed. This action is blocked until the "
                      "contract/event can be screened cleanly.",
        }))
        return 0


# --------------------------------------------------------------- status

def cmd_status(a) -> int:
    ws = _workspace(a.workspace)
    c = tp.load_active(ws)
    if c is None:
        # A corrupt legacy contract file makes load_active return None, but the
        # enforcement hook BLOCKS on the same file — so "no active contract"
        # here would tell the human they're ungoverned when they are actually
        # governed-but-broken. Surface the corruption, fail closed (v2.3.1).
        legacy = tp.active_contract_path(ws, None)
        if os.path.exists(legacy):
            try:
                with open(legacy, encoding="utf-8") as f:
                    json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(json.dumps({
                    "active_contract": "CORRUPT",
                    "path": legacy,
                    "error": str(e),
                    "enforcement": "the PreToolUse hook BLOCKS all actions "
                                   "while this file is unreadable — the "
                                   "workspace is governed-but-broken, not "
                                   "ungoverned",
                    "remedy": "inspect/restore the file (git checkout), or "
                              "`tp.py clear` from an ungoverned context to "
                              "reset it",
                }, indent=2))
                return 1
        print("taskplane: no active contract in this workspace "
              "(run `tp.py new …`).")
        return 0
    coding = c.get("coding") or {}
    budget = c.get("budget") or {}
    print(json.dumps({
        "task_id": c.get("task_id"), "task": c.get("task"),
        "read_only": bool(c.get("read_only")),
        "write_allow": c.get("write_allow") or [],
        "scope_paths": coding.get("scope_paths") or [],
        "out_of_scope_paths": coding.get("out_of_scope_paths") or [],
        "deny": (coding.get("command_policy") or {}).get("deny") or [],
        "allowed_tools": c.get("allowed_tools") or "(any)",
        "max_actions": budget.get("max_actions"),
        "budget_ceiling_usd": budget.get("max_cost_usd", "(action-metered; "
                                          "no dollar ceiling set)"),
        "budget_note": budget.get("note"),
        "dod": coding.get("dod") or {},
    }, indent=2))
    return 0


def cmd_budget(a) -> int:
    ws = _workspace(a.workspace)
    c = tp.load_active(ws)
    if c is None:
        print("taskplane: no active contract.", file=sys.stderr)
        return 1
    if getattr(a, "grant", None):
        # The approval half of the budget gate: exhaustion blocks and asks
        # the human; this records the human's YES. Meant for the HUMAN /
        # the ungoverned main session — a governed agent's own `tp budget
        # --grant` is still screened (and budget-blocked) like any other
        # command; the wall is intentional.
        if a.grant < 1:
            print("taskplane: --grant must be a positive action count.",
                  file=sys.stderr)
            return 1
        updated = tp.grant_budget(ws, a.grant)
        if updated is None:
            print("taskplane: this contract has no action ceiling to raise.",
                  file=sys.stderr)
            return 1
        new_max = updated["budget"]["max_actions"]
        used = _meter_load(ws).get(updated.get("task_id", "_"), {}) \
            .get("actions", 0)
        print(f"taskplane: budget granted — +{a.grant} actions, ceiling now "
              f"{new_max} ({used} used). Work may continue.")
        return 0
    if a.spent is None:
        print("taskplane: pass --spent USD (cooperative estimate) or "
              "--grant N (raise the action ceiling).", file=sys.stderr)
        return 1
    ceiling = (c.get("budget") or {}).get("max_cost_usd")
    if ceiling is None:
        print("taskplane: this contract has no dollar ceiling — it's metered "
              "by action budget only (see `tp.py status`). The cooperative "
              "dollar estimate applies only to contracts created with "
              "`tp.py new --budget`.")
        return 0
    tp.trace(ws, "budget_estimate", spent_usd=a.spent, ceiling_usd=ceiling)
    over = a.spent > ceiling
    print(f"taskplane: cooperative budget — est ${a.spent:.2f} / "
          f"${ceiling:.2f} ceiling{'  ⚠ OVER' if over else ''}")
    print("  (advisory: Cowork does not intercept model spend; treat the "
          "ceiling as a stop signal)")
    return 2 if over else 0


# --------------------------------------------------------------- dod

def cmd_dod(a) -> int:
    ws = _workspace(a.workspace)
    c = tp.load_active(ws)
    if c is None:
        print("taskplane: no active contract — nothing to validate.",
              file=sys.stderr)
        return 1
    snap_path = os.path.join(tp.tp_dir(ws), "snapshot")
    snapshot = None
    if os.path.exists(snap_path):
        with open(snap_path, encoding="utf-8") as f:
            snapshot = f.read().strip() or None

    errors = tp.dod_check(c, ws, snapshot)
    import kb as kbmod
    errors += [f"{p['file']}: {p['problem']}" for p in kbmod.lint(ws)]
    tp.trace(ws, "dod", passed=not errors, errors=errors)
    if errors:
        print("taskplane DoD: FAIL ❌")
        for e in errors:
            print("  - " + e)
        return 1
    changed = tp.changed_files(ws, snapshot) if snapshot else []
    print("taskplane DoD: PASS ✅ (diff in scope"
          + (", tests pass" if c["coding"]["dod"].get("test_command") else "")
          + ")")
    if changed:
        print("  files changed (in scope): " + ", ".join(changed[:12]))
    return 0


# --------------------------------------------------------------- loop

def cmd_loop(a) -> int:
    """Drive the taskplane-owned Evaluate-Loop state machine."""
    import loop as loopmod
    ws = _workspace(a.workspace)
    action = a.loop_action
    out = None
    if action == "init":
        checkpoints = (a.checkpoints.split(",") if a.checkpoints is not None
                       else ["plan", "em"])
        st = loopmod.init(ws, " ".join(a.goal or []) or (a.spec or "spec"),
                          spec_path=a.spec, max_fix_cycles=a.max_fix_cycles,
                          checkpoints=[c for c in checkpoints if c],
                          requirement_id=a.req, parallel=a.parallel,
                          design=a.design, design_only=a.design_only,
                          force=getattr(a, "force", False))
        # Only collapse to the success summary when the engine did NOT refuse.
        # Previously any dict with a "step" key (including a refusal that also
        # carries the CURRENT step) was reported as {"initialized": true} with
        # exit 0, silently swallowing an in-flight-loop refusal or the
        # --force archive note (v2.3.1). Surface errors/notes verbatim and
        # exit non-zero on error.
        if isinstance(st, dict) and st.get("error"):
            out = st
        elif isinstance(st, dict) and "step" in st:
            out = {"initialized": True, "step": st["step"]}
            for k in ("note", "archived", "warning"):
                if st.get(k):
                    out[k] = st[k]
        else:
            out = st
    elif action == "next":
        out = loopmod.next_action(ws, rid=getattr(a, "req", None))
    elif action == "submit":
        out = loopmod.submit(ws, a.outcome, note=a.note or "", task_id=a.task)
    elif action == "gate":
        out = loopmod.gate(ws, a.outcome, note=a.note or "", task_id=a.task,
                           rid=getattr(a, "req", None))
        # Tier-routing observability at the gate summary, ON BY DEFAULT: the
        # cheap/deep routing the briefs resolve is only real if dispatch used
        # it, so every gate shows expected-vs-observed models. Pure audit —
        # no new enforcement, never changes the gate outcome or exit code.
        if isinstance(out, dict):
            try:
                rep = tp.dispatch_report(ws)
                out.setdefault("dispatch_audit", {
                    "expected": rep["expected"],
                    "observed": rep["observed"],
                    "mismatches": rep["mismatches"],
                    "unobserved": rep["unobserved"],
                    "hook_active": rep["hook_active"],
                    "note": rep["note"]})
            except Exception:
                pass                 # audit must never break the gate
    elif action == "wave":
        out = loopmod.wave(ws)
    elif action == "claim":
        out = loopmod.claim(ws, a.task_id, a.agent_workspace)
    elif action == "approve":
        out = loopmod.approve(ws, force=a.force, by=getattr(a, "by", None))
    elif action == "select":
        out = loopmod.select(ws, a.choice, note=a.note or "")
    elif action == "resolve":
        out = loopmod.resolve(ws, a.decision)
    elif action == "evidence":
        out = loopmod.evidence(ws, task_id=getattr(a, "task", None),
                               write=getattr(a, "write", False))
    elif action == "status":
        out = loopmod.status(ws)
    elif action == "retro":
        out = loopmod.retro(ws)
        if isinstance(out, dict) and not out.get("error"):
            try:
                rep = tp.dispatch_report(ws)
                if not rep["hook_active"]:
                    out.setdefault(
                        "tier_routing",
                        "UNOBSERVED — no dispatches were verified against "
                        "the resolved model tiers; the cheap-tier cost "
                        "saving is unproven for this run. " + (rep["note"]
                                                               or ""))
            except Exception:
                pass
    elif action == "verify-dispatch":
        rep = tp.dispatch_report(ws)
        print(json.dumps(rep, indent=2))
        return 1 if rep["mismatches"] else 0
    # R-0004 stage emitter (contract:wave-workflow): `loop wave` and
    # `loop next` are the STAGE dispatch surfaces — when the payload is a
    # stage dispatch (execute wave / evaluate / fix) and the workflow path
    # is chosen, wrap it as ONE ready-to-run stage workflow invocation.
    # On the task path _emit_stage returns None and the print below stays
    # BYTE-IDENTICAL to the pre-workflow payload (the MANDATORY fallback
    # and the only Codex path — R-0004's core promise).
    if action in ("wave", "next"):
        wrapped = _emit_stage(ws, out, getattr(a, "emit", None) or "auto")
        if wrapped is _STAGE_REFUSED:
            # C3/E5: the emission was refused (reason already traced and
            # printed to stderr) — nonzero exit, NO payload on stdout, so a
            # scripted driver can never dispatch what cannot run.
            return 1
        _record_parallel_expectations(ws, out)
        if wrapped is not None:
            print(json.dumps(wrapped, indent=2))
            return 0
    print(json.dumps(out, indent=2))
    # An engine refusal ({"error": ...}) is a FAILURE: exit nonzero so a
    # scripted driver (`&&` chain, CI wrapper, Tag thread) can never mistake
    # a refused gate/submit/wave for success. Matches the convention the
    # other subcommands (req score, kb lint, share push) already follow.
    return 1 if isinstance(out, dict) and out.get("error") else 0


def workflow_available(ws) -> dict:
    """Can this host run plugin Dynamic Workflows? CONSERVATIVE, env-based.

    The workflow path is an optimization, never a dependency (R-W2: the
    Task-dispatch fallback is MANDATORY and byte-identical). Precedence:
      1. Codex (CODEX_HOME/CODEX_THREAD_ID) — ALWAYS unavailable: Codex has
         no workflow runtime (verified, docs/v3-strategy addendum), and no
         opt-in may override that.
      2. TASKPLANE_WORKFLOWS=0 — operator/org kill-switch (orgs can disable
         workflows entirely; nothing may depend on them exclusively).
      3. TASKPLANE_WORKFLOWS=1 — explicit opt-in.
      4. CLAUDE_CODE_WORKFLOWS (truthy) — a detectable Claude Code workflow
         runtime marker.
      5. Default: UNAVAILABLE — when in doubt, take the path that is proven
         byte-identical to today's behavior.

    `definitive` (C3, R-0009) marks an unavailability that is a KNOWN host
    fact — Codex has no workflow runtime (verified), the kill-switch is an
    explicit operator decision — as opposed to the conservative default,
    where a runtime is merely undetected. An explicit `--emit workflow`
    override REFUSES on a definitive negative (the payload could never
    run there) but is still honored on the undetected default (the human
    may know the runtime better than the detector).
    """
    del ws  # detection is host-level, not workspace-level (reserved)
    env = os.environ
    if env.get("CODEX_HOME") or env.get("CODEX_THREAD_ID"):
        return {"available": False, "definitive": True,
                "reason": "codex host (CODEX_HOME/CODEX_THREAD_ID): "
                          "no workflow runtime"}
    toggle = (env.get("TASKPLANE_WORKFLOWS") or "").strip().lower()
    # EM v3: '0'-only matching silently ignored 'false'/'no'/'off' — an
    # operator writing any conventional falsey spelling MUST get the
    # kill-switch (fail toward disabled, the conservative side).
    if toggle in ("0", "false", "no", "off"):
        return {"available": False, "definitive": True,
                "reason": "disabled by TASKPLANE_WORKFLOWS="
                          + (env.get("TASKPLANE_WORKFLOWS") or "0")}
    if toggle in ("1", "true", "yes", "on"):
        return {"available": True,
                "reason": "explicit TASKPLANE_WORKFLOWS opt-in"}
    marker = (env.get("CLAUDE_CODE_WORKFLOWS") or "").strip().lower()
    if marker and marker not in ("0", "false", "no", "off"):
        return {"available": True,
                "reason": "claude workflow runtime marker "
                          "(CLAUDE_CODE_WORKFLOWS)"}
    return {"available": False, "definitive": False,
            "reason": "no workflow runtime detected (conservative default "
                      "— set TASKPLANE_WORKFLOWS=1 to opt in)"}


def _emit_workflow_refusal(avail: dict) -> "str | None":
    """C3 (R-0009): the refusal reason for an explicit `--emit workflow`
    on a DEFINITIVELY workflow-less host, or None when the override may
    proceed. Refuse-with-reason replaces force-printing an uninvokable
    payload (product decision recorded at the pm step): the reason names
    the host state (the detector's own words) and points at the Task
    path — the byte-identical mandatory fallback and the only path on
    Codex. A merely-undetected runtime (the conservative default) keeps
    the explicit override: the human may know the host better than the
    detector, and the dispatch-parity pins prove the payload is the same
    either way."""
    if avail["available"] or not avail.get("definitive"):
        return None
    return ("--emit workflow refused: " + avail["reason"]
            + ". This host cannot run plugin workflows — use the Task "
              "path (--emit task, or the default auto): byte-identical "
              "briefs, the only path on Codex.")


# --------------------------------------------------- stage emitter (R-0004)
#
# contract:wave-workflow — the governed stages between human gates compile
# to at most ONE journaled workflow run per stage: the emitter wraps the
# UNMODIFIED stage dispatch payload (what `loop wave`/`loop next` print on
# the Task path today) as a single workflow{name, args} invocation of the
# matching stage workflow file (workflows/execute-wave.js / evaluate-wave.js
# / fix-wave.js). One emission = one run covering EVERY brief the stage
# payload dispatches; human gates stay at conversation level by construction
# (the run contains agents, never an approval step). The emitter lives in
# tp.py — loop.py and lens.py stay workflow-agnostic (pinned) — and calls
# workflow_available() DIRECTLY: the single detector; nothing here may
# parse the workflow environment itself.

STAGE_WAVE_NAMES = {"execute": "execute-wave", "evaluate": "evaluate-wave",
                    "fix": "fix-wave"}


def _record_parallel_expectations(ws: str, payload: dict) -> None:
    """Register native identities once a parallel wave is actually emitted."""
    if not isinstance(payload, dict) or payload.get("step") != "execute" \
            or not payload.get("parallel"):
        return
    for entry in payload.get("wave") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("task"), dict):
            continue
        task_id = entry["task"].get("id")
        if not task_id:
            continue
        tp.record_expected_dispatch(
            ws, "step", entry.get("role", "tp-executor"),
            entry.get("model_tier", "standard"), entry.get("model"),
            ref=task_id, task_name=entry.get("task_name"),
            reasoning_effort=entry.get("reasoning_effort"),
            role_marker_value=entry.get("role_marker"))


def _stage_activation(slot: str) -> str:
    """C1 (R-0009): the per-task contract-slot activation block, in BOTH
    host forms for the SAME slot.

    `export TASKPLANE_TASK=<slot>` is POSIX-only. An agent on cmd.exe
    cannot run it, silently never activates its slot, and lands in the
    slot-less fallback — the most-restrictive UNION of every active
    per-task contract (taskplane_lite.load_active / _union_contract),
    where its OWN in-scope work is refused because a sibling's contract
    does not allow it. That is fail-CLOSED, never an escape, but it is
    also unworkable, so the brief carries a labelled cmd alternative —
    the same both-forms pattern hooks/hooks.json already ships as
    `command` + `commandWindows`.

    The POSIX line stays FIRST and unchanged in wording (the dominant
    host reads it as before, and the Task-path byte pins that follow it
    are untouched); the cmd line is an ADDITION, never a replacement.
    Both forms take the slot the emitter already validated against the
    ONE enforced charset (_valid_slot_id), so neither can carry a value
    the screener would refuse. ASCII only: this block is read, and
    retyped, in consoles whose default code page is not UTF-8."""
    return (f"export TASKPLANE_TASK={slot}\n\n"
            "Windows (cmd.exe): the POSIX line above will not run there - "
            "use this equivalent activation for the SAME slot instead:\n"
            f"set TASKPLANE_TASK={slot}\n")


def _stage_agent_prompt(slot: str, instruction: str, entry: dict) -> str:
    """The prompt ONE governed stage agent receives on the workflow rail.

    Composed ONLY of Task-path bytes, never rewritten: the per-task
    contract-slot activation (`export TASKPLANE_TASK=<slot>` plus its
    labelled cmd.exe alternative — the same slot protocol every
    dispatched worker uses, see _stage_activation), the stage payload's
    own `instruction` VERBATIM (it carries the claim/submit-not-advance
    protocol: workers submit evidence, only the orchestrator gates), and
    the task-path payload entry VERBATIM (json.dumps, indent=2 — the same
    serialization the Task path prints on stdout)."""
    return (_stage_activation(slot) + f"\n{instruction}\n\n"
            + json.dumps(entry, indent=2))


def _valid_slot_id(tid) -> bool:
    """E5 (R-0011): a task id the emitter embeds into an
    `export TASKPLANE_TASK=<id>` prompt line must already BE a valid
    contract slot — validated against the ONE enforced slot charset
    (taskplane_lite._TASK_SLOT_RE), never a second regex that could
    drift from the screener's."""
    return bool(isinstance(tid, str) and tp._TASK_SLOT_RE.match(tid))


def _entry_problem(entry, i: int) -> "dict | None":
    """Validate ONE wave entry before any brief is composed. Returns None
    (well-formed), an A6 degrade marker {path: 'task', reason} for a
    malformed entry (missing/ill-shaped task or id — fail OPEN: the Task
    path can always print what the loop printed), or an E5 refusal
    {path: 'refused', reason} for an id outside the slot charset (fail
    CLOSED on the WORKFLOW rail only — that rail composes an
    `export TASKPLANE_TASK=<id>` line, so the refusal lands before any
    such line exists. `_emit_stage` scopes it: the Task path composes no
    shell line and degrades instead — see its RAIL ORDER note)."""
    task = entry.get("task") if isinstance(entry, dict) else None
    tid = task.get("id") if isinstance(task, dict) else None
    if not tid or not isinstance(tid, str):
        return {"path": "task",
                "reason": "malformed wave entry — fail-open to task path: "
                          f"entry {i} has no task.id"}
    if not _valid_slot_id(tid):
        return {"path": "refused",
                "reason": f"invalid task id {tid!r} (entry {i}): a stage "
                          "brief embeds `export TASKPLANE_TASK=<id>`, so "
                          "the id must match the enforced slot charset "
                          + tp._TASK_SLOT_RE.pattern
                          + " — emission refused, nothing composed"}
    return None


def _stage_wave_run(payload) -> "tuple[str, dict | None, dict | None] | None":
    """Map a `loop wave`/`loop next` payload to its stage workflow run —
    (stage, workflow{name, args}, None) — or None when the payload is not
    a stage dispatch (error, human pause, empty wave, non-stage step), or
    (stage, None, problem) when a stage payload cannot be composed:
    problem = {path: 'task', reason} (A6 — malformed entry, degrade to
    the Task path) | {path: 'refused', reason} (E5 — id outside the slot
    charset, refuse emission). Every entry is validated BEFORE any prompt
    line is composed.

    args carries the SAME brief set the Task path prints
    (contract:wave-workflow): execute/evaluate workflows consume
    args.briefs, fix consumes args.verdicts; each brief is
    {id, worktree, prompt} with the prompt built from Task-path bytes
    verbatim (_stage_agent_prompt). A parallel EXECUTE wave compiles to a
    single run whose briefs cover EVERY wave entry."""
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    step = payload.get("step")
    instruction = payload.get("instruction") or ""
    if step == "execute" and payload.get("parallel") and "wave" in payload:
        entries = payload.get("wave") or []
        if not entries:
            return None                      # nothing to dispatch this wave
        for i, e in enumerate(entries):      # validate ALL before composing
            problem = _entry_problem(e, i)
            if problem is not None:
                return "execute", None, problem
        briefs = [{"id": e["task"]["id"], "worktree": e.get("worktree"),
                   "prompt": _stage_agent_prompt(e["task"]["id"],
                                                 instruction, e)}
                  for e in entries]
        return ("execute", {"name": "execute-wave",
                            "args": {"briefs": briefs}}, None)
    if step in STAGE_WAVE_NAMES and "task" in payload:
        # same validation on the single-task step shapes (A6/E5): a
        # payload CARRYING a task that is malformed degrades to the Task
        # path traced (never a silent skip), an un-slottable id refuses.
        problem = _entry_problem(payload, 0)
        if problem is not None:
            return step, None, problem
        tid = payload["task"]["id"]
        _wt = payload["task"].get("workspace")
        # Dispatch briefs are CROSS-HOST artifacts — the parity goldens
        # compare them byte for byte between Claude and Codex — so a host
        # separator must never reach one.
        brief = {"id": tid,
                 "worktree": tp.to_posix(_wt) if _wt else _wt,
                 "prompt": _stage_agent_prompt(tid, instruction, payload)}
        key = "verdicts" if step == "fix" else "briefs"
        return (step, {"name": STAGE_WAVE_NAMES[step],
                       "args": {key: [brief]}}, None)
    return None


# Sentinel: the emission was REFUSED (reason already on stderr + trace);
# the caller must exit nonzero and print NO payload.
_STAGE_REFUSED = object()


def _emit_stage(ws, payload, emit: str):
    """R-0004 stage emitter. Returns the workflow-path payload to print,
    None → the caller prints the untouched Task-path payload (stdout stays
    BYTE-IDENTICAL to the pre-workflow bytes: the MANDATORY fallback and
    the only Codex path), or _STAGE_REFUSED → the caller exits nonzero
    (reason already traced + printed to stderr; nothing on stdout). The
    chosen path is traced as stage_dispatch_path {stage, path, reason} on
    ALL rails, printed on neither task stdout. Detection is delegated to
    workflow_available() — single detector, no second env parse here.

    RAIL ORDER (Phase 3 fix): the E5 slot-charset refusal is a property of
    the WORKFLOW rail only — that rail interpolates the id into a composed
    `export TASKPLANE_TASK=<id>` line. The Task path composes no shell line
    (it prints the engine's own payload verbatim), so it is NEVER refused
    for a slot-charset reason: refusing it denied the MANDATORY fallback —
    on Codex, the only rail there is — and dead-ended an already-approved
    plan. Bad ids are caught EARLY instead, at the plan gate
    (taskplane_lite.plan_task_id_errors, where the remedy is a free edit to
    plan/tasks.json), and late at `claim` (task_slot's StateError)."""
    run = _stage_wave_run(payload)
    if run is None:
        return None
    stage, workflow, problem = run
    if problem is not None and problem["path"] != "refused":
        # A6: malformed entry → degrade to the Task path (fail open).
        tp.trace(ws, "stage_dispatch_path", stage=stage,
                 path=problem["path"], reason=problem["reason"], emit=emit)
        return None
    avail = workflow_available(ws)
    if emit == "workflow":
        refusal = _emit_workflow_refusal(avail)
        if refusal is not None:
            # C3: explicit --emit workflow on a definitively workflow-less
            # host (Codex, kill-switch) — refuse with the named remedy
            # instead of force-printing an uninvokable payload.
            tp.trace(ws, "stage_dispatch_path", stage=stage, path="refused",
                     reason=refusal, emit=emit)
            print("taskplane: " + refusal, file=sys.stderr)
            return _STAGE_REFUSED
        path = "workflow"
        reason = "explicit --emit workflow" + (
            "" if avail["available"] else f" (forced: {avail['reason']})")
    elif emit == "task":
        path, reason = "task", "explicit --emit task"
    else:
        path = "workflow" if avail["available"] else "task"
        reason = avail["reason"]
    if problem is not None:
        # E5, rail-scoped: refuse the WORKFLOW emission (nothing composed);
        # on the Task rail degrade instead — the id is never interpolated
        # there, and the mandatory fallback must stay reachable.
        if path == "workflow":
            tp.trace(ws, "stage_dispatch_path", stage=stage, path="refused",
                     reason=problem["reason"], emit=emit)
            print("taskplane: " + problem["reason"], file=sys.stderr)
            return _STAGE_REFUSED
        tp.trace(ws, "stage_dispatch_path", stage=stage, path="task",
                 reason=problem["reason"] + " — Task path unaffected (no "
                 "shell line is composed there); fix the id in "
                 "plan/tasks.json before the next plan gate", emit=emit)
        return None
    tp.trace(ws, "stage_dispatch_path", stage=stage, path=path,
             reason=reason, emit=emit)
    if path != "workflow":
        return None
    out = dict(payload)
    out["dispatch_path"] = "workflow"
    out["reason"] = reason
    # At most ONE workflow run per stage between human gates: a single
    # workflow object whose args carry every brief this stage dispatches.
    out["workflow"] = workflow
    return out


def cmd_lens(a) -> int:
    """Route / list / show / dispatch lenses."""
    import lens as lensmod
    ws = _workspace(a.workspace)
    action = getattr(a, "lens_action", "route")

    if action == "list":
        cat = lensmod.catalog_summary()
        if getattr(a, "json", False):
            print(json.dumps(cat, indent=2))
        else:
            print(f"{len(cat)} lenses:")
            for l in cat:
                print(f"  {l['id']:<20} {l['name']:<28} {l['looks_for'][:60]}")
        return 0

    if action == "show":
        b = lensmod.lens_brief(a.id)
        if b is None:
            print(f"taskplane: no lens '{a.id}'", file=sys.stderr)
            return 1
        print(json.dumps(b, indent=2))
        return 0

    breadth = "all" if getattr(a, "breadth_all", False) else "routed"
    if getattr(a, "artifact_type", None):
        routing = lensmod.route([], artifact_type=a.artifact_type,
                                only=(a.only.split(",") if a.only else None),
                                skip=(a.skip.split(",") if a.skip else None),
                                breadth=breadth)
    else:
        routing = lensmod.route_git_diff(ws, base=a.base, task_type=a.task_type,
                                         only=(a.only.split(",") if a.only else None),
                                         skip=(a.skip.split(",") if a.skip else None),
                                         breadth=breadth)

    if action == "dispatch":
        # C3 (R-0009): an explicit --emit workflow on a definitively
        # workflow-less host (Codex, operator kill-switch) is refused UP
        # FRONT — before briefs are composed or expected dispatches are
        # recorded — so a refusal leaves no verify-dispatch expectations
        # behind. The merely-undetected default keeps the override
        # (dispatch-parity pins prove the payload is identical either way).
        if (getattr(a, "emit", "auto") or "auto") == "workflow" \
                and not getattr(a, "dashboard", False):
            refusal = _emit_workflow_refusal(workflow_available(ws))
            if refusal is not None:
                tp.trace(ws, "review_dispatch_path", path="refused",
                         reason=refusal, emit="workflow")
                print("taskplane: " + refusal, file=sys.stderr)
                return 1
        impact_ctx = None
        try:
            import depgraph as dg
            _files = (routing.get("context") or {}).get("files") or []
            if _files and dg.load(ws).get("modules"):
                _imp = dg.impact(ws, _files)
                if _imp["touched"]:
                    impact_ctx = dg.render_context(_imp)
        except Exception:
            impact_ctx = None
        briefs = lensmod.dispatch_briefs(routing, base=a.base,
                                         max_actions=a.max_actions,
                                         impact_context=impact_ctx)
        # --dashboard is a PURE VIEW that the driver re-runs as agents land;
        # recording expectations there would append a fresh unmatched set on
        # every re-render and turn `loop verify-dispatch` into noise. Only a
        # real dispatch (JSON briefs) records what SHOULD be dispatched.
        if not getattr(a, "dashboard", False):
            for b in briefs.get("deep") or []:
                tp.record_expected_dispatch(ws, "lens",
                                            b.get("agent", "tp-lens"),
                                            b.get("model_tier", "standard"),
                                            b.get("model"), ref=b.get("id"),
                                            task_name=b.get("task_name"),
                                            reasoning_effort=b.get(
                                                "reasoning_effort"))
            sw = briefs.get("sweep")
            if sw:
                tp.record_expected_dispatch(ws, "lens",
                                            sw.get("agent", "tp-lens"),
                                            sw.get("model_tier", "cheap"),
                                            sw.get("model"), ref="sweep",
                                            task_name=sw.get("task_name"),
                                            reasoning_effort=sw.get(
                                                "reasoning_effort"))
        if getattr(a, "dashboard", False):
            import dashboard

            def _lane(lid, name):
                # R1 (v2.2.1): status derives from the lens's findings file —
                # deterministic, zero-token. Re-run `lens dispatch
                # --dashboard` after agents land and the wave shows DONE
                # lanes with counts, so the human watches the fan-out
                # instead of trusting the driver to narrate it.
                p = os.path.join(ws, ".em-review", f"lens-{lid}",
                                 "findings.json")
                if os.path.isfile(p):
                    try:
                        with open(p, encoding="utf-8") as f:
                            n = len(json.load(f).get("findings") or [])
                    except (OSError, ValueError):
                        n = None
                    return {"id": lid, "name": name, "status": "done",
                            "findings": n}
                return {"id": lid, "name": name, "status": "running",
                        "findings": None}

            lanes = [_lane(b["id"], b["name"]) for b in briefs["deep"]]
            if briefs["sweep"]:
                lanes.append(_lane("sweep", "sweep"))
            done = sum(1 for x in lanes if x["status"] == "done")
            print(dashboard.render_lens_wave(
                lanes, {"title": ("review — wave complete"
                                  if done == len(lanes) else
                                  "review — lenses running"),
                        "subtitle": f"{done}/{len(lanes)} lens-agents "
                        f"reported · read-only, in parallel · diff vs "
                        f"{briefs['base']}"}))
            return 0
        # Emit path (R-W2): 'auto' picks the workflow when the host has a
        # runtime, else today's Task dispatch. The chosen path + reason are
        # TRACED (review_dispatch_path) on BOTH paths, but printed only on
        # the workflow path — the task-path stdout stays BYTE-IDENTICAL to
        # the pre-workflow payload (Codex parity, R-0002's core promise).
        emit = getattr(a, "emit", "auto") or "auto"
        avail = workflow_available(ws)
        if emit == "workflow":
            path = "workflow"
            reason = "explicit --emit workflow" + (
                "" if avail["available"] else f" (forced: {avail['reason']})")
        elif emit == "task":
            path, reason = "task", "explicit --emit task"
        else:
            path = "workflow" if avail["available"] else "task"
            reason = avail["reason"]
        tp.trace(ws, "review_dispatch_path", path=path, reason=reason,
                 emit=emit)
        if path == "workflow":
            out = dict(briefs)
            out["dispatch_path"] = "workflow"
            out["reason"] = reason
            # args IS the unmodified dispatch payload — the workflow and
            # the Task path consume the identical contract:lens-brief set.
            out["workflow"] = {"name": "review-wave", "args": briefs}
            print(json.dumps(out, indent=2))
            return 0
        print(json.dumps(briefs, indent=2))
        return 0

    if a.json:
        print(json.dumps(routing, indent=2))
    else:
        print(lensmod.render(routing))
    return 0


def cmd_yield(a) -> int:
    """What the harness RETURNS, beside what ci_loop_cost.py says it costs.

    Read-only and advisory by construction: this command cannot fail a
    build or block a gate, and nothing in the engine reads its ledger. It
    exists so that dropping a lens is a decision backed by evidence rather
    than by taste.
    """
    import yield_meter
    ws = _workspace(a.workspace)
    if a.yield_action == "mark":
        res = yield_meter.record_disposition(
            ws, a.finding, a.verdict, by=getattr(a, "by", None) or None,
            note=getattr(a, "note", "") or "")
        print(json.dumps(res, indent=2) if a.json
              else (res.get("error") or
                    f"{res['verdict']}: {res['recorded']}"))
        return 1 if res.get("error") else 0
    rep = yield_meter.report(ws)
    print(json.dumps(rep, indent=2, sort_keys=True) if a.json
          else yield_meter.render(rep))
    return 0


def cmd_kb(a) -> int:
    """Record / retrieve / list knowledge-base decisions."""
    import kb as kbmod
    ws = _workspace(a.workspace)
    if a.kb_action == "record":
        e = kbmod.record_decision(
            ws, a.title, context=a.context or "", decision=a.decision or "",
            rationale=a.rationale or "",
            tags=(a.tags.split(",") if a.tags else None),
            context_files=(a.files.split(",") if a.files else None))
        print(json.dumps({"recorded": e["id"], "file": e["file"]}, indent=2))
    elif a.kb_action == "retrieve":
        ds = kbmod.retrieve(ws, files=(a.files.split(",") if a.files else None),
                            tags=(a.tags.split(",") if a.tags else None),
                            limit=a.limit)
        print(kbmod.render_context(ds) or "no relevant decisions.")
    elif a.kb_action == "lint":
        problems = kbmod.lint(ws)
        if problems:
            print("kb lint: FAIL — prompt data / oversized fields in the "
                  "committed store:")
            for p in problems:
                print(f"  ✗ {p['file']}: {p['problem']}")
            return 1
        print("kb lint: clean — committed store is decision data only.")
    elif a.kb_action == "list":
        for d in kbmod.list_decisions(ws):
            print(f"[{d['id']}] {d['status']:<10} {d['title']}  "
                  f"tags={','.join(d['tags']) or '—'}")
    elif a.kb_action == "where":
        store = tp.store_root(ws)
        legacy = os.path.join(ws, "knowledge")
        print(json.dumps({
            "store": store,
            "knowledge": tp.kb_root(ws),
            "meta": tp.store_meta_path(ws),
            "legacy_in_repo_present": os.path.isdir(legacy),
            "migrated": os.path.isdir(os.path.join(store, "knowledge")),
        }, indent=2))
    elif a.kb_action == "migrate":
        res = _migrate_kb(ws)
        if res["moved"]:
            print(f"taskplane: moved in-repo knowledge/ → {res['store']}")
        else:
            print(f"taskplane: nothing to move — knowledge base already at "
                  f"{res['store']}")
        if res["untracked"]:
            print("  · untracked knowledge/ in git (commit the removal to "
                  "finish)")
        if res["gitignored"]:
            print("  · added knowledge/ to .gitignore")
    return 0


def cmd_req(a) -> int:
    """Requirements — record, score refinement, suggest mode, track debt."""
    import requirements as req
    ws = _workspace(a.workspace)
    if a.req_action == "new":
        try:
            nfr = dict(kv.split("=", 1) for kv in (a.nfr or []))
        except ValueError:
            bad = [kv for kv in (a.nfr or []) if "=" not in kv]
            print(f"taskplane: --nfr expects LENS=STATEMENT; missing '=' in "
                  f"{bad}", file=sys.stderr)
            return 1
        contracts = []
        for raw in (getattr(a, "contract", None) or []):
            if ":" not in raw:
                print("taskplane: --contract expects "
                      "provides|consumes|changes:CONTRACT", file=sys.stderr)
                return 1
            relation, cid = raw.split(":", 1)
            relation = relation.strip().lower()
            cid = cid.strip()
            if relation not in ("provides", "consumes", "changes") or not cid:
                print("taskplane: --contract expects "
                      "provides|consumes|changes:CONTRACT", file=sys.stderr)
                return 1
            node = cid if cid.startswith(("contract:", "resource:")) \
                else "contract:" + cid
            contracts.append({"relation": relation, "id": node})
        deps = list(a.depends or [])
        if a.changed_from:
            deps.append(a.changed_from)
        e = req.record_requirement(
            ws, a.title,
            functional=(a.functional or None),
            nfr=nfr,
            acceptance=(a.acceptance or None),
            open_questions=(a.open or None),
            tags=(a.tags.split(",") if a.tags else None),
            context_files=(a.files.split(",") if a.files else None),
            changed_from=a.changed_from, depends_on=deps,
            contracts=contracts)
        # Product dependencies land in the graph immediately — a change
        # request also gets a depends edge to its origin requirement.
        if deps or contracts:
            import depgraph as dg
            for d in deps:
                dg.link_requirement_dep(ws, e["id"], d)
            for contract in contracts:
                dg.record_edge(ws, dg.req_node(e["id"]), contract["id"],
                               kind=contract["relation"], confidence="high")
        print(json.dumps({"recorded": e["id"], "status": e["status"],
                          "depends": deps or None,
                          "contracts": contracts or None,
                          "file": e["file"]}, indent=2))
    elif a.req_action == "score":
        r = req.get_requirement(ws, a.id)
        if r is None:
            print(f"taskplane: no requirement {a.id}", file=sys.stderr)
            return 1
        files = a.files.split(",") if a.files else None
        g = req.gate(r, threshold=a.threshold, high_cost=a.high_cost,
                     changed_files=files, task_type=a.task_type)
        print(json.dumps(g, indent=2))
        return 1 if g["blocking"] else 0
    elif a.req_action == "mode":
        m = req.suggest_mode(a.refinement, a.size)
        print(json.dumps(m, indent=2))
    elif a.req_action == "debt":
        e = req.record_debt(ws, a.title, requirement_id=a.req,
                            reason=a.reason or "", follow_up=a.follow_up or "",
                            tags=(a.tags.split(",") if a.tags else None),
                            context_files=(a.files.split(",") if a.files
                                           else None))
        print(json.dumps({"recorded": e["id"], "file": e["file"]}, indent=2))
    elif a.req_action == "list":
        for r in req.list_requirements(ws):
            oq = f" ({len(r['open_questions'])} open Q)" if r.get(
                "open_questions") else ""
            print(f"[{r['id']}] {r['status']:<10} {r['title']}{oq}")
        for d in req.list_debt(ws):
            print(f"[{d['id']}] debt/open   {d['title']} "
                  f"→ {d.get('requirement_id') or '—'}")
    return 0


PRODUCT_MD = """# Product context

What this product is, who it serves, what "good" means here. The product
persona reads this before shaping requirements; the on-demand north-star
review measures every strategic call against the Direction line below.

- **Direction / north star:** (one sentence — the direction every strategic call is judged against)
- **Product:**
- **Users / customers:**
- **Current goals (what "good" looks like this quarter):**
- **What we say no to:**
"""

TECH_MD = """# Tech stack & constraints

What the engineering lenses should assume. The architecture lens keeps the
system model in `knowledge/architecture.md`; this file is the coarse truth.

- **Languages / frameworks:**
- **Infra (where it runs):**
- **Non-negotiables (compliance, uptime, budgets):**
"""

CURRENT_STATE_MD = """# Current state — as-built inventory

> What ALREADY EXISTS. Every design lens grounds its review here: a design
> is judged as a DELTA against this inventory, never in a vacuum.
> Reinventing a listed component, or contradicting a fact below, is a
> blocker-class finding. Keep it short and true; record the big as-built
> choices as ACCEPTED decisions (`tp decision new "<title>" --modules
> <globs>`) so they also govern future work automatically.

- **Built & running (components, who owns them):**
- **Data & integrations that exist (sources, pipelines, stores):**
- **Hardware / physical constraints already in place:**
- **In flight (started, not landed):**
- **Known debt on the built parts:**
"""

WORKFLOW_MD = """# Workflow conventions

How this team ships. The loop reads these as defaults.

- **Definition of Done extras (beyond tests + scope diff):**
- **Branching / merge conventions:**
- **Human gates (default: plan approval + EM sign-off):**
"""


def _ensure_gitignored(ws, entries, header) -> list:
    """Append any missing entries to the repo .gitignore. Returns what it
    added."""
    gi_path = os.path.join(ws, ".gitignore")
    existing = ""
    if os.path.exists(gi_path):
        with open(gi_path, encoding="utf-8") as f:
            existing = f.read()
    missing = [e for e in entries if e not in existing]
    if missing:
        with open(gi_path, "a", encoding="utf-8") as f:
            f.write("\n# " + header + "\n" + "\n".join(missing) + "\n")
    return missing


def _migrate_kb(ws) -> dict:
    """Relocate a legacy in-repo knowledge/ to the external store, UNTRACK it
    in git, and gitignore it. Idempotent — a no-op once migrated."""
    legacy = os.path.join(ws, "knowledge")
    was_tracked = False
    if os.path.isdir(legacy):
        tracked = tp._run(["git", "ls-files", "knowledge"], cwd=ws).stdout
        was_tracked = bool(tracked.strip())
        if was_tracked:
            tp._run(["git", "rm", "-r", "--cached", "--ignore-unmatch",
                     "--quiet", "knowledge"], cwd=ws)
    res = tp.migrate_store(ws)            # move data + write meta.json
    # ANCHORED pattern: a bare `knowledge/` also matches `.taskplane-kb/
    # knowledge/`, so on a team plan the shared store became uncommittable
    # and sharing silently never worked (v1.5.2). `/knowledge/` matches only
    # the repo-root legacy dir this rule is about. Rewrite any pre-existing
    # unanchored line in place.
    gi_path = os.path.join(ws, ".gitignore")
    if os.path.exists(gi_path):
        with open(gi_path, encoding="utf-8") as f:
            body = f.read()
        fixed = re.sub(r'(?m)^knowledge/\s*$', '/knowledge/', body)
        if fixed != body:
            with open(gi_path, "w", encoding="utf-8") as f:
                f.write(fixed)
    ignored = _ensure_gitignored(
        ws, ["/knowledge/"],
        "taskplane knowledge base — lives in the external store "
        "(~/.taskplane), never the repo")
    res.update({"untracked": was_tracked, "gitignored": bool(ignored)})
    return res


def cmd_share(a) -> int:
    """v1.5.0 — plan-aware knowledge sharing.

    share status                     mode, plan, store, unpublished count
    share plan personal|team|enterprise    set (and change) the plan
    share set private|shared         private mode even on a team plan
    share push [--ids 0001,0002]     publish private decisions to the
                                     shared repo store — like a git push"""
    import kb as kbmod
    ws = _workspace(a.workspace)
    act = a.share_cmd
    if act == "status":
        mode = tp.get_mode(ws)
        unpublished = unpub_flows = 0
        try:
            with open(os.path.join(tp.external_store_root(ws),
                                   "knowledge", "index.json"), encoding="utf-8") as f:
                pidx = json.load(f)
            unpublished = sum(1 for d in pidx.get("decisions", [])
                              if not d.get("published_as"))
            unpub_flows = sum(1 for d in pidx.get("flows", [])
                              if not d.get("published_as"))
        except (OSError, ValueError):
            pass
        print(json.dumps({**mode, "store_path": tp.store_root(ws),
                          "private_decisions_unpublished": unpublished,
                          "private_flows_unpublished": unpub_flows,
                          "not_covered_by_push": "requirements + context "
                          "docs (stay private)",
                          "change": {"plan": "tp share plan "
                                     "personal|team|enterprise",
                                     "privacy": "tp share set "
                                     "private|shared",
                                     "publish": "tp share push "
                                     "[--ids 0001,0002]"}}, indent=2))
    elif act == "plan":
        mode = tp.set_mode(ws, plan=a.value)
        out = {**mode, "store_path": tp.store_root(ws)}
        if a.value == "personal" and mode["store"] == "repo":
            # Plan says personal but a committed shared config keeps the store
            # in-repo — so decisions still land in the team store. Make the
            # mismatch explicit rather than silently surprising. (v1.5.2)
            out["notice"] = ("plan is personal, but this repo has a committed "
                             "shared store (.taskplane-kb/config.json) so "
                             "knowledge still goes to the team. To work "
                             "privately here, run `tp share set private`.")
        print(json.dumps(out, indent=2))
    elif act == "set":
        want_private = (a.value == "private")
        if want_private and tp.store_env() == "repo":
            # v1.5.1 (B2): inside Claude Tag the env mandates the repo
            # store and the private store cannot survive the ephemeral
            # sandbox — a silent "private" flag that still writes to the
            # committed store is the worst outcome. Refuse loudly.
            print(json.dumps({"error": "private mode is unavailable here: "
                              "TASKPLANE_STORE=repo forces the shared "
                              "in-repo store (Claude Tag sandbox — the "
                              "private store would not survive it). Work "
                              "privately from Claude Code/Cowork instead."}))
            return 1
        mode = tp.set_mode(ws, private=want_private)
        if not want_private and mode["store"] != "repo":
            # v1.5.1: "set shared" with no shared store configured was a
            # silent no-op — the user asked to share, nothing changed.
            print(json.dumps({**mode, "store_path": tp.store_root(ws),
                              "error": "no shared store is configured — "
                              "sharing needs a team/enterprise plan. Run "
                              "`tp share plan team|enterprise` first."},
                             indent=2))
            return 1
        print(json.dumps({**mode, "store_path": tp.store_root(ws)},
                         indent=2))
    elif act == "push":
        # Guard: publishing only makes sense to a team store. On a personal
        # plan with no committed shared config, push would silently create a
        # .taskplane-kb no teammate will ever see — same failure `set shared`
        # now rejects. (v1.5.2)
        mode = tp.get_mode(ws)
        shared_cfg = os.path.exists(os.path.join(
            tp.repo_store_root(ws), "config.json"))
        if mode["plan"] not in ("team", "enterprise") and not shared_cfg:
            print(json.dumps({"error": "nothing to publish to — sharing "
                              "needs a team/enterprise plan. Run `tp share "
                              "plan team|enterprise` first.",
                              "plan": mode["plan"]}, indent=2))
            return 1
        ids = [x.strip() for x in (a.ids or "").split(",")
               if x.strip()] or None
        out = kbmod.publish(ws, ids=ids)
        print(json.dumps(out, indent=2))
        if out.get("error") or out.get("unknown_ids"):
            return 1
    else:
        print(json.dumps({"error": "share needs a subcommand: status | "
                          "plan | set | push"}))
        return 1
    return 0


def cmd_init(a) -> int:
    """Scaffold a project for governed work: context docs, KB dirs, graph.
    The knowledge base lives in the EXTERNAL per-project store (~/.taskplane),
    not the repo — any legacy in-repo knowledge/ is migrated out here."""
    import depgraph as dg
    ws = _workspace(a.workspace)
    # v1.5.1 (B3): the plan decides WHERE the store lives — record it
    # BEFORE anything resolves store paths, or context docs land in the
    # wrong store and the project looks un-initialized right after init.
    if getattr(a, "plan", None):
        tp.set_mode(ws, plan=a.plan)
    mig = _migrate_kb(ws)                 # relocate + untrack + gitignore
    store = tp.store_root(ws)
    ctx = os.path.join(tp.kb_root(ws), "context")
    os.makedirs(ctx, exist_ok=True)
    wrote = []
    for name, body in (("product.md", PRODUCT_MD),
                       ("tech-stack.md", TECH_MD),
                       ("workflow.md", WORKFLOW_MD),
                       ("current-state.md", CURRENT_STATE_MD)):
        p = os.path.join(ctx, name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            wrote.append(f"context/{name}")
    # Runtime paths that stay LOCAL to the checkout — never committed.
    missing = _ensure_gitignored(
        ws, [".taskplane/", ".eval/", ".em-review/", ".security-review/",
             ".tp-work/"],
        "taskplane runtime (local-only — see docs/state-spec.md)")
    g = dg.scan(ws)
    head = tp.git_head(ws)
    if head and not _is_commit_sha(head):
        head = None    # empty repo: rev-parse echoes "HEAD"
    mode = tp.get_mode(ws)
    tp.trace(ws, "project_init", context_docs=wrote,
             graph_modules=len(g["modules"]), store=store,
             migrated=mig.get("moved"))
    print(json.dumps({
        "knowledge_store": store,
        "mode": mode,
        "plan_question": None if (mode["source"] != "default") else
            "ASK THE HUMAN: keep taskplane knowledge private/local, or "
            "share it with the team in the repository? Set private/local "
            "with `tp share plan personal`; set shared with `tp share plan "
            "team|enterprise`. Private keeps knowledge outside the repo "
            "(~/.taskplane); shared keeps it in-repo (.taskplane-kb/). "
            "On a team plan, `tp share set private` works privately and "
            "`tp share push` publishes selected decisions later.",
        "migrated_from_repo": mig.get("moved") or False,
        "context_docs_created": wrote or "(already present)",
        "graph": {"modules": len(g["modules"]), "edges": len(g["edges"])},
        "gitignored_runtime": missing or "(already present)",
        "committed_state": (
            "NONE — the knowledge base is external (~/.taskplane); the repo "
            "carries no taskplane artifacts" if mode["store"] == "external"
            else "SHARED — the knowledge base lives in-repo (.taskplane-kb/) "
                 "and is committed with the code (team/enterprise plan)"),
        "git": head[:12] if head else
               (os.path.isdir(os.path.join(ws, ".git")) and
                "REPO HAS NO COMMITS — `git add -A && git commit` "
                "(gates need a snapshot)" or
                "NOT A REPO — `git init && git add -A && git commit` "
                "(gates need a snapshot)"),
        "next": "fill the context docs in the store, then state a goal via "
                "the tp-go skill (or `tp.py req new` + `tp.py loop init`)",
    }, indent=2))
    return 0


def cmd_track(a) -> int:
    """Multiple workstreams over one engine; shared KB/graph across tracks."""
    import track as tr
    ws = _workspace(a.workspace)
    out = None
    if a.track_action == "new":
        out = tr.new(ws, a.name, " ".join(a.goal or []) or a.name,
                     requirement_id=a.req)
    elif a.track_action == "list":
        out = tr.list_(ws)
    elif a.track_action == "switch":
        out = tr.switch(ws, a.name)
    elif a.track_action == "close":
        out = tr.close(ws, a.name, status=a.status)
    print(json.dumps(out, indent=2))
    # Same exit-code contract as cmd_loop: an engine refusal is nonzero.
    return 1 if isinstance(out, dict) and out.get("error") else 0


def cmd_context(a) -> int:
    """Compact session context (SessionStart hook): where things stand."""
    import depgraph as dg
    import kb as kbmod
    import loop as loopmod
    import requirements as reqmod
    import track as tr
    ws = _workspace(a.workspace)
    if not os.path.isdir(tp.kb_root(ws)) and \
            not os.path.isdir(tp.tp_dir(ws)):
        # An installed plugin must expose its on-ramp. Using the same report
        # as tp-go also recognizes linked worktrees (.git is a file) and
        # keeps the prompt specific to the single missing prerequisite.
        report = _onboard_report(ws)
        prompts = {
            "attach_folder": "no project folder is connected yet",
            "init_git": "this folder needs a git repo with an initial commit",
            "tp_init": "this repo needs taskplane initialization",
        }
        missing = prompts.get(report["next_action"], "setup is incomplete")
        print(f"[taskplane] installed; {missing} — say \"set up taskplane\" "
              "to continue, or \"taskplane help\" for the tour.")
        return 0
    st = loopmod.status(ws)
    g = dg.load(ws)
    reqs_open = [r for r in reqmod.list_requirements(ws)
                 if r["status"] not in ("done",)]
    debt = reqmod.list_debt(ws)
    trk = tr.list_(ws)
    lines = ["[taskplane] governed workspace:"]
    if trk["active"]:
        lines.append(f"  track: {trk['active']} "
                     f"({len(trk['tracks'])} total)")
    if st.get("loop") != "none":
        lines.append(f"  loop: step={st['step']} goal=\"{st['goal'][:48]}\" "
                     f"tasks={len(st.get('tasks') or [])}")
    if reqs_open:
        lines.append(f"  requirements open: {len(reqs_open)} "
                     f"(latest {reqs_open[-1]['id']} {reqs_open[-1]['title'][:40]})")
    if debt:
        lines.append(f"  tracked debt: {len(debt)} open item(s)")
    if g["modules"]:
        lines.append(f"  dep graph: {len(g['modules'])} components / "
                     f"{len(g['edges'])} edges (tp.py graph impact for "
                     "blast radius)")
    ds = kbmod.list_decisions(ws)
    if ds:
        lines.append(f"  KB: {len(ds)} decision(s) — recall before "
                     "re-deriving anything")
    if len(lines) > 1:
        print("\n".join(lines))
    return 0


def cmd_summary(a) -> int:
    """User control-plane view: outcome, progress, and decisions only."""
    import loop as loopmod
    ws = _workspace(a.workspace)
    summary = loopmod.user_summary(ws)
    if getattr(a, "json", False):
        print(json.dumps(summary, indent=2))
        return 0
    headline = summary["headline"]
    decision = summary.get("decision")
    if decision and decision in headline:
        # The ACTION REQUIRED line below carries the decision sentence ONCE;
        # printing it inside the headline too diluted the four-line surface
        # whose whole job is to say the one thing needed.
        headline = (headline.replace(decision, "").strip(" —–-–:.")
                    or "Decision required")
    print("taskplane: " + headline)
    if summary.get("next") and summary.get("state") in ("not_started", "done"):
        print("  next: " + summary["next"])
    if summary.get("goal"):
        print("  goal: " + summary["goal"])
    if summary.get("decision"):
        print("  ACTION REQUIRED: " + summary["decision"])
    elif summary.get("state") not in ("done", "not_started"):
        print("  no action required — agents are working under the harness")
    graph = summary.get("graph") or {}
    if graph.get("modules"):
        print(f"  dependency graph: {graph['modules']} nodes / "
              f"{graph['edges']} edges")
    if summary.get("assurance"):
        print("  assurance: " + summary["assurance"])
    return 0


def cmd_dashboard(a) -> int:
    """Emit the mission-control view. Default: the inline widget fragment
    for mcp__visualize__show_widget (the driver pipes it straight in).
    --out also writes a standalone HTML file (no-desktop fallback)."""
    import dashboard
    ws = _workspace(a.workspace)
    if a.out:
        dashboard.render(ws, out=a.out)
    # v1.5.3: headline first (never-skippable), then the widget / pages.
    print("HEADLINE: " + dashboard.headline_loop(ws))
    if getattr(a, "paged", False):
        pages = dashboard.widget_paged(ws)
        print(json.dumps({"headline": dashboard.headline_loop(ws),
                          "pages": pages,
                          "render": "call mcp__visualize__show_widget once "
                          "PER PAGE, in order, each page's html VERBATIM — "
                          "no edits, no restyling, no re-authoring"}, indent=2))
        return 0
    print(dashboard.widget(ws))
    return 0


def cmd_onboard(a) -> int:
    """Cold-start onboarding. Detects whether the workspace is ready for a
    governed run (folder + git snapshot + init) and, by default, prints the
    onboarding dashboard fragment that walks a new user in from zero.
    --json prints the readiness report instead (for the driver to branch on)."""
    import dashboard
    ws = _workspace(a.workspace)
    report = _onboard_report(ws)
    if a.json:
        print(json.dumps(report, indent=2))
        return 0
    # Render contract (v1.5.3/4): the HEADLINE is the never-skippable carrier
    # — on hosts without inline widgets (Codex) it is the primary channel.
    print("HEADLINE: " + dashboard.headline_onboarding(report))
    # R-0005 install truth: wherever onboarding talks about installation it
    # speaks by account type — org-managed / personal when detectable, the
    # by-account-type triage otherwise. Plain text, same channel as the
    # HEADLINE, so it survives hosts without inline widgets.
    for line in report.get("install", {}).get("paths", []):
        print(line)
    print(dashboard.render_onboarding(report, out=a.out))
    return 0


def cmd_findings(a) -> int:
    """Render a REVIEW findings dashboard from a findings JSON — every
    severity, filterable, each finding expandable. A pure review has no loop
    state, so this is how tp-engineering shows ALL findings at the review
    gate (the loop dashboard can't). Prints the inline widget fragment."""
    import dashboard
    path = a.file or os.path.join(_workspace(a.workspace), ".em-review",
                                  "findings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"taskplane: cannot read findings {path}: {e}", file=sys.stderr)
        return 1
    findings = data.get("findings", data) if isinstance(data, dict) else data
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    # v1.5.4: default the workspace so the findings dashboard can render the
    # dependency-graph blast-radius panel (or explain its absence); a review
    # that recorded `impact`/`lens_coverage` in meta flows through to the
    # panels and the headline.
    meta.setdefault("ws", _workspace(a.workspace))
    # Render-reliability contract (v1.5.3): the headline ALWAYS prints first,
    # so the key numbers reach the human even if the widget render is skipped.
    print("HEADLINE: " + dashboard.headline_findings(findings, meta))
    if getattr(a, "paged", False):
        pages = dashboard.render_findings_paged(findings, meta)
        print(json.dumps({"headline":
                          dashboard.headline_findings(findings, meta),
                          "pages": pages,
                          "render": "call mcp__visualize__show_widget once "
                          "PER PAGE, in order, each page's html VERBATIM — "
                          "byte-for-byte, no edits, no restyling, no "
                          "re-authoring. The pages ARE the deliverable; "
                          "never summarize them as prose"}, indent=2))
        return 0
    frag = dashboard.render_findings(findings, meta, out=a.out)
    print(frag)
    return 0


def cmd_northstar(a) -> int:
    """On-demand NORTH-STAR REVIEW helper (skills/tp-northstar). With no
    --render, print the project's Direction / north star (the line the review
    measures against) as JSON. With --render <note.json>, print the strategic-
    note widget fragment (dashboard.render_strategy_note). Read-only, advisory —
    it never touches the loop."""
    ws = _workspace(a.workspace)
    if a.render:
        import dashboard
        try:
            with open(a.render, encoding="utf-8") as f:
                note = json.load(f)
        except (OSError, ValueError) as e:
            print(f"taskplane: cannot read note {a.render}: {e}",
                  file=sys.stderr)
            return 1
        note.setdefault("north_star", north_star(ws))
        # v1.5.4: same render flow as every other command — headline first
        # (never-skippable), then the widget fragment.
        print("HEADLINE: " + dashboard.headline_northstar(note))
        print(dashboard.render_strategy_note(note, out=a.out))
        return 0
    ns = north_star(ws)
    print(json.dumps({
        "north_star": ns,
        "set": ns is not None,
        "source": "context/product.md (Direction / north star)",
        "hint": None if ns else "Add a 'Direction / north star:' line to "
                "context/product.md so the review has a direction to measure "
                "against.",
    }, indent=2))
    return 0


def cmd_graph(a) -> int:
    """Dependency graph: scan (deterministic, no tokens), impact, html."""
    import depgraph as dg
    ws = _workspace(a.workspace)
    if a.graph_action == "scan":
        dec = bool(getattr(a, "decompose", False))
        g = dg.scan(ws, decompose=dec)
        out = {"modules": len(g["modules"]),
               "edges": len(g["edges"]),
               "files": len(g["files"]),
               "stored": os.path.join(tp.kb_root(ws), "graph.json")}
        if dec:   # ADDITIVE: without --decompose the output is unchanged
            out["components"] = len(g.get("components") or [])
        print(json.dumps(out, indent=2))
    elif a.graph_action == "impact":
        files = (a.files.split(",") if a.files else
                 _changed_for_impact(ws, a.base))
        policy = {"local_depth": a.depth,
                  "boundary_mode": a.boundary,
                  "contract_depth": a.contract_depth,
                  "requirement_depth": a.requirement_depth}
        imp = dg.impact(ws, files, max_depth=a.depth, policy=policy)
        prod = dg.product_impact(ws, files)
        imp["affected_requirements"] = prod["affected_requirements"]
        imp["dependent_requirements"] = prod["dependent_requirements"]
        # --json must emit PURE JSON on stdout — the prose context line before
        # it broke json.load for machine consumers (v2.3.1). Mutually
        # exclusive, matching cmd_lens/cmd_onboard.
        if a.json:
            print(json.dumps(imp, indent=2))
        else:
            print(dg.render_context(imp) or "no modules touched.")
    elif a.graph_action == "edge":
        e = dg.record_edge(ws, a.src, a.dst, kind=a.kind, note=a.note or "",
                           confidence=a.confidence)
        print(json.dumps({"recorded": e}, indent=2))
    elif a.graph_action == "contract":
        if not a.provider and not (a.consumer or []):
            print("taskplane: graph contract needs --provider and/or "
                  "--consumer", file=sys.stderr)
            return 1
        node = a.name if a.name.startswith(("contract:", "resource:")) \
            else "contract:" + a.name
        edges = []
        if a.provider:
            edges.append(dg.record_edge(ws, a.provider, node,
                                        kind="provides", confidence="high"))
        for consumer in a.consumer or []:
            edges.append(dg.record_edge(ws, consumer, node,
                                        kind="consumes", confidence="high"))
        print(json.dumps({"contract": node, "edges": edges,
                          "boundary": "dependency impact stops at the "
                                      "contract between entities"}, indent=2))
    elif a.graph_action == "link":
        r = dg.link_requirement(ws, a.req, (a.files or "").split(","),
                                kind=a.kind,
                                replace=not a.keep)
        print(json.dumps(r, indent=2))
    elif a.graph_action == "html":
        files = (a.files.split(",") if a.files else
                 _changed_for_impact(ws, a.base))
        out = dg.to_html(ws, files, out=a.out)
        print(out)
    return 0


def _changed_for_impact(ws, base):
    # One implementation, not two: the kernel's changed_files (diff +
    # untracked, RUNTIME_OWNED bookkeeping excluded, deduped) — so `tp graph
    # impact` computes the SAME blast radius the loop engine computes for
    # the same diff, instead of inflating it with .taskplane/, plan/ and
    # knowledge/ bookkeeping.
    return tp.changed_files(ws, base or "HEAD")


# ------------------------------------------------------------- version
#
# Single-source version. Cutting a release used to hand-edit 7+ locations
# across 6 files, and the unguarded surface drifted exactly as predicted
# (the v2.2.1 tag still said 2.2.0 throughout docs/openai-submission.md).
# True single-sourcing across two host manifest formats isn't possible —
# both hosts insist on their own literal "version" field — so ONE file is
# authoritative and everything else is mechanically VERIFIED against it:
# `tp version --verify` (CI-callable, exit 1 on any drift).

def _plugin_repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plugin_version(root: str | None = None) -> str:
    """The ONE authoritative plugin version: .codex-plugin/plugin.json —
    the manifest scripts/package_openai.py already packages from. Every
    other version field is derived and checked, never independently edited."""
    root = root or _plugin_repo_root()
    src = os.path.join(root, ".codex-plugin", "plugin.json")
    data = tp.load_json(src, what="authoritative version manifest")
    v = data.get("version")
    if not isinstance(v, str) or not v.strip():
        raise tp.StateError(src, "manifest has no usable 'version' field",
                            "restore the authoritative version string")
    return v.strip()


def _walk_versions(obj, prefix=""):
    """Yield (json_path, value) for every literal 'version' key at any
    depth — marketplace.json carries the version in TWO places."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path_ = f"{prefix}.{k}" if prefix else k
            if k == "version":
                yield path_, v
            else:
                yield from _walk_versions(v, path_)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_versions(v, f"{prefix}[{i}]")


def version_report(root: str | None = None) -> dict:
    """Cross-check every derived version surface against the single source.
    Manifests must carry the exact version; docs that exist must mention it
    (the containment check is what catches the shipped 2.2.0-in-docs drift)."""
    root = root or _plugin_repo_root()
    authoritative = plugin_version(root)
    checks = []

    def _manifest(rel):
        p = os.path.join(root, rel)
        try:
            data = tp.load_json(p, what="version manifest")
        except tp.StateError as e:
            checks.append({"file": rel, "field": "(unreadable)",
                           "found": str(e), "ok": False})
            return
        found_any = False
        for field, v in _walk_versions(data):
            found_any = True
            checks.append({"file": rel, "field": field, "found": v,
                           "ok": v == authoritative})
        if not found_any:
            checks.append({"file": rel, "field": "version", "found": None,
                           "ok": False})

    _manifest(os.path.join(".claude-plugin", "plugin.json"))
    _manifest(os.path.join(".claude-plugin", "marketplace.json"))
    _manifest(os.path.join(".codex-plugin", "plugin.json"))

    # Two doc rules:
    #   - README/CHANGELOG MUST mention the authoritative version — every
    #     release adds a history row there, so absence means the release
    #     forgot them.
    #   - openai-submission.md must never carry a STALE version: any
    #     version literal it mentions must include the authoritative one,
    #     but a doc with NO version literals cannot drift and is clean
    #     (the worksheet dropped its hand-synced version mentions for
    #     exactly this reason).
    for rel, must_mention in (
            ("README.md", True),
            ("CHANGELOG.md", True),
            (os.path.join("docs", "openai-submission.md"), False)):
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue      # docs aren't packaged into every install shape
        try:
            with open(p, encoding="utf-8") as f:
                body = f.read()
        except OSError as e:
            checks.append({"file": rel, "field": "(unreadable)",
                           "found": str(e), "ok": False})
            continue
        if must_mention:
            needle = "v" + authoritative
            checks.append({"file": rel, "field": f"mentions '{needle}'",
                           "found": needle if needle in body else "ABSENT",
                           "ok": needle in body})
        else:
            mentioned = set(re.findall(r"\bv?(\d+\.\d+\.\d+)\b", body))
            ok = (not mentioned) or (authoritative in mentioned)
            checks.append({
                "file": rel,
                "field": "no stale version literals",
                "found": (", ".join(sorted(mentioned)) or
                          "(no version literals — cannot drift)"),
                "ok": ok})

    mismatches = [c for c in checks if not c["ok"]]
    return {"version": authoritative,
            "source": ".codex-plugin/plugin.json (authoritative — edit the "
                      "version THERE; everything else is verified)",
            "checks": checks, "mismatches": mismatches,
            "ok": not mismatches}


def cmd_version(a) -> int:
    if not getattr(a, "verify", False):
        print(plugin_version())
        return 0
    rep = version_report()
    print(json.dumps(rep, indent=2))
    if not rep["ok"]:
        print(f"taskplane: version drift — {len(rep['mismatches'])} "
              f"surface(s) disagree with the authoritative "
              f"{rep['version']} (.codex-plugin/plugin.json).",
              file=sys.stderr)
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------------
# D5 (R-0010): the CLI reference is GENERATED, never hand-written.
#
# `tp help --md` walks the LIVE argparse tree — the same parser object the
# CLI dispatches with — and prints a deterministic markdown reference of
# every subcommand (nested subparsers included) and every long flag with
# its help text. Nothing environmental enters the output: no timestamps,
# no absolute paths, no terminal-width wrapping (argparse's own
# format_help() is width-dependent, so it is deliberately NOT used), and
# every level is emitted in sorted order.
#
# The generator REFUSES rather than emits a reference it cannot stand
# behind: a subcommand or long flag with empty/missing help text, or a
# degenerate walk that found no commands or no flags. That refusal is the
# replacement for the old hand-maintained exemption list — a new flag
# without help prose cannot be generated at all, and a new flag whose
# reference was not regenerated fails the CI drift leg.
# ---------------------------------------------------------------------


class CliReferenceError(RuntimeError):
    """The CLI reference generator refused to emit."""


def _cli_commands(parser, path=(), help_text=None):
    """Yield (path, parser, help) for `parser` and every nested subparser.

    Depth-first, each level in sorted name order, so the walk order is a
    pure function of the parser tree.
    """
    yield path, parser, help_text
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {ca.dest: ca.help for ca in action._choices_actions}
        for name in sorted(action.choices):
            yield from _cli_commands(action.choices[name], path + (name,),
                                     helps.get(name))


def _cli_cell(text):
    """One markdown table cell: single-line, pipes escaped."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _cli_value(action):
    """What the flag takes, as a table cell."""
    if action.nargs == 0:
        value = "flag"
    elif action.choices:
        value = "one of: " + ", ".join(str(c) for c in action.choices)
    elif action.metavar:
        value = str(action.metavar)
    else:
        value = action.dest.upper()
    marks = []
    if action.required:
        marks.append("required")
    if isinstance(action, argparse._AppendAction):
        marks.append("repeatable")
    if marks:
        value += " (" + ", ".join(marks) + ")"
    return value


def _cli_flags(parser):
    """[(rendered flag names, action)] for this parser, sorted."""
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction,
                               argparse._HelpAction)):
            continue
        longs = sorted(o for o in action.option_strings if o.startswith("--"))
        if not longs:
            continue
        rows.append((longs, action))
    return sorted(rows, key=lambda r: r[0][0])


def _cli_positionals(parser):
    """[(name, action)] for this parser's positional arguments."""
    out = []
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction,
                               argparse._HelpAction)):
            continue
        if action.option_strings:
            continue
        out.append((str(action.metavar or action.dest), action))
    return out


_CLI_NARGS_NOTE = {"*": "zero or more", "+": "one or more", "?": "optional"}

CLI_REFERENCE_REGEN = ("python3 taskplane/tp.py help --md > "
                       "docs/cli-reference.md")


def cli_reference_markdown(parser) -> str:
    """Render `parser`'s whole command tree as markdown, or refuse.

    Raises CliReferenceError (naming what is wrong) when the reference
    would be degenerate, or when any subcommand or long flag carries no
    help text.
    """
    walk = sorted(_cli_commands(parser), key=lambda row: row[0])
    commands = [row for row in walk if row[0]]
    flag_total = sum(len(_cli_flags(par)) for _path, par, _h in walk)
    if not commands or not flag_total:
        raise CliReferenceError(
            "refusing to emit a degenerate CLI reference: the argparse walk "
            f"found {len(commands)} subcommand(s) and {flag_total} long "
            "flag(s) — the parser tree is empty or was not built")

    undocumented = []
    for path, par, help_text in walk:
        name = " ".join((parser.prog,) + path)
        if path and not str(help_text or "").strip():
            undocumented.append(f"subcommand `{name}` has no help text")
        for longs, action in _cli_flags(par):
            if not str(action.help or "").strip():
                undocumented.append(
                    f"flag `{longs[0]}` on `{name}` has no help text")
    if undocumented:
        raise CliReferenceError(
            "refusing to emit an incomplete CLI reference — "
            f"{len(undocumented)} undocumented surface(s): "
            + "; ".join(undocumented)
            + ". Write the argparse help= text (that IS the documentation) "
              "and regenerate")

    out = [f"# `{parser.prog}` CLI reference", ""]
    out += [
        f"> This file is GENERATED from `{parser.prog}`'s live argparse "
        "tree — don't",
        "> hand-edit. Regenerate with",
        f"> `{CLI_REFERENCE_REGEN}`.",
        "> CI regenerates and diffs this file on every push, so a stale "
        "copy fails",
        "> the build.",
        "",
        f"Every subcommand of `{parser.prog}` and every long flag it "
        "accepts, walked",
        "from the parser the CLI actually dispatches with: a flag cannot "
        "be listed",
        "here without existing, and cannot exist without being listed. "
        "The generator",
        "REFUSES to emit when a subcommand or a long flag carries no help "
        "text, so",
        "the documentation ratchet is enforced at generation time rather "
        "than by an",
        "exemption list.",
        "",
        "argparse's own `-h` / `--help` is accepted by every command below "
        "and is",
        "not repeated in the tables.",
        "",
        "## Commands",
        "",
        "| Command | What it does |",
        "| --- | --- |",
    ]
    for path, _par, help_text in commands:
        name = " ".join((parser.prog,) + path)
        out.append(f"| `{name}` | {_cli_cell(help_text)} |")
    out.append("")

    for path, par, help_text in commands:
        name = " ".join((parser.prog,) + path)
        out += [f"## `{name}`", "", _cli_cell(help_text), ""]
        positionals = _cli_positionals(par)
        if positionals:
            out.append("Positional arguments:")
            out.append("")
            for pname, action in positionals:
                note = _CLI_NARGS_NOTE.get(action.nargs)
                line = f"- `{pname}`"
                if note:
                    line += f" ({note})"
                if str(action.help or "").strip():
                    line += f" — {_cli_cell(action.help)}"
                out.append(line)
            out.append("")
        flags = _cli_flags(par)
        if flags:
            out += ["| Flag | Value | What it does |", "| --- | --- | --- |"]
            for longs, action in flags:
                shown = ", ".join(f"`{o}`" for o in longs)
                out.append(f"| {shown} | {_cli_cell(_cli_value(action))} "
                           f"| {_cli_cell(action.help)} |")
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def cmd_help(a) -> int:
    """`tp help` — the parser's own help; `tp help --md` — the reference."""
    parser = getattr(a, "root_parser", None)
    if parser is None:
        print("taskplane: help is unavailable — no parser was bound to the "
              "command (internal error)", file=sys.stderr)
        return 1
    if not getattr(a, "md", False):
        parser.print_help()
        return 0
    try:
        markdown = cli_reference_markdown(parser)
    except CliReferenceError as exc:
        print(f"taskplane: help --md refused: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(markdown)
    return 0


def _utf8_streams() -> None:
    """Make stdout/stderr UTF-8 regardless of the host's console codepage.

    Windows consoles default to a legacy codepage (cp1252 on en-US), and
    taskplane's own output carries arrows and em dashes — `tp kb migrate`
    and `tp northstar` died mid-print with UnicodeEncodeError, taking the
    command's exit code with them. The text is ours and it is UTF-8; the
    stream should say so. Guarded: a stream may be replaced by a test
    harness or a wrapper with no reconfigure().
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    _utf8_streams()
    p = argparse.ArgumentParser(prog="tp.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create + activate a Task Contract")
    n.add_argument("goal", nargs="+")
    n.add_argument("--scope", help="comma-separated scope globs (relative)")
    n.add_argument("--deny", action="append", help="extra deny command (repeatable)")
    n.add_argument("--tools", help="comma-separated allowed tools (default: any)")
    n.add_argument("--tests", help="DoD test command")
    n.add_argument("--budget", type=float, help="cooperative $ ceiling")
    n.add_argument("--max-actions", type=int, dest="max_actions",
                   help="hook-enforced action ceiling (default 60)")
    n.add_argument("--read-only", action="store_true",
                   help="review/plan role — block filesystem writes")
    n.add_argument("--write-allow", action="append", metavar="GLOB",
                   help="in read-only mode, dirs that ARE writable "
                        "(e.g. .em-review/**) — repeatable")
    n.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    n.set_defaults(fn=cmd_new)

    dc = sub.add_parser("decision", help="decision registry — structured "
                        "ADRs with lifecycle, links and supersede chains")
    dc.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    dsub = dc.add_subparsers(dest="decision_action", required=True)
    dn = dsub.add_parser("new", help="record a new decision (ADR)")
    dn.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    dn.add_argument("title")
    dn.add_argument("--context", help="the situation that forced the "
                    "decision")
    dn.add_argument("--decision", help="what was decided, in one sentence")
    dn.add_argument("--rationale", help="why this option won over the "
                    "alternatives")
    dn.add_argument("--alternative", action="append",
                    help="repeatable: 'option | gained | given up'")
    dn.add_argument("--req", help="linked requirement R-XXXX")
    dn.add_argument("--modules", help="comma-separated module globs this "
                    "decision governs (drives always-on context injection)")
    dn.add_argument("--supersedes", help="decision id this one replaces")
    dn.add_argument("--status", default="accepted",
                    choices=["proposed", "accepted"],
                    help="lifecycle state to record it in "
                         "(default: accepted)")
    dn.add_argument("--tags", help="comma-separated tags for retrieval")
    dl = dsub.add_parser("list", help="list recorded decisions")
    dl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    dl.add_argument("--status", dest="status_filter",
                    help="list only decisions in this lifecycle state")
    dsh = dsub.add_parser("show", help="print one decision in full")
    dsh.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    dsh.add_argument("id")
    da = dsub.add_parser("accept", help="move a proposed decision to accepted")
    da.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    da.add_argument("id")
    dsp = dsub.add_parser("supersede", help="mark a decision replaced by a newer one")
    dsp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    dsp.add_argument("id")
    dsp.add_argument("--by", required=True,
                     help="id of the decision that replaces this one")
    dc.set_defaults(fn=cmd_decision)

    s = sub.add_parser("screen", help="PreToolUse hook entrypoint (stdin event)")
    s.set_defaults(fn=cmd_screen)

    sd = sub.add_parser("screen-dispatch", help="PreToolUse hook for the "
                        "Agent tool: verify tier-routed model was passed "
                        "(inert unless TASKPLANE_ENFORCE_DISPATCH=warn|strict)")
    sd.set_defaults(fn=cmd_screen_dispatch)

    sas = sub.add_parser("subagent-start", help="SubagentStart lifecycle "
                         "trace and bounded contract context (stdin event)")
    sas.set_defaults(fn=cmd_subagent_start)
    saz = sub.add_parser("subagent-stop", help="SubagentStop lifecycle trace "
                         "(stdin event; advisory, never a completion gate)")
    saz.set_defaults(fn=cmd_subagent_stop)

    rd = sub.add_parser("ready", help="Definition-of-Ready entry gate")
    rd.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rd.set_defaults(fn=cmd_ready)

    gcp = sub.add_parser("gc", help="prune runtime artifacts (tombstones, "
                         "stale locks, orphaned tmp) — never governance "
                         "records")
    gcp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    gcp.set_defaults(fn=cmd_gc)
    cl = sub.add_parser("clear", help="deactivate the workspace contract")
    cl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    cl.set_defaults(fn=cmd_clear)

    st = sub.add_parser("status", help="show the active contract")
    st.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    st.set_defaults(fn=cmd_status)

    b = sub.add_parser("budget", help="record a cooperative spend estimate, "
                       "or --grant N more actions (the budget approval gate)")
    b.add_argument("--spent", type=float,
                   help="cooperative $ estimate (advisory)")
    b.add_argument("--grant", type=int, metavar="N",
                   help="raise the enforced action ceiling by N — for the "
                        "human / ungoverned main session after approving "
                        "more budget (a governed agent cannot grant itself)")
    b.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    b.set_defaults(fn=cmd_budget)

    d = sub.add_parser("dod", help="Definition-of-Done exit gate (+ kb lint)")
    d.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    d.set_defaults(fn=cmd_dod)

    lp = sub.add_parser("loop", help="drive the Evaluate-Loop engine")
    lp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    lsub = lp.add_subparsers(dest="loop_action", required=True)
    li = lsub.add_parser("init", help="start an Evaluate-Loop for a goal")
    li.add_argument("goal", nargs="*")
    li.add_argument("--spec", help="path to an existing spec (skips PM)")
    li.add_argument("--max-fix-cycles", type=int, default=2,
                    help="fix cycles the loop may run before it escalates "
                         "to the human (default 2)")
    li.add_argument("--checkpoints", help="comma list: plan,em (default both)")
    li.add_argument("--req", help="anchor the loop to a requirement R-id")
    li.add_argument("--parallel", action="store_true",
                    help="execute waves of scope-disjoint tasks concurrently, "
                         "one governed agent per task")
    li.add_argument("--design", action="store_true",
                    help="run the Design Contract + human design approval "
                         "before implementation planning")
    li.add_argument("--design-only", action="store_true",
                    help="stop after the human approves the Design Contract "
                         "instead of continuing to Plan/Build/Review")
    li.add_argument("--force", action="store_true",
                    help="replace an in-flight loop (the old loop.json is "
                         "archived first — without this flag re-init refuses)")
    ln = lsub.add_parser("next", help="print the next stage brief for the active loop")
    ln.add_argument("--req", help="attach requirement R-id to the loop "
                    "before DoR evaluation (design anchor)")
    ln.add_argument("--emit", choices=["workflow", "task", "auto"],
                    default="auto",
                    help="stage dispatch surface (R-0004): 'workflow' wraps "
                         "an evaluate/fix stage payload as ONE ready-to-run "
                         "stage-wave workflow invocation, 'task' prints "
                         "today's payload byte-identically (the mandatory "
                         "fallback and the only Codex path), 'auto' consults "
                         "workflow_available() (default)")
    lw = lsub.add_parser("wave", help="print the EXECUTE wave: one brief per scope-disjoint task")
    lw.add_argument("--emit", choices=["workflow", "task", "auto"],
                    default="auto",
                    help="stage dispatch surface (R-0004): 'workflow' wraps "
                         "the EXECUTE wave as ONE ready-to-run execute-wave "
                         "workflow invocation covering every wave entry, "
                         "'task' prints today's wave payload byte-identically "
                         "(the mandatory fallback and the only Codex path), "
                         "'auto' consults workflow_available() (default)")
    lc = lsub.add_parser("claim", help="a worker claims one wave task into its own worktree")
    lc.add_argument("task_id")
    lc.add_argument("--agent-workspace", required=True,
                    help="the worker's worktree — its contract activates there")
    lg = lsub.add_parser("gate", help="orchestrator-only: judge the evidence and advance the loop")
    lg.add_argument("outcome", choices=["pass", "fail"])
    lg.add_argument("--note", default="",
                    help="one-line note recorded with the gate decision")
    lg.add_argument("--task", help="task id (parallel execute waves)")
    lg.add_argument("--req", help="attach requirement R-id to the loop "
                    "before DoR evaluation (design anchor)")
    lsu = lsub.add_parser("submit", help="worker submits evidence without "
                            "transitioning state; the orchestrator gates")
    lsu.add_argument("outcome", choices=["pass", "fail"])
    lsu.add_argument("--note", default="",
                     help="one-line evidence note recorded with the "
                          "submission")
    lsu.add_argument("--task", help="task id (parallel execute waves)")
    ls_ = lsub.add_parser("select", help="A/B selection gate: pick the "
                          "variant that ships (or 'hybrid')")
    ls_.add_argument("choice", help="variant letter, task id, or 'hybrid'")
    ls_.add_argument("--note", help="the WHY — recorded to the KB")
    la = lsub.add_parser("approve", help="record a human approval at a checkpoint gate")
    la.add_argument("--by", default=None,
                    help="who approved and where (e.g. a Slack user + "
                         "quoted reply) — recorded in trace + KB")
    la.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    la.add_argument("--force", action="store_true",
                    help="pass a BLOCKED refinement gate anyway")
    lr = lsub.add_parser("resolve", help="resolve a blocked loop: retry, skip, defer or abort")
    lr.add_argument("decision", choices=["retry", "skip", "defer", "abort"])
    le = lsub.add_parser("evidence", help="assemble every mechanically-derivable "
                         "fact the evaluate gate will check (suite result, diff, "
                         "criteria, routed lenses, graph obligations) with the "
                         "judgment slots left empty for the evaluator to fill")
    le.add_argument("--task", help="task id (default: the loop's current task)")
    le.add_argument("--write", action="store_true",
                    help="also drop the skeleton at .eval/verdict.json when no "
                         "verdict is already there (never overwrites)")
    lsub.add_parser("status", help="show the loop's stage, tasks and gates")
    lsub.add_parser("retro", help="print the loop retrospective")
    lsub.add_parser("verify-dispatch", help="audit whether dispatched agents "
                    "used the models the briefs resolved (tier routing)")
    lp.set_defaults(fn=cmd_loop)

    ln = sub.add_parser("lens", help="route lenses for a change")
    lnsub = ln.add_subparsers(dest="lens_action", required=True)
    lnr = lnsub.add_parser("route", help="decide which lenses a change needs")
    lnr.add_argument("--base", default="HEAD", help="git base to diff against")
    lnr.add_argument("--task-type", help="declared task type (feature, "
                     "bugfix, refactor, ...) — widens the routed set")
    lnr.add_argument("--artifact-type",
                     help="route on an artifact instead of the diff — "
                          "'strategy' summons the advisory (board) tier")
    lnr.add_argument("--only", help="comma list — only these lenses")
    lnr.add_argument("--skip", help="comma list — skip these lenses")
    lnr.add_argument("--all", action="store_true", dest="breadth_all",
                     help="full catalog: routed lenses run deep, the rest "
                          "as a quick sweep — nothing skipped")
    lnr.add_argument("--json", action="store_true",
                     help="print the routing decision as JSON")
    lnr.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    lnr.set_defaults(fn=cmd_lens)

    lnl = lnsub.add_parser("list", help="every lens in the catalog")
    lnl.add_argument("--json", action="store_true",
                     help="print the catalog as JSON")
    lnl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    lnl.set_defaults(fn=cmd_lens)

    lns = lnsub.add_parser("show", help="the full brief for one lens")
    lns.add_argument("id")
    lns.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    lns.set_defaults(fn=cmd_lens)

    lnd = lnsub.add_parser("dispatch", help="ready-to-dispatch lens-agent "
                           "briefs — one read-only agent per deep lens, "
                           "fanned out in parallel")
    lnd.add_argument("--base", default="HEAD",
                     help="git base to diff against (default HEAD)")
    lnd.add_argument("--task-type", help="declared task type (feature, "
                     "bugfix, refactor, ...) — widens the routed set")
    lnd.add_argument("--only", help="comma list — dispatch only these "
                     "lenses")
    lnd.add_argument("--skip", help="comma list — do not dispatch these "
                     "lenses")
    lnd.add_argument("--all", action="store_true", dest="breadth_all",
                     help="full catalog: routed lenses run deep, the rest "
                          "as a quick sweep — nothing skipped")
    lnd.add_argument("--max-actions", type=int, default=30,
                     dest="max_actions",
                     help="per-agent action ceiling written into each "
                          "dispatched lens brief (default 30)")
    lnd.add_argument("--artifact-type",
                     help="route on an artifact instead of the diff — "
                          "'strategy' summons the advisory (board) tier")
    lnd.add_argument("--dashboard", action="store_true",
                     help="print the live lens-wave progress board instead "
                          "of the JSON briefs (render this BEFORE dispatch)")
    lnd.add_argument("--emit", choices=["workflow", "task", "auto"],
                     default="auto",
                     help="dispatch path: 'workflow' wraps the briefs as "
                          "/taskplane:review-wave args, 'task' prints "
                          "today's Task-dispatch payload byte-identically, "
                          "'auto' (default) picks workflow only when the "
                          "host runtime is detected (Codex: always task)")
    lnd.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    lnd.set_defaults(fn=cmd_lens)

    yl = sub.add_parser("yield", help="what the harness returns (lens yield "
                        "and where findings are caught) — advisory, gates "
                        "nothing")
    yl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    yl.add_argument("--json", action="store_true",
                    help="emit the raw report instead of the table")
    ylsub = yl.add_subparsers(dest="yield_action")
    ym = ylsub.add_parser("mark", help="record a human verdict on one "
                          "finding: acted or dismissed")
    ym.add_argument("finding", help="the finding fingerprint from `tp yield`")
    ym.add_argument("verdict", choices=["acted", "dismissed"])
    ym.add_argument("--by", help="who decided (attribution, like gates)")
    ym.add_argument("--note", default="", help="why, in one line")
    yl.set_defaults(fn=cmd_yield)

    kbp = sub.add_parser("kb", help="knowledge base (decisions)")
    kbp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    kbsub = kbp.add_subparsers(dest="kb_action", required=True)
    kr = kbsub.add_parser("record", help="record a decision in the knowledge base")
    kr.add_argument("title")
    kr.add_argument("--context", help="the situation the decision was "
                    "made in")
    kr.add_argument("--decision", help="what was decided, in one sentence")
    kr.add_argument("--rationale", help="why this option won")
    kr.add_argument("--tags", help="comma-separated tags for retrieval")
    kr.add_argument("--files", help="comma-separated context file globs")
    kt = kbsub.add_parser("retrieve", help="recall the decisions that govern given files or tags")
    kt.add_argument("--files", help="comma-separated file globs — retrieve "
                    "decisions that govern them")
    kt.add_argument("--tags", help="comma-separated tags to match")
    kt.add_argument("--limit", type=int, default=5,
                    help="most decisions to return (default 5)")
    kbsub.add_parser("list", help="list every recorded decision")
    kbsub.add_parser("lint", help="check the knowledge base for malformed or empty records")
    kbsub.add_parser("where", help="show the external store path for this "
                     "project (and whether a legacy in-repo KB remains)")
    kbsub.add_parser("migrate", help="move a legacy in-repo knowledge/ to the "
                     "external store, untrack it, and gitignore it")
    kbp.set_defaults(fn=cmd_kb)

    rq = sub.add_parser("req", help="requirements: record, refine, mode, debt")
    rq.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rqsub = rq.add_subparsers(dest="req_action", required=True)
    rn = rqsub.add_parser("new", help="record a requirement (or a change request)")
    rn.add_argument("title")
    rn.add_argument("--functional", action="append",
                    help="a functional statement (repeatable)")
    rn.add_argument("--nfr", action="append", metavar="LENS=STATEMENT",
                    help="a non-functional requirement by lens (repeatable)")
    rn.add_argument("--acceptance", action="append",
                    help="an acceptance criterion (repeatable)")
    rn.add_argument("--open", action="append",
                    help="an open question (repeatable)")
    rn.add_argument("--tags", help="comma-separated tags for retrieval")
    rn.add_argument("--files",
                    help="comma-separated context file globs")
    rn.add_argument("--changed-from", dest="changed_from",
                    help="R-id this change request derives from")
    rn.add_argument("--depends", action="append", metavar="R-XXXX",
                    help="R-id this requirement depends on (repeatable) — "
                         "recorded as a product edge in the graph")
    rn.add_argument("--contract", action="append",
                    metavar="RELATION:CONTRACT",
                    help="repeatable requirement boundary: provides, consumes, "
                         "or changes a named API/event/data/runtime contract")
    rs = rqsub.add_parser("score", help="score a requirement's refinement against the bar")
    rs.add_argument("id")
    rs.add_argument("--files", help="comma-separated changed-file globs")
    rs.add_argument("--task-type", help="declared task type — sets the "
                    "refinement bar this requirement is scored against")
    rs.add_argument("--threshold", type=float, default=0.6,
                    help="refinement score the requirement must reach "
                         "(default 0.6)")
    rs.add_argument("--high-cost", action="store_true",
                    help="hard-block below threshold (irreversible work)")
    rm = rqsub.add_parser("mode", help="pick the delivery mode for a refinement score and change size")
    rm.add_argument("--refinement", type=float, required=True,
                    help="the requirement's refinement score (0.0-1.0)")
    rm.add_argument("--size", type=int, required=True, help="files changed")
    rdb = rqsub.add_parser("debt", help="record technical debt taken on knowingly")
    rdb.add_argument("title")
    rdb.add_argument("--req", help="requirement id this debt belongs to")
    rdb.add_argument("--reason", help="why the debt was taken on")
    rdb.add_argument("--follow-up", dest="follow_up",
                     help="what would pay the debt off")
    rdb.add_argument("--tags", help="comma-separated tags for retrieval")
    rdb.add_argument("--files",
                     help="comma-separated file globs the debt lives in")
    rqsub.add_parser("list", help="list recorded requirements")
    rq.set_defaults(fn=cmd_req)

    gp = sub.add_parser("graph", help="dependency graph: scan, impact, "
                        "contracts, requirement links, visualization")
    gp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    gsub = gp.add_subparsers(dest="graph_action", required=True)
    gs = gsub.add_parser("scan", help="rebuild the dependency graph from the working tree")
    gs.add_argument("--decompose", action="store_true",
                    help="derive the component layer (graph.json "
                         "'components'; R-0003 contract:component-map)")
    gi = gsub.add_parser("impact", help="what a change reaches: blast radius across the graph")
    gi.add_argument("--files", help="comma-separated changed files "
                    "(default: git diff + untracked)")
    gi.add_argument("--base", default="HEAD",
                    help="git base to diff against (default HEAD)")
    gi.add_argument("--depth", type=int, default=3,
                    help="dependency hops to walk locally (default 3)")
    gi.add_argument("--boundary", choices=["contract-only", "stop", "expand"],
                    default="contract-only",
                    help="what the walk does at a distributed boundary "
                         "(default contract-only)")
    gi.add_argument("--contract-depth", type=int, default=1,
                    help="hops to keep walking past a contract boundary "
                         "(default 1)")
    gi.add_argument("--requirement-depth", type=int, default=1,
                    help="hops to walk into the requirement layer "
                         "(default 1)")
    gi.add_argument("--json", action="store_true",
                    help="print the impact set as JSON")
    ge = gsub.add_parser("edge", help="record an edge the scanner cannot see")
    ge.add_argument("src"); ge.add_argument("dst")
    ge.add_argument("--kind", default="runtime",
                    help="edge kind, e.g. runtime or build "
                         "(default runtime)")
    ge.add_argument("--note", help="why this edge exists")
    ge.add_argument("--confidence", choices=["high", "medium", "low"],
                    default="medium",
                    help="how sure the edge is (default medium)")
    gc = gsub.add_parser("contract", help="record an explicit distributed "
                         "boundary; consumers depend on the contract")
    gc.add_argument("name")
    gc.add_argument("--provider", help="module that provides the contract")
    gc.add_argument("--consumer", action="append",
                    help="module that consumes the contract (repeatable)")
    gl = gsub.add_parser("link", help="product layer: link a requirement "
                         "to the modules that plan/realize it")
    gl.add_argument("--req", required=True, metavar="R-XXXX",
                    help="the requirement being linked")
    gl.add_argument("--files", required=True,
                    help="comma-separated files or scope globs")
    gl.add_argument("--kind", default="realizes",
                    choices=["planned", "realizes"],
                    help="link kind: a planned or a realized requirement "
                         "(default realizes)")
    gl.add_argument("--keep", action="store_true",
                    help="append instead of replacing existing links")
    gh = gsub.add_parser("html", help="render the graph as a standalone HTML view")
    gh.add_argument("--files", help="comma-separated changed files to "
                    "highlight (default: git diff + untracked)")
    gh.add_argument("--base", default="HEAD",
                    help="git base to diff against (default HEAD)")
    gh.add_argument("--out",
                    help="write the HTML here instead of stdout")
    gp.set_defaults(fn=cmd_graph)

    db = sub.add_parser("dashboard", help="render the mission-control view")
    db.add_argument("--out", help="also write the fragment to this path")
    db.add_argument("--paged", action="store_true",
                    help="emit ordered <=14KB pages (JSON) for reliable "
                         "inline rendering + a never-skippable headline")
    db.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    db.set_defaults(fn=cmd_dashboard)

    op = sub.add_parser("onboard", help="cold-start readiness — folder + git "
                        "snapshot + init; renders the onboarding dashboard")
    op.add_argument("--json", action="store_true",
                    help="print the readiness report instead of the widget")
    op.add_argument("--out", help="also write the fragment to this path")
    op.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    op.set_defaults(fn=cmd_onboard)

    fp = sub.add_parser("findings", help="render a review findings dashboard "
                        "(all severities, filterable) from a findings JSON")
    fp.add_argument("--file", help="findings JSON (default "
                    ".em-review/findings.json)")
    fp.add_argument("--out", help="also write the fragment to this path")
    fp.add_argument("--paged", action="store_true",
                    help="emit ordered <=14KB pages (JSON) for reliable "
                         "inline rendering + a never-skippable headline")
    fp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    fp.set_defaults(fn=cmd_findings)

    nsp = sub.add_parser("north-star", help="on-demand strategic review: print "
                         "the project's north star, or render a strategic note")
    nsp.add_argument("--render", help="a strategic-note JSON to render as the "
                     "inline widget fragment")
    nsp.add_argument("--out", help="also write the fragment to this path")
    nsp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    nsp.set_defaults(fn=cmd_northstar)

    shp = sub.add_parser("share", help="plan-aware knowledge sharing: "
                         "status / plan / set private|shared / push")
    shsub = shp.add_subparsers(dest="share_cmd", required=True)
    sst = shsub.add_parser("status", help="show what is private and what is shared")
    sst.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    spl = shsub.add_parser("plan", help="set the knowledge-storage plan")
    spl.add_argument("value", choices=["personal", "team", "enterprise"])
    spl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    sse = shsub.add_parser("set", help="set the default visibility of new decisions")
    sse.add_argument("value", choices=["private", "shared"])
    sse.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    spu = shsub.add_parser("push", help="publish private decisions to the shared store")
    spu.add_argument("--ids", default=None,
                     help="comma-separated private decision ids; default = "
                          "everything unpublished")
    spu.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    shp.set_defaults(fn=cmd_share)

    ip = sub.add_parser("init", help="scaffold context docs + KB + graph")
    ip.add_argument("--plan", choices=["personal", "team", "enterprise"],
                    default=None, help="choose knowledge storage at init — "
                    "personal is private/external; team/enterprise is "
                    "shared in-repo")
    ip.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    ip.set_defaults(fn=cmd_init)

    tk = sub.add_parser("track", help="multi-track workstreams")
    tk.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tksub = tk.add_subparsers(dest="track_action", required=True)
    tn = tksub.add_parser("new", help="open a new track")
    tn.add_argument("name")
    tn.add_argument("goal", nargs="*")
    tn.add_argument("--req", help="requirement R-id this track delivers")
    tksub.add_parser("list", help="list every track")
    tsw = tksub.add_parser("switch", help="make another track the active one")
    tsw.add_argument("name")
    tcl = tksub.add_parser("close", help="close a track")
    tcl.add_argument("name")
    tcl.add_argument("--status", default="done",
                     help="status to close the track in (default done)")
    tk.set_defaults(fn=cmd_track)

    cx = sub.add_parser("context", help="session-start context summary")
    cx.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    cx.set_defaults(fn=cmd_context)

    us = sub.add_parser("summary", help="simple human view: progress and "
                        "decisions, while agents keep the detailed harness")
    us.add_argument("--json", action="store_true",
                    help="print the summary as JSON")
    us.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    us.set_defaults(fn=cmd_summary)

    hp = sub.add_parser("help", help="print this help; with --md, the "
                        "generated markdown CLI reference "
                        "(docs/cli-reference.md)")
    hp.add_argument("--md", action="store_true",
                    help="print the deterministic markdown CLI "
                         "reference walked from the live argparse "
                         "tree, instead of argparse's own help")
    hp.set_defaults(fn=cmd_help, root_parser=p)

    vp = sub.add_parser("version", help="print the plugin version; "
                        "--verify cross-checks every derived version "
                        "surface against the single source "
                        "(.codex-plugin/plugin.json) — CI-callable, "
                        "exit 1 on drift")
    vp.add_argument("--verify", action="store_true",
                    help="cross-check every derived version surface "
                         "against the single source; exit 1 on drift")
    vp.set_defaults(fn=cmd_version)

    a = p.parse_args(argv)
    if not hasattr(a, "workspace"):
        a.workspace = None      # SUPPRESS leaves it unset when never passed
    # USER-LAYER ERROR BOUNDARY. The "simple front" translates raw
    # tracebacks into governed messages — but it NEVER swallows: a failure
    # stays a FAILURE (nonzero exit) and the full detail stays available.
    #   - tp.StateError: a GOVERNED failure — the message already carries
    #     the path, the why, and the remedy. One clean line, exit 1.
    #   - missing git binary: the documented prerequisite — the remedy line,
    #     exit 1.
    #   - anything UNEXPECTED: a short reason line PLUS the full traceback
    #     on stderr (detail preserved, never hidden), exit 70 (EX_SOFTWARE)
    #     so drivers can tell an internal fault from a governed refusal
    #     (exit 1) and from success (exit 0).
    # TASKPLANE_DEBUG=1 re-raises for interactive debugging. (cmd_screen
    # keeps its own inner fail-closed boundary — the hook protocol needs a
    # block decision on stdout, which this boundary preserves by never
    # reaching it.)
    try:
        return a.fn(a)
    except BrokenPipeError:
        raise                    # handled at the __main__ boundary below
    except Exception as exc:     # noqa: BLE001 — the user-layer boundary
        if os.environ.get("TASKPLANE_DEBUG"):
            raise                # operator asked for the raw exception
        if isinstance(exc, FileNotFoundError) and \
                getattr(exc, "filename", None) == "git":
            # git is the load-bearing external tool of the whole design;
            # its absence gets the documented remedy, not a stack trace.
            print(f"taskplane: {a.cmd} failed: git is required — install "
                  "git and re-run (see README prerequisites).",
                  file=sys.stderr)
            return 1
        if isinstance(exc, tp.StateError):
            # StateError already carries the path, the why, and the remedy.
            print(f"taskplane: {a.cmd} failed: {exc}", file=sys.stderr)
            print("  (set TASKPLANE_DEBUG=1 for the full traceback)",
                  file=sys.stderr)
            return 1
        # Unexpected: short reason first, then the FULL traceback — the
        # boundary governs the message, it never destroys the evidence.
        print(f"taskplane: {a.cmd} failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 70


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Downstream (head/less) closed the pipe — exit quietly instead of
        # dumping a traceback. Redirect stdout to devnull so the interpreter's
        # final flush doesn't re-raise. (v1.5.2)
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
