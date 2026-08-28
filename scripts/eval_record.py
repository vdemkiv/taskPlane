#!/usr/bin/env python3
"""Record a model-behaviour eval run — the half that needs a model.

    python3 scripts/eval_record.py --build /tmp/fixture     # just the tree
    python3 scripts/eval_record.py --check                  # verify the pins

WHY THE LAYER IS SPLIT HERE
---------------------------
`taskplane/eval_rubric.py` scores a recorded run and is a PURE function of
data, so it can gate CI forever at no cost. This module produces the record,
and producing one costs a model. Those two facts pull in opposite directions,
and the split is what lets both be true at once: the recorder runs
out-of-band, by hand or on a schedule, and CI only ever reads what it froze.

That split is only honest if the recorder is itself testable, because
otherwise the half that produces every record is the half nobody checks. So
the model-driving step is a SEAM — `record_run(driver=...)` takes a callable.
In the field it drives a real model; in `taskplane/tests/test_eval_recorder.py`
it is a stub, and the fixture build, the credential scrub, the pre-flight
probe, the synthesis and the eligibility rule are all exercised end to end at
zero token cost.

WHAT A RECORDED RUN IS
----------------------
A frozen pull request (`evals/fixture-repo/`), reviewed inside a throwaway
checkout with no credentials and a local bare origin, by a model whose
session is instrumented, with the engine's own records frozen afterwards into

    evals/runs/<skill>/<run_id>/
        trace.jsonl obligations.jsonl dispatch.json derivations.jsonl
        context.jsonl run.json

under `eval_rubric.RECORD_FILES`' exact names. Three of those the engine
writes. `context.jsonl` no engine writes at all, and the per-brief rows under
`dispatch.json`'s additive `briefs` key are not in the report the engine
emits — `eval_scenario.SYNTHETIC_EVENTS` and `SYNTHETIC_FIELDS` name every
one of those gaps, and closing them is this module's other job.

THE TWO REFUSALS
----------------
`derivation.probe()` runs BEFORE the model does, and `None` aborts the run.
A ledger with no probe row scores `instrument: broken` for the whole record,
because zero repeats over a ledger nobody could write is not a measurement —
it is the shape of a recorder that never appended a line.

Only a clean out-of-band run may set or satisfy a baseline. An in-session
`subagent` run is informational forever, and a run the dispatch hook never
observed (`hook_active` false) reports UNKNOWN fan-out rather than zero. Both
rules are written into `run.json` as `baseline_eligible` with a reason, not
into a document — a rule that lives in prose is a rule that gets waived by
whoever needs a baseline moved.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import derivation                      # noqa: E402
import eval_drivers                    # noqa: E402
import eval_rubric                     # noqa: E402
import eval_scenario                   # noqa: E402
import lens as lens_router             # noqa: E402
import loop as loop_mod                # noqa: E402
import obligations                     # noqa: E402
import runtime_eval                    # noqa: E402
import spend                           # noqa: E402
import target as target_mod            # noqa: E402
import taskplane_lite as tp            # noqa: E402

RUN_SCHEMA = "taskplane.eval-run/v1"
RUN_SCHEMA_V2 = "taskplane.eval-run/v2"

FIXTURE_DIRNAME = os.path.join("evals", "fixture-repo")
RUNS_DIRNAME = os.path.join("evals", "runs")
RUNS_V2_DIRNAME = os.path.join("evals", "runs-v2")
MANIFEST_NAME = "manifest.json"

# How the run was driven, and it decides eligibility on its own. An
# in-session subagent shares a transcript, a token budget and a host with the
# session that spawned it, so its behaviour is not the behaviour of a skill
# invoked cleanly — it is informational, permanently.
MODES = ("out-of-band", "subagent")

# Scrubbed from every environment this module builds. A recorded run reviews
# a LOCAL fixture: a token buys it nothing and risks a model reaching the
# network in the middle of an eval, which is the single thing the frozen
# fixture exists to prevent.
CREDENTIAL_VARS = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_API_TOKEN",
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "NPM_TOKEN",
)

# Anything whose NAME says it carries a secret. The list above is what is
# known today; this is what catches the one added tomorrow.
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY",
                   "APIKEY", "CREDENTIAL", "PRIVATE_KEY")

# The only ambient variables that survive into a built environment. An
# allowlist, not a denylist: a denylist is a promise to have thought of every
# variable a host might invent.
PASSTHROUGH = ("PATH", "PYTHONPATH", "TMPDIR", "TEMP", "TMP",
               "LD_LIBRARY_PATH", "SystemRoot", "COMSPEC", "PATHEXT")

# The dirs a run's own machinery owns. Never part of "what the model wrote".
_RUNTIME_DIRS = (".git", ".taskplane", ".claude", ".codex",
                 ".taskplane-eval")

# Never copied out of a fixture tree: derived bytes, not source.
_NEVER_COPY = ("__pycache__", ".pytest_cache", ".mypy_cache", ".git")


class RecorderError(RuntimeError):
    """The run cannot be recorded, and saying so is the whole point."""


class FixtureMismatch(RecorderError):
    """The built tree is not the tree the manifest pins."""


class InstrumentBroken(RecorderError):
    """The derivation ledger could not be probed, so nothing it says counts."""


class CredentialLeak(RecorderError):
    """A secret was about to be handed to a recorded run."""


# ================================================================== the I/O

def _read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True, default=str)
        f.write("\n")


def _read_jsonl(path):
    out = []
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue          # a torn last line must not blind a run
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return out
    return out


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _sha256(path):
    try:
        with io.open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _mtime(path, fallback):
    try:
        return float(os.stat(path).st_mtime)
    except OSError:
        return float(fallback)


def _rel(ws, path):
    return tp.to_posix(os.path.relpath(path, ws))


# ================================================================== the git
#
# Every call names its encoding. `text=True` alone decodes the child with the
# locale's encoding, which is ascii on a bare CI runner, and this module
# shells out to git dozens of times per run.

def _git(args, cwd, env, *, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "").strip()
    if check and proc.returncode != 0:
        raise RecorderError(f"git {' '.join(args)} failed in {cwd} "
                            f"(exit {proc.returncode}):\n{out}")
    return out


def _base_env(home):
    """An environment BUILT, never inherited.

    Inheriting is how a fixture becomes reproducible on one machine and
    nowhere else: an ambient `GIT_AUTHOR_DATE`, a global `[user]` block or a
    `core.autocrlf` set years ago all move the commit SHA, and the resulting
    failure names nothing that would lead anyone to the cause.
    """
    env = {k: os.environ[k] for k in PASSTHROUGH if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env.update({
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        # All three config sources, off. GIT_CONFIG_GLOBAL alone leaves
        # /etc/gitconfig live, and a system-wide core.autocrlf would rewrite
        # the trees under the builder's feet.
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _pinned_env(manifest, home):
    """`_base_env` plus the identity, dates, TZ and locale the SHAs depend on."""
    ident = manifest.get("identity") or {}
    env = _base_env(home)
    env.update({
        "GIT_AUTHOR_NAME": ident.get("author_name", "taskplane fixture"),
        "GIT_AUTHOR_EMAIL": ident.get("author_email", "fixture@invalid"),
        "GIT_COMMITTER_NAME": ident.get("committer_name", "taskplane fixture"),
        "GIT_COMMITTER_EMAIL": ident.get("committer_email", "fixture@invalid"),
        "TZ": ident.get("tz", "UTC"),
        "LC_ALL": ident.get("locale", "C"),
        "LANG": ident.get("locale", "C"),
        "LANGUAGE": "",
    })
    return env


# ============================================================== the fixture

def fixture_dir(root: str) -> str:
    return os.path.join(root, FIXTURE_DIRNAME)


def load_manifest(root: str) -> dict:
    return _read_json(os.path.join(fixture_dir(root), MANIFEST_NAME))


def _materialize(tree: str, dest: str) -> None:
    """Make `dest` hold exactly `tree`, keeping only the repo's own metadata.

    Deletions matter: the second commit must be able to REMOVE a file, and a
    builder that only copies over the top would record an addition-only diff
    that no longer matches the trees on disk.
    """
    for name in sorted(os.listdir(dest)):
        if name in _RUNTIME_DIRS:
            continue
        path = os.path.join(dest, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    for dirpath, dirs, files in os.walk(tree):
        # A byte cache is not part of the fixture. One left behind by an
        # import would move the SHA and the refusal would name the TREE,
        # sending the reader to look for a source change that is not there.
        dirs[:] = sorted(d for d in dirs if d not in _NEVER_COPY)
        rel = os.path.relpath(dirpath, tree)
        out = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(out, exist_ok=True)
        os.chmod(out, 0o755)
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            src = os.path.join(dirpath, name)
            dst = os.path.join(out, name)
            shutil.copyfile(src, dst)
            # The MODE is part of the tree object. Pinning identity and dates
            # while letting an inherited umask or a copied executable bit
            # through would move the SHA for a reason nobody would look for.
            os.chmod(dst, 0o644)


def build_fixture(root: str, dest: str, *, fixture_root: str | None = None,
                  manifest: dict | None = None) -> dict:
    """Build `evals/fixture-repo/`'s two trees into a real repo at `dest`.

    Returns {path, base, head, shas, branch, env}. Raises `FixtureMismatch`
    the moment a commit does not hash to the SHA `manifest.json` pins — before
    a recorded run is allowed to proceed, because a run against an unpinned
    tree cannot be compared with any other run.
    """
    fixture = fixture_root or fixture_dir(root)
    man = manifest or _read_json(os.path.join(fixture, MANIFEST_NAME))
    dest = os.path.abspath(dest)
    if os.path.isdir(dest) and os.listdir(dest):
        raise RecorderError(f"{dest} already has contents — the fixture is "
                            f"built into a THROWAWAY directory, never over "
                            f"an existing checkout")
    os.makedirs(dest, exist_ok=True)
    home = dest.rstrip(os.sep) + "-home"
    os.makedirs(os.path.join(home, ".config"), exist_ok=True)
    env = _pinned_env(man, home)
    branch = man.get("branch") or "main"

    _git(["init", "-q", "."], dest, env)
    # Not `git init -b`: that flag is younger than the git versions this has
    # to run on, and the SHA of a commit does not depend on the ref that
    # points at it — but the branch NAME is part of what a driver checks out.
    _git(["symbolic-ref", "HEAD", "refs/heads/" + branch], dest, env)
    for key, value in sorted((man.get("config") or {}).items()):
        _git(["config", key, str(value)], dest, env)

    shas = []
    for spec in man.get("commits") or ():
        tree = os.path.join(fixture, str(spec.get("tree") or ""))
        if not os.path.isdir(tree):
            raise RecorderError(f"the manifest names a tree that is not "
                                f"there: {spec.get('tree')!r}")
        _materialize(tree, dest)
        _git(["add", "-A", "."], dest, env)
        dated = dict(env, GIT_AUTHOR_DATE=str(spec.get("date") or ""),
                     GIT_COMMITTER_DATE=str(spec.get("date") or ""))
        _git(["commit", "-q", "--no-verify", "--cleanup=verbatim",
              "-m", str(spec.get("message") or "")], dest, dated)
        sha = _git(["rev-parse", "HEAD"], dest, env)
        want = spec.get("sha")
        if want and want != "PENDING" and sha != want:
            raise FixtureMismatch(
                f"{spec.get('tree')} committed to {sha}, and manifest.json "
                f"pins {want}. Either the tree's bytes changed — one appended "
                f"byte is enough — or an ambient identity, clock, TZ, locale "
                f"or git config reached the build. Re-pin deliberately with "
                f"`--build` only after deciding which it was.")
        shas.append(sha)
    if not shas:
        raise RecorderError("the manifest declares no commits")

    built = {"path": dest, "shas": tuple(shas), "base": shas[0],
             "head": shas[-1], "branch": branch, "env": env, "home": home}
    for key in ("base", "head"):
        want = man.get(key)
        if want and want != "PENDING" and built[key] != want:
            raise FixtureMismatch(f"the built {key} is {built[key]} and "
                                  f"manifest.json pins {want}")
    return built


def changed_files(dest: str, base: str, head: str) -> list:
    """The files the fixture's pull request touches, base to head."""
    env = _base_env(os.path.join(dest, ".no-home"))
    out = _git(["diff", "--name-only", base, head], dest, env)
    return [line for line in out.splitlines() if line.strip()]


