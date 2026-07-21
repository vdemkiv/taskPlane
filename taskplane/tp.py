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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taskplane_lite as tp  # noqa: E402


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
        text = open(p, encoding="utf-8").read()
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


def _git_head(ws: str) -> str | None:
    r = tp._run(["git", "rev-parse", "HEAD"], cwd=ws)
    return r.stdout.strip() or None


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
    if ws not in (home, "/", "/root", "/home/claude"):
        return False
    inside_git = tp._run(["git", "rev-parse", "--is-inside-work-tree"],
                         cwd=ws).stdout.strip() == "true"
    return not (inside_git and _is_commit_sha(_git_head(ws)))


def _onboard_report(ws: str) -> dict:
    """Cold-start readiness: does the workspace have the three things a
    governed run needs — a real folder to work in, a git repo with a
    snapshot (gates fail closed without one), and taskplane initialized.
    Returns a checklist + the single next action, so the onboarding UI can
    walk a brand-new user in from a zero state (no folder, no repo)."""
    inside_git = tp._run(["git", "rev-parse", "--is-inside-work-tree"],
                         cwd=ws).stdout.strip() == "true"
    head = _git_head(ws)
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
    looks_like_project = has_files and not bare_root
    has_context = os.path.isdir(os.path.join(tp.kb_root(ws), "context"))

    checks = [
        {"id": "workspace", "label": "A folder to work in",
         "ok": looks_like_project,
         "detail": ws if looks_like_project else
         f"{ws} — looks empty or scratch",
         "hint": "Connect a folder (Cowork: attach a folder; Code: open your "
                 "project) — or use this one if it's where you want to work."},
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
    return {"workspace": ws, "looks_like_project": looks_like_project,
            "is_git": inside_git, "has_commit": has_commit,
            "has_context": has_context, "ready": ready,
            "checks": checks, "next_action": nxt,
            # Resolved model routing, visible at cold start: with defaults
            # only `cheap` pins a model — standard/deep inherit the session
            # model until TASKPLANE_MODEL_<TIER> is set (discipline/
            # model-tiers.md). Surfacing it here is what makes the routing
            # discoverable instead of a silent no-op.
            "model_tiers": {t: (tp.model_for_tier(t) or "inherit")
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
                     if getattr(a, "max_actions", None) else None),
    )
    # cooperative dollar advisory (kept on the shared shape as an optional key)
    c["budget"]["max_cost_usd"] = float(a.budget) if a.budget \
        else DEFAULT_MAX_COST_USD

    snapshot = _git_head(ws)
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
    and fail-open — inert unless TASKPLANE_ENFORCE_DISPATCH=warn|strict.
    warn: allow + a visible correction message. strict: deny with the same
    message so the driver re-dispatches with the right model."""
    mode = (os.environ.get("TASKPLANE_ENFORCE_DISPATCH") or "").strip().lower()
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if mode not in ("warn", "strict"):
        return 0                                   # opt-in: default inert
    try:
        ti = event.get("tool_input") or {}
        agent = (ti.get("subagent_type") or "")
        model = ti.get("model")
        ws = _workspace(event.get("cwd"))
        exp = tp.consume_expectation(ws, agent)
        expected_model = exp and exp.get("model")
        ok = (exp is None) or (expected_model is None) \
            or (model == expected_model)
        tp.record_observed_dispatch(ws, agent, model, exp, ok)
        if ok:
            return 0
        reason = (f"taskplane dispatch check: the {exp['kind']} brief "
                  f"'{exp.get('ref') or exp['agent']}' resolved "
                  f"model={expected_model} (tier {exp['model_tier']}) but "
                  f"this agent was dispatched with "
                  f"model={model or '<inherit session model>'} — pass "
                  f"model=\"{expected_model}\" to the Agent tool.")
        if mode == "strict":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        else:
            print(json.dumps({"systemMessage": reason}))
        return 0
    except Exception:
        return 0                                   # never break dispatch


def cmd_clear(a) -> int:
    """Deactivate the workspace contract (e.g. when a review ends), so the
    enforcement hook stops governing subsequent work."""
    ws = _workspace(a.workspace)
    path = os.path.join(tp.tp_dir(ws), "active_contract.json")
    if os.path.exists(path):
        c = tp.load_active(ws) or {}
        tp.clear(ws)                      # FUSE-safe removal (safe_remove)
        print(f"taskplane: contract {c.get('task_id','')} cleared — "
              "workspace is ungoverned again.")
    else:
        print("taskplane: no active contract to clear.")
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
        snapshot = open(snap_path).read().strip() or None
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
            return json.load(open(p))
        except (ValueError, OSError):
            if strict:
                raise MeterCorrupt(p)
    return {}


def _meter_bump(ws, task_id, key) -> dict:
    import time
    now = time.time()
    try:
        m = _meter_load(ws, strict=True)
    except MeterCorrupt:
        m = {}                      # bumping rebuilds a clean file atomically
    e = m.setdefault(task_id, {"actions": 0, "denies": 0})
    e[key] = e.get(key, 0) + 1
    # last_seen_ts = the owner was alive AT ALL (any screen call, approve or
    # deny) — used by the orphan idle-backstop to tell a crashed owner (no
    # calls) from a live one. last_action_ts = last APPROVED action.
    e["last_seen_ts"] = now
    if key == "actions":
        e["last_action_ts"] = now
    d = tp.tp_dir(ws)
    os.makedirs(d, exist_ok=True)
    # Atomic write so a concurrent reader never sees a torn file.
    path = os.path.join(d, "meter.json")
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(m, f)
    os.replace(tmp, path)
    return e


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
    home = os.path.abspath(os.path.expanduser("~"))
    # git worktree/repo top of cwd — the walk must not climb past it.
    top = tp._run(["git", "rev-parse", "--show-toplevel"], cwd=start).stdout.strip()
    top = os.path.abspath(top) if top else None
    cur = start
    while True:
        if os.path.exists(os.path.join(tp.tp_dir(cur), "active_contract.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur or cur == home or cur == top:
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
        snapshot = open(snap_path).read().strip() or None

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
    if action == "init":
        checkpoints = (a.checkpoints.split(",") if a.checkpoints is not None
                       else ["plan", "em"])
        st = loopmod.init(ws, " ".join(a.goal or []) or (a.spec or "spec"),
                          spec_path=a.spec, max_fix_cycles=a.max_fix_cycles,
                          checkpoints=[c for c in checkpoints if c],
                          requirement_id=a.req, parallel=a.parallel)
        print(json.dumps({"initialized": True, "step": st["step"]}, indent=2))
    elif action == "next":
        print(json.dumps(loopmod.next_action(ws), indent=2))
    elif action == "gate":
        print(json.dumps(loopmod.gate(ws, a.outcome, note=a.note or "",
                                      task_id=a.task), indent=2))
    elif action == "wave":
        print(json.dumps(loopmod.wave(ws), indent=2))
    elif action == "claim":
        print(json.dumps(loopmod.claim(ws, a.task_id, a.agent_workspace),
                         indent=2))
    elif action == "approve":
        print(json.dumps(loopmod.approve(ws, force=a.force), indent=2))
    elif action == "select":
        print(json.dumps(loopmod.select(ws, a.choice, note=a.note or ""),
                         indent=2))
    elif action == "resolve":
        print(json.dumps(loopmod.resolve(ws, a.decision), indent=2))
    elif action == "status":
        print(json.dumps(loopmod.status(ws), indent=2))
    elif action == "retro":
        print(json.dumps(loopmod.retro(ws), indent=2))
    elif action == "verify-dispatch":
        rep = tp.dispatch_report(ws)
        print(json.dumps(rep, indent=2))
        return 1 if rep["mismatches"] else 0
    return 0


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
        briefs = lensmod.dispatch_briefs(routing, base=a.base,
                                         max_actions=a.max_actions)
        for b in briefs.get("deep") or []:
            tp.record_expected_dispatch(ws, "lens", b.get("agent", "tp-lens"),
                                        b.get("model_tier", "standard"),
                                        b.get("model"), ref=b.get("id"))
        sw = briefs.get("sweep")
        if sw:
            tp.record_expected_dispatch(ws, "lens",
                                        sw.get("agent", "tp-lens"),
                                        sw.get("model_tier", "cheap"),
                                        sw.get("model"), ref="sweep")
        if getattr(a, "dashboard", False):
            import dashboard
            lanes = [{"id": b["id"], "name": b["name"], "status": "running",
                      "findings": None} for b in briefs["deep"]]
            if briefs["sweep"]:
                lanes.append({"id": "sweep", "name": "sweep", "status":
                              "running", "findings": None})
            print(dashboard.render_lens_wave(
                lanes, {"title": "review — lenses running",
                        "subtitle": f"{len(lanes)} read-only lens-agents, "
                        f"in parallel · diff vs {briefs['base']}"}))
            return 0
        print(json.dumps(briefs, indent=2))
        return 0

    if a.json:
        print(json.dumps(routing, indent=2))
    else:
        print(lensmod.render(routing))
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
        e = req.record_requirement(
            ws, a.title,
            functional=(a.functional or None),
            nfr=nfr,
            acceptance=(a.acceptance or None),
            open_questions=(a.open or None),
            tags=(a.tags.split(",") if a.tags else None),
            context_files=(a.files.split(",") if a.files else None),
            changed_from=a.changed_from)
        # Product dependencies land in the graph immediately — a change
        # request also gets a depends edge to its origin requirement.
        deps = list(a.depends or [])
        if a.changed_from:
            deps.append(a.changed_from)
        if deps:
            import depgraph as dg
            for d in deps:
                dg.link_requirement_dep(ws, e["id"], d)
        print(json.dumps({"recorded": e["id"], "status": e["status"],
                          "depends": deps or None,
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
        with open(gi_path) as f:
            existing = f.read()
    missing = [e for e in entries if e not in existing]
    if missing:
        with open(gi_path, "a") as f:
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
    ignored = _ensure_gitignored(
        ws, ["knowledge/"],
        "taskplane knowledge base — lives in the external store "
        "(~/.taskplane), never the repo")
    res.update({"untracked": was_tracked, "gitignored": bool(ignored)})
    return res


def cmd_init(a) -> int:
    """Scaffold a project for governed work: context docs, KB dirs, graph.
    The knowledge base lives in the EXTERNAL per-project store (~/.taskplane),
    not the repo — any legacy in-repo knowledge/ is migrated out here."""
    import depgraph as dg
    ws = _workspace(a.workspace)
    mig = _migrate_kb(ws)                 # relocate + untrack + gitignore
    store = tp.store_root(ws)
    ctx = os.path.join(tp.kb_root(ws), "context")
    os.makedirs(ctx, exist_ok=True)
    wrote = []
    for name, body in (("product.md", PRODUCT_MD),
                       ("tech-stack.md", TECH_MD),
                       ("workflow.md", WORKFLOW_MD)):
        p = os.path.join(ctx, name)
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(body)
            wrote.append(f"context/{name}")
    # Runtime paths that stay LOCAL to the checkout — never committed.
    missing = _ensure_gitignored(
        ws, [".taskplane/", ".eval/", ".em-review/", ".security-review/",
             ".tp-work/"],
        "taskplane runtime (local-only — see docs/state-spec.md)")
    g = dg.scan(ws)
    head = _git_head(ws)
    if head and not _is_commit_sha(head):
        head = None    # empty repo: rev-parse echoes "HEAD"
    tp.trace(ws, "project_init", context_docs=wrote,
             graph_modules=len(g["modules"]), store=store,
             migrated=mig.get("moved"))
    print(json.dumps({
        "knowledge_store": store,
        "migrated_from_repo": mig.get("moved") or False,
        "context_docs_created": wrote or "(already present)",
        "graph": {"modules": len(g["modules"]), "edges": len(g["edges"])},
        "gitignored_runtime": missing or "(already present)",
        "committed_state": "NONE — the knowledge base is external "
                           "(~/.taskplane); the repo carries no taskplane "
                           "artifacts",
        "git": head[:12] if head else "NOT A REPO — git init && commit "
                                      "(gates need a snapshot)",
        "next": "fill the context docs in the store, then state a goal via "
                "the tp-go skill (or `tp.py req new` + `tp.py loop init`)",
    }, indent=2))
    return 0


def cmd_track(a) -> int:
    """Multiple workstreams over one engine; shared KB/graph across tracks."""
    import track as tr
    ws = _workspace(a.workspace)
    if a.track_action == "new":
        print(json.dumps(tr.new(ws, a.name, " ".join(a.goal or []) or a.name,
                                requirement_id=a.req), indent=2))
    elif a.track_action == "list":
        print(json.dumps(tr.list_(ws), indent=2))
    elif a.track_action == "switch":
        print(json.dumps(tr.switch(ws, a.name), indent=2))
    elif a.track_action == "close":
        print(json.dumps(tr.close(ws, a.name, status=a.status), indent=2))
    return 0


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
        # Ungoverned workspace. In a code repo, offer the one-line on-ramp;
        # anywhere else stay completely silent (no noise in random folders).
        if os.path.isdir(os.path.join(ws, ".git")):
            print("[taskplane] installed, this repo isn't governed yet — "
                  "say \"set up taskplane\" to onboard it, or \"taskplane "
                  "help\" for the tour.")
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


def cmd_dashboard(a) -> int:
    """Emit the mission-control view. Default: the inline widget fragment
    for mcp__visualize__show_widget (the driver pipes it straight in).
    --out also writes a standalone HTML file (no-desktop fallback)."""
    import dashboard
    ws = _workspace(a.workspace)
    if a.out:
        dashboard.render(ws, out=a.out)
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
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"taskplane: cannot read findings {path}: {e}", file=sys.stderr)
        return 1
    findings = data.get("findings", data) if isinstance(data, dict) else data
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
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
            with open(a.render) as f:
                note = json.load(f)
        except (OSError, ValueError) as e:
            print(f"taskplane: cannot read note {a.render}: {e}",
                  file=sys.stderr)
            return 1
        note.setdefault("north_star", north_star(ws))
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
        g = dg.scan(ws)
        print(json.dumps({"modules": len(g["modules"]),
                          "edges": len(g["edges"]),
                          "files": len(g["files"]),
                          "stored": os.path.join(tp.kb_root(ws),
                                                 "graph.json")}, indent=2))
    elif a.graph_action == "impact":
        files = (a.files.split(",") if a.files else
                 _changed_for_impact(ws, a.base))
        imp = dg.impact(ws, files, max_depth=a.depth)
        prod = dg.product_impact(ws, files)
        imp["affected_requirements"] = prod["affected_requirements"]
        imp["dependent_requirements"] = prod["dependent_requirements"]
        print(dg.render_context(imp) or "no modules touched.")
        if a.json:
            print(json.dumps(imp, indent=2))
    elif a.graph_action == "edge":
        e = dg.record_edge(ws, a.src, a.dst, kind=a.kind, note=a.note or "")
        print(json.dumps({"recorded": e}, indent=2))
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
    import subprocess
    r = subprocess.run(["git", "diff", "--name-only", base or "HEAD"],
                       cwd=ws, capture_output=True, text=True)
    u = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                       cwd=ws, capture_output=True, text=True)
    return [f for f in (r.stdout + u.stdout).splitlines() if f]


def main() -> int:
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
    n.add_argument("--workspace")
    n.set_defaults(fn=cmd_new)

    s = sub.add_parser("screen", help="PreToolUse hook entrypoint (stdin event)")
    s.set_defaults(fn=cmd_screen)

    sd = sub.add_parser("screen-dispatch", help="PreToolUse hook for the "
                        "Agent tool: verify tier-routed model was passed "
                        "(inert unless TASKPLANE_ENFORCE_DISPATCH=warn|strict)")
    sd.set_defaults(fn=cmd_screen_dispatch)

    rd = sub.add_parser("ready", help="Definition-of-Ready entry gate")
    rd.add_argument("--workspace")
    rd.set_defaults(fn=cmd_ready)

    cl = sub.add_parser("clear", help="deactivate the workspace contract")
    cl.add_argument("--workspace")
    cl.set_defaults(fn=cmd_clear)

    st = sub.add_parser("status", help="show the active contract")
    st.add_argument("--workspace")
    st.set_defaults(fn=cmd_status)

    b = sub.add_parser("budget", help="record a cooperative spend estimate, "
                       "or --grant N more actions (the budget approval gate)")
    b.add_argument("--spent", type=float,
                   help="cooperative $ estimate (advisory)")
    b.add_argument("--grant", type=int, metavar="N",
                   help="raise the enforced action ceiling by N — for the "
                        "human / ungoverned main session after approving "
                        "more budget (a governed agent cannot grant itself)")
    b.add_argument("--workspace")
    b.set_defaults(fn=cmd_budget)

    d = sub.add_parser("dod", help="Definition-of-Done exit gate (+ kb lint)")
    d.add_argument("--workspace")
    d.set_defaults(fn=cmd_dod)

    lp = sub.add_parser("loop", help="drive the Evaluate-Loop engine")
    lp.add_argument("--workspace")
    lsub = lp.add_subparsers(dest="loop_action", required=True)
    li = lsub.add_parser("init")
    li.add_argument("goal", nargs="*")
    li.add_argument("--spec", help="path to an existing spec (skips PM)")
    li.add_argument("--max-fix-cycles", type=int, default=2)
    li.add_argument("--checkpoints", help="comma list: plan,em (default both)")
    li.add_argument("--req", help="anchor the loop to a requirement R-id")
    li.add_argument("--parallel", action="store_true",
                    help="execute waves of scope-disjoint tasks concurrently, "
                         "one governed agent per task")
    lsub.add_parser("next")
    lsub.add_parser("wave")
    lc = lsub.add_parser("claim")
    lc.add_argument("task_id")
    lc.add_argument("--agent-workspace", required=True,
                    help="the worker's worktree — its contract activates there")
    lg = lsub.add_parser("gate")
    lg.add_argument("outcome", choices=["pass", "fail"])
    lg.add_argument("--note", default="")
    lg.add_argument("--task", help="task id (parallel execute waves)")
    ls_ = lsub.add_parser("select", help="A/B selection gate: pick the "
                          "variant that ships (or 'hybrid')")
    ls_.add_argument("choice", help="variant letter, task id, or 'hybrid'")
    ls_.add_argument("--note", help="the WHY — recorded to the KB")
    la = lsub.add_parser("approve")
    la.add_argument("--force", action="store_true",
                    help="pass a BLOCKED refinement gate anyway")
    lr = lsub.add_parser("resolve")
    lr.add_argument("decision", choices=["retry", "skip", "defer", "abort"])
    lsub.add_parser("status")
    lsub.add_parser("retro")
    lsub.add_parser("verify-dispatch", help="audit whether dispatched agents "
                    "used the models the briefs resolved (tier routing)")
    lp.set_defaults(fn=cmd_loop)

    ln = sub.add_parser("lens", help="route lenses for a change")
    lnsub = ln.add_subparsers(dest="lens_action", required=True)
    lnr = lnsub.add_parser("route")
    lnr.add_argument("--base", default="HEAD", help="git base to diff against")
    lnr.add_argument("--task-type")
    lnr.add_argument("--artifact-type",
                     help="route on an artifact instead of the diff — "
                          "'strategy' summons the advisory (board) tier")
    lnr.add_argument("--only", help="comma list — only these lenses")
    lnr.add_argument("--skip", help="comma list — skip these lenses")
    lnr.add_argument("--all", action="store_true", dest="breadth_all",
                     help="full catalog: routed lenses run deep, the rest "
                          "as a quick sweep — nothing skipped")
    lnr.add_argument("--json", action="store_true")
    lnr.add_argument("--workspace")
    lnr.set_defaults(fn=cmd_lens)

    lnl = lnsub.add_parser("list", help="every lens in the catalog")
    lnl.add_argument("--json", action="store_true")
    lnl.add_argument("--workspace")
    lnl.set_defaults(fn=cmd_lens)

    lns = lnsub.add_parser("show", help="the full brief for one lens")
    lns.add_argument("id")
    lns.add_argument("--workspace")
    lns.set_defaults(fn=cmd_lens)

    lnd = lnsub.add_parser("dispatch", help="ready-to-dispatch lens-agent "
                           "briefs — one read-only agent per deep lens, "
                           "fanned out in parallel")
    lnd.add_argument("--base", default="HEAD")
    lnd.add_argument("--task-type")
    lnd.add_argument("--only"); lnd.add_argument("--skip")
    lnd.add_argument("--all", action="store_true", dest="breadth_all")
    lnd.add_argument("--max-actions", type=int, default=30, dest="max_actions")
    lnd.add_argument("--artifact-type")
    lnd.add_argument("--dashboard", action="store_true",
                     help="print the live lens-wave progress board instead "
                          "of the JSON briefs (render this BEFORE dispatch)")
    lnd.add_argument("--workspace")
    lnd.set_defaults(fn=cmd_lens)

    kbp = sub.add_parser("kb", help="knowledge base (decisions)")
    kbp.add_argument("--workspace")
    kbsub = kbp.add_subparsers(dest="kb_action", required=True)
    kr = kbsub.add_parser("record")
    kr.add_argument("title")
    kr.add_argument("--context"); kr.add_argument("--decision")
    kr.add_argument("--rationale"); kr.add_argument("--tags")
    kr.add_argument("--files", help="comma-separated context file globs")
    kt = kbsub.add_parser("retrieve")
    kt.add_argument("--files"); kt.add_argument("--tags")
    kt.add_argument("--limit", type=int, default=5)
    kbsub.add_parser("list")
    kbsub.add_parser("lint")
    kbsub.add_parser("where", help="show the external store path for this "
                     "project (and whether a legacy in-repo KB remains)")
    kbsub.add_parser("migrate", help="move a legacy in-repo knowledge/ to the "
                     "external store, untrack it, and gitignore it")
    kbp.set_defaults(fn=cmd_kb)

    rq = sub.add_parser("req", help="requirements: record, refine, mode, debt")
    rq.add_argument("--workspace")
    rqsub = rq.add_subparsers(dest="req_action", required=True)
    rn = rqsub.add_parser("new")
    rn.add_argument("title")
    rn.add_argument("--functional", action="append",
                    help="a functional statement (repeatable)")
    rn.add_argument("--nfr", action="append", metavar="LENS=STATEMENT",
                    help="a non-functional requirement by lens (repeatable)")
    rn.add_argument("--acceptance", action="append",
                    help="an acceptance criterion (repeatable)")
    rn.add_argument("--open", action="append",
                    help="an open question (repeatable)")
    rn.add_argument("--tags"); rn.add_argument("--files",
                    help="comma-separated context file globs")
    rn.add_argument("--changed-from", dest="changed_from",
                    help="R-id this change request derives from")
    rn.add_argument("--depends", action="append", metavar="R-XXXX",
                    help="R-id this requirement depends on (repeatable) — "
                         "recorded as a product edge in the graph")
    rs = rqsub.add_parser("score")
    rs.add_argument("id")
    rs.add_argument("--files", help="comma-separated changed-file globs")
    rs.add_argument("--task-type")
    rs.add_argument("--threshold", type=float, default=0.6)
    rs.add_argument("--high-cost", action="store_true",
                    help="hard-block below threshold (irreversible work)")
    rm = rqsub.add_parser("mode")
    rm.add_argument("--refinement", type=float, required=True)
    rm.add_argument("--size", type=int, required=True, help="files changed")
    rdb = rqsub.add_parser("debt")
    rdb.add_argument("title")
    rdb.add_argument("--req", help="requirement id this debt belongs to")
    rdb.add_argument("--reason"); rdb.add_argument("--follow-up",
                     dest="follow_up")
    rdb.add_argument("--tags"); rdb.add_argument("--files")
    rqsub.add_parser("list")
    rq.set_defaults(fn=cmd_req)

    gp = sub.add_parser("graph", help="dependency graph: scan/impact/edge/html")
    gp.add_argument("--workspace")
    gsub = gp.add_subparsers(dest="graph_action", required=True)
    gsub.add_parser("scan")
    gi = gsub.add_parser("impact")
    gi.add_argument("--files", help="comma-separated changed files "
                    "(default: git diff + untracked)")
    gi.add_argument("--base", default="HEAD")
    gi.add_argument("--depth", type=int, default=3)
    gi.add_argument("--json", action="store_true")
    ge = gsub.add_parser("edge")
    ge.add_argument("src"); ge.add_argument("dst")
    ge.add_argument("--kind", default="runtime")
    ge.add_argument("--note")
    gl = gsub.add_parser("link", help="product layer: link a requirement "
                         "to the modules that plan/realize it")
    gl.add_argument("--req", required=True, metavar="R-XXXX")
    gl.add_argument("--files", required=True,
                    help="comma-separated files or scope globs")
    gl.add_argument("--kind", default="realizes",
                    choices=["planned", "realizes"])
    gl.add_argument("--keep", action="store_true",
                    help="append instead of replacing existing links")
    gh = gsub.add_parser("html")
    gh.add_argument("--files"); gh.add_argument("--base", default="HEAD")
    gh.add_argument("--out")
    gp.set_defaults(fn=cmd_graph)

    db = sub.add_parser("dashboard", help="render the mission-control view")
    db.add_argument("--out")
    db.add_argument("--workspace")
    db.set_defaults(fn=cmd_dashboard)

    op = sub.add_parser("onboard", help="cold-start readiness — folder + git "
                        "snapshot + init; renders the onboarding dashboard")
    op.add_argument("--json", action="store_true",
                    help="print the readiness report instead of the widget")
    op.add_argument("--out", help="also write the fragment to this path")
    op.add_argument("--workspace")
    op.set_defaults(fn=cmd_onboard)

    fp = sub.add_parser("findings", help="render a review findings dashboard "
                        "(all severities, filterable) from a findings JSON")
    fp.add_argument("--file", help="findings JSON (default "
                    ".em-review/findings.json)")
    fp.add_argument("--out", help="also write the fragment to this path")
    fp.add_argument("--workspace")
    fp.set_defaults(fn=cmd_findings)

    nsp = sub.add_parser("north-star", help="on-demand strategic review: print "
                         "the project's north star, or render a strategic note")
    nsp.add_argument("--render", help="a strategic-note JSON to render as the "
                     "inline widget fragment")
    nsp.add_argument("--out", help="also write the fragment to this path")
    nsp.add_argument("--workspace")
    nsp.set_defaults(fn=cmd_northstar)

    ip = sub.add_parser("init", help="scaffold context docs + KB + graph")
    ip.add_argument("--workspace")
    ip.set_defaults(fn=cmd_init)

    tk = sub.add_parser("track", help="multi-track workstreams")
    tk.add_argument("--workspace")
    tksub = tk.add_subparsers(dest="track_action", required=True)
    tn = tksub.add_parser("new"); tn.add_argument("name")
    tn.add_argument("goal", nargs="*"); tn.add_argument("--req")
    tksub.add_parser("list")
    tsw = tksub.add_parser("switch"); tsw.add_argument("name")
    tcl = tksub.add_parser("close"); tcl.add_argument("name")
    tcl.add_argument("--status", default="done")
    tk.set_defaults(fn=cmd_track)

    cx = sub.add_parser("context", help="session-start context summary")
    cx.add_argument("--workspace")
    cx.set_defaults(fn=cmd_context)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
