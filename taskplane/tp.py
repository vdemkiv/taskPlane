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

import os
import sys


_MINIMUM_PYTHON = (3, 10)


def _enforce_supported_python(version_info=None) -> None:
    """Refuse unsupported interpreters before imports or repository state."""
    version = version_info if version_info is not None else sys.version_info
    if tuple(version[:2]) >= _MINIMUM_PYTHON:
        return
    found = ".".join(str(part) for part in tuple(version[:3]))
    print("taskplane requires Python 3.10 or newer; found Python " + found,
          file=sys.stderr)
    raise SystemExit(2)


_enforce_supported_python()

import argparse
import ast
import contextlib
import hashlib
import io
import json
import re
import time as _time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taskplane_lite as tp  # noqa: E402
import host_capabilities as host_caps  # noqa: E402
import enforcement as enforcement_kernel  # noqa: E402
import collision as collision_kernel  # noqa: E402


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
            "install (Codex host, personal or team): taskplane ships "
            "as an OpenAI marketplace "
            "package — install/update it with the Codex plugin tooling "
            "(`codex plugin` in the CLI, or the desktop app's plugin "
            "catalog). See README > Quickstart: Codex.",
            "A managed Codex member cannot override organization policy; "
            "ask a Codex admin to make taskplane available when the catalog "
            "or hook policy blocks it.",
            "The Claude org-admin/marketplace paths do not apply on this host.",
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


_CODEX_HOOK_CONFIG = os.path.join(".codex", "hooks.json")
_CODEX_HOOK_RUNNER = os.path.join(".taskplane", "codex-hook.py")
_CODEX_HOOK_MARKER = ".taskplane/codex-hook.py"
_PLUGIN_MANIFEST = os.path.join(".codex-plugin", "plugin.json")
_PLUGIN_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _codex_hooks_report(ws: str) -> dict:
    """Mechanical configuration state of the repo-local Codex hook bridge.

    A matching file and runner prove configuration, not that the current host
    session loaded either.  `_onboard_report` obtains runtime truth from the
    separate HostCapabilitySnapshot authority.
    """
    config_path = os.path.join(ws, _CODEX_HOOK_CONFIG)
    runner_path = os.path.join(ws, _CODEX_HOOK_RUNNER)
    try:
        config = tp.load_json(config_path, default=None,
                              what="Codex hook configuration")
        encoded = json.dumps(config, sort_keys=True) if isinstance(config, dict) else ""
        with open(runner_path, encoding="utf-8") as handle:
            runner_body = handle.read()
    except Exception as exc:
        return {"ok": False, "status": "missing", "path": config_path,
                "reason": str(exc)}
    configured = _CODEX_HOOK_MARKER in encoded
    family = _codex_runner_family(runner_body)
    if family:
        installed_engine = _resolve_taskplane_engine(family)
    else:
        legacy = _codex_runner_engine(runner_body)
        current = os.path.normcase(os.path.abspath(__file__))
        installed_engine = (legacy if legacy and os.path.normcase(
            os.path.abspath(legacy)) == current else None)
    runner = bool(installed_engine and os.path.isfile(installed_engine))
    return {
        "ok": bool(configured and runner),
        "status": "ready" if configured and runner else "stale",
        "config": config_path, "runner": runner_path,
        "resolved_engine": installed_engine,
        "hint": (None if configured and runner else
                 "Run `tp onboard --install-codex-hooks --json`. A new "
                 "Codex task is needed only when workspace hooks have not "
                 "been loaded before; version refreshes use the stable runner."),
    }


def _existing_loop_step(ws: str) -> str | None:
    """Return the bounded current loop step without making it hook truth.

    An existing loop proves continuation context, not that Codex hooks are
    live.  Onboarding uses this only to expose the already-supported,
    attributable advisory path instead of demanding a restart.
    """
    try:
        import loop as loop_runtime
        state = loop_runtime.load(ws)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    step = str(state.get("step") or "").strip()
    return step[:64] or None


def _prefer_existing_loop_advisory(ws: str, projection: dict) -> dict:
    """Offer current-task advisory continuation for an established loop.

    ``ready`` stays false and the effective path stays ``transitioning``: no
    runtime receipt exists, so live enforcement remains unproven.  Only the
    recovery recommendation changes to the explicit ``--advisory --by`` path
    that governed commands already validate and persist.
    """
    if projection.get("next_action") != "start_new_session":
        return projection
    step = _existing_loop_step(ws)
    if not step:
        return projection
    updated = dict(projection)
    effective = dict(updated.get("effective_path") or {})
    effective["reason"] = (
        "an existing Taskplane loop can continue in this Codex task with "
        "explicit --advisory --by attribution; start a new task only when "
        "live hook enforcement is required")
    updated["effective_path"] = effective
    updated["next_action"] = "continue_advisory"
    updated["continuation"] = {
        "available": True,
        "loop_step": step,
        "status": "advisory",
        "requires": ["--advisory", "--by <human>"],
    }
    return updated


def _host_capability_snapshot(ws: str, install_context: str | None = None):
    """One capability snapshot for all onboarding host-path decisions."""
    context = install_context or _install_context()
    bridge = _codex_hooks_report(ws)
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    native_manifest = os.path.join(plugin_root, "hooks", "hooks.json")
    host = ("codex" if (os.environ.get("CODEX_HOME")
                        or os.environ.get("CODEX_THREAD_ID")) else "claude")
    version = (os.environ.get("CODEX_VERSION") if host == "codex" else
               os.environ.get("CLAUDE_CODE_VERSION"))
    session_id = (os.environ.get("CODEX_THREAD_ID")
                  or os.environ.get("CLAUDE_SESSION_ID"))
    observations = host_caps.runtime_hook_observations(
        tp.store_home(), session_id=session_id, workspace=ws)
    # Explicit adapter-owned environment receipts take precedence over the
    # short-lived runtime receipt when both exist.
    observations.update(host_caps.observations_from_environment(os.environ))
    return host_caps.probe_snapshot(
        ws, host=host, host_version=version, session_id=session_id,
        install_context=context, native_installed=os.path.isfile(
            native_manifest), bridge_configured=bool(bridge.get("ok")),
        observations=observations,
        now=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()))


def _screen_enforcement_mode(host: str) -> str:
    """Resolve the rollback lever without guessing an undeclared host."""
    raw = os.environ.get("TASKPLANE_ENFORCE_SCREEN")
    if raw is not None:
        mode = str(raw).strip().lower()
        if mode not in enforcement_kernel.MODES:
            raise enforcement_kernel.EnforcementError(
                "TASKPLANE_ENFORCE_SCREEN must be strict, warn, or off")
        return mode
    declared_claude = host == "claude" and bool(
        os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("CLAUDE_CODE_VERSION")
        or str(os.environ.get("TASKPLANE_HOST") or "").lower()
        in {"claude", "claude-code", "cowork", "chat"})
    return "strict" if declared_claude else "warn"


def _saved_enforcement(value) -> dict | None:
    if isinstance(value, dict) and value.get("schema") == \
            "taskplane.run-enforcement/v1":
        value = value.get("current")
    try:
        checked = enforcement_kernel.validate_decision(value)
    except (TypeError, ValueError):
        return None
    return checked


def _enforcement_check(
        ws: str, *, saved=None, advisory: bool = False,
        actor: str | None = None, run_id: str | None = None,
        revision: str | int | None = None) -> tuple[dict, dict | None]:
    """Compute one decision and, in strict mode, one machine refusal."""
    prior = _saved_enforcement(saved)
    workspace_fp = hashlib.sha256(os.path.normcase(os.path.realpath(
        os.path.abspath(ws))).encode("utf-8")).hexdigest()
    if (prior and prior.get("status") == "advisory"
            and prior.get("workspace_fingerprint") == workspace_fp):
        return prior, None
    snapshot = _host_capability_snapshot(ws)
    mode = _screen_enforcement_mode(snapshot.host)
    decision = enforcement_kernel.enforcement_status(
        ws, snapshot=snapshot, liveness=tp.screen_liveness(ws),
        run_id=run_id, revision=(revision if revision is not None
                                 else tp.git_head(ws)), mode=mode,
        observed_at=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()))
    if advisory:
        try:
            decision = enforcement_kernel.acknowledge_advisory(
                decision, actor=str(actor or ""))
        except enforcement_kernel.EnforcementError as exc:
            return decision, {
                "schema": "taskplane.enforcement-refusal/v1",
                "error": str(exc), "enforcement": decision,
                "recovery": ["repeat with --advisory --by <human>"],
            }
    if mode == "strict" and decision["status"] == "unproven":
        recovery = (["run /reload-plugins, then retry this exact command",
                     "or repeat with --advisory --by <human>"]
                    if snapshot.host == "claude" and
                    (os.environ.get("CLAUDE_CODE_VERSION") or
                     os.environ.get("CLAUDE_SESSION_ID")) else
                    ["start a new host conversation and retry",
                     "or repeat with --advisory --by <human>"])
        return decision, {
            "schema": "taskplane.enforcement-refusal/v1",
            "error": "governed action refused: screen enforcement is unproven",
            "enforcement": decision,
            "recovery": recovery,
        }
    return decision, None


def _codex_runner_engine(runner_body: str) -> str | None:
    """Read a legacy generated ENGINE literal.

    Kept only so onboarding can identify and replace pre-v2.16.4 bridges.
    """
    match = re.search(r"^ENGINE = (.+)$", str(runner_body or ""), re.MULTILINE)
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def _codex_runner_family(runner_body: str) -> str | None:
    """Read the stable plugin-family literal from a generated runner."""
    match = re.search(r"^PLUGIN_FAMILY = (.+)$",
                      str(runner_body or ""), re.MULTILINE)
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def _valid_plugin_root(root: str, family: str) -> tuple[tuple[int, int, int],
                                                        str] | None:
    """Validate one contained taskplane installation candidate."""
    family_real = os.path.realpath(family)
    root_real = os.path.realpath(root)
    try:
        if os.path.commonpath((family_real, root_real)) != family_real:
            return None
    except ValueError:
        return None
    manifest = os.path.join(root_real, _PLUGIN_MANIFEST)
    engine = os.path.realpath(os.path.join(root_real, "taskplane", "tp.py"))
    try:
        if os.path.commonpath((family_real, engine)) != family_real:
            return None
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    match = _PLUGIN_SEMVER_RE.fullmatch(str(version or ""))
    if (not isinstance(data, dict) or not match
            or data.get("name") != "taskplane"
            or not os.path.isfile(engine)):
        return None
    # Installed cache children are named by version. The family itself is
    # also accepted so a source checkout remains a valid development runner.
    if (root_real != family_real
            and os.path.basename(root_real) != str(version)):
        return None
    return tuple(int(part) for part in match.groups()), engine


def _resolve_taskplane_engine(family: str | None) -> str | None:
    """Resolve the newest valid engine inside one installation family."""
    if not isinstance(family, str) or not family:
        return None
    family_real = os.path.realpath(os.path.expanduser(family))
    roots = [family_real]
    try:
        roots.extend(os.path.join(family_real, name)
                     for name in os.listdir(family_real))
    except OSError:
        return None
    candidates = [row for root in roots
                  if (row := _valid_plugin_root(root, family_real))]
    return max(candidates, default=(None, None), key=lambda row: row[0])[1]


def _plugin_family_for_engine(engine: str) -> str:
    """Return the stable cache family, or the source root in development."""
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(engine)))
    candidate = _valid_plugin_root(plugin_root, plugin_root)
    if candidate:
        version = ".".join(str(part) for part in candidate[0])
        if os.path.basename(plugin_root) == version:
            return os.path.dirname(plugin_root)
    return plugin_root


def _codex_runner_body(family: str) -> str:
    """Render a standalone bridge that survives removal of old versions."""
    return f'''# Generated locally by taskplane onboarding; .taskplane is ignored.
import json, os, re, runpy, sys
PLUGIN_FAMILY = {os.path.abspath(family)!r}
family = os.path.realpath(os.path.expanduser(PLUGIN_FAMILY))
version_re = re.compile(r"^(\\d+)\\.(\\d+)\\.(\\d+)$")
candidates = []
try:
    roots = [family] + [os.path.join(family, name) for name in os.listdir(family)]
except OSError:
    roots = []
for root in roots:
    real_root = os.path.realpath(root)
    manifest = os.path.join(real_root, ".codex-plugin", "plugin.json")
    engine = os.path.realpath(os.path.join(real_root, "taskplane", "tp.py"))
    try:
        if os.path.commonpath((family, real_root)) != family:
            continue
        if os.path.commonpath((family, engine)) != family:
            continue
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        continue
    version = data.get("version") if isinstance(data, dict) else None
    match = version_re.fullmatch(str(version or ""))
    if not isinstance(data, dict) or not match or data.get("name") != "taskplane" or not os.path.isfile(engine):
        continue
    if real_root != family and os.path.basename(real_root) != str(version):
        continue
    candidates.append((tuple(int(part) for part in match.groups()), engine))
if not candidates:
    raise SystemExit("taskplane Codex hook bridge found no valid installed engine")
ENGINE = max(candidates, key=lambda row: row[0])[1]
sys.argv = [ENGINE, *sys.argv[1:]]
runpy.run_path(ENGINE, run_name="__main__")
'''


def _codex_hook_action(command: str) -> str:
    value = str(command or "")
    if re.search(r'host_native_runtime\.py"?\s+check\s+--host\s+claude(?:\s|$)', value):
        # Host-native capability discovery is context acquisition. Treat it
        # as the same fail-closed class so repo-local Codex hook installation
        # can preserve the shared Claude/Codex declaration safely.
        return "context"
    match = re.search(r'tp\.py"?\s+([a-z][a-z0-9-]*)', value)
    if not match:
        raise RuntimeError("bundled hook command has no taskplane action")
    return match.group(1)


def _codex_hook_rows() -> dict:
    """Translate bundled host-neutral hooks to a repo-local Codex runner."""
    source_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "hooks", "hooks.json")
    source = tp.load_json(source_path, what="bundled hook configuration")
    generated = json.loads(json.dumps(source.get("hooks") or {}))
    for rows in generated.values():
        for row in rows:
            for hook in row.get("hooks") or []:
                command = str(hook.get("command") or "")
                is_host_native_check = "host_native_runtime.py" in command
                _codex_hook_action(command)
                # Keep the bundled missing-runner fallback. A Codex-managed
                # worktree contains the tracked hook configuration but not
                # the ignored local bridge, and the originating worktree may
                # be removed while its task is still open.
                hook["command"] = command.replace(
                    "TASKPLANE_HOOK_PATH=native",
                    "TASKPLANE_HOOK_PATH=bridge")
                hook["commandWindows"] = str(
                    hook.get("commandWindows") or "").replace(
                        "TASKPLANE_HOOK_PATH=native",
                        "TASKPLANE_HOOK_PATH=bridge")
                if is_host_native_check:
                    hook["command"] = hook["command"].replace(
                        "check --host claude", "check --host codex")
                    hook["commandWindows"] = hook["commandWindows"].replace(
                        "check --host claude", "check --host codex")
    return generated


def _taskplane_only_codex_config(value: dict) -> bool:
    """True only for an untracked config composed entirely by Taskplane."""
    if not isinstance(value, dict) or set(value) != {"hooks"} or not \
            isinstance(value.get("hooks"), dict):
        return False
    commands = [str(hook.get("command") or "")
                for rows in value["hooks"].values()
                for row in rows for hook in row.get("hooks") or []]
    return bool(commands) and all(
        _CODEX_HOOK_MARKER in command
        or "host_native_runtime.py" in command
        for command in commands)


def _exclude_generated_codex_config(ws: str, value: dict) -> None:
    """Keep Taskplane's local bridge out of the repository's review diff."""
    if not _taskplane_only_codex_config(value):
        return
    tracked = tp._run(
        ["git", "ls-files", "--error-unmatch", "--", _CODEX_HOOK_CONFIG],
        cwd=ws)
    if tracked.returncode == 0:
        return
    location = tp._run(
        ["git", "rev-parse", "--git-path", "info/exclude"], cwd=ws)
    if location.returncode:
        return
    path = location.stdout.strip()
    if not os.path.isabs(path):
        path = os.path.join(ws, path)
    pattern = "/.codex/hooks.json"
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        lines = []
    if pattern in lines:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + f".tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            if lines:
                handle.write("\n".join(lines) + "\n")
            handle.write(pattern + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _install_codex_hooks(ws: str) -> dict:
    """Install the portable workspace config and ignored local engine bridge.

    Marketplace plugins provide skills/apps, while Codex lifecycle hooks load
    from workspace configuration. The committed config stays portable; the
    ignored runner holds a stable installation-family path and resolves the
    newest valid engine on every invocation.
    """
    # Managed policy is an authority boundary, not a setup inconvenience.
    # Refuse before opening the workspace config and never edit a managed
    # settings file.  Unknown remains non-destructive here: the user can
    # inspect onboarding and obtain an administrator/session receipt first.
    observations = host_caps.observations_from_environment(os.environ)
    policy = observations.get("managed_policy_permission")
    install_context = _install_context()
    if ((policy and policy.status in ("unsupported", "contradictory"))
            or (install_context == "org-managed" and policy is None)):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "organization hook policy requires administrator action",
            "hint": "Ask an administrator to allow taskPlane hooks; managed "
                    "settings were not changed.",
        }

    config_path = os.path.join(ws, _CODEX_HOOK_CONFIG)
    prior = tp.load_json(config_path, default={"hooks": {}},
                         what="Codex hook configuration")
    if not isinstance(prior, dict) or not isinstance(prior.get("hooks"), dict):
        raise RuntimeError("existing .codex/hooks.json is not a hook object")
    hooks = prior["hooks"]
    for event, rows in _codex_hook_rows().items():
        existing = [row for row in hooks.get(event, [])
                    if _CODEX_HOOK_MARKER not in json.dumps(row)
                    and "host_native_runtime.py" not in json.dumps(row)]
        hooks[event] = existing + rows
    tp.atomic_write_json(config_path, prior, indent=2, sort_keys=False)
    _exclude_generated_codex_config(ws, prior)

    runner_path = os.path.join(ws, _CODEX_HOOK_RUNNER)
    os.makedirs(os.path.dirname(runner_path), exist_ok=True)
    family = _plugin_family_for_engine(os.path.abspath(__file__))
    body = _codex_runner_body(family)
    tmp = runner_path + f".tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        os.replace(tmp, runner_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return _codex_hooks_report(ws)