def _local_origin(dest: str, ws: str, branch: str, env: dict) -> str:
    """A bare origin on this disk. There is nothing to reach for and no
    credential that would help if there were."""
    bare = os.path.join(dest, "origin.git")
    _git(["init", "-q", "--bare", bare], dest, env)
    _git(["remote", "add", "origin", bare], ws, env)
    _git(["push", "-q", "origin", branch], ws, env)
    return bare


# ========================================================== the environment

def assert_no_credentials(env) -> None:
    """Raise unless `env` is free of anything that looks like a secret."""
    found = sorted(k for k in (env or {})
                   if k in CREDENTIAL_VARS
                   or any(m in k.upper() for m in _SECRET_MARKERS))
    if found:
        raise CredentialLeak(
            "a recorded run reviews a LOCAL fixture and must carry no "
            "credentials; this environment carries " + ", ".join(found))


def run_env(*, root: str, ws: str, home: str, store: str,
            manifest: dict | None = None) -> dict:
    """The environment the driver runs under: no credentials, no ambient git
    config, its own taskplane store, and the dispatch hook switched on."""
    env = _pinned_env(manifest or {}, home)
    env.update({
        # The engine's own wiring. Named as literal keys rather than module
        # constants: this file is not the documentation surface for the
        # engine's environment, and a second copy of a variable name is a
        # second thing free to drift.
        "TASKPLANE_WORKSPACE": ws,
        "TASKPLANE_HOME": store,
        # Without this the PreToolUse Task hook is inert, `hook_active` is
        # false, and the run may not set a baseline (see `_eligibility`).
        "TASKPLANE_ENFORCE_DISPATCH": "warn",
        "PLUGIN_ROOT": root,
        "CLAUDE_PLUGIN_ROOT": root,
    })
    assert_no_credentials(env)
    return env


@contextlib.contextmanager
def _applied(env):
    """Run with `env` as the process environment, then restore exactly.

    The recorder reads the engine's ledgers IN-PROCESS (`derivation.probe`,
    `obligations.read`), and those resolve their paths from the environment.
    A driver's children and the recorder's own reads must therefore see the
    SAME environment, or the probe certifies one ledger and the record
    freezes another.
    """
    prior = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        yield env
    finally:
        os.environ.clear()
        os.environ.update(prior)


