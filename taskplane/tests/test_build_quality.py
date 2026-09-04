"""Behavioral contracts for candidate-bound Build quality evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import (
    build_quality, ci_policy, phase_handoff, phase_pickup, test_strategy,
)
from taskplane.tests.test_stateless_phase_pickup import _handoff


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "design" / "test-strategy.json"
CI_CANDIDATE = Path(__file__).parent / "fixtures" / "ci-policy" / "candidate.json"
PRODUCER_ID = "validation:test-strategy"
FIXTURE_PATH = "design/test-strategy.json"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strategy() -> dict:
    return test_strategy.seal_strategy(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def _binding(name: str = "candidate-2") -> dict:
    return {
        "candidate": {"id": name, "fingerprint": _digest(name)},
        "run_id": "run-2",
        "stage_instance": "build:FAILURE-BUILD-QUALITY:1",
        "settings_digest": _digest("settings"),
        "runtime_digest": _digest("python-3.13"),
        "environment_digest": _digest("darwin-arm64"),
    }


def _receipt(strategy: dict | None = None, *, paths: list[str] | None = None) -> dict:
    return build_quality.begin_receipt(
        strategy or _strategy(),
        binding=_binding(),
        criterion_ids=["AC-TST1"],
        changed_producer_ids=[PRODUCER_ID],
        changed_paths=paths or ["taskplane/test_strategy.py", FIXTURE_PATH],
    )


def _static(receipt: dict) -> dict:
    python_paths = [path for path in receipt["changed_paths"] if path.endswith(".py")]
    return {
        "compile_import": {"paths": python_paths, "passed": True},
        "focused_static": {
            "paths": python_paths,
            "checks": ["ruff", "typed-boundaries"],
            "passed": True,
        },
    }


def _exact(receipt: dict) -> dict:
    selectors = receipt["selectors"]
    return {
        "collection": {
            "requested": selectors,
            "collected": selectors,
            "passed": True,
        },
        "execution": {
            "executed": selectors,
            "passed": selectors,
            "failed": [],
        },
    }


def _radius(receipt: dict) -> dict:
    producers = receipt["changed_producers"]
    return {
        "consumer_radius": [
            {"producer": producer["id"], "consumer": consumer}
            for producer in producers
            for consumer in producer["consumers"]
        ],
        "severed_edges": [
            {
                "producer": producer["id"],
                "consumer": edge["consumer"],
                "mutation": edge["mutation"],
                "selector": edge["selector"],
                "baseline_passed": True,
                "severed_failed": True,
                "restored_passed": True,
            }
            for producer in producers
            for edge in producer["severed_edges"]
        ],
        "fixture_cochange": [
            {"producer": producer["id"], "path": fixture["path"]}
            for producer in producers
            if producer.get("interface_kind", "serialized") in {
                "serialized", "external"
            }
            for fixture in producer.get("interface_fixtures", [])
        ],
    }


def _advance(
    strategy: dict, receipt: dict, layer: str, payload: dict, execution: str
) -> dict:
    evidence = build_quality.seal_layer_evidence(receipt, layer, payload)
    return build_quality.advance_validation(
        strategy, receipt, layer, evidence, execution=execution
    )


def _complete_build(strategy: dict, receipt: dict) -> dict:
    receipt = _advance(strategy, receipt, "static", _static(receipt), "local")
    receipt = _advance(
        strategy, receipt, "exact-selector", _exact(receipt), "local"
    )
    receipt = _advance(
        strategy, receipt, "changed-radius", _radius(receipt), "ci"
    )
    return _advance(
        strategy,
        receipt,
        "proportional-suite",
        {"scope": ["taskplane/tests/test_test_strategy_contract.py"], "passed": True},
        "ci",
    )


def _published_build_checkout(tmp_path: Path) -> tuple[Path, dict]:
    checkout = tmp_path / "producer"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(checkout)],
                   check=True)
    subprocess.run(["git", "config", "user.email", "build@example.invalid"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.name", "Build test"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "rm", "-q", "design/contract.json"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "remove unrelated contract"],
                   cwd=checkout, check=True)
    handoff = _handoff(checkout, "build")
    phase_handoff.publish_phase_handoff(checkout, handoff)
    subprocess.run(["git", "add", "-f", "exports/pickup"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "publish build handoff"],
                   cwd=checkout, check=True)
    return checkout, handoff


def test_receipt_proves_the_complete_build_progression_and_exact_binding():
    strategy = _strategy()
    receipt = _complete_build(strategy, _receipt(strategy))

    admitted = build_quality.admit_build_quality(
        strategy, receipt, expected_binding=_binding()
    )
    assert admitted["progression"]["completed"] == list(
        build_quality.BUILD_REQUIRED_LAYERS
    )
    assert admitted["build_complete"] is True
    assert admitted["authoritative"] is False

    authoritative = _advance(
        strategy,
        admitted,
        "authoritative-ci",
        {"matrix_runs": 1, "passed": True},
        "ci",
    )
    assert authoritative["authoritative"] is True
    assert authoritative["progression"]["matrix_runs"] == 1

    stale_binding = _binding()
    stale_binding["stage_instance"] = "build:another-stage:1"
    with pytest.raises(build_quality.BuildQualityError, match="another active stage"):
        build_quality.admit_build_quality(
            strategy, receipt, expected_binding=stale_binding
        )


def test_uncollected_selector_and_progression_skip_fail_closed():
    strategy = _strategy()
    receipt = _advance(
        strategy, _receipt(strategy), "static", _static(_receipt(strategy)), "local"
    )
    missing = _exact(receipt)
    missing["collection"]["collected"] = []
    evidence = build_quality.seal_layer_evidence(
        receipt, "exact-selector", missing
    )
    with pytest.raises(build_quality.BuildQualityError, match="declared Build scope"):
        build_quality.advance_validation(
            strategy, receipt, "exact-selector", evidence, execution="local"
        )

    radius_evidence = build_quality.seal_layer_evidence(
        receipt, "changed-radius", _radius(receipt)
    )
    with pytest.raises(build_quality.BuildQualityError, match="advance to 'exact-selector'"):
        build_quality.advance_validation(
            strategy, receipt, "changed-radius", radius_evidence, execution="ci"
        )


def test_tampered_or_other_candidate_evidence_never_enters_the_receipt():
    strategy = _strategy()
    receipt = _receipt(strategy)
    evidence = build_quality.seal_layer_evidence(receipt, "static", _static(receipt))
    tampered = copy.deepcopy(evidence)
    tampered["payload"]["compile_import"]["passed"] = False
    with pytest.raises(build_quality.BuildQualityError, match="digest is stale"):
        build_quality.advance_validation(
            strategy, receipt, "static", tampered, execution="local"
        )

    other = build_quality.begin_receipt(
        strategy,
        binding=_binding("candidate-other"),
        criterion_ids=["AC-TST1"],
        changed_producer_ids=[PRODUCER_ID],
        changed_paths=["taskplane/test_strategy.py", FIXTURE_PATH],
    )
    with pytest.raises(build_quality.BuildQualityError, match="current Build inputs"):
        build_quality.advance_validation(
            strategy, other, "static", evidence, execution="local"
        )


def test_fixture_cochange_applies_only_to_serialized_or_external_interfaces():
    strategy = _strategy()
    serialized = _receipt(strategy, paths=["taskplane/test_strategy.py"])
    serialized = _advance(
        strategy, serialized, "static", _static(serialized), "local"
    )
    serialized = _advance(
        strategy, serialized, "exact-selector", _exact(serialized), "local"
    )
    evidence = build_quality.seal_layer_evidence(
        serialized, "changed-radius", _radius(serialized)
    )
    with pytest.raises(build_quality.BuildQualityError, match="did not co-change"):
        build_quality.advance_validation(
            strategy, serialized, "changed-radius", evidence, execution="ci"
        )

    in_process_strategy = copy.deepcopy(strategy)
    producer = next(
        row for row in in_process_strategy["producers"] if row["id"] == PRODUCER_ID
    )
    producer["interface_kind"] = "in-process"
    producer["interface_fixtures"] = []
    in_process_strategy = test_strategy.seal_strategy(in_process_strategy)
    in_process = _receipt(
        in_process_strategy, paths=["taskplane/test_strategy.py"]
    )
    in_process = _advance(
        in_process_strategy, in_process, "static", _static(in_process), "local"
    )
    in_process = _advance(
        in_process_strategy,
        in_process,
        "exact-selector",
        _exact(in_process),
        "local",
    )
    in_process = _advance(
        in_process_strategy,
        in_process,
        "changed-radius",
        _radius(in_process),
        "ci",
    )
    assert in_process["progression"]["completed"][-1] == "changed-radius"
    assert _radius(in_process)["fixture_cochange"] == []


def test_ci_adapter_consumes_the_same_progression_authority():
    candidate = ci_policy.freeze_candidate(
        json.loads(CI_CANDIDATE.read_text(encoding="utf-8"))
    )
    assert ci_policy.VALIDATION_LAYERS is build_quality.VALIDATION_LAYERS
    progression = None
    for layer, execution in (
        ("static", "local"),
        ("exact-selector", "local"),
        ("changed-radius", "ci"),
        ("proportional-suite", "ci"),
        ("authoritative-ci", "ci"),
    ):
        progression = ci_policy.advance_validation(
            candidate,
            layer,
            execution=execution,
            prior=progression,
        )

    assert progression["authoritative"] is True
    assert progression["completed"] == list(build_quality.VALIDATION_LAYERS)


def test_committed_build_diff_reconstructs_internal_submission(tmp_path):
    checkout, handoff = _published_build_checkout(tmp_path)
    pickup = phase_pickup.prepare(str(checkout), handoff)
    task_id = pickup["task"]["id"]
    del pickup

    source = checkout / "taskplane" / "phase_handoff.py"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# committed Build output\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "taskplane/phase_handoff.py"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "author Build task"],
                   cwd=checkout, check=True)

    result = phase_pickup.submit_committed(
        str(checkout), handoff, task_id=task_id)

    assert result["status"] == "complete"
    assert result["task_id"] == task_id
    assert "lease" not in result


def test_out_of_scope_committed_diff_never_reaches_build_c(
        tmp_path, monkeypatch):
    checkout, handoff = _published_build_checkout(tmp_path)
    task_id = phase_pickup.prepare(str(checkout), handoff)["task"]["id"]
    readme = checkout / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nout of scope\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "README.md"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "author outside Build scope"],
                   cwd=checkout, check=True)
    reached_build_c = False

    def forbidden(*_args, **_kwargs):
        nonlocal reached_build_c
        reached_build_c = True
        raise AssertionError("BUILD-C ran for an out-of-scope diff")

    monkeypatch.setattr(phase_pickup.build_c, "run_phase_pickup", forbidden)
    with pytest.raises(phase_pickup.PhasePickupError, match="source-stale"):
        phase_pickup.submit_committed(
            str(checkout), handoff, task_id=task_id)
    assert reached_build_c is False
