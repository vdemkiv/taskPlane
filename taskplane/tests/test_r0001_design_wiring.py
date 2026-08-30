from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import zipfile

import pytest

from taskplane import checkpoint, design_contract, dispatch_telemetry
from taskplane.delivery_ports import FakeClock, RecordedPlatformCiQuery
from taskplane.release_evidence import CURRENT_VERSION, create_release_green
from taskplane.wiring_closure import (
    WiringClosureError,
    validate_acceptance_map,
    validate_producer_edges,
)


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = "lenses/references/prompt-injection-defense.md"
R0001_CLOSED_DESIGN_REVISION = "c9ec81a021ac74b048bfa58abfbfec870e49711a"


def _design_contract() -> dict:
    # R-0013 legitimately owns the live Design Contract. These release
    # regressions validate the immutable, already-closed R-0001 contract
    # without rewriting that historical record or confusing it with HEAD.
    result = subprocess.run(
        ["git", "show", f"{R0001_CLOSED_DESIGN_REVISION}:design/contract.json"],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _all_test_references(rows) -> list[str]:
    references = []
    for row in rows:
        references.append(row["edge_test"])
        references.extend(row.get("additional_tests") or [])
    return references


def test_each_acceptance_row_declares_existing_test_file_and_exact_selector():
    acceptance_map = _design_contract()["acceptance_map"]
    references = [
        reference
        for row in acceptance_map
        for reference in row["tests"]
    ]
    receipt = validate_acceptance_map(
        acceptance_map,
        caller_root=ROOT,
    )

    assert receipt["status"] == "closed"
    assert receipt["criterion_count"] == 12
    assert receipt["selectors"] == sorted(set(references))
    assert len(receipt["fingerprint"]) == 64

    missing_selector = deepcopy(acceptance_map)
    missing_selector[0]["tests"][0] += "_not_declared"
    with pytest.raises(WiringClosureError, match="exact selector") as exc:
        validate_acceptance_map(missing_selector, caller_root=ROOT)
    assert missing_selector[0]["tests"][0] in str(exc.value)


def test_present_acceptance_map_cannot_omit_tests_from_every_row(tmp_path):
    contract = {
        "acceptance_map": [{
            "criterion": "The exact acceptance criterion",
            "design_element": "The checkpoint adapter",
            "validation": "The exact selector is resolved before execution",
        }],
    }

    with pytest.raises(
        design_contract.DesignAcceptanceError,
        match="acceptance criterion has no exact tests: "
              "The exact acceptance criterion",
    ):
        design_contract.acceptance_test_map(contract)
    with pytest.raises(
        design_contract.DesignAcceptanceError,
        match="acceptance criterion has no exact tests: "
              "The exact acceptance criterion",
    ):
        design_contract.checkpoint_acceptance_tests(
            str(tmp_path), contract, ["The exact acceptance criterion"]
        )

    assert design_contract.acceptance_test_map({}) is None
    assert design_contract.checkpoint_acceptance_tests(
        str(tmp_path), {}, ["legacy criterion"]
    ) is None


def _checkpoint_repository(tmp_path: Path, acceptance_map: list[dict]) -> Path:
    root = tmp_path / "checkout"
    proof = root / "taskplane" / "tests" / "test_ac.py"
    proof.parent.mkdir(parents=True)
    proof.write_text("def test_proof():\n    assert True\n", encoding="utf-8")
    contract_path = root / "design" / "contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        json.dumps({"acceptance_map": acceptance_map}), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Taskplane Test",
            "-c", "user.email=taskplane@example.invalid",
            "commit", "-qm", "checkpoint fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


def test_checkpoint_refuses_named_missing_test_file(tmp_path):
    missing = "taskplane/tests/test_missing.py::test_missing"
    root = _checkpoint_repository(tmp_path, [{
        "criterion": "The exact acceptance criterion",
        "tests": [
            "taskplane/tests/test_ac.py::test_proof",
            missing,
        ],
    }])
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        encoding="utf-8", errors="replace"
    ).strip()
    spec = {
        "schema": checkpoint.CHECKPOINT_SCHEMA,
        "checkpoint_id": "cp-design-ac-1",
        "phase": "build",
        "ac_ids": ["The exact acceptance criterion"],
        "predecessor_checkpoint_ids": [],
        "worktree_revision": revision,
        "declared_scope": ["taskplane/tests/**"],
        "focused_proof": {
            "path": "taskplane/tests/test_ac.py",
            "argv": [
                "python3", "-m", "pytest", "-q",
                "taskplane/tests/test_ac.py::test_proof",
            ],
        },
        "ratchet_baseline": {"cycle_count": 0},
    }

    with pytest.raises(checkpoint.CheckpointSpecError, match="declared test file") \
            as exc:
        checkpoint.validate_checkpoint_spec(str(root), spec)
    assert "taskplane/tests/test_missing.py" in str(exc.value)