def _tool_report() -> dict:
    try:
        import target as _tgt
        rep = _tgt.tools()
        rep["install_hint"] = _tgt.install_hint()
        rep["ok"] = bool(rep["git"]["present"] and rep["gh"]["present"])
        return rep
    except Exception:
        return {"ok": None}


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
    codex_hooks = _codex_hooks_report(ws) if is_codex else None
    host_capabilities = None
    if codex_hooks is not None:
        snapshot = _host_capability_snapshot(ws)
        host_capabilities = host_caps.onboarding_projection(snapshot)
        host_capabilities = _prefer_existing_loop_advisory(
            ws, host_capabilities)
        native_effective = (host_capabilities["effective_path"]["value"]
                            == "native_effective")
        advisory_continuation = (
            host_capabilities.get("next_action") == "continue_advisory")
        if native_effective:
            checks[0]["hint"] = (
                "The loaded native Taskplane hook governs this checkout; "
                "continue in the current Codex task.")
        checks.extend((
            {
                "id": "hook_install", "label": "Hook installation",
                "ok": host_capabilities["install"]["status"] == "supported",
                "detail": host_capabilities["install"]["status"],
                "hint": "Install taskPlane hooks before starting governed work.",
            },
            {
                "id": "repository_trust", "label": "Repository trust",
                "ok": (native_effective or
                       host_capabilities["trust"]["status"] == "supported"),
                "detail": ("not required for native hooks" if
                           native_effective else
                           host_capabilities["trust"]["status"]),
                "hint": "Review the repository trust decision in Codex when "
                        "the workspace bridge is required.",
            },
            {
                "id": "managed_policy", "label": "Managed hook policy",
                "ok": (host_capabilities["managed_policy"]["status"]
                       == "supported"),
                "detail": host_capabilities["managed_policy"]["status"],
                "hint": "An organization-managed restriction requires "
                        "administrator action; taskPlane will not change it.",
            },
            {
                "id": "loaded_session", "label": "Hooks loaded this session",
                "ok": (host_capabilities["loaded_session"]["status"]
                       == "supported"),
                "detail": host_capabilities["loaded_session"]["status"],
                "hint": ("This Codex task already has an observed hook "
                         "receipt; no restart is required." if
                         host_capabilities["loaded_session"]["status"] ==
                         "supported" else
                         "Continue the existing loop in this task with "
                         "explicit --advisory --by attribution; start a new "
                         "task only if live enforcement is required." if
                         advisory_continuation else
                         "Start one new Codex task only after the initial "
                         "hook installation or a host policy change."),
            },
            {
                "id": "effective_hook_path", "label": "Effective hook path",
                "ok": host_capabilities["ready"],
                "detail": host_capabilities["effective_path"]["value"],
                "hint": host_capabilities["effective_path"]["reason"],
            },
        ))
    base_ready = looks_like_project and inside_git and has_commit and has_context
    ready = base_ready and (host_capabilities is None
                            or bool(host_capabilities["ready"]))
    if not looks_like_project:
        nxt = "attach_folder"
    elif not (inside_git and has_commit):
        nxt = "init_git"
    elif not has_context:
        nxt = "tp_init"
    elif host_capabilities is not None and not host_capabilities["ready"]:
        nxt = host_capabilities["next_action"]
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
    # Onboarding is one of the explicit discovery boundaries. Status below
    # reads this durable result and never scans the tree independently.
    foreign_state = collision_kernel.discover_state_roots(ws)
    if foreign_state:
        collision_kernel.persist(ws, roots=foreign_state)
    return {"workspace": ws, "host": host, "artifacts": artifacts,
            "codex_hooks": codex_hooks,
            "host_capabilities": host_capabilities,
            # R-0005 install truth: the account-type install/update paths,
            # matched to the detected context (org-managed / personal) or
            # the honest by-account-type triage when undetectable — never
            # a step an org member cannot run.
            "install": {"context": _ictx,
                        "paths": _install_paths_lines(_ictx)},
            # v2.12.0: git and gh are dependencies, not conveniences.
            # taskplane is a git-shaped product — contracts snapshot HEAD,
            # routing diffs against a base, the graph scans a tree, a review
            # is pinned to a commit — and reviewing a REMOTE pull request
            # additionally needs the PR's title, body, linked issues and
            # discussion, none of which are in the git objects. In the field
            # `gh` was absent and that context arrived over unauthenticated
            # web reads that nothing recorded. Surfacing it at onboarding is
            # where a missing tool costs one command instead of a review.
            "tools": _tool_report(),
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
                                for t in tp.MODEL_TIERS},
            "foreign_state": foreign_state}


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
    enforcement, refusal = _enforcement_check(
        ws, advisory=bool(getattr(a, "advisory", False)),
        actor=getattr(a, "by", None))
    if refusal:
        print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
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
    allowed_foreign = list(getattr(a, "allow_foreign_state", None) or [])
    if allowed_foreign:
        if not str(getattr(a, "by", None) or "").strip():
            print("taskplane: --allow-foreign-state requires --by ACTOR",
                  file=sys.stderr)
            return 1
        c["foreign_state_override"] = {
            "schema": "taskplane.foreign-state-override/v1",
            "roots": allowed_foreign, "actor": str(a.by),
        }
    c["enforcement"] = enforcement
    # cooperative dollar advisory (kept on the shared shape as an optional
    # key). `is not None`, NOT truthiness: `--budget 0` means a ZERO ceiling
    # (maximally strict — any spend is over), never the $3 default.
    if a.budget is not None and a.budget < 0:
        print("taskplane: --budget must be >= 0 (0 means no cooperative "
              "spend allowed).", file=sys.stderr)
        return 1
    c["budget"]["max_cost_usd"] = float(a.budget) if a.budget is not None \
        else DEFAULT_MAX_COST_USD
    if getattr(a, "max_tokens", None) is not None:
        c["budget"]["max_tokens"] = int(a.max_tokens)

    # BIND THE CONTRACT TO A TREE (v2.12.0). A review's target used to be
    # free text in `task`, which is why two field reviews of one PR could
    # not prove they had cloned anything. --target pins the checkout (and
    # fetches the PR first when asked), and the record is what the
    # completion gate checks.
    _tgt_rec = None
    if getattr(a, "target", None) or getattr(a, "base", None):
        import target as _tgt
        spec = getattr(a, "target", None)
        parsed = _tgt.parse(spec) if spec else None
        if parsed and parsed["kind"] == "pr" and getattr(a, "fetch", False):
            _tgt_rec = _tgt.acquire(ws, spec, base=getattr(a, "base", None))
        else:
            _tgt_rec = _tgt.pin(ws, base=getattr(a, "base", None),
                                target=parsed)
        if not _tgt_rec.get("ok"):
            print(f"taskplane: {_tgt_rec.get('reason')}", file=sys.stderr)
            return 1
        _tgt.save(ws, _tgt_rec)
        c["target"] = {k: _tgt_rec.get(k) for k in
                       ("origin", "head", "base", "base_ref", "branch",
                        "fingerprint", "target")}

    snapshot = tp.git_head(ws)
    tp.activate(ws, c, snapshot=snapshot)
    if _tgt_rec:
        print(f"  target    : {(_tgt_rec.get('head') or '')[:12]} vs "
              f"{(_tgt_rec.get('base') or '(no base)')[:12]} — "
              f"fingerprint {_tgt_rec['fingerprint']}")

    projection = tp.contract_projection(c)
    mode = "READ-ONLY review" if projection["read_only"] else "build"
    print(f"taskplane: contract {c['task_id']} active ({mode}).")
    if c.get("read_only"):
        print(f"  writable  : {c.get('write_allow') or '(nothing — reads only)'}")
    print(f"  scope     : {projection['display_scope'] or '(any — set --scope!)'}")
    print(f"  deny cmds : {projection['deny']}")
    print(f"  tests     : {projection['test_command'] or '(none)'}")
    snap_disp = snapshot[:12] if snapshot else "NONE (git commit first)"
    print(f"  snapshot  : {snap_disp}")
    if not snapshot:
        print("  ! not a git repo / no commit: run `git init && git add -A "
              "&& git commit -m init` for the DoD scope-diff to work.",
              file=sys.stderr)
    owed = _seed_owed(ws, getattr(a, "owes", None), c.get("task_id", ""))
    print("\nThe PreToolUse hook now blocks out-of-scope writes, denied "
          "commands, and disallowed tools.")
    if owed:
        print(f"  owes      : {', '.join(owed)} — recorded now, before the "
              "work starts, so a skip is a fact rather than an absence. "
              "`tp dod` and `tp loop submit` stay blocked until each is "
              "shown and acknowledged (`tp ack --status`).")
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

# ---- the render seam ------------------------------------------------
#
# WHY THIS EXISTS. obligations.py used to state, as its central design
# premise, that the engine "CANNOT see whether a rendered artifact was
# actually put in front of a human" because the render "happens in the
# host, outside every process taskplane runs" — and therefore that showing
# an artifact could only ever be a CLAIM. That premise was wrong. A
# PreToolUse matcher is a regex over TOOL NAMES, and an MCP tool is named
# `mcp__<server>__<tool>`, so it matches like any other. The render is
# reachable at exactly the seam that already screens writes and dispatches.
#
# So the demand ("show this"), the claim ("I showed it") and the FACT
# ("the render tool ran, with these bytes") are three separate records,
# and the two failures this project has actually suffered separate
# cleanly without anyone watching: a skipped render leaves a demand with
# no observation, and a SUBSTITUTED render — a hand-drawn chart, or the
# engine's own HTML edited on the way through — leaves an observation
# whose fingerprint is not the artifact the engine built. The render
# contract says "byte-for-byte, no restyling, no re-authoring"; this is
# the first mechanism that can tell.
#
# WHAT IT MAY NOT DO. It never denies. A hook that could block a render
# would be the instrument stopping the very thing it exists to encourage,
# and taskplane's instruments do not gate. Every path returns 0, every
# write is best effort, and deleting this function changes no behaviour
# except that the ledger stops learning.
# The artifacts each run type OWES. Declared here rather than in a skill,
# because a skill is a prompt — the model can read it and proceed anyway —
# while this is recorded in the ledger before the work starts and read by a
# hook that does not need the model's cooperation.
RUN_OWES = {
    "review": (
        ("render_dashboard", "the engine-authored review dashboard, rendered "
                             "after dispatch and after collection; it embeds "
                             "the exact dependency/blast-radius graph"),
    ),
}


def _seed_owed(ws, run_type, task_id):
    """Record what this run owes BEFORE any of the work happens.

    The obligations ledger could previously only record a demand the engine
    had already made, so an artifact nobody ever asked for left no trace:
    if `tp graph html` was never run, no obligation existed and nothing was
    missing. Seeding inverts that — absence is recorded from the first
    second, which is the whole difference between an instrument and a hope.
    """
    run = (run_type or "").strip().lower()
    if not run:
        return []
    try:
        import obligations
    except Exception:
        return []
    seeded = []
    for kind, detail in RUN_OWES.get(run, ()):  # unknown run type owes nothing
        oid = obligations.issue(ws, kind, detail=detail, step=run,
                                key=f"{run}:{kind}", session=task_id,
                                binding=True)
        if oid:
            seeded.append(f"{kind} ({oid})")
    return seeded


def cmd_session_verify(a) -> int:
    """Stop / SessionEnd hook: report artifacts this run owes and never showed.

    Exits 2 with the list on stderr when anything is open. The host's exact
    blocking semantics for `Stop` are not documented precisely enough to
    rely on, so this is written to be USEFUL either way: if Stop can block,
    the turn does not end quietly; if it cannot, the list is still surfaced
    where a human reads it. The PreToolUse conversion is the mechanism that
    actually holds — this is the net under it.
    """
    try:
        event = json.load(sys.stdin)
        event = event if isinstance(event, dict) else {}
    except Exception:
        event = {}
    submission = _submission_stop_check(
        event, workspace=getattr(a, "workspace", None))
    if submission and submission.get("block"):
        _emit_submission_stop_block("Stop", submission)
        return 2
    try:
        import obligations
        ws = _workspace(getattr(a, "workspace", None))
        owed = obligations.blocking(ws)
    except Exception:
        return 0
    if not owed:
        return 0
    print("taskplane: this run owes artifacts that were never shown:",
          file=sys.stderr)
    for o in owed:
        print(f"  {o['id']}  {o.get('kind')}  {o.get('detail') or ''}",
              file=sys.stderr)
    print("Render each one, then `tp ack <id>` (ack is unmetered — it can "
          "always be run).", file=sys.stderr)
    # STALL DETECTION (v2.11.0). This hook used to print one instruction
    # forever. On karpenter#9464 it fired ~12 consecutive times with no
    # state change, because `tp ack` was itself budget-blocked: the hook
    # demanded an action the harness refused, and nothing the agent could
    # do satisfied it. The refusal still stands — an obligation that can be
    # waited out is not an obligation — but a hook that repeats an
    # unsatisfiable instruction is a hang, not enforcement. So when nothing
    # has changed since the last firing, say what is actually in the way and
    # name the command that clears it.
    try:
        stalled, detail = _session_verify_stall(ws, owed)
    except Exception:
        stalled, detail = False, ""
    if stalled:
        print("", file=sys.stderr)
        print(f"taskplane: NO PROGRESS since the last check — {detail}",
              file=sys.stderr)
    return 2


def _session_verify_stall(ws: str, owed: list) -> tuple:
    """(stalled, what is actually blocking). Stalled = this hook has fired
    before with exactly this set of open obligations and the same action
    count, i.e. repeating the instruction cannot help."""
    import hashlib
    used = None
    contract = None
    try:
        contract = tp.load_active(ws)
        tid = (contract or {}).get("task_id", "_")
        used = _meter_load(ws).get(tid, {}).get("actions")
    except Exception:
        contract, used = contract, None
    key = hashlib.sha1(
        ("|".join(sorted(o["id"] for o in owed)) + f"#{used}").encode()
    ).hexdigest()[:16]
    path = os.path.join(tp.tp_dir(ws), "session_verify_stall.json")
    prior = tp.load_json(path, default={}, what="stall marker") or {}
    count = int(prior.get("count") or 0) + 1 if prior.get("key") == key else 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "count": count}, f)
    except OSError:
        pass
    if count < 2:
        return False, ""
    max_a = ((contract or {}).get("budget") or {}).get("max_actions")
    if max_a is not None and used is not None and int(used) >= int(max_a):
        return True, (
            f"the action budget is exhausted ({used}/{max_a}), so the work "
            f"cannot continue. `tp ack <id>` is unmetered and still works; "
            f"closing commands (dod, findings, decision, req) draw on the "
            f"reserved closing actions. If more WORK is genuinely needed, a "
            f"human can approve in chat, then run: `tp.py budget --grant N "
            f"--approved-by <human> --workspace {ws}`. No new task or "
            f"outside-workspace workaround is required.")
    return True, (
        f"the same {len(owed)} obligation(s) have been open across "
        f"{count} checks. Render the artifact the engine produced — its own "
        f"bytes, not a summary — then `tp ack <id>`. `tp ack --status` shows "
        f"whether a render was observed, substituted, or only claimed")


def cmd_screen_render(a) -> int:
    """PreToolUse hook for `mcp__visualize__*`: observe, never deny."""
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0                       # malformed input is not the model's
    try:
        import obligations
        ti = event.get("tool_input") or {}
        # The payload key differs by tool and may change; take the longest
        # string argument rather than pinning one name, so a renamed field
        # degrades to a smaller fingerprint instead of no record at all.
        body, title = "", ""
        for k, v in ti.items():
            if not isinstance(v, str):
                continue
            if k in ("title", "name") and not title:
                title = v
            if len(v) > len(body):
                body = v
        ws = _workspace(event.get("cwd"))
        obligations.observe(
            ws,
            tool=event.get("tool_name") or "",
            fingerprint=obligations.content_fingerprint(body) if body else None,
            title=title, bytes_len=len(body.encode("utf-8")) if body else 0,
            session=event.get("session_id"))
    except Exception:
        pass
    return 0


def _collision_authority(ws: str) -> dict:
    """Read exact-workspace governed state without discovering foreign data."""
    contract = tp.load_active(ws)
    state = None
    try:
        import loop as loop_engine
        state = loop_engine._load_raw(ws)
    except Exception:
        state = None
    review_state = None
    try:
        import review as review_engine
        review_state = review_engine._load_state(ws)
    except Exception:
        review_state = None
    loop_active = isinstance(state, dict) and state.get("step") not in {
        None, "done", "failed"}
    review_active = isinstance(review_state, dict) and \
        review_state.get("status") not in {
            None, "complete", "failed", "cancelled", "unavailable"}
    governed = bool(contract or loop_active or review_active)
    step = ((state or {}).get("step") or (review_state or {}).get("stage")
            or (contract or {}).get("stage") or "contract")
    run_id = ((review_state or {}).get("run_id")
              or (state or {}).get("run_id")
              or (state or {}).get("requirement_id")
              or (contract or {}).get("task_id"))
    enforcement = (((state or {}).get("enforcement") or {}).get("current")
                   or (contract or {}).get("enforcement") or {})
    return {"governed": governed, "run_id": run_id, "step": step,
            "advisory": enforcement.get("status") == "advisory"}


def _collision_allowlist() -> list[str]:
    raw = os.environ.get("TASKPLANE_SKILL_ALLOW") or ""
    return [item.strip() for item in raw.split(",")
            if item.strip()]


def _classify_collision(ws: str, kind: str, identity: str,
                        *, brief_owned: bool = False) -> dict:
    authority = _collision_authority(ws)
    if brief_owned and authority["governed"]:
        return collision_kernel.classify(
            kind, "tp_" + identity, governed=True,
            run_id=authority["run_id"], step=authority["step"])
    collision_mode = (os.environ.get("TASKPLANE_COLLISION_SCREEN")
                      or "on").strip().lower()
    strict = collision_mode == "strict" or \
        (os.environ.get("TASKPLANE_SKILL_STRICT") or "").strip().lower() \
        in {"1", "true", "yes", "strict"}
    decision = collision_kernel.classify(
        kind, identity, governed=authority["governed"],
        run_id=authority["run_id"], step=authority["step"], strict=strict,
        advisory=(authority["advisory"] or collision_mode in {
            "0", "false", "no", "off", "disabled"}),
        allow=_collision_allowlist())
    if decision.get("record"):
        collision_kernel.persist(ws, decision=decision,
                                 run_id=authority["run_id"])
        tp.trace(ws, "foreign_interference", kind=kind, identity=identity,
                 action=decision["action"], step=authority["step"],
                 run_id=authority["run_id"],
                 registry_version=decision["registry_version"],
                 registry_fingerprint=decision["registry_fingerprint"])
    return decision


def _emit_collision(decision: dict) -> None:
    if decision.get("action") == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": decision["reason"]}}))
    elif decision.get("action") in {"advise", "observed"}:
        print(json.dumps({"systemMessage": decision["reason"]}))


