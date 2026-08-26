from concurrent.futures import ThreadPoolExecutor

import pytest

from taskplane.delivery_ports import (
    EVIDENCE_FAULT_SEAMS,
    EnumeratingFaultInjector,
    FakeClock,
    InjectedFault,
    RecordedHostActionCapabilitySource,
    SandboxEvidenceStore,
)
from taskplane.review_authority import (
    ReviewAuthorityError,
    project_kernel_lifecycle,
    rebind,
    rebind_request_digest,
    reconcile,
)


CONTRACT_FINGERPRINT = "c" * 64
PRIOR_BINDING = {"run_id": "review-run", "routing": "old"}
REPLACEMENT_BINDING = {"run_id": "review-run", "routing": "replacement"}


def _unstarted():
    return project_kernel_lifecycle()


def _capability(source, *, sequence=1, actor="human:operator", reason="repair binding"):
    digest = rebind_request_digest(
        run_id="delivery-run",
        kernel_id="kernel-1",
        stage="Evaluate",
        prior_binding=PRIOR_BINDING,
        replacement_binding=REPLACEMENT_BINDING,
        lifecycle=_unstarted(),
        human_actor=actor,
        reason=reason,
        host_session_id="session-1",
        host_turn_id="turn-1",
        host_sequence=sequence,
        contract_fingerprint=CONTRACT_FINGERPRINT,
    )
    return source.issue(
        capability_id=f"cap-{sequence}",
        purpose="review_rebind",
        sequence=sequence,
        host_session_id="session-1",
        host_turn_id="turn-1",
        run_id="delivery-run",
        kernel_id="kernel-1",
        task_id=None,
        stage="Evaluate",
        request_or_output_digest=digest,
        contract_fingerprint=CONTRACT_FINGERPRINT,
        issued_at=10.0,
        expires_at=20.0,
        nonce=f"nonce-{sequence}".ljust(32, "0"),
    )


def _rebind(store, source, handle, *, lifecycle=None, predecessor_digest=None):
    return rebind(
        run_id="delivery-run",
        kernel_id="kernel-1",
        stage="Evaluate",
        prior_binding=PRIOR_BINDING,
        replacement_binding=REPLACEMENT_BINDING,
        lifecycle=lifecycle or _unstarted(),
        human_actor="human:operator",
        reason="repair binding",
        host_session_id="session-1",
        host_turn_id="turn-1",
        host_sequence=1,
        contract_fingerprint=CONTRACT_FINGERPRINT,
        capability_handle=handle,
        capability_source=source,
        evidence_store=store,
        clock=FakeClock(wall_time=11.0),
        predecessor_digest=predecessor_digest,
    )


def test_human_override_rebinds_only_unstarted_kernel(tmp_path):
    store = SandboxEvidenceStore(tmp_path, "repo", "run")
    source = RecordedHostActionCapabilitySource()
    receipt = _rebind(store, source, _capability(source))

    assert receipt["schema"] == "taskplane.review-kernel-override/v1"
    assert (
        receipt["prior_binding_fingerprint"]
        != receipt["replacement_binding_fingerprint"]
    )
    assert receipt["zero_start_evidence"]["unstarted"] is True
    assert receipt["human_authority_receipt"]["actor"] == "human:operator"
    assert (
        receipt["human_authority_receipt"]["cryptographic_authenticity_claimed"]
        is False
    )

    source = RecordedHostActionCapabilitySource()
    with pytest.raises(ReviewAuthorityError, match="human actor"):
        rebind_request_digest(
            run_id="delivery-run",
            kernel_id="kernel-1",
            stage="Evaluate",
            prior_binding=PRIOR_BINDING,
            replacement_binding=REPLACEMENT_BINDING,
            lifecycle=_unstarted(),
            human_actor="agent:worker",
            reason="repair binding",
            host_session_id="session-1",
            host_turn_id="turn-1",
            host_sequence=2,
            contract_fingerprint=CONTRACT_FINGERPRINT,
        )


@pytest.mark.parametrize(
    "lifecycle",
    [
        project_kernel_lifecycle(slot_starts=["slot-1"]),
        project_kernel_lifecycle(producer_assignments=["slot-1"]),
        project_kernel_lifecycle(write_observations=["slot-1"]),
        project_kernel_lifecycle(collection_reservations=["slot-1"]),
        project_kernel_lifecycle(revision=1),
    ],
)
def test_started_slot_rebind_is_immutable(tmp_path, lifecycle):
    store = SandboxEvidenceStore(tmp_path, "repo", "run")
    source = RecordedHostActionCapabilitySource()

    with pytest.raises(ReviewAuthorityError, match="immutable"):
        _rebind(store, source, _capability(source), lifecycle=lifecycle)

    assert reconcile(store) == ()


