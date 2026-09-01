from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "ci_local_value_contract", ROOT / "scripts" / "ci_local.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _runtime(runner):
    return runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request", ref="482", run_id="9001",
    )


def test_runner_plan_has_one_unsharded_suite_and_no_pytest_replays():
    runner = _runner()
    runtime = _runtime(runner)
    cells = runtime["plan"]["cells"]
    pytest_commands = [
        command
        for cell in cells
        for command in runner._ci_cell_commands(cell, Path("/owned"))
        if command[1:3] == ["-m", "pytest"]
    ]
    core = next(cell for cell in cells if cell["id"] == "pytest-1")

    assert [cell["id"] for cell in cells if cell["kind"] == "pytest"] == [
        "pytest-1",
    ]
    assert core["runtime"] == "3.12"
    assert core["selectors"] == list(runner._authoritative_pytest_files())
    assert core["excluded_selectors"] == list(runner.CI_WINDOWS_SELECTORS)
    assert len(pytest_commands) == 3  # core, real browser, native Windows only
    assert all(cell["kind"] != "pytest" for cell in cells
               if cell["id"].startswith("interpreter-import-"))
    assert not any(command[1:3] == ["-m", "pytest"] for command in
                   runner._ci_cell_commands(
                       next(cell for cell in cells
                            if cell["id"] == "quality-package"), Path("/owned")))


def test_direct_topology_is_disjoint_and_names_only_justified_serialization():
    runner = _runner()
    plan = _runtime(runner)["plan"]
    ids = [cell["id"] for cell in plan["cells"]]

    assert ids == [
        "pytest-1", "quality-package", "dashboard-browser",
        "interpreter-import-3.10", "interpreter-import-3.11",
        "interpreter-import-3.13", "os-portability-windows",
        "security-no-egress",
    ]
    assert plan["max_parallel"] == len(ids)
    assert plan["serializations"] == [{
        "name": "package-build-before-provenance",
        "cells": ["quality-package"],
        "reason": "archive validation consumes package outputs",
    }]
    browser = next(cell for cell in plan["cells"]
                   if cell["id"] == "dashboard-browser")
    assert browser["selectors"] == list(runner.CI_BROWSER_SELECTORS)
    assert len(browser["selectors"]) == 4
    assert set(browser["selectors"]).isdisjoint(
        next(cell for cell in plan["cells"]
             if cell["id"] == "pytest-1")["selectors"])


def test_failure_routing_uses_typed_evidence_and_unknown_holds():
    runner = _runner()
    runtime = _runtime(runner)
    cell = next(row for row in runtime["plan"]["cells"]
                if row["id"] == "pytest-1")
    routed = runner._typed_failure_routing(
        runtime, cell, outcome="failure", output_digest="0" * 64,
        command_receipts=[],
    )
    record = routed["records"][0]

    assert record["class"] == "unknown"
    assert record["route"] == "hold"
    assert record["evidence"]["output_digest"] == "0" * 64
    assert routed["next"] == "hold"
    assert routed["product_fix_allowed"] is False
    assert "assertion" not in record["reason"].lower()

    environment = runner._typed_failure_routing(
        runtime, cell, outcome="failure", output_digest="1" * 64,
        command_receipts=[], known_class="environment",
    )
    assert environment["records"][0]["route"] == "environment-recovery"


def test_cleanup_refuses_ambiguous_and_durable_artifact_targets(tmp_path):
    runner = _runner()
    runtime = _runtime(runner)
    target, ownership = runner._owned_cell_root(runtime, "pytest-1", tmp_path)
    target.mkdir()
    unsafe = tmp_path / "taskplane-ci-unowned"
    unsafe.mkdir()
    with pytest.raises(runner.RunnerError, match="ambiguous or unowned"):
        runner._cleanup_ci_cell_root(unsafe, ownership)
    assert unsafe.exists()

    target.rmdir()
    artifact_target, ownership = runner._owned_cell_root(
        runtime, "pytest-1", tmp_path,
    )
    artifact_target.mkdir()
    with pytest.raises(runner.RunnerError, match="durable CI artifacts"):
        runner._cleanup_ci_cell_root(
            artifact_target, ownership,
            durable_artifacts={"root": str(artifact_target)},
        )
    assert artifact_target.exists()
