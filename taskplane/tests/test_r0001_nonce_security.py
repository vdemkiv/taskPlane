"""Local nonce/effect boundary proofs; these do not claim native host success."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest

from taskplane import producer_observation as producer
from taskplane.delivery_ports import FakeClock, LocatorEvidenceStore


def _bindings(phase: str = "build", role: str = "owner") -> dict[str, str | float]:
    return {
        "run_id": "run-1", "phase_id": phase, "attempt_id": f"{role}-1",
        "operation_id": f"{phase}-{role}-operation", "candidate_fingerprint": "a" * 64,
        "definition_set_fingerprint": "b" * 64,
        "phase_definition_fingerprint": "c" * 64,
        "sealed_package_fingerprint": "d" * 64, "knowledge_fingerprint": "e" * 64,
        "authority_fingerprint": "f" * 64, "host_kind": "codex",
        "host_version": "test-local-boundary", "deadline": 200.0,
    }


def _source(tmp_path: Path) -> producer.AttemptNonceSource:
    store = LocatorEvidenceStore(tmp_path, "repository", "run-1")
    source = producer.AttemptNonceSource(
        store, key=b"k" * 32, clock=FakeClock(wall_time=100.0))
    source.activate_key()
    return source


def _reopen(source: producer.AttemptNonceSource) -> producer.AttemptNonceSource:
    store = LocatorEvidenceStore(source.store.caller_root,
                                 source.store.repository_fingerprint,
                                 source.store.run_namespace)
    return producer.AttemptNonceSource(
        store, key=b"k" * 32, clock=FakeClock(wall_time=100.0))


def test_nonce_uses_os_csprng_with_at_least_256_bits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    requested: list[int] = []
    real_token_bytes = producer.secrets.token_bytes

    def observe_entropy(size: int) -> bytes:
        requested.append(size)
        return real_token_bytes(size)

    monkeypatch.setattr(producer.secrets, "token_bytes", observe_entropy)
    binding = _bindings()
    issued = source.issue(binding)
    assert requested == [32]
    assert len(issued.secret) == 32
    assert issued.receipt["nonce_digest"] == hashlib.sha256(issued.secret).hexdigest()
    assert issued.secret.hex() not in json.dumps(issued.receipt)
    assert issued.secret.hex() not in repr(issued)
    assert _reopen(source).issue(binding) == issued
    assert requested == [32], "exact replay must not generate a fresh nonce"
    launched: list[str] = []
    source.dispatch(issued, binding, lambda: launched.append("launched"))
    assert launched == ["launched"]
    assert source.effect_state(binding) == "dispatch_uncertain"


@pytest.mark.parametrize("boundary", ["issuance", "dispatch", "effect"])
@pytest.mark.parametrize("severance", ["disabled_key", "missing_nonce", "changed_nonce"])
def test_emergency_key_disable_blocks_new_issuance_dispatch_and_effects(
    tmp_path: Path, boundary: str, severance: str,
) -> None:
    source = _source(tmp_path)
    binding = _bindings()
    issued = source.issue(binding)
    if boundary == "effect":
        source.dispatch(issued, binding, lambda: None)
        source.reconcile(issued, binding, lambda operation: "observed")
    if severance == "disabled_key":
        source.disable_key()
        issued_for_consumer = issued
    elif severance == "missing_nonce":
        issued_for_consumer = replace(issued, secret=b"")
    else:
        issued_for_consumer = replace(issued, secret=b"x" * 32)
    calls: list[str] = []
    current = _reopen(source)
    if boundary == "issuance" and severance != "disabled_key":
        # Sever the actual producer result at its verification consumer.
        with pytest.raises(producer.ProducerObservationError, match="nonce"):
            current.validate(issued_for_consumer, binding)
    else:
        with pytest.raises(producer.ProducerObservationError):
            if boundary == "issuance":
                current.issue({**binding, "operation_id": "new-operation"})
            elif boundary == "dispatch":
                current.dispatch(issued_for_consumer, binding, lambda: calls.append("dispatch"))
            else:
                current.effect(issued_for_consumer, binding, lambda: calls.append("effect"))
    assert calls == []
    if severance == "disabled_key":
        with pytest.raises(producer.ProducerObservationError, match="disabled"):
            current.activate_key()


@pytest.mark.parametrize("phase", ["design", "build", "evaluate", "em"])
@pytest.mark.parametrize("role", ["owner", "reviewer"])
@pytest.mark.parametrize("fault", ["before_persist", "after_persist", "external_effect"])
@pytest.mark.parametrize("boundary", ["dispatch", "effect"])
def test_no_nonce_reissue_while_effect_state_is_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    phase: str, role: str, fault: str, boundary: str,
) -> None:
    source = _source(tmp_path)
    binding = _bindings(phase, role)
    issued = source.issue(binding)
    if boundary == "effect":
        source.dispatch(issued, binding, lambda: None)
        source.reconcile(issued, binding, lambda operation: "observed")
    launches: list[str] = []
    original_write = source.store._write_atomic

    def interrupted_write(path: Path, raw: bytes) -> None:
        if fault == "before_persist":
            raise OSError("injected persistence interruption")
        original_write(path, raw)
        raise OSError("injected acknowledgement interruption")

    def launch() -> None:
        launches.append("effect")
        raise OSError("injected host response loss")

    with monkeypatch.context() as patch:
        if fault != "external_effect":
            patch.setattr(source.store, "_write_atomic", interrupted_write)
        with pytest.raises(OSError):
            if boundary == "dispatch":
                source.dispatch(issued, binding, launch)
            else:
                source.effect(issued, binding, launch)
    current = _reopen(source)
    assert launches == (["effect"] if fault == "external_effect" else [])
    if fault == "before_persist":
        assert current.effect_state(binding) == ("issued" if boundary == "dispatch" else "dispatched")
    else:
        with pytest.raises(producer.ProducerObservationError, match="uncertain"):
            current.issue(binding)
        with pytest.raises(producer.ProducerObservationError, match="uncertain"):
            current.dispatch(issued, binding, launch)
        with pytest.raises(producer.ProducerObservationError, match="reconciliation unavailable"):
            current.reconcile(issued, binding, None)
        assert current.effect_state(binding) == f"{boundary}_uncertain"
        current.reconcile(issued, binding, lambda operation: "effect_free")
    if boundary == "dispatch":
        assert current.issue(binding) == issued
    else:
        assert current.effect_state(binding) == "dispatched"
        assert current.validate(issued, binding) == issued.receipt
    with pytest.raises(producer.ProducerObservationError, match="changed"):
        current.issue({**binding, "sealed_package_fingerprint": "0" * 64})


@pytest.mark.parametrize("state", ["observed", "effect_free", "uncertain"])
def test_effect_reconciliation_preserves_truth(
    tmp_path: Path, state: Literal["observed", "effect_free", "uncertain"],
) -> None:
    source = _source(tmp_path)
    binding = _bindings()
    issued = source.issue(binding)
    source.dispatch(issued, binding, lambda: None)
    source.reconcile(issued, binding, lambda operation: "observed")
    calls: list[str] = []
    source.effect(issued, binding, lambda: calls.append("effect"))
    assert calls == ["effect"]
    current = _reopen(source)
    with pytest.raises(producer.ProducerObservationError, match="uncertain"):
        current.issue(binding)
    current.reconcile(issued, binding, lambda operation: state)
    assert current.effect_state(binding) == {
        "observed": "effect_observed", "effect_free": "dispatched",
        "uncertain": "effect_uncertain",
    }[state]


def test_key_material_and_private_nonce_state_are_controlled(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(producer.ProducerObservationError, match="256"):
        producer.AttemptNonceSource(source.store, key=b"short")
    foreign = producer.AttemptNonceSource(source.store, key=b"z" * 32)
    with pytest.raises(producer.ProducerObservationError, match="inactive"):
        foreign.issue(_bindings())
    issued = source.issue(_bindings())
    path = source.store.path / "producer_observation" / "nonce-state.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert (b"k" * 32).hex() not in path.read_text()
    assert source.validate(issued, _bindings()) == issued.receipt


def test_issuance_acknowledgement_loss_replays_durable_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    original_write = source.store._write_atomic
    durable_secret: list[bytes] = []

    def lose_acknowledgement(path: Path, raw: bytes) -> None:
        original_write(path, raw)
        durable_secret.append(bytes.fromhex(
            json.loads(raw)["attempts"]["build-owner-operation"]["secret"]))
        raise OSError("injected acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(source.store, "_write_atomic", lose_acknowledgement)
        with pytest.raises(OSError):
            source.issue(_bindings())
    assert _reopen(source).issue(_bindings()).secret == durable_secret[0]


@pytest.mark.parametrize("field", ["signature", "nonce_digest"])
def test_severed_signed_receipt_cannot_dispatch(tmp_path: Path, field: str) -> None:
    source = _source(tmp_path)
    binding = _bindings()
    issued = source.issue(binding)
    altered = replace(issued, receipt={**issued.receipt, field: "0" * 64})
    with pytest.raises(producer.ProducerObservationError, match="signature"):
        source.dispatch(altered, binding, lambda: pytest.fail("severed receipt launched"))


def test_disabled_key_cannot_be_bypassed_by_a_new_key(tmp_path: Path) -> None:
    source = _source(tmp_path)
    issued = source.issue(_bindings())
    source.disable_key()
    replacement = producer.AttemptNonceSource(
        source.store, key=b"z" * 32, clock=FakeClock(wall_time=100.0))
    replacement.activate_key()
    with pytest.raises(producer.ProducerObservationError, match="key"):
        replacement.dispatch(issued, _bindings(), lambda: pytest.fail("old key launched"))
    with pytest.raises(producer.ProducerObservationError, match="key"):
        replacement.issue(_bindings())
    fresh = {**_bindings(), "operation_id": "fresh-operation", "attempt_id": "owner-2"}
    assert replacement.issue(fresh).receipt["key_id"] != issued.receipt["key_id"]


@pytest.mark.parametrize("field", list(_bindings()))
def test_nonce_rejects_each_changed_binding(tmp_path: Path, field: str) -> None:
    source = _source(tmp_path)
    bindings = _bindings()
    issued = source.issue(bindings)
    changed = {**bindings, field: 201.0 if field == "deadline" else "foreign"}
    with pytest.raises(producer.ProducerObservationError):
        source.dispatch(issued, changed, lambda: pytest.fail("changed binding launched"))
