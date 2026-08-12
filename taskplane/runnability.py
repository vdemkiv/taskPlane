"""Can the build and the tests actually RUN here? Probe once, tell everyone.

Found by the karpenter field report (aws/karpenter-provider-aws#9464): six
lens agents were dispatched in parallel and SIX of them independently spent
actions discovering that `go test ./...` could not run in that sandbox. Each
one tried it, read the failure, reasoned about whether the failure was the
PR's fault, and only then fell back to reading code — six times, from a cold
start, for one fact that was true of the environment and not of the diff.

That is the shape of waste this module removes. Runnability is a property of
the CHECKOUT, not of the lens: it is knowable once, cheaply, before any agent
is dispatched, and it belongs in every brief as a stated fact.

Three design rules, each of which a previous attempt got wrong:

  1. PROBE, DO NOT RUN. The probe must never be the test suite. Every probe
     here is bounded (a timeout, a cheap subcommand) and answers only "would
     the real command get off the ground", never "does it pass".
  2. CACHE PER TREE STATE, NOT PER SESSION. The verdict is keyed by the
     manifests that decide it plus the PATH that resolves the toolchain, so
     `go install`-ing the missing toolchain mid-review re-probes, while six
     agents in the same second share one answer.
  3. STATE IT, DO NOT ENFORCE IT. Nothing here blocks anything. A lens that
     needs a dynamic check it cannot run says so in its finding; it does not
     get denied, and no gate consults this file. Enforcement lives in tp.py
     and the hooks (see the deletability contract) — never here, and never
     in loop.py.
"""
import hashlib
import json
import os
import shutil
import subprocess

# Verdicts. "runs" is the only optimistic one and it is deliberately weak:
# the probe proves the command starts, not that the suite is green.
RUNS = "runs"
UNAVAILABLE = "unavailable"     # the toolchain itself is not on PATH
BROKEN = "broken"               # toolchain present, probe failed (deps, etc.)
UNKNOWN = "unknown"             # probe timed out or could not be attempted

CACHE_NAME = "runnability.json"
DEFAULT_TIMEOUT = 25

# Ordered so the summary line names the repo's primary toolchain first.
SPECS = (
    {"id": "go", "markers": ("go.mod",), "tool": "go",
     "test_cmd": "go test ./...",
     "probe": ("go", "list", "./..."),
     # `go list` resolves the module graph exactly like `go test` does, so a
     # sandbox with no module cache and no network fails HERE, in seconds,
     # instead of failing once per agent halfway through a review.
     },
    {"id": "node", "markers": ("package.json",), "tool": "node",
     "test_cmd": "npm test",
     "probe": ("node", "--version"),
     "needs_dir": "node_modules",
     "needs_dir_reason": "dependencies are not installed (no `node_modules`)"},
    {"id": "python", "markers": ("pyproject.toml", "setup.py", "tox.ini",
                                 "pytest.ini"),
     "tool": "python3", "test_cmd": "pytest",
     "probe": ("python3", "-c", "import pytest")},
    {"id": "rust", "markers": ("Cargo.toml",), "tool": "cargo",
     "test_cmd": "cargo test",
     "probe": ("cargo", "metadata", "--no-deps", "--offline",
               "--format-version", "1")},
    {"id": "maven", "markers": ("pom.xml",), "tool": "mvn",
     "test_cmd": "mvn test", "probe": ("mvn", "--version")},
    {"id": "gradle", "markers": ("build.gradle", "build.gradle.kts"),
     "tool": "gradle", "test_cmd": "gradle test",
     "probe": ("gradle", "--version")},
)

_BY_ID = {s["id"]: s for s in SPECS}


def enabled() -> bool:
    """`TASKPLANE_RUNNABILITY=off` skips the probe entirely (air-gapped hosts,
    or anyone who would rather pay the six-agent tax than a subprocess)."""
    return (os.environ.get("TASKPLANE_RUNNABILITY", "") or "").strip().lower() \
        not in ("off", "0", "false", "no")


def detect(root: str) -> list:
    """Which toolchains this checkout declares, in SPECS order."""
    out = []
    for spec in SPECS:
        for m in spec["markers"]:
            if os.path.exists(os.path.join(root, m)):
                out.append(spec["id"])
                break
    return out


def _markers_present(root: str, spec) -> list:
    return [m for m in spec["markers"]
            if os.path.exists(os.path.join(root, m))]


def fingerprint(root: str) -> str:
    """What the verdict actually depends on: which manifests exist and their
    size/mtime, plus the PATH that resolves the toolchains. Installing a
    toolchain or editing a manifest invalidates; six agents in one wave do
    not."""
    h = hashlib.sha1()
    for spec in SPECS:
        for m in spec["markers"]:
            p = os.path.join(root, m)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{m}:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
    h.update(("PATH=" + (os.environ.get("PATH") or "")).encode("utf-8"))
    return h.hexdigest()[:16]


