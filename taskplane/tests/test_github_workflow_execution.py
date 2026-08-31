from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re


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
        payload = {
            "schema": runner.CI_CELL_SCHEMA,
            "id": cell["id"],
            "kind": cell["kind"],
            "status": "green",
            "candidate_fingerprint": runtime["candidate"]["fingerprint"],
            "source_sha": source_sha,
            "plan_fingerprint": plan["fingerprint"],
            "settings_receipt_fingerprint": settings["fingerprint"],
            "cleanup": {"leak_count": 0},
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

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--emit-ci-plan" in workflow
    assert "--ci-cell \"${{ matrix.id }}\"" in workflow
    assert "--aggregate-ci" in workflow
    assert "max-parallel: ${{ fromJSON(needs.wave3-contracts.outputs.max-parallel) }}" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "permissions:\n  contents: read" in workflow
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
    assert action_refs and all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
