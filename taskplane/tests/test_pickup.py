from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import checkpoint
import pickup
import storage
import taskplane_lite as contract_engine
import tp


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _signature(value: object, key: bytes) -> str:
    return hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


def _git(checkout: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=checkout, text=True
    ).strip()


def _commit(checkout: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "commit", "-qm", message], cwd=checkout, check=True
    )
    return _git(checkout, "rev-parse", "HEAD")


def _authority_document(source_sha: str, *, key: bytes, key_id: str,
                        receipt_sha: str | None = None) -> dict:
    design = {
        "schema": "taskplane.pickup-design/v1",
        "source_sha": source_sha,
        "element": {
            "id": "element-ac1",
            "scope": ["tests/test_proof.py"],
            "acceptance": [{
                "id": "AC1",
                "proof": {
                    "path": "tests/test_proof.py",
                    "argv": [
                        "python3", "-m", "pytest", "-q",
                        "-p", "no:cacheprovider", "tests/test_proof.py",
                    ],
                },
            }],
        },
    }
    fingerprint = hashlib.sha256(_canonical(design)).hexdigest()
    approval = {
        "schema": "taskplane.pickup-design-approval/v1",
        "actor": "human:test-operator",
        "design_fingerprint": fingerprint,
        "key_id": key_id,
    }
    approval["signature"] = _signature(approval, key)
    engine_receipt = {
        "schema": "taskplane.pickup-engine-receipt/v1",
        "producer": "taskplane.design-approval-engine/v1",
        "source_sha": receipt_sha or source_sha,
        "design_fingerprint": fingerprint,
        "key_id": key_id,
    }
    engine_receipt["signature"] = _signature(engine_receipt, key)
    return {
        "schema": "taskplane.approved-pickup-contract/v1",
        "design": design,
        "approval": approval,
        "engine_receipt": engine_receipt,
    }


@pytest.fixture
def signed_shelf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    checkout = tmp_path / "repo"
    proof = checkout / "tests" / "test_proof.py"
    proof.parent.mkdir(parents=True)
    proof.write_text(
        "import os\n\n"
        "def test_proof():\n"
        "    size = int(os.environ.get('TASKPLANE_PICKUP_TEST_OUTPUT_BYTES', '0'))\n"
        "    if size:\n"
        "        print('x' * size)\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (checkout / ".gitignore").write_text(
        ".taskplane/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "config", "user.email", "pickup@example.test"],
        cwd=checkout, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Pickup Test"],
        cwd=checkout, check=True,
    )
    signing_authority = contract_engine._review_contract_authority(  # noqa: SLF001
        str(checkout), create=True
    )
    source_sha = _commit(checkout, "source")
    authority_path = checkout / "design" / "shelf.json"
    authority_path.parent.mkdir()
    authority_path.write_text(
        json.dumps(_authority_document(
            source_sha, key=signing_authority["secret"],
            key_id=signing_authority["key_id"],
        ), indent=2) + "\n",
        encoding="utf-8",
    )
    _commit(checkout, "signed shelf authority")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return checkout, "design/shelf.json"


def _home_snapshot(home: Path) -> list[str]:
    return sorted(
        str(path.relative_to(home)) for path in home.rglob("*")
    ) if home.exists() else []


def _byte_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    } if root.exists() else {}