def test_rebind_receipt_is_append_only_and_attributed(tmp_path):
    store = SandboxEvidenceStore(tmp_path, "repo", "run")
    source = RecordedHostActionCapabilitySource()
    first = _rebind(store, source, _capability(source))
    first_path = next((store.path / "review_rebind" / "receipts").glob("*.json"))
    first_bytes = first_path.read_bytes()

    second_prior = REPLACEMENT_BINDING
    second_replacement = {"run_id": "review-run", "routing": "final"}
    digest = rebind_request_digest(
        run_id="delivery-run",
        kernel_id="kernel-1",
        stage="Evaluate",
        prior_binding=second_prior,
        replacement_binding=second_replacement,
        lifecycle=_unstarted(),
        human_actor="human:operator",
        reason="final repair",
        host_session_id="session-1",
        host_turn_id="turn-2",
        host_sequence=2,
        contract_fingerprint=CONTRACT_FINGERPRINT,
    )
    handle = source.issue(
        capability_id="cap-2",
        purpose="review_rebind",
        sequence=2,
        host_session_id="session-1",
        host_turn_id="turn-2",
        run_id="delivery-run",
        kernel_id="kernel-1",
        task_id=None,
        stage="Evaluate",
        request_or_output_digest=digest,
        contract_fingerprint=CONTRACT_FINGERPRINT,
        issued_at=10.0,
        expires_at=20.0,
        nonce="nonce-2".ljust(32, "0"),
    )
    second = rebind(
        run_id="delivery-run",
        kernel_id="kernel-1",
        stage="Evaluate",
        prior_binding=second_prior,
        replacement_binding=second_replacement,
        lifecycle=_unstarted(),
        human_actor="human:operator",
        reason="final repair",
        host_session_id="session-1",
        host_turn_id="turn-2",
        host_sequence=2,
        contract_fingerprint=CONTRACT_FINGERPRINT,
        capability_handle=handle,
        capability_source=source,
        evidence_store=store,
        clock=FakeClock(11.0),
        predecessor_digest=first["fingerprint"],
    )

    assert first_path.read_bytes() == first_bytes
    assert len(list((store.path / "review_rebind" / "receipts").glob("*.json"))) == 2
    assert second["predecessor_digest"] == first["fingerprint"]
    assert [row["fingerprint"] for row in reconcile(store)] == [
        first["fingerprint"],
        second["fingerprint"],
    ]
    assert second["human_authority_receipt"]["actor"] == "human:operator"


@pytest.mark.parametrize("seam", EVIDENCE_FAULT_SEAMS)
def test_override_publication_recovers_each_atomic_fault_without_fork(tmp_path, seam):
    injector = EnumeratingFaultInjector(seam)
    store = SandboxEvidenceStore(tmp_path, "repo", "run", fault_injector=injector)
    source = RecordedHostActionCapabilitySource()
    handle = _capability(source)

    try:
        receipt = _rebind(store, source, handle)
    except InjectedFault:
        try:
            recovered = reconcile(store)
        except InjectedFault:
            recovered = reconcile(store)
        if recovered:
            receipt = recovered[-1]
        else:
            receipt = _rebind(store, source, handle)

    chain = reconcile(store)
    assert chain == (receipt,)
    assert len(list((store.path / "review_rebind" / "receipts").glob("*.json"))) == 1
    assert seam in injector.visited


def test_concurrent_override_cas_allows_one_successor(tmp_path):
    store = SandboxEvidenceStore(tmp_path, "repo", "run")

    def attempt(number):
        source = RecordedHostActionCapabilitySource()
        try:
            return ("committed", _rebind(store, source, _capability(source)))
        except ReviewAuthorityError as exc:
            return ("refused", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (1, 2)))

    assert [status for status, _ in outcomes].count("committed") == 1
    assert [status for status, _ in outcomes].count("refused") == 1
    assert "predecessor CAS mismatch" in next(
        detail for status, detail in outcomes if status == "refused"
    )
    assert len(reconcile(store)) == 1
