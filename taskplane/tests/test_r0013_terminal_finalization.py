"""Focused R-0013 AC7 terminal-finalization fault and refusal proofs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import repository, terminal_truth, wiring_closure


SHA = "a" * 40
OTHER_SHA = "b" * 40
RUN_ID = "r0013-test-run"
REQUIREMENT = "R-0013"
REPOSITORY_FP = "1" * 64
DESIGN_FP = "2" * 64
PLAN_FP = "3" * 64
GRAPH_FP = "4" * 64
USAGE_FP = "5" * 64
SUITE_FP = "6" * 64
PREDECESSOR_FP = "0" * 64
OPERATION = "finalize-r0013"


def _canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _seal(value: dict) -> dict:
    result = dict(value)
    result["fingerprint"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _real_registered_checkout(tmp_path: Path, *, repository_fingerprint=REPOSITORY_FP):
    """Create a real clean Git CAS fixture with the exact Design selectors."""
    root = tmp_path / "registered-candidate"
    root.mkdir(parents=True)
    selectors_by_path = {}
    for selector in wiring_closure.R0013_NAMED_SELECTOR_INVENTORY:
        path, function = selector.split("::", 1)
        selectors_by_path.setdefault(path, []).append(function)
    for relative, functions in selectors_by_path.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n\n".join(f"def {function}():\n    pass" for function in functions)
            + "\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "r0013-test@example.invalid"],
        cwd=root, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "R0013 Test"], cwd=root, check=True
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture: exact selector inventory"],
        cwd=root, check=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        encoding="utf-8", errors="replace",
    ).strip()
    original_gettempdir = wiring_closure.tempfile.gettempdir
    wiring_closure.tempfile.gettempdir = lambda: str(tmp_path / "not-candidate-root")
    try:
        return wiring_closure.register_candidate_checkout(
            root,
            repository_fingerprint=repository_fingerprint,
            expected_head_sha=head,
        )
    finally:
        wiring_closure.tempfile.gettempdir = original_gettempdir


@pytest.fixture(scope="module")
def registered_candidate(tmp_path_factory):
    return _real_registered_checkout(tmp_path_factory.mktemp("r0013-candidate"))


_EXECUTED_CANDIDATE_RECEIPTS = {}


def candidate_wiring_receipt(registration, *, unbroken_edge=None):
    """Mint test authority only through the public live executor."""
    cache_key = (
        str(registration.root), registration.repository_fingerprint, unbroken_edge
    )
    if cache_key in _EXECUTED_CANDIDATE_RECEIPTS:
        return _EXECUTED_CANDIDATE_RECEIPTS[cache_key]
    mutation_checkouts = {}
    original_gettempdir = wiring_closure.tempfile.gettempdir
    wiring_closure.tempfile.gettempdir = lambda: str(
        registration.root.parent / "not-a-checkout"
    )
    try:
        for edge_id, _, _, selector in \
                wiring_closure.R0013_PRODUCTION_EDGE_BINDINGS:
            sibling = registration.root.parent / f"mutation-{edge_id}"
            subprocess.run(
                [
                    "git", "worktree", "add", "--detach", str(sibling),
                    registration.full_head_sha,
                ],
                cwd=registration.root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            tracked = sibling / selector.split("::", 1)[0]
            function = selector.split("::", 1)[1]
            source = tracked.read_text(encoding="utf-8")
            clean_body = f"def {function}():\n    pass"
            severed_body = (
                clean_body + f"\n# unbroken {edge_id}"
                if edge_id == unbroken_edge
                else (
                    f"def {function}():\n"
                    f"    assert False, 'severed {edge_id}'"
                )
            )
            assert clean_body in source
            tracked.write_text(
                source.replace(clean_body, severed_body, 1),
                encoding="utf-8",
            )
            mutation_checkouts[edge_id] = \
                wiring_closure.register_edge_mutation_checkout(
                    sibling,
                    clean_registration=registration,
                    edge_id=edge_id,
                )
    finally:
        wiring_closure.tempfile.gettempdir = original_gettempdir

    ticks = iter(float(number) for number in range(1, 1000))

    receipt = wiring_closure.execute_candidate_checkout(
        registration,
        requirement_id=REQUIREMENT,
        design_fingerprint=DESIGN_FP,
        plan_fingerprint=PLAN_FP,
        mutation_checkouts=mutation_checkouts,
        clock=lambda: next(ticks),
    )
    _EXECUTED_CANDIDATE_RECEIPTS[cache_key] = receipt
    return receipt


def terminal_identity(wiring_receipt):
    return {
        "full_source_sha": wiring_receipt["checkout_identity"]["full_head_sha"],
        "terminal_status": terminal_truth.TERMINAL_STATUS,
        "requirement_id": REQUIREMENT,
        "design_fingerprint": DESIGN_FP,
        "plan_fingerprint": PLAN_FP,
        "graph_fingerprint": GRAPH_FP,
        "native_usage_fingerprint": USAGE_FP,
        "candidate_wiring_fingerprint": wiring_receipt["fingerprint"],
        "full_suite_fingerprint": SUITE_FP,
        "predecessor_fingerprint": PREDECESSOR_FP,
    }


def prepared_delivery(coordinator, registration, *, fault_at=None):
    wiring = candidate_wiring_receipt(registration)
    identity = terminal_identity(wiring)
    surfaces = {
        surface_id: terminal_truth.prepare_terminal_surface(
            surface_id, identity, {"surface": surface_id, "redacted": True}
        )
        for surface_id in terminal_truth.SURFACE_IDS
    }
    prepared = coordinator.prepare_delivery(
        run_id=RUN_ID,
        operation_id=OPERATION,
        identity=identity,
        surfaces=surfaces,
        candidate_wiring_receipt=wiring,
        fault_at=fault_at,
    )
    capability = coordinator.issue_capability(
        run_id=RUN_ID,
        full_source_sha=identity["full_source_sha"],
        design_fingerprint=DESIGN_FP,
        plan_fingerprint=PLAN_FP,
        expected_predecessor_fingerprint=PREDECESSOR_FP,
        operation_id=OPERATION,
    )
    return prepared, capability, surfaces, wiring


def finalized_delivery(tmp_path: Path, registration):
    coordinator = terminal_truth.TerminalCoordinator(
        tmp_path / "authority", exports_root=tmp_path / "exports" / "terminal" / "r0013"
    )
    prepared, capability, surfaces, wiring = prepared_delivery(coordinator, registration)
    coordinator.commit_delivery(
        capability, prepared, observed_head_sha=capability.full_source_sha,
        checkout_clean=True,
    )
    reconciliation = coordinator.reconcile_delivery(capability, prepared)
    return coordinator, prepared, capability, reconciliation, wiring


def test_finalization_refuses_each_missing_nonterminal_or_mixed_sha_surface(
    tmp_path, registered_candidate
):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    wiring = candidate_wiring_receipt(registered_candidate)
    identity = terminal_identity(wiring)
    surfaces = {
        surface_id: terminal_truth.prepare_terminal_surface(
            surface_id, identity,
            {"surface": surface_id, "redacted": True}
        )
        for surface_id in terminal_truth.SURFACE_IDS
    }
    for missing in terminal_truth.SURFACE_IDS:
        partial = dict(surfaces)
        partial.pop(missing)
        with pytest.raises(terminal_truth.TerminalTruthError, match="eight"):
            coordinator.prepare_delivery(
                run_id=RUN_ID, operation_id=OPERATION, identity=identity,
                surfaces=partial, candidate_wiring_receipt=wiring,
            )
    nonterminal = dict(identity, terminal_status="executing")
    with pytest.raises(terminal_truth.TerminalTruthError, match="not complete"):
        terminal_truth.prepare_terminal_surface("git_head", nonterminal, {})
    mixed = dict(surfaces)
    mixed_identity = dict(identity, full_source_sha=OTHER_SHA)
    mixed["run_journal"] = terminal_truth.prepare_terminal_surface(
        "run_journal", mixed_identity, {"surface": "run_journal"}
    )
    with pytest.raises(terminal_truth.TerminalTruthError, match="mixed"):
        coordinator.prepare_delivery(
            run_id=RUN_ID, operation_id=OPERATION, identity=identity,
            surfaces=mixed, candidate_wiring_receipt=wiring,
        )


def test_main_delivery_cannot_retain_executing_progress(tmp_path, registered_candidate):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    wiring = candidate_wiring_receipt(registered_candidate)
    identity = terminal_identity(wiring)
    with pytest.raises(terminal_truth.TerminalTruthError, match="executing/nonterminal"):
        terminal_truth.prepare_terminal_surface(
            "governed_progress",
            identity,
            {"run": {"lifecycle": {"state": "executing"}}},
        )
    with pytest.raises(terminal_truth.TerminalTruthError, match="private detail"):
        terminal_truth.prepare_terminal_surface(
            "exports_terminal_evidence",
            identity,
            {"redacted": True, "nested": [{"provider_response": {"token": "x"}}]},
        )
    assert not coordinator.head_path.exists()


def test_finalize_replay_is_idempotent_on_one_sha(tmp_path, registered_candidate):
    coordinator, prepared, capability, first_reconciliation, _ = finalized_delivery(
        tmp_path, registered_candidate
    )
    head_bytes = coordinator.head_path.read_bytes()
    projection_bytes = {
        surface_id: coordinator.projection_path(surface_id).read_bytes()
        for surface_id in terminal_truth.SURFACE_IDS
    }
    replay_head = coordinator.commit_delivery(
        capability, prepared, observed_head_sha=capability.full_source_sha,
        checkout_clean=True,
    )
    replay_reconciliation = coordinator.reconcile_delivery(capability, prepared)
    assert replay_head["bundle_fingerprint"] == prepared.bundle["fingerprint"]
    assert replay_reconciliation == first_reconciliation
    assert coordinator.head_path.read_bytes() == head_bytes
    assert {
        surface_id: coordinator.projection_path(surface_id).read_bytes()
        for surface_id in terminal_truth.SURFACE_IDS
    } == projection_bytes
    authority = coordinator.read_terminal_receipt()
    assert authority["bundle"]["identity"]["full_source_sha"] == \
        capability.full_source_sha
    assert set(authority["bundle"]["surface_digests"]) == set(terminal_truth.SURFACE_IDS)


def test_crash_before_terminal_cas_publishes_no_terminal_authority(
    tmp_path, registered_candidate
):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    prepared, capability, _, _ = prepared_delivery(coordinator, registered_candidate)
    with pytest.raises(terminal_truth.TerminalTruthError, match="before terminal CAS"):
        coordinator.commit_delivery(
            capability, prepared, observed_head_sha=capability.full_source_sha,
            checkout_clean=True,
            fault_at="before_cas",
        )
    assert not coordinator.head_path.exists()
    with pytest.raises(terminal_truth.TerminalTruthError, match="does not exist"):
        coordinator.read_terminal_receipt()


def test_post_cas_missing_projection_blocks_then_reconciles_byte_identically(
    tmp_path, registered_candidate
):
    coordinator, prepared, capability, first_reconciliation, _ = finalized_delivery(
        tmp_path, registered_candidate
    )
    projection = coordinator.projection_path("public_report")
    expected = projection.read_bytes()
    projection.unlink()
    with pytest.raises(terminal_truth.TerminalTruthError, match="partial"):
        coordinator.read_terminal_receipt()
    second_reconciliation = coordinator.reconcile_delivery(capability, prepared)
    assert projection.read_bytes() == expected
    assert second_reconciliation == first_reconciliation
    assert coordinator.reconcile_delivery(capability, prepared) == first_reconciliation
    assert coordinator.read_terminal_receipt()["status"] == "complete"


def test_export_file_is_terminal_truth_and_reconciles_from_immutable_bytes(
    tmp_path, registered_candidate
):
    coordinator, prepared, capability, first_reconciliation, _ = finalized_delivery(
        tmp_path, registered_candidate
    )
    export_path = coordinator.export_path(capability.full_source_sha)
    expected = prepared.surface_bytes["exports_terminal_evidence"]
    assert export_path.read_bytes() == expected
    export_path.unlink()
    with pytest.raises(terminal_truth.TerminalTruthError, match="exports/terminal/r0013"):
        coordinator.read_terminal_receipt()
    assert coordinator.reconcile_delivery(capability, prepared) == first_reconciliation
    assert export_path.read_bytes() == expected
    export_path.write_bytes(b"fabricated")
    with pytest.raises(terminal_truth.TerminalTruthError, match="exports/terminal/r0013"):
        coordinator.read_terminal_receipt()
    coordinator.reconcile_delivery(capability, prepared)
    assert export_path.read_bytes() == expected


@pytest.mark.parametrize(
    "fault_at,head_exists",
    [
        ("prepare:git_head", False),
        ("prepare:bundle", False),
        ("fsync:git_head", False),
        ("before_cas", False),
        ("after_cas", True),
        ("reconcile:git_head", True),
        ("after_reconcile", True),
    ],
)
def test_terminal_fault_matrix_covers_prepare_fsync_cas_and_reconcile_boundaries(
    tmp_path, fault_at, head_exists, registered_candidate
):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / fault_at.replace(":", "-"))
    if fault_at.startswith("prepare:"):
        with pytest.raises(terminal_truth.TerminalTruthError, match="fault injected"):
            prepared_delivery(
                coordinator, registered_candidate, fault_at=fault_at
            )
        assert not coordinator.head_path.exists()
        return
    prepared, capability, _, _ = prepared_delivery(coordinator, registered_candidate)
    if fault_at.startswith("fsync:") or fault_at in {"before_cas", "after_cas"}:
        with pytest.raises(terminal_truth.TerminalTruthError, match="fault injected"):
            coordinator.commit_delivery(
                capability, prepared, observed_head_sha=capability.full_source_sha,
                checkout_clean=True,
                fault_at=fault_at,
            )
    else:
        coordinator.commit_delivery(
            capability, prepared, observed_head_sha=capability.full_source_sha,
            checkout_clean=True,
        )
        with pytest.raises(terminal_truth.TerminalTruthError, match="fault injected"):
            coordinator.reconcile_delivery(capability, prepared, fault_at=fault_at)
    assert coordinator.head_path.exists() is head_exists
    if not head_exists:
        with pytest.raises(terminal_truth.TerminalTruthError):
            coordinator.read_terminal_receipt()
        return
    coordinator.commit_delivery(
        capability, prepared, observed_head_sha=capability.full_source_sha,
        checkout_clean=True,
    )
    coordinator.reconcile_delivery(capability, prepared)
    assert coordinator.read_terminal_receipt()["status"] == "complete"


def test_unauthorized_task_cannot_advance_terminal_head(tmp_path, registered_candidate):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    prepared, _, _, _ = prepared_delivery(coordinator, registered_candidate)
    source_sha = prepared.bundle["identity"]["full_source_sha"]
    foreign = terminal_truth.TerminalCoordinator(tmp_path / "foreign")
    foreign_capability = foreign.issue_capability(
        run_id=RUN_ID, full_source_sha=source_sha, design_fingerprint=DESIGN_FP,
        plan_fingerprint=PLAN_FP, expected_predecessor_fingerprint=PREDECESSOR_FP,
        operation_id=OPERATION,
    )
    with pytest.raises(terminal_truth.TerminalTruthError, match="capability"):
        coordinator.commit_delivery(
            foreign_capability, prepared, observed_head_sha=source_sha,
            checkout_clean=True,
        )
    assert not coordinator.head_path.exists()


def test_new_caller_cannot_self_issue_for_claimed_authority_root(
    tmp_path, registered_candidate
):
    root = tmp_path / "authority"
    coordinator = terminal_truth.TerminalCoordinator(root)
    prepared, capability, _, _ = prepared_delivery(coordinator, registered_candidate)
    intruder = terminal_truth.TerminalCoordinator(root)
    with pytest.raises(terminal_truth.TerminalTruthError, match="bound root orchestrator"):
        intruder.issue_capability(
            run_id=RUN_ID, full_source_sha=capability.full_source_sha,
            design_fingerprint=DESIGN_FP,
            plan_fingerprint=PLAN_FP,
            expected_predecessor_fingerprint=PREDECESSOR_FP,
            operation_id=OPERATION,
        )
    reopened = terminal_truth.TerminalCoordinator(
        root, orchestrator_issuer=coordinator.orchestrator_issuer
    )
    reopened.commit_delivery(
        capability, prepared, observed_head_sha=capability.full_source_sha,
        checkout_clean=True,
    )
    reopened.reconcile_delivery(capability, prepared)
    assert reopened.read_terminal_receipt()["status"] == "complete"


def test_hash_only_terminal_receipt_cannot_authorize_downstream_guard(
    tmp_path, registered_candidate
):
    coordinator, _, capability, _, _ = finalized_delivery(
        tmp_path, registered_candidate
    )
    live = coordinator.read_terminal_receipt()
    fabricated = json.loads(json.dumps(dict(live)))
    with pytest.raises(repository.RepositoryAcquisitionError, match="live coordinator-bound"):
        repository.guard_terminal_delivery(
            fabricated, action="done", current_sha=capability.full_source_sha
        )


def test_cleanup_waits_for_successful_reconciliation_receipt(
    tmp_path, registered_candidate
):
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    prepared, capability, _, _ = prepared_delivery(coordinator, registered_candidate)
    coordinator.commit_delivery(
        capability, prepared, observed_head_sha=capability.full_source_sha,
        checkout_clean=True,
    )
    cleanup_calls = []
    with pytest.raises(terminal_truth.TerminalTruthError):
        coordinator.cleanup_private_usage(
            capability, prepared, {}, lambda: cleanup_calls.append("deleted")
        )
    assert cleanup_calls == []
    reconciliation = coordinator.reconcile_delivery(capability, prepared)
    cleanup_receipt = coordinator.cleanup_private_usage(
        capability, prepared, reconciliation, lambda: cleanup_calls.append("deleted")
    )
    assert cleanup_calls == ["deleted"]
    assert cleanup_receipt["schema"] == terminal_truth.PRIVATE_USAGE_CLEANUP_SCHEMA
    assert coordinator.cleanup_receipt_path(
        prepared.bundle["fingerprint"]
    ).read_bytes() == _canonical(cleanup_receipt)
    replay = coordinator.cleanup_private_usage(
        capability, prepared, reconciliation, lambda: cleanup_calls.append("deleted-again")
    )
    assert replay == cleanup_receipt
    assert cleanup_calls == ["deleted"]


def test_sha_changing_merge_invalidates_finalization(tmp_path, registered_candidate):
    coordinator, _, capability, _, _ = finalized_delivery(
        tmp_path, registered_candidate
    )
    source_sha = capability.full_source_sha
    terminal = coordinator.read_terminal_receipt()
    assert repository.guard_terminal_delivery(
        terminal, action="merge", current_sha=source_sha, resulting_sha=source_sha
    )["status"] == "complete"
    with pytest.raises(repository.RepositoryAcquisitionError, match="SHA-changing"):
        repository.guard_terminal_delivery(
            terminal, action="merge", current_sha=source_sha, resulting_sha=OTHER_SHA
        )
    with pytest.raises(repository.RepositoryAcquisitionError, match="resulting_sha"):
        repository.guard_terminal_delivery(
            terminal, action="merge", current_sha=source_sha
        )
