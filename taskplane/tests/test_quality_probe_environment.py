"""Quality readiness must describe the environment used for evidence."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import governed_commands, review_evidence, runnability, stage_artifacts


MODULE = "taskplane_quality_probe_fixture"


@pytest.fixture
def quality_tool(monkeypatch):
    monkeypatch.setattr(runnability, "_LANGUAGE_QUALITY_CHECKS", {
        "python": ({"id": "lint", "module": MODULE,
                    "arguments": ("check",)},),
    })
    return MODULE


@pytest.mark.parametrize("source", ["pythonpath", "user-site"])
def test_ambient_only_tool_is_unavailable_to_evidence(
        tmp_path, monkeypatch, quality_tool, source):
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    if source == "pythonpath":
        module_root = tmp_path / "ambient-tools"
        monkeypatch.setenv("PYTHONPATH", str(module_root))
    else:
        user_base = tmp_path / "user-base"
        monkeypatch.setenv("PYTHONUSERBASE", str(user_base))
        site_probe = subprocess.run(
            [sys.executable, "-c",
             "import site; print(site.ENABLE_USER_SITE); "
             "print(site.getusersitepackages())"],
            cwd=workspace, capture_output=True, text=True, check=True,
            timeout=10)
        enabled, site_path = site_probe.stdout.strip().splitlines()
        if enabled != "True":
            pytest.skip("this interpreter disables user-site packages already")
        module_root = Path(site_path)
        assert module_root.is_relative_to(user_base)
    module_root.mkdir(parents=True)
    (module_root / f"{quality_tool}.py").write_text(
        "print('ambient-tool 1.0')\n", encoding="utf-8")

    ambient = subprocess.run(
        [sys.executable, "-m", quality_tool, "--version"],
        cwd=workspace, capture_output=True, text=True, timeout=10)
    assert ambient.returncode == 0
    assert ambient.stdout.strip() == "ambient-tool 1.0"

    check = runnability.probe_language_quality_toolchains(
        str(workspace), ["python"], timeout=10)[0]["checks"][0]

    assert check["verdict"] == runnability.UNAVAILABLE
    assert "No module named" in check["tool_version"]
    assert check["argv"] == [sys.executable, "-m", quality_tool, "check"]


def test_available_probe_and_evidence_use_one_environment(
        tmp_path, monkeypatch, quality_tool):
    (tmp_path / f"{quality_tool}.py").write_text(
        "import os, site, sys\n"
        "assert not site.ENABLE_USER_SITE\n"
        "assert os.environ['PYTHONPATH'] == ''\n"
        "assert 'TASKPLANE_PROBE_AMBIENT_SENTINEL' not in os.environ\n"
        "print('isolated-tool 1.0')\n",
        encoding="utf-8")
    monkeypatch.setenv("TASKPLANE_PROBE_AMBIENT_SENTINEL", "ambient")
    observed = []
    real_run = subprocess.run

    def capture_probe(argv, **kwargs):
        observed.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(runnability.subprocess, "run", capture_probe)
    check = runnability.probe_language_quality_toolchains(
        str(tmp_path), ["python"], timeout=10)[0]["checks"][0]
    assert check["verdict"] == runnability.RUNS
    assert check["tool_version"] == "isolated-tool 1.0"
    assert observed[0][0] == [sys.executable, "-m", quality_tool, "--version"]

    binding = {"task_id": "quality-task", "candidate_sha": "candidate",
               "source_tree": "tree"}
    assignment = {
        "binding": binding, "assignment_digest": "assignment",
        "implementation_files": [f"{quality_tool}.py"],
        "language_obligations": [{"required_commands": [check]}],
    }
    monkeypatch.setattr(review_evidence.ArtifactStore, "read",
                        lambda _self, _reference: {
                            "run_id": "quality-run", "assignment": assignment})
    monkeypatch.setattr(stage_artifacts, "_validate_assignment", lambda row: row)
    monkeypatch.setattr(governed_commands, "_git_output",
                        lambda _workspace, *_args: (
                            "tree" if _args[-1] == "HEAD^{tree}" else "candidate"))
    monkeypatch.setattr(governed_commands, "_regular_file_binding",
                        lambda path, **_kwargs: {"path": str(path)})
    boundary = governed_commands._evidence_command_boundary(
        str(tmp_path), {"evidence_input": {},
                        "worker_lifecycle": {"task": "quality-task"}},
        check["argv"], {"run_id": "quality-run", "task_id": "quality-task"},
        binding)

    assert observed[0][1]["env"] == boundary["runtime_environment"]
    execution = real_run(
        check["argv"], cwd=tmp_path, env=boundary["runtime_environment"],
        capture_output=True, text=True, timeout=10)
    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert execution.stdout.strip() == check["tool_version"]


@pytest.mark.parametrize("error,detail", [
    (subprocess.TimeoutExpired("quality-probe", 1), "timed out"),
    (PermissionError("cannot start"), "PermissionError"),
])
def test_unperformed_quality_probe_is_unknown(
        tmp_path, monkeypatch, quality_tool, error, detail):
    def fail_to_probe(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(runnability.subprocess, "run", fail_to_probe)
    check = runnability.probe_language_quality_toolchains(
        str(tmp_path), ["python"], timeout=1)[0]["checks"][0]
    assert check["verdict"] == runnability.UNKNOWN
    assert detail in check["tool_version"]
