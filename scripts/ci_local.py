#!/usr/bin/env python3
"""Run the same product checks used by CI, with failures visible in the log."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("all", "portability", "core", "native", "packages", "capacity")


def suite_for(nodeid: str, *, capacity: bool = False) -> str:
    """One owner for every test; adding an ordinary test keeps it in core."""
    path, _, name = nodeid.replace("\\", "/").partition("::")
    filename = path.rsplit("/", 1)[-1]
    if filename == "test_dashboard_browser.py":
        return "browser"
    if capacity:
        return "capacity"
    if filename in ("test_context.py", "test_context_delivery.py"):
        return "portability"
    if filename == "test_native_workflow_cli.py":
        return "native"
    if filename == "test_workflow_packages.py" and name.startswith("test_generated_archives_match_verified_source["):
        return "packages"
    return "core"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Include the real-browser dashboard suite")
    parser.add_argument("--check", choices=["tests", "quality", "package", "browser"])
    parser.add_argument("--suite", choices=SUITES, default="all")
    parser.add_argument("--host", choices=["codex", "claude"])
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--package-output", type=Path)
    args = parser.parse_args()
    if args.suite != "all" and args.check != "tests":
        parser.error("--suite requires --check tests")
    if args.host and (args.check != "tests" or args.suite not in ("packages", "capacity")):
        parser.error("--host requires the packages or capacity test suite")
    if args.junitxml and args.check not in ("tests", "browser"):
        parser.error("--junitxml requires --check tests or browser")
    if args.package_output and args.check not in (None, "package"):
        parser.error("--package-output requires the package check")
    checks = {
        "tests": [[sys.executable, "-m", "pytest", "taskplane/tests", "--ignore=taskplane/tests/test_dashboard_browser.py", "-q", "--durations=25", "--taskplane-suite", args.suite]],
        "quality": [[sys.executable, "-m", "ruff", "check", "taskplane", "scripts"],
                    [sys.executable, "-m", "mypy"],
                    [sys.executable, "taskplane/tp.py", "version", "--verify"]],
        "package": [[sys.executable, f"scripts/package_{host}.py"] for host in ("openai", "claude")],
        "browser": [[sys.executable, "-m", "pytest", "taskplane/tests/test_dashboard_browser.py", "-q", "--durations=25"]],
    }
    if args.host:
        checks["tests"][0] += ["--taskplane-host", args.host]
    if args.junitxml:
        checks[args.check][0] += ["--junitxml", str(args.junitxml.resolve())]
    if args.package_output:
        for command in checks["package"]:
            command += ["--output-dir", str(args.package_output.resolve())]
    selected = [args.check] if args.check else ["tests", "quality", "package"] + (["browser"] if args.browser else [])
    with tempfile.TemporaryDirectory(prefix="taskplane-ci-") as temp:
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("CODEX_", "CLAUDE_", "TASKPLANE_")) or k == "TASKPLANE_BROWSER_EXECUTABLE"}
        environment.update(CODEX_HOME=str(Path(temp) / "codex"),
                           CLAUDE_CONFIG_DIR=str(Path(temp) / "claude"), PYTHONUTF8="1")
        for name in selected:
            for command in checks[name]:
                print(f"\n{name}: {' '.join(command[1:])}", flush=True)
                result = subprocess.run(command, cwd=ROOT, env=environment)
                if result.returncode:
                    return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
