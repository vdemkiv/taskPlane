"""R-0006 B1/B2 compatibility and startup-refusal regressions."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TASKPLANE = ROOT / "taskplane"


@pytest.mark.parametrize("feature_version", [(3, 10), (3, 11), (3, 12)])
def test_stage_dependency_parses_on_every_supported_python(feature_version):
    source = TASKPLANE / "stage_entities.py"
    body = source.read_text(encoding="utf-8")

    # The exact B1 regression is accepted by PEP 701 parsers even when
    # ``feature_version`` requests an older grammar. Keep the source-shape
    # assertion beside the matrix parse so a newer test runner cannot mask it.
    assert 'f"attempt-{request_fingerprint({' not in body
    ast.parse(body, filename=str(source), feature_version=feature_version)


def _broken_stage_startup(tmp_path: Path, *, debug: bool) \
        -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    overlay = tmp_path / "broken-dependency"
    overlay.mkdir()
    (overlay / "stage_entities.py").write_text(
        "def deliberately_unparseable(:\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_home = tmp_path / "state-home"
    script = "\n".join([
        "import os, sys",
        f"engine = {str(TASKPLANE)!r}",
        f"overlay = {str(overlay)!r}",
        "sys.path.insert(0, engine)",
        "import tp",
        "import run_store",
        "sys.modules.pop('stage_entities', None)",
        "sys.path[:] = [p for p in sys.path if p != engine]",
        "sys.path.insert(0, overlay)",
        f"raise SystemExit(tp.main(['summary', '--workspace', "
        f"{str(workspace)!r}]))",
    ])
    env = {
        **os.environ,
        "TASKPLANE_HOME": str(state_home),
    }
    if debug:
        env["TASKPLANE_DEBUG"] = "1"
    else:
        env.pop("TASKPLANE_DEBUG", None)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(tmp_path), env=env,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    return result, workspace, state_home


def test_unparseable_stage_dependency_refuses_once_before_state_creation(
        tmp_path):
    result, workspace, state_home = _broken_stage_startup(
        tmp_path, debug=False)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("TaskplaneCompatibilityError") == 1
    assert "stage_entities" in result.stderr
    assert "Traceback" not in result.stderr
    assert "SyntaxError" not in result.stderr
    assert list(workspace.iterdir()) == []
    assert not state_home.exists()


def test_unparseable_stage_dependency_keeps_debug_traceback(tmp_path):
    result, workspace, state_home = _broken_stage_startup(
        tmp_path, debug=True)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" in result.stderr
    assert "SyntaxError" in result.stderr
    assert "TaskplaneCompatibilityError" in result.stderr
    assert list(workspace.iterdir()) == []
    assert not state_home.exists()


def test_valid_stage_dependency_allows_cli_startup(tmp_path):
    state_home = tmp_path / "state-home"
    env = {**os.environ, "TASKPLANE_HOME": str(state_home)}
    env.pop("TASKPLANE_DEBUG", None)

    result = subprocess.run(
        [sys.executable, str(TASKPLANE / "tp.py"), "version"],
        cwd=str(tmp_path), env=env, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(
        encoding="utf-8"))["version"] in result.stdout
    assert not state_home.exists()
