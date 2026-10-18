"""Synthetic, host-parameterized observations; no live account/host certification."""
from copy import deepcopy

import pytest

from taskplane import delegated_approval as relay_api, workflow as w, workflow_host as h
from taskplane.tests.test_delegated_checkpoint import digest, observation
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decide, submit


# Preserve the reported question's semantics without its private task/message IDs.
QUESTION = (
    "The native hooks are working, but the Product proposal was sealed against the old baseline. "
    "The harness requires an explicit change request to reopen it.\n\n"
    "May I reopen that Product checkpoint, update it to verified 2.33.10, "
    "and refresh only the affected review evidence? "
    "This doesn’t approve the proposal or start implementation."
)


@pytest.fixture(params=["codex", "claude"])
def pending(tmp_path, request):
    source, _, _ = prepare(tmp_path)
    c = h.Controller(tmp_path, "root", h.installed_adapter(request.param))
    state = c.start({"scope": source["scope"], "request_reference": "fixture/user-request"})
    return c, submit(c, state)


def reopen_observation(state, question=QUESTION, answer="yes", *, linked=False):
    request = ordinary(state)
    request["choice"] = "changes_requested"
    relay = request["relay"]
    relay["question"]["text"] = question
    raw = relay["observation"]["raw"]
    raw["before"][0]["content"]["text"] = question
    raw["message"]["content"]["text"] = answer
    if linked:
        raw["message"]["reply_to_id"] = relay["question"]["message_id"]
    digest(request)
    return request


@pytest.mark.parametrize("linked", [False, True])
def test_reopen_yes_preserves_history_and_allows_only_checkpoint_revision(pending, linked):
    c, state = pending
    request = reopen_observation(state, linked=linked)
    original = deepcopy(request)
    packet = deepcopy(w.current(state)["packet"])
    sealed = {path: (c.workspace / path).read_bytes() for path in packet["manifest"]}
    result = decide(c, state, request)
    event = result["decisions"][request["event_id"]]
    assert w.current(result)["decision"] == "changes_requested"
    assert w.current(result)["work"] == "working"
    assert event["provenance"]["relay"] == original["relay"] and request == original
    assert event["provenance"]["excerpt"] == "yes"
    assert event["provenance"]["host_attested"] is False
    assert result["scope"] == state["scope"] and result["index"] == state["index"]
    assert not result.get("approval_policy") and w.current(result)["packet"] == packet
    assert all((c.workspace / path).read_bytes() == data for path, data in sealed.items())
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.apply("advance", result["run"], expected_revision=result["revision"], phase="design")
    with pytest.raises(w.Refusal):
        decide(c, result, request)
    assert c._path().read_bytes() == before
    revised = submit(c, result)
    assert w.current(revised)["decision"] == "awaiting_human_approval"
    assert revised["decisions"] == result["decisions"]
    assert {"visit": w.current(state)["id"], "packet": packet, "decision": "changes_requested"} in revised["history"]
    assert not any(row["choice"] == "approved" for row in revised["decisions"].values())
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, revised, request)
    assert c._path().read_bytes() == before




def test_yes_to_acceptance_stays_approval(pending):
    c, state = pending
    request = ordinary(state)
    request["relay"]["observation"]["raw"]["message"]["content"]["text"] = "yes"
    digest(request)
    assert w.current(decide(c, state, request))["decision"] == "approved"










@pytest.mark.parametrize("bad", [
    "partial", "later_message", "wrong_owner", "unverified_owner", "wrong_source", "receiver",
    "stale_binding", "foreign_run", "changed_raw", "changed_digest", "old_question",
    "answer_before_question", "wrong_event", "duplicate_event", "duplicate_source",
])
def test_reopen_retains_evidence_and_replay_boundaries(pending, bad):
    c, state = pending
    request = reopen_observation(state)
    relay = request["relay"]; raw = relay["observation"]["raw"]
    prior = {}
    if bad == "partial": raw["partial"] = True
    elif bad == "later_message": raw["after"] = [{"content": {"text": "stop"}}]
    elif bad == "wrong_owner": raw["message"]["author_id"] = "someone-else"
    elif bad == "unverified_owner": relay["owner"]["verified"] = False
    elif bad == "wrong_source": relay["owner"]["source_thread"] = "another-source"
    elif bad == "receiver": relay["receiver_thread"] = "another-root"
    elif bad == "stale_binding": request["binding"]["revision"] += 1
    elif bad == "foreign_run": request["binding"]["run"] = "foreign"
    elif bad == "changed_raw": raw["before"][0]["content"]["text"] = "May I approve this Product?"
    elif bad == "old_question": relay["question"]["sent_at"] = raw["before"][0]["sent_at"] = state["started_at"]
    elif bad == "answer_before_question": raw["message"]["sent_at"] = relay["question"]["sent_at"]
    elif bad == "wrong_event": request["event_id"] = "invented-event"
    elif bad in {"duplicate_event", "duplicate_source"}:
        event = relay_api.verify_checkpoint(state, request, request["binding"], {})
        prior[request["event_id"] if bad == "duplicate_event" else "different-key"] = event
    digest(request)
    if bad == "changed_digest": relay["digest"] = "0" * 64
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        if prior:
            relay_api.verify_checkpoint(state, request, request["binding"], prior)
        else:
            decide(c, state, request)
    assert c._path().read_bytes() == before
