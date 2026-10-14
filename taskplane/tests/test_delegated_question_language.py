"""Scoped question language regressions using synthetic source observations.

Both local host adapters are exercised. These fixtures neither authenticate
accounts nor claim an installed Codex/Claude smoke test.
"""
from copy import deepcopy

import pytest

from taskplane import workflow as w, workflow_host as host_api
from taskplane.tests.test_delegated_checkpoint import observation, digest
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import submit, decide


@pytest.fixture(params=["codex", "claude"])
def pending(tmp_path, request):
    initial, _, _ = prepare(tmp_path)
    (tmp_path / "build-check.txt").write_text("Fixture Build check passed")
    (tmp_path / "check.txt").write_text("Observed fixture check result")
    controller = host_api.Controller(tmp_path, "root", host_api.installed_adapter(request.param))
    state = controller.start({"scope": initial["scope"], "request_reference": "fixture/user-request"})
    return controller, submit(controller, state)


def question_request(state, text, linkage="adjacent"):
    request = observation(state) if linkage == "reaction" else ordinary(state)
    relay = request["relay"]
    relay["question"]["text"] = text
    raw = relay["observation"]["raw"]
    if linkage == "reaction":
        raw["message"]["content"]["text"] = text
    else:
        raw["before"][0]["content"]["text"] = text
        raw["message"]["content"]["text"] = "approve"
        if linkage == "direct":
            raw["message"]["reply_to_id"] = relay["question"]["message_id"]
    digest(request)
    return request


@pytest.mark.parametrize("linkage", ["adjacent", "direct", "reaction"])
@pytest.mark.parametrize("question", [
    "Approve this Product scope to proceed to Design?",
    "Accept the Product scope?",
    "Please approve this Product scope?",
    "Will you please approve this Product scope?",
    "Do you approve this Product scope?",
    "The Product checkpoint is ready.\n\n"
    "- Preserve completed work and retry residual obligations.\n"
    "- Verify required evidence in both hosts.\n\n"
    "Approve this Product scope to proceed to Design? Installation and publication "
    "remain separate, and later phase approvals are not implied.",
])
def test_clear_question_preserves_original_evidence_and_only_approves_product(pending, question, linkage):
    controller, state = pending
    request = question_request(state, question, linkage)
    original = deepcopy(request)
    packet = deepcopy(w.current(state)["packet"])
    result = decide(controller, state, request)
    event = result["decisions"][request["event_id"]]
    assert w.current(result)["decision"] == "approved"
    assert w.current(result)["packet"] == packet
    assert event["provenance"]["relay"] == original["relay"]
    assert request == original
    assert result.get("approval_policy") is None
    advanced = controller.apply("advance", result["run"], expected_revision=result["revision"], phase="design")
    assert w.current(advanced)["phase"] == "design"
    assert w.current(advanced)["decision"] == "not_requested"
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(controller, advanced, request)
    assert controller._path().read_bytes() == before


@pytest.mark.parametrize("question", [
    "Approve this Design scope?",
    "Approve this Product and approve Design?",
    "Approve this Product or Design?",
    "Approve this Product? Do you approve this Design?",
    "Do you approve this Product? Approve this Design?",
    "Approve this Product? Shall I publish the package?",
    "Approve this Product if the unrun checks pass?",
    "Please do not approve this Product?",
    "Do you not approve this Product?",
    "Could this example work: approve this Product?",
    "The documentation says 'Approve this Product?'",
    "Approve this Product checkpoint " + "f" * 32 + "?",
    "Approve this invoice?",
])
def test_ambiguous_conditional_foreign_or_unrelated_question_keeps_checkpoint(pending, question):
    controller, state = pending
    request = question_request(state, question)
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(controller, state, request)
    assert controller._path().read_bytes() == before


@pytest.mark.parametrize("answer", [
    "No", "Reject", "Maybe", "Approve if the checks pass", "Approve Design",
    "Approve. Actually do not approve", "Product and Design are approved",
    "Thanks for the invoice", "'approve'",
])
def test_imperative_question_does_not_weaken_answer_validation(pending, answer):
    controller, state = pending
    request = question_request(state, "Approve this Product scope to proceed to Design?")
    request["relay"]["observation"]["raw"]["message"]["content"]["text"] = answer
    digest(request)
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(controller, state, request)
    assert controller._path().read_bytes() == before
