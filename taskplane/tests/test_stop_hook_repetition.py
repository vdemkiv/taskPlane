"""Unmet stop gates remain blocking on every invocation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import StringIO
import json

import pytest

from taskplane.workflow_local import Harness


def active(tmp_path):
    harness = Harness(tmp_path, "root")
    harness.update(selected=True)
    state = {"run": "run", "revision": 19, "index": 0,
             "visits": [{"id": "build", "phase": "build", "decision": "not_requested"}]}
    return harness, state


@pytest.mark.parametrize("event", [{}, {"stop_hook_active": False}, {"stop_hook_active": True}])
def test_repeated_stop_remains_blocking_after_restart(tmp_path, event):
    harness, state = active(tmp_path)
    before = deepcopy(state)
    assert harness.stop(event, state)["continue"] is False
    repeated = Harness(tmp_path, "root").stop(event, state)
    assert repeated["continue"] is False
    assert "decision" not in repeated and "systemMessage" not in repeated
    assert "phase evidence is not submitted" in repeated["stopReason"]
    assert state == before


def test_old_stop_notice_cannot_bypass_gate(tmp_path):
    harness, state = active(tmp_path)
    reason = harness.stop({}, state)["stopReason"]
    harness.update(stop_notice={"binding": harness.binding(state), "reason": reason})
    assert Harness(tmp_path, "root").stop({}, state)["continue"] is False


@pytest.mark.parametrize("change", ["revision", "visit", "run", "reason"])
def test_changed_binding_or_blocker_still_blocks(tmp_path, change):
    harness, state = active(tmp_path)
    harness.stop({}, state)
    if change == "revision":
        state["revision"] += 1
    elif change == "visit":
        state["visits"][0]["id"] = "next-build"
    elif change == "run":
        state["run"] = "next-run"
    else:
        state["invalidation_pending"] = True
    assert harness.stop({}, state)["continue"] is False
    assert harness.stop({}, state)["continue"] is False


def test_concurrent_stop_events_both_enforce_gate(tmp_path):
    _, state = active(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Harness(tmp_path, "root").stop({}, state), range(2)))
    assert all(result.get("continue") is False for result in results)


def test_wait_and_finished_status_still_do_not_block(tmp_path):
    harness, state = active(tmp_path)
    harness.wait(state, "Need the user's selected target")
    assert "waiting for user input" in harness.stop({}, state)["systemMessage"]
    state["finished"] = True
    assert harness.stop({}, state) == {}


@pytest.mark.parametrize("payload", ["{", "[]"])
def test_invalid_stop_input_halts_without_continuation(monkeypatch, capsys, payload):
    from taskplane import flow

    monkeypatch.setattr("sys.stdin", StringIO(payload))
    assert flow.run_hook("session-verify") == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"continue": False, "stopReason": "Taskplane hook input/state is invalid."}