def install_hooks(root: str, ws: str) -> str:
    """Put the plugin's hooks in the throwaway checkout's settings.

    An uninstrumented run is not a cheaper run; it is a run whose records are
    empty for reasons that have nothing to do with the model.
    """
    payload = _read_json(os.path.join(root, "hooks", "hooks.json"))
    path = os.path.join(ws, ".claude", "settings.json")
    _write_json(path, {"hooks": payload.get("hooks") or {},
                       "env": {"PLUGIN_ROOT": root,
                               "TASKPLANE_ENFORCE_DISPATCH": "warn"}})
    return path


# =============================================================== the naming

def lens_name(raw) -> str:
    """The lens id behind an agent name, a task name or a directory name.

    `subagent_start` carries `tp-lens-security`, the findings file lives in
    `.em-review/lens-security/`, and the rubric pairs the two on one key. One
    normalizer, or the pairing fails for a spelling difference.
    """
    name = str(raw or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    for prefix in ("tp-lens-", "lens-"):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return name


def brief_lens(entry) -> str:
    """Which lens a composed brief is for.

    `tp lens dispatch` records the lens id in `ref` and a generic `tp-lens`
    in `agent`, so `ref` is the answer whenever the brief is a lens brief.
    """
    ref = entry.get("ref")
    if entry.get("kind") == "lens" and isinstance(ref, str) and ref:
        return lens_name(ref)
    return lens_name(entry.get("agent") or entry.get("task_name") or "")


# ============================================================ the synthesis
#
# Everything the engine does NOT write, listed in
# `eval_scenario.SYNTHETIC_EVENTS` / `SYNTHETIC_FIELDS`. Pure functions over
# rows wherever the fact is in the rows, so the hard part is testable without
# a run.

def _ts(row):
    value = row.get(eval_scenario.ORDER_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (1, 0.0)
    return (0, float(value))


def _audit_field_key(name: str) -> str:
    """The closed trace key used for a machine field not on its allowlist."""
    return "field:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]


def _restore_closed_bool(row: dict, name: str) -> None:
    """Restore one known boolean without recovering arbitrary trace text."""
    value = row.get(_audit_field_key(name))
    if isinstance(value, bool):
        row[name] = value


def catalog_ids(root: str | None = None) -> set:
    try:
        cat = lens_router.load_catalog(root)
    except (OSError, ValueError, KeyError):
        return set()
    return {l.get("id") for l in (cat.get("lenses") or []) if l.get("id")}


def breadth_of(row, known) -> "str | None":
    """The routing breadth a `lens_route` row STATES, or `None` for "cannot
    tell".

    `--all` is the flag that switches the applicability engine off, and the
    rubric scores exactly that distinction. `loop.py` now writes the breadth
    it asked the router for onto the `lens_route` row itself
    (`requested_breadth`, beside `engine_ran`), so the answer is READ.

    WHY IT IS NO LONGER INFERRED. The old rule was: routed-set ⊇ catalog ⇒
    "all". `lens._route_v2` emits an output entry for EVERY catalog lens —
    n/a ones included, carrying their negative evidence, because coverage
    honesty needs them — so a signal-routed review's lens list IS the whole
    catalog. The rule did not merely misfire at an edge: it read `--all` on
    every routed review there has ever been, and the rubric row that means to
    catch "the model forced the catalog instead of letting the engine route"
    accused only compliant runs.

    THE FALLBACK IS REPAIRED, NOT REMOVED. Routes the loop makes with no
    workspace to record into leave no `lens_breadth` row and no stamp: the
    pm / plan / design briefs and `prime_scope` at execute/fix pass no
    workspace at all, an ungoverned tree has no `.taskplane/` to append to,
    and a PARALLEL evaluate routes against the worktree, so the breadth row
    lands there while `lens_route` lands in `ws`. For those:

      * a STRICT SUBSET of the catalog is still "routed" — a sweep of
        everything cannot produce fewer than everything, so this one is a
        deduction and not a guess;
      * the FULL catalog is `None`. It is equally consistent with a routed
        review and a forced one, and answering "all" is the instrument
        reporting its own blind spot as a finding — the failure this whole
        layer exists to catch. `None` writes no `breadth` at all, so the row
        is scored as unmeasured rather than as a violation.
    """
    for field in ("breadth", "requested_breadth"):
        stated = row.get(field)
        if isinstance(stated, str) and stated:
            return stated
    lenses = row.get("lenses")
    if not isinstance(lenses, (list, tuple)) or not lenses:
        return None
    named = set()
    for item in lenses:
        # `loop.py` traces [[id, mode], ...]; the recorder's own fixtures use
        # bare ids. Indexing a pair as a dict raised, so the inference could
        # not read a real governed run's trace at all.
        if isinstance(item, (list, tuple)):
            item = item[0] if item else None
        elif isinstance(item, dict):
            item = item.get("id")
        if not isinstance(item, str) or not item.strip():
            # A privacy-minimized lens identity is not evidence that the
            # route was a strict catalog subset. Treat any unreadable entry
            # as an instrument gap instead of synthesizing "routed" from an
            # apparently incomplete set.
            return None
        named.add(lens_name(item))
    if not known:
        return None
    return None if set(known) <= named else "routed"


def workspace_snapshot(ws: str) -> dict:
    """Stable file identity immediately before the measured model phase."""
    out = {}
    for dirpath, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in _RUNTIME_DIRS]
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            out[_rel(ws, path)] = (stat.st_mtime_ns, stat.st_size)
    return out


def first_write(ws: str, contract, since, initial_files=None) -> "dict | None":
    """When the run first CHANGED the workspace.

    The engine traces no such event. The contract's write-allow globs say
    where a run is permitted to write, and the earliest mtime under them at
    or after the run started is when it first did. Files older than the run
    are excluded: a glob that also matches reviewed source would otherwise
    date this event to whenever the fixture was checked out, i.e. before the
    contract existed, and every ordering row resting on it would pass for
    free.
    """
    globs = [g for g in ((contract or {}).get("write_allow") or [])
             if isinstance(g, str) and g]
    candidates, source = [], "the contract's write-allow globs"
    for pattern in globs:
        candidates += [p for p in glob.glob(os.path.join(ws, pattern),
                                            recursive=True)
                       if os.path.isfile(p)]
    if not candidates:
        source = "every file under the workspace, the contract naming none"
        for dirpath, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in _RUNTIME_DIRS]
            candidates += [os.path.join(dirpath, f) for f in files]
    best = None
    for path in candidates:
        try:
            stat = os.stat(path)
            when = float(stat.st_mtime)
        except OSError:
            continue
        rel = _rel(ws, path)
        if initial_files is not None and initial_files.get(rel) == (
                stat.st_mtime_ns, stat.st_size):
            continue
        if since is not None and when < float(since):
            continue
        if best is None or when < best[0]:
            best = (when, path)
    if best is None:
        return None
    return {"ts": best[0], "path": _rel(ws, best[1]), "source": source}