def cmd_screen_skill(a) -> int:
    """PreToolUse authority gate for explicit Skill invocations."""
    try:
        event = json.load(sys.stdin)
    except Exception as exc:
        ws = _workspace()
        authority = _collision_authority(ws)
        if authority["governed"] and not authority["advisory"]:
            reason = ("taskplane skill isolation could not parse hook input "
                      f"({type(exc).__name__}); governed verification fails closed")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        return 0
    ti = event.get("tool_input") if isinstance(event, dict) else {}
    ti = ti if isinstance(ti, dict) else {}
    identity = ti.get("skill") or ti.get("skill_name") or ti.get("name") or ""
    ws = _workspace(event.get("cwd"))
    decision = _classify_collision(ws, "skill", str(identity))
    _emit_collision(decision)
    return 0


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
        authority = _collision_authority(_workspace())
        if ((authority["governed"] and not authority["advisory"])
                or mode == "strict"):
            reason = ("taskplane dispatch check: malformed hook input "
                      f"({type(exc).__name__}); strict verification cannot "
                      "prove this dispatch, so it is denied.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
        return 0
    foreign_message = None
    try:
        ti = event.get("tool_input") or {}
        agent = (ti.get("task_name") or ti.get("subagent_type")
                 or ti.get("agent_type") or "")
        ws = _workspace(event.get("cwd"))
        expectation = tp.peek_expectation(ws, agent, strict=False)
        brief_owned = bool(expectation and agent in {
            expectation.get("task_name"), expectation.get("agent")})
        collision = _classify_collision(
            ws, "agent", str(agent), brief_owned=brief_owned)
        if collision.get("action") == "deny":
            _emit_collision(collision)
            return 0
        if collision.get("action") in {"advise", "observed"}:
            foreign_message = collision["reason"]
    except Exception as exc:
        try:
            authority = _collision_authority(
                _workspace(event.get("cwd")))
        except Exception:
            authority = {"governed": True, "advisory": False}
        if authority["governed"] and not authority["advisory"]:
            reason = ("taskplane dispatch isolation errored "
                      f"({type(exc).__name__}); governed verification fails closed")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
            return 0
    if mode not in ("warn", "strict"):
        if foreign_message:
            print(json.dumps({"systemMessage": foreign_message}))
        return 0                                   # tier verification opt-in
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
            if foreign_message:
                print(json.dumps({"systemMessage": foreign_message}))
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
            joined = reason if not foreign_message else \
                foreign_message + "\n" + reason
            print(json.dumps({"systemMessage": joined}))
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


def _submission_stop_check(event: dict, *, workspace: str | None = None,
                           loop_state=None) -> dict | None:
    """Read-only Stop/SubagentStop submission decision for the active slot."""
    ws = _workspace(workspace) if workspace else _subagent_workspace(event)
    try:
        contract = tp.load_active(ws)
    except Exception as exc:
        tp.trace(ws, "submission_stop_checked", status="contract_error",
                 error=type(exc).__name__)
        return {"schema": tp.SUBMISSION_STATUS_SCHEMA, "status": "corrupt",
                "valid": False, "required": True, "block": True,
                "contract_id": None, "task": None, "stage": None,
                "slot": tp.task_slot(), "artifact": "active contract",
                "recovery": "return to the orchestrator or human"}
    if not isinstance(contract, dict) or not contract:
        return None
    if loop_state is None:
        try:
            import loop as loop_engine
            loop_state = loop_engine.load(ws)
        except Exception:
            loop_state = None
    observed_slot = (event.get("task_slot")
                     if isinstance(event.get("task_slot"), str)
                     else tp.task_slot())
    decision = tp.stop_submission_decision(
        ws, contract, observed_slot=observed_slot, loop_state=loop_state)
    tp.trace(ws, "submission_stop_checked", status=decision.get("status"),
             contract_id=decision.get("contract_id"),
             task=decision.get("task"), stage=decision.get("stage"),
             task_slot=decision.get("slot"), block=decision.get("block"),
             artifact=decision.get("artifact"))
    if decision.get("block"):
        tp.trace(ws, "submission_stop_blocked",
                 status=decision.get("status"),
                 contract_id=decision.get("contract_id"),
                 task=decision.get("task"), stage=decision.get("stage"),
                 task_slot=decision.get("slot"))
    return decision


def _emit_submission_stop_block(event_name: str, status: dict) -> None:
    reason = (
        "taskplane blocked lifecycle completion: "
        f"contract={status.get('contract_id')}; task={status.get('task')}; "
        f"stage={status.get('stage')}; slot={status.get('slot')}; "
        f"status={status.get('status')}; missing artifact="
        f"{status.get('artifact')}. Recovery: {status.get('recovery')}")
    print(json.dumps({"decision": "block", "reason": reason,
                      "hookSpecificOutput": {
                          "hookEventName": event_name,
                          "permissionDecision": "deny",
                          "permissionDecisionReason": reason}}))


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
        try:
            import review as _review
            assignment = _review.register_slot_producer(
                ws, event=event, contract=contract, task_slot=tp.task_slot())
            if assignment:
                tp.trace(ws, "review_slot_producer_bound",
                         agent_id=agent_id,
                         task_slot=assignment["contract_task_slot"],
                         lease_fingerprint=assignment["lease_fingerprint"])
        except Exception as exc:
            # Lifecycle remains advisory; the authoritative write hook will
            # fail closed because no matching producer assignment exists.
            tp.trace(ws, "review_slot_producer_bind_failed", agent_id=agent_id,
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
    submission = _submission_stop_check(event)
    if submission and submission.get("block"):
        _emit_submission_stop_block("SubagentStop", submission)
        return 2
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


def cmd_worktree_cleanup(a) -> int:
    """Run one receipt-scoped, no-force maintenance replay."""
    import loop as loopmod

    ws = _workspace(a.workspace)
    state = loopmod.load(ws)
    enforcement, refusal = _enforcement_check(
        ws, saved=(state or {}).get("enforcement"),
        run_id=(state or {}).get("run_id"),
        revision=((state or {}).get("baseline") or tp.git_head(ws)))
    if refusal:
        print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
        return 1
    if state is not None:
        loopmod.record_enforcement(ws, enforcement)
    out = loopmod.cleanup_replay(ws)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 1 if out.get("error") else 0


# READ-ONLY inspection only. `clear` is deliberately NOT here.
#
# The field report asked for `clear` to be exempt from metering, because an
# exhausted agent cannot release its own contract. That is a real deadlock
# and the suggested cure is worse: clearing a contract leaves the workspace
# UNGOVERNED, where the screener abstains — so an agent that hit its ceiling
# could un-govern itself and carry on unmetered. `test_recovery.py::
# TestTheWallHolds` pins exactly that, alongside `budget --grant` and `rm
# active_contract.json`: you may not spend past the wall, raise it, or
# remove it. The wall stands.
#
# What was actually missing was a RECOVERY path from outside — `tp
# contracts` to see the slots, `tp clear --all/--slot` to release them, and
# a message that names the slots that exist instead of claiming there are
# none. Those are the fix; this exemption covers only commands that change
# nothing at all, so a stuck agent can still report why it is stuck.
#
# v2.11.0 adds `ack`, and the distinction matters because v2.10.0 got it
# wrong by lumping the two together. `clear` LOOSENS governance — it leaves
# the workspace with no contract, where the screener abstains, so an
# exhausted agent could un-govern itself and carry on. `ack` does the
# opposite: it discharges an obligation the run already owes, moving the run
# TOWARD the gate and never widening what it may touch. It is bounded by the
# number of obligations issued, and the one abuse it appears to open —
# acking a render that never happened — is already closed by the
# render-observation ledger, which records that as `claimed_only` rather
# than as evidence. Refusing it produced a hard deadlock in the field: the
# `session-verify` stop hook demanded `tp ack <id>`, the budget refused
# `tp ack <id>`, and the hook fired twelve times with no reachable state
# that satisfied it.
_RELEASE_VERBS = ("status", "contracts", "version", "ack")

# The CLOSING RESERVE. On aws/karpenter-provider-aws#9464 the 40-action
# default was spent entirely on taskplane's own mandated orchestration —
# onboard, init, contract, impact, route, two renders, dispatch — so the
# review reached its verdict with nothing left to RECORD it. `.em-review/`
# is git-ignored scratch in an ephemeral sandbox, which made the blocked
# step the one whose entire purpose is surviving the session.
#
# This is a carve-out, NOT an increase: the ceiling is unchanged and the
# working wall drops below it, so a contract can never spend more than it
# could before. The last few actions are simply reserved for the commands
# that finish and persist the work, and cannot be spent on more of it.
_CLOSING_RESERVE = 5
_CLOSING_VERBS = ("dod", "findings", "decision", "req")


def _is_completion_command(command: str) -> bool:
    """A taskplane command that DECLARES the work finished. Shares the
    obligation ledger's pattern set so there is one answer to "is this a
    conclusion", not two that can drift apart."""
    import re as _re
    import obligations as _ob
    verb = tp.taskplane_verb(command)
    if not verb:
        return False
    text = " ".join(str(command or "").split())
    return any(_re.search(p, text) for p in _ob.COMPLETION_PATTERNS)


def _is_release_command(command: str) -> bool:
    """Unmetered control-plane commands, including explicit human recovery.

    Mutating recovery is accepted only when the argv carries the human's
    approval marker (or an exact persisted review action).  This prevents an
    exhausted task from becoming an unrecoverable lock while keeping a bare
    self-issued ``clear``/``budget --grant`` behind the wall.
    """
    verb = tp.taskplane_verb(command)
    if verb in _RELEASE_VERBS:
        return True
    tokens = tp._shsplit(" ".join(str(command or "").split()))
    if verb == "clear":
        return "--approved-by" in tokens
    if verb == "budget":
        return "--grant" in tokens and "--approved-by" in tokens
    if verb == "review":
        if all(token in tokens for token in (
                "collect", "--run-id")):
            return True
        return all(token in tokens for token in (
            "resume", "--run-id", "--action-id", "--by"))
    return False


def _is_closing_command(command: str) -> bool:
    """A command that finishes or persists the work rather than doing more
    of it — the only spender of the closing reserve."""
    verb = tp.taskplane_verb(command)
    return verb in _CLOSING_VERBS if verb else False


def cmd_contracts(a) -> int:
    """List every active contract slot — including the stale ones.

    `tp status` reports the contract governing THIS process, which for a
    slot-less caller is an anonymous union. That made leaked slots invisible:
    the only way to find them was `ls .taskplane/active/`, and a leaked slot
    silently tightens every later agent. Slots are named here, with age, so
    an orphan can be seen and released.
    """
    ws = _workspace(a.workspace)
    d = os.path.join(tp.tp_dir(ws), "active")
    rows = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".json"):
            continue
        slot = name[:-5]
        c = tp.load_json(os.path.join(d, name), default={},
                         what="active contract") or {}
        age = _time.time() - float(c.get("activated_at") or 0) \
            if c.get("activated_at") else None
        rows.append({"slot": slot, "task_id": c.get("task_id"),
                     "read_only": bool(c.get("read_only")),
                     "age_seconds": int(age) if age is not None else None,
                     "task": str(c.get("task") or "")[:120]})
    print(json.dumps({
        "slots": rows, "count": len(rows),
        "legacy_slot_present": os.path.exists(
            os.path.join(tp.tp_dir(ws), "active_contract.json")),
        "note": "a slot-less process is governed by the most-restrictive "
                "union of every slot above; release one with "
                "`tp clear --slot <slot>` or all with `tp clear --all`",
    }, indent=2, default=str))
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
    if getattr(a, "all", False):
        d = os.path.join(tp.tp_dir(ws), "active")
        freed = []
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if name.endswith(".json"):
                tp.safe_remove(os.path.join(d, name))
                freed.append(name[:-5])
        legacy = os.path.join(tp.tp_dir(ws), "active_contract.json")
        if os.path.exists(legacy):
            tp.safe_remove(legacy)
            freed.append("(legacy)")
        print(f"taskplane: cleared {len(freed)} contract(s): "
              + (", ".join(freed) or "none"))
        if getattr(a, "approved_by", None):
            tp.trace(ws, "contract_clear_human_approval",
                     approved_by=a.approved_by, slots=freed)
        return 0
    slot = getattr(a, "slot", None)
    path = tp.active_contract_path(ws, slot) if slot \
        else tp.active_contract_path(ws)   # slot-aware: what clear() removes
    if not os.path.exists(path):
        # Truthful: naming the slots that DO exist, because "no active
        # contract to clear" while seven slots governed the workspace is how
        # an operator loses twenty minutes.
        d = os.path.join(tp.tp_dir(ws), "active")
        others = sorted(n[:-5] for n in os.listdir(d)
                        if n.endswith(".json")) if os.path.isdir(d) else []
        if others:
            print("taskplane: nothing in this slot, but "
                  f"{len(others)} contract(s) are active: "
                  + ", ".join(others)
                  + "\n  release one with `tp clear --slot <slot>`, "
                    "or all with `tp clear --all`.")
        else:
            print("taskplane: no active contract to clear.")
        return 0
    try:
        c = tp.load_json(path, default={}, what="active contract")
    except tp.StateError:
        c = {}                            # corrupt slot: still clearable
    if not isinstance(c, dict):
        c = {}
    slot = slot or tp.task_slot()
    if getattr(a, "slot", None):
        tp.safe_remove(path)
    else:
        tp.clear(ws)                      # FUSE-safe removal (safe_remove)
    print(f"taskplane: contract {c.get('task_id','')} cleared"
          + (f" (slot {slot})" if slot else "")
          + " — workspace is ungoverned again.")
    if getattr(a, "approved_by", None):
        tp.trace(ws, "contract_clear_human_approval",
                 approved_by=a.approved_by, slots=[slot or "legacy"])
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
        if os.path.exists(os.path.join(tp.tp_dir(cur),
                                       "active_contract.json")) or \
                tp.list_task_slots(cur):
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
    command = tp.command_text(tool_name, tool_input)

    # Native Codex children can report their host cwd rather than the managed
    # PR checkout. Resolve an absolute sealed lease back to its checkout
    # BEFORE loading/screening the contract; doing this afterward made the
    # exact allowlisted path look external and forced models into mkdir/touch
    # workarounds that the engine should own.
    _write_paths = tp.write_paths(tool_name, tool_input)
    _review_candidate = any(
        marker in "/" + str(path).replace("\\", "/")
        for path in _write_paths
        for marker in ("/kernel-v2/results/", "/lenses/results/"))
    _review_ws = None
    _review_authority = None
    _review_lookup_error = None
    if _review_candidate:
        try:
            import review as _review
            _review_authority = _review.leased_result_authority(
                ws, _write_paths)
        except Exception as exc:
            _review_lookup_error = exc
        if _review_authority:
            _review_ws = _review_authority["workspace"]
            ws = _review_ws

    if _review_lookup_error is not None:
        print(json.dumps({
            "decision": "block",
            "reason": "taskplane: leased review result lookup failed "
                      f"closed ({_review_lookup_error})"}))
        return 0

    contract = (_review_authority["contract"] if _review_authority
                else tp.load_active(ws))
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
    # INSPECTION MUST NOT COST. A stuck agent should still be able to say why
    # it is stuck. These commands read and print; none of them changes a
    # contract, a budget, or a ledger, so neither the ceiling nor the meter
    # has anything to protect against them. `clear` is NOT among them — see
    # _RELEASE_VERBS for why.
    if _is_release_command(command):
        # THE DERIVATION LEDGER — RECORDING ONLY, AND LAST (second site).
        #
        # The abstain above happens LONG before the approve path where the
        # ledger is written, so `status`, `contracts`, `version` and `ack`
        # left no row at all: R10 (did the run invent a CLI surface) and
        # every efficiency reading were blind to the release verbs, and a
        # run that polled `tp status` twenty times looked like one that
        # never called it.
        #
        # Same rule, same ordering as the approve site: the decision is
        # already final when this runs — an abstain emits the EMPTY payload,
        # which is complete before the instrument is touched — and nothing
        # here can change it. Failures are swallowed at both layers.
        try:
            sys.stdout.flush()
            import derivation as _dv
            _dv.record(ws, command, "abstain")
        except Exception:                            # noqa: BLE001
            pass
        return 0                      # abstain: not metered, not denied

    # TOKEN CEILING (v2.13.0) — the budget that tracks what is scarce.
    # An action cost ~11k effective tokens on the measured review and ranged
    # over two orders of magnitude, so the action ceiling could never be
    # "tuned": raising it bought tokens sight unseen. This reads what the
    # host RECORDED and fails open in every direction, with the action
    # ceiling still standing underneath.
    if not _is_release_command(command):
        try:
            import spend as _spend
            _tpath = _spend.event_transcript(event)
            _rep = _spend.read_transcript(_tpath) if _tpath else None
            if _rep and _rep.get("available"):
                _tok_ok, _tok_why = _spend.status(contract, _rep["effective"])
                if not _tok_ok:
                    _meter_bump(ws, tid, "denies")
                    tp.trace(ws, "token_budget_deny", tool=tool_name,
                             effective=_rep["effective"])
                    print(json.dumps({
                        "decision": "block",
                        "reason": f"taskplane contract {tid}: {_tok_why}"}))
                    return 0
        except Exception:
            pass

    ok, reason = tp.budget_status(
        contract, used, reserve=_CLOSING_RESERVE,
        closing=_is_closing_command(command))
    if not ok:
        _meter_bump(ws, tid, "denies")
        tp.trace(ws, "budget_deny", tool=tool_name, used=used,
                 max=(contract.get("budget") or {}).get("max_actions"))
        print(json.dumps({
            "decision": "block",
            "reason": f"taskplane contract {tid}: {reason}",
        }))
        return 0

    # OBLIGATION → PROHIBITION. A hook can deny an action; it cannot compel
    # one. That asymmetry is why every prohibition in this product holds at
    # 100% and every obligation ("show the board", "show the graph") held at
    # 0% — five structural attempts to fix it by instruction all failed,
    # because an instruction is not a mechanism. So a BINDING obligation
    # borrows the enforcement that already works: it does not demand the
    # render, it refuses the CONCLUSION until the render has happened.
    #
    # Deliberately narrow. Only taskplane's own completion commands are
    # reachable here, so this can never block an edit, a test, or any part
    # of doing the work — only the act of declaring it finished. Best
    # effort: if the ledger cannot be read, the command proceeds, because a
    # broken instrument must not become a broken product.
    try:
        import obligations as _ob
        _owed = _ob.blocked_reason(ws, command)
    except Exception:
        _owed = None
    if _owed:
        _meter_bump(ws, tid, "denies")
        tp.trace(ws, "obligation_deny", tool=tool_name,
                 open=len(_ob.blocking(ws)))
        print(json.dumps({"decision": "block", "reason": _owed}))
        return 0

    # A REVIEW MUST NAME THE TREE IT REVIEWED (v2.12.0).
    #
    # Same conversion as the obligations above, applied to a different
    # unprovable claim. Two field reviews of one PR both cloned the
    # repository and neither could prove it: the contract recorded no
    # origin, no base, no head, so a review conducted entirely from a
    # rendered web diff would have produced identical artifacts and an
    # identical gate. Now a read-only review cannot DECLARE ITSELF FINISHED
    # until the workspace is pinned to a checkout — never blocking the
    # review itself, only the conclusion, and only for read-only contracts
    # (a build contract already carries its snapshot).
    if contract.get("read_only"):
        try:
            import target as _tgt
            _unbound = (
                _tgt.binding_problem(ws)
                if _is_completion_command(command)
                else None)
        except Exception:
            _unbound = None
        if _unbound:
            _meter_bump(ws, tid, "denies")
            tp.trace(ws, "target_unbound_deny", tool=tool_name)
            print(json.dumps({
                "decision": "block",
                "reason": "taskplane: " + _unbound}))
            return 0

    allow, reason = tp.screen_tool(contract, tool_name, tool_input, ws)
    if allow:
        # A leased review result needs evidence stronger than the JSON's own
        # `authored_by` string.  The always-on write hook records the observed
        # host session and exact active producer contract before the write is
        # allowed; collect later requires this separate receipt.
        if _review_candidate and not _review_authority:
            _meter_bump(ws, tid, "denies")
            tp.trace(ws, "slot_provenance_deny", tool=tool_name,
                     error="UnleasedResultPath")
            print(json.dumps({
                "decision": "block",
                "reason": "taskplane: leased review result lookup failed "
                          "closed (write is not an active leased result path)"}))
            return 0
        if _review_authority:
            try:
                _review.record_slot_write_observation(
                    _review_ws, event=event, contract=contract,
                    task_slot=_review_authority["task_slot"])
            except Exception as exc:
                _meter_bump(ws, tid, "denies")
                tp.trace(ws, "slot_provenance_deny", tool=tool_name,
                         error=type(exc).__name__)
                print(json.dumps({
                    "decision": "block",
                    "reason": "taskplane: leased review result provenance "
                              f"could not be established ({exc})"}))
                return 0
        _meter_bump(ws, tid, "actions")
        # Codex does not support the legacy PreToolUse
        # {"decision":"approve"} shape. A successful hook with no output
        # means continue while preserving Codex's normal sandbox/approval
        # policy. Claude events do not carry Codex's turn_id extension, so
        # retain the existing approval response there for backwards
        # compatibility.
        if "turn_id" not in event:
            print(json.dumps({"decision": "approve"}))
        # THE DERIVATION LEDGER — RECORDING ONLY, AND LAST.
        #
        # Until now an ALLOWED command left no trace of WHAT RAN: only
        # refusals reached trace.jsonl (`hook_deny`, just below), so nothing
        # could answer "did this run re-derive the diff/impact it already
        # had" (R7a) or "did it invoke a subcommand that does not exist"
        # (R10). This records the VERB — never the command text or its
        # arguments — beside the meter that already counted the action.
        #
        # Ordering is deliberate and tested: the decision payload is written
        # and flushed BEFORE the instrument is touched, so a slow, blocked
        # or broken ledger cannot delay, alter or lose a decision the agent
        # is waiting on. derivation.record swallows its own failures; this
        # try/except is the layer that still stands if it ever stops.
        try:
            sys.stdout.flush()
            import derivation as _dv
            _dv.record(ws, command, "approve")
        except Exception:                            # noqa: BLE001
            pass
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

def _loop_status_snapshot(ws: str) -> dict:
    try:
        import loop as loopmod
        snapshot = loopmod.status(ws)
        raw = loopmod._load_raw(ws)
        if isinstance(raw, dict):
            snapshot["task_merges"] = raw.get("task_merges") or {}
            snapshot["worktree_cleanups"] = raw.get("worktree_cleanups") or {}
        return snapshot
    except Exception as exc:
        return {"loop": "unavailable",
                "error": f"{exc.__class__.__name__}: {exc}"}


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
        loop_status = _loop_status_snapshot(ws)
        saved = ((loop_status.get("enforcement") or {}).get("current")
                 if isinstance(loop_status, dict) else None)
        enforcement, _ = _enforcement_check(ws, saved=saved)
        print(json.dumps({
            "active_contract": None,
            "loop": loop_status,
            "enforcement": enforcement,
            "foreign_interference": collision_kernel.load_ledger(ws),
            "headline": ("no active contract; project loop status is shown "
                         "below"),
        }, indent=2))
        return 0
    projection = tp.contract_projection(c)
    budget = projection["budget"]
    print(json.dumps({
        "active_contract": "active",
        "task_id": c.get("task_id"), "task": c.get("task"),
        "read_only": bool(c.get("read_only")),
        "write_allow": c.get("write_allow") or [],
        "scope_paths": projection["scope_paths"],
        "out_of_scope_paths": projection["out_of_scope_paths"],
        "deny": projection["deny"],
        "allowed_tools": c.get("allowed_tools") or "(any)",
        "max_actions": budget.get("max_actions"),
        "budget_ceiling_usd": budget.get("max_cost_usd", "(action-metered; "
                                          "no dollar ceiling set)"),
        "budget_note": budget.get("note"),
        "dod": projection["dod"],
        "enforcement": (_saved_enforcement(c.get("enforcement"))
                        or _enforcement_check(ws)[0]),
        "foreign_state": c.get("foreign_state"),
        "foreign_interference": collision_kernel.load_ledger(ws),
        "loop": _loop_status_snapshot(ws),
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
        if getattr(a, "approved_by", None):
            tp.trace(ws, "budget_human_approval",
                     approved_by=a.approved_by, extra_actions=a.grant)
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

def _graph_quality_refusal(ws: str, surface: str) -> dict | None:
    """One persisted producer record, consumed by every strict CLI gate."""
    import depgraph
    graph = depgraph.load(ws)
    errors = depgraph.quality_errors(graph)
    if not errors:
        return None
    return {
        "schema": "taskplane.graph-quality-refusal/v1",
        "error": errors[0],
        "surface": surface,
        "graph_quality": depgraph.scan_quality(graph),
    }


def cmd_dod(a) -> int:
    ws = _workspace(a.workspace)
    refusal = _graph_quality_refusal(ws, "DoD")
    if refusal:
        print(json.dumps(refusal, indent=2))
        return 1
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

    notices: list = []
    errors = tp.dod_check(c, ws, snapshot, notices=notices)
    import kb as kbmod
    errors += [f"{p['file']}: {p['problem']}" for p in kbmod.lint(ws)]
    tp.trace(ws, "dod", passed=not errors, errors=errors, notices=notices)
    if errors:
        print("taskplane DoD: FAIL ❌")
        for e in errors:
            print("  - " + e)
        for n in notices:
            print("  ! " + n)
        return 1
    changed = tp.changed_files(ws, snapshot) if snapshot else []
    projection = tp.contract_projection(c)
    print("taskplane DoD: PASS ✅ (diff in scope"
          + (", tests pass" if projection["test_command"] else "")
          + ")")
    # D-0008: a PASS that nobody executed must say so at the moment it is
    # read, not only in the trace.
    for n in notices:
        print("  ! " + n)
    if changed:
        print("  files changed (in scope): " + ", ".join(changed[:12]))
    return 0


# --------------------------------------------------------------- loop

def _loop_evidence_workspaces(loopmod, workspace: str,
                              task_id: str | None) -> tuple:
    """Return (authority, evidence, error) for one exact task claim.

    Parallel evaluators operate on claimed worktree bytes while loop state
    remains owned by the primary checkout. Managed locators name that parent;
    legacy nested worktrees are resolved only among this Git repository's
    worktrees, and only a canonical task record with an exact path match wins.
    """
    origin = os.path.realpath(workspace)
    state = loopmod._load_raw(origin)
    authority = origin if state is not None else None
    if authority is None:
        import storage as runtime_storage
        candidates = []
        try:
            locator = runtime_storage.load_workspace_locator(origin)
        except runtime_storage.StorageIdentityError:
            locator = None
        if locator:
            candidates.append(str(locator.get("primary_checkout") or ""))
        try:
            listed = tp._run(
                ["git", "worktree", "list", "--porcelain"], cwd=origin)
            candidates.extend(
                line[len("worktree "):]
                for line in listed.stdout.splitlines()
                if line.startswith("worktree "))
        except OSError:
            candidates = []
        matches = []
        for raw in dict.fromkeys(candidates):
            candidate = os.path.realpath(str(raw or ""))
            if not candidate or candidate == origin:
                continue
            try:
                candidate_state = loopmod._load_raw(candidate)
            except (OSError, tp.StateError):
                continue
            for task in (candidate_state or {}).get("tasks") or []:
                if task_id is not None and str(task.get("id")) != str(task_id):
                    continue
                claimed = str(task.get("workspace") or "")
                if claimed and os.path.realpath(claimed) == origin:
                    matches.append((candidate, candidate_state))
                    break
        if len(matches) != 1:
            return None, None, ({"error": "no active loop"} if not matches
                                else {"error": "multiple active loops claim "
                                               "this task worktree"})
        authority, state = matches[0]

    task = (loopmod._current_task(state) if task_id is None else
            next((row for row in state.get("tasks") or []
                  if str(row.get("id")) == str(task_id)), None))
    if task is None:
        return authority, authority, None
    claimed = str(task.get("workspace") or "")
    evidence_ws = (os.path.realpath(claimed)
                   if claimed and os.path.isdir(claimed) else authority)
    if origin != authority and origin != evidence_ws:
        return None, None, {
            "error": "this worktree is not the claimed workspace for "
                     f"task {task.get('id')!r}"}
    return authority, evidence_ws, None

def cmd_loop(a) -> int:
    """Drive the taskplane-owned Evaluate-Loop state machine."""
    import loop as loopmod
    ws = _workspace(a.workspace)
    action = a.loop_action
    out = None
    enforcement = None
    if action in {"next", "gate"}:
        refusal = _graph_quality_refusal(ws, "Design/Plan/Review/DoD")
        if refusal:
            print(json.dumps(refusal, indent=2))
            return 1
    guarded_actions = {"init", "next", "wave", "claim", "gate", "approve"}
    if action in guarded_actions:
        current = loopmod.load(ws)
        enforcement, refusal = _enforcement_check(
            ws, saved=(current or {}).get("enforcement"),
            advisory=bool(getattr(a, "advisory", False)),
            actor=getattr(a, "by", None),
            run_id=(current or {}).get("run_id"),
            revision=((current or {}).get("baseline") or tp.git_head(ws)))
        if refusal:
            print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
            return 1
        if current is not None:
            loopmod.record_enforcement(ws, enforcement)
    if action == "init":
        checkpoints = (a.checkpoints.split(",") if a.checkpoints is not None
                       else ["plan", "em"])
        st = loopmod.init(ws, " ".join(a.goal or []) or (a.spec or "spec"),
                          spec_path=a.spec, max_fix_cycles=a.max_fix_cycles,
                          checkpoints=[c for c in checkpoints if c],
                          requirement_id=a.req, parallel=a.parallel,
                          design=a.design, design_only=a.design_only,
                          force=getattr(a, "force", False),
                          by=getattr(a, "by", None),
                          reuse_approved_design=getattr(
                              a, "reuse_approved_design", False))
        if isinstance(st, dict) and not st.get("error") and enforcement:
            loopmod.record_enforcement(ws, enforcement)
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
        import depgraph
        try:
            with depgraph.strict_quality():
                out = loopmod.next_action(ws, rid=getattr(a, "req", None))
        except depgraph.GraphQualityDegraded as exc:
            out = {"error": str(exc), "step": "graph-quality"}
    elif action == "submit":
        out = loopmod.submit(ws, a.outcome, note=a.note or "", task_id=a.task)
    elif action == "gate":
        import depgraph
        try:
            with depgraph.strict_quality():
                out = loopmod.gate(
                    ws, a.outcome, note=a.note or "", task_id=a.task,
                    rid=getattr(a, "req", None))
        except depgraph.GraphQualityDegraded as exc:
            out = {"error": str(exc), "step": "graph-quality"}
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
    elif action == "replan":
        out = loopmod.replan(ws, by=a.by, reason=a.reason)
    elif action == "evidence":
        state_ws, evidence_ws, error = _loop_evidence_workspaces(
            loopmod, ws, getattr(a, "task", None))
        if error:
            out = error
        else:
            token = loopmod._EVIDENCE_STATE_WORKSPACE.set(state_ws)
            try:
                out = loopmod.evidence(
                    evidence_ws, task_id=getattr(a, "task", None),
                    write=getattr(a, "write", False))
            finally:
                loopmod._EVIDENCE_STATE_WORKSPACE.reset(token)
    elif action == "guide":
        out = loopmod.guide(ws, task_id=getattr(a, "task", None))
    elif action == "authorize":
        # Host/facade dispatchers derive routine authority through the same
        # production boundary as the loop engine.  A CLI surface keeps host
        # adapters from reimplementing receipt or target checks.
        out = loopmod.authorize_routine_flow(ws, a.flow)
    elif action == "host-input":
        # Taskplane runs inside one trusted local Codex/Claude session.  Its
        # host adapter supplies separate session attribution; actor/thread/
        # revision/boolean labels in the event body are never authority.
        try:
            event = json.load(sys.stdin)
        except (json.JSONDecodeError, UnicodeError) as exc:
            out = {"error": f"host event must be valid JSON: {exc.msg}"}
        else:
            if not isinstance(event, dict):
                out = {"error": "host event must be a JSON object"}
            else:
                raw_host_event = os.environ.get(
                    "TASKPLANE_HOST_SESSION_EVENT", "")
                try:
                    host_event = json.loads(raw_host_event) \
                        if raw_host_event else None
                except json.JSONDecodeError:
                    host_event = None
                out = loopmod.handle_host_input(
                    ws, event, host_event=host_event)
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
    if isinstance(out, dict):
        canonical = enforcement or _saved_enforcement(
            (loopmod.load(ws) or {}).get("enforcement"))
        if canonical:
            out.setdefault("enforcement", canonical)
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


_MAX_STAGE_COMMAND_BYTES = 1024 * 1024


def _stage_command_request(source: str) -> tuple[dict | None, dict | None]:
    """Read one closed, bounded JSON object from a file or standard input."""
    try:
        if source == "-":
            body = sys.stdin.read(_MAX_STAGE_COMMAND_BYTES + 1)
        else:
            with open(source, encoding="utf-8") as handle:
                body = handle.read(_MAX_STAGE_COMMAND_BYTES + 1)
    except (OSError, UnicodeError) as exc:
        return None, {"error": f"stage request is unavailable: {exc}"}
    if len(body.encode("utf-8")) > _MAX_STAGE_COMMAND_BYTES:
        return None, {
            "error": "stage request exceeds the 1048576-byte bound",
        }
    try:
        request = json.loads(body)
    except (json.JSONDecodeError, UnicodeError) as exc:
        return None, {"error": f"stage request must be valid JSON: {exc}"}
    if not isinstance(request, dict):
        return None, {"error": "stage request must be a JSON object"}
    return request, None


def cmd_stage(a) -> int:
    """Drive stage-native lifecycle commands through one JSON boundary.

    ``stage_entities`` remains a lazy runtime dependency: importing the CLI
    and using legacy ``loop next``/``loop wave`` never loads the stage domain.
    The loop adapter owns rollout gating, receipt validation and dispatch.
    """
    request, error = _stage_command_request(a.request)
    if error is not None:
        print(json.dumps(error, indent=2))
        return 1
    import loop as loopmod
    out = loopmod.stage_command(
        _workspace(a.workspace), a.stage_action, request)
    if not isinstance(out, dict):
        out = {"error": "stage runtime returned a non-object result"}
    print(json.dumps(out, indent=2))
    return 1 if out.get("error") else 0


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
        if step == "evaluate":
            for field in ("output_contract", "output_schema",
                          "resume_identity", "max_attempts"):
                if field in payload:
                    brief[field] = payload[field]
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


def tp_target_diff(ws: str, base: str) -> tuple:
    """The diff every lens agent would otherwise re-derive. Bounded: a diff
    too large to be shared cheaply is not shared at all, and the briefs fall
    back to embedding the blast radius as before."""
    import review
    return review.canonical_diff_patch(ws, base)


def _lane_landed(ws: str, lid: str) -> bool:
    """Has this lens already written evidence? The SAME source the wave board
    reads (v2.2.1), so `--resume` and the board can never disagree about who
    is done. A findings.json that is unreadable counts as NOT landed — a
    corrupt lane must be re-run, never silently accepted as complete."""
    import storage as runtime_storage
    p = runtime_storage.lane_findings_path(ws, lid)
    if not os.path.isfile(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            return isinstance(json.load(f).get("findings"), list)
    except (OSError, ValueError, AttributeError):
        return False


def _resume_filter(ws: str, briefs: dict) -> dict:
    """Drop the briefs whose evidence is already on disk.

    An interrupted wave is the single most expensive accident in this
    product: four of ten sub-agent transcripts in one measured session
    existed because a fan-out was spawned in a turn that died before the
    agents reported, and the whole wave was paid for twice (~16% of that
    session's effective tokens). Everything needed to avoid it was already
    written down — each lens writes its own findings.json the moment it
    lands. This reads it."""
    out = dict(briefs)
    kept, skipped = [], []
    for b in briefs.get("deep") or []:
        (skipped if _lane_landed(ws, b["id"]) else kept).append(b)
    out["deep"] = kept
    sw = briefs.get("sweep")
    if sw and _lane_landed(ws, "sweep"):
        out["sweep"] = None
        skipped.append(sw)
    out["resumed"] = {
        "skipped": [b.get("id") or "sweep" for b in skipped],
        "dispatching": [b["id"] for b in kept] + (["sweep"] if out.get("sweep")
                                                  else []),
    }
    if skipped and not kept and not out.get("sweep"):
        out["nothing_to_review"] = True
        out["instruction"] = (
            "Every lane already has findings on disk — dispatch NOTHING. "
            "Merge the existing lens findings into `tp findings` and render "
            "them for the human review gate.")
    elif skipped:
        out["instruction"] = (
            f"RESUMING an interrupted wave: "
            f"{len(skipped)} lane(s) already reported and are NOT being "
            f"re-dispatched ({', '.join(out['resumed']['skipped'])}). "
            + out.get("instruction", ""))
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

    # v2.11.0 — the CLI now ASKS for signal-driven routing.
    #
    # route v2 (the applicability engine: content + graph + requirement
    # signals, per-lens deep | light | n/a with machine-checkable negative
    # evidence) shipped in v2.4.0 and was never reachable from here.
    # `route()` enables it only when `stage` or `use_signals` is passed, and
    # `cmd_lens` passed neither — so every `lens route` and every
    # `lens dispatch`, which is where the review actually spends its tokens,
    # took the glob-based legacy path. The one caller in the codebase that
    # passed `stage="review"` was audit.py, the coverage REPORTER. The
    # engine scored the diff for a report and the wave ignored it.
    #
    # Measured on a reconstruction of aws/karpenter-provider-aws#9464 (a Go
    # type addition plus a docs edit): legacy routed 6 lenses deep and
    # marked nothing n/a; route v2 routed 2 deep, 4 light, and 20 n/a —
    # each n/a carrying its evidence, e.g. product -> "0 product signals:
    # no spec/requirements files, no acceptance-criteria markers". The
    # field run dispatched 6 agents and 336k tokens for that diff.
    #
    # Coverage is NOT reduced by this — it is DISCLOSED. v2 emits an entry
    # for every catalog lens and `routing_decision` carries each one's
    # verdict, so the dashboard still shows all 26 and can now say WHY a
    # lens did not run instead of running it to avoid the question. A
    # failing engine still falls open to legacy breadth=all (more coverage,
    # never less) and says so.
    stage = None if getattr(a, "breadth_all", False) else "review"
    breadth = "all" if getattr(a, "breadth_all", False) else "routed"
    if getattr(a, "artifact_type", None):
        routing = lensmod.route([], artifact_type=a.artifact_type,
                                only=(a.only.split(",") if a.only else None),
                                skip=(a.skip.split(",") if a.skip else None),
                                breadth=breadth, stage=stage, workspace=ws)
    else:
        routing = lensmod.route_git_diff(ws, base=a.base, task_type=a.task_type,
                                         only=(a.only.split(",") if a.only else None),
                                         skip=(a.skip.split(",") if a.skip else None),
                                         breadth=breadth, stage=stage)

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
        # B9 (v2.10.0): probe build/test runnability ONCE, here, and put the
        # verdict in every brief. On karpenter#9464 six lens agents each
        # burned actions discovering `go test` could not run — one fact about
        # the checkout, paid for six times. Cached per tree state, so
        # re-rendering the wave board costs nothing.
        run_probe = None
        try:
            import runnability as runmod
            run_probe = runmod.probe_once(ws)
            if not (run_probe.get("checks") or run_probe.get("skipped")):
                run_probe = None
        except Exception:
            run_probe = None
        # v2.13.0: write the diff + blast radius ONCE and cite the paths,
        # instead of embedding a copy in every brief at output weight.
        ctx_paths = {}
        if not getattr(a, "dashboard", False):
            try:
                import review as _rv
                _diff = ""
                if a.base:
                    _rc, _diff = tp_target_diff(ws, a.base)
                ctx_paths = _rv.write_context(
                    ws, diff=_diff, blast_radius=impact_ctx or "")
            except Exception:
                ctx_paths = {}
        briefs = lensmod.dispatch_briefs(routing, base=a.base,
                                         max_actions=a.max_actions,
                                         impact_context=(
                                             None if ctx_paths else impact_ctx),
                                         runnability=run_probe,
                                         context_paths=ctx_paths)
        # --resume: a wave interrupted mid-flight (a killed turn, a crashed
        # host) used to cost the WHOLE fan-out again — measured at ~16% of
        # one real session's tokens, because four of ten lens agents were
        # spawned twice. Landed evidence is already on disk and the wave
        # board already reads status from it; this makes the DISPATCH read
        # the same source. A lane with findings.json is done, so it is not
        # re-briefed. Deliberately NOT the default: a fresh review of a
        # changed diff must re-run every lens, and silently reusing stale
        # findings would be the worse failure.
        # ...and never on --dashboard: the board is the human's view of the
        # WHOLE wave, including the lanes that already landed. Filtering it
        # would hide exactly the progress it exists to show.
        if getattr(a, "resume", False) and not getattr(a, "dashboard", False):
            briefs = _resume_filter(ws, briefs)
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
                import storage as runtime_storage
                p = runtime_storage.lane_findings_path(ws, lid)
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
            _sub = (f"{done}/{len(lanes)} lens-agents reported · read-only, "
                    f"in parallel · diff vs {briefs['base']}")
            if run_probe and run_probe.get("checks"):
                # The wave board is where the human watches the fan-out, so
                # it is where "the tests can't run here" belongs — before
                # they read a review that had to be static.
                _sub += " · " + run_probe.get("summary", "")
            print(dashboard.render_lens_wave(
                lanes, {"title": ("review — wave complete"
                                  if done == len(lanes) else
                                  "review — lenses running"),
                        "subtitle": _sub}))
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
                          "file": req.requirement_file(ws, e),
                          "store_file": e["file"],
                          "next": f"tp req amend {e['id']} <fields>"},
                         indent=2))
    elif a.req_action == "amend":
        try:
            nfr = (dict(kv.split("=", 1) for kv in (a.nfr or []))
                   if a.nfr is not None else None)
        except ValueError:
            print("taskplane: --nfr expects LENS=STATEMENT", file=sys.stderr)
            return 1
        try:
            e = req.amend_requirement(
                ws, a.id,
                functional=a.functional,
                nfr=nfr,
                acceptance=a.acceptance,
                open_questions=a.open,
                clear_open=bool(a.clear_open),
                context_files=(a.files.split(",")
                               if a.files is not None else None))
        except req.ProductSignoffError as exc:
            print(f"taskplane: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"amended": e["id"], "status": e["status"],
                          "file": req.requirement_file(ws, e),
                          "store_file": e["file"]}, indent=2))
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
    elif a.req_action == "signoff":
        try:
            result = req.product_signoff(
                ws, a.id, decision=a.decision, by=a.by,
                note=a.note or "")
        except req.ProductSignoffError as exc:
            print(f"taskplane: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
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


def _ensure_excluded(ws, entries, header) -> list:
    """Ignore paths WITHOUT touching a tracked file (v2.11.0).

    `.git/info/exclude` is per-checkout, never committed, and does exactly
    what `.gitignore` does for the person running the command. Writing to
    `.gitignore` instead had three costs during a review of somebody else's
    repository, all observed on aws/karpenter-provider-aws#9464: the working
    tree went dirty on top of the exact commit under review; the file joined
    `git diff <base>`, so routing reported "5 files changed" for a 4-file PR
    and every lens brief had to be told to ignore it; and git-cleanliness
    hooks then demanded a commit that would have been wrong to make in a
    third-party repo.

    Falls back to `.gitignore` only where there is no `.git` directory to
    write into (a worktree or a non-repo), so the paths still get ignored.
    """
    git_dir = os.path.join(ws, ".git")
    if os.path.isdir(git_dir):
        info = os.path.join(git_dir, "info")
        path = os.path.join(info, "exclude")
        existing = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                existing = f.read()
        missing = [e for e in entries if e not in existing]
        if missing:
            try:
                os.makedirs(info, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write("\n# " + header + "\n" + "\n".join(missing) + "\n")
                return missing
            except OSError:
                pass          # unwritable .git — fall through to .gitignore
        else:
            return missing
    return _ensure_gitignored(ws, entries, header)


def _ensure_gitignored(ws, entries, header) -> list:
    """Append any missing entries to the repo .gitignore. Returns what it
    added. Prefer `_ensure_excluded` — see its docstring."""
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
    ignored = _ensure_excluded(
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
    missing = _ensure_excluded(
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


_HOOK_COMMANDS = frozenset({
    "screen", "screen-skill", "screen-dispatch", "screen-render", "context",
    "subagent-start", "subagent-stop", "session-verify",
})


def _hook_response_class(command: str, output: str, returncode: int) -> str:
    """Reduce a hook response to the only datum safe to replay.

    The response body may contain contract details or model-supplied context,
    so the claim journal deliberately stores only this bounded class.  Empty
    and explicit allow remain distinct: replaying an approval for a Codex
    hook that originally abstained would bypass the host's normal permission
    policy.
    """
    if returncode:
        return "block" if returncode > 0 else "error"
    body = output.strip()
    if not body:
        return "empty"
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return "context" if command == "context" else "advisory"
    if not isinstance(payload, dict):
        return "advisory"
    hook_output = payload.get("hookSpecificOutput")
    hook_output = hook_output if isinstance(hook_output, dict) else {}
    if payload.get("decision") == "block" or \
            hook_output.get("permissionDecision") == "deny":
        return "block"
    if "additionalContext" in hook_output:
        return "context"
    if "systemMessage" in payload:
        return "advisory"
    if payload.get("decision") == "approve" or not payload:
        return "allow"
    return "advisory"


def _replay_hook_response(command: str, response_class: str) -> int:
    """Replay protocol semantics without replaying a hook's side effects."""
    if response_class in {"block", "error"}:
        reason = ("taskplane already processed this lifecycle event; the "
                  "first blocking decision remains authoritative.")
        event_name = {
            "subagent-stop": "SubagentStop",
            "session-verify": "Stop",
        }.get(command, "PreToolUse")
        print(json.dumps({
            "decision": "block", "reason": reason,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }))
        return 2 if command in {"subagent-stop", "session-verify"} else 0
    if response_class == "allow":
        if command == "screen":
            print(json.dumps({"decision": "approve"}))
        elif command == "subagent-stop":
            print("{}")
        return 0
    if response_class == "context":
        event_name = ("SubagentStart" if command == "subagent-start"
                      else "SessionStart")
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": ("[taskplane] This lifecycle event was "
                                  "already processed by the other installed "
                                  "hook path."),
        }}))
        return 0
    if response_class == "advisory":
        print(json.dumps({
            "systemMessage": ("taskplane already processed this lifecycle "
                              "event through the other installed hook path.")
        }))
    return 0


