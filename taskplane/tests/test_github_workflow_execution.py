from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SPEC = importlib.util.spec_from_file_location(
    "ci_local_workflow_contract", ROOT / "scripts" / "ci_local.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _workflow() -> dict:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _cell_invocations(job: dict) -> list[str]:
    found = []
    for step in job["steps"]:
        run = step.get("run")
        if not isinstance(run, str):
            continue
        found.extend(re.findall(r"--ci-cell\s+([^\s]+)", run))
    return found


def _expanded_check_names(workflow: dict) -> list[str]:
    names = []
    for job in workflow["jobs"].values():
        name = job["name"]
        matrix = job.get("strategy", {}).get("matrix", {})
        if "${{ matrix.python }}" in name:
            names.extend(
                name.replace("${{ matrix.python }}", version)
                for version in matrix["python"]
            )
        else:
            names.append(name)
    return names


def test_release_check_authority_matches_expanded_direct_workflow_checks():
    workflow = _workflow()
    policy = json.loads(
        (ROOT / "design" / "compatibility.json").read_text(encoding="utf-8")
    )

    assert policy["release_authority"]["required_checks"] == \
        _expanded_check_names(workflow)


def test_workflow_dispatches_each_runner_cell_directly_without_join_jobs():
    runner = _runner()
    workflow = _workflow()
    jobs = workflow["jobs"]
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request", ref="482", run_id="9001",
    )
    expected = {cell["id"] for cell in runtime["plan"]["cells"]}
    observed = set()
    for job in jobs.values():
        assert "needs" not in job
        observed.update(_cell_invocations(job))

    # Expand the one interpreter expression as GitHub's matrix does.
    observed.remove("interpreter-import-${{")
    observed.update({f"interpreter-import-{version}" for version in
                     jobs["interpreter-import"]["strategy"]["matrix"]["python"]})
    assert observed == expected
    assert set(jobs) == {
        "tests", "quality-package", "dashboard-browser",
        "interpreter-import", "native-portability", "security-no-egress",
    }
    assert not ({"ci-plan", "pr-head-sha-proof", "pushed-sha-proof",
                 "tests-authority"} & set(jobs))


def test_direct_jobs_are_candidate_bound_pinned_and_publish_on_every_outcome():
    workflow = _workflow()
    for job in workflow["jobs"].values():
        checkouts = [step for step in job["steps"]
                     if str(step.get("uses", "")).startswith("actions/checkout@")]
        assert len(checkouts) == 1
        checkout = checkouts[0]
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
        assert checkout["with"]["ref"] == "${{ env.CANDIDATE_SHA }}"
        assert checkout["with"]["persist-credentials"] is False

        actions = [step["uses"] for step in job["steps"] if "uses" in step]
        assert all(re.search(r"@[0-9a-f]{40}$", action) for action in actions)
        publishers = [step for step in job["steps"]
                      if str(step.get("uses", "")).startswith(
                          "actions/upload-artifact@")]
        assert len(publishers) == 1
        assert publishers[0]["if"] == "always()"
        assert publishers[0]["with"]["if-no-files-found"] == "error"


def test_workflow_topology_has_no_compatibility_pytest_or_quality_replay():
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert jobs["interpreter-import"]["strategy"]["matrix"]["python"] == [
        "3.10", "3.11", "3.13",
    ]
    assert jobs["native-portability"]["runs-on"] == "windows-latest"
    assert jobs["tests"]["runs-on"] == "ubuntu-latest"

    for job_id in ("quality-package", "interpreter-import",
                   "security-no-egress"):
        commands = "\n".join(
            step.get("run", "") for step in jobs[job_id]["steps"]
            if isinstance(step.get("run"), str)
        )
        assert "pytest" not in commands
    assert _cell_invocations(jobs["tests"]) == ["pytest-1"]
    assert _cell_invocations(jobs["dashboard-browser"]) == [
        "dashboard-browser",
    ]
    assert _cell_invocations(jobs["native-portability"]) == [
        "os-portability-windows",
    ]