def synthesize_trace(rows, *, known_lenses=(), contract=None,
                     write=None, started_at=None, loop_state=None,
                     review_states=()) -> list:
    """The engine's trace plus what it does not emit, ordered.

    Both additions are conditional on ABSENCE. `contract_activated` is traced
    by `taskplane_lite.activate` today; synthesizing a second copy would
    double-count the event the rubric's own reference row selects on.
    """
    states_by_run = {
        state.get("run_id"): state for state in review_states
        if isinstance(state, dict) and state.get("run_id")
    }
    out = []
    for row in rows:
        row = dict(row)
        restored = []
        if row.get("event") == "dor" and "ready" not in row:
            _restore_closed_bool(row, "ready")
            if "ready" in row:
                restored.append("ready")
        if row.get("event") == "review_kernel_started":
            state = states_by_run.get(row.get("run_id")) or {}
            target = state.get("target") or {}
            envelope = state.get("envelope") or {}
            if isinstance(target.get("head"), str):
                row["target_head"] = target["head"]
                restored.append("target_head")
            if isinstance(envelope.get("fingerprint"), str):
                row["context_fingerprint"] = envelope["fingerprint"]
                restored.append("context_fingerprint")
            if "dispositions_complete" not in row:
                _restore_closed_bool(row, "dispositions_complete")
                if "dispositions_complete" in row:
                    restored.append("dispositions_complete")
        if restored:
            row["recorder_restored_fields"] = restored
            row["recorder_restore_source"] = (
                "review kernel state and closed audit booleans")
        if row.get("event") == "lens_route" and not row.get("breadth"):
            breadth = breadth_of(row, known_lenses)
            if breadth:
                # WHICH of the two answered matters to whoever reads the
                # verdict: a value the router wrote down and a value deduced
                # from the catalog do not carry the same weight, and a reader
                # who cannot tell them apart trusts both equally.
                row["breadth"] = breadth
                row["breadth_source"] = (
                    "recorded by the route itself (requested_breadth)"
                    if row.get("requested_breadth") else
                    "derived from the routed set against the lens catalog")
                row["synthesized_fields"] = ["breadth"]
        out.append(row)
    have = {r.get("event") for r in out}
    if started_at is not None and "evaluation_started" not in have:
        out.append({"event": "evaluation_started", "ts": float(started_at),
                    "synthesized": True, "source": "recorder boundary"})
    if "dor" not in have:
        ready = next((r for r in out if r.get("event") == "loop_step"
                      and r.get("dor_ready") is not None), None)
        if ready is None:
            ready = next((r for r in out
                          if r.get("event") == "review_kernel_started"), None)
        if ready is not None:
            out.append({"event": "dor", "ts": float(ready.get("ts", 0)),
                        "ready": (ready.get("dor_ready") is True
                                  if "dor_ready" in ready else
                                  ready.get("graph_quality_status") == "complete"),
                        "synthesized": True,
                        "source": ready.get("event")})
    if "dod" not in have:
        collected = next((r for r in out
                          if r.get("event") == "review_kernel_collected"), None)
        if collected is not None:
            out.append({"event": "dod", "ts": float(collected.get("ts", 0)),
                        "passed": True, "synthesized": True,
                        "source": "review_kernel_collected"})
    if isinstance(loop_state, dict) and \
            loop_state.get("step") in loop_mod.HUMAN_STEPS:
        out.append({"event": "human_gate_wait", "ts": time.time(),
                    "step": loop_state.get("step"), "synthesized": True,
                    "source": "frozen loop state"})
    have = {r.get("event") for r in out}
    if write and "workspace_write" not in have:
        out.append({"event": "workspace_write", "ts": float(write["ts"]),
                    "path": write["path"], "synthesized": True,
                    "source": write["source"]})
    if isinstance(contract, dict) and "contract_activated" not in have:
        when = contract.get("activated_at")
        if isinstance(when, (int, float)) and not isinstance(when, bool):
            out.append({"event": "contract_activated", "ts": float(when),
                        "task_id": contract.get("task_id"),
                        "read_only": bool(contract.get("read_only")),
                        "write_allow": contract.get("write_allow"),
                        "synthesized": True,
                        "source": "the active contract's own activated_at"})
    out.sort(key=_ts)
    return out


def synthesize_context(ws: str, *, trace_rows, fallback_ts) -> list:
    """`context.jsonl` — what the run PUT ON DISK for sharing.

    The only record no engine writes, and the only one that can answer "the
    diff was derived once and every lens read that one copy", because that is
    a fact about artifacts rather than about events.

    Nothing here invents a row. In particular the `target` row comes from the
    run's own pin and is ABSENT when the run never pinned one: manufacturing
    it from the fixture's head would fabricate the exact evidence the first
    rubric row exists to demand.
    """
    out = []
    pinned = target_mod.load(ws)
    if isinstance(pinned, dict) and pinned.get("head"):
        out.append({
            "kind": "target",
            "path": pinned.get("root") or ws,
            "head": pinned.get("head"),
            "base": pinned.get("base"),
            "origin": pinned.get("origin"),
            "fingerprint": pinned.get("fingerprint"),
            "ts": _mtime(target_mod.record_path(ws), fallback_ts),
            "synthesized": True,
        })
    emitted_context_paths = set()
    for row in trace_rows:
        if row.get("event") != "review_context_written":
            continue
        digests = row.get("sha256") if isinstance(row.get("sha256"), dict) \
            else {}
        when = row.get(eval_scenario.ORDER_KEY)
        declared = row.get("paths")
        paths = ([rel for rel in declared if isinstance(rel, str) and rel]
                 if isinstance(declared, list) else [])
        if not paths:
            paths = [_rel(ws, path) for path in sorted(glob.glob(
                os.path.join(ws, ".em-review", "context", "**", "*"),
                recursive=True)) if os.path.isfile(path)]
        for rel in paths:
            if rel in emitted_context_paths:
                continue
            emitted_context_paths.add(rel)
            out.append({
                "kind": "context_file",
                "path": rel,
                "sha256": digests.get(rel) or _sha256(os.path.join(ws, rel)),
                "bytes": (os.path.getsize(os.path.join(ws, rel))
                          if os.path.isfile(os.path.join(ws, rel)) else None),
                "status": row.get("status"),
                "ts": float(when) if isinstance(when, (int, float))
                else float(fallback_ts),
                "synthesized": True,
            })
    findings = os.path.join(ws, ".em-review", "findings.json")
    if os.path.isfile(findings):
        out.append({"kind": "findings", "path": _rel(ws, findings),
                    "sha256": _sha256(findings),
                    "ts": _mtime(findings, fallback_ts), "synthesized": True})
    for path in sorted(glob.glob(os.path.join(ws, ".em-review", "lens-*",
                                              "findings.json"))):
        out.append({"kind": "lens_findings",
                    "lens": lens_name(os.path.basename(os.path.dirname(path))),
                    "path": _rel(ws, path), "sha256": _sha256(path),
                    "ts": _mtime(path, fallback_ts), "synthesized": True})
    # ReviewKernel v2 replaces the legacy `.em-review/context/` and
    # `lens-*/findings.json` layout with one immutable envelope and leased
    # slot results.  Freeze those native artifacts directly instead of
    # grading the current workflow against files it intentionally removed.
    have_target = any(r.get("kind") == "target" for r in out)
    for state in _review_kernel_states(ws, trace_rows=trace_rows):
        target = state.get("target") or {}
        if not have_target and target.get("head"):
            out.append({"kind": "target", "path": target.get("root") or ws,
                        "head": target.get("head"), "base": target.get("base"),
                        "origin": target.get("origin"),
                        "fingerprint": target.get("fingerprint"),
                        "ts": float(fallback_ts), "synthesized": True,
                        "source": "review kernel state"})
            have_target = True
        envelope = state.get("envelope") or {}
        rel = envelope.get("relative_path")
        if rel:
            out.append({"kind": "review_envelope", "path": rel,
                        "fingerprint": envelope.get("fingerprint"),
                        "sha256": envelope.get("digest"),
                        "bytes": envelope.get("bytes"),
                        "run_id": state.get("run_id"),
                        "ts": _mtime(os.path.join(ws, rel), fallback_ts),
                        "synthesized": True})
        for slot in state.get("slots") or ():
            result = slot.get("result_path")
            if not result:
                continue
            path = os.path.join(ws, result)
            if not os.path.isfile(path):
                continue
            out.append({"kind": "slot_result", "path": result,
                        "slot_id": slot.get("slot_id"),
                        "lens_ids": list(slot.get("lens_ids") or ()),
                        "sha256": _sha256(path), "ts": _mtime(path, fallback_ts),
                        "synthesized": True})
    out.sort(key=_ts)
    return out


