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
import shlex
import subprocess
import sys
import tempfile

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

_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".tox",
    ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".fixwave", "_to_delete",
}

_SENSITIVE_ENV = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH|API_KEY|"
    r"PRIVATE_KEY|ACCESS_KEY|SESSION)", re.IGNORECASE)

_RUNNER_ENV_ALLOW = {
    "PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
    "TMPDIR", "TMP", "TEMP", "CI", "GITHUB_ACTIONS",
    "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC",
}


class RegressionDiscoveryError(RuntimeError):
    """The candidate test surface could not be bounded safely."""


def _skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith("_incoming-")


def _skipped_path(path: str) -> bool:
    """Whether a repository-relative path lives under a non-product root.

    Changed-file lists can contain deleted paths that no longer participate in
    discovery.  Apply the same boundary here so a removed transfer/scratch
    mirror cannot impersonate a real enforcement path by suffix.
    """
    parts = path.replace("\\", "/").strip("/").split("/")
    return any(_skip_dir(part) for part in parts[:-1])


def _under_roots(path: str, roots: set[str]) -> bool:
    path = path.replace("\\", "/").strip("/")
    for root in roots:
        root = root.replace("\\", "/").strip("/")
        if not root or root == "." or path == root or path.startswith(root + "/"):
            return True
    return False


def _python_test_membership(parts: list[str] | tuple[str, ...],
                            name: str) -> tuple[bool, bool]:
    """Return (collectable_test, test_support) for a Python path.

    Pytest does not collect every `.py` file below a `tests/` directory.
    Fixtures and helpers belong to the test side of the source boundary, but
    must not be passed to pytest as standalone radius targets.
    """
    in_test_tree = any(part in ("test", "tests") for part in parts)
    collectable = name.startswith("test_") or name.endswith("_test.py")
    return collectable, in_test_tree or collectable or name == "conftest.py"


def _git_candidates(ws: str) -> list[str] | None:
    """Tracked plus untracked-unignored paths, or None outside Git."""
    marker = os.path.lexists(os.path.join(ws, ".git"))
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=ws,
            capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        if marker:
            raise RegressionDiscoveryError(
                f"could not verify Git test boundary: {exc}") from exc
        return None
    if probe.returncode != 0:
        if marker:
            raise RegressionDiscoveryError(
                "Git worktree discovery failed: "
                + (probe.stderr.strip() or "git rev-parse failed"))
        return None
    if probe.stdout.strip() != "true":
        return None
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"], cwd=ws, capture_output=True,
            text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegressionDiscoveryError(
            f"Git file discovery failed: {exc}") from exc
    if proc.returncode != 0:
        raise RegressionDiscoveryError(
            "Git file discovery failed: "
            + (proc.stderr.strip() or "git ls-files failed"))
    return sorted({p for p in proc.stdout.split("\0") if p})


def _python_files(ws: str, *, tests: bool,
                  roots: set[str] | None = None) -> list[str]:
    """Repository-relative Python source or test files, for any layout.

    This deliberately does not assume `taskplane/` or `taskplane/tests/`.
    Transfer/build/cache roots are excluded even before `.gitignore` catches
    them so a local scratch copy can never widen a regression radius.
    """
    out: set[str] = set()
    root_set = {"."} if roots is None else set(roots)
    if not root_set:
        return []
    scan_roots = sorted(root_set)
    ws_real = os.path.realpath(ws)
    candidates = _git_candidates(ws)
    if candidates is not None:
        for rel in candidates:
            if not _under_roots(rel, root_set):
                continue
            parts = rel.replace("\\", "/").split("/")
            if any(_skip_dir(p) for p in parts[:-1]):
                continue
            name = parts[-1]
            if not name.endswith(".py"):
                continue
            path = os.path.join(ws, rel)
            if not os.path.lexists(path):
                continue
            if os.path.islink(path):
                raise RegressionDiscoveryError(
                    f"refusing symlinked Python file in test surface: {rel}")
            collectable, test_support = _python_test_membership(
                parts[:-1], name)
            if (tests and collectable) or (not tests and not test_support):
                out.add(rel)
        return sorted(out)
    for rel_scan in scan_roots:
        start = os.path.realpath(os.path.join(ws, rel_scan))
        if os.path.commonpath([ws_real, start]) != ws_real:
            raise RegressionDiscoveryError(
                f"test root escapes workspace: {rel_scan}")
        if os.path.islink(os.path.join(ws, rel_scan)):
            raise RegressionDiscoveryError(
                f"refusing symlinked test root: {rel_scan}")
        for root, dirs, names in os.walk(start):
            dirs[:] = sorted(d for d in dirs if not _skip_dir(d))
            for d in dirs:
                if os.path.islink(os.path.join(root, d)):
                    raise RegressionDiscoveryError(
                        "refusing symlinked directory in test surface: "
                        + os.path.relpath(os.path.join(root, d), ws_real))
            rel_root = os.path.relpath(root, ws_real)
            parts = () if rel_root == "." else tuple(rel_root.split(os.sep))
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                if os.path.islink(path):
                    raise RegressionDiscoveryError(
                        "refusing symlinked Python file in test surface: "
                        + os.path.relpath(path, ws_real))
                collectable, test_support = _python_test_membership(parts, name)
                if not ((tests and collectable)
                        or (not tests and not test_support)):
                    continue
                rel = name if rel_root == "." else os.path.join(rel_root, name)
                out.add(rel)
    return sorted(out)


