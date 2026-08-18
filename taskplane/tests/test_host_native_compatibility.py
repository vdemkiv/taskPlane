from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane.host_capabilities import Observation, negotiate_host_surfaces
from taskplane.host_native import HostSurfaceSnapshot


ROOT = Path(__file__).resolve().parents[2]
SURFACES = (
    "pip", "visualization", "carousel", "approval", "sandbox", "hosting",
    "browser", "side_panel",
)
IDENTITY = {
    "workflow_id": "wf-14",
    "run_id": "run-14",
    "target": "repo@feature",
    "revision": "abc123",
    "task_id": "t5",
    "slot_id": "slot-2",
}


def _runtime_module():
    path = ROOT / "hooks" / "host_native_runtime.py"
    spec = importlib.util.spec_from_file_location("host_native_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(sequence: int, state: str = "running") -> HostSurfaceSnapshot:
    return HostSurfaceSnapshot.create(
        workflow_id=IDENTITY["workflow_id"], run_id=IDENTITY["run_id"],
        target=IDENTITY["target"], revision=IDENTITY["revision"],
        sequence=sequence, stage="review", state=state,
        values={
            "task_id": IDENTITY["task_id"], "slot_id": IDENTITY["slot_id"],
            "gate": {"state": "awaiting_human", "evidence": "sha256:e14"},
            "preview": {"id": "preview-14", "workflow_id": IDENTITY["workflow_id"],
                        "run_id": IDENTITY["run_id"],
                        "target": IDENTITY["target"],
                        "revision": IDENTITY["revision"]},
            "agents": {"status": "active", "items": [
                {"id": "agent-1", "task_id": IDENTITY["task_id"],
                 "slot_id": IDENTITY["slot_id"]}], "provenance": "audit:14"},
            "artifacts": {"status": "ready", "items": [
                {"id": "report", "evidence": "sha256:e14"}],
                "provenance": "audit:14"},
        },
        evidence=("sha256:e14",), safe_actions=("inspect", "approve"),
    )


def test_codex_and_claude_packages_declare_one_canonical_contract() -> None:
    runtime = _runtime_module()
    codex = runtime.discover_host_native_contract(ROOT, "codex")
    claude = runtime.discover_host_native_contract(ROOT, "claude")
    hook_contract = runtime.discover_hook_contract(ROOT)

    assert codex == claude == hook_contract
    assert codex["schema"] == "taskplane.host-native-package/v1"
    assert codex["canonicalModel"] == "taskplane.host-surface-snapshot/v1"
    assert codex["capabilityContract"] == "taskplane.host-capabilities/v1"
    assert tuple(codex["optionalSurfaces"]) == SURFACES
    assert codex["fallback"] == "accessible_bounded"
    assert codex["nativeUiIsAuthority"] is False

    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    commands = [hook["command"] for entry in hooks["hooks"]["SessionStart"]
                for hook in entry["hooks"]]
    assert any("host_native_runtime.py\" check --host claude" in command
               for command in commands)
    checked = subprocess.run(
        [sys.executable, str(ROOT / "hooks/host_native_runtime.py"),
         "check", "--host", "claude"], cwd=ROOT, check=False,
        capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize(
    "case", ("concurrent", "reconnect", "stale", "duplicate", "host_switch",
             "terminal_close"),
)
def test_recovery_fixtures_keep_one_identity_and_ordered_audit(case: str) -> None:
    runtime = _runtime_module()
    observations = {
        name: Observation(status="supported", source="fixture", confidence="high")
        for name in SURFACES
    }
    selections = negotiate_host_surfaces(
        host="codex", host_version="test", observations=observations)
    recovery = runtime.HostNativeRecovery()
    snapshots = [_snapshot(1), _snapshot(2)]
    if case == "concurrent":
        delivered = [snapshots[1], snapshots[0]]
    elif case == "reconnect":
        recovery = runtime.HostNativeRecovery.resume(
            (snapshots[0],), selections=selections)
        delivered = [snapshots[0], snapshots[1]]
    elif case in {"stale", "duplicate"}:
        delivered = [snapshots[0], snapshots[0], snapshots[1]]
    else:
        delivered = snapshots
    terminal = _snapshot(3, "completed")
    if case == "terminal_close":
        delivered += [terminal, terminal, _snapshot(4, "running")]

    recovery.recover(delivered, host="codex", selections=selections)
    # Switching hosts reprojects the last canonical state but cannot append a
    # duplicate event to the workflow audit.
    current = terminal if case == "terminal_close" else snapshots[1]
    before_switch = len(recovery.audit)
    recovery.apply(current, host="claude", selections=selections)
    assert len(recovery.audit) == before_switch

    sequences = [event.sequence for event in recovery.audit]
    assert sequences == sorted(set(sequences))
    assert sum(event.event_type == "completed" for event in recovery.audit) <= 1
    assert recovery.identity == tuple(IDENTITY.values())

    expected_sequence = 3 if case == "terminal_close" else 2
    for host in ("codex", "claude"):
        assert set(recovery.projections[host]) == set(runtime.SURFACE_ROLES)
        for role, projection in recovery.projections[host].items():
            canonical = projection["canonical"]
            assert projection["surface_role"] == role
            assert canonical["workflow_id"] == IDENTITY["workflow_id"]
            assert canonical["run_id"] == IDENTITY["run_id"]
            assert canonical["target"] == IDENTITY["target"]
            assert canonical["revision"] == IDENTITY["revision"]
            assert canonical["sequence"] == expected_sequence
            assert canonical["values"]["task_id"] == IDENTITY["task_id"]
            assert canonical["values"]["slot_id"] == IDENTITY["slot_id"]
            assert canonical["values"]["gate"]["state"] == "awaiting_human"
            assert canonical["evidence"] == ["sha256:e14"]
            assert [row["sequence"] for row in projection["audit"]] == sequences


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("disabled", SURFACES)
def test_each_capability_falls_back_independently_without_losing_truth(
    host: str, disabled: str
) -> None:
    observations = {
        name: Observation(status="supported", source=f"{host}:runtime",
                          confidence="high", observed_at="2026-08-18T12:00:00Z")
        for name in SURFACES
    }
    observations[disabled] = Observation(
        status="unsupported", source=f"{host}:runtime", confidence="high",
        reason="host surface unavailable", observed_at="2026-08-18T12:00:00Z")
    selections = negotiate_host_surfaces(
        host=host, host_version="test", observations=observations)
    snapshot = _snapshot(1)
    projection = snapshot.project(selections[disabled])

    assert projection["canonical"] == snapshot.to_dict()
    assert projection["presentation"]["kind"] == "accessible_bounded"
    assert projection["presentation"]["reason"] == "unavailable"
    assert projection["presentation"]["user_declined"] is False
    assert all(selections[name].selected_surface == "native"
               for name in SURFACES if name != disabled)


def test_native_compatibility_suite_contains_no_weakening_markers() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = tuple("".join(parts) for parts in (
        ("pytest", ".skip"), ("pytest", ".xfail"),
        ("@pytest.mark", ".skip"), ("@pytest.mark", ".xfail"),
    ))
    assert not any(marker in source for marker in forbidden)


@pytest.mark.parametrize("flow", (
    "design", "build", "review", "status", "approval", "artifact",
))
def test_native_disabled_legacy_flows_keep_evidence_actions_and_gate(
    flow: str,
) -> None:
    snapshot = HostSurfaceSnapshot.create(
        workflow_id=f"wf-{flow}", run_id=f"run-{flow}", target="repo",
        revision="legacy-rev", sequence=7, stage=flow, state="waiting",
        values={"gate": {"state": "awaiting_human", "owner": "human"},
                "flow": flow}, evidence=(f"sha256:{flow}",),
        safe_actions=("inspect", "approve"),
    )
    observations = {
        name: Observation(status="unknown", source="no-host-receipt")
        for name in SURFACES
    }
    selections = negotiate_host_surfaces(
        host="legacy", host_version=None, observations=observations)

    for selection in selections.values():
        fallback = snapshot.project(selection)
        canonical = fallback["canonical"]
        assert canonical["evidence"] == [f"sha256:{flow}"]
        assert canonical["safe_actions"] == ["inspect", "approve"]
        assert canonical["values"]["gate"] == {
            "state": "awaiting_human", "owner": "human"}
        assert fallback["presentation"]["reason"] == "unavailable"
        assert fallback["presentation"]["user_declined"] is False


def test_documented_legacy_flows_keep_evidence_and_human_gates() -> None:
    guidance = (ROOT / "docs" / "host-native-ux.md").read_text(
        encoding="utf-8")
    for flow in ("design", "build", "review", "status", "approval",
                 "artifact"):
        assert f"`{flow}`" in guidance
    assert "canonical evidence" in guidance
    assert "human gate" in guidance
    assert "unavailable, not declined" in guidance


def test_hooks_skills_and_agents_share_projection_not_authority_semantics() -> None:
    hook_contract = _runtime_module().discover_hook_contract(ROOT)
    assert hook_contract["canonicalModel"] == \
        "taskplane.host-surface-snapshot/v1"
    assert hook_contract["runtimeReceiptRequired"] is True
    assert hook_contract["nativeUiIsAuthority"] is False

    skill = (ROOT / "skills" / "taskplane" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "Host-native projection contract" in skill
    for role in ("tp-orchestrator.md", "tp-executor.md", "tp-evaluator.md"):
        text = (ROOT / "agents" / role).read_text(encoding="utf-8")
        assert "canonical host-surface identity" in text