def _review_kernel_states(ws: str, *, trace_rows=()) -> list:
    """The canonical run named by this trace, never accumulated run history."""
    traced = [str(row.get("run_id")) for row in trace_rows
              if isinstance(row, dict)
              and row.get("event") == "review_kernel_started"
              and row.get("run_id")]
    run_id = traced[-1] if traced else None
    if not run_id:
        try:
            index = _read_json(os.path.join(ws, ".em-review", "kernel-v2",
                                            "active.json"))
        except (OSError, ValueError):
            index = {}
        if isinstance(index, dict):
            run_id = index.get("latest")
    pattern = os.path.join(ws, ".em-review", "kernel-v2", "runs",
                           str(run_id) if run_id else "*", "state.json")
    rows = []
    for path in sorted(glob.glob(pattern)):
        try:
            row = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def primary_context_path(trace_rows) -> "str | None":
    """The shared context path a brief cites instead of carrying the payload.

    `review.write_context` writes the diff first, and the diff is the thing an
    agent re-derives when it is not told the copy is already there.
    """
    for row in reversed(list(trace_rows)):
        if row.get("event") == "review_context_written" \
                and row.get("status") == "written":
            declared = row.get("paths")
            paths = ([p for p in declared if isinstance(p, str) and p]
                     if isinstance(declared, list) else [])
            if paths:
                return paths[0]
    return None


def _queue(ws: str, name: str) -> list:
    rows = []
    try:
        value = _read_json(os.path.join(tp.tp_dir(ws), name))
    except (OSError, ValueError):
        return rows
    if isinstance(value, list):
        rows = [r for r in value if isinstance(r, dict)]
    return rows


def synthesize_briefs(ws: str, *, trace_rows, context_path) -> list:
    """One row per composed brief — `dispatch.json` records counts, not rows.

    The expected-dispatch queue IS the set of briefs the engine composed, so
    it is the primary source. A subagent the engine never expected is added
    from the trace rather than dropped: an unexpected dispatch is a fact
    about the run, and a recorder that silently omits it grades a session
    that did not happen.
    """
    rows = {}
    planned_task_names = set()
    for state in _review_kernel_states(ws, trace_rows=trace_rows):
        envelope = state.get("envelope") or {}
        for slot in state.get("slots") or ():
            slot_id = str(slot.get("slot_id") or "")
            if not slot_id:
                continue
            brief = slot.get("brief") or {}
            brief_body = {}
            brief_path = os.path.join(ws, brief.get("relative_path", ""))
            try:
                brief_body = _read_json(brief_path)
            except (OSError, TypeError, ValueError):
                pass
            role = ((brief_body or {}).get("role") or {}
                    if isinstance(brief_body, dict) else {})
            task_name = role.get("task_name") or slot_id
            planned_task_names.add(task_name)
            rows["slot:" + slot_id] = {
                "lens": slot_id, "slot_id": slot_id,
                "lens_ids": list(slot.get("lens_ids") or ()),
                "context_path": envelope.get("relative_path"),
                "context_fingerprint": envelope.get("fingerprint"),
                "ts": _mtime(os.path.join(ws, brief.get("relative_path", "")),
                             0.0),
                "kind": "review-kernel-slot",
                "task_name": task_name, "agent": role.get("agent"),
                "model": role.get("model"),
                "reasoning_effort": role.get("reasoning_effort"),
                "source": "review kernel state"}
    for entry in _queue(ws, "expected_dispatch.json"):
        lens = brief_lens(entry)
        if not lens or lens in rows:
            continue
        when = entry.get("ts")
        rows[lens] = {"lens": lens, "context_path": context_path,
                      "ts": float(when) if isinstance(when, (int, float))
                      else 0.0,
                      "kind": entry.get("kind"),
                      "task_name": entry.get("task_name"),
                      "source": "expected_dispatch"}
    for row in trace_rows:
        if row.get("event") != "subagent_start":
            continue
        if row.get("task_name") in planned_task_names:
            continue
        lens = lens_name(row.get("agent_type") or "")
        # Audit identities are pseudonymized.  They remain visible in the
        # trace and dispatch counts, but an opaque pseudonym is not a lens
        # identity and must not be promoted into a scorer brief row.
        if lens.startswith("anon:"):
            continue
        if not lens or lens in rows:
            continue
        when = row.get(eval_scenario.ORDER_KEY)
        rows[lens] = {"lens": lens, "context_path": context_path,
                      "ts": float(when) if isinstance(when, (int, float))
                      else 0.0,
                      "kind": "subagent", "task_name": row.get("agent_id"),
                      "source": "trace subagent_start"}
    return sorted(rows.values(), key=lambda r: (r["ts"], r["lens"]))


# ============================================================ the run itself

class RunContext(object):
    """What the driver is handed. A model gets a checkout and its identity —
    never the recorder, which it must not be able to steer."""

    __slots__ = ("root", "ws", "dest", "env", "base", "head", "branch",
                 "origin", "skill", "run_id", "mode", "started_at", "probe",
                 "model", "reasoning_effort")

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))

    def __repr__(self):                                # pragma: no cover
        return f"<RunContext {self.skill}/{self.run_id} at {self.ws}>"


def _eligibility(mode: str, hook_active: bool) -> tuple:
    """(eligible, reason). Written into the record, never left to prose."""
    if mode != "out-of-band":
        return False, (f"recorded in-session (mode: {mode}) — an in-session "
                       f"run shares a transcript, a budget and a host with "
                       f"the session that spawned it, so it is informational "
                       f"forever and may never set or satisfy a baseline")
    if not hook_active:
        return False, ("the dispatch hook observed nothing (hook_active "
                       "false), so this run's fan-out is UNKNOWN rather than "
                       "zero — a baseline taken here would pin a number "
                       "nobody measured")
    return True, "a clean out-of-band run, observed by the dispatch hook"


def _cost(transcript, provider=None) -> dict:
    if not transcript:
        return {"available": False, "reason": "no transcript path given",
                "effective": None}
    got = (spend.read_provider_transcript(transcript, provider=provider)
           if provider else spend.read_transcript(transcript))
    result = {"available": bool(got.get("available")),
              "reason": got.get("reason"),
              "effective": got.get("effective") if got.get("available") else None,
              "messages": got.get("messages")}
    for key in ("schema", "provider", "uncached_input_tokens",
                "cached_input_tokens", "cache_creation_tokens",
                "output_tokens", "raw_total_tokens", "effective_tokens",
                "duplicates_removed"):
        if key in got:
            result[key] = got[key]
    return result


def _nonnegative(value, default=0):
    return (value if isinstance(value, int) and not isinstance(value, bool)
            and value >= 0 else default)


def _driver_artifacts(out_dir: str, result) -> dict:
    """Store bounded host output once and return digest references only."""
    if not isinstance(result, dict):
        return {}
    clean = {k: v for k, v in result.items()
             if k not in ("stdout", "stderr", "native_trace",
                          "native_derivations")}
    refs = {}
    for name in ("stdout", "stderr"):
        value = result.get(name)
        if value in (None, "", b""):
            continue
        body = value if isinstance(value, bytes) else str(value).encode(
            "utf-8", "replace")
        rel = f"driver.{name}.txt"
        path = os.path.join(out_dir, rel)
        os.makedirs(out_dir, exist_ok=True)
        with io.open(path, "xb") as f:
            f.write(body)
        refs[name] = {"path": rel, "sha256": hashlib.sha256(body).hexdigest(),
                      "bytes": len(body)}
    if refs:
        clean["artifacts"] = refs
    return clean


