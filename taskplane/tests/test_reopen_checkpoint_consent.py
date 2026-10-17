"""Synthetic, host-parameterized observations; no live account/host certification."""
from copy import deepcopy

import pytest

from taskplane import delegated_approval as relay_api, workflow as w, workflow_host as h
from taskplane.tests.test_delegated_checkpoint import digest, observation
from taskplane.tests.test_delegated_checkpoint import consent_transport, assert_consent_refused
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
    assert event["provenance"]["normalization"]["question_choice"] == "changes_requested"
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


@pytest.mark.parametrize("question,answer", [
    ("May I reopen that Product checkpoint?", "yes"),
    ("Can we revise this Product proposal?", "Yes, please."),
    ("Would you want me to request changes to the Product output?", "Please do"),
    ("Please reopen this Product checkpoint?", "Request changes"),
    ("Should I reopen the Product checkpoint to refresh the affected reviews?", "go ahead"),
    ("Could I revise the Product proposal, update its baseline to v3.1.2?", "Okay!"),
    ("The Product proposal is not current. May I reopen that Product checkpoint?", "yes"),
])
def test_scoped_reopen_variants(pending, question, answer):
    c, state = pending
    assert w.current(decide(c, state, reopen_observation(state, question, answer)))["decision"] == "changes_requested"


def test_yes_to_acceptance_stays_approval(pending):
    c, state = pending
    request = ordinary(state)
    request["relay"]["observation"]["raw"]["message"]["content"]["text"] = "yes"
    digest(request)
    assert w.current(decide(c, state, request))["decision"] == "approved"


@pytest.mark.parametrize("question", [
    "May I reopen that Design checkpoint?",
    "May I reopen that Product checkpoint and approve it?",
    "May I reopen that Product checkpoint and start implementation?",
    "May I reopen that Product checkpoint or Design?",
    "May I reopen that Product checkpoint? Shall I publish it?",
    "Do you accept this Product? May I reopen that Product checkpoint?",
    "May I not reopen that Product checkpoint?",
    "May I reopen that Product checkpoint if checks pass?",
    "If you approve, may I reopen that Product checkpoint?",
    "Example:\nMay I reopen that Product checkpoint?",
    "```\nMay I reopen that Product checkpoint?\n```",
    "> May I reopen that Product checkpoint?",
    "May I reopen that Product checkpoint? This approves the proposal.",
    "Do not reopen it.\nMay I reopen that Product checkpoint?",
    "This request was withdrawn. May I reopen that Product checkpoint?",
    "May I reopen that Product checkpoint, update it to 3.0.0 and update it to 4.0.0?",
    "May I reopen that Product checkpoint, update it to verified 3.0.0 and merge main?",
    "May I reopen that Product checkpoint? This doesn’t approve the proposal or start implementation, but approves Design.",
    "May I reopen that Product checkpoint " + "f" * 32 + "?",
])
def test_mixed_ambiguous_or_foreign_questions_refuse_without_mutation(pending, question):
    c, state = pending
    request = reopen_observation(state, question)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("answer", [
    "no", "maybe", "Approved", "Approve Product", "yes, approve Design",
    "yes if checks pass", "yes; start implementation", "yes, but don't reopen", "'yes'",
    "Request changes: approve Design", "Request changes: revise Design", "👍",
])
def test_reopen_assent_cannot_be_replaced_with_acceptance_or_unknown_text(pending, answer):
    c, state = pending
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, reopen_observation(state, answer=answer))
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("tail", [
    "Only after CI passes.", "Wait until checks pass.", "Once I confirm.",
    "Don't proceed.", "Don’t proceed.", "Stop.", "Please delete all records.",
    "Delete all records.", "Also send the report.", "Run the migration.",
    "I will delete all records.", "The status is that I will delete all records.",
    "The correction is authorized only after CI passes.",
])
def test_extra_timing_negative_or_unknown_instructions_cannot_be_discarded(pending, linked, tail):
    c, state = pending
    request = reopen_observation(state, "May I reopen that Product checkpoint? " + tail, linked=linked)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("linked", [False, True])
def test_unknown_instruction_before_question_is_not_discarded(pending, linked):
    c, state = pending
    request = reopen_observation(state, "Please delete all records. May I reopen that Product checkpoint?", linked=linked)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("bad", [
    "partial", "later_message", "wrong_owner", "unverified_owner", "wrong_source", "receiver",
    "stale_binding", "foreign_run", "changed_raw", "changed_digest", "old_question",
    "answer_before_question", "wrong_choice", "wrong_event", "duplicate_event", "duplicate_source",
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
    elif bad == "wrong_choice": request["choice"] = "approved"
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


def test_reopen_reaction_is_not_misreported_as_acceptance(pending):
    c, state = pending
    request = observation(state)
    request["relay"]["question"]["text"] = QUESTION
    request["relay"]["observation"]["raw"]["message"]["content"]["text"] = QUESTION
    digest(request)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


def test_sec01_reopen_transport_cannot_record_approval_or_continue(consent_transport):
    """The original reopen question cannot authorize the fixture's approved claim.

    Successful delegated changes_requested and resubmission remain covered by
    test_reopen_yes_preserves_history_and_allows_only_checkpoint_revision.
    This matrix does not pretend unsupported text/reaction transports can reopen.
    """
    case = consent_transport("May I reopen this Product checkpoint?", "yes")
    assert_consent_refused(case)
    controller, state = case["controller"], case["state"]
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        controller.apply("advance", state["run"], expected_revision=state["revision"], phase="design")
    assert controller._path().read_bytes() == before
