"""Native workers receive only a bounded zero-turn startup."""
from __future__ import annotations

from taskplane import loop


def test_wait_description_leaves_timeout_and_reissue_to_native_tools():
    policy = loop.event_wait_policy("review:set", 2)
    for wake in (None, "timeout", "completion", "attention"):
        request = loop.event_wait_invocation(policy, ["review-a", "review-b"], wake=wake)
        assert request["outstanding_members"] == ["review-a", "review-b"]
        assert request["wait_owner"] == "native"
        assert not ({"timeout_seconds", "minimum_timeout_seconds", "reissue_after",
            "scheduled_polling", "reissue"} & (set(policy) | set(request)))


def test_worker_receives_no_inherited_conversation_turns(tmp_path):
    policy = loop.event_wait_policy("execute:t1", 1)
    intent = loop._native_dispatch_intent(
        str(tmp_path), {"goal": "bounded worker", "baseline": "abc"},
        step="execute", task_id="t1",
        dispatch={"role": "tp-executor", "task_name": "executor-t1"},
        wait_policy=policy)
    assert intent["fork_turns"] == "none"
    assert intent["inherited_turns"] == 0
    assert "conversation" not in str(intent).lower()