def test_build_dor_refuses_unnamed_selector(tmp_path, monkeypatch):
    contract = {
        "acceptance_map": [{
            "criterion": "The exact acceptance criterion",
            "tests": ["taskplane/tests/test_ac.py"],
        }],
        "graph": {},
        "contracts": [],
    }
    monkeypatch.setattr(
        design_contract, "design_contract", lambda _ws: (contract, [])
    )

    errors = design_contract.design_plan_errors(
        str(tmp_path), {"design_required": True, "tasks": []}
    )

    assert any("file.py::selector" in error for error in errors)


def test_every_changed_producer_has_closed_consumer_edges_and_edge_tests():
    wiring = _design_contract()["wiring_closure"]
    receipt = validate_producer_edges(wiring, caller_root=ROOT)

    assert receipt["schema"] == "taskplane.wiring-closure/v1"
    assert receipt["status"] == "designed"
    assert receipt["edge_count"] == 32
    assert receipt["producer_count"] == 18
    assert [edge["id"] for edge in receipt["edges"]] == [
        f"W{number:02d}" for number in range(1, 33)
    ]
    assert len(receipt["fingerprint"]) == 64

    severed = deepcopy(wiring)
    severed["edges"] = severed["edges"][:-1]
    severed["edge_count"] = len(severed["edges"])
    with pytest.raises(WiringClosureError, match="W32"):
        validate_producer_edges(severed, caller_root=ROOT)

    severed_producer = deepcopy(wiring)
    severed_producer["producer_closure"][0]["consumer_classes"].pop()
    with pytest.raises(WiringClosureError, match="consumer classes"):
        validate_producer_edges(severed_producer, caller_root=ROOT)


def test_producer_closure_rejects_swapped_valid_edge_ids():
    wiring = _design_contract()["wiring_closure"]
    severed = deepcopy(wiring)
    delivery_edges = severed["producer_closure"][0]["edge_ids"]
    review_edges = severed["producer_closure"][1]["edge_ids"]
    delivery_index = delivery_edges.index("W02")
    review_index = review_edges.index("W05")
    delivery_edges[delivery_index], review_edges[review_index] = (
        review_edges[review_index],
        delivery_edges[delivery_index],
    )

    with pytest.raises(WiringClosureError, match="producer.*edge binding"):
        validate_producer_edges(severed, caller_root=ROOT)


def test_w31_remains_open_without_external_host_receipt():
    wiring = _design_contract()["wiring_closure"]
    receipt = validate_producer_edges(wiring, caller_root=ROOT)

    assert receipt["status"] == "designed"
    assert next(
        edge for edge in receipt["edges"] if edge["id"] == "W31"
    )["required_status"] == "closed"
    self_declared = deepcopy(wiring)
    self_declared["status"] = "closed"
    with pytest.raises(WiringClosureError, match="W31.*external host"):
        validate_producer_edges(self_declared, caller_root=ROOT)


def test_all_wiring_selectors_resolve():
    wiring = _design_contract()["wiring_closure"]
    references = _all_test_references(wiring["edges"])
    receipt = validate_producer_edges(wiring, caller_root=ROOT)
    assert receipt["selectors"] == sorted(set(references))

    missing_file = "taskplane/tests/test_missing_wiring_edge.py"
    missing = deepcopy(wiring)
    missing["edges"][0]["edge_test"] = missing_file + "::test_missing"
    with pytest.raises(WiringClosureError, match="declared test file") as exc:
        validate_producer_edges(missing, caller_root=ROOT)
    assert missing_file in str(exc.value)