def _lifecycle_record(*, run_id: str, host: str, driver_result,
                      model, reasoning_effort, cost: dict) -> dict:
    """Project only bounded machine-owned driver fields into lifecycle v1."""
    result = driver_result if isinstance(driver_result, dict) else {}
    native_starts = [row for row in result.get("native_trace") or []
                     if isinstance(row, dict)
                     and row.get("event") == "subagent_start"
                     and row.get("host_observed") is True]
    receipt = native_starts[-1] if native_starts else {}
    output_contract = (result.get("output_contract")
                       if isinstance(result.get("output_contract"), dict)
                       else {})
    validation = (result.get("output_validation")
                  if isinstance(result.get("output_validation"), dict)
                  else {})
    terminal = str(result.get("status") or "unavailable")
    if terminal == "capability_unavailable":
        terminal = "unavailable"
    if terminal not in {"success", "failed", "timeout", "cancelled",
                        "unavailable"}:
        terminal = "failed"
    attempts = result.get("attempts")
    if not isinstance(attempts, list):
        attempts = ([{"attempt": 1, "status": terminal,
                      "duration_ms": result.get("duration_ms") or 0}]
                    if result else [])
    capability_source = result.get("capability_source")
    if isinstance(capability_source, list):
        capability_source = ",".join(str(item) for item in capability_source)
    capability_source = str(capability_source or
                            result.get("telemetry_method") or "unavailable")
    lifecycle = runtime_eval.build_evaluation_lifecycle(
        run_id=run_id, host=host, host_version=result.get("host_version"),
        capability_source=capability_source,
        transport=str(result.get("transport") or "unavailable"),
        schema_transport=str(output_contract.get("transport")
                             or result.get("schema_transport")
                             or "unavailable"),
        schema_fallback_reason=(output_contract.get("fallback_reason")
                                or result.get("schema_fallback_reason")),
        task=result.get("task"), slot=result.get("slot"),
        lease=result.get("lease"), planned_model=model,
        planned_effort=reasoning_effort,
        observed_model=receipt.get("model") or result.get("observed_model"),
        observed_effort=receipt.get("reasoning_effort")
        or result.get("observed_reasoning_effort"),
        attempts=attempts, duration_ms=result.get("duration_ms") or 0,
        terminal_status=terminal,
        validation_status=str(validation.get("status") or
                              result.get("validation_status") or "unavailable"),
        telemetry={"available": bool(cost.get("available")),
                   "reason": cost.get("reason")},
        diagnostics=([{"code": terminal,
                       "message": result.get("reason")}]
                     if result.get("reason") else []))
    errors = runtime_eval.validate_evaluation_lifecycle(lifecycle)
    if errors:
        raise RecorderError("evaluation lifecycle is invalid: " +
                            "; ".join(errors))
    return lifecycle


def merge_native_dispatch_report(report: dict, expected: list,
                                 trace_rows: list) -> dict:
    """Overlay Codex-host session evidence without forging hook queue rows."""
    report = dict(report or {})
    native_starts = [row for row in trace_rows
                     if row.get("event") == "subagent_start"
                     and row.get("source") == "codex_session_store"
                     and row.get("host_observed") is True]
    if not native_starts:
        return report
    expected_by_name = {row.get("task_name"): row for row in expected
                        if row.get("task_name")}
    covered = set()
    native_mismatches = []
    for row in native_starts:
        planned = expected_by_name.get(row.get("task_name"))
        model_ok = bool(planned) and (
            planned.get("model") in (None, row.get("model")))
        effort_ok = bool(planned) and (
            planned.get("reasoning_effort") in
            (None, row.get("reasoning_effort")))
        if planned and model_ok and effort_ok:
            covered.add(planned.get("task_name"))
        else:
            native_mismatches.append({
                "agent": row.get("task_name"),
                "model": row.get("model"),
                "reasoning_effort": row.get("reasoning_effort"),
                "ok": False, "source": "codex_session_store",
                "reason": "unexpected native task or routing mismatch",
            })
    already_matched = {row.get("task_name") for row in expected
                       if row.get("matched")}
    report["observed"] = int(report.get("observed") or 0) + len(
        {row.get("task_name") for row in native_starts} - already_matched)
    report["expected"] = max(int(report.get("expected") or 0),
                             len(expected_by_name))
    report["unobserved"] = sum(
        not row.get("matched") and row.get("task_name") not in covered
        for row in expected)
    report["mismatches"] = list(report.get("mismatches") or []) + \
        native_mismatches
    report["hook_active"] = True
    report["observation_source"] = "codex_session_store"
    report["native_observed"] = len(native_starts)
    report["note"] = None
    return report


def merge_native_derivations(ledger: list, native: list) -> list:
    """Take the maximum observed count per safe derivation signature.

    Hook and native-session evidence may describe the same command. Using a
    maximum rather than concatenating prevents one real ReviewKernel call
    from looking like a repeated derivation, while two native calls still
    remain two and fail the efficiency rule.
    """
    from collections import Counter

    def signature(row):
        if not isinstance(row, dict):
            return None
        if row.get("event") == "command":
            return ("command", row.get("verb"))
        if row.get("event") == "derived" and not row.get("probe"):
            return ("derived", row.get("key"), row.get("input_key"))
        return None

    merged = list(ledger or [])
    have = Counter(sig for row in merged if (sig := signature(row)))
    seen = Counter()
    for row in native or []:
        sig = signature(row)
        if sig is None:
            continue
        seen[sig] += 1
        if seen[sig] > have[sig]:
            merged.append(dict(row))
    return sorted(merged, key=lambda row: float(row.get("ts") or 0.0))


def _efficiency(*, ledger, obligation_rows, report, context_rows,
                driver_result, cost) -> dict:
    supplied = ((driver_result or {}).get("efficiency") or {}
                if isinstance(driver_result, dict) else {})
    measured = derivation.metrics(rows=ledger)
    context_bytes = sum(_nonnegative(r.get("bytes")) for r in context_rows)
    observed_rows = [r for r in obligation_rows
                     if isinstance(r, dict) and r.get("event") == "observed"]
    issued_html = {r.get("fingerprint") for r in obligation_rows
                   if isinstance(r, dict) and r.get("event") == "issued"
                   and (r.get("kind") in ("render_dashboard", "render_graph")
                        or str(r.get("artifact") or "").lower().endswith(".html"))
                   and r.get("fingerprint")}
    seen_fingerprints = set()
    duplicate_bytes = duplicate_html = 0
    for row in observed_rows:
        fp = row.get("fingerprint")
        if fp and fp in seen_fingerprints:
            duplicate_bytes += _nonnegative(row.get("bytes"))
            if fp in issued_html:
                duplicate_html += 1
        if fp:
            seen_fingerprints.add(fp)
    def observed(name, fallback):
        return supplied[name] if name in supplied else fallback
    counters = {
        "cli_count": observed("cli_count", measured["cli_count"]),
        "emitted_bytes": observed("emitted_bytes", measured["emitted_bytes"]),
        "repeated_derivation_bytes": observed(
            "repeated_derivation_bytes", measured["repeated_derivation_bytes"]),
        "dispatched_agent_count": observed(
            "dispatched_agent_count", _nonnegative((report or {}).get("observed"))),
        "prompt_view_bytes": observed("prompt_view_bytes", context_bytes),
        # These describe host rendering/re-emission and cannot be inferred
        # honestly from an artifact merely existing on disk.
        "artifact_render_bytes": observed(
            "artifact_render_bytes",
            sum(_nonnegative(r.get("bytes")) for r in observed_rows)),
        "duplicate_artifact_bytes": observed(
            "duplicate_artifact_bytes", duplicate_bytes),
        "duplicate_html_emissions": observed(
            "duplicate_html_emissions", duplicate_html),
        "effective_tokens": (supplied.get("effective_tokens")
                             if supplied.get("effective_tokens") is not None
                             else cost.get("effective")),
    }
    counters["derivation_bytes_observed"] = \
        measured["derivation_bytes_observed"]
    return counters


