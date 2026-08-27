"""Focused R-0013 real-checkout registration and consumer refusal proofs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import checkpoint, release_evidence, terminal_truth, wiring_closure
from taskplane.tests.test_r0013_terminal_finalization import (
    DESIGN_FP,
    PLAN_FP,
    REPOSITORY_FP,
    REQUIREMENT,
    SHA,
    _real_registered_checkout,
    candidate_wiring_receipt,
    finalized_delivery,
    prepared_delivery,
    registered_candidate,
)


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_receipt_refuses_non_git_temp_or_head_mismatch(tmp_path):
    with pytest.raises(wiring_closure.WiringClosureError, match="temporary"):
        wiring_closure.register_candidate_checkout(
            tmp_path,
            repository_fingerprint=REPOSITORY_FP,
            expected_head_sha=SHA,
        )
    with pytest.raises(wiring_closure.WiringClosureError, match="HEAD"):
        wiring_closure.register_candidate_checkout(
            ROOT,
            repository_fingerprint=REPOSITORY_FP,
            expected_head_sha="f" * 40,
        )


def test_public_edge_mutation_registrar_returns_live_dirty_sibling(tmp_path):
    clean = _real_registered_checkout(tmp_path)
    sibling = tmp_path / "edge-sibling"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(sibling), clean.full_head_sha],
        cwd=clean.root,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_relative = \
        wiring_closure.R0013_NAMED_SELECTOR_INVENTORY[0].split("::", 1)[0]
    tracked = sibling / tracked_relative
    tracked.write_text(
        tracked.read_text(encoding="utf-8") + "# sever E01\n",
        encoding="utf-8",
    )
    expected_diff = subprocess.check_output(
        ["git", "diff", "--binary", "--", "."], cwd=sibling, text=True
    ).strip()
    original_gettempdir = wiring_closure.tempfile.gettempdir
    wiring_closure.tempfile.gettempdir = lambda: str(tmp_path / "not-a-checkout")
    try:
        mutation = wiring_closure.register_edge_mutation_checkout(
            sibling,
            clean_registration=clean,
            edge_id="E01",
        )
    finally:
        wiring_closure.tempfile.gettempdir = original_gettempdir
    assert isinstance(mutation, wiring_closure.RegisteredCheckout)
    assert mutation.root == sibling.resolve()
    assert mutation.clean_status == "one-edge-mutation"
    assert mutation.mutation_edge_id == "E01"
    assert mutation.mutation_diff_fingerprint == hashlib.sha256(
        expected_diff.encode("utf-8")
    ).hexdigest()
    assert mutation.git_common_dir_fingerprint == clean.git_common_dir_fingerprint
    assert mutation.full_head_sha == clean.full_head_sha
    second_relative = tuple(dict.fromkeys(
        selector.split("::", 1)[0]
        for selector in wiring_closure.R0013_NAMED_SELECTOR_INVENTORY
    ))[1]
    second = sibling / second_relative
    second.write_text(
        second.read_text(encoding="utf-8") + "# arbitrary second edge\n",
        encoding="utf-8",
    )
    mutation_set = {
        edge_id: mutation
        for edge_id in wiring_closure.EXPECTED_R0013_PRODUCTION_EDGE_IDS
    }
    ticks = iter(float(number) for number in range(1, 1000))
    with pytest.raises(wiring_closure.WiringClosureError, match="changed after registration"):
        wiring_closure.execute_candidate_checkout(
            clean,
            requirement_id=REQUIREMENT,
            design_fingerprint=DESIGN_FP,
            plan_fingerprint=PLAN_FP,
            mutation_checkouts=mutation_set,
            clock=lambda: next(ticks),
        )


def test_pinned_and_final_checkout_execute_same_named_selector_inventory(
    registered_candidate
):
    receipt = candidate_wiring_receipt(registered_candidate)
    assert tuple(
        row["exact_selector"] for row in receipt["selector_evidence"]
    ) == wiring_closure.R0013_NAMED_SELECTOR_INVENTORY
    assert tuple(row["edge_id"] for row in receipt["edge_evidence"]) == \
        wiring_closure.EXPECTED_R0013_PRODUCTION_EDGE_IDS
    wiring_closure.validate_candidate_checkout_receipt(
        receipt,
        expected_repository_fingerprint=REPOSITORY_FP,
        expected_head_sha=registered_candidate.full_head_sha,
        expected_requirement_id=REQUIREMENT,
    )


def test_each_named_edge_severed_in_real_checkout_breaks_exact_selector(tmp_path):
    registration = _real_registered_checkout(tmp_path)
    with pytest.raises(wiring_closure.WiringClosureError, match="E11"):
        candidate_wiring_receipt(
            registration, unbroken_edge="E11"
        )


def test_forged_selector_blob_or_public_receipt_clone_fails_live_git_cas(
    registered_candidate, monkeypatch
):
    valid = candidate_wiring_receipt(registered_candidate)
    forged = wiring_closure.CandidateCheckoutReceipt(
        dict(valid),
        registration=registered_candidate,
        token=wiring_closure._CANDIDATE_RECEIPT_TOKEN,
    )
    forged["selector_evidence"] = [
        dict(row) for row in forged["selector_evidence"]
    ]
    forged["selector_evidence"][0]["git_blob_oid"] = "f" * 40
    forged.pop("fingerprint")
    forged["fingerprint"] = hashlib.sha256(json.dumps(
        forged, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    with pytest.raises(wiring_closure.WiringClosureError, match="Git blob"):
        wiring_closure.validate_candidate_checkout_receipt(forged)

    clone = json.loads(json.dumps(dict(valid)))
    with pytest.raises(wiring_closure.WiringClosureError, match="live registered"):
        wiring_closure.validate_candidate_checkout_receipt(clone)

    rebound = wiring_closure.CandidateCheckoutReceipt(
        dict(valid),
        registration=registered_candidate,
        token=wiring_closure._CANDIDATE_RECEIPT_TOKEN,
    )
    rebound["edge_evidence"] = [dict(row) for row in rebound["edge_evidence"]]
    rebound["edge_evidence"][0]["producer_module_symbol"] = "foreign producer"
    rebound.pop("fingerprint")
    rebound["fingerprint"] = hashlib.sha256(json.dumps(
        rebound, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    with pytest.raises(wiring_closure.WiringClosureError, match="E01"):
        wiring_closure.validate_candidate_checkout_receipt(rebound)

    real_git = wiring_closure._git_text
    first_path = wiring_closure.R0013_NAMED_SELECTOR_INVENTORY[0].split("::", 1)[0]

    def missing_symbol(root, *args):
        if args == ("show", f"HEAD:{first_path}"):
            return "def test_another_symbol():\n    pass"
        return real_git(root, *args)

    monkeypatch.setattr(wiring_closure, "_git_text", missing_symbol)
    with pytest.raises(wiring_closure.WiringClosureError, match="selector symbol"):
        wiring_closure.validate_candidate_checkout_receipt(valid)


def test_caller_authored_evidence_cannot_mint_candidate_authority(
    registered_candidate
):
    observed = candidate_wiring_receipt(registered_candidate)
    with pytest.raises(wiring_closure.WiringClosureError, match="caller-authored"):
        wiring_closure.create_candidate_checkout_receipt(
            registered_candidate,
            requirement_id=REQUIREMENT,
            design_fingerprint=DESIGN_FP,
            plan_fingerprint=PLAN_FP,
            selector_evidence=observed["selector_evidence"],
            edge_evidence=observed["edge_evidence"],
        )


def test_public_executor_rejects_fabricated_runner_injection(
    registered_candidate
):
    def fabricated_success(*_args, **_kwargs):
        return {"returncode": 0, "stdout": "fabricated", "stderr": ""}

    with pytest.raises(TypeError, match="runner"):
        wiring_closure.execute_candidate_checkout(
            registered_candidate,
            requirement_id=REQUIREMENT,
            design_fingerprint=DESIGN_FP,
            plan_fingerprint=PLAN_FP,
            mutation_checkouts={},
            runner=fabricated_success,
        )


def test_terminal_commit_rechecks_live_candidate_after_prepare(tmp_path):
    registration = _real_registered_checkout(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    prepared, capability, _, _ = prepared_delivery(coordinator, registration)
    tracked = registration.root / \
        wiring_closure.R0013_NAMED_SELECTOR_INVENTORY[0].split("::", 1)[0]
    tracked.write_text(tracked.read_text(encoding="utf-8") + "# dirty\n", encoding="utf-8")
    with pytest.raises(terminal_truth.TerminalTruthError, match="CAS"):
        coordinator.commit_delivery(
            capability,
            prepared,
            observed_head_sha=capability.full_source_sha,
            checkout_clean=True,
        )
    assert not coordinator.head_path.exists()


def test_cut_design_wiring_validator_from_checkpoint_fails_closed(
    monkeypatch, registered_candidate
):
    def severed(*args, **kwargs):
        raise wiring_closure.WiringClosureError("Design wiring validator severed")

    monkeypatch.setattr(
        wiring_closure, "validate_candidate_checkout_receipt", severed
    )
    with pytest.raises(checkpoint.CheckpointReceiptError, match="severed"):
        checkpoint.validate_candidate_wiring_for_checkpoint(
            candidate_wiring_receipt(registered_candidate),
            repository_fingerprint=REPOSITORY_FP,
            full_source_sha=registered_candidate.full_head_sha,
            requirement_id=REQUIREMENT,
        )


def test_release_refuses_opaque_or_foreign_checkout_wiring_fingerprint(
    tmp_path, registered_candidate
):
    coordinator, _, capability, _, wiring = finalized_delivery(
        tmp_path, registered_candidate
    )
    source_sha = capability.full_source_sha
    terminal = coordinator.read_terminal_receipt()
    with pytest.raises(release_evidence.ReleaseEvidenceError, match="live registered"):
        release_evidence.validate_terminal_release_claim(
            terminal,
            wiring["fingerprint"],
            repository_fingerprint=REPOSITORY_FP,
            full_source_sha=source_sha,
            requirement_id=REQUIREMENT,
        )
    foreign_registration = _real_registered_checkout(
        tmp_path / "foreign", repository_fingerprint="e" * 64
    )
    foreign = candidate_wiring_receipt(foreign_registration)
    with pytest.raises(release_evidence.ReleaseEvidenceError, match="foreign"):
        release_evidence.validate_terminal_release_claim(
            terminal,
            foreign,
            repository_fingerprint=REPOSITORY_FP,
            full_source_sha=source_sha,
            requirement_id=REQUIREMENT,
        )
    checked = release_evidence.validate_terminal_release_claim(
        terminal,
        wiring,
        repository_fingerprint=REPOSITORY_FP,
        full_source_sha=source_sha,
        requirement_id=REQUIREMENT,
    )
    assert checked["bundle"]["identity"]["design_fingerprint"] == DESIGN_FP
    assert checked["bundle"]["identity"]["plan_fingerprint"] == PLAN_FP
