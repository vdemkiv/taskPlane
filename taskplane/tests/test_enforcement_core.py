"""R-0003 t02: canonical enforcement decision and receipt trust."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

import enforcement
import host_capabilities as hc
from run_store import RevisionConflict, RunStore
import storage


def _workspace() -> str:
    root = tempfile.mkdtemp(prefix="tp-enforcement-")
    os.mkdir(os.path.join(root, ".git"))
    return root


def _snapshot(workspace: str, *, session: str | None = "session-1",
              live: bool = True, observed_at: str = "2026-08-20T12:00:00Z"):
    rows = {}
    if live:
        rows = {
            "native_plugin_hooks_loaded": hc.Observation(
                status="supported", source="runtime-hook:native",
                confidence="high", reason="entry PreToolUse executed"),
            "managed_policy_permission": hc.Observation(
                status="supported", source="runtime-hook:execution",
                confidence="high", reason="hook command executed"),
        }
    return hc.probe_snapshot(
        workspace, host="claude", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations=rows, session_id=session, now=observed_at)


def test_one_decision_has_exact_identity_and_stable_evidence_id():
    workspace = _workspace()
    first = enforcement.enforcement_status(
        workspace, snapshot=_snapshot(workspace), run_id="run-1",
        revision="abc123", observed_at="2026-08-20T12:00:01Z")
    second = enforcement.enforcement_status(
        workspace, snapshot=_snapshot(
            workspace, observed_at="2026-08-20T12:00:09Z"),
        run_id="run-1", revision="abc123",
        observed_at="2026-08-20T12:00:09Z")

    assert first["schema"] == "taskplane.enforcement-status/v1"
    assert first["status"] == "live"
    assert first["evidence_id"] == second["evidence_id"]
    assert first["workspace_fingerprint"] == \
        _snapshot(workspace).workspace_fingerprint
    assert len(first["repository_fingerprint"]) == 64
    assert first["session_fingerprint"] == \
        _snapshot(workspace).session_fingerprint


def test_meter_activity_can_prove_live_and_warning_revokes_it():
    workspace = _workspace()
    unproved = _snapshot(workspace, live=False)
    active = enforcement.enforcement_status(
        workspace, snapshot=unproved,
        liveness={"governed": True, "hook_seen": True, "warning": None})
    degraded = enforcement.enforcement_status(
        workspace, snapshot=_snapshot(workspace),
        liveness={"governed": True, "hook_seen": False,
                  "warning": "active contract has ZERO screen activity"})

    assert active["status"] == "live"
    assert degraded["status"] == "unproven"
    assert "screen activity" in degraded["reasons"][0]


def test_advisory_requires_actor_and_is_attributable():
    workspace = _workspace()
    base = enforcement.enforcement_status(
        workspace, snapshot=_snapshot(workspace, live=False),
        run_id="run-1", revision=7,
        observed_at="2026-08-20T12:00:00Z")

    with pytest.raises(enforcement.EnforcementError,
                       match="attributable actor"):
        enforcement.acknowledge_advisory(base, actor="")
    advisory = enforcement.acknowledge_advisory(
        base, actor="human@example.com",
        acknowledged_at="2026-08-20T12:01:00Z")

    assert advisory["status"] == "advisory"
    assert advisory["advisory"]["actor"] == "human@example.com"
    assert advisory["advisory"]["decision_id"].startswith("adv-")
    assert advisory["evidence_id"] != base["evidence_id"]
    assert enforcement.validate_decision(advisory) == advisory


@pytest.mark.parametrize(
    ("age", "expected"), ((299.0, True), (300.0, True), (301.0, False)))
def test_unknown_current_session_bounds_foreign_receipt(age, expected):
    home = tempfile.mkdtemp(prefix="tp-enforcement-receipt-")
    hc.record_runtime_hook_receipt(
        home, hook_path="native", observed_at=100.0,
        event={"session_id": "foreign-session", "tool_use_id": "call-1",
               "hook_event_name": "PreToolUse"})

    rows = hc.runtime_hook_observations(
        home, session_id=None, now=100.0 + age)

    assert ("native_plugin_hooks_loaded" in rows) is expected


def test_known_session_requires_exact_session_bound_receipt():
    home = tempfile.mkdtemp(prefix="tp-enforcement-receipt-")
    hc.record_runtime_hook_receipt(
        home, hook_path="native", observed_at=100.0,
        event={"tool_use_id": "call-1", "hook_event_name": "PreToolUse"})

    assert hc.runtime_hook_observations(
        home, session_id="known-session", now=101.0) == {}


def test_run_store_atomically_records_one_canonical_decision():
    workspace = _workspace()
    home = tempfile.mkdtemp(prefix="tp-enforcement-store-")
    identity = storage.resolve_repository_identity(workspace)
    store = RunStore(home=home)
    manifest = store.create(
        identity, run_id="run-1", checkout=workspace,
        host={"name": "claude"}, target={"kind": "workspace"})
    decision = enforcement.enforcement_status(
        workspace, snapshot=_snapshot(workspace), run_id="run-1",
        revision="abc123", observed_at="2026-08-20T12:00:00Z")

    recorded = store.record_enforcement_decision(
        "run-1", expected_revision=manifest["revision"],
        decision=decision)

    assert recorded["enforcement"]["current"] == decision
    assert recorded["enforcement"]["history"] == [decision]
    assert json.loads(open(store._manifest_path("run-1"),
                           encoding="utf-8").read())["enforcement"][
                               "current"]["evidence_id"] == \
        decision["evidence_id"]
    with pytest.raises(RevisionConflict):
        store.record_enforcement_decision(
            "run-1", expected_revision=manifest["revision"],
            decision=decision)