_PYTHON_EXE = re.compile(
    r"^(?:python(?:\d+(?:\.\d+)*)?|pypy(?:\d+)?)(?:\.exe)?$")
_PYTHON_FLAGS = {"-B", "-E", "-I", "-O", "-OO", "-P", "-q", "-s",
                 "-S", "-u", "-v"}
_PYTEST_VALUE_OPTIONS = {
    "-c", "-k", "-m", "-o", "-p", "--basetemp", "--capture",
    "--confcutdir", "--deselect", "--ignore", "--ignore-glob",
    "--import-mode", "--junitxml", "--maxfail", "--override-ini",
    "--rootdir", "--tb",
}
_PYTEST_FLAGS = {
    "-q", "-s", "-x", "--collect-only", "--disable-warnings",
    "--exitfirst", "--ff", "--lf", "--no-header", "--no-summary",
    "--quiet", "--strict-markers",
}


def _pytest_argv(tokens: list[str]) -> list[str] | None:
    """Return pytest-owned argv only for one recognized invocation grammar."""
    if any(t and all(c in "();<>|&" for c in t) for t in tokens):
        return None
    argv = list(tokens)
    while argv and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0], re.DOTALL):
        argv.pop(0)
    if argv and os.path.basename(argv[0]) == "env":
        argv.pop(0)
        # `env` parses options first, assignments second, then the command.
        # Do not accept an option after the first assignment: real env treats
        # it as the command, so approving the later pytest would widen the
        # regression surface beyond what can actually execute.
        while argv:
            token = argv[0]
            if token == "--":
                argv.pop(0)
                break
            if token in {"-u", "--unset"}:
                if len(argv) < 2 or not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*", argv[1]):
                    return None
                del argv[:2]
                continue
            if token.startswith("-u") and token != "-u":
                name = token[2:]
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    return None
                argv.pop(0)
                continue
            if token.startswith("--unset="):
                name = token.split("=", 1)[1]
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    return None
                argv.pop(0)
                continue
            break
        while argv and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0], re.DOTALL):
            argv.pop(0)
    if len(argv) >= 2 and os.path.basename(argv[0]) in {"uv", "poetry", "pipenv"} \
            and argv[1] == "run":
        argv = argv[2:]
    if not argv:
        return None
    command = os.path.basename(argv[0])
    if command in {"pytest", "py.test"}:
        return argv[1:]
    if command in {"py", "py.exe"}:
        i = 1
        if i < len(argv) and re.fullmatch(r"-3(?:\.\d+)?", argv[i]):
            i += 1
        return argv[i + 2:] if i + 1 < len(argv) \
            and argv[i:i + 2] == ["-m", "pytest"] else None
    if not _PYTHON_EXE.fullmatch(command):
        return None
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "-m":
            return argv[i + 2:] if i + 1 < len(argv) and argv[i + 1] == "pytest" \
                else None
        if token in {"-W", "-X"}:
            i += 2
        elif token in _PYTHON_FLAGS or token.startswith(("-W", "-X")):
            i += 1
        else:
            return None
    return None


