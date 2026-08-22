"""R-0006 B1/B2 compatibility and startup-refusal regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TASKPLANE = ROOT / "taskplane"


SUPPORTED_PYTHON_MINORS = (10, 11, 12, 13)
DIRECT_STAGE_CONSUMERS = (
    "run_store", "loop", "stage_migration", "taskplane_lite",
)


def _interpreter_for_minor(minor: int) -> str | None:
    if sys.version_info[:2] == (3, minor):
        return sys.executable
    return shutil.which(f"python3.{minor}")


def test_supported_matrix_has_an_eligible_interpreter():
    assert any(_interpreter_for_minor(minor)
               for minor in SUPPORTED_PYTHON_MINORS)


def test_stage_dependency_excludes_pep701_only_regression_shape():
    body = (TASKPLANE / "stage_entities.py").read_text(encoding="utf-8")
    assert 'f"attempt-{request_fingerprint({' not in body


@pytest.mark.parametrize("minor", SUPPORTED_PYTHON_MINORS)
def test_stage_dependency_compiles_and_consumers_import_on_supported_python(
        minor):
    interpreter = _interpreter_for_minor(minor)
    if interpreter is None:
        pytest.skip(f"Python 3.{minor} is not installed on this runner")

    script = "\n".join([
        "import importlib, json, pathlib, sys",
        f"engine = pathlib.Path({str(TASKPLANE)!r})",
        "source = engine / 'stage_entities.py'",
        "compile(source.read_text(encoding='utf-8'), str(source), 'exec')",
        "sys.path.insert(0, str(engine))",
        f"modules = {DIRECT_STAGE_CONSUMERS!r}",
        "loaded = [importlib.import_module(name).__name__ for name in modules]",
        "print(json.dumps({'minor': list(sys.version_info[:2]), "
        "                  'loaded': loaded}))",
    ])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [interpreter, "-c", script], cwd=str(ROOT), env=env,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == {
        "minor": [3, minor],
        "loaded": list(DIRECT_STAGE_CONSUMERS),
    }


def _seed_governed_state(workspace: Path, state_home: Path) -> None:
    fixtures = {
        state_home / "runs" / "existing-run" / "manifest.json":
            b'{"schema":"taskplane.run/v4","status":"existing"}\n',
        state_home / "runs" / "existing-run" / "graph" / "graph.json":
            b'{"schema":"taskplane.graph/v1","nodes":["existing"]}\n',
        state_home / "runs" / "existing-run" / "state" / "control" /
        "active_contract.json":
            b'{"schema":"taskplane.contract/v1","task":"existing"}\n',
        state_home / "runs" / "existing-run" / "state" /
        "review-kernel-v2" / "runs" / "existing-review" / "state.json":
            b'{"schema":"taskplane.review-run/v2","status":"complete"}\n',
        state_home / "projects" / "existing-project" / "knowledge" /
        "index.json":
            b'{"requirements":[{"id":"R-existing",'
            b'"file":"requirements/R-existing.md"}]}\n',
        state_home / "projects" / "existing-project" / "knowledge" /
        "requirements" / "R-existing.md":
            b'# Existing requirement\n\nMust remain byte-identical.\n',
        workspace / ".taskplane" / "existing-state.json":
            b'{"schema":"taskplane.workspace-state/v1","preserve":true}\n',
    }
    for path, payload in fixtures.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    snapshot = {}
    for path in (root, *sorted(root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        details = path.lstat()
        metadata = (stat.S_IMODE(details.st_mode), details.st_mtime_ns)
        if path.is_dir():
            snapshot[relative] = ("directory", *metadata)
        elif path.is_file():
            snapshot[relative] = ("file", *metadata, path.read_bytes())
        elif path.is_symlink():
            snapshot[relative] = ("symlink", *metadata, os.readlink(path))
        else:  # pragma: no cover - fixtures create only portable file types
            snapshot[relative] = ("other", *metadata)
    return snapshot


def _governed_snapshot(workspace: Path, state_home: Path) -> dict[str, tuple]:
    return {
        f"workspace/{path}": value
        for path, value in _tree_snapshot(workspace).items()
    } | {
        f"state-home/{path}": value
        for path, value in _tree_snapshot(state_home).items()
    }


def _broken_stage_startup(tmp_path: Path, *, debug: bool) \
        -> tuple[subprocess.CompletedProcess[str], dict, dict]:
    overlay = tmp_path / "broken-dependency"
    overlay.mkdir()
    (overlay / "stage_entities.py").write_text(
        "def deliberately_unparseable(:\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_home = tmp_path / "state-home"
    _seed_governed_state(workspace, state_home)
    before = _governed_snapshot(workspace, state_home)
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
    after = _governed_snapshot(workspace, state_home)
    return result, before, after


def test_unparseable_stage_dependency_refuses_once_before_state_creation(
        tmp_path):
    result, before, after = _broken_stage_startup(
        tmp_path, debug=False)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("TaskplaneCompatibilityError") == 1
    assert "stage_entities" in result.stderr
    assert "Traceback" not in result.stderr
    assert "SyntaxError" not in result.stderr
    assert after == before


def test_unparseable_stage_dependency_keeps_debug_traceback(tmp_path):
    result, before, after = _broken_stage_startup(
        tmp_path, debug=True)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" in result.stderr
    assert "SyntaxError" in result.stderr
    assert "TaskplaneCompatibilityError" in result.stderr
    assert after == before


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
