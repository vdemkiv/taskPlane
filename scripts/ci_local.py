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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Include the real-browser dashboard suite")
    parser.add_argument("--check", choices=["tests", "quality", "package", "browser"])
    args = parser.parse_args()
    checks = {
        "tests": [[sys.executable, "-m", "pytest", "taskplane/tests", "--ignore=taskplane/tests/test_dashboard_browser.py", "-q"]],
        "quality": [[sys.executable, "-m", "ruff", "check", "taskplane", "scripts"],
                    [sys.executable, "-m", "mypy"],
                    [sys.executable, "taskplane/tp.py", "version", "--verify"]],
        "package": [[sys.executable, f"scripts/package_{host}.py"] for host in ("openai", "claude")],
        "browser": [[sys.executable, "-m", "pytest", "taskplane/tests/test_dashboard_browser.py", "-q"]],
    }
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
