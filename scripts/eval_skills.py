#!/usr/bin/env python3
"""Run taskPlane skills against one checkout-local plugin bundle.

This is the paid/model half of the evaluation layer.  It never trusts an
ambient marketplace install: the exact source under evaluation is copied
into the disposable fixture, fingerprinted before and after the run, and the
native host is told to read that exact SKILL.md and execute that copy's CLI.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CODEX_HOME = (os.environ.get("CODEX_HOME") or
                      os.path.join(os.path.expanduser("~"), ".codex"))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import eval_drivers  # noqa: E402
import eval_record  # noqa: E402
import derivation  # noqa: E402

STAGE_DIR = os.path.join(".taskplane-eval", "plugin")
STAGED_PATHS = (
    ".codex-plugin", ".claude-plugin", "agents", "assets", "commands",
    "docs", "hooks", "lenses", "skills", "taskplane",
)

PROMPTS = {
    "taskplane": "Govern a checkout discount validation fix through the facade and stop at the first human gate.",
    "tp-go": "Drive the governed checkout discount validation fix and stop at the first human gate.",
    "tp-build": "Build checkout discount validation, including explicit invalid-code behavior, and stop at design approval.",
    "tp-design": "Design checkout discount validation without changing product code and stop at design approval.",
    "tp-engineering": "Review the frozen branch with one ReviewKernel context, selective lenses, leased results, and canonical collection.",
    "tp-product": "Define and score the WHAT for checkout discount validation; do not design or implement it.",
    "tp-help": "Run onboarding-first help and give only the next useful guidance; do not start delivery.",
    "tp-status": "Report actual taskPlane state and required human action without advancing anything.",
    "tp-northstar": "Assess checkout discount validation against the configured north star; remain advisory and honest if it is unset.",
}
NATIVE_SKILLS = tuple(PROMPTS)
DELEGATING_SKILLS = frozenset({
    "taskplane", "tp-go", "tp-build", "tp-design", "tp-engineering",
})


def _files(root: str):
    for rel in STAGED_PATHS:
        base = os.path.join(root, rel)
        if os.path.isfile(base):
            yield rel, base
            continue
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in {
                "__pycache__", ".pytest_cache", ".mypy_cache"})
            for name in sorted(names):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(dirpath, name)
                yield os.path.relpath(path, root).replace(os.sep, "/"), path


def bundle_fingerprint(root: str) -> str:
    digest = hashlib.sha256()
    for rel, path in sorted(_files(root)):
        digest.update(rel.encode("utf-8") + b"\0")
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def stage_bundle(root: str, ws: str) -> dict:
    destination = os.path.join(ws, STAGE_DIR)
    if os.path.lexists(destination):
        raise eval_record.RecorderError(
            f"evaluation bundle destination already exists: {destination}")
    os.makedirs(destination, exist_ok=False)
    for rel in STAGED_PATHS:
        source = os.path.join(root, rel)
        target = os.path.join(destination, rel)
        if os.path.isdir(source):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".mypy_cache", "*.pyc", "*.pyo"))
        elif os.path.isfile(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
    source_fp = bundle_fingerprint(root)
    staged_fp = bundle_fingerprint(destination)
    if source_fp != staged_fp:
        raise eval_record.RecorderError("staged plugin bundle differs from source")
    with open(os.path.join(destination, ".codex-plugin", "plugin.json"),
              encoding="utf-8") as stream:
        manifest = json.load(stream)
    return {"root": destination, "fingerprint": staged_fp,
            "version": manifest.get("version")}


def skill_manifest(*, skill: str, bundle: dict, ws: str, host: str,
                   base: str | None = None,
                   head: str | None = None) -> dict:
    rel_root = os.path.relpath(bundle["root"], ws).replace(os.sep, "/")
    skill_path = f"{rel_root}/skills/{skill}/SKILL.md"
    instructions = [
        f"Read {skill_path} completely before acting and follow it exactly.",
        f"Use only the taskPlane CLI under {rel_root}/taskplane/tp.py; do not use an ambient installed plugin copy.",
        "The fixture is pre-onboarded with private knowledge storage.",
        "Never approve a human gate. Stop and report it when reached.",
    ]
    if skill in DELEGATING_SKILLS:
        instructions.append(
            "The user explicitly requests and authorizes every native "
            "subagent delegation required by this skill. Dispatch each role "
            "emitted by taskPlane with its exact native brief; never replace "
            "a required worker by performing that role inline.")
    if skill == "tp-engineering" and base and head:
        instructions.append(
            f"Review the exact fixture target {head} against base {base}. "
            "Pass those immutable SHAs to `review start`; do not substitute "
            "a branch name such as main, which may already point at HEAD.")
    return {
        "schema": "taskplane.skill-validation/v2", "skill": skill,
        "host": host, "goal": PROMPTS[skill],
        "bundle": {"version": bundle["version"],
                   "fingerprint": bundle["fingerprint"],
                   "root": rel_root, "skill_path": skill_path},
        "instructions": instructions,
    }


def prepare_fixture(*, bundle: dict, ws: str, env: dict) -> dict:
    """Resolve onboarding deterministically before the paid model starts."""
    cli = os.path.join(bundle["root"], "taskplane", "tp.py")
    commands = (
        [sys.executable, cli, "init", "--plan", "personal",
         "--workspace", ws],
        [sys.executable, cli, "onboard", "--install-codex-hooks", "--json",
         "--workspace", ws],
    )
    outputs = []
    for argv in commands:
        result = subprocess.run(
            argv, cwd=ws, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60)
        outputs.append({"argv": argv[2:], "returncode": result.returncode,
                        "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            return {"returncode": result.returncode, "steps": outputs}
    return {"returncode": 0, "steps": outputs}


def prepare_codex_home(destination: str) -> str:
    """Give eval Codex a private transcript store and only shared auth.

    Native SubagentStart/Stop provenance resolves parent and child records
    from the thread store.  ``--ephemeral`` removes that store and therefore
    makes the collaboration workflow mechanically unprovable.  A disposable
    CODEX_HOME keeps those records out of the user's normal task list while
    ``--ignore-user-config`` still excludes ambient plugins and preferences.
    """
    target = os.path.join(destination, "codex-home")
    os.makedirs(target, exist_ok=False)
    auth = os.path.join(DEFAULT_CODEX_HOME, "auth.json")
    if not os.path.isfile(auth):
        raise eval_record.RecorderError(
            "Codex evaluation requires an authenticated CODEX_HOME/auth.json")
    os.symlink(auth, os.path.join(target, "auth.json"))
    return target


def _event_time(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(
            value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def codex_session_trace(codex_home: str | None) -> list[dict]:
    """Read native parent/child lifecycle from Codex's private session store.

    The model cannot write this directory through its checkout sandbox; Codex
    itself authors it. This keeps a real ``spawn_agent`` call from being
    graded as inline work merely because a repo hook produced no row.
    """
    if not codex_home:
        return []
    root = os.path.join(codex_home, "sessions")
    paths = []
    for dirpath, _dirs, names in os.walk(root):
        paths.extend(os.path.join(dirpath, name) for name in names
                     if name.startswith("rollout-") and name.endswith(".jsonl"))
    rows = []
    for path in sorted(paths)[:256]:
        spawn = None
        child_id = None
        started = None
        model = None
        effort = None
        completed = None
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if len(line) > 2 * 1024 * 1024:
                        continue
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    payload = row.get("payload") or {}
                    if row.get("type") == "session_meta" and spawn is None:
                        source = payload.get("source") or {}
                        candidate = (((source.get("subagent") or {})
                                      .get("thread_spawn"))
                                     if isinstance(source, dict) else None)
                        if isinstance(candidate, dict):
                            spawn = candidate
                            child_id = payload.get("id")
                            started = _event_time(row.get("timestamp"))
                    elif row.get("type") == "turn_context" and model is None:
                        model = payload.get("model")
                        effort = (payload.get("effort") or
                                  payload.get("reasoning_effort"))
                    elif row.get("type") == "event_msg" and \
                            payload.get("type") == "task_complete":
                        completed = payload
        except OSError:
            continue
        if not spawn or not child_id:
            continue
        task_name = os.path.basename(str(spawn.get("agent_path") or ""))
        if not task_name:
            continue
        common = {
            "host": "codex", "source": "codex_session_store",
            "host_observed": True, "agent_id": child_id,
            "agent_type": task_name, "task_name": task_name,
            "parent_thread_id": spawn.get("parent_thread_id"),
            "depth": spawn.get("depth"), "model": model,
            "reasoning_effort": effort,
        }
        rows.append(dict(common, event="subagent_start", ts=started or 0.0))
        if isinstance(completed, dict):
            finished = completed.get("completed_at")
            stop_ts = (float(finished) if isinstance(finished, (int, float))
                       else started or 0.0)
            if started is not None and stop_ts < started:
                stop_ts = started
            rows.append(dict(common, event="subagent_stop",
                             ts=stop_ts,
                             status="completed"))
    return sorted(rows, key=lambda row: (row.get("ts", 0), row["event"],
                                         row["task_name"]))


def _codex_tool_commands(payload: dict) -> list[str]:
    """Extract every exec command from a host-authored rollout tool call.

    Code mode can orchestrate several ``tools.exec_command`` calls inside one
    host tool item.  Counting only the first makes a busy run look cheaper and
    can hide a second diff/impact derivation in the same item.
    """
    if payload.get("type") == "function_call" and payload.get("name") in {
            "exec_command", "functions.exec_command"}:
        raw = payload.get("arguments") or payload.get("input") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        command = str((args or {}).get("cmd") or "")
        return [command] if command else []
    if payload.get("type") != "custom_tool_call" or \
            payload.get("name") != "exec":
        return []
    source = str(payload.get("input") or "")
    markers = [match.start() for match in re.finditer(
        r"tools\.exec_command\(", source)]
    if not markers:
        return []
    # Current Codex code-mode emits a JavaScript object literal, not JSON:
    # `tools.exec_command({cmd:"...",workdir:"..."})`.  The previous parser
    # tried json.loads() on the whole object, so it silently missed every
    # parent taskPlane command while older JSON-shaped test fixtures passed.
    # Extract only the bounded string literal assigned to `cmd`; no command
    # arguments are persisted, and derivation.verb/classify still reduce it
    # to the existing safe vocabulary before anything reaches the record.
    commands = []
    for index, marker in enumerate(markers):
        end = markers[index + 1] if index + 1 < len(markers) else len(source)
        call = source[marker:min(end, marker + 64 * 1024)]
        match = re.search(
            r"[\"']?cmd[\"']?\s*:\s*(\"(?:\\\\.|[^\"\\\\])*\"|"
            r"'(?:\\\\.|[^'\\\\])*')", call, re.DOTALL)
        if match:
            literal = match.group(1)
            try:
                value = (json.loads(literal) if literal.startswith('"')
                         else ast.literal_eval(literal))
            except (SyntaxError, TypeError, ValueError):
                continue
            if isinstance(value, str) and value:
                commands.append(value)
                continue
        # Retain compatibility with the older fully-JSON call shape.
        match = re.search(r"tools\.exec_command\((\{.*?\})\)\s*;?", call,
                          re.DOTALL)
        if not match:
            continue
        try:
            args = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        command = str(args.get("cmd") or "")
        if command:
            commands.append(command)
    return commands


def _codex_tool_command(payload: dict) -> str | None:
    """Compatibility helper for callers that need only the first command."""
    commands = _codex_tool_commands(payload)
    return commands[0] if commands else None


def codex_session_derivations(codex_home: str | None,
                              workspace: str) -> list[dict]:
    """Derivation rows from native Codex tool calls, never command text.

    The checkout hook ledger remains primary. This is the out-of-band
    evaluator's host-authored fallback when the Codex CLI does not deliver
    repository hooks. Only the bounded verb/classification output crosses
    into the record; command arguments and prose never do.
    """
    if not codex_home:
        return []
    root = os.path.join(codex_home, "sessions")
    paths = []
    for dirpath, _dirs, names in os.walk(root):
        paths.extend(os.path.join(dirpath, name) for name in names
                     if name.startswith("rollout-") and name.endswith(".jsonl"))
    rows = []
    for path in sorted(paths)[:256]:
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if len(line) > 2 * 1024 * 1024:
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    payload = event.get("payload") or {}
                    if event.get("type") != "response_item" or not isinstance(
                            payload, dict):
                        continue
                    for command in _codex_tool_commands(payload):
                        verb = derivation.verb(command)
                        if not verb:
                            continue
                        common = {
                            "ts": _event_time(event.get("timestamp")) or 0.0,
                            "host": "codex",
                            "source": "codex_session_store",
                            "host_observed": True}
                        rows.append({**common, "event": "command", "verb": verb,
                                     "decision": "observed"})
                        for key in derivation.classify(command):
                            rows.append({**common, "event": "derived", "key": key,
                                         "input_key": derivation.input_key(
                                             workspace, key)})
        except OSError:
            continue
    return sorted(rows, key=lambda row: (
        row.get("ts", 0), row.get("event", ""), row.get("verb", ""),
        row.get("key", "")))


class EvalCodexAdapter(eval_drivers.CodexAdapter):
    def __init__(self, *, model=None, reasoning_effort=None, **kw):
        super().__init__(**kw)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def argv(self, cwd: str) -> list[str]:
        argv = [self.executable, "exec", "--json", "--cd", cwd,
                "--ignore-user-config", "--approve-for-me",
                "--dangerously-bypass-hook-trust",
                # Native worker dispatch is part of the workflow under test.
                # Make the capability explicit because ignore-user-config
                # must isolate ambient plugins without silently removing the
                # Codex collaboration tool that taskplane is evaluating.
                "--enable", "multi_agent", "--enable", "multi_agent_v2",
                # The model reads the staged bundle by exact path.  Account-
                # installed plugins/apps are unrelated evaluator inputs and
                # must not be synced into the disposable host.
                "--disable", "plugins", "--disable", "remote_plugin",
                "--disable", "apps"]
        if self.model:
            argv += ["--model", self.model]
        if self.reasoning_effort:
            argv += ["--config", "model_reasoning_effort=" +
                     json.dumps(self.reasoning_effort)]
        return argv + ["-"]

    def run(self, manifest, *, cwd: str, timeout_s: float = 900,
            cancel=None, env=None) -> dict:
        result = super().run(manifest, cwd=cwd, timeout_s=timeout_s,
                             cancel=cancel, env=env)
        native = codex_session_trace((env or {}).get("CODEX_HOME"))
        native_derivations = codex_session_derivations(
            (env or {}).get("CODEX_HOME"), cwd)
        result["native_trace"] = native
        result["native_derivations"] = native_derivations
        result["native_dispatches"] = sum(
            row.get("event") == "subagent_start" for row in native)
        if native or native_derivations:
            result["telemetry_method"] = "codex_session_store"
        return result


class EvalClaudeAdapter(eval_drivers.ClaudeAdapter):
    def __init__(self, *, plugin_root: str, model=None,
                 reasoning_effort=None, **kw):
        super().__init__(**kw)
        self.plugin_root = plugin_root
        self.model = model
        self.reasoning_effort = reasoning_effort

    def argv(self, cwd: str) -> list[str]:
        del cwd
        argv = [self.executable, "--print", "--output-format", "stream-json",
                "--verbose", "--no-session-persistence",
                "--setting-sources", "project", "--plugin-dir",
                self.plugin_root, "--dangerously-skip-permissions"]
        if self.model:
            argv += ["--model", self.model]
        if self.reasoning_effort:
            argv += ["--effort", self.reasoning_effort]
        return argv


def run_skill(*, skill: str, host: str, output_root: str,
              timeout_s: float, model: str | None,
              reasoning_effort: str | None) -> dict:
    dest = os.path.join(output_root, "work", skill)
    out_dir = os.path.join(output_root, "records", skill)
    prepared = {}

    def setup(*, root, ws, dest, env):
        # Keep evaluator code outside the Git checkout.  Staging it under
        # `ws` would turn the plugin itself into the largest untracked
        # "feature change" and corrupt diff, graph impact, and lens routing.
        bundle = stage_bundle(root, dest)
        env["PLUGIN_ROOT"] = bundle["root"]
        env["CLAUDE_PLUGIN_ROOT"] = bundle["root"]
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if host == "codex":
            env["CODEX_HOME"] = prepare_codex_home(dest)
        onboard = prepare_fixture(bundle=bundle, ws=ws, env=env)
        if onboard["returncode"]:
            last = onboard["steps"][-1]
            raise eval_record.RecorderError(
                "checkout-local onboarding failed "
                f"(exit {onboard['returncode']}): {last['stderr'] or last['stdout']}")
        prepared.update(bundle=bundle, onboarding=onboard)
        return {"bundle": bundle, "onboarding": onboard}

    def drive(ctx):
        bundle = prepared["bundle"]
        onboard = prepared["onboarding"]
        adapter = (EvalCodexAdapter(model=model,
                                    reasoning_effort=reasoning_effort)
                   if host == "codex" else
                   EvalClaudeAdapter(plugin_root=bundle["root"], model=model,
                                     reasoning_effort=reasoning_effort))
        manifest = skill_manifest(
            skill=skill, bundle=bundle, ws=ctx.ws, host=host,
            base=ctx.base, head=ctx.head)
        result = adapter.run(manifest, cwd=ctx.ws, timeout_s=timeout_s,
                             env=ctx.env)
        result["onboarding"] = onboard
        after = bundle_fingerprint(bundle["root"])
        result["bundle"] = dict(bundle, fingerprint_after=after,
                                unchanged=after == bundle["fingerprint"])
        if after != bundle["fingerprint"]:
            result["status"] = "failed"
            result["reason"] = "model modified the immutable evaluation bundle"
        return result

    return eval_record.record_run(
        root=ROOT, dest=dest, out_dir=out_dir, driver=drive, skill=skill,
        run_id="native-current", mode="out-of-band",
        schema=eval_record.RUN_SCHEMA_V2, model=model,
        reasoning_effort=reasoning_effort, setup=setup)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("skills", nargs="+", choices=("all",) + tuple(
        sorted(NATIVE_SKILLS)))
    parser.add_argument("--host", choices=("codex", "claude"), default="codex")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--model", required=True,
                        help="explicit host model id; never inferred from user config")
    parser.add_argument("--reasoning-effort", default="high")
    args = parser.parse_args(argv)
    skills = (list(NATIVE_SKILLS)
              if args.skills == ["all"] else args.skills)
    rows = []
    for skill in skills:
        result = run_skill(skill=skill, host=args.host,
                           output_root=os.path.abspath(args.output_root),
                           timeout_s=args.timeout, model=args.model,
                           reasoning_effort=args.reasoning_effort)
        run = result["run"]
        rows.append({"skill": skill, "record": result["path"],
                     "eligible": run.get("baseline_eligible"),
                     "reason": run.get("baseline_reason")})
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0 if all(row["eligible"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
