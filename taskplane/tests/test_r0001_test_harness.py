import json

import pytest

from taskplane.delivery_ports import (
    EVIDENCE_DOMAINS,
    EVIDENCE_FAULT_SEAMS,
    FakeClock,
    InjectedFault,
    RecordedEventWaiter,
    SandboxEvidenceStore,
)


def test_fake_clock_and_recorded_waiter_require_no_real_sleep():
    clock = FakeClock(wall_time=100.0, monotonic=20.0)
    waiter = RecordedEventWaiter([[{"member": "a", "kind": "complete"}]], clock)

    events = waiter.wait(
        {"mode": "event", "timeout_seconds": 1800, "scheduled_polling": False}, ["a"]
    )

    assert events == ({"member": "a", "kind": "complete"},)
    assert clock.wall_time() == 100.0
    assert clock.monotonic() == 20.0
    clock.advance(2.5)
    assert (clock.wall_time(), clock.monotonic()) == (102.5, 22.5)


def test_evidence_store_parallel_namespaces_are_isolated_and_teardown_is_scoped(tmp_path):
    first = SandboxEvidenceStore(tmp_path, "repo", "run-a")
    second = SandboxEvidenceStore(tmp_path, "repo", "run-b")
    receipt = first.commit(first.prepare("telemetry", "dispatch-1", {"tokens": 7}))

    assert json.loads(receipt)["domain"] == "telemetry"
    assert not list(second.path.glob("telemetry/receipts/*.json"))
    with pytest.raises(Exception, match="token mismatch"):
        first.teardown(second.namespace_token)
    first.teardown(first.namespace_token)
    assert not first.path.exists()
    assert second.path.exists()


def test_all_domains_expose_prepare_commit_and_idempotent_recovery_fault_seams(tmp_path):
    for domain_number, domain in enumerate(sorted(EVIDENCE_DOMAINS)):
        store = SandboxEvidenceStore(tmp_path, "repo", f"clean-{domain_number}")
        prepared = store.prepare(domain, "operation", {"domain": domain})
        committed = store.commit(prepared)
        assert store.commit(prepared) == committed
        assert store.reconcile(domain) == (committed,)

    for seam_number, seam in enumerate(EVIDENCE_FAULT_SEAMS):
        from taskplane.delivery_ports import EnumeratingFaultInjector

        injector = EnumeratingFaultInjector(seam)
        store = SandboxEvidenceStore(
            tmp_path, "repo", f"fault-{seam_number}", fault_injector=injector
        )
        try:
            prepared = store.prepare("review_rebind", "operation", {"seam": seam})
            store.commit(prepared)
            store.reconcile("review_rebind")
        except InjectedFault:
            recovered = store.reconcile("review_rebind")
            assert len(recovered) == 1
            assert store.reconcile("review_rebind") == recovered
        assert seam in injector.visited
