#!/usr/bin/env python3
"""Run taskPlane skills against one checkout-local plugin bundle.

This is the paid/model half of the evaluation layer.  It never trusts an
ambient marketplace install: the exact source under evaluation is copied
into the disposable fixture, fingerprinted before and after the run, and the
native host is told to read that exact SKILL.md and execute that copy's CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def skill_manifest(*, skill: str, bundle: dict, ws: str, host: str) -> dict:
    rel_root = os.path.relpath(bundle["root"], ws).replace(os.sep, "/")
    skill_path = f"{rel_root}/skills/{skill}/SKILL.md"
    return {
        "schema": "taskplane.skill-validation/v2", "skill": skill,
        "host": host, "goal": PROMPTS[skill],
        "bundle": {"version": bundle["version"],
                   "fingerprint": bundle["fingerprint"],
                   "root": rel_root, "skill_path": skill_path},
        "instructions": [
            f"Read {skill_path} completely before acting and follow it exactly.",
            f"Use only the taskPlane CLI under {rel_root}/taskplane/tp.py; do not use an ambient installed plugin copy.",
            "The fixture is pre-onboarded with private knowledge storage.",
            "Never approve a human gate. Stop and report it when reached.",
        ],
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


class EvalCodexAdapter(eval_drivers.CodexAdapter):
    def __init__(self, *, model=None, reasoning_effort=None, **kw):
        super().__init__(**kw)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def argv(self, cwd: str) -> list[str]:
        argv = [self.executable, "exec", "--json", "--cd", cwd,
                "--ephemeral", "--ignore-user-config", "--approve-for-me",
                "--dangerously-bypass-hook-trust"]
        if self.model:
            argv += ["--model", self.model]
        if self.reasoning_effort:
            argv += ["--config", "model_reasoning_effort=" +
                     json.dumps(self.reasoning_effort)]
        return argv + ["-"]


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
        if host == "codex" and os.path.isdir(DEFAULT_CODEX_HOME):
            # `--ignore-user-config` still reads authentication from
            # CODEX_HOME, while refusing that directory's config/plugins.
            env["CODEX_HOME"] = DEFAULT_CODEX_HOME
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
        manifest = skill_manifest(skill=skill, bundle=bundle, ws=ctx.ws,
                                  host=host)
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
