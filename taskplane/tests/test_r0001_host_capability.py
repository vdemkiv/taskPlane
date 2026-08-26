import pytest

from taskplane.delivery_ports import DeliveryPortError, RecordedHostActionCapabilitySource
from taskplane import loop, tp as cli
from taskplane.producer_observation import ProducerObservationError


def _source_and_handle(**overrides):
    source = RecordedHostActionCapabilitySource()
    bindings = dict(
        capability_id="cap-1", purpose="review_rebind", sequence=1,
        host_session_id="session", host_turn_id="turn", run_id="run", kernel_id="kernel",
        task_id=None, stage="Evaluate", request_or_output_digest="digest",
        contract_fingerprint="contract", issued_at=10.0, expires_at=20.0, nonce="nonce",
    )
    bindings.update(overrides)
    return source, source.issue(**bindings), bindings


def test_rebind_capability_is_single_use_and_exact_bound():
    source, handle, bindings = _source_and_handle()
    expected = {key: bindings[key] for key in ("purpose", "run_id", "kernel_id", "stage")}
    assert source.consume(handle, expected_bindings=expected, now=11.0)["capability_id"] == "cap-1"
    with pytest.raises(DeliveryPortError, match="replay"):
        source.consume(handle, expected_bindings=expected, now=11.0)


def test_producer_capability_rejects_replay_cross_run_stage_task_output():
    for key, wrong in (("run_id", "other"), ("stage", "EM"), ("task_id", "other"), ("request_or_output_digest", "other")):
        source, handle, bindings = _source_and_handle(purpose="producer_observation", task_id="task")
        expected = dict(bindings)
        expected[key] = wrong
        with pytest.raises(DeliveryPortError, match=key):
            source.consume(handle, expected_bindings=expected, now=11.0)


def test_direct_filesystem_injection_cannot_supply_host_capability(tmp_path):
    source, handle, _ = _source_and_handle()
    injected = tmp_path / "capability"
    injected.write_text(handle)
    with pytest.raises(DeliveryPortError, match="missing host-private"):
        RecordedHostActionCapabilitySource().consume(
            injected.read_text(), expected_bindings={}, now=11.0
        )


def test_capability_makes_no_actor_authenticity_claim():
    source, handle, bindings = _source_and_handle()
    capability = source.consume(handle, expected_bindings=bindings, now=11.0)
    assert capability["cryptographic_authenticity_claimed"] is False


def test_cli_host_adapter_never_accepts_a_filesystem_capability_handle(tmp_path):
    injected = tmp_path / "producer-capability"
    injected.write_text("host-private:forged")

    with pytest.raises(DeliveryPortError, match="host-private"):
        cli.require_host_private_capability(
            str(injected), RecordedHostActionCapabilitySource()
        )


def test_recorded_source_cannot_substitute_for_private_host_receipt(tmp_path):
    recorded_source = RecordedHostActionCapabilitySource()

    with pytest.raises(TypeError, match="producer_observation"):
        loop.submit(
            str(tmp_path),
            "pass",
            producer_observation=recorded_source,
        )
    with pytest.raises(ProducerObservationError, match="external host"):
        loop.bind_producer_observation(
            {"step": "evaluate", "task": "task-a"},
            recorded_source,
            output_bytes=b"{}\n",
            output_schema_id="taskplane.evaluator-output/v1",
            output_contract_fingerprint="d" * 64,
        )
