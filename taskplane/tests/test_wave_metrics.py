"""Exact contract for one measurable, closed delivery-wave receipt."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import wave_metrics
from taskplane import dispatch_telemetry
from taskplane import retro
from taskplane import dashboard
from taskplane import audit_projection
from taskplane import release_evidence
from taskplane.delivery_ports import FakeClock, content_fingerprint


FIXTURE = Path(__file__).parent / "fixtures" / "wave-metrics" / "closed-run.json"


def _evidence() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root(*, worker_tokens: int = 0) -> tuple[dict, int]:
    return ({
        "status": "open", "conformance": "pass", "canary_eligible": True,
        "override": None, "host": {"adapter": "codex", "runtime": "native"},
        "session_pseudonym": "1" * 64, "seed_fingerprint": "2" * 64,
        "host_start_fingerprint": "3" * 64,
        "meter": {
            "turns": 2, "first_observed_input_tokens": 40_000,
            "peak_context_tokens": 45_000, "context_rent_tokens": 25_000,
            "resumed": False,
            "usage": {"total_tokens": 100_000,
                      "cached_input_tokens": 50_000},
        },
    }, worker_tokens)


def test_root_hygiene_required_fields_reject_null_and_preserve_zero_and_applicability_null():
    root, workers = _root(worker_tokens=0)
    seal = wave_metrics.finalize_root_hygiene_canary(
        root, candidate_sha="a" * 40, worker_tokens=workers)
    assert seal["totals"] == {
        "root_tokens": 100_000, "worker_tokens": 0,
        "wave_tokens": 100_000}
    assert seal["comparison"] == {
        "applicable": False, "root_share": None, "wave_tokens": None,
        "reason": "worker-usage-unavailable"}
    missing = copy.deepcopy(root)
    missing["meter"]["first_observed_input_tokens"] = None
    with pytest.raises(wave_metrics.WaveMetricsError, match="required"):
        wave_metrics.finalize_root_hygiene_canary(
            missing, candidate_sha="a" * 40, worker_tokens=0)


def test_root_worker_and_wave_totals_remain_separate_and_reconcile_in_all_consumers():
    root, workers = _root(worker_tokens=300_000)
    seal = wave_metrics.finalize_root_hygiene_canary(
        root, candidate_sha="a" * 40, worker_tokens=workers)
    assert seal["totals"] == {
        "root_tokens": 100_000, "worker_tokens": 300_000,
        "wave_tokens": 400_000}
    assert seal["comparison"]["root_share"] == .25
    retro_view = retro.sealed_root_hygiene_projection(
        {"root_hygiene_receipt": seal})
    dashboard_view = dashboard.root_hygiene_projection(seal)
    release_view = release_evidence.root_hygiene_projection(seal)
    audit_view = audit_projection.root_hygiene_projection(seal)
    views = [retro_view, dashboard_view, release_view, audit_view]
    assert all(view["totals"] == seal["totals"] for view in views)
    assert all(view["receipt_fingerprint"] == seal["fingerprint"]
               for view in views)


def test_wave_receipt_covers_baselines_targets_and_guardrails():
    evidence = _evidence()
    receipt = wave_metrics.seal_wave_receipt(evidence)

    assert receipt["schema"] == "taskplane.wave-metrics-receipt/v1"
    assert len(receipt["fingerprint"]) == 64
    assert set(receipt["metrics"]) == set(wave_metrics.METRIC_DEFINITIONS)
    assert all("baseline" in metric and "target" in metric
               for metric in receipt["metrics"].values())
    assert receipt["metrics"]["suite_files"]["baseline"] == 229
    assert receipt["metrics"]["suite_files"]["target"] is None
    assert receipt["metrics"]["suite_files"]["passed"] is None
    assert receipt["metrics"]["suite_cases"]["baseline"] == 4059
    assert receipt["metrics"]["suite_cases"]["target"] is None
    assert receipt["metrics"]["suite_loc"]["baseline"] == 84104
    assert receipt["metrics"]["redundant_families_removed"]["target"] is None
    assert receipt["metrics"]["exact_feedback_p95_seconds"]["baseline"] == 4.01
    assert receipt["metrics"]["exact_feedback_p95_seconds"]["target"] == 60
    assert receipt["metrics"]["ci_critical_path_minutes"]["baseline"] == 13.167
    assert receipt["metrics"]["ci_p50_minutes"]["target"] == 10
    assert receipt["metrics"]["ci_p95_minutes"]["target"] == 15
    assert receipt["metrics"]["ci_parallelism_factor"]["baseline"] == 2.11
    assert receipt["metrics"]["cleanup_leak_count"]["target"] == 0
    assert receipt["metrics"]["token_total_observed"]["baseline"] is None
    assert receipt["metrics"]["token_total_observed"]["target"] is None
    assert receipt["metrics"]["end_to_end_wave_hours"]["baseline"] == 24.969
    assert receipt["metrics"]["plan_returns"]["baseline"] == 21
    assert receipt["metrics"]["plan_returns"]["target"] == 2
    assert receipt["metrics"]["plan_returns"]["source_digest"] == \
        evidence["sources"]["dispatch"]["digest"]

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
    missing_available_billing = _evidence()
    missing_available_billing["usage_truth"]["billing"]["status"] = "available"
    with pytest.raises(wave_metrics.WaveMetricsError, match="available iff"):
        wave_metrics.seal_wave_receipt(missing_available_billing)
    available_billing = _evidence()
    available_billing["usage_truth"]["billing"] = {
        "status": "available", "value": 12_345,
        "source_digest": evidence["sources"]["token_usage"]["digest"],
    }
    assert wave_metrics.seal_wave_receipt(
        available_billing)["usage_truth"]["billing"]["value"] == 12_345

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
    tampered["metrics"]["cleanup_leak_count"]["actual"] += 1
    with pytest.raises(wave_metrics.WaveMetricsError, match="fingerprint"):
        wave_metrics.validate_wave_receipt(tampered)
    tampered_material = {key: value for key, value in tampered.items()
                         if key != "fingerprint"}
    tampered["fingerprint"] = content_fingerprint(tampered_material)
    with pytest.raises(wave_metrics.WaveMetricsError, match="semantics"):
        wave_metrics.validate_wave_receipt(tampered)