def _probe_one(root: str, spec, timeout: int) -> dict:
    tool = spec["tool"]
    entry = {"id": spec["id"], "tool": tool, "command": spec["test_cmd"],
             "markers": _markers_present(root, spec)}
    if shutil.which(tool) is None:
        entry["verdict"] = UNAVAILABLE
        entry["detail"] = f"`{tool}` is not on PATH"
        return entry
    need = spec.get("needs_dir")
    if need and not os.path.isdir(os.path.join(root, need)):
        entry["verdict"] = BROKEN
        entry["detail"] = spec.get("needs_dir_reason",
                                   f"`{need}` is missing")
        return entry
    try:
        # encoding= is mandatory here: text=True alone decodes with the
        # ambient locale, which is ascii on a bare CI runner (v2.9.0 CI red).
        proc = subprocess.run(list(spec["probe"]), cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        entry["verdict"] = UNKNOWN
        entry["detail"] = (f"probe `{' '.join(spec['probe'])}` did not finish "
                           f"in {timeout}s")
        return entry
    except OSError as e:
        entry["verdict"] = UNKNOWN
        entry["detail"] = f"probe could not start ({e.__class__.__name__})"
        return entry
    if proc.returncode == 0:
        entry["verdict"] = RUNS
        entry["detail"] = f"`{' '.join(spec['probe'])}` succeeded"
        return entry
    entry["verdict"] = BROKEN
    entry["detail"] = _first_useful_line(proc.stdout) or (
        f"`{' '.join(spec['probe'])}` exited {proc.returncode}")
    return entry


def _first_useful_line(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line[:220]
    return ""


def probe(root: str, *, timeout: int = DEFAULT_TIMEOUT,
          only: list | None = None) -> dict:
    """Probe every declared toolchain. Never raises — a probe that cannot be
    performed is UNKNOWN, and an unknown verdict is still worth stating."""
    ids = [i for i in detect(root) if not only or i in only]
    checks = [_probe_one(root, _BY_ID[i], timeout) for i in ids]
    return {"fingerprint": fingerprint(root), "checks": checks,
            "summary": summary(checks)}


def summary(checks: list) -> str:
    """One line, safe to put in a headline or in `meta.tests`."""
    if not checks:
        return "no build/test toolchain detected"
    bad = [c for c in checks if c.get("verdict") != RUNS]
    if not bad:
        return " · ".join(f"{c['command']} runs" for c in checks)
    first = bad[0]
    line = f"{first['command']} could not run — {first.get('detail', '')}"
    if len(bad) > 1:
        line += f" (+{len(bad) - 1} more toolchain"
        line += "s)" if len(bad) > 2 else ")"
    return line.strip().rstrip("—").strip()


def can_run_tests(result: dict) -> bool:
    checks = (result or {}).get("checks") or []
    return bool(checks) and all(c.get("verdict") == RUNS for c in checks)


# ------------------------------------------------------------------ cache

def _cache_path(workspace: str) -> str:
    import taskplane_lite as tp
    return os.path.join(tp.tp_dir(workspace), CACHE_NAME)


def cached(workspace: str, root: str | None = None) -> dict | None:
    """The stored verdict IF it still describes this tree, else None."""
    root = root or workspace
    try:
        with open(_cache_path(workspace), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint(root):
        return None
    return data


def store(workspace: str, result: dict) -> dict:
    path = _cache_path(workspace)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    except OSError:
        pass          # a cache that cannot be written must not fail a review
    return result


def probe_once(workspace: str, root: str | None = None, *,
               timeout: int = DEFAULT_TIMEOUT, refresh: bool = False) -> dict:
    """The entry point every caller should use: probe at most once per tree
    state per checkout. This is the whole point of the module — six lens
    agents dispatched in the same wave share ONE answer."""
    root = root or workspace
    if not enabled():
        return {"fingerprint": fingerprint(root), "checks": [],
                "skipped": "TASKPLANE_RUNNABILITY=off",
                "summary": "runnability probe disabled"}
    if not refresh:
        hit = cached(workspace, root)
        if hit is not None:
            hit["cached"] = True
            return hit
    return store(workspace, probe(root, timeout=timeout))


# -------------------------------------------------------------- the notice

def brief_note(result: dict) -> str:
    """What every dispatched agent is told, so none of them re-derives it."""
    if not result or result.get("skipped"):
        return ""
    checks = result.get("checks") or []
    if not checks:
        return ""
    lines = ["\nBUILD/TEST RUNNABILITY (probed ONCE for this review — do NOT "
             "re-probe, and do not spend actions rediscovering it):"]
    for c in checks:
        v = c.get("verdict")
        mark = {RUNS: "CAN RUN", UNAVAILABLE: "CANNOT RUN",
                BROKEN: "CANNOT RUN", UNKNOWN: "UNPROVEN"}.get(v, "UNPROVEN")
        lines.append(f"  {c.get('command')} — {mark}: {c.get('detail', '')}")
    if not can_run_tests(result):
        lines.append(
            "  => Base your verdict on the code and the diff. If a finding "
            "genuinely needs a dynamic check this environment cannot perform, "
            "say so in that finding's `scenario` — do not retry the command.")
    return "\n".join(lines) + "\n"
