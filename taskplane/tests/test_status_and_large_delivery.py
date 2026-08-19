"""R-0001 t6: cheap status and lossless size-aware dashboard delivery."""

import json
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import host_capabilities  # noqa: E402
import loop_status  # noqa: E402
import progress  # noqa: E402
import taskplane_lite  # noqa: E402
import views  # noqa: E402


def _runtime_module():
    path = Path(ROOT) / "hooks" / "host_native_runtime.py"
    spec = importlib.util.spec_from_file_location("t6_host_native_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _model(findings=2):
    return {
        "schema": "taskplane.review-artifact-model/v1",
        "identity": {"workflow_id": "wf-1", "run_id": "run-1",
                     "target": "repo@abc", "revision": 4},
        "gate": {"status": "awaiting-human", "approval_enabled": True},
        "findings": [
            {"id": f"F-{index:04d}", "severity": "high",
             "evidence": "e" * 1024}
            for index in range(findings)
        ],
    }


def _decode_set(result):
    decoded = {}
    for kind, ref in result["artifacts"].items():
        if ref["status"] == "available":
            decoded[kind] = views.decode_dashboard_artifact(
                kind, open(ref["path"], "rb").read())
    if result.get("inline"):
        decoded["inline"] = views.decode_dashboard_artifact(
            "inline", result["inline"]["content"].encode("utf-8"))
    return decoded


def test_small_dashboard_is_inline_and_all_formats_are_semantically_equal(tmp_path):
    model = _model()

    result = views.deliver_dashboard(
        str(tmp_path), model, inline_threshold=32_000,
        html_renderer=lambda canonical: f"<main>{canonical}</main>")

    assert result["status"] == "published"
    assert result["mode"] == "inline"
    assert result["inline"]["complete"] is True
    decoded = _decode_set(result)
    assert decoded["json"] == decoded["markdown"] == decoded["html"]
    assert decoded["inline"] == decoded["json"] == model
    assert decoded["json"]["gate"] == model["gate"]


def test_large_dashboard_automatically_delivers_complete_markdown(tmp_path):
    model = _model(findings=180)

    def broken_html(_canonical):
        raise RuntimeError("renderer unavailable")

    result = views.deliver_dashboard(
        str(tmp_path), model, inline_threshold=8_000,
        html_renderer=broken_html)

    assert result["status"] == "published"
    assert result["mode"] == "complete-markdown"
    assert result["inline"] is None
    assert result["artifacts"]["html"]["status"] == "unavailable"
    assert "renderer unavailable" in result["artifacts"]["html"]["reason"]
    decoded = _decode_set(result)
    assert decoded["json"] == decoded["markdown"] == model
    assert len(decoded["markdown"]["findings"]) == 180
    assert decoded["markdown"]["gate"] == model["gate"]


def test_size_boundary_is_measured_in_canonical_utf8_bytes(tmp_path):
    model = _model()
    size = len(views.canonical_dashboard_bytes(model))
    at = views.deliver_dashboard(str(tmp_path / "at"), model,
                                 inline_threshold=size)
    above = views.deliver_dashboard(str(tmp_path / "above"), model,
                                    inline_threshold=size - 1)
    assert at["mode"] == "inline"
    assert above["mode"] == "complete-markdown"


def test_production_view_refresh_uses_size_appropriate_delivery(tmp_path):
    artifact = tmp_path / "dashboard.html"
    out = {"step": "plan_approval", "findings": [
        {"id": "F-large", "evidence": "e" * 20_000}]}
    with mock.patch("dashboard.report_widget", return_value="<main>visual</main>"), \
            mock.patch("dashboard.standalone_document",
                       return_value="<!DOCTYPE html><main>visual</main>"), \
            mock.patch("storage.dashboard_path", return_value=str(artifact)), \
            mock.patch("storage.load_workspace_locator", return_value=None), \
            mock.patch("views.publish_report", return_value=None), \
            mock.patch("views.LARGE_DASHBOARD_INLINE_BYTES", 8_000):
        views.refresh_views(str(tmp_path), out)

    delivery = out["dashboard"]["delivery"]
    assert delivery["mode"] == "complete-markdown"
    assert delivery["inline"] is None
    assert os.path.isfile(delivery["artifacts"]["json"]["path"])
    assert os.path.isfile(delivery["artifacts"]["markdown"]["path"])


def _event(sequence, at, **values):
    return {"schema": progress.EVENT_SCHEMA, "sequence": sequence,
            "observed_at": at, **values}


@pytest.mark.parametrize("state", [
    "executing", "tool-wait", "agent-wait", "human-wait", "resumed",
])
def test_progress_snapshot_preserves_execution_and_wait_state(tmp_path, state):
    path = tmp_path / "progress.json"
    progress.write_snapshot_from_events(str(path), [
        _event(1, 100.0, workflow_id="wf-1", run_id="run-1",
               owner="orchestrator", agent="executor-6", phase="execute",
               state=state, focus_started_at=90.0, observed_tokens=321),
    ])

    status = progress.read_status_snapshot(str(path), now=110.0)

    assert status["status"] == "available"
    assert status["active"] == {
        "owner": "orchestrator", "agent": "executor-6", "phase": "execute"}
    assert status["state"] == state
    assert status["focus_elapsed_seconds"] == 20.0
    assert status["tokens"] == {"status": "observed", "used": 321}


def test_unknown_tokens_and_sparse_history_do_not_invent_values_or_eta(tmp_path):
    path = tmp_path / "progress.json"
    progress.write_snapshot_from_events(str(path), [
        _event(1, 100.0, workflow_id="wf-1", run_id="run-1",
               owner="orchestrator", agent="executor-6", phase="execute",
               state="executing", focus_started_at=100.0),
    ])

    status = progress.read_status_snapshot(str(path), now=101.0)

    assert status["tokens"] == {"status": "unavailable", "used": None}
    assert status["eta"] == {"status": "unavailable",
                             "reason": "insufficient comparable history"}


def test_comparable_observed_history_produces_sourced_fresh_eta(tmp_path):
    path = tmp_path / "progress.json"
    events = [
        _event(1, 100.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="a", phase="execute", state="executing",
               focus_started_at=100.0, comparable_key="integration-task",
               completed_duration_seconds=30.0),
        _event(2, 110.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="a", phase="execute", state="executing",
               focus_started_at=110.0, comparable_key="integration-task",
               completed_duration_seconds=50.0),
    ]
    progress.write_snapshot_from_events(str(path), events)

    status = progress.read_status_snapshot(str(path), now=120.0)

    assert status["eta"]["status"] == "available"
    assert status["eta"]["remaining_seconds"] == 30.0
    assert status["eta"]["source"] == "observed:comparable-history"
    assert status["eta"]["confidence"] == "medium"
    assert status["eta"]["updated_at"] == 110.0


def test_stale_eta_is_explicitly_unavailable(tmp_path):
    path = tmp_path / "progress.json"
    progress.write_snapshot_from_events(str(path), [
        _event(1, 10.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="a", phase="execute", state="executing",
               focus_started_at=10.0, comparable_key="task",
               completed_duration_seconds=30.0),
        _event(2, 20.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="a", phase="execute", state="executing",
               focus_started_at=20.0, comparable_key="task",
               completed_duration_seconds=50.0),
    ])
    status = progress.read_status_snapshot(
        str(path), now=400.0, eta_max_age_seconds=60.0)
    assert status["eta"] == {"status": "unavailable",
                             "reason": "observed ETA is stale"}


def test_status_read_is_one_bounded_snapshot_read_and_never_recomputes(tmp_path):
    path = tmp_path / "progress.json"
    progress.write_snapshot_from_events(str(path), [
        _event(1, 100.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="a", phase="execute", state="agent-wait",
               focus_started_at=90.0, observed_tokens=7),
    ])
    real_open = open
    reads = []

    def instrumented_open(*args, **kwargs):
        reads.append(args[0])
        return real_open(*args, **kwargs)

    with mock.patch("builtins.open", side_effect=instrumented_open):
        status = progress.read_status_snapshot(str(path), now=101.0,
                                               max_bytes=16_384)
    assert status["status"] == "available"
    assert reads == [str(path)]


def test_missing_interrupted_or_malformed_status_is_non_gating(tmp_path):
    path = tmp_path / "progress.json"
    missing = progress.read_status_snapshot(str(path), now=1.0)
    path.write_text("{broken", encoding="utf-8")
    malformed = progress.read_status_snapshot(str(path), now=1.0)
    assert missing["status"] == "unavailable"
    assert malformed["status"] == "unavailable"
    assert missing["gating"] is False and malformed["gating"] is False


def test_pip_projection_uses_only_durable_status_and_capability_receipt(tmp_path):
    path = tmp_path / "progress.json"
    progress.write_snapshot_from_events(str(path), [
        _event(1, 100.0, workflow_id="wf-1", run_id="run-1", owner="o",
               agent="executor-6", phase="execute", state="agent-wait",
               focus_started_at=90.0, observed_tokens=7),
    ])
    status = progress.read_status_snapshot(str(path), now=101.0)
    receipt = host_capabilities.Observation(
        status="supported", source="host-receipt:test", confidence="high",
        observed_at="100")

    projection = host_capabilities.progress_surface_projection(
        host="codex", host_version="1", pip_observation=receipt,
        durable_status=status)

    assert projection["selected_surface"] == "native-pip"
    assert projection["identity"] == {"workflow_id": "wf-1",
                                       "run_id": "run-1", "sequence": 1}
    assert projection["active"]["agent"] == "executor-6"
    assert projection["state"] == "agent-wait"
    assert projection["gating"] is False


def test_production_trace_writer_continuously_updates_progress_snapshot(tmp_path):
    taskplane_lite.trace(
        str(tmp_path), "loop_step", workflow_id="wf-runtime",
        run_id="run-runtime", step="execute", agent_id="executor-6")

    status = progress.read_workspace_status(str(tmp_path), now=10**12)

    assert status["status"] == "available"
    assert status["identity"]["workflow_id"] == "wf-runtime"
    assert status["identity"]["run_id"] == "run-runtime"
    assert status["active"] == {
        "owner": "taskplane", "agent": "executor-6", "phase": "execute"}
    assert status["state"] == "executing"


def test_production_status_reads_durable_progress_without_review_recompute(tmp_path):
    progress.record_trace_event(
        str(tmp_path), "loop_step",
        {"workflow_id": "wf-status", "run_id": "run-status",
         "step": "evaluate", "agent_id": "evaluator-6"}, observed_at=100.0)

    state = {"step": "evaluate", "goal": "ship", "tasks": [],
             "current_task": 0, "max_fix_cycles": 2, "checkpoints": []}
    with mock.patch("loop.load", return_value=state), \
            mock.patch("depgraph.summary", return_value={"modules": 1,
                                                         "edges": 0}):
        summary = loop_status.user_summary(str(tmp_path), now=101.0)

    assert summary["live_progress"]["status"] == "available"
    assert summary["live_progress"]["active"]["phase"] == "evaluate"
    assert summary["live_progress"]["state"] == "executing"


def test_runtime_output_adapter_uses_fresh_receipt_or_accessible_fallback(tmp_path):
    runtime = _runtime_module()
    progress.record_trace_event(
        str(tmp_path), "loop_step",
        {"workflow_id": "wf-pip", "run_id": "run-pip", "step": "execute",
         "agent_id": "executor-6"}, observed_at=100.0)

    native = runtime.project_progress_surface(
        str(tmp_path), host="codex", host_version="test",
        environment={"TASKPLANE_NATIVE_PIP": "supported",
                     "TASKPLANE_HOST_RECEIPT_AT": "100.0"}, now=101.0)
    stale = runtime.project_progress_surface(
        str(tmp_path), host="codex", host_version="test",
        environment={"TASKPLANE_NATIVE_PIP": "supported",
                     "TASKPLANE_HOST_RECEIPT_AT": "1.0"}, now=1000.0)

    assert native["selected_surface"] == "native-pip"
    assert native["identity"]["run_id"] == "run-pip"
    assert native["active"]["agent"] == "executor-6"
    assert stale["selected_surface"] == "accessible-bounded"
    assert stale["identity"]["run_id"] == "run-pip"
    assert stale["limitation"] == "stale"