def _run_hook_command(a) -> int:
    """Claim native/bridge hook events once, execute once, replay by class."""
    hook_path = (os.environ.get("TASKPLANE_HOOK_PATH") or "").strip().lower()
    if a.cmd not in _HOOK_COMMANDS or hook_path not in {"native", "bridge"}:
        return a.fn(a)

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    event_cwd = event.get("cwd")
    workspace = _workspace(
        event_cwd if isinstance(event_cwd, str) and event_cwd
        else getattr(a, "workspace", None))
    try:
        host_caps.record_runtime_hook_receipt(
            tp.store_home(), hook_path=hook_path, event=event)
    except Exception:
        # Runtime receipt is onboarding evidence, never a reason to break the
        # enforcement hook that is already executing.
        pass
    claim = tp.claim_hook_event(
        workspace, a.cmd, event, hook_path=hook_path)
    if not claim.get("execute"):
        return _replay_hook_response(
            a.cmd, str(claim.get("response_class") or "block"))

    original_stdin = sys.stdin
    captured = io.StringIO()
    try:
        sys.stdin = io.StringIO(raw)
        with contextlib.redirect_stdout(captured):
            returncode = a.fn(a)
    except Exception:
        tp.complete_hook_event(workspace, claim, response_class="error")
        raise
    finally:
        sys.stdin = original_stdin

    output = captured.getvalue()
    response_class = _hook_response_class(a.cmd, output, int(returncode or 0))
    tp.complete_hook_event(
        workspace, claim, response_class=response_class)
    sys.stdout.write(output)
    return int(returncode or 0)


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


def _contracts_elsewhere(ws: str, limit: int = 4) -> list:
    """Other checkouts in this store that currently hold a taskplane
    contract. Governance is keyed on cwd, so "you are in the wrong
    directory" is the single most useful thing to say when a governed
    command finds nothing here — and the store already knows which
    directories the session has been governing."""
    out = []
    here = os.path.realpath(os.path.abspath(ws))
    projects = os.path.join(tp.store_home(), "projects")
    for name in sorted(os.listdir(projects)) if os.path.isdir(projects) else []:
        meta_path = os.path.join(projects, name, "meta.json")
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        owner = meta.get("workspace_realpath") or meta.get("workspace")
        if not owner:
            continue
        owner = os.path.realpath(os.path.abspath(owner))
        if owner == here or not os.path.isdir(owner):
            continue
        active = os.path.join(owner, ".taskplane", "active")
        has = (os.path.isfile(os.path.join(owner, ".taskplane",
                                           "active_contract.json"))
               or (os.path.isdir(active)
                   and any(n.endswith(".json") for n in os.listdir(active))))
        if has:
            out.append(owner)
        if len(out) >= limit:
            break
    return out


def cmd_ack(a) -> int:
    """Discharge an obligation the engine issued (WS-F).

    The engine can render an artifact, write it to disk and point at it. It
    can now also SEE the render tool run, via the `mcp__visualize__.*`
    PreToolUse matcher (`tp screen-render`) — so an ack is no longer the only
    record. It remains a CLAIM and is never renamed to proof, because what a
    hook observes is a tool call, not a human's attention. What the two
    records do together is separate a skip from a substitute from an
    unsupported claim. None of it blocks anything.

    `--fingerprint` is what separates showing the product's artifact from
    showing a substitute. `tp ack <id>` alone reads the fingerprint off the
    artifact the obligation NAMES, so the honest path is also the short one;
    passing a different one on purpose is recorded as a mismatch rather than
    a success.
    """
    import obligations
    ws = _workspace(a.workspace)
    if getattr(a, "status", False):
        st = obligations.status(ws)
        print(json.dumps({
            "issued": st["issued"], "acknowledged": st["acknowledged"],
            "open": [{"id": o["id"], "kind": o["kind"], "step": o.get("step"),
                      "detail": o.get("detail")} for o in st["open"]],
            "mismatched": [{"id": o["id"], "kind": o["kind"],
                            "expected_fingerprint": o.get("fingerprint"),
                            "cited": o.get("cited")}
                           for o in st["mismatched"]],
            "observed": st["observed"],
            "corroborated": [o["id"] for o in st["corroborated"]],
            "claimed_only": [{"id": o["id"], "kind": o["kind"]}
                             for o in st["claimed_only"]],
            "substituted": [{"tool": r.get("tool"), "title": r.get("title"),
                             "fingerprint": r.get("fingerprint"),
                             "bytes": r.get("bytes")}
                            for r in st["substituted"]],
            "unparseable": st["unparseable"],
            "note": "issued = the engine's demand; acknowledged = the "
                    "assistant's CLAIM; observed = the render tool actually "
                    "running, seen at the PreToolUse hook. `substituted` is a "
                    "render whose bytes are not the artifact the engine "
                    "built; `claimed_only` is an ack with no observation "
                    "behind it. An observation is a fact about a tool call, "
                    "never proof that a human looked.",
        }, indent=2))
        return 0
    if not getattr(a, "id", None):
        print("taskplane: ack needs an obligation id (or --status)",
              file=sys.stderr)
        return 1
    # AN ACK MUST NAME AN OBLIGATION THIS WORKSPACE ISSUED (v2.11.0).
    #
    # Governance is keyed on cwd. In the field a shell's working directory
    # reverted from the review checkout to the session home mid-run, and
    # `tp ack <id>` there returned "acknowledged" for three real obligation
    # ids against an empty directory with no contract and no ledger — while
    # the real obligations stayed open. Nothing errored. `ack --status`
    # then reported `issued: 0`, which reads as "nothing owed" rather than
    # "you are in the wrong workspace". A governance command that succeeds
    # at nothing and says OK is worse than one that fails.
    issued = {row.get("id"): row for row in obligations.read(ws)
              if row.get("event") == "issued"}
    if a.id not in issued:
        elsewhere = _contracts_elsewhere(ws)
        msg = (f"taskplane: no obligation '{a.id}' was issued in this "
               f"workspace ({ws}) — refusing to acknowledge it.")
        if not issued:
            msg += (" This workspace has no obligation ledger at all, which "
                    "usually means the working directory is not the one the "
                    "run was started in.")
        else:
            msg += (" Obligations issued here: "
                    + ", ".join(sorted(issued)) + ".")
        if elsewhere:
            msg += (" Active taskplane contracts exist in: "
                    + ", ".join(elsewhere)
                    + " — re-run with `--workspace <that path>`.")
        print(msg, file=sys.stderr)
        return 1
    fp = getattr(a, "fingerprint", None)
    if not fp:
        art = issued[a.id].get("artifact")
        if art:
            fp = obligations.artifact_fingerprint(
                os.path.join(ws, art) if not os.path.isabs(art) else art)
    delivered = getattr(a, "delivered", None)
    if delivered:
        # Delivering the engine's file is not a weaker discharge than
        # rendering it inline — it is the SAME bytes, and the fingerprint is
        # what the ledger compares either way. It is simply the one that
        # does not cost a full re-authoring of the document.
        fp = obligations.artifact_fingerprint(
            delivered if os.path.isabs(delivered)
            else os.path.join(ws, delivered)) or fp
        obligations.observe(ws, tool="delivered_file",
                            fingerprint=fp, title=os.path.basename(delivered),
                            bytes_len=(os.path.getsize(delivered)
                                       if os.path.isfile(delivered) else 0),
                            session=None)
    obligations.acknowledge(ws, a.id, evidence=getattr(a, "evidence", "") or "",
                            fingerprint=fp)
    print(f"acknowledged {a.id}" + (f" ({fp})" if fp else ""))
    return 0


