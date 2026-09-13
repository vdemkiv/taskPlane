"""Harness control APIs reject worker authority and lost hook enforcement."""

import json

import pytest

from taskplane import gates, loop, phase_harness, tp
from taskplane.tests.test_enforcement_integration import _strict


@pytest.mark.parametrize("operation", [
    lambda ws: gates.gate(loop, ws, "pass"),
    lambda ws: gates.select(loop, ws, "A"),
    lambda ws: gates.resolve(loop, ws, "retry"),
    lambda ws: gates.collect_phase(loop, ws, "operation"),
])
def test_worker_cannot_use_control_apis_even_without_cli(monkeypatch, operation):
    monkeypatch.setenv("TASKPLANE_TASK", "worker-slot")
    monkeypatch.setattr(loop, "load", lambda ws: pytest.fail("worker reached control state"))
    assert operation("/unused").get("error")


@pytest.mark.parametrize("args", [
    ["select", "A"], ["resolve", "retry"],
    ["replan", "--by", "human:user", "--reason", "change"],
    ["collect", "--operation", "some-operation"],
    ["restore-settings", "--from", "missing.json"],
    ["terminal", "cancellation", "--by", "human:user"],
    ["amend", "--phase", "product", "--req", "R-0001", "--by", "human:user", "--reason", "change"],
])
def test_mutation_commands_require_fresh_hooks(monkeypatch, tmp_path, capsys, args):
    ws = _strict(monkeypatch, tmp_path, live=False)
    assert tp.main(["loop", "--workspace", ws, *args]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "taskplane.enforcement-refusal/v1"
    assert loop.load(ws) is None


def test_direct_resource_waiver_api_is_inert(monkeypatch):
    monkeypatch.setattr(loop, "load", lambda ws: pytest.fail("waiver read run state"))
    result = phase_harness.advise_resource_limits(loop, "/unused", {}, "human:user")
    assert "bypass is disabled" in result["error"]
    assert result["dispatch_allowed"] is False
