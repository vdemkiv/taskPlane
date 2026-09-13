"""The amendment CLI preserves explicit human and candidate identity."""
from __future__ import annotations

import json

import pytest

from taskplane import tp as cli


def test_amend_preview_is_read_only_and_returns_exact_candidate(monkeypatch, tmp_path, capsys):
    from taskplane import phase_amendment

    expected = {"stage_fingerprint": "a" * 64,
                "requirement_fingerprint": "b" * 64,
                "candidate_fingerprint": "c" * 64}
    calls = []
    monkeypatch.setattr(phase_amendment, "candidate", lambda runtime, ws, phase, rid:
                        calls.append((ws, phase, rid)) or expected)
    monkeypatch.setattr(phase_amendment, "amend", lambda *a, **kw:
                        pytest.fail("preview must not mutate the run"))
    assert cli.main(["loop", "amend", "--workspace", str(tmp_path),
                     "--phase", "design", "--req", "R-0001", "--preview"]) == 0
    assert json.loads(capsys.readouterr().out) == {**expected, "read_only": True}
    assert calls == [(str(tmp_path), "design", "R-0001")]


@pytest.mark.parametrize("phase", ["product", "design"])
@pytest.mark.parametrize("live", [False, True])
def test_amend_requires_hooks_then_forwards_exact_decision(monkeypatch, tmp_path, capsys, phase, live):
    from taskplane import phase_amendment
    import host_capabilities as hc

    # Only the host observation is simulated. Admission uses the real enforcer.
    observations = {name: hc.Observation(status="supported", source=source,
        confidence="high", reason="test host observed hook execution")
        for name, source in (("native_plugin_hooks_loaded", "runtime-hook:native"),
                             ("managed_policy_permission", "runtime-hook:execution"))} if live else {}
    monkeypatch.setattr(cli, "_host_capability_snapshot", lambda ws: hc.probe_snapshot(
        ws, host="claude", install_context="personal", native_installed=True,
        bridge_configured=False, observations=observations, session_id="amendment-test"))

    calls = []
    def refuse(runtime, ws, **kwargs):
        calls.append((ws, kwargs))
        return {"error": "candidate changed", "dispatch_allowed": False}
    monkeypatch.setattr(phase_amendment, "amend", refuse)
    assert cli.main(["loop", "amend", "--workspace", str(tmp_path),
        "--phase", phase, "--req", "R-0001", "--by", "human:owner",
        "--reason", "Keep the setup simple", "--expected-stage-fingerprint", "a" * 64,
        "--requirement-fingerprint", "b" * 64, "--candidate-fingerprint", "c" * 64,
        "--worker-stopped"]) == 1
    if not live:
        assert calls == []
        refusal = json.loads(capsys.readouterr().out)
        assert refusal["schema"] == "taskplane.enforcement-refusal/v1"
        assert refusal["enforcement"]["status"] == "unproven"
        return
    assert calls == [(str(tmp_path), {
        "phase": phase, "by": "human:owner", "reason": "Keep the setup simple",
        "requirement_id": "R-0001", "expected_stage_fingerprint": "a" * 64,
        "requirement_fingerprint": "b" * 64, "candidate_fingerprint": "c" * 64,
        "worker_stopped": True})]
    result = json.loads(capsys.readouterr().out)
    enforcement = result.pop("enforcement")
    assert enforcement["status"] == "live" and enforcement["mode"] == "strict"
    assert enforcement["advisory"] is None
    assert result == {"error": "candidate changed", "dispatch_allowed": False}


def test_dashboard_snapshot_keeps_amendment_separate_from_design_approval():
    from taskplane import dashboard, host_native

    amendment = {"phase": "design", "actor": "human:owner",
                 "reason": "Use <existing> settings", "approval": "pending",
                 "review_basis": "human-directed-amendment"}
    values = host_native._bounded_loop_values({
        "step": "design_approval", "phase_amendment": amendment})
    assert values["phase_amendment"] == amendment
    markup = dashboard.render_canonical_dashboard_snapshot({
        "stage": "design_approval", "values": {"loop": values}})
    assert 'data-phase-amendment="true"' in markup
    assert "Use &lt;existing&gt; settings" in markup
    assert "design_approval" in markup