def _command_efficiency(*, driver_result, cost) -> dict:
    """Freeze canonical runtime counters without retaining model content.

    Provider totals are a fallback denominator, not an additional counter;
    this prevents a host transcript and its driver projection being counted
    twice.  The polling attribution and baseline must remain runtime-owned.
    """
    result = driver_result if isinstance(driver_result, dict) else {}
    supplied = result.get("command_efficiency")
    supplied = dict(supplied) if isinstance(supplied, dict) else {}
    if supplied.get("total_raw_tokens") is None and cost.get("available"):
        supplied["total_raw_tokens"] = cost.get("raw_total_tokens")
    return spend.command_efficiency(supplied)


def _plugin_version(root: str) -> "str | None":
    for rel in ((".codex-plugin", "plugin.json"),
                (".claude-plugin", "plugin.json")):
        try:
            value = _read_json(os.path.join(root, *rel))
            if value.get("version"):
                return str(value["version"])
        except (OSError, ValueError, AttributeError):
            continue
    return None


def _fingerprint(root: str, skill: str) -> "str | None":
    """The flow extract of the skill this run is graded against.

    A recorded run goes stale when the skill it graded CHANGES, and this is
    what detects it — a fingerprint of the flow the source files mandate, not
    of their bytes, so a typo does not fire a gate people would learn to
    waive.
    """
    path = eval_scenario.discover(root).get(skill)
    if not path:
        return None
    try:
        scenario = eval_scenario.load(path)
    except (OSError, ValueError):
        return None
    return eval_scenario.fingerprint(root, scenario.get("source_files") or ())


def freeze(*, out_dir: str, ws: str, root: str, skill: str, run_id: str,
           mode: str, fixture: dict, probe, started_at: float,
           transcript=None, schema: str = RUN_SCHEMA, driver_result=None,
           model=None, reasoning_effort=None, initial_files=None) -> dict:
    """Write the record the scorer reads, under the names it reads them by."""
    trace_rows = []
    for path in tp.trace_paths(ws):
        trace_rows += _read_jsonl(path)
    native_trace = ((driver_result or {}).get("native_trace") or []
                    if isinstance(driver_result, dict) else [])
    expected = _queue(ws, "expected_dispatch.json")
    expected_by_name = {row.get("task_name"): row for row in expected
                        if row.get("task_name")}
    for raw in native_trace:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        planned = expected_by_name.get(row.get("task_name"))
        if planned:
            row["agent_type"] = planned.get("agent") or row.get("agent_type")
            row["dispatch_ref"] = planned.get("ref")
            row["expected_model"] = planned.get("model")
            row["expected_reasoning_effort"] = planned.get(
                "reasoning_effort")
        trace_rows.append(row)
    contract = tp.load_active(ws)
    loop_state = loop_mod.load(ws)
    review_states = _review_kernel_states(ws, trace_rows=trace_rows)
    trace_rows = synthesize_trace(
        trace_rows, known_lenses=catalog_ids(root), contract=contract,
        write=first_write(ws, contract, started_at, initial_files),
        started_at=started_at,
        loop_state=loop_state, review_states=review_states)
    context_rows = synthesize_context(ws, trace_rows=trace_rows,
                                      fallback_ts=started_at)
    brief_rows = synthesize_briefs(
        ws, trace_rows=trace_rows,
        context_path=(primary_context_path(trace_rows) or
                      (".em-review/context/diff.patch" if os.path.isfile(
                          os.path.join(ws, ".em-review", "context",
                                       "diff.patch")) else None)))
    planned_dispatches = expected + [
        row for row in brief_rows if row.get("kind") == "review-kernel-slot"]
    report = tp.dispatch_report(ws)
    report = merge_native_dispatch_report(
        report, planned_dispatches, trace_rows)
    report[eval_rubric.DISPATCH_ROWS] = brief_rows

    files = eval_rubric.RECORD_FILES
    _write_jsonl(os.path.join(out_dir, files["trace"]), trace_rows)
    obligation_rows = obligations.read(ws)
    _write_jsonl(os.path.join(out_dir, files["obligations"]), obligation_rows)
    ledger_rows = merge_native_derivations(
        derivation.read(ws),
        ((driver_result or {}).get("native_derivations") or []
         if isinstance(driver_result, dict) else []))
    _write_jsonl(os.path.join(out_dir, files["derivations"]), ledger_rows)
    _write_jsonl(os.path.join(out_dir, files["context"]), context_rows)
    _write_json(os.path.join(out_dir, files["dispatch"]), report)

    hook_active = bool(report.get("hook_active"))
    eligible, why = _eligibility(mode, hook_active)
    host = ((driver_result or {}).get("host")
            if isinstance(driver_result, dict) else None) or tp.host()
    cost = _cost(transcript, provider=host)
    proof = eval_drivers.hook_proof(trace_rows)
    efficiency = _efficiency(ledger=ledger_rows,
                             obligation_rows=obligation_rows, report=report,
                             context_rows=context_rows,
                             driver_result=driver_result, cost=cost)
    command_efficiency = _command_efficiency(
        driver_result=driver_result, cost=cost)
    driver_record = _driver_artifacts(out_dir, driver_result)
    fixture_key = hashlib.sha256(eval_drivers.canonical_bytes({
        "branch": fixture.get("branch"), "shas": fixture.get("shas") or [],
        "base": fixture.get("base"), "head": fixture.get("head"),
    })).hexdigest()
    run = {
        "schema": schema,
        "skill": skill,
        "run_id": run_id,
        "mode": mode,
        "host": host,
        "recorded_at": float(started_at),
        "frozen_at": time.time(),
        "hook_active": hook_active,
        "baseline_eligible": eligible,
        "baseline_reason": why,
        "target_head": fixture.get("head"),
        "target_base": fixture.get("base"),
        "fixture": {"path": tp.to_posix(FIXTURE_DIRNAME),
                    "branch": fixture.get("branch"),
                    "shas": list(fixture.get("shas") or ())},
        "inputs_fingerprint": _fingerprint(root, skill),
        "probe": probe,
        "effective_tokens": cost.get("effective"),
        "cost": cost,
    }
    if schema == RUN_SCHEMA_V2:
        telemetry_method = ((driver_result or {}).get("telemetry_method")
                            if isinstance(driver_result, dict) else None)
        if not telemetry_method:
            telemetry_method = "transcript" if cost.get("available") else "unavailable"
        run.update({
            "driver": driver_record or {"status": "failed",
                                          "reason": "driver returned no record"},
            "hook_proof": proof,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "taskplane_version": _plugin_version(root),
            "efficiency": efficiency,
            "command_efficiency": command_efficiency,
            "evaluation_lifecycle": _lifecycle_record(
                run_id=run_id, host=host, driver_result=driver_result,
                model=model, reasoning_effort=reasoning_effort, cost=cost),
            "comparison_key": {
                "scenario": skill, "fixture": fixture_key,
                "start_sha": fixture.get("base"),
                "evaluated_sha": fixture.get("head"),
                "taskplane_version": _plugin_version(root), "host": host,
                "model": model, "reasoning_effort": reasoning_effort,
                "telemetry_method": telemetry_method, "run_mode": mode,
            },
        })
        rec = eval_rubric.record(
            trace=trace_rows, obligations=obligation_rows,
            dispatch=report.get(eval_rubric.DISPATCH_ROWS) or [],
            derivations=ledger_rows, context=context_rows, run=run)
        scenario_path = eval_scenario.discover(root).get(skill)
        if scenario_path:
            result = eval_rubric.evaluate_run_v2(
                eval_scenario.load(scenario_path), rec)
            eligible = bool(result["eligible"])
            reasons = (result["workflow"]["failures"]
                       + result["structural_efficiency"]["failures"])
            why = ("absolute workflow and structural compliance passed"
                   if eligible else "ineligible: " + ", ".join(reasons))
            run["evaluation"] = {
                "schema": result["schema"], "eligible": result["eligible"],
                "passed": result["passed"],
                "workflow_failures": result["workflow"]["failures"],
                "structural_failures": result["structural_efficiency"]["failures"],
                "token_status": result["token_efficiency"]["status"],
                "command_efficiency_status": command_efficiency["gate"]["status"],
            }
            if command_efficiency["gate"]["status"] != "pass":
                eligible = False
                why = ("ineligible: command efficiency "
                       + command_efficiency["gate"]["status"] + ": "
                       + ", ".join(command_efficiency["gate"]["failures"]))
        else:
            eligible = False
            why = "ineligible: scenario manifest unavailable"
        run["baseline_eligible"] = eligible
        run["baseline_reason"] = why
    _write_json(os.path.join(out_dir, eval_rubric.RUN_FILE), run)
    return run


