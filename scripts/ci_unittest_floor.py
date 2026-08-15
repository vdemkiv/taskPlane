#!/usr/bin/env python3
"""Guard the `python -m unittest discover` CI leg against silent erosion.

    python3 scripts/ci_unittest_floor.py            # the CI invocation
    python3 scripts/ci_unittest_floor.py --json     # machine-readable

WHY THIS EXISTS (R-0011 / E1)
-----------------------------
CI runs the suite twice: pytest, and `python -m unittest discover`. The
second leg is what makes the "dual-runner store isolation" work real — but
it only proves anything for the tests that runner can COLLECT, and that
slice can shrink two different ways without anyone noticing:

  1. tests vanish from discovery — a module renamed out of the `test_*`
     pattern, a TestCase deleted, a class quietly turned into helpers. The
     leg still reports OK; it just runs less. A pinned FLOOR on the
     collected count closes this direction.

  2. new PYTEST-ONLY files accumulate — modules with bare `def test_*`
     functions and no `unittest.TestCase` subclass. `unittest discover`
     imports them and collects nothing. Each new one moves coverage of the
     second runner down without changing the count of what already exists.
     A pinned MANIFEST of exactly which files are legitimately pytest-only
     closes this direction: a new one fails the leg until it is either
     converted or added to the manifest deliberately.

The recorded design decision (R-0011 row 1) is FLOOR + MANIFEST, convert
NOTHING: the pytest-only files below use pytest fixtures/parametrize
idiomatically and rewriting them would be churn on green code for a
counting convenience.

RATCHET DIRECTION
-----------------
FLOOR only rises. PYTEST_ONLY_MANIFEST is an explicit equality snapshot:
adding a pytest-only module requires a deliberate update here and in the
self-test, while converting one requires its removal from both.

This script COUNTS COLLECTION; it does not run the tests — the ci.yml
`unittest discover` step still does that, unchanged. Discovery is done in a
CHILD interpreter so importing 60+ test modules (each with import-time
temp-store side effects) can never contaminate the caller.
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import subprocess
import sys

# Console codepages are not always UTF-8 (Windows defaults to cp1252, a C
# locale gives ASCII), and this script's own output carries arrows and em
# dashes. The text is ours and it is UTF-8; say so rather than dying in the
# middle of a report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The landing value pinned by t9 from the REAL tree (design-time estimate was
# 995; the tree had grown past it long before this landed, so the estimate is
# NOT what is pinned here). Re-derive after adding tests with:
#     python3 scripts/ci_unittest_floor.py --json
# and raise this to the reported `count`.
FLOOR = 2508

# Every test_*.py that defines ZERO unittest.TestCase subclasses. This is an
# EQUALITY check, not a floor: every pytest-only file must be named, and every
# named file must still be pytest-only. Changes are deliberate and reviewed.
PYTEST_ONLY_MANIFEST = (
    "test_dispatch_parity.py",
    "test_evaluation_output_contract.py",
    "test_host_capabilities.py",
    "test_regression_dod.py",
    "test_regression_gate.py",
    "test_review_convergence.py",
    "test_review_discipline.py",
    "test_review_wave.py",
    "test_stage_waves.py",
    "test_v231_ci.py",
    "test_v231_cli.py",
    "test_v231_dispatch.py",
    "test_v231_guardrails.py",
    "test_workflow_review_kernel_parity.py",
)

TESTS_REL = os.path.join("taskplane", "tests")

# Runs INSIDE the target tree (cwd = repo root) so `-t .` discovery matches
# exactly what ci.yml's `python -m unittest discover -s taskplane/tests -t .`
# does. Emits {count, broken} as JSON on stdout.
_COUNT_PROG = r"""
import json, sys, unittest
sys.path.insert(0, ".")
loader = unittest.defaultTestLoader
suite = loader.discover(sys.argv[1], top_level_dir=".")
count = 0
broken = []
stack = [suite]
while stack:
    node = stack.pop()
    for child in node:
        if isinstance(child, unittest.TestSuite):
            stack.append(child)
        else:
            count += 1
            # unittest turns an unimportable module into a _FailedTest
            # that still COUNTS toward the total; the floor alone would not
            # see it. Report them so the caller can fail loudly.
            # (ASCII-only on purpose: this program is passed as an argv
            # string, and argv is encoded with the FILESYSTEM encoding, so a
            # non-ASCII byte here makes the spawn itself fail on a host
            # whose locale is not UTF-8.)
            if type(child).__name__ in ("_FailedTest", "ModuleImportFailure"):
                broken.append(str(child))
for err in getattr(loader, "errors", []) or []:
    broken.append(str(err).strip().splitlines()[0])