def _pytest_positionals(argv: list[str]) -> list[str] | None:
    """Conservatively parse only pytest positional test targets."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            out.extend(argv[i + 1:])
            break
        if token in _PYTEST_VALUE_OPTIONS:
            if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
                return None
            i += 2
            continue
        if token in _PYTEST_FLAGS or token.startswith(("-q", "-v", "-r")) \
                or (token.startswith("-") and "=" in token):
            i += 1
            continue
        if token.startswith("-"):
            return None
        out.append(token)
        i += 1
    return out


def approved_test_roots(ws: str, test_command: str | None) -> set[str]:
    """Test roots explicitly named by the approved command.

    A bare `pytest` command approves repository-wide discovery. When files or
    directories are named, fallback may widen only within their directories.
    """
    if test_command is None:
        return {"."}  # direct API compatibility: caller explicitly opted in
    if "\n" in test_command or "\r" in test_command:
        return set()  # shell command separators must never widen discovery
    try:
        lexer = shlex.shlex(
            test_command or "", posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        # Preserve Windows path separators. Quoted whitespace still works;
        # an unquoted POSIX `\ ` path fails closed instead of widening.
        lexer.escape = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise RegressionDiscoveryError(
            f"could not parse approved test command: {exc}") from exc
    pytest_argv = _pytest_argv(tokens)
    positionals = (_pytest_positionals(pytest_argv)
                   if pytest_argv is not None else None)
    if positionals is None:
        return set()  # non-Python command authorizes no additional Python code
    roots: set[str] = set()
    ws_real = os.path.realpath(ws)
    for token in positionals:
        raw = token.split("::", 1)[0]
        path = raw if os.path.isabs(raw) else os.path.join(ws, raw)
        if not os.path.exists(path):
            continue
        if not (raw.endswith(".py") or os.path.isdir(path)):
            continue
        real = os.path.realpath(path)
        if os.path.commonpath([ws_real, real]) != ws_real:
            raise RegressionDiscoveryError(
                f"approved test path escapes workspace: {raw}")
        rel = os.path.relpath(real, ws_real)
        roots.add(rel if os.path.isdir(real) else os.path.dirname(rel) or ".")
    return roots or ({"."} if not positionals else set())


def _module_aliases(path: str) -> set[str]:
    """Import spellings that can refer to one Python source path."""
    p = path.replace("\\", "/").strip("/")
    if not p.endswith(".py"):
        p = p.rstrip("/")
        if not p:
            return set()
        p = p + ".py"
    stem = p[:-3]
    if stem.endswith("/__init__"):
        stem = stem[:-9]
    dotted = stem.replace("/", ".").strip(".")
    if not dotted:
        return set()
    parts = dotted.split(".")
    # Full path plus every useful import suffix and basename. `src/acme/x.py`
    # may be imported as src.acme.x, acme.x, or x depending on sys.path.
    return {".".join(parts[i:]) for i in range(len(parts))}


def _imported_modules(src: str, mods: set) -> set:
    """Known source-module aliases imported by a Python test file."""
    found = set()
    for line in src.splitlines():
        s = line.strip()
        m = re.match(r"from\s+([\w.]+)\s+import\s+(.+)", s)
        if m:
            pkg, names = m.group(1), m.group(2)
            candidates = {pkg, pkg.split(".")[-1]}
            for n in re.split(r"[,\s]+", names.split("#")[0]):
                n = n.strip().rstrip("()")
                if n and n != "import":
                    candidates |= {n, f"{pkg}.{n}"}
            found |= candidates & mods
            continue
        m = re.match(r"import\s+(.+)", s)
        if m:
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                found |= {name, name.split(".")[-1]} & mods
    return found


def source_alias_owners(ws: str) -> dict[str, set[str]]:
    """Import alias -> source paths; ambiguous aliases remain explicit."""
    out: dict[str, set[str]] = {}
    for rel in _python_files(ws, tests=False):
        for alias in _module_aliases(rel):
            out.setdefault(alias, set()).add(rel)
    return out


def source_modules(ws: str) -> set:
    """All import aliases of repository Python source modules."""
    return set(source_alias_owners(ws))


def test_import_index(ws: str, test_roots: set[str] | None = None) -> dict:
    """{test_file (repo-relative): set(source module basenames it imports)}.

    A test 'covers' a source module if it imports it — the cheapest honest
    proxy for 'exercises' without coverage instrumentation."""
    owners = source_alias_owners(ws)
    # A basename such as `service` is not proof when two packages own it.
    mods = {alias for alias, paths in owners.items() if len(paths) == 1}
    index: dict = {}
    for rel in _python_files(ws, tests=True, roots=test_roots):
        try:
            with open(os.path.join(ws, rel), encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        index[rel] = _imported_modules(src, mods)
    return index


def _changed_module_groups(changed_files) -> list[set[str]]:
    """Alias set per changed Python source module, for any repo layout."""
    out: list[set[str]] = []
    for f in changed_files:
        f = f.replace("\\", "/")
        if _skipped_path(f):
            continue
        parts = f.split("/")
        name = parts[-1]
        _, test_support = _python_test_membership(parts[:-1], name)
        if f.endswith(".py") and not test_support:
            aliases = _module_aliases(f)
            if aliases:
                out.append(aliases)
    return out


def _changed_modules(changed_files) -> set:
    out: set[str] = set()
    for group in _changed_module_groups(changed_files):
        out |= group
    return out


def _graph_modules(value):
    """Yield module ids from the depth-keyed depgraph impact schema."""
    if isinstance(value, dict):
        if value.get("module"):
            yield str(value["module"])
        else:
            for child in value.values():
                yield from _graph_modules(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _graph_modules(child)
    elif isinstance(value, str) and value:
        yield value


def radius_tests(ws: str, changed_files, graph_impacted=None, *,
                 test_roots: set[str] | None = None,
                 import_index: dict | None = None) -> tuple:
    """(set(test_file), degraded). The test subset covering the changed
    modules plus any graph-impacted modules. degraded=True (→ caller runs the
    full suite and says so) when a changed source module has NO covering test,
    or when nothing maps — never silently narrow."""
    index = (import_index if import_index is not None
             else test_import_index(ws, test_roots))
    changed = _changed_modules(changed_files)
    target = set(changed)
    for g in _graph_modules(graph_impacted or []):
        value = str(g).rstrip("/")
        aliases = _module_aliases(value)
        if aliases:
            target |= aliases
        else:
            target.add(os.path.basename(value))
    if not target:
        return set(), False  # no source modules changed → nothing to diff
    selected = {t for t, imported in index.items() if imported & target}
    covered = set()
    for imported in index.values():
        covered |= imported
    uncovered = [group for group in _changed_module_groups(changed_files)
                 if not (group & covered)]
    degraded = bool(uncovered) or not selected
    if degraded:
        # The old implementation set degraded=True but returned only the
        # incomplete/empty selection. Widen mechanically: a sparse map may
        # cost more, but it may never silently reduce the regression surface.
        selected = set(index)
    return selected, degraded


def _test_files(ws: str, test_roots: set[str] | None = None) -> list:
    """All Python test files, repo-relative."""
    return _python_files(ws, tests=True, roots=test_roots)


def coverage_gaps(changed_files, radius, ws: str | None = None, *,
                  import_index: dict | None = None,
                  test_roots: set[str] | None = None) -> list:
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
        if _skipped_path(f):
            continue
        if not any(f == p or f.endswith("/" + p) for p in ENFORCEMENT_PATHS):
            continue
        base = os.path.basename(f)
        mod = base[:-3] if base.endswith(".py") else None
        covered = False
        if mod:
            if import_index is not None:
                aliases = _module_aliases(f)
                covered = any(aliases & import_index.get(t, set())
                              for t in radius)
        else:
            # config file: covered iff some test names the path/basename
            if config_refs is None:
                config_refs = ""
                for t in _test_files(ws, test_roots):
                    try:
                        with open(os.path.join(ws, t), encoding="utf-8") as fh:
                            config_refs += fh.read()
                    except OSError:
                        pass
            covered = (f in config_refs) or (base in config_refs)
        if not covered:
            gaps.append(f)
    return gaps


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


class RegressionRunnerError(RuntimeError):
    """The regression test runner did not produce a trustworthy verdict."""


def _runner_env(home: str) -> dict:
    """Minimal runtime environment: credential locators never cross."""
    env = {k: v for k, v in os.environ.items()
           if k in _RUNNER_ENV_ALLOW and not _SENSITIVE_ENV.search(k)}
    env["HOME"] = home
    env["TASKPLANE_HOME"] = os.path.join(home, ".taskplane")
    return env


def run_pytest(ws: str, test_files) -> set:
    """Run radius tests and return failing node ids.

    Exit 0 is green and exit 1 is a trustworthy red only when pytest names at
    least one FAILED/ERROR node. Missing pytest, collection/usage/internal
    failures, interruption, or an unparseable non-zero result are control-plane
    failures and raise instead of becoming an empty (false-green) set.
    """
    files = list(test_files or ())
    if not files:
        return set()
    ws_real = os.path.realpath(ws)
    for rel in files:
        path = os.path.join(ws, rel)
        if os.path.islink(path):
            raise RegressionRunnerError(f"refusing symlinked test file: {rel}")
        real = os.path.realpath(path)
        if os.path.commonpath([ws_real, real]) != ws_real:
            raise RegressionRunnerError(f"test file escapes workspace: {rel}")
    cmd = [sys.executable, "-m", "pytest", *files,
           "-q", "--no-header", "--tb=no", "-rfE", "-p", "no:cacheprovider"]
    try:
        with tempfile.TemporaryDirectory(prefix="tp-regenv-") as home:
            proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True,
                                  env=_runner_env(home))
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegressionRunnerError(
            f"could not start pytest with {sys.executable}: {exc}") from exc
    blob = proc.stdout + proc.stderr
    failures = set(_FAILED_RE.findall(blob))
    if proc.returncode == 0:
        return set()
    if proc.returncode == 1 and failures:
        return failures
    tail = " | ".join(blob.strip().splitlines()[-5:]) or "no runner output"
    raise RegressionRunnerError(
        f"pytest exited {proc.returncode} without a trustworthy test-failure "
        f"set: {tail}")


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
    except RegressionRunnerError:
        raise
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt],
                       cwd=ws, capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


def dod_errors(ws: str, snapshot_ref: str | None, changed_files,
               graph_impacted=None, *, runner=run_pytest,
               baseline_failures=_baseline_failures,
               test_command: str | None = None) -> list:
    """DoD-shaped regression errors ([] = clean). Tier 2 (coverage gaps) is
    always checked; Tier 1 (baseline test-diff) runs when a baseline can be
    built from snapshot_ref. Each returned string is a gate blocker."""
    errors: list = []
    roots = approved_test_roots(ws, test_command)
    index = test_import_index(ws, roots)
    radius, degraded = radius_tests(
        ws, changed_files, graph_impacted, test_roots=roots,
        import_index=index)

    # Tier 2 — coverage-gap guard (cheap, always available)
    for gap in coverage_gaps(
            changed_files, radius, ws, import_index=index, test_roots=roots):
        errors.append(
            f"regression_coverage_gap: '{gap}' is an enforcement/public entry "
            "point changed with no test exercising it — add a covering "
            "behavioral test so a future break is detectable")

    # Tier 1 — baseline test-diff (needs a baseline)
    if radius and snapshot_ref:
        try:
            base_fail = baseline_failures(ws, snapshot_ref, radius)
        except RegressionRunnerError as exc:
            errors.append(
                "regression_gate: baseline runner failed — cannot establish "
                f"a trustworthy comparison: {exc}")
            return errors
        if base_fail is None:
            errors.append(
                "regression_gate: baseline could not be built from "
                f"{snapshot_ref[:12]} — Tier-1 test-diff skipped (Tier-2 "
                "coverage guard still applied)")
        else:
            try:
                now_fail = runner(ws, radius)
            except RegressionRunnerError as exc:
                errors.append(
                    "regression_gate: current runner failed — cannot establish "
                    f"a trustworthy verdict: {exc}")
                return errors
            diff = classify(base_fail, now_fail)
            for nid in diff["regressions"]:
                errors.append(
                    f"regression: {nid} was green at {snapshot_ref[:12]} and "
                    "fails now — this change broke a previously-passing test")
    return errors


def regression_scan(ws: str, base_ref: str | None, changed_files,
                    graph_impacted=None, *, runner=run_pytest,
                    baseline_runner=None, test_command: str | None = None) -> dict:
    """Full gate: select the radius, run it now and at the baseline, diff.

    `baseline_runner(test_files) -> set(failed)` lets a caller supply the
    baseline result (e.g. a throwaway worktree at base_ref, or a stored
    last-green set). When None and no base_ref, the baseline is treated as
    all-green (every current failure is then a candidate regression — the
    honest conservative default).
    """
    roots = approved_test_roots(ws, test_command)
    index = test_import_index(ws, roots)
    radius, degraded = radius_tests(
        ws, changed_files, graph_impacted, test_roots=roots,
        import_index=index)
    gaps = coverage_gaps(
        changed_files, radius, ws, import_index=index, test_roots=roots)
    try:
        current_fail = runner(ws, radius)
    except RegressionRunnerError as exc:
        return {
            "radius": sorted(radius), "degraded": degraded,
            "coverage_gaps": gaps, "base_ref": base_ref,
            "regressions": [], "pre_existing": [], "fixed": [],
            "runner_error": str(exc), "blocks": True,
        }
    try:
        baseline_fail = (baseline_runner(radius)
                         if baseline_runner is not None else set())
    except RegressionRunnerError as exc:
        return {
            "radius": sorted(radius), "degraded": degraded,
            "coverage_gaps": gaps, "base_ref": base_ref,
            "regressions": [], "pre_existing": [], "fixed": [],
            "runner_error": "baseline: " + str(exc), "blocks": True,
        }
    diff = classify(baseline_fail, current_fail)
    return {
        "radius": sorted(radius),
        "degraded": degraded,
        "coverage_gaps": gaps,
        "base_ref": base_ref,
        **diff,
        "blocks": bool(diff["regressions"]) or bool(gaps),
    }
