"""Regression for a complete ChatGPT observation with null connector reply IDs.

The synthetic fixture preserves the relevant question/answer shape, metadata and
nulls without private project detail. Times are rebased to each synthetic run.
These cooperative fixtures do not authenticate accounts or certify live loading.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from taskplane import workflow as w
from taskplane.tests.test_delegated_checkpoint import observation, digest
from taskplane.tests.test_workflow_local import setup, submit, decide


def ordinary(state):
    request = observation(state, "owner_text")
    relay = request["relay"]
    source = relay["observation"]["raw"]["message"]
    raw = json.loads((Path(__file__).parent / "fixtures/approval/ordinary-owner-reply.json").read_text())
    raw["message"]["sent_at"] = source["sent_at"]
    raw["before"][0]["sent_at"] = relay["question"]["sent_at"]
    # Keep the source shape and null linkage, but use a supported complete
    # presentation. The immutable seed's older free-form scope description is
    # retained as an explicit refusal regression below, never silently stripped
    # by the production validator.
    raw["before"][0]["content"]["text"] = (
        "The Product checkpoint is ready. No implementation has started.\n\n"
        "Do you accept this Product checkpoint so Design can proceed? "
        "You can approve by reacting 👍 to this message."
    )
    relay["question"]["text"] = raw["before"][0]["content"]["text"]
    relay["observation"]["raw"] = raw
    digest(request)
    return request


def test_ordinary_approval_keeps_null_link_and_complete_fixture_provenance(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = ordinary(state)
    original = deepcopy(request)
    assert request["relay"]["observation"]["raw"]["message"]["content"]["text"] == "Approve"
    result = decide(c, state, request)
    event = result["decisions"][request["event_id"]]
    assert w.current(result)["decision"] == "approved"
    assert event["human"] is True and event["automatic"] is False
    assert event["provenance"]["relay"] == original["relay"]
    assert request == original
    assert event["provenance"]["normalization"]["contextual_reply"]["reply_to_id"] is None
    assert event["provenance"]["excerpt"] == "Approve"
    assert result.get("approval_policy") is None
    advanced = c.apply("advance", result["run"], expected_revision=result["revision"], phase="design")
    assert w.current(advanced)["phase"] == "design"
    assert w.current(advanced)["decision"] == "not_requested"
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, advanced, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("bad", [
    "partial", "missing_partial", "missing_before", "empty_before", "duplicate_before",
    "missing_after", "later_message", "missing_reply", "wrong_reply", "missing_reply_root",
    "wrong_reply_root", "source_not_user", "third_party", "owner_unverified", "generic_method",
    "old_observation", "incomplete_observation", "deleted_answer", "question_id", "question_text",
    "question_time", "question_channel", "question_author", "question_owner", "question_deleted",
    "missing_question_deleted", "duplicate_message_id", "scope", "checkpoint", "manifest",
    "revision", "phase", "before_submission", "answer_before_question", "question_two_prompts",
    "question_two_targets", "question_alternative_phase", "question_foreign_checkpoint",
    "question_conditional", "question_negative", "question_unrelated_prompt", "empty_question_content", "invalid_before",
])
def test_incomplete_conflicting_or_foreign_context_is_rejected_without_mutation(tmp_path, bad):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = ordinary(state)
    relay = request["relay"]; obs = relay["observation"]; raw = obs["raw"]
    message = raw["message"]; shown = raw["before"][0]
    if bad == "partial": raw["partial"] = True
    elif bad == "missing_partial": del raw["partial"]
    elif bad == "missing_before": del raw["before"]
    elif bad == "empty_before": raw["before"] = []
    elif bad == "duplicate_before": raw["before"].append(deepcopy(shown))
    elif bad == "missing_after": del raw["after"]
    elif bad == "later_message": raw["after"] = [{"content": {"text": "Withdraw approval"}}]
    elif bad == "missing_reply": del message["reply_to_id"]
    elif bad == "wrong_reply": message["reply_to_id"] = "another-question"
    elif bad == "missing_reply_root": del message["reply_root_message_id"]
    elif bad == "wrong_reply_root": message["reply_root_message_id"] = "another-thread"
    elif bad == "source_not_user": message["author"] = "tool"
    elif bad == "third_party": message["author_id"] = "another-person"
    elif bad == "owner_unverified": relay["owner"]["verified"] = False
    elif bad == "generic_method": obs["method"] = "tool_result"
    elif bad == "old_observation": obs["current"] = False
    elif bad == "incomplete_observation": obs["complete_message"] = False
    elif bad == "deleted_answer": message["deleted_at"] = obs["observed_at"]
    elif bad == "question_id": shown["message_id"] = "unrelated-question"
    elif bad == "question_text": shown["content"]["text"] = "Do you want a coffee?"
    elif bad == "question_time": shown["sent_at"] = state["started_at"]
    elif bad == "question_channel": shown["channel"] = "another-channel"
    elif bad == "question_author": shown["author"] = "user"
    elif bad == "question_owner": shown["author_id"] = relay["owner"]["user_id"]
    elif bad == "question_deleted": shown["deleted_at"] = obs["observed_at"]
    elif bad == "missing_question_deleted": del shown["deleted_at"]
    elif bad == "duplicate_message_id": message["message_id"] = shown["message_id"]
    elif bad in {"scope", "checkpoint", "manifest", "revision"}:
        field = {"scope": "scope_digest", "manifest": "manifest_digest"}.get(bad, bad)
        request["binding"][field] = "foreign"
    elif bad == "phase": relay["question"]["phase"] = "design"
    elif bad == "before_submission": relay["question"]["sent_at"] = shown["sent_at"] = state["started_at"]
    elif bad == "answer_before_question": message["sent_at"] = relay["question"]["sent_at"]
    elif bad == "empty_question_content": shown["content"] = None
    elif bad == "invalid_before": raw["before"] = [None]
    else:
        text = {
            "question_two_prompts": "Do you accept this Product? Do you approve this Design?",
            "question_two_targets": "Do you accept this Product and approve Design?",
            "question_alternative_phase": "Do you accept this Product or Design?",
            "question_foreign_checkpoint": "Do you accept this Product checkpoint " + "f" * 32 + "?",
            "question_conditional": "Do you accept this Product if unrun checks pass?",
            "question_negative": "Do you not accept this Product?",
            "question_unrelated_prompt": "Do you accept this Product? Shall I buy a domain?",
        }[bad]
        relay["question"]["text"] = shown["content"]["text"] = text
    digest(request)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("text", ["Reject", "Cancelled", "Request changes", "No", "Maybe",
    "Approve if the checks pass", "Approve. Actually do not approve", "Approve Design",
    "Product and Design are approved", "Approve " + "f" * 32, "Thanks for the invoice", "'Approve'"])
def test_ordinary_refusal_ambiguity_or_different_target_cannot_approve(tmp_path, text):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = ordinary(state)
    request["relay"]["observation"]["raw"]["message"]["content"]["text"] = text
    digest(request)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before



def test_immutable_original_scope_description_is_an_explicit_compatibility_refusal(tmp_path):
    """Retain old complete bytes: free-form summary requires a fresh question."""
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = ordinary(state)
    original_raw = json.loads((Path(__file__).parent / "fixtures/approval/ordinary-owner-reply.json").read_text())
    text = original_raw["before"][0]["content"]["text"]
    request["relay"]["question"]["text"] = text
    request["relay"]["observation"]["raw"]["before"][0]["content"]["text"] = text
    digest(request)
    original = deepcopy(request)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match="Unsupported or conditional checkpoint question"):
        decide(c, state, request)
    assert c._path().read_bytes() == before
    assert request == original
