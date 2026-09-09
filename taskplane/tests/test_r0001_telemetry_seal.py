"""T08 actual producer boundaries; the host is explicitly simulated.

These checks prove mechanical receipt admission, not native readiness, Retro
activation, Engineering sign-off, or a complete finalization journey.
"""
from dataclasses import replace
import copy
import json

import pytest

from taskplane import dispatch_telemetry as telemetry
from taskplane import stage_handoff, terminal_truth
from taskplane.delivery_ports import FakeClock, content_fingerprint
from taskplane.tests.test_r0001_agent_runtime import _setup
from taskplane.tests.test_r0001_knowledge_governance import setup_owner, proposal, apply


FRESHNESS = {"candidate_sha": "a" * 40, "source_tree": "b" * 40,
             "impact_manifest_fingerprint": "c" * 64}
KEY = stage_handoff.SigningKey("telemetry-test", b"t" * 32, 0, 10000)


def sources(tmp_path, monkeypatch, *, unavailable=False, missing_terminal=False):
    facade, dispatch, calls = _setup(tmp_path)
    if missing_terminal:
        observe = facade.observe
        facade.observe = lambda identity: replace(observe(identity), terminal_identity=None)
    result = facade.run(dispatch)
    assert result["status"] == ("refused" if missing_terminal else "accepted")
    assert calls == ["launch", "observe"]
    signed = stage_handoff.sign_contract(result, key=KEY, issued_at=100,
        expires_at=1000, freshness=FRESHNESS)
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner, candidate_fingerprint=result["candidate_fingerprint"])
    knowledge = apply(owner, value, key=KEY, now=100)
    ledger = telemetry.new_ledger(run_id="run-1", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan", started_at=100)
    events = [telemetry.dispatch_event(dispatch_id="attempt-1", thread_id="simulated-worker-1",
        thread_type="worker", task_id="build", sequence=1, kind="progress", at=105),
        telemetry.dispatch_event(dispatch_id="attempt-1", thread_id="simulated-worker-1",
        thread_type="worker", task_id="build", sequence=2, kind="complete", at=110)]
    bound = dict(dispatch_id="attempt-1", thread_id="simulated-worker-1", thread_type="worker",
        task_id="build", dependencies=[], shared_owner=None, started_at=100, ended_at=110,
        wait_duration_seconds=2, correction_count=1, events=events)
    telemetry.bind_dispatch(ledger, bound)
    usage = dict(input_tokens=100, cached_input_tokens=60, uncached_input_tokens=40,
                 output_tokens=20, reasoning_tokens=5, total_tokens=130)
    if unavailable:
        # A real producer emits attributable missing usage, not invented zero.
        ledger = telemetry.new_ledger(run_id="run-1", source_sha="a" * 40,
            design_fingerprint="design", plan_fingerprint="plan", started_at=100)
        telemetry.bind_dispatch(ledger, dict(bound, events=events[:1]))
        telemetry.terminalize_unavailable(ledger, dispatch_id="attempt-1", ended_at=110,
            outcome="complete", reason="provider-usage-unavailable")
        events = ledger["bindings"][0]["events"]
    else:
        telemetry.observe_usage(ledger, dispatch_id="attempt-1", usage=usage,
            source_fingerprint="e" * 64)
        first = telemetry.finalize_usage(ledger, dispatch_id="attempt-1", ended_at=110,
            clock=FakeClock(wall_time=110))
        assert telemetry.finalize_usage(ledger, dispatch_id="attempt-1", ended_at=110,
            clock=FakeClock(wall_time=110))["receipt"] == first["receipt"]
    return telemetry.AttemptTelemetryInputs(ledger=ledger, runtime_receipt=signed,
        nonce_source=facade.nonce, nonce=dispatch.issued, nonce_bindings=dispatch.nonce_bindings,
        knowledge_proposals=(value,), knowledge_receipts=(knowledge,),
        trusted_keys={KEY.key_id: KEY}, freshness=FRESHNESS, now=120,
        event_deliveries=tuple(events + [events[-1]]))


@pytest.mark.parametrize("unavailable", [False, True], ids=["measured", "unavailable"])
def test_telemetry_receipt_precedes_seal(tmp_path, monkeypatch, unavailable):
    inputs = sources(tmp_path, monkeypatch, unavailable=unavailable)
    receipt = telemetry.produce_attempt_telemetry(inputs)
    readiness = terminal_truth.attempt_telemetry_readiness(receipt, inputs)
    assert readiness["ready"] is True
    assert readiness["telemetry_fingerprint"] == receipt["fingerprint"]
    for consumer in ("seal", "retro", "continuation"):
        assert terminal_truth.require_attempt_telemetry(readiness, receipt, inputs,
            consumer=consumer) == receipt
    assert receipt == telemetry.produce_attempt_telemetry(inputs)
    assert receipt["replay_count"] == 1
    assert receipt["correction_count"] == 1
    assert receipt["elapsed_ms"] == 10000
    assert receipt["last_progress_at"] == 105
    assert receipt["knowledge_update_admission_counts"] == {"applied": 1}
    if unavailable:
        assert receipt["usage_status"] == "unavailable"
        assert receipt["token_counts_when_available"] is None
        assert receipt["usage_source"] is None
    else:
        assert receipt["token_counts_when_available"] == dict(input_tokens=100,
            cached_input_tokens=60, uncached_input_tokens=40, output_tokens=20,
            reasoning_tokens=5, total_tokens=130)
        assert receipt["usage_source"] == "e" * 64
        assert receipt["cache_semantics"] == "input-includes-cache-read;total-includes-cache-write;reasoning-in-output"
    portable = json.dumps(receipt)
    for private in ("simulated-worker-1", "simulated-stop-1", inputs.nonce.secret.hex(),
                    str(tmp_path), str(inputs.knowledge_proposals[0]["content"])):
        assert private not in portable


@pytest.mark.parametrize("field,value", [
    ("key_id", None), ("key_id", []), ("key_id", 42), ("key_id", {}),
    ("issued_at", None), ("issued_at", []), ("issued_at", "100"),
    ("issued_at", True), ("issued_at", 120.5),
])
def test_advisory_telemetry_refuses_malformed_signing_identity(tmp_path, monkeypatch, field, value):
    inputs = replace(sources(tmp_path, monkeypatch), resource_limits_advisory=True)
    assert telemetry.produce_attempt_telemetry(inputs)["fingerprint"]
    malformed = dict(inputs.runtime_receipt, **{field: value})
    with pytest.raises(telemetry.DispatchTelemetryError, match="signing identity"):
        telemetry.produce_attempt_telemetry(replace(inputs, runtime_receipt=malformed))


@pytest.mark.parametrize("case", ["sever-dispatch-output", "missing-runtime", "missing-knowledge",
    "missing-terminal", "missing-retention", "foreign-run", "stale-candidate", "stale-tree", "stale-impact",
    "untrusted-key", "missing-nonce", "changed-usage", "secret", "prompt", "transcript",
    "private-runtime", "copied-authority", "unavailable-to-zero", "duplicate-count"])
def test_missing_telemetry_blocks_readiness(tmp_path, monkeypatch, case):
    inputs = sources(tmp_path, monkeypatch, unavailable=case == "unavailable-to-zero")
    receipt = telemetry.produce_attempt_telemetry(inputs)
    assert terminal_truth.attempt_telemetry_readiness(receipt, inputs)["ready"] is True
    if case == "sever-dispatch-output":
        receipt = None
    elif case == "missing-runtime":
        inputs = replace(inputs, runtime_receipt={})
    elif case == "missing-terminal":
        inputs = sources(tmp_path / "missing", monkeypatch, missing_terminal=True)
    elif case == "missing-knowledge":
        inputs = replace(inputs, knowledge_receipts=())
    elif case == "missing-retention":
        broken = copy.deepcopy(inputs.knowledge_receipts[0])
        broken["payload"].pop("retention_class")
        inputs = replace(inputs, knowledge_receipts=(broken,))
    elif case == "foreign-run":
        inputs = replace(inputs, nonce_bindings=dict(inputs.nonce_bindings, run_id="foreign"))
    elif case.startswith("stale-"):
        field = {"stale-candidate": "candidate_sha", "stale-tree": "source_tree",
                 "stale-impact": "impact_manifest_fingerprint"}[case]
        inputs = replace(inputs, freshness=dict(inputs.freshness, **{field: "9" * len(FRESHNESS[field])}))
    elif case == "untrusted-key":
        inputs = replace(inputs, trusted_keys={})
    elif case == "missing-nonce":
        inputs = replace(inputs, nonce=replace(inputs.nonce, secret=b""))
    elif case == "changed-usage":
        ledger = copy.deepcopy(inputs.ledger)
        ledger["bindings"][0]["usage"]["total_tokens"] += 1
        inputs = replace(inputs, ledger=ledger)
    else:
        receipt = copy.deepcopy(receipt)
        field = {"secret": "secret", "prompt": "prompt", "transcript": "transcript",
            "private-runtime": "private_runtime", "copied-authority": "authority",
            "unavailable-to-zero": "token_counts_when_available", "duplicate-count": "replay_count"}[case]
        receipt[field] = 0 if case in {"unavailable-to-zero", "duplicate-count"} else "private"
        # Rehashing portable bytes cannot repair a severed production boundary.
        receipt["fingerprint"] = content_fingerprint({k: v for k, v in receipt.items() if k != "fingerprint"})
    readiness = terminal_truth.attempt_telemetry_readiness(receipt, inputs)
    assert readiness["ready"] is False and readiness["alerts"]
    if case == "missing-terminal":
        assert readiness["alerts"][0]["state"] == "telemetry_incomplete"
    for consumer in ("seal", "retro", "continuation"):
        with pytest.raises(terminal_truth.TerminalTruthError):
            terminal_truth.require_attempt_telemetry(readiness, receipt, inputs, consumer=consumer)


@pytest.mark.parametrize("case,changes,state", [
    ("builder-timeout", {"builder_wait_seconds": 30}, "telemetry_incomplete"),
    ("seal-timeout", {"seal_wait_seconds": 61}, "seal_blocked"),
    ("cas-conflict", {"cas_conflict": True}, "budget_blocked"),
    ("orphan-reservation", {"orphan_reservation": True}, "budget_blocked"),
    ("projected-budget", {"projected_use_ratio": .91}, "budget_blocked"),
    ("stale-heartbeat", {"heartbeat_age_seconds": 21}, "lease_stale"),
    ("lease-deadline", {"lease_remaining_seconds": -1}, "lease_stale"),
    ("pending-cancel", {"cancellation_pending_seconds": 61}, "cancellation_reconcile"),
    ("uncertain-effect", {"uncertainty_seconds": 301}, "cancellation_reconcile"),
    ("sever-terminal-output", {}, "seal_blocked"),
])
def test_w20_w24_alerts_are_actionable(tmp_path, monkeypatch, case, changes, state):
    inputs = sources(tmp_path, monkeypatch)
    receipt = telemetry.produce_attempt_telemetry(inputs)
    healthy = terminal_truth.TelemetrySafety(heartbeat_interval_seconds=10)
    ready = terminal_truth.attempt_telemetry_readiness(receipt, inputs, safety=healthy)
    assert ready["ready"] is True
    # Strict 'over' thresholds do not fire at their exact boundary.
    boundary = replace(healthy, builder_wait_seconds=29, seal_wait_seconds=60,
        projected_use_ratio=.9, heartbeat_age_seconds=20, lease_remaining_seconds=0,
        cancellation_pending_seconds=60, uncertainty_seconds=300)
    assert terminal_truth.attempt_telemetry_readiness(receipt, inputs, safety=boundary)["ready"]
    safety = replace(healthy, **changes)
    blocked = terminal_truth.attempt_telemetry_readiness(receipt, inputs, safety=safety)
    if case == "sever-terminal-output":
        blocked = copy.deepcopy(ready)
        blocked.pop("telemetry_fingerprint")
    else:
        assert blocked["ready"] is False
        alert = next(row for row in blocked["alerts"] if row["state"] == state)
        assert alert["action"] == {
            "telemetry_incomplete": "hold_inspect_producer_no_seal",
            "seal_blocked": "reconcile_identity_no_continuation",
            "budget_blocked": "reconcile_operation_no_dispatch_or_double_charge",
            "lease_stale": "reconcile_effects_reclaim_only_effect_free",
            "cancellation_reconcile": "fence_preserve_reconcile_or_request_human_rescope",
        }[state]
        assert alert["operation_id"] == receipt["operation_id"]
        assert alert["recovery"]["schema"] == "taskplane.recovery-decision/v1"
    with pytest.raises(terminal_truth.TerminalTruthError, match=state):
        terminal_truth.require_attempt_telemetry(blocked, receipt, inputs,
            consumer="continuation", safety=safety)
