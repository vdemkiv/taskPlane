from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "ci-runtime" / "contract.json"
SPEC = importlib.util.spec_from_file_location(
    "ci_local_github_contract", ROOT / "scripts" / "ci_local.py",
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
    assert settings["precedence"] == ["defaults", "file", "overlay"]
    assert settings["loader_receipt"]["overlay"]["applied"] == [
        "build.shards", "tests.shards",
    ]
    assert settings["effective"]["build"] == {
        "shards": len(plan["cells"]), "concurrency": "native",
    }
    assert settings["effective"]["tests"]["shards"] == len(plan["cells"])

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
        ownership_material = {
            "schema": "taskplane.ci-owned-cell/v1",
            "candidate_fingerprint": runtime["candidate"]["fingerprint"],
            "source_sha": source_sha,
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
            "implementation": "CPython", "python": "3.12.9",
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
            "source_sha": source_sha,
            "plan_fingerprint": plan["fingerprint"],
            "settings_receipt_fingerprint": settings["fingerprint"],
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
        receipt = {**payload, "receipt": runner._sha256_json(payload)}
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
    assert "--ci-cell dashboard-browser" in workflow
    assert "name: dashboard browser conformance" in workflow
    assert workflow.count("--ignore=taskplane/tests/test_dashboard_browser.py") == 1
    assert workflow.count("strategy:") == 2
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "permissions:\n  contents: read" in workflow
    compatibility = json.loads(
        (ROOT / "design" / "compatibility.json").read_text(encoding="utf-8")
    )
    required = compatibility["release_authority"]["required_checks"]
    assert required[0] == "tests (python 3.12)"
    assert "name: tests (python ${{ matrix.python }})" in workflow
    assert '          - python: "3.12"' in workflow
    assert all(f"name: {name}" in workflow for name in required[1:])
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
    assert action_refs and all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
