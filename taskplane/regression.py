"""Graph-scoped regression gate (v2.3.1).

The DoD gate historically ran a test command and blocked on non-zero exit.
That is not a regression gate: a green suite says nothing about whether THIS
change broke a behavior that used to work, and it says nothing at all about
behaviors the suite never covered. This module adds the missing check, scoped
to the change's blast radius via the dependency graph, run at DoD time.

Two tiers (see docs/regression-gate-design.md):
  Tier 1  covered regressions  — a radius test green at the baseline and
                                 failing now. Objective; blocks.
  Tier 2  coverage-gap guard   — a changed enforcement/public entry point with
                                 no covering test in the radius. The class of
                                 regression a test-diff CANNOT catch (it has no
                                 test). Refuses a clean "verified", not the work.

The pure decision logic (`classify`, `radius_tests`, `coverage_gaps`) is
separated from the impure shell (`run_pytest`, `regression_scan`) so the gate
is itself testable without spawning subprocesses — the discipline it enforces.
"""
from __future__ import annotations

import os
import re
import subprocess

# Enforcement / public entry points: a change here that ships with no covering
# test is the coverage-gap the v2.3.0 wave fell through (broken CI invocation,
# tp.py self-blocked, contract-slot unwired). Path-suffix / basename patterns.
ENFORCEMENT_PATHS = (
    "taskplane/taskplane_lite.py",   # screen_*, dod/dor, contracts
    "taskplane/loop.py",             # gates, transitions
    "taskplane/tp.py",               # the CLI surface
    "hooks/hooks.json",              # the PreToolUse wall
    ".github/workflows/ci.yml",      # the documented invocation
)

# Names whose change most needs a covering behavioral test.
ENFORCEMENT_SYMBOLS = (
    "screen_command", "screen_tool", "dod_check", "dor_check",
    "build_contract", "load_active", "budget_status", "gate",
)

def _imported_modules(src: str, mods: set) -> set:
    """Source-module basenames a test file imports. Handles the real forms:
        from taskplane import loop, kb      -> loop, kb
        from taskplane.loop import x         -> loop
        from loop import x                   -> loop   (bare, sys.path insert)
        import taskplane.loop                -> loop
        import loop                          -> loop
    Only names that are actual source modules count."""
    found = set()
    for line in src.splitlines():
        s = line.strip()
        m = re.match(r"from\s+([\w.]+)\s+import\s+(.+)", s)
        if m:
            pkg, names = m.group(1), m.group(2)
            if pkg in ("taskplane",):
                # `from taskplane import loop, kb` — the names are the modules
                for n in re.split(r"[,\s]+", names.split("#")[0]):
                    n = n.strip().rstrip("()")
                    if n in mods:
                        found.add(n)
            else:
                # `from taskplane.loop import x` / `from loop import x`
                tail = pkg.split(".")[-1]
                if tail in mods:
                    found.add(tail)
            continue
        m = re.match(r"import\s+(.+)", s)
        if m:
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                tail = name.split(".")[-1]
                if tail in mods:
                    found.add(tail)
    return found


def source_modules(ws: str) -> set:
    """Basenames (no .py) of the package's source modules — the import targets
    a test file can name."""
    d = os.path.join(ws, "taskplane")
    out = set()
    try:
        for f in os.listdir(d):
            if f.endswith(".py") and f != "__init__.py":
                out.add(f[:-3])
    except OSError:
        pass
    return out


