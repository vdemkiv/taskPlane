"""Focused H-07/H-08 durable journal and observation proofs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import run_store, stage_entities, storage
from taskplane.delivery_ports import (
    DeliveryPortError,
    EnumeratingFaultInjector,
    FakeClock,
    InjectedFault,
    RecordedHostActionCapabilitySource,
    RecordedProducerEventSource,
    SandboxEvidenceStore,
)
from taskplane.producer_observation import observe_submission


RUN_ID = "run-h1-journal"


def _store(tmp_path: Path) -> tuple[run_store.RunStore, dict]:
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        storage.identity_from_remote("https://github.com/example/project.git"),
        run_id=RUN_ID,
        checkout=str(tmp_path / "checkout"),
        host={"kind": "codex", "session_id": "thread-1"},
        target={"kind": "workspace"},
    )
    return store, manifest


def _stage_mutation(_current: dict) -> dict:
    return {
        "changes": {
            "stage_heads": {},
            "lineage": [],
            "active_stage_projection":
                stage_entities.active_stage_projection({}),
        },
        "receipt": {
            "operation": "rebuild_active_stage_projection",
            "stage_ids": [],
            "result": {"marker": "committed"},
        },
    }


@pytest.mark.parametrize("event_was_durable", [False, True])
def test_h07_stage_and_journal_reconcile_atomically(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        event_was_durable: bool) -> None:
    store, initial = _store(tmp_path)
    original_append = store._append_journal

    def fail_after_manifest(_run_id: str, event: dict) -> None:
        if event.get("event") == "stage_operation_committed":
            if event_was_durable:
                original_append(_run_id, event)
            raise OSError("injected journal failure")
        original_append(_run_id, event)

    monkeypatch.setattr(store, "_append_journal", fail_after_manifest)
    with pytest.raises(OSError, match="injected journal failure"):
        store.commit_stage_operation(
            RUN_ID,
            expected_revision=initial["revision"],
            operation_id="stage-op-a",
            request_fingerprint="a" * 64,
            mutate=_stage_mutation,
            validate_authority=lambda _current: None,
        )

    committed = store.load(RUN_ID)
    monkeypatch.setattr(store, "_append_journal", original_append)

    restarted = run_store.RunStore(home=store.home)
    replay = restarted.commit_stage_operation(
        RUN_ID,
        expected_revision=initial["revision"],
        operation_id="stage-op-a",
        request_fingerprint="a" * 64,
        mutate=lambda _current: (_ for _ in ()).throw(
            AssertionError("replay repeated the stage mutation")),
    )

    rows = [json.loads(line) for line in
            Path(restarted._journal_path(RUN_ID)).read_text().splitlines()]
    matching = [row for row in rows
                if row.get("operation_id") == "stage-op-a"]
    assert replay == committed["stage_operations"]["stage-op-a"]
    assert len(matching) == 1
    assert matching[0]["revision"] == committed["revision"]
    reconciled = restarted.load(RUN_ID)
    assert reconciled["revision"] == committed["revision"]
    assert reconciled["stage_journal_outbox"]["stage-op-a"][
        "delivered"] is True


class _FailingPrepareStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def prepare(self, *_args, **_kwargs):
        self.calls.append("prepare")
        raise DeliveryPortError("injected durable intent failure")

    def commit(self, _prepared):
        self.calls.append("commit")
        raise AssertionError("commit must not run")


class _RecordingCapability(RecordedHostActionCapabilitySource):
    def __init__(self) -> None:
        super().__init__()
        self.consume_calls = 0

    def consume(self, *args, **kwargs):
        self.consume_calls += 1
        return super().consume(*args, **kwargs)


def _observation_material(source: RecordedHostActionCapabilitySource):
    raw = b'{"schema":"taskplane.evaluator-output/v1"}\n'
    event = {
        "schema": "taskplane.host-producer-event/v1",
        "event_id": "event-a",
        "host": "codex",
        "host_session_id": "session-a",
        "host_turn_id": "turn-a",
        "run_id": RUN_ID,
        "task_id": "task-a",
        "stage": "evaluate",
        "producer": "tp-evaluator",
        "output_path": ".eval/verdict.json",
        "output_bytes": len(raw),
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_schema_id": "taskplane.evaluator-output/v1",
        "output_contract_fingerprint": "b" * 64,
        "source_sha": "c" * 40,
        "observed_at": 11.0,
    }
    handle = source.issue(
        capability_id="cap-a",
        purpose="producer_observation",
        sequence=1,
        host_session_id="session-a",
        host_turn_id="turn-a",
        run_id=RUN_ID,
        kernel_id=None,
        task_id="task-a",
        stage="evaluate",
        request_or_output_digest=event["output_sha256"],
        contract_fingerprint="b" * 64,
        issued_at=10.0,
        expires_at=20.0,
    )
    kwargs = {
        "run_id": RUN_ID,
        "task_id": "task-a",
        "stage": "evaluate",
        "producer": "tp-evaluator",
        "host": "codex",
        "host_session_id": "session-a",
        "host_turn_id": "turn-a",
        "output_path": ".eval/verdict.json",
        "output_bytes": raw,
        "output_schema_id": "taskplane.evaluator-output/v1",
        "output_contract_fingerprint": "b" * 64,
        "source_sha": "c" * 40,
        "capability_handle": handle,
        "event_source": RecordedProducerEventSource([event]),
        "capability_source": source,
        "clock": FakeClock(wall_time=11.0, monotonic=1.0),
    }
    return kwargs


def test_h08_durable_intent_precedes_authority_consumption(tmp_path: Path) -> None:
    source = _RecordingCapability()
    kwargs = _observation_material(source)
    failing = _FailingPrepareStore()

    with pytest.raises(DeliveryPortError, match="durable intent"):
        observe_submission(**kwargs, evidence_store=failing)

    assert failing.calls == ["prepare"]
    assert source.consume_calls == 0

    durable = SandboxEvidenceStore(
        tmp_path, "repository-fingerprint", RUN_ID)
    receipt = observe_submission(**kwargs, evidence_store=durable)
    assert receipt["output_sha256"] == hashlib.sha256(
        kwargs["output_bytes"]).hexdigest()
    assert source.consume_calls == 1
    assert len(list((durable.path / "producer_observation" /
                     "intents").glob("*.json"))) == 1
    assert len(list((durable.path / "producer_observation" /
                     "receipts").glob("*.json"))) == 1


def test_h08_restart_reconciles_intent_after_authority_consumption(
        tmp_path: Path) -> None:
    source = _RecordingCapability()
    kwargs = _observation_material(source)
    faulted = SandboxEvidenceStore(
        tmp_path, "repository-fingerprint", RUN_ID,
        fault_injector=EnumeratingFaultInjector("after-immutable-bytes"),
    )

    with pytest.raises(InjectedFault, match="after-immutable-bytes"):
        observe_submission(**kwargs, evidence_store=faulted)

    assert source.consume_calls == 1
    assert len(list((faulted.path / "producer_observation" /
                     "intents").glob("*.json"))) == 1

    restarted = SandboxEvidenceStore(
        tmp_path, "repository-fingerprint", RUN_ID)
    recovered = restarted.reconcile("producer_observation")
    assert len(recovered) == 1
    state = (restarted.path / "producer_observation" / "STATE").read_text()
    assert state.strip()
