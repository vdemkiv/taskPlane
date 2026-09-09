"""T02 diagnostic wiring proofs; these are not real native canary evidence.

The stage producer and retained reader run unchanged. Engine inventory and
host capability observations are controlled external inputs. Only negative
cases sever a produced request/observation at the consuming boundary.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import ci_local
from taskplane import design_host_transport as transport
from taskplane import host_capabilities, stage_entities
from taskplane.tests.test_stage_entities import _authority, _stage


def _request(tmp_path: Path) -> transport.NativeEntryRequest:
    root = tmp_path / "engine"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "taskplane", "version": "2.19.0"}), encoding="utf-8"
    )
    engine = root / "taskplane/tp.py"
    engine.parent.mkdir()
    engine.write_text(
        "from pathlib import Path\nPath(__file__).with_name('effect').touch()\n",
        encoding="utf-8",
    )
    stage = _stage()
    return transport.NativeEntryRequest(
        workspace=str(tmp_path),
        engine_candidates=(str(engine),),
        selected_engine=str(engine),
        arguments=("loop", "next", "", "--literal=$x;"),
        environment=(("TASKPLANE_TASK", "task_12345678"), ("KEEP", "")),
        contract_bytes=stage_entities.canonical_contract_bytes(stage),
        expected_contract_fingerprint=str(stage["fingerprint"]),
        run_id="run-r0004",
        attempt_id="attempt-1",
        owner_attempt_id="owner-1",
        operation_id="operation-1",
        task_slot="task_12345678",
        enforcement_mode="strict",
        host_kind="codex",
        host_version="test-host",
        session_id="codex-thread-1",
    )


def _snapshot(
    request: transport.NativeEntryRequest, *, hooks: bool = True, stable: bool = False
) -> host_capabilities.HostCapabilitySnapshot:
    return host_capabilities.probe_snapshot(
        request.workspace,
        host=request.host_kind,
        host_version=request.host_version,
        session_id=request.session_id,
        install_context="personal",
        native_installed=True,
        bridge_configured=False,
        now="2026-09-06T12:00:00Z",
        observations={
            "native_plugin_hooks_loaded": host_capabilities.Observation(
                "supported" if hooks else "unsupported", "fixture:hook"
            ),
            "stable_event_identity": host_capabilities.Observation(
                "supported" if stable else "unknown", "fixture:identity"
            ),
        },
    )


@pytest.mark.parametrize("entry", ["cli", "native"], ids=["cli", "native"])
def test_cli_and_native_entry_preserve_engine_contract_and_unique_identity(
    tmp_path: Path, entry: str
) -> None:
    request = _request(tmp_path)
    prepared = transport.prepare_native_entry(request, _snapshot(request))
    assert prepared.command == (sys.executable, request.selected_engine, *request.arguments)
    assert prepared.environment == request.environment
    assert prepared.contract_bytes == request.contract_bytes
    assert prepared.enforcement_mode == request.enforcement_mode
    report = ci_local.native_entry_probe(request, snapshot=_snapshot(request))
    assert report == ci_local.native_entry_probe(request, snapshot=_snapshot(request))
    assert report["request_fingerprint"] == prepared.request_fingerprint
    assert report["owner_attempt_id"] == "owner-1"
    assert report["native_identity_claimed"] is False
    replacement = replace(request, attempt_id="attempt-2", operation_id="operation-2")
    other_run = replace(request, run_id="other-run")
    assert (
        ci_local.native_entry_probe(replacement, snapshot=_snapshot(replacement))["receipt_id"]
        != report["receipt_id"]
    )
    with pytest.raises(transport.NativeEntryError, match="invalid_binding"):
        ci_local.native_entry_probe(other_run, snapshot=_snapshot(other_run))
    stage = _stage(run_id="other-run", authority=_authority(run_id="other-run"))
    other_run = replace(
        other_run,
        contract_bytes=stage_entities.canonical_contract_bytes(stage),
        expected_contract_fingerprint=str(stage["fingerprint"]),
    )
    child = replace(request, owner_attempt_id="owner-2")
    for independent in (other_run, child):
        assert (
            ci_local.native_entry_probe(independent, snapshot=_snapshot(independent))["receipt_id"]
            != report["receipt_id"]
        )
    if entry == "cli":
        source = tmp_path / "request.json"
        source.write_text(json.dumps(request.to_dict()), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(ci_local.__file__)), "--native-entry-probe", str(source)],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        cli_report = json.loads(result.stdout)
        assert cli_report["request_fingerprint"] == report["request_fingerprint"]
        assert cli_report["evidence_mode"] == "degraded_observation"
    assert not Path(request.selected_engine).with_name("effect").exists()


@pytest.mark.parametrize(
    "case",
    [
        "absent_engine",
        "invalid_engine",
        "ambiguous_engine",
        "broken_binding",
        "foreign_result",
        "duplicate_event",
        "foreign_workspace",
        "foreign_session",
        "foreign_host",
        "contract_severed",
        "environment_severed",
        "enforcement_severed",
    ],
    ids=str,
)
def test_invalid_ambiguous_or_foreign_native_binding_refuses_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    request = _request(tmp_path)
    snapshot = _snapshot(request)
    if case == "absent_engine":
        request = replace(request, engine_candidates=())
    elif case == "invalid_engine":
        Path(request.selected_engine).unlink()
    elif case == "ambiguous_engine":
        request = replace(request, engine_candidates=request.engine_candidates * 2)
    elif case == "broken_binding":
        request = replace(request, owner_attempt_id="")
    elif case == "foreign_workspace":
        snapshot = replace(snapshot, workspace_fingerprint="f" * 64)
    elif case == "foreign_session":
        snapshot = replace(snapshot, session_fingerprint="f" * 64)
    elif case == "foreign_host":
        snapshot = replace(snapshot, host_version="foreign")
    elif case == "contract_severed":
        request = replace(request, expected_contract_fingerprint="f" * 64)
    elif case == "environment_severed":
        request = replace(request, environment=(("TASKPLANE_TASK", "task_foreign"),))
    elif case == "enforcement_severed":
        request = replace(request, enforcement_mode="made-up")
    else:
        # One output edge is severed after the production producer runs.
        original = transport.prepare_native_entry(request, snapshot)
        observation = dict(original.observation)
        key = "request_fingerprint" if case == "foreign_result" else "receipt_id"
        observation[key] = "foreign"
        severed = replace(original, observation=observation)
        monkeypatch.setattr(transport, "prepare_native_entry", lambda *args: severed)
    with pytest.raises(transport.NativeEntryError):
        ci_local.native_entry_probe(request, snapshot=snapshot)
    assert not Path(request.selected_engine).with_name("effect").exists()


@pytest.mark.parametrize(
    "case",
    ["missing_hook", "missing_identity", "unproven_canary", "capability_boundary_severed"],
    ids=str,
)
def test_missing_host_hook_enters_degraded_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    request = _request(tmp_path)
    snapshot = _snapshot(request, hooks=case != "missing_hook", stable=case == "unproven_canary")
    if case == "capability_boundary_severed":
        monkeypatch.setattr(ci_local, "native_entry_snapshot", lambda request: None)
        snapshot = None
    result = ci_local.native_entry_probe(request, snapshot=snapshot)
    assert result["evidence_mode"] == "degraded_observation"
    assert result["effect_state"] == "none"
    assert result["ready"] is False
    assert result["success"] is False
    assert result["native_identity_claimed"] is False
    assert result["last_observed_event"] is None
    assert result["blocking_journeys"] == ["J0", "J1", "J6", "sign-off", "publication"]
    assert "real_canary" in result["missing_capabilities"]
    if case == "missing_hook":
        assert "hook_execution" in result["missing_capabilities"]
    if case != "unproven_canary":
        assert "stable_event_identity" in result["missing_capabilities"]
    assert not Path(request.selected_engine).with_name("effect").exists()