def test_import_index(ws: str) -> dict:
    """{test_file (repo-relative): set(source module basenames it imports)}.

    A test 'covers' a source module if it imports it — the cheapest honest
    proxy for 'exercises' without coverage instrumentation."""
    mods = source_modules(ws)
    tdir = os.path.join(ws, "taskplane", "tests")
    index: dict = {}
    try:
        names = sorted(os.listdir(tdir))
    except OSError:
        return index
    for f in names:
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        rel = os.path.join("taskplane", "tests", f)
        try:
            with open(os.path.join(tdir, f), encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        index[rel] = _imported_modules(src, mods)
    return index


def _changed_modules(changed_files) -> set:
    """Source-module basenames among the changed files."""
    out = set()
    for f in changed_files:
        f = f.replace("\\", "/")
        if f.startswith("taskplane/") and f.endswith(".py") \
                and "/tests/" not in f:
            out.add(os.path.basename(f)[:-3])
    return out


def radius_tests(ws: str, changed_files, graph_impacted=None) -> tuple:
    """(set(test_file), degraded). The test subset covering the changed
    modules plus any graph-impacted modules. degraded=True (→ caller runs the
    full suite and says so) when a changed source module has NO covering test,
    or when nothing maps — never silently narrow."""
    index = test_import_index(ws)
    changed = _changed_modules(changed_files)
    target = set(changed)
    for g in (graph_impacted or []):
        # graph node ids are directory/module/file-ish; take the trailing
        # name and drop a .py suffix so 'taskplane/dashboard.py',
        # 'taskplane/dashboard' and 'dashboard' all resolve to 'dashboard'.
        name = os.path.basename(str(g).rstrip("/"))
        if name.endswith(".py"):
            name = name[:-3]
        target.add(name)
    if not target:
        return set(), False  # no source modules changed → nothing to diff
    selected = {t for t, mods in index.items() if mods & target}
    covered = set()
    for mods in index.values():
        covered |= mods
    uncovered = changed - covered
    degraded = bool(uncovered) or not selected
    return selected, degraded


def _test_files(ws: str) -> list:
    """All test files, repo-relative — for name-reference coverage of configs
    that no test can `import`."""
    tdir = os.path.join(ws, "taskplane", "tests")
    out = []
    try:
        for f in sorted(os.listdir(tdir)):
            if f.startswith("test_") and f.endswith(".py"):
                out.append(os.path.join("taskplane", "tests", f))
    except OSError:
        pass
    return out


def coverage_gaps(changed_files, radius, ws: str | None = None) -> list:
    """Changed enforcement/public entry points with no covering test — the
    Tier-2 regressions a test-diff can't see. A `.py` module is covered when a
    radius test imports it; a CONFIG file (ci.yml, hooks.json) is covered when
    ANY test references it by name (a lint-test naming the path exercises its
    invariants), since no test can import a YAML/JSON file."""
    gaps = []
    ws = ws or "."
    config_refs = None
    for f in changed_files:
        f = f.replace("\\", "/")
        if not any(f == p or f.endswith("/" + p) for p in ENFORCEMENT_PATHS):
            continue
        base = os.path.basename(f)
        mod = base[:-3] if base.endswith(".py") else None
        covered = False
        if mod:
            idx = test_import_index_cache.get("idx")
            if idx is not None:
                covered = any(mod in idx.get(t, set()) for t in radius)
        else:
            # config file: covered iff some test names the path/basename
            if config_refs is None:
                config_refs = ""
                for t in _test_files(ws):
                    try:
                        with open(os.path.join(ws, t), encoding="utf-8") as fh:
                            config_refs += fh.read()
                    except OSError:
                        pass
            covered = (f in config_refs) or (base in config_refs)
        if not covered:
            gaps.append(f)
    return gaps


# small cache so coverage_gaps can consult the import index without re-scanning
test_import_index_cache: dict = {}


def classify(baseline_fail: set, current_fail: set) -> dict:
    """Pure diff of two failing-test sets. A test failing NOW but not at the
    baseline is a regression; failing in both is pre-existing; failing at
    baseline but not now was fixed by the change."""
    baseline_fail = set(baseline_fail or ())
    current_fail = set(current_fail or ())
    return {
        "regressions": sorted(current_fail - baseline_fail),
        "pre_existing": sorted(current_fail & baseline_fail),
        "fixed": sorted(baseline_fail - current_fail),
    }


_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def run_pytest(ws: str, test_files) -> set:
    """Run the given test files, return the set of failing node ids. Empty set
    means all selected tests passed (or none were selected)."""
    files = list(test_files or ())
    if not files:
        return set()
    cmd = ["python3", "-m", "pytest", *files,
           "-q", "--no-header", "--tb=no", "-rfE", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True)
    return set(_FAILED_RE.findall(proc.stdout + proc.stderr))


def _baseline_failures(ws: str, base_ref: str, radius) -> set | None:
    """Failing node ids when the radius tests run at `base_ref`, via a
    throwaway detached worktree so the working tree is never touched. Returns
    None if the baseline cannot be built (Tier 1 then degrades to skipped, and
    the caller says so) — the gate must never fail merely because it couldn't
    establish a baseline."""
    import tempfile
    import shutil
    # Only files that exist at base_ref can be a baseline — a brand-new test
    # file (the change's own) has no baseline and is handled by normal DoD.
    tmp = tempfile.mkdtemp(prefix="tp-regbase-")
    wt = os.path.join(tmp, "wt")
    try:
        r = subprocess.run(["git", "worktree", "add", "--detach", wt, base_ref],
                           cwd=ws, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        present = [t for t in radius if os.path.exists(os.path.join(wt, t))]
        if not present:
            return set()
        return run_pytest(wt, present)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt],
                       cwd=ws, capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


def dod_errors(ws: str, snapshot_ref: str | None, changed_files,
               graph_impacted=None, *, runner=run_pytest,
               baseline_failures=_baseline_failures) -> list:
    """DoD-shaped regression errors ([] = clean). Tier 2 (coverage gaps) is
    always checked; Tier 1 (baseline test-diff) runs when a baseline can be
    built from snapshot_ref. Each returned string is a gate blocker."""
    errors: list = []
    radius, degraded = radius_tests(ws, changed_files, graph_impacted)
    test_import_index_cache["idx"] = test_import_index(ws)

    # Tier 2 — coverage-gap guard (cheap, always available)
    for gap in coverage_gaps(changed_files, radius, ws):
        errors.append(
            f"regression_coverage_gap: '{gap}' is an enforcement/public entry "
            "point changed with no test exercising it — add a covering "
            "behavioral test so a future break is detectable")

    # Tier 1 — baseline test-diff (needs a baseline)
    if radius and snapshot_ref:
        base_fail = baseline_failures(ws, snapshot_ref, radius)
        if base_fail is None:
            errors.append(
                "regression_gate: baseline could not be built from "
                f"{snapshot_ref[:12]} — Tier-1 test-diff skipped (Tier-2 "
                "coverage guard still applied)")
        else:
            now_fail = runner(ws, radius)
            diff = classify(base_fail, now_fail)
            for nid in diff["regressions"]:
                errors.append(
                    f"regression: {nid} was green at {snapshot_ref[:12]} and "
                    "fails now — this change broke a previously-passing test")
    return errors


def regression_scan(ws: str, base_ref: str | None, changed_files,
                    graph_impacted=None, *, runner=run_pytest,
                    baseline_runner=None) -> dict:
    """Full gate: select the radius, run it now and at the baseline, diff.

    `baseline_runner(test_files) -> set(failed)` lets a caller supply the
    baseline result (e.g. a throwaway worktree at base_ref, or a stored
    last-green set). When None and no base_ref, the baseline is treated as
    all-green (every current failure is then a candidate regression — the
    honest conservative default).
    """
    radius, degraded = radius_tests(ws, changed_files, graph_impacted)
    test_import_index_cache["idx"] = test_import_index(ws)
    gaps = coverage_gaps(changed_files, radius)
    current_fail = runner(ws, radius)
    if baseline_runner is not None:
        baseline_fail = baseline_runner(radius)
    else:
        baseline_fail = set()  # no baseline available → conservative
    diff = classify(baseline_fail, current_fail)
    return {
        "radius": sorted(radius),
        "degraded": degraded,
        "coverage_gaps": gaps,
        "base_ref": base_ref,
        **diff,
        "blocks": bool(diff["regressions"]) or bool(gaps),
    }