def cmd_dashboard(a) -> int:
    """Emit the mission-control view. Default: the inline widget fragment
    for mcp__visualize__show_widget (the driver pipes it straight in).
    --out also writes a standalone HTML file (no-desktop fallback)."""
    import dashboard
    ws = _workspace(a.workspace)
    report = dashboard.report_widget(ws)
    if a.out:
        doc = dashboard.standalone_document(
            [report], title="taskplane — mission control")
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".",
                    exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="") as f:
            f.write(doc)
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
    print(report)
    return 0


def _write_review_html(ws: str, name: str, fragments, *, title: str) -> dict:
    """Write durable HTML plus a widget fragment for inline delivery."""
    import dashboard
    import hashlib
    import review as review_runtime

    path = os.path.join(review_runtime._public_root(ws), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fragments = list(fragments)
    body = dashboard.standalone_document(fragments, title=title)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="") as stream:
        stream.write(body)
    os.replace(tmp, path)
    fragment_path = os.path.splitext(path)[0] + ".fragment.html"
    fragment_body = ('<style>' + dashboard.inline_review_style() + '</style>'
                     '<div class="tp-inline-review" '
                     'id="tp-inline-review-root">' +
                     "\n".join(fragments) + '</div>')
    fragment_tmp = f"{fragment_path}.tmp.{os.getpid()}"
    with open(fragment_tmp, "w", encoding="utf-8", newline="") as stream:
        stream.write(fragment_body)
    os.replace(fragment_tmp, fragment_path)
    raw = body.encode("utf-8")
    relative = os.path.relpath(path, ws)
    display_path = (relative.replace(os.sep, "/")
                    if relative != ".." and not relative.startswith(
                        ".." + os.sep) else path)
    fragment_relative = os.path.relpath(fragment_path, ws)
    fragment_display = (fragment_relative.replace(os.sep, "/")
                        if fragment_relative != ".." and not
                        fragment_relative.startswith(".." + os.sep)
                        else fragment_path)
    fragment_raw = fragment_body.encode("utf-8")
    return {
        "path": display_path,
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "inline": {"path": fragment_display, "bytes": len(fragment_raw),
                   "sha256": hashlib.sha256(fragment_raw).hexdigest()},
        "delivery": "render inline from inline.path using the host widget; "
                    "use path only as a fallback if inline rendering fails",
    }


def _bind_review_visual_obligation(ws: str, kind: str, artifact: str) -> dict:
    """Bind a seeded Review obligation to the bytes now on disk."""
    import obligations

    seeded = next((row for row in reversed(obligations.read(ws))
                   if row.get("event") == "issued"
                   and row.get("kind") == kind
                   and row.get("step") == "review"), None)
    detail = ("deliver the taskPlane lens workflow/dashboard by reference"
              if kind == "render_dashboard" else
              "deliver the taskPlane dependency/blast-radius graph by reference")
    oid = obligations.issue(
        ws, kind, detail=detail, step="review", artifact=artifact,
        key=f"review:{kind}", session=(seeded or {}).get("session"),
        binding=True)
    root = os.path.realpath(ws)
    resolved = os.path.realpath(artifact)
    display_path = (os.path.relpath(resolved, root).replace(os.sep, "/")
                    if os.path.commonpath((root, resolved)) == root
                    else resolved)
    return {"kind": kind, "id": oid,
            "path": display_path}


def _review_visuals(ws: str, manifest: dict, *, final: bool) -> tuple[dict, list]:
    """Project the ReviewKernel into the canonical taskPlane visual system.

    This is a driver/presentation seam, not a second review derivation.  It
    reads the already sealed envelope/routing/revision and writes one human-
    facing dashboard. The graph is embedded in that dashboard and is never
    emitted as a second user-facing artifact or gate.
    """
    import dashboard
    import review as rv
    import review_evidence as evidence

    state = rv._load_state(ws, manifest.get("run_id"))
    store = evidence.ArtifactStore(ws)
    envelope = {}
    if state.get("envelope"):
        envelope = store.read(state["envelope"])
    impact = envelope.get("impact") or {}
    quality = {}
    if state.get("quality"):
        quality = store.read(state["quality"])
    findings = list((state.get("revision") or {}).get("findings") or [])
    by_lens = {}
    for finding in findings:
        lid = str(finding.get("lens") or "")
        by_lens[lid] = by_lens.get(lid, 0) + 1
    lanes = []
    for slot in state.get("slots") or []:
        lids = list(slot.get("lens_ids") or [])
        result_path = str(slot.get("result_path") or "")
        result_exists = bool(result_path) and os.path.isfile(
            result_path if os.path.isabs(result_path)
            else os.path.join(ws, result_path))
        lanes.append({
            "id": str(slot.get("slot_id") or ",".join(lids)),
            "name": ", ".join(lids),
            "status": "done" if final or result_exists else "running",
            "findings": (sum(by_lens.get(lid, 0) for lid in lids)
                         if final else None),
            "slot_id": slot.get("slot_id"),
        })
    status = str(manifest.get("status") or state.get("status") or "ready")
    workflow = dashboard.render_review_workflow(
        status=status, slots=lanes,
        graph_complete=quality.get("status") == "complete")
    wave = dashboard.render_lens_wave(lanes, {
        "title": ("review — canonical collection complete" if final else
                  "review — selective lenses running"),
        "subtitle": (f"{len(lanes)} leased slot(s) · one immutable context · "
                     "no per-lens diff or graph re-derivation"),
    })
    graph = dashboard.render_review_graph(ws, impact)
    execution = state.get("review_execution") or rv.review_execution_preflight()
    dor_evidence = ((envelope.get("change") or {}).get("dor") or
                    rv.review_dor_evidence(
                        ws, state.get("target") or envelope.get("target") or {},
                        requirement=(envelope.get("requirements") or {}).get(
                            "requirement"),
                        acceptance=(envelope.get("requirements") or {}).get(
                            "acceptance"),
                        contracts=envelope.get("contracts")))
    dor_evidence = dict(dor_evidence)
    dor_evidence["requested_lenses"] = rv._directive_lens_ids(
        dor_evidence.get("review_directives") or [],
        __import__("lens").load_catalog())
    effective_decision = {}
    if state.get("routing_decision"):
        effective_decision = store.read(state["routing_decision"]).get(
            "dispositions") or {}
    dor_evidence["lens_dispositions"] = {
        lid: str((effective_decision.get(lid) or {}).get("verdict") or "n/a")
        for lid in dor_evidence["requested_lenses"]}
    diagnostics = {
        "engine": f"taskplane/{plugin_version()}",
        "routing_policy": hashlib.sha256(
            rv.KERNEL_POLICY_VERSION.encode("utf-8")).hexdigest(),
        "graph": str(quality.get("fingerprint") or
                     (state.get("quality") or {}).get("fingerprint") or
                     "unavailable"),
        "routing_decision": str(
            (state.get("routing_decision") or {}).get("fingerprint") or
            "unavailable"),
    }
    opening = dashboard.render_findings([], {
        "title": "Engineering review — in progress",
        "subtitle": "one canonical diff · graph-qualified selective lenses",
        "graph_fragment": graph,
        "review_execution": execution,
        "dor_evidence": dor_evidence,
        "diagnostic_fingerprints": diagnostics,
    })
    visuals = {
        "workflow_and_wave": _write_review_html(
            ws, "dashboard.html", [workflow, wave, opening],
            title="taskplane — governed review workflow"),
    }
    if final:
        findings_path = os.path.join(rv._public_root(ws), "findings.json")
        try:
            with open(findings_path, encoding="utf-8") as stream:
                projection = json.load(stream)
        except (OSError, ValueError):
            projection = {"findings": findings, "meta": {}}
        final_findings = list(projection.get("findings") or [])
        review_notes = list(projection.get("notes") or
                            (state.get("revision") or {}).get("notes") or [])
        meta = dict(projection.get("meta") or {})
        requirements_validation = meta.get("requirements_validation")
        if not isinstance(requirements_validation, dict):
            envelope_diff = envelope.get("diff") or {}
            diff_ref = envelope_diff.get("artifact")
            diff_record = (store.read(diff_ref)
                           if isinstance(diff_ref, dict) else
                           {"files": envelope_diff.get("files") or [],
                            "patch": ""})
            requirements_validation = rv.evaluate_review_requirements(
                dor_evidence, diff_record, final_findings, execution)
        decision = effective_decision
        runnability = envelope.get("runnability") or {}
        clean_evidence = [check for row in (state.get("lens_results") or [])
                          if row.get("verdict") == "pass"
                          for check in (row.get("checked_evidence") or [])]
        unchecked = sorted(str(row.get("lens") or "")
                           for row in (state.get("lens_results") or [])
                           if row.get("verdict") == "pass"
                           and not row.get("checked_evidence"))
        if unchecked:
            review_notes.append({
                "title": "Clean verdicts without source-anchored evidence",
                "scenario": ("Not shown as clean: " + ", ".join(unchecked)),
                "lens": "review-kernel",
            })
        meta.update({
            "ws": ws, "impact": impact, "routing_decision": decision,
            "title": "Engineering review",
            "subtitle": "one canonical diff · graph-qualified selective lenses",
            "tests": runnability.get("summary"),
            "clean_evidence": clean_evidence,
            "review_notes": review_notes,
            "review_execution": execution,
            "dor_evidence": dor_evidence,
            "requirements_validation": requirements_validation,
            "diagnostic_fingerprints": diagnostics,
            "graph_fragment": graph,
            "revision_identity": evidence.revision_identity(
                state.get("revision") or {}),
            "gate": True,
            "gate_title": "review complete — approve or request changes",
            "gate_buttons": [
                {"label": "Approve review",
                 "prompt": f"Approve review run {state.get('run_id')}",
                 "primary": True},
                {"label": "Request changes",
                 "prompt": f"Request changes for review run {state.get('run_id')}"},
            ],
            "note": "Approval remains a human decision; taskPlane does not "
                    "self-approve this gate.",
        })
        findings_fragment = dashboard.render_findings(final_findings, meta)
        visuals["final_dashboard"] = _write_review_html(
            ws, "dashboard.html", [workflow, wave, findings_fragment],
            title="taskplane — engineering review")
    dashboard_ref = (visuals["final_dashboard"] if final else
                     visuals["workflow_and_wave"])
    obligations = [_bind_review_visual_obligation(
        ws, "render_dashboard", os.path.join(ws, dashboard_ref["path"]))]
    if final:
        for row in obligations:
            row["ack"] = (f"tp ack {row['id']} --delivered {row['path']}"
                          if row.get("id") else None)
    return visuals, obligations