def test_native_design_schema_inventory_matches_runtime_and_retires_scheduler():
    contract = _design_contract()
    bundle = json.loads((
        ROOT / "design" / "schemas" / "r0001-evidence-schemas.json"
    ).read_text(encoding="utf-8"))
    compatibility = json.loads((
        ROOT / "design" / "compatibility.json"
    ).read_text(encoding="utf-8"))

    assert bundle["$id"] == "taskplane.r0001-evidence-schemas/v2"
    definitions = bundle["$defs"]
    assert {"dispatchAdmission", "executionDag"}.isdisjoint(definitions)
    assert {
        "planTopology", "dispatchSet", "dispatchTelemetryBinding",
        "waveBudget", "executionMetrics",
    } <= set(definitions)
    assert definitions["releaseGreen"]["properties"]["version"]["const"] == \
        CURRENT_VERSION
    assert {"scheduler_admission", "execution_dag"}.isdisjoint(contract)
    assert {"dispatch_admission", "execution_dag"}.isdisjoint(
        contract["receipt_contracts"])

    expected_telemetry_fields = {
        "schema", "duration_seconds", "fingerprint",
        *dispatch_telemetry._DISPATCH_FIELDS,
        *dispatch_telemetry._USAGE_FIELDS,
    }
    assert set(definitions["dispatchTelemetry"]["required"]) == \
        expected_telemetry_fields
    amendment = compatibility["design_schema_bundle_amendment"]
    assert amendment["previous_bundle_id"].endswith("/v1")
    assert amendment["current_bundle_id"] == bundle["$id"]
    assert amendment["retired_definitions"] == [
        "dispatchAdmission", "executionDag",
    ]


def _load_packager(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        f"_r0001_wiring_{name.replace('.', '_')}", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_security_methodology_reference_exists_is_packaged_and_loads(tmp_path):
    source = (ROOT / REFERENCE).read_bytes()
    assert source
    for packager_name in ("package_openai.py", "package_claude.py"):
        packager = _load_packager(packager_name)
        files = (
            packager.package_files(packager.load_manifest())
            if packager_name == "package_openai.py"
            else packager.package_files()
        )
        archive_path = tmp_path / f"{packager_name}.zip"
        packager.write_zip(files, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            security = archive.read("taskplane/lenses/security.md").decode()
            methodology_pointer = re.search(
                r"`(lenses/references/security-methodology\.md)`", security
            )
            assert methodology_pointer is not None
            methodology = archive.read(
                "taskplane/" + methodology_pointer.group(1)
            ).decode()
            reference_pointer = re.search(
                r"`(references/prompt-injection-defense\.md)`", methodology
            )
            assert reference_pointer is not None
            member = "taskplane/lenses/" + reference_pointer.group(1)
            assert archive.read(member) == source


def test_prompt_injection_reference_declares_detect_obstruct_flag_contract():
    reference = (ROOT / REFERENCE).read_text(encoding="utf-8")
    assert "detect → obstruct → flag" in reference
    assert "## Detect" in reference
    assert "## Obstruct" in reference
    assert "## Flag" in reference
    assert "High-risk sinks fail closed" in reference


def test_release_green_binds_reviewed_prompt_injection_reference_digest():
    source_sha = "a" * 40
    digest = hashlib.sha256((ROOT / REFERENCE).read_bytes()).hexdigest()
    response = {
        "schema": "taskplane.platform-ci-proof/v1",
        "provider": "github",
        "repository_id": "openai/taskplane",
        "protected_default_branch": "main",
        "pushed_sha": source_sha,
        "workflow_run_id": "run-21722",
        "check_run_ids": ["check-linux"],
        "required_check_names": ["full / linux"],
        "conclusions": {"full / linux": "success"},
        "queried_at": 100.0,
        "fresh_until": 200.0,
        "platform_response_digest": "e" * 64,
    }
    receipt = create_release_green(
        source_sha=source_sha,
        version="2.18.1",
        wiring_closure_fingerprint="1" * 64,
        feature_receipt_digests=["2" * 64],
        full_matrix_receipts=["3" * 64],
        package_manifest_receipts=["4" * 64, "5" * 64],
        compatibility_policy_fingerprint="6" * 64,
        schema_bundle_fingerprint="7" * 64,
        compatibility_diff_receipt="8" * 64,
        mixed_version_matrix_receipt="9" * 64,
        live_host_canary_receipt="b" * 64,
        recorded_event_replay_receipt="c" * 64,
        host_action_capability_refusal_receipt="d" * 64,
        task_dispatch_capability_default_deny_receipt="e" * 64,
        reviewed_prompt_injection_reference_digest=digest,
        repository_id="openai/taskplane",
        protected_default_branch="main",
        workflow_run_id="run-21722",
        check_run_ids=["check-linux"],
        required_check_names=["full / linux"],
        outside_model_human_recheck={
            "actor": "human:release-owner",
            "channel": "outside-model",
            "action": "release-candidate",
            "source_sha": source_sha,
            "confirmed": True,
            "cryptographic_authenticity_claimed": False,
        },
        platform_ci_query=RecordedPlatformCiQuery([response]),
        clock=FakeClock(wall_time=110.0),
    )

    assert receipt["reviewed_prompt_injection_reference_digest"] == digest
