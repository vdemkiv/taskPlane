"""Exact contract for one measurable, closed delivery-wave receipt."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import wave_metrics
from taskplane import dispatch_telemetry
from taskplane import retro
from taskplane.delivery_ports import FakeClock, content_fingerprint


FIXTURE = Path(__file__).parent / "fixtures" / "wave-metrics" / "closed-run.json"


def _evidence() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_wave_receipt_covers_baselines_targets_and_guardrails():
    evidence = _evidence()
    receipt = wave_metrics.seal_wave_receipt(evidence)

    assert receipt["schema"] == "taskplane.wave-metrics-receipt/v1"
    assert len(receipt["fingerprint"]) == 64
    assert set(receipt["metrics"]) == set(wave_metrics.METRIC_DEFINITIONS)
    assert all("baseline" in metric and "target" in metric
               for metric in receipt["metrics"].values())
    assert receipt["metrics"]["suite_files"]["baseline"] == 266
    assert receipt["metrics"]["suite_cases"]["baseline"] == 4909
    assert receipt["metrics"]["suite_loc"]["baseline"] == 95601
    assert receipt["metrics"]["redundant_families_removed"]["target"] == 6
    assert receipt["metrics"]["exact_feedback_p95_seconds"]["target"] == 60
    assert receipt["metrics"]["ci_critical_path_minutes"]["baseline"] == 15
    assert receipt["metrics"]["ci_p50_minutes"]["target"] == 10
    assert receipt["metrics"]["ci_p95_minutes"]["target"] == 15
    assert receipt["metrics"]["ci_parallelism_factor"]["baseline"] == 2.59
    assert receipt["metrics"]["cleanup_leak_count"]["target"] == 0
    assert receipt["metrics"]["token_total_observed"]["baseline"] == 540_300_000
    assert receipt["metrics"]["end_to_end_wave_hours"]["baseline"] == 40.583

    assert set(receipt["sources"]) == set(wave_metrics.SOURCE_NAMES)
    assert all(source["counting"] == "non-cumulative"
               for source in receipt["sources"].values())
    assert receipt["run"]["integration_ready_at"] == \
        evidence["run"]["integration_ready_at"]
    assert receipt["usage_truth"]["billing"] == {
        "status": "unavailable", "value": None,
        "source_digest": evidence["sources"]["token_usage"]["digest"],
    }
    assert receipt["usage_truth"]["observed"]["total_tokens"] == 90_000_000
    assert receipt["usage_truth"]["archive_upper_bound"] == {
        "total_tokens": 1_292_000_000,
        "relation": "upper-bound-not-billing",
        "source_digest": evidence["sources"]["token_usage"]["digest"],
    }
    assert receipt["signoff"] == {
        "ready": True, "blocking_reasons": [], "unexplained_ceilings": []}
    assert [row["name"] for row in receipt["serializations"]] == [
        "settings-before-side-effects", "candidate-before-terminal-ci",
        "terminal-receipts-before-metrics", "cleanup-join-before-signoff"]
    assert receipt["redaction"] == {
        "paths": "omitted", "host_identity": "omitted", "raw_logs": "omitted"}

    projections = {
        consumer: wave_metrics.consumer_projection(receipt, consumer=consumer)
        for consumer in ("dashboard", "retro", "engineering", "release")
    }
    assert {row["receipt_fingerprint"] for row in projections.values()} == {
        receipt["fingerprint"]}
    assert all(row["metrics"] == receipt["metrics"]
               for row in projections.values())
    assert retro.sealed_wave_metrics_projection(
        {"wave_metrics_receipt": receipt}) == projections["retro"]

    clock = FakeClock(wall_time=100, monotonic=100)
    ledger = dispatch_telemetry.new_ledger(
        run_id="private-run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan", started_at=0)
    dispatch_telemetry.admit(ledger, {
        "dispatch_id": "private-dispatch", "thread_id": "private-thread",
        "thread_type": "worker", "task_id": "private-task",
        "dependencies": [], "shared_owner": None, "started_at": 0,
        "ended_at": 50, "wait_duration_seconds": 0,
        "correction_count": 0, "events": [],
    }, {
        "input_tokens": 100, "cached_input_tokens": 40,
        "uncached_input_tokens": 60, "output_tokens": 20,
        "reasoning_tokens": 10, "total_tokens": 130,
    }, clock)
    dispatch_source = dispatch_telemetry.closed_wave_metrics_source(
        ledger, clock, candidate_fingerprint="b" * 64,
        archive_upper_bound_tokens=300)
    assert dispatch_source["observed"] == {
        "sessions": 1, "total_tokens": 130,
        "uncached_input_tokens": 60, "elapsed_seconds": 100.0}
    assert dispatch_source["billing"] == {
        "status": "unavailable", "total_tokens": None}
    assert dispatch_source["archive_upper_bound"] == {
        "status": "available", "total_tokens": 300,
        "relation": "upper-bound-not-billing"}
    assert "private" not in json.dumps(dispatch_source)

    cumulative = _evidence()
    cumulative["sources"]["dispatch"]["counting"] = "cumulative-archive"
    with pytest.raises(wave_metrics.WaveMetricsError, match="non-cumulative"):
        wave_metrics.seal_wave_receipt(cumulative)

    mixed_candidate = _evidence()
    mixed_candidate["sources"]["ci"]["candidate_fingerprint"] = "c" * 64
    with pytest.raises(wave_metrics.WaveMetricsError, match="outside"):
        wave_metrics.seal_wave_receipt(mixed_candidate)

    billing_conflation = _evidence()
    billing_conflation["usage_truth"]["billing"]["value"] = 90_000_000
    with pytest.raises(wave_metrics.WaveMetricsError, match="billing"):
        wave_metrics.seal_wave_receipt(billing_conflation)

    leaked = _evidence()
    leaked["actuals"]["cleanup_leak_count"] = 1
    leaked["cleanup"] = {"leak_count": 1, "status": "attention"}
    assert wave_metrics.seal_wave_receipt(leaked)["signoff"]["ready"] is False

    unexplained = _evidence()
    unexplained["actuals"]["token_total_observed"] = 150_000_000
    unexplained["usage_truth"]["observed"]["total_tokens"] = 150_000_000
    unexplained["ceilings"][0]["observed"] = 150_000_000
    stopped = wave_metrics.seal_wave_receipt(unexplained)
    assert stopped["signoff"]["ready"] is False
    assert stopped["signoff"]["unexplained_ceilings"] == ["total_tokens"]

    exposed = _evidence()
    exposed["serializations"][0]["reason"] = "/private/host/user/repo"
    with pytest.raises(wave_metrics.WaveMetricsError, match="paths"):
        wave_metrics.seal_wave_receipt(exposed)

    tampered = copy.deepcopy(receipt)
    tampered["metrics"]["suite_files"]["actual"] += 1
    with pytest.raises(wave_metrics.WaveMetricsError, match="fingerprint"):
        wave_metrics.validate_wave_receipt(tampered)
    tampered_material = {key: value for key, value in tampered.items()
                         if key != "fingerprint"}
    tampered["fingerprint"] = content_fingerprint(tampered_material)
    with pytest.raises(wave_metrics.WaveMetricsError, match="semantics"):
        wave_metrics.validate_wave_receipt(tampered)
