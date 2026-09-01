"""Behavioral proof that terminal usage survives into the human Retro."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskplane import dispatch_telemetry, loop, retro, run_artifacts, wave_metrics
from taskplane.delivery_ports import FakeClock


FIXTURE = (Path(__file__).parent / "fixtures" / "wave-metrics" /
           "closed-run.json")
CANDIDATE = "b" * 64


def _dispatch(dispatch_id: str, thread_id: str, started: int) -> dict:
    return {
        "dispatch_id": dispatch_id, "thread_id": thread_id,
        "thread_type": "evaluator", "task_id": "task-" + dispatch_id,
        "dependencies": [], "shared_owner": None,
        "started_at": started, "ended_at": started,
        "wait_duration_seconds": 0, "correction_count": 0, "events": [],
    }


def _closed_ledger() -> tuple[dict, FakeClock]:
    clock = FakeClock(wall_time=100, monotonic=100)
    ledger = dispatch_telemetry.new_ledger(
        run_id="run-current", source_sha="a" * 40,
        design_fingerprint="design-current",
        plan_fingerprint="plan-current", started_at=0)
    rows = [
        (_dispatch("one", "thread-one", 1), {
            "input_tokens": 100, "cached_input_tokens": 40,
            "uncached_input_tokens": 60, "output_tokens": 20,
            "reasoning_tokens": 5, "total_tokens": 130,
        }),
        (_dispatch("two", "thread-two", 3), {
            "input_tokens": 50, "cached_input_tokens": 10,
            "uncached_input_tokens": 40, "output_tokens": 5,
            "reasoning_tokens": 1, "total_tokens": 55,
        }),
    ]
    for index, (dispatch, usage) in enumerate(rows, start=1):
        dispatch_telemetry.bind_dispatch(
            ledger, dispatch, usage=usage,
            source_fingerprint=("a" if index == 1 else "b") * 64)
        dispatch_telemetry.finalize_usage(
            ledger, dispatch_id=dispatch["dispatch_id"], ended_at=10,
            clock=clock, events=[{"kind": "complete", "sequence": 1}])
    return ledger, clock


def _report(wave: dict, evaluator: dict) -> dict:
    return {
        "tasks": [], "hook_denials": 0, "parallel_waves": 1,
        "findings": {"total": 0, "by_severity": {}, "by_lens": {}},
        "execution_metrics": {
            "parallelism_factor": 1,
            "longest_serial_chain": {"tasks": [], "seconds": 0}},
        "execution_metric_source": "terminal-test",
        "wave_metrics": wave, "evaluator_summary": evaluator,
        "graph_true_up": {
            "content_fingerprint": "f" * 64, "scanned_head": "a" * 40,
            "modules": 1, "edges": 0, "components": 1},
        "lessons": ["terminal evidence retained"],
    }


def test_real_terminal_ledger_renders_token_and_evaluator_truth(tmp_path):
    ledger, clock = _closed_ledger()
    evidence = json.loads(FIXTURE.read_text(encoding="utf-8"))
    receipt = wave_metrics.seal_terminal_metrics(
        evidence, dispatch_ledger=ledger, clock=clock,
        candidate_fingerprint=CANDIDATE,
        archive_upper_bound_tokens=300)
    projection = wave_metrics.consumer_projection(receipt, consumer="retro")

    usage = projection["token_usage"]
    assert {key: usage[key] for key in (
        "schema", "status", "total_tokens", "uncached_input_tokens",
        "effective_tokens", "reason",
    )} == {
        "schema": "taskplane.token-usage-summary/v1",
        "status": "available", "total_tokens": 185,
        "uncached_input_tokens": 100, "effective_tokens": 250,
        "reason": None,
    }
    assert len(usage["attempts"]) == 2
    assert {row["thread_type"] for row in usage["attempts"]} == {"evaluator"}
    assert {row["outcome"] for row in usage["attempts"]} == {"complete"}
    assert all(row["usage_status"] == "measured"
               for row in usage["attempts"])
    tasks = [{"id": "EVAL-1", "evaluation": {
        "status": "unavailable", "verdict": "non-judged",
        "reason_code": "producer_receipt_unavailable",
        "outage_identity": {"fingerprint": "e" * 64}}}]
    evaluator = retro.evaluator_summary(tasks)
    retro._write_report(
        str(tmp_path), {"goal": "truthful retro"},
        _report(projection, evaluator), [])
    rendered = (tmp_path / ".taskplane" / "retro.md").read_text(
        encoding="utf-8")

    assert "token usage status: available" in rendered
    assert "observed total tokens: 185" in rendered
    assert "observed uncached input tokens: 100" in rendered
    assert "observed effective tokens: 250" in rendered
    assert '"unavailable": 1' in rendered
    assert "producer_receipt_unavailable" in rendered
    assert "e" * 64 in rendered


def test_loop_produces_and_replays_current_terminal_metrics_without_archive():
    ledger, _clock = _closed_ledger()
    tasks = [{"id": "EVAL-1", "evaluation": {
        "status": "complete", "verdict": "pass", "reason_code": "none",
        "outage_identity": {"fingerprint": "e" * 64}}}]
    state = {
        "tasks": tasks, "dispatch_telemetry": ledger,
        "settings_digest": "c" * 64,
        "run_artifact_binding": {
            "candidate": {"fingerprint": CANDIDATE},
            "settings_digest": "c" * 64,
        },
    }

    first = loop._seal_terminal_metrics_before_retro(state)
    evidence = json.loads(json.dumps(state["wave_metrics_evidence"]))
    receipt = json.loads(json.dumps(state["wave_metrics_receipt"]))
    second = loop._seal_terminal_metrics_before_retro(state)

    assert first == second == {
        "status": "measured", "fingerprint": receipt["fingerprint"]}
    assert state["wave_metrics_evidence"] == evidence
    assert state["wave_metrics_receipt"] == receipt
    assert evidence["schema"] == \
        "taskplane.terminal-wave-metrics-evidence/v1"
    assert receipt["schema"] == \
        "taskplane.terminal-wave-metrics-receipt/v1"
    assert receipt["usage_truth"]["observed"][
        "effective_tokens"] == 250
    assert receipt["usage_truth"]["archive_upper_bound"] == {
        "status": "unavailable", "total_tokens": None,
        "relation": "upper-bound-not-billing",
        "source_digest": receipt["sources"]["token_usage"]["digest"],
    }
    assert "token_archive_upper_bound" not in receipt["metrics"]
    assert receipt["evaluator_summary"] == retro.evaluator_summary(tasks)


def test_loop_persists_attributable_unavailable_instead_of_zero():
    ledger = dispatch_telemetry.new_ledger(
        run_id="run-missing", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan", started_at=0)
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("missing", "thread-missing", 1))
    state = {
        "tasks": [], "dispatch_telemetry": ledger,
        "settings_digest": "c" * 64,
        "run_artifact_binding": {
            "candidate": {"fingerprint": CANDIDATE},
            "settings_digest": "c" * 64,
        },
    }

    result = loop._seal_terminal_metrics_before_retro(state)
    first_evidence = json.loads(json.dumps(state["wave_metrics_evidence"]))
    replay = loop._seal_terminal_metrics_before_retro(state)
    projection = retro.sealed_wave_metrics_projection(state)

    assert result["status"] == replay["status"] == "unavailable"
    assert state["wave_metrics_evidence"] == first_evidence
    assert first_evidence["schema"] == \
        "taskplane.terminal-wave-metrics-unavailable-evidence/v1"
    assert first_evidence["attempts"][0]["usage_status"] == "unavailable"
    assert projection["token_usage"]["status"] == "unavailable"
    assert projection["token_usage"]["total_tokens"] is None
    assert projection["token_usage"]["uncached_input_tokens"] is None
    assert projection["token_usage"]["effective_tokens"] is None


def test_severed_usage_refuses_sealing_and_retro_reports_unknown(tmp_path):
    clock = FakeClock(wall_time=100, monotonic=100)
    ledger = dispatch_telemetry.new_ledger(
        run_id="run-severed", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan", started_at=0)
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("severed", "thread-severed", 1))
    evidence = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with pytest.raises(
            wave_metrics.WaveMetricsError,
            match="complete host-observed usage"):
        wave_metrics.seal_terminal_metrics(
            evidence, dispatch_ledger=ledger, clock=clock,
            candidate_fingerprint=CANDIDATE,
            archive_upper_bound_tokens=300)

    attribution = dispatch_telemetry.terminal_attempt_attribution(ledger)
    projection = retro.sealed_wave_metrics_projection({
        "wave_metrics_unavailable": {
            "reason": "provider usage missing for one evaluator attempt",
            "attempts": attribution,
        }})
    assert projection["token_usage"] == {
        "schema": "taskplane.token-usage-summary/v1",
        "status": "unavailable", "total_tokens": None,
        "uncached_input_tokens": None, "effective_tokens": None,
        "attempts": attribution,
        "reason": "provider usage missing for one evaluator attempt",
    }
    retro._write_report(
        str(tmp_path), {"goal": "missing usage"},
        _report(projection, retro.evaluator_summary([])), [])
    rendered = (tmp_path / ".taskplane" / "retro.md").read_text(
        encoding="utf-8")
    assert "token usage status: unavailable" in rendered
    assert "observed total tokens: unavailable" in rendered
    assert "observed uncached input tokens: unavailable" in rendered
    assert "observed effective tokens: unavailable" in rendered
    assert "observed total tokens: 0" not in rendered


def test_cancellation_interruption_handoff_and_retry_remain_attributable():
    clock = FakeClock(wall_time=100, monotonic=100)
    ledger = dispatch_telemetry.new_ledger(
        run_id="run-lifecycle", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan", started_at=0)
    outcomes = ["cancelled", "interrupted", "handoff", "complete"]
    for index, outcome in enumerate(outcomes, start=1):
        dispatch = _dispatch(f"attempt-{index}", f"thread-{index}", index)
        dispatch["task_id"] = "task-retried"
        dispatch["correction_count"] = index - 1
        usage = {
            "input_tokens": 10, "cached_input_tokens": 2,
            "uncached_input_tokens": 8, "output_tokens": 3,
            "reasoning_tokens": 1, "total_tokens": 13,
        }
        dispatch_telemetry.bind_dispatch(
            ledger, dispatch, usage=usage,
            source_fingerprint=f"{index}" * 64)
        dispatch_telemetry.finalize_usage(
            ledger, dispatch_id=dispatch["dispatch_id"], ended_at=20 + index,
            clock=clock, events=[{"kind": outcome, "sequence": 1}])

    source = dispatch_telemetry.terminal_metrics_source(
        ledger, clock, candidate_fingerprint=CANDIDATE,
        archive_upper_bound_tokens=100)
    assert [row["outcome"] for row in source["attempts"]] == outcomes
    assert [row["correction_count"] for row in source["attempts"]] == \
        [0, 1, 2, 3]
    assert len({row["attempt_fingerprint"] for row in source["attempts"]}) == 4
    assert all(row["usage_status"] == "measured"
               for row in source["attempts"])


@pytest.mark.parametrize("lifecycle_outcome", [
    pytest.param("success", id="success"),
    pytest.param("timeout", id="timeout"),
    pytest.param("recovery", id="recovery"),
])
def test_terminal_telemetry_and_retro_publish_before_cleanup_and_retry_once(
        tmp_path, lifecycle_outcome):
    ledger, clock = _closed_ledger()
    evidence = json.loads(FIXTURE.read_text(encoding="utf-8"))
    receipt = wave_metrics.seal_terminal_metrics(
        evidence, dispatch_ledger=ledger, clock=clock,
        candidate_fingerprint=CANDIDATE,
        archive_upper_bound_tokens=300)
    projection = wave_metrics.consumer_projection(receipt, consumer="retro")
    evaluator = retro.evaluator_summary([{"id": "EVAL-1", "evaluation": {
        "status": "complete", "verdict": "pass", "reason_code": "none",
        "outage_identity": {"fingerprint": "e" * 64}}}])
    report = _report(projection, evaluator)

    artifact_root = tmp_path / "artifacts"
    binding = run_artifacts.create_binding(
        repository_id="repo-current", run_id="run-current",
        stage_id="retro", stage_instance_id="retro-current",
        candidate={"fingerprint": CANDIDATE, "head": "a" * 40},
        settings_digest="c" * 64, source_fingerprint="d" * 64)
    run_artifacts.create_manifest(artifact_root, binding=binding)

    first = retro.publish_terminal_artifacts(
        str(artifact_root), wave_receipt=receipt, report=report,
        lifecycle_outcome=lifecycle_outcome, publication_attempt=1)
    retry = retro.publish_terminal_artifacts(
        str(artifact_root), wave_receipt=receipt, report=report,
        lifecycle_outcome=lifecycle_outcome, publication_attempt=2)
    manifest = run_artifacts.load_manifest(artifact_root)
    assert first["bundle_fingerprint"] == retry["bundle_fingerprint"]
    assert first["lifecycle_outcome"] == lifecycle_outcome
    assert len(manifest["classes"]["telemetry"]["entries"]) == 1
    assert len(manifest["classes"]["retro"]["entries"]) == 1
    assert manifest["classes"]["telemetry"]["entries"][0][
        "metadata"]["artifact_role"] == "terminal-telemetry"
    assert manifest["classes"]["retro"]["entries"][0][
        "metadata"]["artifact_role"] == "terminal-retro"

    run_artifacts.publish_artifact(
        artifact_root, "cleanup", {"status": "started"})
    with pytest.raises(
            wave_metrics.WaveMetricsError, match="before cleanup"):
        retro.publish_terminal_artifacts(
            str(artifact_root), wave_receipt=receipt, report=report,
            lifecycle_outcome=lifecycle_outcome, publication_attempt=3)
