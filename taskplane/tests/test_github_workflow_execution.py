from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "ci-runtime" / "contract.json"
SPEC = importlib.util.spec_from_file_location(
    "ci_local_github_contract", ROOT / "scripts" / "ci_local.py",
)
SUPPORTED_PYTHONS = ("3.10", "3.11", "3.12", "3.13")
COMPATIBILITY_SELECTOR = (
    "taskplane/tests/test_github_workflow_execution.py::"
    "test_supported_python_matrix_compiles_imports_before_tests"
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _browser():
    return {
        "executable": "/opt/chromium/chrome",
        "version": "Chromium 131.0.0",
        "flags": ["--headless=new", "--disable-gpu"],
        "fixture_server": "1" * 64,
        "snapshot": "2" * 64,
        "dashboard_artifact": "3" * 64,
        "selectors": "4" * 64,
    }


def _compatibility_job(workflow: str) -> str:
    start = workflow.index("  python-compatibility:\n")
    end = workflow.index("\n  authoritative-tests:", start)
    return workflow[start:end]


def _compile_import_payload(workflow: str) -> str:
    start_marker = "# R-0006-COMPILE-IMPORT-BEGIN"
    end_marker = "# R-0006-COMPILE-IMPORT-END"
    start = workflow.index(start_marker) + len(start_marker)
    end = workflow.index(end_marker, start)
    return textwrap.dedent("\n".join(
        workflow[start:end].splitlines()
    )).strip()


def _copy_shipped_python_surface(destination: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "taskplane/*.py", "hooks/*.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert tracked
    for relative in tracked:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(
        ["git", "add", "taskplane", "hooks"], cwd=destination, check=True
    )


def test_supported_python_matrix_compiles_imports_before_tests(tmp_path):
    """Keep the 3.10-3.13 matrix and execute its pre-test source gate."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = _compatibility_job(workflow)
    versions = tuple(re.findall(
        r'^          - python: "(3\.\d+)"$', job, flags=re.MULTILINE
    ))
    assert versions == SUPPORTED_PYTHONS
    assert "python-version: ${{ matrix.python }}" in job

    starts = [job.index(f'          - python: "{version}"')
              for version in SUPPORTED_PYTHONS]
    ends = starts[1:] + [job.index("    steps:", starts[-1])]
    for start, end in zip(starts, ends):
        assert job[start:end].count(COMPATIBILITY_SELECTOR) == 1

    gate_name = "      - name: Compile and import every shipped Python entry point"
    test_name = "      - name: Run the authoritative suite or compatibility smoke set"
    gate_at = job.index(gate_name)
    tests_at = job.index(test_name)
    assert gate_at < tests_at
    assert "if: matrix.python" not in job[gate_at:tests_at]

    payload = _compile_import_payload(workflow)
    assert '"git", "ls-files"' in payload
    assert '"taskplane/*.py", "hooks/*.py"' in payload
    assert payload.index("compile(source") < payload.index(
        "importlib.import_module"
    )
    clean = subprocess.run(
        [sys.executable, "-B", "-c", payload],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert clean.returncode == 0, clean.stderr

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _copy_shipped_python_surface(checkout)
    (checkout / "taskplane" / "stage_entities.py").write_text(
        "def seeded_syntax_error(:\n", encoding="utf-8"
    )
    failed = subprocess.run(
        [sys.executable, "-B", "-c", payload],
        cwd=checkout,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert failed.returncode != 0
    assert "stage_entities.py" in failed.stderr


def _green_receipt(runner, runtime, cell, owned, *, python_version="3.12.9"):
    ownership_material = {
        "schema": "taskplane.ci-owned-cell/v1",
        "candidate_fingerprint": runtime["candidate"]["fingerprint"],
        "source_sha": runtime["candidate"]["source_sha"],
        "cell_id": cell["id"],
        "containment_root": str(owned.parent),
        "relative_name": owned.name,
        "registered_before_run": True,
    }
    ownership = {
        **ownership_material,
        "fingerprint": runner._sha256_json(ownership_material),
    }
    cleanup_material = {
        "schema": runner.CI_CLEANUP_SCHEMA,
        "registration_fingerprint": ownership["fingerprint"],
        "outcome": "success",
        "resources": [str(owned)],
        "status": "clean",
        "leak_count": 0,
        "leaks": [],
    }
    cleanup = {
        **cleanup_material,
        "fingerprint": runner._sha256_json(cleanup_material),
    }
    observed = {
        "implementation": "CPython", "python": python_version,
        "os": "posix", "platform": "Linux", "machine": "x86_64",
    }
    commands = [{
        "argv": argv, "returncode": 0, "duration_ms": 0,
        "output_digest": runner._digest(""),
    } for argv in runner._ci_cell_commands(cell, owned)]
    payload = {
        "schema": runner.CI_CELL_SCHEMA,
        "id": cell["id"],
        "kind": cell["kind"],
        "status": "green",
        "outcome": "success",
        "classification": None,
        "candidate_fingerprint": runtime["candidate"]["fingerprint"],
        "source_sha": runtime["candidate"]["source_sha"],
        "plan_fingerprint": runtime["plan"]["fingerprint"],
        "settings_receipt_fingerprint": runtime["settings_receipt"]["fingerprint"],
        "environment": {
            "candidate_fingerprint": runtime["candidate"]["fingerprints"]["environment"],
            "observed": observed,
            "observed_fingerprint": runner._sha256_json(observed),
        },
        "browser_fingerprint": (
            runtime["candidate"]["browser_fingerprint"]
            if cell["kind"] == "browser" else None
        ),
        "browser_observation": (
            runtime["candidate"]["browser"]
            if cell["kind"] == "browser" else None
        ),
        "selectors": cell["selectors"],
        "duration_ms": 0,
        "commands": commands,
        "output_digest": runner._digest(""),
        "ownership": ownership,
        "cleanup": cleanup,
    }
    return {**payload, "receipt": runner._sha256_json(payload)}


def test_authoritative_workflow_uses_settings_derived_disjoint_shards(tmp_path):
    runner = _runner()
    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source_sha = runner._git("rev-parse", "HEAD")
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=source_sha,
        event="pull_request",
        ref="482",
        run_id="9001",
        browser=_browser(),
    )
    plan = runtime["plan"]
    settings = runtime["settings_receipt"]

    assert plan["source_sha"] == source_sha
    assert plan["candidate_frozen_before_cells"] is True
    assert plan["matrices"] == contract["matrices"]
    assert [cell["id"] for cell in plan["cells"]] == contract["cell_ids"]
    assert len(plan["matrices"]) <= 3
    assert plan["max_parallel"] >= contract["minimum_parallelism"]
    assert sum(cell["timeout_seconds"] for cell in plan["cells"]) <= (
        contract["runner_minutes_max"] * 60
    )
    assert settings["candidate_sha"] == source_sha
    assert settings["settings_digest"] == plan["settings_digest"]
    assert settings["precedence"] == ["defaults", "file"]
    assert settings["loader_receipt"]["overlay"] is None
    assert settings["effective"]["build"] == {
        "shards": 1, "concurrency": "native",
    }
    assert settings["effective"]["tests"]["shards"] == len([
        cell for cell in plan["cells"] if cell["kind"] == "pytest"
    ])
    assert settings["effective"]["limits"]["timeouts"]["subprocess_seconds"] == 300

    selectors = [
        selector for cell in plan["cells"] for selector in cell["selectors"]
    ]
    paths = [path for cell in plan["cells"] for path in cell["paths"]]
    assert len(selectors) == len(set(selectors))
    assert len(paths) == len(set(paths))
    assert plan["terminal_aggregate"] == {
        "candidate_fingerprint": runtime["candidate"]["fingerprint"],
        "needs": contract["cell_ids"],
        "matching_receipts_only": True,
    }

    runtime_path = tmp_path / "runtime.json"
    runner._atomic_write_json(runtime_path, runtime)
    receipt_root = tmp_path / "receipts"
    for cell in plan["cells"]:
        owned = tmp_path / "owned" / cell["id"]
        receipt = _green_receipt(runner, runtime, cell, owned)
        runner._atomic_write_json(
            receipt_root / cell["id"] / f"{cell['id']}.json", receipt,
        )
    terminal_path = tmp_path / "terminal.json"
    assert runner.aggregate_authoritative_ci(
        runtime_path, receipt_root, terminal_path,
    ) == 0
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["green"] is True
    assert terminal["source_sha"] == source_sha
    assert terminal["settings_receipt"]["fingerprint"] == settings["fingerprint"]

    tampered_path = next(receipt_root.rglob("pytest-1.json"))
    tampered = json.loads(tampered_path.read_text(encoding="utf-8"))
    tampered["environment"]["observed"]["python"] = "3.13-tampered"
    tampered["receipt"] = runner._sha256_json({
        key: value for key, value in tampered.items() if key != "receipt"
    })
    runner._atomic_write_json(tampered_path, tampered)
    with pytest.raises(runner.RunnerError, match="observed environment"):
        runner.aggregate_authoritative_ci(
            runtime_path, receipt_root, tmp_path / "refused-terminal.json",
        )

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--emit-ci-plan" in workflow
    assert "name: settings-derived authoritative CI plan" in workflow
    assert "needs: [ci-plan]" in workflow
    assert 'matrix: ${{ fromJSON(needs.ci-plan.outputs.pytest-matrix) }}' in workflow
    assert 'max-parallel: ${{ fromJSON(needs.ci-plan.outputs.max-parallel) }}' in workflow
    assert '--ci-cell "${{ matrix.cell }}"' in workflow
    assert workflow.count('--ci-cell "$cell"') == 2
    assert workflow.count("--ci-cell") == 3
    assert "name: dashboard browser conformance" in workflow
    assert "name: authoritative CI terminal matrix" in workflow
    assert "needs: [ci-plan, authoritative-tests, python-quality, dashboard-browser]" in workflow
    authoritative_tests = workflow.split("  authoritative-tests:", 1)[1].split(
        "\n  python-quality:", 1,
    )[0]
    assert "fetch-depth: 30" in authoritative_tests
    assert 'expected = [cell["id"] for cell in runtime["plan"]["cells"]]' in workflow
    assert 'expected = ["pytest-1"' not in workflow
    assert "--aggregate-ci" in workflow
    assert "pattern: ci-cell-*-${{ needs.ci-plan.outputs.candidate-sha }}" in workflow
    assert "name: terminal-matrix-${{ needs.ci-plan.outputs.candidate-sha }}" in workflow
    assert workflow.count("TERMINAL_RESULT:") == 3
    assert workflow.count("BROWSER_RESULT:") == 3
    assert workflow.count("Restore terminal receipt evidence") == 3
    assert workflow.count("Restore browser receipt evidence") == 3
    assert "--ignore=taskplane/tests/test_dashboard_browser.py" not in workflow
    assert workflow.count("strategy:") == 3
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "permissions:\n  contents: read" in workflow
    compatibility = json.loads(
        (ROOT / "design" / "compatibility.json").read_text(encoding="utf-8")
    )
    required = compatibility["release_authority"]["required_checks"]
    assert required[0] == "tests (python 3.12)"
    assert "name: tests (python 3.12)" in workflow
    assert "name: Python compatibility (${{ matrix.python }})" in workflow
    assert '          - python: "3.12"' in workflow
    assert all(f"name: {name}" in workflow for name in required[1:])
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
    assert action_refs and all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)


def test_quality_receipt_command_identity_is_cross_runner_and_tamper_closed(
    tmp_path, monkeypatch,
):
    runner = _runner()
    source_sha = runner._git("rev-parse", "HEAD")
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=source_sha,
        event="pull_request",
        ref="482",
        run_id="9001",
        browser=_browser(),
    )
    runtime_path = tmp_path / "runtime.json"
    runner._atomic_write_json(runtime_path, runtime)
    receipt_root = tmp_path / "receipts"
    quality_path = receipt_root / "quality-package" / "quality-package.json"
    for cell in runtime["plan"]["cells"]:
        receipt = _green_receipt(
            runner,
            runtime,
            cell,
            tmp_path / "owned" / cell["id"],
            python_version="3.14.0" if cell["id"] == "quality-package" else "3.12.9",
        )
        runner._atomic_write_json(
            receipt_root / cell["id"] / f"{cell['id']}.json", receipt,
        )

    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    assert {command["argv"][0] for command in quality["commands"]} == {
        runner.CI_LOGICAL_PYTHON,
    }
    monkeypatch.setattr(runner, "PYTHON", "/opt/cpython-3.12/bin/python3")
    assert runner.aggregate_authoritative_ci(
        runtime_path, receipt_root, tmp_path / "terminal.json",
    ) == 0

    quality["commands"][0]["argv"][0] = "/opt/cpython-3.14/bin/python3"
    quality["receipt"] = runner._sha256_json({
        key: value for key, value in quality.items() if key != "receipt"
    })
    runner._atomic_write_json(quality_path, quality)
    with pytest.raises(runner.RunnerError, match="command evidence is not exact"):
        runner.aggregate_authoritative_ci(
            runtime_path, receipt_root, tmp_path / "refused-terminal.json",
        )
