from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import yaml

from taskplane import ci_policy


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "ci_local_r0002_journey", ROOT / "scripts" / "ci_local.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_r0002_direct_ci_journey_reduces_thirteen_pytest_processes_to_three():
    runner = _runner()
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request", ref="482", run_id="9001",
    )
    cells = runtime["plan"]["cells"]
    commands = {
        cell["id"]: runner._ci_cell_commands(cell, Path("/owned"))
        for cell in cells
    }
    pytest_cells = [
        cell_id for cell_id, rows in commands.items()
        if any(row[1:3] == ["-m", "pytest"] for row in rows)
    ]

    assert pytest_cells == [
        "pytest-1", "dashboard-browser", "os-portability-windows",
    ]
    assert len(pytest_cells) == 3
    assert len([cell for cell in cells if cell["id"] == "pytest-1"]) == 1
    core_argv = commands["pytest-1"][0]
    assert all(f"--deselect={selector}" in core_argv
               for selector in runner.CI_WINDOWS_SELECTORS)
    assert commands["os-portability-windows"][0][-1] == \
        runner.CI_WINDOWS_SELECTORS[0]
    for cell_id in (
        "quality-package", "interpreter-import-3.10",
        "interpreter-import-3.11", "interpreter-import-3.13",
        "security-no-egress",
    ):
        assert all(row[1:3] != ["-m", "pytest"] for row in commands[cell_id])


def test_r0002_every_direct_cell_is_settings_candidate_and_cleanup_bound():
    runner = _runner()
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="push", ref="refs/heads/main", run_id="9002",
    )
    assert runtime["settings_receipt"]["precedence"] == ["defaults", "file"]
    assert runtime["plan"]["settings_digest"] == \
        runtime["settings_receipt"]["settings_digest"]
    for cell in runtime["plan"]["cells"]:
        assert cell["candidate_fingerprint"] == runtime["candidate"]["fingerprint"]
        assert cell["source_sha"] == runtime["candidate"]["source_sha"]
        assert cell["cleanup"]["registered_before_run"] is True
        assert set(cell["cleanup"]["outcomes"]) == set(
            runner.CI_TERMINAL_OUTCOMES)
    assert runtime["plan"]["cancellation"] == {
        "group": "protected-main-9002",
        "cancel_in_progress": False,
        "scope": "never",
    }


def test_r0002_runtime_and_workflow_consume_one_canonical_policy_result(
    monkeypatch,
):
    runner = _runner()
    canonical = runner.build_ci_plan
    calls = []

    def observed(candidate, declaration):
        calls.append((candidate, declaration))
        return canonical(candidate, declaration)

    monkeypatch.setattr(runner, "build_ci_plan", observed)
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request", ref="482", run_id="9001",
    )
    assert len(calls) == 1
    assert runtime["plan"] == ci_policy.build_ci_plan(*calls[0])
    assert runtime["plan"]["validation_domains"] == [
        "tests", "quality-package", "browser", "interpreter-import",
        "os-portability", "security-no-egress",
    ]
    assert "matrices" not in runtime["plan"]

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"))
    invoked = set()
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            command = step.get("run")
            if isinstance(command, str):
                invoked.update(re.findall(r"--ci-cell\s+([^\s]+)", command))
    invoked.remove("interpreter-import-${{")
    invoked.update({
        "interpreter-import-3.10", "interpreter-import-3.11",
        "interpreter-import-3.13",
    })
    assert invoked == {cell["id"] for cell in runtime["plan"]["cells"]}


def test_r0002_credential_empty_guard_executes_one_no_egress_corpus(
    tmp_path, monkeypatch,
):
    runner = _runner()
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    assert runner._internal("zero-token-corpus") == 0
