"""R-0001 t7: cross-host integration and shipped contract parity."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

import authority  # noqa: E402
import views  # noqa: E402


def _load_runtime():
    path = ROOT / "hooks" / "host_native_runtime.py"
    spec = importlib.util.spec_from_file_location("t7_host_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _authority_fields() -> dict:
    return {
        "requirement": "R-0001",
        "acceptance": ["cross-host parity"],
        "target": {"repository": "repo", "revision": "abc"},
        "scope": ["taskplane/**"],
        "contracts": {"contract:authorization": "one packet"},
        "design": {"decision": "consolidated flow"},
        "plan": {"tasks": ["t1", "t7"]},
        "dynamic_validation": "declared tests",
        "sandbox": "ordinary scoped writes",
        "recovery": {"attempts": 3, "gate_weakening": False},
        "evaluation": "routed lenses and collection",
        "artifact_delivery": ["json", "markdown", "html"],
        "execution_bounds": {"external_effects": False},
    }


def _host_event(event: dict, *, actor="user-1", thread="thread-1",
                revision="rev-1", event_ref="event-1") -> dict:
    return {
        "schema": authority.HOST_SESSION_EVENT_SCHEMA,
        "event_fingerprint": authority._fingerprint(
            authority._host_event_payload(event)),
        "event_ref": event_ref,
        "actor": actor,
        "thread": thread,
        "revision": revision,
        "target": {"revision": revision},
        "source": "trusted-local-host-session",
    }


def test_facade_and_delivery_flows_expose_one_preimplementation_authorization():
    facade = json.loads((ROOT / "skills/taskplane/flow.json").read_text())
    delivery = json.loads((ROOT / "skills/tp-go/flow.json").read_text())
    facade_nodes = {node["id"] for node in facade["nodes"]}
    delivery_nodes = {node["id"] for node in delivery["nodes"]}

    assert {"help", "status", "product", "design", "build", "review"} \
        <= facade_nodes
    assert "authorization" in delivery_nodes
    assert "design_approval" not in delivery_nodes
    assert "plan_approval" not in delivery_nodes
    assert ["plan", "authorization"] in delivery["edges"]
    assert ["authorization", "build"] in delivery["edges"]


@pytest.mark.parametrize("host", ["claude", "codex", "slack"])
def test_one_attributed_host_event_authorizes_all_routine_flows(host):
    event = {"type": "approval", "response": {"decision": "approve"}}
    observed = authority.HostSessionAdapter().observe(
        event, _host_event(event, event_ref=f"{host}-event"),
        expected_actor="user-1", expected_thread="thread-1",
        expected_revision="rev-1", expected_target={"revision": "rev-1"})
    assert observed["attributed"] is True

    fields = _authority_fields()
    packet = authority.create_packet(fields)
    receipt = authority.approve(
        packet, actor=observed["actor"], thread=observed["thread"],
        authenticated=observed["attributed"])
    trace = authority.routine_flow_trace(
        packet, receipt, current=fields, actor=observed["actor"],
        thread=observed["thread"])

    assert trace["authorized"] is True
    assert len(trace["stages"]) == 10
    assert {row["receipt_fingerprint"] for row in trace["stages"].values()} \
        == {receipt["fingerprint"]}


def test_wrong_thread_and_replayed_decision_cannot_authorize():
    result = authority.decision_input(
        "final_signoff", {"decision": "approve", "authenticated": True},
        fact="canonical review complete", consequence="close governed run",
        actor="user-1", thread="wrong-thread", revision="rev-1",
        expected_actor="user-1", expected_thread="thread-1",
        expected_revision="rev-1", consumed=True)

    assert result["authorized"] is False
    assert {"wrong_thread", "replayed_decision"} <= set(result["reasons"])


def test_large_thread_delivery_keeps_complete_canonical_markdown(tmp_path):
    model = {
        "schema": "taskplane.review-artifact-model/v1",
        "identity": {"workflow_id": "wf", "run_id": "run",
                     "target": "repo@abc", "revision": 1},
        "gate": {"status": "awaiting-human", "approval_enabled": False},
        "findings": [
            {"id": f"F-{index:04d}", "severity": "high",
             "evidence": "e" * 1024}
            for index in range(140)
        ],
    }
    delivered = views.deliver_dashboard(
        str(tmp_path), model, inline_threshold=4_000,
        html_renderer=lambda _: (_ for _ in ()).throw(
            RuntimeError("native renderer unavailable")))

    assert delivered["status"] == "published"
    assert delivered["mode"] == "complete-markdown"
    assert delivered["inline"] is None
    markdown = views.decode_dashboard_artifact(
        "markdown", Path(delivered["artifacts"]["markdown"]["path"]).read_bytes())
    canonical = views.decode_dashboard_artifact(
        "json", Path(delivered["artifacts"]["json"]["path"]).read_bytes())
    assert markdown == canonical == model
    assert delivered["artifacts"]["html"]["status"] == "unavailable"


def test_host_runtime_uses_accessible_fallback_without_decision_authority():
    runtime = _load_runtime()
    declaration = runtime.discover_host_native_contract(ROOT, "codex")

    assert declaration["fallback"] == "accessible_bounded"
    assert declaration["runtimeReceiptRequired"] is True
    assert declaration["nativeUiIsAuthority"] is False


def test_thread_flow_separates_initial_authorization_from_final_signoff():
    flow = json.loads((ROOT / "skills/tp-tag/flow.json").read_text())
    nodes = {node["id"]: node for node in flow["nodes"]}

    assert nodes["authorization"]["kind"] == "gate"
    assert nodes["signoff"]["kind"] == "gate"
    assert ["authorization", "work"] in flow["edges"]
    assert ["dashboard", "signoff"] in flow["edges"]
    assert flow["invariants"]["approval_requires"] == "attributed host event"