def cmd_review(a) -> int:
    """Open a review in ONE call.

    A review used to run about ten shell commands before a single lens
    looked at the diff — onboard, init, new, target, graph scan, graph
    impact, lens route, lens dispatch, two dashboard renders — at a measured
    ~11k effective tokens each, and each command AND its output then sits in
    the conversation to be re-read on every later turn. `tp loop evidence`
    proved the fix for the evaluate step in v2.6: return everything the step
    needs in one payload, with every judgement slot left EMPTY.

    This does the same for the review's opening. It decides nothing: no
    finding, no verdict, no severity. It establishes facts — tools, target,
    graph, impact, routing, runnability — activates the contract, writes the
    shared context once, and hands back the briefs.
    """
    import target as tgt
    import review as rv
    ws = _workspace(a.workspace)
    review_action = getattr(a, "review_action", None)
    enforcement = None
    if review_action in {"start", "resume", "signoff"}:
        active = tp.load_active(ws) or {}
        enforcement, refusal = _enforcement_check(
            ws, saved=active.get("enforcement"),
            advisory=bool(getattr(a, "advisory", False)),
            actor=getattr(a, "by", None),
            run_id=getattr(a, "run_id", None))
        if refusal:
            print(json.dumps(refusal, sort_keys=True, separators=(",", ":")))
            return 1
    if getattr(a, "review_action", None) == "option":
        try:
            ws = rv.resolve_review_workspace(ws, a.run_id)
            state = rv._load_state(ws, a.run_id)
            pending = state.get("review_execution") or \
                rv.review_execution_preflight(run_id=state.get("run_id"))
            action_id = str((pending.get("action") or {}).get("id") or
                            rv._review_execution_action_id(
                                state.get("run_id"), "review-execution-mode"))
            result = rv.configure_review_execution(
                ws, selection=a.selection, by=getattr(a, "by", None),
                approval_receipt=None, run_id=a.run_id)
            visuals, owed = _review_visuals(ws, result, final=False)
            result = rv._manifest({**result, "visuals": visuals,
                                   "obligations": owed})
            kernel_state = rv._load_state(ws, result.get("run_id"))
            rv._save_state(ws, dict(
                kernel_state, manifest=result,
                counters=result["counters"]))
        except Exception as exc:
            print(json.dumps({"schema": "taskplane.review-execution-preflight/v1",
                              "status": "configuration_failed",
                              "reason": f"{exc.__class__.__name__}: {exc}"},
                             sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if getattr(a, "review_action", None) == "evidence":
        try:
            ws = rv.resolve_review_workspace(ws, a.run_id)
            state = rv._load_state(ws, a.run_id)
            execution = state.get("review_execution") or {}
            action_id = str((execution.get(a.kind) or {}).get(
                "action_id") or "")
            if a.status == "executed":
                receipt = rv._host_review_execution_receipt(
                    run_id=state["run_id"], action_id=action_id,
                    kind=a.kind,
                    after_receipt_id=str((execution.get(
                        "approval_receipt") or {}).get("receipt_id") or ""),
                    receipt_ref=getattr(a, "receipt", None))
            else:
                receipt = None
            result = rv.record_review_execution(
                ws, kind=a.kind, status=a.status, detail=a.detail or "",
                run_id=a.run_id,
                approval_receipt=receipt)
        except Exception as exc:
            print(json.dumps({"schema": "taskplane.review-execution-preflight/v1",
                              "status": "evidence_failed",
                              "reason": f"{exc.__class__.__name__}: {exc}"},
                             sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if getattr(a, "review_action", None) == "sandbox":
        try:
            ws = rv.resolve_review_workspace(ws, a.run_id)
            result = rv.prepare_review_validation_sandbox(
                ws, run_id=a.run_id)
        except Exception as exc:
            print(json.dumps({
                "schema": "taskplane.review-validation-sandbox/v1",
                "status": "preparation_failed",
                "reason": f"{exc.__class__.__name__}: {exc}"},
                sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if getattr(a, "review_action", None) == "validate":
        try:
            ws = rv.resolve_review_workspace(ws, a.run_id)
            command = list(a.command or [])
            if command and command[0] == "--":
                command = command[1:]
            result = rv.run_review_validation_command(
                ws, command=command, cwd=a.cwd, run_id=a.run_id,
                timeout=a.timeout)
        except Exception as exc:
            print(json.dumps({
                "schema": "taskplane.review-validation-command/v1",
                "status": "validation_failed",
                "reason": f"{exc.__class__.__name__}: {exc}"},
                sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("status") == "executed" else 1
        return 0
    if getattr(a, "review_action", None) == "collect":
        try:
            ws = rv.resolve_review_workspace(
                ws, getattr(a, "run_id", None))
            result = rv.collect_review(
                ws, publish=not bool(getattr(a, "no_publish", False)),
                run_id=getattr(a, "run_id", None))
            visuals, owed = _review_visuals(
                ws, result, final=result.get("status") == "complete")
            result = rv._manifest({**result, "visuals": visuals,
                                   "obligations": owed})
        except Exception as exc:
            failure = {"schema": "taskplane.review-collect-manifest/v2",
                       "status": "collect_failed",
                       "reason": f"{exc.__class__.__name__}: {exc}"}
            if isinstance(exc, rv.ReviewSlotValidationErrors):
                failure["repairs"] = exc.repairs
                failure["next_action"] = (
                    "dispatch every listed repair to its original producer "
                    "in one batch, then retry review collect once")
            print(json.dumps(failure,
                             sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if getattr(a, "review_action", None) == "signoff":
        try:
            ws = rv.resolve_review_workspace(
                ws, getattr(a, "run_id", None))
            result = rv.signoff_review(
                ws, decision=a.decision, by=a.by, note=a.note or "",
                run_id=getattr(a, "run_id", None))
            if enforcement:
                result["enforcement"] = enforcement
        except Exception as exc:
            print(json.dumps({"schema": "taskplane.review-signoff/v1",
                              "status": "signoff_failed",
                              "reason": f"{exc.__class__.__name__}: {exc}"},
                             sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    repository_run = None
    repository_preflight = None
    spec = getattr(a, "spec", None)
    parsed = tgt.parse(spec) if spec else None
    remote_repository = False
    if spec and (not parsed or parsed.get("kind") != "pr"):
        try:
            import storage as repository_storage
            repository_storage.identity_from_remote(spec)
            remote_repository = True
        except ValueError:
            remote_repository = False
    if getattr(a, "review_action", None) == "resume":
        import preflight as repository_preflight_module

        engine = repository_preflight_module.RepositoryPreflight()
        bootstrap = repository_preflight_module.find_bootstrap(
            ws, run_id=a.run_id)
        try:
            if bootstrap:
                authorized = repository_preflight_module.authorize_bootstrap(
                    ws, run_id=a.run_id, action_id=a.action_id,
                    response=a.response, approved_by=a.by)
                if authorized.get("status") == "cancelled":
                    print(json.dumps(authorized, sort_keys=True,
                                     separators=(",", ":")))
                    return 0
                repository_preflight = engine.prepare(
                    bootstrap["spec"], workspace=bootstrap["workspace"],
                    host=bootstrap["host"], run_id=a.run_id)
                # Reaching any structured result proves the external store is
                # available again. Any later pause now lives canonically in
                # RunStore and must not be shadowed by this bootstrap gate.
                repository_preflight_module.clear_bootstrap(bootstrap)
            else:
                repository_preflight = engine.resume(
                    a.run_id, action_id=a.action_id, response=a.response,
                    approved_by=a.by)
        except Exception as exc:
            if bootstrap and isinstance(exc, OSError):
                repository_preflight = \
                    repository_preflight_module.persist_storage_pause(
                        ws, spec=bootstrap["spec"], host=bootstrap["host"],
                        run_id=a.run_id,
                        detail=f"{exc.__class__.__name__}: {exc}")
                print(json.dumps(repository_preflight, sort_keys=True,
                                 separators=(",", ":")))
                return 2
            print(json.dumps({
                "schema": "taskplane.preflight/v1", "status": "needs_user",
                "run_id": a.run_id,
                "reason": f"{exc.__class__.__name__}: {exc}"},
                sort_keys=True, separators=(",", ":")))
            return 2
        if repository_preflight.get("status") != "ready":
            print(json.dumps(repository_preflight, sort_keys=True,
                             separators=(",", ":")))
            return 0 if repository_preflight.get("status") == \
                "cancelled" else 2
        repository_run = repository_preflight["run_id"]
        ws = os.path.realpath(repository_preflight["checkout"])
        spec = str(((repository_preflight.get("target") or {}).get(
            "target") or {}).get("spec") or "")
        parsed = tgt.parse(spec)
    elif (parsed and parsed.get("kind") == "pr" and all(
            parsed.get(key) for key in ("host", "owner", "repo"))) or \
            remote_repository:
        import preflight as repository_preflight_module

        host_record = {
            "kind": tp.host(),
            "session_id": (os.environ.get("CODEX_THREAD_ID") or
                           os.environ.get("CLAUDE_SESSION_ID")),
        }
        pending = repository_preflight_module.find_bootstrap(ws, spec=spec)
        if pending:
            print(json.dumps(
                repository_preflight_module.bootstrap_response(pending),
                sort_keys=True, separators=(",", ":")))
            return 2
        repository_run = (getattr(a, "run_id", None) or
                          repository_preflight_module.new_run_id())
        try:
            repository_preflight = \
                repository_preflight_module.RepositoryPreflight().prepare(
                    spec, workspace=ws, host=host_record,
                    run_id=repository_run)
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            if isinstance(exc, OSError):
                paused = repository_preflight_module.persist_storage_pause(
                    ws, spec=spec, host=host_record, run_id=repository_run,
                    detail=detail)
                print(json.dumps(paused, sort_keys=True,
                                 separators=(",", ":")))
            else:
                print(json.dumps({
                    "schema": "taskplane.preflight/v1",
                    "status": "needs_user", "run_id": repository_run,
                    "reason": detail, "action": None},
                    sort_keys=True, separators=(",", ":")))
            return 2
        if repository_preflight.get("status") != "ready":
            print(json.dumps(repository_preflight, sort_keys=True,
                             separators=(",", ":")))
            return 2
        repository_run = repository_preflight["run_id"]
        ws = os.path.realpath(repository_preflight["checkout"])
    out = {"steps": []}

    def step(name, ok, **extra):
        out["steps"].append({"step": name, "ok": bool(ok), **extra})

    # 1. tools — a remote PR review without gh degrades into unrecorded web
    #    reads, so it is answered up front rather than discovered later.
    t = tgt.tools()
    out["tools"] = t
    step("tools", t["git"]["present"],
         gh=t["gh"]["present"], hint=(None if t["gh"]["present"]
                                      else tgt.install_hint()))

    # 2. target — acquire and pin, so the findings can cite the tree.
    if repository_preflight is not None:
        rec = dict(repository_preflight["target"])
    elif parsed and parsed["kind"] == "pr" and getattr(a, "fetch", False):
        rec = tgt.acquire(ws, spec, base=getattr(a, "base", None))
    else:
        rec = tgt.pin(ws, base=getattr(a, "base", None), target=parsed)
    if not rec.get("ok"):
        step("target", False, reason=rec.get("reason"))
        out["ok"] = False
        out["next"] = rec.get("reason")
        print(json.dumps(out, indent=2, sort_keys=True))
        return 1
    # SETUP MUST BE TRUE BEFORE GRAPH QUALITY CAN SAY ANYTHING.  A wrong
    # repository, missing merge base, or un-checked-out target is not sparse
    # graph evidence.  Refuse before saving a target, scanning a graph,
    # activating a contract, or minting a kernel/cache entry.
    preflight = tgt.review_preflight(ws, rec)
    if repository_preflight is not None:
        preflight["repository_run_id"] = repository_run
        preflight["storage"] = "hybrid-external-run"
    out["preflight"] = preflight
    if not preflight["ok"]:
        step("target", False, status=preflight["status"],
             reason=preflight["reason"], recovery=preflight["recovery"])
        out.update({"ok": False, "status": preflight["status"],
                    "reason": preflight["reason"],
                    "recovery": preflight["recovery"],
                    "next": preflight["recovery"]})
        print(json.dumps(out, indent=2, sort_keys=True))
        return 1
    tgt.save(ws, rec)
    out["target"] = rec
    # The canonical PR patch starts at the pinned merge-base, not the moving
    # tip of the base branch.  Repository preflight already resolved and
    # hydrated this exact range; using base_ref here can pull unrelated
    # upstream commits into the review whenever the branch advanced after
    # the PR diverged, inflating symbols and graph impact.
    base = rec.get("merge_base") or rec.get("base_ref") or \
        getattr(a, "base", None) or "HEAD"
    step("target", True, head=rec["head"][:12], base=(rec.get("base") or "")[:12],
         fingerprint=rec["fingerprint"], changed=len(rec.get("changed_files") or []))

    # 3. graph + impact — impact-first is not optional, and it costs nothing
    #    here that it would not cost as its own call.
    files = rec.get("changed_files") or []
    g, imp = {}, {}
    try:
        import depgraph as dg
        g = dg.load(ws)
        # RESCAN A GRAPH THAT DESCRIBES ANOTHER TREE (D2). The blast radius
        # is the one input every lens is told NOT to re-derive, so a stored
        # graph scanned at a different head hands the wrong revision to the
        # whole wave at once. A graph with no `scanned_head` at all cannot
        # say which tree it describes, so it is rescanned too.
        _scanned = ((g.get("meta") or {}).get("scanned_head") or "")[:12]
        if not g.get("modules") or _scanned != (rec.get("head") or "")[:12]:
            g = dg.scan(ws)
        imp = dg.impact(ws, files) if files else {}
        out["impact"] = imp
        step("graph", True, modules=len(g.get("modules") or {}),
             edges=len(g.get("edges") or []),
             impacted=imp.get("total_impacted", 0))
    except Exception as e:
        # Never pretend a stale/partially loaded graph is complete. The
        # canonical pinned diff remains reviewable; ReviewKernel records the
        # degraded graph and routes from diff/content with mandatory floors.
        g, imp = {}, {}
        step("graph", False, reason=e.__class__.__name__)
    graph_errors = dg.quality_errors(g) if g and "dg" in locals() else []
    if graph_errors:
        quality = dg.scan_quality(g)
        warning = {
            "schema": "taskplane.graph-quality-warning/v1",
            "status": "degraded",
            "reason": graph_errors[0],
            "graph_quality": quality,
            "recovery": (quality.get("recovery") or
                         dg.GRAPH_SCAN_RECOVERY),
            "continuation": "immutable_diff_with_architecture_security_floors",
        }
        # Standalone Review is intentionally useful when graph enrichment is
        # incomplete: preserve the producer failure visibly, then let the
        # review kernel route from the pinned immutable diff with its
        # architecture/security floors. Governed Evaluate/EM and DoD retain
        # their strict refusals in cmd_loop/cmd_dod.
        step("graph", False, reason=graph_errors[0],
             recovery=warning["recovery"], continuation=warning["continuation"])
        out["graph_quality_warning"] = warning
        preflight["graph_quality_warning"] = warning
        # Adapt the producer-complete scan record into ReviewKernel's existing
        # impact-quality interface. A failed graph producer makes the derived
        # radius unknown, and the same degraded graph cannot honestly repair
        # that uncertainty through caller expansion. The full producer record
        # remains attached to the impact evidence rather than being recast as
        # an unrelated truncation or coverage failure.
        imp = dict(imp)
        imp.update({
            "unknown": True,
            "unknown_reason": "graph_scan_degraded",
            "graph_scan_quality": quality,
        })
        out["impact"] = imp
    rec["review_cache"] = tgt.review_cache_identity(rec, g)
    preflight["cache_identity"] = rec["review_cache"]
    tgt.save(ws, rec)

    # 4. contract — prepare it now, activate only after the kernel is ready.
    #    A mapper refusal must never strand the caller under an active
    #    read-only contract; graph degradation proceeds from the pinned diff.
    import storage as runtime_storage
    locator = runtime_storage.load_workspace_locator(ws)
    write_allow = [".em-review/**"]
    if locator:
        write_allow = [os.path.join(path, "**")
                       for path in locator["paths"].values()]
    c = tp.build_contract(
        (" ".join(a.goal) if getattr(a, "goal", None)
         else f"engineering review: {spec or base}"),
        read_only=True, write_allow=write_allow,
        max_actions=(int(a.max_actions)
                     if getattr(a, "max_actions", None) is not None else None))
    if enforcement:
        c["enforcement"] = enforcement
    c["budget"]["max_cost_usd"] = DEFAULT_MAX_COST_USD
    if getattr(a, "max_tokens", None) is not None:
        c["budget"]["max_tokens"] = int(a.max_tokens)
    c["target"] = {k: rec.get(k) for k in
                   ("origin", "head", "base", "base_ref", "branch",
                    "merge_base", "shallow", "fingerprint", "target",
                    "review_cache")}
    out["contract"] = {"task_id": c["task_id"], "read_only": True,
                       "status": "prepared", "write_allow": write_allow,
                       "budget": c.get("budget")}

    # 5. One normal-flow kernel call: quality before mapping, then exactly
    #    one immutable envelope and exact deep/light slots. Large bytes are
    #    artifacts; stdout is only the compact manifest.
    try:
        import runnability as runmod
        probe = runmod.probe_once(ws)
        diff_rc, patch = tp_target_diff(ws, base)
        if diff_rc:
            raise RuntimeError("canonical diff derivation failed")
        import review_evidence as _re
        store = _re.ArtifactStore(ws)
        diff_ref = store.put("diff", {"base": base, "patch": patch,
                                      "files": files})
        symbols = rv.changed_symbols_from_patch(patch)
        manifest = rv.start_review(
            ws, target=rec, graph=g, impact=imp,
            diff={"files": files, "changed_symbols": symbols,
                  "artifact": rv._portable_ref(diff_ref)},
            runnability=runmod.evidence_record(probe),
            requirement={}, acceptance=[], contracts=[], stage="review",
            task_type="review", base=base,
            caller_expander=(None if graph_errors else
                             rv.bounded_caller_expander(g)),
            routing_content=rv.changed_content_from_patch(patch))
        if manifest.get("status") not in {"ready", "needs_user"}:
            if repository_run:
                import run_store as repository_run_store
                store_record = repository_run_store.RunStore()
                current = store_record.load(repository_run)
                store_record.commit(
                    repository_run,
                    expected_revision=int(current["revision"]),
                    changes={"status": "review_blocked",
                             "contract": {"status": "inactive",
                                          "task_id": None},
                             "review": {"kernel_run_id":
                                        manifest.get("run_id"),
                                        "status": manifest.get("status")}})
            manifest["contract"] = {"status": "inactive",
                                    "reason": manifest.get("status")}
            manifest["preflight"] = preflight
            print(json.dumps(rv._manifest(manifest), sort_keys=True,
                             separators=(",", ":")))
            return 1
        tp.activate(ws, c, snapshot=tp.git_head(ws))
        step("contract", True, task_id=c["task_id"])
        try:
            out["owes"] = _seed_owed(ws, "review", c["task_id"])
            step("obligations", True, owed=len(out["owes"] or []))
        except Exception as e:
            step("obligations", False, reason=e.__class__.__name__)
        if repository_run:
            import run_store as repository_run_store
            store_record = repository_run_store.RunStore()
            current = store_record.load(repository_run)
            store_record.commit(
                repository_run, expected_revision=int(current["revision"]),
                changes={"status": "governed",
                         "contract": {"status": "active",
                                      "task_id": c["task_id"]},
                         "review": {"kernel_run_id": manifest.get("run_id"),
                                    "status": manifest.get("status")}})
        manifest["contract"] = {"task_id": c["task_id"],
                                "read_only": True, "status": "active"}
        if enforcement:
            manifest["enforcement"] = enforcement
        if repository_run:
            manifest["repository_run_id"] = repository_run
        manifest["tools"] = {"git": bool(t["git"]["present"]),
                             "gh": bool(t["gh"]["present"])}
        manifest["preflight"] = preflight
        visuals, owed = _review_visuals(ws, manifest, final=False)
        manifest["visuals"] = visuals
        manifest["obligations"] = owed
        manifest = rv._manifest(manifest)
        kernel_state = rv._load_state(ws, manifest.get("run_id"))
        rv._save_state(ws, dict(
            kernel_state, manifest=manifest, counters=manifest["counters"],
            **({"enforcement": enforcement} if enforcement else {})))
    except Exception as e:
        step("route", False, reason=f"{e.__class__.__name__}: {e}")
        # review start owns this contract.  A failed opening must not leave a
        # read-only contract behind to block the user's next command.
        try:
            active = tp.load_active(ws) or {}
            if active.get("task_id") == c.get("task_id"):
                tp.clear(ws)
        except Exception:
            pass
        print(json.dumps({"schema": "taskplane.review-start-manifest/v2",
                          "status": "start_failed",
                          "reason": f"{e.__class__.__name__}: {e}"},
                         sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0 if manifest.get("status") == "ready" else 2


def cmd_target(a) -> int:
    """Acquire / pin / inspect WHAT is being reviewed.

    Added because two field reviews of the same PR both cloned the
    repository and neither could prove it: the contract recorded no origin,
    no base, no head, so nothing distinguished a review of a checkout from
    a review of a rendered web diff."""
    import target as tgt
    ws = _workspace(a.workspace)
    action = getattr(a, "target_action", None) or "show"

    if action == "tools":
        t = tgt.tools()
        if getattr(a, "install", False):
            res = tgt.ensure_gh()
            t = tgt.tools()
            t["install"] = res
        t["hint"] = tgt.install_hint()
        if getattr(a, "json", False):
            print(json.dumps(t, indent=2, sort_keys=True))
            return 0
        print(f"git : {'yes' if t['git']['present'] else 'NO'}"
              f"   {t['git']['version'] or ''}")
        gh = t["gh"]
        auth = ("authenticated" if gh["authenticated"] else
                "NOT authenticated (`gh auth login`)"
                if gh["present"] else "")
        print(f"gh  : {'yes' if gh['present'] else 'NO'}"
              f"   {gh['version'] or ''}  {auth}")
        if not gh["present"]:
            print(f"\n`gh` is REQUIRED to review a remote pull request. A "
                  f"clone carries the code and none of the intent — the "
                  f"title, body, linked issues and review conversation are "
                  f"not in the git objects. Install it:\n  {t['hint']}\n"
                  f"or run `tp target tools --install`.", file=sys.stderr)
            return 1
        return 0

    if action == "fetch":
        rec = tgt.acquire(ws, a.spec, base=getattr(a, "base", None))
        if not rec.get("ok"):
            print(f"taskplane: {rec.get('reason')}", file=sys.stderr)
            return 1
        tgt.save(ws, rec)
        _print_target(rec, tgt)
        return 0

    if action == "pin":
        rec = tgt.pin(ws, base=getattr(a, "base", None),
                      target=tgt.parse(getattr(a, "spec", None))
                      if getattr(a, "spec", None) else None)
        if not rec.get("ok"):
            print(f"taskplane: {rec.get('reason')}", file=sys.stderr)
            return 1
        tgt.save(ws, rec)
        _print_target(rec, tgt)
        return 0

    rec = tgt.load(ws)
    if getattr(a, "json", False):
        print(json.dumps(rec or {"ok": False, "reason": "no target record"},
                         indent=2, sort_keys=True))
        return 0 if rec else 1
    if not rec:
        print("taskplane: this workspace is not bound to a reviewed tree. "
              "`tp target pin --base <ref>`, or `tp target fetch <pr-url>`.",
              file=sys.stderr)
        return 1
    _print_target(rec, tgt)
    return 0


def cmd_repository(a) -> int:
    """Prepare or resume one canonical repository precondition."""
    import preflight as repository_preflight_module
    import run_store as repository_run_store

    action = str(getattr(a, "repository_action", None) or "status")
    ws = _workspace(a.workspace)
    engine = repository_preflight_module.RepositoryPreflight()
    if action == "migrate":
        import storage_migration
        value = storage_migration.migrate_legacy_checkouts(
            _workspace(a.workspace))
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0 if not value.get("review_required") else 2
    if action == "status":
        try:
            value = repository_run_store.RunStore().load(a.run_id)
        except Exception as exc:
            try:
                import review as review_runtime
                review_ws = review_runtime.resolve_review_workspace(
                    ws, a.run_id)
                state = review_runtime._load_state(review_ws, a.run_id)
                value = state.get("manifest")
                if state.get("status") == "complete" or \
                        not isinstance(value, dict) or \
                        value.get("run_id") != a.run_id:
                    raise review_runtime.ReviewKernelError(
                        "active review manifest is unavailable")
            except Exception:
                print(json.dumps({
                    "schema": "taskplane.preflight/v1",
                    "status": "unavailable", "run_id": a.run_id,
                    "reason": f"{exc.__class__.__name__}: {exc}"},
                    sort_keys=True, separators=(",", ":")))
                return 1
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0
    if action == "resume":
        bootstrap = repository_preflight_module.find_bootstrap(
            ws, run_id=a.run_id)
        try:
            if bootstrap:
                authorized = repository_preflight_module.authorize_bootstrap(
                    ws, run_id=a.run_id, action_id=a.action_id,
                    response=a.response, approved_by=a.by)
                if authorized.get("status") == "cancelled":
                    value = authorized
                else:
                    value = engine.prepare(
                        bootstrap["spec"], workspace=bootstrap["workspace"],
                        host=bootstrap["host"], run_id=a.run_id)
                    repository_preflight_module.clear_bootstrap(bootstrap)
            else:
                value = engine.resume(
                    a.run_id, action_id=a.action_id, response=a.response,
                    approved_by=a.by)
        except Exception as exc:
            if bootstrap and isinstance(exc, OSError):
                value = repository_preflight_module.persist_storage_pause(
                    ws, spec=bootstrap["spec"], host=bootstrap["host"],
                    run_id=a.run_id,
                    detail=f"{exc.__class__.__name__}: {exc}")
                print(json.dumps(value, sort_keys=True,
                                 separators=(",", ":")))
                return 2
            print(json.dumps({
                "schema": "taskplane.preflight/v1", "status": "needs_user",
                "run_id": a.run_id,
                "reason": f"{exc.__class__.__name__}: {exc}"},
                sort_keys=True, separators=(",", ":")))
            return 2
    else:
        host = {"kind": tp.host(),
                "session_id": (os.environ.get("CODEX_THREAD_ID") or
                               os.environ.get("CLAUDE_SESSION_ID"))}
        pending = repository_preflight_module.find_bootstrap(ws, spec=a.spec)
        if pending:
            value = repository_preflight_module.bootstrap_response(pending)
        else:
            run_id = (getattr(a, "run_id", None) or
                      repository_preflight_module.new_run_id())
            try:
                value = engine.prepare(
                    a.spec, workspace=ws, host=host, run_id=run_id)
            except OSError as exc:
                value = repository_preflight_module.persist_storage_pause(
                    ws, spec=a.spec, host=host, run_id=run_id,
                    detail=f"{exc.__class__.__name__}: {exc}")
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0 if value.get("status") in {"ready", "cancelled"} else 2


def _print_target(rec, tgt) -> None:
    t = rec.get("target") or {}
    if t.get("kind") == "pr":
        who = "/".join(x for x in (t.get("owner"), t.get("repo")) if x)
        print(f"target      : {who}#{t.get('number')}" if who
              else f"target      : PR #{t.get('number')}")
    print(f"origin      : {rec.get('origin') or '(none)'}")
    print(f"head        : {(rec.get('head') or '')[:12]} "
          f"({rec.get('branch') or 'detached'})")
    if rec.get("base"):
        print(f"base        : {(rec.get('base') or '')[:12]} "
              f"({rec.get('base_ref')})")
        print(f"changed     : {len(rec.get('changed_files') or [])} file(s)")
    if rec.get("dirty"):
        print(f"dirty       : {len(rec['dirty'])} path(s) — "
              f"the tree is not exactly this commit")
    print(f"fingerprint : {rec.get('fingerprint')}")
    print("Cite this in findings `meta.target` so the sign-off gate can "
          "check that the findings and the tree are the same thing.")


def cmd_onboard(a) -> int:
    """Cold-start onboarding. Detects whether the workspace is ready for a
    governed run (folder + git snapshot + init) and, by default, prints the
    onboarding dashboard fragment that walks a new user in from zero.
    --json prints the readiness report instead (for the driver to branch on)."""
    import dashboard
    ws = _workspace(a.workspace)
    if getattr(a, "install_codex_hooks", False):
        _install_codex_hooks(ws)
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
    for row in report.get("foreign_state") or []:
        print("FOREIGN STATE: " + str(row.get("plugin")) + " at "
              + str(row.get("root")) + " — "
              + str(row.get("remediation") or ""))
    print(dashboard.render_onboarding(report, out=a.out))
    return 0


def _inline_max() -> int:
    """Above this many characters, an artifact is delivered rather than
    retyped. 24k is roughly where a fragment stops being a message and
    starts being a document; TASKPLANE_INLINE_MAX overrides it, and 0
    disables reference mode entirely."""
    raw = (os.environ.get("TASKPLANE_INLINE_MAX") or "").strip()
    try:
        return int(raw) if raw else 24_000
    except ValueError:
        return 24_000


def cmd_findings(a) -> int:
    """Render a REVIEW findings dashboard from a findings JSON — every
    severity, filterable, each finding expandable. A pure review has no loop
    state, so this is how tp-engineering shows ALL findings at the review
    gate (the loop dashboard can't). Prints the inline widget fragment."""
    import dashboard
    import review as review_runtime
    review_ws = _workspace(a.workspace)
    review_root = review_runtime._public_root(review_ws)
    path = a.file or os.path.join(review_root, "findings.json")
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
    # DOES THIS DOCUMENT NAME THE TREE IT CAME FROM? (v2.12.0)
    #
    # Reported, never silently corrected, and never a reason to withhold
    # the findings themselves — a human reading a review is better served
    # by "here are the findings AND they do not cite a tree" than by a
    # refusal. It is the SIGN-OFF the binding gates, via the screener.
    _bind = None
    try:
        import target as _tgt
        _bind = _tgt.binding_problem(_workspace(a.workspace), data
                                     if isinstance(data, dict) else None)
    except Exception:
        _bind = None
    if _bind:
        print("UNBOUND: " + _bind, file=sys.stderr)
        meta["target_unbound"] = _bind
    # Render-reliability contract (v1.5.3): the headline ALWAYS prints first,
    # so the key numbers reach the human even if the widget render is skipped.
    print("HEADLINE: " + dashboard.headline_findings(findings, meta))

    # RENDER BY REFERENCE (v2.13.0). The obligation mechanism this product
    # added in v2.9.0 made "show the graph" enforceable, and then made the
    # cheapest compliance path the most expensive one: the driver pasted the
    # engine's full HTML back through a widget tool, so ~52k characters that
    # taskplane had ALREADY written to disk were re-authored at output
    # weight. Inline dashboards were 450k effective tokens of one measured
    # review — the single largest addressable slice, caused by the
    # enforcement rather than by the work.
    #
    # So above a threshold the engine stops handing back a blob and hands
    # back a PATH. The artifact is identical, its fingerprint is what the
    # obligation ledger already checks, and delivering the file discharges
    # the obligation exactly as a widget render does.
    _imax = _inline_max()
    if _imax and not getattr(a, "paged", False) \
            and not getattr(a, "html", False):
        _doc = dashboard.render_findings(findings, meta)
        if len(_doc) > _imax:
            _p = a.out or os.path.join(review_root, "findings.html")
            try:
                os.makedirs(os.path.dirname(_p), exist_ok=True)
                with open(_p, "w", encoding="utf-8") as f:
                    f.write(dashboard.standalone_document(
                        [_doc], title="review findings"))
                print(f"RENDER-BY-REFERENCE: {_p}")
                print(f"  {len(_doc):,} chars — too large to retype through a "
                      f"widget tool. DELIVER THIS FILE (SendUserFile / the "
                      f"host's artifact channel); do NOT paste its contents "
                      f"back. `tp ack <id> --delivered {_p}` records it, and "
                      f"the fingerprint proves it was the engine's own bytes.")
                return 0
            except OSError:
                pass          # unwritable: fall through and print inline
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
    if getattr(a, "html", False):
        pages = dashboard.render_findings_paged(findings, meta)
        doc = dashboard.standalone_document(
            [p["html"] for p in pages],
            title=str(meta.get("title") or "review findings"))
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".",
                        exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(doc)
            print(a.out)
        else:
            print(doc)
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
        quality = dg.scan_quality(g)
        out = {"modules": len(g["modules"]),
               "edges": len(g["edges"]),
               "files": len(g["files"]),
               "stored": os.path.join(tp.kb_root(ws), "graph.json")}
        if dec:   # ADDITIVE: without --decompose the output is unchanged
            out["components"] = len(g.get("components") or [])
        if quality.get("degraded"):
            out["degraded"] = True
            out["graph_quality"] = quality
        if getattr(a, "text", False):
            print(f"graph scan: modules={out['modules']} edges={out['edges']} "
                  f"files={out['files']} degraded="
                  + str(bool(quality.get("degraded"))).lower())
            for row in quality.get("failures") or []:
                print("  - {producer}: {module} {file} {error_class}: "
                      "{reason}".format(**row))
            if quality.get("degraded"):
                print("  recovery: " + str(quality.get("recovery") or ""))
        else:
            print(json.dumps(out, indent=2))
        if bool(getattr(a, "strict", False)) and quality.get("degraded"):
            return 1
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
        # AN EMPTY GRAPH IS NOT A PICTURE (v2.11.0). Run from the wrong
        # directory, this used to emit ~5.7 KB of perfectly valid-looking
        # dependency-graph fragment for a workspace that was not a repo and
        # had never been scanned — a graph of NOTHING, rendered to a human
        # as the review's blast radius, with no way to tell it apart from
        # the real one. Refusing costs a human one command; not refusing
        # costs them a wrong decision they cannot see is wrong.
        _g = dg.load(ws)
        if not (_g.get("modules") or _g.get("edges")):
            hint = ""
            other = _contracts_elsewhere(ws)
            if other:
                hint = (" Active taskplane contracts exist in: "
                        + ", ".join(other) + ".")
            print(f"taskplane: refusing to render a dependency graph for "
                  f"{ws} — nothing has been scanned there (0 modules, 0 "
                  f"edges). Run `tp graph scan` in the workspace under "
                  f"review, or pass `--workspace <path>`.{hint}",
                  file=sys.stderr)
            return 1
        files = (a.files.split(",") if a.files else
                 _changed_for_impact(ws, a.base))
        out = dg.to_html(ws, files, out=a.out,
                         focus=getattr(a, "focus", None),
                         fragment=bool(getattr(a, "fragment", False)))
        print(out)
        # WS-F: this is THE designed dependency + system-design view. The
        # recurring failure was not that it could not be produced — it was an
        # assistant drawing its own chart instead. The obligation carries this
        # artifact's content fingerprint, so a substitute has nothing to cite.
        if getattr(a, "out", None):
            try:
                import obligations
                # A standalone Review seeds its graph obligation before the
                # artifact exists.  Re-issue that SAME logical obligation
                # now that graph.html has bytes instead of creating a second
                # graph debt for the same file.  The append-only ledger keeps
                # the demand timestamp while its deterministic id lets the
                # artifact-bound row replace the placeholder during status
                # reconciliation.  Outside Review, graph html retains its
                # ordinary graph-scoped obligation.
                seeded = next((row for row in reversed(obligations.read(ws))
                               if row.get("event") == "issued"
                               and row.get("kind") == "render_graph"
                               and row.get("step") == "review"), None)
                oid = obligations.issue(
                    ws, "render_graph",
                    detail="show the product's own dependency/system-design "
                           "view — not a re-drawn substitute",
                    step="review" if seeded else "graph", artifact=a.out,
                    key="review:render_graph" if seeded else None,
                    session=seeded.get("session") if seeded else None)
                if oid:
                    print(f"OBLIGATION {oid}: show this view, then "
                          f"`tp ack {oid}`", file=sys.stderr)
            except Exception:
                pass
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


# Where a version may be read from, in order. `.codex-plugin/plugin.json`
# stays AUTHORITATIVE — it is the one a human edits and the one every other
# surface is checked against — but it is not the only place it may be READ.
_VERSION_SOURCES = (
    (".codex-plugin", "plugin.json"),
    (".claude-plugin", "plugin.json"),
)


def plugin_version(root: str | None = None) -> str:
    """The plugin version, from whichever manifest this INSTALL actually has.

    This used to read `.codex-plugin/plugin.json` and nothing else, which was
    correct in the repository and broken in the shipped product: the Claude
    package and the `.plugin` archive contain `.claude-plugin/` only, so
    `tp version` raised "missing authoritative version manifest" on every
    Claude-side install of v2.9.0. CI never saw it because CI runs against
    the repo, where both manifests exist — a gate that only ever inspects
    the source tree cannot see a defect introduced by packaging.

    Authority is unchanged: `.codex-plugin/plugin.json` is still the single
    source `version --verify` checks every other surface against, and it is
    still preferred here. The fallback exists so that READING the version
    works wherever the plugin is installed; it does not make a second
    manifest editable.
    """
    root = root or _plugin_repo_root()
    tried = []
    for parts in _VERSION_SOURCES:
        src = os.path.join(root, *parts)
        tried.append(src)
        if not os.path.exists(src):
            continue
        data = tp.load_json(src, what="version manifest")
        v = data.get("version")
        if not isinstance(v, str) or not v.strip():
            raise tp.StateError(src, "manifest has no usable 'version' field",
                                "restore the authoritative version string")
        return v.strip()
    raise tp.StateError(
        tried[0], "no plugin manifest found (looked for "
        + ", ".join("/".join(p) for p in _VERSION_SOURCES) + ")",
        "reinstall the plugin — the package appears to be missing its "
        "manifest entirely")


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

_CLI_REVIEW_OPTION_NOTE = [
    "When ReviewKernel returns `status: needs_user`, execute the selected",
    "`action.choices[*].command` verbatim through the stable workspace launcher.",
    "Use `python3` on macOS/Linux and `py` on Windows:",
    "",
    "```bash",
    "python3 .taskplane/codex-hook.py review option dynamic --run-id <run-id>",
    "python3 .taskplane/codex-hook.py review option dynamic-render --run-id <run-id>",
    "python3 .taskplane/codex-hook.py review option static --run-id <run-id>",
    "py .taskplane/codex-hook.py review option dynamic --run-id <run-id>",
    "py .taskplane/codex-hook.py review option dynamic-render --run-id <run-id>",
    "py .taskplane/codex-hook.py review option static --run-id <run-id>",
    "```",
    "",
    "Do not substitute `review resume`: that command resolves repository",
    "preflight decisions, not review-execution mode. Render the opening canonical",
    "dashboard from `visuals.workflow_and_wave.inline.path` and the collected",
    "canonical dashboard from `visuals.final_dashboard.inline.path`.",
    "",
]

# The stage CLI crosses argparse as one closed JSON object, so its field
# surface cannot be discovered from argparse actions. Keep the documentation
# schema beside the generator and pin it to loop._STAGE_REQUEST_FIELDS in the
# focused CLI tests. This preserves tp.py's lazy stage-domain import boundary.
_CLI_STAGE_REQUEST_FIELDS = {
    "history": ("schema", "run_id", "cursor", "limit"),
    "start": (
        "schema", "stage", "expected_revision", "operation_id",
        "expected_predecessor_fingerprints", "foreground", "authority",
        "declared_scope",
    ),
    "reuse": (
        "schema", "stage", "successor_stage", "expected_revision",
        "operation_id", "expected_predecessor_fingerprints", "foreground",
        "authority", "declared_scope", "reason", "actor",
    ),
    "resume": (
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "attempt_id", "authority",
        "declared_scope",
    ),
    "terminalize": (
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "outcome", "actor",
        "terminalized_at", "reason_code", "reason",
        "completed_deliverables", "completion_evidence", "handoff_manifest",
        "authority",
    ),
    "terminalize-and-start": (
        "schema", "run_id", "predecessor_stage_id", "stage",
        "successor_stage", "expected_head_fingerprint", "expected_revision",
        "operation_id", "outcome", "actor", "terminalized_at",
        "reason_code", "reason", "completed_deliverables",
        "completion_evidence", "foreground", "authority", "declared_scope",
    ),
    "split": (
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "child_specs", "actor",
        "terminalized_at", "reason", "authority", "declared_scopes",
    ),
}

_CLI_STAGE_REQUIRED_FIELDS = {
    "history": ("run_id",),
    "start": ("stage", "expected_revision", "operation_id", "authority"),
    "reuse": (
        "stage OR successor_stage", "expected_revision", "operation_id",
        "expected_predecessor_fingerprints", "authority", "reason", "actor",
    ),
    "resume": (
        "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "authority",
    ),
    "terminalize": (
        "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "outcome", "actor",
        "terminalized_at", "authority",
    ),
    "terminalize-and-start": (
        "predecessor_stage_id", "stage OR successor_stage",
        "expected_head_fingerprint", "expected_revision", "operation_id",
        "outcome", "actor", "terminalized_at", "authority",
    ),
    "split": (
        "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "child_specs", "actor",
        "terminalized_at", "reason", "authority",
    ),
}

_CLI_STAGE_OPTIONAL_FIELDS = {
    "history": ("schema", "cursor", "limit"),
    "start": (
        "schema", "expected_predecessor_fingerprints (required for a "
        "successor; omit for a root)", "foreground", "declared_scope",
    ),
    "reuse": ("schema", "foreground", "declared_scope"),
    "resume": ("schema", "attempt_id", "declared_scope"),
    "terminalize": (
        "schema", "reason_code + reason (required for closed/discarded; "
        "forbidden for done)", "completed_deliverables + completion_evidence "
        "(all deliverables and non-empty evidence required for done)",
        "handoff_manifest",
    ),
    "terminalize-and-start": (
        "schema", "run_id", "reason_code + reason (required for "
        "closed/discarded; forbidden for done)",
        "completed_deliverables + completion_evidence (all deliverables and "
        "non-empty evidence required for done)", "foreground",
        "declared_scope",
    ),
    "split": ("schema", "declared_scopes"),
}

_CLI_STAGE_ARTIFACT_REFERENCE_EXAMPLE = {
    "schema": "taskplane.artifact-reference/v1",
    "kind": "test-report",
    "fingerprint": "a" * 64,
    "digest": "a" * 64,
    "bytes": 128,
    "locator": "artifact://test-report/" + "a" * 64,
    "transport": "artifact-reference",
}

_CLI_STAGE_AUTHORITY_EXAMPLE = {
    "schema": "taskplane.stage-authority-binding/v1",
    "run_id": "run-r0004",
    "repository_id": "github.com/vdemkiv/taskplane",
    "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
    "worktree_id": "t06-worktree",
    "target_revision": "1" * 40,
    "worktree_revision": "2" * 40,
    "requirement_id": "R-0004",
    "requirement_revision": "4",
    "design_revision": "2",
    "design_fingerprint": "c" * 64,
    "actor": "human:operator",
    "session_id": "codex-thread-1",
    "authority_revision": 7,
    "authority_fingerprint": "d" * 64,
}

_CLI_STAGE_VALUE_EXAMPLE = {
    "schema": "taskplane.stage/v1",
    "run_id": "run-r0004",
    "stage_id": "stage-evaluate-001",
    "requirement": {
        "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
    },
    "design": {"revision": "2", "fingerprint": "c" * 64},
    "stage_kind": "evaluate",
    "parent_stage_ids": [],
    "predecessor_stage_ids": ["stage-build-001"],
    "input_manifest_ref": {
        "schema": "taskplane.artifact-reference/v1",
        "kind": "stage-handoff",
        "fingerprint": "e" * 64,
        "digest": "e" * 64,
        "bytes": 1024,
        "locator": "artifact://stage-handoff/" + "e" * 64,
        "transport": "artifact-reference",
    },
    "execution_root_id": "execution-stage-evaluate-001",
    "deliverables": ["evaluation-verdict"],
    "selected_artifacts": [{
        "schema": "taskplane.artifact-reference/v1",
        "kind": "source",
        "fingerprint": "f" * 64,
        "digest": "f" * 64,
        "bytes": 4096,
        "locator": "artifact://source/" + "f" * 64,
        "transport": "artifact-reference",
    }],
    "budget": {"attempt_limit": 3, "token_limit": 8000},
    "dependencies": ["t06-cross-host-rollout"],
    "contracts": ["contract:stage-artifact-handoff"],
    "authority": _CLI_STAGE_AUTHORITY_EXAMPLE,
    "state": "active",
    "outcome": None,
    "default_consumable": True,
    "terminal": None,
    "created_at": "2026-08-21T18:00:00Z",
    "aggregate_revision": 1,
}

_CLI_STAGE_SUCCESSOR_EXAMPLE = {
    "schema": "taskplane.stage-command/v1",
    "predecessor_stage_id": "stage-build-001",
    "successor_stage": _CLI_STAGE_VALUE_EXAMPLE,
    "expected_head_fingerprint": "9" * 64,
    "expected_revision": 12,
    "operation_id": "build-to-evaluate-001",
    "outcome": "done",
    "actor": "human:operator",
    "terminalized_at": "2026-08-21T18:00:00Z",
    "completed_deliverables": ["build-commit", "declared-tests"],
    "completion_evidence": [_CLI_STAGE_ARTIFACT_REFERENCE_EXAMPLE],
    "foreground": True,
    "authority": _CLI_STAGE_AUTHORITY_EXAMPLE,
    "declared_scope": {
        "scope_paths": ["taskplane/**"],
        "out_of_scope_paths": [],
    },
}

_CLI_STAGE_HISTORY_EXAMPLE = {
    "schema": "taskplane.stage-command/v1",
    "run_id": "run-r0004",
    "cursor": "0",
    "limit": 25,
}


def _cli_stage_request_note() -> list[str]:
    """Generator-owned JSON boundary documentation for ``tp.py stage``."""
    def render_fields(command: str, entries: tuple[str, ...]) -> str:
        rendered_entries = []
        fields = sorted(
            _CLI_STAGE_REQUEST_FIELDS[command], key=len, reverse=True)
        for entry in entries:
            rendered = entry
            for field in fields:
                rendered = re.sub(
                    rf"\b{re.escape(field)}\b", f"`{field}`", rendered)
            rendered_entries.append(rendered)
        return ", ".join(rendered_entries)

    out = [
        "### Closed stage-command request",
        "",
        "Every stage subcommand accepts one UTF-8 JSON object from",
        "`--request FILE` or standard input with `--request -`. The object is",
        "bounded to 1,048,576 bytes and may declare schema",
        "`taskplane.stage-command/v1`. Unknown fields and predecessor runtime",
        "context (agents, conversations, event logs, tool transcripts, leases,",
        "runtime state, workspaces, paths, or execution roots) are rejected.",
        "The table distinguishes fields required on every call from optional",
        "or outcome-dependent fields. Fields joined by `OR` are exclusive",
        "alternatives. Values remain subject to identity, authority, lifecycle,",
        "and artifact validation.",
        "",
        "| Stage command | Required fields | Optional or conditional fields |",
        "| --- | --- | --- |",
    ]
    for command in _CLI_STAGE_REQUEST_FIELDS:
        required = render_fields(
            command, _CLI_STAGE_REQUIRED_FIELDS[command])
        optional = render_fields(
            command, _CLI_STAGE_OPTIONAL_FIELDS[command])
        out.append(f"| `{command}` | {required} | {optional} |")
    out += [
        "",
        "#### Automatic pristine new-run bootstrap",
        "",
        "Set `TASKPLANE_STAGE_NATIVE=new-run` before `tp.py loop init`. Supply",
        "an exact existing requirement with `--req` and the accountable human",
        "with `--by`; use stage identifier syntax such as",
        "`human:vdemkiv` (letters, digits, `.`, `_`, `:`, or `-`; no spaces).",
        "That value becomes the root stage `authority.actor`. A",
        "stable session identity must already be present in",
        "`TASKPLANE_SESSION_ID`, `CODEX_THREAD_ID`, or `CLAUDE_SESSION_ID`.",
        "The workspace must already have a governed locator bound to an",
        "unmigrated v3 run with an exact target revision.",
        "",
        "Only that successful normal initialization mints the private",
        "pristine-new-run marker; do not add, copy, or infer the marker later.",
        "",
        "The first normal `tp.py loop next` atomically creates, commits, and",
        "dispatches one deterministic root stage through the internal",
        "lifecycle. It derives root authority from verified governed run facts",
        "and stores the bounded input handoff",
        "internally. Replaying the same call reuses the committed operation.",
        "The loop caller must not create stage JSON, authority JSON, a handoff",
        "artifact, or a separate `tp.py stage start` request.",
        "`tp.py loop wave` never bootstraps a root: it requires the already",
        "bound v4 journey and fails closed when that binding is missing.",
        "",
        "New-run initialization also refuses any existing singleton history,",
        "including terminal history and `--force`; use a fresh governed run.",
        "Initialization refuses without singleton or stage mutation when the",
        "requirement is missing or unknown, `--by` is missing, stable session",
        "identity is missing, the governed locator is missing, the bound run is",
        "not unmigrated v3, or its exact target revision is unavailable.",
        "Bootstrap also refuses when `new-run` was enabled only after init, the",
        "private marker is absent, the singleton is no longer structurally",
        "pristine, legacy progress exists, or the bound locator/run/store",
        "identity becomes mismatched or corrupt. After the v4 root commit, the",
        "singleton retains a durable run binding; losing or corrupting its",
        "locator or store remains a",
        "fail-closed refusal rather than a fallback to legacy dispatch.",
        "",
        "#### Closed nested shapes",
        "",
        "A `taskplane.stage/v1` request value is a closed active-stage object.",
        "It requires every key shown below except `fingerprint`, which may be",
        "omitted and is recomputed canonically. `requirement` has exactly `id`,",
        "`revision`, and `fingerprint`; `design` is either null or has exactly",
        "`revision` and `fingerprint`. An input stage must have `state: active`,",
        "`outcome: null`, `default_consumable: true`, and `terminal: null`.",
        "Collections must already be sorted and unique. The execution root is",
        "always `execution-<stage_id>`.",
        "",
        "```json",
        *json.dumps(
            _CLI_STAGE_VALUE_EXAMPLE, indent=2, ensure_ascii=False,
        ).splitlines(),
        "```",
        "",
        "`authority` is a closed `taskplane.stage-authority-binding/v1` object",
        "with exactly the keys shown above. All identity and revision values",
        "must match the live run, checkout, requirement, design, actor, and",
        "session. When `design` is null, both authority design fields are null.",
        "The top-level request `authority` and the stage's nested `authority`",
        "must describe the same current binding.",
        "",
        "Every `input_manifest_ref`, `selected_artifacts` entry, and",
        "`completion_evidence` entry is a closed",
        "`taskplane.artifact-reference/v1` object with exactly `schema`, `kind`,",
        "`fingerprint`, `digest`, `bytes`, `locator`, and `transport`. The",
        "locator is `artifact://<kind>/<fingerprint>`, both hashes are 64",
        "lowercase hexadecimal characters, bytes is a non-negative integer,",
        "and transport is `artifact-reference`. For example:",
        "",
        "```json",
        *json.dumps(
            _CLI_STAGE_ARTIFACT_REFERENCE_EXAMPLE,
            indent=2, ensure_ascii=False,
        ).splitlines(),
        "```",
        "",
        "`declared_scope` is either absent or a closed object with exactly",
        "`scope_paths` and `out_of_scope_paths`. Each is a sorted, unique array",
        "of at most 64 non-empty strings. `declared_scopes` on `split` is an",
        "object keyed by generated child stage id whose values have this exact",
        "shape.",
        "",
        "#### Runnable request templates",
        "",
        "History needs no lifecycle payload. Save this as `history.json` and",
        "replace `run-r0004` with an existing run id:",
        "",
        "```json",
        *json.dumps(
            _CLI_STAGE_HISTORY_EXAMPLE, indent=2, ensure_ascii=False,
        ).splitlines(),
        "```",
        "",
        "```bash",
        "tp.py stage history --request history.json",
        "```",
        "",
        "Atomic predecessor terminalization and successor startup use one",
        "shape-complete request and one receipt:",
        "",
        "```bash",
        "tp.py stage terminalize-and-start --request request.json",
        "```",
        "",
        "Save the following as `request.json`. Before running it, replace the",
        "example identifiers, revisions, hashes, byte counts, and timestamps",
        "with values from the live predecessor, stored handoff, artifact, and",
        "authority receipts. Replace whole values; do not use string",
        "placeholders or local paths:",
        "",
        "```json",
        *json.dumps(
            _CLI_STAGE_SUCCESSOR_EXAMPLE, indent=2, ensure_ascii=False,
        ).splitlines(),
        "```",
        "",
        "The command atomically records the predecessor's immutable terminal",
        "outcome and starts the successor from its verified bounded handoff.",
        "A validation or authority failure changes neither stage.",
        "",
    ]
    return out


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
        if name == "tp.py review option":
            out += _CLI_REVIEW_OPTION_NOTE
        if name == "tp.py stage":
            out += _cli_stage_request_note()
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


def _enforce_stage_compatibility() -> None:
    """Refuse a broken stage dependency before opening governed state."""
    import run_store as repository_run_store
    try:
        repository_run_store.ensure_stage_compatibility()
    except repository_run_store.TaskplaneCompatibilityError as exc:
        if os.environ.get("TASKPLANE_DEBUG"):
            raise
        print("taskplane: compatibility failed: "
              f"TaskplaneCompatibilityError: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def main(argv=None) -> int:
    _utf8_streams()
    _enforce_stage_compatibility()
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
    cs = sub.add_parser("contracts", help="list every active contract slot, "
                        "including stale ones a union is silently applying")
    cs.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    cs.set_defaults(fn=cmd_contracts)

    n.add_argument("--owes", metavar="RUN_TYPE",
                   help="seed the artifacts this run type owes as BINDING "
                        "obligations (e.g. `review`): recorded before the "
                        "work starts, and taskplane's own completion "
                        "commands stay blocked until each is shown")
    n.add_argument("--max-tokens", type=int, default=None, dest="max_tokens",
                   metavar="N",
                   help="EFFECTIVE-token ceiling for this contract (cache "
                        "reads x0.1, cache writes x2, output x5 — the "
                        "weighting cost actually follows). Counts what the "
                        "host recorded, so it tracks spend where the action "
                        "ceiling only counts tool calls. Unset = action "
                        "ceiling only, exactly as before.")
    n.add_argument("--target", metavar="SPEC",
                   help="what is being reviewed — a PR url, OWNER/REPO#N, "
                        "or a ref. Pins this checkout (origin, head, base, "
                        "dirty state) so the findings can cite the tree they "
                        "came from and the completion gate can check it")
    n.add_argument("--base", metavar="REF",
                   help="diff base for the target pin (e.g. origin/main)")
    n.add_argument("--fetch", action="store_true",
                   help="with a PR --target, fetch pull/N/head into this "
                        "checkout first (needs git; `gh` is what supplies "
                        "the PR's title, body and discussion)")
    n.add_argument("--advisory", action="store_true",
                   help="continue with visibly advisory screen enforcement")
    n.add_argument("--by", default=None,
                   help="human identity required with --advisory or a "
                        "foreign-state override")
    n.add_argument("--allow-foreign-state", action="append", metavar="ROOT",
                   help="repeatable exact signed foreign-state root to include; "
                        "requires --by and is recorded on the contract")
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

    ss = sub.add_parser("screen-skill", help="PreToolUse collision gate for "
                        "Skill invocations during governed work")
    ss.set_defaults(fn=cmd_screen_skill)

    sv = sub.add_parser("session-verify", help="Stop/SessionEnd hook: exit 2 "
                        "listing artifacts this run owes and never showed")
    sv.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    sv.set_defaults(fn=cmd_session_verify)

    sr = sub.add_parser("screen-render", help="PreToolUse hook for the "
                        "inline-render tool: record that a render RAN, and "
                        "with which bytes. Observes only — never denies")
    sr.set_defaults(fn=cmd_screen_render)

    sd = sub.add_parser("screen-dispatch", help="PreToolUse hook for the "
                        "Agent tool: verify tier-routed model was passed "
                        "(inert unless TASKPLANE_ENFORCE_DISPATCH=warn|strict)")
    sd.set_defaults(fn=cmd_screen_dispatch)

    sas = sub.add_parser(
        "subagent-start", help="SubagentStart lifecycle trace, bounded "
        "contract context, and leased review-child identity binding "
        "(stdin event)")
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
    wtc = sub.add_parser(
        "worktree-cleanup", help="replay receipt-scoped post-merge cleanup "
        "once; never force-removes or deletes branches")
    wtc.add_argument("action", choices=["replay"],
                     help="bounded maintenance action")
    wtc.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    wtc.set_defaults(fn=cmd_worktree_cleanup)
    cl = sub.add_parser("clear", help="deactivate the workspace contract")
    cl.add_argument("--all", action="store_true",
                    help="release EVERY active slot, not just this process's "
                         "— the way out when a wave leaked contracts")
    cl.add_argument("--slot", metavar="SLOT",
                    help="release one named slot (see `tp contracts`) "
                         "without setting TASKPLANE_TASK")
    cl.add_argument("--approved-by",
                    help="human chat identity authorizing recovery past an "
                         "exhausted budget")
    cl.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    cl.set_defaults(fn=cmd_clear)

    st = sub.add_parser("status", help="show project loop status and the "
                        "active contract")
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
    b.add_argument("--approved-by",
                   help="human chat identity authorizing this budget grant")
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
    li.add_argument("--req", help="anchor the loop to a requirement R-id; "
                    "TASKPLANE_STAGE_NATIVE=new-run requires an exact "
                    "existing requirement")
    li.add_argument("--parallel", action="store_true",
                    help="execute waves of scope-disjoint tasks concurrently, "
                         "one governed agent per task")
    li.add_argument("--design", action="store_true",
                    help="run the Design Contract + human design approval "
                         "before implementation planning")
    li.add_argument("--design-only", action="store_true",
                    help="stop after the human approves the Design Contract "
                         "instead of continuing to Plan/Build/Review")
    li.add_argument("--reuse-approved-design", action="store_true",
                    help="start at Plan from an unchanged completed "
                         "design-only loop with the same requirement/spec "
                         "and attributable --by authority")
    li.add_argument("--force", action="store_true",
                    help="replace an in-flight loop (the old loop.json is "
                         "archived first — without this flag re-init refuses)")
    li.add_argument("--advisory", action="store_true",
                    help="continue with visibly advisory screen enforcement")
    li.add_argument("--by", default=None,
                    help="human identity required with --advisory and with "
                         "TASKPLANE_STAGE_NATIVE=new-run; the new-run value "
                         "becomes the root stage authority.actor and must use "
                         "identifier syntax (for example human:vdemkiv; no "
                         "spaces)")
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
    ln.add_argument("--advisory", action="store_true",
                    help="acknowledge degraded screen enforcement")
    ln.add_argument("--by", default=None,
                    help="human identity required with --advisory")
    lw = lsub.add_parser("wave", help="print the EXECUTE wave: one brief per scope-disjoint task")
    lw.add_argument("--emit", choices=["workflow", "task", "auto"],
                    default="auto",
                    help="stage dispatch surface (R-0004): 'workflow' wraps "
                         "the EXECUTE wave as ONE ready-to-run execute-wave "
                         "workflow invocation covering every wave entry, "
                         "'task' prints today's wave payload byte-identically "
                         "(the mandatory fallback and the only Codex path), "
                         "'auto' consults workflow_available() (default)")
    lw.add_argument("--advisory", action="store_true",
                    help="acknowledge degraded screen enforcement")
    lw.add_argument("--by", default=None,
                    help="human identity required with --advisory")
    lc = lsub.add_parser("claim", help="a worker claims one wave task into its own worktree")
    lc.add_argument("task_id")
    lc.add_argument("--agent-workspace", required=True,
                    help="the worker's worktree — its contract activates there")
    lc.add_argument("--advisory", action="store_true",
                    help="acknowledge degraded screen enforcement")
    lc.add_argument("--by", default=None,
                    help="human identity required with --advisory")
    lg = lsub.add_parser("gate", help="orchestrator-only: judge the evidence and advance the loop")
    lg.add_argument("outcome", choices=["pass", "fail", "unavailable"])
    lg.add_argument("--note", default="",
                    help="one-line note recorded with the gate decision")
    lg.add_argument("--task", help="task id (parallel execute waves)")
    lg.add_argument("--req", help="attach requirement R-id to the loop "
                    "before DoR evaluation (design anchor)")
    lg.add_argument("--advisory", action="store_true",
                    help="acknowledge degraded screen enforcement")
    lg.add_argument("--by", default=None,
                    help="human identity required with --advisory")
    lsu = lsub.add_parser("submit", help="worker submits evidence without "
                            "transitioning state; the orchestrator gates")
    lsu.add_argument("outcome", choices=["pass", "fail", "unavailable"])
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
    la.add_argument("--advisory", action="store_true",
                    help="acknowledge degraded screen enforcement")
    lr = lsub.add_parser(
        "resolve", help="resolve a blocked loop: retry, pass, skip, defer or abort")
    lr.add_argument(
        "decision", choices=["retry", "pass", "skip", "defer", "abort"])
    lrp = lsub.add_parser(
        "replan", help="human: archive frozen tasks and return to Plan for "
        "a corrected plan plus fresh approval")
    lrp.add_argument("--by", required=True,
                     help="human approving the return to Plan")
    lrp.add_argument("--reason", required=True,
                     help="configuration defect or changed decision")
    le = lsub.add_parser("evidence", help="assemble every mechanically-derivable "
                         "fact the evaluate gate will check (suite result, diff, "
                         "criteria, routed lenses, graph obligations) with the "
                         "judgment slots left empty for the evaluator to fill")
    le.add_argument("--task", help="task id (default: the loop's current task)")
    le.add_argument("--write", action="store_true",
                    help="also drop the skeleton at .eval/verdict.json when no "
                         "verdict is already there (never overwrites)")
    lguide = lsub.add_parser(
        "guide", help="before pass submission, check deterministic workflow "
        "facts and return one bounded drift correction")
    lguide.add_argument("--task", help="task id (parallel execute waves)")
    lau = lsub.add_parser(
        "authorize", help="derive routine authority for a real host/facade "
        "flow from the bound consolidated receipt")
    lau.add_argument(
        "flow", help="routine flow identity (facade, delivery, product, "
        "design, build, engineering, status, help, north_star or tag_slack)")
    lsub.add_parser(
        "host-input", help="consume one trusted-session host event JSON "
        "object from stdin through the governed human-input boundary")
    lsub.add_parser("status", help="show the loop's stage, tasks and gates")
    lsub.add_parser("retro", help="print the loop retrospective")
    lsub.add_parser("verify-dispatch", help="audit whether dispatched agents "
                    "used the models the briefs resolved (tier routing)")
    lp.set_defaults(fn=cmd_loop)

    sg = sub.add_parser(
        "stage", help="drive isolated stage lifecycle and bounded handoffs")
    sg.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    sgsub = sg.add_subparsers(dest="stage_action", required=True)
    for action, help_text in (
            ("start", "start a root or verified successor stage"),
            ("resume", "create a fresh attempt in an active stage root"),
            ("terminalize", "record one immutable terminal outcome"),
            ("terminalize-and-start", "atomically terminalize a predecessor "
                                      "and start its verified successor"),
            ("split", "close a parent and atomically create isolated children"),
            ("history", "read a bounded page of immutable stage summaries"),
            ("reuse", "explicitly authorize non-default artifact reuse")):
        command = sgsub.add_parser(action, help=help_text)
        command.add_argument(
            "--request", required=True, metavar="FILE|-",
            help="closed stage-command JSON object; '-' reads standard input")
        command.add_argument(
            "--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    sg.set_defaults(fn=cmd_stage)

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
    lnd.add_argument("--max-actions", type=int, default=None,
                     dest="max_actions",
                     help="per-agent action ceiling written into each "
                          "dispatched lens brief. Default scales with the "
                          "brief: 45 for a deep lens (it owns one subject at "
                          "full depth and reads widely), 30 for the sweep. "
                          "An explicit value applies to every brief.")
    lnd.add_argument("--artifact-type",
                     help="route on an artifact instead of the diff — "
                          "'strategy' summons the advisory (board) tier")
    lnd.add_argument("--dashboard", action="store_true",
                     help="print the live lens-wave progress board instead "
                          "of the JSON briefs (render this BEFORE dispatch)")
    lnd.add_argument("--resume", action="store_true",
                     help="re-dispatch ONLY the lanes that have no "
                          "findings.json yet — an interrupted wave costs "
                          "the lenses that did not land, not all of them")
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
    ym.add_argument("verdict", choices=[
        "acted", "dismissed", "resolved", "accepted", "closed", "deferred",
        "not-a-defect"], help="durable human disposition")
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
    ra = rqsub.add_parser("amend", help="revise the same requirement after "
                            "Product requests changes")
    ra.add_argument("id", metavar="R-XXXX")
    ra.add_argument("--functional", action="append",
                    help="replace functional statements (repeatable)")
    ra.add_argument("--nfr", action="append", metavar="LENS=STATEMENT",
                    help="add or replace an NFR by lens (repeatable)")
    ra.add_argument("--acceptance", action="append",
                    help="replace acceptance criteria (repeatable)")
    ra.add_argument("--open", action="append",
                    help="replace open questions (repeatable)")
    ra.add_argument("--clear-open", action="store_true",
                    help="close every open product question")
    ra.add_argument("--files", help="replace comma-separated context globs")
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
    rsg = rqsub.add_parser("signoff", help="record the human Product gate")
    rsg.add_argument("id", metavar="R-XXXX")
    rsg.add_argument("decision", choices=("approve", "changes"))
    rsg.add_argument("--by", required=True,
                     help="the human approval or change-request words")
    rsg.add_argument("--note", help="optional decision rationale")
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
    gs.add_argument("--strict", action="store_true",
                    help="persist the normal fail-open record, then return "
                         "nonzero when any graph producer is degraded")
    gsf = gs.add_mutually_exclusive_group()
    gsf.add_argument("--json", action="store_true",
                     help="print machine JSON (the backward-compatible "
                          "default)")
    gsf.add_argument("--text", action="store_true",
                     help="print a concise human graph-quality report")
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
    gh.add_argument("--focus", type=int, metavar="DEPTH",
                    help="crop to the changed set plus everything within "
                         "DEPTH dependency hops — the same map, small "
                         "enough to render inline in chat")
    gh.add_argument("--fragment", action="store_true",
                    help="emit an embeddable fragment (the same page, "
                         "carried byte-for-byte in an srcdoc iframe) so the "
                         "graph can be shown inline instead of as a file")
    gp.set_defaults(fn=cmd_graph)

    db = sub.add_parser("dashboard", help="render the mission-control view")
    db.add_argument("--out", help="write the standalone report to this path")
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
    op.add_argument("--install-codex-hooks", action="store_true",
                    help="install/refresh the repo-local Codex lifecycle hook "
                         "bridge before reporting readiness")
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
    fp.add_argument("--html", action="store_true",
                    help="emit ONE self-contained HTML document (palette and "
                         "dark mode included) — the documented fallback when "
                         "the host cannot render inline fragments")
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

    rvp = sub.add_parser("review", help="open a review in ONE call — tools, "
                         "target pin, graph, impact, contract, obligations, "
                         "routing, runnability and the ready-to-dispatch "
                         "briefs, as one JSON payload")
    rvsub = rvp.add_subparsers(dest="review_action")
    rvs = rvsub.add_parser("start", help="establish the facts and activate "
                           "the read-only contract")
    rvs.add_argument("spec", nargs="?", help="PR url, OWNER/REPO#N, or a ref")
    rvs.add_argument("--base", default=None, help="diff base ref")
    rvs.add_argument("--fetch", action="store_true",
                     help="fetch pull/N/head into this checkout first")
    rvs.add_argument("--goal", nargs="*", default=None,
                     help="contract goal text (default: derived)")
    rvs.add_argument("--max-actions", type=int, default=None,
                     dest="max_actions",
                     help="action ceiling for the review contract (default "
                          "40). Prefer --max-tokens: an action cost ~11k "
                          "effective tokens on the measured review, with a "
                          "two-order-of-magnitude spread")
    rvs.add_argument("--max-tokens", type=int, default=None, dest="max_tokens",
                     help="effective-token ceiling for the review contract")
    rvs.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvs.add_argument("--run-id", default=None,
                     help="resume or deterministically name the repository "
                          "preflight run")
    rvs.add_argument("--advisory", action="store_true",
                     help="continue with visibly advisory screen enforcement")
    rvs.add_argument("--by", default=None,
                     help="human identity required with --advisory")
    rvs.set_defaults(fn=cmd_review)
    rvr = rvsub.add_parser(
        "resume", help="apply one explicit user decision and continue the "
        "same repository preflight and review")
    rvr.add_argument("--run-id", required=True,
                     help="run-id from the needs_user preflight response")
    rvr.add_argument("--action-id", required=True,
                     help="exact pending user-action id")
    rvr.add_argument("--response", required=True,
                     choices=("approve", "retry", "initialize", "cancel"),
                     help="the user's decision for the pending action")
    rvr.add_argument("--by", required=True,
                     help="the user's approving/cancelling chat identity")
    rvr.add_argument("--advisory", action="store_true",
                     help="continue with visibly advisory screen enforcement")
    rvr.add_argument("--goal", nargs="*", default=None,
                     help="contract goal text after preflight resumes")
    rvr.add_argument("--max-actions", type=int, default=None,
                     dest="max_actions",
                     help="action ceiling for the resumed review contract")
    rvr.add_argument("--max-tokens", type=int, default=None,
                     dest="max_tokens",
                     help="effective-token ceiling for the resumed review")
    rvr.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvr.set_defaults(fn=cmd_review)
    rvc = rvsub.add_parser("collect", help="validate leased lens results and "
                           "publish one canonical findings revision")
    rvc.add_argument("--no-publish", action="store_true",
                     help="skip the external artifact-store snapshot (tests "
                          "and isolated calibration only)")
    rvc.add_argument("--run-id", default=None,
                     help="select one active review when several starts coexist")
    rvc.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvc.set_defaults(fn=cmd_review)
    rvo = rvsub.add_parser(
        "option", help="record the human's optional dynamic review/render choice")
    rvo.add_argument("selection", choices=("static", "dynamic", "dynamic-render"))
    rvo.add_argument("--receipt", default=None,
                     help="optional host message/turn reference; receipt "
                          "content is resolved from the host transcript")
    rvo.add_argument("--by", default=None,
                     help="deprecated display attribution; receipt actor is authoritative")
    rvo.add_argument("--run-id", required=True, help="active review run")
    rvo.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvo.set_defaults(fn=cmd_review)
    rve = rvsub.add_parser(
        "evidence", help="record approved dynamic validation or render evidence")
    rve.add_argument("kind", choices=("dynamic_validation", "functionality_render"))
    rve.add_argument("status", choices=("unavailable", "failed", "executed"))
    rve.add_argument("--detail", default="", help="bounded evidence summary")
    rve.add_argument("--receipt", default=None,
                     help="optional host message/turn reference; receipt "
                          "content is resolved from the host transcript")
    rve.add_argument("--run-id", required=True, help="active review run")
    rve.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rve.set_defaults(fn=cmd_review)
    rvsa = rvsub.add_parser(
        "sandbox", help="create a disposable writable PR copy for "
        "validation-only build repair and dynamic checks")
    rvsa.add_argument("--run-id", required=True, help="active review run")
    rvsa.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvsa.set_defaults(fn=cmd_review)
    rvv = rvsub.add_parser(
        "validate", help="run one argv-only dynamic check inside the registered "
        "validation sandbox and record its evidence")
    rvv.add_argument("--run-id", required=True, help="active review run")
    rvv.add_argument("--cwd", default=".", help="sandbox-relative working directory")
    rvv.add_argument("--timeout", type=int, default=600,
                     help="command timeout in seconds (maximum 1800)")
    rvv.add_argument("command", nargs=argparse.REMAINDER,
                     help="command argv after --; no shell interpretation")
    rvv.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvv.set_defaults(fn=cmd_review)
    rvsg = rvsub.add_parser("signoff", help="record the human decision for a "
                            "collected standalone review")
    rvsg.add_argument("decision", choices=("approve", "changes"))
    rvsg.add_argument("--by", required=True,
                      help="the human approval or change-request words")
    rvsg.add_argument("--note", help="optional decision rationale")
    rvsg.add_argument("--run-id", default=None,
                      help="select the collected review run")
    rvsg.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvsg.add_argument("--advisory", action="store_true",
                      help="acknowledge degraded screen enforcement")
    rvsg.set_defaults(fn=cmd_review)
    rvp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rvp.set_defaults(fn=cmd_review)

    tg = sub.add_parser("target", help="what is being reviewed — acquire a "
                        "pull request, pin the checkout, or check that git "
                        "and gh are actually available")
    tg.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tg.add_argument("--json", action="store_true",
                    help="print the target record as JSON")
    tgsub = tg.add_subparsers(dest="target_action")
    tgf = tgsub.add_parser("fetch", help="fetch a pull request into this "
                           "checkout and pin it (git fetch pull/N/head)")
    tgf.add_argument("spec", help="PR url, OWNER/REPO#N, or #N")
    tgf.add_argument("--base", default=None,
                     help="diff base (default: the remote's default branch)")
    tgf.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tgp = tgsub.add_parser("pin", help="record what THIS checkout is — "
                           "origin, head, base, dirty state, fingerprint")
    tgp.add_argument("--base", default=None, help="diff base ref")
    tgp.add_argument("--spec", default=None,
                     help="the target this checkout represents (PR url, ref)")
    tgp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tgt_ = tgsub.add_parser("tools", help="is git present, is gh present and "
                            "authenticated — a remote PR review needs both")
    tgt_.add_argument("--install", action="store_true",
                      help="install gh via this host's package manager")
    tgt_.add_argument("--json", action="store_true", help="JSON report")
    tgt_.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tgs = tgsub.add_parser("show", help="print the pinned target record")
    tgs.add_argument("--json", action="store_true", help="JSON report")
    tgs.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    tg.set_defaults(fn=cmd_target)

    rp = sub.add_parser(
        "repository", help="automatic source precondition: resolve, "
        "authenticate, acquire, checkout, verify, and resume")
    rpsub = rp.add_subparsers(dest="repository_action", required=True)
    rpp = rpsub.add_parser(
        "prepare", help="prepare a local repository or remote pull request")
    rpp.add_argument("spec", help="PR URL, OWNER/REPO#N, ref, or local target")
    rpp.add_argument("--run-id", default=None,
                     help="optional stable run id for idempotent retry")
    rpp.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rpp.set_defaults(fn=cmd_repository)
    rpr = rpsub.add_parser(
        "resume", help="apply an explicit user action and resume the same run")
    rpr.add_argument("--run-id", required=True,
                     help="run-id from the needs_user response")
    rpr.add_argument("--action-id", required=True,
                     help="exact pending user-action id")
    rpr.add_argument("--response", required=True,
                     choices=("approve", "retry", "initialize", "cancel"),
                     help="the user's decision for the pending action")
    rpr.add_argument("--by", required=True,
                     help="human chat identity approving the action")
    rpr.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rpr.set_defaults(fn=cmd_repository)
    rps = rpsub.add_parser("status", help="print one canonical run manifest")
    rps.add_argument("--run-id", required=True,
                     help="canonical repository/run manifest id")
    rps.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rps.set_defaults(fn=cmd_repository)
    rpm = rpsub.add_parser(
        "migrate", help="register clean legacy .em-review/scratch clones "
        "without moving or deleting anything")
    rpm.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    rpm.set_defaults(fn=cmd_repository)

    ak = sub.add_parser("ack", help="discharge an obligation the engine "
                        "issued (WS-F evals); --status lists what is open")
    ak.add_argument("id", nargs="?", help="obligation id, e.g. o-1a2b3c4d5e")
    ak.add_argument("--evidence", default="",
                    help="one line on how it was shown")
    ak.add_argument("--fingerprint", default=None,
                    help="content fingerprint of what was actually shown "
                         "(defaults to the artifact the obligation names)")
    ak.add_argument("--delivered", metavar="PATH",
                    help="discharge by DELIVERING the engine's artifact file "
                         "(SendUserFile / the host's artifact channel) "
                         "instead of retyping it inline — same bytes, same "
                         "fingerprint, none of the re-authoring cost")
    ak.add_argument("--status", action="store_true",
                    help="print issued / acknowledged / open / mismatched")
    ak.add_argument("--workspace", default=argparse.SUPPRESS, help=_WS_HELP)
    ak.set_defaults(fn=cmd_ack)

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
        return _run_hook_command(a)
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