print(json.dumps({"count": count, "broken": broken}))
"""


def discover_count(root: str, tests_rel: str = TESTS_REL) -> tuple[int, list]:
    """(collected test count, broken-module descriptions) for `root`."""
    # Explicit `encoding`: `text=True` decodes with the locale's preferred
    # encoding, which is ascii on a bare CI runner. A collection error
    # naming a non-ASCII path would crash the counter instead of reporting.
    proc = subprocess.run([sys.executable, "-c", _COUNT_PROG, tests_rel],
                          capture_output=True, text=True, cwd=root,
                          encoding="utf-8", errors="replace")
    line = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not line:
        raise SystemExit(
            "unittest discovery failed to run in "
            f"{root} (exit {proc.returncode}):\n{proc.stderr.strip()}")
    return_doc = json.loads(line[-1])
    return return_doc["count"], return_doc["broken"]


def _derives_from_testcase(tree: ast.Module) -> bool:
    """True if the module defines any unittest.TestCase subclass, following
    in-file inheritance chains (a shared local base class counts)."""
    bases: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases[node.name] = [ast.unparse(b) for b in node.bases]
    seen: set[str] = set()

    def resolves(name: str) -> bool:
        if name in seen:
            return False
        seen.add(name)
        for b in bases.get(name, []):
            if b.split("[")[0].split(".")[-1] == "TestCase":
                return True
            if resolves(b.split("[")[0].split(".")[-1]):
                return True
        return False

    return any(resolves(cls) for cls in bases)


def pytest_only_files(root: str, tests_rel: str = TESTS_REL) -> list[str]:
    """Basenames of test_*.py files unittest discovery collects NOTHING
    from (no unittest.TestCase subclass anywhere in the module)."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, tests_rel, "test_*.py"))):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:                      # pragma: no cover
            raise SystemExit(f"{path}: cannot parse ({exc})") from None
        if not _derives_from_testcase(tree):
            out.append(os.path.basename(path))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ROOT,
                    help="repo root to check (default: this repo). Used by "
                         "test_ci_floor.py to drive synthetic trees.")
    ap.add_argument("--tests-dir", default=TESTS_REL,
                    help="tests package, relative to --root")
    ap.add_argument("--floor", type=int, default=None,
                    help="override the pinned floor (self-tests only)")
    ap.add_argument("--manifest", default=None,
                    help="override the pinned pytest-only manifest as a "
                         "comma-separated list (self-tests only); pass an "
                         "empty string for 'no file may be pytest-only'")
    ap.add_argument("--json", action="store_true",
                    help="emit the report as JSON instead of prose")
    args = ap.parse_args(argv)

    floor = FLOOR if args.floor is None else args.floor
    if args.manifest is None:
        manifest = list(PYTEST_ONLY_MANIFEST)
    else:
        manifest = [x.strip() for x in args.manifest.split(",") if x.strip()]
    manifest = sorted(manifest)

    count, broken = discover_count(args.root, args.tests_dir)
    found = pytest_only_files(args.root, args.tests_dir)

    problems = []
    if broken:
        problems.append(
            "unittest discovery produced import-failure placeholders (they "
            "COUNT toward the total but run nothing): " + "; ".join(broken))
    if count < floor:
        problems.append(
            f"unittest discovery collected {count} tests, below the pinned "
            f"floor of {floor}: {floor - count} test(s) left the "
            "discover leg. Restore them, or — if the removal is deliberate "
            "and approved — lower FLOOR in scripts/ci_unittest_floor.py AND "
            "the ratchet literal in taskplane/tests/test_ci_floor.py.")
    added = sorted(set(found) - set(manifest))
    gone = sorted(set(manifest) - set(found))
    if added:
        problems.append(
            "new pytest-only test file(s) not in PYTEST_ONLY_MANIFEST: "
            f"{added}. `unittest discover` collects NOTHING from these, so "
            "each one shrinks the second runner's coverage. Give the file a "
            "unittest.TestCase, or add it to the manifest deliberately.")
    if gone:
        problems.append(
            f"stale PYTEST_ONLY_MANIFEST entry/entries: {gone} — these now "
            "define unittest.TestCase subclasses (or no longer exist). "
            "Remove the stale entries from the explicit manifest.")

    ok = not problems
    if args.json:
        print(json.dumps({
            "ok": ok, "count": count, "floor": floor,
            "pytest_only": found, "manifest": manifest,
            "broken": broken, "problems": problems,
        }, indent=2))
    else:
        print(f"unittest discover: {count} tests collected "
              f"(pinned floor {floor}; headroom {count - floor})")
        print(f"pytest-only files: {len(found)} "
              f"(pinned manifest {len(manifest)})")
        for p in problems:
            print("FAIL: " + p, file=sys.stderr)
        if ok:
            print("ok: discover-leg floor and pytest-only manifest hold")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
