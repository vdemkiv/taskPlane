import copy

import pytest

from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime
from taskplane import loop
from taskplane.review_session import (
    ReviewSessionError,
    apply_failure,
    create_session,
    record_consent,
    request_authority,
    validation_evidence,
)


KNOWN_ACTIONS = [
    {"id": "dynamic_validation", "non_destructive": True},
    {"id": "inline_render", "non_destructive": True},
    {"id": "sandbox_repair", "non_destructive": True},
    {"id": "collection", "non_destructive": True},
    {"id": "mechanical_repair", "non_destructive": True},
    {"id": "affected_retry", "non_destructive": True},
    {"id": "artifact_publication", "non_destructive": True},
]


def _configured(response="run the full dynamic review and show it inline"):
    session = create_session(
        run_id="r-1",
        target={"fingerprint": "a" * 64, "revision": "abc123"},
        available_actions=KNOWN_ACTIONS,
    )
    return record_consent(session, response=response, actor="human")


@pytest.mark.parametrize("response", [
    "run the full dynamic review and show it inline",
    "please test it, render the dashboard, and finish the review",
    "dynamic-render",
])
def test_free_form_complete_consent_covers_known_nondestructive_flow(response):
    session = _configured(response)

    assert session["consent"]["mode"] == "dynamic-render"
    assert set(session["consent"]["actions"]) == {
        row["id"] for row in KNOWN_ACTIONS
    }
    for action in session["consent"]["actions"]:
        assert request_authority(
            session, action=action, fact=f"routine {action}") is None
    assert session["metrics"]["approval_requests"] == 1


@pytest.mark.parametrize(("trigger", "authority"), [
    ("target_or_scope_changed", "review the changed target"),
    ("destructive_or_external_action", "allow remote write"),
    ("permission_or_credential_escalation", "use a credential"),
    ("unsafe_operation", "run an unsafe operation"),
    ("irreconcilable_requirement_ambiguity", "choose the requirement"),
    ("final_disposition", "approve or reject the review"),
])
def test_only_material_authority_changes_reprompt(trigger, authority):
    session = _configured()

    request = request_authority(
        session, action="new-action", trigger=trigger,
        fact="the relevant fact changed", authority=authority,
    )

    assert request == {
        "schema": "taskplane.review-authority-request/v1",
        "trigger": trigger,
        "fact": "the relevant fact changed",
        "authority": authority,
        "action": "new-action",
    }
    assert session["metrics"]["approval_requests"] == 2


def test_routine_unknown_action_is_rejected_without_inventing_an_approval():
    session = _configured()
    with pytest.raises(ReviewSessionError, match="material authority trigger"):
        request_authority(session, action="surprise-routine-step",
                          fact="adapter asked again")
    assert session["metrics"]["approval_requests"] == 1


@pytest.mark.parametrize(("kind", "status"), [
    ("host_limitation", "unavailable"),
    ("dynamic_check_unavailable", "unavailable"),
    ("invalid_reference", "incomplete"),
    ("renderer_failure", "incomplete"),
    ("artifact_write_failure", "incomplete"),
    ("unrepaired_slot", "incomplete"),
])
def test_faults_are_stable_non_success_and_preserve_findings(kind, status):
    session = _configured()
    session["findings"] = [{"id": "f-1", "severity": "high"}]

    failed = apply_failure(session, kind=kind, detail="bounded detail")

    assert failed["status"] == status
    assert failed["completed"] is False
    assert failed["passed"] is False
    assert failed["findings"] == [{"id": "f-1", "severity": "high"}]
    assert failed["failure"]["code"] == kind
    assert failed["failure"]["action"]
    assert "declined" not in str(failed["failure"]).lower()


def test_host_transport_is_metadata_only_and_runtime_keeps_session_binding(
        tmp_path):
    canonical = []
    for host in ("claude", "codex"):
        runtime = CommandRuntime(
            str(tmp_path / host), workspace="repo", authorization="actor")
        adapter = CommandAdapter(
            host=host, runtime=runtime,
            launcher=lambda command, cwd: HostLaunch(binding={"pid": 10}),
        )
        session = _configured()
        handle = adapter.launch_review_validation(
            ["npm", "test"], cwd="/sandbox", session=session,
            sandbox={"disposable": True, "push_disabled": True,
                     "sandbox_id": "box-1"},
        )
        adapter.notify(handle, {"status": "completed", "exit_code": 0})
        envelope = adapter.wait_review_event(handle, consumer="review")
        assert envelope["transport"]["host"] == host
        assert envelope["event"]["review_session"]["run_id"] == "r-1"
        canonical.append(envelope["event"])

    assert canonical[0] == canonical[1]


def test_dynamic_validation_requires_disposable_push_disabled_copy(tmp_path):
    runtime = CommandRuntime(
        str(tmp_path / "commands"), workspace="repo", authorization="actor")
    launches = []
    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: (
            launches.append((command, cwd)) or HostLaunch(binding={"pid": 10})
        ),
    )
    with pytest.raises(ValueError, match="disposable push-disabled"):
        adapter.launch_review_validation(
            ["npm", "test"], cwd="/submitted", session=_configured(),
            sandbox={"disposable": False, "push_disabled": False},
        )
    assert launches == []


def test_validation_evidence_distinguishes_submitted_and_sandbox_outcomes():
    evidence = validation_evidence(
        submitted={"head_before": "abc", "head_after": "abc",
                   "remote_before": "origin", "remote_after": "origin",
                   "outcome": "build_failed"},
        sandbox={"disposable": True, "push_disabled": True,
                 "push_attempts": 0, "delta_ref": "sha256:123",
                 "outcome": "build_passed_after_validation_repair"},
    )

    assert evidence["submitted_pr"]["outcome"] == "build_failed"
    assert evidence["sandbox"]["outcome"].startswith("build_passed")
    assert evidence["sandbox"]["delta_ref"] == "sha256:123"
    assert evidence["submitted_pr"]["unchanged"] is True
    assert evidence["remote"]["unchanged"] is True
    assert evidence["push_attempts"] == 0


def test_loop_consumes_canonical_consent_without_routine_human_gate():
    session = _configured()
    before = copy.deepcopy(session)
    assert loop.review_session_authority_gate(
        session, action="collection", fact="all slots returned") is None
    assert session == before

    gate = loop.review_session_authority_gate(
        session, action="final_disposition", fact="canonical review is ready",
        trigger="final_disposition", authority="approve or reject",
    )
    assert gate["trigger"] == "final_disposition"