def test_signed_shelf_pickup_reaches_checkpoint_and_green_merge_without_orchestration_state(
        signed_shelf: tuple[Path, str], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    checkout, authority_rel = signed_shelf
    private_home = tmp_path / "empty-private-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(private_home))
    before = _home_snapshot(private_home)
    control_before = _byte_snapshot(checkout / ".taskplane")
    mutation_calls: list[str] = []

    def forbid_mutation(*args: object, **kwargs: object) -> None:
        mutation_calls.append("called")
        raise AssertionError("pickup called a private orchestration mutation")

    for owner, name in (
        (storage, "register_task_worktree"),
        (storage, "write_workspace_locator"),
        (storage, "claim_stage_execution_root_for_run"),
        (storage, "bind_worker_locator"),
        (contract_engine, "activate"),
        (contract_engine, "atomic_write_json"),
    ):
        monkeypatch.setattr(owner, name, forbid_mutation)

    exit_code = tp.main([
        "pickup", authority_rel, "--workspace", str(checkout),
    ])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "integrated"
    assert result["trace"] == [
        "pickup.preflight.authority",
        "pickup.preflight.checkout",
        "pickup.micro_plan.ready",
        "pickup.build_c.assigned",
        "pickup.checkpoint.started",
        "pickup.checkpoint.terminal",
        "pickup.integration.outcome",
        "pickup.storage.audit",
    ]
    assert result["checkpoint"]["producer"] == \
        "taskplane.checkpoint-engine/v1"
    assert result["checkpoint"]["verdict"] == "green"
    assert result["integration"]["status"] == "integrated"
    assert result["storage_audit"] == {
        "run": 0, "track": 0, "claim": 0, "lease": 0, "wave": 0,
        "equivalent": 0,
    }
    assert _home_snapshot(private_home) == before
    assert _byte_snapshot(checkout / ".taskplane") == control_before
    assert mutation_calls == []


def test_severed_pickup_to_build_c_edge_fails(
        signed_shelf: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    checkout, authority_rel = signed_shelf

    # Remove the callable itself so this exercises a genuinely absent runtime
    # edge rather than replacing BUILD-C with a mock that can report success.
    monkeypatch.delattr(pickup.build_c, "run_pickup")

    exit_code = tp.main([
        "pickup", authority_rel, "--workspace", str(checkout),
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "pickup-build-c: BUILD-C entry is unavailable" in captured.err


def test_fresh_checkout_reaches_first_executing_checkpoint_under_120_seconds(
        signed_shelf: tuple[Path, str], tmp_path: Path) -> None:
    checkout, authority_rel = signed_shelf
    repository_root = Path(__file__).resolve().parents[2]
    implementation_sha = _git(repository_root, "rev-parse", "HEAD")
    implementation_tree = _git(
        repository_root, "rev-parse", f"{implementation_sha}^{{tree}}"
    )
    fresh_checkout = tmp_path / "fresh-implementation-checkout"
    subprocess.run(
        [
            "git", "clone", "--quiet", "--no-hardlinks", "--no-checkout",
            str(repository_root), str(fresh_checkout),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", implementation_sha],
        cwd=fresh_checkout,
        check=True,
    )
    assert _git(fresh_checkout, "rev-parse", "HEAD") == implementation_sha
    assert _git(fresh_checkout, "rev-parse", "HEAD^{tree}") == \
        implementation_tree

    private_home = tmp_path / "empty-unrelated-taskplane-home"
    assert not private_home.exists()
    clean_environment = {
        "PATH": os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TASKPLANE_HOME": str(private_home),
    }
    # Keep the cold child beyond Python-aware suite-runner interception while
    # rebuilding only the explicitly allowed environment before exec.
    command = [
        "/usr/bin/env", "-i",
        *(f"{name}={value}" for name, value in clean_environment.items()),
        sys.executable, "-I", str(fresh_checkout / "taskplane" / "tp.py"),
        "pickup", authority_rel, "--workspace", str(checkout),
    ]
    cli_entry = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=fresh_checkout,
        env={},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    cli_terminal = time.monotonic()

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    cold_start_seconds = result["timing"]["pickup.cold_start.seconds"]
    assert 0.0 <= cold_start_seconds < 120.0
    assert cli_terminal - cli_entry < 120.0
    assert result["trace"].index("pickup.checkpoint.started") < \
        result["trace"].index("pickup.checkpoint.terminal")
    assert not private_home.exists()
    assert _git(fresh_checkout, "status", "--porcelain=v1") == ""


def test_public_tp_pickup_cli_delegates_workspace_contract_and_renders_result(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    observed: list[tuple[str, str]] = []
    delegated = {
        "schema": "taskplane.pickup-result/v1",
        "status": "integrated",
        "trace": ["pickup.integration.outcome"],
    }

    def run(checkout: str, design_path: str) -> dict:
        observed.append((checkout, design_path))
        return delegated

    monkeypatch.setattr(pickup, "run", run)

    exit_code = tp.main([
        "pickup", "design/shelf.json", "--workspace", str(tmp_path),
    ])

    assert exit_code == 0
    assert observed == [(str(tmp_path.resolve()), "design/shelf.json")]
    assert json.loads(capsys.readouterr().out) == delegated


def test_large_focused_proof_retains_bounded_evidence_and_reaches_terminal_pickup(
        signed_shelf: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    checkout, authority_rel = signed_shelf
    authority_path = checkout / authority_rel
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    signing_authority = contract_engine._review_contract_authority(  # noqa: SLF001
        str(checkout), create=False
    )
    argv = authority["design"]["element"]["acceptance"][0]["proof"]["argv"]
    argv.insert(-1, "-s")
    design_fingerprint = hashlib.sha256(
        _canonical(authority["design"])
    ).hexdigest()
    authority["approval"]["design_fingerprint"] = design_fingerprint
    authority["approval"].pop("signature")
    authority["approval"]["signature"] = _signature(
        authority["approval"], signing_authority["secret"]
    )
    authority["engine_receipt"]["design_fingerprint"] = design_fingerprint
    authority["engine_receipt"].pop("signature")
    authority["engine_receipt"]["signature"] = _signature(
        authority["engine_receipt"], signing_authority["secret"]
    )
    authority_path.write_text(
        json.dumps(authority, indent=2) + "\n", encoding="utf-8"
    )
    _commit(checkout, "large-output shelf authority")

    observed: dict[str, dict] = {}
    validate_and_mint = checkpoint.validate_and_mint

    def capture_result(worktree: str, spec: dict, command_result: dict,
                       **kwargs: object) -> dict:
        observed["command_result"] = command_result
        return validate_and_mint(worktree, spec, command_result, **kwargs)

    monkeypatch.setattr(checkpoint, "validate_and_mint", capture_result)
    emitted_bytes = checkpoint._PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES * 2  # noqa: SLF001
    monkeypatch.setenv("TASKPLANE_PICKUP_TEST_OUTPUT_BYTES", str(emitted_bytes))

    exit_code = tp.main([
        "pickup", authority_rel, "--workspace", str(checkout),
    ])

    assert exit_code == 0
    pickup_result = json.loads(capsys.readouterr().out)
    command_result = observed["command_result"]
    event = command_result["event"]
    snapshot = command_result["snapshot"]
    artifact = event["artifact"]
    retained = event["output_delta"]
    assert pickup_result["status"] == "integrated"
    assert "pickup.checkpoint.terminal" in pickup_result["trace"]
    assert pickup_result["trace"][-2:] == [
        "pickup.integration.outcome", "pickup.storage.audit",
    ]
    assert artifact["truncated"] is True
    assert artifact["bytes"] > emitted_bytes
    assert len(retained.encode("utf-8")) <= \
        checkpoint._PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES  # noqa: SLF001
    assert retained == snapshot["output_summary"]
    assert checkpoint._OUTPUT_TRUNCATION_MARKER.decode("ascii") in retained  # noqa: SLF001
    assert checkpoint._OBSERVED_REVISION_PREFIX + _git(  # noqa: SLF001
        checkout, "rev-parse", "HEAD"
    ) in retained
    assert snapshot["output_digest"] == artifact["sha256"]
    assert hashlib.sha256(retained.encode("utf-8")).hexdigest() != \
        artifact["sha256"]
    receipt_output = pickup_result["checkpoint"]["output"]
    assert receipt_output == {
        "sha256": artifact["sha256"], "bytes": artifact["bytes"],
        "truncated": True, "redactions": 0,
    }


def test_dirty_checkout_refuses_before_build_c_without_state_or_receipt(
        signed_shelf: tuple[Path, str], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, authority_rel = signed_shelf
    private_home = tmp_path / "private-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(private_home))
    (checkout / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    trace: list[str] = []

    with pytest.raises(pickup.PickupRefusal, match="checkout-clean"):
        pickup.run(str(checkout), authority_rel, trace=trace)

    assert not any("build_c" in event or "checkpoint" in event
                   for event in trace)
    assert _home_snapshot(private_home) == []
    assert not (checkout / "exports").exists()


def test_missing_or_mismatched_engine_receipt_refuses_before_build_c_without_state_or_receipt(
        signed_shelf: tuple[Path, str], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, authority_rel = signed_shelf
    private_home = tmp_path / "private-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(private_home))
    authority_path = checkout / authority_rel
    original = json.loads(authority_path.read_text(encoding="utf-8"))
    signing_authority = contract_engine._review_contract_authority(  # noqa: SLF001
        str(checkout), create=False
    )

    for mutation in ("missing", "mismatched"):
        value = json.loads(json.dumps(original))
        if mutation == "missing":
            value.pop("engine_receipt")
        else:
            value["engine_receipt"]["source_sha"] = "0" * 40
            unsigned = dict(value["engine_receipt"])
            unsigned.pop("signature")
            value["engine_receipt"]["signature"] = _signature(
                unsigned, signing_authority["secret"]
            )
        authority_path.write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )
        _commit(checkout, f"{mutation} engine receipt")
        trace: list[str] = []

        with pytest.raises(pickup.PickupRefusal, match="engine-receipt"):
            pickup.run(str(checkout), authority_rel, trace=trace)

        assert not any("build_c" in event or "checkpoint" in event
                       for event in trace)
        assert _home_snapshot(private_home) == []
        assert not (checkout / "exports").exists()


def test_tampered_or_unsigned_contract_refuses_before_build_c_without_state_or_receipt(
        signed_shelf: tuple[Path, str], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, authority_rel = signed_shelf
    private_home = tmp_path / "private-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(private_home))
    authority_path = checkout / authority_rel
    original = json.loads(authority_path.read_text(encoding="utf-8"))

    for mutation in ("tampered", "unsigned"):
        value = json.loads(json.dumps(original))
        if mutation == "tampered":
            value["design"]["element"]["id"] = "tampered"
        else:
            value["approval"].pop("signature")
        authority_path.write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )
        _commit(checkout, f"{mutation} shelf contract")
        trace: list[str] = []

        with pytest.raises(pickup.PickupRefusal, match="approved-design"):
            pickup.run(str(checkout), authority_rel, trace=trace)

        assert not any("build_c" in event or "checkpoint" in event
                       for event in trace)
        assert _home_snapshot(private_home) == []
        assert not (checkout / "exports").exists()