def record_run(*, root: str, dest: str, driver, skill: str = "tp-engineering",
               run_id: str | None = None, mode: str = "out-of-band",
               out_dir: str | None = None, transcript=None,
               schema: str = RUN_SCHEMA, model=None,
               reasoning_effort=None, setup=None) -> dict:
    """Record one run: build, instrument, probe, DRIVE, freeze.

    `driver` is the seam. It is called once with a `RunContext` and is the
    only step that needs a model, which is why everything around it is
    testable with a stub and why this function never runs in CI.
    """
    if mode not in MODES:
        raise RecorderError(f"unknown mode {mode!r} — a run is one of "
                            f"{', '.join(MODES)}, and the mode is what "
                            f"decides whether it may ever set a baseline")
    root = os.path.abspath(root)
    dest = os.path.abspath(dest)
    run_id = run_id or (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                        + "-" + uuid.uuid4().hex[:6])
    dirname = RUNS_V2_DIRNAME if schema == RUN_SCHEMA_V2 else RUNS_DIRNAME
    out_dir = out_dir or os.path.join(root, dirname, skill, run_id)
    os.makedirs(dest, exist_ok=True)

    ws = os.path.join(dest, "checkout")
    manifest = load_manifest(root)
    fixture = build_fixture(root, ws, manifest=manifest)
    origin = _local_origin(dest, ws, fixture["branch"], fixture["env"])

    home = os.path.join(dest, "home")
    store = os.path.join(dest, "store")
    for path in (home, store):
        os.makedirs(path, exist_ok=True)
    env = run_env(root=root, ws=ws, home=home, store=store, manifest=manifest)
    install_hooks(root, ws)

    with _applied(env):
        setup_result = (setup(root=root, ws=ws, dest=dest, env=env)
                        if setup is not None else None)
        # Evaluator staging/onboarding is outside the measured model phase.
        # Otherwise a help/status response is blamed for the harness's own
        # files and every cost/ordering timestamp starts too early.
        started_at = time.time()
        initial_files = workspace_snapshot(ws)
        probe = derivation.probe(ws)
        if not probe:
            raise InstrumentBroken(
                f"the derivation ledger at {derivation.ledger_path(ws)} "
                f"could not be written and read back, so every row it later "
                f"carries is unusable: zero repeats over a ledger nobody "
                f"could write is not a measurement. The run is refused "
                f"rather than recorded as `instrument: broken`.")
        ctx = RunContext(root=root, ws=ws, dest=dest, env=dict(env),
                         base=fixture["base"], head=fixture["head"],
                         branch=fixture["branch"], origin=origin, skill=skill,
                         run_id=run_id, mode=mode, started_at=started_at,
                         probe=probe, model=model,
                         reasoning_effort=reasoning_effort)
        driver_result = driver(ctx)
        run = freeze(out_dir=out_dir, ws=ws, root=root, skill=skill,
                     run_id=run_id, mode=mode, fixture=fixture, probe=probe,
                     started_at=started_at, transcript=transcript,
                     schema=schema, driver_result=driver_result, model=model,
                     reasoning_effort=reasoning_effort,
                     initial_files=initial_files)

    return {"path": out_dir, "run": run, "checkout": ws, "dest": dest,
            "origin": origin, "fixture": fixture, "probe": probe,
            "setup": setup_result}


def record_run_v2(*, host: str, root: str, dest: str,
                  manifest, skill: str = "tp-engineering", adapter=None,
                  timeout_s: float = 900, cancel=None, model=None,
                  reasoning_effort=None, **kw) -> dict:
    """Drive and freeze one native attempted run under the v2 schema."""
    native = adapter or eval_drivers.adapter(host)
    canonical = (manifest if isinstance(manifest, bytes)
                 else eval_drivers.canonical_bytes(manifest))

    def drive(ctx):
        return native.run(canonical, cwd=ctx.ws, timeout_s=timeout_s,
                          cancel=cancel, env=ctx.env)

    return record_run(root=root, dest=dest, driver=drive, skill=skill,
                      schema=RUN_SCHEMA_V2, model=model,
                      reasoning_effort=reasoning_effort, **kw)


# ======================================================================= CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", metavar="DIR",
                    help="build the fixture repo into DIR and print its SHAs")
    ap.add_argument("--check", action="store_true",
                    help="build the fixture into a temp dir and verify it "
                         "against manifest.json")
    ap.add_argument("--root", default=ROOT, help="repo root")
    args = ap.parse_args(argv)

    if not (args.build or args.check):
        ap.error("nothing to do: recording a run needs a MODEL and is driven "
                 "from a session, not from this CLI. Use --build/--check to "
                 "work on the fixture.")
    dest = args.build
    tmp = None
    if not dest:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tp-fixture-")
        dest = os.path.join(tmp, "checkout")
    try:
        built = build_fixture(args.root, dest)
        print(f"fixture built at {built['path']}")
        print(f"  branch {built['branch']}")
        for sha, spec in zip(built["shas"],
                             load_manifest(args.root).get("commits") or ()):
            print(f"  {sha}  {spec.get('tree')}  {spec.get('message')}")
        print(f"  base {built['base']}\n  head {built['head']}")
        print("  files changed: "
              + ", ".join(changed_files(built["path"], built["base"],
                                        built["head"])))
    except FixtureMismatch as exc:
        print(f"fixture MISMATCH: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp:
            shutil.rmtree(tmp, True)
            shutil.rmtree(dest.rstrip(os.sep) + "-home", True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
